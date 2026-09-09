"""Record-crate artwork and efficient, keyboard-accessible sound-list rendering."""

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QPainter, QPen, QPixmap, QPainterPath
from PySide6.QtWidgets import QPushButton, QSizePolicy, QStyledItemDelegate, QStyle

from .theme import q, label_font, base_font, mono_font
from .visual_assets import INSTRUMENT_ICONS, studio_icon

SOUND_ROLE = int(Qt.UserRole) + 1
GROUP_ROLE = int(Qt.UserRole) + 2
COUNT_ROLE = int(Qt.UserRole) + 3


@lru_cache(maxsize=1)
def crate_atlas():
    source = Path(__file__).resolve().parents[2] / "assets/studio/vinyl-crates.png"
    # Decode/downsample once on the UI thread. No repaint touches the disk.
    return QPixmap(str(source)).scaled(512, 512, Qt.KeepAspectRatio, Qt.SmoothTransformation)


class CrateButton(QPushButton):
    def __init__(self, index, title, parent=None):
        super().__init__(title, parent)
        self.index = index
        self.count = 0
        self.detailed = True
        self._atlas = crate_atlas()
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(112)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(0)
        self.setAccessibleName(f"{title.title()} record crate")

    def set_detailed(self, detailed):
        self.detailed = bool(detailed)
        self.setFixedHeight(112 if self.detailed else 32)
        self.update()

    def set_count(self, count):
        self.count = count
        self.setToolTip(f"Open {self.text().lower()} · {count} sounds organized by instrument")
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(q("item_sel" if self.isChecked() else "bg3" if self.underMouse() else "bg2"))
        p.setPen(QPen(q("accent" if self.hasFocus() else "line"), 1))
        p.drawRoundedRect(rect, 2, 2)
        if self.isChecked():
            p.fillRect(QRectF(1, 6, 2, self.height() - 12), q("accent"))
        atlas = self._atlas if self.detailed else QPixmap()
        if not atlas.isNull():
            side = min(self.width() - 16, 82)
            tile = atlas.width() / 2
            p.drawPixmap(
                QRectF((self.width() - side) / 2, 3, side, side),
                atlas,
                QRectF((self.index % 2) * tile, (self.index // 2) * tile, tile, tile),
            )
        p.setFont(base_font(9.5))
        p.setPen(q("accent_hi" if self.isChecked() else "fg"))
        title = p.fontMetrics().elidedText(self.text().title(), Qt.ElideRight, self.width() - 45)
        label_y = 84 if self.detailed else 3
        p.drawText(QRectF(9, label_y, self.width() - 45, 26), Qt.AlignVCenter, title)
        p.setFont(mono_font(8.5))
        p.setPen(q("dim"))
        p.drawText(
            QRectF(self.width() - 36, label_y, 28, 26),
            Qt.AlignRight | Qt.AlignVCenter,
            str(self.count),
        )


class SoundDelegate(QStyledItemDelegate):
    """Compact folders and sound rows; preview controls only belong to sounds."""

    def sizeHint(self, option, index):
        return QSize(120, 30)

    def paint(self, painter, option, index):
        data = index.data(SOUND_ROLE)
        if not data:
            painter.save()
            painter.fillRect(
                option.rect, q("item_sel" if option.state & QStyle.State_Selected else "bg2")
            )
            painter.setFont(label_font(8, True))
            painter.setPen(q("accent_hi" if not index.parent().isValid() else "dim"))
            title = painter.fontMetrics().elidedText(
                str(index.data() or ""), Qt.ElideRight, option.rect.width() - 8
            )
            painter.drawText(option.rect.adjusted(3, 0, -5, 0), Qt.AlignVCenter, title)
            painter.restore()
            return
        p = painter
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        p.fillRect(rect, q("item_sel" if selected else "item_hover" if hover else "bg2"))
        if selected:
            p.fillRect(QRectF(rect.x(), rect.y() + 4, 2, rect.height() - 8), q("accent"))
        play = QRectF(rect.x() + 9, rect.center().y() - 8, 16, 16)
        p.setPen(QPen(q("edge"), 1))
        p.setBrush(q("bg3"))
        p.drawRoundedRect(play, 3, 3)
        triangle = QPainterPath()
        triangle.moveTo(play.x() + 6, play.y() + 4)
        triangle.lineTo(play.x() + 11, play.y() + 8)
        triangle.lineTo(play.x() + 6, play.y() + 12)
        triangle.closeSubpath()
        p.fillPath(triangle, q("accent_hi" if selected else "dim"))
        p.setFont(base_font(9))
        p.setPen(q("accent_hi" if selected else "fg"))
        title_left = 34
        if rect.width() >= 210:
            name = INSTRUMENT_ICONS.get(data.get("instrument"), "sample")
            studio_icon(name).paint(p, int(rect.x()) + 32, int(rect.center().y()) - 8, 16, 16)
            title_left = 55
        title_rect = rect.adjusted(title_left, 0, -49, 0)
        title = p.fontMetrics().elidedText(data["name"], Qt.ElideRight, int(title_rect.width()))
        p.drawText(title_rect, Qt.AlignVCenter, title)
        p.setFont(mono_font(7.5))
        p.setPen(q("dim2"))
        seconds = data["duration"]
        duration = (
            f"{seconds:.1f}s" if seconds < 60 else f"{int(seconds // 60)}:{int(seconds % 60):02d}"
        )
        p.drawText(
            rect.adjusted(rect.width() - 49, 0, -8, 0), Qt.AlignRight | Qt.AlignVCenter, duration
        )
        p.restore()
