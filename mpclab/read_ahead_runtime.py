"""Attach managed read-ahead to the decoded library and sample voices."""

from __future__ import annotations

from .read_ahead import ReadAheadManager

_INSTALLED = False


def _settings(project):
    state = getattr(project, "daw_expansion", {})
    streaming = state.get("streaming", {}) if isinstance(state, dict) else {}
    return {
        "enabled": bool(streaming.get("enabled", True)),
        "read_ahead_frames": int(streaming.get("read_ahead_frames", 262_144)),
        "request_capacity": int(streaming.get("request_capacity", 1024)),
    }


def install_read_ahead_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from .engine import Engine
    from .library import Library

    original_library_init = Library.__init__
    original_audio = Library.audio
    original_delete = Library.delete
    original_configure_rate = getattr(Library, "configure_sample_rate", None)
    original_voice_for_pad = Engine._voice_for_pad
    original_audio_clip_source = Engine._audio_clip_source

    def make_manager(library, settings=None):
        options = settings or {
            "enabled": True,
            "read_ahead_frames": 262_144,
            "request_capacity": 1024,
        }
        old = getattr(library, "read_ahead", None)
        if old is not None:
            old.close()
        manager = ReadAheadManager(
            options["read_ahead_frames"], options["request_capacity"]
        )
        library.read_ahead = manager
        for clip_id, audio in library._audio.items():
            manager.register(clip_id, audio)
        if options["enabled"]:
            manager.start()
        return manager

    def library_init(library, *args, **kwargs):
        original_library_init(library, *args, **kwargs)
        make_manager(library)

    def audio(library, clip_id):
        result = original_audio(library, clip_id)
        if result is not None:
            library.read_ahead.register(clip_id, result)
            library.read_ahead.request(clip_id, 0)
        return result

    def delete(library, clip_id):
        manager = getattr(library, "read_ahead", None)
        if manager is not None:
            manager.unregister(clip_id)
        return original_delete(library, clip_id)

    def configure_rate(library, sample_rate):
        old = getattr(library, "read_ahead", None)
        was_running = bool(old is not None and old.running)
        read_ahead_frames = getattr(old, "read_ahead_frames", 262_144)
        request_capacity = getattr(old, "capacity", 1024)
        result = (
            original_configure_rate(library, sample_rate)
            if original_configure_rate is not None
            else sample_rate
        )
        make_manager(
            library,
            {
                "enabled": was_running,
                "read_ahead_frames": read_ahead_frames,
                "request_capacity": request_capacity,
            },
        )
        return result

    def voice_for_pad(engine, pad, velocity, note=None):
        voice = original_voice_for_pad(engine, pad, velocity, note)
        manager = getattr(engine.lib, "read_ahead", None)
        if voice is not None and manager is not None and voice.source_id:
            frame = voice.s1 - 1 if pad.reverse else voice.s0
            manager.request(
                voice.source_id,
                frame,
                reverse=bool(pad.reverse),
                frames=max(engine.blocksize * 8, manager.read_ahead_frames),
            )
        return voice

    def audio_clip_source(engine, clip):
        result = original_audio_clip_source(engine, clip)
        if result and result[0] is not None:
            manager = getattr(engine.lib, "read_ahead", None)
            if manager is not None:
                _audio, start, _end = result
                manager.request(clip.ref, start, reverse=bool(clip.reverse))
        return result

    def configure_streaming(engine, project=None):
        project = project or engine.project
        manager = getattr(engine.lib, "read_ahead", None)
        options = _settings(project)
        if manager is None or (
            manager.read_ahead_frames != options["read_ahead_frames"]
            or manager.capacity != options["request_capacity"]
        ):
            manager = make_manager(engine.lib, options)
        elif options["enabled"]:
            manager.start()
        else:
            manager.pause()
        return manager

    Library.__init__ = library_init
    Library.audio = audio
    Library.delete = delete
    if original_configure_rate is not None:
        Library.configure_sample_rate = configure_rate
    Engine._voice_for_pad = voice_for_pad
    Engine._audio_clip_source = audio_clip_source
    Engine.configure_streaming = configure_streaming
    _INSTALLED = True
