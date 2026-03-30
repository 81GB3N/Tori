from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class FusionPatchError(ValueError):
    """Raised when a JSON patch file is invalid."""


@dataclass(frozen=True)
class SketchPatch:
    name: str
    type: str
    width_param: str
    height_param: str


@dataclass(frozen=True)
class ExtrudePatch:
    name: str
    distance_param: str
    operation: str
    direction: str


@dataclass(frozen=True)
class FusionPatch:
    input_path: Path
    output_path: Path
    parameters: dict[str, str]
    sketches: list[SketchPatch]
    extrudes: list[ExtrudePatch]


def load_patch_file(path: str | Path) -> FusionPatch:
    patch_path = Path(path).expanduser().resolve()

    try:
        payload = json.loads(patch_path.read_text())
    except FileNotFoundError as exc:
        raise FusionPatchError(f"Patch file not found: {patch_path}") from exc
    except json.JSONDecodeError as exc:
        raise FusionPatchError(f"Patch file is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise FusionPatchError("Patch file root must be a JSON object.")

    input_path = _require_path(payload, "input")
    output_path = _require_path(payload, "output")

    if input_path == output_path:
        raise FusionPatchError("`input` and `output` must be different paths.")

    parameters = _parse_parameters(payload.get("parameters", {}))
    sketches = _parse_sketches(payload.get("sketches", []))
    extrudes = _parse_extrudes(payload.get("extrudes", []))

    return FusionPatch(
        input_path=input_path,
        output_path=output_path,
        parameters=parameters,
        sketches=sketches,
        extrudes=extrudes,
    )


def _require_path(payload: dict[str, Any], key: str) -> Path:
    raw_value = payload.get(key)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise FusionPatchError(f"`{key}` must be a non-empty string path.")
    return Path(raw_value).expanduser().resolve()


def _parse_parameters(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise FusionPatchError("`parameters` must be a JSON object.")

    parsed: dict[str, str] = {}
    for name, expression in payload.items():
        if not isinstance(name, str) or not name.strip():
            raise FusionPatchError("Parameter names must be non-empty strings.")
        if not isinstance(expression, str) or not expression.strip():
            raise FusionPatchError(f"Parameter {name!r} must map to a non-empty expression string.")
        parsed[name] = expression.strip()
    return parsed


def _parse_sketches(payload: Any) -> list[SketchPatch]:
    if not isinstance(payload, list):
        raise FusionPatchError("`sketches` must be a JSON array.")

    sketches: list[SketchPatch] = []
    for item in payload:
        if not isinstance(item, dict):
            raise FusionPatchError("Each sketch patch must be a JSON object.")
        sketch = SketchPatch(
            name=_require_string(item, "name"),
            type=_require_string(item, "type"),
            width_param=_require_string(item, "width_param"),
            height_param=_require_string(item, "height_param"),
        )
        if sketch.type != "origin_rectangle":
            raise FusionPatchError(
                f"Unsupported sketch patch type {sketch.type!r}. Only 'origin_rectangle' is supported."
            )
        sketches.append(sketch)
    return sketches


def _parse_extrudes(payload: Any) -> list[ExtrudePatch]:
    if not isinstance(payload, list):
        raise FusionPatchError("`extrudes` must be a JSON array.")

    extrudes: list[ExtrudePatch] = []
    for item in payload:
        if not isinstance(item, dict):
            raise FusionPatchError("Each extrude patch must be a JSON object.")
        extrude = ExtrudePatch(
            name=_require_string(item, "name"),
            distance_param=_require_string(item, "distance_param"),
            operation=_require_string(item, "operation"),
            direction=_require_string(item, "direction"),
        )
        if extrude.operation != "new_body":
            raise FusionPatchError(
                f"Unsupported extrude operation {extrude.operation!r}. Only 'new_body' is supported."
            )
        if extrude.direction not in {"positive", "negative"}:
            raise FusionPatchError(
                f"Unsupported extrude direction {extrude.direction!r}. Use 'positive' or 'negative'."
            )
        extrudes.append(extrude)
    return extrudes


def _require_string(payload: dict[str, Any], key: str) -> str:
    raw_value = payload.get(key)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise FusionPatchError(f"`{key}` must be a non-empty string.")
    return raw_value.strip()
