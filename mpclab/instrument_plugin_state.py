"""Persistence validation for stable-ID hosted external instruments."""

from __future__ import annotations

from copy import deepcopy

from .model import MAX_INSTRUMENTS
from .plugin_registry import validate_plugin_spec


def validate_instrument_plugins(value, project=None) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_INSTRUMENTS:
        raise ValueError("instrument_plugins must be a bounded object")
    known = None
    if project is not None:
        known = {instrument.id for instrument in project.instruments}
    result = {}
    for instrument_id, specification in value.items():
        if (
            not isinstance(instrument_id, str)
            or not instrument_id
            or len(instrument_id) > 128
            or (known is not None and instrument_id not in known)
        ):
            raise ValueError("hosted instrument plugin refers to an unknown instrument")
        result[instrument_id] = validate_plugin_spec(deepcopy(specification))
    return result
