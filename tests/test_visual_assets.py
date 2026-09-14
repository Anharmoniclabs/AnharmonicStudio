"""Theme-aware vector artwork remains readable, responsive and optional."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QTabWidget

from mpclab.ui import theme
from mpclab.ui.studio import StudioPanel
from mpclab.ui.visual_assets import (
    ASSET_NAMES,
    PAGES,
    WorkspaceHeader,
    _source,
    brand_pixmap,
    studio_icon,
)


def test_signature_renders_at_high_dpi_and_follows_project_accent():
    original_accent = theme.C["accent"]
    try:
        theme.set_theme("dark")
        theme.set_accent("#427BFF")
        blue = brand_pixmap(2.0)
        assert not blue.isNull()
        assert blue.devicePixelRatio() == 2.0
        assert (blue.width(), blue.height()) == (436, 88)
        theme.set_accent("#E65A91")
        pink = brand_pixmap(2.0)
        assert blue.toImage() != pink.toImage()
        theme.set_theme("light")
        assert pink.toImage() != brand_pixmap(2.0).toImage()
    finally:
        theme.set_theme("dark")
        theme.set_accent(original_accent)


@pytest.fixture
def workspace(qtbot=None):
    tabs = QTabWidget()
    pages = []
    for index in range(8):
        page = QLabel(f"page {index}")
        pages.append(page)
        tabs.addTab(page, f"Tab {index}")
    studio = StudioPanel(tabs)
    studio.activate(True)
    yield studio, pages
    studio.deleteLater()
    tabs.deleteLater()


def test_every_declared_asset_exists_and_parses():
    for name in sorted(ASSET_NAMES):
        path = (
            Path(__file__).resolve().parents[1] / "assets" / "studio" / "interface" / f"{name}.svg"
        )
        assert path.is_file(), name
        data = path.read_bytes()
        assert b"<svg" in data and b"#d6ab65" in data
        assert _source(name) == data
        assert not studio_icon(name).isNull()


def test_unknown_or_missing_optional_artwork_fails_softly(monkeypatch):
    with pytest.raises(ValueError):
        _source("does-not-exist")

    _source.cache_clear()
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(OSError("missing")))
    assert _source("beats") == b""
    _source.cache_clear()


def test_icons_follow_theme_and_include_high_dpi_sizes():
    theme.set_theme("dark")
    dark = studio_icon("beats")
    theme.set_theme("light")
    light = studio_icon("beats")
    assert not dark.isNull() and not light.isNull()
    assert dark.cacheKey() != light.cacheKey()
    for icon in (dark, light):
        sizes = {(size.width(), size.height()) for size in icon.availableSizes()}
        assert (16, 16) in sizes and (48, 48) in sizes and (96, 96) in sizes


def test_workspace_icons_navigation_and_persistent_pages(workspace):
    studio, pages = workspace
    for index, button in studio.buttons.items():
        button.click()
        assert studio.selected == index
        assert studio.visuals.header.page == index
        assert not button.icon().isNull()
        assert studio.pages[index] is pages[index]
        assert pages[index].parent() is studio.docks[index]
    for index in (5, 7):
        studio.select(index)
        assert studio.visuals.header.accessibleName() == PAGES[index][1]
    studio.activate(False)
    assert all(pages[i].parent() is studio.holders[i] for i in range(8))


@pytest.mark.parametrize("mode", ("light", "dark"))
@pytest.mark.parametrize("width", (480, 640, 1100))
def test_narrow_header_and_navigation_render(workspace, mode, width):
    studio, _ = workspace
    theme.set_theme(mode)
    studio.setStyleSheet(theme.stylesheet())
    studio.resize(width, 500)
    QApplication.processEvents()
    assert studio.width() == width
    assert studio.visuals.header.width() <= width
    assert studio.visuals.header.height() == 60
    assert not studio.grab().isNull()
    assert studio.buttons[1].icon().cacheKey() == studio_icon("beats").cacheKey()


@pytest.mark.parametrize("width", (240, 360))
def test_header_itself_elides_at_small_width(workspace, width):
    header = WorkspaceHeader()
    try:
        header.resize(width, 60)
        header.set_page(6)
        assert header.width() == width
        assert not header.grab().isNull()
    finally:
        header.deleteLater()
