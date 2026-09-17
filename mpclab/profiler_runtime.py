"""Runtime hooks for per-track, per-plugin and disk-pressure diagnostics."""

from __future__ import annotations

import time

from .audio_profiler import AudioPerformanceProfiler

_INSTALLED = False


def _record_worker_metric(plugin, elapsed_ns: int) -> None:
    elapsed_ms = max(0.0, elapsed_ns * 1e-6)
    plugin.profile_last_ms = elapsed_ms
    plugin.profile_calls = getattr(plugin, "profile_calls", 0) + 1
    plugin.profile_total_ms = getattr(plugin, "profile_total_ms", 0.0) + elapsed_ms
    plugin.profile_max_ms = max(getattr(plugin, "profile_max_ms", 0.0), elapsed_ms)


def plugin_profile(plugin) -> dict:
    calls = int(getattr(plugin, "profile_calls", 0))
    return {
        "calls": calls,
        "last_ms": float(getattr(plugin, "profile_last_ms", 0.0)),
        "mean_ms": float(getattr(plugin, "profile_total_ms", 0.0)) / max(1, calls),
        "max_ms": float(getattr(plugin, "profile_max_ms", 0.0)),
    }


def install_profiler_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from .engine import Engine
    from .plugin_host import IsolatedPlugin
    from .plugin_chain_host import IsolatedPluginChain

    original_engine_init = Engine.__init__
    original_prepare_fx = Engine.prepare_fx
    original_plugin_render = IsolatedPlugin.render
    original_chain_render = IsolatedPluginChain.render

    def init(engine, *args, **kwargs):
        original_engine_init(engine, *args, **kwargs)
        engine.performance_profiler = AudioPerformanceProfiler()
        engine.performance_profiler.register("callback", "master")
        engine.performance_profiler.replace_track_set(track.id for track in engine.project.tracks)

    def prepare_fx(engine, project=None):
        result = original_prepare_fx(engine, project)
        project = project or engine.project
        engine.performance_profiler.replace_track_set(track.id for track in project.tracks)
        for instrument in project.instruments:
            engine.performance_profiler.register(f"instrument:{instrument.id}")
        return result

    def plugin_render(plugin, *args, **kwargs):
        started = time.perf_counter_ns()
        try:
            return original_plugin_render(plugin, *args, **kwargs)
        finally:
            _record_worker_metric(plugin, time.perf_counter_ns() - started)

    def chain_render(plugin, *args, **kwargs):
        started = time.perf_counter_ns()
        try:
            return original_chain_render(plugin, *args, **kwargs)
        finally:
            _record_worker_metric(plugin, time.perf_counter_ns() - started)

    def performance_report(engine) -> dict:
        profiler = engine.performance_profiler
        manager = getattr(engine.lib, "read_ahead", None)
        if manager is not None:
            profiler.set_disk_pressure(manager.pressure)
        report = profiler.snapshot()
        plugins = {}
        for instrument_id, plugin in engine.external.plugin_map().items():
            inner = getattr(plugin, "plugin", plugin)
            plugins[f"instrument:{instrument_id or 'legacy'}"] = plugin_profile(inner)
        effect = getattr(engine.external, "effect", None)
        if effect is not None:
            plugins["master-effect"] = plugin_profile(getattr(effect, "plugin", effect))
        chains = getattr(engine, "plugin_chains", None)
        if chains is not None:
            for target, bridge in chains.bridges.items():
                plugins[f"chain:{target}"] = plugin_profile(getattr(bridge, "plugin", bridge))
        report["plugins"] = plugins
        report["callback"] = engine.timing_stats()
        if manager is not None:
            report["streaming"] = manager.stats()
        return report

    Engine.__init__ = init
    Engine.prepare_fx = prepare_fx
    Engine.performance_report = performance_report
    IsolatedPlugin.render = plugin_render
    IsolatedPluginChain.render = chain_render
    _INSTALLED = True
