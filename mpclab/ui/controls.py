"""Crisp native-painted transport symbols, independent of installed symbol fonts."""

from functools import lru_cache

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPainterPath, QColor


@lru_cache(maxsize=24)
def transport_icon(kind, color):
    image = QPixmap(36, 36)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    if kind == "play":
        path = QPainterPath()
        path.moveTo(11, 6)
        path.lineTo(29, 18)
        path.lineTo(11, 30)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "pause":
        p.drawRoundedRect(QRectF(8, 7, 7, 22), 1, 1)
        p.drawRoundedRect(QRectF(21, 7, 7, 22), 1, 1)
    elif kind == "stop":
        p.drawRoundedRect(QRectF(8, 8, 20, 20), 2, 2)
    else:
        p.drawEllipse(QRectF(7, 7, 22, 22))
    p.end()
    return QIcon(image)
