// Studio sampler operations. Dependencies are supplied by the application.
window.AnharmonicStudioParts = window.AnharmonicStudioParts || {};
window.AnharmonicStudioParts.sampler = context => {
  const {
    padBuffer,
    state,
    padMediaId,
    shell,
    button,
    stage,
    $,
    previewSelection,
    setStatus,
    projectStore,
    openMenu,
    pads
  } = context;
  function renderSampler() {
    const buffer = padBuffer(state.selectedPad);
    const duration = buffer?.duration || 2.84;
    const selection =
        state.selections.get(padMediaId(state.selectedPad)) || {start: 0, end: duration};
    shell(
        'Sampler',
        `${button('IMPORT', 'import-sampler')} ${button('PREVIEW', 'preview-pad')} ${
            button('CHOP TOOLS ▾', 'chop-tools')} ${button('CUT SAMPLE', 'cut-sample')}`,
        `<div class="sampler-editor"><div class="sampler-canvas"><canvas id="waveform" aria-label="Audio waveform"></canvas></div><div class="sampler-actions"><label>START <input id="sample-start" type="number" min="0" max="${
            duration.toFixed(3)}" step="0.001" value="${
            selection.start.toFixed(
                3)}"> s</label><label>END <input id="sample-end" type="number" min="0" max="${
            duration.toFixed(3)}" step="0.001" value="${
            selection.end.toFixed(
                3)}"> s</label><button class="sample-snap">SNAP: BEAT ▾</button> ${
            button(
                'ASSIGN TO PAD',
                'assign-sample')}</div><p class="sampler-help">Drag the start and end markers in the waveform, or edit the time fields. Selection plays on the selected pad.</p></div>`);
    drawWaveform(buffer, selection);
    stage.querySelector('.import-sampler')
        .addEventListener('click', () => $('#audio-file').click());
    stage.querySelector('.preview-pad').addEventListener('click', () => previewSelection());
    stage.querySelector('#sample-start')
        .addEventListener('change', event => updateSelection('start', event.target.value));
    stage.querySelector('#sample-end')
        .addEventListener('change', event => updateSelection('end', event.target.value));
    stage.querySelector('.assign-sample').addEventListener('click', () => {
      const mediaId = padMediaId(state.selectedPad);
      if (!mediaId) return setStatus('load audio before assigning a sample');
      projectStore.assignPad(
          state.selectedPad, mediaId, {start: selection.start, end: selection.end});
      setStatus(`selection assigned to pad ${String(state.selectedPad + 1).padStart(2, '0')}`);
    });
    stage.querySelector('.sample-snap')
        .addEventListener('click', event => openMenu(event.currentTarget, [
                                     'OFF', 'ZERO CROSSING', 'BEAT', '1/8', '1/16'
                                   ].map(snap => ({
                                           label: snap,
                                           action: () => {
                                             state.sampleSnap = snap;
                                             event.currentTarget.textContent = `SNAP: ${snap} ▾`;
                                             setStatus(`sample snap: ${snap}`);
                                           }
                                         }))));
    stage.querySelector('.chop-tools')
        .addEventListener(
            'click', event => openMenu(event.currentTarget, [
                       {
                         label: 'Detect transients',
                         action: () => setStatus('transient detection queued for this sample')
                       },
                       {
                         label: 'Map slices to pads',
                         action: () => setStatus('select a detected slice, then assign it to a pad')
                       },
                       {
                         label: 'Detect tempo',
                         action: () => setStatus('tempo detection queued for this sample')
                       }
                     ]));
    stage.querySelector('.cut-sample').addEventListener('click', () => {
      const buffer = padBuffer(state.selectedPad);
      if (!buffer) return setStatus('load audio before cutting a sample');
      const clip = projectStore.project.pads[state.selectedPad];
      projectStore.assignPad(
          state.selectedPad, padMediaId(state.selectedPad),
          {start: selection.start, end: selection.end, name: clip.name});
      setStatus(`sample range cut: ${selection.start.toFixed(2)}–${selection.end.toFixed(2)}s`);
    });
  }

  function drawWaveform(buffer, selection) {
    const canvas = $('#waveform');
    if (!canvas) return;
    const bounds = canvas.getBoundingClientRect();
    canvas.width = Math.max(400, Math.floor(bounds.width * window.devicePixelRatio));
    canvas.height = Math.max(160, Math.floor(bounds.height * window.devicePixelRatio));
    const context = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;
    const data = buffer?.getChannelData(0);
    context.clearRect(0, 0, width, height);
    context.fillStyle =
        getComputedStyle(document.documentElement).getPropertyValue('--wave').trim() || '#d6ab65';
    context.globalAlpha = .85;
    context.beginPath();
    if (data) {
      const stride = Math.max(1, Math.floor(data.length / width));
      for (let x = 0; x < width; x += 1) {
        let peak = 0;
        for (let i = x * stride; i < Math.min(data.length, (x + 1) * stride); i += 1)
          peak = Math.max(peak, Math.abs(data[i]));
        context.moveTo(x, height / 2 - peak * height * .45);
        context.lineTo(x, height / 2 + peak * height * .45);
      }
    } else {
      for (let x = 0; x < width; x += 4) {
        const peak = (Math.abs(Math.sin(x * .07)) * .35 + .1) * height;
        context.moveTo(x, height / 2 - peak);
        context.lineTo(x, height / 2 + peak);
      }
    }
    context.strokeStyle =
        getComputedStyle(document.documentElement).getPropertyValue('--wave').trim() || '#d6ab65';
    context.stroke();
    context.globalAlpha = 1;
    const startX = width * selection.start / (buffer?.duration || 2.84);
    const endX = width * selection.end / (buffer?.duration || 2.84);
    context.fillStyle =
        getComputedStyle(document.documentElement).getPropertyValue('--selection').trim() ||
        '#d6ab6533';
    context.fillRect(startX, 0, endX - startX, height);
    context.strokeStyle =
        getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
    context.lineWidth = Math.max(2, window.devicePixelRatio);
    context.beginPath();
    context.moveTo(startX, 0);
    context.lineTo(startX, height);
    context.moveTo(endX, 0);
    context.lineTo(endX, height);
    context.stroke();
  }

  function updateSelection(edge, value) {
    const buffer = padBuffer(state.selectedPad);
    const duration = buffer?.duration || 2.84;
    const current =
        state.selections.get(padMediaId(state.selectedPad)) || {start: 0, end: duration};
    current[edge] = Math.max(0, Math.min(duration, Number(value) || 0));
    if (current.end <= current.start) current.end = Math.min(duration, current.start + .01);
    state.selections.set(padMediaId(state.selectedPad), current);
    renderSampler();
  }
  return {renderSampler, drawWaveform, updateSelection};
};
