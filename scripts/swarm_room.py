#!/usr/bin/env python3
"""Local, read-only visualization of supervised production-repair agents.

Run with the project's Python. Agents publish explicit events with --update;
the window never executes tasks, opens project media, or sends network traffic.
"""

import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "automation" / "production_swarm"
ROLES = [
    (
        "research",
        "ATLAS",
        "Research",
        "#80c9ed",
        "Observant / evidence first",
        "Investigate DAW architecture, plugin ownership, recording and realtime safety; cite primary sources.",
        "Read source and official documentation; publish evidence; no application edits.",
    ),
    (
        "integration",
        "FOREMAN",
        "Integration planner",
        "#ecc576",
        "Methodical / dependency keeper",
        "Turn research and complaints into scoped changes, ownership and release gates.",
        "Coordinate shared files; integrate only reviewed changes; no automatic release or push.",
    ),
    (
        "vst",
        "PATCH",
        "Instruments / VST",
        "#af98ec",
        "Precise / isolation specialist",
        "Separate instrument state, plugin instances, editor targeting and recorded melodies by track.",
        "Own instrument state and hosting; preserve old projects; prove two-track independence.",
    ),
    (
        "ui",
        "PRISM",
        "Layout / controls",
        "#ed95bd",
        "Clear / interaction engineer",
        "Find inert controls, confusing selection and expensive UI updates; repair concrete failures.",
        "Scoped UI edits; no broad redesign; coordinate shared window files.",
    ),
    (
        "ux",
        "LUMEN",
        "UX / UI director",
        "#e9dc9b",
        "Empathetic / workflow critic",
        "Review record, sample, select, edit and export journeys; direct the UI specialist.",
        "Read-only review first; flag evidence and severity; no invented usability claims.",
    ),
    (
        "mixer",
        "BUS",
        "Mixer / mastering",
        "#8ed7b1",
        "Measured / signal steward",
        "Audit routing, solo/mute, gain, inserts and export agreement.",
        "Finite audio and isolation tests; no loud playback or changes to system audio.",
    ),
    (
        "sequence",
        "TICK",
        "Sequence / beats / notes",
        "#efa47a",
        "Rhythmic / boundary watcher",
        "Audit note ownership, clip timing, beat edits, selection and transport boundaries.",
        "Deterministic timing tests; protect pattern identity and undo; coordinate model edits.",
    ),
    (
        "sampling",
        "SPLICE",
        "MPC / recording",
        "#83d5d8",
        "Tactile / take guardian",
        "Audit pad triggering, sampling, arm/record/stop, take capture and input handling.",
        "Synthetic audio tests; never overwrite songs, samples or takes; no microphone activation.",
    ),
    (
        "stability",
        "ANCHOR",
        "Stability / repository",
        "#a8b9e4",
        "Patient / resource keeper",
        "Reproduce crashes and lag; inspect CI and repository health; repair verified faults.",
        "Bound test memory and concurrency; no deleting branches, credentials or creative media.",
    ),
    (
        "audit",
        "SENTINEL",
        "Independent auditor",
        "#cad494",
        "Skeptical / regression hunter",
        "Review all changes for failed logic, run focused regressions and report release blockers.",
        "No rubber stamps; completion requires evidence; hardware acceptance stays explicit.",
    ),
]
VALID = {"queued", "running", "review", "passed", "blocked", "waiting"}


def publish(role, status, task, evidence="", agent=""):
    if role not in {r[0] for r in ROLES} or status not in VALID:
        raise ValueError("Unknown role or status")
    STATE.mkdir(parents=True, exist_ok=True)
    path = STATE / f"{role}.json"
    previous = json.loads(path.read_text()) if path.exists() else {}
    now = time.time()
    events = previous.get("events", [])[-29:]
    events.append({"at": now, "status": status, "task": task, "evidence": evidence})
    value = {
        "role": role,
        "status": status,
        "task": task,
        "evidence": evidence,
        "agent": agent or previous.get("agent", ""),
        "updated": now,
        "events": events,
    }
    fd, temporary = tempfile.mkstemp(prefix=f".{role}-", dir=STATE)
    with os.fdopen(fd, "w") as out:
        json.dump(value, out, indent=2)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", choices=[r[0] for r in ROLES])
    parser.add_argument("--status", choices=sorted(VALID), default="running")
    parser.add_argument("--task", default="")
    parser.add_argument("--evidence", default="")
    parser.add_argument("--agent", default="")
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    if args.update:
        publish(args.update, args.status, args.task, args.evidence, args.agent)
        return

    from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QPainter
    from PySide6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QMainWindow,
        QPushButton,
        QTextBrowser,
        QVBoxLayout,
        QWidget,
    )

    class Room(QWidget):
        def __init__(self, select):
            super().__init__()
            self.select = select
            self.selected = 0
            self.states = {}
            self.setMinimumSize(620, 470)
            self.setMouseTracking(True)
            self.hitboxes = []

        def mousePressEvent(self, event):
            for i, rect in self.hitboxes:
                if rect.contains(event.position()):
                    self.select(i)
                    break

        def paintEvent(self, event):
            p = QPainter(self)
            p.fillRect(self.rect(), QColor("#101b28"))
            sx, sy = self.width() / 960, self.height() / 670
            p.scale(sx, sy)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

            def rect(x, y, w, h, color):
                p.fillRect(QRectF(x, y, w, h), QColor(color))

            def text(x, y, value, color="#bacbd7", size=10):
                p.setPen(QColor(color))
                p.setFont(QFont("DejaVu Sans", size))
                p.drawText(QPointF(x, y), value)

            # An original orbital recording workshop, rendered entirely in code.
            for x in range(32, 950, 43):
                for y in range(25, 150, 37):
                    if (x + y) % 5 < 2:
                        rect(x, y, 2, 2, "#7491a5")
            rect(25, 156, 910, 482, "#1c2c3d")
            rect(25, 156, 910, 8, "#516579")
            rect(25, 632, 910, 6, "#516579")
            for x in range(25, 935, 45):
                p.setPen(QColor("#26394d"))
                p.drawLine(x, 165, x, 632)
            for y in range(165, 633, 39):
                p.drawLine(25, y, 935, y)
            text(35, 43, "O R B I T A L   S O U N D W O R K S", "#f0d49b", 18)
            text(35, 72, "ANHARMONIC STUDIO  /  PRODUCTION REPAIR CREW", size=10)
            text(
                35,
                105,
                "Select a character or station to inspect its assignment and evidence.",
                size=10,
            )
            text(
                35,
                132,
                "Animation indicates a reported task state. It is not a measure of progress.",
                "#8099ad",
                9,
            )
            self.hitboxes = []
            for i, role in enumerate(ROLES):
                rid, name, label, color, *_ = role
                col, row = i % 5, i // 5
                x, y = 43 + col * 177, 191 + row * 216
                state = self.states.get(rid, {})
                status = state.get("status", "queued")
                running = status == "running"
                selected = self.selected == i
                rect(x, y, 163, 192, "#2c4054" if selected else "#203246")
                rect(x, y, 163, 3, color if selected else "#405466")
                # Console, display, and functional status lamp.
                rect(x + 15, y + 28, 129, 49, "#0d1722")
                rect(x + 21, y + 33, 115, 32, "#152b36")
                for bar in range(12):
                    height = 5 + ((bar * 7 + i * 3) % 21)
                    if running:
                        height += int(3 * math.sin(time.monotonic() * 2 + bar))
                    rect(
                        x + 27 + bar * 8,
                        y + 62 - height,
                        4,
                        height,
                        color if running else "#395266",
                    )
                rect(x + 11, y + 77, 138, 13, "#556475")
                rect(x + 17, y + 90, 8, 35, "#344656")
                rect(x + 135, y + 90, 8, 35, "#344656")
                # Block character: cuboid head, coat, arms, boots and individual headgear.
                cx, cy = x + 69, y + 83
                rect(cx - 9, cy + 65, 60, 5, "#142333")
                rect(cx, cy, 30, 27, "#d7b698")
                rect(cx + 30, cy + 4, 8, 23, "#9b7f71")
                rect(cx, cy - 6, 30, 9, color)
                rect(cx + 6 + i % 3 * 6, cy - 13, 9, 7, color)
                rect(cx + 5, cy + 10, 5, 5, "#152234")
                rect(cx + 21, cy + 10, 5, 5, "#152234")
                if i in (0, 2, 9):
                    rect(cx + 3, cy + 8, 25, 3, "#e5f2ef")
                rect(cx - 1, cy + 29, 34, 25, color)
                rect(cx + 24, cy + 30, 9, 24, QColor(color).darker(145).name())
                rect(cx + 4, cy + 35, 8, 8, "#e2e8df")
                offset = int(math.sin(time.monotonic() * 3 + i) * 3) if running else 0
                rect(cx - 11, cy + 29 + offset, 9, 22, color)
                rect(cx + 34, cy + 29 - offset, 9, 22, color)
                rect(cx + 1, cy + 55, 11, 13, "#111e2b")
                rect(cx + 21, cy + 55, 11, 13, "#111e2b")
                text(x + 13, y + 18, name, color, 10)
                text(x + 13, y + 168, label, "#e2eaf0", 8)
                status_color = {
                    "running": "#8fe0b9",
                    "blocked": "#efad87",
                    "passed": "#b7d893",
                }.get(status, "#9bacc0")
                text(x + 13, y + 183, status.upper(), status_color, 8)
                self.hitboxes.append((i, QRectF(x * sx, y * sy, 163 * sx, 192 * sy)))
            text(
                35,
                660,
                "LOCAL TELEMETRY  •  3 SPECIALIST SLOTS  •  REVIEW BEFORE INTEGRATION",
                "#8ca5b8",
                9,
            )
            p.end()

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Orbital Soundworks — Anharmonic Agent Control Room")
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.resize(1450, 920)
            self.states = {}
            self.last_detail = None
            self.setStyleSheet("""
                QWidget { background: #101b28; color: #dce6ee; font: 12px 'DejaVu Sans'; }
                QLabel#headline { color: #f0d49b; font-size: 24px; font-weight: bold; }
                QListWidget, QTextBrowser { background: #172637; border: 1px solid #33485b; padding: 8px; }
                QListWidget::item { padding: 7px; }
                QListWidget::item:selected { background: #354b62; color: #fff0cc; }
                QPushButton { background: #293e52; border: 1px solid #4b6275; padding: 8px; }
            """)
            central = QWidget()
            self.setCentralWidget(central)
            layout = QVBoxLayout(central)
            heading = QLabel("ORBITAL SOUNDWORKS     /     Production control")
            heading.setObjectName("headline")
            layout.addWidget(heading)
            self.summary = QLabel()
            layout.addWidget(self.summary)
            body = QHBoxLayout()
            layout.addLayout(body, 1)
            left = QVBoxLayout()
            body.addLayout(left, 3)
            self.room = Room(self.select)
            left.addWidget(self.room, 4)
            self.feed = QTextBrowser()
            self.feed.setMaximumHeight(180)
            left.addWidget(self.feed, 1)
            right = QVBoxLayout()
            body.addLayout(right, 1)
            self.roster = QListWidget()
            for role in ROLES:
                self.roster.addItem(f"{role[1]}  /  {role[2]}")
            self.roster.currentRowChanged.connect(self.select)
            right.addWidget(self.roster, 2)
            self.detail = QTextBrowser()
            right.addWidget(self.detail, 3)
            self.motion = QPushButton("Pause character motion")
            self.motion.clicked.connect(self.toggle_motion)
            right.addWidget(self.motion)
            self.policy = QLabel(
                "Safeguards: local files only · no commands from this window · no media changes · no automatic publishing\nClosing this viewer does not stop agents. Agent execution is supervised in the chat session."
            )
            self.policy.setWordWrap(True)
            layout.addWidget(self.policy)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.room.update)
            self.timer.start(150)
            poll = QTimer(self)
            poll.timeout.connect(self.refresh)
            poll.start(1500)
            self.roster.setCurrentRow(0)
            self.refresh()

        def toggle_motion(self):
            if self.timer.isActive():
                self.timer.stop()
                self.motion.setText("Resume character motion")
            else:
                self.timer.start(150)
                self.motion.setText("Pause character motion")

        def select(self, index):
            if not 0 <= index < len(ROLES):
                return
            self.room.selected = index
            if self.roster.currentRow() != index:
                self.roster.setCurrentRow(index)
            self.show_detail()
            self.room.update()

        def show_detail(self):
            role = ROLES[self.room.selected]
            state = self.states.get(role[0], {})
            signature = (role[0], json.dumps(state, sort_keys=True))
            if signature == self.last_detail:
                return
            self.last_detail = signature
            stamp = state.get("updated")
            updated = (
                time.strftime("%H:%M:%S", time.localtime(stamp)) if stamp else "No agent event yet"
            )
            self.detail.setPlainText(
                f"{role[1]} — {role[2]}\n{role[4]}\n\nASSIGNMENT / SKILLS\n{role[5]}\n\n"
                f"CONSTRAINTS\n{role[6]}\n\nSTATUS: {state.get('status', 'queued').upper()}\n"
                f"{state.get('task', 'Waiting for supervised dispatch after the window opens.')}\n\n"
                f"EVIDENCE\n{state.get('evidence') or 'None reported yet.'}\n\n"
                f"AGENT\n{state.get('agent') or 'Not dispatched'}\nLast reported: {updated}\n\n"
                "Status is explicitly reported by the agent or supervisor; there is no simulated completion percentage."
            )

        def refresh(self):
            events = []
            errors = []
            for role in ROLES:
                path = STATE / f"{role[0]}.json"
                try:
                    if path.exists():
                        self.states[role[0]] = json.loads(path.read_text())
                except (OSError, ValueError) as error:
                    errors.append(f"{role[1]} telemetry unavailable: {type(error).__name__}")
                state = self.states.get(role[0], {})
                for item in state.get("events", []):
                    events.append((item["at"], role[1], item["status"], item["task"]))
            self.room.states = self.states
            active = sum(s.get("status") == "running" for s in self.states.values())
            reviewed = sum(s.get("status") == "passed" for s in self.states.values())
            self.summary.setText(
                f"{active} roles reporting work  /  {reviewed} reviews passed  /  10 stations     •     Polling local event files every 1.5 seconds"
            )
            lines = [
                f"{time.strftime('%H:%M:%S', time.localtime(at))}  {name} [{status}]  {task}"
                for at, name, status, task in sorted(events, reverse=True)[:15]
            ]
            feed = (
                "\n".join(errors + lines)
                or "Control room ready. Specialists are queued; no repair work has been reported yet."
            )
            if self.feed.toPlainText() != feed:
                self.feed.setPlainText(feed)
            self.show_detail()
            self.room.update()

    app = QApplication(sys.argv[:1])
    app.setDesktopFileName("anharmonic-swarm-room")
    window = Window()
    window.show()
    if args.snapshot:
        QTimer.singleShot(200, lambda: (window.grab().save(str(args.snapshot)), app.quit()))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
