"""Runtime ownership and graph-aware delay compensation for insert chains."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .plugin_chain_host import IsolatedPluginChain, live_plugin_chain
from .plugin_latency import MAX_COMPENSATION_SAMPLES, plugin_path_latency_samples
from .pro_daw_state import plugin_chains
from .workflow_routing import MASTER_TARGET, RoutingPlan


def target_chain_specs(project, target: str) -> list[dict]:
    return [item for item in plugin_chains(project).get(target, []) if not item.get("bypass", False)]


def known_chain_targets(project) -> set[str]:
    targets = {"master", *(f"track:{track.id}" for track in project.tracks)}
    workflow = getattr(project, "workflow", {})
    routing = workflow.get("routing", {}) if isinstance(workflow, dict) else {}
    buses = routing.get("buses", []) if isinstance(routing, dict) else []
    if isinstance(buses, list):
        targets.update(
            f"bus:{item.get('id')}"
            for item in buses
            if isinstance(item, dict) and item.get("id")
        )
    return targets


class LivePluginChains:
    """Live chain bridges. Mutations happen off the callback thread."""

    def __init__(self):
        self.bridges: dict[str, object] = {}

    def install(self, target: str, bridge) -> None:
        old = self.bridges.pop(target, None)
        self.bridges[target] = bridge
        if old is not None and old is not bridge:
            old.close()

    def remove(self, target: str) -> None:
        old = self.bridges.pop(target, None)
        if old is not None:
            old.close()

    def render(self, target: str, block: np.ndarray) -> None:
        bridge = self.bridges.get(target)
        if bridge is None or getattr(bridge, "error", ""):
            return
        output = bridge.render(block, len(block))
        if output is not None:
            block[:] = output

    def latencies(self) -> dict[str, int]:
        return {
            target: plugin_path_latency_samples(bridge, include_live_bridge=True)
            for target, bridge in self.bridges.items()
            if bridge is not None and not getattr(bridge, "error", "")
        }

    def close(self) -> None:
        bridges, self.bridges = self.bridges, {}
        for bridge in bridges.values():
            bridge.close()


class OfflinePluginChains:
    """Synchronous insert chains for bounce/export with no live bridge delay."""

    def __init__(self, project, sample_rate: int, *, factory=IsolatedPluginChain):
        self.chains: dict[str, IsolatedPluginChain] = {}
        try:
            valid = known_chain_targets(project)
            for target, raw in plugin_chains(project).items():
                specs = [item for item in raw if not item.get("bypass", False)]
                if target in valid and specs:
                    self.chains[target] = factory(specs, sample_rate)
        except Exception:
            self.close()
            raise

    def render(self, target: str, block: np.ndarray) -> None:
        chain = self.chains.get(target)
        if chain is not None:
            block[:] = chain.render(block, len(block))

    def latencies(self) -> dict[str, int]:
        return {
            target: plugin_path_latency_samples(chain, include_live_bridge=False)
            for target, chain in self.chains.items()
        }

    def close(self) -> None:
        chains, self.chains = self.chains, {}
        for chain in chains.values():
            chain.close()


@dataclass(frozen=True, slots=True)
class ChainLatencyPlan:
    edge_delays: dict[str, int]
    node_latencies: dict[str, int]
    master_input_latency: int
    output_latency: int


def compile_chain_latency_plan(
    plan: RoutingPlan,
    chain_latencies: dict[str, int],
) -> ChainLatencyPlan:
    """Align every incoming edge at each routing summing junction.

    Track chains establish source-node latency. Buses are visited in the same
    topological order as the routing plan. Each bus first aligns all incoming
    paths, then adds its own insert-chain latency. Master-bound paths are aligned
    last, before the master chain. This handles nested buses and arbitrary sends
    without delaying paths that never meet.
    """
    incoming: dict[int, list[tuple[str, int]]] = {}
    edge_delays: dict[str, int] = {}
    node_latencies: dict[str, int] = {}

    def bounded(value) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            number = 0
        return max(0, min(MAX_COMPENSATION_SAMPLES, number))

    def add(target: int, edge: str, latency: int) -> None:
        incoming.setdefault(target, []).append((edge, bounded(latency)))

    for index, track_id in enumerate(plan.track_ids):
        latency = bounded(chain_latencies.get(f"track:{track_id}", 0))
        node_latencies[f"track:{track_id}"] = latency
        add(plan.track_outputs[index], plan.track_output_edges[index], latency)
        for send in plan.track_sends[index]:
            add(send.target, send.edge, latency)

    for index, bus in enumerate(plan.buses):
        sources = incoming.get(index, [])
        arrival = max((latency for _edge, latency in sources), default=0)
        for edge, latency in sources:
            difference = arrival - latency
            if difference > 0:
                edge_delays[edge] = difference
        latency = bounded(arrival + chain_latencies.get(f"bus:{bus.id}", 0))
        node_latencies[f"bus:{bus.id}"] = latency
        add(bus.output, bus.output_edge, latency)
        for send in bus.sends:
            add(send.target, send.edge, latency)

    master_sources = incoming.get(MASTER_TARGET, [])
    master_arrival = max((latency for _edge, latency in master_sources), default=0)
    for edge, latency in master_sources:
        difference = master_arrival - latency
        if difference > 0:
            edge_delays[edge] = difference
    output = bounded(master_arrival + chain_latencies.get("master", 0))
    node_latencies["master"] = output
    return ChainLatencyPlan(edge_delays, node_latencies, master_arrival, output)


class _StereoDelay:
    def __init__(self, delay: int, blocksize: int):
        self.delay = max(1, int(delay))
        self.position = 0
        self.history = np.zeros((self.delay, 2), dtype=np.float32)
        self.output = np.zeros((max(1, int(blocksize)), 2), dtype=np.float32)

    def ensure_blocksize(self, frames: int) -> None:
        if frames > len(self.output):
            self.output = np.zeros((frames, 2), dtype=np.float32)

    def process(self, source: np.ndarray) -> np.ndarray:
        frames = len(source)
        self.ensure_blocksize(frames)
        destination = self.output[:frames]
        offset = 0
        position = self.position
        while offset < frames:
            take = min(frames - offset, self.delay - position)
            destination[offset : offset + take] = self.history[position : position + take]
            self.history[position : position + take] = source[offset : offset + take]
            offset += take
            position += take
            if position == self.delay:
                position = 0
        self.position = position
        return destination

    def reset(self) -> None:
        self.position = 0
        self.history.fill(0.0)
        self.output.fill(0.0)


class RoutingDelayBank:
    """Preallocated per-edge delay lines used only where a graph sum needs PDC."""

    def __init__(self, blocksize: int):
        self.blocksize = max(1, int(blocksize))
        self.lines: dict[str, _StereoDelay] = {}
        self.plan = ChainLatencyPlan({}, {}, 0, 0)

    def configure(self, plan: ChainLatencyPlan, blocksize: int | None = None) -> None:
        if blocksize is not None:
            self.blocksize = max(1, int(blocksize))
        self.plan = plan
        self.lines = {
            edge: _StereoDelay(delay, self.blocksize)
            for edge, delay in plan.edge_delays.items()
            if delay > 0
        }

    def process(self, edge: str, source: np.ndarray) -> np.ndarray:
        line = self.lines.get(edge)
        return source if line is None else line.process(source)

    def reset(self) -> None:
        for line in self.lines.values():
            line.reset()

    def lock_arrays(self) -> list[np.ndarray]:
        arrays = []
        for line in self.lines.values():
            arrays.extend((line.history, line.output))
        return arrays


def install_plugin_chain_runtime() -> None:
    """Attach optional chain ownership/PDC after the routing extension is installed."""
    from .engine import Engine

    if getattr(Engine, "_pro_plugin_chains_installed", False):
        return
    original_init = Engine.__init__
    original_configure_blocksize = Engine.configure_blocksize

    def refresh_latency(engine):
        plan = getattr(engine, "_routing_plan", None)
        if plan is None:
            return None
        latency = compile_chain_latency_plan(plan, engine.plugin_chains.latencies())
        engine.plugin_chain_delays.configure(latency, engine.blocksize)
        arrays = engine.plugin_chain_delays.lock_arrays()
        if arrays:
            engine.linux_audio.lock_arrays(*arrays)
        return latency

    def init(engine, *args, **kwargs):
        original_init(engine, *args, **kwargs)
        engine.plugin_chains = LivePluginChains()
        engine.plugin_chain_delays = RoutingDelayBank(engine.blocksize)
        refresh_latency(engine)

    def configure_blocksize(engine, frames):
        # Existing live plugin bridges are fixed to their creation block size.
        # Close them before reconfiguration instead of letting the callback hit
        # a stale bridge. The UI controller reloads persisted chains afterward.
        if hasattr(engine, "plugin_chains"):
            engine.plugin_chains.close()
        result = original_configure_blocksize(engine, frames)
        engine.plugin_chain_delays = RoutingDelayBank(engine.blocksize)
        refresh_latency(engine)
        return result

    Engine.__init__ = init
    Engine.configure_blocksize = configure_blocksize
    Engine.prepare_plugin_chain_latency = refresh_latency
    Engine._pro_plugin_chains_installed = True


def build_live_chain(specifications, sample_rate: int, blocksize: int):
    return live_plugin_chain(specifications, sample_rate, blocksize)
