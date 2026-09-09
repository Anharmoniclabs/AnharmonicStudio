"""Crate filtering, audition routing, and reuse of the live production editors."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mpclab.crates import sample_group
from mpclab.library import Clip, Library
from mpclab.ui.browser import BrowserPanel
from mpclab.ui.crates import SOUND_ROLE, crate_atlas


@pytest.mark.parametrize(
    "name,category,expected",
    [
        ("E808_BD-01", "Hits/Bass Drum [BD]", ("drums", "Kicks")),
        ("TapeSnare_04", "", ("drums", "Snares")),
        ("808_CHH-07", "", ("drums", "Hi-hats")),
        ("BoomBapBreak", "Drum loops", ("drums", "Drum loops")),
        ("Felt", "Instruments/Piano", ("melodic", "Keys & piano")),
        ("Sub bass", "", ("melodic", "Bass")),
        ("StringsLoop", "", ("melodic", "Melodic loops")),
        ("Velvet vox chop", "", ("vocals", "Vocal chops")),
        ("Dust texture", "", ("vinyl", "Textures & FX")),
        ("Kickstart the evening", "", ("vinyl", "Samples & recordings")),
    ],
)
def test_classification_uses_instrument_metadata_without_substring_false_hits(
    name, category, expected
):
    assert sample_group(Clip(id="sample", name=name, category=category)) == expected


@pytest.fixture
def browser(tmp_path, monkeypatch):
    monkeypatch.setattr("mpclab.ui.browser.separate.available", lambda: False)
    library = Library(tmp_path / "library")
    sounds = ("Warm snare", "Tape kick", "Wood percussion", "Felt piano", "Vocal chop", "Mystery")
    library.clips = {
        str(i): Clip(id=str(i), name=name, duration=0.5) for i, name in enumerate(sounds)
    }
    app = SimpleNamespace(library=library, separator=SimpleNamespace(jobs={}))
    panel = BrowserPanel(app)
    panel.resize(300, 850)
    panel.show()
    QApplication.processEvents()
    try:
        yield panel
    finally:
        panel.close()
        panel.deleteLater()


def test_click_crate_lists_real_sounds_in_instrument_order_and_search_composes(browser):
    original = dict(browser.app.library.clips)
    QTest.mouseClick(browser.crate_buttons["drums"], Qt.LeftButton)
    assert len(browser.list.sounds()) == 3
    assert [
        browser.list.sounds()[i].parent().parent().text(0).split("  ·")[0] for i in range(3)
    ] == [
        "Kicks",
        "Snares",
        "Percussion",
    ]
    assert [browser.list.sounds()[i].data(0, Qt.UserRole) for i in range(3)] == ["1", "0", "2"]
    browser.search.setText("snare")
    assert len(browser.list.sounds()) == 1
    assert browser.list.sounds()[0].data(0, SOUND_ROLE)["name"] == "Warm snare"
    browser.search.setText("piano")
    assert len(browser.list.sounds()) == 0
    assert browser.empty_label.isVisible()
    browser.all_crates.click()
    assert len(browser.list.sounds()) == 1
    assert browser.list.sounds()[0].data(0, Qt.UserRole) == "3"
    assert browser.app.library.clips == original


def test_sound_play_icon_and_keyboard_audition_keep_real_clip_id(browser):
    browser.open_crate("drums")
    QApplication.processEvents()
    calls = []
    browser.clipActivated.connect(calls.append)
    item = browser.list.sounds()[0]
    item.parent().parent().setExpanded(True)
    item.parent().setExpanded(True)
    QApplication.processEvents()
    rect = browser.list.visualItemRect(item)
    QTest.mouseClick(
        browser.list.viewport(), Qt.LeftButton, pos=QPoint(rect.left() + 16, rect.bottom() - 12)
    )
    assert calls == ["1"]
    QTest.keyClick(browser.list, Qt.Key_Return)
    assert calls == ["1", "1"]


def test_import_selection_reveals_sound_hidden_by_previous_crate(browser):
    browser.open_crate("drums")
    browser.search.setText("missing")
    browser.refresh(select="4")
    assert browser._crate == "all"
    assert browser.selected_clip_id() == "4"
    assert browser.search.text() == ""
    assert not crate_atlas().isNull()
    assert crate_atlas().hasAlphaChannel()


def test_recording_and_separation_tags_group_even_without_instrument_names():
    assert sample_group(Clip(id="take", name="Take 04", kind="vocal")) == ("vocals", "Vocals")
    assert sample_group(Clip(id="stem", name="Part 1", kind="stem", stem="drums")) == (
        "drums",
        "Drum loops",
    )


def test_studio_reuses_editors_and_keeps_selection_across_focused_tabs(tmp_path, monkeypatch):
    from mpclab.engine import Engine
    from mpclab.ui import main_window
    from scripts.render_studio_preview import PreviewSettings

    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self, device=None: None)
    window = main_window.MainWindow(tmp_path, restore_session=False)
    try:
        assert window.tabs.currentIndex() == 8
        original = window.playlist
        window.playlist.px_per_beat = 37
        assert window.studio.pages[2].parent() is window.studio.arrangement
        window.show_tab(window.TAB_PLAYLIST)
        # Workspace shortcuts select the existing Studio editor; the single
        # navigation shell keeps the page in its permanent dock.
        assert window.tabs.currentIndex() == 8
        assert window.studio.selected == window.TAB_PLAYLIST
        assert window.studio.pages[2].parent() is window.studio.arrangement
        window.tabs.setCurrentIndex(8)
        assert window.playlist is original
        assert window.playlist.px_per_beat == 37
        assert window.studio.pages[3] is window.mixer
        assert window.studio.pages[2].parent() is window.studio.arrangement
        assert window.btn_play.accessibleName() == "Play or pause"
    finally:
        window._dirty = False
        window.close()


@pytest.mark.parametrize(
    "code,folder,instrument",
    [
        ("CL", "Claves [CL]", "Percussion"),
        ("HC", "Congas [HC-MC-LC]", "Percussion"),
        ("CB", "Cowbell [CB]", "Percussion"),
        ("MA", "Maracas [MA]", "Percussion"),
        ("RS", "Rimshot [RS]", "Rimshots"),
        ("CY", "Cymbal [CY]", "Cymbals"),
    ],
)
def test_actual_808_pack_percussion_and_loops(code, folder, instrument):
    assert sample_group(
        Clip(id="hit", kind="pack", name=f"E808_{code}-01", category=f"Hits/{folder}")
    ) == ("drums", instrument)
    assert sample_group(
        Clip(id="loop", kind="pack", name=f"E808_Loop_{code}_105-01", category="Loops/105bpm")
    ) == ("drums", "Drum loops")


def test_stem_tags_override_song_names_and_808_kit_is_bass():
    assert sample_group(
        Clip(id="stem", kind="stem", name="Drums of peace - bass", stem="bass")
    ) == ("melodic", "Bass")
    assert sample_group(Clip(id="kit", kind="kit", name="Trap Kit - 808 ALT")) == (
        "melodic",
        "Bass",
    )
    assert sample_group(Clip(id="record", name="The Piano Song", duration=180)) == (
        "vinyl",
        "Full tracks",
    )


def test_folders_are_not_samples_and_search_preserves_expansion(browser):
    calls = []
    browser.clipActivated.connect(calls.append)
    root = browser.list.topLevelItem(0)
    assert not root.isExpanded()
    browser.list.setCurrentItem(root)
    assert browser.selected_clip_id() is None
    QTest.keyClick(browser.list, Qt.Key_Return)
    assert calls == []
    root.setExpanded(True)
    root.child(0).setExpanded(True)
    browser.search.setText("snare")
    assert browser.list.topLevelItem(0).isExpanded()
    browser.search.clear()
    assert browser.list.topLevelItem(0).isExpanded()
    assert browser.list.topLevelItem(0).child(0).isExpanded()
    assert not browser.filter_panel.isVisible()
    browser.crate_detail_toggle.click()
    QApplication.processEvents()
    assert browser.list.height() > browser.height() * 0.6


def test_pack_folder_provenance_and_natural_number_order(browser):
    library = browser.app.library
    library.clips = {
        str(i): Clip(
            id=str(i),
            name=f"Kick {number}",
            kind="pack",
            pack="808 Pack",
            category="Hits/Bass Drum [BD]",
        )
        for i, number in enumerate((10, 2, 1))
    }
    browser.refresh()
    assert [item.text(0) for item in browser.list.sounds()] == ["Kick 1", "Kick 2", "Kick 10"]
    assert browser.list.sounds()[0].parent().text(0) == "Bass Drum [BD] · 808 Pack  ·  3"


def test_duplicate_recordings_stay_distinct_and_drag_keeps_original_id(browser, monkeypatch):
    clips = {key: Clip(id=key, name="testbeat") for key in ("abcdef123456", "abcdef654321")}
    browser.app.library.clips = clips
    browser.refresh()
    items = browser.list.sounds()
    assert len({item.data(0, SOUND_ROLE)["name"] for item in items}) == 2
    assert all(clip.name == "testbeat" for clip in clips.values())
    drags = []

    class Drag:
        def __init__(self, parent):
            drags.append(self)

        def setMimeData(self, mime):
            self.mime = mime

        def setPixmap(self, pixmap):
            pass

        def exec(self, action):
            return action

    monkeypatch.setattr("mpclab.ui.browser.QDrag", Drag)
    browser.list.setCurrentItem(items[0].parent())
    browser.list.startDrag(Qt.CopyAction)
    assert not drags
    browser.list.setCurrentItem(items[0])
    browser.list.startDrag(Qt.CopyAction)
    assert bytes(drags[0].mime.data("application/x-mpclab-clip")).decode() == items[0].data(
        0, Qt.UserRole
    )
