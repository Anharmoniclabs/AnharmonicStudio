"""Compiled project routing graph shared by realtime playback and export.

The core audio engine still owns eight source tracks. Advanced routing lives in
validated workflow metadata and is compiled on the GUI thread into compact,
callback-safe integer targets. The callback only clears fixed scratch buffers
and performs NumPy adds/multiplies; it never walks arbitrary JSON or allocates a
new routing object.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .sample_voice import _balance_gains

MAX_ROUTING_BUSES = 16
MASTER_TARGET = -1
_ENGINE_INSTALLED = False


@dataclass(frozen=True, slots=True)
class SendSpec:
    target: int
    gain: float
    pre_fader: bool = False
    edge: str = ""


@dataclass(frozen=True, slots=True)
class BusSpec:
    id: str
    name: str
    gain: float
    pan: float
    mute: bool
    output: int
    sends: tuple[SendSpec, ...] = ()
    output_edge: str = ""


@dataclass(frozen=True, slots=True)
class RoutingPlan:
    buses: tuple[BusSpec, ...]
    track_outputs: tuple[int, ...]
    track_sends: tuple[tuple[SendSpec, ...], ...]
    track_ids: tuple[str, ...] = ()
    track_output_edges: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return (
            bool(self.buses)
            or any(target != MASTER_TARGET for target in self.track_outputs)
            or any(self.track_sends)
        )

    @classmethod
    def empty(cls, tracks) -> "RoutingPlan":
        if isinstance(tracks, int):
            track_ids = tuple(str(index) for index in range(tracks))
        else:
            track_ids = tuple(str(item) for item in tracks)
        return cls(
            (),
            (MASTER_TARGET,) * len(track_ids),
            ((),) * len(track_ids),
            track_ids,
            tuple(f"track-output:{track_id}" for track_id in track_ids),
        )


def _workflow_routing(project) -> dict:
    workflow = getattr(project, "workflow", None)
    if not isinstance(workflow, dict):
        return {}
    routing = workflow.get("routing", {})
    return routing if isinstance(routing, dict) else {}


def _topological_bus_ids(buses: list[dict], sends: list[dict]) -> list[str]:
    ids = [str(item.get("id", "")) for item in buses]
    bus_ids = set(ids)
    edges = {bus_id: set() for bus_id in ids}
    indegree = dict.fromkeys(ids, 0)

    def connect(source: str, target: str) -> None:
        if source not in bus_ids or target not in bus_ids or target in edges[source]:
            return
        edges[source].add(target)
        indegree[target] += 1

    for item in buses:
        connect(str(item.get("id", "")), str(item.get("output", "master")))
    for item in sends:
        connect(str(item.get("source", "")), str(item.get("target", "master")))

    order_index = {bus_id: index for index, bus_id in enumerate(ids)}
    ready = sorted((bus_id for bus_id in ids if indegree[bus_id] == 0), key=order_index.get)
    ordered: list[str] = []
    while ready:
        source = ready.pop(0)
        ordered.append(source)
        for target in sorted(edges[source], key=order_index.get):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=order_index.get)
    if len(ordered) != len(ids):
        raise ValueError("routing buses must form an acyclic graph")
    return ordered


def compile_routing(project) -> RoutingPlan:
    """Compile persisted routing metadata into callback-safe integer targets."""
    routing = _workflow_routing(project)
    buses = list(routing.get("buses", [])) if isinstance(routing.get("buses", []), list) else []
    sends = list(routing.get("sends", [])) if isinstance(routing.get("sends", []), list) else []
    track_ids = tuple(track.id for track in project.tracks)
    if len(buses) > MAX_ROUTING_BUSES:
        raise ValueError(f"routing supports at most {MAX_ROUTING_BUSES} buses")
    if not buses and not sends and not routing.get("track_outputs"):
        return RoutingPlan.empty(track_ids)

    ordered_ids = _topological_bus_ids(buses, sends)
    raw_by_id = {str(item.get("id", "")): item for item in buses}
    bus_index = {bus_id: index for index, bus_id in enumerate(ordered_ids)}

    def target_index(target: str) -> int:
        return MASTER_TARGET if target == "master" else bus_index[target]

    bus_sends: dict[str, list[SendSpec]] = {bus_id: [] for bus_id in ordered_ids}
    track_by_id = {track.id: index for index, track in enumerate(project.tracks)}
    track_sends: list[list[SendSpec]] = [[] for _ in project.tracks]
    for item in sends:
        if not item.get("enabled", True):
            continue
        source = str(item.get("source", ""))
        target = str(item.get("target", "master"))
        if target != "master" and target not in bus_index:
            continue
        spec = SendSpec(
            target=target_index(target),
            gain=max(0.0, min(2.0, float(item.get("gain", 1.0)))),
            pre_fader=bool(item.get("pre_fader", False)),
            edge=f"send:{item.get('id', '')}",
        )
        if source in bus_sends:
            bus_sends[source].append(spec)
        elif source in track_by_id:
            track_sends[track_by_id[source]].append(spec)

    compiled_buses = []
    for bus_id in ordered_ids:
        item = raw_by_id[bus_id]
        output = str(item.get("output", "master"))
        if output != "master" and output not in bus_index:
            output = "master"
        compiled_buses.append(
            BusSpec(
                id=bus_id,
                name=str(item.get("name", "BUS")),
                gain=max(0.0, min(2.0, float(item.get("gain", 1.0)))),
                pan=max(-1.0, min(1.0, float(item.get("pan", 0.0)))),
                mute=bool(item.get("mute", False)),
                output=target_index(output),
                sends=tuple(bus_sends[bus_id]),
                output_edge=f"bus-output:{bus_id}",
            )
        )

    outputs = [MASTER_TARGET] * len(project.tracks)
    raw_outputs = routing.get("track_outputs", {})
    if isinstance(raw_outputs, dict):
        for track_id, target in raw_outputs.items():
            index = track_by_id.get(str(track_id))
            target = str(target)
            if index is not None and target in bus_index:
                outputs[index] = bus_index[target]

    return RoutingPlan(
        buses=tuple(compiled_buses),
        track_outputs=tuple(outputs),
        track_sends=tuple(tuple(items) for items in track_sends),
        track_ids=track_ids,
        track_output_edges=tuple(f"track-output:{track_id}" for track_id in track_ids),
    )


def clear_bus_buffers(plan: RoutingPlan, buffers: np.ndarray, frames: int) -> None:
    if plan.buses:
        buffers[: len(plan.buses), :frames].fill(0.0)


def _accumulate(
    target: int,
    source: np.ndarray,
    gain: float,
    master: np.ndarray,
    buses: np.ndarray,
    scratch: np.ndarray,
    *,
    edge: str = "",
    delays=None,
) -> None:
    if delays is not None and edge:
        source = delays.process(edge, source)
    destination = master if target == MASTER_TARGET else buses[target, : len(source)]
    if gain == 1.0:
        np.add(destination, source, out=destination)
        return
    np.multiply(source, np.float32(gain), out=scratch[: len(source)])
    np.add(destination, scratch[: len(source)], out=destination)


def route_track(
    plan: RoutingPlan,
    track_index: int,
    pre_fader: np.ndarray,
    post_fader: np.ndarray,
    master: np.ndarray,
    buses: np.ndarray,
    scratch: np.ndarray,
    delays=None,
) -> None:
    """Route one post-insert track to its output plus arbitrary sends."""
    output_edge = (
        plan.track_output_edges[track_index] if track_index < len(plan.track_output_edges) else ""
    )
    _accumulate(
        plan.track_outputs[track_index],
        post_fader,
        1.0,
        master,
        buses,
        scratch,
        edge=output_edge,
        delays=delays,
    )
    for send in plan.track_sends[track_index]:
        source = pre_fader if send.pre_fader else post_fader
        _accumulate(
            send.target,
            source,
            send.gain,
            master,
            buses,
            scratch,
            edge=send.edge,
            delays=delays,
        )


def finish_buses(
    plan: RoutingPlan,
    master: np.ndarray,
    buses: np.ndarray,
    scratch: np.ndarray,
    frames: int,
    delays=None,
    bus_processor=None,
) -> None:
    """Process buses in topological order and route them toward the master."""
    for index, bus in enumerate(plan.buses):
        block = buses[index, :frames]
        # Bus inserts are pre-fader, matching track insert semantics. Running the
        # processor even on silence lets effect tails drain deterministically.
        if bus_processor is not None:
            bus_processor(f"bus:{bus.id}", block)
        for send in bus.sends:
            if send.pre_fader:
                _accumulate(
                    send.target,
                    block,
                    send.gain,
                    master,
                    buses,
                    scratch,
                    edge=send.edge,
                    delays=delays,
                )

        if bus.mute:
            block.fill(0.0)
        else:
            left, right = _balance_gains(bus.pan)
            np.multiply(block[:, 0], np.float32(bus.gain * left), out=block[:, 0])
            np.multiply(block[:, 1], np.float32(bus.gain * right), out=block[:, 1])

        _accumulate(
            bus.output,
            block,
            1.0,
            master,
            buses,
            scratch,
            edge=bus.output_edge,
            delays=delays,
        )
        for send in bus.sends:
            if not send.pre_fader:
                _accumulate(
                    send.target,
                    block,
                    send.gain,
                    master,
                    buses,
                    scratch,
                    edge=send.edge,
                    delays=delays,
                )


def install_engine_routing_extensions() -> None:
    """Install engine scratch/state before MainWindow creates the audio engine."""
    global _ENGINE_INSTALLED
    if _ENGINE_INSTALLED:
        return

    from .engine import Engine
    from .model import NTRACKS
    from .plugin_latency import PluginDelayCompensator, plugin_path_latency_samples

    original_init = Engine.__init__
    original_prepare_fx = Engine.prepare_fx
    original_configure_blocksize = Engine.configure_blocksize

    def allocate(engine) -> None:
        engine._routing_buses = np.zeros((MAX_ROUTING_BUSES, engine.blocksize, 2), dtype=np.float32)
        engine._external_instrument = np.zeros((engine.blocksize, 2), dtype=np.float32)
        if not hasattr(engine, "plugin_pdc"):
            engine.plugin_pdc = PluginDelayCompensator(NTRACKS, engine.blocksize)
        else:
            engine.plugin_pdc.configure(0, engine.blocksize)
        engine._routing_plan = compile_routing(engine.project)
        engine.linux_audio.lock_arrays(
            engine._routing_buses,
            engine._external_instrument,
            engine.plugin_pdc.history,
            engine.plugin_pdc.output,
        )

    def init(engine, *args, **kwargs):
        original_init(engine, *args, **kwargs)
        allocate(engine)

    def prepare_routing(engine, project=None):
        plan = compile_routing(project or engine.project)
        engine._routing_plan = plan
        refresh = getattr(engine, "prepare_plugin_chain_latency", None)
        if refresh is not None:
            refresh()
        return plan

    def prepare_plugin_latency(engine):
        delay = plugin_path_latency_samples(engine.external.instrument, include_live_bridge=True)
        engine.plugin_pdc.configure(delay, engine.blocksize)
        engine.linux_audio.lock_arrays(engine.plugin_pdc.history, engine.plugin_pdc.output)
        return delay

    def prepare_fx(engine, project=None):
        result = original_prepare_fx(engine, project)
        prepare_routing(engine, project)
        return result

    def configure_blocksize(engine, frames):
        result = original_configure_blocksize(engine, frames)
        allocate(engine)
        prepare_plugin_latency(engine)
        return result

    Engine.__init__ = init
    Engine.prepare_routing = prepare_routing
    Engine.prepare_plugin_latency = prepare_plugin_latency
    Engine.prepare_fx = prepare_fx
    Engine.configure_blocksize = configure_blocksize
    _ENGINE_INSTALLED = True
