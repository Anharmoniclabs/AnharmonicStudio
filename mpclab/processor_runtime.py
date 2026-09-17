"""Install lifecycle-addressable wrappers over the engine's first-party DSP."""

from __future__ import annotations

from .processor_adapters import build_processor_graph

_INSTALLED = False


def install_processor_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from .engine import Engine

    original_init = Engine.__init__
    original_prepare_fx = Engine.prepare_fx
    original_configure_blocksize = Engine.configure_blocksize

    def rebuild(engine, project=None):
        project = project or engine.project
        engine.processors = build_processor_graph(
            engine.rack,
            project,
            engine.sr,
            engine.blocksize,
        )
        return engine.processors

    def reset(engine):
        for processor in getattr(engine, "processors", {}).values():
            processor.reset()

    def processor_state(engine) -> dict:
        return {
            name: {
                "latency_samples": processor.latency_samples(),
                "tail_samples": processor.tail_samples(),
                "state": processor.save_state(),
            }
            for name, processor in getattr(engine, "processors", {}).items()
        }

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
    Engine.rebuild_processors = rebuild
    Engine.reset_processors = reset
    Engine.processor_state = processor_state
    _INSTALLED = True
