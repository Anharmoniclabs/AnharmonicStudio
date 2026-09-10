(() => {
  'use strict';

  // The browser owns its audio clock. The UI only paints positions delivered by
  // this engine; both transport and WAV export use the same events and graph.
  const LIMITS = Object.freeze({ voices: 128, events: 50000, renderNodes: 50000, renderPCMBytes: 128 * 1024 * 1024, renderSeconds: 300, sampleRate: 48000 });
  const EPSILON = 1e-8;
  const finite = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const clamp = (value, low, high, fallback = 0) => Math.min(high, Math.max(low, finite(value, fallback)));
  const secondsPerBeat = project => 60 / clamp(project.bpm, 20, 400, 110);
  const patternLength = pattern => Math.max(1, finite(pattern?.bars, 1)) * 4;
  const selectedPattern = project => project.patterns?.[project.selected_pattern || 0] || project.patterns?.[0];
  const audible = (items, item) => !item?.mute && (!items.some(candidate => candidate.solo) || Boolean(item?.solo));
  const db = value => 10 ** (clamp(value, -60, 24, 0) / 20);

  function lengthBeats(project, mode = 'pattern') {
    if (mode !== 'song') return patternLength(selectedPattern(project));
    return (project.rows || []).reduce((end, row) => Math.max(end, ...(row.clips || []).map(clip => finite(clip.start_beat) + finite(clip.length_beats))), 0);
  }

  function swingOffset(project, pattern, step) {
    const division = Math.max(1, finite(pattern.div, 4));
    const perEighth = Math.max(1, Math.floor(division / 2));
    return division >= 2 && Math.floor(step / perEighth) % 2 === 1 ? clamp(project.swing, 0, 100) / 100 / division * .66 : 0;
  }

  // Half-open beat windows prevent duplicate hits at scheduling boundaries.
  function collectEvents(project, mode, from, to, maxEvents = LIMITS.events) {
    if (!(to > from)) return [];
    const result = [];
    const append = event => {
      if (result.length >= maxEvents) throw new Error(`Audio event limit exceeded (${maxEvents}). Reduce the arrangement density.`);
      result.push(event);
    };
    const patternEvents = (pattern, origin, end, gain = 1, row = null, sequence = pattern.id) => {
      const length = patternLength(pattern);
      const division = Math.max(1, finite(pattern.div, 4));
      const totalSteps = length * division;
      const first = Math.max(0, Math.floor((from - origin) / length));
      const last = Math.max(first, Math.floor((Math.min(to, end) - origin) / length));
      // Reject malicious horizons without constructing an unbounded event list.
      if (last - first > maxEvents) throw new Error('Audio scheduling window is too large.');
      for (let cycle = first; cycle <= last; cycle += 1) {
        const base = origin + cycle * length;
        for (const [pad, hits] of Object.entries(pattern.steps || {})) {
          for (const [stepText, value] of Object.entries(hits || {})) {
            const step = Number(stepText); const velocity = clamp(value, 0, 1);
            if (!Number.isInteger(step) || step < 0 || step >= totalSteps || !velocity) continue;
            const beat = base + step / division + swingOffset(project, pattern, step);
            if (beat + EPSILON >= from && beat < Math.min(to, end) - EPSILON) append({ kind: 'pad', pad: Number(pad), beat, velocity, gain, row, sequence, clipId: row ? sequence : null, durationBeats: Math.min(1 / division, end - beat) });
          }
        }
        for (const note of pattern.notes || []) {
          const start = finite(note.start); const beat = base + start;
          if (start < 0 || start >= length || finite(note.duration, .25) <= 0 || finite(note.velocity, .8) <= 0 || beat + EPSILON < from || beat >= Math.min(to, end) - EPSILON) continue;
          append({ kind: 'note', pitch: clamp(note.pitch, 0, 127, 60), pad: note.pad ?? null, beat, velocity: clamp(note.velocity, 0, 1, .8), durationBeats: Math.min(finite(note.duration, .25), length - start, end - beat), gain, row, sequence, clipId: row ? sequence : null });
        }
      }
    };
    if (mode !== 'song') {
      const pattern = selectedPattern(project);
      if (pattern) patternEvents(pattern, 0, Infinity);
    } else {
      const rows = project.rows || [];
      for (const row of rows) {
        if (!audible(rows, row)) continue;
        for (const clip of row.clips || []) {
          const start = finite(clip.start_beat); const end = start + finite(clip.length_beats);
          if (clip.mute || finite(clip.gain, 1) <= 0 || end <= from || start >= to) continue;
          if (clip.kind === 'pattern') {
            const pattern = project.patterns.find(item => item.id === clip.ref);
            if (!pattern) throw new Error(`Arrangement clip references missing pattern: ${clip.ref}`);
            patternEvents(pattern, start, end, finite(clip.gain, 1), row.id, clip.id);
          } else if (clip.kind === 'audio') {
            if (start + EPSILON >= from) append({ kind: 'audio', beat: start, clip, row: row.id });
          } else throw new Error(`Unsupported arrangement clip kind: ${clip.kind}`);
        }
      }
    }
    return result.sort((a, b) => a.beat - b.beat);
  }

  function encodeWav(buffer) {
    const channels = Math.min(2, buffer.numberOfChannels);
    const frames = buffer.length;
    if (!channels || frames > LIMITS.renderSeconds * LIMITS.sampleRate) throw new Error('Audio is too large for browser WAV export.');
    const output = new ArrayBuffer(44 + frames * channels * 2); const view = new DataView(output);
    const write = (offset, text) => { for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i)); };
    write(0, 'RIFF'); view.setUint32(4, output.byteLength - 8, true); write(8, 'WAVE'); write(12, 'fmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, channels, true);
    view.setUint32(24, buffer.sampleRate, true); view.setUint32(28, buffer.sampleRate * channels * 2, true);
    view.setUint16(32, channels * 2, true); view.setUint16(34, 16, true); write(36, 'data'); view.setUint32(40, output.byteLength - 44, true);
    const data = Array.from({ length: channels }, (_, channel) => buffer.getChannelData(channel));
    let offset = 44;
    for (let frame = 0; frame < frames; frame += 1) for (let channel = 0; channel < channels; channel += 1) {
      const sample = clamp(data[channel][frame], -1, 1);
      view.setInt16(offset, Math.round(sample * (sample < 0 ? 32768 : 32767)), true); offset += 2;
    }
    return new Blob([output], { type: 'audio/wav' });
  }

  function requireSupportedProject(project) {
    const unsupported = [];
    if ((project.automation || []).some(lane => lane.enabled !== false && lane.points?.length)) unsupported.push('automation');
    if (Object.values(project.plugins || {}).some(plugin => plugin && !plugin.bypass)) unsupported.push('native plugins');
    if (Object.values(project.pro_daw?.plugin_chains || {}).some(chain => Array.isArray(chain) && chain.some(plugin => !plugin.bypass))) unsupported.push('native insert chains');
    const workflow = project.workflow || {}; const routing = workflow.routing || {};
    const groups = workflow.groups || [];
    if (groups.some(group => (group.members || []).some(id => project.tracks.some(track => track.id === id)) && (group.mute || finite(group.gain, 1) !== 1))) unsupported.push('mixer group gain or mute');
    if ((routing.buses || []).length || (routing.sends || []).some(send => !send.mute && finite(send.gain, 1) > 0) || Object.values(routing.track_outputs || {}).some(target => target !== 'master')) unsupported.push('custom bus routing');
    if (Object.keys(workflow.clip_edits || {}).length) unsupported.push('desktop clip processing');
    if (Object.keys(workflow.sidechains || {}).length) unsupported.push('sidechain processing');
    if (unsupported.length) throw new Error(`This project uses ${unsupported.join(', ')} that the browser cannot reproduce. Render those parts to audio in the desktop app before browser playback or WAV export. The saved settings have been preserved.`);
  }

  function distortion(amount) {
    if (amount <= 0) return null;
    const curve = new Float32Array(2048); const drive = 1 + amount * 12;
    for (let i = 0; i < curve.length; i += 1) curve[i] = Math.tanh((2 * i / (curve.length - 1) - 1) * drive) / Math.tanh(drive);
    return curve;
  }

  function loopSpec(buffer, offset, duration, crossfade) {
    const start = Math.round(offset * buffer.sampleRate); const length = Math.min(buffer.length - start, Math.round(duration * buffer.sampleRate));
    const fade = Math.min(Math.floor(length / 2) - 1, Math.round(crossfade * buffer.sampleRate));
    return { start, length, fade, key: `${start}:${length}:${fade}`, bytes: buffer.numberOfChannels * length * 4 };
  }

  function preparedPCMBytes(events, project, getBuffer) {
    let bytes = 0; const reversed = new Set(); const loops = new Map();
    const reserve = count => {
      bytes += count;
      if (bytes > LIMITS.renderPCMBytes) throw new Error('This export exceeds the 128 MiB prepared-audio budget. Use shorter source ranges, fewer unique loop trims, or the desktop app.');
    };
    for (const event of events) {
      if (event.kind === 'note' && event.pad === null) continue;
      const clip = event.kind === 'audio' ? event.clip : null; const pad = clip ? null : project.pads[event.pad];
      const settings = clip || pad;
      if (!settings || !audible(project.tracks, project.tracks[settings.track || 0]) || finite(settings.gain, 1) <= 0 || finite(event.gain, 1) <= 0 || finite(event.velocity, 1) <= 0) continue;
      const id = clip ? clip.ref : pad.sample_id; if (!id) continue;
      const buffer = getBuffer(id); if (!buffer) throw new Error(`Missing audio: ${id}. Import or relink it before export.`);
      const rawStart = finite(clip ? clip.offset : pad.start); const start = clamp(rawStart, 0, buffer.duration);
      const rawEnd = clip ? (clip.source_length > 0 ? rawStart + clip.source_length : buffer.duration) : pad.end;
      const end = rawEnd > 0 ? clamp(rawEnd, 0, buffer.duration) : buffer.duration;
      if (!(end > start)) throw new Error('Sample trim must end after its start.');
      if (settings.reverse && !reversed.has(buffer)) { reserve(buffer.numberOfChannels * buffer.length * 4); reversed.add(buffer); }
      if (!(clip ? clip.loop : pad.mode === 'loop')) continue;
      const spec = loopSpec(buffer, settings.reverse ? buffer.duration - end : start, end - start, clamp(settings.loop_crossfade, 0, 1, .005));
      if (spec.fade <= 0) continue;
      let keys = loops.get(buffer); if (!keys) { keys = new Set(); loops.set(buffer, keys); }
      const key = `${Boolean(settings.reverse)}:${spec.key}`;
      if (!keys.has(key)) { reserve(spec.bytes); keys.add(key); }
    }
    return bytes;
  }

  function makeGraph(context, project) {
    const nodes = []; const create = kind => { const node = context[kind](); nodes.push(node); return node; };
    const filter = (type, frequency) => { const node = create('createBiquadFilter'); node.type = type; node.frequency.value = Math.min(frequency, context.sampleRate * .49); return node; };
    const compressorStage = input => {
      const compressor = create('createDynamicsCompressor'); const wet = create('createGain'); const dry = create('createGain'); const output = create('createGain');
      input.connect(compressor).connect(wet).connect(output); input.connect(dry).connect(output);
      return { compressor, wet, dry, output };
    };
    const input = create('createGain'); const master = create('createGain'); const tone = filter('lowshelf', 120); const mid = filter('peaking', 900); const high = filter('highshelf', 6000); const drive = create('createWaveShaper');
    input.connect(tone).connect(mid).connect(high).connect(drive);
    const glue = compressorStage(drive); glue.output.connect(master);
    const analyser = create('createAnalyser'); analyser.fftSize = 1024; master.connect(analyser).connect(context.destination);
    const delay = context.createDelay(4); nodes.push(delay); const delayGain = create('createGain'); const feedback = create('createGain'); const delayFilter = filter('lowpass', 6000);
    delay.connect(delayFilter).connect(delayGain).connect(input); delayFilter.connect(feedback);
    const feedbackStraight = create('createGain'); const feedbackCross = create('createGain'); const feedbackSplit = context.createChannelSplitter(2); const feedbackMerge = context.createChannelMerger(2); nodes.push(feedbackSplit, feedbackMerge);
    feedback.connect(feedbackStraight).connect(delay); feedback.connect(feedbackSplit); feedbackSplit.connect(feedbackMerge, 0, 1); feedbackSplit.connect(feedbackMerge, 1, 0); feedbackMerge.connect(feedbackCross).connect(delay);
    const reverb = create('createConvolver'); const reverbGain = create('createGain'); const predelay = context.createDelay(.3); nodes.push(predelay); predelay.connect(reverb).connect(reverbGain).connect(input);
    // Seeded impulses make browser previews and exports reproducible. Create
    // one only when a send is used; silent/default projects allocate no IR.
    let impulseSignature = '';
    const updateImpulse = fx => {
      const size = clamp(fx.size, 0, 1, .55); const damping = clamp(fx.damping, 0, 1, .45); const width = clamp(fx.width, 0, 1, 1);
      const signature = `${size}:${damping}:${width}`; if (signature === impulseSignature) return;
      const impulse = context.createBuffer(2, Math.ceil(context.sampleRate * (.3 + size * 4)), context.sampleRate);
      let seed = 0x416e6861; let previous = 0;
      for (let channel = 0; channel < 2; channel += 1) {
        const data = impulse.getChannelData(channel); const left = impulse.getChannelData(0); previous = 0;
        for (let frame = 0; frame < data.length; frame += 1) {
          seed = (Math.imul(seed, 1664525) + 1013904223) | 0; previous += (seed / 2147483648 - previous) * (1 - damping * .92);
          const sample = previous * (1 - frame / data.length) ** (2.5 + (1 - size) * 2);
          data[frame] = channel ? left[frame] * (1 - width) + sample * width : sample;
        }
      }
      reverb.buffer = impulse; impulseSignature = signature;
    };
    const trackBuses = Array.from({ length: 8 }, () => {
      const channelInput = create('createGain'); const gain = create('createGain'); const pan = create('createStereoPanner'); const low = filter('lowshelf', 120); const middle = filter('peaking', 900); const upper = filter('highshelf', 6000); const cutoff = filter('allpass', 20000); const saturation = create('createWaveShaper');
      const filterWet = create('createGain'); const filterDry = create('createGain');
      channelInput.connect(low).connect(middle).connect(upper);
      upper.connect(cutoff).connect(filterWet).connect(saturation); upper.connect(filterDry).connect(saturation);
      const comp = compressorStage(saturation); const makeup = create('createGain'); comp.output.connect(makeup).connect(gain);
      const splitter = context.createChannelSplitter(2); const merger = context.createChannelMerger(2); nodes.push(splitter, merger);
      const balanceLeft = create('createGain'); const balanceRight = create('createGain'); gain.connect(splitter);
      splitter.connect(balanceLeft, 0); splitter.connect(balanceRight, 1); balanceLeft.connect(merger, 0, 0); balanceRight.connect(merger, 0, 1); merger.connect(pan).connect(input);
      const sendDelay = create('createGain'); const sendReverb = create('createGain'); pan.connect(sendDelay).connect(delay); pan.connect(sendReverb).connect(predelay);
      const analyser = create('createAnalyser'); analyser.fftSize = 256; pan.connect(analyser);
      return { input: channelInput, gain, pan, balanceLeft, balanceRight, low, mid: middle, high: upper, filter: cutoff, filterWet, filterDry, drive: saturation, ...comp, makeup, sendDelay, sendReverb, analyser, meterData: new Float32Array(256), driveAmount: -1 };
    });
    const legacyDelay = create('createGain'); trackBuses.forEach(bus => bus.pan.connect(legacyDelay)); legacyDelay.connect(delay);
    const graph = { master, tone, compressor: glue.compressor, delayGain: legacyDelay, trackBuses, analyser, nodes, driveAmount: -1 };
    graph.sync = document => {
      const masterFX = document.master_fx || {}; const webFX = document.web_effects || {};
      master.gain.value = clamp(document.master, 0, 2, .82);
      tone.gain.value = clamp(masterFX.low, -24, 24) + clamp(webFX.tone, -12, 12);
      mid.gain.value = clamp(masterFX.mid, -24, 24); high.gain.value = clamp(masterFX.high, -24, 24);
      const driveAmount = clamp(masterFX.drive, 0, 1);
      if (graph.driveAmount !== driveAmount) { drive.curve = distortion(driveAmount); graph.driveAmount = driveAmount; }
      const ratio = clamp(webFX.compression, 1, 20, 1); const compress = masterFX.glue || ratio > 1;
      glue.wet.gain.value = compress ? 1 : 0; glue.dry.gain.value = compress ? 0 : 1;
      glue.compressor.threshold.value = masterFX.glue ? -8 - clamp(masterFX.glue_amount, 0, 1, .4) * 16 : -8;
      glue.compressor.ratio.value = masterFX.glue ? 2 + clamp(masterFX.glue_amount, 0, 1, .4) * 3 : ratio;
      glue.compressor.attack.value = .01; glue.compressor.release.value = .18;
      legacyDelay.gain.value = clamp(webFX.delay, 0, 100) / 100;
      const delayFX = document.delay_fx || {}; const reverbFX = document.reverb_fx || {};
      const syncBeats = { '1/1': 4, '1/2': 2, '1/4': 1, '1/8': .5, '1/16': .25, '1/8.': .75, '1/8D': .75, '1/8T': 1 / 3, '1/16T': 1 / 6 };
      delay.delayTime.value = clamp(secondsPerBeat(document) * (syncBeats[delayFX.sync] || .5), .01, 4);
      feedback.gain.value = clamp(delayFX.feedback, 0, .95, .36);
      feedbackCross.gain.value = delayFX.ping_pong === false ? 0 : 1; feedbackStraight.gain.value = delayFX.ping_pong === false ? 1 : 0;
      delayGain.gain.value = delayFX.enabled === false ? 0 : clamp(delayFX.level, 0, 4, .9);
      delayFilter.frequency.value = clamp(18000 * (1 - clamp(delayFX.damping, 0, 1, .4)), 200, context.sampleRate * .49);
      reverbGain.gain.value = reverbFX.enabled === false ? 0 : clamp(reverbFX.level, 0, 4, .9);
      predelay.delayTime.value = clamp(reverbFX.predelay, 0, .3, .018);
      const tracks = document.tracks || [];
      if (reverbFX.enabled !== false && tracks.some(track => track.fx?.send_reverb > 0)) updateImpulse(reverbFX);
      trackBuses.forEach((bus, index) => {
        const track = tracks[index] || {}; const fx = track.fx || {};
        const active = audible(tracks, track); bus.input.gain.value = active ? 1 : 0;
        bus.gain.gain.value = active ? clamp(track.gain, 0, 4, .85) : 0;
        const pan = clamp(track.pan, -1, 1); bus.balanceLeft.gain.value = Math.cos(Math.max(pan, 0) * Math.PI / 2); bus.balanceRight.gain.value = Math.cos(Math.min(pan, 0) * Math.PI / 2);
        bus.low.gain.value = clamp(fx.low, -24, 24); bus.mid.gain.value = clamp(fx.mid, -24, 24); bus.high.gain.value = clamp(fx.high, -24, 24); bus.mid.frequency.value = clamp(fx.mid_freq, 20, context.sampleRate * .49, 900);
        bus.filter.type = ['lowpass', 'highpass'].includes(fx.filter_type) ? fx.filter_type : 'allpass';
        bus.filterWet.gain.value = fx.filter_type === 'lowpass' || fx.filter_type === 'highpass' ? 1 : 0; bus.filterDry.gain.value = 1 - bus.filterWet.gain.value;
        bus.filter.frequency.value = clamp(fx.cutoff, 20, context.sampleRate * .49, 20000); bus.filter.Q.value = .1 + clamp(fx.resonance, 0, 1, .15) * 16;
        const amount = clamp(fx.drive, 0, 1); if (bus.driveAmount !== amount) { bus.drive.curve = distortion(amount); bus.driveAmount = amount; }
        bus.wet.gain.value = fx.comp ? 1 : 0; bus.dry.gain.value = fx.comp ? 0 : 1;
        bus.compressor.threshold.value = clamp(fx.threshold, -100, 0, -18); bus.compressor.ratio.value = clamp(fx.ratio, 1, 20, 4);
        bus.compressor.attack.value = clamp(fx.attack, 0, 1, .01); bus.compressor.release.value = clamp(fx.release, 0, 1, .12);
        bus.makeup.gain.value = fx.comp ? db(fx.makeup) : 1;
        bus.sendDelay.gain.value = clamp(fx.send_delay, 0, 1); bus.sendReverb.gain.value = clamp(fx.send_reverb, 0, 1);
      });
    };
    graph.disconnect = () => nodes.forEach(node => { try { node.disconnect(); } catch { /* already disconnected */ } });
    graph.sync(project);
    return graph;
  }

  class AudioEngine {
    constructor({ getProject, getBuffer, onPosition = () => {}, onError = () => {} }) {
      if (typeof getProject !== 'function' || typeof getBuffer !== 'function') throw new Error('Audio engine requires project and sample accessors.');
      this.getProject = getProject; this.getBuffer = getBuffer; this.onPosition = onPosition; this.onError = onError;
      this.context = null; this.graph = null; this.playing = false; this.mode = 'pattern'; this.timer = null;
      this.voices = new Set(); this.retiringVoices = new Set(); this.reverseBuffers = new WeakMap(); this.loopBuffers = new WeakMap(); this.noiseBuffers = new WeakMap(); this.metronome = false; this.rendering = false;
      this.preparedBudget = null;
      this.anchorTime = 0; this.anchorBeat = 0; this.cursor = 0; this.tempo = 110; this.project = null; this.lastPosition = -1;
    }

    async resume() {
      if (!this.context) {
        const Context = window.AudioContext || window.webkitAudioContext;
        if (!Context) throw new Error('Web Audio is unavailable in this browser.');
        this.context = new Context({ latencyHint: 'interactive' }); this.graph = makeGraph(this.context, this.getProject());
      }
      if (this.context.state === 'suspended') await this.context.resume();
      if (this.context.state !== 'running') throw new Error('Audio could not start. Enable audio for this site and try again.');
      this.sync();
      return this.context;
    }

    sync() {
      const project = this.getProject();
      if (this.playing && this.project !== project) {
        try { requireSupportedProject(project); } catch (error) { this.lastError = error; this.stop(); this.onError(error); return; }
      }
      this.graph?.sync(project);
      if (this.playing && (this.project !== project || this.tempo !== project.bpm)) {
        const time = this.context.currentTime + .008; const beat = this.beatAt(time);
        const changedTempo = this.tempo !== project.bpm;
        for (const voice of [...this.voices, ...this.retiringVoices]) {
          const row = voice.row ? project.rows.find(item => item.id === voice.row) : null;
          const clip = voice.clipId ? row?.clips.find(item => item.id === voice.clipId) : null;
          if (voice.when >= time || (voice.row && (!row || !audible(project.rows, row))) || (voice.clipId && (!clip || clip.mute || finite(clip.gain, 1) <= 0))) voice.stop(time, true);
        }
        this.anchorTime = time; this.anchorBeat = beat; this.cursor = beat; this.tempo = project.bpm;
        if (changedTempo) for (const voice of this.voices) if (voice.when < time && voice.endBeat > beat) voice.retime?.(this.timeAt(voice.endBeat));
      }
      this.project = project;
    }

    beatAt(time) { return this.anchorBeat + (time - this.anchorTime) * this.tempo / 60; }
    timeAt(beat) { return this.anchorTime + (beat - this.anchorBeat) * 60 / this.tempo; }
    setMetronome(enabled) { this.metronome = Boolean(enabled); }
    output(track, graph = this.graph) { return graph.trackBuses[Math.round(clamp(track, 0, 7))].input; }

    sample(id) {
      const buffer = this.getBuffer(id);
      if (!buffer) throw new Error(`Missing audio: ${id}. Import or relink the original sample before playback or export.`);
      return buffer;
    }

    reversed(buffer, context) {
      let result = this.reverseBuffers.get(buffer);
      if (!result) {
        this.reservePreparedPCM(buffer.numberOfChannels * buffer.length * 4);
        result = context.createBuffer(buffer.numberOfChannels, buffer.length, buffer.sampleRate);
        for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) { const from = buffer.getChannelData(channel); const to = result.getChannelData(channel); for (let i = 0; i < from.length; i += 1) to[i] = from[from.length - i - 1]; }
        this.reverseBuffers.set(buffer, result);
      }
      return result;
    }

    crossfadedLoop(buffer, offset, duration, crossfade, context) {
      const { start, length, fade, key, bytes } = loopSpec(buffer, offset, duration, crossfade);
      if (fade <= 0) return null;
      let variants = this.loopBuffers.get(buffer); if (!variants) { variants = new Map(); this.loopBuffers.set(buffer, variants); }
      if (variants.has(key)) return variants.get(key);
      this.reservePreparedPCM(bytes);
      const prepared = context.createBuffer(buffer.numberOfChannels, length, buffer.sampleRate);
      for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
        const input = buffer.getChannelData(channel); const output = prepared.getChannelData(channel);
        output.set(input.subarray(start, start + length));
        for (let frame = 0; frame < fade; frame += 1) { const alpha = frame / fade; output[length - fade + frame] = input[start + length - fade + frame] * (1 - alpha) + input[start + frame] * alpha; }
      }
      // Match the desktop loop: preserve the first head, blend the tail, then
      // skip the head consumed by the crossfade on subsequent cycles.
      const result = { buffer: prepared, loopStart: fade / buffer.sampleRate, loopEnd: length / buffer.sampleRate };
      // Offline sources retain their buffers until rendering completes. Keep
      // every budgeted unique variant to avoid reallocating evicted PCM per hit.
      if (!this.preparedBudget && variants.size >= 2) variants.delete(variants.keys().next().value);
      variants.set(key, result); return result;
    }

    reservePreparedPCM(bytes) {
      if (!this.preparedBudget) return;
      if (this.preparedBudget.bytes + bytes > LIMITS.renderPCMBytes) throw new Error('This export exceeds the 128 MiB prepared-audio budget. Render it in the desktop app.');
      this.preparedBudget.bytes += bytes;
    }

    noiseBuffer(context) {
      let buffer = this.noiseBuffers.get(context);
      if (!buffer) {
        buffer = context.createBuffer(1, context.sampleRate, context.sampleRate); const data = buffer.getChannelData(0);
        let seed = 54321;
        for (let i = 0; i < data.length; i += 1) { seed = (Math.imul(seed, 1103515245) + 12345) | 0; data[i] = seed / 2147483648; }
        this.noiseBuffers.set(context, buffer);
      }
      return buffer;
    }

    registerVoice(voice, pool = this.voices, offline = false) {
      // Offline nodes are scheduled in advance. Only overlapping voices count
      // toward polyphony; past voices may safely be removed from this index.
      const expiredAt = offline ? voice.when : (this.context?.currentTime || 0);
      for (const existing of pool) if (existing.end <= expiredAt) pool.delete(existing);
      if (!offline) for (const existing of this.retiringVoices) if (existing.end <= expiredAt) this.retiringVoices.delete(existing);
      if (pool.size >= LIMITS.voices) {
        if (offline) throw new Error(`Export exceeds ${LIMITS.voices} simultaneous voices.`);
        const victim = pool.values().next().value; victim.stop(voice.when, true); pool.delete(victim);
        // A future cancellation still needs ownership until its scheduled end
        // so a subsequent Stop can silence the voice immediately.
        if (victim.end > expiredAt) this.retiringVoices.add(victim);
      }
      pool.add(voice);
      return voice;
    }

    bufferVoice(buffer, settings, opts, context, graph, pool, offline) {
      const level = clamp(settings.gain, 0, 4, 1) * clamp(opts.velocity, 0, 1, 1) * clamp(opts.gain, 0, 4, 1);
      if (!level) return null;
      const when = Math.max(0, finite(opts.when, context.currentTime));
      const start = clamp(opts.start ?? settings.start, 0, buffer.duration);
      const selectedEnd = opts.end ?? settings.end;
      const end = selectedEnd > 0 ? clamp(selectedEnd, 0, buffer.duration) : buffer.duration;
      if (!(end > start)) throw new Error('Sample trim must end after its start.');
      const semitones = clamp(settings.pitch, -96, 96) + (opts.pitch === undefined ? 0 : finite(opts.pitch) - finite(settings.root_note, 60));
      let rate = 2 ** (semitones / 12);
      if (settings.sync_beats > 0) rate *= (end - start) / (settings.sync_beats * secondsPerBeat(opts.project));
      rate = clamp(rate, .00390625, 256, 1);
      const looping = settings.mode === 'loop'; const gate = settings.mode === 'gate' || looping;
      const naturalDuration = (end - start) / rate;
      const phase = Math.max(0, finite(opts.phaseSeconds)); const availableDuration = Math.max(0, naturalDuration - phase);
      if (!looping && !availableDuration) return null;
      const requestedDuration = opts.duration === undefined ? (looping ? 60 : availableDuration) : Math.max(.001, opts.duration);
      const duration = looping ? requestedDuration : (gate || opts.forceDuration ? Math.min(availableDuration, requestedDuration) : availableDuration);
      const release = Math.min(duration / 2, clamp(settings.release, 0, 10, .03));
      const attack = Math.min(duration / 2, clamp(settings.attack, 0, 10, .002));
      const source = context.createBufferSource(); let sourceBuffer = settings.reverse ? this.reversed(buffer, context) : buffer;
      source.playbackRate.value = rate;
      const offset = settings.reverse ? buffer.duration - end : start;
      let playbackOffset = offset + (looping ? phase % naturalDuration : phase) * rate;
      source.loop = looping; source.loopStart = offset; source.loopEnd = offset + end - start;
      if (looping) {
        const prepared = this.crossfadedLoop(sourceBuffer, offset, end - start, clamp(settings.loop_crossfade, 0, 1, .005), context);
        if (prepared) {
          sourceBuffer = prepared.buffer; source.loopStart = prepared.loopStart; source.loopEnd = prepared.loopEnd;
          const travel = phase * rate;
          playbackOffset = travel < prepared.loopEnd ? travel : prepared.loopStart + (travel - prepared.loopEnd) % (prepared.loopEnd - prepared.loopStart);
        }
      }
      source.buffer = sourceBuffer;
      const gain = context.createGain(); const pan = context.createStereoPanner(); pan.pan.value = clamp(settings.pan, -1, 1);
      gain.gain.setValueAtTime(attack > 0 ? 0 : level, when); if (attack > 0) gain.gain.linearRampToValueAtTime(level, when + attack);
      gain.gain.setValueAtTime(level, when + duration - release); gain.gain.linearRampToValueAtTime(0, when + duration);
      source.connect(gain).connect(pan).connect(this.output(settings.track, graph));
      const voice = { when, end: when + duration, pad: opts.pad, pitch: opts.pitch, row: opts.row, clipId: opts.clipId, sequence: opts.sequence || 'live', sourceId: settings.sample_id, choke: settings.choke, gate, source,
        retime: time => {
          if (!gate && !opts.forceDuration) return;
          const at = context.currentTime; const nextEnd = Math.min(time, looping ? Infinity : when + availableDuration);
          if (nextEnd <= at) { voice.stop(at, true); return; }
          gain.gain.cancelAndHoldAtTime(at); gain.gain.setValueAtTime(level, Math.max(at, nextEnd - release)); gain.gain.linearRampToValueAtTime(0, nextEnd); source.stop(nextEnd); voice.end = nextEnd;
        },
        stop: (time = context.currentTime, immediate = false) => {
          const at = Math.max(context.currentTime, finite(time)); if (at >= voice.end) return;
          const fade = immediate || at < when ? 0 : Math.min(.02, release, Math.max(0, voice.end - at));
          const held = at < when ? 0 : at < when + attack ? level * (at - when) / attack : release && at > voice.end - release ? level * (voice.end - at) / release : level;
          try { gain.gain.cancelAndHoldAtTime(at); gain.gain.setValueAtTime(held, at); if (fade) gain.gain.linearRampToValueAtTime(0, at + fade); else gain.gain.setValueAtTime(0, at); source.stop(at + fade); } catch { /* source already ended */ }
          voice.end = Math.min(voice.end, at + fade); if (voice.end <= context.currentTime) pool.delete(voice);
        }
      };
      source.onended = () => { pool.delete(voice); this.retiringVoices.delete(voice); source.disconnect(); gain.disconnect(); pan.disconnect(); };
      this.registerVoice(voice, pool, offline);
      source.start(when, playbackOffset); source.stop(when + duration);
      return voice;
    }

    padVoice(index, opts, context, graph, pool, offline = false) {
      const project = opts.project || this.getProject(); const pad = project.pads[index];
      if (!pad) throw new Error(`Unknown pad: ${index + 1}`);
      if (!pad.sample_id) return null;
      if (!audible(project.tracks, project.tracks[pad.track || 0])) return null;
      const buffer = this.sample(pad.sample_id); const when = finite(opts.when, context.currentTime);
      for (const voice of [...pool]) {
        if (typeof voice.pad !== 'number' || voice.sequence !== (opts.sequence || 'live')) continue;
        const chromatic = voice.pitch !== undefined || opts.pitch !== undefined;
        const cuts = chromatic ? voice.pitch !== undefined && opts.pitch !== undefined && voice.pad === index && (pad.mono || voice.pitch === opts.pitch) : (pad.choke && voice.choke === pad.choke) || (pad.mode !== 'one-shot' && voice.pad === index) || (project.self_choke && voice.sourceId === pad.sample_id);
        if (cuts) voice.stop(when);
      }
      return this.bufferVoice(buffer, pad, { ...opts, project, pad: index }, context, graph, pool, offline);
    }

    triggerPad(index, opts = {}) {
      if (!this.context) throw new Error('Start audio before triggering a pad.');
      this.sync(); return this.padVoice(index, opts, this.context, this.graph, this.voices);
    }
    releasePad(index) { for (const voice of [...this.voices, ...this.retiringVoices]) if (voice.pad === index && voice.gate) voice.stop(); }

    synthVoice(pitch, opts, context, graph, pool, offline = false) {
      const project = opts.project || this.getProject(); const patch = project.synth || {};
      if (!audible(project.tracks, project.tracks[patch.track || 0])) return null;
      if (patch.sample_source || patch.sample_layer) throw new Error('This instrument uses desktop sample layers. Choose a browser oscillator preset or render this instrument to audio in the desktop app.');
      const when = Math.max(0, finite(opts.when, context.currentTime)); const duration = clamp(opts.duration, .001, 60, 30);
      const attack = Math.min(duration, clamp(patch.attack, .001, 10, .025)); const decay = clamp(patch.decay, .001, 10, .32); const sustain = clamp(patch.sustain, 0, 1, .68); const release = clamp(patch.release, .005, 10, .65);
      const gain = context.createGain(); const filter = context.createBiquadFilter(); const drive = context.createWaveShaper(); const nodes = [gain, filter, drive]; const sources = [];
      const level = clamp(patch.volume, 0, 1, .42) * clamp(opts.velocity, 0, 1, .8) * clamp(opts.gain, 0, 4, 1) * .5;
      const frequency = 440 * 2 ** ((clamp(pitch, 0, 127, 60) - 69) / 12);
      filter.type = 'lowpass'; filter.frequency.value = clamp(patch.cutoff, 20, context.sampleRate * .49, 2400); filter.Q.value = .1 + clamp(patch.resonance, 0, 1, .28) * 16; drive.curve = distortion(clamp(patch.drive, 0, 1));
      filter.connect(drive).connect(gain).connect(this.output(patch.track, graph));
      const osc = (wave, hz, volume, panValue, detune = 0) => {
        let source;
        if (wave === 'noise') {
          source = context.createBufferSource(); source.buffer = this.noiseBuffer(context); source.loop = true;
        } else {
          source = context.createOscillator();
          if (wave === 'pulse') {
            const real = new Float32Array(65); const imag = new Float32Array(65); const width = clamp(patch.pulse_width, .05, .95, .5);
            for (let harmonic = 1; harmonic < real.length; harmonic += 1) { real[harmonic] = 2 * Math.sin(2 * Math.PI * harmonic * width) / (Math.PI * harmonic); imag[harmonic] = 2 * (1 - Math.cos(2 * Math.PI * harmonic * width)) / (Math.PI * harmonic); }
            source.setPeriodicWave(context.createPeriodicWave(real, imag, { disableNormalization: true }));
          } else source.type = wave === 'saw' ? 'sawtooth' : ['sine', 'triangle', 'square', 'sawtooth'].includes(wave) ? wave : 'sine';
          source.frequency.value = Math.min(context.sampleRate * .49, hz);
        }
        source.detune.value = detune;
        const mix = context.createGain(); mix.gain.value = volume; const pan = context.createStereoPanner(); pan.pan.value = panValue;
        source.connect(mix).connect(pan).connect(filter); nodes.push(mix, pan); sources.push(source); return source;
      };
      const mix = clamp(patch.osc_mix, 0, 1, .42); const spread = clamp(patch.spread, 0, 1, .42);
      const one = osc(patch.osc1 || 'saw', frequency, 1 - mix, -spread, -clamp(patch.detune, 0, 100, 8) / 2);
      const two = osc(patch.osc2 || 'square', frequency * 2 ** clamp(patch.osc2_octave, -3, 3), mix, spread, clamp(patch.detune, 0, 100, 8) / 2);
      if (patch.sub > 0) osc('sine', frequency / 2, clamp(patch.sub, 0, 1), 0);
      if (patch.noise > 0) {
        const noise = context.createBufferSource(); const noiseGain = context.createGain(); noiseGain.gain.value = clamp(patch.noise, 0, 1);
        noise.buffer = this.noiseBuffer(context); noise.loop = true; noise.connect(noiseGain).connect(filter); sources.push(noise); nodes.push(noiseGain);
      }
      if (patch.lfo_pitch > 0 && patch.lfo_rate > 0) {
        const lfo = context.createOscillator(); const amount = context.createGain(); lfo.frequency.value = clamp(patch.lfo_rate, .01, 30, .32); amount.gain.value = clamp(patch.lfo_pitch, 0, 100, 2);
        lfo.connect(amount); amount.connect(one.detune); amount.connect(two.detune); nodes.push(amount); sources.push(lfo);
      }
      gain.gain.setValueAtTime(0, when); gain.gain.linearRampToValueAtTime(level, when + attack);
      const decayEnd = Math.min(duration, attack + decay); const sustainLevel = level * (1 - (1 - sustain) * Math.min(1, Math.max(0, duration - attack) / decay));
      gain.gain.linearRampToValueAtTime(sustainLevel, when + decayEnd); gain.gain.setValueAtTime(sustainLevel, when + duration); gain.gain.linearRampToValueAtTime(0, when + duration + release);
      const voice = { when, end: when + duration + release, pitch, pad: null, row: opts.row, clipId: opts.clipId, gate: true,
        retime: time => {
          const at = context.currentTime; if (time <= at) { voice.stop(at); return; }
          gain.gain.cancelAndHoldAtTime(at); gain.gain.setValueAtTime(sustainLevel, time); gain.gain.linearRampToValueAtTime(0, time + release);
          sources.forEach(source => source.stop(time + release)); voice.end = time + release;
        },
        stop: (time = context.currentTime, immediate = false) => {
          const at = Math.max(context.currentTime, finite(time)); if (at >= voice.end) return;
          const fade = immediate || at < when ? 0 : Math.min(release, Math.max(0, voice.end - at));
          const age = at - when;
          const held = age < 0 ? 0 : age < attack ? level * age / attack : age < decayEnd ? level + (sustainLevel - level) * (age - attack) / (decayEnd - attack) : at > voice.end - release ? sustainLevel * (voice.end - at) / release : sustainLevel;
          try { gain.gain.cancelAndHoldAtTime(at); gain.gain.setValueAtTime(held, at); if (fade) gain.gain.linearRampToValueAtTime(0, at + fade); else gain.gain.setValueAtTime(0, at); sources.forEach(source => source.stop(at + fade)); } catch { /* already ended */ }
          voice.end = Math.min(voice.end, at + fade); if (voice.end <= context.currentTime) pool.delete(voice);
        }
      };
      this.registerVoice(voice, pool, offline);
      sources.forEach(source => { source.start(when); source.stop(voice.end); });
      sources[0].onended = () => { pool.delete(voice); this.retiringVoices.delete(voice); sources.forEach(source => source.disconnect()); nodes.forEach(node => node.disconnect()); };
      return voice;
    }

    triggerNote(pitch, opts = {}) {
      if (!this.context) throw new Error('Start audio before triggering a note.');
      this.sync();
      return opts.pad !== null && opts.pad !== undefined ? this.padVoice(opts.pad, { ...opts, pitch }, this.context, this.graph, this.voices) : this.synthVoice(pitch, opts, this.context, this.graph, this.voices);
    }
    releaseNote(pitch) { for (const voice of [...this.voices, ...this.retiringVoices]) if (voice.pitch === pitch) voice.stop(); }

    schedule(event, when, project, context, graph, pool, offline = false) {
      const spb = secondsPerBeat(project);
      const opts = { when, project, velocity: event.velocity, gain: event.gain, duration: event.durationBeats * spb, row: event.row, sequence: event.sequence, clipId: event.clipId };
      if (event.kind === 'pad') return this.padVoice(event.pad, opts, context, graph, pool, offline);
      if (event.kind === 'note') return event.pad === null ? this.synthVoice(event.pitch, opts, context, graph, pool, offline) : this.padVoice(event.pad, { ...opts, pitch: event.pitch }, context, graph, pool, offline);
      const clip = event.clip;
      if (!audible(project.tracks, project.tracks[clip.track || 0])) return null;
      const buffer = this.sample(clip.ref); const start = finite(clip.offset); const end = clip.source_length > 0 ? start + clip.source_length : buffer.duration;
      return this.bufferVoice(buffer, { start, end, gain: finite(clip.gain, 1), track: clip.track, reverse: clip.reverse, mode: clip.loop ? 'loop' : 'one-shot', loop_crossfade: clip.loop_crossfade, attack: .002, release: .005 }, { when, project, duration: (event.durationBeats ?? clip.length_beats) * spb, forceDuration: true, phaseSeconds: event.phaseSeconds, row: event.row, clipId: clip.id }, context, graph, pool, offline);
    }

    async start(mode = 'pattern') {
      await this.resume(); this.stop(); this.mode = mode === 'song' ? 'song' : 'pattern';
      const project = this.getProject();
      if (this.mode === 'song' && !lengthBeats(project, 'song')) throw new Error('The song is empty. Add arrangement clips or select pattern playback.');
      // Validate every scheduled media reference before a transport can start.
      this.validate(project, this.mode);
      this.playing = true; this.tempo = project.bpm; this.anchorTime = this.context.currentTime + .04; this.anchorBeat = 0; this.cursor = 0; this.project = project; this.lastPosition = -1; this.lastError = null;
      this.tick(); if (this.lastError) throw this.lastError;
      if (this.playing) this.timer = window.setInterval(() => this.tick(), 25);
    }

    validate(project, mode) {
      requireSupportedProject(project);
      const end = lengthBeats(project, mode);
      // Validation walks referenced patterns once; a long song does not need
      // to allocate every repeated event merely to verify its media.
      const patterns = mode === 'song' ? [] : [selectedPattern(project)];
      if (mode === 'song') {
        const rows = project.rows || [];
        for (const row of rows) if (audible(rows, row)) for (const clip of row.clips || []) {
          if (clip.mute || finite(clip.gain, 1) === 0) continue;
          if (clip.kind === 'audio') { if (audible(project.tracks, project.tracks[clip.track || 0])) this.sample(clip.ref); }
          else if (clip.kind === 'pattern') {
            if (finite(clip.offset) !== 0) throw new Error('Pattern clips with an offset must be rendered to audio in the desktop app before browser playback.');
            const pattern = project.patterns.find(item => item.id === clip.ref); if (!pattern) throw new Error(`Missing pattern: ${clip.ref}`); patterns.push(pattern);
          } else throw new Error(`Unsupported arrangement clip kind: ${clip.kind}`);
        }
      }
      for (const pattern of new Set(patterns)) {
        if (!pattern) continue;
        for (const event of collectEvents({ ...project, patterns: [pattern], selected_pattern: 0 }, 'pattern', 0, patternLength(pattern))) {
          if (event.pad !== null) { const pad = project.pads[event.pad]; if (!pad) throw new Error(`Unknown pad: ${event.pad}`); if (pad.sample_id && audible(project.tracks, project.tracks[pad.track || 0])) this.sample(pad.sample_id); }
          else if (project.synth?.sample_source || project.synth?.sample_layer) throw new Error('This instrument uses desktop sample layers. Render it to audio in the desktop app before browser playback.');
        }
      }
      return end;
    }

    tick() {
      if (!this.playing) return;
      try {
        this.sync(); if (!this.playing) return;
        const project = this.getProject(); const now = this.context.currentTime;
        const beat = Math.max(0, this.beatAt(now)); let from = this.cursor; const until = Math.max(from, this.beatAt(now + .12));
        // If the page was suspended, skip missed events instead of a burst of
        // late notes. Audio already scheduled on the device keeps its timing.
        if (this.timeAt(from) < now) from = Math.max(from, this.beatAt(now + .005));
        const songEnd = lengthBeats(project, this.mode); const looping = this.mode === 'song' && project.loop_enabled;
        const loopStart = clamp(project.loop_start, 0, Math.max(0, songEnd), 0);
        const loopEnd = Math.max(loopStart + .25, finite(project.loop_end, songEnd));
        const span = loopEnd - loopStart;
        const songLocal = absolute => looping && absolute >= loopEnd ? loopStart + (absolute - loopEnd) % span : absolute;
        const horizon = this.mode === 'song' && !looping ? Math.min(until, songEnd) : until;
        while (from < horizon - EPSILON) {
          const localFrom = this.mode === 'song' ? songLocal(from) : from;
          const regionEnd = looping ? from + (loopEnd - localFrom) : Infinity;
          const to = Math.min(horizon, regionEnd); const offset = from - localFrom;
          if (looping && from >= loopEnd && Math.abs(localFrom - loopStart) < EPSILON) this.resumeClips(localFrom, this.timeAt(from), project, this.timeAt(regionEnd));
          for (const event of collectEvents(project, this.mode, localFrom, to - offset, 4096)) {
            const voice = this.schedule(event, this.timeAt(event.beat + offset), project, this.context, this.graph, this.voices);
            if (voice && (voice.gate || event.kind === 'audio')) voice.endBeat = Math.min(regionEnd, event.beat + offset + (event.durationBeats ?? event.clip.length_beats));
            if (looping && voice) voice.stop(this.timeAt(regionEnd), true);
          }
          if (this.metronome) for (let beatIndex = Math.ceil(localFrom - EPSILON); beatIndex < to - offset - EPSILON; beatIndex += 1) this.click(this.timeAt(beatIndex + offset), beatIndex % 4 === 0);
          from = to;
        }
        this.cursor = horizon;
        if (this.mode === 'song' && !looping && beat >= songEnd) { this.stop(); return; }
        const local = this.mode === 'pattern' ? beat % songEnd : songLocal(beat);
        const division = finite(selectedPattern(project)?.div, 4); const step = Math.floor(local * division + EPSILON);
        if (step !== this.lastPosition) { this.lastPosition = step; this.onPosition({ beat: local, step, bar: Math.floor(local / 4), mode: this.mode, playing: true }); }
      } catch (error) { this.lastError = error; this.stop(); this.onError(error); }
    }

    resumeClips(beat, when, project, boundaryTime = Infinity) {
      const rows = project.rows || [];
      for (const row of rows) if (audible(rows, row)) for (const clip of row.clips || []) {
        const elapsed = beat - clip.start_beat;
        if (clip.kind !== 'audio' || clip.mute || elapsed <= 0 || elapsed >= clip.length_beats) continue;
        if (!audible(project.tracks, project.tracks[clip.track || 0])) continue;
        const source = this.sample(clip.ref); const sourceStart = finite(clip.offset); const sourceDuration = clip.source_length > 0 ? clip.source_length : source.duration - sourceStart;
        if (!(sourceDuration > 0)) throw new Error('Audio clip source range must have positive duration.');
        const elapsedSeconds = elapsed * secondsPerBeat(project); const offset = clip.loop ? elapsedSeconds % sourceDuration : elapsedSeconds;
        if (offset >= sourceDuration) continue;
        // Phase and trim are independent: the full original range must repeat.
        const voice = this.schedule({ kind: 'audio', clip, durationBeats: clip.length_beats - elapsed, phaseSeconds: elapsedSeconds, row: row.id }, when, project, this.context, this.graph, this.voices);
        if (voice && Number.isFinite(boundaryTime)) voice.stop(boundaryTime, true);
      }
    }

    click(when, accent) {
      const source = this.context.createOscillator(); const gain = this.context.createGain(); source.frequency.value = accent ? 1200 : 800;
      gain.gain.setValueAtTime(.08, when); gain.gain.exponentialRampToValueAtTime(.0001, when + .035); source.connect(gain).connect(this.graph.master);
      const voice = { when, end: when + .04, stop: time => { try { source.stop(time); } catch { /* ended */ } this.voices.delete(voice); } };
      this.registerVoice(voice); source.onended = () => { this.voices.delete(voice); this.retiringVoices.delete(voice); source.disconnect(); gain.disconnect(); }; source.start(when); source.stop(voice.end);
    }

    stop() {
      window.clearInterval(this.timer); this.timer = null; this.playing = false;
      for (const voice of [...this.voices, ...this.retiringVoices]) voice.stop(this.context?.currentTime || 0, true);
      this.voices.clear(); this.retiringVoices.clear(); this.cursor = 0; this.lastPosition = -1;
      // Disconnect delay/reverb tails as well as source nodes. Rebuild the
      // graph to keep stopped playback silent without closing the context.
      if (this.context && this.graph) { this.graph.disconnect(); this.graph = makeGraph(this.context, this.getProject()); }
      this.onPosition({ beat: 0, step: -1, bar: 0, mode: this.mode, playing: false });
    }

    meter() {
      if (!this.graph) return { peak: 0, rms: 0, tracks: [] };
      const measure = (analyser, data) => {
        analyser.getFloatTimeDomainData(data);
        let peak = 0; let sum = 0; for (const sample of data) { peak = Math.max(peak, Math.abs(sample)); sum += sample * sample; }
        return { peak, rms: Math.sqrt(sum / data.length) };
      };
      this.meterData ||= new Float32Array(this.graph.analyser.fftSize);
      return { ...measure(this.graph.analyser, this.meterData), tracks: this.graph.trackBuses.map(bus => measure(bus.analyser, bus.meterData)) };
    }

    async render(mode = 'pattern', opts = {}) {
      if (this.rendering) throw new Error('A WAV export is already running.');
      this.rendering = true;
      let graph; const pool = new Set();
      try {
        const project = JSON.parse(JSON.stringify(this.getProject())); const beats = this.validate(project, mode);
        const buffers = new Map();
        for (const pad of project.pads) if (pad.sample_id) buffers.set(pad.sample_id, this.getBuffer(pad.sample_id));
        for (const row of project.rows || []) for (const clip of row.clips || []) if (clip.kind === 'audio') buffers.set(clip.ref, this.getBuffer(clip.ref));
        const renderer = new AudioEngine({ getProject: () => project, getBuffer: id => buffers.get(id) });
        renderer.preparedBudget = { bytes: 0 };
        if (!beats) throw new Error('The song is empty. Add arrangement clips before exporting.');
        const musicalDuration = beats * secondsPerBeat(project);
        const tail = clamp(opts.tail, 0, 10, 3); const duration = musicalDuration + tail;
        if (duration > LIMITS.renderSeconds) throw new Error(`Browser WAV export is limited to ${LIMITS.renderSeconds} seconds including effect tails. Use the desktop app for longer exports.`);
        const Context = window.OfflineAudioContext || window.webkitOfflineAudioContext;
        if (!Context) throw new Error('This browser does not support offline audio export.');
        const sampleRate = Math.round(clamp(opts.sampleRate, 22050, LIMITS.sampleRate, 44100));
        const events = collectEvents(project, mode, 0, beats);
        let estimatedNodes = 200;
        for (const event of events) {
          estimatedNodes += event.kind === 'note' && event.pad === null ? 24 : 3;
          if (estimatedNodes > LIMITS.renderNodes) throw new Error('This export exceeds the browser audio resource budget. Export fewer clips or render the song in the desktop app.');
        }
        preparedPCMBytes(events, project, id => buffers.get(id));
        const context = new Context(2, Math.ceil(duration * sampleRate), sampleRate); graph = makeGraph(context, project);
        for (let index = 0; index < events.length; index += 1) {
          renderer.schedule(events[index], events[index].beat * secondsPerBeat(project), project, context, graph, pool, true);
          if (index % 512 === 511) await new Promise(resolve => window.setTimeout(resolve, 0));
        }
        return encodeWav(await context.startRendering());
      } finally { graph?.disconnect(); pool.clear(); this.rendering = false; }
    }
  }

  window.AnharmonicAudio = Object.freeze({ AudioEngine, collectEvents, lengthBeats, swingOffset, encodeWav, requireSupportedProject, preparedPCMBytes, LIMITS });
})();
