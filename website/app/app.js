(() => {
  'use strict';

  const STORAGE_KEY = 'anharmonic-web-project-v1';
  const PAD_COUNT = 16;
  const STEP_COUNT = 16;
  const padNames = ['Kick', 'Snare', 'Closed Hat', 'Open Hat', 'Clap', 'Low Tom', 'Perc', 'Texture', 'Pad 09', 'Pad 10', 'Pad 11', 'Pad 12', 'Pad 13', 'Pad 14', 'Pad 15', 'Pad 16'];
  const state = {
    project: createProject(),
    selectedPad: 0,
    isPlaying: false,
    currentStep: -1,
    timer: null,
    nextStepTime: 0,
    audio: null,
    buffers: new Map()
  };

  const $ = selector => document.querySelector(selector);
  const projectName = $('#project-name');
  const padGrid = $('#pad-grid');
  const sequencer = $('#sequencer');
  const audioStatus = $('#audio-status');
  const toast = $('#toast');

  function createProject() {
    const steps = Array.from({ length: PAD_COUNT }, () => []);
    steps[0] = [0, 4, 8, 12];
    steps[1] = [4, 12];
    steps[2] = [0, 2, 4, 6, 8, 10, 12, 14];
    steps[3] = [7, 15];
    return { format_version: 5, name: 'Untitled session', tempo: 110, selected_pattern: 0, patterns: [{ id: 'pattern-1', name: 'pattern 1', bars: 1, div: 4, steps }] };
  }

  function render() {
    projectName.value = state.project.name;
    $('#tempo').value = state.project.tempo;
    renderPads();
    renderSequencer();
    updateStepReadout();
  }

  function renderPads() {
    padGrid.replaceChildren();
    state.project.patterns[0].steps.forEach((steps, index) => {
      const pad = document.createElement('button');
      pad.type = 'button';
      pad.className = `pad${index === state.selectedPad ? ' selected' : ''}`;
      pad.dataset.pad = index;
      pad.innerHTML = `<strong>${String(index + 1).padStart(2, '0')}</strong><small>${state.project.pads?.[index]?.name || padNames[index]}</small>`;
      pad.addEventListener('click', () => { state.selectedPad = index; updateSelection(); playPad(index); });
      padGrid.append(pad);
    });
    $('#selected-pad').textContent = `Pad ${String(state.selectedPad + 1).padStart(2, '0')} selected`;
  }

  function renderSequencer() {
    const grid = document.createElement('div');
    grid.className = 'step-grid';
    grid.append(document.createElement('span'));
    for (let step = 0; step < STEP_COUNT; step += 1) {
      const number = document.createElement('span');
      number.className = 'step-label';
      number.textContent = String(step + 1).padStart(2, '0');
      number.style.justifyContent = 'center';
      number.style.color = 'var(--dim)';
      grid.append(number);
    }
    state.project.patterns[0].steps.forEach((steps, padIndex) => {
      const label = document.createElement('div');
      label.className = 'step-label';
      label.innerHTML = `<b>${String(padIndex + 1).padStart(2, '0')}</b><span>${state.project.pads?.[padIndex]?.name || padNames[padIndex]}</span>`;
      grid.append(label);
      for (let step = 0; step < STEP_COUNT; step += 1) {
        const cell = document.createElement('button');
        cell.type = 'button';
        cell.className = `step${steps.includes(step) ? ' on' : ''}${state.currentStep === step ? ' current' : ''}`;
        cell.dataset.pad = padIndex;
        cell.dataset.step = step;
        cell.setAttribute('aria-label', `${padNames[padIndex]}, step ${step + 1}`);
        cell.addEventListener('click', () => toggleStep(padIndex, step));
        grid.append(cell);
      }
    });
    sequencer.replaceChildren(grid);
  }

  function updateSelection() {
    document.querySelectorAll('.pad').forEach((pad, index) => pad.classList.toggle('selected', index === state.selectedPad));
    $('#selected-pad').textContent = `Pad ${String(state.selectedPad + 1).padStart(2, '0')} selected`;
  }

  function toggleStep(pad, step) {
    const steps = state.project.patterns[0].steps[pad];
    const position = steps.indexOf(step);
    if (position === -1) steps.push(step);
    else steps.splice(position, 1);
    steps.sort((a, b) => a - b);
    renderSequencer();
  }

  function updateStepReadout() {
    $('#step-readout').textContent = String(Math.max(0, state.currentStep) + 1).padStart(2, '0');
  }

  function ensureAudio() {
    if (!state.audio) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) throw new Error('Web Audio is not supported in this browser.');
      const context = new AudioContextClass();
      const master = context.createGain();
      master.gain.value = 0.72;
      master.connect(context.destination);
      state.audio = { context, master };
    }
    if (state.audio.context.state === 'suspended') return state.audio.context.resume();
    return Promise.resolve();
  }

  function playPad(index, when = null, destination = null) {
    if (!state.audio) return;
    const { context, master } = state.audio;
    const start = when ?? context.currentTime;
    const output = destination || master;
    const buffer = state.buffers.get(index);
    if (buffer) {
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(output);
      source.start(start);
      flashPad(index, start - context.currentTime);
      return;
    }
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    const filter = context.createBiquadFilter();
    const isHat = index === 2 || index === 3;
    const duration = isHat ? 0.08 : index === 0 ? 0.28 : 0.18;
    oscillator.type = index === 0 ? 'sine' : isHat ? 'square' : 'triangle';
    oscillator.frequency.setValueAtTime(index === 0 ? 125 : index === 1 ? 190 : isHat ? 6200 : 300 + index * 35, start);
    if (index === 0) oscillator.frequency.exponentialRampToValueAtTime(48, start + duration);
    filter.type = 'highpass';
    filter.frequency.value = isHat ? 4500 : 45;
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(isHat ? 0.16 : 0.38, start + 0.006);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    oscillator.connect(filter).connect(gain).connect(output);
    oscillator.start(start);
    oscillator.stop(start + duration + 0.02);
    flashPad(index, start - context.currentTime);
  }

  function flashPad(index, delay) {
    window.setTimeout(() => {
      const pad = document.querySelector(`.pad[data-pad="${index}"]`);
      if (!pad) return;
      pad.classList.add('hit');
      window.setTimeout(() => pad.classList.remove('hit'), 90);
    }, Math.max(0, delay * 1000));
  }

  function startPlayback() {
    ensureAudio().then(() => {
      state.isPlaying = true;
      state.currentStep = -1;
      state.nextStepTime = state.audio.context.currentTime + 0.05;
      $('#play-toggle').classList.add('playing');
      $('#play-label').textContent = 'Pause';
      state.timer = window.setInterval(schedule, 25);
      schedule();
    }).catch(error => showToast(error.message));
  }

  function schedule() {
    if (!state.audio || !state.isPlaying) return;
    const secondsPerStep = 60 / Number(state.project.tempo) / 4;
    while (state.nextStepTime < state.audio.context.currentTime + 0.12) {
      state.currentStep = (state.currentStep + 1) % STEP_COUNT;
      state.project.patterns[0].steps.forEach((steps, pad) => { if (steps.includes(state.currentStep)) playPad(pad, state.nextStepTime); });
      const scheduledStep = state.currentStep;
      window.setTimeout(() => { if (state.isPlaying) { updateStepReadout(); renderSequencer(); } }, Math.max(0, (state.nextStepTime - state.audio.context.currentTime) * 1000));
      state.nextStepTime += secondsPerStep;
      if (scheduledStep === STEP_COUNT - 1) showToast('Looping pattern');
    }
  }

  function stopPlayback() {
    state.isPlaying = false;
    window.clearInterval(state.timer);
    state.timer = null;
    state.currentStep = -1;
    $('#play-toggle').classList.remove('playing');
    $('#play-label').textContent = 'Play';
    updateStepReadout();
    renderSequencer();
  }

  function serializeProject() {
    return JSON.stringify({ ...state.project, name: projectName.value.trim() || 'Untitled session', tempo: Number($('#tempo').value) }, null, 2);
  }

  function saveProject() {
    state.project.name = projectName.value.trim() || 'Untitled session';
    state.project.tempo = Number($('#tempo').value);
    localStorage.setItem(STORAGE_KEY, serializeProject());
    showToast('Project saved locally');
  }

  function exportProject() {
    state.project.name = projectName.value.trim() || 'Untitled session';
    state.project.tempo = Number($('#tempo').value);
    download(`${state.project.name.replace(/[^a-z0-9_-]+/gi, '-')}.json`, serializeProject(), 'application/json');
    showToast('Project JSON exported');
  }

  function importProject(file) {
    file.text().then(text => {
      const imported = JSON.parse(text);
      if (!imported || !Array.isArray(imported.patterns) || !Array.isArray(imported.patterns[0]?.steps)) throw new Error('Unsupported project format');
      state.project = { ...createProject(), ...imported, patterns: [{ ...createProject().patterns[0], ...imported.patterns[0] }] };
      state.project.pads = state.project.pads || Array.from({ length: PAD_COUNT }, () => ({}));
      render();
      saveProject();
      showToast('Project JSON imported');
    }).catch(error => showToast(`Could not import project: ${error.message}`));
  }

  function download(filename, content, type) {
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([content], { type }));
    link.download = filename;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  function loadAudio(file) {
    ensureAudio().then(() => file.arrayBuffer()).then(data => state.audio.context.decodeAudioData(data)).then(buffer => {
      state.buffers.set(state.selectedPad, buffer);
      state.project.pads = state.project.pads || Array.from({ length: PAD_COUNT }, () => ({}));
      state.project.pads[state.selectedPad] = { name: file.name.replace(/\.[^.]+$/, '') };
      $('#sample-name').textContent = file.name;
      $('#sample-meta').textContent = `${buffer.duration.toFixed(2)} seconds · ${buffer.sampleRate} Hz · assigned to Pad ${String(state.selectedPad + 1).padStart(2, '0')}`;
      drawWaveform(buffer);
      renderPads();
      renderSequencer();
      showToast('Sample loaded');
    }).catch(error => showToast(`Could not load audio: ${error.message}`));
  }

  function drawWaveform(buffer) {
    const wave = $('#sample-wave');
    wave.replaceChildren();
    const data = buffer.getChannelData(0);
    const bars = 80;
    for (let i = 0; i < bars; i += 1) {
      const start = Math.floor(i * data.length / bars);
      const end = Math.max(start + 1, Math.floor((i + 1) * data.length / bars));
      let peak = 0;
      for (let j = start; j < end; j += 1) peak = Math.max(peak, Math.abs(data[j]));
      const bar = document.createElement('i');
      bar.style.height = `${Math.max(4, peak * 58)}px`;
      wave.append(bar);
    }
  }

  async function exportWav() {
    const Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!Offline) { showToast('Offline audio is not supported here'); return; }
    const tempo = Number($('#tempo').value);
    const duration = (60 / tempo) * 4 + 0.5;
    const offline = new Offline(2, Math.ceil(44100 * duration), 44100);
    const master = offline.createGain();
    master.gain.value = 0.72;
    master.connect(offline.destination);
    const stepDuration = 60 / tempo / 4;
    state.project.patterns[0].steps.forEach((steps, pad) => steps.forEach(step => renderOfflinePad(offline, master, pad, step * stepDuration)));
    const rendered = await offline.startRendering();
    download(`${(projectName.value.trim() || 'anharmonic-session').replace(/[^a-z0-9_-]+/gi, '-')}.wav`, audioBufferToWav(rendered), 'audio/wav');
    showToast('WAV exported');
  }

  function renderOfflinePad(context, destination, index, start) {
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    const duration = index === 2 || index === 3 ? 0.08 : index === 0 ? 0.28 : 0.18;
    oscillator.type = index === 0 ? 'sine' : index === 2 || index === 3 ? 'square' : 'triangle';
    oscillator.frequency.setValueAtTime(index === 0 ? 125 : index === 1 ? 190 : index > 1 ? 6200 : 300, start);
    if (index === 0) oscillator.frequency.exponentialRampToValueAtTime(48, start + duration);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(index > 1 ? 0.16 : 0.38, start + 0.006);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    oscillator.connect(gain).connect(destination);
    oscillator.start(start);
    oscillator.stop(start + duration + 0.02);
  }

  function audioBufferToWav(buffer) {
    const channels = buffer.numberOfChannels;
    const length = buffer.length * channels * 2 + 44;
    const view = new DataView(new ArrayBuffer(length));
    const write = (offset, text) => [...text].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
    write(0, 'RIFF'); view.setUint32(4, length - 8, true); write(8, 'WAVE'); write(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, channels, true); view.setUint32(24, buffer.sampleRate, true); view.setUint32(28, buffer.sampleRate * channels * 2, true); view.setUint16(32, channels * 2, true); view.setUint16(34, 16, true); write(36, 'data'); view.setUint32(40, length - 44, true);
    const data = Array.from({ length: buffer.length }, (_, index) => Array.from({ length: channels }, (_, channel) => buffer.getChannelData(channel)[index]));
    let offset = 44;
    data.forEach(frame => frame.forEach(sample => { view.setInt16(offset, Math.max(-1, Math.min(1, sample)) * 0x7fff, true); offset += 2; }));
    return view;
  }

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add('visible');
    window.clearTimeout(showToast.timeout);
    showToast.timeout = window.setTimeout(() => toast.classList.remove('visible'), 2200);
  }

  $('#play-toggle').addEventListener('click', () => state.isPlaying ? stopPlayback() : startPlayback());
  $('#stop-button').addEventListener('click', stopPlayback);
  $('#tempo-down').addEventListener('click', () => { $('#tempo').value = Math.max(40, Number($('#tempo').value) - 1); state.project.tempo = Number($('#tempo').value); });
  $('#tempo-up').addEventListener('click', () => { $('#tempo').value = Math.min(220, Number($('#tempo').value) + 1); state.project.tempo = Number($('#tempo').value); });
  $('#tempo').addEventListener('change', event => { state.project.tempo = Math.max(40, Math.min(220, Number(event.target.value) || 110)); event.target.value = state.project.tempo; });
  $('#audio-file').addEventListener('change', event => { if (event.target.files[0]) loadAudio(event.target.files[0]); event.target.value = ''; });
  $('#preview-sample').addEventListener('click', () => ensureAudio().then(() => playPad(state.selectedPad)).catch(error => showToast(error.message)));
  $('#clear-pattern').addEventListener('click', () => { state.project.patterns[0].steps = Array.from({ length: PAD_COUNT }, () => []); renderSequencer(); showToast('Pattern cleared'); });
  $('#save-project').addEventListener('click', saveProject);
  $('#export-project').addEventListener('click', exportProject);
  $('#import-project').addEventListener('click', () => $('#project-file').click());
  $('#project-file').addEventListener('change', event => { if (event.target.files[0]) importProject(event.target.files[0]); event.target.value = ''; });
  $('#new-project').addEventListener('click', () => { stopPlayback(); state.project = createProject(); state.buffers.clear(); $('#sample-name').textContent = 'No sample loaded'; $('#sample-meta').textContent = 'Import a WAV, MP3, or browser-supported audio file'; $('#sample-wave').replaceChildren(); render(); showToast('New project ready'); });
  $('#export-wav').addEventListener('click', () => exportWav().catch(error => showToast(`Export failed: ${error.message}`)));
  projectName.addEventListener('change', () => { state.project.name = projectName.value.trim() || 'Untitled session'; });
  window.addEventListener('keydown', event => { if (event.target.matches('input')) return; const index = Number(event.key) - 1; if (index >= 0 && index < 8) { ensureAudio().then(() => playPad(index)); } if (event.code === 'Space') { event.preventDefault(); state.isPlaying ? stopPlayback() : startPlayback(); } });

  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved) { try { state.project = { ...createProject(), ...JSON.parse(saved) }; showToast('Restored local project'); } catch { localStorage.removeItem(STORAGE_KEY); } }
  state.project.pads = state.project.pads || Array.from({ length: PAD_COUNT }, () => ({}));
  audioStatus.textContent = window.AudioContext || window.webkitAudioContext ? 'Audio engine ready' : 'Web Audio unavailable';
  render();
})();
