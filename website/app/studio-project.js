// Studio project operations. Dependencies are supplied by the application.
window.AnharmonicStudioParts = window.AnharmonicStudioParts || {};
window.AnharmonicStudioParts.project = context => {
  const {
    projectStore,
    $,
    state,
    storageKey,
    setStatus,
    renderOfflineHit,
    renderPads,
    renderWorkspace
  } = context;
  function projectDocument() {
    const project = projectStore.toJSON();
    project.name = $('.project-name').value.trim() || 'Untitled project';
    project.selected_pad = state.selectedPad;
    return project;
  }

  function download(name, data, type) {
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([data], {type}));
    link.download = name;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  function saveProject() {
    const project = projectDocument();
    localStorage.setItem(storageKey, JSON.stringify(project));
    setStatus('project saved locally');
  }

  function exportProject() {
    const project = projectDocument();
    download(
        `${project.name.replace(/[^a-z0-9_-]+/gi, '-')}.json`, JSON.stringify(project, null, 2),
        'application/json');
    setStatus('project exported');
  }

  async function exportWav() {
    const OfflineContext = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!OfflineContext) {
      setStatus('offline audio export is unavailable');
      return;
    }
    const secondsPerStep = 60 / state.tempo / 4;
    const context = new OfflineContext(2, Math.ceil(44100 * (secondsPerStep * 16 + 0.5)), 44100);
    const master = context.createGain();
    master.gain.value = 0.7;
    master.connect(context.destination);
    state.steps.forEach(
        (hits, pad) =>
            hits.forEach(step => renderOfflineHit(context, master, pad, step * secondsPerStep)));
    const buffer = await context.startRendering();
    download(
        `${
            ($('.project-name').value.trim() || 'anharmonic-pattern')
                .replace(/[^a-z0-9_-]+/gi, '-')}.wav`,
        encodeWav(buffer), 'audio/wav');
    setStatus('WAV exported');
  }

  function encodeWav(buffer) {
    const channels = buffer.numberOfChannels;
    const length = 44 + buffer.length * channels * 2;
    const view = new DataView(new ArrayBuffer(length));
    const write = (offset, text) => [...text].forEach(
        (char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
    write(0, 'RIFF');
    view.setUint32(4, length - 8, true);
    write(8, 'WAVE');
    write(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, channels, true);
    view.setUint32(24, buffer.sampleRate, true);
    view.setUint32(28, buffer.sampleRate * channels * 2, true);
    view.setUint16(32, channels * 2, true);
    view.setUint16(34, 16, true);
    write(36, 'data');
    view.setUint32(40, length - 44, true);
    let offset = 44;
    for (let frame = 0; frame < buffer.length; frame += 1)
      for (let channel = 0; channel < channels; channel += 1) {
        const sample = Math.max(-1, Math.min(1, buffer.getChannelData(channel)[frame]));
        view.setInt16(offset, sample * 0x7fff, true);
        offset += 2;
      }
    return view;
  }

  function loadProject(file) {
    file.text()
        .then(text => {
          const project = JSON.parse(text);
          projectStore.load(project);
          state.selectedPad = Number(project.selected_pad) || 0;
          $('.project-name').value = project.name || 'Untitled project';
          $('#tempo').value = projectStore.project.bpm;
          renderPads();
          renderWorkspace(state.workspace);
          setStatus('project loaded');
        })
        .catch(error => setStatus(`load failed: ${error.message}`));
  }
  return {projectDocument, download, saveProject, exportProject, exportWav, encodeWav, loadProject};
};
