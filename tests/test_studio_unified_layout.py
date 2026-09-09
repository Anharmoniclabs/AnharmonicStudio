"""Regression coverage for the stable Studio ownership model.

The visual shell may change, but live editors must never be moved between
containers while the user navigates or resizes Studio. Reparenting those heavy
Qt editors was the source of lost focus, disappearing chop controls and layout
churn during the UI revamp.
"""

from PySide6.QtWidgets import QApplication, QLabel, QTabWidget

from mpclab.ui.studio import StudioPanel


def _studio():
    tabs = QTabWidget()
    pages = []
    for index in range(8):
        page = QLabel(f"page {index}")
        pages.append(page)
        tabs.addTab(page, f"Tab {index}")
    studio = StudioPanel(tabs)
    studio.activate(True)
    return studio, tabs, pages


def test_navigation_never_reparents_live_editors():
    studio, tabs, pages = _studio()
    try:
        expected_parents = {index: studio.docks[index] for index in range(8)}
        for index in (0, 1, 2, 3, 6, 4, 7, 5, 0, 2, 1):
            studio.select(index)
            QApplication.processEvents()
            assert studio.stack.currentWidget() is studio.docks[index]
            for page_index, page in enumerate(pages):
                assert page.parent() is expected_parents[page_index]
    finally:
        studio.deleteLater()
        tabs.deleteLater()


def test_sampler_chop_workspace_survives_repeated_resize_and_mode_switches():
    studio, tabs, pages = _studio()
    try:
        sampler = pages[0]
        sampler_parent = studio.docks[0]
        for width in (1680, 1180, 900, 1440, 760, 1280):
            studio.resize(width, 820)
            for index in (2, 1, 3, 6, 0):
                studio.select(index)
            QApplication.processEvents()
            assert sampler.parent() is sampler_parent
            assert studio.stack.indexOf(sampler_parent) >= 0

        studio.select(0)
        assert studio.stack.currentWidget() is sampler_parent
        assert sampler.isVisibleTo(studio)
    finally:
        studio.deleteLater()
        tabs.deleteLater()


def test_more_menu_keeps_all_workflow_actions():
    studio, tabs, _pages = _studio()
    try:
        menu = studio.more_button.menu()
        assert menu is not None
        assert [action.text() for action in menu.actions()] == [
            "Record vocals in Song",
            "Automation",
            "Master output",
            "Musical typing",
            "Song / beat tools",
            "Workflow tips",
        ]
    finally:
        studio.deleteLater()
        tabs.deleteLater()


def test_activate_cycle_has_exactly_one_owner_per_editor():
    studio, tabs, pages = _studio()
    try:
        studio.activate(False)
        QApplication.processEvents()
        for index, page in enumerate(pages):
            assert page.parent() is studio.holders[index]

        studio.activate(True)
        QApplication.processEvents()
        for index, page in enumerate(pages):
            assert page.parent() is studio.docks[index]
    finally:
        studio.deleteLater()
        tabs.deleteLater()
