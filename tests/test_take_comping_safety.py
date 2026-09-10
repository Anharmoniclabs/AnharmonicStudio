from __future__ import annotations

from copy import deepcopy

import pytest

from mpclab.model import Clip, Pattern, Project, Row
from mpclab.music import Note
from mpclab.take_comping import comp_row, swipe_comp_range, take_group
from mpclab.workflow_state import validate_workflow


def test_missing_note_source_fails_before_comp_or_playback_state_changes():
    project = Project(name="comp transaction safety", bpm=120.0)
    source = Row(id="source-notes", name="Keys", record_source="notes", record_track=2)
    broken = Row(
        id="lane-broken",
        name="Keys · Take 1",
        record_source="notes",
        record_track=2,
        mute=True,
    )
    active = Row(
        id="lane-active",
        name="Keys · Take 2",
        record_source="notes",
        record_track=2,
        mute=False,
    )
    broken.clips.append(
        Clip(kind="pattern", ref="missing-pattern", start_beat=0.0, length_beats=4.0)
    )
    active_pattern = Pattern(id="active-pattern", name="Take 2")
    active_pattern.notes = [Note(64, 0.0, 4.0, 0.8)]
    project.patterns = [active_pattern]
    active.clips.append(
        Clip(kind="pattern", ref=active_pattern.id, start_beat=0.0, length_beats=4.0)
    )
    project.rows = [source, broken, active]
    group_id = "takes:safety"
    project.workflow = validate_workflow(
        {
            "recording": {
                "take_groups": [
                    {
                        "id": group_id,
                        "name": "Keys Takes 1",
                        "source_row": source.id,
                        "lanes": [broken.id, active.id],
                        "start": 0.0,
                        "end": 4.0,
                        "active_lane": active.id,
                    }
                ]
            }
        }
    )

    before_rows = [row.id for row in project.rows]
    before_mutes = {row.id: row.mute for row in project.rows}
    before_patterns = deepcopy(project.patterns)
    before_workflow = deepcopy(project.workflow)

    with pytest.raises(ValueError, match="pattern is unavailable"):
        swipe_comp_range(project, group_id, broken.id, 1.0, 2.0)

    group = take_group(project, group_id)
    assert comp_row(project, group) is None
    assert [row.id for row in project.rows] == before_rows
    assert {row.id: row.mute for row in project.rows} == before_mutes
    assert project.patterns == before_patterns
    assert project.workflow == before_workflow
