"""Small-display controls remain reachable, including nested editor/dialog overflow."""

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QPushButton, QLabel
import pytest

from mpclab.engine import Engine
from mpclab.ui.main_window import MainWindow
from mpclab.ui.workspace_fit import inspect_hardware, fit_to_screen


class Screen:
    def __init__(self, width, height, scale=1):
        self.rect = QRect(-width, 0, width, height)
        self.scale = scale

    def availableGeometry(self):
        return self.rect

    def devicePixelRatio(self):
        return self.scale


@pytest.mark.parametrize(
    "width,height,scale", [(1024, 600, 1), (1366, 768, 1), (1280, 720, 2), (1920, 1080, 1)]
)
def test_probe_uses_logical_work_area_once(width, height, scale):
    profile = inspect_hardware(Screen(width, height, scale))
    assert profile.width == width and profile.height == height
    assert profile.scale == scale
    assert profile.initial_size[0] <= width and profile.initial_size[1] < height
    assert profile.cpu_threads >= 1
    assert profile.compact == (width < 1440 or height < 850)


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, "start", lambda self: None)
    window = MainWindow(tmp_path, restore_session=False)
    window.show()
    QApplication.processEvents()
    yield window
    window._dirty = False
    window.close()


def test_all_editors_scroll_instead_of_compressing_controls(window):
    parents = {index: page.parent() for index, page in window.studio.pages.items()}
    for width, height in ((1024, 600), (1366, 704), (1920, 1000), (800, 560)):
        window.resize(width, height)
        for index, page in window.studio.pages.items():
            window.studio.select(index)
            QApplication.processEvents()
            dock = window.studio.docks[index]
            assert page.parent() is parents[index]
            assert page.width() >= page.minimumSizeHint().width()
            assert page.height() >= page.minimumSizeHint().height()
            assert dock.verticalScrollBar().maximum() >= page.height() - dock.viewport().height()
        if width < 1100:
            assert window.browser_frame.isHidden() and window.pad_side.isHidden()


def test_dialog_footer_reachable_on_small_negative_origin_monitor(window):
    dialog = QDialog(window)
    layout = QVBoxLayout(dialog)
    label = QLabel("Large content")
    label.setMinimumSize(1100, 800)
    layout.addWidget(label)
    button = QPushButton("Done")
    clicked = []
    button.clicked.connect(lambda: clicked.append(True))
    layout.addWidget(button)
    dialog.resize(1400, 1000)
    window.workspace_fit.screen = Screen(1024, 600)
    window.workspace_fit.fit_dialog(dialog)
    dialog.show()
    QApplication.processEvents()
    fit_to_screen(dialog, window.workspace_fit.screen)
    assert dialog.width() <= 1000 and dialog.height() <= 544
    scroll = dialog.layout().itemAt(0).widget()
    scroll.ensureWidgetVisible(button)
    QApplication.processEvents()
    assert (
        scroll.viewport().rect().contains(button.mapTo(scroll.viewport(), button.rect().center()))
    )
    button.click()
    assert clicked == [True]
    dialog.close()
    window.workspace_fit.screen = None


def test_known_808_pitch_applies_only_to_new_pad(window):
    clip = next(c for c in window.library.clips.values() if c.root_note == 24)
    window.assign_sample_to_pad(0, clip.id)
    assert window.project.pads[0].root_note == 24
    window.project.pads[0].root_note = 33
    window.assign_sample_to_pad(0, clip.id)
    assert window.project.pads[0].root_note == 33
    window.studio.kit.setCurrentIndex(3)
    window.create_factory_beat()
    assert window.project.pattern().name == "Trap Foundry groove"
    assert len(window.project.pattern().steps) == 8
    assert window.project.pads[0].sample_id == clip.id


def test_transport_overflow_stays_available_in_focus_mode(window):
    window.resize(800, 560)
    for focus in (False, True, False):
        window.set_playlist_focus(focus)
        QApplication.processEvents()
        assert not window.transport_more.isHidden()
        menu = window.transport_more.menu()
        menu.popup(window.transport_more.mapToGlobal(window.transport_more.rect().bottomLeft()))
        QApplication.processEvents()
        for control in (window.audio_buffer, window.btn_audio_retry, window.master_slider):
            assert control.isVisible()
        window.master_slider.setValue(73)
        assert window.project.master == pytest.approx(0.73)
        menu.close()
        assert window.transport_bar.sizeHint().width() <= window.width()
