"""Shared Qt layout primitives; no workstation or audio ownership."""

from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QScrollArea,
    QFrame,
    QSizePolicy,
)


def scrolling_bar(inner: QWidget) -> QScrollArea:
    """Let a dense toolbar scroll sideways instead of crushing its own labels.

    A narrow window used to squeeze `MAP RANGE → PAD A1` down to `ANGE → P`.
    Holding every control at its natural width and scrolling the surplus keeps
    the app readable beside another window, which is how it is meant to be used.
    """
    area = QScrollArea()
    area.setObjectName("toolbarScroll")
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    area.setWidget(inner)
    inner.setMinimumWidth(inner.sizeHint().width())
    area.setFixedHeight(inner.sizeHint().height() + 11)
    area.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    return area


def separator() -> QFrame:
    """A hairline that groups a toolbar into readable clusters."""
    line = QFrame()
    line.setObjectName("sep")
    line.setFrameShape(QFrame.VLine)
    line.setFixedWidth(1)
    return line


def header(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("header")
    return lab


def small(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("hint")
    return lab


def yielding(widget: QWidget) -> QWidget:
    """Let a widget be squeezed before its toolbar resorts to scrolling.

    Hints and readouts are the first things that should give up room; the
    controls beside them are not.
    """
    widget.setSizePolicy(QSizePolicy.Ignored, widget.sizePolicy().verticalPolicy())
    widget.setMinimumWidth(0)
    return widget
