(() => {
  'use strict';

  const pads = [
    'Kick', 'Snare', 'Closed Hat', 'Open Hat', 'Clap', 'Low Tom', 'Perc', 'Texture', 'Pad 09',
    'Pad 10', 'Pad 11', 'Pad 12', 'Pad 13', 'Pad 14', 'Pad 15', 'Pad 16'
  ];
  const synthPresets = {
    'Midnight Brass': {
      osc1: 'saw',
      osc2: 'square',
      cutoff: 2400,
      resonance: .28,
      attack: .025,
      decay: .32,
      sustain: .68,
      release: .65,
      drive: .18,
      spread: .42,
      volume: .42
    },
    'Copper Pluck': {
      osc1: 'saw',
      osc2: 'triangle',
      cutoff: 1150,
      resonance: .34,
      attack: .002,
      decay: .18,
      sustain: .08,
      release: .22,
      drive: .28,
      spread: .18,
      volume: .48
    },
    'Velvet Poly': {
      osc1: 'saw',
      osc2: 'saw',
      cutoff: 1750,
      resonance: .18,
      attack: .08,
      decay: .55,
      sustain: .72,
      release: 1.25,
      drive: .12,
      spread: .76,
      volume: .38
    },
    'Acid Orchard': {
      osc1: 'square',
      osc2: 'saw',
      cutoff: 520,
      resonance: .82,
      attack: .002,
      decay: .24,
      sustain: .18,
      release: .12,
      drive: .46,
      spread: .08,
      volume: .34
    },
    'Neon Sub': {
      osc1: 'sine',
      osc2: 'square',
      cutoff: 680,
      resonance: .16,
      attack: .004,
      decay: .3,
      sustain: .8,
      release: .32,
      drive: .4,
      spread: .04,
      volume: .5
    }
  };
  const storageKey = 'anharmonic-web-studio-v2';
  const projectStore = new window.AnharmonicProject.ProjectStore();
  const stepArrays = steps => Array.from(
      {length: 64},
      (_, pad) =>
          Object.entries(steps[pad] || {}).map(([step]) => Number(step)).sort((a, b) => a - b));
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
    accent: localStorage.getItem('anharmonic-accent') || '#d6ab65',
    effects: {tone: 0, compression: 3, delay: 0},
    synthVoices: new Map(),
    heldSynth: new Set(),
    arpTimer: null,
    arpIndex: 0
  };
  const $ = selector => document.querySelector(selector);
  const stage = $('#stage');
  projectStore.subscribe(document => {
    state.steps = stepArrays(document.patterns[document.selected_pattern].steps);
    state.tempo = document.bpm;
    const undo = $('#undo-project');
    const redo = $('#redo-project');
    if (undo) undo.disabled = !projectStore.history.length;
    if (redo) redo.disabled = !projectStore.future.length;
    if (state.audio)
      state.audio.trackBuses.forEach((bus, index) => {
        const track = document.tracks[index];
        bus.gain.value = trackAudible(index) ? track.gain : 0;
        bus.pan.pan.value = track.pan;
      });
  });
  const mediaDb = window.indexedDB ? new Promise((resolve, reject) => {
    const request = indexedDB.open('anharmonic-studio-media', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('audio');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  }) :
                                     Promise.resolve(null);

  const parts = {};
  const dependencies = {
    state,
    projectStore,
    pads,
    synthPresets,
    storageKey,
    stepArrays,
    $,
    stage,
    mediaDb,
    padRecord,
    padMediaId,
    padBuffer,
    storeMedia,
    restoreMedia,
    ensureAudio,
    playPad,
    trackAudible,
    trackOutput,
    button,
    shell,
    openMenu,
    renderSong,
    renderBeats,
    renderSampler,
    drawWaveform,
    updateSelection,
    previewSelection,
    renderNotes,
    renderInstruments,
    playSynthVoice,
    startArp,
    stopArp,
    synthNoteOn,
    synthNoteOff,
    renderAutotune,
    renderMix,
    renderWorkspace,
    renderPads,
    setStatus,
    togglePlayback,
    projectDocument,
    download,
    saveProject,
    exportProject,
    exportWav,
    renderOfflineHit,
    encodeWav,
    loadProject,
    loadAudio,
    toggleRecording,
    applyTheme
  };
  parts.audio = window.AnharmonicStudioParts.audio(dependencies);
  parts.media = window.AnharmonicStudioParts.media(dependencies);
  parts.project = window.AnharmonicStudioParts.project(dependencies);
  parts.song = window.AnharmonicStudioParts.song(dependencies);
  parts.sampler = window.AnharmonicStudioParts.sampler(dependencies);
  parts.instruments = window.AnharmonicStudioParts.instruments(dependencies);

  function padRecord(index) {
    return projectStore.project.pads[index] || {};
  }
  function padMediaId(index) {
    return padRecord(index).sample_id || '';
  }
  function padBuffer(index) {
    return state.buffers.get(padMediaId(index));
  }

  function storeMedia(...args) {
    return parts.media.storeMedia(...args);
  }

  function restoreMedia(...args) {
    return parts.media.restoreMedia(...args);
  }

  function ensureAudio(...args) {
    return parts.audio.ensureAudio(...args);
  }

  function playPad(...args) {
    return parts.audio.playPad(...args);
  }

  function trackAudible(...args) {
    return parts.audio.trackAudible(...args);
  }
  function trackOutput(...args) {
    return parts.audio.trackOutput(...args);
  }

  function button(label, className = '') {
    return `<button class="${className}" type="button">${label}</button>`;
  }
  function shell(title, tools, content) {
    stage.innerHTML = `<div class="stage-inner"><div class="stage-toolbar"><strong>${
        title}</strong>${tools}</div>${content}</div>`;
  }
  function openMenu(anchor, options) {
    document.querySelector('.app-menu')?.remove();
    const menu = document.createElement('div');
    menu.className = 'app-menu';
    options.forEach(option => {
      const item = document.createElement('button');
      item.type = 'button';
      item.textContent = option.label;
      item.addEventListener('click', () => {
        menu.remove();
        option.action();
      });
      menu.append(item);
    });
    document.body.append(menu);
    const rect = anchor.getBoundingClientRect();
    menu.style.left = `${Math.min(window.innerWidth - menu.offsetWidth - 8, rect.left)}px`;
    menu.style.top = `${rect.bottom + 4}px`;
    const close = event => {
      if (!menu.contains(event.target) && event.target !== anchor) {
        menu.remove();
        document.removeEventListener('pointerdown', close);
      }
    };
    window.setTimeout(() => document.addEventListener('pointerdown', close), 0);
  }

  function renderSong(...args) {
    return parts.song.renderSong(...args);
  }

  function renderBeats(...args) {
    return parts.song.renderBeats(...args);
  }

  function renderSampler(...args) {
    return parts.sampler.renderSampler(...args);
  }

  function drawWaveform(...args) {
    return parts.sampler.drawWaveform(...args);
  }

  function updateSelection(...args) {
    return parts.sampler.updateSelection(...args);
  }

  function previewSelection(...args) {
    return parts.audio.previewSelection(...args);
  }

  function renderNotes(...args) {
    return parts.instruments.renderNotes(...args);
  }

  function renderInstruments(...args) {
    return parts.instruments.renderInstruments(...args);
  }

  function playSynthVoice(...args) {
    return parts.audio.playSynthVoice(...args);
  }
  function startArp(...args) {
    return parts.audio.startArp(...args);
  }
  function stopArp(...args) {
    return parts.audio.stopArp(...args);
  }
  function synthNoteOn(...args) {
    return parts.audio.synthNoteOn(...args);
  }
  function synthNoteOff(...args) {
    return parts.audio.synthNoteOff(...args);
  }

  function renderAutotune(...args) {
    return parts.instruments.renderAutotune(...args);
  }

  function renderMix(...args) {
    return parts.instruments.renderMix(...args);
  }
  function renderWorkspace(name) {
    state.workspace = name;
    ({
      song: renderSong,
      beats: renderBeats,
      notes: renderNotes,
      sampler: renderSampler,
      mix: renderMix,
      instruments: renderInstruments,
      autotune: renderAutotune
    }[name] ||
     renderSong)();
  }

  function renderPads(...args) {
    return parts.instruments.renderPads(...args);
  }

  function setStatus(message) {
    $('#status').textContent = message;
  }
  function togglePlayback() {
    state.playing = !state.playing;
    $('#play').textContent = state.playing ? 'Ⅱ' : '▶';
    if (!state.playing) {
      window.clearInterval(state.timer);
      setStatus('ready');
      return;
    }
    ensureAudio()
        .then(() => {
          state.step = -1;
          setStatus('playing current pattern');
          state.timer = window.setInterval(() => {
            state.step = (state.step + 1) % 16;
            $('#counter').textContent = `001 . ${Math.floor(state.step / 4) + 1} . ${
                String(state.step % 4).padStart(2, '0')}`;
            state.steps.forEach((hits, pad) => {
              if (hits.includes(state.step)) playPad(pad);
            });
            if (state.workspace === 'beats') renderBeats();
          }, Math.max(60, 60000 / state.tempo / 4));
        })
        .catch(error => {
          state.playing = false;
          $('#play').textContent = '▶';
          setStatus(error.message);
        });
  }

  function projectDocument(...args) {
    return parts.project.projectDocument(...args);
  }
  function download(...args) {
    return parts.project.download(...args);
  }
  function saveProject(...args) {
    return parts.project.saveProject(...args);
  }
  function exportProject(...args) {
    return parts.project.exportProject(...args);
  }
  function exportWav(...args) {
    return parts.project.exportWav(...args);
  }
  function renderOfflineHit(...args) {
    return parts.audio.renderOfflineHit(...args);
  }
  function encodeWav(...args) {
    return parts.project.encodeWav(...args);
  }
  function loadProject(...args) {
    return parts.project.loadProject(...args);
  }
  function loadAudio(...args) {
    return parts.media.loadAudio(...args);
  }

  function toggleRecording(...args) {
    return parts.media.toggleRecording(...args);
  }
  function applyTheme() {
    document.documentElement.dataset.theme = state.theme;
    document.documentElement.style.setProperty('--accent', state.accent);
    document.documentElement.style.setProperty('--accent2', state.accent);
    $('#theme-toggle').textContent = state.theme === 'dark' ? 'Light' : 'Dark';
  }

  document.querySelectorAll('.studio-nav button[data-workspace]')
      .forEach(tab => tab.addEventListener('click', () => {
        document.querySelectorAll('.studio-nav button[data-workspace]')
            .forEach(item => item.classList.remove('active'));
        tab.classList.add('active');
        renderWorkspace(tab.dataset.workspace);
      }));
  document.querySelectorAll('.panel-toggle[data-toggle]')
      .forEach(
          toggle => toggle.addEventListener(
              'click', () => $(`#${toggle.dataset.toggle}-panel`).classList.toggle('open')));
  document.querySelectorAll('[data-close]')
      .forEach(
          button => button.addEventListener(
              'click', () => $(`#${button.dataset.close}-panel`).classList.remove('open')));
  document.querySelector('#play').addEventListener('click', togglePlayback);
  document.querySelector('#stop').addEventListener('click', () => {
    state.playing = false;
    window.clearInterval(state.timer);
    $('#play').textContent = '▶';
    $('#counter').textContent = '001 . 1 . 00';
    setStatus('ready');
  });
  document.querySelector('#record').addEventListener('click', toggleRecording);
  document.querySelector('#tempo').addEventListener('change', event => {
    projectStore.setTempo(event.target.value);
    event.target.value = state.tempo;
  });
  document.querySelector('#playback-mode')
      .addEventListener('change', event => setStatus(`${event.target.value} playback selected`));
  document.querySelector('#swing').addEventListener('input', event => {
    projectStore.transact('change swing', document => {
      document.swing = Number(event.target.value);
    });
    $('#swing-value').textContent = `${event.target.value}%`;
  });
  document.querySelector('#master-volume').addEventListener('input', event => {
    const value = Number(event.target.value) / 100;
    projectStore.transact('change master volume', document => {
      document.master = value;
    });
    if (state.audio) state.audio.master.gain.value = value;
    $('#master-value').textContent = `${event.target.value}%`;
  });
  document.querySelector('#tap-tempo').addEventListener('click', () => {
    const now = performance.now();
    state.taps = (state.taps || []).filter(time => now - time < 2000);
    state.taps.push(now);
    if (state.taps.length > 1) {
      const interval = (now - state.taps[0]) / (state.taps.length - 1);
      projectStore.setTempo(Math.round(Math.max(40, Math.min(240, 60000 / interval))));
      $('#tempo').value = state.tempo;
    }
    setStatus('tempo tapped');
  });
  document.querySelector('#metronome').addEventListener('click', event => {
    event.currentTarget.classList.toggle('active');
    setStatus(event.currentTarget.classList.contains('active') ? 'metronome on' : 'metronome off');
  });
  document.querySelector('#cut-source').addEventListener('click', event => {
    event.currentTarget.classList.toggle('active');
    setStatus(
        event.currentTarget.classList.contains('active') ? 'cut source on' : 'cut source off');
  });
  document.querySelector('#save-project').addEventListener('click', saveProject);
  document.querySelector('#export-project').addEventListener('click', exportProject);
  document.querySelector('#export-wav')
      .addEventListener(
          'click', () => exportWav().catch(error => setStatus(`export failed: ${error.message}`)));
  document.querySelector('#undo-project').addEventListener('click', () => {
    if (projectStore.undo()) {
      renderWorkspace(state.workspace);
      setStatus('undid last edit');
    }
  });
  document.querySelector('#redo-project').addEventListener('click', () => {
    if (projectStore.redo()) {
      renderWorkspace(state.workspace);
      setStatus('redid edit');
    }
  });
  document.querySelector('#load-project')
      .addEventListener('click', () => $('#project-file').click());
  document.querySelector('#project-file').addEventListener('change', event => {
    if (event.target.files[0]) loadProject(event.target.files[0]);
    event.target.value = '';
  });
  document.querySelector('#import-audio').addEventListener('click', () => $('#audio-file').click());
  document.querySelector('#audio-file').addEventListener('change', event => {
    if (event.target.files[0]) loadAudio(event.target.files[0]);
    event.target.value = '';
  });
  document.querySelector('#theme-toggle').addEventListener('click', () => {
    state.theme = state.theme === 'dark' ? 'light' : 'dark';
    localStorage.setItem('anharmonic-theme', state.theme);
    applyTheme();
  });
  document.querySelector('#accent-toggle')
      .addEventListener('click', () => $('#accent-picker').click());
  document.querySelector('#accent-picker').addEventListener('input', event => {
    state.accent = event.target.value;
    localStorage.setItem('anharmonic-accent', state.accent);
    applyTheme();
  });
  document.querySelector('#typing-toggle')
      .addEventListener(
          'click', () => setStatus('Keys: press 1–8 to play pads, Space to play or pause'));
  document.querySelector('#help-toggle')
      .addEventListener(
          'click',
          () => setStatus('Import audio from Browser, select a pad, then use Beats or Sampler'));
  document.querySelector('#stems-button')
      .addEventListener(
          'click',
          () => setStatus(
              'Stem separation needs a server model job or a WebGPU model; no fake split is performed in the browser'));
  document.querySelector('.tools').addEventListener(
      'click',
      () => setStatus(
          'Tools: recording, Autotune preview, automation, and master effects are browser capabilities; LV2 plugins remain desktop-only'));
  document.querySelectorAll('.sound').forEach(sound => sound.addEventListener('click', () => {
    document.querySelectorAll('.sound').forEach(item => item.classList.remove('selected'));
    sound.classList.add('selected');
    pads[state.selectedPad] = sound.querySelector('strong').textContent;
    renderPads();
    setStatus(`${pads[state.selectedPad]} selected`);
  }));
  window.addEventListener('keydown', event => {
    if (event.ctrlKey && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      if (event.shiftKey)
        projectStore.redo();
      else
        projectStore.undo();
      renderWorkspace(state.workspace);
      return;
    }
    if (event.code === 'Space' && !event.target.matches('input,select')) {
      event.preventDefault();
      togglePlayback();
    }
    const index = Number(event.key) - 1;
    if (index >= 0 && index < 8)
      ensureAudio().then(() => playPad(index)).catch(error => setStatus(error.message));
  });

  const savedProject = localStorage.getItem(storageKey);
  if (savedProject) {
    try {
      projectStore.load(JSON.parse(savedProject));
    } catch {
      localStorage.removeItem(storageKey);
    }
  }
  $('.project-name').value = projectStore.project.name;
  $('#tempo').value = projectStore.project.bpm;
  $('#swing').value = projectStore.project.swing || 0;
  $('#swing-value').textContent = `${projectStore.project.swing || 0}%`;
  $('#master-volume').value = Math.round((projectStore.project.master ?? .82) * 100);
  $('#master-value').textContent = `${$('#master-volume').value}%`;
  applyTheme();
  renderPads();
  renderWorkspace('song');
})();
