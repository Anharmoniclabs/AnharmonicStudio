"""Install a bounded post-master analysis tap without putting UI work in audio."""

from __future__ import annotations

from .mastering_analysis import LiveAnalysisTap

_INSTALLED = False


def install_mastering_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .engine import Engine

    original_init = Engine.__init__
    original_callback = Engine._callback

    def init(engine, *args, **kwargs):
        original_init(engine, *args, **kwargs)
        engine.mastering_tap = LiveAnalysisTap(engine.sr)

    def callback(engine, outdata, frames, time_info, status):
        result = original_callback(engine, outdata, frames, time_info, status)
        tap = getattr(engine, "mastering_tap", None)
        if tap is not None:
            tap.push(outdata[:frames])
        return result

    Engine.__init__ = init
    Engine._callback = callback
    _INSTALLED = True
