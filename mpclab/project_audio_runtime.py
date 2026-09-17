"""Project-level sample-rate lifecycle and resampling policy."""

from __future__ import annotations

from copy import deepcopy

from .daw_expansion_state import SAMPLE_RATES, default_daw_expansion, validate_daw_expansion
from .fx_unification import configure_fx_sample_rate

_INSTALLED = False


def project_sample_rate(project, fallback=48_000) -> int:
    state = getattr(project, "daw_expansion", None)
    if not isinstance(state, dict):
        return int(fallback)
    rate = state.get("sample_rate", fallback)
    return int(rate) if type(rate) is int and rate in SAMPLE_RATES else int(fallback)


def install_project_audio_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from .engine import Engine
    from .fx import MixRack
    from .library import Library

    original_engine_init = Engine.__init__
    original_start = Engine.start

    def library_configure_sample_rate(library, sample_rate: int) -> int:
        rate = int(sample_rate)
        if rate not in SAMPLE_RATES:
            raise ValueError("library sample rate must be 44.1, 48, 88.2 or 96 kHz")
        if rate == library.sr:
            return rate
        with library._cache_lock:
            # Existing voices hold their own array references. Engine sample-rate
            # changes clear voices before this method is called, so dropping the
            # catalog caches cannot invalidate active playback.
            library._audio.clear()
            library._reversed.clear()
            library._visual_cache.clear()
            library.sr = rate
        manager = getattr(library, "read_ahead", None)
        if manager is not None:
            manager.close()
        return rate

    Library.configure_sample_rate = library_configure_sample_rate

    def configure_project_sample_rate(engine, sample_rate: int, project=None) -> int:
        project = project or engine.project
        rate = int(sample_rate)
        if rate not in SAMPLE_RATES:
            raise ValueError("project sample rate must be 44.1, 48, 88.2 or 96 kHz")
        if engine.stream is not None or engine._live_starting:
            raise RuntimeError("stop audio before changing the project sample rate")
        state = deepcopy(getattr(project, "daw_expansion", default_daw_expansion()))
        state["sample_rate"] = rate
        project.daw_expansion = validate_daw_expansion(state, project=project)
        if rate == engine.sr:
            configure_fx_sample_rate(rate)
            return rate

        old_rate = int(engine.sr)
        old_library_rate = int(getattr(engine.lib, "sr", old_rate))
        # Third-party bridges are created for one sample rate. Close them before
        # changing the clock; DevicesController.sync_project reloads persisted
        # plugins at the new rate. First-party expansion instruments are rebuilt
        # by their runtime sync hook.
        if hasattr(engine, "plugin_chains"):
            engine.plugin_chains.close()
        engine.external.close()
        engine.voices.clear()
        engine.synth_voices.clear()
        try:
            if hasattr(engine.lib, "configure_sample_rate"):
                engine.lib.configure_sample_rate(rate)
            engine.sr = rate
            configure_fx_sample_rate(rate)
            engine.rack = MixRack(len(project.tracks))
            engine.rack.prepare(engine.blocksize)
            engine._track_tone_requests = [None] * len(project.tracks)
            engine._master_tone_request = None
            engine.configure_blocksize(engine.blocksize)
            sync = getattr(engine, "sync_expansion_instruments", None)
            if sync is not None:
                sync(project)
            return rate
        except Exception:
            engine.sr = old_rate
            configure_fx_sample_rate(old_rate)
            if hasattr(engine.lib, "configure_sample_rate"):
                engine.lib.configure_sample_rate(old_library_rate)
            engine.rack = MixRack(len(project.tracks))
            engine.rack.prepare(engine.blocksize)
            engine.configure_blocksize(engine.blocksize)
            state["sample_rate"] = old_rate
            project.daw_expansion = validate_daw_expansion(state, project=project)
            raise

    def engine_init(engine, *args, **kwargs):
        original_engine_init(engine, *args, **kwargs)
        state = default_daw_expansion()
        state["sample_rate"] = int(engine.sr) if int(engine.sr) in SAMPLE_RATES else 48_000
        if not hasattr(engine.project, "daw_expansion"):
            engine.project.daw_expansion = validate_daw_expansion(state, project=engine.project)
        configure_fx_sample_rate(engine.sr)

    def start(engine, *args, **kwargs):
        desired = project_sample_rate(engine.project, engine.sr)
        if desired != engine.sr:
            engine.configure_project_sample_rate(desired, engine.project)
        return original_start(engine, *args, **kwargs)

    Engine.__init__ = engine_init
    Engine.configure_project_sample_rate = configure_project_sample_rate
    Engine.start = start
    _INSTALLED = True
