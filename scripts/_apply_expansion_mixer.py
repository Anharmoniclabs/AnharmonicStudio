from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1))


# ── Realtime mixer: profiler, sidechain dependency order, float64 master ──
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

# ── Offline renderer: stable instruments, true aux audio, same PDC, precision ──
replace_once(
    "mpclab/engine_offline.py",
    '''from .plugin_latency import PluginDelayCompensator, plugin_path_latency_samples\n''',
    '''from .plugin_latency import (\n    PluginDelayCompensator,\n    StereoDelayCompensator,\n    plugin_path_latency_samples,\n)\nfrom .offline_owned_instruments import OfflineOwnedInstruments\nfrom .sidechain import SidechainRouter\n''',
    "offline expansion imports",
)
replace_once(
    "mpclab/engine_offline.py",
    '''    plugins = None\n    chains = None\n    try:\n''',
    '''    plugins = None\n    chains = None\n    owned = None\n    try:\n''',
    "offline owned lifetime",
)
replace_once(
    "mpclab/engine_offline.py",
    '''        synth_voices = []\n        next_synth = 0\n        if proj.plugins:\n''',
    '''        synth_voices = []\n        next_synth = 0\n        if proj.plugins:\n''',
    "offline synth marker",
)
replace_once(
    "mpclab/engine_offline.py",
    '''                plugins.events.sort(key=lambda event: event[0])\n        voices: list[tuple[int, PadVoice]] = []\n''',
    '''                plugins.events.sort(key=lambda event: event[0])\n        owned = OfflineOwnedInstruments(engine, proj, synth_events, control_events)\n        voices: list[tuple[int, PadVoice]] = []\n''',
    "open stable offline instruments",
)
replace_once(
    "mpclab/engine_offline.py",
    '''        plugin_pdc = PluginDelayCompensator(track_count, blocksize)\n        if plugins is not None and plugins.instrument is not None:\n            plugin_pdc.configure(\n                plugin_path_latency_samples(plugins.instrument, include_live_bridge=False)\n            )\n        chains = OfflinePluginChains(proj, engine.sr)\n''',
    '''        plugin_pdc = PluginDelayCompensator(track_count, blocksize)\n        legacy_latency = (\n            plugin_path_latency_samples(plugins.instrument, include_live_bridge=False)\n            if plugins is not None and plugins.instrument is not None\n            else 0\n        )\n        global_latency = max(legacy_latency, owned.max_latency if owned is not None else 0)\n        plugin_pdc.configure(global_latency)\n        legacy_extra = StereoDelayCompensator(blocksize)\n        legacy_extra.configure(max(0, global_latency - legacy_latency), blocksize)\n        if owned is not None:\n            for instrument_id, plugin in owned.plugins.items():\n                latency = plugin_path_latency_samples(plugin, include_live_bridge=False)\n                owned.delays[instrument_id].configure(max(0, global_latency - latency), blocksize)\n        chains = OfflinePluginChains(proj, engine.sr)\n        sidechains = SidechainRouter(proj, blocksize)\n        chains._sidechain_router = sidechains\n''',
    "global offline instrument pdc",
)
replace_once(
    "mpclab/engine_offline.py",
    '''            while next_synth < len(synth_events) and synth_events[next_synth][0] < stop:\n                at, pitch, velocity, gate, instrument_id, channel, sequence_id = synth_events[\n                    next_synth\n                ]\n                if instrument_id is not None or plugins is None or plugins.instrument is None:\n                    engine._spawn_synth(\n''',
    '''            while next_synth < len(synth_events) and synth_events[next_synth][0] < stop:\n                at, pitch, velocity, gate, instrument_id, channel, sequence_id = synth_events[\n                    next_synth\n                ]\n                owned_id = owned is not None and instrument_id in owned.plugins\n                if not owned_id and (\n                    instrument_id is not None or plugins is None or plugins.instrument is None\n                ):\n                    engine._spawn_synth(\n''',
    "skip stable instruments in native synth scheduler",
)
replace_once(
    "mpclab/engine_offline.py",
    '''            synth_voices[:] = [voice for voice in synth_voices if not voice.dead]\n            if plugins is not None and plugins.instrument is not None:\n                external_block = external[:frames]\n''',
    '''            synth_voices[:] = [voice for voice in synth_voices if not voice.dead]\n            if global_latency > 0:\n                plugin_pdc.process(tracks, frames)\n            if plugins is not None and plugins.instrument is not None:\n                external_block = external[:frames]\n''',
    "offline dry pdc before hosted paths",
)
replace_once(
    "mpclab/engine_offline.py",
    '''                plugins.render_instrument(external_block, start, frames, parameters)\n                if plugin_pdc.delay_samples > 0:\n                    plugin_pdc.process(tracks, frames)\n                synth_track = proj.validate_track_index(proj.synth.track, "synth output")\n                np.add(tracks[synth_track], external_block, out=tracks[synth_track])\n\n            automation_beats = engine._automation_beats(mode, start / (spb * engine.sr), frames)\n''',
    '''                plugins.render_instrument(external_block, start, frames, parameters)\n                legacy_extra.process(external_block, frames)\n                synth_track = proj.validate_track_index(proj.synth.track, "synth output")\n                np.add(tracks[synth_track], external_block, out=tracks[synth_track])\n            if owned is not None:\n                for instrument in proj.instruments:\n                    if instrument.id not in owned.plugins:\n                        continue\n                    target = proj.validate_track_index(\n                        instrument.patch.track, "instrument output"\n                    )\n                    owned.render(instrument.id, tracks[target], start, frames)\n\n            automation_beats = engine._automation_beats(mode, start / (spb * engine.sr), frames)\n''',
    "render stable offline instruments",
)
replace_once(
    "mpclab/engine_offline.py",
    '''            delay_send = reverb_send = None\n            if run_sends:\n                delay_send, reverb_send = rack.send_buffers(frames)\n            for i in range(track_count):\n                track = proj.tracks[i]\n''',
    '''            delay_send = reverb_send = None\n            if run_sends:\n                delay_send, reverb_send = rack.send_buffers(frames)\n            sidechains.begin_block(start, frames)\n            for i in sidechains.track_order():\n                track = proj.tracks[i]\n''',
    "offline sidechain dependency order",
)
replace_once(
    "mpclab/engine_offline.py",
    '''                if fx.active:\n                    rack.tracks[i].process(buf, fx)\n                chains.render(f"track:{track.id}", buf)\n                np.multiply(buf[:, 0], left, out=panned[:frames, 0])\n''',
    '''                if fx.active:\n                    rack.tracks[i].process(buf, fx)\n                chains.render(f"track:{track.id}", buf)\n                sidechains.capture(f"track:{track.id}", buf, frames, pre_fader=True)\n                np.multiply(buf[:, 0], left, out=panned[:frames, 0])\n''',
    "offline pre-fader sidechain capture",
)
replace_once(
    "mpclab/engine_offline.py",
    '''                np.multiply(buf[:, 0], left, out=panned[:frames, 0])\n                np.multiply(buf[:, 1], right, out=panned[:frames, 1])\n                route_track(\n''',
    '''                np.multiply(buf[:, 0], left, out=panned[:frames, 0])\n                np.multiply(buf[:, 1], right, out=panned[:frames, 1])\n                sidechains.capture(\n                    f"track:{track.id}", panned[:frames], frames, pre_fader=False\n                )\n                route_track(\n''',
    "offline post-fader sidechain capture",
)
replace_once(
    "mpclab/engine_offline.py",
    '''    finally:\n        if chains is not None:\n            chains.close()\n        engine.mode = saved_mode\n''',
    '''    finally:\n        if chains is not None:\n            chains.close()\n        if owned is not None:\n            owned.close()\n        engine.mode = saved_mode\n''',
    "offline owned cleanup",
)

# Vector noise source stays deterministic and block-vectorized.
replace_once(
    "mpclab/expansion_instruments.py",
    '''    if shape == "triangle":\n        return 1.0 - 4.0 * np.abs(cycle - 0.5)\n    raise ValueError(f"unsupported oscillator source: {shape}")\n''',
    '''    if shape == "triangle":\n        return 1.0 - 4.0 * np.abs(cycle - 0.5)\n    if shape == "noise":\n        hashed = np.sin((wrapped + 0.17320508075688773) * 12.9898) * 43758.5453\n        return 2.0 * (hashed - np.floor(hashed)) - 1.0\n    raise ValueError(f"unsupported oscillator source: {shape}")\n''',
    "vector noise source",
)
