// Studio song operations. Dependencies are supplied by the application.
window.AnharmonicStudioParts = window.AnharmonicStudioParts || {};
window.AnharmonicStudioParts.song = context => {
  const {
    projectStore,
    shell,
    button,
    stage,
    setStatus,
    openMenu,
    state,
    pads,
    ensureAudio,
    playPad,
    $
  } = context;
  function renderSong() {
    const rows = projectStore.project.rows;
    shell(
        'Song',
        `${button('+ ADD TRACK', 'add-track')} ${button('SELECT ▾', 'song-tool')} ${
            button('DRAW')} ${button('SNAP: BAR ▾', 'song-snap')}`,
        `<div class="timeline"><div class="ruler"><span>BAR / BEAT</span>${
            Array.from({length: 16}, (_, i) => `<span>${String(i + 1).padStart(2, '0')}</span>`)
                .join('')}</div>${
            rows.map(
                    (row, rowIndex) => `<div class="track-row" data-row="${
                        row.id}"><div class="track-head"><strong>${row.name}</strong><small>${
                        row.clips.length} clip${
                        row.clips.length === 1 ? '' : 's'}</small><span><button class="row-record ${
                        row.record_armed ?
                            'active' :
                            ''}" data-row-index="${rowIndex}">R</button> <button class="row-mute ${
                        row.mute ?
                            'active' :
                            ''}" data-row-index="${rowIndex}">M</button> <button class="row-solo ${
                        row.solo ? 'active' : ''}" data-row-index="${
                        rowIndex}">S</button></span></div><div class="track-lane">${
                        row.clips
                            .map(
                                clip =>
                                    `<button class="clip ${clip.kind === 'audio' ? 'audio' : ''} ${
                                        clip.mute ? 'muted' :
                                                    ''}" data-clip="${clip.id}" data-row-index="${
                                        rowIndex}" style="left:${clip.start_beat * 90}px;width:${
                                        Math.max(45, clip.length_beats * 90)}px">${
                                        clip.kind === 'audio' ?
                                            'Keys idea' :
                                            'Pattern 01 · drums'}<i></i></button>`)
                            .join('')}</div></div>`)
                .join(
                    '')}<div class="empty-timeline">Drag clips to move. Drag the right edge to resize. Right-click a clip to delete.</div></div>`);
    stage.querySelector('.add-track').addEventListener('click', () => {
      projectStore.transact('add arrangement track', document => {
        document.rows.push({
          id: `row-${Date.now()}`,
          name: `Track ${document.rows.length + 1}`,
          mute: false,
          solo: false,
          clips: [],
          record_source: 'audio',
          record_track: Math.min(7, document.rows.length)
        });
      });
      renderSong();
      setStatus('track added');
    });
    stage.querySelector('.song-tool')
        .addEventListener('click', event => openMenu(event.currentTarget, [
                                     'select', 'draw', 'paint', 'slice', 'mute', 'erase'
                                   ].map(tool => ({
                                           label: tool.toUpperCase(),
                                           action: () => {
                                             state.arrangeTool = tool;
                                             event.currentTarget.textContent =
                                                 `${tool.toUpperCase()} ▾`;
                                             setStatus(`arrangement tool: ${tool}`);
                                           }
                                         }))));
    stage.querySelector('.song-snap')
        .addEventListener('click', event => openMenu(event.currentTarget, [
                                     'BAR', 'BEAT', '1/16', 'OFF'
                                   ].map(snap => ({
                                           label: snap,
                                           action: () => {
                                             state.arrangeSnap = snap;
                                             event.currentTarget.textContent = `SNAP: ${snap} ▾`;
                                             setStatus(`arrangement snap: ${snap}`);
                                           }
                                         }))));
    stage.querySelectorAll('.row-mute')
        .forEach(button => button.addEventListener('click', event => {
          event.stopPropagation();
          const row = projectStore.project.rows[Number(button.dataset.rowIndex)];
          projectStore.transact('mute arrangement row', document => {
            document.rows[Number(button.dataset.rowIndex)].mute = !row.mute;
          });
          renderSong();
        }));
    stage.querySelectorAll('.row-solo')
        .forEach(button => button.addEventListener('click', event => {
          event.stopPropagation();
          projectStore.transact('solo arrangement row', document => {
            document.rows[Number(button.dataset.rowIndex)].solo =
                !document.rows[Number(button.dataset.rowIndex)].solo;
          });
          renderSong();
        }));
    stage.querySelectorAll('.row-record')
        .forEach(button => button.addEventListener('click', event => {
          event.stopPropagation();
          const rowIndex = Number(button.dataset.rowIndex);
          projectStore.transact('arm arrangement row', document => {
            document.rows.forEach((row, index) => {
              row.record_armed = index === rowIndex ? !row.record_armed : false;
            });
          });
          renderSong();
          setStatus(
              projectStore.project.rows[rowIndex].record_armed ?
                  `${rows[rowIndex].name} armed for recording` :
                  'record arm cleared');
        }));
    stage.querySelectorAll('.clip').forEach(clipElement => {
      let drag = null;
      clipElement.addEventListener('pointerdown', event => {
        event.preventDefault();
        event.stopPropagation();
        const rowIndex = Number(clipElement.dataset.rowIndex);
        const clip = projectStore.project.rows[rowIndex].clips.find(
            item => item.id === clipElement.dataset.clip);
        drag = {
          rowIndex,
          clip,
          x: event.clientX,
          start: clip.start_beat,
          length: clip.length_beats,
          resize: event.offsetX > clipElement.offsetWidth - 12
        };
        clipElement.setPointerCapture?.(event.pointerId);
      });
      clipElement.addEventListener('pointermove', event => {
        if (!drag) return;
        const delta = Math.round(((event.clientX - drag.x) / 90) * 4) / 4;
        if (drag.resize) {
          clipElement.style.width = `${Math.max(45, (drag.length + delta) * 90)}px`;
        } else {
          clipElement.style.transform = `translateX(${delta * 90}px)`;
        }
      });
      clipElement.addEventListener('pointerup', event => {
        if (!drag) return;
        const delta = Math.round(((event.clientX - drag.x) / 90) * 4) / 4;
        const {rowIndex, clip} = drag;
        projectStore.transact(
            drag.resize ? 'resize arrangement clip' : 'move arrangement clip', document => {
              const target = document.rows[rowIndex].clips.find(item => item.id === clip.id);
              if (!target) return;
              if (drag.resize)
                target.length_beats = Math.max(.25, drag.length + delta);
              else
                target.start_beat = Math.max(0, drag.start + delta);
            });
        drag = null;
        renderSong();
        setStatus('arrangement edited');
      });
      clipElement.addEventListener('contextmenu', event => {
        event.preventDefault();
        const rowIndex = Number(clipElement.dataset.rowIndex);
        projectStore.transact('delete arrangement clip', document => {
          document.rows[rowIndex].clips =
              document.rows[rowIndex].clips.filter(item => item.id !== clipElement.dataset.clip);
        });
        renderSong();
        setStatus('clip deleted');
      });
    });
  }

  function renderBeats() {
    const pattern = projectStore.pattern;
    const totalSteps = Math.max(1, pattern.bars * 4 * pattern.div);
    const rows =
        Array
            .from(
                {length: 16},
                (_, index) => {
                  const padSteps = pattern.steps[index] || {};
                  return `<div class="step-line"><label><b>${
                      String(index + 1).padStart(2, '0')}</b> ${pads[index]}</label>${
                      Array
                          .from(
                              {length: totalSteps},
                              (_, step) => {
                                const velocity = padSteps[step] || 0;
                                return `<button class="${velocity ? 'on ' : ''}${
                                    state.step === step ? 'current' : ''}" style="--velocity:${
                                    velocity}" data-pad="${index}" data-step="${
                                    step}" aria-label="${pads[index]} step ${step + 1}"></button>`;
                              })
                          .join('')}</div>`;
                })
            .join('');
    shell(
        'Steps',
        `${button('PATTERN 01 ▾', 'pattern-menu')} ${
            button('−', 'bars-down')} <span class="bars-readout">${pattern.bars} BARS · ${
            pattern.div === 4 ? '1/16' : `1/${pattern.div * 4}`}</span> ${button('+', 'bars-up')} ${
            button('LOADED PADS', 'loaded-toggle')} ${button('FOLLOW PLAYHEAD', 'follow-toggle')} ${
            button('CLEAR', 'clear-beats')}`,
        `<div class="step-editor"><div class="step-grid">${rows}</div></div>`);
    const grid = stage.querySelector('.step-grid');
    let gesture = null;
    const cellAt = event => event.target.closest('.step-line button');
    const paint = cell => {
      if (!cell || !gesture) return;
      const pad = Number(cell.dataset.pad);
      const step = Number(cell.dataset.step);
      const key = `${pad}:${step}`;
      if (gesture.seen.has(key)) return;
      gesture.seen.add(key);
      projectStore.setStep(pad, step, gesture.erase ? null : gesture.velocity);
      if (!gesture.erase)
        ensureAudio().then(() => playPad(pad)).catch(error => setStatus(error.message));
      const velocity = gesture.erase ? 0 : gesture.velocity;
      cell.style.setProperty('--velocity', velocity);
      cell.classList.toggle('on', velocity > 0);
    };
    grid.addEventListener('pointerdown', event => {
      const cell = cellAt(event);
      if (!cell) return;
      event.preventDefault();
      grid.setPointerCapture?.(event.pointerId);
      const existing = Number(cell.style.getPropertyValue('--velocity')) || 0;
      gesture = {
        erase: event.button === 2 || existing > 0,
        velocity: existing > 0 ? existing : 1,
        seen: new Set()
      };
      paint(cell);
    });
    grid.addEventListener('pointermove', event => {
      if (gesture) paint(cellAt(event));
    });
    grid.addEventListener('pointerup', () => {
      gesture = null;
    });
    grid.addEventListener('pointercancel', () => {
      gesture = null;
    });
    grid.addEventListener('contextmenu', event => event.preventDefault());
    grid.addEventListener('wheel', event => {
      const cell = cellAt(event);
      if (!cell || !(Number(cell.style.getPropertyValue('--velocity')) > 0)) return;
      event.preventDefault();
      const next = Math.max(
          .1,
          Math.min(
              1,
              (Number(cell.style.getPropertyValue('--velocity')) || 1) +
                  (event.deltaY < 0 ? .08 : -.08)));
      projectStore.setStep(Number(cell.dataset.pad), Number(cell.dataset.step), next);
      renderBeats();
      setStatus(`velocity ${Math.round(next * 100)}%`);
    }, {passive: false});
    $('.clear-beats').addEventListener('click', () => {
      projectStore.transact('clear pattern', document => {
        document.patterns[document.selected_pattern].steps = {};
      });
      renderBeats();
      setStatus('pattern cleared');
    });
    $('.bars-down').addEventListener('click', () => {
      projectStore.transact('shorten pattern', document => {
        document.patterns[document.selected_pattern].bars =
            Math.max(1, document.patterns[document.selected_pattern].bars - 1);
      });
      renderBeats();
      setStatus('pattern shortened');
    });
    $('.bars-up').addEventListener('click', () => {
      projectStore.transact('lengthen pattern', document => {
        document.patterns[document.selected_pattern].bars += 1;
      });
      renderBeats();
      setStatus('pattern lengthened');
    });
    $('.pattern-menu')
        .addEventListener(
            'click',
            event => openMenu(
                event.currentTarget,
                projectStore.project.patterns.map((pattern, index) => ({
                                                    label: pattern.name,
                                                    action: () => {
                                                      projectStore.transact(
                                                          'select pattern', document => {
                                                            document.selected_pattern = index;
                                                          });
                                                      renderBeats();
                                                      setStatus(`${pattern.name} selected`);
                                                    }
                                                  }))));
    $('.loaded-toggle').addEventListener('click', event => {
      event.currentTarget.classList.toggle('active');
      setStatus(
          event.currentTarget.classList.contains('active') ? 'showing loaded pad lanes' :
                                                             'showing all pad lanes');
    });
    $('.follow-toggle').addEventListener('click', event => {
      event.currentTarget.classList.toggle('active');
      setStatus(
          event.currentTarget.classList.contains('active') ? 'playhead follow on' :
                                                             'playhead follow off');
    });
  }
  return {renderSong, renderBeats};
};
