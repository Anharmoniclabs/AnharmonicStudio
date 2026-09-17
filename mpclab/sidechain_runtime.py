"""Install project sidechain routing into live isolated plugin chains."""

from __future__ import annotations

from .sidechain import SidechainRouter

_INSTALLED = False


def install_sidechain_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from .engine import Engine
    from .plugin_chain_runtime import LivePluginChains

    original_init = Engine.__init__
    original_prepare_fx = Engine.prepare_fx
    original_configure_blocksize = Engine.configure_blocksize
    original_render = LivePluginChains.render

    def bind(engine) -> None:
        if not hasattr(engine, "sidechains"):
            engine.sidechains = SidechainRouter(engine.project, engine.blocksize)
        else:
            engine.sidechains.configure(engine.project, engine.blocksize)
        if hasattr(engine, "plugin_chains"):
            engine.plugin_chains._sidechain_engine = engine

    def init(engine, *args, **kwargs):
        original_init(engine, *args, **kwargs)
        bind(engine)

    def prepare_fx(engine, project=None):
        result = original_prepare_fx(engine, project)
        if hasattr(engine, "sidechains"):
            engine.sidechains.configure(project or engine.project, engine.blocksize)
        if hasattr(engine, "plugin_chains"):
            engine.plugin_chains._sidechain_engine = engine
        return result

    def configure_blocksize(engine, frames):
        result = original_configure_blocksize(engine, frames)
        bind(engine)
        return result

    def render(chains, target, block):
        bridge = chains.bridges.get(target)
        if bridge is None or getattr(bridge, "error", ""):
            return
        engine = getattr(chains, "_sidechain_engine", None)
        router = getattr(engine, "sidechains", None) if engine is not None else None
        sidechains = router.for_target(target, len(block)) if router is not None else {}
        if sidechains:
            supported = set(getattr(bridge, "info", {}).get("sidechain_slots", []))
            requested = set(sidechains)
            if not requested <= supported:
                bridge.error = (
                    "Configured sidechain targets a plugin slot that does not expose auxiliary audio"
                )
                return
            try:
                output = bridge.render(block, len(block), sidechains=sidechains)
            except TypeError:
                bridge.error = "Loaded plugin bridge does not support auxiliary audio; reload the chain"
                return
            if output is not None:
                block[:] = output
            return
        original_render(chains, target, block)

    Engine.__init__ = init
    Engine.prepare_fx = prepare_fx
    Engine.configure_blocksize = configure_blocksize
    LivePluginChains.render = render
    _INSTALLED = True
