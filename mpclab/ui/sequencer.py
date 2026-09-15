"""Step sequencer grid — one lane per pad in the active bank.

Two things make a 16 × 32 grid readable at a glance: bars are shaded in
alternating blocks so the eye can count them without reading numbers, and a
step's velocity is its fill height rather than a hairline underneath it.  Empty
lanes hide by default, so a four-piece kit is four rows and not sixteen.
"""

from __future__ import annotations

from .window_client import WindowClient

from PySide6.QtCore import Qt, QRectF, QSize, Signal, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QFontMetrics
from PySide6.QtWidgets import QWidget, QMenu

from ..model import PADS_PER_BANK, PAD_KEYS
from .theme import q, TRACK_COLORS
from .sample_drag import SoundDropFilter

LABEL_W = 180
ROW_H = 36
CELL_W = 25
RULER_H = 20
GAP = 2

# Right-click a lane to fill it. Value is the step interval in sixteenths of a
# beat divided by the pattern's own division, resolved against `div` at use.
FILLS = (
    ("every beat", 1.0),
    ("every 1/8", 0.5),
    ("every 1/16", 0.25),
    ("off-beats", None),
    ("every other step", 0.0),
)


class StepGrid(WindowClient, QWidget):
    stepEdited = Signal()
    padAuditioned = Signal(int)
    padSelected = Signal(int)
    followRequested = Signal(int, int)  # x, width of the playing step

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.bank = 0
        self.only_loaded = True
        self.follow = True
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
        self._paint_vel = None  # velocity being dragged in
        self._last_cell = None
        self._hover = None
        self._clipboard: dict[int, float] | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self._playing_step = -1
        self._sample_drop = SoundDropFilter(app, self, "beats", self.drop_target)

    # ── layout ───────────────────────────────────────────────
    def pattern(self):
        return self.app.project.pattern()

    def lanes(self) -> list[int]:
        """Global pad indices for the rows on screen, top row first.

        An empty bank still shows all sixteen lanes — hiding everything would
        leave nothing to drop a first sound onto.
        """
        base = self.bank * PADS_PER_BANK
        every = list(range(base, base + PADS_PER_BANK))
        if not self.only_loaded:
            return every
        pattern = self.pattern()
        melodic = {note.pad for note in pattern.notes if note.pad is not None}
        used = [
            gi
            for gi in every
            if not self.app.project.pads[gi].empty or pattern.steps.get(gi) or gi in melodic
        ]
        return used or every

    def set_only_loaded(self, on: bool) -> None:
        self.only_loaded = bool(on)
        self.refresh()

    def _wanted(self, pat) -> QSize:
        return QSize(
            LABEL_W + pat.total_steps * (CELL_W + GAP) + 20,
            RULER_H + (len(self.lanes()) + 1) * (ROW_H + GAP) + 12,
        )

    def sizeHint(self):
        return self._wanted(self.pattern())

    def minimumSizeHint(self):
        return self._wanted(self.pattern())

    def refresh(self):
        self.updateGeometry()
        self.resize(self.minimumSizeHint())
        self.update()

    def _row_at(self, y: float) -> int | None:
        if y < RULER_H:
            return None
        row = int((y - RULER_H) // (ROW_H + GAP))
        lanes = self.lanes()
        return row if 0 <= row < len(lanes) else None

    def _cell_at(self, pos):
        pat = self.pattern()
        if pos.x() < LABEL_W:
            return None, None
        row = self._row_at(pos.y())
        col = int((pos.x() - LABEL_W) // (CELL_W + GAP))
        if row is None or not (0 <= col < pat.total_steps):
            return None, None
        return row, col

    def _pad_for_row(self, row: int) -> int:
        return self.lanes()[row]

    def drop_target(self, pos):
        bottom = RULER_H + len(self.lanes()) * (ROW_H + GAP)
        if bottom <= pos.y() < bottom + ROW_H:
            return None
        row = self._row_at(pos.y())
        if row is not None and pos.x() < LABEL_W:
            return self._pad_for_row(row)
        return False

    # ── interaction ──────────────────────────────────────────
    def mousePressEvent(self, ev):
        pat = self.pattern()
        pos = ev.position()

        if pos.x() < LABEL_W and pos.y() >= RULER_H:
            row = self._row_at(pos.y())
            if row is None:
                return
            gi = self._pad_for_row(row)
            if ev.modifiers() & Qt.ShiftModifier:
                self.app.snapshot()
                pat.steps.pop(gi, None)
                self.stepEdited.emit()
            else:
                self.padSelected.emit(gi)
                self.padAuditioned.emit(gi)
            self.update()
            return

        row, col = self._cell_at(pos)
        if row is None:
            return
        gi = self._pad_for_row(row)
        self.app.snapshot()
        current = pat.get(gi, col)
        if ev.button() == Qt.RightButton:
            pat.set(gi, col, None)
            self._paint_vel = 0
        else:
            self._paint_vel = None if current else 1.0
            pat.set(gi, col, self._paint_vel)
            if self._paint_vel and not self.app.engine.playing:
                self.padAuditioned.emit(gi)
        self._last_cell = (row, col)
        self.stepEdited.emit()
        self.update()

    def mouseMoveEvent(self, ev):
        row, col = self._cell_at(ev.position())
        if not (ev.buttons() & (Qt.LeftButton | Qt.RightButton)):
            hover = (row, col) if row is not None else None
            if hover != self._hover:
                self._hover = hover
                self.update()
            return
        if row is None or (row, col) == self._last_cell:
            return
        self._last_cell = (row, col)
        gi = self._pad_for_row(row)
        vel = None if self._paint_vel in (None, 0) else self._paint_vel
        self.pattern().set(gi, col, vel)
        self.stepEdited.emit()
        self.update()

    def mouseReleaseEvent(self, ev):
        self._paint_vel = None
        self._last_cell = None
        # A filtered lane can disappear when its final step is erased.
        self.refresh()

    def leaveEvent(self, ev):
        self._hover = None
        self.update()

    def wheelEvent(self, ev):
        row, col = self._cell_at(ev.position())
        if row is None:
            ev.ignore()
            return
        pat = self.pattern()
        gi = self._pad_for_row(row)
        cur = pat.get(gi, col)
        if cur is None:
            ev.ignore()
            return
        delta = 0.08 if ev.angleDelta().y() > 0 else -0.08
        self.app.snapshot()
        pat.set(gi, col, max(0.1, min(1.0, cur + delta)))
        self.stepEdited.emit()
        self.update()

    # ── lane menu ────────────────────────────────────────────
    def contextMenuEvent(self, ev):
        pos = ev.pos()
        if pos.x() >= LABEL_W or pos.y() < RULER_H:
            return  # the grid itself erases on right-drag
        row = self._row_at(pos.y())
        if row is None:
            return
        gi = self._pad_for_row(row)
        pad = self.app.project.pads[gi]

        menu = QMenu(self)
        menu.addAction(
            f"Pad {gi % PADS_PER_BANK + 1}{'  ·  ' + pad.name if pad.name else ''}"
        ).setEnabled(False)
        menu.addSeparator()
        menu.addAction("Audition", lambda: self.padAuditioned.emit(gi))
        menu.addAction("Select this pad", lambda: self.padSelected.emit(gi))
        menu.addAction("Open in Notes", lambda: self.app.sample_workflow.open_notes(gi))
        menu.addAction(
            "Convert steps to notes (keep swing)", lambda: self.app.sample_workflow.step_notes(gi)
        )
        menu.addSeparator()
        fill = menu.addMenu("Fill lane")
        for label, beats in FILLS:
            fill.addAction(label, lambda b=beats: self._fill(gi, b))
        menu.addAction("Humanise velocity", lambda: self._humanise(gi))
        menu.addAction("Nudge later", lambda: self._shift(gi, 1))
        menu.addAction("Nudge earlier", lambda: self._shift(gi, -1))
        menu.addSeparator()
        menu.addAction("Copy lane", lambda: self._copy(gi))
        paste = menu.addAction("Paste lane", lambda: self._paste(gi))
        paste.setEnabled(self._clipboard is not None)
        menu.addAction("Clear lane", lambda: self._clear_lane(gi))
        menu.exec(ev.globalPos())

    def _commit(self):
        self.stepEdited.emit()
        self.refresh()

    def _fill(self, gi: int, beats: float | None):
        self.app.snapshot()
        pat = self.pattern()
        div = max(1, pat.div)
        if beats is None:  # off-beats: the and of each beat
            stride, offset = div, div // 2
        elif beats == 0.0:  # every other step
            stride, offset = 2, 0
        else:
            stride, offset = max(1, int(round(beats * div))), 0
        pat.steps.pop(gi, None)
        for step in range(offset, pat.total_steps, stride):
            pat.set(gi, step, 1.0)
        self._commit()

    def _humanise(self, gi: int):
        import random

        row = self.pattern().steps.get(gi)
        if not row:
            return
        self.app.snapshot()
        for step in list(row):
            row[step] = max(0.35, min(1.0, row[step] * random.uniform(0.72, 1.0)))
        self._commit()

    def _shift(self, gi: int, by: int):
        pat = self.pattern()
        row = pat.steps.get(gi)
        if not row:
            return
        self.app.snapshot()
        total = pat.total_steps
        pat.steps[gi] = {(step + by) % total: vel for step, vel in row.items()}
        self._commit()

    def _copy(self, gi: int):
        self._clipboard = dict(self.pattern().steps.get(gi, {}))

    def _paste(self, gi: int):
        if self._clipboard is None:
            return
        self.app.snapshot()
        self.pattern().steps[gi] = dict(self._clipboard)
        self._commit()

    def _clear_lane(self, gi: int):
        self.app.snapshot()
        self.pattern().steps.pop(gi, None)
        self._commit()

    # ── transport follow ─────────────────────────────────────
    def _tick(self):
        eng = self.app.engine
        pat = self.pattern()
        step = -1
        if eng.playing and pat.total_steps:
            length = pat.length_beats
            local = eng.beat % length if length else 0.0
            step = int(local * pat.div) % pat.total_steps
        if step != self._playing_step:
            self._playing_step = step
            self.update()
            if self.follow and step >= 0:
                self.followRequested.emit(LABEL_W + step * (CELL_W + GAP), CELL_W)

    # ── painting ─────────────────────────────────────────────
    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), q("bg"))
        proj = self.app.project
        pat = self.pattern()
        total = pat.total_steps
        div = max(1, pat.div)
        lanes = self.lanes()
        steps_per_bar = div * 4
        grid_h = len(lanes) * (ROW_H + GAP)

        tiny = QFont(self.font())
        tiny.setPointSizeF(9.0)
        bar_font = QFont(self.font())
        bar_font.setPointSizeF(10.0)
        bar_font.setBold(True)

        # Alternating bar blocks behind everything, so bars are countable.
        for step in range(0, total, steps_per_bar):
            if (step // steps_per_bar) % 2:
                continue
            x = LABEL_W + step * (CELL_W + GAP)
            width = min(steps_per_bar, total - step) * (CELL_W + GAP)
            p.fillRect(QRectF(x - 1, 0, width, RULER_H + grid_h + 4), q("bg2"))

        # ruler: bar numbers, then beats inside the bar
        p.setFont(bar_font)
        for step in range(0, total, steps_per_bar):
            x = LABEL_W + step * (CELL_W + GAP)
            p.setPen(q("fg"))
            p.drawText(
                QRectF(x + 2, 0, CELL_W * 3, RULER_H),
                Qt.AlignLeft | Qt.AlignVCenter,
                str(step // steps_per_bar + 1),
            )
        p.setFont(tiny)
        p.setPen(q("dim2"))
        for step in range(0, total, div):
            if step % steps_per_bar == 0:
                continue
            x = LABEL_W + step * (CELL_W + GAP)
            p.drawText(
                QRectF(x + 1, 0, CELL_W * 2, RULER_H),
                Qt.AlignLeft | Qt.AlignVCenter,
                f"{(step // div) % 4 + 1}",
            )

        fm = QFontMetrics(tiny)
        p.setFont(tiny)
        for row, gi in enumerate(lanes):
            pad = proj.pads[gi]
            local = gi % PADS_PER_BANK
            y = RULER_H + row * (ROW_H + GAP)

            label_rect = QRectF(0, y, LABEL_W - 6, ROW_H)
            selected = gi == self.app.pads.selected
            p.setBrush(q("bg3") if not selected else q("item_sel"))
            p.setPen(QPen(q("accent") if selected else q("line")))
            p.drawRoundedRect(label_rect, 3, 3)
            if not pad.empty:
                p.setBrush(QColor(TRACK_COLORS[pad.track % len(TRACK_COLORS)]))
                p.setPen(Qt.NoPen)
                p.drawEllipse(QRectF(6, y + ROW_H / 2 - 3, 6, 6))
            clip = self.app.library.clips.get(pad.sample_id) if pad.sample_id else None
            name = pad.name or (clip.name if clip else f"pad {local + 1}")
            p.setPen(q("fg") if not pad.empty else q("dim2"))
            p.drawText(
                QRectF(17, y, LABEL_W - 48, ROW_H),
                Qt.AlignLeft | Qt.AlignVCenter,
                fm.elidedText(name, Qt.ElideRight, LABEL_W - 52),
            )
            p.setPen(q("dim2"))
            p.drawText(
                QRectF(LABEL_W - 28, y, 20, ROW_H), Qt.AlignRight | Qt.AlignVCenter, PAD_KEYS[local]
            )

            for step in range(total):
                x = LABEL_W + step * (CELL_W + GAP)
                cell = QRectF(x, y, CELL_W, ROW_H)
                vel = pat.get(gi, step)
                bar_start = step % steps_per_bar == 0
                if vel:
                    p.setBrush(q("cell"))
                    p.setPen(QPen(q("cell_on_line")))
                    p.drawRoundedRect(cell, 3, 3)
                    # Velocity is the height of the block: a soft ghost note
                    # reads as a short bar without having to be measured.
                    filled = max(4.0, (ROW_H - 4) * vel)
                    p.setBrush(q("accent"))
                    p.setPen(Qt.NoPen)
                    p.drawRoundedRect(
                        QRectF(x + 2, y + ROW_H - 2 - filled, CELL_W - 4, filled), 2, 2
                    )
                else:
                    beat_start = step % div == 0
                    p.setBrush(q("cell_beat") if beat_start else q("cell"))
                    p.setPen(QPen(q("cell_bar") if bar_start else q("cell_line")))
                    p.drawRoundedRect(cell, 3, 3)
                if self._hover == (row, step):
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(q("accent2"), 1))
                    p.drawRoundedRect(cell, 3, 3)

        footer = QRectF(3, RULER_H + grid_h + 3, min(self.width() - 6, 440), ROW_H - 2)
        p.setPen(
            QPen(q("accent2") if self.property("sampleDropActive") else q("line"), 1, Qt.DashLine)
        )
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(footer, 3, 3)
        p.setPen(q("fg"))
        p.drawText(
            footer.adjusted(8, 0, -8, 0),
            Qt.AlignVCenter,
            "+ Drop a sound here · or use Browser → Add to Beats",
        )

        # playhead: a column over the whole grid, not a single outlined cell
        if self._playing_step >= 0:
            x = LABEL_W + self._playing_step * (CELL_W + GAP)
            p.setPen(Qt.NoPen)
            p.setBrush(q("accent2", 46))
            p.drawRect(QRectF(x - 1, RULER_H - 2, CELL_W + 2, grid_h + 2))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(q("accent2"), 2))
            p.drawRoundedRect(QRectF(x - 1, RULER_H - 2, CELL_W + 2, grid_h + 2), 3, 3)
