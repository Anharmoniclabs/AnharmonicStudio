"""Sample browser, graphical clip badges, and local stem-separation drawer."""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

from .window_client import WindowClient

from PySide6.QtCore import Qt, QTimer, Signal, QMimeData, QRectF
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDrag,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QComboBox,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QProgressBar,
    QFrame,
    QMenu,
    QGridLayout,
    QButtonGroup,
)

from .. import APP_NAME
from ..library import AUDIO_EXT
from .. import separate
from ..crates import CRATES, INSTRUMENTS, sample_group, sample_location, natural_key
from .crates import CrateButton, SoundDelegate, SOUND_ROLE, GROUP_ROLE, COUNT_ROLE
from .theme import q, C


def fmt_time(s: float) -> str:
    return f"{int(s // 60)}:{int(s % 60):02d}"


def _clip_icon(kind: str) -> QIcon:
    """Small native-painted waveform badge; it follows the live palette."""
    pix = QPixmap(22, 22)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    rect = QRectF(1.5, 1.5, 19, 19)
    painter.setPen(QPen(q("accent" if kind == "stem" else "line"), 1))
    painter.setBrush(q("item_sel" if kind == "stem" else "bg3"))
    painter.drawRoundedRect(rect, 5, 5)
    colour = q("accent_hi" if kind == "stem" else "ok" if kind == "render" else "dim")
    painter.setPen(QPen(colour, 1.35, Qt.SolidLine, Qt.RoundCap))
    mid = rect.center().y()
    if kind == "stem":
        for i, height in enumerate((4, 9, 6, 12, 7)):
            x = 5.0 + i * 3.0
            painter.drawLine(int(x), int(mid - height / 2), int(x), int(mid + height / 2))
    elif kind == "render":
        path = QPainterPath()
        path.moveTo(7, 6)
        path.lineTo(16, 11)
        path.lineTo(7, 16)
        path.closeSubpath()
        painter.setBrush(colour)
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)
    else:
        points = ((4, 11), (6, 8), (8, 13), (10, 6), (12, 15), (14, 9), (16, 12), (18, 10))
        path = QPainterPath()
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)
        painter.drawPath(path)
    painter.end()
    return QIcon(pix)


class StemDeck(QWidget):
    """Compact visual feedback for the separated instrument lanes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "ready"
        self.progress = 0.0
        self.stems = ("drums", "bass", "other", "vocals")
        self.setFixedHeight(72)
        self.setToolTip("Demucs splits the selected song into independent audio clips")

    def set_status(
        self, state: str, progress: float = 0.0, stems: tuple[str, ...] | list[str] | None = None
    ) -> None:
        self.state = state
        self.progress = min(1.0, max(0.0, float(progress)))
        if stems:
            self.stems = tuple(stems)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        card = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        gradient = QLinearGradient(card.topLeft(), card.bottomLeft())
        gradient.setColorAt(0.0, q("bg3"))
        gradient.setColorAt(1.0, q("sunken"))
        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(q("accent" if self.state == "running" else "line"), 1))
        painter.drawRoundedRect(card, 7, 7)

        left = 43.0
        right = card.right() - 7.0
        top = card.top() + 7.0
        lane_h = (card.height() - 14.0) / max(1, len(self.stems))
        active_x = left + (right - left) * self.progress
        labels = {
            "drums": "DRM",
            "bass": "BAS",
            "other": "MUS",
            "vocals": "VOX",
            "guitar": "GTR",
            "piano": "PNO",
        }

        for lane, stem in enumerate(self.stems):
            centre = top + lane_h * (lane + 0.5)
            painter.setPen(q("dim2"))
            painter.drawText(
                QRectF(6, centre - lane_h / 2, left - 11, lane_h),
                Qt.AlignRight | Qt.AlignVCenter,
                labels.get(stem, stem[:3].upper()),
            )
            painter.setPen(QPen(q("fg", 18), 1))
            painter.drawLine(int(left), int(centre), int(right), int(centre))

            path = QPainterPath()
            samples = 44
            for sample in range(samples):
                fraction = sample / (samples - 1)
                x = left + fraction * (right - left)
                carrier = math.sin((sample + lane * 7) * (0.62 + lane * 0.08))
                envelope = 0.35 + 0.65 * math.sin(math.pi * fraction) ** 2
                y = centre + carrier * lane_h * 0.30 * envelope
                path.moveTo(x, y) if sample == 0 else path.lineTo(x, y)
            colour = q("accent_hi")
            if self.state == "offline":
                colour = q("dim2")
            elif self.state == "error":
                colour = q("rec")
            elif self.state == "done":
                colour = q("ok")
            painter.setPen(QPen(colour, 1.25, Qt.SolidLine, Qt.RoundCap))
            painter.drawPath(path)

        if self.state == "running":
            painter.setPen(QPen(q("accent_hi", 175), 1))
            painter.drawLine(
                int(active_x), int(card.top() + 4), int(active_x), int(card.bottom() - 4)
            )


class ClipList(QTreeWidget):
    """Library list that can start a drag onto a pad or playlist track."""

    auditionRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(QTreeWidget.SingleSelection)
        self.setHeaderHidden(True)
        self.setIndentation(12)
        self.setUniformRowHeights(True)
        self.setAnimated(False)
        self.itemActivated.connect(self._activate)
        self.setItemDelegate(SoundDelegate(self))
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAccessibleName("Sounds organized by instrument")

    def items(self):
        iterator = QTreeWidgetItemIterator(self)
        while iterator.value():
            yield iterator.value()
            iterator += 1

    def sounds(self):
        return [item for item in self.items() if item.data(0, Qt.UserRole)]

    def _activate(self, item, column=0):
        clip_id = item.data(0, Qt.UserRole)
        if clip_id:
            self.auditionRequested.emit(clip_id)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        item = self.itemAt(event.position().toPoint())
        if item and item.data(0, Qt.UserRole) and event.button() == Qt.LeftButton:
            rect = self.visualItemRect(item)
            if rect.left() + 8 <= event.position().x() <= rect.left() + 28:
                self.auditionRequested.emit(item.data(0, Qt.UserRole))

    def startDrag(self, actions):
        item = self.currentItem()
        if not item or not item.data(0, Qt.UserRole):
            return
        clip_id = item.data(0, Qt.UserRole)
        mime = QMimeData()
        mime.setData("application/x-mpclab-clip", clip_id.encode())
        mime.setText(item.text(0))

        pix = QPixmap(190, 22)
        pix.fill(QColor(C["bg3"]))
        p = QPainter(pix)
        p.setPen(QColor(C["accent"]))
        p.drawRect(0, 0, 189, 21)
        p.drawText(pix.rect().adjusted(6, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft, item.text(0))
        p.end()

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(pix)
        drag.exec(Qt.CopyAction)


class BrowserPanel(WindowClient, QWidget):
    clipSelected = Signal(str)
    clipActivated = Signal(str)  # double-click → audition
    libraryChanged = Signal()
    separationFinished = Signal(list)

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setAcceptDrops(True)
        self._filter = ""
        self._crate = "all"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QWidget()
        hh = QHBoxLayout(head)
        hh.setContentsMargins(9, 6, 9, 6)
        self.crate_detail_toggle = QPushButton("CRATES ▾")
        self.crate_detail_toggle.setObjectName("mini")
        self.crate_detail_toggle.setCheckable(True)
        self.crate_detail_toggle.setChecked(True)
        self.crate_detail_toggle.setToolTip(
            "Show detailed record crates; collapse for more sound rows"
        )
        self.crate_detail_toggle.toggled.connect(self._set_crate_detail)
        add = QPushButton("+ FILE")
        add.setObjectName("mini")
        add.clicked.connect(self.import_dialog)
        self.add_pack = QPushButton("+ PACK")
        self.add_pack.setObjectName("mini")
        self.add_pack.setToolTip("Add an extracted sample-pack folder without copying its sounds")
        self.add_pack.setAccessibleName("Add sample pack folder")
        self.add_pack.clicked.connect(self.import_pack_dialog)
        self.sep_toggle = QPushButton("STEMS ▾")
        self.sep_toggle.setObjectName("mini")
        self.sep_toggle.setCheckable(True)
        self.sep_toggle.setChecked(False)
        self.sep_toggle.setToolTip("Open the local stem-separation deck")
        self.sep_toggle.toggled.connect(self._set_sep_expanded)
        hh.addWidget(self.crate_detail_toggle)
        hh.addStretch(1)
        hh.addWidget(self.sep_toggle)
        hh.addWidget(add)
        hh.addWidget(self.add_pack)
        head.setObjectName("browserHead")
        head.setAttribute(Qt.WA_StyledBackground, True)
        lay.addWidget(head)

        shelf = QWidget()
        self.crate_shelf = shelf
        shelf_layout = QGridLayout(shelf)
        shelf_layout.setContentsMargins(8, 8, 8, 4)
        shelf_layout.setSpacing(6)
        self.crate_group = QButtonGroup(self)
        self.crate_buttons = {}
        for index, (key, label) in enumerate(CRATES):
            button = CrateButton(index, label)
            self.crate_group.addButton(button)
            button.clicked.connect(lambda checked=False, category=key: self.open_crate(category))
            self.crate_buttons[key] = button
            shelf_layout.addWidget(button, index // 2, index % 2)
        lay.addWidget(shelf)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search sounds · Ctrl+F")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._set_filter)
        self.source_filter = QComboBox()
        self.source_filter.addItem("ALL SOUNDS", "all")
        self.source_filter.addItem("PROJECT LIBRARY", "library")
        self.source_filter.addItem("SAMPLE PACKS", "packs")
        self.source_filter.currentIndexChanged.connect(self._source_changed)
        self.category_filter = QComboBox()
        self.category_filter.addItem("ALL PACK FOLDERS", "")
        self.category_filter.currentIndexChanged.connect(lambda _index: self.refresh())
        self.category_filter.setVisible(False)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(8, 7, 8, 5)
        self.all_crates = QPushButton("ALL SOUNDS")
        self.all_crates.setObjectName("mini")
        self.all_crates.setCheckable(True)
        self.all_crates.setChecked(True)
        self.crate_group.addButton(self.all_crates)
        self.all_crates.clicked.connect(lambda: self.open_crate("all"))
        self.crate_summary = QLabel()
        self.crate_summary.setObjectName("hint")
        shelf_bar = QHBoxLayout()
        shelf_bar.addWidget(self.all_crates)
        self.filter_toggle = QPushButton("FILTERS")
        self.filter_toggle.setObjectName("mini")
        self.filter_toggle.setCheckable(True)
        shelf_bar.addWidget(self.filter_toggle)
        shelf_bar.addStretch()
        shelf_bar.addWidget(self.crate_summary)
        wl.addLayout(shelf_bar)
        wl.addWidget(self.search)
        self.filter_panel = QWidget()
        filter_layout = QVBoxLayout(self.filter_panel)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.addWidget(self.source_filter)
        filter_layout.addWidget(self.category_filter)
        wl.addWidget(self.filter_panel)
        self.filter_panel.hide()
        self.filter_toggle.toggled.connect(self.filter_panel.setVisible)
        lay.addWidget(wrap)

        self.list = ClipList()
        self.list.currentItemChanged.connect(self._selection_changed)
        self.list.auditionRequested.connect(self.clipActivated)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        lay.addWidget(self.list, 1)
        self.empty_label = QLabel(
            "No matching sounds.\nClear search or filters, or choose All Sounds."
        )
        self.empty_label.setObjectName("hint")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        lay.addWidget(self.empty_label)

        actions = QHBoxLayout()
        for label, destination in (("Play in Notes", "notes"), ("Add to Beats", "beats")):
            button = QPushButton(label)
            button.setMinimumHeight(30)
            button.clicked.connect(
                lambda checked=False, d=destination: self.app.sample_workflow.send(
                    self.selected_clip_id(), destination=d
                )
            )
            actions.addWidget(button)
        lay.addLayout(actions)
        hint = QLabel(
            "▶ Preview · double-click or Enter to audition\n"
            "Drag to Beats lanes, Notes, pads or Arrange"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        hint.setContentsMargins(9, 6, 9, 8)
        lay.addWidget(hint)

        # ── stem drawer ─────────────────────────────────────
        self.sep_panel = QFrame()
        self.sep_panel.setObjectName("stemPanel")
        panel_layout = QVBoxLayout(self.sep_panel)
        panel_layout.setContentsMargins(9, 8, 9, 9)
        panel_layout.setSpacing(6)

        self.stem_deck = StemDeck()
        panel_layout.addWidget(self.stem_deck)

        status_row = QWidget()
        status_layout = QHBoxLayout(status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        self.sep_status = QLabel("checking local engine")
        self.sep_status.setObjectName("hint")
        self.sep_status.setWordWrap(True)
        status_layout.addWidget(self.sep_status, 1)
        panel_layout.addWidget(status_row)

        self.model_box = QComboBox()
        for key, info in separate.MODELS.items():
            self.model_box.addItem(info["label"], key)
        self.model_box.currentIndexChanged.connect(self._model_changed)
        panel_layout.addWidget(self.model_box)

        self.model_detail = QLabel()
        self.model_detail.setObjectName("hint")
        self.model_detail.setWordWrap(True)
        panel_layout.addWidget(self.model_detail)

        action_row = QWidget()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(6)
        self.hq = QCheckBox("refine ×4")
        self.hq.setToolTip("Four inference shifts can make edges cleaner, but take longer")
        self.sep_btn = QPushButton("SPLIT SELECTED")
        self.sep_btn.setObjectName("go")
        self.sep_btn.clicked.connect(self.start_separation)
        action_layout.addWidget(self.hq)
        action_layout.addWidget(self.sep_btn, 1)
        panel_layout.addWidget(action_row)

        self.jobs_box = QVBoxLayout()
        self.jobs_box.setSpacing(5)
        panel_layout.addLayout(self.jobs_box)
        lay.addWidget(self.sep_panel)
        self.sep_panel.setVisible(False)

        self._job_widgets: dict[str, tuple] = {}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_jobs)
        self._timer.start(400)

        self._model_changed()
        self.refresh_separation_status()

        self.refresh()

    # ── library ──────────────────────────────────────────────
    def _set_crate_detail(self, detailed):
        self.crate_detail_toggle.setText("CRATES ▾" if detailed else "CRATES ▸")
        for button in self.crate_buttons.values():
            button.set_detailed(detailed)
        self.crate_shelf.layout().invalidate()
        self.crate_shelf.layout().activate()
        self.crate_shelf.updateGeometry()
        self.layout().activate()

    def open_crate(self, key):
        self._crate = key if key in self.crate_buttons else "all"
        button = self.all_crates if self._crate == "all" else self.crate_buttons[self._crate]
        button.setChecked(True)
        self.refresh()

    def _set_filter(self, text):
        self._filter = text.lower().strip()
        self.refresh()

    def _source_changed(self, *_args):
        packs = self.source_filter.currentData() == "packs"
        self.category_filter.setVisible(packs)
        self.refresh()

    def refresh(self, select: str | None = None, *_args):
        # A newly imported/created sound must be reachable even when a crate
        # or search from the previous selection would otherwise hide it.
        if select and select in self.app.library.clips:
            self._crate = "all"
            self.all_crates.setChecked(True)
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            self._filter = ""
            self.source_filter.blockSignals(True)
            self.source_filter.setCurrentIndex(0)
            self.source_filter.blockSignals(False)
            self.category_filter.setVisible(False)
        pack_categories = sorted(
            {
                clip.category or "Loose"
                for clip in self.app.library.clips.values()
                if clip.kind == "pack"
            },
            key=natural_key,
        )
        category = self.category_filter.currentData() or ""
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("ALL PACK FOLDERS", "")
        for value in pack_categories:
            self.category_filter.addItem(value, value)
        category_index = self.category_filter.findData(category)
        self.category_filter.setCurrentIndex(max(0, category_index))
        self.category_filter.blockSignals(False)

        current = select or (
            self.list.currentItem().data(0, Qt.UserRole) if self.list.currentItem() else None
        )
        source_mode = self.source_filter.currentData()
        category = self.category_filter.currentData() or ""
        # Save browsing state independently of temporary search results.
        if not getattr(self, "_was_searching", False):
            expanded = getattr(self, "_expanded", set())
            visible_paths = {
                item.data(0, GROUP_ROLE) for item in self.list.items() if item.data(0, GROUP_ROLE)
            }
            self._expanded = (expanded - visible_paths) | {
                item.data(0, GROUP_ROLE)
                for item in self.list.items()
                if item.isExpanded() and item.data(0, GROUP_ROLE)
            }
        self._was_searching = bool(self._filter)
        self.list.blockSignals(True)
        self.list.clear()
        groups = {}
        counts = dict.fromkeys(self.crate_buttons, 0)
        for clip in self.app.library.ordered():
            crate, instrument = sample_group(clip)
            counts[crate] += 1
            if self._crate != "all" and crate != self._crate:
                continue
            if source_mode == "library" and clip.kind == "pack":
                continue
            if source_mode == "packs" and clip.kind != "pack":
                continue
            if source_mode == "packs" and category and (clip.category or "Loose") != category:
                continue
            searchable = " ".join(
                (clip.name, clip.pack or "", clip.category or "", instrument, crate)
            ).lower()
            if self._filter and self._filter not in searchable:
                continue
            groups.setdefault(instrument, []).append(clip)
        for key, button in self.crate_buttons.items():
            button.set_count(counts[key])
        folders = {}
        for instrument in INSTRUMENTS:
            clips = groups.get(instrument, [])
            if not clips:
                continue
            root = QTreeWidgetItem(self.list, [instrument])
            root.setData(0, GROUP_ROLE, (instrument,))
            root.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            folders[(instrument,)] = root
            for clip in sorted(
                clips,
                key=lambda sound: (
                    natural_key(" / ".join(sample_location(sound))),
                    natural_key(sound.name),
                    sound.id,
                ),
            ):
                path = (instrument,)
                parent = root
                # Keep the original pack folder path visible without deep nesting.
                location = " / ".join(sample_location(clip))
                path += (location,)
                if path not in folders:
                    label = (
                        f"{Path(clip.category or 'Loose files').name} · {clip.pack or 'Sample pack'}"
                        if clip.kind == "pack"
                        else location
                    )
                    folder = QTreeWidgetItem(parent, [label])
                    folder.setData(0, GROUP_ROLE, path)
                    folder.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    folder.setToolTip(0, location)
                    folders[path] = folder
                self._add_sound(clip, instrument, folders[path], current)
        # Identical names are separate recordings. Show their stable IDs rather
        # than silently merging them or renaming files referenced by projects.
        for folder in folders.values():
            children = [
                folder.child(i)
                for i in range(folder.childCount())
                if folder.child(i).data(0, Qt.UserRole)
            ]
            names = Counter(item.text(0).casefold() for item in children)
            for item in children:
                if names[item.text(0).casefold()] > 1:
                    name = f"{item.text(0)} · {item.data(0, Qt.UserRole)}"
                    item.setText(0, name)
                    data = item.data(0, SOUND_ROLE)
                    data["name"] = name
                    item.setData(0, SOUND_ROLE, data)
        for path, folder in reversed(list(folders.items())):
            count = sum(
                1 if folder.child(i).data(0, Qt.UserRole) else folder.child(i).data(0, COUNT_ROLE)
                for i in range(folder.childCount())
            )
            folder.setData(0, COUNT_ROLE, count)
            folder.setText(0, f"{folder.text(0)}  ·  {count}")
            folder.setExpanded(bool(self._filter) or path in getattr(self, "_expanded", set()))
        if self.list.currentItem():
            parent = self.list.currentItem().parent()
            while parent:
                parent.setExpanded(True)
                parent = parent.parent()
            self.list.scrollToItem(self.list.currentItem())
        count = sum(len(clips) for clips in groups.values())
        self.crate_summary.setText(f"{count:,}")
        self.crate_summary.setToolTip(f"{count:,} matching sounds")
        self.filter_toggle.setText("FILTERS •" if source_mode != "all" else "FILTERS")
        self.empty_label.setVisible(count == 0)
        self.list.blockSignals(False)
        if select:
            self._emit_current()

    def _add_sound(self, clip, instrument, parent, current):
        item = QTreeWidgetItem(parent, [clip.name])
        item.setData(0, Qt.UserRole, clip.id)
        item.setData(
            0, SOUND_ROLE, dict(name=clip.name, duration=clip.duration, instrument=instrument)
        )
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
        tip = [
            clip.name,
            " / ".join(sample_location(clip)),
            f"{clip.kind} · {clip.duration:.2f}s",
            f"ID: {clip.id}",
        ]
        if clip.bpm:
            tip.append(f"{clip.bpm} BPM")
        tip.append(clip.source_path or f"Library: {clip.id}")
        item.setToolTip(0, "\n".join(tip))
        if clip.id == current:
            self.list.setCurrentItem(item)

    def _emit_current(self):
        item = self.list.currentItem()
        if item and item.data(0, Qt.UserRole):
            self.clipSelected.emit(item.data(0, Qt.UserRole))

    def _selection_changed(self, current, previous):
        if current and current.data(0, Qt.UserRole):
            self.clipSelected.emit(current.data(0, Qt.UserRole))

    def selected_clip_id(self) -> str | None:
        item = self.list.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def _context_menu(self, pos):
        item = self.list.itemAt(pos)
        if not item or not item.data(0, Qt.UserRole):
            return
        clip_id = item.data(0, Qt.UserRole)
        clip = self.app.library.clips.get(clip_id)
        menu = QMenu(self)
        menu.addAction(
            "Play in Notes", lambda: self.app.sample_workflow.send(clip_id, destination="notes")
        )
        menu.addAction("Add to Beats", lambda: self.app.sample_workflow.send(clip_id))
        menu.addAction(
            "Replace selected pad sound (keep rhythm)",
            lambda: self.app.sample_workflow.send(clip_id, index=self.app.pads.selected),
        )
        menu.addSeparator()
        act_sep = menu.addAction("Split into stems")
        act_analyze = menu.addAction("Detect BPM / transients")
        menu.addSeparator()
        act_del = menu.addAction(
            "Pack file is linked read-only"
            if clip and clip.kind == "pack"
            else "Move to library trash"
        )
        act_del.setEnabled(bool(clip and clip.kind != "pack"))
        chosen = menu.exec(self.list.mapToGlobal(pos))
        if chosen == act_del:
            pad_refs = sum(pad.sample_id == clip_id for pad in self.app.project.pads)
            playlist_refs = sum(
                block.kind == "audio" and block.ref == clip_id
                for row in self.app.project.rows
                for block in row.clips
            )
            slice_refs = int(bool(self.app.project.slices.get(clip_id)))
            references = pad_refs + playlist_refs + slice_refs
            if references:
                QMessageBox.warning(
                    self,
                    "Clip is still in use",
                    f"“{clip.name}” has {references} project reference"
                    f"{'s' if references != 1 else ''}. Remove its pads, Playlist "
                    "blocks, and saved slices before moving it to trash.",
                )
            elif (
                QMessageBox.question(
                    self,
                    "Move to trash",
                    f"Move “{clip.name}” to the library trash?\n\n"
                    "Its files will remain recoverable inside library/_trash.",
                )
                == QMessageBox.Yes
            ):
                self.app.snapshot()
                try:
                    moved_to = self.app.library.delete(clip_id)
                except Exception as exc:
                    self.app.discard_snapshot()
                    QMessageBox.warning(self, "Move failed", str(exc))
                    return
                if self.app.current_clip == clip_id:
                    self.app.current_clip = None
                    self.app.wave.set_clip(None, None, 0.0, [], clip_id=None)
                    self.app.nav.set_overview(None)
                    self.app.clip_label.setText("no sample loaded")
                self.refresh()
                self.libraryChanged.emit()
                self.app.status.showMessage(f"moved to trash → {moved_to}", 5000)
        elif chosen == act_sep:
            self.list.setCurrentItem(item)
            self.sep_toggle.setChecked(True)
            self.start_separation()
        elif chosen == act_analyze:
            self.app.library.analyze(clip_id)
            self.refresh()
            self.libraryChanged.emit()

    # ── import ───────────────────────────────────────────────
    def import_pack_dialog(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Add extracted sample pack folder", str(Path.home() / "Music")
        )
        if not folder:
            return
        try:
            count = self.app.library.register_pack(Path(folder))
        except Exception as exc:
            QMessageBox.warning(self, "Could not add sample pack", str(exc))
            return
        self.search.clear()
        self._crate = "all"
        self.all_crates.setChecked(True)
        self.source_filter.setCurrentIndex(self.source_filter.findData("packs"))
        self.category_filter.setCurrentIndex(0)
        self.refresh()
        self.libraryChanged.emit()
        self.app.status.showMessage(
            f"{count} new sounds indexed from {Path(folder).name}. "
            "Keep this folder in place; its audio was not copied.",
            8000,
        )

    def import_dialog(self):
        exts = " ".join(f"*{e}" for e in sorted(AUDIO_EXT))
        start = Path.home() / "Downloads"
        if not start.is_dir():
            start = Path.home()
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import audio", str(start), f"Audio / video ({exts});;All files (*)"
        )
        self.import_paths(paths)

    def import_paths(self, paths, *, select_imported=True):
        imported = []
        last = None
        for path in paths:
            self.app.snapshot()
            try:
                clip = self.app.library.import_file(Path(path))
                imported.append(clip)
                last = clip.id
            except Exception as exc:
                self.app.discard_snapshot()
                QMessageBox.warning(self, "Import failed", f"{Path(path).name}\n\n{exc}")
        if last:
            self.refresh(select=last if select_imported else None)
            self.libraryChanged.emit()
        return imported

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        self.import_paths([p for p in paths if Path(p).suffix.lower() in AUDIO_EXT])
        ev.acceptProposedAction()

    # ── stem separation ─────────────────────────────────────
    def _set_sep_expanded(self, expanded: bool) -> None:
        self.sep_panel.setVisible(expanded)
        self.sep_toggle.setText("STEMS ▴" if expanded else "STEMS ▾")

    def _model_changed(self, *_args) -> None:
        model = self.model_box.currentData()
        info = separate.MODELS.get(model, separate.MODELS["htdemucs"])
        self.model_detail.setText(info["detail"])
        self.stem_deck.set_status(self.stem_deck.state, self.stem_deck.progress, info["stems"])

    def refresh_separation_status(self) -> None:
        ready = separate.available()
        if ready:
            self.sep_status.setText("LOCAL ENGINE READY · CPU")
            self.sep_status.setStyleSheet(f"color: {C['ok']};")
            self.sep_btn.setEnabled(True)
            if self.stem_deck.state == "offline":
                self.stem_deck.set_status(
                    "ready", stems=separate.MODELS[self.model_box.currentData()]["stems"]
                )
        else:
            self.sep_status.setText("ADD ENGINE · ./install-separation.sh")
            self.sep_status.setStyleSheet(f"color: {C['dim']};")
            self.sep_btn.setEnabled(False)
            self.stem_deck.set_status(
                "offline", stems=separate.MODELS[self.model_box.currentData()]["stems"]
            )

    def start_separation(self) -> None:
        clip_id = self.selected_clip_id()
        if not clip_id:
            QMessageBox.information(
                self, "Stem separation", "Select a song or sample in the browser first."
            )
            return
        if not separate.available():
            QMessageBox.information(
                self,
                "Stem engine not installed",
                f"Run ./install-separation.sh in the project folder, then restart {APP_NAME}.",
            )
            return
        clip = self.app.library.clips.get(clip_id)
        if clip is None:
            return
        try:
            job = self.app.separator.start(
                self.app.library.wav_path(clip_id),
                model=self.model_box.currentData(),
                shifts=4 if self.hq.isChecked() else 0,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Could not start separation", str(exc))
            return
        job.name = clip.name
        job.source_clip = clip_id
        self.sep_toggle.setChecked(True)
        self._add_job_widget(job)
        self.stem_deck.set_status("running", 0.0, separate.MODELS[job.model]["stems"])
        self.app.status.showMessage(f"splitting {clip.name} into stems", 5000)

    def _add_job_widget(self, job) -> None:
        frame = QFrame()
        frame.setObjectName("jobCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(7, 6, 7, 6)
        layout.setSpacing(4)

        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(job.name)
        label.setObjectName("title")
        cancel = QPushButton("×")
        cancel.setObjectName("mini")
        cancel.setFixedWidth(24)
        cancel.setToolTip("Cancel this separation")
        cancel.clicked.connect(lambda _checked=False, jid=job.id: self.app.separator.cancel(jid))
        top_layout.addWidget(label, 1)
        top_layout.addWidget(cancel)

        detail = QLabel("loading model")
        detail.setObjectName("hint")
        progress = QProgressBar()
        progress.setRange(0, 1000)
        progress.setTextVisible(False)
        progress.setFixedHeight(5)
        layout.addWidget(top)
        layout.addWidget(detail)
        layout.addWidget(progress)
        self.jobs_box.addWidget(frame)
        self._job_widgets[job.id] = (frame, label, detail, progress, cancel)

    def _poll_jobs(self) -> None:
        for job in list(self.app.separator.jobs.values()):
            widgets = self._job_widgets.get(job.id)
            if widgets is None:
                continue
            frame, label, detail, progress, cancel = widgets
            progress.setValue(int(job.progress * 1000))
            label.setText(job.name)
            detail.setText(f"{job.message} · {job.progress * 100:.0f}% · {job.elapsed:.0f}s")
            if job.state in ("queued", "running"):
                self.stem_deck.set_status(
                    "running", job.progress, separate.MODELS[job.model]["stems"]
                )
            elif job.state == "done" and not job.adopted:
                detail.setText("importing stems into the library")
                try:
                    stems = self.app.adopt_stems(job)
                except Exception as exc:
                    job.state = "error"
                    job.message = f"import failed: {exc}"
                    self.stem_deck.set_status("error", job.progress)
                    continue
                job.adopted = True
                cancel.setVisible(False)
                detail.setText(f"READY · {len(stems)} clips added · {job.elapsed:.0f}s")
                detail.setStyleSheet(f"color: {C['ok']};")
                progress.setProperty("complete", True)
                progress.style().unpolish(progress)
                progress.style().polish(progress)
                self.stem_deck.set_status("done", 1.0, separate.MODELS[job.model]["stems"])
                self.refresh(select=stems[0] if stems else None)
                self.libraryChanged.emit()
                self.separationFinished.emit(stems)
                self.app.status.showMessage(f"{len(stems)} stems ready from {job.name}", 7000)
                QTimer.singleShot(
                    9000, frame, lambda jid=job.id, widget=frame: self._drop_job_widget(jid, widget)
                )
            elif job.state == "error":
                detail.setText(job.message)
                detail.setStyleSheet(f"color: {C['rec']};")
                cancel.setVisible(False)
                self.stem_deck.set_status("error", job.progress)
            elif job.state == "cancelled":
                self.stem_deck.set_status(
                    "ready", stems=separate.MODELS[self.model_box.currentData()]["stems"]
                )
                self._drop_job_widget(job.id, frame)

    def _drop_job_widget(self, job_id: str, frame: QFrame) -> None:
        self._job_widgets.pop(job_id, None)
        frame.setParent(None)
        frame.deleteLater()
