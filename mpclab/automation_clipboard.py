"""Bounded automation lane copy/paste commands.

The clipboard is application-local on purpose: project automation stays typed,
validated and undoable instead of trusting arbitrary system-clipboard JSON.
"""

from __future__ import annotations

from dataclasses import dataclass

from .music import AutomationLane, AutomationPoint, target_range
from .workflow_commands import CommandSpec

MAX_CLIPBOARD_POINTS = 100_000


@dataclass(frozen=True)
class AutomationClipboard:
    interpolation: str
    enabled: bool
    points: tuple[tuple[float, float], ...]


def copy_automation_lane(window) -> AutomationClipboard:
    panel = window.automation_panel
    lane = panel.lane()
    if lane is None or not lane.points:
        raise ValueError("current automation lane has no points to copy")
    if len(lane.points) > MAX_CLIPBOARD_POINTS:
        raise ValueError("automation lane exceeds clipboard point limit")
    payload = AutomationClipboard(
        lane.interpolation,
        bool(lane.enabled),
        tuple((float(point.beat), float(point.value)) for point in lane.points),
    )
    window._automation_clipboard = payload
    window.status.showMessage(f"Copied {len(payload.points)} automation points", 3000)
    return payload


def paste_automation_lane(window, *, at_playhead: bool = False) -> AutomationLane:
    payload = getattr(window, "_automation_clipboard", None)
    if not isinstance(payload, AutomationClipboard) or not payload.points:
        raise ValueError("automation clipboard is empty")
    panel = window.automation_panel
    target = panel.target.currentData()
    lo, hi = target_range(target)
    shift = 0.0
    if at_playhead:
        first = min(point[0] for point in payload.points)
        shift = max(0.0, float(window.engine.beat)) - first
    points = [
        AutomationPoint(max(0.0, beat + shift), min(hi, max(lo, value)))
        for beat, value in payload.points
    ]
    window.snapshot()
    replacement = AutomationLane(
        target=target,
        points=points,
        enabled=payload.enabled,
        interpolation=payload.interpolation,
    )
    window.project.automation = [lane for lane in window.project.automation if lane.target != target]
    window.project.automation.append(replacement)
    window._set_dirty(True)
    panel.sync()
    window.status.showMessage(
        f"Pasted {len(points)} automation points" + (" at playhead" if at_playhead else ""),
        3000,
    )
    return replacement


def attach_automation_clipboard(window, controller):
    """Register copy/paste in the existing searchable command registry."""
    registry = controller.registry
    commands = (
        CommandSpec(
            "automation.copy_lane",
            "Copy current automation lane",
            lambda: controller._run(copy_automation_lane, window),
            category="Automation",
            keywords=("envelope", "points", "clipboard"),
        ),
        CommandSpec(
            "automation.paste_lane",
            "Paste automation lane",
            lambda: controller._run(paste_automation_lane, window),
            category="Automation",
            keywords=("envelope", "replace", "clipboard"),
        ),
        CommandSpec(
            "automation.paste_at_playhead",
            "Paste automation lane at playhead",
            lambda: controller._run(paste_automation_lane, window, at_playhead=True),
            category="Automation",
            keywords=("envelope", "offset", "clipboard"),
        ),
    )
    for command in commands:
        try:
            registry.get(command.id)
        except KeyError:
            registry.register(command)
    return commands
