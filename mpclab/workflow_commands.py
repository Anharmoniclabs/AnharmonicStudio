"""User-remappable workstation commands and bounded multi-action macros.

The GUI historically bound keys directly to slots. This module provides the
stable action layer needed by keyboard presets, MIDI learn, menus and command
search without making any of those surfaces own the implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Iterable


CommandCallback = Callable[[], object]
MAX_BINDING_FILE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class CommandSpec:
    id: str
    title: str
    callback: CommandCallback
    category: str = "Workflow"
    keywords: tuple[str, ...] = ()
    default_shortcut: str = ""

    def __post_init__(self) -> None:
        if not self.id or len(self.id) > 128 or any(ch.isspace() for ch in self.id):
            raise ValueError("command id must be a compact non-empty token")
        if not self.title.strip() or len(self.title) > 160:
            raise ValueError("command title is required")
        if len(self.category) > 80:
            raise ValueError("command category is too long")


@dataclass
class MacroSpec:
    name: str
    commands: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not self.name.strip() or len(self.name) > 96:
            raise ValueError("macro name is required")
        if not self.commands or len(self.commands) > 64:
            raise ValueError("macro must contain between 1 and 64 commands")
        for command_id in self.commands:
            if not isinstance(command_id, str) or not command_id or len(command_id) > 128:
                raise ValueError("macro command id is invalid")


PRESET_BINDINGS: dict[str, dict[str, str]] = {
    "Anharmonic / FL-style": {
        "transport.play_toggle": "Space",
        "transport.stop": "Esc",
        "transport.rewind": "Home",
        "transport.record": "R",
        "workspace.playlist": "F5",
        "workspace.sequencer": "F6",
        "workspace.instrument": "F7",
        "workspace.browser": "F8",
        "workspace.mixer": "F9",
        "workspace.vocals": "F10",
        "playlist.split": "Shift+S",
        "playlist.duplicate": "Ctrl+D",
        "workflow.command_palette": "Ctrl+Shift+P",
        "notes.quantize": "Ctrl+Q",
        "clip.consolidate": "Ctrl+J",
        "clip.bounce_in_place": "Ctrl+Alt+B",
    },
    "Ableton-style": {
        "transport.play_toggle": "Space",
        "transport.stop": "Esc",
        "playlist.split": "Ctrl+E",
        "playlist.duplicate": "Ctrl+D",
        "clip.consolidate": "Ctrl+J",
        "clip.loop_toggle": "Ctrl+L",
        "clip.bounce_in_place": "Ctrl+B",
        "workflow.command_palette": "Ctrl+Shift+P",
    },
    "Logic-style": {
        "transport.play_toggle": "Space",
        "transport.stop": "Return",
        "playlist.split": "Cmd+T",
        "workflow.command_palette": "Cmd+Shift+P",
        "notes.quantize": "Q",
    },
    "Pro Tools-style": {
        "transport.play_toggle": "Space",
        "transport.stop": "Esc",
        "playlist.split": "Ctrl+E",
        "playlist.duplicate": "Ctrl+D",
        "workflow.command_palette": "Ctrl+Shift+P",
    },
}


def _valid_shortcut(value: object) -> str:
    text = str(value or "").strip()
    if len(text) > 80 or "\n" in text or "\r" in text:
        raise ValueError("shortcut is invalid")
    return text


def _read_binding_document(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_BINDING_FILE_BYTES + 1)
    except OSError:
        raise
    if len(raw) > MAX_BINDING_FILE_BYTES:
        raise ValueError("workflow binding file exceeds the 1 MiB safety limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("workflow binding file must be UTF-8 JSON") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"workflow binding file is not valid JSON (line {exc.lineno}, column {exc.colno})"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError("workflow binding file must contain a JSON object")
    return payload


class CommandRegistry:
    """Stable command catalog with persisted key bindings and macros."""

    VERSION = 1

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self._commands: dict[str, CommandSpec] = {}
        self.bindings: dict[str, str] = {}
        self.macros: dict[str, MacroSpec] = {}
        self.preset = "Anharmonic / FL-style"
        self._running_macro = False
        if self.path and self.path.exists():
            self.load()

    @property
    def commands(self) -> tuple[CommandSpec, ...]:
        return tuple(sorted(self._commands.values(), key=lambda item: (item.category, item.title)))

    def register(self, spec: CommandSpec) -> None:
        if spec.id in self._commands:
            raise ValueError(f"duplicate command id: {spec.id}")
        self._commands[spec.id] = spec
        if self.preset != "Custom" and spec.id not in self.bindings and spec.default_shortcut:
            shortcut = _valid_shortcut(spec.default_shortcut)
            occupied = {value.casefold() for value in self.bindings.values()}
            if shortcut.casefold() not in occupied:
                self.bindings[spec.id] = shortcut

    def register_many(self, specs: Iterable[CommandSpec]) -> None:
        for spec in specs:
            self.register(spec)

    def get(self, command_id: str) -> CommandSpec:
        try:
            return self._commands[command_id]
        except KeyError as exc:
            raise KeyError(f"unknown command: {command_id}") from exc

    def execute(self, command_id: str):
        return self.get(command_id).callback()

    def search(self, text: str, limit: int = 40) -> list[CommandSpec]:
        query = text.casefold().strip()
        if not query:
            return list(self.commands[:limit])
        terms = [part for part in query.split() if part]
        ranked = []
        for spec in self._commands.values():
            haystack = " ".join((spec.id, spec.title, spec.category, *spec.keywords)).casefold()
            if not all(term in haystack for term in terms):
                continue
            score = 0
            if spec.title.casefold().startswith(query):
                score += 8
            if spec.id.casefold().startswith(query):
                score += 6
            score += sum(2 for term in terms if term in spec.title.casefold())
            ranked.append((-score, spec.category.casefold(), spec.title.casefold(), spec))
        ranked.sort(key=lambda item: item[:3])
        return [item[3] for item in ranked[: max(1, min(200, int(limit)))]]

    def bind(self, command_id: str, shortcut: str) -> None:
        self.get(command_id)
        shortcut = _valid_shortcut(shortcut)
        if shortcut:
            folded = shortcut.casefold()
            for other, existing in list(self.bindings.items()):
                if other != command_id and existing.casefold() == folded:
                    self.bindings.pop(other, None)
            self.bindings[command_id] = shortcut
        else:
            self.bindings.pop(command_id, None)
        self.preset = "Custom"
        self.save()

    def apply_preset(self, name: str) -> None:
        if name not in PRESET_BINDINGS:
            raise ValueError("unknown keymap preset")
        self.preset = name
        bindings: dict[str, str] = {}
        occupied: set[str] = set()
        for command_id, raw_shortcut in PRESET_BINDINGS[name].items():
            shortcut = _valid_shortcut(raw_shortcut)
            folded = shortcut.casefold()
            if shortcut and folded not in occupied:
                bindings[command_id] = shortcut
                occupied.add(folded)
        for spec in self._commands.values():
            if not spec.default_shortcut or spec.id in bindings:
                continue
            shortcut = _valid_shortcut(spec.default_shortcut)
            folded = shortcut.casefold()
            if folded not in occupied:
                bindings[spec.id] = shortcut
                occupied.add(folded)
        self.bindings = bindings
        self.save()

    def define_macro(self, name: str, command_ids: Iterable[str]) -> MacroSpec:
        spec = MacroSpec(name=name.strip(), commands=list(command_ids))
        spec.validate()
        for command_id in spec.commands:
            self.get(command_id)
        self.macros[spec.name] = spec
        self.save()
        return spec

    def remove_macro(self, name: str) -> None:
        self.macros.pop(name, None)
        self.save()

    def run_macro(self, name: str) -> list[object]:
        if self._running_macro:
            raise RuntimeError("macros cannot recursively execute macros")
        try:
            spec = self.macros[name]
        except KeyError as exc:
            raise KeyError(f"unknown macro: {name}") from exc
        spec.validate()
        self._running_macro = True
        results = []
        try:
            for command_id in spec.commands:
                results.append(self.execute(command_id))
        finally:
            self._running_macro = False
        return results

    def payload(self) -> dict:
        return {
            "version": self.VERSION,
            "preset": self.preset,
            "bindings": dict(sorted(self.bindings.items())),
            "macros": {name: list(spec.commands) for name, spec in sorted(self.macros.items())},
        }

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.payload(), indent=2)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def load(self) -> None:
        if not self.path or not self.path.exists():
            return
        payload = _read_binding_document(self.path)
        if payload.get("version") != self.VERSION:
            raise ValueError("unsupported workflow binding file")
        preset = payload.get("preset", "Anharmonic / FL-style")
        if preset not in PRESET_BINDINGS and preset != "Custom":
            preset = "Anharmonic / FL-style"
        raw_bindings = payload.get("bindings", {})
        if not isinstance(raw_bindings, dict) or len(raw_bindings) > 1024:
            raise ValueError("workflow bindings must be a bounded object")
        bindings: dict[str, str] = {}
        occupied: set[str] = set()
        for command_id, shortcut in raw_bindings.items():
            if not isinstance(command_id, str) or len(command_id) > 128:
                raise ValueError("workflow binding command id is invalid")
            shortcut = _valid_shortcut(shortcut)
            folded = shortcut.casefold()
            if shortcut and folded not in occupied:
                bindings[command_id] = shortcut
                occupied.add(folded)
        raw_macros = payload.get("macros", {})
        if not isinstance(raw_macros, dict) or len(raw_macros) > 256:
            raise ValueError("workflow macros must be a bounded object")
        macros = {}
        for name, command_ids in raw_macros.items():
            if not isinstance(name, str) or not isinstance(command_ids, list):
                raise ValueError("workflow macro is invalid")
            spec = MacroSpec(name, [str(item) for item in command_ids])
            spec.validate()
            macros[name] = spec
        self.preset = preset
        self.bindings = bindings
        self.macros = macros
