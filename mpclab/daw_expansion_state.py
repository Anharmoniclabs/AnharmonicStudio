"""Validated project-owned state for the advanced DAW/audio expansion.

The expansion stays in one optional schema field so older projects continue to
load while new routing, streaming and instrument-engine settings have a single
bounded persistence contract.
"""

from __future__ import annotations

from copy import deepcopy
import math

SAMPLE_RATES = (44_100, 48_000, 88_200, 96_000)
SUMMING_PRECISIONS = ("float32", "float64")
ENGINE_TYPES = ("multisample", "vector", "modal", "string", "fm4")
MAX_SIDECHAINS = 256
MAX_INSTRUMENT_ENGINES = 127
MAX_REGIONS = 4096
MAX_GESTURE_POINTS = 16384


def default_daw_expansion() -> dict:
    return {
        "sample_rate": 48_000,
        "summation_precision": "float32",
        "parallel_workers": 0,
        "streaming": {
            "enabled": True,
            "read_ahead_frames": 262_144,
            "request_capacity": 1024,
        },
        "sidechains": [],
        "track_channels": {},
        "hardware_patchbay": {},
        "instrument_engines": {},
    }


def _finite(value, label: str, low: float, high: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    value = float(value)
    if not low <= value <= high:
        raise ValueError(f"{label} must be between {low:g} and {high:g}")
    return value


def _integer(value, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer from {low} to {high}")
    return value


def _identifier(value, label: str, *, allow_master: bool = False) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or value.strip() != value:
        raise ValueError(f"{label} must be a bounded non-empty identifier")
    if allow_master and value == "master":
        return value
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{label} contains control characters")
    return value


def _validate_sidechain(item: dict, project=None) -> dict:
    if not isinstance(item, dict):
        raise ValueError("sidechain entries must be objects")
    allowed = {"id", "source", "target", "slot", "gain", "pre_fader", "enabled"}
    if set(item) - allowed:
        raise ValueError("sidechain entry contains unsupported fields")
    result = {
        "id": _identifier(item.get("id", ""), "sidechain id"),
        "source": _identifier(item.get("source", ""), "sidechain source", allow_master=True),
        "target": _identifier(item.get("target", ""), "sidechain target", allow_master=True),
        "slot": _integer(item.get("slot", 0), "sidechain plugin slot", 0, 31),
        "gain": _finite(item.get("gain", 1.0), "sidechain gain", 0.0, 4.0),
        "pre_fader": item.get("pre_fader", False),
        "enabled": item.get("enabled", True),
    }
    if type(result["pre_fader"]) is not bool or type(result["enabled"]) is not bool:
        raise ValueError("sidechain enabled/pre_fader fields must be booleans")
    if project is not None:
        track_ids = {track.id for track in project.tracks}
        valid = {"master", *(f"track:{track_id}" for track_id in track_ids)}
        workflow = getattr(project, "workflow", {})
        routing = workflow.get("routing", {}) if isinstance(workflow, dict) else {}
        for bus in routing.get("buses", []) if isinstance(routing, dict) else []:
            if isinstance(bus, dict) and bus.get("id"):
                valid.add(f"bus:{bus['id']}")
        for field in ("source", "target"):
            if result[field] not in valid:
                raise ValueError(f"sidechain {field} does not exist")
    return result


def _validate_region(item: dict) -> dict:
    if not isinstance(item, dict):
        raise ValueError("multisample regions must be objects")
    allowed = {
        "sample_id",
        "key_low",
        "key_high",
        "root_key",
        "velocity_low",
        "velocity_high",
        "round_robin",
        "stack",
        "gain",
        "release_sample_id",
    }
    if set(item) - allowed:
        raise ValueError("multisample region contains unsupported fields")
    sample_id = _identifier(item.get("sample_id", ""), "region sample id")
    release = item.get("release_sample_id")
    if release not in (None, ""):
        release = _identifier(release, "release sample id")
    low = _integer(item.get("key_low", 0), "region key_low", 0, 127)
    high = _integer(item.get("key_high", 127), "region key_high", 0, 127)
    root = _integer(item.get("root_key", 60), "region root_key", 0, 127)
    velocity_low = _integer(item.get("velocity_low", 1), "region velocity_low", 1, 127)
    velocity_high = _integer(item.get("velocity_high", 127), "region velocity_high", 1, 127)
    if low > high or velocity_low > velocity_high:
        raise ValueError("multisample region ranges must be ascending")
    return {
        "sample_id": sample_id,
        "key_low": low,
        "key_high": high,
        "root_key": root,
        "velocity_low": velocity_low,
        "velocity_high": velocity_high,
        "round_robin": _integer(item.get("round_robin", 0), "round robin index", 0, 255),
        "stack": _integer(item.get("stack", 0), "stack group", 0, 255),
        "gain": _finite(item.get("gain", 1.0), "region gain", 0.0, 8.0),
        "release_sample_id": release or None,
    }


def _validate_engine(instrument_id: str, raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("instrument engine state must be an object")
    engine_type = raw.get("type")
    if engine_type not in ENGINE_TYPES:
        raise ValueError("unsupported instrument engine type")
    enabled = raw.get("enabled", True)
    if type(enabled) is not bool:
        raise ValueError("instrument engine enabled must be boolean")
    state = deepcopy(raw.get("state", {}))
    if not isinstance(state, dict):
        raise ValueError("instrument engine state payload must be an object")

    if engine_type == "multisample":
        regions = state.get("regions", [])
        if not isinstance(regions, list) or len(regions) > MAX_REGIONS:
            raise ValueError("multisample regions exceed the safety limit")
        state = {
            "regions": [_validate_region(region) for region in regions],
            "polyphony": _integer(state.get("polyphony", 32), "multisample polyphony", 1, 128),
        }
    elif engine_type == "vector":
        sources = state.get("sources", ["sine", "saw", "square", "triangle"])
        allowed_sources = {"sine", "saw", "square", "triangle", "noise"}
        if (
            not isinstance(sources, list)
            or len(sources) != 4
            or any(source not in allowed_sources for source in sources)
        ):
            raise ValueError("vector engine requires four valid sources")
        gesture = state.get("gesture", [])
        if not isinstance(gesture, list) or len(gesture) > MAX_GESTURE_POINTS:
            raise ValueError("vector gesture exceeds the safety limit")
        clean_gesture = []
        previous = -1.0
        for point in gesture:
            if not isinstance(point, dict):
                raise ValueError("vector gesture points must be objects")
            at = _finite(point.get("at", 0.0), "vector gesture time", 0.0, 86_400.0)
            if at < previous:
                raise ValueError("vector gesture points must be ordered")
            previous = at
            clean_gesture.append(
                {
                    "at": at,
                    "x": _finite(point.get("x", 0.5), "vector x", 0.0, 1.0),
                    "y": _finite(point.get("y", 0.5), "vector y", 0.0, 1.0),
                }
            )
        state = {
            "sources": list(sources),
            "x": _finite(state.get("x", 0.5), "vector x", 0.0, 1.0),
            "y": _finite(state.get("y", 0.5), "vector y", 0.0, 1.0),
            "gesture": clean_gesture,
            "gesture_loop": bool(state.get("gesture_loop", False)),
        }
    elif engine_type == "modal":
        modes = state.get("modes", [])
        if not isinstance(modes, list) or len(modes) > 128:
            raise ValueError("modal engine supports at most 128 modes")
        cleaned = []
        for mode in modes:
            if not isinstance(mode, dict):
                raise ValueError("modal modes must be objects")
            cleaned.append(
                {
                    "ratio": _finite(mode.get("ratio", 1.0), "modal ratio", 0.01, 64.0),
                    "gain": _finite(mode.get("gain", 1.0), "modal gain", 0.0, 8.0),
                    "decay": _finite(mode.get("decay", 1.0), "modal decay", 0.005, 60.0),
                }
            )
        state = {"modes": cleaned}
    elif engine_type == "string":
        state = {
            "damping": _finite(state.get("damping", 0.995), "string damping", 0.8, 0.99999),
            "brightness": _finite(state.get("brightness", 0.5), "string brightness", 0.0, 1.0),
            "pick_position": _finite(state.get("pick_position", 0.2), "pick position", 0.01, 0.99),
        }
    else:  # fm4
        operators = state.get("operators", [])
        if not operators:
            operators = [{"ratio": ratio, "level": level} for ratio, level in ((1, 1), (2, 0.5), (3, 0.25), (4, 0.1))]
        if not isinstance(operators, list) or len(operators) != 4:
            raise ValueError("FM engine requires four operators")
        cleaned = []
        for operator in operators:
            if not isinstance(operator, dict):
                raise ValueError("FM operators must be objects")
            cleaned.append(
                {
                    "ratio": _finite(operator.get("ratio", 1.0), "FM ratio", 0.01, 64.0),
                    "fixed_hz": _finite(operator.get("fixed_hz", 0.0), "FM fixed frequency", 0.0, 24_000.0),
                    "level": _finite(operator.get("level", 1.0), "FM level", 0.0, 8.0),
                    "attack": _finite(operator.get("attack", 0.005), "FM attack", 0.0, 30.0),
                    "decay": _finite(operator.get("decay", 0.25), "FM decay", 0.0, 30.0),
                    "sustain": _finite(operator.get("sustain", 0.7), "FM sustain", 0.0, 1.0),
                    "release": _finite(operator.get("release", 0.4), "FM release", 0.001, 60.0),
                }
            )
        state = {
            "operators": cleaned,
            "algorithm": _integer(state.get("algorithm", 0), "FM algorithm", 0, 7),
            "feedback": _finite(state.get("feedback", 0.0), "FM feedback", 0.0, 1.0),
        }

    return {"type": engine_type, "enabled": enabled, "state": state, "instrument_id": instrument_id}


def validate_daw_expansion(value, *, project=None) -> dict:
    """Validate and normalize the optional advanced DAW state."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("daw_expansion must be an object")
    unknown = set(value) - set(default_daw_expansion())
    if unknown:
        raise ValueError(f"daw_expansion contains unsupported fields: {sorted(unknown)}")
    result = default_daw_expansion()

    rate = value.get("sample_rate", result["sample_rate"])
    if type(rate) is not int or rate not in SAMPLE_RATES:
        raise ValueError("project sample_rate must be 44.1, 48, 88.2 or 96 kHz")
    result["sample_rate"] = rate

    precision = value.get("summation_precision", result["summation_precision"])
    if precision not in SUMMING_PRECISIONS:
        raise ValueError("summation_precision must be float32 or float64")
    result["summation_precision"] = precision
    result["parallel_workers"] = _integer(
        value.get("parallel_workers", 0), "parallel workers", 0, 16
    )

    streaming = value.get("streaming", result["streaming"])
    if not isinstance(streaming, dict) or set(streaming) - {
        "enabled",
        "read_ahead_frames",
        "request_capacity",
    }:
        raise ValueError("streaming settings are invalid")
    enabled = streaming.get("enabled", True)
    if type(enabled) is not bool:
        raise ValueError("streaming enabled must be boolean")
    result["streaming"] = {
        "enabled": enabled,
        "read_ahead_frames": _integer(
            streaming.get("read_ahead_frames", 262_144), "read-ahead frames", 4_096, 4_194_304
        ),
        "request_capacity": _integer(
            streaming.get("request_capacity", 1024), "read-ahead request capacity", 16, 65_536
        ),
    }

    sidechains = value.get("sidechains", [])
    if not isinstance(sidechains, list) or len(sidechains) > MAX_SIDECHAINS:
        raise ValueError("sidechain routing exceeds the safety limit")
    result["sidechains"] = [_validate_sidechain(item, project) for item in sidechains]
    if len({item["id"] for item in result["sidechains"]}) != len(result["sidechains"]):
        raise ValueError("sidechain IDs must be unique")

    channels = value.get("track_channels", {})
    if not isinstance(channels, dict) or len(channels) > 128:
        raise ValueError("track channel map must be bounded")
    known_tracks = {track.id for track in project.tracks} if project is not None else None
    result["track_channels"] = {}
    for track_id, count in channels.items():
        track_id = _identifier(track_id, "track channel id")
        if known_tracks is not None and track_id not in known_tracks:
            raise ValueError("track channel map references a missing track")
        if count not in (1, 2, 4, 6, 8):
            raise ValueError("track channel count must be 1, 2, 4, 6 or 8")
        result["track_channels"][track_id] = int(count)

    patchbay = value.get("hardware_patchbay", {})
    if not isinstance(patchbay, dict) or len(patchbay) > 128:
        raise ValueError("hardware patchbay must be a bounded object")
    clean_patchbay = {}
    for key, value_item in patchbay.items():
        key = _identifier(key, "patchbay endpoint")
        if not isinstance(value_item, list) or len(value_item) > 64:
            raise ValueError("patchbay endpoint must contain bounded channel indices")
        clean_patchbay[key] = [_integer(channel, "hardware channel", 0, 127) for channel in value_item]
    result["hardware_patchbay"] = clean_patchbay

    engines = value.get("instrument_engines", {})
    if not isinstance(engines, dict) or len(engines) > MAX_INSTRUMENT_ENGINES:
        raise ValueError("instrument engine map exceeds the safety limit")
    known_instruments = {instrument.id for instrument in project.instruments} if project is not None else None
    result["instrument_engines"] = {}
    for instrument_id, config in engines.items():
        instrument_id = _identifier(instrument_id, "instrument engine id")
        if known_instruments is not None and instrument_id not in known_instruments:
            raise ValueError("instrument engine references a missing instrument")
        result["instrument_engines"][instrument_id] = _validate_engine(instrument_id, config)

    return result
