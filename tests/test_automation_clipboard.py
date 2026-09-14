from types import SimpleNamespace

from mpclab.automation_clipboard import copy_automation_lane, paste_automation_lane
from mpclab.music import AutomationLane, AutomationPoint


class Status:
    def showMessage(self, *args):
        pass


class Panel:
    def __init__(self, window, target="master"):
        self.window = window
        self._target = target
        self.target = SimpleNamespace(currentData=lambda: self._target)
        self.syncs = 0

    def lane(self):
        return next(
            (lane for lane in self.window.project.automation if lane.target == self._target),
            None,
        )

    def sync(self):
        self.syncs += 1


class Window:
    def __init__(self):
        self.project = SimpleNamespace(
            automation=[
                AutomationLane(
                    "master",
                    [AutomationPoint(2.0, 0.2), AutomationPoint(6.0, 1.2)],
                    interpolation="smooth",
                )
            ]
        )
        self.engine = SimpleNamespace(beat=10.0)
        self.status = Status()
        self.automation_panel = Panel(self)
        self.snapshots = 0
        self.dirty = False

    def snapshot(self):
        self.snapshots += 1

    def _set_dirty(self, value):
        self.dirty = bool(value)


def test_copy_and_paste_preserves_curve_and_is_undoable():
    window = Window()
    copied = copy_automation_lane(window)
    window.automation_panel._target = "track:0:gain"
    result = paste_automation_lane(window)
    assert copied.interpolation == result.interpolation == "smooth"
    assert [(p.beat, p.value) for p in result.points] == [(2.0, 0.2), (6.0, 1.2)]
    assert window.snapshots == 1
    assert window.dirty
    assert window.automation_panel.syncs == 1


def test_paste_at_playhead_shifts_first_point_and_clamps_target_range():
    window = Window()
    window.project.automation[0].points[1].value = 1.3
    copy_automation_lane(window)
    window.automation_panel._target = "track:0:pan"
    result = paste_automation_lane(window, at_playhead=True)
    assert [(p.beat, p.value) for p in result.points] == [(10.0, 0.2), (14.0, 1.0)]


def test_paste_replaces_only_current_target_lane():
    window = Window()
    copy_automation_lane(window)
    old = AutomationLane("track:0:gain", [AutomationPoint(1.0, 0.7)])
    other = AutomationLane("track:1:gain", [AutomationPoint(1.0, 0.4)])
    window.project.automation.extend((old, other))
    window.automation_panel._target = "track:0:gain"
    replacement = paste_automation_lane(window)
    assert replacement in window.project.automation
    assert old not in window.project.automation
    assert other in window.project.automation
