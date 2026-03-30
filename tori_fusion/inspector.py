from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .archive import ArchiveEntry, F3dArchive


PROPERTIES_ENTRY = "Properties.dat"
DESIGN_BULK_ENTRY = "FusionAssetName[Active]/FusionDesignSegmentType1/BulkStream.dat"
DESIGN_META_ENTRY = "FusionAssetName[Active]/FusionDesignSegmentType1/MetaStream.dat"

FEATURE_TOKENS = {
    "sketch": "DcSketchMetaType",
    "extrude": "DcExtrudeFeatureMetaType",
}

ASCII_STRING_RE = re.compile(rb"[ -~]{4,}")


def inspect_archive(path: str | Path) -> dict[str, Any]:
    archive = F3dArchive(path)
    entries = archive.entries()
    properties = _load_properties(archive)
    design_bulk = _safe_read_textish(archive, DESIGN_BULK_ENTRY)
    design_meta = _safe_read_textish(archive, DESIGN_META_ENTRY)

    return {
        "path": str(Path(path).resolve()),
        "entry_count": len(entries),
        "entries": [_entry_to_dict(entry) for entry in entries],
        "properties": properties,
        "segments": _segment_names(entries),
        "feature_hints": _feature_hints(design_bulk, design_meta),
        "string_hints": _interesting_strings(design_bulk, design_meta),
    }


def format_inspection_report(report: dict[str, Any]) -> str:
    lines = [
        f"File: {report['path']}",
        f"Entries: {report['entry_count']}",
    ]

    properties = report.get("properties")
    if properties:
        lines.append(f"Document type: {properties.get('type', 'unknown')}")
        lines.append(f"Subtype: {properties.get('subtype', 'unknown')}")

    feature_hints = report.get("feature_hints", {})
    if feature_hints:
        present = [name for name, enabled in feature_hints.items() if enabled]
        lines.append("Feature hints: " + (", ".join(present) if present else "none"))

    segments = report.get("segments", [])
    if segments:
        lines.append("Segments: " + ", ".join(segments))

    string_hints = report.get("string_hints", [])
    if string_hints:
        lines.append("Interesting strings:")
        for value in string_hints:
            lines.append(f"  - {value}")

    return "\n".join(lines)


def _entry_to_dict(entry: ArchiveEntry) -> dict[str, Any]:
    return {
        "name": entry.name,
        "compression_method": entry.compression_method,
        "compressed_size": entry.compressed_size,
        "uncompressed_size": entry.uncompressed_size,
    }


def _load_properties(archive: F3dArchive) -> dict[str, Any] | None:
    try:
        raw = archive.read_entry(PROPERTIES_ENTRY)
    except KeyError:
        return None

    payload = _decode_json_payload(raw)
    if payload is None:
        return None

    docstruct = payload.get("docstruct", {})
    return {
        "version": docstruct.get("version"),
        "type": docstruct.get("type"),
        "subtype": docstruct.get("subtype"),
        "attributes": docstruct.get("attributes", {}),
    }


def _decode_json_payload(raw: bytes) -> dict[str, Any] | None:
    candidates = [raw]
    if len(raw) > 4:
        prefix_length = int.from_bytes(raw[:4], "little", signed=False)
        if prefix_length <= len(raw) - 4:
            candidates.append(raw[4 : 4 + prefix_length])

    for candidate in candidates:
        try:
            payload = json.loads(candidate.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _segment_names(entries: list[ArchiveEntry]) -> list[str]:
    seen: list[str] = []
    for entry in entries:
        name = entry.name
        if not name.endswith("/"):
            continue
        if "SegmentType" not in name:
            continue
        segment = name.rstrip("/").split("/")[-1]
        if segment not in seen:
            seen.append(segment)
    return seen


def _feature_hints(design_bulk: list[str], design_meta: list[str]) -> dict[str, bool]:
    haystack = set(design_bulk) | set(design_meta)
    return {feature: token in haystack for feature, token in FEATURE_TOKENS.items()}


def _interesting_strings(design_bulk: list[str], design_meta: list[str]) -> list[str]:
    needles = (
        "DcSketchMetaType",
        "DcExtrudeFeatureMetaType",
        "SketchesRoot",
        "ComponentsRoot",
        "UnitSystems",
        "EntityGenesis",
    )
    seen: list[str] = []
    for value in design_bulk + design_meta:
        if any(needle in value for needle in needles) and value not in seen:
            seen.append(value)
    return seen[:20]


def _safe_read_textish(archive: F3dArchive, entry_name: str) -> list[str]:
    try:
        data = archive.read_entry(entry_name)
    except KeyError:
        return []
    return [match.decode("ascii", errors="ignore") for match in ASCII_STRING_RE.findall(data)]
