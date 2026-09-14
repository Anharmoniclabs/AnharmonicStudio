"""The same explicit format-5 music fixture is checked by Python and JavaScript.

Audio bytes and browser-only UI metadata are deliberately outside this contract.
The fixture covers semantic values; the two clients may add their own defaults.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from mpclab.model import Project


FIXTURE = Path(__file__).parent / "web" / "fixtures" / "core-project.json"


def assert_semantics(testcase, expected, actual, path="project"):
    """Require every supplied native field, allowing filled defaults and UI IDs."""
    if isinstance(expected, dict):
        for key, value in expected.items():
            if key == "selected_pattern" or (
                key == "id" and (".pads[" in path or ".notes[" in path)
            ):
                continue  # Desktop addresses pads by index and notes by value.
            testcase.assertIn(key, actual, f"{path}.{key}")
            assert_semantics(testcase, value, actual[key], f"{path}.{key}")
    elif isinstance(expected, list):
        testcase.assertGreaterEqual(len(actual), len(expected), path)
        for index, value in enumerate(expected):
            assert_semantics(testcase, value, actual[index], f"{path}[{index}]")
    else:
        testcase.assertEqual(expected, actual, path)


class WebProjectInterchangeTests(unittest.TestCase):
    def test_browser_fixture_retains_every_explicit_native_music_field(self):
        document = json.loads(FIXTURE.read_text())
        project = Project.from_dict(document)
        assert_semantics(self, document, project.to_dict())
        self.assertEqual(project.current_pattern, document["current_pattern"])
        self.assertEqual(project.pattern().notes[0].pitch, 0)
        self.assertIsNone(project.pattern().notes[0].pad)
        self.assertEqual(project.pattern().notes[1].pad, 0)
        self.assertEqual(project.pads[0].sample_id, "audio-first")

    def test_native_save_reload_retains_the_shared_browser_fixture(self):
        document = json.loads(FIXTURE.read_text())
        first = Project.from_dict(document)
        second = Project.from_dict(json.loads(json.dumps(first.to_dict())))
        assert_semantics(self, document, second.to_dict())
        self.assertEqual(first.to_dict(), second.to_dict())


if __name__ == "__main__":
    unittest.main()
