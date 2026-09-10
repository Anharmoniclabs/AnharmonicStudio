"""Shared bounded offline mixdown and its original reference renderer.

Engine owns the mutable state and device/plugin lifecycle. These functions take
that coordinator explicitly and never create a second engine or audio stream.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from .audio_kernel import MasteringKernel
from .engine_constants import FADE
from .external_dsp import OfflinePlugins
from .fx import MixRack
from .model import NPADS
from .music import automation_values
from .plugin_chain_runtime import OfflinePluginChains, RoutingDelayBank, compile_chain_latency_plan
from .plugin_latency import PluginDelayCompensator, plugin_path_latency_samples
from .sample_voice import PadRenderWorkspace, PadVoice, _balance_gains
from .workflow_routing import (
    MAX_ROUTING_BUSES,
    clear_bus_buffers,
    compile_routing,
    finish_buses,
    route_track,
)

if TYPE_CHECKING:
    from .engine import Engine


def render_offline_reference(
    engine: Engine,
    mode: str = "song",
    repeats: int = 1,
    tail: float = 2.5,
    progress=None,
    *,
    voice_chunk: int,
) -> np.ndarray:
    """Original whole-session renderer retained for equivalence tests."""
    # Offline rendering is allowed to load assets; the live callback is not.
    engine.preload_project_audio()
    proj = engine.project
    proj._validate_track_ids()
    proj._validate_track_references()
    track_count = len(proj.tracks)
    spb = 60.0 / proj.bpm
    if mode == "song":
        length_beats = proj.song_end()
    else:
        length_beats = proj.pattern().length_beats * max(1, repeats)
    if length_beats <= 0:
        raise ValueError("nothing to render — place some clips or steps first")

    total = int((length_beats * spb + tail) * engine.sr)
    out = np.zeros((total, 2), dtype=np.float32)
    tbuf = np.zeros((track_count, total, 2), dtype=np.float32)

    saved_mode, engine.mode = engine.mode, mode
    chains = None
    try:
        notes, audio = engine._collect(0.0, length_beats)
        voices: list[tuple[int, PadVoice]] = []
        for event in notes:
            item = engine._offline_pad_event(*event, spb)
            if item is not None:
                voices.append(item)
        for clip in audio:
            data, s0, s1 = engine._audio_clip_source(clip)
            if data is None:
                continue
            if s1 - s0 < 8:
                continue
            arranged = max(1, int(clip.length_beats * spb * engine.sr))
            voice_length = arranged if clip.loop else min(arranged, s1 - s0)
            pl, pr = _balance_gains(0.0)
            voices.append(
                (
                    int(clip.start_beat * spb * engine.sr),
                    PadVoice(
                        data=data,
                        source_id=clip.ref,
                        s0=s0,
                        s1=s1,
                        rate=1.0,
                        gain=float(clip.gain),
                        pan_l=pl,
                        pan_r=pr,
                        attack=max(1, int(0.003 * engine.sr)),
                        release=max(1, int(0.008 * engine.sr)),
                        length=voice_length,
                        track=proj.validate_track_index(clip.track, "clip output"),
                        choke=0,
                        pad_index=-1,
                        loop=bool(clip.loop),
                        loop_crossfade=max(0, int(clip.loop_crossfade * engine.sr)),
                        quality="offline",
                    ),
                )
            )

        # Apply the same choke/source-stealing decisions as the live callback.
        voices.sort(key=lambda item: item[0])
        fade = max(1, int(FADE * engine.sr))
        for index, (at, voice) in enumerate(voices):
            if not (0 <= voice.pad_index < len(proj.pads)):
                continue
            pad = proj.pads[voice.pad_index]
            for previous_at, previous in voices[:index]:
                if engine._pad_trigger_cuts(previous, voice, pad, voice.pad_index):
                    elapsed = max(0, at - previous_at)
                    previous.length = min(previous.length, elapsed + fade)
                    previous.release = fade

        voice_workspace = PadRenderWorkspace(min(voice_chunk, max(1, total)))
        for n, (at, voice) in enumerate(voices):
            room = total - at
            if room <= 0:
                continue
            voice.length = min(voice.length, room)
            rendered = 0
            while rendered < room and not voice.dead:
                take = min(voice_chunk, room - rendered)
                start = at + rendered
                voice.render(tbuf[voice.track][start : start + take], 0, voice_workspace)
                rendered += take
            if progress and n % 32 == 0:
                progress(n / max(1, len(voices)))

        any_solo = proj.any_solo()
        gains = []
        for i in range(track_count):
            track = proj.tracks[i]
            gain = 0.0 if (track.mute or (any_solo and not track.solo)) else track.gain
            left, right = _balance_gains(track.pan)
            gains.append((gain * left, gain * right, gain > 0.0))

        rack = MixRack(track_count)
        rack.prepare(engine.blocksize)
        kernel = MasteringKernel(engine.sr, blocksize=engine.blocksize)
        run_sends = any(track.fx.sends_active for track in proj.tracks) and (
            proj.delay_fx.enabled or proj.reverb_fx.enabled
        )
        routing_plan = compile_routing(proj)
        routing_buses = np.zeros((MAX_ROUTING_BUSES, engine.blocksize, 2), dtype=np.float32)
        panned = np.zeros((engine.blocksize, 2), dtype=np.float32)
        routing_scratch = np.zeros((engine.blocksize, 2), dtype=np.float32)
        chains = OfflinePluginChains(proj, engine.sr)
        chain_delays = RoutingDelayBank(engine.blocksize)
        chain_delays.configure(compile_chain_latency_plan(routing_plan, chains.latencies()))
        for start in range(0, total, engine.blocksize):
            stop = min(total, start + engine.blocksize)
            frames = stop - start
            block = out[start:stop]
            clear_bus_buffers(routing_plan, routing_buses, frames)
            delay_send = reverb_send = None
            if run_sends:
                delay_send, reverb_send = rack.send_buffers(frames)
            for i in range(track_count):
                left, right, live = gains[i]
                if not live:
                    continue
                buf = tbuf[i][start:stop]
                fx = proj.tracks[i].fx
                if fx.active:
                    rack.tracks[i].process(buf, fx)
                chains.render(f"track:{proj.tracks[i].id}", buf)
                np.multiply(buf[:, 0], left, out=panned[:frames, 0])
                np.multiply(buf[:, 1], right, out=panned[:frames, 1])
                route_track(
                    routing_plan,
                    i,
                    buf,
                    panned[:frames],
                    block,
                    routing_buses,
                    routing_scratch,
                    chain_delays,
                )
                if run_sends and fx.sends_active:
                    if fx.send_delay > 1e-4:
                        delay_send += panned[:frames] * np.float32(fx.send_delay)
                    if fx.send_reverb > 1e-4:
                        reverb_send += panned[:frames] * np.float32(fx.send_reverb)
            finish_buses(
                routing_plan,
                block,
                routing_buses,
                routing_scratch,
                frames,
                chain_delays,
                chains.render,
            )
            if run_sends:
                if proj.delay_fx.enabled:
                    block += rack.delay.process(delay_send, proj.delay_fx, proj.bpm)
                if proj.reverb_fx.enabled:
                    block += rack.reverb.process(reverb_send, proj.reverb_fx)
            if proj.master_fx.active:
                rack.master.process(block, proj.master_fx)
            chains.render("master", block)
            block *= proj.master
            kernel.process(block)
    finally:
        if chains is not None:
            chains.close()
        engine.mode = saved_mode
    if progress:
        progress(1.0)
    return out


def offline_pad_event(engine: Engine, beat, index, velocity, gate, sequence_id, spb):
    pitch = None
    if index >= NPADS:
        index, pitch = divmod(index - NPADS, 128)
    if not 0 <= index < len(engine.project.pads):
        return None
    pad = engine.project.pads[index]
    voice = engine._voice_for_pad(pad, velocity, pitch)
    if voice is None:
        return None
    voice.pad_index, voice.sequence_id = index, sequence_id
    voice.quality = "offline"
    if pitch is not None and voice.gated:
        voice.length = min(voice.length, max(1, int(gate * spb * engine.sr)) + voice.release)
    frame = round(beat * spb * engine.sr) if pitch is not None else int(beat * spb * engine.sr)
    return frame, voice


def offline_frame_count(engine: Engine, mode: str, repeats: int, tail: float) -> int:
    proj = engine.project
    spb = 60.0 / proj.bpm
    length_beats = (
        proj.song_end() if mode == "song" else proj.pattern().length_beats * max(1, repeats)
    )
    if length_beats <= 0:
        raise ValueError("nothing to render — place some clips or steps first")
    return int((length_beats * spb + tail) * engine.sr)


def iter_offline_blocks(
    engine: Engine, mode: str = "song", repeats: int = 1, tail: float = 2.5, progress=None
):
    """Yield a mixdown in bounded float32 blocks.

    The caller must consume or copy each block before requesting the next.
    Each yielded value is already an independent array, so file writers can
    pass it straight to ``SoundFile.write``. Memory is bounded by events,
    active voices and ``len(project.tracks) * blocksize`` rather than song duration.
    """
    engine.preload_project_audio()
    proj = engine.project
    proj._validate_track_ids()
    proj._validate_track_references()
    track_count = len(proj.tracks)
    spb = 60.0 / proj.bpm
    length_beats = (
        proj.song_end() if mode == "song" else proj.pattern().length_beats * max(1, repeats)
    )
    total = engine._offline_frame_count(mode, repeats, tail)

    saved_mode, engine.mode = engine.mode, mode
    plugins = None
    chains = None
    try:
        notes, audio = engine._collect(0.0, length_beats)
        synth_events = sorted(
            [
                (
                    round(beat * spb * engine.sr),
                    -idx - 1,
                    vel,
                    max(1, int(gate * spb * engine.sr)),
                )
                for beat, idx, vel, gate, _sequence_id in notes
                if idx < 0
            ],
            key=lambda event: event[0],
        )
        synth_voices = []
        next_synth = 0
        if proj.plugins:
            plugins = OfflinePlugins(proj.plugins, engine.sr, synth_events)
        voices: list[tuple[int, PadVoice]] = []
        for event in notes:
            item = engine._offline_pad_event(*event, spb)
            if item is not None:
                voices.append(item)
        for clip in audio:
            data, s0, s1 = engine._audio_clip_source(clip)
            if data is None or s1 - s0 < 8:
                continue
            arranged = max(1, int(clip.length_beats * spb * engine.sr))
            voice_length = arranged if clip.loop else min(arranged, s1 - s0)
            left, right = _balance_gains(0.0)
            voices.append(
                (
                    int(clip.start_beat * spb * engine.sr),
                    PadVoice(
                        data=data,
                        source_id=clip.ref,
                        s0=s0,
                        s1=s1,
                        rate=1.0,
                        gain=float(clip.gain),
                        pan_l=left,
                        pan_r=right,
                        attack=max(1, int(0.003 * engine.sr)),
                        release=max(1, int(0.008 * engine.sr)),
                        length=voice_length,
                        track=proj.validate_track_index(clip.track, "clip output"),
                        choke=0,
                        pad_index=-1,
                        loop=bool(clip.loop),
                        loop_crossfade=max(0, int(clip.loop_crossfade * engine.sr)),
                        quality="offline",
                    ),
                )
            )

        voices.sort(key=lambda item: item[0])
        fade = max(1, int(FADE * engine.sr))
        for index, (at, voice) in enumerate(voices):
            if not (0 <= voice.pad_index < len(proj.pads)):
                continue
            pad = proj.pads[voice.pad_index]
            for previous_index in range(index):
                previous_at, previous = voices[previous_index]
                if engine._pad_trigger_cuts(previous, voice, pad, voice.pad_index):
                    elapsed = max(0, at - previous_at)
                    previous.length = min(previous.length, elapsed + fade)
                    previous.release = fade

        blocksize = engine.blocksize
        tbuf = np.zeros((track_count, blocksize, 2), dtype=np.float32)
        output = np.zeros((blocksize, 2), dtype=np.float32)
        panned = np.zeros((blocksize, 2), dtype=np.float32)
        routing_scratch = np.zeros((blocksize, 2), dtype=np.float32)
        routing_buses = np.zeros((MAX_ROUTING_BUSES, blocksize, 2), dtype=np.float32)
        external = np.zeros((blocksize, 2), dtype=np.float32)
        routing_plan = compile_routing(proj)
        plugin_pdc = PluginDelayCompensator(track_count, blocksize)
        if plugins is not None and plugins.instrument is not None:
            plugin_pdc.configure(
                plugin_path_latency_samples(plugins.instrument, include_live_bridge=False)
            )
        chains = OfflinePluginChains(proj, engine.sr)
        chain_delays = RoutingDelayBank(blocksize)
        chain_delays.configure(compile_chain_latency_plan(routing_plan, chains.latencies()))
        voice_workspace = PadRenderWorkspace(blocksize)
        active: list[tuple[int, PadVoice]] = []
        next_voice = 0

        any_solo = proj.any_solo()
        rack = MixRack(track_count)
        rack.prepare(blocksize)
        kernel = MasteringKernel(engine.sr, blocksize=blocksize)
        run_sends = any(track.fx.sends_active for track in proj.tracks) and (
            proj.delay_fx.enabled or proj.reverb_fx.enabled
        )

        for start in range(0, total, blocksize):
            stop = min(total, start + blocksize)
            frames = stop - start
            tracks = tbuf[:, :frames]
            tracks.fill(0.0)
            block = output[:frames]
            block.fill(0.0)
            clear_bus_buffers(routing_plan, routing_buses, frames)

            while next_voice < len(voices) and voices[next_voice][0] < stop:
                at, voice = voices[next_voice]
                voice.length = min(voice.length, total - at)
                active.append((at, voice))
                next_voice += 1
            for at, voice in active:
                offset = max(0, at - start)
                voice.render(tracks[voice.track], offset, voice_workspace)
            for index in range(len(active) - 1, -1, -1):
                if active[index][1].dead:
                    del active[index]

            while next_synth < len(synth_events) and synth_events[next_synth][0] < stop:
                at, pitch, velocity, gate = synth_events[next_synth]
                if plugins is None or plugins.instrument is None:
                    engine._spawn_synth(
                        pitch,
                        velocity,
                        max(0, at - start),
                        gate,
                        voices=synth_voices,
                        live_trigger=False,
                    )
                next_synth += 1
            for voice in synth_voices:
                voice.render(tracks[voice.track], proj.synth)
            synth_voices[:] = [voice for voice in synth_voices if not voice.dead]
            if plugins is not None and plugins.instrument is not None:
                external_block = external[:frames]
                external_block.fill(0.0)
                plugins.render_instrument(external_block, start, frames)
                if plugin_pdc.delay_samples > 0:
                    plugin_pdc.process(tracks, frames)
                synth_track = proj.validate_track_index(proj.synth.track, "synth output")
                np.add(tracks[synth_track], external_block, out=tracks[synth_track])

            automation_beats = engine._automation_beats(mode, start / (spb * engine.sr), frames)
            delay_send = reverb_send = None
            if run_sends:
                delay_send, reverb_send = rack.send_buffers(frames)
            for i in range(track_count):
                track = proj.tracks[i]
                left, right = engine._track_controls(i, automation_beats)
                if track.mute or (any_solo and not track.solo):
                    continue
                buf = tracks[i]
                fx = track.fx
                if fx.active:
                    rack.tracks[i].process(buf, fx)
                chains.render(f"track:{track.id}", buf)
                np.multiply(buf[:, 0], left, out=panned[:frames, 0])
                np.multiply(buf[:, 1], right, out=panned[:frames, 1])
                route_track(
                    routing_plan,
                    i,
                    buf,
                    panned[:frames],
                    block,
                    routing_buses,
                    routing_scratch,
                    chain_delays,
                )
                if run_sends and fx.sends_active:
                    if fx.send_delay > 1e-4:
                        delay_send += panned[:frames] * np.float32(fx.send_delay)
                    if fx.send_reverb > 1e-4:
                        reverb_send += panned[:frames] * np.float32(fx.send_reverb)
            finish_buses(
                routing_plan,
                block,
                routing_buses,
                routing_scratch,
                frames,
                chain_delays,
                chains.render,
            )
            if run_sends:
                if proj.delay_fx.enabled:
                    block += rack.delay.process(delay_send, proj.delay_fx, proj.bpm)
                if proj.reverb_fx.enabled:
                    block += rack.reverb.process(reverb_send, proj.reverb_fx)
            if proj.master_fx.active:
                rack.master.process(block, proj.master_fx)
            if plugins is not None:
                plugins.render_effect(block)
            chains.render("master", block)
            gain = (
                proj.master
                if automation_beats is None
                else automation_values(proj, "master", automation_beats, proj.master)
            )
            block *= gain[:, None] if isinstance(gain, np.ndarray) else gain
            kernel.process(block)
            if progress:
                progress(stop / max(1, total))
            yield block.copy()
    finally:
        if plugins is not None:
            plugins.close()
        if chains is not None:
            chains.close()
        engine.mode = saved_mode
    if progress:
        progress(1.0)


def render_offline(
    engine: Engine, mode: str = "song", repeats: int = 1, tail: float = 2.5, progress=None
) -> np.ndarray:
    """Render to memory for API callers; file export uses the block iterator."""
    blocks = list(
        engine.iter_offline_blocks(mode=mode, repeats=repeats, tail=tail, progress=progress)
    )
    return np.concatenate(blocks, axis=0)
