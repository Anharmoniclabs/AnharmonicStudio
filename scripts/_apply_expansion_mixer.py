from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1))


replace_once(
    "mpclab/engine_mixing.py",
    '''    master = engine._master[:frames]\n    master.fill(0.0)\n    preview_bus = engine._preview_bus[:frames]\n''',
    '''    pro_graph = getattr(engine, "pro_audio_graph", None)\n    high_precision = pro_graph is not None and pro_graph.precision == "float64"\n    if high_precision:\n        master = pro_graph.accumulator.clear(frames)\n    else:\n        master = engine._master[:frames]\n        master.fill(0.0)\n    preview_bus = engine._preview_bus[:frames]\n''',
    "float64 master boundary",
)

replace_once(
    "mpclab/engine_mixing.py",
    '''    external_bus = getattr(engine, "_external_instrument", None)\n    dry_pdc = getattr(engine, "plugin_pdc", None)\n    instrument_pdc = getattr(engine, "instrument_pdc", None)\n''',
    '''    profiler = getattr(engine, "performance_profiler", None)\n    external_bus = getattr(engine, "_external_instrument", None)\n    dry_pdc = getattr(engine, "plugin_pdc", None)\n    instrument_pdc = getattr(engine, "instrument_pdc", None)\n''',
    "profiler handle",
)

replace_once(
    "mpclab/engine_mixing.py",
    '''    for instrument_id, plugin, target_track in hosted:\n        prism_parameters = {}\n''',
    '''    for instrument_id, plugin, target_track in hosted:\n        instrument_metric = f"instrument:{instrument_id or 'legacy'}"\n        if profiler is not None:\n            profiler.register(instrument_metric)\n            instrument_started = profiler.begin(instrument_metric)\n        else:\n            instrument_started = None\n        prism_parameters = {}\n''',
    "instrument profiler begin",
)

replace_once(
    "mpclab/engine_mixing.py",
    '''        if external_bus is None:\n            engine.external.render_instrument(\n                tbuf[target_track],\n                frames,\n                engine.sr,\n                proj.bpm,\n                prism_parameters,\n                instrument_id=instrument_id,\n            )\n            continue\n        external = external_bus[:frames]\n''',
    '''        if external_bus is None:\n            engine.external.render_instrument(\n                tbuf[target_track],\n                frames,\n                engine.sr,\n                proj.bpm,\n                prism_parameters,\n                instrument_id=instrument_id,\n            )\n            if profiler is not None:\n                profiler.end(instrument_metric, instrument_started)\n            continue\n        external = external_bus[:frames]\n''',
    "instrument direct profiler end",
)

replace_once(
    "mpclab/engine_mixing.py",
    '''        if instrument_pdc is not None:\n            instrument_pdc.process(instrument_id, external, frames)\n        np.add(tbuf[target_track], external, out=tbuf[target_track])\n\n    # 4 ─ inserts → track buses → arbitrary routing → sends → master\n''',
    '''        if instrument_pdc is not None:\n            instrument_pdc.process(instrument_id, external, frames)\n        np.add(tbuf[target_track], external, out=tbuf[target_track])\n        if profiler is not None:\n            profiler.end(instrument_metric, instrument_started)\n\n    # 4 ─ inserts → track buses → arbitrary routing → sends → master\n''',
    "instrument profiler end",
)

replace_once(
    "mpclab/engine_mixing.py",
    '''    if routed:\n        clear_bus_buffers(routing_plan, routing_buses, frames)\n\n    for i in range(track_count):\n        t = proj.tracks[i]\n''',
    '''    if routed:\n        clear_bus_buffers(routing_plan, routing_buses, frames)\n    sidechains = getattr(engine, "sidechains", None)\n    if sidechains is not None:\n        sidechains.begin_block(engine._trace_frame, frames)\n        track_order = sidechains.track_order()\n    else:\n        track_order = range(track_count)\n\n    for i in track_order:\n        t = proj.tracks[i]\n        track_metric = f"track:{t.id}"\n        track_started = profiler.begin(track_metric) if profiler is not None else None\n''',
    "track dependency/profiler order",
)

replace_once(
    "mpclab/engine_mixing.py",
    '''        if chains is not None:\n            chains.render(f"track:{t.id}", buf)\n        if g <= 0.0:\n            engine.meters[i] = 0.0\n            engine.peaks[i] = 0.0\n            continue\n''',
    '''        if chains is not None:\n            chains.render(f"track:{t.id}", buf)\n        if sidechains is not None:\n            sidechains.capture(f"track:{t.id}", buf, frames, pre_fader=True)\n        if g <= 0.0:\n            engine.meters[i] = 0.0\n            engine.peaks[i] = 0.0\n            if profiler is not None:\n                profiler.end(track_metric, track_started)\n            continue\n''',
    "pre-fader sidechain capture",
)

replace_once(
    "mpclab/engine_mixing.py",
    '''            engine.peaks[i] = float(np.max(meter_scratch))\n        if routed:\n''',
    '''            engine.peaks[i] = float(np.max(meter_scratch))\n        if sidechains is not None:\n            sidechains.capture(f"track:{t.id}", bus, frames, pre_fader=False)\n        if routed:\n''',
    "post-fader sidechain capture",
)

replace_once(
    "mpclab/engine_mixing.py",
    '''            if t.fx.send_reverb > 1e-4:\n                np.multiply(bus, np.float32(t.fx.send_reverb), out=send_scratch)\n                np.add(reverb_send, send_scratch, out=reverb_send)\n\n    if routed:\n''',
    '''            if t.fx.send_reverb > 1e-4:\n                np.multiply(bus, np.float32(t.fx.send_reverb), out=send_scratch)\n                np.add(reverb_send, send_scratch, out=reverb_send)\n        if profiler is not None:\n            profiler.end(track_metric, track_started)\n\n    if routed:\n''',
    "track profiler end",
)

replace_once(
    "mpclab/engine_mixing.py",
    '''    if run_sends:\n        if proj.delay_fx.enabled:\n            master += rack.delay.process(delay_send, proj.delay_fx, proj.bpm)\n        if proj.reverb_fx.enabled:\n            master += rack.reverb.process(reverb_send, proj.reverb_fx)\n\n    # Master tone and glue sit ahead of the fader, so riding the fader\n''',
    '''    if run_sends:\n        if proj.delay_fx.enabled:\n            master += rack.delay.process(delay_send, proj.delay_fx, proj.bpm)\n        if proj.reverb_fx.enabled:\n            master += rack.reverb.process(reverb_send, proj.reverb_fx)\n\n    if high_precision:\n        float_master = engine._master[:frames]\n        np.copyto(float_master, master, casting="unsafe")\n        master = float_master\n\n    # Master tone and glue sit ahead of the fader, so riding the fader\n''',
    "float64 output boundary",
)
