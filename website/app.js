(() => {
  'use strict';
  const video = document.querySelector('#commercial');
  const play = document.querySelector('#film-play');
  const status = document.querySelector('#video-status');
  play.hidden = false;
  video.controls = false;
  play.addEventListener('click', async () => {
    play.hidden = true;
    video.controls = true;
    try { await video.play(); video.focus(); }
    catch { status.textContent = 'Press play in the video controls, or download the film below.'; }
  });
  video.addEventListener('play', () => { play.hidden = true; video.controls = true; status.textContent = ''; });
  video.addEventListener('ended', () => { play.hidden = false; video.controls = false; });
  video.addEventListener('error', () => { play.hidden = true; video.controls = true; status.textContent = 'The film could not load. Use the download link to watch it.'; });

  const workspaces = {
    sample: ['FIND THE MOMENT', 'A whole new life for any sound.', 'Import audio, find the part that moves you, and turn it into playable slices. Trim by hand, detect transients, or map your cuts across four banks of pads.', 'The actual Anharmonic sampler displaying a drum-break waveform, trim controls, and pad mapping.'],
    beats: ['GIVE IT A PULSE', 'Your rhythm. Step by step.', 'Play the pads or draw hits into the step sequencer. Shape velocities, switch pad banks, and build a groove that feels like you.', 'The Anharmonic Beats workspace with kick, snare, hat, and percussion steps in a drum pattern.'],
    synth: ['SHAPE YOUR SIGNATURE', 'Find a sound. Then go further.', 'Explore playable sounds and the built-in analog synth. Adjust oscillators, filters, envelopes, and the arpeggiator to turn a starting point into your signature.', 'The Anharmonic Instruments workspace showing its sound browser, synthesizer visualizer, filter response, and playable keyboard.'],
    notes: ['LET THE MELODY IN', 'Let a sample sing.', 'Play a sample chromatically or write for the synth. Draw, move, and resize notes in the piano roll, with control over pitch, timing, and velocity.', 'The Anharmonic piano roll for writing and editing chromatic sample and synthesizer notes.'],
    song: ['SEE THE BIG PICTURE', 'A loop is only the beginning.', 'Bring patterns and audio together in the song timeline. Layer sections, move clips, create variations, and build the arrangement around your idea.', 'The Anharmonic Song timeline with layered drums, bass, keys, atmosphere, and melody patterns.'],
    mix: ['MAKE EVERY LAYER COUNT', 'Find the balance. Feel the finish.', 'Bring each part into focus with track levels, EQ, saturation, compression, delay, and reverb. Then bounce the arrangement to a stereo WAV.', 'The Anharmonic mixer with eight track channels, a master output, and an effects rack.']
  };
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  function selectTab(tab, focus = false) {
    const key = tab.dataset.workspace;
    const [kicker, title, copy, alt] = workspaces[key];
    tabs.forEach(t => { t.setAttribute('aria-selected', String(t === tab)); t.tabIndex = t === tab ? 0 : -1; });
    document.querySelector('#workspace-panel').setAttribute('aria-labelledby', tab.id);
    document.querySelector('#workspace-image').src = `assets/${key}.png`;
    document.querySelector('#workspace-image').alt = alt;
    document.querySelector('#workspace-kicker').textContent = kicker;
    document.querySelector('#workspace-title').textContent = title;
    document.querySelector('#workspace-copy').textContent = copy;
    document.querySelector('#workspace-count').textContent = `0${tabs.indexOf(tab) + 1} / 06`;
    if (focus) tab.focus({ preventScroll: true });
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => selectTab(tab));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length - 1;
      if (next !== undefined) { event.preventDefault(); selectTab(tabs[next], true); }
    });
  });

  const bars = document.querySelector('.sound-bars');
  for (let i = 0; i < 48; i++) {
    const bar = document.createElement('i');
    bar.style.setProperty('--bar-height', `${8 + Math.abs(Math.sin(i * 1.72) * Math.cos(i * .23)) * 35}px`);
    bar.style.setProperty('--bar-delay', `${-i * .073}s`);
    bars.append(bar);
  }

  const dialog = document.querySelector('#pack-dialog');
  const close = () => dialog.close();
  dialog.querySelector('.dialog-close').addEventListener('click', close);
  dialog.addEventListener('click', event => {
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) close();
  });
  let checkout;
  try {
    const url = new URL(window.ANHARMONIC_CONFIG?.checkoutUrl);
    if (url.protocol === 'https:' && !url.username && !url.password) checkout = url.href;
  } catch { /* No checkout configured: preserve the honest coming-soon state. */ }
  document.querySelectorAll('.pack-link').forEach(link => {
    if (checkout) {
      link.href = checkout; link.target = '_blank'; link.rel = 'noopener noreferrer';
      link.firstChild.textContent = 'Get the EXE pack ';
    } else {
      link.addEventListener('click', event => {
        if (typeof dialog.showModal === 'function') { event.preventDefault(); dialog.showModal(); }
      });
    }
  });
  if (checkout) {
    document.querySelector('[data-availability]').textContent = 'AVAILABLE';
    document.querySelector('#pack-status').textContent = 'The official Windows EXE pack is available. Follow the pack link for release details.';
    document.querySelector('#availability-answer').textContent = 'Yes. Use the EXE pack link above to see the available pack and release details.';
  }
})();
