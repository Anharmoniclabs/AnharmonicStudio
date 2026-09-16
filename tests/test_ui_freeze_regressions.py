# Regression coverage for observed Qt event/paint freeze storms.

from PySide6.QtCore import QEvent
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog

from mpclab.prism_motion import HAND_EFFECTS
from mpclab.ui.prism_camera import HandPreview, PLACEHOLDER_TIPS
from mpclab.ui.typing_keyboard import TypingKeyboardWindow

from tests.test_product_hardening_ui import window  # noqa: F401


def test_prism_whole_hand_placeholder_matches_effect_count(window):  # noqa: F811
    assert len(PLACEHOLDER_TIPS) == len(HAND_EFFECTS)
    preview = HandPreview()
    preview.resize(420, 230)
    pixmap = QPixmap(preview.size())
    preview.render(pixmap)
    assert not pixmap.isNull()
    preview.deleteLater()


def test_musical_typing_filter_tolerates_window_method_shadow(window):  # noqa: F811
    keyboard = TypingKeyboardWindow(window)
    keyboard.show()
    dialog = QDialog(window)
    # Reproduce the old failure: several Qt helpers used this attribute name.
    dialog.window = window
    try:
        assert keyboard.eventFilter(dialog, QEvent(QEvent.User)) is False
    finally:
        keyboard.close()
        dialog.close()
