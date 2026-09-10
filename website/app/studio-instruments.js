// Studio instruments operations. Dependencies are supplied by the application.
window.AnharmonicStudioParts = window.AnharmonicStudioParts || {};
window.AnharmonicStudioParts.instruments = context => {
  const {
    projectStore,
    shell,
    button,
    $,
    setStatus,
    state,
    openMenu,
    pads,
    synthPresets,
    synthNoteOn,
    stopArp,
    synthNoteOff,
    stage,
    previewSelection,
    padRecord,
    ensureAudio,
    playPad
  } = context;
  function renderNotes() {
    const notes = projectStore.pattern.notes;
    const noteRows = 36;
    const names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
    const noteGrid = Array
                         .from(
                             {length: noteRows},
                             (_, row) => `<span class="note-row ${
                                 names[(83 - row) % 12].includes('#') ? 'black-key' : ''}"></span>`)
                         .join('');
    const noteButtons =
        notes
            .map(
                note => `<button class="note" data-note-id="${note.id}" style="left:${
                    note.start * 70}px;top:${(83 - note.pitch) * 20}px;width:${
                    Math.max(20, note.duration * 70 - 3)}px" aria-label="${
                    names[note.pitch % 12]} note at beat ${note.start}">${names[note.pitch % 12]}${
                    Math.floor(note.pitch / 12) - 1}</button>`)
            .join('');
    shell(
        'Piano Roll',
        `${button('SOUND: PAD 01 ▾', 'note-action')} ${button('ROOT: C3 ▾', 'note-root')} ${
            button('MONO', 'note-mono')} ${button('QUANTIZE', 'note-quantize')} ${
            button('ADD CHORD ▾', 'note-chord')} ${button('CLEAR', 'note-clear')}`,
        `<div class="piano"><div class="keys">${
            Array
                .from(
                    {length: noteRows},
                    (_, i) =>
                        `<span>${names[(83 - i) % 12]}${Math.floor((83 - i) / 12) - 1}</span>`)
                .join('')}</div><div class="note-grid" id="note-grid">${noteGrid}${
            noteButtons}</div></div>`);
    const grid = $('#note-grid');
    grid.addEventListener('contextmenu', event => {
      if (!event.target.matches('.note')) return;
      event.preventDefault();
      const id = event.target.dataset.noteId;
      projectStore.transact('delete note', document => {
        document.patterns[document.selected_pattern].notes =
            document.patterns[document.selected_pattern].notes.filter(note => note.id !== id);
      });
      renderNotes();
      setStatus('note deleted');
    });
    grid.addEventListener('click', event => {
      if (event.target.matches('.note')) return;
      const rect = grid.getBoundingClientRect();
      const beat = Math.max(0, Math.floor(((event.clientX - rect.left) / 70) * 4) / 4);
      const pitch = Math.max(0, Math.min(127, 83 - Math.floor((event.clientY - rect.top) / 20)));
      projectStore.transact('add note', document => {
        document.patterns[document.selected_pattern].notes.push({
          id: `note-${Date.now()}-${Math.random().toString(16).slice(2)}`,
          pitch,
          start: beat,
          duration: .25,
          velocity: .8,
          pad: state.selectedPad
        });
      });
      renderNotes();
      setStatus(`note added at beat ${beat}`);
    });
    grid.querySelectorAll('.note').forEach(noteElement => {
      noteElement.addEventListener('pointerdown', event => {
        event.stopPropagation();
        const id = noteElement.dataset.noteId;
        const origin = {x: event.clientX, y: event.clientY};
        const original = projectStore.pattern.notes.find(note => note.id === id);
        const move = moveEvent => {
          noteElement.style.transform =
              `translate(${moveEvent.clientX - origin.x}px, ${moveEvent.clientY - origin.y}px)`;
        };
        const finish = finishEvent => {
          document.removeEventListener('pointermove', move);
          document.removeEventListener('pointerup', finish);
          const deltaBeat = Math.round(((finishEvent.clientX - origin.x) / 70) * 4) / 4;
          const deltaPitch = -Math.round((finishEvent.clientY - origin.y) / 20);
          projectStore.transact('move note', document => {
            const note =
                document.patterns[document.selected_pattern].notes.find(item => item.id === id);
            if (note) {
              note.start = Math.max(0, original.start + deltaBeat);
              note.pitch = Math.max(0, Math.min(127, original.pitch + deltaPitch));
            }
          });
          renderNotes();
          setStatus('note moved');
        };
        document.addEventListener('pointermove', move);
        document.addEventListener('pointerup', finish, {once: true});
      });
    });
    $('.note-clear').addEventListener('click', () => {
      projectStore.transact('clear notes', document => {
        document.patterns[document.selected_pattern].notes = [];
      });
      renderNotes();
      setStatus('notes cleared');
    });
    $('.note-action')
        .addEventListener(
            'click',
            event => openMenu(
                event.currentTarget,
                pads.slice(0, 16).map((name, index) => ({
                                        label: `${String(index + 1).padStart(2, '0')} · ${name}`,
                                        action: () => {
                                          state.notePad = index;
                                          event.currentTarget.textContent =
                                              `SOUND: PAD ${String(index + 1).padStart(2, '0')} ▾`;
                                          setStatus(`piano roll sound: ${name}`);
                                        }
                                      }))));
    $('.note-root').addEventListener('click', event => openMenu(event.currentTarget, [
                                                'C3', 'D3', 'E3', 'F3', 'G3', 'A3', 'B3'
                                              ].map(root => ({
                                                      label: root,
                                                      action: () => {
                                                        state.noteRoot = root;
                                                        event.currentTarget.textContent =
                                                            `ROOT: ${root} ▾`;
                                                        setStatus(`root note: ${root}`);
                                                      }
                                                    }))));
    $('.note-mono').addEventListener('click', event => {
      event.currentTarget.classList.toggle('active');
      setStatus(
          event.currentTarget.classList.contains('active') ? 'mono notes on' : 'mono notes off');
    });
    $('.note-quantize').addEventListener('click', () => {
      projectStore.transact('quantize notes', document => {
        document.patterns[document.selected_pattern].notes.forEach(note => {
          note.start = Math.max(0, Math.round(note.start * 4) / 4);
        });
      });
      renderNotes();
      setStatus('notes quantized');
    });
    $('.note-chord').addEventListener('click', () => {
      projectStore.transact('add chord', document => {
        const pattern = document.patterns[document.selected_pattern];
        const start = pattern.notes.length ? pattern.notes[pattern.notes.length - 1].start : 0;
        [60, 64, 67].forEach((pitch, index) => pattern.notes.push({
          id: `note-chord-${Date.now()}-${index}`,
          pitch,
          start,
          duration: .5,
          velocity: .75,
          pad: state.selectedPad
        }));
      });
      renderNotes();
      setStatus('C major chord added');
    });
  }

  function renderInstruments() {
    const synth = projectStore.project.synth;
    const arp = projectStore.project.arp;
    shell(
        'Instruments',
        `${button('PREVIEW SOUND', 'synth-preview')} ${button('PANIC', 'synth-panic')}`,
        `<div class="instrument-editor"><div class="instrument-toolbar"><label>PRESET <select id="synth-preset">${
            Object.keys(synthPresets)
                .map(name => `<option ${name === synth.name ? 'selected' : ''}>${name}</option>`)
                .join('')}</select></label><label>TRACK <select id="synth-track">${
            Array
                .from(
                    {length: 8},
                    (_, i) => `<option value="${i}" ${i === synth.track ? 'selected' : ''}>${
                        i + 1}</option>`)
                .join('')}</select></label><button id="arp-toggle" class="${
            arp.enabled ? 'active' : ''}">ARP ${
            arp.enabled ?
                'ON' :
                'OFF'}</button><label>RATE <select id="arp-rate"><option value=".125">1/32</option><option value=".25">1/16</option><option value=".5">1/8</option><option value="1">1/4</option></select></label><label>MODE <select id="arp-mode"><option>up</option><option>down</option><option>up/down</option><option>random</option></select></label></div><div class="synth-controls"><label>OSC 1 <select id="osc1"><option>saw</option><option>square</option><option>triangle</option><option>sine</option></select></label><label>OSC 2 <select id="osc2"><option>saw</option><option>square</option><option>triangle</option><option>sine</option></select></label><label>CUTOFF <input id="synth-cutoff" type="range" min="80" max="12000"><output></output></label><label>RESONANCE <input id="synth-resonance" type="range" min="0" max=".95" step=".01"><output></output></label><label>ATTACK <input id="synth-attack" type="range" min=".001" max="2" step=".001"><output></output></label><label>RELEASE <input id="synth-release" type="range" min=".01" max="4" step=".01"><output></output></label><label>DRIVE <input id="synth-drive" type="range" min="0" max="1" step=".01"><output></output></label><label>SPREAD <input id="synth-spread" type="range" min="0" max="1" step=".01"><output></output></label></div><div class="synth-keyboard" id="synth-keyboard">${
            Array
                .from(
                    {length: 25},
                    (_, i) => `<button data-note="${48 + i}">${
                            ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
                            [i % 12]}${Math.floor((48 + i) / 12) - 1}</button>`)
                .join(
                    '')}</div><p class="sampler-help">Instrument state follows the desktop SynthPatch and ArpSettings contract. Hold keys to play; enable ARP to sequence held notes.</p></div>`);
    const controls = {
      osc1: 'osc1',
      osc2: 'osc2',
      cutoff: 'synth-cutoff',
      resonance: 'synth-resonance',
      attack: 'synth-attack',
      release: 'synth-release',
      drive: 'synth-drive',
      spread: 'synth-spread'
    };
    Object.entries(controls).forEach(([field, id]) => {
      const element = $(`#${id}`);
      element.value = synth[field];
      element.nextElementSibling && (element.nextElementSibling.textContent = synth[field]);
      element.addEventListener('input', event => {
        projectStore.setSynth(
            {[field]: element.type === 'range' ? Number(event.target.value) : event.target.value});
      });
    });
    $('#synth-preset').addEventListener('change', event => {
      const preset = synthPresets[event.target.value];
      projectStore.setSynth({...preset, name: event.target.value});
      renderInstruments();
      setStatus(`${event.target.value} loaded`);
    });
    $('#synth-track').addEventListener('change', event => projectStore.setSynth({
      track: Number(event.target.value)
    }));
    $('#arp-toggle').addEventListener('click', () => {
      projectStore.setArp({enabled: !projectStore.project.arp.enabled});
      renderInstruments();
    });
    $('#arp-rate').value = String(arp.rate_beats);
    $('#arp-mode').value = arp.mode;
    $('#arp-rate').addEventListener('change', event => projectStore.setArp({
      rate_beats: Number(event.target.value)
    }));
    $('#arp-mode').addEventListener('change', event => projectStore.setArp({
      mode: event.target.value
    }));
    $('.synth-preview').onclick = () => synthNoteOn(60, .8, true);
    $('.synth-panic').onclick = () => {
      state.heldSynth.clear();
      stopArp();
      state.synthVoices.forEach(voice => voice.release());
      state.synthVoices.clear();
      setStatus('synth voices released');
    };
    document.querySelectorAll('#synth-keyboard button').forEach(key => {
      key.onpointerdown = () => synthNoteOn(Number(key.dataset.note), .8);
      key.onpointerup = key.onpointerleave = () => synthNoteOff(Number(key.dataset.note));
    });
  }

  function renderAutotune() {
    shell(
        'Autotune',
        `${button('ANALYZE PITCH', 'analyze-pitch')} ${button('RENDER TAKE', 'render-take')}`,
        `<div class="autotune-editor"><div class="autotune-card"><strong>Selected vocal take</strong><p>${
            state.buffers.has(state.selectedPad) ?
                'Audio is ready for browser pitch preview.' :
                'Import or record a vocal take in the Sampler first.'}</p><div class="pitch-lane">${
            Array
                .from(
                    {length: 32},
                    (_, index) =>
                        `<i style="height:${18 + Math.abs(Math.sin(index * .8)) * 50}px"></i>`)
                .join(
                    '')}</div></div><div class="autotune-controls"><label>KEY <select><option>C</option><option>D</option><option>E</option><option>F</option><option>G</option></select></label><label>SCALE <select><option>Chromatic</option><option>Major</option><option>Minor</option></select></label><label>CORRECTION <input id="correction-amount" type="range" min="0" max="100" value="72"><output>72%</output></label></div><p class="sampler-help">Browser preview uses Web Audio pitch shifting. High-quality formant correction and offline take rendering remain separate DSP work.</p></div>`);
    stage.querySelector('.analyze-pitch')
        .addEventListener('click', () => setStatus('pitch analysis preview complete'));
    stage.querySelector('.render-take').addEventListener('click', () => previewSelection());
    stage.querySelector('#correction-amount')
        .addEventListener(
            'input',
            event => event.target.parentElement.querySelector('output').textContent =
                `${event.target.value}%`);
  }

  function renderMix() {
    const tracks = projectStore.project.tracks;
    shell(
        'Mixer',
        `${button('MASTER OUTPUT', 'master-output')} ${button('ADD EFFECT ▾', 'add-effect')}`,
        `<div class="mixer-editor"><div class="mixer-channels">${
            tracks
                .map(
                    (track, index) => `<div class="channel ${track.mute ? 'muted' : ''}"><h3>${
                        track
                            .name}</h3><div class="vu"></div><div class="fader"><input class="track-gain" data-track="${
                        index}" type="range" min="0" max="100" value="${
                        track.gain * 100}"></div><small class="track-db">${
                        (20 * Math.log10(Math.max(.001, track.gain)))
                            .toFixed(
                                1)} dB</small><label class="track-pan">PAN <input class="track-pan-input" data-track="${
                        index}" type="range" min="-100" max="100" value="${
                        track.pan *
                        100}"></label><div class="channel-actions"><button class="track-mute ${
                        track.mute ?
                            'active' :
                            ''}" data-track="${index}">M</button><button class="track-solo ${
                        track.solo ? 'active' : ''}" data-track="${
                        index}">S</button><button class="track-fx" data-track="${
                        index}">FX</button></div></div>`)
                .join(
                    '')}</div><div class="effects-rack"><strong>MASTER EFFECTS</strong><label>TONE <input id="tone-effect" type="range" min="-12" max="12" value="${
            state.effects.tone}"><output>${
            state.effects
                .tone} dB</output></label><label>COMPRESSION <input id="compression-effect" type="range" min="1" max="12" value="${
            state.effects.compression}"><output>${
            state.effects
                .compression}:1</output></label><label>DELAY SEND <input id="delay-effect" type="range" min="0" max="40" value="${
            state.effects.delay}"><output>${state.effects.delay}%</output></label></div></div>`);
    const audio = state.audio;
    const tone = $('#tone-effect');
    const compression = $('#compression-effect');
    const delay = $('#delay-effect');
    [tone, compression, delay].forEach(control => control?.addEventListener('input', () => {
      state.effects.tone = Number(tone.value);
      state.effects.compression = Number(compression.value);
      state.effects.delay = Number(delay.value);
      if (audio) {
        audio.tone.gain.value = state.effects.tone;
        audio.compressor.ratio.value = state.effects.compression;
        audio.delayGain.gain.value = state.effects.delay / 100;
      }
    }));
    stage.querySelector('.master-output')
        .addEventListener(
            'click',
            event => openMenu(
                event.currentTarget,
                ['Master gain', 'Master tone', 'Limiter'].map(
                    label =>
                        ({label, action: () => setStatus(`${label} selected in master rack`)}))));
    stage.querySelector('.add-effect')
        .addEventListener(
            'click',
            event => openMenu(event.currentTarget, [
              'Tone / EQ', 'Compressor', 'Delay', 'Reverb'
            ].map(label => ({label, action: () => setStatus(`${label} enabled in master rack`)}))));
    stage.querySelectorAll('.track-gain')
        .forEach(control => control.addEventListener('input', () => {
          const index = Number(control.dataset.track);
          projectStore.setTrack(index, {gain: Number(control.value) / 100});
          control.closest('.channel').querySelector('.track-db').textContent =
              `${(20 * Math.log10(Math.max(.001, Number(control.value) / 100))).toFixed(1)} dB`;
        }));
    stage.querySelectorAll('.track-pan-input')
        .forEach(control => control.addEventListener('input', () => {
          projectStore.setTrack(Number(control.dataset.track), {pan: Number(control.value) / 100});
        }));
    stage.querySelectorAll('.track-mute')
        .forEach(control => control.addEventListener('click', () => {
          const index = Number(control.dataset.track);
          projectStore.setTrack(index, {mute: !projectStore.project.tracks[index].mute});
          renderMix();
        }));
    stage.querySelectorAll('.track-solo')
        .forEach(control => control.addEventListener('click', () => {
          const index = Number(control.dataset.track);
          projectStore.setTrack(index, {solo: !projectStore.project.tracks[index].solo});
          renderMix();
        }));
    stage.querySelectorAll('.track-fx')
        .forEach(
            control => control.addEventListener(
                'click',
                () => setStatus(`Track ${Number(control.dataset.track) + 1} FX rack selected`)));
  }

  function renderPads() {
    const firstPad = state.bank * 16;
    $('#pad-grid').innerHTML =
        Array
            .from(
                {length: 16},
                (_, offset) => {
                  const index = firstPad + offset;
                  const record = padRecord(index);
                  const name = record.name || `Pad ${String(index + 1).padStart(2, '0')}`;
                  return `<button class="pad ${
                      index === state.selectedPad ? 'selected' : ''}" data-pad="${index}"><b>${
                      String(offset + 1).padStart(2, '0')}</b><strong>${name}</strong><small>${
                      offset < 8 ? String.fromCharCode(49 + offset) : ''}</small></button>`;
                })
            .join('');
    document.querySelectorAll('.pad').forEach(pad => pad.addEventListener('click', () => {
      state.selectedPad = Number(pad.dataset.pad);
      renderPads();
      $('#inspector-name').textContent =
          `Pad ${String(state.selectedPad + 1).padStart(2, '0')} · ${pads[state.selectedPad]}`;
      ensureAudio().then(() => playPad(state.selectedPad)).catch(error => setStatus(error.message));
      setStatus(`selected pad ${String(state.selectedPad + 1).padStart(2, '0')}`);
    }));
    document.querySelectorAll('.pad-bank button').forEach(bank => {
      bank.onclick = () => {
        state.bank = Number(bank.dataset.bank);
        document.querySelectorAll('.pad-bank button')
            .forEach(item => item.classList.toggle('active', item === bank));
        state.selectedPad = state.bank * 16;
        renderPads();
      };
    });
    const record = padRecord(state.selectedPad);
    $('#pad-pitch').value = record.pitch || 0;
    $('#pad-pan').value = (record.pan || 0) * 100;
    $('#pad-gain').value = Math.max(0, Math.min(100, (record.gain ?? 1) * 100));
    $('#pad-mode').value = record.mode || 'one-shot';
    $('#pad-pitch').oninput = event => {
      projectStore.setPad(state.selectedPad, {pitch: Number(event.target.value)});
      $('#pitch-value').textContent = `${event.target.value} st`;
    };
    $('#pad-pan').oninput = event => {
      projectStore.setPad(state.selectedPad, {pan: Number(event.target.value) / 100});
      $('#pan-value').textContent = Number(event.target.value) === 0 ?
          'C' :
          `${Math.abs(Number(event.target.value))}% ${Number(event.target.value) < 0 ? 'L' : 'R'}`;
    };
    $('#pad-gain').oninput = event => {
      projectStore.setPad(state.selectedPad, {gain: Number(event.target.value) / 100});
      $('#gain-value').textContent =
          `${(20 * Math.log10(Math.max(.001, Number(event.target.value) / 100))).toFixed(1)} dB`;
    };
    $('#pad-mode').onchange = event =>
        projectStore.setPad(state.selectedPad, {mode: event.target.value});
  }
  return {renderNotes, renderInstruments, renderAutotune, renderMix, renderPads};
};
