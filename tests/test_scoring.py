from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
import verovio
from PySide6.QtGui import QImage, QPainter
from PySide6.QtCore import Qt

from mpclab.model import Project, Instrument, Row, Clip
from mpclab.music import Note
from mpclab.scoring import collect_score, musicxml
from mpclab.ui.scoring import ScoringPanel


def project_with_parts():
    project = Project()
    project.instruments = [Instrument(name="Strings & brass")]
    project.pattern().notes = [Note(60, 0, 1), Note(64, 0, 1), Note(67, .5, 2),
                               Note(48, 3.5, 3, instrument=project.instruments[0].id)]
    project.pattern().set(0, 0, .8)
    return project


def test_parts_chords_overlapping_voices_and_ties_preserve_project():
    project = project_with_parts()
    before = project.to_dict()
    score = collect_score(project)
    assert len(score.parts) == 3
    root = ET.fromstring(musicxml(score))
    assert root.findtext("part-list/score-part[2]/part-name") == "Strings & brass"
    assert root.findall(".//chord")
    assert root.findall(".//backup")
    assert root.findall(".//tie[@type='start']")
    assert root.findall(".//tie[@type='stop']")
    assert root.findall(".//unpitched")
    assert project.to_dict() == before
    for measure in root.findall(".//measure"):
        position = 0
        for event in measure:
            if event.tag == "backup":
                assert position == 16
                position -= int(event.findtext("duration"))
            elif event.tag == "note" and event.find("chord") is None:
                position += int(event.findtext("duration"))
        assert position == 16


def test_arrangement_repeats_trims_and_preserves_empty_bars():
    project = Project()
    pattern = project.pattern()
    pattern.bars = 1
    pattern.notes = [Note(60, 0, 8)]
    project.rows = [Row(clips=[Clip(ref=pattern.id, start_beat=4, length_beats=6),
                               Clip(kind="audio", start_beat=12, length_beats=4)])]
    score = collect_score(project, song=True)
    assert score.bars == 4
    assert score.audio_clips == 1
    assert [(n.start, n.duration) for n in score.parts[0].notes] == [(16, 16), (32, 8)]
    xml = ET.fromstring(musicxml(score))
    assert xml.find("part/measure/note/rest") is not None


def test_part_identity_and_rounding_at_final_barline():
    project = Project()
    project.instruments = [Instrument(name=project.synth.name)]
    project.pattern().bars = 1
    project.pattern().notes = [Note(60, 3.99, .01),
                               Note(72, 0, 1, instrument=project.instruments[0].id)]
    score = collect_score(project)
    assert len(score.parts) == 2
    root = ET.fromstring(musicxml(score, score.parts[0].key))
    assert len(root.findall("part")) == 1
    assert score.parts[0].notes[0].start == 15


def test_empty_score_does_not_invent_notes():
    with pytest.raises(ValueError, match="No notes"):
        musicxml(collect_score(Project()))


def test_real_engraver_renders_notes_and_multiple_pages():
    project = project_with_parts()
    project.pattern().bars = 32
    project.pattern().notes += [Note(72, beat, .25) for beat in range(128)]
    toolkit = verovio.toolkit()
    toolkit.setOptions({"pageWidth": 2100, "pageHeight": 2970, "scale": 40})
    assert toolkit.loadData(musicxml(collect_score(project)))
    assert toolkit.getPageCount() > 1
    for page in (1, toolkit.getPageCount()):
        svg = toolkit.renderToSVG(page)
        assert 'class="note"' in svg
        assert "<svg" in svg


def test_panel_composition_and_exports(tmp_path):
    project = project_with_parts()
    snapshots = []
    dirty = []
    app = SimpleNamespace(project=project, snapshot=lambda: snapshots.append(project.to_dict()),
                          _set_dirty=dirty.append,
                          piano_roll=SimpleNamespace(canvas=SimpleNamespace(refresh=lambda: None)))
    panel = ScoringPanel(app)
    panel.refresh()
    assert panel.page_count > 0
    assert panel.paper.renderer().isValid()
    image = QImage(840, 1188, QImage.Format_RGB32)
    image.fill(Qt.white)
    painter = QPainter(image)
    panel.paper.renderer().render(painter)
    painter.end()
    ink = sum(image.pixelColor(x, y).lightness() < 100
              for x in range(0, 840, 2) for y in range(0, 500, 2))
    assert ink > 200, "The rendered sheet must contain visible notation, not a blank SVG viewport"
    initial = len(project.pattern().notes)
    panel.pitch.setValue(74)
    panel.bar.setValue(2)
    panel.beat.setValue(1)
    panel.write_note(False)
    assert len(project.pattern().notes) == initial + 1
    assert project.pattern().notes[-1].pitch == 74
    assert project.pattern().notes[-1].start == 4
    panel.table.selectRow(initial)
    panel.pitch.setValue(75)
    panel.write_note(True)
    assert project.pattern().notes[-1].pitch == 75
    panel.table.selectRow(initial)
    panel.delete_note()
    assert len(project.pattern().notes) == initial
    assert len(snapshots) == 3 and dirty == [True] * 3
    panel.write_pdf(tmp_path / "score.pdf")
    assert (tmp_path / "score.pdf").read_bytes().startswith(b"%PDF")
    panel.parts.setCurrentIndex(2)
    assert len(ET.fromstring(panel.xml).findall("part")) == 1
    panel.scope.setCurrentIndex(1)
    assert not panel.xml and not panel.pdf_button.isEnabled()
    panel.deleteLater()
