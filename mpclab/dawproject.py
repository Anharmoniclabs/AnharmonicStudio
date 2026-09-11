"""Shared bounded DAWproject schema and archive helpers.

The production Anharmonic model adapter lives in :mod:`mpclab.dawproject_io`.
This module owns only format/security primitives so there is one importer and
one exporter instead of a stale second implementation.
"""

from __future__ import annotations

import math
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET
import zipfile

MAX_ENTRIES = 4096
MAX_PROJECT_XML = 16 * 1024 * 1024
MAX_MEDIA_FILE = 1024 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 4 * 1024 * 1024 * 1024
MAX_TRACKS = 256
MAX_CLIPS = 100_000
MAX_NOTES = 500_000


class DawProjectError(ValueError):
    """The DAWproject archive or supported interchange subset is invalid."""


def _number(value, *, low=-1_000_000.0, high=1_000_000.0, default=None, label="value"):
    if value is None:
        if default is not None:
            return float(default)
        raise DawProjectError(f"{label} is required")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DawProjectError(f"{label} must be numeric") from exc
    if not math.isfinite(number) or not low <= number <= high:
        raise DawProjectError(f"{label} is out of range")
    return number


def _bool(value, default=False):
    if value is None:
        return default
    normalized = str(value).casefold()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise DawProjectError("boolean attribute is invalid")


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
        raise DawProjectError("DAWproject contains an unsafe archive path")
    return path


def _xml_id(prefix: str, index: int) -> str:
    return f"anh_{prefix}_{index + 1}"


def _parameter(parent, tag, value, unit, *, minimum=None, maximum=None, identifier=None):
    attrs = {"value": f"{float(value):.12g}", "unit": unit}
    if minimum is not None:
        attrs["min"] = f"{float(minimum):.12g}"
    if maximum is not None:
        attrs["max"] = f"{float(maximum):.12g}"
    if identifier:
        attrs["id"] = identifier
    return ET.SubElement(parent, tag, attrs)


def _media_info(library, sample_id: str):
    meta = library.clips.get(sample_id)
    if meta is None:
        raise DawProjectError(f"audio source is missing from the library: {sample_id}")
    path = Path(library.wav_path(sample_id)).expanduser().resolve()
    if not path.is_file():
        raise DawProjectError(f"audio source file is missing: {path}")
    if path.stat().st_size > MAX_MEDIA_FILE:
        limit = MAX_MEDIA_FILE // (1024 * 1024)
        raise DawProjectError(f"audio source exceeds {limit} MiB: {path.name}")
    return meta, path


def _archive_inventory(archive: zipfile.ZipFile):
    infos = archive.infolist()
    if not infos or len(infos) > MAX_ENTRIES:
        raise DawProjectError("DAWproject archive has an invalid entry count")
    total = 0
    names = set()
    inventory = {}
    for info in infos:
        path = _safe_member(info.filename)
        name = str(path)
        if name in names:
            raise DawProjectError("DAWproject contains duplicate archive paths")
        names.add(name)
        total += max(0, int(info.file_size))
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise DawProjectError("DAWproject exceeds the uncompressed size limit")
        if info.file_size > MAX_MEDIA_FILE and info.filename != "project.xml":
            raise DawProjectError("DAWproject contains an oversized media entry")
        inventory[info.filename] = info
    return inventory


def _read_project_xml(archive: zipfile.ZipFile, inventory) -> ET.Element:
    info = inventory.get("project.xml")
    if info is None or info.file_size > MAX_PROJECT_XML:
        raise DawProjectError("DAWproject project.xml is missing or oversized")
    raw = archive.read(info)
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise DawProjectError("DAWproject XML entities/DTDs are not accepted")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise DawProjectError("DAWproject project.xml is malformed") from exc
    if root.tag != "Project":
        raise DawProjectError("DAWproject XML root must be Project")
    return root
