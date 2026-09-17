"""Install first-party advanced engines on stable hosted-instrument routes."""

from __future__ import annotations

from copy import deepcopy

from .expansion_instruments import ExpansionInstrumentBridge

_INSTALLED = False


def install_expansion_instrument_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from .engine import Engine

    original_init = Engine.__init__
    original_start = Engine.start
    original_prepare_fx = Engine.prepare_fx

    def preload_multisample(engine, config) -> None:
        if config.get("type") != "multisample":
            return
        for region in config.get("state", {}).get("regions", []):
            for key in ("sample_id", "release_sample_id"):
                sample_id = region.get(key)
                if sample_id:
                    engine.lib.audio(sample_id)

    def sync(engine, project=None):
        project = project or engine.project
        state = getattr(project, "daw_expansion", {})
        configs = state.get("instrument_engines", {}) if isinstance(state, dict) else {}
        external_specs = getattr(project, "instrument_plugins", {})
        desired = {}
        for instrument_id, config in configs.items():
            if not config.get("enabled", True):
                continue
            spec = external_specs.get(instrument_id)
            if isinstance(spec, dict) and not spec.get("bypass", False):
                # A saved third-party instrument owns this identity. Never race
                # its asynchronous loader with a first-party fallback.
                continue
            desired[instrument_id] = config

        for instrument_id, config in desired.items():
            preload_multisample(engine, config)
            current = engine.external.instrument_for(instrument_id)
            fingerprint = (config["type"], repr(config.get("state", {})))
            if (
                current is not None
                and getattr(current, "info", {}).get("first_party")
                and getattr(current, "_expansion_fingerprint", None) == fingerprint
            ):
                continue
            bridge = ExpansionInstrumentBridge(
                config["type"],
                deepcopy(config.get("state", {})),
                engine.sr,
                engine.lib,
            )
            bridge._expansion_fingerprint = fingerprint
            old = engine.external.set_instrument(instrument_id, bridge)
            if old is not None and old is not bridge:
                old.close()

        for instrument_id in tuple(engine.external.active_instrument_ids()):
            if instrument_id in desired:
                continue
            plugin = engine.external.instrument_for(instrument_id)
            if plugin is not None and getattr(plugin, "info", {}).get("first_party"):
                old = engine.external.remove_instrument(instrument_id)
                if old is not None:
                    old.close()

        prepare_latency = getattr(engine, "prepare_plugin_latency", None)
        if prepare_latency is not None:
            prepare_latency()
        return tuple(desired)

    def init(engine, *args, **kwargs):
        original_init(engine, *args, **kwargs)
        engine.sync_expansion_instruments = lambda project=None: sync(engine, project)

    def prepare_fx(engine, project=None):
        result = original_prepare_fx(engine, project)
        if engine.stream is None and hasattr(engine, "sync_expansion_instruments"):
            engine.sync_expansion_instruments(project or engine.project)
        return result

    def start(engine, *args, **kwargs):
        engine.sync_expansion_instruments(engine.project)
        return original_start(engine, *args, **kwargs)

    Engine.__init__ = init
    Engine.prepare_fx = prepare_fx
    Engine.start = start
    _INSTALLED = True
