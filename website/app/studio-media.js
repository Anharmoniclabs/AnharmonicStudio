// Studio media operations. Dependencies are supplied by the application.
window.AnharmonicStudioParts = window.AnharmonicStudioParts || {};
window.AnharmonicStudioParts.media = context => {
  const {state, mediaDb, setStatus, ensureAudio, projectStore, pads, renderPads, renderSampler, $} =
      context;
  async function storeMedia(mediaId, blob) {
    state.media.set(mediaId, blob);
    const database = await mediaDb;
    if (!database) return;
    await new Promise((resolve, reject) => {
      const request =
          database.transaction('audio', 'readwrite').objectStore('audio').put(blob, mediaId);
      request.onsuccess = resolve;
      request.onerror = () => reject(request.error);
    });
  }

  async function restoreMedia() {
    if (state.mediaRestored || !state.audio) return;
    state.mediaRestored = true;
    const database = await mediaDb;
    if (!database) return;
    const keys = await new Promise((resolve, reject) => {
      const request = database.transaction('audio').objectStore('audio').getAllKeys();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    for (const key of keys) {
      const blob = await new Promise((resolve, reject) => {
        const request = database.transaction('audio').objectStore('audio').get(key);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      if (!blob) continue;
      try {
        state.media.set(String(key), blob);
        state.buffers.set(
            String(key), await state.audio.context.decodeAudioData(await blob.arrayBuffer()));
      } catch {
        setStatus('some stored media could not be decoded');
      }
    }
  }

  function loadAudio(file) {
    ensureAudio()
        .then(() => file.arrayBuffer())
        .then(data => state.audio.context.decodeAudioData(data))
        .then(buffer => {
          const mediaId = projectStore.addMedia({
            name: file.name,
            mime: file.type,
            duration: buffer.duration,
            sample_rate: buffer.sampleRate,
            size: file.size
          });
          state.buffers.set(mediaId, buffer);
          return storeMedia(mediaId, file).then(() => ({buffer, mediaId}));
        })
        .then(({buffer, mediaId}) => {
          state.selections.set(mediaId, {start: 0, end: buffer.duration});
          projectStore.assignPad(state.selectedPad, mediaId, {
            name: file.name.replace(/\.[^.]+$/, '').slice(0, 20),
            start: 0,
            end: buffer.duration
          });
          pads[state.selectedPad] = file.name.replace(/\.[^.]+$/, '').slice(0, 20);
          renderPads();
          if (state.workspace === 'sampler') renderSampler();
          setStatus(
              `${file.name} loaded into pad ${String(state.selectedPad + 1).padStart(2, '0')}`);
        })
        .catch(error => setStatus(`audio load failed: ${error.message}`));
  }

  async function toggleRecording() {
    if (state.recording) {
      state.recording.stop();
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setStatus('microphone recording is unavailable in this browser');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({audio: true});
      const recorder = new MediaRecorder(stream);
      const chunks = [];
      const armedRow = projectStore.project.rows.find(row => row.record_armed);
      state.recording = recorder;
      $('#record').classList.add('recording');
      setStatus(
          armedRow ? `recording microphone into ${armedRow.name}` :
                     'recording microphone into selected pad');
      recorder.addEventListener('dataavailable', event => {
        if (event.data.size) chunks.push(event.data);
      });
      recorder.addEventListener('stop', async () => {
        stream.getTracks().forEach(track => track.stop());
        state.recording = null;
        $('#record').classList.remove('recording');
        const blob = new Blob(chunks, {type: recorder.mimeType || 'audio/webm'});
        const buffer = await state.audio.context.decodeAudioData(await blob.arrayBuffer());
        const mediaId = projectStore.addMedia({
          name: 'Microphone recording',
          mime: blob.type,
          duration: buffer.duration,
          sample_rate: buffer.sampleRate,
          size: blob.size
        });
        state.buffers.set(mediaId, buffer);
        await storeMedia(mediaId, blob);
        state.selections.set(mediaId, {start: 0, end: buffer.duration});
        projectStore.assignPad(
            state.selectedPad, mediaId,
            {name: `Recording ${new Date().toLocaleTimeString()}`, start: 0, end: buffer.duration});
        pads[state.selectedPad] = `Recording ${new Date().toLocaleTimeString()}`;
        renderPads();
        if (state.workspace === 'sampler') renderSampler();
        setStatus('recording captured');
      });
      recorder.start();
    } catch (error) {
      setStatus(`recording failed: ${error.message}`);
    }
  }
  return {storeMedia, restoreMedia, loadAudio, toggleRecording};
};
