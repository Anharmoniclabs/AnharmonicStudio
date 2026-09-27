#!/usr/bin/env python
"""Observe real controller input in an isolated desktop session. Never inject MIDI."""

import argparse
from collections import deque
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mpclab.production_runtime import configure

configure()
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication
from mpclab.application_features import install_application_runtime, attach_application_features
from mpclab.model import Project
from mpclab.ui import main_window
from audit_creative_workflow import AuditSettings


class HardwareAudit:
    def __init__(self, app, root, template, *, resume=False, controllers=None, history=None):
        self.app, self.root = app, root
        self.started = time.monotonic()
        self.incoming = deque()
        self.events, self.metrics = [], []
        self.command_id = None
        self.phase = "calibration"
        self.last_write = 0
        self.last_recording = False
        main_window.QSettings = AuditSettings
        AuditSettings.buffer_frames = 256
        self.window = w = main_window.MainWindow(root / "session", restore_session=False)
        attach_application_features(w)
        project = Project.load(template)
        if not resume:
            project.name = "MPK hardware recording test"
            project.bpm = 100
            project.pattern().steps.clear()
            project.pattern().notes.clear()
            project.pattern().bars = 16
        w._apply_project(project)
        for port, settings in (controllers or {}).items():
            w.devices.router.settings[port] = settings
        w.piano_roll.select_channel(
            project.selected_instrument if resume else project.instruments[0].id
        )
        w.set_mode("pattern")
        w.show_tab(w.TAB_SEQ)
        w.setWindowTitle("Anharmonic Studio — MPK hardware recording test")
        w.setAttribute(Qt.WA_ShowWithoutActivating)
        w.resize(1600, 950)
        w.show()
        w._autosave_timer.stop()
        if history is not None:
            w._load_history(history)
            w._try_save_history()
        original = w.engine.midi.apply_event

        def observe(event, offset=0):
            start = time.monotonic()
            original(event, offset)
            port, message, stamp = event
            self.incoming.append(
                dict(
                    t=start - self.started,
                    port=port,
                    message=list(message),
                    received=stamp,
                    processing=start,
                    input_queue_ms=max(0, (start - stamp) * 1000),
                    handler_ms=(time.monotonic() - start) * 1000,
                    beat=w.engine.midi.event_beat,
                    playing=w.engine.playing,
                    recording=w.engine.recording,
                    phase=self.phase,
                )
            )

        w.engine.midi.apply_event = observe
        self.timer = QTimer(w)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        self.note("ready", instruction="Real hardware only; no injected messages")

    def note(self, action, **fields):
        row = dict(t=time.monotonic() - self.started, action=action, **fields)
        self.events.append(row)
        print(json.dumps(row), flush=True)

    def snapshot(self, name):
        w = self.window
        w.project.save(self.root / f"{name}.json")
        if not w.engine.playing:
            w.grab().save(str(self.root / f"{name}.png"))
        self.note(
            "saved",
            name=name,
            steps=w.project.pattern().steps,
            notes=[
                dict(
                    pitch=n.pitch,
                    start=n.start,
                    duration=n.duration,
                    instrument=n.instrument,
                    pad=n.pad,
                    channel=n.channel,
                )
                for n in w.project.pattern().notes
            ],
        )

    def command(self, command):
        w = self.window
        operation = command["operation"]
        if operation == "prepare":
            w.stop_all()
            self.phase = command["phase"]
            w.project.pattern().steps.clear()
            w.project.pattern().notes.clear()
            target = command.get("instrument", 0)
            if target == "sample":
                clip = next(
                    c
                    for c in w.library.clips.values()
                    if "Destiny - 90 BPM G Maj Guitar Chords" in c.name
                )
                w.sample_workflow.send(clip.id, index=3, destination="notes")
                w.project.pads[3].mode = "gate"
                w.project.pads[3].mono = False
                w.piano_roll.select_channel(3)
            else:
                w.piano_roll.select_channel(w.project.instruments[target].id)
            w.show_tab(w.TAB_PIANO if command.get("notes", False) else w.TAB_SEQ)
            w.step_grid.refresh()
            w.piano_roll.canvas.refresh()
            for port, settings in command.get("controllers", {}).items():
                w.devices.router.settings[port] = settings
            self.note("prepared", phase=self.phase, target=target)
        elif operation == "record":
            w.btn_rec.setChecked(True)
            self.note("count-in-started", deadline=w._record_count_deadline)
        elif operation == "stop":
            self.note("take-timing", phase=self.phase, timing=w.engine.timing_stats())
            w.stop_all()
            QTimer.singleShot(250, lambda: self.snapshot(self.phase))
        elif operation == "play":
            w.engine.play(0)
        elif operation == "workspace":
            w.studio._workspace_clicked(command["index"])
        elif operation == "insert-instrument":
            from mpclab.ui.instruments import insert_pattern_instrument

            owner = insert_pattern_instrument(
                w, command["kind"], layer_notes=command.get("layer_notes", False)
            )
            w.show_tab(w.TAB_SYNTH)
            self.note("inserted", instrument=owner, kind=command["kind"])
        elif operation == "history-check":
            saved_undo, saved_redo, dirty = list(w._undo), list(w._redo), w._dirty
            name = w.project.name
            hosts = {key: id(value) for key, value in w.engine.external.plugin_map().items()}
            timings = []
            try:
                w.snapshot()
                w.project.name = "Undo responsiveness check"
                for action in (w.undo, w.redo, w.undo):
                    started = time.perf_counter()
                    action()
                    timings.append((time.perf_counter() - started) * 1000)
                self.note(
                    "history-check",
                    milliseconds=timings,
                    hosts_preserved=hosts
                    == {key: id(value) for key, value in w.engine.external.plugin_map().items()},
                    restored=w.project.name == name,
                )
            finally:
                w.project.name = name
                w.proj_name.setText(name)
                w._undo, w._redo = saved_undo, saved_redo
                w._set_dirty(dirty)
                w._try_save_history()
        elif operation == "snapshot":
            self.snapshot(self.phase)
        self.note("command", operation=operation, id=command["id"])

    def tick(self):
        w = self.window
        while self.incoming:
            self.events.append(self.incoming.popleft())
        command_file = self.root / "control.json"
        if command_file.exists():
            try:
                command = json.loads(command_file.read_text())
                if command.get("id") != self.command_id:
                    self.command_id = command["id"]
                    self.command(command)
            except Exception as error:
                self.note("command-error", error=str(error))
        if w.engine.recording and not self.last_recording:
            w.engine.reset_timing()
            self.note("recording-started", phase=self.phase)
        self.last_recording = w.engine.recording
        now = time.monotonic()
        if now - self.last_write >= 0.5:
            self.last_write = now
            ports = [
                dict(id=p.id, name=p.name, connected=p.connected, error=p.error)
                for p in w.devices.service.ports
            ]
            state = dict(
                phase=self.phase,
                ports=ports,
                recording=w.engine.recording,
                playing=w.engine.playing,
                beat=w.engine.beat,
                song_capture_active=w.track_capture.active,
                song_capture_pending=w.track_capture.pending,
                count_in=w._record_count_deadline,
                notes=len(w.project.pattern().notes),
                steps=w.project.pattern().steps,
                timing=w.engine.timing_stats(),
                audio_error=w._audio_start_error,
                library_clips=len(w.library.clips),
                instruments=[dict(id=i.id, name=i.name) for i in w.project.instruments],
                pattern_instruments=list(w.project.pattern().instrument_ids),
                selected_instrument=w.project.selected_instrument,
                workspace=w.studio.selected,
                undo_steps=len(w._undo),
                redo_steps=len(w._redo),
                missing_samples=[
                    p.name
                    for p in w.project.pads
                    if p.sample_id and p.sample_id not in w.library.clips
                ],
                plugins={
                    str(k): dict(
                        error=getattr(v, "error", ""),
                        misses=getattr(v, "misses", None),
                        recoveries=getattr(v, "backlog_recoveries", None),
                    )
                    for k, v in w.engine.external.plugin_map().items()
                },
            )
            self.metrics.append(dict(t=now - self.started, **state))
            (self.root / "state.json").write_text(json.dumps(state, indent=2))
            (self.root / "events.json").write_text(json.dumps(self.events, indent=2))
            (self.root / "metrics.json").write_text(json.dumps(self.metrics, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument(
        "--library",
        type=Path,
        required=True,
        help="Existing library whose pack registrations the test inherits",
    )
    parser.add_argument(
        "--resume", action="store_true", help="Preserve the supplied project contents"
    )
    parser.add_argument("--controllers", type=Path, help="Saved controller configuration JSON")
    parser.add_argument("--history", type=Path, help="Preserve existing undo/redo stacks")
    parser.add_argument(
        "--check-only", action="store_true", help="Validate session assets without opening Studio"
    )
    args = parser.parse_args()
    # Prepare the library before opening audio: a fresh output directory must
    # never leave referenced pads silently missing their sample pack.
    from mpclab.library import Library
    from shutil import copy2, copytree

    session = args.output / "session"
    session.mkdir(parents=True, exist_ok=True)
    library_root = session / "library"
    library_root.mkdir(exist_ok=True)
    source_packs = args.library / "packs.json"
    if source_packs.is_file():
        copy2(source_packs, library_root / "packs.json")
    source_library = Library(args.library)
    for clip in source_library.clips.values():
        if clip.kind != "pack":
            source_folder = source_library.folder(clip.id)
            if source_folder.is_dir() and not source_folder.is_symlink():
                copytree(source_folder, library_root / clip.id, dirs_exist_ok=True)
    if (args.library / ".library-history.json").exists():
        copy2(args.library / ".library-history.json", library_root / ".library-history.json")
    if (args.library / "_trash").is_dir():
        copytree(args.library / "_trash", library_root / "_trash", dirs_exist_ok=True)
    library = Library(library_root)
    project = Project.load(args.project)
    missing = [
        pad.name for pad in project.pads if pad.sample_id and pad.sample_id not in library.clips
    ]
    if missing:
        raise RuntimeError(f"Hardware test has missing samples: {missing}")
    print(json.dumps({"library_clips": len(library.clips), "missing_samples": missing}), flush=True)
    controllers = (
        json.loads(args.controllers.read_text())["controllers"] if args.controllers else None
    )
    if args.check_only:
        return 0
    install_application_runtime()
    app = QApplication([])
    app.setApplicationName("anharmonic-hardware-audit")
    app.audit = HardwareAudit(
        app,
        args.output,
        args.project,
        resume=args.resume,
        controllers=controllers,
        history=args.history,
    )
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
