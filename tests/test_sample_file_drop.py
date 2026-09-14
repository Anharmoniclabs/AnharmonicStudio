"""Real file-manager MIME events, managed import, bank safety and pack browsing."""

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent

from mpclab.engine import Engine
from mpclab.model import Project
from mpclab.music import Note
from mpclab.ui import main_window
from mpclab.ui.sample_file_drop import local_audio_paths, send_local_files
from mpclab.ui.sequencer import GAP, ROW_H, RULER_H
from scripts.render_studio_preview import PreviewSettings


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self: None)
    w = main_window.MainWindow(tmp_path, restore_session=False)
    yield w
    w._dirty = False
    w.close()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture
def sounds(tmp_path):
    root = tmp_path / "Trap pack with spaces"
    root.mkdir()
    data = (np.sin(np.arange(4800) * 0.03) * 0.1).astype(np.float32)
    paths = [root / "Kick 01.WAV", root / "808 Bass.wav"]
    for path in paths:
        sf.write(path, data, 48000)
    return paths


def file_mime(paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    return mime


def drop(widget, mime, pos):
    enter = QDragEnterEvent(pos, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QCoreApplication.sendEvent(widget, enter)
    event = QDropEvent(QPointF(pos), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QCoreApplication.sendEvent(widget, event)
    return event


def test_paths_keep_spaces_uppercase_and_deduplicate(sounds):
    assert local_audio_paths(file_mime(sounds + sounds[:1])) == tuple(sounds)


@pytest.mark.parametrize("kind", ["zip", "directory", "missing", "remote", "network", "mixed"])
def test_invalid_sources_rejected_without_import(tmp_path, sounds, kind):
    candidate = tmp_path / "pack.zip"
    candidate.write_bytes(b"not audio")
    mime = file_mime([candidate])
    if kind == "directory":
        mime = file_mime([sounds[0].parent])
    elif kind == "missing":
        mime = file_mime([tmp_path / "missing.wav"])
    elif kind == "remote":
        mime.setUrls([QUrl("https://example.com/sound.wav")])
    elif kind == "network":
        mime.setUrls([QUrl("file://server/share/sound.wav")])
    elif kind == "mixed":
        mime = file_mime([sounds[0], candidate])
    assert local_audio_paths(mime) == ()


def test_excessive_drop_rejected(sounds):
    assert local_audio_paths(file_mime([sounds[0]] * 17)) == ()


def test_lane_file_drop_imports_and_preserves_rhythm(window, sounds, tmp_path):
    window.project.pattern().steps[0] = {0: 0.7, 4: 0.4}
    window.project.pads[0].track = 3
    window.project.pads[0].gain = 0.42
    before = window.project.to_dict()
    source_bytes = sounds[0].read_bytes()
    event = drop(window.step_grid, file_mime(sounds[:1]), QPoint(40, RULER_H + 12))
    assert event.isAccepted() and event.dropAction() == Qt.CopyAction
    pad = window.project.pads[0]
    assert pad.sample_id in window.library.clips
    assert pad.name == "Kick 01" and pad.track == 3 and pad.gain == 0.42
    assert window.project.pattern().steps[0] == {0: 0.7, 4: 0.4}
    assert window.library.wav_path(pad.sample_id).is_file()
    assert sounds[0].read_bytes() == source_bytes
    saved = tmp_path / "drop-project.json"
    window.project.save(saved)
    assert Project.load(saved).pads[0].sample_id == pad.sample_id
    window.undo()  # assignment
    assert window.project.to_dict() == before
    window.undo()  # managed import
    assert pad.sample_id not in window.library.clips
    window.redo()
    window.redo()
    assert window.project.pads[0].sample_id in window.library.clips


def test_batch_footer_avoids_reserved_slots(window, sounds):
    window.project.pattern().notes = [Note(pad=0)]
    bottom = RULER_H + len(window.step_grid.lanes()) * (ROW_H + GAP)
    event = drop(window.step_grid, file_mime(sounds), QPoint(40, bottom + 12))
    assert event.isAccepted()
    assert window.project.pads[0].empty
    assert [window.project.pads[i].name for i in (1, 2)] == ["Kick 01", "808 Bass"]


def test_multiple_files_cannot_overwrite_one_lane(window, sounds):
    before = window.project.to_dict()
    event = drop(window.step_grid, file_mime(sounds), QPoint(40, RULER_H + 12))
    assert not event.isAccepted()
    assert window.project.to_dict() == before and not window.library.clips


def test_full_bank_does_not_even_import_files(window, sounds):
    window.project.pattern().notes = [Note(pad=i) for i in range(16)]
    assert not send_local_files(window, sounds, destination="beats")
    assert not window.library.clips and not window._undo


@pytest.mark.parametrize("target", [1, 6])
def test_os_drop_on_workspace_tab(window, sounds, target):
    button = window.studio.buttons[target]
    event = drop(button, file_mime(sounds[:1]), QPoint(8, 8))
    assert event.isAccepted()
    assert window.project.pads[0].name == "Kick 01"
    if target == 6:
        assert window.piano_roll.target_pad == 0
        assert window.project.pads[0].mode == "gate"


def test_drag_enter_does_not_import(window, sounds):
    mime = file_mime(sounds[:1])
    event = QDragEnterEvent(
        QPoint(40, RULER_H + 12), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
    )
    QCoreApplication.sendEvent(window.step_grid, event)
    assert event.isAccepted()
    assert not window.library.clips and not window._undo
    QCoreApplication.sendEvent(window.step_grid, QEvent(QEvent.DragLeave))
    assert not window.step_grid.property("sampleDropActive")


def test_add_pack_folder_indexes_originals(window, sounds, monkeypatch):
    from mpclab.ui import browser

    monkeypatch.setattr(
        browser.QFileDialog, "getExistingDirectory", lambda *args: str(sounds[0].parent)
    )
    window.browser.add_pack.click()
    assert len(window.library.clips) == 2
    assert all(c.kind == "pack" for c in window.library.clips.values())
    assert {Path(c.source_path) for c in window.library.clips.values()} == set(sounds)
    assert window.browser.source_filter.currentData() == "packs"
    assert window.browser.list.topLevelItemCount() > 0
    window.library.scan()
    assert len(window.library.clips) == 2


def test_cancel_pack_folder_is_noop(window, monkeypatch):
    from mpclab.ui import browser

    monkeypatch.setattr(browser.QFileDialog, "getExistingDirectory", lambda *args: "")
    window.browser.add_pack.click()
    assert not window.library.packs_path.exists()
