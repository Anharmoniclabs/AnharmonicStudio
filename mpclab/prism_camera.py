"""Opt-in local camera inference, isolated from the UI and audio processes."""

from __future__ import annotations

import multiprocessing
from pathlib import Path
import queue
import time
import os

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)


def model_path():
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return root / "anharmonic-studio/models/hand_landmarker.task"


def camera_worker(device, model, messages, stop):
    """Only this child imports camera/ML libraries or opens a camera."""
    messages.cancel_join_thread()
    camera = detector = None
    try:
        import cv2
        import mediapipe as mp
        import numpy as np

        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=model),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.65,
            min_hand_presence_confidence=0.65,
            min_tracking_confidence=0.65,
        )
        detector = mp.tasks.vision.HandLandmarker.create_from_options(options)
        camera = cv2.VideoCapture(device)
        if not camera.isOpened():
            raise RuntimeError("Camera unavailable. Check its permission or choose another camera.")
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        previous = 0
        while not stop.is_set():
            started = time.monotonic()
            ok, frame = camera.read()
            if not ok:
                raise RuntimeError("Camera stopped delivering frames. Stop and reconnect it.")
            rgb = cv2.cvtColor(cv2.flip(frame, 1), cv2.COLOR_BGR2RGB)
            rgb = np.ascontiguousarray(cv2.resize(rgb, (480, 360)))
            stamp = max(previous + 1, int(time.monotonic() * 1000))
            previous = stamp
            result = detector.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), stamp
            )
            points = (
                tuple((p.x, p.y, p.z) for p in result.hand_landmarks[0])
                if result.hand_landmarks
                else ()
            )
            message = ("frame", time.monotonic(), rgb, points)
            try:
                messages.put_nowait(message)
            except queue.Full:
                pass  # Bounded preview; never queue a growing video backlog.
            stop.wait(max(0, 1 / 24 - (time.monotonic() - started)))
    except Exception as exc:
        # Make room for a visible failure instead of leaving the last frame active.
        try:
            messages.get_nowait()
        except queue.Empty:
            pass
        try:
            messages.put(("error", str(exc)), timeout=0.1)
        except queue.Full:
            pass
    finally:
        if camera is not None:
            camera.release()
        if detector is not None:
            detector.close()


class CameraSession:
    def __init__(self):
        self.process = None
        self.messages = None
        self.stop_event = None

    def start(self, device=0):
        import importlib.util

        if self.process is not None:
            raise RuntimeError("Stop the current camera before starting another")
        if any(importlib.util.find_spec(name) is None for name in ("cv2", "mediapipe")):
            raise RuntimeError("Install camera support first: uv sync --extra camera")
        if not model_path().is_file():
            raise RuntimeError("Hand model missing. Run scripts/setup_prism_camera.py first.")
        context = multiprocessing.get_context("spawn")
        self.messages = context.Queue(maxsize=2)
        self.stop_event = context.Event()
        self.process = context.Process(
            target=camera_worker,
            args=(device, str(model_path()), self.messages, self.stop_event),
            name="Prism camera tracking",
            daemon=True,
        )
        self.process.start()

    def poll(self):
        latest = None
        if self.messages is not None:
            try:
                while True:
                    latest = self.messages.get_nowait()
            except queue.Empty:
                pass
        if latest is None and self.process is not None and not self.process.is_alive():
            return ("error", "Camera tracking stopped. Restart the camera to reconnect.")
        return latest

    def stop(self):
        if self.process is not None:
            self.stop_event.set()
            self.process.join(timeout=0.3)
            if self.process.is_alive():
                # This handle belongs only to the child created in start(),
                # never to Studio, another camera app, or the active chat.
                self.process.terminate()
                self.process.join(timeout=0.3)
            self.process.close()
            self.process = None
        if self.messages is not None:
            self.messages.close()
            self.messages = None
