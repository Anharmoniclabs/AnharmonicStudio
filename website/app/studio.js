(() => {
  'use strict';

  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
  const uid = prefix => prefix + '-' + crypto.randomUUID();
  const clamp = (value, min, max) => Math.min(max, Math.max(min, Number(value) || 0));
  const padNumber = index => String(index + 1).padStart(2, '0');
  const noteName = pitch => ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B'][pitch % 12] + (Math.floor(pitch / 12) - 1);
  const storageKey = 'anharmonic-web-studio-v2';
  const projectStore = new window.AnharmonicProject.ProjectStore();
  const state = {
    selectedPad: 0, bank: 0, workspace: 'song', playing: false, step: -1, beat: 0,
    engine: null, media: new Map(), buffers: new Map(), selectedMedia: null, selections: new Map(),
    generation: 0, revision: 0, loading: false, saving: false, dirty: false, corruptSaved: null, recording: null, recordPending: false,
    arrangeTool: 'select', arrangeSnap: 1, loadedOnly: false, follow: false,
    notePad: null, noteRoot: 60, noteMono: false, sampleSnap: 0,
    heldPads: new Map(), heldSynth: new Set(), arpTimer: null, arpIndex: 0,
    browserOpen: true, padsOpen: true, focused: false, metronome: false, theme: 'dark', accent: '#d6ab65'
  };
  const stage = $('#stage');
  function setStatus(message) { $('#status').textContent = String(message); }
  function report(error) { setStatus(error?.message || String(error)); }
  const act = action => (...args) => { try { return Promise.resolve(action(...args)).catch(report); } catch (error) { report(error); } };
  const on = (selector, event, action) => $(selector)?.addEventListener(event, act(action));
  const padRecord = (index = state.selectedPad) => projectStore.project.pads[index];
  const padBuffer = (index = state.selectedPad) => state.buffers.get(padRecord(index).sample_id);
  const selectionKey = () => state.selectedPad + ':' + padRecord().sample_id;
  const decodedBytes = buffer => buffer.length * buffer.numberOfChannels * 4;
  function checkAudioBudget(buffer, collection = state.buffers, replacedId = null) {
    const total = [...collection].reduce((sum, [id, current]) => sum + (id === replacedId ? 0 : decodedBytes(current)), 0);
    if (buffer.duration > 600 || buffer.numberOfChannels > 8 || decodedBytes(buffer) > 128 * 1024 * 1024 || total + decodedBytes(buffer) > 256 * 1024 * 1024) {
      throw new Error('Audio exceeds the browser limit: 10 minutes / 128 MB decoded per sound and 256 MB decoded per session. Split long files before importing.');
    }
  }
  let decodeQueue = Promise.resolve();
  function decodeAudio(blob, context, recording = false) {
    const pending = decodeQueue.then(() => decodeAudioNow(blob, context, recording));
    decodeQueue = pending.catch(() => {});
    return pending;
  }
  async function decodeAudioNow(blob, context, recording) {
    if (blob.size > 128 * 1024 * 1024) throw new Error('Audio file exceeds the 128 MB import limit.');
    if (recording) return context.decodeAudioData(await blob.arrayBuffer());
    // Ask the media parser for duration before allocating decoded PCM. A
    // compressed file can otherwise expand far beyond its download size.
    const media = document.createElement('audio'), url = URL.createObjectURL(blob);
    try {
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error('Audio metadata took too long to read. Convert this file to WAV before importing.')), 10000);
        const finish = action => { clearTimeout(timeout); action(); };
        media.preload = 'metadata';
        media.onloadedmetadata = () => finish(() => {
          if (!Number.isFinite(media.duration) || media.duration <= 0) reject(new Error('Audio duration could not be determined. Convert this file to WAV before importing.'));
          else if (media.duration > 600) reject(new Error('Audio exceeds the 10 minute browser limit. Split it before importing.'));
          else resolve();
        });
        media.onerror = () => finish(() => reject(new Error('This browser cannot read the audio format. Convert it to WAV before importing.')));
        media.src = url;
      });
    } finally { media.removeAttribute('src'); media.load(); URL.revokeObjectURL(url); }
    return context.decodeAudioData(await blob.arrayBuffer());
  }
  const currentSelection = () => state.selections.get(selectionKey()) || { start: padRecord().start || 0, end: padRecord().end || padBuffer()?.duration || 0 };
  function shell(title, tools, content) {
    stage.innerHTML = '<div class="stage-inner"><div class="stage-toolbar"><strong>' + escapeHTML(title) + '</strong>' + tools + '</div>' + content + '</div>';
  }
  const button = (text, className, disabled = false) => '<button type="button" class="' + className + '"' + (disabled ? ' disabled' : '') + '>' + escapeHTML(text) + '</button>';
  let closeMenu = null;
  function openMenu(anchor, options) {
    closeMenu?.();
    const menu = document.createElement('div');
    menu.className = 'app-menu'; menu.setAttribute('role', 'menu');
    const close = () => { menu.remove(); document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', keyboard); closeMenu = null; };
    const outside = event => { if (!menu.contains(event.target) && !anchor.contains(event.target)) close(); };
    const keyboard = event => { if (event.key === 'Escape') { close(); anchor.focus(); } };
    options.forEach(option => {
      const item = document.createElement('button');
      item.type = 'button'; item.textContent = option.label; item.disabled = Boolean(option.disabled);
      item.setAttribute('role', 'menuitem');
      item.addEventListener('click', act(() => { close(); return option.action(); }));
      menu.append(item);
    });
    document.body.append(menu);
    const rect = anchor.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(window.innerWidth - menu.offsetWidth - 8, rect.left)) + 'px';
    menu.style.top = Math.max(8, Math.min(window.innerHeight - menu.offsetHeight - 8, rect.bottom + 4)) + 'px';
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', keyboard);
    closeMenu = close;
  }

  // IndexedDB retains original audio bytes. Saving never discards unreadable or older saves.
  const mediaDb = new Promise(resolve => {
    if (!window.indexedDB) return resolve(null);
    let request;
    try { request = indexedDB.open('anharmonic-studio-media', 1); } catch { return resolve(null); }
    request.onupgradeneeded = () => { if (!request.result.objectStoreNames.contains('audio')) request.result.createObjectStore('audio'); };
    request.onsuccess = () => resolve(request.result);
    request.onerror = request.onblocked = () => resolve(null);
  });
  async function dbMedia(id, blob) {
    const database = await mediaDb;
    if (!database) { if (blob) throw new Error('Browser audio storage is unavailable. Download Project + audio to keep your work.'); return null; }
    return new Promise((resolve, reject) => {
      const transaction = database.transaction('audio', blob ? 'readwrite' : 'readonly');
      const request = blob ? transaction.objectStore('audio').put(blob, id) : transaction.objectStore('audio').get(id);
      transaction.oncomplete = () => resolve(blob || request.result || null);
      transaction.onerror = transaction.onabort = () => reject(transaction.error || new Error('Audio storage failed. Download Project + audio to keep your work.'));
    });
  }
  function mediaIds(project = projectStore.project) {
    return [...new Set([...project.media.map(item => item.id), ...project.pads.map(pad => pad.sample_id), ...project.rows.flatMap(row => row.clips.filter(clip => clip.kind === 'audio').map(clip => clip.ref))].filter(Boolean))];
  }
  async function ensureAudio() {
    if (state.audioPromise && state.audioPromiseGeneration === state.generation) return state.audioPromise;
    const pending = initializeAudio();
    state.audioPromise = pending; state.audioPromiseGeneration = state.generation;
    try { return await pending; } finally { if (state.audioPromise === pending) state.audioPromise = null; }
  }
  async function initializeAudio() {
    if (!state.engine) state.engine = new window.AnharmonicAudio.AudioEngine({
      getProject: () => projectStore.project,
      getBuffer: id => state.buffers.get(id),
      onPosition: position => {
        state.beat = position.beat; state.step = position.step; state.playing = position.playing;
        $('#play').textContent = position.playing ? 'Ⅱ' : '▶';
        $('#counter').textContent = String(Math.floor(position.beat / 4) + 1).padStart(3, '0') + ' . ' + (Math.floor(position.beat % 4) + 1) + ' . ' + String(Math.floor((position.beat % 1) * 100)).padStart(2, '0');
        $$('.step-line button.current').forEach(cell => cell.classList.remove('current'));
        if (position.playing && state.workspace === 'beats') {
          $$('.step-line button[data-step="' + position.step + '"]').forEach(cell => cell.classList.add('current'));
          if (state.follow) $('.step-line button.current')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        }
        const playhead = $('#song-playhead'); if (playhead) playhead.style.left = (190 + position.beat * 45) + 'px';
      },
      onError: report
    });
    await state.engine.resume();
    $('.audio-ready').textContent = 'AUDIO ACTIVE';
    const generation = state.generation;
    for (const id of mediaIds()) {
      if (state.buffers.has(id)) continue;
      const blob = state.media.get(id) || await dbMedia(id);
      if (!blob) continue;
      try {
        const buffer = await decodeAudio(blob, state.engine.context);
        if (generation !== state.generation) return;
        checkAudioBudget(buffer, state.buffers, id);
        state.media.set(id, blob); state.buffers.set(id, buffer);
      } catch { setStatus('Some stored audio cannot be decoded. Reimport the original sound.'); }
    }
    state.engine.setMetronome(state.metronome); state.engine.sync();
    return state.engine;
  }
  async function triggerPad(index, options = {}) {
    const engine = await ensureAudio();
    if (!engine || !padRecord(index)?.sample_id || !padBuffer(index)) { setStatus('This pad has no available audio. Import or assign a sound first.'); return; }
    engine.triggerPad(index, options);
    const pad = $('.pad[data-pad="' + index + '"]');
    pad?.classList.add('hit'); setTimeout(() => pad?.classList.remove('hit'), 100);
  }
  function releasePad(index) { state.engine?.releasePad(index); }
  function stopPlayback() {
    state.engine?.stop(); state.playing = false; state.step = -1; state.beat = 0;
    stopArp(); state.heldSynth.forEach(note => state.engine?.releaseNote(note)); state.heldSynth.clear();
    state.heldPads.forEach(index => releasePad(index)); state.heldPads.clear();
    $('#play').textContent = '▶'; $('#counter').textContent = '001 . 1 . 00';
    $$('.current').forEach(cell => cell.classList.remove('current'));
  }
  async function togglePlayback() {
    if (state.playing) { stopPlayback(); setStatus('Stopped'); return; }
    const engine = await ensureAudio();
    if (!engine) return;
    const mode = $('#playback-mode').value;
    await engine.start(mode);
    state.playing = true; $('#play').textContent = 'Ⅱ';
    setStatus(mode === 'song' ? 'Playing song timeline' : 'Playing ' + projectStore.pattern.name);
  }
  projectStore.subscribe(() => {
    state.revision += 1;
    state.dirty = true;
    $('#undo-project').disabled = !projectStore.history.length;
    $('#redo-project').disabled = !projectStore.future.length;
    state.engine?.sync();
  });
  function projectDocument() {
    const project = projectStore.toJSON();
    project.name = $('.project-name').value.trim() || 'Untitled project'; project.selected_pad = state.selectedPad;
    return project;
  }
  function download(name, data, mime) {
    const blob = data instanceof Blob ? data : new Blob([data], { type: mime });
    const link = document.createElement('a'); const url = URL.createObjectURL(blob);
    link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }
  const safeFilename = name => (String(name).replace(/[^a-z0-9_-]+/gi, '-').slice(0, 100) || 'anharmonic-project');
  async function saveProject() {
    if (state.saving) return;
    state.saving = true; $('#save-project').disabled = true;
    try {
    const project = projectDocument(), revision = state.revision, generation = state.generation;
    for (const id of mediaIds(project)) {
      const blob = state.media.get(id) || await dbMedia(id);
      if (!blob) throw new Error('Cannot save a complete session: missing audio. Reimport the sound or download the project for recovery.');
      await dbMedia(id, blob);
    }
    if (state.generation !== generation) throw new Error('Save cancelled because the project changed. Save the current session when ready.');
    if (state.corruptSaved) {
      localStorage.setItem(storageKey + '-recovery-' + Date.now(), state.corruptSaved);
      state.corruptSaved = null;
    }
    localStorage.setItem(storageKey, JSON.stringify(project));
    if (state.generation === generation && state.revision === revision) state.dirty = false;
    setStatus('Project and audio saved in this browser.' + (state.dirty ? ' Newer edits still need saving.' : ' Download Project + audio for a portable backup.'));
    } finally { state.saving = false; $('#save-project').disabled = false; }
  }
  function toBase64(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
  }
  async function exportProject() {
    const project = projectDocument(); const media = [];
    let bytes = 0;
    for (const id of mediaIds(project)) {
      const blob = state.media.get(id) || await dbMedia(id);
      if (!blob) throw new Error('Cannot export a portable session: audio for "' + id + '" is missing. Reimport it first.');
      bytes += blob.size;
      if (bytes > 256 * 1024 * 1024) throw new Error('Portable export exceeds 256 MB. Save locally and split this project before downloading.');
      media.push({ id, mime: blob.type || 'audio/wav', data: await toBase64(blob) });
    }
    download(safeFilename(project.name) + '.json', JSON.stringify({ anharmonic_bundle: 1, project, media }), 'application/json');
    setStatus('Portable project downloaded with ' + media.length + ' audio file(s).');
  }
  async function exportWav() {
    $('#export-wav').disabled = true;
    try {
      const engine = await ensureAudio(); if (!engine) return;
      setStatus('Rendering ' + ($('#playback-mode').value === 'song' ? 'song' : 'current pattern') + '…');
      const blob = await engine.render($('#playback-mode').value, { sampleRate: 44100 });
      download(safeFilename(projectDocument().name) + '.wav', blob);
      setStatus('WAV exported with the selected playback scope and browser mixer settings.');
    } finally { $('#export-wav').disabled = false; }
  }
  function canReplace() {
    if (state.recording || state.recordPending) throw new Error('Stop microphone recording before changing projects.');
    return !state.dirty || window.confirm('Replace this session? Save or download your changes first. Your previous browser save will be retained.');
  }
  async function loadProject(file) {
    if (state.loading) return;
    if (!canReplace()) return;
    state.loading = true;
    $$('.project-bar,.transport-bar,.studio-nav,.main-split').forEach(element => element.inert = true);
    setStatus('Opening project and validating audio…');
    const generation = state.generation, revision = state.revision;
    try {
    if (file.size > 360 * 1024 * 1024) throw new Error('Project file exceeds the 360 MB import limit.');
    const imported = JSON.parse(await file.text());
    const bundle = imported.anharmonic_bundle === 1;
    const project = window.AnharmonicProject.normalize(bundle ? imported.project : imported);
    if (bundle && (!Array.isArray(imported.media) || imported.media.length > 100000)) throw new Error('Invalid portable media list.');
    const engine = await ensureAudio(); if (!engine) return;
    const nextMedia = new Map(), nextBuffers = new Map();
    if (bundle) {
      let total = 0;
      for (const item of imported.media) {
        if (!item || typeof item.id !== 'string' || typeof item.data !== 'string' || !/^[A-Za-z0-9+/]*={0,2}$/.test(item.data) || nextMedia.has(item.id)) throw new Error('Invalid portable audio entry.');
        total += item.data.length * .75;
        if (total > 256 * 1024 * 1024) throw new Error('Portable audio exceeds 256 MB.');
        const raw = atob(item.data); const bytes = Uint8Array.from(raw, character => character.charCodeAt(0));
        const blob = new Blob([bytes], { type: typeof item.mime === 'string' && item.mime.startsWith('audio/') ? item.mime : 'audio/wav' });
        const buffer = await decodeAudio(blob, engine.context);
        checkAudioBudget(buffer, nextBuffers);
        nextMedia.set(item.id, blob); nextBuffers.set(item.id, buffer);
      }
      for (const id of mediaIds(project)) if (!nextMedia.has(id)) throw new Error('Portable project is missing audio "' + id + '".');
    }
    if (generation !== state.generation || revision !== state.revision) throw new Error('Project load cancelled because the session changed.');
    stopPlayback(); state.generation += 1;
    state.media = nextMedia; state.buffers = nextBuffers; state.selections.clear();
    state.selectedMedia = null; projectStore.load(project);
    state.selectedPad = clamp(project.selected_pad, 0, 63); state.bank = Math.floor(state.selectedPad / 16);
    state.notePad = null; state.dirty = true;
    syncControls(); renderAll();
    if (!bundle) await ensureAudio();
    renderAll();
    const missing = mediaIds().filter(id => !state.buffers.has(id)).length;
    setStatus(missing ? 'Project loaded; ' + missing + ' local sound(s) need reimporting. Native-only processors are retained but unavailable here.' : 'Project loaded. Use Save to keep it in this browser.');
    } finally {
      state.loading = false;
      $$('.project-bar,.transport-bar,.studio-nav,.main-split').forEach(element => element.inert = false);
    }
  }
  async function importAudio(files) {
    const targetPad = state.selectedPad, generation = state.generation;
    const engine = await ensureAudio(); if (!engine) return;
    let imported = 0, failed = 0; let storageWarning = false; let lastError = '';
    for (const file of files) {
      try {
        if (file.size > 128 * 1024 * 1024) throw new Error(file.name + ' exceeds the 128 MB audio import limit.');
        const buffer = await decodeAudio(file, engine.context);
        if (state.generation !== generation) return;
        checkAudioBudget(buffer);
        // Relink native project media by exact filename or media name, retaining its original ID.
        const baseName = file.name.replace(/\.[^.]+$/, '');
        const missing = projectStore.project.media.find(item => !state.buffers.has(item.id) && (item.name === file.name || item.name === baseName));
        const mediaId = projectStore.addMedia({ id: missing?.id, name: file.name, mime: file.type || 'audio/wav', duration: buffer.duration, sample_rate: buffer.sampleRate, size: file.size });
        state.media.set(mediaId, file); state.buffers.set(mediaId, buffer);
        try { await dbMedia(mediaId, file); } catch { storageWarning = true; }
        if (state.generation !== generation) return;
        if (!imported && !missing) projectStore.assignPad(targetPad, mediaId, { name: baseName, start: 0, end: buffer.duration });
        state.selectedMedia = mediaId; imported += 1;
      } catch (error) { failed += 1; lastError = error.message; }
    }
    $('.search').value = ''; renderAll();
    setStatus(imported + ' sound(s) imported' + (failed ? '; ' + failed + ' failed: ' + lastError : '') + (storageWarning ? '. Browser storage failed; download Project + audio now to keep the imported audio.' : '.'));
  }

  function renderLibrary() {
    const query = $('.search').value.trim().toLowerCase();
    const matches = projectStore.project.media.filter(item => String(item.name).toLowerCase().includes(query));
    const media = matches.slice(0, 500);
    const tree = $('#sound-tree'); tree.replaceChildren();
    $('#library-count').textContent = String(projectStore.project.media.length);
    if (!media.length) { const empty = document.createElement('p'); empty.className = 'library-empty'; empty.textContent = query ? 'No matching sounds.' : 'Your library is empty. Import audio files or a folder to begin.'; tree.append(empty); }
    if (matches.length > media.length) { const notice = document.createElement('p'); notice.className = 'library-empty'; notice.textContent = 'Showing the first 500 matches. Narrow your search to see other sounds.'; tree.append(notice); }
    media.forEach(item => {
      const sound = document.createElement('button');
      sound.className = 'sound' + (item.id === state.selectedMedia ? ' selected' : '');
      sound.draggable = true; sound.dataset.mediaId = item.id;
      const icon = document.createElement('span'); icon.className = 'sound-icon'; icon.textContent = '∿';
      const details = document.createElement('span'), name = document.createElement('strong'), meta = document.createElement('small');
      name.textContent = item.name; meta.textContent = (Number(item.duration) || 0).toFixed(2) + ' s · ' + (state.buffers.has(item.id) || state.media.has(item.id) ? 'Audio available' : 'Load to check audio');
      details.append(name, meta); sound.append(icon, details);
      sound.addEventListener('click', act(async () => { state.selectedMedia = item.id; $$('#sound-tree .sound[data-media-id]').forEach(element => element.classList.toggle('selected', element.dataset.mediaId === item.id)); $('#use-sound').disabled = $('#download-sound').disabled = false; const engine = await ensureAudio(); const buffer = state.buffers.get(item.id); if (!engine || !buffer) throw new Error('This sound is missing. Reimport its original file.'); state.previewSource?.stop(); const source = engine.context.createBufferSource(); source.buffer = buffer; source.connect(engine.graph.master); source.start(); state.previewSource = source; source.onended = () => { source.disconnect(); if (state.previewSource === source) state.previewSource = null; }; }));
      sound.addEventListener('dblclick', act(() => assignMedia(item.id, state.selectedPad)));
      sound.addEventListener('dragstart', event => { event.dataTransfer.setData('application/x-anharmonic-media', item.id); event.dataTransfer.effectAllowed = 'copy'; });
      tree.append(sound);
    });
    $('#use-sound').disabled = !state.selectedMedia;
    $('#download-sound').disabled = !state.selectedMedia;
    mediaIds().filter(id => !state.buffers.has(id) && !state.media.has(id)).slice(0, 128).forEach(id => {
      const relink = document.createElement('button'); relink.className = 'sound missing-media';
      relink.textContent = 'Relink missing sound: ' + id; relink.title = 'Choose imported audio to restore every pad and clip that references this sound.';
      relink.addEventListener('click', act(event => openMenu(event.currentTarget, projectStore.project.media.filter(item => state.buffers.has(item.id)).map(item => ({ label: item.name, action: () => relinkMedia(id, item.id) })))));
      tree.append(relink);
    });
  }
  async function relinkMedia(missingId, selectedId) {
    const generation = state.generation;
    await ensureAudio();
    const blob = state.media.get(selectedId), buffer = state.buffers.get(selectedId), metadata = projectStore.project.media.find(item => item.id === selectedId);
    if (!blob || !buffer || !metadata) throw new Error('Import and select a replacement sound in the library first.');
    if (generation !== state.generation) return;
    checkAudioBudget(buffer, state.buffers, missingId);
    state.media.set(missingId, blob); state.buffers.set(missingId, buffer);
    projectStore.addMedia({ ...metadata, id: missingId }); renderAll();
    try { await dbMedia(missingId, blob); setStatus('All references to the missing sound have been relinked.'); }
    catch { setStatus('Sound relinked. Browser storage failed; download Project + audio to keep the replacement.'); }
  }
  function assignMedia(mediaId, index) {
    const item = projectStore.project.media.find(media => media.id === mediaId);
    if (!item) throw new Error('Select a sound from the project library.');
    projectStore.assignPad(index, mediaId, { name: item.name.replace(/\.[^.]+$/, ''), start: 0, end: item.duration || 0 });
    state.selections.delete(index + ':' + mediaId);
    renderPads(); if (state.workspace === 'sampler' || state.workspace === 'beats') renderWorkspace(state.workspace);
    setStatus(item.name + ' assigned to pad ' + padNumber(index));
  }

  function drawWaveform(canvas, buffer, selection = null) {
    if (!canvas) return;
    const bounds = canvas.getBoundingClientRect(), ratio = window.devicePixelRatio || 1;
    canvas.width = Math.max(100, Math.floor(bounds.width * ratio)); canvas.height = Math.max(30, Math.floor(bounds.height * ratio));
    const context = canvas.getContext('2d'), width = canvas.width, height = canvas.height;
    context.clearRect(0, 0, width, height);
    context.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim(); context.beginPath();
    if (buffer) {
      const data = buffer.getChannelData(0), stride = Math.max(1, Math.floor(data.length / width));
      for (let x = 0; x < width; x++) {
        let min = 0, max = 0;
        for (let index = Math.floor(x / width * data.length); index < Math.min(data.length, Math.floor(x / width * data.length) + stride); index++) { min = Math.min(min, data[index]); max = Math.max(max, data[index]); }
        context.moveTo(x, height / 2 + min * height * .46); context.lineTo(x, height / 2 + max * height * .46);
      }
    } else { context.moveTo(0, height / 2); context.lineTo(width, height / 2); }
    context.stroke();
    if (buffer && selection) {
      const start = width * selection.start / buffer.duration, end = width * selection.end / buffer.duration;
      context.globalAlpha = .18; context.fillStyle = context.strokeStyle; context.fillRect(start, 0, end - start, height); context.globalAlpha = 1;
      context.beginPath(); context.moveTo(start, 0); context.lineTo(start, height); context.moveTo(end, 0); context.lineTo(end, height); context.lineWidth = 2 * ratio; context.stroke();
    }
  }
  function renderPads() {
    $('#pad-grid').innerHTML = Array.from({ length: 16 }, (_, offset) => {
      const index = state.bank * 16 + offset, record = padRecord(index);
      return '<button class="pad ' + (index === state.selectedPad ? 'selected' : '') + '" data-pad="' + index + '" aria-label="Pad ' + padNumber(index) + ': ' + escapeHTML(record.name) + '"><b>' + padNumber(offset) + '</b><strong>' + escapeHTML(record.name) + '</strong><small>' + (record.sample_id ? '● ' : '') + (offset < 8 ? offset + 1 : '') + '</small></button>';
    }).join('');
    $$('.pad').forEach(pad => {
      pad.addEventListener('pointerdown', act(async event => {
        if (event.button !== 0) return;
        const index = Number(pad.dataset.pad); state.selectedPad = index;
        $$('.pad').forEach(item => item.classList.toggle('selected', item === pad)); syncInspector();
        if (state.workspace === 'sampler') renderSampler();
        pad.setPointerCapture?.(event.pointerId);
        let released = false;
        const release = () => { released = true; releasePad(index); pad.removeEventListener('pointerup', release); pad.removeEventListener('pointercancel', release); };
        pad.addEventListener('pointerup', release); pad.addEventListener('pointercancel', release);
        await triggerPad(index);
        if (released && padRecord(index).mode !== 'one-shot') releasePad(index);
      }));
      pad.addEventListener('keydown', act(event => { if (event.code === 'Enter') return triggerPad(Number(pad.dataset.pad)); }));
      pad.addEventListener('dragover', event => event.preventDefault());
      pad.addEventListener('drop', act(event => { event.preventDefault(); assignMedia(event.dataTransfer.getData('application/x-anharmonic-media'), Number(pad.dataset.pad)); }));
    });
    $$('.pad-bank button').forEach(item => item.classList.toggle('active', Number(item.dataset.bank) === state.bank));
    syncInspector();
  }
  function syncInspector() {
    const record = padRecord();
    $('#inspector-name').textContent = 'Pad ' + padNumber(state.selectedPad) + ' · ' + record.name;
    $('#pad-pitch').value = record.pitch; $('#pitch-value').textContent = record.pitch + ' st';
    $('#pad-pan').value = record.pan * 100; $('#pan-value').textContent = record.pan === 0 ? 'C' : Math.round(Math.abs(record.pan) * 100) + '% ' + (record.pan < 0 ? 'L' : 'R');
    $('#pad-gain').value = record.gain * 100; $('#gain-value').textContent = record.gain > 0 ? (20 * Math.log10(record.gain)).toFixed(1) + ' dB' : '−∞ dB';
    $('#pad-mode').value = record.mode; $('#pad-track-label').textContent = 'TRACK ' + padNumber(record.track);
    drawWaveform($('#inspector-waveform'), padBuffer(), currentSelection());
  }


  function snapBeat(beat, resolution = state.arrangeSnap) { return Math.max(0, resolution ? Math.round(beat / resolution) * resolution : Math.round(beat * 100) / 100); }
  function clipName(clip) {
    return clip.kind === 'audio'
      ? projectStore.project.media.find(item => item.id === clip.ref)?.name || 'Missing audio'
      : projectStore.project.patterns.find(pattern => pattern.id === clip.ref)?.name || 'Missing pattern';
  }
  function addClip(rowIndex, beat, mediaId = null) {
    const media = mediaId && projectStore.project.media.find(item => item.id === mediaId);
    if (mediaId && !media) throw new Error('Drop an audio item from this project library.');
    const length = media ? media.duration * projectStore.project.bpm / 60 : projectStore.pattern.bars * 4;
    projectStore.transact('add arrangement clip', project => project.rows[rowIndex].clips.push({
      id: uid('clip'), kind: media ? 'audio' : 'pattern', ref: media ? media.id : projectStore.pattern.id,
      start_beat: snapBeat(beat), length_beats: Math.max(.01, length), offset: 0,
      source_length: media ? media.duration : length, gain: 1, track: project.rows[rowIndex].record_track || 0, loop: false, reverse: false, mute: false
    }));
    renderSong(); setStatus((media ? media.name : projectStore.pattern.name) + ' added to song.');
  }
  function renderSong() {
    const rows = projectStore.project.rows;
    const end = rows.reduce((maximum, row) => row.clips.reduce((value, clip) => Math.max(value, clip.start_beat + clip.length_beats + 4), maximum), 32);
    if (rows.length > 128 || rows.reduce((sum, row) => sum + row.clips.length, 0) > 4096 || end > 2048) {
      shell('Song', '', '<p class="sampler-help">This arrangement exceeds the browser editor limit (128 rows, 4096 clips, or 2048 beats). Its data is preserved. Use the desktop editor to shorten or divide the arrangement. Browser playback and export remain available within the audio engine limits.</p>'); return;
    }
    const width = Math.ceil(end / 4) * 4 * 45;
    const ruler = Array.from({ length: Math.ceil(end / 4) }, (_, index) => '<span style="width:180px">' + String(index + 1).padStart(2, '0') + '</span>').join('');
    shell('Song', button('+ ADD TRACK', 'add-track') + button(state.arrangeTool.toUpperCase() + ' ▾', 'song-tool') + button('SNAP: ' + ({ 4: 'BAR', 1: 'BEAT', .25: '1/16', 0: 'OFF' }[state.arrangeSnap]) + ' ▾', 'song-snap') + button('PATTERN: ' + projectStore.pattern.name + ' ▾', 'pattern-menu'),
      '<div class="timeline"><div class="ruler" style="width:' + (190 + width) + 'px"><span style="width:190px">BAR / BEAT</span>' + ruler + '</div><div id="song-playhead" aria-hidden="true"></div>' +
      rows.map((row, rowIndex) => '<div class="track-row" data-row-index="' + rowIndex + '" style="min-width:' + (190 + width) + 'px"><div class="track-head"><strong>' + escapeHTML(row.name) + '</strong><span>' +
        '<button class="row-record ' + (row.record_armed ? 'active' : '') + '" data-row-index="' + rowIndex + '" aria-label="Arm ' + escapeHTML(row.name) + ' for microphone recording">R</button> ' +
        '<button class="row-mute ' + (row.mute ? 'active' : '') + '" data-row-index="' + rowIndex + '" aria-label="Mute ' + escapeHTML(row.name) + '">M</button> ' +
        '<button class="row-solo ' + (row.solo ? 'active' : '') + '" data-row-index="' + rowIndex + '" aria-label="Solo ' + escapeHTML(row.name) + '">S</button> ' +
        '<button class="row-options" data-row-index="' + rowIndex + '" aria-label="Options for ' + escapeHTML(row.name) + '">⋮</button></span></div><div class="track-lane" data-row-index="' + rowIndex + '" style="min-width:' + width + 'px">' +
        row.clips.map(clip => '<button class="clip ' + (clip.kind === 'audio' ? 'audio ' : '') + (clip.mute ? 'muted' : '') + '" data-clip="' + escapeHTML(clip.id) + '" data-row-index="' + rowIndex + '" style="left:' + clip.start_beat * 45 + 'px;width:' + Math.max(8, clip.length_beats * 45) + 'px" title="' + escapeHTML(clipName(clip)) + '">' + escapeHTML(clipName(clip)) + '<i></i></button>').join('') + '</div></div>').join('') +
      '<p class="empty-timeline">Draw places the selected pattern. Drop library audio onto a row. Select moves clips; the right edge resizes. Right-click opens clip options. Arm a row, then Record to capture a microphone take.</p></div>');
    on('.add-track', 'click', () => { projectStore.transact('add arrangement row', project => project.rows.push({ id: uid('row'), name: 'Track ' + (project.rows.length + 1), mute: false, solo: false, record_armed: false, record_source: 'audio', record_track: Math.min(7, project.rows.length), clips: [] })); renderSong(); });
    on('.song-tool', 'click', event => openMenu(event.currentTarget, ['select', 'draw', 'slice', 'mute', 'erase'].map(tool => ({ label: tool.toUpperCase(), action: () => { state.arrangeTool = tool; renderSong(); } }))));
    on('.song-snap', 'click', event => openMenu(event.currentTarget, [['BAR', 4], ['BEAT', 1], ['1/16', .25], ['OFF', 0]].map(([label, value]) => ({ label, action: () => { state.arrangeSnap = value; renderSong(); } }))));
    on('.pattern-menu', 'click', event => patternMenu(event.currentTarget));
    for (const action of ['mute', 'solo', 'record']) $$('.row-' + action).forEach(control => control.addEventListener('click', act(() => {
      const index = Number(control.dataset.rowIndex);
      projectStore.transact(action + ' arrangement row', project => {
        const row = project.rows[index];
        if (action === 'record') { const armed = !row.record_armed; project.rows.forEach(item => item.record_armed = false); row.record_armed = armed; }
        else row[action] = !row[action];
      }); renderSong();
    })));
    $$('.row-options').forEach(control => control.addEventListener('click', act(event => {
      const rowIndex = Number(control.dataset.rowIndex);
      openMenu(event.currentTarget, [
        { label: 'Rename row', action: () => { const name = prompt('Row name', projectStore.project.rows[rowIndex].name); if (name?.trim()) { projectStore.setRow(rowIndex, { name: name.trim().slice(0, 200) }); renderSong(); } } },
        ...projectStore.project.tracks.map((track, index) => ({ label: 'Route audio to ' + track.name + (projectStore.project.rows[rowIndex].record_track === index ? ' ✓' : ''), action: () => { projectStore.transact('route arrangement audio', project => { project.rows[rowIndex].record_track = index; project.rows[rowIndex].clips.filter(clip => clip.kind === 'audio').forEach(clip => clip.track = index); }); renderSong(); } })),
        { label: 'Delete row (Undo available)', action: () => { projectStore.transact('delete arrangement row', project => project.rows.splice(rowIndex, 1)); renderSong(); } }
      ]);
    })));
    $$('.track-lane').forEach(lane => {
      lane.addEventListener('click', act(event => {
        if (event.target.closest('.clip')) return;
        if (state.arrangeTool === 'draw') addClip(Number(lane.dataset.rowIndex), (event.clientX - lane.getBoundingClientRect().left) / 45);
      }));
      lane.addEventListener('dragover', event => event.preventDefault());
      lane.addEventListener('drop', act(event => { event.preventDefault(); addClip(Number(lane.dataset.rowIndex), (event.clientX - lane.getBoundingClientRect().left) / 45, event.dataTransfer.getData('application/x-anharmonic-media')); }));
    });
    $$('.clip').forEach(element => {
      const rowIndex = Number(element.dataset.rowIndex), id = element.dataset.clip;
      const edit = (label, change) => { projectStore.transact(label, project => { const row = project.rows[rowIndex]; const index = row.clips.findIndex(clip => clip.id === id); if (index >= 0) change(row, row.clips[index], index); }); renderSong(); };
      element.addEventListener('contextmenu', act(event => { event.preventDefault(); openMenu(element, [
        { label: 'Mute / unmute clip', action: () => edit('mute clip', (_row, clip) => clip.mute = !clip.mute) },
        { label: 'Loop / unloop clip', action: () => edit('loop clip', (_row, clip) => clip.loop = !clip.loop) },
        ...(projectStore.project.rows[rowIndex].clips.find(clip => clip.id === id)?.kind === 'audio' ? [{ label: 'Reverse audio clip', action: () => edit('reverse audio clip', (_row, clip) => clip.reverse = !clip.reverse) }] : []),
        { label: 'Duplicate clip', action: () => edit('duplicate clip', (row, clip) => row.clips.push({ ...clip, id: uid('clip'), start_beat: clip.start_beat + clip.length_beats })) },
        { label: 'Delete clip (Undo available)', action: () => edit('delete clip', (row, _clip, index) => row.clips.splice(index, 1)) }
      ]); }));
      element.addEventListener('pointerdown', act(event => {
        if (event.button !== 0) return;
        event.preventDefault(); event.stopPropagation();
        const original = { ...projectStore.project.rows[rowIndex].clips.find(clip => clip.id === id) };
        if (state.arrangeTool === 'erase') return edit('erase clip', (row, _clip, index) => row.clips.splice(index, 1));
        if (state.arrangeTool === 'mute') return edit('mute clip', (_row, clip) => clip.mute = !clip.mute);
        if (state.arrangeTool === 'slice') {
          const split = snapBeat((event.clientX - element.getBoundingClientRect().left) / 45);
          if (split <= 0 || split >= original.length_beats) return;
          if (original.kind === 'pattern') {
            const length = projectStore.project.patterns.find(pattern => pattern.id === original.ref)?.bars * 4;
            if (!length || split % length !== 0) throw new Error('Pattern clips can be split at whole pattern boundaries. Render a pattern to WAV for a cut within the pattern.');
          } else if (original.reverse || original.loop) throw new Error('Render reversed or looping clips to WAV before slicing to preserve their playback phase.');
          return edit('slice clip', (row, clip) => {
            const right = { ...clip, id: uid('clip'), start_beat: clip.start_beat + split, length_beats: clip.length_beats - split, offset: clip.kind === 'audio' ? (clip.offset || 0) + split * 60 / projectStore.project.bpm : 0 };
            if (clip.kind === 'audio' && clip.source_length > 0) {
              const elapsed = split * 60 / projectStore.project.bpm;
              if (elapsed >= clip.source_length) throw new Error('The cut falls beyond the source audio. Render this extended clip before slicing.');
              right.source_length = Math.max(.001, clip.source_length - elapsed);
              clip.source_length = Math.min(clip.source_length, elapsed);
            }
            clip.length_beats = split; row.clips.push(right);
          });
        }
        const resize = event.clientX - element.getBoundingClientRect().left > element.offsetWidth - 10;
        const startX = event.clientX;
        element.setPointerCapture?.(event.pointerId);
        const move = current => {
          const delta = (current.clientX - startX) / 45;
          if (resize) element.style.width = Math.max(8, snapBeat(original.length_beats + delta) * 45) + 'px';
          else element.style.left = snapBeat(original.start_beat + delta) * 45 + 'px';
        };
        const cleanup = () => { element.removeEventListener('pointermove', move); element.removeEventListener('pointerup', finish); element.removeEventListener('pointercancel', cancel); };
        const cancel = () => { cleanup(); renderSong(); };
        const finish = act(current => {
          cleanup(); const delta = (current.clientX - startX) / 45;
          if (Math.abs(current.clientX - startX) < 2) return;
          edit(resize ? 'resize clip' : 'move clip', (_row, clip) => { if (resize) clip.length_beats = Math.max(.01, snapBeat(original.length_beats + delta)); else clip.start_beat = snapBeat(original.start_beat + delta); });
        });
        element.addEventListener('pointermove', move); element.addEventListener('pointerup', finish); element.addEventListener('pointercancel', cancel);
      }));
    });
  }

  function patternMenu(anchor) {
    openMenu(anchor, [
      ...projectStore.project.patterns.map((pattern, index) => ({ label: pattern.name + (index === projectStore.project.selected_pattern ? ' ✓' : ''), action: () => { projectStore.transact('select pattern', project => project.selected_pattern = index); renderWorkspace(state.workspace); } })),
      { label: '+ New empty pattern', action: () => { projectStore.transact('new pattern', project => { project.patterns.push({ id: uid('pattern'), name: 'Pattern ' + (project.patterns.length + 1), bars: 1, div: 4, notes: [], steps: {} }); project.selected_pattern = project.patterns.length - 1; }); renderWorkspace(state.workspace); } },
      { label: 'Duplicate current pattern', action: () => { projectStore.transact('duplicate pattern', project => { const pattern = JSON.parse(JSON.stringify(project.patterns[project.selected_pattern])); pattern.id = uid('pattern'); pattern.name += ' copy'; pattern.notes.forEach(note => note.id = uid('note')); project.patterns.push(pattern); project.selected_pattern = project.patterns.length - 1; }); renderWorkspace(state.workspace); } },
      { label: 'Rename current pattern', action: () => { const name = prompt('Pattern name', projectStore.pattern.name); if (name?.trim()) { projectStore.transact('rename pattern', project => project.patterns[project.selected_pattern].name = name.trim().slice(0, 200)); renderWorkspace(state.workspace); } } }
    ]);
  }
  function renderBeats() {
    const pattern = projectStore.pattern, total = pattern.bars * 4 * pattern.div;
    if (total > 256) {
      shell('Beats', button(pattern.name + ' ▾', 'pattern-menu'), '<p class="sampler-help">This pattern exceeds the browser editor limit of 256 steps. All steps are retained for playback and export. Choose or create a shorter pattern here, or edit this long pattern in the desktop application.</p>');
      on('.pattern-menu', 'click', event => patternMenu(event.currentTarget)); return;
    }
    const indices = Array.from({ length: 16 }, (_, index) => state.bank * 16 + index).filter(index => !state.loadedOnly || padRecord(index).sample_id);
    shell('Beats', button(pattern.name + ' ▾', 'pattern-menu') + button('− BAR', 'bars-down', pattern.bars <= 1) + '<span class="bars-readout">' + pattern.bars + ' BARS</span>' + button('+ BAR', 'bars-up', pattern.bars >= 64) + '<label>GRID <select id="step-division"><option value="2">1/8</option><option value="3">1/12</option><option value="4">1/16</option><option value="6">1/24</option><option value="8">1/32</option></select></label>' + button(state.loadedOnly ? 'LOADED ✓' : 'LOADED', 'loaded-toggle') + button(state.follow ? 'FOLLOW ✓' : 'FOLLOW', 'follow-toggle') + button('CLEAR', 'clear-beats'),
      '<div class="step-editor"><div class="step-grid">' + indices.map(index => '<div class="step-line" style="grid-template-columns:150px repeat(' + total + ',24px)"><label><b>' + padNumber(index) + '</b> ' + escapeHTML(padRecord(index).name) + '</label>' + Array.from({ length: total }, (_, step) => {
        const velocity = pattern.steps[index]?.[step] || 0;
        return '<button class="' + (velocity ? 'on' : '') + '" style="--velocity:' + velocity + '" data-pad="' + index + '" data-step="' + step + '" aria-pressed="' + Boolean(velocity) + '" aria-label="Pad ' + padNumber(index) + ' step ' + (step + 1) + '"></button>';
      }).join('') + '</div>').join('') + '</div><p class="sampler-help">Bank ' + 'ABCD'[state.bank] + ' · Click or drag to paint steps. Right-drag erases. Scroll a lit cell to change velocity. Pattern playback follows this pattern’s length and division.</p></div>');
    $('#step-division').value = String(pattern.div);
    on('.pattern-menu', 'click', event => patternMenu(event.currentTarget));
    on('.bars-down', 'click', () => { projectStore.transact('shorten pattern', project => project.patterns[project.selected_pattern].bars = Math.max(1, pattern.bars - 1)); renderBeats(); });
    on('.bars-up', 'click', () => { projectStore.transact('lengthen pattern', project => project.patterns[project.selected_pattern].bars = Math.min(64, pattern.bars + 1)); renderBeats(); });
    on('#step-division', 'change', event => {
      const division = Number(event.target.value);
      projectStore.transact('change pattern grid', project => {
        const current = project.patterns[project.selected_pattern], ratio = division / current.div;
        Object.keys(current.steps).forEach(pad => current.steps[pad] = Object.fromEntries(Object.entries(current.steps[pad]).map(([step, velocity]) => [Math.round(Number(step) * ratio), velocity])));
        current.div = division;
      }); renderBeats();
    });
    on('.loaded-toggle', 'click', () => { state.loadedOnly = !state.loadedOnly; renderBeats(); });
    on('.follow-toggle', 'click', () => { state.follow = !state.follow; renderBeats(); });
    on('.clear-beats', 'click', () => { projectStore.transact('clear pattern steps', project => project.patterns[project.selected_pattern].steps = {}); renderBeats(); });
    const grid = $('.step-grid'); let gesture = null;
    const cellAt = event => document.elementFromPoint(event.clientX, event.clientY)?.closest('.step-line button');
    const paint = cell => {
      if (!gesture || !cell || !grid.contains(cell)) return;
      const key = cell.dataset.pad + ':' + cell.dataset.step;
      if (gesture.seen.has(key)) return; gesture.seen.add(key);
      const velocity = gesture.erase ? null : 1;
      projectStore.setStep(Number(cell.dataset.pad), Number(cell.dataset.step), velocity);
      cell.classList.toggle('on', Boolean(velocity)); cell.style.setProperty('--velocity', velocity || 0); cell.setAttribute('aria-pressed', String(Boolean(velocity)));
    };
    grid.addEventListener('pointerdown', act(event => { const cell = cellAt(event); if (!cell) return; event.preventDefault(); gesture = { erase: event.button === 2 || cell.classList.contains('on'), seen: new Set() }; grid.setPointerCapture(event.pointerId); paint(cell); }));
    grid.addEventListener('pointermove', act(event => paint(cellAt(event))));
    grid.addEventListener('pointerup', () => gesture = null); grid.addEventListener('pointercancel', () => gesture = null);
    grid.addEventListener('contextmenu', event => event.preventDefault());
    grid.addEventListener('click', act(event => { if (event.detail === 0 && event.target.matches('.step-line button')) { projectStore.toggleStep(Number(event.target.dataset.pad), Number(event.target.dataset.step)); renderBeats(); } }));
    grid.addEventListener('wheel', event => {
      const cell = cellAt(event); if (!cell?.classList.contains('on')) return;
      event.preventDefault();
      const velocity = clamp(Number(cell.style.getPropertyValue('--velocity')) + (event.deltaY < 0 ? .05 : -.05), .05, 1);
      try { projectStore.setStep(Number(cell.dataset.pad), Number(cell.dataset.step), velocity); cell.style.setProperty('--velocity', velocity); setStatus('Velocity ' + Math.round(velocity * 100) + '%'); } catch (error) { report(error); }
    }, { passive: false });
  }

  function renderSampler() {
    const buffer = padBuffer(), selection = currentSelection(), duration = buffer?.duration || 0;
    shell('Sampler · Pad ' + padNumber(state.selectedPad), button('IMPORT', 'import-sampler') + button('PREVIEW', 'preview-pad', !buffer) + button('MAP SLICES ▾', 'chop-tools', !buffer) + button('REVERSE ' + (padRecord().reverse ? '✓' : ''), 'reverse-sample', !buffer),
      '<div class="sampler-editor"><div class="sampler-canvas"><canvas id="waveform" aria-label="Audio waveform; drag selection markers"></canvas></div><div class="sampler-actions"><label>START <input id="sample-start" type="number" min="0" max="' + duration + '" step=".001" value="' + selection.start.toFixed(3) + '"' + (!buffer ? ' disabled' : '') + '> s</label><label>END <input id="sample-end" type="number" min="0" max="' + duration + '" step=".001" value="' + selection.end.toFixed(3) + '"' + (!buffer ? ' disabled' : '') + '> s</label>' + button('SNAP: ' + ({ 0: 'OFF', 1: 'BEAT', .5: '1/8', .25: '1/16' }[state.sampleSnap]) + ' ▾', 'sample-snap') + button('APPLY RANGE', 'assign-sample', !buffer) + '</div><p class="sampler-help">' + (buffer ? escapeHTML(padRecord().name) + ' · ' + duration.toFixed(3) + ' seconds · ' + buffer.sampleRate + ' Hz. Drag near either selection edge. Apply Range saves the trim without altering the source audio.' : 'Import or assign a real audio file to edit its waveform.') + '</p></div>');
    drawWaveform($('#waveform'), buffer, selection);
    on('.import-sampler', 'click', () => $('#audio-file').click());
    on('.preview-pad', 'click', () => triggerPad(state.selectedPad, { start: selection.start, end: selection.end, duration: selection.end - selection.start }));
    on('#sample-start', 'change', event => updateSelection('start', event.target.value));
    on('#sample-end', 'change', event => updateSelection('end', event.target.value));
    on('.assign-sample', 'click', () => { projectStore.setPad(state.selectedPad, currentSelection()); syncInspector(); setStatus('Pad trim saved. Source audio is unchanged.'); });
    on('.reverse-sample', 'click', () => { projectStore.setPad(state.selectedPad, { reverse: !padRecord().reverse }); renderSampler(); });
    on('.sample-snap', 'click', event => openMenu(event.currentTarget, [['OFF', 0], ['BEAT', 1], ['1/8', .5], ['1/16', .25]].map(([label, value]) => ({ label, action: () => { state.sampleSnap = value; renderSampler(); } }))));
    on('.chop-tools', 'click', event => openMenu(event.currentTarget, [4, 8, 16].map(count => ({
      label: count + ' equal slices from the selection',
      action: () => {
        const available = 64 - state.selectedPad, slices = Math.min(count, available);
        const targets = projectStore.project.pads.slice(state.selectedPad, state.selectedPad + slices);
        if (targets.slice(1).some(pad => pad.sample_id) && !confirm('Replace samples on the next ' + (slices - 1) + ' pads? Undo will restore them.')) return;
        projectStore.transact('map sample slices', project => {
          for (let offset = 0; offset < slices; offset++) project.pads[state.selectedPad + offset] = {
            ...project.pads[state.selectedPad + offset], sample_id: padRecord().sample_id,
            name: padRecord().name + ' · ' + (offset + 1), start: selection.start + (selection.end - selection.start) * offset / count,
            end: selection.start + (selection.end - selection.start) * (offset + 1) / count
          };
        }); state.selections.clear(); renderPads(); renderSampler(); setStatus(slices + ' slices mapped to pads.');
      }
    }))));
    if (buffer) {
      const canvas = $('#waveform'); let edge = null;
      canvas.addEventListener('pointerdown', event => {
        event.preventDefault(); canvas.setPointerCapture(event.pointerId);
        const value = (event.clientX - canvas.getBoundingClientRect().left) / canvas.getBoundingClientRect().width * duration;
        const current = currentSelection(); edge = Math.abs(value - current.start) <= Math.abs(value - current.end) ? 'start' : 'end';
        updateSelection(edge, value, false);
      });
      canvas.addEventListener('pointermove', event => { if (edge) updateSelection(edge, (event.clientX - canvas.getBoundingClientRect().left) / canvas.getBoundingClientRect().width * duration, false); });
      canvas.addEventListener('pointerup', () => { edge = null; }); canvas.addEventListener('pointercancel', () => edge = null);
    }
  }
  function updateSelection(edge, value, rerender = true) {
    const buffer = padBuffer(); if (!buffer) return;
    const selection = { ...currentSelection() }, quantum = state.sampleSnap * 60 / projectStore.project.bpm;
    const point = quantum ? Math.round(Number(value) / quantum) * quantum : Number(value);
    if (edge === 'start') selection.start = clamp(point, 0, Math.max(0, selection.end - 1 / buffer.sampleRate));
    else selection.end = clamp(point, selection.start + 1 / buffer.sampleRate, buffer.duration);
    state.selections.set(selectionKey(), selection);
    if (rerender) renderSampler(); else { $('#sample-start').value = selection.start.toFixed(3); $('#sample-end').value = selection.end.toFixed(3); drawWaveform($('#waveform'), buffer, selection); }
  }


  function addNotes(pitches, start = 0, duration = .5) {
    const target = state.notePad, mono = target === null ? state.noteMono : padRecord(target).mono;
    projectStore.transact('add notes', project => {
      const pattern = project.patterns[project.selected_pattern];
      if (mono) pattern.notes = pattern.notes.filter(note => note.pad !== target || note.start + note.duration <= start || note.start >= start + duration);
      (mono ? pitches.slice(0, 1) : pitches).forEach(pitch => pattern.notes.push({ id: uid('note'), pitch: clamp(pitch, 0, 127), start, duration, velocity: .8, pad: target }));
    });
    renderNotes();
  }
  function renderNotes() {
    const pattern = projectStore.pattern, notes = pattern.notes.filter(note => note.pad === state.notePad);
    if (notes.length > 4096 || notes.some(note => note.start + note.duration > 2048)) {
      shell('Notes', button(pattern.name + ' ▾', 'pattern-menu'), '<p class="sampler-help">This piano roll exceeds the browser editor limit (4096 notes or 2048 beats). Its notes are preserved for playback and export. Choose a shorter pattern or edit this one in the desktop application.</p>');
      on('.pattern-menu', 'click', event => patternMenu(event.currentTarget)); return;
    }
    const root = state.notePad === null ? state.noteRoot : padRecord(state.notePad).root_note;
    const mono = state.notePad === null ? state.noteMono : padRecord(state.notePad).mono;
    const topPitch = notes.reduce((maximum, note) => Math.max(maximum, note.pitch), 83);
    const bottomPitch = notes.reduce((minimum, note) => Math.min(minimum, note.pitch), 48), rows = topPitch - bottomPitch + 1;
    const width = notes.reduce((maximum, note) => Math.max(maximum, (note.start + note.duration + 1) * 70), Math.max(800, pattern.bars * 4 * 70));
    shell('Notes', button(pattern.name + ' ▾', 'pattern-menu') + button('SOUND: ' + (state.notePad === null ? 'SYNTH' : 'PAD ' + padNumber(state.notePad)) + ' ▾', 'note-action') + button('ROOT: ' + noteName(root) + ' ▾', 'note-root') + button('MONO ' + (mono ? '✓' : ''), 'note-mono') + button('QUANTIZE', 'note-quantize') + button('CHORD ▾', 'note-chord', mono) + button('CLEAR', 'note-clear'),
      '<div class="piano"><div class="keys">' + Array.from({ length: rows }, (_, index) => '<span>' + noteName(topPitch - index) + '</span>').join('') + '</div><div class="note-grid" id="note-grid" style="width:' + width + 'px;min-width:' + width + 'px;height:' + rows * 20 + 'px">' +
      Array.from({ length: rows }, (_, index) => '<span class="note-row ' + ([1, 3, 6, 8, 10].includes((topPitch - index) % 12) ? 'black-key' : '') + '"></span>').join('') +
      notes.map(note => '<button class="note" data-note-id="' + escapeHTML(note.id) + '" style="left:' + note.start * 70 + 'px;top:' + (topPitch - note.pitch) * 20 + 'px;width:' + Math.max(9, note.duration * 70 - 2) + 'px;opacity:' + (.35 + note.velocity * .65) + '" aria-label="' + noteName(note.pitch) + ' at beat ' + note.start + '">' + noteName(note.pitch) + '<i></i></button>').join('') +
      '</div></div><p class="sampler-help">Click to add a note. Drag to move, or drag the right edge to resize. Right-click deletes; scroll changes velocity. Notes use the selected sound. Root sets the sample’s untransposed pitch.</p>');
    on('.pattern-menu', 'click', event => patternMenu(event.currentTarget));
    on('.note-action', 'click', event => openMenu(event.currentTarget, [
      { label: 'Synthesizer', action: () => { state.notePad = null; renderNotes(); } },
      ...projectStore.project.pads.map((pad, index) => ({ label: 'Pad ' + padNumber(index) + ' · ' + pad.name, action: () => { state.notePad = index; renderNotes(); } }))
    ]));
    on('.note-root', 'click', event => openMenu(event.currentTarget, Array.from({ length: 25 }, (_, index) => ({ label: noteName(48 + index), action: () => { if (state.notePad === null) state.noteRoot = 48 + index; else projectStore.setPad(state.notePad, { root_note: 48 + index }); renderNotes(); } }))));
    on('.note-mono', 'click', () => { if (state.notePad === null) state.noteMono = !state.noteMono; else projectStore.setPad(state.notePad, { mono: !padRecord(state.notePad).mono }); renderNotes(); });
    on('.note-quantize', 'click', () => { projectStore.transact('quantize notes', project => project.patterns[project.selected_pattern].notes.filter(note => note.pad === state.notePad).forEach(note => { note.start = Math.round(note.start * pattern.div) / pattern.div; note.duration = Math.max(1 / pattern.div, Math.round(note.duration * pattern.div) / pattern.div); })); renderNotes(); });
    on('.note-chord', 'click', event => openMenu(event.currentTarget, [['Major', [0, 4, 7]], ['Minor', [0, 3, 7]], ['Dominant 7', [0, 4, 7, 10]], ['Minor 7', [0, 3, 7, 10]]].map(([label, intervals]) => ({ label: noteName(root) + ' ' + label, action: () => addNotes(intervals.map(interval => root + interval), snapBeat(state.beat, 1 / pattern.div)) }))));
    on('.note-clear', 'click', () => { projectStore.transact('clear notes for sound', project => project.patterns[project.selected_pattern].notes = project.patterns[project.selected_pattern].notes.filter(note => note.pad !== state.notePad)); renderNotes(); });
    const grid = $('#note-grid'); let moved = false;
    grid.addEventListener('click', act(async event => {
      if (event.target.closest('.note') || moved) { moved = false; return; }
      const rect = grid.getBoundingClientRect(), start = Math.max(0, Math.floor((event.clientX - rect.left) / 70 * pattern.div) / pattern.div);
      const pitch = clamp(topPitch - Math.floor((event.clientY - rect.top) / 20), 0, 127);
      addNotes([pitch], start, 1 / pattern.div);
      const engine = await ensureAudio(); engine?.triggerNote(pitch, { pad: state.notePad, velocity: .8, duration: 60 / projectStore.project.bpm / pattern.div });
    }));
    grid.querySelectorAll('.note').forEach(element => {
      const id = element.dataset.noteId;
      element.addEventListener('contextmenu', act(event => { event.preventDefault(); projectStore.transact('delete note', project => project.patterns[project.selected_pattern].notes = project.patterns[project.selected_pattern].notes.filter(note => note.id !== id)); renderNotes(); }));
      element.addEventListener('wheel', event => { event.preventDefault(); try { projectStore.transact('note velocity', project => { const note = project.patterns[project.selected_pattern].notes.find(item => item.id === id); note.velocity = clamp(note.velocity + (event.deltaY < 0 ? .05 : -.05), .05, 1); }); renderNotes(); } catch (error) { report(error); } }, { passive: false });
      element.addEventListener('pointerdown', act(event => {
        if (event.button !== 0) return;
        event.preventDefault(); event.stopPropagation();
        const original = { ...pattern.notes.find(note => note.id === id) }, origin = { x: event.clientX, y: event.clientY };
        const resize = event.clientX - element.getBoundingClientRect().left > element.offsetWidth - 8;
        element.setPointerCapture(event.pointerId);
        const move = current => {
          if (resize) element.style.width = Math.max(9, original.duration * 70 + current.clientX - origin.x) + 'px';
          else element.style.transform = 'translate(' + (current.clientX - origin.x) + 'px,' + (current.clientY - origin.y) + 'px)';
        };
        const cleanup = () => { element.removeEventListener('pointermove', move); element.removeEventListener('pointerup', finish); element.removeEventListener('pointercancel', cancel); };
        const cancel = () => { cleanup(); renderNotes(); };
        const finish = act(current => {
          cleanup(); const delta = Math.round((current.clientX - origin.x) / 70 * pattern.div) / pattern.div;
          if (Math.abs(current.clientX - origin.x) + Math.abs(current.clientY - origin.y) < 2) return;
          moved = true;
          projectStore.transact(resize ? 'resize note' : 'move note', project => {
            const note = project.patterns[project.selected_pattern].notes.find(item => item.id === id);
            if (resize) note.duration = Math.max(1 / pattern.div, original.duration + delta);
            else { note.start = Math.max(0, original.start + delta); note.pitch = clamp(original.pitch - Math.round((current.clientY - origin.y) / 20), 0, 127); }
          }); renderNotes();
        });
        element.addEventListener('pointermove', move); element.addEventListener('pointerup', finish); element.addEventListener('pointercancel', cancel);
      }));
    });
  }

  const synthPresets = {
    'Midnight Brass': { osc1: 'saw', osc2: 'square', cutoff: 2400, resonance: .28, attack: .025, decay: .32, sustain: .68, release: .65, drive: .18, spread: .42, volume: .42 },
    'Copper Pluck': { osc1: 'saw', osc2: 'triangle', cutoff: 1150, resonance: .34, attack: .002, decay: .18, sustain: .08, release: .22, drive: .28, spread: .18, volume: .48 },
    'Velvet Poly': { osc1: 'saw', osc2: 'saw', cutoff: 1750, resonance: .18, attack: .08, decay: .55, sustain: .72, release: 1.25, drive: .12, spread: .76, volume: .38 },
    'Neon Sub': { osc1: 'sine', osc2: 'square', cutoff: 680, resonance: .16, attack: .004, decay: .3, sustain: .8, release: .32, drive: .4, spread: .04, volume: .5 }
  };
  function renderInstruments() {
    const synth = projectStore.project.synth, arp = projectStore.project.arp;
    const ranges = {
      cutoff: [80, 20000, 1], resonance: [0, .95, .01], attack: [.001, 2, .001], decay: [.001, 2, .001],
      sustain: [0, 1, .01], release: [.01, 4, .01], drive: [0, 1, .01], spread: [0, 1, .01],
      volume: [0, 1, .01], osc_mix: [0, 1, .01], detune: [0, 50, 1], sub: [0, 1, .01]
    };
    shell('Instruments', button('PREVIEW', 'synth-preview') + button('RELEASE ALL', 'synth-panic'),
      '<div class="instrument-editor"><div class="instrument-toolbar"><label>PRESET<select id="synth-preset">' + (synthPresets[synth.name] ? '' : '<option>' + escapeHTML(synth.name) + '</option>') + Object.keys(synthPresets).map(name => '<option' + (name === synth.name ? ' selected' : '') + '>' + escapeHTML(name) + '</option>').join('') + '</select></label><label>TRACK<select id="synth-track">' + projectStore.project.tracks.map((track, index) => '<option value="' + index + '"' + (index === synth.track ? ' selected' : '') + '>' + escapeHTML(track.name) + '</option>').join('') + '</select></label>' +
      '<button id="arp-toggle" class="' + (arp.enabled ? 'active' : '') + '">ARP ' + (arp.enabled ? 'ON' : 'OFF') + '</button><label>RATE<select id="arp-rate"><option value=".125">1/32</option><option value=".25">1/16</option><option value=".5">1/8</option><option value="1">1/4</option></select></label><label>MODE<select id="arp-mode"><option>up</option><option>down</option><option>up/down</option><option>random</option></select></label><label>OCTAVES<select id="arp-octaves"><option>1</option><option>2</option><option>3</option><option>4</option></select></label></div>' +
      '<div class="synth-controls">' + ['osc1', 'osc2'].map(field => '<label>' + field.toUpperCase() + '<select id="' + field + '">' + ['saw', 'square', 'triangle', 'sine'].map(wave => '<option' + (wave === synth[field] ? ' selected' : '') + '>' + wave + '</option>').join('') + '</select></label>').join('') +
      Object.entries(ranges).map(([field, [min, max, step]]) => '<label>' + field.replace('_', ' ').toUpperCase() + '<input data-synth="' + field + '" type="range" min="' + min + '" max="' + max + '" step="' + step + '" value="' + synth[field] + '"><output>' + synth[field] + '</output></label>').join('') + '</div>' +
      '<div class="synth-keyboard" id="synth-keyboard">' + Array.from({ length: 25 }, (_, index) => '<button class="' + ([1, 3, 6, 8, 10].includes(index % 12) ? 'black' : '') + '" data-note="' + (48 + index) + '">' + noteName(48 + index) + '</button>').join('') + '</div><p class="sampler-help">Hold keys to play. ARP sequences held notes. Create synth notes in Notes and place the pattern in Song to arrange it.</p></div>');
    ['osc1', 'osc2'].forEach(field => on('#' + field, 'change', event => projectStore.setSynth({ [field]: event.target.value })));
    $$('[data-synth]').forEach(control => control.addEventListener('input', act(() => { projectStore.setSynth({ [control.dataset.synth]: Number(control.value) }); control.nextElementSibling.textContent = control.value; })));
    on('#synth-preset', 'change', event => { const patch = synthPresets[event.target.value]; if (patch) projectStore.setSynth({ ...patch, name: event.target.value }); renderInstruments(); });
    on('#synth-track', 'change', event => projectStore.setSynth({ track: Number(event.target.value) }));
    on('#arp-toggle', 'click', () => { state.heldSynth.forEach(note => state.engine?.releaseNote(note)); state.heldSynth.clear(); stopArp(); projectStore.setArp({ enabled: !arp.enabled }); renderInstruments(); });
    $('#arp-rate').value = String(arp.rate_beats).replace(/^0\./, '.'); $('#arp-mode').value = arp.mode; $('#arp-octaves').value = String(arp.octaves);
    on('#arp-rate', 'change', event => { projectStore.setArp({ rate_beats: Number(event.target.value) }); if (state.arpTimer) { stopArp(); startArp(); } });
    on('#arp-mode', 'change', event => projectStore.setArp({ mode: event.target.value }));
    on('#arp-octaves', 'change', event => projectStore.setArp({ octaves: Number(event.target.value) }));
    on('.synth-preview', 'click', async () => { const engine = await ensureAudio(); engine?.triggerNote(60, { velocity: .8, duration: .5 }); });
    on('.synth-panic', 'click', () => { stopArp(); state.heldSynth.forEach(note => state.engine?.releaseNote(note)); state.heldSynth.clear(); setStatus('Held synth voices released.'); });
    $$('#synth-keyboard button').forEach(key => {
      const note = Number(key.dataset.note);
      key.addEventListener('pointerdown', act(async event => {
        event.preventDefault(); key.setPointerCapture(event.pointerId);
        state.heldSynth.add(note); const engine = await ensureAudio(); if (!state.heldSynth.has(note) || !engine) return;
        if (projectStore.project.arp.enabled) startArp(); else engine.triggerNote(note, { velocity: .8 });
      }));
      const release = () => { state.heldSynth.delete(note); state.engine?.releaseNote(note); if (!state.heldSynth.size) stopArp(); };
      key.addEventListener('pointerup', release); key.addEventListener('pointercancel', release);
      key.addEventListener('keydown', act(async event => { if (event.code === 'Enter' && !event.repeat) { const engine = await ensureAudio(); engine?.triggerNote(note, { duration: .4 }); } }));
    });
  }
  function startArp() {
    if (state.arpTimer || !state.heldSynth.size) return;
    const tick = () => {
      const arp = projectStore.project.arp;
      const held = [...state.heldSynth].sort((a, b) => a - b);
      if (!held.length || !arp.enabled) { stopArp(); return; }
      let sequence = Array.from({ length: arp.octaves }, (_, octave) => held.map(note => note + octave * 12)).flat().filter(note => note <= 127);
      if (arp.mode === 'down') sequence.reverse();
      if (arp.mode === 'up/down' && sequence.length > 2) sequence = sequence.concat(sequence.slice(1, -1).reverse());
      const index = arp.mode === 'random' ? Math.floor(Math.random() * sequence.length) : state.arpIndex++ % sequence.length;
      state.engine?.triggerNote(sequence[index], { velocity: .8, duration: 60 / projectStore.project.bpm * arp.rate_beats * arp.gate });
      state.arpTimer = setTimeout(tick, 60000 / projectStore.project.bpm * arp.rate_beats);
    };
    tick();
  }
  function stopArp() { clearTimeout(state.arpTimer); state.arpTimer = null; state.arpIndex = 0; }

  function renderMix() {
    const tracks = projectStore.project.tracks, effects = { tone: 0, compression: 1, delay: 0, ...(projectStore.project.web_effects || {}) };
    shell('Mixer', '<span class="bars-readout">Eight stereo tracks → Master</span>',
      '<div class="mixer-editor"><div class="mixer-channels">' + tracks.map((track, index) => '<div class="channel ' + (track.mute ? 'muted' : '') + '"><button class="track-name" data-track="' + index + '" title="Rename track">' + escapeHTML(track.name) + '</button><meter class="track-meter" data-track="' + index + '" min="0" max="1" value="0" aria-label="Measured track level"></meter><div class="fader"><input class="track-gain" aria-label="Track ' + (index + 1) + ' gain" data-track="' + index + '" type="range" min="0" max="200" value="' + track.gain * 100 + '"></div><small class="track-db">' + (track.gain > 0 ? (20 * Math.log10(track.gain)).toFixed(1) + ' dB' : '−∞ dB') + '</small><label class="track-pan">PAN<input class="track-pan-input" aria-label="Track ' + (index + 1) + ' pan" data-track="' + index + '" type="range" min="-100" max="100" value="' + track.pan * 100 + '"></label><div class="channel-actions"><button class="track-mute ' + (track.mute ? 'active' : '') + '" data-track="' + index + '" aria-label="Mute track ' + (index + 1) + '">M</button><button class="track-solo ' + (track.solo ? 'active' : '') + '" data-track="' + index + '" aria-label="Solo track ' + (index + 1) + '">S</button></div></div>').join('') +
      '</div><div class="effects-rack"><strong>MASTER EFFECTS</strong><label>TONE<input data-effect="tone" id="tone-effect" type="range" min="-12" max="12" step=".5" value="' + effects.tone + '"><output>' + effects.tone + ' dB</output></label><label>COMPRESSION<input data-effect="compression" id="compression-effect" type="range" min="1" max="12" step=".1" value="' + effects.compression + '"><output>' + effects.compression + ':1</output></label><label>DELAY<input data-effect="delay" id="delay-effect" type="range" min="0" max="40" value="' + effects.delay + '"><output>' + effects.delay + '%</output></label></div><p class="sampler-help">The output meter shows the measured master level. These browser master effects are saved and included in WAV export. Imported native plugin chains require the desktop application.</p></div>');
    $$('.track-name').forEach(control => control.addEventListener('click', act(() => { const index = Number(control.dataset.track), name = prompt('Track name', tracks[index].name); if (name?.trim()) { projectStore.setTrack(index, { name: name.trim().slice(0, 200) }); renderMix(); } })));
    $$('.track-gain').forEach(control => control.addEventListener('input', act(() => { const gain = Number(control.value) / 100; projectStore.setTrack(Number(control.dataset.track), { gain }); control.closest('.channel').querySelector('.track-db').textContent = gain > 0 ? (20 * Math.log10(gain)).toFixed(1) + ' dB' : '−∞ dB'; })));
    $$('.track-pan-input').forEach(control => control.addEventListener('input', act(() => projectStore.setTrack(Number(control.dataset.track), { pan: Number(control.value) / 100 }))));
    for (const action of ['mute', 'solo']) $$('.track-' + action).forEach(control => control.addEventListener('click', act(() => { const index = Number(control.dataset.track); projectStore.setTrack(index, { [action]: !projectStore.project.tracks[index][action] }); renderMix(); })));
    $$('[data-effect]').forEach(control => control.addEventListener('input', act(() => {
      const field = control.dataset.effect, value = Number(control.value);
      projectStore.transact('master ' + field, project => project.web_effects = { ...effects, ...(project.web_effects || {}), [field]: value });
      control.nextElementSibling.textContent = value + ({ tone: ' dB', compression: ':1', delay: '%' }[field]);
    })));
  }

  async function toggleRecording() {
    if (state.recording) { if (state.recording.recorder.state !== 'inactive') state.recording.recorder.stop(); return; }
    if (state.recordPending) return;
    if (state.recoveryRecording) throw new Error('Use Recover take to download and clear the pending recovery before starting another recording.');
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error('Microphone recording needs a supported browser and HTTPS or localhost.');
    const pad = state.selectedPad, row = projectStore.project.rows.find(item => item.record_armed), rowId = row?.id;
    const generation = state.generation;
    let startBeat = 0;
    state.recordPending = true; $('#record').disabled = true;
    let stream;
    try {
      const engine = await ensureAudio(); if (!engine) return;
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
      const recorder = new MediaRecorder(stream), chunks = [];
      const maximumSeconds = Math.min(240, Math.floor(window.AnharmonicAudio.LIMITS.renderSeconds * window.AnharmonicAudio.LIMITS.sampleRate / engine.context.sampleRate) - 1);
      let recordedBytes = 0, recordTimer;
      state.recording = { recorder, stream }; $('#record').classList.add('recording'); $('#record').setAttribute('aria-label', 'Stop microphone recording');
      const cleanup = () => {
        clearTimeout(recordTimer);
        stream.getTracks().forEach(track => track.stop());
        state.recording = null; $('#record').classList.remove('recording'); $('#record').setAttribute('aria-label', 'Record microphone');
      };
      const keepRecovery = () => {
        const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
        if (blob.size) { state.recoveryRecording = blob; $('#recover-recording').hidden = false; state.dirty = true; }
      };
      let failed = false;
      recorder.addEventListener('dataavailable', event => { if (event.data.size) { chunks.push(event.data); recordedBytes += event.data.size; if (recordedBytes >= 32 * 1024 * 1024 && recorder.state === 'recording') recorder.stop(); } });
      recorder.addEventListener('error', event => { failed = true; cleanup(); keepRecovery(); report(new Error((event.error?.message || 'Microphone recorder failed.') + (state.recoveryRecording ? ' Use Recover take to download the original capture.' : ''))); });
      recorder.addEventListener('stop', act(async () => {
        cleanup();
        state.recordPending = true; $('#record').disabled = true;
        try {
        if (failed || generation !== state.generation) { keepRecovery(); return; }
        const recorded = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
        if (!recorded.size) throw new Error('The microphone produced no audio. Try a longer take.');
        const buffer = await decodeAudio(recorded, engine.context, true);
        if (generation !== state.generation) return;
        checkAudioBudget(buffer);
        const blob = window.AnharmonicAudio.encodeWav(buffer), name = 'Recording ' + new Date().toISOString().replace(/[:.]/g, '-') + '.wav';
        const mediaId = projectStore.addMedia({ name, mime: 'audio/wav', duration: buffer.duration, sample_rate: buffer.sampleRate, size: blob.size });
        state.media.set(mediaId, blob); state.buffers.set(mediaId, buffer);
        let warning = '';
        try { await dbMedia(mediaId, blob); } catch { warning = ' Browser storage failed; download Project + audio to keep this take.'; }
        if (generation !== state.generation) return;
        if (rowId) {
          const index = projectStore.project.rows.findIndex(item => item.id === rowId);
          if (index >= 0) projectStore.transact('record audio clip', project => project.rows[index].clips.push({ id: uid('clip'), kind: 'audio', ref: mediaId, start_beat: startBeat, length_beats: buffer.duration * project.bpm / 60, offset: 0, source_length: buffer.duration, gain: 1, track: project.rows[index].record_track || 0, loop: false, reverse: false, mute: false }));
          else warning += ' The armed row was removed; the take is in the library.';
        } else projectStore.assignPad(pad, mediaId, { name: name.replace('.wav', ''), start: 0, end: buffer.duration });
        state.selectedMedia = mediaId; renderAll(); setStatus('Microphone take captured in ' + (rowId ? 'the armed arrangement row' : 'pad ' + padNumber(pad)) + '.' + warning);
        } catch (error) { keepRecovery(); throw new Error(error.message + (state.recoveryRecording ? ' Use Recover take to download the original capture.' : '')); }
        finally { state.recordPending = false; $('#record').disabled = false; }
      }));
      // Preserve the selected target across permission prompts, but anchor the
      // take to the audio clock only when capture actually begins.
      if (engine.playing) {
        const project = projectStore.project, beat = Math.max(0, engine.beatAt(engine.context.currentTime));
        if (engine.mode === 'pattern') startBeat = beat % (projectStore.pattern.bars * 4);
        else {
          const songEnd = window.AnharmonicAudio.lengthBeats(project, 'song');
          const loopStart = clamp(project.loop_start, 0, songEnd), loopEnd = Math.max(loopStart + .25, Number(project.loop_end ?? songEnd));
          startBeat = project.loop_enabled && beat >= loopEnd ? loopStart + (beat - loopEnd) % (loopEnd - loopStart) : Math.min(beat, songEnd);
        }
      }
      recorder.start(250);
      recordTimer = setTimeout(() => { if (recorder.state === 'recording') recorder.stop(); }, maximumSeconds * 1000);
      setStatus('Recording microphone into ' + (row ? row.name : 'pad ' + padNumber(pad)) + '. Press Record or Stop to finish (maximum ' + maximumSeconds + ' seconds).');
    } catch (error) {
      stream?.getTracks().forEach(track => track.stop()); state.recording = null; $('#record').classList.remove('recording'); throw error;
    } finally { state.recordPending = false; $('#record').disabled = false; }
  }

  function renderWorkspace(name) {
    closeMenu?.(); state.workspace = name;
    $$('.studio-nav [data-workspace]').forEach(tab => tab.classList.toggle('active', tab.dataset.workspace === name));
    ({ song: renderSong, beats: renderBeats, notes: renderNotes, sampler: renderSampler, instruments: renderInstruments, mix: renderMix }[name] || renderSong)();
  }
  function renderAll() { renderLibrary(); renderPads(); renderWorkspace(state.workspace); }
  function syncControls() {
    const project = projectStore.project;
    $('.project-name').value = project.name; $('#tempo').value = project.bpm;
    $('#swing').value = project.swing; $('#swing-value').textContent = project.swing + '%';
    $('#master-volume').value = project.master * 100; $('#master-value').textContent = Math.round(project.master * 100) + '%';
  }
  function applyTheme() {
    document.documentElement.dataset.theme = state.theme;
    document.documentElement.style.setProperty('--accent', state.accent);
    $('#theme-toggle').textContent = state.theme === 'dark' ? 'Light' : 'Dark';
    $('#accent-picker').value = state.accent;
    drawWaveform($('#inspector-waveform'), padBuffer(), currentSelection());
    if (state.workspace === 'sampler') drawWaveform($('#waveform'), padBuffer(), currentSelection());
  }
  function updatePanels() {
    const mobile = window.matchMedia('(max-width:760px)').matches;
    const browser = state.browserOpen && !state.focused, pads = state.padsOpen && !state.focused;
    $('.main-split').classList.toggle('hide-browser', !browser); $('.main-split').classList.toggle('hide-pads', !pads);
    $('#browser-panel').classList.toggle('open', mobile && browser); $('#pads-panel').classList.toggle('open', mobile && pads);
    $('#focus-toggle').classList.toggle('active', state.focused);
    $$('[data-toggle]').forEach(toggle => toggle.classList.toggle('active', toggle.dataset.toggle === 'browser' ? browser : pads));
  }
  $$('.studio-nav [data-workspace]').forEach(tab => tab.addEventListener('click', act(() => renderWorkspace(tab.dataset.workspace))));
  $$('.pad-bank button').forEach(bank => bank.addEventListener('click', act(() => { state.bank = Number(bank.dataset.bank); state.selectedPad = state.bank * 16; renderPads(); if (state.workspace === 'beats' || state.workspace === 'sampler') renderWorkspace(state.workspace); })));
  $$('[data-toggle],[data-close]').forEach(control => control.addEventListener('click', () => {
    const panel = control.dataset.toggle || control.dataset.close; state.focused = false;
    state[panel === 'browser' ? 'browserOpen' : 'padsOpen'] = control.dataset.close ? false : !state[panel === 'browser' ? 'browserOpen' : 'padsOpen']; updatePanels();
  }));
  on('#focus-toggle', 'click', () => { state.focused = !state.focused; updatePanels(); });
  on('#play', 'click', togglePlayback);
  on('#stop', 'click', () => { if (state.recording?.recorder.state !== 'inactive') state.recording?.recorder.stop(); state.previewSource?.stop(); stopPlayback(); setStatus('Stopped'); });
  on('#record', 'click', toggleRecording);
  on('#recover-recording', 'click', event => {
    if (!state.recoveryRecording) return;
    openMenu(event.currentTarget, [
      { label: 'Download original recording', action: () => {
        const blob = state.recoveryRecording;
        download('microphone-recovery.' + (blob.type.includes('wav') ? 'wav' : blob.type.includes('mp4') ? 'm4a' : 'webm'), blob);
        setStatus('Recovery download started. The take is retained until you explicitly clear it from Recover take.');
      } },
      { label: 'Clear recovered take', action: () => {
        if (!confirm('Clear this recovery take? Save its download first. Clearing the recovery cannot be undone.')) return;
        state.recoveryRecording = null; $('#recover-recording').hidden = true;
        setStatus('Recovery cleared. Microphone recording is available again.');
      } }
    ]);
  });
  on('#playback-mode', 'change', () => { if (state.playing) stopPlayback(); setStatus('Playback and WAV export scope: ' + $('#playback-mode').selectedOptions[0].textContent); });
  on('#tempo', 'change', event => { projectStore.setTempo(Number(event.target.value)); event.target.value = projectStore.project.bpm; });
  on('#swing', 'input', event => { projectStore.transact('change swing', project => project.swing = Number(event.target.value)); $('#swing-value').textContent = event.target.value + '%'; });
  on('#master-volume', 'input', event => { projectStore.transact('master gain', project => project.master = Number(event.target.value) / 100); $('#master-value').textContent = event.target.value + '%'; });
  on('#tap-tempo', 'click', () => { const now = performance.now(); state.taps = (state.taps || []).filter(time => now - time < 2000); state.taps.push(now); if (state.taps.length > 1) { projectStore.setTempo(Math.round(clamp(60000 * (state.taps.length - 1) / (now - state.taps[0]), 40, 240))); $('#tempo').value = projectStore.project.bpm; } });
  on('#metronome', 'click', () => { state.metronome = !state.metronome; state.engine?.setMetronome(state.metronome); $('#metronome').classList.toggle('active', state.metronome); $('#metronome').setAttribute('aria-pressed', String(state.metronome)); });
  on('.project-name', 'change', event => projectStore.transact('project name', project => project.name = event.target.value.trim() || 'Untitled project'));
  on('#save-project', 'click', saveProject); on('#export-project', 'click', exportProject); on('#export-wav', 'click', exportWav);
  on('#export-desktop', 'click', () => { const project = projectDocument(); download(safeFilename(project.name) + '-desktop.json', JSON.stringify(project, null, 2), 'application/json'); setStatus('Desktop project metadata downloaded. Relink audio files and recreate browser master effects in the native app.'); });
  on('#new-project', 'click', () => { if (!canReplace()) return; stopPlayback(); state.generation++; state.selectedMedia = null; state.media = new Map(); state.buffers = new Map(); state.selections.clear(); state.selectedPad = state.bank = 0; projectStore.load(window.AnharmonicProject.defaultProject()); state.dirty = false; syncControls(); renderAll(); setStatus('New empty project. Your previous saved session is retained until you Save.'); });
  on('#undo-project', 'click', () => { if (projectStore.undo()) { syncControls(); renderAll(); } });
  on('#redo-project', 'click', () => { if (projectStore.redo()) { syncControls(); renderAll(); } });
  on('#load-project', 'click', () => $('#project-file').click());
  on('#project-file', 'change', event => { const file = event.target.files[0]; event.target.value = ''; if (file) return loadProject(file); });
  on('#import-audio', 'click', () => $('#audio-file').click()); on('#import-pack', 'click', () => $('#pack-file').click());
  on('#audio-file', 'change', event => { const files = [...event.target.files]; event.target.value = ''; return importAudio(files); });
  on('#pack-file', 'change', event => { const files = [...event.target.files].filter(file => /^audio\//.test(file.type) || /\.(wav|mp3|ogg|flac|m4a|aac|aif|aiff)$/i.test(file.name)); event.target.value = ''; return importAudio(files); });
  on('.search', 'input', renderLibrary); on('#use-sound', 'click', () => assignMedia(state.selectedMedia, state.selectedPad));
  on('#download-sound', 'click', async () => {
    const id = state.selectedMedia, metadata = projectStore.project.media.find(item => item.id === id);
    if (!metadata) throw new Error('Select an imported or recorded sound first.');
    const blob = state.media.get(id) || await dbMedia(id);
    if (!blob) throw new Error('The original audio is missing. Relink this sound first.');
    const extension = blob.type.includes('wav') ? '.wav' : blob.type.includes('mpeg') ? '.mp3' : blob.type.includes('ogg') ? '.ogg' : blob.type.includes('mp4') ? '.m4a' : '.webm';
    const name = /\.[a-z0-9]{2,5}$/i.test(metadata.name) ? metadata.name : metadata.name + extension;
    download(name.replace(/[\\/]/g, '-'), blob); setStatus('Original audio downloaded for backup or desktop relinking.');
  });
  on('#assign-track', 'click', event => openMenu(event.currentTarget, projectStore.project.tracks.map((track, index) => ({ label: track.name + (padRecord().track === index ? ' ✓' : ''), action: () => { projectStore.setPad(state.selectedPad, { track: index }); syncInspector(); } }))));
  on('#pad-options', 'click', event => openMenu(event.currentTarget, [
    { label: 'Relink this missing sound from selected library audio', disabled: !padRecord().sample_id || state.buffers.has(padRecord().sample_id) || !state.selectedMedia, action: async () => {
      const missingId = padRecord().sample_id, selectedId = state.selectedMedia, generation = state.generation;
      await ensureAudio();
      const blob = state.media.get(selectedId), buffer = state.buffers.get(selectedId), metadata = projectStore.project.media.find(item => item.id === selectedId);
      if (!blob || !buffer || !metadata) throw new Error('Import and select a sound in the library first.');
      if (generation !== state.generation) return;
      checkAudioBudget(buffer, state.buffers, missingId);
      state.media.set(missingId, blob); state.buffers.set(missingId, buffer);
      projectStore.addMedia({ ...metadata, id: missingId });
      renderAll();
      try { await dbMedia(missingId, blob); setStatus('All references to the missing sound have been relinked.'); }
      catch { setStatus('Sound relinked. Browser storage failed; download Project + audio to keep the replacement.'); }
    } },
    { label: 'Rename pad', action: () => { const name = prompt('Pad name', padRecord().name); if (name?.trim()) { projectStore.setPad(state.selectedPad, { name: name.trim().slice(0, 200) }); renderAll(); } } },
    { label: 'Clear pad assignment (Undo available)', action: () => { projectStore.setPad(state.selectedPad, { sample_id: '', name: 'Pad ' + padNumber(state.selectedPad), start: 0, end: 0 }); renderAll(); } }
  ]));
  on('#pad-pitch', 'input', event => { projectStore.setPad(state.selectedPad, { pitch: Number(event.target.value) }); syncInspector(); });
  on('#pad-pan', 'input', event => { projectStore.setPad(state.selectedPad, { pan: Number(event.target.value) / 100 }); syncInspector(); });
  on('#pad-gain', 'input', event => { projectStore.setPad(state.selectedPad, { gain: Number(event.target.value) / 100 }); syncInspector(); });
  on('#pad-mode', 'change', event => projectStore.setPad(state.selectedPad, { mode: event.target.value }));
  on('#theme-toggle', 'click', () => { state.theme = state.theme === 'dark' ? 'light' : 'dark'; applyTheme(); localStorage.setItem('anharmonic-theme', state.theme); });
  on('#accent-toggle', 'click', () => $('#accent-picker').click());
  on('#accent-picker', 'input', event => { state.accent = event.target.value; applyTheme(); localStorage.setItem('anharmonic-accent', state.accent); });
  on('#help-toggle', 'click', () => $('#help-dialog').showModal()); on('#capabilities', 'click', () => $('#help-dialog').showModal());
  const typing = target => target instanceof Element && Boolean(target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"]),dialog[open]'));
  window.addEventListener('keydown', act(async event => {
    if (state.loading) return;
    if (typing(event.target) || event.altKey) return;
    const modifier = event.ctrlKey || event.metaKey;
    if (modifier && event.key.toLowerCase() === 's') { event.preventDefault(); await saveProject(); return; }
    if (modifier && event.key.toLowerCase() === 'z') { event.preventDefault(); event.shiftKey ? projectStore.redo() : projectStore.undo(); syncControls(); renderAll(); return; }
    if (modifier || event.repeat) return;
    if (event.code === 'Space') { event.preventDefault(); await togglePlayback(); return; }
    const offset = Number(event.key) - 1;
    if (offset >= 0 && offset < 8 && /^[1-8]$/.test(event.key)) { event.preventDefault(); const index = state.bank * 16 + offset; state.heldPads.set(event.code, index); await triggerPad(index); if (!state.heldPads.has(event.code) && padRecord(index).mode !== 'one-shot') releasePad(index); }
  }));
  window.addEventListener('keyup', event => { const index = state.heldPads.get(event.code); if (index !== undefined) { releasePad(index); state.heldPads.delete(event.code); } });
  window.addEventListener('blur', () => { state.heldPads.forEach(releasePad); state.heldPads.clear(); state.heldSynth.forEach(note => state.engine?.releaseNote(note)); state.heldSynth.clear(); stopArp(); });
  window.addEventListener('beforeunload', event => { if (state.dirty || state.recording || state.recordPending || state.loading || state.recoveryRecording) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('resize', () => { updatePanels(); syncInspector(); if (state.workspace === 'sampler') drawWaveform($('#waveform'), padBuffer(), currentSelection()); });
  setInterval(() => {
    const meter = state.engine?.meter(), peak = Math.min(1, Number(meter?.peak) || 0);
    $$('.track-meter').forEach(element => element.value = Math.min(1, meter?.tracks?.[Number(element.dataset.track)]?.peak || 0));
    $('#master-meter').value = peak; $('.meter-value').textContent = peak > .00001 ? (20 * Math.log10(peak)).toFixed(1) + ' dB' : '−∞ dB';
  }, 80);

  try {
    state.theme = localStorage.getItem('anharmonic-theme') === 'light' ? 'light' : 'dark';
    const accent = localStorage.getItem('anharmonic-accent'); if (/^#[0-9a-f]{6}$/i.test(accent || '')) state.accent = accent;
    const saved = localStorage.getItem(storageKey);
    if (saved) {
      try { projectStore.load(JSON.parse(saved)); state.selectedPad = clamp(projectStore.project.selected_pad, 0, 63); state.bank = Math.floor(state.selectedPad / 16); setStatus('Saved project restored. Audio will load on your first playback or preview.'); }
      catch (error) { state.corruptSaved = saved; setStatus('The saved project could not be opened; its original data is retained. ' + error.message); }
    }
  } catch { setStatus('Browser storage is unavailable. Download Project + audio to keep your work.'); }
  state.dirty = false;
  if (window.matchMedia('(max-width:760px)').matches) state.browserOpen = state.padsOpen = false;
  syncControls(); renderAll(); applyTheme(); updatePanels();
})();
