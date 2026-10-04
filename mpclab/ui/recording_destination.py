"""Read-only recording destination feedback, using the transport's routing rules."""


def recording_destination(window):
    capture = window.track_capture
    project = window.project
    instrument = next(
        (
            item.name
            for item in getattr(project, "instruments", ())
            if item.id == getattr(project, "selected_instrument", None)
        ),
        "Notes",
    )
    pattern_workspace = window.studio.selected in {
        window.TAB_SEQ,
        window.TAB_PIANO,
        window.TAB_SYNTH,
    }
    if window.engine.recording or (
        window._record_count_deadline is not None and not (capture.active or capture.pending)
    ):
        pattern_workspace = True
    # An in-progress take retains its original destination across navigation.
    row = capture.target if capture.active or capture.pending else None
    if row is None and not pattern_workspace and not window.engine.recording:
        row = next((r for r in project.rows if r.id == capture.armed_id), None)
    song = not pattern_workspace and (
        window.studio.selected == window.TAB_PLAYLIST or window.engine.mode == "song"
    )
    if row is None and song and not window.engine.recording:
        selected = window.track_inspector.row()
        if selected is not None and selected.record_source in ("notes", "sampler"):
            row = selected
    if row is not None:
        source = {"audio": "Audio input", "sampler": "Sample pads", "notes": instrument}.get(
            row.record_source, row.record_source
        )
        destination = f"{row.name} · {source}"
        beats = capture.row_monitor(row.id)["count_in_bars"] * 4
        immediate = (
            window.engine.playing
            and window.engine.mode == "song"
            and row.record_source
            in (
                "notes",
                "sampler",
            )
            and not getattr(capture, "_advanced_recording", None)
        )
    elif song and not window.engine.recording:
        destination = "New Song notes track"
        beats, immediate = 4, window.engine.playing and window.engine.mode == "song"
    else:
        voice = "Pads & notes" if instrument == "Notes" else f"Pads + {instrument}"
        destination = f"{project.pattern().name} · {voice}"
        beats, immediate = 4, window.engine.playing
    if capture.active or window.engine.recording:
        state = "Recording"
    else:
        state = "Record"
    if window._record_count_deadline is not None:
        timing = "Counting in"
    elif state == "Recording":
        timing = "Take in progress"
    elif immediate:
        timing = "Join playback"
    else:
        timing = f"{beats}-beat count-in" if beats else "No count-in"
    return f"{state} → {destination}", timing


def refresh_recording_destination(window):
    label = getattr(window, "record_destination_label", None)
    if label is None:
        return
    destination, timing = recording_destination(window)
    text = f"{destination} · {timing}"
    if label.toolTip() == text:
        return
    from PySide6.QtCore import Qt

    window.btn_rec.setToolTip(f"{text}\nRecord ends the take; Stop ends playback. (R)")
    label.setToolTip(text)
    label.setAccessibleName(text)
    label.setText(label.fontMetrics().elidedText(text, Qt.ElideRight, label.width()))
