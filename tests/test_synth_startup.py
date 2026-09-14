"""First-note callback checks must run before another test warms NumPy."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("entry", ["keyboard", "pattern", "arp"])
def test_first_synth_notes_do_not_import_or_open_files_in_callback(entry):
    result = subprocess.run(
        [sys.executable, "-c", COLD_NOTES, entry],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    report = json.loads(result.stdout)
    assert report["callback_io"] == [], report
    assert report["peak"] > 0.001
    assert report["tail_peak"] == 0.0
    assert not report["device_imported"]


COLD_NOTES = r"""
import json
import sys
import numpy as np
from mpclab.engine import Engine
from mpclab.music import Note

class Library:
    def audio(self, sample_id):
        return None

engine = Engine(Library(), sample_rate=48000, blocksize=512)
engine.project.synth.noise = 0.17
engine.project.synth.release = 0.01
entry = sys.argv[1]
notes = (48, 52, 55, 60, 64, 67, 72, 76)
if entry == "pattern":
    engine.project.pattern().notes = [Note(n, 0, 0.125, 0.7) for n in notes]
    engine.playing = True
else:
    engine.project.arp.enabled = entry == "arp"
    for note in notes:
        engine.synth_note_on(note, 0.7)

inside_callback = False
events = []
def audit(event, args):
    if inside_callback and event in ("import", "open"):
        events.append([event, str(args[0])])
sys.addaudithook(audit)

audio = np.zeros((96, 512, 2), dtype=np.float32)
for index, block in enumerate(audio):
    if index == 32 and entry != "pattern":
        for note in notes:
            engine.synth_note_off(note)
    inside_callback = True
    try:
        engine._callback(block, 512, None, False)
    finally:
        inside_callback = False
assert np.isfinite(audio).all()
print(json.dumps(dict(callback_io=events, peak=float(np.max(np.abs(audio))),
    tail_peak=float(np.max(np.abs(audio[-8:]))), device_imported="sounddevice" in sys.modules)))
"""
