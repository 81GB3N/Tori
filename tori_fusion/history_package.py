from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .inspector import inspect_archive


@dataclass(frozen=True)
class HistoryPackagePaths:
    step_path: Path
    history_path: Path


def package_paths(output_step: str | Path) -> HistoryPackagePaths:
    step_path = Path(output_step).expanduser().resolve()
    history_path = step_path.with_suffix(step_path.suffix + ".history.json")
    return HistoryPackagePaths(step_path=step_path, history_path=history_path)


def write_history_report(
    destination_json: str | Path,
    source_f3d: str | Path,
    fusion_history: dict[str, Any],
    *,
    step_path: str | Path | None = None,
) -> Path:
    destination = Path(destination_json).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    inspector_report: dict[str, Any]
    inspector_error: str | None = None
    try:
        inspector_report = inspect_archive(source_f3d)
    except Exception as exc:
        inspector_report = {}
        inspector_error = str(exc)
    payload = {
        "version": 1,
        "source_f3d": str(Path(source_f3d).expanduser().resolve()),
        "step_path": str(Path(step_path).expanduser().resolve()) if step_path else None,
        "feature_hints": inspector_report.get("feature_hints", {}),
        "inspector_report": {
            "entry_count": inspector_report.get("entry_count"),
            "segments": inspector_report.get("segments", []),
            "string_hints": inspector_report.get("string_hints", []),
            "properties": inspector_report.get("properties"),
        },
        "inspector_error": inspector_error,
        "fusion_history": fusion_history,
    }
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return destination


def write_history_sidecar(
    source_f3d: str | Path,
    output_step: str | Path,
    fusion_history: dict[str, Any],
) -> HistoryPackagePaths:
    paths = package_paths(output_step)
    write_history_report(paths.history_path, source_f3d, fusion_history, step_path=paths.step_path)
    return paths


def read_history_sidecar(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).expanduser().resolve().read_text())


def make_reconstruction_patch(history_sidecar: dict[str, Any], output_f3d: str | Path) -> dict[str, Any]:
    fusion_history = history_sidecar.get("fusion_history", {})
    parameters = {
        item["name"]: item["expression"]
        for item in fusion_history.get("parameters", [])
        if item.get("name") and item.get("expression")
    }
    sketches = []
    for sketch_index, item in enumerate(fusion_history.get("sketches", [])):
        sketch_type = item.get("type")
        if sketch_type not in {"origin_rectangle", "planar_curve_set"}:
            continue
        patch_sketch = {
            "name": item["name"],
            "type": sketch_type,
            "plane_name": item.get("plane_name"),
            "origin": item.get("origin"),
            "x_direction": item.get("x_direction"),
            "y_direction": item.get("y_direction"),
            "lines": item.get("lines", []),
            "circles": item.get("circles", []),
            "arcs": item.get("arcs", []),
            "fitted_splines": item.get("fitted_splines", []),
        }
        if sketch_type == "origin_rectangle":
            width_param = _ensure_parameter_reference(
                parameters,
                preferred_name=_preferred_parameter_name("width", sketch_index, item),
                reference=item.get("width_param"),
                expression=item.get("width_expression"),
            )
            height_param = _ensure_parameter_reference(
                parameters,
                preferred_name=_preferred_parameter_name("height", sketch_index, item),
                reference=item.get("height_param"),
                expression=item.get("height_expression"),
            )
            if not width_param or not height_param:
                continue
            patch_sketch["width_param"] = width_param
            patch_sketch["height_param"] = height_param
        sketches.append(patch_sketch)

    extrudes = []
    for extrude_index, item in enumerate(fusion_history.get("extrudes", [])):
        distance_param = _ensure_parameter_reference(
            parameters,
            preferred_name=_preferred_parameter_name("depth", extrude_index, item),
            reference=item.get("distance_param"),
            expression=item.get("distance_expression"),
        )
        if not distance_param:
            continue
        start_param = _ensure_optional_parameter_reference(
            parameters,
            preferred_name=_preferred_parameter_name("start", extrude_index, item),
            reference=item.get("start_param"),
            expression=item.get("start_expression"),
        )
        extrudes.append(
            {
                "name": item["name"],
                "distance_param": distance_param,
                "operation": item.get("operation", "new_body"),
                "direction": item.get("direction", "positive"),
                "sketch_name": item.get("sketch_name"),
                "profile_index": item.get("profile_index", 0),
                "start_param": start_param,
            }
        )

    return {
        "input_step": history_sidecar.get("step_path"),
        "output_f3d": str(Path(output_f3d).expanduser().resolve()),
        "parameters": parameters,
        "sketches": sketches,
        "extrudes": extrudes,
        "unsupported_reasons": fusion_history.get("unsupported_reasons", []),
    }


def _ensure_parameter_reference(
    parameters: dict[str, str],
    *,
    preferred_name: str,
    reference: str | None,
    expression: str | None,
) -> str | None:
    if reference and reference in parameters:
        return reference
    if expression and expression in parameters:
        return expression
    fallback_expression = expression or reference
    if not fallback_expression:
        return None
    synthetic_name = _normalize_parameter_name(preferred_name)
    suffix = 2
    while synthetic_name in parameters and parameters[synthetic_name] != fallback_expression:
        synthetic_name = f"{_normalize_parameter_name(preferred_name)}_{suffix}"
        suffix += 1
    parameters[synthetic_name] = fallback_expression
    return synthetic_name


def _normalize_parameter_name(value: str) -> str:
    normalized = "".join(ch if ch.isalnum() else "_" for ch in value).strip("_").lower()
    return normalized or "param"


def _preferred_parameter_name(kind: str, index: int, item: dict[str, Any]) -> str:
    if kind in {"width", "height", "depth"} and index == 0:
        return kind
    base_name = item.get("name") or ("Sketch" if kind in {"width", "height"} else "Extrude")
    suffix = "distance" if kind == "depth" else kind
    return f"{base_name}_{suffix}"


def _ensure_optional_parameter_reference(
    parameters: dict[str, str],
    *,
    preferred_name: str,
    reference: str | None,
    expression: str | None,
) -> str | None:
    fallback_expression = expression or reference
    if not fallback_expression:
        return None
    return _ensure_parameter_reference(
        parameters,
        preferred_name=preferred_name,
        reference=reference,
        expression=expression,
    )
