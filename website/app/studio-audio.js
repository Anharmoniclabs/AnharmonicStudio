// Studio audio operations. Dependencies are supplied by the application.
window.AnharmonicStudioParts = window.AnharmonicStudioParts || {};
window.AnharmonicStudioParts.audio = context => {
  const {state, projectStore, restoreMedia, padRecord, padBuffer, $, setStatus, padMediaId} =
      context;
  async function ensureAudio() {
    if (!state.audio) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) throw new Error('Web Audio is unavailable in this browser.');
      const context = new AudioContextClass();
      const master = context.createGain();
      master.gain.value = projectStore.project.master;
      const tone = context.createBiquadFilter();
      tone.type = 'lowshelf';
      tone.frequency.value = 180;
      tone.gain.value = 0;
      const compressor = context.createDynamicsCompressor();
      compressor.threshold.value = -8;
      compressor.ratio.value = 3;
      compressor.attack.value = .01;
      compressor.release.value = .18;
      const delay = context.createDelay(1);
      delay.delayTime.value = .22;
      const delayGain = context.createGain();
      delayGain.gain.value = 0;
      master.connect(tone).connect(compressor).connect(context.destination);
      master.connect(delay).connect(delayGain).connect(compressor);
      const trackBuses = Array.from({length: 8}, () => {
        const gain = context.createGain();
        const pan = context.createStereoPanner();
        gain.connect(pan).connect(master);
        return {gain, pan};
      });
      state.audio = {context, master, tone, compressor, delayGain, trackBuses};
    }
    await restoreMedia();
    return state.audio.context.state === 'suspended' ? state.audio.context.resume() :
                                                       Promise.resolve();
  }

  function playPad(index, when = null) {
    if (!state.audio) return;
    const {context} = state.audio;
    const trackIndex = padRecord(index).track || 0;
    if (!trackAudible(trackIndex)) return;
    const output = trackOutput(trackIndex);
    const start = when ?? context.currentTime;
    const buffer = padBuffer(index);
    if (buffer) {
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(output);
      source.start(start);
    } else {
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      const hat = index === 2 || index === 3;
      const duration = hat ? 0.08 : index === 0 ? 0.28 : 0.18;
      oscillator.type = index === 0 ? 'sine' : hat ? 'square' : 'triangle';
      oscillator.frequency.setValueAtTime(
          index === 0     ? 125 :
              index === 1 ? 190 :
              hat         ? 6200 :
                            280 + index * 35,
          start);
      if (index === 0) oscillator.frequency.exponentialRampToValueAtTime(48, start + duration);
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(hat ? 0.14 : 0.32, start + 0.006);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
      oscillator.connect(gain).connect(output);
      oscillator.start(start);
      oscillator.stop(start + duration + 0.02);
    }
    window.setTimeout(() => {
      const pad = $(`.pad[data-pad="${index}"]`);
      if (pad) {
        pad.classList.add('hit');
        window.setTimeout(() => pad.classList.remove('hit'), 90);
      }
    }, Math.max(0, (start - context.currentTime) * 1000));
  }

  function trackAudible(trackIndex) {
    const tracks = projectStore.project.tracks;
    const soloed = tracks.some(track => track.solo);
    return !tracks[trackIndex]?.mute && (!soloed || Boolean(tracks[trackIndex]?.solo));
  }

  function trackOutput(trackIndex) {
    const track = projectStore.project.tracks[trackIndex] || projectStore.project.tracks[0];
    const bus = state.audio.trackBuses[trackIndex] || state.audio.trackBuses[0];
    bus.gain.value = trackAudible(trackIndex) ? track.gain : 0;
    bus.pan.pan.value = track.pan;
    return bus.gain;
  }

  function playSynthVoice(note, velocity = .8, preview = false) {
    ensureAudio()
        .then(() => {
          const synth = projectStore.project.synth;
          const context = state.audio.context;
          if (!trackAudible(synth.track || 0)) return;
          const start = context.currentTime;
          const frequency = 440 * Math.pow(2, (note - 69) / 12);
          const output = context.createGain();
          const filter = context.createBiquadFilter();
          filter.type = 'lowpass';
          filter.frequency.value = synth.cutoff;
          filter.Q.value = synth.resonance * 18;
          const osc1 = context.createOscillator();
          const osc2 = context.createOscillator();
          const waveform = type => type === 'saw' ? 'sawtooth' : type;
          osc1.type = waveform(synth.osc1);
          osc2.type = waveform(synth.osc2);
          osc1.frequency.value = frequency;
          osc2.frequency.value = frequency * Math.pow(2, synth.osc2_octave || 0);
          osc1.detune.value = -synth.detune / 2;
          osc2.detune.value = synth.detune / 2;
          const mix1 = context.createGain();
          const mix2 = context.createGain();
          mix1.gain.value = 1 - synth.osc_mix;
          mix2.gain.value = synth.osc_mix;
          osc1.connect(mix1).connect(filter);
          osc2.connect(mix2).connect(filter);
          filter.connect(output).connect(trackOutput(synth.track || 0));
          const peak = Math.max(.0001, synth.volume * velocity);
          output.gain.setValueAtTime(.0001, start);
          output.gain.exponentialRampToValueAtTime(peak, start + Math.max(.001, synth.attack));
          output.gain.exponentialRampToValueAtTime(
              Math.max(.0001, peak * synth.sustain),
              start + Math.max(.001, synth.attack) + Math.max(.001, synth.decay));
          osc1.start(start);
          osc2.start(start);
          let released = false;
          const voice = {
            release: () => {
              if (released) return;
              released = true;
              const end = context.currentTime + Math.max(.01, synth.release);
              output.gain.cancelAndHoldAtTime(context.currentTime);
              output.gain.exponentialRampToValueAtTime(.0001, end);
              osc1.stop(end);
              osc2.stop(end);
              osc2.onended = () => {
                osc1.disconnect();
                osc2.disconnect();
                mix1.disconnect();
                mix2.disconnect();
                filter.disconnect();
                output.disconnect();
              };
            }
          };
          if (!preview) {
            state.synthVoices.get(note)?.release();
            state.synthVoices.set(note, voice);
          } else
            window.setTimeout(voice.release, 500);
        })
        .catch(error => setStatus(`instrument failed: ${error.message}`));
  }

  function startArp() {
    if (state.arpTimer || !projectStore.project.arp.enabled) return;
    state.arpTimer = window.setInterval(() => {
      const held = [...state.heldSynth].sort((a, b) => a - b);
      if (!held.length) return;
      const arp = projectStore.project.arp;
      const ordered = arp.mode === 'down' ? held.reverse() : held;
      const note = ordered[state.arpIndex % ordered.length] +
          (Math.floor(state.arpIndex / Math.max(1, ordered.length)) % Math.max(1, arp.octaves)) *
              12;
      state.arpIndex += 1;
      playSynthVoice(note, .8);
    }, Math.max(40, 60000 / state.tempo * projectStore.project.arp.rate_beats));
  }

  function stopArp() {
    window.clearInterval(state.arpTimer);
    state.arpTimer = null;
    state.arpIndex = 0;
  }

  function synthNoteOn(note, velocity = .8, preview = false) {
    if (!preview && projectStore.project.arp.enabled) {
      state.heldSynth.add(note);
      startArp();
      return;
    }
    playSynthVoice(note, velocity, preview);
  }

  function synthNoteOff(note) {
    state.heldSynth.delete(note);
    const voice = state.synthVoices.get(note);
    if (voice) {
      voice.release();
      state.synthVoices.delete(note);
    }
    if (!state.heldSynth.size) stopArp();
  }

  function renderOfflineHit(context, destination, index, start) {
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    const hat = index === 2 || index === 3;
    const duration = hat ? 0.08 : index === 0 ? 0.28 : 0.18;
    oscillator.type = index === 0 ? 'sine' : hat ? 'square' : 'triangle';
    oscillator.frequency.setValueAtTime(
        index === 0     ? 125 :
            index === 1 ? 190 :
            hat         ? 6200 :
                          280 + index * 35,
        start);
    if (index === 0) oscillator.frequency.exponentialRampToValueAtTime(48, start + duration);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(hat ? 0.14 : 0.32, start + 0.006);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    oscillator.connect(gain).connect(destination);
    oscillator.start(start);
    oscillator.stop(start + duration + 0.02);
  }

  function previewSelection() {
    ensureAudio()
        .then(() => {
          const buffer = padBuffer(state.selectedPad);
          if (!buffer) return playPad(state.selectedPad);
          const selection = state.selections.get(padMediaId(state.selectedPad)) ||
              {start: 0, end: buffer.duration};
          const source = state.audio.context.createBufferSource();
          source.buffer = buffer;
          source.connect(state.audio.master);
          source.start(0, selection.start, selection.end - selection.start);
        })
        .catch(error => setStatus(error.message));
  }
  return {
    ensureAudio,
    playPad,
    trackAudible,
    trackOutput,
    playSynthVoice,
    startArp,
    stopArp,
    synthNoteOn,
    synthNoteOff,
    renderOfflineHit,
    previewSelection
  };
};
