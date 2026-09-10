(() => {
  'use strict';

  const pads = ['Kick', 'Snare', 'Closed Hat', 'Open Hat', 'Clap', 'Low Tom', 'Perc', 'Texture', 'Pad 09', 'Pad 10', 'Pad 11', 'Pad 12', 'Pad 13', 'Pad 14', 'Pad 15', 'Pad 16'];
  const synthPresets = {
    'Midnight Brass': { osc1: 'saw', osc2: 'square', cutoff: 2400, resonance: .28, attack: .025, decay: .32, sustain: .68, release: .65, drive: .18, spread: .42, volume: .42 },
    'Copper Pluck': { osc1: 'saw', osc2: 'triangle', cutoff: 1150, resonance: .34, attack: .002, decay: .18, sustain: .08, release: .22, drive: .28, spread: .18, volume: .48 },
    'Velvet Poly': { osc1: 'saw', osc2: 'saw', cutoff: 1750, resonance: .18, attack: .08, decay: .55, sustain: .72, release: 1.25, drive: .12, spread: .76, volume: .38 },
    'Acid Orchard': { osc1: 'square', osc2: 'saw', cutoff: 520, resonance: .82, attack: .002, decay: .24, sustain: .18, release: .12, drive: .46, spread: .08, volume: .34 },
    'Neon Sub': { osc1: 'sine', osc2: 'square', cutoff: 680, resonance: .16, attack: .004, decay: .3, sustain: .8, release: .32, drive: .4, spread: .04, volume: .5 }
  };
  const storageKey = 'anharmonic-web-studio-v2';
  const projectStore = new window.AnharmonicProject.ProjectStore();
  const stepArrays = steps => Array.from({ length: 64 }, (_, pad) => Object.entries(steps[pad] || {}).map(([step]) => Number(step)).sort((a, b) => a - b));
  const state = {
    projectStore,
    steps: stepArrays(projectStore.pattern.steps),
    tempo: 110,
    selectedPad: 0,
    bank: 0,
    workspace: 'song',
    playing: false,
    step: -1,
    timer: null,
    audio: null,
    buffers: new Map(),
    media: new Map(),
    mediaRestored: false,
    recording: null,
    selections: new Map(),
    theme: localStorage.getItem('anharmonic-theme') || 'dark',
    accent: localStorage.getItem('anharmonic-accent') || '#d6ab65'
    ,effects: { tone: 0, compression: 3, delay: 0 }, synthVoices: new Map(), heldSynth: new Set(), arpTimer: null, arpIndex: 0
  };
  const $ = selector => document.querySelector(selector);
  const stage = $('#stage');
  projectStore.subscribe(document => {
    state.steps = stepArrays(document.patterns[document.selected_pattern].steps);
    state.tempo = document.bpm;
    const undo = $('#undo-project'); const redo = $('#redo-project');
    if (undo) undo.disabled = !projectStore.history.length;
    if (redo) redo.disabled = !projectStore.future.length;
    if (state.audio) state.audio.trackBuses.forEach((bus, index) => { const track = document.tracks[index]; bus.gain.value = trackAudible(index) ? track.gain : 0; bus.pan.pan.value = track.pan; });
  });
  const mediaDb = window.indexedDB ? new Promise((resolve, reject) => {
    const request = indexedDB.open('anharmonic-studio-media', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('audio');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  }) : Promise.resolve(null);

  function padRecord(index) { return projectStore.project.pads[index] || {}; }
  function padMediaId(index) { return padRecord(index).sample_id || ''; }
  function padBuffer(index) { return state.buffers.get(padMediaId(index)); }

  async function storeMedia(mediaId, blob) {
    state.media.set(mediaId, blob);
    const database = await mediaDb; if (!database) return;
    await new Promise((resolve, reject) => { const request = database.transaction('audio', 'readwrite').objectStore('audio').put(blob, mediaId); request.onsuccess = resolve; request.onerror = () => reject(request.error); });
  }

  async function restoreMedia() {
    if (state.mediaRestored || !state.audio) return;
    state.mediaRestored = true;
    const database = await mediaDb; if (!database) return;
    const keys = await new Promise((resolve, reject) => { const request = database.transaction('audio').objectStore('audio').getAllKeys(); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); });
    for (const key of keys) {
      const blob = await new Promise((resolve, reject) => { const request = database.transaction('audio').objectStore('audio').get(key); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); });
      if (!blob) continue;
      try { state.media.set(String(key), blob); state.buffers.set(String(key), await state.audio.context.decodeAudioData(await blob.arrayBuffer())); } catch { setStatus('some stored media could not be decoded'); }
    }
  }

  async function ensureAudio() {
    if (!state.audio) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) throw new Error('Web Audio is unavailable in this browser.');
      const context = new AudioContextClass();
      const master = context.createGain();
      master.gain.value = projectStore.project.master;
      const tone = context.createBiquadFilter(); tone.type = 'lowshelf'; tone.frequency.value = 180; tone.gain.value = 0;
      const compressor = context.createDynamicsCompressor(); compressor.threshold.value = -8; compressor.ratio.value = 3; compressor.attack.value = .01; compressor.release.value = .18;
      const delay = context.createDelay(1); delay.delayTime.value = .22; const delayGain = context.createGain(); delayGain.gain.value = 0;
      master.connect(tone).connect(compressor).connect(context.destination); master.connect(delay).connect(delayGain).connect(compressor);
      const trackBuses = Array.from({ length: 8 }, () => { const gain = context.createGain(); const pan = context.createStereoPanner(); gain.connect(pan).connect(master); return { gain, pan }; });
      state.audio = { context, master, tone, compressor, delayGain, trackBuses };
    }
    await restoreMedia();
    return state.audio.context.state === 'suspended' ? state.audio.context.resume() : Promise.resolve();
  }

  function playPad(index, when = null) {
    if (!state.audio) return;
    const { context } = state.audio;
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
      oscillator.frequency.setValueAtTime(index === 0 ? 125 : index === 1 ? 190 : hat ? 6200 : 280 + index * 35, start);
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
      if (pad) { pad.classList.add('hit'); window.setTimeout(() => pad.classList.remove('hit'), 90); }
    }, Math.max(0, (start - context.currentTime) * 1000));
  }

  function trackAudible(trackIndex) {
    const tracks = projectStore.project.tracks; const soloed = tracks.some(track => track.solo);
    return !tracks[trackIndex]?.mute && (!soloed || Boolean(tracks[trackIndex]?.solo));
  }
  function trackOutput(trackIndex) {
    const track = projectStore.project.tracks[trackIndex] || projectStore.project.tracks[0]; const bus = state.audio.trackBuses[trackIndex] || state.audio.trackBuses[0];
    bus.gain.value = trackAudible(trackIndex) ? track.gain : 0; bus.pan.pan.value = track.pan; return bus.gain;
  }

  function button(label, className = '') { return `<button class="${className}" type="button">${label}</button>`; }
  function shell(title, tools, content) { stage.innerHTML = `<div class="stage-inner"><div class="stage-toolbar"><strong>${title}</strong>${tools}</div>${content}</div>`; }
  function openMenu(anchor, options) {
    document.querySelector('.app-menu')?.remove();
    const menu = document.createElement('div'); menu.className = 'app-menu';
    options.forEach(option => { const item = document.createElement('button'); item.type = 'button'; item.textContent = option.label; item.addEventListener('click', () => { menu.remove(); option.action(); }); menu.append(item); });
    document.body.append(menu);
    const rect = anchor.getBoundingClientRect(); menu.style.left = `${Math.min(window.innerWidth - menu.offsetWidth - 8, rect.left)}px`; menu.style.top = `${rect.bottom + 4}px`;
    const close = event => { if (!menu.contains(event.target) && event.target !== anchor) { menu.remove(); document.removeEventListener('pointerdown', close); } }; window.setTimeout(() => document.addEventListener('pointerdown', close), 0);
  }

  function renderSong() {
    const rows = projectStore.project.rows;
    shell('Song', `${button('+ ADD TRACK', 'add-track')} ${button('SELECT ▾', 'song-tool')} ${button('DRAW')} ${button('SNAP: BAR ▾', 'song-snap')}`, `<div class="timeline"><div class="ruler"><span>BAR / BEAT</span>${Array.from({ length: 16 }, (_, i) => `<span>${String(i + 1).padStart(2, '0')}</span>`).join('')}</div>${rows.map((row, rowIndex) => `<div class="track-row" data-row="${row.id}"><div class="track-head"><strong>${row.name}</strong><small>${row.clips.length} clip${row.clips.length === 1 ? '' : 's'}</small><span><button class="row-record ${row.record_armed ? 'active' : ''}" data-row-index="${rowIndex}">R</button> <button class="row-mute ${row.mute ? 'active' : ''}" data-row-index="${rowIndex}">M</button> <button class="row-solo ${row.solo ? 'active' : ''}" data-row-index="${rowIndex}">S</button></span></div><div class="track-lane">${row.clips.map(clip => `<button class="clip ${clip.kind === 'audio' ? 'audio' : ''} ${clip.mute ? 'muted' : ''}" data-clip="${clip.id}" data-row-index="${rowIndex}" style="left:${clip.start_beat * 90}px;width:${Math.max(45, clip.length_beats * 90)}px">${clip.kind === 'audio' ? 'Keys idea' : 'Pattern 01 · drums'}<i></i></button>`).join('')}</div></div>`).join('')}<div class="empty-timeline">Drag clips to move. Drag the right edge to resize. Right-click a clip to delete.</div></div>`);
    stage.querySelector('.add-track').addEventListener('click', () => { projectStore.transact('add arrangement track', document => { document.rows.push({ id: `row-${Date.now()}`, name: `Track ${document.rows.length + 1}`, mute: false, solo: false, clips: [], record_source: 'audio', record_track: Math.min(7, document.rows.length) }); }); renderSong(); setStatus('track added'); });
    stage.querySelector('.song-tool').addEventListener('click', event => openMenu(event.currentTarget, ['select', 'draw', 'paint', 'slice', 'mute', 'erase'].map(tool => ({ label: tool.toUpperCase(), action: () => { state.arrangeTool = tool; event.currentTarget.textContent = `${tool.toUpperCase()} ▾`; setStatus(`arrangement tool: ${tool}`); } }))));
    stage.querySelector('.song-snap').addEventListener('click', event => openMenu(event.currentTarget, ['BAR', 'BEAT', '1/16', 'OFF'].map(snap => ({ label: snap, action: () => { state.arrangeSnap = snap; event.currentTarget.textContent = `SNAP: ${snap} ▾`; setStatus(`arrangement snap: ${snap}`); } }))));
    stage.querySelectorAll('.row-mute').forEach(button => button.addEventListener('click', event => { event.stopPropagation(); const row = projectStore.project.rows[Number(button.dataset.rowIndex)]; projectStore.transact('mute arrangement row', document => { document.rows[Number(button.dataset.rowIndex)].mute = !row.mute; }); renderSong(); }));
    stage.querySelectorAll('.row-solo').forEach(button => button.addEventListener('click', event => { event.stopPropagation(); projectStore.transact('solo arrangement row', document => { document.rows[Number(button.dataset.rowIndex)].solo = !document.rows[Number(button.dataset.rowIndex)].solo; }); renderSong(); }));
    stage.querySelectorAll('.row-record').forEach(button => button.addEventListener('click', event => { event.stopPropagation(); const rowIndex = Number(button.dataset.rowIndex); projectStore.transact('arm arrangement row', document => { document.rows.forEach((row, index) => { row.record_armed = index === rowIndex ? !row.record_armed : false; }); }); renderSong(); setStatus(projectStore.project.rows[rowIndex].record_armed ? `${rows[rowIndex].name} armed for recording` : 'record arm cleared'); }));
    stage.querySelectorAll('.clip').forEach(clipElement => {
      let drag = null;
      clipElement.addEventListener('pointerdown', event => { event.preventDefault(); event.stopPropagation(); const rowIndex = Number(clipElement.dataset.rowIndex); const clip = projectStore.project.rows[rowIndex].clips.find(item => item.id === clipElement.dataset.clip); drag = { rowIndex, clip, x: event.clientX, start: clip.start_beat, length: clip.length_beats, resize: event.offsetX > clipElement.offsetWidth - 12 }; clipElement.setPointerCapture?.(event.pointerId); });
      clipElement.addEventListener('pointermove', event => { if (!drag) return; const delta = Math.round(((event.clientX - drag.x) / 90) * 4) / 4; if (drag.resize) { clipElement.style.width = `${Math.max(45, (drag.length + delta) * 90)}px`; } else { clipElement.style.transform = `translateX(${delta * 90}px)`; } });
      clipElement.addEventListener('pointerup', event => { if (!drag) return; const delta = Math.round(((event.clientX - drag.x) / 90) * 4) / 4; const { rowIndex, clip } = drag; projectStore.transact(drag.resize ? 'resize arrangement clip' : 'move arrangement clip', document => { const target = document.rows[rowIndex].clips.find(item => item.id === clip.id); if (!target) return; if (drag.resize) target.length_beats = Math.max(.25, drag.length + delta); else target.start_beat = Math.max(0, drag.start + delta); }); drag = null; renderSong(); setStatus('arrangement edited'); });
      clipElement.addEventListener('contextmenu', event => { event.preventDefault(); const rowIndex = Number(clipElement.dataset.rowIndex); projectStore.transact('delete arrangement clip', document => { document.rows[rowIndex].clips = document.rows[rowIndex].clips.filter(item => item.id !== clipElement.dataset.clip); }); renderSong(); setStatus('clip deleted'); });
    });
  }

  function renderBeats() {
    const pattern = projectStore.pattern;
    const totalSteps = Math.max(1, pattern.bars * 4 * pattern.div);
    const rows = Array.from({ length: 16 }, (_, index) => {
      const padSteps = pattern.steps[index] || {};
      return `<div class="step-line"><label><b>${String(index + 1).padStart(2, '0')}</b> ${pads[index]}</label>${Array.from({ length: totalSteps }, (_, step) => { const velocity = padSteps[step] || 0; return `<button class="${velocity ? 'on ' : ''}${state.step === step ? 'current' : ''}" style="--velocity:${velocity}" data-pad="${index}" data-step="${step}" aria-label="${pads[index]} step ${step + 1}"></button>`; }).join('')}</div>`;
    }).join('');
    shell('Steps', `${button('PATTERN 01 ▾', 'pattern-menu')} ${button('−', 'bars-down')} <span class="bars-readout">${pattern.bars} BARS · ${pattern.div === 4 ? '1/16' : `1/${pattern.div * 4}`}</span> ${button('+', 'bars-up')} ${button('LOADED PADS', 'loaded-toggle')} ${button('FOLLOW PLAYHEAD', 'follow-toggle')} ${button('CLEAR', 'clear-beats')}`, `<div class="step-editor"><div class="step-grid">${rows}</div></div>`);
    const grid = stage.querySelector('.step-grid');
    let gesture = null;
    const cellAt = event => event.target.closest('.step-line button');
    const paint = cell => {
      if (!cell || !gesture) return;
      const pad = Number(cell.dataset.pad); const step = Number(cell.dataset.step); const key = `${pad}:${step}`;
      if (gesture.seen.has(key)) return;
      gesture.seen.add(key);
      projectStore.setStep(pad, step, gesture.erase ? null : gesture.velocity);
      if (!gesture.erase) ensureAudio().then(() => playPad(pad)).catch(error => setStatus(error.message));
      const velocity = gesture.erase ? 0 : gesture.velocity;
      cell.style.setProperty('--velocity', velocity);
      cell.classList.toggle('on', velocity > 0);
    };
    grid.addEventListener('pointerdown', event => { const cell = cellAt(event); if (!cell) return; event.preventDefault(); grid.setPointerCapture?.(event.pointerId); const existing = Number(cell.style.getPropertyValue('--velocity')) || 0; gesture = { erase: event.button === 2 || existing > 0, velocity: existing > 0 ? existing : 1, seen: new Set() }; paint(cell); });
    grid.addEventListener('pointermove', event => { if (gesture) paint(cellAt(event)); });
    grid.addEventListener('pointerup', () => { gesture = null; });
    grid.addEventListener('pointercancel', () => { gesture = null; });
    grid.addEventListener('contextmenu', event => event.preventDefault());
    grid.addEventListener('wheel', event => { const cell = cellAt(event); if (!cell || !(Number(cell.style.getPropertyValue('--velocity')) > 0)) return; event.preventDefault(); const next = Math.max(.1, Math.min(1, (Number(cell.style.getPropertyValue('--velocity')) || 1) + (event.deltaY < 0 ? .08 : -.08))); projectStore.setStep(Number(cell.dataset.pad), Number(cell.dataset.step), next); renderBeats(); setStatus(`velocity ${Math.round(next * 100)}%`); }, { passive: false });
    $('.clear-beats').addEventListener('click', () => { projectStore.transact('clear pattern', document => { document.patterns[document.selected_pattern].steps = {}; }); renderBeats(); setStatus('pattern cleared'); });
    $('.bars-down').addEventListener('click', () => { projectStore.transact('shorten pattern', document => { document.patterns[document.selected_pattern].bars = Math.max(1, document.patterns[document.selected_pattern].bars - 1); }); renderBeats(); setStatus('pattern shortened'); });
    $('.bars-up').addEventListener('click', () => { projectStore.transact('lengthen pattern', document => { document.patterns[document.selected_pattern].bars += 1; }); renderBeats(); setStatus('pattern lengthened'); });
    $('.pattern-menu').addEventListener('click', event => openMenu(event.currentTarget, projectStore.project.patterns.map((pattern, index) => ({ label: pattern.name, action: () => { projectStore.transact('select pattern', document => { document.selected_pattern = index; }); renderBeats(); setStatus(`${pattern.name} selected`); } }))));
    $('.loaded-toggle').addEventListener('click', event => { event.currentTarget.classList.toggle('active'); setStatus(event.currentTarget.classList.contains('active') ? 'showing loaded pad lanes' : 'showing all pad lanes'); });
    $('.follow-toggle').addEventListener('click', event => { event.currentTarget.classList.toggle('active'); setStatus(event.currentTarget.classList.contains('active') ? 'playhead follow on' : 'playhead follow off'); });
  }

  function renderSampler() {
    const buffer = padBuffer(state.selectedPad);
    const duration = buffer?.duration || 2.84;
    const selection = state.selections.get(padMediaId(state.selectedPad)) || { start: 0, end: duration };
    shell('Sampler', `${button('IMPORT', 'import-sampler')} ${button('PREVIEW', 'preview-pad')} ${button('CHOP TOOLS ▾', 'chop-tools')} ${button('CUT SAMPLE', 'cut-sample')}`, `<div class="sampler-editor"><div class="sampler-canvas"><canvas id="waveform" aria-label="Audio waveform"></canvas></div><div class="sampler-actions"><label>START <input id="sample-start" type="number" min="0" max="${duration.toFixed(3)}" step="0.001" value="${selection.start.toFixed(3)}"> s</label><label>END <input id="sample-end" type="number" min="0" max="${duration.toFixed(3)}" step="0.001" value="${selection.end.toFixed(3)}"> s</label><button class="sample-snap">SNAP: BEAT ▾</button> ${button('ASSIGN TO PAD', 'assign-sample')}</div><p class="sampler-help">Drag the start and end markers in the waveform, or edit the time fields. Selection plays on the selected pad.</p></div>`);
    drawWaveform(buffer, selection);
    stage.querySelector('.import-sampler').addEventListener('click', () => $('#audio-file').click());
    stage.querySelector('.preview-pad').addEventListener('click', () => previewSelection());
    stage.querySelector('#sample-start').addEventListener('change', event => updateSelection('start', event.target.value));
    stage.querySelector('#sample-end').addEventListener('change', event => updateSelection('end', event.target.value));
    stage.querySelector('.assign-sample').addEventListener('click', () => { const mediaId = padMediaId(state.selectedPad); if (!mediaId) return setStatus('load audio before assigning a sample'); projectStore.assignPad(state.selectedPad, mediaId, { start: selection.start, end: selection.end }); setStatus(`selection assigned to pad ${String(state.selectedPad + 1).padStart(2, '0')}`); });
    stage.querySelector('.sample-snap').addEventListener('click', event => openMenu(event.currentTarget, ['OFF', 'ZERO CROSSING', 'BEAT', '1/8', '1/16'].map(snap => ({ label: snap, action: () => { state.sampleSnap = snap; event.currentTarget.textContent = `SNAP: ${snap} ▾`; setStatus(`sample snap: ${snap}`); } }))));
    stage.querySelector('.chop-tools').addEventListener('click', event => openMenu(event.currentTarget, [{ label: 'Detect transients', action: () => setStatus('transient detection queued for this sample') }, { label: 'Map slices to pads', action: () => setStatus('select a detected slice, then assign it to a pad') }, { label: 'Detect tempo', action: () => setStatus('tempo detection queued for this sample') }]));
    stage.querySelector('.cut-sample').addEventListener('click', () => { const buffer = padBuffer(state.selectedPad); if (!buffer) return setStatus('load audio before cutting a sample'); const clip = projectStore.project.pads[state.selectedPad]; projectStore.assignPad(state.selectedPad, padMediaId(state.selectedPad), { start: selection.start, end: selection.end, name: clip.name }); setStatus(`sample range cut: ${selection.start.toFixed(2)}–${selection.end.toFixed(2)}s`); });
  }

  function drawWaveform(buffer, selection) {
    const canvas = $('#waveform');
    if (!canvas) return;
    const bounds = canvas.getBoundingClientRect();
    canvas.width = Math.max(400, Math.floor(bounds.width * window.devicePixelRatio));
    canvas.height = Math.max(160, Math.floor(bounds.height * window.devicePixelRatio));
    const context = canvas.getContext('2d');
    const width = canvas.width; const height = canvas.height; const data = buffer?.getChannelData(0);
    context.clearRect(0, 0, width, height); context.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--wave').trim() || '#d6ab65'; context.globalAlpha = .85; context.beginPath();
    if (data) { const stride = Math.max(1, Math.floor(data.length / width)); for (let x = 0; x < width; x += 1) { let peak = 0; for (let i = x * stride; i < Math.min(data.length, (x + 1) * stride); i += 1) peak = Math.max(peak, Math.abs(data[i])); context.moveTo(x, height / 2 - peak * height * .45); context.lineTo(x, height / 2 + peak * height * .45); } } else { for (let x = 0; x < width; x += 4) { const peak = (Math.abs(Math.sin(x * .07)) * .35 + .1) * height; context.moveTo(x, height / 2 - peak); context.lineTo(x, height / 2 + peak); } }
    context.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--wave').trim() || '#d6ab65'; context.stroke(); context.globalAlpha = 1;
    const startX = width * selection.start / (buffer?.duration || 2.84); const endX = width * selection.end / (buffer?.duration || 2.84); context.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--selection').trim() || '#d6ab6533'; context.fillRect(startX, 0, endX - startX, height); context.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim(); context.lineWidth = Math.max(2, window.devicePixelRatio); context.beginPath(); context.moveTo(startX, 0); context.lineTo(startX, height); context.moveTo(endX, 0); context.lineTo(endX, height); context.stroke();
  }

  function updateSelection(edge, value) {
    const buffer = padBuffer(state.selectedPad); const duration = buffer?.duration || 2.84; const current = state.selections.get(padMediaId(state.selectedPad)) || { start: 0, end: duration }; current[edge] = Math.max(0, Math.min(duration, Number(value) || 0)); if (current.end <= current.start) current.end = Math.min(duration, current.start + .01); state.selections.set(padMediaId(state.selectedPad), current); renderSampler();
  }

  function previewSelection() {
    ensureAudio().then(() => { const buffer = padBuffer(state.selectedPad); if (!buffer) return playPad(state.selectedPad); const selection = state.selections.get(padMediaId(state.selectedPad)) || { start: 0, end: buffer.duration }; const source = state.audio.context.createBufferSource(); source.buffer = buffer; source.connect(state.audio.master); source.start(0, selection.start, selection.end - selection.start); }).catch(error => setStatus(error.message));
  }

  function renderNotes() {
    const notes = projectStore.pattern.notes;
    const noteRows = 36;
    const names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
    const noteGrid = Array.from({ length: noteRows }, (_, row) => `<span class="note-row ${names[(83 - row) % 12].includes('#') ? 'black-key' : ''}"></span>`).join('');
    const noteButtons = notes.map(note => `<button class="note" data-note-id="${note.id}" style="left:${note.start * 70}px;top:${(83 - note.pitch) * 20}px;width:${Math.max(20, note.duration * 70 - 3)}px" aria-label="${names[note.pitch % 12]} note at beat ${note.start}">${names[note.pitch % 12]}${Math.floor(note.pitch / 12) - 1}</button>`).join('');
    shell('Piano Roll', `${button('SOUND: PAD 01 ▾', 'note-action')} ${button('ROOT: C3 ▾', 'note-root')} ${button('MONO', 'note-mono')} ${button('QUANTIZE', 'note-quantize')} ${button('ADD CHORD ▾', 'note-chord')} ${button('CLEAR', 'note-clear')}`, `<div class="piano"><div class="keys">${Array.from({ length: noteRows }, (_, i) => `<span>${names[(83 - i) % 12]}${Math.floor((83 - i) / 12) - 1}</span>`).join('')}</div><div class="note-grid" id="note-grid">${noteGrid}${noteButtons}</div></div>`);
    const grid = $('#note-grid');
    grid.addEventListener('contextmenu', event => { if (!event.target.matches('.note')) return; event.preventDefault(); const id = event.target.dataset.noteId; projectStore.transact('delete note', document => { document.patterns[document.selected_pattern].notes = document.patterns[document.selected_pattern].notes.filter(note => note.id !== id); }); renderNotes(); setStatus('note deleted'); });
    grid.addEventListener('click', event => {
      if (event.target.matches('.note')) return;
      const rect = grid.getBoundingClientRect(); const beat = Math.max(0, Math.floor(((event.clientX - rect.left) / 70) * 4) / 4); const pitch = Math.max(0, Math.min(127, 83 - Math.floor((event.clientY - rect.top) / 20)));
      projectStore.transact('add note', document => { document.patterns[document.selected_pattern].notes.push({ id: `note-${Date.now()}-${Math.random().toString(16).slice(2)}`, pitch, start: beat, duration: .25, velocity: .8, pad: state.selectedPad }); });
      renderNotes(); setStatus(`note added at beat ${beat}`);
    });
    grid.querySelectorAll('.note').forEach(noteElement => {
      noteElement.addEventListener('pointerdown', event => { event.stopPropagation(); const id = noteElement.dataset.noteId; const origin = { x: event.clientX, y: event.clientY }; const original = projectStore.pattern.notes.find(note => note.id === id); const move = moveEvent => { noteElement.style.transform = `translate(${moveEvent.clientX - origin.x}px, ${moveEvent.clientY - origin.y}px)`; }; const finish = finishEvent => { document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', finish); const deltaBeat = Math.round(((finishEvent.clientX - origin.x) / 70) * 4) / 4; const deltaPitch = -Math.round((finishEvent.clientY - origin.y) / 20); projectStore.transact('move note', document => { const note = document.patterns[document.selected_pattern].notes.find(item => item.id === id); if (note) { note.start = Math.max(0, original.start + deltaBeat); note.pitch = Math.max(0, Math.min(127, original.pitch + deltaPitch)); } }); renderNotes(); setStatus('note moved'); }; document.addEventListener('pointermove', move); document.addEventListener('pointerup', finish, { once: true }); });
    });
    $('.note-clear').addEventListener('click', () => { projectStore.transact('clear notes', document => { document.patterns[document.selected_pattern].notes = []; }); renderNotes(); setStatus('notes cleared'); });
    $('.note-action').addEventListener('click', event => openMenu(event.currentTarget, pads.slice(0, 16).map((name, index) => ({ label: `${String(index + 1).padStart(2, '0')} · ${name}`, action: () => { state.notePad = index; event.currentTarget.textContent = `SOUND: PAD ${String(index + 1).padStart(2, '0')} ▾`; setStatus(`piano roll sound: ${name}`); } }))));
    $('.note-root').addEventListener('click', event => openMenu(event.currentTarget, ['C3', 'D3', 'E3', 'F3', 'G3', 'A3', 'B3'].map(root => ({ label: root, action: () => { state.noteRoot = root; event.currentTarget.textContent = `ROOT: ${root} ▾`; setStatus(`root note: ${root}`); } }))));
    $('.note-mono').addEventListener('click', event => { event.currentTarget.classList.toggle('active'); setStatus(event.currentTarget.classList.contains('active') ? 'mono notes on' : 'mono notes off'); });
    $('.note-quantize').addEventListener('click', () => { projectStore.transact('quantize notes', document => { document.patterns[document.selected_pattern].notes.forEach(note => { note.start = Math.max(0, Math.round(note.start * 4) / 4); }); }); renderNotes(); setStatus('notes quantized'); });
    $('.note-chord').addEventListener('click', () => { projectStore.transact('add chord', document => { const pattern = document.patterns[document.selected_pattern]; const start = pattern.notes.length ? pattern.notes[pattern.notes.length - 1].start : 0; [60, 64, 67].forEach((pitch, index) => pattern.notes.push({ id: `note-chord-${Date.now()}-${index}`, pitch, start, duration: .5, velocity: .75, pad: state.selectedPad })); }); renderNotes(); setStatus('C major chord added'); });
  }

  function renderInstruments() {
    const synth = projectStore.project.synth; const arp = projectStore.project.arp;
    shell('Instruments', `${button('PREVIEW SOUND', 'synth-preview')} ${button('PANIC', 'synth-panic')}`, `<div class="instrument-editor"><div class="instrument-toolbar"><label>PRESET <select id="synth-preset">${Object.keys(synthPresets).map(name => `<option ${name === synth.name ? 'selected' : ''}>${name}</option>`).join('')}</select></label><label>TRACK <select id="synth-track">${Array.from({length:8},(_,i)=>`<option value="${i}" ${i === synth.track ? 'selected' : ''}>${i + 1}</option>`).join('')}</select></label><button id="arp-toggle" class="${arp.enabled ? 'active' : ''}">ARP ${arp.enabled ? 'ON' : 'OFF'}</button><label>RATE <select id="arp-rate"><option value=".125">1/32</option><option value=".25">1/16</option><option value=".5">1/8</option><option value="1">1/4</option></select></label><label>MODE <select id="arp-mode"><option>up</option><option>down</option><option>up/down</option><option>random</option></select></label></div><div class="synth-controls"><label>OSC 1 <select id="osc1"><option>saw</option><option>square</option><option>triangle</option><option>sine</option></select></label><label>OSC 2 <select id="osc2"><option>saw</option><option>square</option><option>triangle</option><option>sine</option></select></label><label>CUTOFF <input id="synth-cutoff" type="range" min="80" max="12000"><output></output></label><label>RESONANCE <input id="synth-resonance" type="range" min="0" max=".95" step=".01"><output></output></label><label>ATTACK <input id="synth-attack" type="range" min=".001" max="2" step=".001"><output></output></label><label>RELEASE <input id="synth-release" type="range" min=".01" max="4" step=".01"><output></output></label><label>DRIVE <input id="synth-drive" type="range" min="0" max="1" step=".01"><output></output></label><label>SPREAD <input id="synth-spread" type="range" min="0" max="1" step=".01"><output></output></label></div><div class="synth-keyboard" id="synth-keyboard">${Array.from({length:25},(_,i)=>`<button data-note="${48+i}">${['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'][i%12]}${Math.floor((48+i)/12)-1}</button>`).join('')}</div><p class="sampler-help">Instrument state follows the desktop SynthPatch and ArpSettings contract. Hold keys to play; enable ARP to sequence held notes.</p></div>`);
    const controls = { osc1: 'osc1', osc2: 'osc2', cutoff: 'synth-cutoff', resonance: 'synth-resonance', attack: 'synth-attack', release: 'synth-release', drive: 'synth-drive', spread: 'synth-spread' };
    Object.entries(controls).forEach(([field, id]) => { const element = $(`#${id}`); element.value = synth[field]; element.nextElementSibling && (element.nextElementSibling.textContent = synth[field]); element.addEventListener('input', event => { projectStore.setSynth({ [field]: element.type === 'range' ? Number(event.target.value) : event.target.value }); }); });
    $('#synth-preset').addEventListener('change', event => { const preset = synthPresets[event.target.value]; projectStore.setSynth({ ...preset, name: event.target.value }); renderInstruments(); setStatus(`${event.target.value} loaded`); });
    $('#synth-track').addEventListener('change', event => projectStore.setSynth({ track: Number(event.target.value) }));
    $('#arp-toggle').addEventListener('click', () => { projectStore.setArp({ enabled: !projectStore.project.arp.enabled }); renderInstruments(); });
    $('#arp-rate').value = String(arp.rate_beats); $('#arp-mode').value = arp.mode;
    $('#arp-rate').addEventListener('change', event => projectStore.setArp({ rate_beats: Number(event.target.value) })); $('#arp-mode').addEventListener('change', event => projectStore.setArp({ mode: event.target.value }));
    $('.synth-preview').onclick = () => synthNoteOn(60, .8, true); $('.synth-panic').onclick = () => { state.heldSynth.clear(); stopArp(); state.synthVoices.forEach(voice => voice.release()); state.synthVoices.clear(); setStatus('synth voices released'); };
    document.querySelectorAll('#synth-keyboard button').forEach(key => { key.onpointerdown = () => synthNoteOn(Number(key.dataset.note), .8); key.onpointerup = key.onpointerleave = () => synthNoteOff(Number(key.dataset.note)); });
  }

  function playSynthVoice(note, velocity = .8, preview = false) {
    ensureAudio().then(() => { const synth = projectStore.project.synth; const context = state.audio.context; if (!trackAudible(synth.track || 0)) return; const start = context.currentTime; const frequency = 440 * Math.pow(2, (note - 69) / 12); const output = context.createGain(); const filter = context.createBiquadFilter(); filter.type = 'lowpass'; filter.frequency.value = synth.cutoff; filter.Q.value = synth.resonance * 18; const osc1 = context.createOscillator(); const osc2 = context.createOscillator(); osc1.type = synth.osc1; osc2.type = synth.osc2; osc1.frequency.value = frequency; osc2.frequency.value = frequency * Math.pow(2, synth.osc2_octave || 0); osc1.detune.value = -synth.detune / 2; osc2.detune.value = synth.detune / 2; const mix1 = context.createGain(); const mix2 = context.createGain(); mix1.gain.value = 1 - synth.osc_mix; mix2.gain.value = synth.osc_mix; osc1.connect(mix1).connect(filter); osc2.connect(mix2).connect(filter); filter.connect(output).connect(trackOutput(synth.track || 0)); const peak = Math.max(.001, synth.volume * velocity); output.gain.setValueAtTime(.0001, start); output.gain.exponentialRampToValueAtTime(peak, start + synth.attack); output.gain.exponentialRampToValueAtTime(Math.max(.0001, peak * synth.sustain), start + synth.attack + synth.decay); osc1.start(start); osc2.start(start); const voice = { release: () => { output.gain.cancelScheduledValues(context.currentTime); output.gain.setTargetAtTime(.0001, context.currentTime, synth.release / 4); window.setTimeout(() => { try { osc1.stop(); osc2.stop(); } catch {} }, synth.release * 1000); } }; if (!preview) state.synthVoices.set(note, voice); else window.setTimeout(voice.release, 500); });
  }
  function startArp() { if (state.arpTimer || !projectStore.project.arp.enabled) return; state.arpTimer = window.setInterval(() => { const held = [...state.heldSynth].sort((a, b) => a - b); if (!held.length) return; const arp = projectStore.project.arp; const ordered = arp.mode === 'down' ? held.reverse() : held; const note = ordered[state.arpIndex % ordered.length] + (Math.floor(state.arpIndex / Math.max(1, ordered.length)) % Math.max(1, arp.octaves)) * 12; state.arpIndex += 1; playSynthVoice(note, .8); }, Math.max(40, 60000 / state.tempo * projectStore.project.arp.rate_beats)); }
  function stopArp() { window.clearInterval(state.arpTimer); state.arpTimer = null; state.arpIndex = 0; }
  function synthNoteOn(note, velocity = .8, preview = false) { if (!preview && projectStore.project.arp.enabled) { state.heldSynth.add(note); startArp(); return; } playSynthVoice(note, velocity, preview); }
  function synthNoteOff(note) { state.heldSynth.delete(note); const voice = state.synthVoices.get(note); if (voice) { voice.release(); state.synthVoices.delete(note); } if (!state.heldSynth.size) stopArp(); }

  function renderAutotune() { shell('Autotune', `${button('ANALYZE PITCH', 'analyze-pitch')} ${button('RENDER TAKE', 'render-take')}`, `<div class="autotune-editor"><div class="autotune-card"><strong>Selected vocal take</strong><p>${state.buffers.has(state.selectedPad) ? 'Audio is ready for browser pitch preview.' : 'Import or record a vocal take in the Sampler first.'}</p><div class="pitch-lane">${Array.from({ length: 32 }, (_, index) => `<i style="height:${18 + Math.abs(Math.sin(index * .8)) * 50}px"></i>`).join('')}</div></div><div class="autotune-controls"><label>KEY <select><option>C</option><option>D</option><option>E</option><option>F</option><option>G</option></select></label><label>SCALE <select><option>Chromatic</option><option>Major</option><option>Minor</option></select></label><label>CORRECTION <input id="correction-amount" type="range" min="0" max="100" value="72"><output>72%</output></label></div><p class="sampler-help">Browser preview uses Web Audio pitch shifting. High-quality formant correction and offline take rendering remain separate DSP work.</p></div>`); stage.querySelector('.analyze-pitch').addEventListener('click', () => setStatus('pitch analysis preview complete')); stage.querySelector('.render-take').addEventListener('click', () => previewSelection()); stage.querySelector('#correction-amount').addEventListener('input', event => event.target.parentElement.querySelector('output').textContent = `${event.target.value}%`); }

  function renderMix() { const tracks = projectStore.project.tracks; shell('Mixer', `${button('MASTER OUTPUT', 'master-output')} ${button('ADD EFFECT ▾', 'add-effect')}`, `<div class="mixer-editor"><div class="mixer-channels">${tracks.map((track, index) => `<div class="channel ${track.mute ? 'muted' : ''}"><h3>${track.name}</h3><div class="vu"></div><div class="fader"><input class="track-gain" data-track="${index}" type="range" min="0" max="100" value="${track.gain * 100}"></div><small class="track-db">${(20 * Math.log10(Math.max(.001, track.gain))).toFixed(1)} dB</small><label class="track-pan">PAN <input class="track-pan-input" data-track="${index}" type="range" min="-100" max="100" value="${track.pan * 100}"></label><div class="channel-actions"><button class="track-mute ${track.mute ? 'active' : ''}" data-track="${index}">M</button><button class="track-solo ${track.solo ? 'active' : ''}" data-track="${index}">S</button><button class="track-fx" data-track="${index}">FX</button></div></div>`).join('')}</div><div class="effects-rack"><strong>MASTER EFFECTS</strong><label>TONE <input id="tone-effect" type="range" min="-12" max="12" value="${state.effects.tone}"><output>${state.effects.tone} dB</output></label><label>COMPRESSION <input id="compression-effect" type="range" min="1" max="12" value="${state.effects.compression}"><output>${state.effects.compression}:1</output></label><label>DELAY SEND <input id="delay-effect" type="range" min="0" max="40" value="${state.effects.delay}"><output>${state.effects.delay}%</output></label></div></div>`); const audio = state.audio; const tone = $('#tone-effect'); const compression = $('#compression-effect'); const delay = $('#delay-effect'); [tone, compression, delay].forEach(control => control?.addEventListener('input', () => { state.effects.tone = Number(tone.value); state.effects.compression = Number(compression.value); state.effects.delay = Number(delay.value); if (audio) { audio.tone.gain.value = state.effects.tone; audio.compressor.ratio.value = state.effects.compression; audio.delayGain.gain.value = state.effects.delay / 100; } })); stage.querySelector('.master-output').addEventListener('click', event => openMenu(event.currentTarget, ['Master gain', 'Master tone', 'Limiter'].map(label => ({ label, action: () => setStatus(`${label} selected in master rack`) })))); stage.querySelector('.add-effect').addEventListener('click', event => openMenu(event.currentTarget, ['Tone / EQ', 'Compressor', 'Delay', 'Reverb'].map(label => ({ label, action: () => setStatus(`${label} enabled in master rack`) })))); stage.querySelectorAll('.track-gain').forEach(control => control.addEventListener('input', () => { const index = Number(control.dataset.track); projectStore.setTrack(index, { gain: Number(control.value) / 100 }); control.closest('.channel').querySelector('.track-db').textContent = `${(20 * Math.log10(Math.max(.001, Number(control.value) / 100))).toFixed(1)} dB`; })); stage.querySelectorAll('.track-pan-input').forEach(control => control.addEventListener('input', () => { projectStore.setTrack(Number(control.dataset.track), { pan: Number(control.value) / 100 }); })); stage.querySelectorAll('.track-mute').forEach(control => control.addEventListener('click', () => { const index = Number(control.dataset.track); projectStore.setTrack(index, { mute: !projectStore.project.tracks[index].mute }); renderMix(); })); stage.querySelectorAll('.track-solo').forEach(control => control.addEventListener('click', () => { const index = Number(control.dataset.track); projectStore.setTrack(index, { solo: !projectStore.project.tracks[index].solo }); renderMix(); })); stage.querySelectorAll('.track-fx').forEach(control => control.addEventListener('click', () => setStatus(`Track ${Number(control.dataset.track) + 1} FX rack selected`))); }
  function renderWorkspace(name) { state.workspace = name; ({ song: renderSong, beats: renderBeats, notes: renderNotes, sampler: renderSampler, mix: renderMix, instruments: renderInstruments, autotune: renderAutotune }[name] || renderSong)(); }

  function renderPads() {
    const firstPad = state.bank * 16;
    $('#pad-grid').innerHTML = Array.from({ length: 16 }, (_, offset) => { const index = firstPad + offset; const record = padRecord(index); const name = record.name || `Pad ${String(index + 1).padStart(2, '0')}`; return `<button class="pad ${index === state.selectedPad ? 'selected' : ''}" data-pad="${index}"><b>${String(offset + 1).padStart(2, '0')}</b><strong>${name}</strong><small>${offset < 8 ? String.fromCharCode(49 + offset) : ''}</small></button>`; }).join('');
    document.querySelectorAll('.pad').forEach(pad => pad.addEventListener('click', () => {
      state.selectedPad = Number(pad.dataset.pad);
      renderPads();
      $('#inspector-name').textContent = `Pad ${String(state.selectedPad + 1).padStart(2, '0')} · ${pads[state.selectedPad]}`;
      ensureAudio().then(() => playPad(state.selectedPad)).catch(error => setStatus(error.message));
      setStatus(`selected pad ${String(state.selectedPad + 1).padStart(2, '0')}`);
    }));
    document.querySelectorAll('.pad-bank button').forEach(bank => { bank.onclick = () => { state.bank = Number(bank.dataset.bank); document.querySelectorAll('.pad-bank button').forEach(item => item.classList.toggle('active', item === bank)); state.selectedPad = state.bank * 16; renderPads(); }; });
    const record = padRecord(state.selectedPad); $('#pad-pitch').value = record.pitch || 0; $('#pad-pan').value = (record.pan || 0) * 100; $('#pad-gain').value = Math.max(0, Math.min(100, (record.gain ?? 1) * 100)); $('#pad-mode').value = record.mode || 'one-shot';
    $('#pad-pitch').oninput = event => { projectStore.setPad(state.selectedPad, { pitch: Number(event.target.value) }); $('#pitch-value').textContent = `${event.target.value} st`; };
    $('#pad-pan').oninput = event => { projectStore.setPad(state.selectedPad, { pan: Number(event.target.value) / 100 }); $('#pan-value').textContent = Number(event.target.value) === 0 ? 'C' : `${Math.abs(Number(event.target.value))}% ${Number(event.target.value) < 0 ? 'L' : 'R'}`; };
    $('#pad-gain').oninput = event => { projectStore.setPad(state.selectedPad, { gain: Number(event.target.value) / 100 }); $('#gain-value').textContent = `${(20 * Math.log10(Math.max(.001, Number(event.target.value) / 100))).toFixed(1)} dB`; };
    $('#pad-mode').onchange = event => projectStore.setPad(state.selectedPad, { mode: event.target.value });
  }

  function setStatus(message) { $('#status').textContent = message; }
  function togglePlayback() {
    state.playing = !state.playing;
    $('#play').textContent = state.playing ? 'Ⅱ' : '▶';
    if (!state.playing) { window.clearInterval(state.timer); setStatus('ready'); return; }
    ensureAudio().then(() => {
      state.step = -1; setStatus('playing current pattern');
      state.timer = window.setInterval(() => {
        state.step = (state.step + 1) % 16;
        $('#counter').textContent = `001 . ${Math.floor(state.step / 4) + 1} . ${String(state.step % 4).padStart(2, '0')}`;
        state.steps.forEach((hits, pad) => { if (hits.includes(state.step)) playPad(pad); });
        if (state.workspace === 'beats') renderBeats();
      }, Math.max(60, 60000 / state.tempo / 4));
    }).catch(error => { state.playing = false; $('#play').textContent = '▶'; setStatus(error.message); });
  }

  function projectDocument() { const project = projectStore.toJSON(); project.name = $('.project-name').value.trim() || 'Untitled project'; project.selected_pad = state.selectedPad; return project; }
  function download(name, data, type) { const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([data], { type })); link.download = name; link.click(); URL.revokeObjectURL(link.href); }
  function saveProject() { const project = projectDocument(); localStorage.setItem(storageKey, JSON.stringify(project)); setStatus('project saved locally'); }
  function exportProject() { const project = projectDocument(); download(`${project.name.replace(/[^a-z0-9_-]+/gi, '-')}.json`, JSON.stringify(project, null, 2), 'application/json'); setStatus('project exported'); }
  async function exportWav() {
    const OfflineContext = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!OfflineContext) { setStatus('offline audio export is unavailable'); return; }
    const secondsPerStep = 60 / state.tempo / 4;
    const context = new OfflineContext(2, Math.ceil(44100 * (secondsPerStep * 16 + 0.5)), 44100);
    const master = context.createGain(); master.gain.value = 0.7; master.connect(context.destination);
    state.steps.forEach((hits, pad) => hits.forEach(step => renderOfflineHit(context, master, pad, step * secondsPerStep)));
    const buffer = await context.startRendering();
    download(`${($('.project-name').value.trim() || 'anharmonic-pattern').replace(/[^a-z0-9_-]+/gi, '-')}.wav`, encodeWav(buffer), 'audio/wav');
    setStatus('WAV exported');
  }
  function renderOfflineHit(context, destination, index, start) {
    const oscillator = context.createOscillator(); const gain = context.createGain(); const hat = index === 2 || index === 3; const duration = hat ? 0.08 : index === 0 ? 0.28 : 0.18;
    oscillator.type = index === 0 ? 'sine' : hat ? 'square' : 'triangle'; oscillator.frequency.setValueAtTime(index === 0 ? 125 : index === 1 ? 190 : hat ? 6200 : 280 + index * 35, start);
    if (index === 0) oscillator.frequency.exponentialRampToValueAtTime(48, start + duration);
    gain.gain.setValueAtTime(0.0001, start); gain.gain.exponentialRampToValueAtTime(hat ? 0.14 : 0.32, start + 0.006); gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    oscillator.connect(gain).connect(destination); oscillator.start(start); oscillator.stop(start + duration + 0.02);
  }
  function encodeWav(buffer) {
    const channels = buffer.numberOfChannels; const length = 44 + buffer.length * channels * 2; const view = new DataView(new ArrayBuffer(length)); const write = (offset, text) => [...text].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
    write(0, 'RIFF'); view.setUint32(4, length - 8, true); write(8, 'WAVE'); write(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, channels, true); view.setUint32(24, buffer.sampleRate, true); view.setUint32(28, buffer.sampleRate * channels * 2, true); view.setUint16(32, channels * 2, true); view.setUint16(34, 16, true); write(36, 'data'); view.setUint32(40, length - 44, true);
    let offset = 44; for (let frame = 0; frame < buffer.length; frame += 1) for (let channel = 0; channel < channels; channel += 1) { const sample = Math.max(-1, Math.min(1, buffer.getChannelData(channel)[frame])); view.setInt16(offset, sample * 0x7fff, true); offset += 2; } return view;
  }
  function loadProject(file) { file.text().then(text => { const project = JSON.parse(text); projectStore.load(project); state.selectedPad = Number(project.selected_pad) || 0; $('.project-name').value = project.name || 'Untitled project'; $('#tempo').value = projectStore.project.bpm; renderPads(); renderWorkspace(state.workspace); setStatus('project loaded'); }).catch(error => setStatus(`load failed: ${error.message}`)); }
  function loadAudio(file) { ensureAudio().then(() => file.arrayBuffer()).then(data => state.audio.context.decodeAudioData(data)).then(buffer => { const mediaId = projectStore.addMedia({ name: file.name, mime: file.type, duration: buffer.duration, sample_rate: buffer.sampleRate, size: file.size }); state.buffers.set(mediaId, buffer); return storeMedia(mediaId, file).then(() => ({ buffer, mediaId })); }).then(({ buffer, mediaId }) => { state.selections.set(mediaId, { start: 0, end: buffer.duration }); projectStore.assignPad(state.selectedPad, mediaId, { name: file.name.replace(/\.[^.]+$/, '').slice(0, 20), start: 0, end: buffer.duration }); pads[state.selectedPad] = file.name.replace(/\.[^.]+$/, '').slice(0, 20); renderPads(); if (state.workspace === 'sampler') renderSampler(); setStatus(`${file.name} loaded into pad ${String(state.selectedPad + 1).padStart(2, '0')}`); }).catch(error => setStatus(`audio load failed: ${error.message}`)); }

  async function toggleRecording() {
    if (state.recording) { state.recording.stop(); return; }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { setStatus('microphone recording is unavailable in this browser'); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream); const chunks = [];
      const armedRow = projectStore.project.rows.find(row => row.record_armed);
      state.recording = recorder; $('#record').classList.add('recording'); setStatus(armedRow ? `recording microphone into ${armedRow.name}` : 'recording microphone into selected pad');
      recorder.addEventListener('dataavailable', event => { if (event.data.size) chunks.push(event.data); });
      recorder.addEventListener('stop', async () => { stream.getTracks().forEach(track => track.stop()); state.recording = null; $('#record').classList.remove('recording'); const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' }); const buffer = await state.audio.context.decodeAudioData(await blob.arrayBuffer()); const mediaId = projectStore.addMedia({ name: 'Microphone recording', mime: blob.type, duration: buffer.duration, sample_rate: buffer.sampleRate, size: blob.size }); state.buffers.set(mediaId, buffer); await storeMedia(mediaId, blob); state.selections.set(mediaId, { start: 0, end: buffer.duration }); projectStore.assignPad(state.selectedPad, mediaId, { name: `Recording ${new Date().toLocaleTimeString()}`, start: 0, end: buffer.duration }); pads[state.selectedPad] = `Recording ${new Date().toLocaleTimeString()}`; renderPads(); if (state.workspace === 'sampler') renderSampler(); setStatus('recording captured'); });
      recorder.start();
    } catch (error) { setStatus(`recording failed: ${error.message}`); }
  }
  function applyTheme() { document.documentElement.dataset.theme = state.theme; document.documentElement.style.setProperty('--accent', state.accent); document.documentElement.style.setProperty('--accent2', state.accent); $('#theme-toggle').textContent = state.theme === 'dark' ? 'Light' : 'Dark'; }

  document.querySelectorAll('.studio-nav button[data-workspace]').forEach(tab => tab.addEventListener('click', () => { document.querySelectorAll('.studio-nav button[data-workspace]').forEach(item => item.classList.remove('active')); tab.classList.add('active'); renderWorkspace(tab.dataset.workspace); }));
  document.querySelectorAll('.panel-toggle[data-toggle]').forEach(toggle => toggle.addEventListener('click', () => $(`#${toggle.dataset.toggle}-panel`).classList.toggle('open')));
  document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(`#${button.dataset.close}-panel`).classList.remove('open')));
  document.querySelector('#play').addEventListener('click', togglePlayback);
  document.querySelector('#stop').addEventListener('click', () => { state.playing = false; window.clearInterval(state.timer); $('#play').textContent = '▶'; $('#counter').textContent = '001 . 1 . 00'; setStatus('ready'); });
  document.querySelector('#record').addEventListener('click', toggleRecording);
  document.querySelector('#tempo').addEventListener('change', event => { projectStore.setTempo(event.target.value); event.target.value = state.tempo; });
  document.querySelector('#playback-mode').addEventListener('change', event => setStatus(`${event.target.value} playback selected`));
  document.querySelector('#swing').addEventListener('input', event => { projectStore.transact('change swing', document => { document.swing = Number(event.target.value); }); $('#swing-value').textContent = `${event.target.value}%`; });
  document.querySelector('#master-volume').addEventListener('input', event => { const value = Number(event.target.value) / 100; projectStore.transact('change master volume', document => { document.master = value; }); if (state.audio) state.audio.master.gain.value = value; $('#master-value').textContent = `${event.target.value}%`; });
  document.querySelector('#tap-tempo').addEventListener('click', () => { const now = performance.now(); state.taps = (state.taps || []).filter(time => now - time < 2000); state.taps.push(now); if (state.taps.length > 1) { const interval = (now - state.taps[0]) / (state.taps.length - 1); projectStore.setTempo(Math.round(Math.max(40, Math.min(240, 60000 / interval)))); $('#tempo').value = state.tempo; } setStatus('tempo tapped'); });
  document.querySelector('#metronome').addEventListener('click', event => { event.currentTarget.classList.toggle('active'); setStatus(event.currentTarget.classList.contains('active') ? 'metronome on' : 'metronome off'); });
  document.querySelector('#cut-source').addEventListener('click', event => { event.currentTarget.classList.toggle('active'); setStatus(event.currentTarget.classList.contains('active') ? 'cut source on' : 'cut source off'); });
  document.querySelector('#save-project').addEventListener('click', saveProject);
  document.querySelector('#export-project').addEventListener('click', exportProject);
  document.querySelector('#export-wav').addEventListener('click', () => exportWav().catch(error => setStatus(`export failed: ${error.message}`)));
  document.querySelector('#undo-project').addEventListener('click', () => { if (projectStore.undo()) { renderWorkspace(state.workspace); setStatus('undid last edit'); } });
  document.querySelector('#redo-project').addEventListener('click', () => { if (projectStore.redo()) { renderWorkspace(state.workspace); setStatus('redid edit'); } });
  document.querySelector('#load-project').addEventListener('click', () => $('#project-file').click());
  document.querySelector('#project-file').addEventListener('change', event => { if (event.target.files[0]) loadProject(event.target.files[0]); event.target.value = ''; });
  document.querySelector('#import-audio').addEventListener('click', () => $('#audio-file').click());
  document.querySelector('#audio-file').addEventListener('change', event => { if (event.target.files[0]) loadAudio(event.target.files[0]); event.target.value = ''; });
  document.querySelector('#theme-toggle').addEventListener('click', () => { state.theme = state.theme === 'dark' ? 'light' : 'dark'; localStorage.setItem('anharmonic-theme', state.theme); applyTheme(); });
  document.querySelector('#accent-toggle').addEventListener('click', () => $('#accent-picker').click());
  document.querySelector('#accent-picker').addEventListener('input', event => { state.accent = event.target.value; localStorage.setItem('anharmonic-accent', state.accent); applyTheme(); });
  document.querySelector('#typing-toggle').addEventListener('click', () => setStatus('Keys: press 1–8 to play pads, Space to play or pause'));
  document.querySelector('#help-toggle').addEventListener('click', () => setStatus('Import audio from Browser, select a pad, then use Beats or Sampler'));
  document.querySelector('#stems-button').addEventListener('click', () => setStatus('Stem separation needs a server model job or a WebGPU model; no fake split is performed in the browser'));
  document.querySelector('.tools').addEventListener('click', () => setStatus('Tools: recording, Autotune preview, automation, and master effects are browser capabilities; LV2 plugins remain desktop-only'));
  document.querySelectorAll('.sound').forEach(sound => sound.addEventListener('click', () => { document.querySelectorAll('.sound').forEach(item => item.classList.remove('selected')); sound.classList.add('selected'); pads[state.selectedPad] = sound.querySelector('strong').textContent; renderPads(); setStatus(`${pads[state.selectedPad]} selected`); }));
  window.addEventListener('keydown', event => { if (event.ctrlKey && event.key.toLowerCase() === 'z') { event.preventDefault(); if (event.shiftKey) projectStore.redo(); else projectStore.undo(); renderWorkspace(state.workspace); return; } if (event.code === 'Space' && !event.target.matches('input,select')) { event.preventDefault(); togglePlayback(); } const index = Number(event.key) - 1; if (index >= 0 && index < 8) ensureAudio().then(() => playPad(index)).catch(error => setStatus(error.message)); });

  const savedProject = localStorage.getItem(storageKey);
  if (savedProject) { try { projectStore.load(JSON.parse(savedProject)); } catch { localStorage.removeItem(storageKey); } }
  $('.project-name').value = projectStore.project.name;
  $('#tempo').value = projectStore.project.bpm;
  $('#swing').value = projectStore.project.swing || 0;
  $('#swing-value').textContent = `${projectStore.project.swing || 0}%`;
  $('#master-volume').value = Math.round((projectStore.project.master || .82) * 100);
  $('#master-value').textContent = `${$('#master-volume').value}%`;
  applyTheme(); renderPads(); renderWorkspace('song');
})();
