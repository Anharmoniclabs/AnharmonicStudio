"""Prism's opt-in hand performance and gesture overdub panel."""

from pathlib import Path
import time

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import QImage, QPainter, QColor, QPen
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QCheckBox,
    QDoubleSpinBox,
    QWidget,
    QProgressBar,
    QFrame,
)

from .window_client import WindowClient
from ..prism_camera import CameraSession
from ..prism_motion import CONTROLS, FINGER_EFFECTS, GestureFrame, GestureTake, FingerFXMapper

CONNECTIONS = (
    (0, 1, 2, 3, 4),
    (0, 5, 6, 7, 8),
    (5, 9, 10, 11, 12),
    (9, 13, 14, 15, 16),
    (13, 17, 18, 19, 20),
    (0, 17),
)


class HandPreview(QWidget):
    def __init__(self):
        super().__init__()
        self.image = QImage()
        self.points = ()
        self.active = False
        self.finger = None
        self.names = [effect[2] for effect in FINGER_EFFECTS]
        self.setMinimumSize(420, 230)
        self.setAccessibleName("Mirrored camera preview and hand landmarks")

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#161718"))
        if self.image.isNull():
            p.setRenderHint(QPainter.Antialiasing)
            cx, cy = self.width() / 2, self.height() / 2
            scale = min(self.height() / 300, self.width() / 600)

            def at(x, y):
                return QPointF(cx + x * scale, cy + y * scale)

            p.setPen(QPen(QColor("#313944"), 1))
            for radius in (75, 115, 155):
                p.drawEllipse(at(0, 0), radius * scale, radius * scale)
            wrist = at(0, 95)
            tips = [(-65, -72), (-20, -108), (27, -91), (67, -50)]
            for i, (x, y) in enumerate(tips):
                color = QColor(FINGER_EFFECTS[i][4])
                p.setPen(QPen(QColor("#657781"), 9 * scale, Qt.SolidLine, Qt.RoundCap))
                joint = at(x * 0.65, 15)
                p.drawLine(wrist, joint)
                p.drawLine(joint, at(x, y))
                p.setPen(Qt.NoPen)
                glow = QColor(color)
                glow.setAlpha(35)
                p.setBrush(glow)
                p.drawEllipse(at(x, y), 18 * scale, 18 * scale)
                p.setBrush(color)
                p.drawEllipse(at(x, y), 6 * scale, 6 * scale)
                p.setPen(color)
                p.drawText(at(x - 17, y - 25), self.names[i])
            p.setPen(QPen(QColor("#c6d4ce"), 9 * scale, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(wrist, at(-72, 35))
            p.drawLine(at(-72, 35), at(-105, -5))
            p.setPen(QColor("#b9c9c3"))
            p.drawText(
                QRectF(0, self.height() - 30, self.width(), 25),
                Qt.AlignCenter,
                "FOUR FINGERS. FOUR WAYS TO SHAPE YOUR SOUND.",
            )
            return
        size = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
        r = QRectF(
            (self.width() - size.width()) / 2,
            (self.height() - size.height()) / 2,
            size.width(),
            size.height(),
        )
        p.drawImage(r, self.image)
        p.fillRect(r, QColor(12, 16, 25, 75))

        def point(index):
            x, y, _ = self.points[index]
            return QPointF(r.left() + x * r.width(), r.top() + y * r.height())

        if len(self.points) == 21:
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor("#f2c77d" if self.active else "#eeeae2"), 2))
            for chain in CONNECTIONS:
                for a, b in zip(chain, chain[1:], strict=False):
                    p.drawLine(point(a), point(b))
            for finger, index in enumerate((8, 12, 16, 20)):
                color = QColor(FINGER_EFFECTS[finger][4])
                selected = self.active and self.finger == finger
                glow = QColor(color)
                glow.setAlpha(65 if selected else 25)
                p.setPen(Qt.NoPen)
                p.setBrush(glow)
                p.drawEllipse(point(index), 23 if selected else 13, 23 if selected else 13)
                p.setBrush(color)
                p.drawEllipse(point(index), 7, 7)
                p.setPen(QPen(color, 3))
                if selected:
                    p.drawLine(point(4), point(index))
                    p.drawEllipse(point(4), 12, 12)
                p.drawText(point(index) + QPointF(12, -12), self.names[finger])


class PrismCameraDialog(WindowClient, QDialog):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.app = panel.app
        self.setWindowTitle("Prism · Hand FX")
        self.resize(760, 740)
        self.session = CameraSession()
        self.mapper = FingerFXMapper()
        self.take = GestureTake()
        self._project = self.app.project
        self._last_frame = 0
        self._gesture = False
        self._was_writing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        title = QLabel("PRISM  /  HAND FX")
        title.setStyleSheet(
            "font-size: 24px; font-weight: 600; letter-spacing: 3px; color: #d7fff1;"
        )
        layout.addWidget(title)
        hint = QLabel(
            "Play a Prism sound. Touch a finger to your thumb to grab its effect.\n"
            "Move up or right for more · down or left for less · release to keep it."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.preview = HandPreview()
        layout.addWidget(self.preview, 1)
        self.status = QLabel("Camera off · no video is saved or uploaded")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        cards = QHBoxLayout()
        self.effect_cards = []
        self.effect_names = []
        self.effect_details = []
        self.effect_meters = []
        for finger, key, name, description, color in FINGER_EFFECTS:
            card = QFrame()
            card.setStyleSheet(
                f"QFrame {{ background: #22252d; border: 1px solid {color}; border-radius: 10px; }} QLabel {{ border: none; }}"
            )
            body = QVBoxLayout(card)
            label = QLabel(f"{finger.upper()} + THUMB")
            label.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: 600;")
            body.addWidget(label)
            title = QLabel(name)
            title.setStyleSheet("font-size: 21px; font-weight: 600; color: #f3f3f6;")
            body.addWidget(title)
            detail = QLabel(description)
            detail.setStyleSheet("font-size: 10px; color: #b8bac5;")
            body.addWidget(detail)
            self.effect_details.append(detail)
            meter = QProgressBar()
            meter.setRange(0, 100)
            meter.setValue(round(self.panel.values.get(key, 0) * 100))
            meter.setFormat("%p%")
            meter.setStyleSheet(
                f"QProgressBar {{ background: #15171e; border: none; border-radius: 4px; height: 18px; text-align: center; color: white; }} QProgressBar::chunk {{ background: {color}; border-radius: 4px; }}"
            )
            body.addWidget(meter)
            self.effect_cards.append(card)
            self.effect_names.append(title)
            self.effect_meters.append(meter)
            cards.addWidget(card)
        layout.addLayout(cards)
        settings_toggle = QPushButton("Customize effects & camera ▸")
        settings_toggle.setCheckable(True)
        layout.addWidget(settings_toggle)
        settings = QWidget()
        settings_layout = QVBoxLayout(settings)
        row = QHBoxLayout()
        self.camera = QComboBox()
        self.camera.setAccessibleName("Camera device")
        devices = sorted(Path("/sys/class/video4linux").glob("video*"))
        if devices:
            for device in devices:
                try:
                    name = (device / "name").read_text().strip()
                except OSError:
                    name = device.name
                self.camera.addItem(f"{name} · {device.name}", "/dev/" + device.name)
        else:
            for index in range(4):
                self.camera.addItem(f"Camera {index + 1}", index)
        row.addWidget(self.camera, 1)
        self.start = QPushButton("Start camera")
        self.start.clicked.connect(self.toggle_camera)
        row.addWidget(self.start)
        settings_layout.addLayout(row)
        grid = QGridLayout()
        self.mapping = []
        for index, (finger, key, _name, _, _) in enumerate(FINGER_EFFECTS):
            box = QComboBox()
            box.setAccessibleName(finger + " pinch effect")
            for target, label in CONTROLS.items():
                box.addItem(label, target)
            box.setCurrentIndex(box.findData(key))
            self.mapping.append(box)
            box.currentIndexChanged.connect(self.configure)
            grid.addWidget(QLabel(finger + " + thumb"), index // 2, (index % 2) * 2)
            grid.addWidget(box, index // 2, (index % 2) * 2 + 1)
        self.sensitivity = QDoubleSpinBox()
        self.sensitivity.setRange(0.25, 4)
        self.sensitivity.setSingleStep(0.25)
        self.sensitivity.setValue(1.5)
        self.sensitivity.valueChanged.connect(self.configure)
        grid.addWidget(QLabel("Movement response"), 2, 0)
        grid.addWidget(self.sensitivity, 2, 1)
        settings_layout.addLayout(grid)
        layout.addWidget(settings)
        settings.hide()
        settings_toggle.toggled.connect(settings.setVisible)
        # The main camera button stays visible; device selection is optional.
        row.removeWidget(self.start)
        layout.insertWidget(2, self.start)
        self.write = QCheckBox("Record effect moves with Song playback")
        self.write.setToolTip(
            "Record with a new take, or overdub movements over an existing take. "
            "Only the controls you touch are written. Audio recording is separate."
        )
        self.write.toggled.connect(self.release_gesture)
        layout.addWidget(self.write)
        self.take_status = QLabel("Live control only. Saved gestures replay with the camera off.")
        self.take_status.setWordWrap(True)
        layout.addWidget(self.take_status)
        row = QHBoxLayout()
        edit = QPushButton("Edit recorded moves")
        edit.clicked.connect(self.edit_curves)
        row.addWidget(edit)
        row.addStretch()
        close = QPushButton("Done")
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def configure(self, *_):
        if len(self.mapping) != 4:
            return
        self.release_gesture()
        for i, box in enumerate(self.mapping):
            self.effect_names[i].setText(box.currentText())
            self.preview.names[i] = box.currentText()
            self.effect_details[i].setText("Pinch · move to adjust")
        try:
            self.mapper = FingerFXMapper(
                tuple(box.currentData() for box in self.mapping), self.sensitivity.value()
            )
        except ValueError as exc:
            self.mapper = None
            self.status.setText(str(exc))

    def release_gesture(self, *_):
        self.app.engine.prism_gesture_targets = frozenset()
        if self.mapper is not None:
            self.mapper.release()
        self.take.end()
        self._gesture = False
        self.preview.finger = None
        self.preview.active = False
        for meter in self.effect_meters:
            meter.setFormat("%p%")
        self.preview.update()

    def toggle_camera(self):
        if self.session.process is not None:
            self.stop_camera()
            return
        try:
            self.session.start(self.camera.currentData())
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self._last_frame = time.monotonic()
        self.start.setText("Stop camera")
        self.camera.setEnabled(False)
        self.status.setText("Starting local hand tracking…")

    def stop_camera(self):
        self.release_gesture()
        self.session.stop()
        self.start.setText("Start camera")
        self.camera.setEnabled(True)
        self.preview.image = QImage()
        self.preview.points = ()
        self.preview.update()
        self.status.setText("Camera off · saved gestures remain in the project")

    def tick(self):
        if self.app.project is not self._project:
            self.release_gesture()
            self._project = self.app.project
            self.write.setChecked(False)
        engine = self.app.engine
        writing = self.write.isChecked() and engine.playing and engine.mode == "song"
        if writing != self._was_writing:
            self.release_gesture()
        self._was_writing = writing
        self.take_status.setText(
            "Writing touched controls into automation"
            if writing
            else "Armed · play the arrangement in Song mode to write gestures"
            if self.write.isChecked()
            else "Live control only. Saved gestures replay with the camera off."
        )
        message = self.session.poll()
        if message is None:
            if self._gesture and time.monotonic() - self._last_frame > 0.3:
                self.release_gesture()
                self.status.setText("Tracking paused · show your hand and pinch again")
            return
        if message[0] == "error":
            self.stop_camera()
            self.status.setText(message[1])
            return
        _, timestamp, rgb, points = message
        self._last_frame = timestamp
        if time.monotonic() - timestamp > 0.3:
            self.release_gesture()
            return
        self.preview.image = QImage(
            rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888
        ).copy()
        self.preview.points = points
        bridge = self.panel.bridge()
        if (
            self.mapper is None
            or getattr(bridge, "info", {}).get("name") != "Anharmonic Prism"
            or getattr(bridge, "error", "")
        ):
            self.release_gesture()
            self.status.setText(
                "Load a working Prism instrument and give each finger a different effect"
            )
            return
        # Use the latest plugin values for pickup after automation playback.
        current = dict(self.panel.values)
        current.update(
            {
                key: p["value"]
                for key, p in bridge.info.get("parameters", {}).items()
                if "value" in p
            }
        )
        values = self.mapper.update(GestureFrame(timestamp, points), current)
        if not values:
            if self._gesture:
                self.release_gesture()
            self.status.setText(
                "Ready · touch a fingertip to your thumb"
                if points
                else "Show one hand to the camera"
            )
        else:
            if not self._gesture:
                self.app.snapshot()
                self._gesture = True
            if getattr(engine, "prism_gesture_targets", frozenset()) != frozenset(values):
                self.take.end()
            engine.prism_gesture_targets = frozenset(values)
            try:
                self.panel.change(values)
                if writing and self.take.write(self.app.project, engine.beat, values):
                    self.app._set_dirty(True)
            except Exception as exc:
                self.release_gesture()
                self.write.setChecked(False)
                self.status.setText(str(exc))
                return
            self.status.setText(
                FINGER_EFFECTS[self.mapper.finger][0]
                + " pinch · "
                + "   ".join(f"{CONTROLS[k]} {v:.0%}" for k, v in values.items())
            )
        self.preview.finger = self.mapper.finger
        for i, box in enumerate(self.mapping):
            value = values.get(box.currentData(), current.get(box.currentData(), 0))
            self.effect_meters[i].setValue(round(value * 100))
            self.effect_meters[i].setFormat(
                "LIVE · %p%" if self._gesture and self.mapper.finger == i else "%p%"
            )
        self.preview.active = self._gesture
        self.preview.update()

    def edit_curves(self):
        editor = self.app.automation_panel
        target = "prism:" + self.mapping[0].currentData()
        editor.target.setCurrentIndex(editor.target.findData(target))
        self.app.show_tab(self.app.TAB_AUTO)
        editor.setFocus()
        self.take_status.setText("Prism curve selected in the automation editor")

    def closeEvent(self, event):
        self.stop_camera()
        self.timer.stop()
        super().closeEvent(event)

    def showEvent(self, event):
        self.timer.start()
        super().showEvent(event)
