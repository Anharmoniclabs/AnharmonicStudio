"""Local vector artwork for the native studio; no network or audio work."""

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QObject, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from . import theme

ASSET_ROOT = Path(__file__).resolve().parents[2] / "assets/studio/interface"
ASSET_NAMES = frozenset(
    "beats sample instruments notes arrange mix record automation identity vinyl kick snare hat bass".split()
)
PAGES = {
    0: ("sample", "Sample", "Trim, chop and audition source audio before mapping it."),
    1: ("beats", "Beats", "Build drum patterns; drop sounds directly onto lane names."),
    2: (
        "arrange",
        "Arrange · Full song",
        "Map patterns, full audio, vocals and sections on one timeline.",
    ),
    3: ("mix", "Mix", "Balance every routed sound, effect send and the master."),
    4: ("instruments", "Instruments", "Choose a playable sound and shape its performance."),
    5: ("record", "Record", "Capture takes, comp performances and place them in Arrange."),
    6: ("notes", "Notes", "Compose synth or chromatic sample notes in the piano roll."),
    7: ("automation", "Automation", "Draw level and pan movement across the song."),
}
INSTRUMENT_ICONS = {
    "Kicks": "kick",
    "Snares": "snare",
    "Rimshots": "snare",
    "Claps": "snare",
    "Hi-hats": "hat",
    "Cymbals": "hat",
    "Toms": "kick",
    "Percussion": "beats",
    "Drum loops": "beats",
    "Bass": "bass",
    "Keys & piano": "instruments",
    "Guitars": "instruments",
    "Strings": "notes",
    "Synths": "instruments",
    "Melodic loops": "notes",
    "Vocals": "record",
    "Vocal chops": "record",
    "Textures & FX": "automation",
    "Full tracks": "vinyl",
}


@lru_cache(maxsize=16)
def _source(name: str) -> bytes:
    if name not in ASSET_NAMES:
        raise ValueError(f"Unknown studio icon: {name}")
    try:
        return (ASSET_ROOT / f"{name}.svg").read_bytes()
    except OSError:
        return b""


@lru_cache(maxsize=64)
def _icon(name: str, normal: str, accent: str, disabled: str) -> QIcon:
    source = _source(name)
    result = QIcon()
    if not source:
        return result
    for mode, state, color in (
        (QIcon.Normal, QIcon.Off, normal),
        (QIcon.Normal, QIcon.On, accent),
        (QIcon.Disabled, QIcon.Off, disabled),
        (QIcon.Disabled, QIcon.On, disabled),
    ):
        renderer = QSvgRenderer(QByteArray(source.replace(b"#d6ab65", color.encode("ascii"))))
        if not renderer.isValid():
            return QIcon()
        for size in (16, 24, 32, 48, 64, 96):
            image = QPixmap(size, size)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            result.addPixmap(image, mode, state)
    return result


def studio_icon(name: str) -> QIcon:
    """Return theme-aware cached artwork. Call only on the GUI thread."""
    return QIcon(_icon(name, theme.C["dim"], theme.C["accent"], theme.C["dim2"]))


class WorkspaceHeader(QWidget):
    """Compact orientation above editors, with a distinct full-song Arrange state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.page = 1
        self.setFixedHeight(60)
        self.setMinimumWidth(0)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.set_page(1)

    def set_page(self, page: int) -> None:
        if page not in PAGES:
            return
        self.page = page
        self.setAccessibleName(PAGES[page][1])
        self.setAccessibleDescription(PAGES[page][2])
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), theme.q("bg2"))
        p.fillRect(0, self.height() - 1, self.width(), 1, theme.q("line"))
        p.fillRect(0, 11, 3, 38, theme.q("accent"))
        studio_icon(PAGES[self.page][0]).paint(
            p, 14, 16, 28, 28, Qt.AlignCenter, QIcon.Normal, QIcon.On
        )

        right = 190 if self.width() >= 780 else 12
        text_width = max(0, self.width() - 56 - right)
        p.setFont(theme.base_font(11.5))
        p.setPen(theme.q("fg"))
        title = p.fontMetrics().elidedText(PAGES[self.page][1], Qt.ElideRight, text_width)
        p.drawText(QRectF(56, 8, text_width, 23), Qt.AlignVCenter, title)
        p.setFont(theme.base_font(8.5))
        p.setPen(theme.q("dim"))
        caption = p.fontMetrics().elidedText(PAGES[self.page][2], Qt.ElideRight, text_width)
        p.drawText(QRectF(56, 32, text_width, 17), Qt.AlignVCenter, caption)

        if self.width() >= 780:
            if self.page == 2:
                pill = QRectF(self.width() - 176, 17, 86, 25)
                p.setPen(theme.q("accent"))
                p.setBrush(theme.q("accent", 28))
                p.drawRoundedRect(pill, 6, 6)
                p.setFont(theme.label_font(7.5, True))
                p.setPen(theme.q("accent_hi"))
                p.drawText(pill, Qt.AlignCenter, "FULL SONG")
                studio_icon("identity").paint(p, self.width() - 78, 18, 22, 22)
            else:
                studio_icon("identity").paint(p, self.width() - 154, 18, 22, 22)
                p.setFont(theme.label_font(8, True))
                p.setPen(theme.q("dim"))
                p.drawText(QRectF(self.width() - 123, 16, 113, 26), Qt.AlignVCenter, "ANHARMONIC")
        p.end()


class WorkspaceVisuals(QObject):
    """Refresh decoration when selection, palette, or stylesheet changes."""

    def __init__(self, studio, more):
        super().__init__(studio)
        self.studio = studio
        self.more = more
        self.header = WorkspaceHeader(studio)
        studio.layout().insertWidget(0, self.header)
        studio.installEventFilter(self)
        self.refresh()

    def refresh(self):
        self.header.set_page(self.studio.selected)
        for index, (name, title, description) in PAGES.items():
            icon = studio_icon(name)
            self.studio.tabs.setTabIcon(index, icon)
            button = self.studio.buttons.get(index)
            if button is not None:
                button.setIcon(icon)
                button.setIconSize(QSize(18, 18))
                button.setAccessibleName(title)
                button.setToolTip(description)
        self.more.setIcon(studio_icon("automation"))
        self.more.setIconSize(QSize(16, 16))
        self.studio.create_beat.setIcon(studio_icon("beats"))
        self.studio.arrange_pattern.setIcon(studio_icon("arrange"))

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.StyleChange, QEvent.PaletteChange):
            self.refresh()
        return False
