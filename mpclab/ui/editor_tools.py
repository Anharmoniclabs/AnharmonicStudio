"""Editor toolbars preserve complete control labels in narrow workspaces."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QScrollArea, QFrame, QSizePolicy


def editor_bar(layout):
    inner = QWidget()
    inner.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    for index in range(layout.count()):
        widget = layout.itemAt(index).widget()
        if widget is not None:
            widget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
    inner.setMinimumWidth(inner.sizeHint().width())
    area = QScrollArea()
    area.setFrameShape(QFrame.NoFrame)
    area.setWidgetResizable(True)
    area.setWidget(inner)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    area.setFixedHeight(inner.sizeHint().height() + 12)
    area.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    return area
