from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import Any

from .patch_schema import ExtrudePatch, FusionPatch, FusionPatchError, SketchPatch, load_patch_file

try:
    import adsk.core  # type: ignore[import-not-found]
    import adsk.fusion  # type: ignore[import-not-found]
except ImportError:
    adsk = None  # type: ignore[assignment]
else:
    adsk = sys.modules["adsk"]


class FusionRuntimeError(RuntimeError):
    """Raised when Fusion-side execution fails."""


def run_patch_file(patch_path: str | Path) -> dict[str, Any]:
    if adsk is None:
        raise FusionRuntimeError("Fusion 360 API modules are not available in this Python runtime.")

    patch = load_patch_file(patch_path)
    app = adsk.core.Application.get()
    if not app:
        raise FusionRuntimeError("Unable to access the Fusion 360 application instance.")

    document = None
    try:
        document = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not design:
            raise FusionRuntimeError("Active product is not a Fusion design.")

        imported_target = _import_archive(app, design, patch.input_path)
        target_component = _as_component(imported_target, design.rootComponent)

        _apply_parameters(design, patch)
        for sketch_patch in patch.sketches:
            _apply_sketch_patch(target_component, patch, sketch_patch)
        for extrude_patch in patch.extrudes:
            _apply_extrude_patch(target_component, patch, extrude_patch)

        _export_archive(design, patch.output_path, target_component)
        return {
            "input": str(patch.input_path),
            "output": str(patch.output_path),
            "target_component": target_component.name,
        }
    finally:
        if document:
            document.close(False)


def resolve_patch_path(argv: list[str] | None = None) -> Path:
    argv = argv or sys.argv[1:]
    if argv:
        return Path(argv[0]).expanduser().resolve()

    env_path = os.getenv("TORI_PATCH_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()

    if adsk is None:
        raise FusionRuntimeError("Patch path must be provided as an argument or TORI_PATCH_PATH.")

    app = adsk.core.Application.get()
    ui = app.userInterface if app else None
    if not ui:
        raise FusionRuntimeError("Patch path must be provided as an argument or TORI_PATCH_PATH.")

    dialog = ui.createFileDialog()
    dialog.title = "Select Tori Fusion Patch JSON"
    dialog.filter = "JSON files (*.json)"
    if dialog.showOpen() != adsk.core.DialogResults.DialogOK:
        raise FusionRuntimeError("Patch selection canceled.")
    return Path(dialog.filename).expanduser().resolve()


def run(context: Any) -> None:
    if adsk is None:
        raise RuntimeError("This script must be run inside Fusion 360.")

    app = adsk.core.Application.get()
    ui = app.userInterface if app else None
    try:
        patch_path = resolve_patch_path()
        result = run_patch_file(patch_path)
        if ui:
            ui.messageBox(f"Patched design exported to:\n{result['output']}")
    except Exception:
        details = traceback.format_exc()
        if ui:
            ui.messageBox(details)
        else:
            raise


def stop(context: Any) -> None:
    del context


def _import_archive(app: Any, design: Any, input_path: Path) -> Any:
    import_manager = app.importManager
    options = import_manager.createFusionArchiveImportOptions(str(input_path))
    if hasattr(import_manager, "importToTarget2"):
        results = import_manager.importToTarget2(options, design.rootComponent)
        imported = _first_imported_target(results)
    else:
        import_manager.importToTarget(options, design.rootComponent)
        imported = None
    return imported or design.rootComponent


def _export_archive(design: Any, output_path: Path, geometry: Any) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    export_manager = design.exportManager
    options = export_manager.createFusionArchiveExportOptions(str(output_path), geometry)
    export_manager.execute(options)


def _first_imported_target(results: Any) -> Any | None:
    if not results:
        return None

    try:
        count = results.count
    except AttributeError:
        return None

    for index in range(count):
        item = results.item(index)
        if item:
            return item
    return None


def _as_component(target_geometry: Any, fallback: Any) -> Any:
    occurrence_type = getattr(adsk.fusion.Occurrence, "classType", lambda: "")()
    component_type = getattr(adsk.fusion.Component, "classType", lambda: "")()
    object_type = getattr(target_geometry, "objectType", "")

    if object_type == occurrence_type:
        return target_geometry.component
    if object_type == component_type:
        return target_geometry
    return fallback


def _apply_parameters(design: Any, patch: FusionPatch) -> None:
    for name, expression in patch.parameters.items():
        parameter = _find_user_parameter(design, name)
        if not parameter:
            raise FusionRuntimeError(f"User parameter {name!r} was not found.")
        try:
            parameter.expression = expression
        except Exception as exc:
            raise FusionRuntimeError(
                f"Failed to update parameter {name!r} with expression {expression!r}: {exc}"
            ) from exc


def _apply_sketch_patch(component: Any, patch: FusionPatch, sketch_patch: SketchPatch) -> None:
    sketch = _find_named_item(component.sketches, sketch_patch.name, "sketch")
    width_expression = _resolve_expression(patch, sketch_patch.width_param)
    height_expression = _resolve_expression(patch, sketch_patch.height_param)

    if _sketch_uses_parameter_name(sketch, sketch_patch.width_param) and _sketch_uses_parameter_name(
        sketch, sketch_patch.height_param
    ):
        return

    dimensions = _classify_sketch_dimensions(sketch)
    if not dimensions["horizontal"] or not dimensions["vertical"]:
        raise FusionRuntimeError(
            f"Sketch {sketch_patch.name!r} does not have a clear horizontal/vertical dimension pair."
        )

    _set_dimension_expression(dimensions["horizontal"], width_expression, sketch_patch.width_param)
    _set_dimension_expression(dimensions["vertical"], height_expression, sketch_patch.height_param)


def _apply_extrude_patch(component: Any, patch: FusionPatch, extrude_patch: ExtrudePatch) -> None:
    extrude = _find_named_item(component.features.extrudeFeatures, extrude_patch.name, "extrude")
    expression = _resolve_expression(patch, extrude_patch.distance_param)

    if _extrude_uses_parameter_name(extrude, extrude_patch.distance_param):
        return

    expected_operation = adsk.fusion.FeatureOperations.NewBodyFeatureOperation
    if getattr(extrude, "operation", expected_operation) != expected_operation:
        raise FusionRuntimeError(
            f"Extrude {extrude_patch.name!r} uses an unsupported operation; expected new body."
        )

    timeline_object = getattr(extrude, "timelineObject", None)
    if timeline_object:
        timeline_object.rollTo(True)

    direction = _direction_enum(extrude_patch.direction)
    value_input = adsk.core.ValueInput.createByString(expression)
    extent = adsk.fusion.DistanceExtentDefinition.create(value_input)

    try:
        extrude.setOneSideExtent(extent, direction)
    except Exception as exc:
        raise FusionRuntimeError(
            f"Failed to update extrude {extrude_patch.name!r} with expression {expression!r}: {exc}"
        ) from exc
    finally:
        timeline = component.parentDesign.timeline
        if timeline:
            timeline.moveToEnd()


def _find_user_parameter(design: Any, name: str) -> Any | None:
    parameters = design.userParameters
    for index in range(parameters.count):
        parameter = parameters.item(index)
        if parameter and parameter.name == name:
            return parameter
    return None


def _find_named_item(collection: Any, name: str, label: str) -> Any:
    for index in range(collection.count):
        item = collection.item(index)
        if item and item.name == name:
            return item
    raise FusionRuntimeError(f"{label.capitalize()} {name!r} was not found.")


def _resolve_expression(patch: FusionPatch, parameter_name: str) -> str:
    expression = patch.parameters.get(parameter_name)
    if not expression:
        raise FusionRuntimeError(
            f"Patch references parameter {parameter_name!r}, but no expression was provided."
        )
    return expression


def _sketch_uses_parameter_name(sketch: Any, parameter_name: str) -> bool:
    for index in range(sketch.sketchDimensions.count):
        dimension = sketch.sketchDimensions.item(index)
        model_parameter = getattr(dimension, "parameter", None)
        expression = getattr(model_parameter, "expression", None)
        if _matches_expression_name(expression, parameter_name):
            return True
    return False


def _extrude_uses_parameter_name(extrude: Any, parameter_name: str) -> bool:
    extent = getattr(extrude, "extentOne", None)
    distance = getattr(extent, "distance", None) if extent else None
    expression = getattr(distance, "expression", None)
    return _matches_expression_name(expression, parameter_name)


def _matches_expression_name(expression: str | None, parameter_name: str) -> bool:
    if not expression:
        return False
    return expression.replace(" ", "").lower() == parameter_name.replace(" ", "").lower()


def _classify_sketch_dimensions(sketch: Any) -> dict[str, Any | None]:
    result = {"horizontal": None, "vertical": None}
    for index in range(sketch.sketchDimensions.count):
        dimension = sketch.sketchDimensions.item(index)
        parameter = getattr(dimension, "parameter", None)
        if not parameter:
            continue

        object_type = getattr(dimension, "objectType", "")
        if "LinearDimension" not in object_type:
            continue

        orientation = getattr(dimension, "orientation", None)
        horizontal = getattr(adsk.fusion.DimensionOrientations, "HorizontalDimensionOrientation", None)
        vertical = getattr(adsk.fusion.DimensionOrientations, "VerticalDimensionOrientation", None)
        if orientation == horizontal and result["horizontal"] is None:
            result["horizontal"] = dimension
        elif orientation == vertical and result["vertical"] is None:
            result["vertical"] = dimension

    return result


def _set_dimension_expression(dimension: Any, expression: str, parameter_name: str) -> None:
    model_parameter = getattr(dimension, "parameter", None)
    if not model_parameter:
        raise FusionRuntimeError(f"Sketch dimension for {parameter_name!r} has no model parameter.")
    try:
        model_parameter.expression = expression
    except Exception as exc:
        raise FusionRuntimeError(
            f"Failed to update sketch dimension for {parameter_name!r} with {expression!r}: {exc}"
        ) from exc


def _direction_enum(direction_name: str) -> Any:
    if direction_name == "positive":
        return adsk.fusion.ExtentDirections.PositiveExtentDirection
    if direction_name == "negative":
        return adsk.fusion.ExtentDirections.NegativeExtentDirection
    raise FusionRuntimeError(f"Unsupported extent direction {direction_name!r}.")


def cli(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print("Usage: python3 scripts/fusion_apply_patch.py /absolute/path/to/patch.json")
        return 2

    patch_path = argv[0]
    try:
        result = run_patch_file(patch_path)
    except (FusionPatchError, FusionRuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Patched design exported to {result['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
