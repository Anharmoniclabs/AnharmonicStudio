"""Hot-plug MIDI inputs, owned exclusively by a small device worker thread."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import queue
import re
import threading
import time

from .model import MAX_TRACKS

CONTROL_TARGETS = {"play", "stop", "record", "master", *(f"track:{i}" for i in range(MAX_TRACKS))}


def controller_settings(value):
    result = {}
    if not isinstance(value, dict):
        return result
    for key, config in list(value.items())[:128]:
        if not isinstance(key, str) or not isinstance(config, dict):
            continue
        mode = config.get("mode", "Keys + drum channel")
        base = config.get("pad_base", 36)
        valid = {
            "mode": mode
            if mode in ("Keys", "Pads", "Keys + drum channel")
            else "Keys + drum channel",
            "pad_base": base if type(base) is int and 0 <= base <= 112 else 36,
            "pads": {},
            "cc": {},
        }
        for note, pad in (
            config.get("pads", {}) if isinstance(config.get("pads"), dict) else {}
        ).items():
            if (
                isinstance(note, str)
                and note.isdigit()
                and 0 <= int(note) < 128
                and type(pad) is int
                and 0 <= pad < 16
            ):
                valid["pads"][note] = pad
        for number, target in (
            config.get("cc", {}) if isinstance(config.get("cc"), dict) else {}
        ).items():
            if (
                isinstance(number, str)
                and re.fullmatch(r"\d{1,2}:\d{1,3}", number)
                and isinstance(target, str)
                and target in CONTROL_TARGETS
            ):
                channel, cc = map(int, number.split(":"))
                if channel < 16 and cc < 128:
                    valid["cc"][number] = target
        result[key] = valid
    return result


@dataclass(frozen=True)
class MidiPort:
    id: str
    name: str
    index: int
    connected: bool = False
    error: str = ""


def stable_ports(names: list[str], backend: str) -> list[MidiPort]:
    """ALSA client numbers change on reconnect; musical port names usually do not."""
    seen: dict[str, int] = {}
    ports = []
    for index, name in enumerate(names):
        label = re.sub(r"\s+\d+:\d+$", "", name).strip()
        if backend == "alsa":
            label = re.sub(r"\s+\d+:", ":", label)
        occurrence = seen.get(label, 0)
        seen[label] = occurrence + 1
        key = f"{backend}:{label}:{occurrence}"
        ports.append(MidiPort(hashlib.sha256(key.encode()).hexdigest()[:24], label, index))
    return ports


class MidiService:
    """Enumerate and open inputs without running native device calls on the UI thread.

    The bounded event queue reports overflow as a reset, so a lost note-off cannot
    leave a voice held indefinitely. This service never sends MIDI or opens audio.
    """

    def __init__(self, factory=None, *, auto_connect=True, queue_size=2048):
        self.factory = factory
        self.auto_connect = auto_connect
        self.disabled: set[str] = set()
        self.events: queue.Queue = queue.Queue(maxsize=queue_size)
        self.ports: tuple[MidiPort, ...] = ()
        self.error = ""
        self.overflow = False
        self._stop = threading.Event()
        self._rescan = threading.Event()
        self._thread = None

    def start(self):
        if self._stop.is_set():
            return
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._run, name="MIDI device discovery", daemon=True
            )
            self._thread.start()

    def rescan(self):
        self._rescan.set()

    def enable(self, port_id, enabled):
        self.disabled = self.disabled - {port_id} if enabled else self.disabled | {port_id}
        self.rescan()

    def _emit(self, port_id, message):
        try:
            self.events.put_nowait((port_id, tuple(message), time.monotonic()))
        except queue.Full:
            self.overflow = True

    def drain(self, limit=256):
        if self.overflow:
            self.overflow = False
            while True:
                try:
                    self.events.get_nowait()
                except queue.Empty:
                    break
            return [("", (), time.monotonic())]
        result = []
        for _ in range(limit):
            try:
                result.append(self.events.get_nowait())
            except queue.Empty:
                break
        return result

    @staticmethod
    def _dispose(port):
        try:
            port.close_port()
        except Exception:
            pass
        try:
            delete = getattr(port, "delete", None)
            if delete:
                delete()
        except Exception:
            pass

    def _run(self):
        opened = {}
        addresses = {}
        inventory = None
        backend = "midi"
        try:
            factory = self.factory
            if factory is None:
                import rtmidi

                def factory():
                    return rtmidi.MidiIn(name="Anharmonic Studio input")

                if rtmidi.API_LINUX_ALSA in rtmidi.get_compiled_api():
                    backend = "alsa"
            next_scan = 0.0
            while not self._stop.is_set():
                now = time.monotonic()
                if now >= next_scan or self._rescan.is_set():
                    self._rescan.clear()
                    next_scan = now + 1.0
                    try:
                        if inventory is None:
                            inventory = factory()
                        names = inventory.get_ports()
                        found = stable_ports(names, backend)
                        self.error = ""
                    except Exception as exc:
                        found = []
                        names = []
                        self.error = str(exc) or "MIDI service unavailable"
                        if inventory is not None:
                            self._dispose(inventory)
                            inventory = None
                    wanted = {
                        p.id
                        for p in found
                        if self.auto_connect
                        and p.id not in self.disabled
                        and not any(
                            word in p.name.casefold() for word in ("midi through", "anharmonic")
                        )
                    }
                    for key in list(opened):
                        current = next((p for p in found if p.id == key), None)
                        if key not in wanted or (
                            current is not None and addresses.get(key) != names[current.index]
                        ):
                            self._dispose(opened.pop(key))
                            addresses.pop(key, None)
                            self._emit(key, ())
                    rows = []
                    for p in found:
                        error = ""
                        if p.id in wanted and p.id not in opened:
                            port = None
                            try:
                                port = factory()
                                port.ignore_types(sysex=True, timing=False, active_sense=True)
                                # Recheck the list on this native handle before opening an index.
                                current_names = port.get_ports()
                                current = stable_ports(current_names, backend)
                                match = next(item for item in current if item.id == p.id)
                                port.open_port(match.index, "Anharmonic Studio input")
                                opened[p.id] = port
                                addresses[p.id] = current_names[match.index]
                            except Exception as exc:
                                error = str(exc) or "Cannot open MIDI input"
                                if port is not None:
                                    self._dispose(port)
                        rows.append(MidiPort(p.id, p.name, p.index, p.id in opened, error))
                    self.ports = tuple(rows)
                for key, port in list(opened.items()):
                    try:
                        for _ in range(128):
                            event = port.get_message()
                            if event is None:
                                break
                            message, _delta = event
                            self._emit(key, message)
                    except Exception:
                        self._dispose(opened.pop(key))
                        addresses.pop(key, None)
                        self._emit(key, ())
                        self._rescan.set()
                self._stop.wait(0.002)
        except Exception as exc:
            self.error = str(exc) or "MIDI service unavailable"
        finally:
            for port in opened.values():
                self._dispose(port)
            if inventory is not None:
                self._dispose(inventory)
            self.ports = ()
            self._emit("", ())

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


class MidiRouter:
    """Remember the destination of held notes across bank/selection changes."""

    def __init__(self, note_on, note_off, pad_on, pad_off, bank, control, expression=None):
        self.note_on, self.note_off = note_on, note_off
        self.pad_on, self.pad_off, self.bank, self.control = pad_on, pad_off, bank, control
        self.settings: dict[str, dict] = {}
        self.held = {}
        self.sustained = set()
        self.pedals = set()
        self.learn = None
        self.learned = None
        self.last_event = "Waiting for MIDI"
        self.control_values = {}
        self.expression = expression or (lambda message: None)

    def release_port(self, port_id=None):
        for key in list(self.held):
            if port_id is None or key[0] == port_id:
                self._release(key)
        self.pedals = {key for key in self.pedals if port_id is not None and key[0] != port_id}
        self.control_values = {
            key: value
            for key, value in self.control_values.items()
            if port_id is not None and key[0] != port_id
        }

    def _release(self, key):
        self.sustained.discard(key)
        destination = self.held.pop(key, None)
        if destination is None or destination in self.held.values():
            return
        kind, number = destination
        (self.pad_off if kind == "pad" else self.note_off)(number)

    def handle(self, port_id, message, _timestamp=0):
        if not message:
            self.release_port(port_id or None)
            return
        status = message[0]
        if not isinstance(status, int) or not 0x80 <= status <= 0xFF:
            return
        if status in (0xFA, 0xFB, 0xFC):
            self.control("stop" if status == 0xFC else "play", 127)
            return
        if status >= 0xF0:
            return
        kind, channel = status & 0xF0, status & 15
        if len(message) < 3 or any(type(v) is not int or not 0 <= v < 128 for v in message[1:3]):
            return
        number, value = message[1:3]
        settings = self.settings.setdefault(port_id, {})
        if kind == 0xE0:
            self.last_event = f"Ch {channel + 1} · Pitch bend"
            self.expression([0xE0, number, value])
            return
        if kind == 0x90 and value == 0:
            kind = 0x80
        self.last_event = (
            f"Ch {channel + 1} · {'CC' if kind == 0xB0 else 'Note'} {number} · {value}"
        )
        if self.learn and self.learn[0] == port_id and kind in (0x90, 0xB0):
            _, target = self.learn
            if target.startswith("pad:") and kind == 0x90:
                settings.setdefault("pads", {})[str(number)] = int(target.split(":")[1])
            elif not target.startswith("pad:") and kind == 0xB0:
                settings.setdefault("cc", {})[f"{channel}:{number}"] = target
            else:
                return
            self.learned, self.learn = (port_id, target), None
            return
        key = (port_id, channel, number)
        if kind == 0x80:
            if (port_id, channel) in self.pedals:
                self.sustained.add(key)
            else:
                self._release(key)
        elif kind == 0x90:
            self._release(key)
            mode = settings.get("mode", "Keys + drum channel")
            pad_map = settings.get("pads", {})
            if (
                str(number) in pad_map
                or mode == "Pads"
                or (mode == "Keys + drum channel" and channel == 9)
            ):
                local = pad_map.get(str(number), number - int(settings.get("pad_base", 36)))
                if not 0 <= local < 16:
                    return
                destination = ("pad", self.bank() * 16 + local)
            else:
                destination = ("note", number)
            already_held = destination in self.held.values()
            self.held[key] = destination
            if not already_held:
                (self.pad_on if destination[0] == "pad" else self.note_on)(
                    destination[1], value / 127
                )
        elif kind == 0xB0:
            if number in (120, 123):
                self.release_port(port_id)
            elif number == 64:
                pedal = (port_id, channel)
                if value >= 64:
                    self.pedals.add(pedal)
                else:
                    self.pedals.discard(pedal)
                    for held in list(self.sustained):
                        if held[:2] == pedal:
                            self._release(held)
            target = settings.get("cc", {}).get(f"{channel}:{number}")
            if target:
                previous = self.control_values.get(key, 0)
                self.control_values[key] = value
                if target not in {"play", "stop", "record"} or value >= 64 > previous:
                    self.control(target, value)
            elif number not in (64, 120, 123):
                self.expression([0xB0, number, value])
