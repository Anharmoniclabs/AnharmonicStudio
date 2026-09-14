"""Bounded worker monitoring: dry capture never runs pitch DSP in its callback."""

from __future__ import annotations
from copy import deepcopy
import queue
import threading
from collections import deque
import numpy as np
from .shifter import Shifter
from ..vocal import _pitch_frame, allowed_notes


class LiveMonitor:
    def __init__(self, settings, sr, output):
        self.settings = deepcopy(settings)
        self.settings.__post_init__()
        self.sr, self.output = sr, output
        self.queue = queue.Queue(maxsize=4)
        self.stop = threading.Event()
        self.failed = False
        self.overruns = 0
        self.error = ""
        self.shifter = Shifter(sr)
        self.latency_ms = 1000 * (self.shifter.delay + 2048 + 512) / sr
        self.worker = threading.Thread(target=self.run, daemon=True, name="vocal-corrected-monitor")
        try:
            self.worker.start()
        except BaseException:
            self.shifter.close()
            raise

    def push(self, block):
        if self.stop.is_set():
            return
        if self.failed:
            # The worker may still be inside output (including the stateful
            # rate adapter). Never overlap its final wet block with dry output
            # from the input callback. Capture itself continues uninterrupted.
            if not self.worker.is_alive():
                self.output(block)
            return
        try:
            self.queue.put_nowait(block.copy())
        except queue.Full:
            # Permanently fall back for this take; never alternate delayed wet
            # and undelayed dry on repeated overloads.
            self.overruns += 1
            self.failed = True
            self.error = "Corrected monitor overloaded; using dry monitoring"

    def close(self):
        self.stop.set()
        self.worker.join(timeout=0.25)

    def run(self):
        history = np.zeros((2048, 2), np.float32)
        pending = np.empty((0, 2), np.float32)
        settings, shifter = self.settings, self.shifter
        choices = allowed_notes(settings)
        low = 440 * 2 ** ((settings.low_note - 69) / 12)
        high = 440 * 2 ** ((settings.high_note - 69) / 12)
        state = 0.0
        movement = deque(maxlen=10)
        previous_target = None
        dry_pending = np.empty((0, 2), np.float32)
        discard = shifter.delay
        try:
            for start in range(0, shifter.pad, shifter.block):
                for out in shifter.push(
                    np.zeros((min(shifter.block, shifter.pad - start), 2), np.float32)
                ):
                    discard -= min(discard, len(out))
            while not self.stop.is_set() and not self.failed:
                try:
                    block = self.queue.get(timeout=0.03)
                except queue.Empty:
                    continue
                pending = np.concatenate((pending, block))
                while len(pending) >= shifter.block and not self.stop.is_set() and not self.failed:
                    block, pending = pending[: shifter.block], pending[shifter.block :]
                    history[: -len(block)] = history[len(block) :]
                    history[-len(block) :] = block
                    hz, confidence = _pitch_frame(history[:, 0], self.sr, low, high)
                    error = 0.0
                    if hz and confidence >= 0.35 and settings.enabled:
                        midi = 69 + 12 * np.log2(hz / 440)
                        target = choices[np.argmin(abs(choices - midi))] + settings.transpose
                        if target != previous_target:
                            movement.clear()
                        previous_target = target
                        movement.append(midi)
                        error = (
                            target - midi + settings.humanize * (midi - float(np.median(movement)))
                        ) * settings.strength
                    else:
                        movement.clear()
                        previous_target = None
                    alpha = (
                        1.0
                        if settings.retune_ms <= 0
                        else -np.expm1(-len(block) * 1000 / self.sr / settings.retune_ms)
                    )
                    state += alpha * (error - state)
                    dry_pending = np.concatenate((dry_pending, block))
                    if len(dry_pending) > shifter.pad + shifter.delay + shifter.block * 8:
                        raise RuntimeError("Pitch engine stopped returning cue audio")
                    for out in shifter.push(
                        block, float(np.exp2(np.clip(state, -24, 24) / 12)), settings.formant
                    ):
                        skip = min(discard, len(out))
                        discard -= skip
                        out = out[skip:]
                        count = min(len(out), len(dry_pending))
                        if count and not self.failed and not self.stop.is_set():
                            self.output(
                                out[:count] * settings.mix
                                + dry_pending[:count] * (1 - settings.mix)
                            )
                        dry_pending = dry_pending[count:]
        except Exception as exc:
            self.failed = True
            self.error = f"Corrected monitor failed; using dry monitoring: {exc}"
        finally:
            shifter.close()


class MonitorRoute:
    """Continuous-rate cue adapter; preserves fractional position across callbacks."""

    def __init__(self, recorder, engine, gain):
        self.recorder, self.engine, self.gain = recorder, engine, gain
        self.position = 0.0
        self.previous = None

    def __call__(self, block):
        source_rate, target_rate = self.recorder.sample_rate, self.engine.sr
        if source_rate == target_rate:
            self.engine.queue_monitor(block, self.gain)
            return
        data = block if self.previous is None else np.concatenate((self.previous, block))
        step = source_rate / target_rate
        positions = np.arange(self.position, max(self.position, len(data) - 1), step)
        lo = positions.astype(np.int64)
        frac = (positions - lo).astype(np.float32)[:, None]
        out = data[lo] * (1 - frac) + data[lo + 1] * frac
        self.position = (positions[-1] + step if len(positions) else self.position) - (
            len(data) - 1
        )
        self.previous = data[-1:].copy()
        if len(out):
            self.engine.queue_monitor(np.ascontiguousarray(out, np.float32), self.gain)
