#!/usr/bin/env python
"""Bounded, application-owned desktop workflow capture using the real Qt/DSP app.

Launch on a silent workspace with focus disabled. No global input injection.
Projects/settings are isolated; pack audio is read-only. Frame timestamps expose
capture overhead. This automates UI handlers and model note entry, not a human UX trial.
"""

import argparse
from dataclasses import replace
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mpclab.production_runtime import configure

configure()

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication
from mpclab.application_features import install_application_runtime, attach_application_features
from mpclab.model import Project, Pattern, Row, Clip
from mpclab.music import Note
from mpclab.synth import PATCHES
from mpclab.ui import main_window


class AuditSettings:
    IniFormat = UserScope = None
    buffer_frames = 512

    def __init__(self, *_):
        self.values = {"audio/setup_complete": True, "audio/buffer_frames": self.buffer_frames}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class Audit:
    def __init__(self, app, output, project=None, record_inputs=False, keyboard_audit=False):
        self.app, self.output = app, output
        self.started = time.monotonic()
        self.events, self.frames, self.timings = [], [], []
        self.capture_enabled = True
        self.phase = "startup"
        self.frame_dir = output / "frames"
        self.frame_dir.mkdir(exist_ok=True)
        main_window.QSettings = AuditSettings
        self.window = main_window.MainWindow(output / "session", restore_session=False)
        attach_application_features(self.window)
        self.window.setAttribute(Qt.WA_ShowWithoutActivating)
        self.window.setWindowTitle("Anharmonic Studio — Creative workflow audit")
        self.window.resize(1600, 950)
        self.window.show()
        self.window._autosave_timer.stop()
        self.capture = QTimer()
        self.capture.timeout.connect(self.frame)
        self.capture.start(200)
        self.metric = QTimer()
        self.metric.timeout.connect(self.measure)
        self.metric.start(500)
        self.actions = iter(
            self.keyboard_workflow(project)
            if keyboard_audit
            else self.input_workflow(project)
            if record_inputs
            else self.playback_workflow(project)
            if project
            else self.workflow()
        )
        QTimer.singleShot(1500, self.advance)

    def event(self, action, **extra):
        item = dict(t=round(time.monotonic() - self.started, 4), action=action, **extra)
        self.events.append(item)
        print(json.dumps(item), flush=True)
        (self.output / "events.json").write_text(json.dumps(self.events, indent=2))

    def frame(self):
        if not self.capture_enabled:
            return
        start = time.monotonic()
        name = f"frame-{len(self.frames):05d}.jpg"
        self.window.grab().save(str(self.frame_dir / name), "JPG", 85)
        self.frames.append(
            dict(
                t=start - self.started,
                name=name,
                phase=self.phase,
                capture_ms=(time.monotonic() - start) * 1000,
            )
        )

    def measure(self):
        w = self.window
        self.timings.append(
            dict(
                t=time.monotonic() - self.started,
                phase=self.phase,
                playing=w.engine.playing,
                audio_error=w._audio_start_error,
                timing=w.engine.timing_stats(),
                plugins={
                    str(k): dict(
                        error=getattr(v, "error", ""), deadline_misses=getattr(v, "misses", None)
                    )
                    for k, v in w.engine.external.plugin_map().items()
                },
            )
        )

    def advance(self):
        try:
            delay, name, action = next(self.actions)
            self.phase = name
            start = time.monotonic()
            action()
            self.event(
                name,
                handler_ms=round((time.monotonic() - start) * 1000, 3),
                status=self.window.status.currentMessage(),
            )
            QTimer.singleShot(delay, self.advance)
        except StopIteration:
            self.finish()
        except Exception:
            self.event("FAILED", traceback=traceback.format_exc())
            self.finish()

    def select(self, text):
        return next(c for c in self.window.library.clips.values() if text.lower() in c.name.lower())

    def send(self, text, slot, destination="beats"):
        clip = self.select(text)
        self.window.sample_workflow.send(clip.id, index=slot, destination=destination)
        self.window.project.pads[slot].track = slot
        self.event("sample-loaded", name=clip.name, source=clip.source_path, slot=slot)

    def rhythm(self, trap):
        w = self.window
        p = w.project.pattern()
        p.bars = 4
        p.name = "Half-time drums" if trap else "Dust groove"
        p.steps = {
            0: {
                s: 0.85
                for s in (
                    [0, 11, 24, 30, 32, 43, 54, 60] if trap else [0, 6, 16, 23, 32, 38, 48, 55]
                )
            },
            1: {s: 0.72 for s in ([8, 24, 40, 56] if trap else range(4, 64, 8))},
            2: {s: 0.48 if s % 4 else 0.64 for s in range(0, 64, 2)},
        }
        if trap:
            for s in (14, 15, 46, 47, 62, 63):
                p.steps[2][s] = 0.38
        w.project.rows = [
            Row(name=p.name, clips=[Clip(ref=p.id, start_beat=b, length_beats=16) for b in (0, 16)])
        ]
        w.project.song_length_beats = 32
        w.project.loop_start, w.project.loop_end = 0, 32
        w.project.loop_enabled = True
        w._sync_pattern_controls()
        w.step_grid.refresh()
        w.playlist.refresh()

    def sample_phrase(self):
        w = self.window
        clip = self.select("Destiny - 90 BPM G Maj Guitar Chords")
        # Explicit source slicing: retain original source and arranged duration.
        w.project.rows.append(
            Row(
                name="PG guitar slices",
                clips=[
                    Clip(
                        kind="audio",
                        ref=clip.id,
                        start_beat=b,
                        length_beats=4,
                        offset=(i % 2) * 4 * 60 / 90,
                        source_length=4 * 60 / 90,
                        gain=0.48,
                        track=3,
                    )
                    for i, b in enumerate(range(0, 32, 4))
                ],
            )
        )
        w.engine.preload_project_audio()
        w.playlist.refresh()
        w.show_tab(w.TAB_PLAYLIST)
        self.event("sample-sliced", name=clip.name, duration=clip.duration, slices=8)

    def new_song(self, trap=False):
        self.window.engine.stop_transport(True)
        p = Project(
            name="PG Midnight Trap" if trap else "PG Sunday Sample", bpm=140 if trap else 90
        )
        p.master = 0.62
        self.window._apply_project(p)
        self.trap = trap

    def add_prism(self, name, patch, track):
        w = self.window
        instrument = w.project.add_instrument(name, replace(PATCHES[patch], track=track))
        w.piano_roll.sync_channels()
        w.piano_roll.select_channel(instrument.id)
        w.synth_panel.sync()
        w.show_tab(w.TAB_SYNTH)
        w.synth_panel._use_prism()
        self.current_id = instrument.id

    def prism_notes(self, bass=False):
        w = self.window
        bridge = w.engine.external.instrument_for(self.current_id)
        if bridge is None or getattr(bridge, "error", ""):
            raise RuntimeError(w.devices.plugin_status)
        w.synth_panel.sync_plugin_mode()
        editor = w.synth_panel.prism_surface
        editor.knobs["12"].setValue(6200 if bass else 7600)
        pat = Pattern(name="Prism bass" if bass else "Prism melody", bars=4)
        tonic = 36 if self.trap else 43
        events = (
            [
                (tonic, 0, 2.5),
                (tonic, 3.5, 0.5),
                (tonic + 3, 4, 2.5),
                (tonic + 7, 7, 1),
                (tonic, 8, 3),
                (tonic + 3, 12, 2),
                (tonic - 2, 14, 1.5),
            ]
            if bass
            else [
                (tonic + 24 + n, i, 0.55)
                for i, n in enumerate([0, 7, 10, 7, 3, 7, 12, 10, 0, 7, 10, 14, 12, 7, 3, 7])
            ]
        )
        pat.notes = [
            Note(p, start, length, 0.6 if bass else 0.45, instrument=self.current_id)
            for p, start, length in events
        ]
        w.project.patterns.append(pat)
        w.project.rows.append(
            Row(
                name=pat.name,
                clips=[Clip(ref=pat.id, start_beat=b, length_beats=16) for b in (0, 16)],
            )
        )
        w.project.current_pattern = pat.id
        w._sync_pattern_controls()
        w.playlist.refresh()
        self.event("prism-ready", instrument=self.current_id, pid=bridge.plugin.process.pid)

    def save(self, name):
        w = self.window
        path = self.output / "session/projects" / f"{name}.json"
        assert w._save_project_to(path)
        restored = Project.load(path)
        assert len(restored.instrument_plugins) == 2
        self.event(
            "saved-roundtrip", path=str(path), instrument_ids=list(restored.instrument_plugins)
        )

    def begin_record(self):
        w = self.window
        self.note_count_before = len(w.project.pattern().notes)
        w.set_mode("pattern")
        w.engine.set_position(0)
        w.btn_rec.click()

    def record_note(self, release=False):
        w = self.window
        pitch = 60 if self.trap else 67
        if not release:
            assert w.engine.recording and w.engine.playing, "count-in did not start recording"
            w.play_synth_note(pitch, 0.5)
        else:
            w.release_synth_note(pitch)
            w.stop_all()
            notes = w.project.pattern().notes
            assert len(notes) > self.note_count_before, "Prism performance was not captured"
            assert notes[-1].instrument == self.current_id
            self.event(
                "recorded-note",
                instrument=notes[-1].instrument,
                pitch=notes[-1].pitch,
                duration=notes[-1].duration,
            )

    def play(self):
        w = self.window
        w.engine.preload_project_audio()
        w.set_mode("song")
        w.engine.loop_song = True
        w.engine.reset_timing()
        w.engine.play(0)

    def reduce_instruments(self, drums_only=False):
        w = self.window
        # Remove the melody notes along with its host; otherwise they fall back
        # to the built-in synth and no longer isolate instrument count.
        w.engine.stop_transport(True)
        ids = list(w.engine.external.active_instrument_ids()) if drums_only else [self.current_id]
        for instrument_id in ids:
            w.devices.remove_plugin("instrument", instrument_id=instrument_id)
        w.project.rows = w.project.rows[:1] if drums_only else w.project.rows[:2]
        w.playlist.refresh()
        self.play()

    def workflow(self):
        w = self.window
        yield (
            1500,
            "PG library",
            lambda: (
                w.browser.open_crate("all"),
                self.event("library", count=len(w.library.clips), audio_error=w._audio_start_error),
            ),
        )
        for trap, slug in ((False, "sample-beat"), (True, "trap-beat")):
            yield 1200, slug + " new", lambda t=trap: self.new_song(t)
            yield 1200, "PG kick", lambda: self.send("Kick (Axel)", 0)
            yield 1200, "PG snare", lambda: self.send("Snare (Aztec)", 1)
            yield 1200, "PG hats", lambda: self.send("Hihat (", 2)
            yield 1800, "sequence drums", lambda t=trap: self.rhythm(t)
            if not trap:
                yield 2000, "slice sample", self.sample_phrase
            yield 4500, "load Prism bass", lambda: self.add_prism("Prism bass", "Carbon Pulse", 4)
            yield 1800, "write bass", lambda: self.prism_notes(True)
            yield (
                4500,
                "load Prism melody",
                lambda: self.add_prism("Prism melody", "Prism Droplets", 5),
            )
            yield 1800, "write melody", self.prism_notes
            yield 2800, "record count-in", self.begin_record
            yield 350, "record Prism note on", self.record_note
            yield 1000, "record Prism note off", lambda: self.record_note(True)
            yield 1000, "save " + slug, lambda s=slug: self.save(s)
            yield 22000, slug + " two Prisms visualizer capture", self.play
            yield 1000, "stop", lambda: w.engine.stop_transport(True)
        yield (
            12000,
            "ablation scope hidden capture",
            lambda: (w.show_tab(w.TAB_PLAYLIST), self.play()),
        )
        yield 1000, "stop", lambda: w.engine.stop_transport(True)
        yield (
            12000,
            "ablation scope visible no capture",
            lambda: (w.show_tab(w.TAB_SYNTH), setattr(self, "capture_enabled", False), self.play()),
        )
        yield (
            1000,
            "stop",
            lambda: (setattr(self, "capture_enabled", True), w.engine.stop_transport(True)),
        )
        yield 12000, "ablation one Prism", self.reduce_instruments
        yield 1000, "stop", lambda: w.engine.stop_transport(True)
        yield 12000, "ablation drums only", lambda: self.reduce_instruments(True)
        yield 1000, "stop", lambda: w.engine.stop_transport(True)

    def playback_workflow(self, project):
        w = self.window

        def load():
            assert w.load_project_path(project, clear_session=False)
            w.show_tab(w.TAB_SYNTH)

        yield 5000, "reopen saved trap", load
        self.independence = {}

        def sound(index, preset):
            instrument_id = w.project.instruments[index].id
            w.piano_roll.select_channel(instrument_id)
            w.synth_panel.sync_plugin_mode()
            w.synth_panel.prism_surface.preset(preset)
            self.event("sound-changed", instrument=instrument_id, preset=preset)

        def remember():
            self.independence = {
                instrument.id: dict(
                    specification=deepcopy(w.project.instrument_plugins[instrument.id]),
                    parameters=deepcopy(
                        w.engine.external.instrument_for(instrument.id).info["parameters"]
                    ),
                    pid=w.engine.external.instrument_for(instrument.id).plugin.process.pid,
                )
                for instrument in w.project.instruments
            }
            assert len(self.independence) == 2
            assert len({v["pid"] for v in self.independence.values()}) == 2
            filename = (
                "independent-before.json"
                if not (self.output / "independent-before.json").exists()
                else "independent-before-b.json"
            )
            w.project.save(self.output / filename)

        def verify_other(index, filename):
            instrument_id = w.project.instruments[index].id
            prior = self.independence[instrument_id]
            bridge = w.engine.external.instrument_for(instrument_id)
            assert w.project.instrument_plugins[instrument_id] == prior["specification"]
            assert bridge.info["parameters"] == prior["parameters"]
            assert bridge.plugin.process.pid == prior["pid"]
            w.project.save(self.output / filename)
            self.event(
                "other-Prism-unchanged",
                instrument=instrument_id,
                parameter_count=len(prior["parameters"]),
                pid=prior["pid"],
            )

        yield 1500, "Prism A Copper Pluck", lambda: sound(0, "Copper Pluck")
        yield 1500, "Prism B Velvet Poly", lambda: sound(1, "Velvet Poly")
        yield 1000, "remember independent sounds", remember
        yield 1500, "change only A to Acid Orchard", lambda: sound(0, "Acid Orchard")
        yield 1000, "verify B unchanged", lambda: verify_other(1, "independent-after-a.json")
        yield 1000, "remember changed A", remember
        yield 1500, "change only B to Dust Choir", lambda: sound(1, "Dust Choir")
        yield 1000, "verify A unchanged", lambda: verify_other(0, "independent-after-b.json")
        yield 15000, "reopened two Prisms capture", self.play
        yield 1000, "stop", lambda: w.engine.stop_transport(True)
        yield (
            15000,
            "reopened two Prisms no capture",
            lambda: (setattr(self, "capture_enabled", False), self.play()),
        )
        yield 1000, "stop", lambda: w.engine.stop_transport(True)
        yield (
            15000,
            "reopened arrange no capture",
            lambda: (w.show_tab(w.TAB_PLAYLIST), self.play()),
        )
        yield (
            1000,
            "stop",
            lambda: (setattr(self, "capture_enabled", True), w.engine.stop_transport(True)),
        )

    def input_workflow(self, project):
        w = self.window
        yield 2000, "open saved two-Prism project", lambda: w._apply_project(Project.load(project))

        def prepare(workspace, index):
            self.current_id = w.project.instruments[index].id
            pattern = w.project.pattern()
            pattern.steps.clear()
            pattern.notes.clear()
            w.set_mode("pattern")
            w.piano_roll.select_channel(self.current_id)
            w.show_tab(workspace)
            w.devices.router.settings["audit-mpc"] = {"mode": "Pads", "pad_base": 36}
            w.devices._tick()
            w.engine.metronome = False
            w.bpm_box.setValue(120)
            w.step_grid.refresh()

        def midi(port, message):
            assert w.engine.recording and w.engine.playing
            w.engine.midi.submit(port, message)

        def count_in():
            w.btn_rec.click()
            deadline = w._record_count_deadline
            # Keep the last GUI tick late while audio and input keep running.
            QTimer.singleShot(1250, w._record_count_timer.stop)

            def first_hit():
                if time.monotonic() < deadline:
                    QTimer.singleShot(1, first_hit)
                    return
                w.engine.midi.submit("audit-mpc", [0x99, 36, 100])
                self.event(
                    "first-hit-after-count-in", delay_ms=(time.monotonic() - deadline) * 1000
                )
                QTimer.singleShot(100, lambda: w.engine.midi.submit("audit-mpc", [0x89, 36, 0]))

            QTimer.singleShot(1500, first_hit)

        def verify_first():
            assert 0 in w.project.pattern().steps.get(0, {}), w.project.pattern().steps
            assert not w.project.pattern().notes
            self.event("first-hit-saved-at-step-zero", gui_count_in_tick_delayed=True)
            w._advance_record_count()

        def verify(label):
            w.stop_all()
            pattern = w.project.pattern()
            assert sum(len(row) for row in pattern.steps.values()) == 3, pattern.steps
            assert len(pattern.notes) == 2, pattern.notes
            assert all(n.pad is None and n.instrument == self.current_id for n in pattern.notes)
            path = self.output / f"{label}-recorded.json"
            w.project.save(path)
            self.event(
                "input-recording-verified",
                workspace=label,
                pad_steps=3,
                instrument_notes=2,
                instrument=self.current_id,
                physical_controller=False,
                project=str(path),
            )

        for label, workspace, index in (("sequence", w.TAB_SEQ, 0), ("notes", w.TAB_PIANO, 1)):
            yield 1000, f"prepare {label}", lambda ws=workspace, i=index: prepare(ws, i)
            yield 2200, f"{label} audible count-in", count_in
            yield 100, "verify first beat", verify_first
            yield 150, "injected MPC pad on", lambda: midi("audit-mpc", [0x99, 36, 100])
            yield 400, "injected MPC pad off", lambda: midi("audit-mpc", [0x89, 36, 0])
            yield 150, "Studio pad on", lambda: w._pad_pressed(0, 0.8)
            yield 400, "Studio pad off", lambda: w._pad_released(0)
            yield 250, "injected MIDI keyboard on", lambda: midi("audit-keys", [0x90, 64, 100])
            yield 400, "injected MIDI keyboard off", lambda: midi("audit-keys", [0x80, 64, 0])
            yield 250, "typing keyboard handler on", lambda: w.play_synth_note(67, 0.7)
            yield 700, "typing keyboard handler off", lambda: w.release_synth_note(67)
            yield 2500, f"verify {label}", lambda name=label: verify(name)

    def keyboard_workflow(self, project):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent
        from mpclab.ui.typing_keyboard import TypingKeyboardWindow
        import statistics

        w = self.window
        yield (
            2000,
            "open two-Prism keyboard session",
            lambda: w._apply_project(Project.load(project)),
        )
        keyboard = TypingKeyboardWindow(w)
        w.typing_keyboard = keyboard  # Owned, hidden tool; never takes desktop focus.
        keys = (Qt.Key_Z, Qt.Key_C, Qt.Key_B)

        def prepare(target):
            w.project.pattern().steps.clear()
            w.project.pattern().notes.clear()
            w.project.pattern().bars = 4
            w.bpm_box.setValue(120)
            if target == "sample":
                self.send("Destiny - 90 BPM G Maj Guitar Chords", 3, "notes")
                w.project.pads[3].mode = "gate"
                w.project.pads[3].mono = False
                w.piano_roll.select_channel(3)
            else:
                w.piano_roll.select_channel(w.project.instruments[target].id)
            w.show_tab(w.TAB_PIANO)
            w.set_mode("pattern")
            self.key_times = []
            self.chords_sent = 0
            w.btn_rec.click()

        def press():
            for key in keys:
                start = time.perf_counter()
                keyboard._handle_press(QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))
                self.key_times.append((time.perf_counter() - start) * 1000)
            self.chords_sent += 1
            QTimer.singleShot(120, release)

        def release():
            for key in keys:
                keyboard._handle_release(QKeyEvent(QEvent.KeyRelease, key, Qt.NoModifier))
            if self.chords_sent < 20:
                QTimer.singleShot(180, press)

        def run_chords():
            assert w.engine.recording and w.engine.playing
            self.capture_enabled = False
            w.engine.reset_timing()
            press()

        def verify(target):
            stats = w.engine.timing_stats()
            w.stop_all()
            self.capture_enabled = True
            notes = w.project.pattern().notes
            assert len(notes) == 60, len(notes)
            assert not w.project.pattern().steps
            expected = None if target == "sample" else w.project.instruments[target].id
            assert all(
                n.instrument == expected and n.pad == (3 if target == "sample" else None)
                for n in notes
            )
            spread = [
                (max(n.start for n in notes[i : i + 3]) - min(n.start for n in notes[i : i + 3]))
                * 500
                for i in range(0, 60, 3)
            ]
            values = sorted(self.key_times)
            self.event(
                "keyboard-chords-verified",
                target=target,
                notes=60,
                chords=20,
                handler_median_ms=statistics.median(values),
                handler_p99_ms=values[-1],
                chord_spread_max_ms=max(spread),
                timing=stats,
                plugin_misses={str(k): v.misses for k, v in w.engine.external.plugin_map().items()},
                physical_keyboard=False,
                capture_during_playback=False,
            )
            w.project.save(self.output / f"keyboard-{target}.json")

        for target in (0, 1, "sample"):
            yield 2200, f"prepare keyboard {target}", lambda t=target: prepare(t)
            yield 7000, f"play 20 chords {target}", run_chords
            yield 1800, f"verify keyboard {target}", lambda t=target: verify(t)

    def finish(self):
        self.capture.stop()
        self.metric.stop()
        self.window.engine.stop_transport(True)
        for name, value in (("frames.json", self.frames), ("timings.json", self.timings)):
            (self.output / name).write_text(json.dumps(value, indent=2))
        self.window._dirty = False
        self.window.close()  # This audit's owned Qt window only.
        self.app.quit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--record-inputs", action="store_true")
    parser.add_argument("--keyboard-audit", action="store_true")
    parser.add_argument("--buffer", type=int, choices=(256, 512, 1024, 2048), default=512)
    args = parser.parse_args()
    install_application_runtime()
    app = QApplication([])
    app.setApplicationName("anharmonic-creative-audit")
    app.setDesktopFileName("anharmonic-creative-audit")
    AuditSettings.buffer_frames = args.buffer
    app.audit = Audit(app, args.output, args.project, args.record_inputs, args.keyboard_audit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
