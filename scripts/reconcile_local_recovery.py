"""Reconcile the recovered DAW/MIDI branch with the recovered Prism/Hand FX work.

This is intentionally idempotent: it can run again while recovery CI is being
iterated without duplicating code. Remove it after the recovery is validated.
"""

from pathlib import Path
import subprocess


def ensure_replace(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text()
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"{label}: expected source shape not found")
    p.write_text(text.replace(old, new, 1))


def reconcile_engine() -> None:
    ensure_replace(
        "mpclab/engine.py",
        """        instrument_id: str | None = None,\n        midi_channel: int = 0,\n        midi_owner: str | None = None,\n    ) -> None:""",
        """        instrument_id: str | None = None,\n        midi_channel: int = 0,\n        midi_owner: str | None = None,\n        event_source=None,\n        trigger_id=None,\n    ) -> None:""",
        "engine _spawn_synth signature",
    )
    ensure_replace(
        "mpclab/engine.py",
        """            self.external.note_on(\n                note, velocity, offset, gate_frames, live_trigger, channel=midi_channel\n            )""",
        """            self.external.note_on(\n                note,\n                velocity,\n                offset,\n                gate_frames,\n                live_trigger,\n                channel=midi_channel,\n                event_source=event_source,\n                trigger_id=trigger_id,\n            )""",
        "engine external synth routing",
    )


def reconcile_realtime_mixer() -> None:
    ensure_replace(
        "mpclab/engine_mixing.py",
        """        for event in notes:\n            beat, pad_idx, vel, _gate, sequence_id = event\n            off = int(max(0.0, (beat - b0) / bps))""",
        """        for event in notes:\n            beat, pad_idx, vel, _gate, sequence_id = event\n            source = getattr(event, \"source\", None)\n            off = int(max(0.0, (beat - b0) / bps))""",
        "realtime event source",
    )
    ensure_replace(
        "mpclab/engine_mixing.py",
        """                    instrument_id=instrument_id,\n                    midi_channel=getattr(event, \"channel\", 0),\n                )""",
        """                    instrument_id=instrument_id,\n                    midi_channel=getattr(event, \"channel\", 0),\n                    event_source=source,\n                )""",
        "realtime synth ownership",
    )


def reconcile_offline_renderer() -> None:
    ensure_replace(
        "mpclab/engine_offline.py",
        """from .music import automation_values\nfrom .instrument_state import decode_destination, voice_patch""",
        """from .music import automation_values\nfrom .prism_motion import automation_parameters\nfrom .instrument_state import decode_destination, voice_patch""",
        "offline Prism import",
    )
    ensure_replace(
        "mpclab/engine_offline.py",
        """                [(*event[:4], event[5]) for event in synth_events if event[4] is None],\n            )""",
        """                [(*event[:4], event[5]) for event in synth_events if event[4] is None],\n                proj.bpm,\n            )""",
        "offline Prism tempo",
    )
    ensure_replace(
        "mpclab/engine_offline.py",
        """                plugins.render_instrument(external_block, start, frames)""",
        """                parameters = {}\n                if mode == \"song\" and plugins.instrument.info.get(\"name\") == \"Anharmonic Prism\":\n                    parameters = automation_parameters(proj, start / (spb * engine.sr))\n                plugins.render_instrument(external_block, start, frames, parameters)""",
        "offline Prism automation",
    )


def reconcile_synth_fallback() -> None:
    path = Path("mpclab/synth.py")
    merged = path.read_text()
    baseline = subprocess.check_output(
        ["git", "show", "09cd01e6a333903a609745a24177a70f81df4a4c:mpclab/synth.py"],
        text=True,
    )
    render_marker = "    def render(self, dest: np.ndarray, patch: SynthPatch) -> None:\n"
    block_marker = "        sr = float(self.sample_rate)\n"
    end_marker = "\n\nclass ArpState:"
    merged_render = merged.index(render_marker)
    current_start = merged.index(block_marker, merged_render)
    current_end = merged.index(end_marker, current_start)
    base_render = baseline.index(render_marker)
    base_start = baseline.index(block_marker, base_render)
    base_end = baseline.index(end_marker, base_start)
    block = baseline[base_start:base_end]
    block = block.replace(
        "base = midi_to_hz(self.note)",
        "base = midi_to_hz(self.note + self.pitch_bend)",
    )
    block = block.replace(
        "pitch_mod = np.exp2(lfo * patch.lfo_pitch / 1200.0)",
        "pitch_mod = np.exp2(lfo * (patch.lfo_pitch + self.modulation * 40) / 1200.0)",
    )
    block = block.replace(
        """                cutoff = patch.cutoff * math.exp2(\n                    patch.filter_env * self._half_envelope * 4.5\n                    + patch.lfo_filter * self._half_lfo * 3.0\n                )""",
        """                cutoff = patch.cutoff * (1.0 + self.pressure) * math.exp2(\n                    patch.filter_env * self._half_envelope * 4.5\n                    + patch.lfo_filter * self._half_lfo * 3.0\n                )""",
    )
    block = block.replace(
        "        gain = env * min(1.0, max(0.0, self.velocity)) * max(0.0, patch.volume)",
        """        gain = (\n            env\n            * min(1.0, max(0.0, self.velocity))\n            * max(0.0, patch.volume)\n            * self.expression_gain\n        )""",
    )
    current = merged[current_start:current_end]
    if current != block:
        path.write_text(merged[:current_start] + block + merged[current_end:])


def reconcile_track_recording() -> None:
    p = Path("mpclab/ui/track_recording.py")
    text = p.read_text()
    text = text.replace(
        "import math\nimport numpy as np\n\nimport numpy as np\n",
        "import math\n\nimport numpy as np\n",
        1,
    )
    if "        self._live_monitor = None\n" not in text:
        marker = "        self._capture_sample_rate = None\n"
        if marker not in text:
            raise SystemExit("track recorder monitor state: expected source shape not found")
        text = text.replace(marker, marker + "        self._live_monitor = None\n", 1)

    old_start = """                self.recorder.sample_rate = app.engine.sr\n                self.recorder.blocksize = app.engine.blocksize\n                from ..autotune.live import LiveMonitor, MonitorRoute\n\n                route = MonitorRoute(self.recorder, app.engine, self.settings.monitor_gain)\n                # Start dry capture first, then initialize using its negotiated rate.\n                self.recorder.start(\n                    device, self.settings.input_gain_db, route if self.settings.monitor else None\n                )\n                self.recorder.input_channels = tuple(self.settings.input_channels)\n                self.recorder.start(device, self.settings.input_gain_db, monitor)"""
    new_start = """                self.recorder.sample_rate = app.engine.sr\n                self.recorder.blocksize = app.engine.blocksize\n                self.recorder.input_channels = tuple(self.settings.input_channels)\n                from ..autotune.live import LiveMonitor, MonitorRoute\n\n                route = MonitorRoute(self.recorder, app.engine, self.settings.monitor_gain)\n                self.recorder.start(\n                    device, self.settings.input_gain_db, route if self.settings.monitor else None\n                )\n                if self.settings.monitor and self.settings.corrected_monitor:\n                    try:\n                        self._live_monitor = LiveMonitor(\n                            self.project.vocal, self.recorder.sample_rate, route\n                        )\n                        self.recorder.monitor_callback = self._live_monitor.push\n                    except Exception as exc:\n                        self._cue_error = f\"Corrected cue unavailable; monitoring dry · {exc}\"\n                self._capture_sample_rate = self.recorder.sample_rate"""
    if new_start not in text:
        if old_start not in text:
            raise SystemExit("track recorder start: expected source shape not found")
        text = text.replace(old_start, new_start, 1)

    finish_old = """        if self.midi_take is not None:\n            completed = self.app.engine.midi.end_take()"""
    finish_new = """        if self._live_monitor is not None:\n            self._live_monitor.close()\n            self._live_monitor = None\n        if self.midi_take is not None:\n            completed = self.app.engine.midi.end_take()"""
    if finish_new not in text:
        if finish_old not in text:
            raise SystemExit("track recorder MIDI finish shape not found")
        text = text.replace(finish_old, finish_new, 1)

    add_old = (
        '                    sources.append(app.library.add_audio(block, label, kind="recording"))'
    )
    add_new = """                    sources.append(\n                        app.library.add_audio(\n                            block,\n                            label,\n                            kind=\"recording\",\n                            source_sample_rate=self._capture_sample_rate,\n                        )\n                    )"""
    if add_new not in text:
        if add_old not in text:
            raise SystemExit("multichannel library add shape not found")
        text = text.replace(add_old, add_new, 1)

    placement_old = """            row.clips.append(clip)\n            position = app.project.rows.index(row) + 1\n            app.project.rows[position:position] = additional_rows"""
    placement_new = """            row.clips.append(clip)\n            position = app.project.rows.index(row) + 1\n            app.project.rows[position:position] = additional_rows\n            if audio is not None:\n                app.engine.preload_project_audio()"""
    if placement_new not in text:
        if placement_old not in text:
            raise SystemExit("recording placement shape not found")
        text = text.replace(placement_old, placement_new, 1)

    discard_old = (
        """        self.unsaved = None\n        self.target = None\n        self.notes.clear()"""
    )
    discard_new = """        self.unsaved = None\n        self.midi_take = None\n        self.target = None\n        self.notes.clear()"""
    if discard_new not in text:
        if discard_old not in text:
            raise SystemExit("discard take shape not found")
        text = text.replace(discard_old, discard_new, 1)
    p.write_text(text)


def reconcile_test_lint() -> None:
    p = Path("tests/test_prism.py")
    text = p.read_text()
    text = text.replace(
        "            for block in range(32):\n", "            for _block in range(32):\n", 1
    )
    p.write_text(text)


def main() -> None:
    reconcile_engine()
    reconcile_realtime_mixer()
    reconcile_offline_renderer()
    reconcile_synth_fallback()
    reconcile_track_recording()
    reconcile_test_lint()


if __name__ == "__main__":
    main()
