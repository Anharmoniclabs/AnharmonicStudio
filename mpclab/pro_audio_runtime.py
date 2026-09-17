"""Install multichannel/precision/patchbay graph infrastructure."""

from __future__ import annotations

from .pro_audio_graph import ProAudioGraph

_INSTALLED = False


def install_pro_audio_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from .engine import Engine

    original_init = Engine.__init__
    original_prepare_fx = Engine.prepare_fx
    original_configure_blocksize = Engine.configure_blocksize

    def rebuild(engine, project=None):
        engine.pro_audio_graph = ProAudioGraph(project or engine.project, engine.blocksize)
        return engine.pro_audio_graph

    def init(engine, *args, **kwargs):
        original_init(engine, *args, **kwargs)
        rebuild(engine)

    def prepare_fx(engine, project=None):
        result = original_prepare_fx(engine, project)
        rebuild(engine, project)
        return result

    def configure_blocksize(engine, frames):
        result = original_configure_blocksize(engine, frames)
        rebuild(engine)
        return result

    Engine.__init__ = init
    Engine.prepare_fx = prepare_fx
    Engine.configure_blocksize = configure_blocksize
    Engine.rebuild_pro_audio_graph = rebuild
    _INSTALLED = True
