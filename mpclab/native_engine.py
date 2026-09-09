"""Control-plane binding for the compiled Anharmonic realtime engine.

The PortAudio callback, MIDI ingestion, voice rendering, limiting and LV2 graph
run in C++. Python owns lifecycle and UI calls only; no Python callable is
invoked from the audio thread.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path


class NativeEngineError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class NativeCapabilities:
    realtime_safe: bool = True
    sample_accurate_events: bool = False
    streaming_media: bool = False
    plugin_graph: bool = True
    midi_input: bool = True
    plugin_format: str = "LV2"
    version: str = "0.1.0"


@dataclass(frozen=True, slots=True)
class NativeEngineLoadResult:
    engine: "NativeEngine | None"
    capabilities: NativeCapabilities | None
    reason: str

    @property
    def available(self) -> bool:
        return self.engine is not None and self.capabilities is not None


def _library_candidates() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    result: list[Path] = []
    if custom := os.environ.get("ANHARMONIC_NATIVE_LIBRARY"):
        result.append(Path(custom).expanduser())
    result.extend(
        [
            root / "native" / "build" / "libanharmonic_native.so",
            root / "native" / "build" / "lib" / "libanharmonic_native.so",
            Path("/usr/local/lib/libanharmonic_native.so"),
            Path("/usr/lib/libanharmonic_native.so"),
        ]
    )
    return result


def _load_library() -> ctypes.CDLL:
    for path in _library_candidates():
        if path.is_file():
            return ctypes.CDLL(str(path))
    raise NativeEngineError(
        "compiled Anharmonic engine not found; run scripts/build-native.sh "
        "or set ANHARMONIC_NATIVE_LIBRARY"
    )


class NativeEngine:
    def __init__(self, sample_rate: int = 48_000, blocksize: int = 256):
        self._lib = _load_library()
        self._bind()
        self._handle = self._lib.anh_engine_create(float(sample_rate), int(blocksize))
        if not self._handle:
            raise NativeEngineError(self._last_error())

    def _bind(self) -> None:
        lib = self._lib
        lib.anh_last_error.restype = ctypes.c_char_p
        lib.anh_engine_create.argtypes = [ctypes.c_double, ctypes.c_ulong]
        lib.anh_engine_create.restype = ctypes.c_void_p
        lib.anh_engine_destroy.argtypes = [ctypes.c_void_p]
        lib.anh_engine_start.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        lib.anh_engine_stop.argtypes = [ctypes.c_void_p]
        lib.anh_engine_midi_client.argtypes = [ctypes.c_void_p]
        lib.anh_engine_midi_client.restype = ctypes.c_int
        lib.anh_engine_midi_port.argtypes = [ctypes.c_void_p]
        lib.anh_engine_midi_port.restype = ctypes.c_int
        lib.anh_engine_connect_midi.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        lib.anh_engine_xruns.argtypes = [ctypes.c_void_p]
        lib.anh_engine_xruns.restype = ctypes.c_ulonglong
        lib.anh_engine_dropped_midi.argtypes = [ctypes.c_void_p]
        lib.anh_engine_dropped_midi.restype = ctypes.c_ulonglong
        lib.anh_lv2_scan.argtypes = [ctypes.c_void_p]
        lib.anh_lv2_count.argtypes = [ctypes.c_void_p]
        lib.anh_lv2_count.restype = ctypes.c_ulong
        lib.anh_lv2_uri.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_char_p,
            ctypes.c_ulong,
        ]
        lib.anh_lv2_load.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.anh_lv2_load.restype = ctypes.c_long
        lib.anh_lv2_clear.argtypes = [ctypes.c_void_p]
        lib.anh_lv2_set_control.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_uint,
            ctypes.c_float,
        ]

    def _last_error(self) -> str:
        raw = self._lib.anh_last_error()
        return raw.decode("utf-8", "replace") if raw else "native engine error"

    def _check(self, result: int) -> None:
        if result != 0:
            raise NativeEngineError(self._last_error())

    def capabilities(self) -> NativeCapabilities:
        return NativeCapabilities()

    def start(self, output_device: int = -1, input_device: int = -1) -> None:
        self._check(self._lib.anh_engine_start(self._handle, output_device, input_device))

    def stop(self) -> None:
        if self._handle:
            self._check(self._lib.anh_engine_stop(self._handle))

    @property
    def midi_address(self) -> tuple[int, int]:
        return (
            self._lib.anh_engine_midi_client(self._handle),
            self._lib.anh_engine_midi_port(self._handle),
        )

    def connect_midi(self, source_client: int, source_port: int) -> None:
        self._check(
            self._lib.anh_engine_connect_midi(self._handle, int(source_client), int(source_port))
        )

    def scan_lv2(self) -> list[str]:
        self._check(self._lib.anh_lv2_scan(self._handle))
        result: list[str] = []
        for index in range(self._lib.anh_lv2_count(self._handle)):
            buffer = ctypes.create_string_buffer(4096)
            self._check(self._lib.anh_lv2_uri(self._handle, index, buffer, ctypes.sizeof(buffer)))
            result.append(buffer.value.decode("utf-8", "replace"))
        return result

    def load_lv2(self, uri: str) -> int:
        index = self._lib.anh_lv2_load(self._handle, uri.encode("utf-8"))
        if index < 0:
            raise NativeEngineError(self._last_error())
        return int(index)

    def clear_plugins(self) -> None:
        self._check(self._lib.anh_lv2_clear(self._handle))

    def set_plugin_control(self, plugin: int, port: int, value: float) -> None:
        self._check(
            self._lib.anh_lv2_set_control(self._handle, plugin, port, ctypes.c_float(value))
        )

    @property
    def diagnostics(self) -> dict[str, int]:
        return {
            "xruns": int(self._lib.anh_engine_xruns(self._handle)),
            "dropped_midi": int(self._lib.anh_engine_dropped_midi(self._handle)),
        }

    def close(self) -> None:
        if self._handle:
            self._lib.anh_engine_stop(self._handle)
            self._lib.anh_engine_destroy(self._handle)
            self._handle = None

    def __enter__(self) -> "NativeEngine":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def load_native_engine(
    module_name: str = "anharmonic_native",
    *,
    sample_rate: int = 48_000,
    blocksize: int = 256,
) -> NativeEngineLoadResult:
    del module_name
    try:
        engine = NativeEngine(sample_rate=sample_rate, blocksize=blocksize)
    except Exception as exc:
        return NativeEngineLoadResult(None, None, f"native engine unavailable: {exc}")
    caps = engine.capabilities()
    return NativeEngineLoadResult(engine, caps, "ready")
