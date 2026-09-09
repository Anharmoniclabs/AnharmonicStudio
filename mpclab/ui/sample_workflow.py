"""Explicit, reversible sample destinations using the existing stable pad slots."""

from dataclasses import replace
import math

from .window_client import WindowClient

from ..model import PADS_PER_BANK
from ..music import Note

MIN_SAMPLE_FRAMES = 8


def reserved_slots(project):
    slots = {index for pattern in project.patterns for index in pattern.steps}
    slots.update(n.pad for p in project.patterns for n in p.notes if n.pad is not None)
    return slots


def valid_sample_range(clip, start, end, sample_rate):
    """Return normalized finite bounds or None without touching project state."""
    try:
        start = float(start)
        end = clip.duration if end is None else float(end)
        duration = float(clip.duration)
        rate = int(sample_rate)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(v) for v in (start, end, duration)):
        return None
    if rate <= 0 or not 0 <= start < end <= duration + 1e-6:
        return None
    end = min(end, duration)
    if round((end - start) * rate) < MIN_SAMPLE_FRAMES:
        return None
    return start, end


class SampleWorkflow(WindowClient):
    def __init__(self, app):
        self.app = app
        self.held = {}  # pitch -> slot captured at key-down; None is the original synth
        self.recorded = {}

    def send(
        self, sample_id, start=0.0, end=None, *, destination="beats", index=None, source_clip=None
    ):
        app = self.app
        clip = app.library.clips.get(sample_id)
        if clip is None or destination not in ("beats", "notes"):
            app.status.showMessage("Select an available sound first", 4000)
            return None
        bounds = valid_sample_range(clip, start, end, app.engine.sr)
        if bounds is None:
            app.status.showMessage("Choose a valid audible range inside the sound", 4000)
            return None
        start, end = bounds

        new = index is None
        if new:
            reserved = reserved_slots(app.project)
            base = app.pads.bank * PADS_PER_BANK
            index = next(
                (
                    i
                    for i in range(base, base + PADS_PER_BANK)
                    if app.project.pads[i].empty and i not in reserved
                ),
                None,
            )
        if index is None:
            app.status.showMessage(
                "This bank is full. Choose another bank or replace a lane explicitly.", 6000
            )
            return None
        if type(index) is not int or not 0 <= index < len(app.project.pads):
            return None

        # Resolve audio before changing the document. The callback only sees
        # cache-ready audio. Failure creates neither a partial edit nor history.
        try:
            data = app.library.audio(sample_id)
            if data is None:
                raise ValueError("audio is unavailable")
            if source_clip is not None and source_clip.reverse:
                app.library.reversed_audio(sample_id)
        except Exception as exc:
            app.status.showMessage(f"Could not load sound: {exc}", 6000)
            return None

        previous = app.project.pads[index]
        replacing_loaded = not previous.empty
        # Replacing a lane/sample instrument is intentionally non-destructive:
        # keep the user's gain, pan, mixer route, envelope, mode, mono choice,
        # choke and recorded root. Only source-specific trim/reverse/sync state
        # resets. A newly-created Notes instrument still assumes C4 explicitly.
        pad = replace(
            previous,
            sample_id=sample_id,
            name=clip.name,
            start=start,
            end=end,
            root_note=previous.root_note if replacing_loaded else 60,
            reverse=False,
            sync_beats=0.0,
        )
        if new or previous.empty:
            pad.mode = "gate" if destination == "notes" else "one-shot"
        if source_clip is not None:
            pad = replace(
                pad,
                gain=source_clip.gain,
                track=source_clip.track,
                reverse=source_clip.reverse,
                loop_crossfade=source_clip.loop_crossfade,
                mode="loop" if source_clip.loop else "gate",
            )

        app.snapshot()
        app.project.pads[index] = pad
        app.select_pad(index)
        app.step_grid.refresh()
        app.piano_roll.sync()
        app._set_dirty(True)
        if destination == "notes":
            self.open_notes(index)
        elif app.studio.enabled:
            app.studio.select(1)
        else:
            app.show_tab(1)
        bank = chr(65 + index // PADS_PER_BANK)
        root_message = (
            f"root MIDI {pad.root_note} preserved"
            if replacing_loaded and destination == "notes"
            else "root C4 assumed; set recorded pitch in Notes"
        )
        app.status.showMessage(
            f"{clip.name} → {bank}{index % PADS_PER_BANK + 1} · "
            + (root_message if destination == "notes" else "rhythm and mixer route preserved"),
            6000,
        )
        return index

    def from_arrangement(self, clip=None):
        """Open the picked audio block as a chromatic sample instrument."""
        app = self.app
        clip = clip or app.playlist.selected_clip
        if clip is None or clip.kind != "audio" or app.playlist.row_for_clip(clip) is None:
            return None
        meta = app.library.clips.get(clip.ref)
        if meta is None:
            app.status.showMessage("The sample for this clip is unavailable", 4000)
            return None
        start = clip.offset
        end = (
            min(meta.duration, start + clip.source_length)
            if clip.source_length > 0
            else meta.duration
        )
        if not clip.loop:
            duration = min(end - start, clip.length_beats * 60 / app.project.bpm)
            if clip.reverse:
                start = end - duration
            else:
                end = start + duration
        mode = "loop" if clip.loop else "gate"
        for index, pad in enumerate(app.project.pads):
            if (
                pad.sample_id == clip.ref
                and pad.start == start
                and pad.end == end
                and pad.reverse == clip.reverse
                and pad.track == clip.track
                and pad.gain == clip.gain
                and pad.mode == mode
                and pad.loop_crossfade == clip.loop_crossfade
            ):
                app.select_pad(index)
                self.open_notes(index)
                app.piano_roll.canvas.setFocus()
                return index
        index = self.send(clip.ref, start, end, destination="notes", source_clip=clip)
        if index is not None:
            app.piano_roll.canvas.setFocus()
        return index

    def from_selection(self, destination):
        if not self.app.current_clip:
            self.app.status.showMessage("Select a sample first", 3000)
            return None
        return self.send(
            self.app.current_clip,
            *self.app.wave.selection(),
            destination=destination,
        )

    def open_notes(self, index):
        self.app.piano_roll.select_channel(index)
        if self.app.studio.enabled:
            self.app.studio.select(6)
        else:
            self.app.show_tab(6)

    def step_notes(self, index):
        app = self.app
        pattern = app.project.pattern()
        steps = pattern.steps.get(index)
        if steps:
            app.snapshot()
            for step, velocity in sorted(steps.items()):
                start = step / pattern.div + app.engine._swing_offset(pattern, step)
                if start < pattern.length_beats:
                    pattern.notes.append(
                        Note(
                            app.project.pads[index].root_note,
                            start,
                            min(1 / pattern.div, pattern.length_beats - start),
                            velocity,
                            index,
                        )
                    )
            pattern.steps.pop(index)
            app._set_dirty(True)
            app.step_grid.refresh()
        self.open_notes(index)

    def note_on(self, note, velocity):
        if note in self.held:
            self.note_off(note)
        app = self.app
        slot = app.piano_roll.target_pad
        self.held[note] = slot
        if slot is None:
            app.play_synth_note(note, velocity)
            return
        if app.project.pads[slot].empty:
            app.status.showMessage("This instrument has no sound. Load or replace it first.", 4000)
            return
        capture = getattr(app, "track_capture", None)
        if capture is not None:
            capture.note_on(note, velocity, slot)
        if app.engine.recording and app.engine.playing and app.engine.mode == "pattern":
            app.snapshot()
            self.recorded[note] = (app.project.pattern().id, app.engine.beat, velocity, slot)
        app.engine.sample_note_on(slot, note, velocity)
        if app.typing_keyboard is not None:
            app.typing_keyboard.keyboard.set_note_active(note, True)

    def note_off(self, note):
        if note not in self.held:
            return
        app = self.app
        slot = self.held.pop(note)
        if slot is None:
            app.release_synth_note(note)
            return
        capture = getattr(app, "track_capture", None)
        if capture is not None:
            capture.note_off(note, slot)
        recorded = self.recorded.pop(note, None)
        if recorded:
            pattern_id, start, velocity, recorded_slot = recorded
            pattern = next((p for p in app.project.patterns if p.id == pattern_id), None)
            if pattern:
                beat = start % pattern.length_beats
                duration = min(max(0.03125, app.engine.beat - start), pattern.length_beats - beat)
                pattern.notes.append(Note(note, beat, duration, velocity, recorded_slot))
                app._set_dirty(True)
                app.piano_roll.canvas.refresh()
        app.engine.sample_note_off(slot, note)
        if app.typing_keyboard is not None:
            app.typing_keyboard.keyboard.set_note_active(note, False)

    def panic(self):
        for note in tuple(self.held):
            self.note_off(note)
        self.app.engine.sample_panic()
