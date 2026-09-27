#!/usr/bin/env python
"""Scan local song/stem copies and build an isolated, auditionable sampler project."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import sys
import time

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--song-id", required=True)
    parser.add_argument("--drums-id", required=True)
    parser.add_argument("--music-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()
    if not args.show:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from mpclab import detect
    from mpclab.application_features import install_application_runtime, attach_application_features
    from mpclab.model import Project, map_sample_range
    from mpclab.engine import Engine
    from mpclab.ui import main_window
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication

    class AuditSettings:
        IniFormat = UserScope = None

        def __init__(self, *_args):
            self.values = {"audio/setup_complete": True}

        def value(self, key, default=None):
            return self.values.get(key, default)

        def setValue(self, key, value):
            self.values[key] = value

    args.output.mkdir(parents=True, exist_ok=True)
    session = args.output / "session"
    results, audio_by_id, report = {}, {}, {}
    rate = None
    for label, cid, kind in (
        ("song", args.song_id, None),
        ("drums", args.drums_id, "drums"),
        ("music", args.music_id, "other"),
    ):
        source = args.library / cid
        target = session / "library" / cid
        if not target.exists():
            shutil.copytree(source, target)
        audio, sr = sf.read(target / "audio.wav", dtype="float32", always_2d=True)
        if rate is not None and rate != sr:
            raise ValueError("Audit sources must share a sample rate")
        rate = sr
        audio_by_id[cid] = audio
        started = time.monotonic()
        result = detect.scan(audio, sr, source_kind=kind)
        results[cid] = result
        report[label] = {
            "clip_id": cid,
            "scan_seconds": time.monotonic() - started,
            "cuts": len(result["onsets"]),
            "bpm": result["bpm"],
            "shortlist": {k: [asdict(c) for c in v] for k, v in result["by_kind"].items()},
            "phrases": [asdict(c) for c in result["loops"]],
        }

    install_application_runtime()
    song_name = json.loads((args.library / args.song_id / "meta.json").read_text())["name"]
    project = Project(name=f"{song_name} · clean chop audition", bpm=results[args.song_id]["bpm"])
    for cid, result in results.items():
        project.slices[cid] = result["onsets"]
    placements = []
    groups = [
        (0, args.drums_id, sorted(detect.layout_hits(results[args.drums_id]["by_kind"]).items())),
        (16, args.song_id, list(enumerate(results[args.song_id]["loops"]))),
        (32, args.music_id, list(enumerate(results[args.music_id]["loops"]))),
        (48, args.music_id, list(enumerate(results[args.music_id]["by_kind"]["tonal"]))),
    ]
    audition = []
    exports = args.output / "cuts"
    exports.mkdir(exist_ok=True)
    for base, cid, candidates in groups:
        for local, candidate in candidates:
            pad = project.pads[base + local]
            name = f"{chr(65 + base // 16)}{local + 1} {candidate.kind}"
            map_sample_range(pad, cid, candidate.start, candidate.end, name)
            if candidate.kind == "loop":
                pad.sync_beats = candidate.detail["bars"] * 4
            pad.choke = 1 if candidate.kind == "hat" else 0
            cut = audio_by_id[cid][
                round(candidate.start * rate) : round(candidate.end * rate)
            ].copy()
            # Tiny audition fades supplement quiet cuts; source media is untouched.
            fade = min(round(rate * 0.002), len(cut) // 2)
            if fade:
                cut[:fade] *= np.linspace(0, 1, fade)[:, None]
                cut[-fade:] *= np.linspace(1, 0, fade)[:, None]
            sf.write(exports / f"{name}.wav", cut, rate, subtype="PCM_24")
            audition.extend(
                (cut[: rate * 8], np.zeros((round(rate * 0.35), cut.shape[1]), np.float32))
            )
            placements.append({"pad": name, "clip_id": cid, **asdict(candidate)})
    sf.write(args.output / "audition.wav", np.concatenate(audition), rate, subtype="PCM_24")
    project.save(args.output / "sampler-audition.json")
    report["pads"] = placements
    prior_report = args.output / "report.json"
    if args.show and prior_report.exists():
        previous = json.loads(prior_report.read_text())
        if "engine_pad_checks" in previous:
            report["engine_pad_checks"] = previous["engine_pad_checks"]
    (args.output / "report.json").write_text(json.dumps(report, indent=2))
    app = QApplication([])
    main_window.QSettings = AuditSettings
    if not args.show:
        Engine.start = lambda self: None
    window = main_window.MainWindow(session, restore_session=False)
    attach_application_features(window)
    window._apply_project(project)
    if not args.show:
        checks = []
        for base, cid, candidates in groups:
            window.library.audio(cid)
            for local, _candidate in candidates:
                voice = window.engine._voice_for_pad(project.pads[base + local], 1.0)
                if voice is None:
                    raise RuntimeError(f"No playable voice for pad {base + local}")
                buffer = np.zeros((rate // 2, 2), np.float32)
                voice.render(buffer, 0)
                peak = float(np.max(np.abs(buffer)))
                if not np.isfinite(buffer).all() or peak <= 1e-6:
                    raise RuntimeError(f"Silent or invalid pad {base + local}")
                checks.append({"pad_index": base + local, "peak": peak})
        report["engine_pad_checks"] = checks
        (args.output / "report.json").write_text(json.dumps(report, indent=2))
    window._scans.update(results)
    window.load_clip_into_editor(args.song_id)
    window._apply_scan_to_wave(args.song_id)
    window._rebuild_chips()
    focus_candidates = results[args.song_id]["by_kind"]["snare"] or results[args.song_id]["hits"]
    if focus_candidates:
        focus = focus_candidates[0]
        window.wave.set_selection(focus.start, focus.end)
        window.wave.zoom_to_selection(padding=5)
    window.status.showMessage(
        "A: drum hits · B: song phrases · C: musical phrases · D: tonal chops"
    )
    window.show_tab(window.TAB_CHOP)
    window.setWindowTitle("Anharmonic Studio — clean chop audition")
    window.setAttribute(Qt.WA_ShowWithoutActivating)
    window.resize(1850, 1060)
    window.show()

    def capture():
        window.grab().save(str(args.output / "sampler.png"))
        if not args.show:
            window._dirty = False
            window.close()
            app.quit()

    QTimer.singleShot(1500, capture)
    print(
        json.dumps(
            {"pads": len(placements), "project": str(args.output / "sampler-audition.json")}
        ),
        flush=True,
    )
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
