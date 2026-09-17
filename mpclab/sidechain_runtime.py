"""Install project sidechain routing into live and offline isolated plugin chains."""

from __future__ import annotations

from .sidechain import SidechainRouter

_INSTALLED = False


def install_sidechain_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from .engine import Engine
    from .plugin_chain_runtime import LivePluginChains, OfflinePluginChains

    original_init = Engine.__init__
    original_prepare_fx = Engine.prepare_fx
    original_configure_blocksize = Engine.configure_blocksize
    original_live_render = LivePluginChains.render
    original_offline_render = OfflinePluginChains.render

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

    def live_render(chains, target, block):
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
        original_live_render(chains, target, block)

    def offline_render(chains, target, block):
        chain = chains.chains.get(target)
        if chain is None:
            return
        router = getattr(chains, "_sidechain_router", None)
        sidechains = router.for_target(target, len(block)) if router is not None else {}
        if not sidechains:
            return original_offline_render(chains, target, block)
        supported = set(getattr(chain, "info", {}).get("sidechain_slots", []))
        requested = set(sidechains)
        if not requested <= supported:
            raise ValueError(
                "Configured sidechain targets a plugin slot that does not expose auxiliary audio"
            )
        block[:] = chain.render(block, len(block), sidechains=sidechains)

    Engine.__init__ = init
    Engine.prepare_fx = prepare_fx
    Engine.configure_blocksize = configure_blocksize
    LivePluginChains.render = live_render
    OfflinePluginChains.render = offline_render
    _INSTALLED = True
