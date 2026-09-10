"""Shared block mixing, automation and meters for the realtime callback.

Engine owns the mutable state and device/plugin lifecycle. These functions take
that coordinator explicitly and never create a second engine or audio stream.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from .engine_constants import AUDITION, METRONOME, SEND_TAIL, TRACK_DSP_TAIL
from .model import NPADS
from .music import automation_values
from .sample_voice import _balance_gains
from .workflow_routing import clear_bus_buffers, finish_buses, route_track

if TYPE_CHECKING:
    from .engine import Engine


def automation_beats(engine: Engine, mode, start, frames):
    if mode != "song" or not any(a.enabled and a.points for a in engine.project.automation):
        return None
    return start + np.arange(frames) * (engine.project.bpm / 60.0 / engine.sr)


def track_controls(engine: Engine, index, beats):
    track = engine.project.tracks[index]
    if beats is None:
        pl, pr = _balance_gains(track.pan)
        return track.gain * pl, track.gain * pr
    gain = automation_values(engine.project, f"track:{index}:gain", beats, track.gain)
    pan = automation_values(engine.project, f"track:{index}:pan", beats, track.pan)
    return (
        gain * np.cos(np.maximum(pan, 0) * np.pi * 0.5),
        gain * np.cos(np.minimum(pan, 0) * np.pi * 0.5),
    )


def render_block(engine: Engine, outdata, frames, monitor=None):
    proj = engine.project
    track_count = len(proj.tracks)
    if track_count != engine._tbuf.shape[0] or track_count != len(engine.rack.tracks):
        raise RuntimeError("mixer layout changed: stop audio and call prepare_fx before playback")
    start_beat = engine.beat
    if frames > engine._tbuf.shape[1]:  # PortAudio asked for a bigger block
        engine._tbuf = np.zeros((track_count, frames, 2), dtype=np.float32)
        engine._master = np.zeros((frames, 2), dtype=np.float32)
        engine._preview = np.zeros((frames, 2), dtype=np.float32)
        engine._send_scratch = np.zeros((frames, 2), dtype=np.float32)
        engine._meter_scratch = np.zeros((frames, 2), dtype=np.float32)
        if hasattr(engine, "_routing_buses"):
            engine._routing_buses = np.zeros(
                (engine._routing_buses.shape[0], frames, 2), dtype=np.float32
            )
        if hasattr(engine, "_external_instrument"):
            engine._external_instrument = np.zeros((frames, 2), dtype=np.float32)
        if hasattr(engine, "plugin_pdc"):
            engine.plugin_pdc.ensure_blocksize(frames)
    tbuf = engine._tbuf[:, :frames]
    tbuf.fill(0.0)
    master = engine._master[:frames]
    master.fill(0.0)
    preview_bus = engine._preview[:frames]
    preview_bus.fill(0.0)

    # Automation uses the block start, before transport advances.
    automation_beats = engine._automation_beats(engine.mode, engine.beat, frames)

    # 2 ─ transport
    if engine.playing:
        bps = (proj.bpm / 60.0) / engine.sr
        b0 = engine.beat
        b1 = b0 + frames * bps
        notes, audio = engine._collect(b0, b1, reuse=True)
        for beat, pad_idx, vel, _gate, sequence_id in notes:
            off = int(max(0.0, (beat - b0) / bps))
            if pad_idx < 0:
                engine._spawn_synth(
                    -pad_idx - 1,
                    vel,
                    min(max(0, round((beat - b0) / bps)), frames - 1),
                    max(1, int(_gate / bps)),
                    live_trigger=False,
                )
            elif pad_idx >= NPADS:
                index, pitch = divmod(pad_idx - NPADS, 128)
                engine._spawn(
                    proj.pads[index],
                    index,
                    vel,
                    min(max(0, round((beat - b0) / bps)), frames - 1),
                    max(1, int(_gate / bps)),
                    live_trigger=False,
                    sequence_id=sequence_id,
                    note=pitch,
                )
            elif 0 <= pad_idx < len(proj.pads):
                engine._spawn(
                    proj.pads[pad_idx],
                    pad_idx,
                    vel,
                    min(off, frames - 1),
                    live_trigger=False,
                    sequence_id=sequence_id,
                )
        for clip in audio:
            off = int(max(0.0, (clip.start_beat - b0) / bps))
            engine._spawn_audio_clip(clip, min(off, frames - 1))
        if engine.mode == "song" and engine._resume_audio:
            for clip, elapsed in engine._audio_overlaps(b0):
                engine._spawn_audio_clip(clip, 0, elapsed)
            engine._resume_audio = False
        if engine.metronome:
            for b in range(int(np.ceil(b0 - 1e-9)), int(np.ceil(b1 - 1e-9))):
                engine._click(int(max(0.0, (b - b0) / bps)), b % 4 == 0)
        engine.beat = b1
    # 3 ─ voices
    engine._schedule_arp(frames, start_beat)
    preview = None
    for v in engine.voices:
        destination = preview_bus if v.pad_index in (AUDITION, METRONOME) else tbuf[v.track]
        v.render(destination, v.start_offset, engine._pad_workspace)
        v.start_offset = 0
        if not v.dead and v.pad_index == AUDITION:
            preview = v
    for index in range(len(engine.voices) - 1, -1, -1):
        if engine.voices[index].dead:
            del engine.voices[index]
    if preview is None:
        engine.audition_time = None
    else:
        travelled = preview.age * preview.rate
        span = max(1, preview.s1 - preview.s0)
        if preview.loop:
            travelled %= span
        engine.audition_time = (preview.s0 + travelled) / engine.sr
    for voice in engine.synth_voices:
        voice.render(tbuf[voice.track], proj.synth)
    for index in range(len(engine.synth_voices) - 1, -1, -1):
        if engine.synth_voices[index].dead:
            del engine.synth_voices[index]

    synth_track = proj.validate_track_index(proj.synth.track, "synth output")
    external_bus = getattr(engine, "_external_instrument", None)
    if external_bus is None:
        engine.external.render_instrument(tbuf[synth_track], frames, engine.sr)
    else:
        external = external_bus[:frames]
        external.fill(0.0)
        engine.external.render_instrument(external, frames, engine.sr)
        pdc = getattr(engine, "plugin_pdc", None)
        if engine.external.instrument is not None and pdc is not None and pdc.delay_samples > 0:
            pdc.process(tbuf, frames)
        np.add(tbuf[synth_track], external, out=tbuf[synth_track])

    # 4 ─ inserts → track buses → arbitrary routing → sends → master
    any_solo = False
    for track in proj.tracks:
        if track.solo:
            any_solo = True
            break
    rack = engine.rack
    chains = getattr(engine, "plugin_chains", None)
    chain_delays = getattr(engine, "plugin_chain_delays", None)
    send_has_input = False
    for i, track in enumerate(proj.tracks):
        if (
            track.fx.sends_active
            and not (track.mute or (any_solo and not track.solo))
            and bool(np.any(tbuf[i]))
        ):
            send_has_input = True
            break
    if send_has_input:
        engine._send_tail = int(SEND_TAIL * engine.sr)
    else:
        engine._send_tail = max(0, engine._send_tail - frames)
    # Keep the sends running past the last note so their tails ring out
    # instead of being cut the moment a send knob reaches zero.
    run_sends = (send_has_input or engine._send_tail > 0) and (
        proj.delay_fx.enabled or proj.reverb_fx.enabled
    )
    delay_send = reverb_send = None
    if run_sends:
        delay_send, reverb_send = rack.send_buffers(frames)

    if frames > len(engine._bus):
        engine._bus = np.zeros((frames, 2), dtype=np.float32)
    bus = engine._bus[:frames]
    send_scratch = engine._send_scratch[:frames]
    meter_scratch = engine._meter_scratch[:frames]
    routing_plan = getattr(engine, "_routing_plan", None)
    routing_buses = getattr(engine, "_routing_buses", None)
    routed = routing_plan is not None and routing_buses is not None
    if routed:
        clear_bus_buffers(routing_plan, routing_buses, frames)

    for i in range(track_count):
        t = proj.tracks[i]
        left, right = engine._track_controls(i, automation_beats)
        # Faders are post-insert. A zero fader must not reset filter or
        # compressor history before an automated fade opens again.
        g = 0.0 if (t.mute or (any_solo and not t.solo)) else 1.0
        buf = tbuf[i]
        has_input = g > 0.0 and bool(np.any(buf))
        if g <= 0.0:
            # Muting must drain existing DSP state with silence, not keep
            # feeding hidden voices into the effect chain.
            buf.fill(0.0)
        tailing = engine._track_tail[i] > 0
        # Stateful built-in DSP consumes a finite run of silent blocks, then sleeps.
        if t.fx.active and (has_input or tailing):
            rack.tracks[i].process(buf, t.fx, prepared_only=True)
            if has_input:
                engine._track_tail[i] = int(TRACK_DSP_TAIL * engine.sr)
            else:
                engine._track_tail[i] = max(0, engine._track_tail[i] - frames)
        elif not t.fx.active:
            engine._track_tail[i] = 0
        # External chains are one isolated bridge per whole serial chain. They
        # stay fed with silence while present so third-party reverb/delay tails
        # can drain without ever blocking this callback.
        if chains is not None:
            chains.render(f"track:{t.id}", buf)
        if g <= 0.0:
            engine.meters[i] = 0.0
            engine.peaks[i] = 0.0
            continue
        np.multiply(buf[:, 0], left, out=bus[:, 0])
        np.multiply(buf[:, 1], right, out=bus[:, 1])
        np.square(bus, out=meter_scratch)
        engine.meters[i] = float(np.sqrt(np.mean(meter_scratch)))
        np.abs(bus, out=meter_scratch)
        engine.peaks[i] = float(np.max(meter_scratch))
        if routed:
            route_track(
                routing_plan,
                i,
                buf,
                bus,
                master,
                routing_buses,
                send_scratch,
                chain_delays,
            )
        else:
            master += bus
        if run_sends and t.fx.sends_active:
            if t.fx.send_delay > 1e-4:
                np.multiply(bus, np.float32(t.fx.send_delay), out=send_scratch)
                np.add(delay_send, send_scratch, out=delay_send)
            if t.fx.send_reverb > 1e-4:
                np.multiply(bus, np.float32(t.fx.send_reverb), out=send_scratch)
                np.add(reverb_send, send_scratch, out=reverb_send)

    if routed:
        finish_buses(
            routing_plan,
            master,
            routing_buses,
            send_scratch,
            frames,
            chain_delays,
            chains.render if chains is not None else None,
        )

    if run_sends:
        if proj.delay_fx.enabled:
            master += rack.delay.process(delay_send, proj.delay_fx, proj.bpm)
        if proj.reverb_fx.enabled:
            master += rack.reverb.process(reverb_send, proj.reverb_fx)

    # Master tone and glue sit ahead of the fader, so riding the fader
    # never changes how hard the bus compressor is working.
    if proj.master_fx.active:
        rack.master.process(master, proj.master_fx, prepared_only=True)
    # Preserve the original single master-effect slot for project compatibility;
    # the professional serial chain follows it and can hold up to eight effects.
    engine.external.render_effect(master)
    if chains is not None:
        chains.render("master", master)
    master_gain = (
        proj.master
        if automation_beats is None
        else automation_values(proj, "master", automation_beats, proj.master)
    )
    master *= master_gain[:, None] if isinstance(master_gain, np.ndarray) else master_gain
    # Browser/CHOP preview is an independent cue bus: Track 1 mute, pan and
    # insert choices must never make a source audition disappear or change.
    np.multiply(preview_bus, np.float32(engine.preview_gain), out=send_scratch)
    np.add(master, send_scratch, out=master)
    # The dry microphone cue intentionally bypasses project inserts and
    # the master fader, like a studio interface's monitor path. Consume
    # only the freshest block so recovery from an xrun cannot echo stale
    # speech seconds later.
    if monitor is not None and len(monitor):
        take = min(frames, len(monitor))
        np.add(master[:take], monitor[:take], out=master[:take])
    engine.mastering.process(master)

    np.square(master, out=meter_scratch)
    engine.master_meter[0] = float(np.sqrt(np.mean(meter_scratch[:, 0])))
    engine.master_meter[1] = float(np.sqrt(np.mean(meter_scratch[:, 1])))
    np.abs(master, out=meter_scratch)
    engine.master_peak = float(np.max(meter_scratch))
    outdata[:] = master
