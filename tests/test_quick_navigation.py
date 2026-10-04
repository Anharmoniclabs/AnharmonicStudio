"""Exercise real QML input, state sync and recovery without an audio device."""

from PySide6.QtCore import Qt, QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mpclab.ui.quick_navigation import QuickNavigation
from tests.test_studio_unified_layout import _studio


def click_item(view, name):
    pending = [view.rootObject()]
    item = None
    while pending:
        candidate = pending.pop()
        if candidate.objectName() == name:
            item = candidate
            break
        pending.extend(candidate.childItems())
    assert item is not None
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(view, Qt.LeftButton, pos=point)
    QApplication.processEvents()


def test_qml_tabs_and_compact_picker_drive_the_same_editors():
    studio, tabs, pages = _studio()
    studio.resize(1000, 500)
    studio.show()
    QTest.qWait(20)
    quick = studio.quick_navigation
    assert quick.ready, [error.toString() for error in quick.errors()]
    parents = [page.parent() for page in pages]
    click_item(quick, "workspace_4")
    assert studio.selected == 4
    assert studio.stack.currentWidget() is studio.docks[4]
    studio.resize(480, 500)
    QApplication.processEvents()
    click_item(quick, "workspacePicker")
    QTest.keyClick(quick, Qt.Key_Home)
    QTest.keyClick(quick, Qt.Key_Return)
    QApplication.processEvents()
    assert studio.selected == 2
    assert [page.parent() for page in pages] == parents
    studio.select(6)
    assert quick.rootObject().property("selectedPage") == 6
    studio.close()
    tabs.close()


def test_scene_failure_restores_working_widget_navigation():
    studio, tabs, _pages = _studio()
    studio.show()
    studio.quick_navigation.unavailable.emit("graphics backend unavailable")
    QApplication.processEvents()
    assert studio.quick_navigation.isHidden()
    assert studio.mode_scroll.isVisible()
    studio.buttons[4].click()
    assert studio.selected == 4
    studio.close()
    tabs.close()


def test_missing_qml_is_reported_without_crashing(tmp_path):
    quick = QuickNavigation([], source=tmp_path / "missing.qml")
    assert not quick.ready
    assert quick.errors()
    quick.close()
