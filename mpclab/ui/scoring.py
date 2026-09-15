"""Instrument scores, printable pages, and pattern note composition."""

from pathlib import Path
from xml.etree import ElementTree as ET

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QPainter, QPdfWriter, QPageSize
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QScrollArea, QSpinBox, QDoubleSpinBox, QFileDialog, QMessageBox,
    QTableWidget, QTableWidgetItem, QAbstractItemView,
)

from ..music import Note
from ..model import safe_filename
from ..scoring import collect_score, musicxml, note_key
from .layout_helpers import scrolling_bar


def qt_score_svg(svg):
    """Qt SVG Tiny skips Verovio's nested viewport; promote it to the root."""
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    root = ET.fromstring(svg)
    nested = root.find("{http://www.w3.org/2000/svg}svg")
    if nested is not None:
        root.set("viewBox", nested.attrib.pop("viewBox"))
        nested.tag = "{http://www.w3.org/2000/svg}g"
    # SVG Tiny accepts one level of tspan. Verovio nests them for MEI IDs;
    # flatten inherited text attributes so titles and instrument names survive.
    namespace = "{http://www.w3.org/2000/svg}"
    for text in root.iter(namespace + "text"):
        spans = []

        def flatten(node, inherited, result):
            attrs = dict(inherited)
            attrs.update({key: value for key, value in node.attrib.items()
                          if key not in ("id", "class")})
            if node.text and node.text.strip():
                content = node.text
                if "\ue1d5" in content:
                    content = content.replace("\ue1d5", "♩")
                    attrs["font-family"] = "serif"
                    attrs["font-size"] = "405px"
                span = ET.Element(namespace + "tspan", attrs)
                span.text = content
                result.append(span)
            for child in node:
                if child.tag == namespace + "tspan":
                    flatten(child, attrs, result)

        for child in list(text):
            if child.tag == namespace + "tspan":
                flatten(child, {}, spans)
            text.remove(child)
        text.text = None
        text.set("font-size", "405px")
        text.set("font-family", "serif")
        text.extend(spans)
    return QByteArray(ET.tostring(root, encoding="utf-8"))


class ScoringPanel(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.xml = ""
        self.toolkit = None
        self.score = None
        self.transcription_dialog = None
        self.note_rows = []
        layout = QVBoxLayout(self)
        toolbar = QWidget()
        row = QHBoxLayout(toolbar)
        self.scope = QComboBox()
        self.scope.addItems(["Current pattern", "Full song"])
        self.scope.setAccessibleName("Score source")
        self.parts = QComboBox()
        self.parts.setMinimumWidth(170)
        self.parts.setAccessibleName("Instrument part")
        refresh = QPushButton("Refresh score")
        self.convert_button = QPushButton("Audio → score…")
        self.convert_button.setToolTip("Convert a song, library sample or instrument recording to editable notation")
        self.convert_button.clicked.connect(self.convert_audio)
        self.xml_button = QPushButton("MusicXML…")
        self.pdf_button = QPushButton("PDF…")
        for widget in (self.scope, self.parts, refresh, self.convert_button, self.xml_button, self.pdf_button):
            row.addWidget(widget)
        row.addStretch()
        layout.addWidget(scrolling_bar(toolbar))
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.message)
        navigation = QHBoxLayout()
        self.previous = QPushButton("Previous page")
        self.next = QPushButton("Next page")
        self.page_label = QLabel()
        self.zoom = QComboBox()
        self.zoom.addItems(["75%", "100%", "125%", "150%"])
        self.zoom.setCurrentIndex(1)
        self.zoom.setAccessibleName("Score zoom")
        for widget in (self.previous, self.page_label, self.next, self.zoom):
            navigation.addWidget(widget)
        navigation.addStretch()
        layout.addLayout(navigation)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignHCenter)
        self.paper = QSvgWidget()
        self.paper.setStyleSheet("background: white;")
        self.paper.setAccessibleName("Engraved sheet music")
        self.scroll.setWidget(self.paper)
        layout.addWidget(self.scroll, 1)
        self.editor = QWidget()
        edit_layout = QVBoxLayout(self.editor)
        edit_layout.setContentsMargins(0, 0, 0, 0)
        edit_layout.addWidget(QLabel("Compose in the current pattern · select a note to edit its placement"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Instrument", "Pitch", "Bar : beat", "Length (beats)"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMaximumHeight(150)
        self.table.horizontalHeader().setStretchLastSection(True)
        edit_layout.addWidget(self.table)
        controls = QWidget()
        edit = QHBoxLayout(controls)
        edit.setContentsMargins(0, 0, 0, 0)
        self.target = QComboBox()
        self.target.setAccessibleName("Instrument for new note")
        self.pitch = QSpinBox()
        self.pitch.setRange(0, 127)
        self.pitch.setValue(60)
        self.pitch.setToolTip("MIDI pitch: 60 = C4")
        self.bar = QSpinBox()
        self.bar.setRange(1, 2048)
        self.beat = QDoubleSpinBox()
        self.beat.setRange(1, 4.75)
        self.beat.setSingleStep(.25)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(.25, 4096)
        self.duration.setSingleStep(.25)
        self.duration.setValue(1)
        for label, widget in (("Instrument", self.target), ("Pitch", self.pitch),
                              ("Bar", self.bar), ("Beat", self.beat), ("Length", self.duration)):
            edit.addWidget(QLabel(label))
            widget.setAccessibleName(label)
            edit.addWidget(widget)
        self.add = QPushButton("Add note")
        self.apply = QPushButton("Apply")
        self.delete = QPushButton("Delete")
        for button in (self.add, self.apply, self.delete):
            edit.addWidget(button)
        edit_layout.addWidget(scrolling_bar(controls))
        layout.addWidget(self.editor)
        self.scope.currentIndexChanged.connect(self.refresh)
        self.parts.currentIndexChanged.connect(self.render_score)
        refresh.clicked.connect(self.refresh)
        self.xml_button.clicked.connect(self.export_xml)
        self.pdf_button.clicked.connect(self.export_pdf)
        self.previous.clicked.connect(lambda: self.show_page(self.page - 1))
        self.next.clicked.connect(lambda: self.show_page(self.page + 1))
        self.zoom.currentIndexChanged.connect(lambda: self.show_page(self.page))
        self.table.itemSelectionChanged.connect(self.select_note)
        self.add.clicked.connect(lambda: self.write_note(False))
        self.apply.clicked.connect(lambda: self.write_note(True))
        self.delete.clicked.connect(self.delete_note)
        self.page = 1
        self.page_count = 0
        self.xml_button.setEnabled(False)
        self.pdf_button.setEnabled(False)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self, *_):
        selected = self.parts.currentData()
        self.parts.blockSignals(True)
        self.parts.clear()
        self.parts.addItem("Full score · all instruments", None)
        try:
            self.score = collect_score(self.app.project, self.scope.currentIndex() == 1)
            for part in self.score.parts:
                self.parts.addItem(part.name + (" · percussion" if part.percussion else ""), part.key)
            self.parts.setCurrentIndex(max(0, self.parts.findData(selected)))
        except ValueError as exc:
            self.score = None
            self.message.setText(str(exc))
        finally:
            self.parts.blockSignals(False)
        target = self.target.currentData()
        self.target.clear()
        self.target.addItem(self.app.project.synth.name, "synth")
        for instrument in self.app.project.instruments:
            self.target.addItem(instrument.name, f"instrument:{instrument.id}")
        for index, pad in enumerate(self.app.project.pads):
            if not pad.empty:
                self.target.addItem(pad.name or f"Sample {index + 1}", f"pad:{index}")
        self.target.setCurrentIndex(max(0, self.target.findData(target)))
        self.bar.setMaximum(self.app.project.pattern().bars)
        self.render_score()

    def render_score(self, *_):
        self.xml = ""
        self.toolkit = None
        self.page_count = 0
        self.paper.hide()
        self.xml_button.setEnabled(False)
        self.pdf_button.setEnabled(False)
        self.refresh_notes()
        if self.score is not None:
            self.message.setText(
                "4/4 · concert pitch · sharps · notation rounded to 1/16; playback stays unchanged. "
                "Song includes all placed parts, including muted clips."
                + (f" {self.score.audio_clips} audio clip(s): use Audio → score to detect their notes."
                   if self.score.audio_clips else "")
            )
            try:
                self.xml = musicxml(self.score, self.parts.currentData())
                self.xml_button.setEnabled(True)
                import verovio

                self.toolkit = verovio.toolkit()
                self.toolkit.setOptions({"pageWidth": 2100, "pageHeight": 2970, "scale": 40,
                                         "adjustPageHeight": False, "breaks": "auto"})
                if not self.toolkit.loadData(self.xml):
                    raise ValueError("The notation engine could not read this score.")
                self.page_count = self.toolkit.getPageCount()
                self.pdf_button.setEnabled(self.page_count > 0)
            except (ImportError, RuntimeError, ValueError) as exc:
                self.message.setText(self.message.text() + "\n" + str(exc))
        self.show_page(1)

    def show_page(self, page):
        self.page = max(1, min(page, self.page_count))
        self.previous.setEnabled(self.page > 1)
        self.next.setEnabled(self.page < self.page_count)
        self.page_label.setText(f"Page {self.page} of {self.page_count}" if self.page_count else "No score pages")
        if not self.toolkit or not self.page_count:
            return
        svg = self.toolkit.renderToSVG(self.page)
        self.paper.load(qt_score_svg(svg))
        factor = (0.75, 1, 1.25, 1.5)[self.zoom.currentIndex()]
        self.paper.resize(self.paper.renderer().defaultSize() * factor)
        self.paper.show()

    def convert_audio(self):
        from .transcription import TranscriptionDialog

        dialog = TranscriptionDialog(self.app)
        self.transcription_dialog = dialog
        dialog.exec()
        self.transcription_dialog = None
        dialog.deleteLater()

    def shutdown(self):
        if self.transcription_dialog is not None:
            self.transcription_dialog.cancel_conversion()

    def refresh_notes(self):
        self.editor.setVisible(self.scope.currentIndex() == 0)
        key = self.parts.currentData()
        if self.target.findData(key) >= 0:
            self.target.setCurrentIndex(self.target.findData(key))
        self.note_rows = [n for n in self.app.project.pattern().notes if key is None or note_key(n) == key]
        names = {p.key: p.name for p in self.score.parts} if self.score else {}
        self.table.setRowCount(0)
        for note in self.note_rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            pitch = ("C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B")[note.pitch % 12]
            values = (names.get(note_key(note), note_key(note)), f"{pitch}{note.pitch // 12 - 1}",
                      f"{int(note.start // 4) + 1} : {note.start % 4 + 1:g}", f"{note.duration:g}")
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.select_note()

    def selected_note(self):
        row = self.table.currentRow()
        return self.note_rows[row] if 0 <= row < len(self.note_rows) else None

    def select_note(self):
        note = self.selected_note()
        self.apply.setEnabled(note is not None)
        self.delete.setEnabled(note is not None)
        if note is not None:
            self.pitch.setValue(note.pitch)
            self.bar.setValue(int(note.start // 4) + 1)
            self.beat.setValue(note.start % 4 + 1)
            self.duration.setValue(note.duration)
            self.target.setCurrentIndex(max(0, self.target.findData(note_key(note))))

    def write_note(self, replace):
        pattern = self.app.project.pattern()
        old = self.selected_note() if replace else None
        if replace and (old is None or not any(n is old for n in pattern.notes)):
            self.refresh()
            return
        start = (self.bar.value() - 1) * 4 + self.beat.value() - 1
        if start + self.duration.value() > pattern.length_beats:
            QMessageBox.information(self, "Note exceeds pattern", "Shorten the note or extend the pattern in Notes.")
            return
        key = self.target.currentData()
        note = Note(pitch=self.pitch.value(), start=start, duration=self.duration.value(),
                    pad=int(key.split(":", 1)[1]) if key.startswith("pad:") else None,
                    instrument=key.split(":", 1)[1] if key.startswith("instrument:") else None,
                    velocity=old.velocity if old else .8,
                    channel=old.channel if old else 0,
                    release_velocity=old.release_velocity if old else 0)
        self.app.snapshot()
        if old is not None:
            index = next(i for i, n in enumerate(pattern.notes) if n is old)
            pattern.notes[index] = note
        else:
            pattern.notes.append(note)
        self.commit()

    def delete_note(self):
        note = self.selected_note()
        pattern = self.app.project.pattern()
        if note is not None and any(n is note for n in pattern.notes):
            self.app.snapshot()
            pattern.notes[:] = [n for n in pattern.notes if n is not note]
            self.commit()

    def commit(self):
        self.app._set_dirty(True)
        self.app.piano_roll.canvas.refresh()
        self.refresh()

    def export_xml(self):
        if not self.xml:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export MusicXML", safe_filename(self.score.title) + ".musicxml",
                                             "MusicXML (*.musicxml)")
        if path:
            try:
                Path(path).write_text(self.xml, encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "Export failed", str(exc))

    def write_pdf(self, path):
        writer = QPdfWriter(str(path))
        writer.setPageSize(QPageSize(QPageSize.A4))
        writer.setResolution(144)
        writer.setTitle(self.score.title)
        painter = QPainter(writer)
        if not painter.isActive():
            raise OSError("Could not write the PDF file.")
        try:
            for page in range(1, self.page_count + 1):
                if page > 1 and not writer.newPage():
                    raise OSError("Could not create a PDF page.")
                renderer = QSvgRenderer(qt_score_svg(self.toolkit.renderToSVG(page)))
                size = renderer.defaultSize()
                factor = min(writer.width() / size.width(), writer.height() / size.height())
                renderer.render(painter, QRectF(0, 0, size.width() * factor, size.height() * factor))
        finally:
            painter.end()

    def export_pdf(self):
        if not self.page_count:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export score PDF", safe_filename(self.score.title) + ".pdf",
                                             "PDF (*.pdf)")
        if path:
            try:
                self.write_pdf(path)
            except (OSError, RuntimeError) as exc:
                QMessageBox.warning(self, "Export failed", str(exc))
