from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from .bridge_protocol import DEFAULT_BRIDGE_DIR, ensure_bridge_dirs, read_request, update_heartbeat, write_response
from .bridge_version import BRIDGE_VERSION
try:
    import adsk.core  # type: ignore[import-not-found]
    import adsk.fusion  # type: ignore[import-not-found]
except ImportError:
    adsk = None  # type: ignore[assignment]
else:
    adsk = sys.modules["adsk"]


EVENT_ID = "ToriFusionBridgeJob"
POLL_INTERVAL_SECONDS = 2.0
SUPPORTED_OPERATIONS = ["export_package", "rehydrate_package", "capture_history"]

_handlers: list[Any] = []
_worker_thread: threading.Thread | None = None
_stop_event = threading.Event()
_pending_lock = threading.Lock()
_pending_requests: list[Path] = []


def run(context: Any) -> None:
    del context
    if adsk is None:
        raise RuntimeError("Fusion bridge add-in must run inside Fusion 360.")

    app = adsk.core.Application.get()
    custom_event = app.registerCustomEvent(EVENT_ID)
    handler = _BridgeEventHandler()
    custom_event.add(handler)
    _handlers.append(handler)

    update_heartbeat(
        status="starting",
        details={"event_id": EVENT_ID, "capabilities": SUPPORTED_OPERATIONS, "bridge_version": BRIDGE_VERSION},
    )

    global _worker_thread
    _stop_event.clear()
    _worker_thread = threading.Thread(target=_watch_requests, daemon=True)
    _worker_thread.start()

    update_heartbeat(
        status="idle",
        details={"event_id": EVENT_ID, "capabilities": SUPPORTED_OPERATIONS, "bridge_version": BRIDGE_VERSION},
    )
    app.log("Tori Fusion bridge started.")


def stop(context: Any) -> None:
    del context
    _stop_event.set()
    app = adsk.core.Application.get() if adsk else None
    if app:
        try:
            app.unregisterCustomEvent(EVENT_ID)
        except Exception:
            app.log(f"Failed to unregister custom event:\n{traceback.format_exc()}")
    update_heartbeat(
        status="stopped",
        details={"event_id": EVENT_ID, "capabilities": SUPPORTED_OPERATIONS, "bridge_version": BRIDGE_VERSION},
    )


class _BridgeEventHandler(adsk.core.CustomEventHandler):  # type: ignore[misc]
    def __init__(self) -> None:
        super().__init__()

    def notify(self, args: Any) -> None:
        try:
            with _pending_lock:
                batch = list(_pending_requests)
                _pending_requests.clear()
            for request_path in batch:
                _handle_request(request_path)
        except Exception:
            app = adsk.core.Application.get()
            app.log(f"Tori bridge handler failure:\n{traceback.format_exc()}")


def _watch_requests() -> None:
    bridge = ensure_bridge_dirs(DEFAULT_BRIDGE_DIR)
    app = adsk.core.Application.get()
    while not _stop_event.is_set():
        update_heartbeat(
            status="idle",
            details={
                "queue_dir": str(bridge.requests),
                "capabilities": SUPPORTED_OPERATIONS,
                "bridge_version": BRIDGE_VERSION,
            },
        )
        for request_path in sorted(bridge.requests.glob("*.json")):
            with _pending_lock:
                if request_path not in _pending_requests:
                    _pending_requests.append(request_path)
            app.fireCustomEvent(EVENT_ID, str(request_path))
        time.sleep(POLL_INTERVAL_SECONDS)


def _handle_request(request_path: Path) -> None:
    request = read_request(request_path)
    request_id = request["request_id"]
    operation = request["operation"]
    payload = request["payload"]
    try:
        update_heartbeat(
            status="processing",
            details={
                "request_id": request_id,
                "operation": operation,
                "capabilities": SUPPORTED_OPERATIONS,
                "bridge_version": BRIDGE_VERSION,
            },
        )
        if operation == "export_package":
            result = _export_package(payload)
        elif operation == "rehydrate_package":
            result = _rehydrate_package(payload)
        elif operation == "capture_history":
            result = _capture_history_from_f3d(payload)
        else:
            raise RuntimeError(f"Unsupported bridge operation: {operation}")

        write_response(
            request_id,
            {
                "ok": True,
                "request_id": request_id,
                "operation": operation,
                "result": result,
            },
        )
    except Exception:
        write_response(
            request_id,
            {
                "ok": False,
                "request_id": request_id,
                "operation": operation,
                "error": traceback.format_exc(),
            },
        )
    finally:
        request_path.unlink(missing_ok=True)
        update_heartbeat(
            status="idle",
            details={
                "last_request_id": request_id,
                "capabilities": SUPPORTED_OPERATIONS,
                "bridge_version": BRIDGE_VERSION,
            },
        )


def _export_package(payload: dict[str, Any]) -> dict[str, Any]:
    input_f3d = Path(payload["input_f3d"]).expanduser().resolve()
    output_step = Path(payload["output_step"]).expanduser().resolve()
    output_step.parent.mkdir(parents=True, exist_ok=True)

    document, design, target_component = _open_f3d_document(input_f3d)
    try:
        export_manager = design.exportManager
        step_options = export_manager.createSTEPExportOptions(str(output_step), target_component)
        export_manager.execute(step_options)

        fusion_history = _capture_history(design, target_component)
        return {
            "step_path": str(output_step),
            "source_f3d": str(input_f3d),
            "fusion_history": fusion_history,
            "reconstructable": fusion_history.get("reconstructable", False),
        }
    finally:
        document.close(False)


def _rehydrate_package(payload: dict[str, Any]) -> dict[str, Any]:
    output_f3d = Path(payload["output_f3d"]).expanduser().resolve()
    output_f3d.parent.mkdir(parents=True, exist_ok=True)
    step_path = Path(payload["input_step"]).expanduser().resolve()
    parameters = payload.get("parameters", {})
    sketches = payload.get("sketches", [])
    extrudes = payload.get("extrudes", [])
    unsupported_reasons = payload.get("unsupported_reasons", [])

    app = adsk.core.Application.get()
    document = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise RuntimeError("Active product is not a Fusion design.")

    try:
        if _should_feature_rebuild(sketches, extrudes, unsupported_reasons):
            target_component = _rebuild_supported_design(design, parameters, sketches, extrudes)
            mode = "feature_rebuild"
        else:
            target_component = _import_step_into_design(app, design, step_path)
            _upsert_user_parameters(design, parameters)
            mode = "step_import"

        export_options = design.exportManager.createFusionArchiveExportOptions(str(output_f3d), target_component)
        design.exportManager.execute(export_options)
        return {
            "output_f3d": str(output_f3d),
            "mode": mode,
            "fallback_reasons": unsupported_reasons if mode == "step_import" else [],
            "replayed_sketches": len(sketches) if mode == "feature_rebuild" else 0,
            "replayed_extrudes": len(extrudes) if mode == "feature_rebuild" else 0,
        }
    finally:
        document.close(False)


def _capture_history_from_f3d(payload: dict[str, Any]) -> dict[str, Any]:
    input_f3d = Path(payload["input_f3d"]).expanduser().resolve()
    document, design, target_component = _open_f3d_document(input_f3d)
    try:
        fusion_history = _capture_history(design, target_component)
        return {
            "source_f3d": str(input_f3d),
            "fusion_history": fusion_history,
            "reconstructable": fusion_history.get("reconstructable", False),
        }
    finally:
        document.close(False)


def _open_f3d_document(path: Path) -> tuple[Any, Any, Any]:
    app = adsk.core.Application.get()
    last_error: Exception | None = None
    for attempt in range(5):
        document = None
        try:
            document = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
            design = adsk.fusion.Design.cast(app.activeProduct)
            if not design:
                raise RuntimeError("Active product is not a Fusion design.")

            options = app.importManager.createFusionArchiveImportOptions(str(path))
            if hasattr(app.importManager, "importToTarget2"):
                results = app.importManager.importToTarget2(options, design.rootComponent)
                imported = _first_import_result(results)
            else:
                app.importManager.importToTarget(options, design.rootComponent)
                imported = None
            target_component = _as_component(imported, design.rootComponent)
            return document, design, target_component
        except RuntimeError as exc:
            last_error = exc
            if document:
                try:
                    document.close(False)
                except Exception:
                    pass
            if "InternalValidationError" not in str(exc) or attempt == 4:
                raise
            time.sleep(1.0)
    raise last_error if last_error else RuntimeError(f"Failed to open Fusion archive: {path}")


def _import_step_into_design(app: Any, design: Any, step_path: Path) -> Any:
    options = app.importManager.createSTEPImportOptions(str(step_path))
    if hasattr(app.importManager, "importToTarget2"):
        results = app.importManager.importToTarget2(options, design.rootComponent)
        imported = _first_import_result(results)
    else:
        app.importManager.importToTarget(options, design.rootComponent)
        imported = None
    return _as_component(imported, design.rootComponent)


def _capture_history(design: Any, component: Any) -> dict[str, Any]:
    parameters = []
    for index in range(design.userParameters.count):
        parameter = design.userParameters.item(index)
        parameters.append(
            {
                "name": parameter.name,
                "expression": parameter.expression,
                "comment": parameter.comment,
                "unit": parameter.unit,
            }
        )

    sketches = []
    for index in range(component.sketches.count):
        sketch = component.sketches.item(index)
        record = _capture_sketch(sketch, index)
        sketches.append(record)

    extrudes = []
    for index in range(component.features.extrudeFeatures.count):
        extrude = component.features.extrudeFeatures.item(index)
        extrudes.append(_capture_extrude(extrude, sketches))

    lofts = _capture_generic_features(getattr(component.features, "loftFeatures", None), "loft")
    fillets = _capture_generic_features(getattr(component.features, "filletFeatures", None), "fillet")
    moves = _capture_generic_features(getattr(component.features, "moveFeatures", None), "move")
    scales = _capture_generic_features(getattr(component.features, "scaleFeatures", None), "scale")

    unsupported_reasons = _unsupported_reasons(sketches, lofts, fillets, moves, scales)
    reconstructable = not unsupported_reasons and bool(sketches) and bool(extrudes)
    return {
        "parameters": parameters,
        "sketches": sketches,
        "extrudes": extrudes,
        "lofts": lofts,
        "fillets": fillets,
        "moves": moves,
        "scales": scales,
        "unsupported_reasons": unsupported_reasons,
        "reconstructable": reconstructable,
    }


def _capture_sketch(sketch: Any, sketch_index: int) -> dict[str, Any]:
    width_param = None
    height_param = None
    width_expression = None
    height_expression = None
    sketch_curves = sketch.sketchCurves
    dimension_count = sketch.sketchDimensions.count
    line_count = getattr(sketch_curves.sketchLines, "count", 0)
    arc_count = getattr(getattr(sketch_curves, "sketchArcs", None), "count", 0)
    circle_count = getattr(getattr(sketch_curves, "sketchCircles", None), "count", 0)
    fitted_spline_count = getattr(getattr(sketch_curves, "sketchFittedSplines", None), "count", 0)
    control_point_spline_count = getattr(getattr(sketch_curves, "sketchControlPointSplines", None), "count", 0)
    fixed_spline_count = getattr(getattr(sketch_curves, "sketchFixedSplines", None), "count", 0)
    linked_curve_count = 0
    reference_curve_count = 0
    lines = []
    circles = []
    arcs = []
    fitted_splines = []

    horizontal_orientation = getattr(adsk.fusion.DimensionOrientations, "HorizontalDimensionOrientation", None)
    vertical_orientation = getattr(adsk.fusion.DimensionOrientations, "VerticalDimensionOrientation", None)

    for index in range(dimension_count):
        dimension = sketch.sketchDimensions.item(index)
        parameter = getattr(dimension, "parameter", None)
        if not parameter:
            continue
        orientation = getattr(dimension, "orientation", None)
        if orientation == horizontal_orientation and not width_param:
            width_param = parameter.expression
            width_expression = parameter.expression
        elif orientation == vertical_orientation and not height_param:
            height_param = parameter.expression
            height_expression = parameter.expression

    for collection_name in (
        "sketchLines",
        "sketchArcs",
        "sketchCircles",
        "sketchEllipses",
        "sketchFittedSplines",
        "sketchControlPointSplines",
        "sketchFixedSplines",
    ):
        collection = getattr(sketch_curves, collection_name, None)
        if not collection:
            continue
        for curve_index in range(getattr(collection, "count", 0)):
            curve = collection.item(curve_index)
            if getattr(curve, "isLinked", False):
                linked_curve_count += 1
            if getattr(curve, "isReference", False):
                reference_curve_count += 1

    for curve_index in range(line_count):
        line = sketch_curves.sketchLines.item(curve_index)
        lines.append(
            {
                "start": _sketch_point_to_dict(line.startSketchPoint),
                "end": _sketch_point_to_dict(line.endSketchPoint),
            }
        )

    sketch_circles = getattr(sketch_curves, "sketchCircles", None)
    for curve_index in range(circle_count):
        circle = sketch_circles.item(curve_index)
        circles.append(
            {
                "center": _sketch_point_to_dict(circle.centerSketchPoint),
                "radius": float(circle.radius),
            }
        )

    sketch_arcs = getattr(sketch_curves, "sketchArcs", None)
    for curve_index in range(arc_count):
        arc = sketch_arcs.item(curve_index)
        arcs.append(
            {
                "center": _sketch_point_to_dict(arc.centerSketchPoint),
                "start": _sketch_point_to_dict(arc.startSketchPoint),
                "end": _sketch_point_to_dict(arc.endSketchPoint),
            }
        )

    sketch_fitted_splines = getattr(sketch_curves, "sketchFittedSplines", None)
    for curve_index in range(fitted_spline_count):
        spline = sketch_fitted_splines.item(curve_index)
        fit_points = getattr(spline, "fitPoints", None)
        points = []
        if fit_points:
            for point_index in range(fit_points.count):
                points.append(_sketch_point_to_dict(fit_points.item(point_index)))
        fitted_splines.append({"points": points})

    plane_name = None
    reference_plane = getattr(sketch, "referencePlane", None)
    if reference_plane:
        plane_name = getattr(reference_plane, "name", None)
    origin = _point_to_dict(sketch.origin)
    x_direction = _vector_to_dict(sketch.xDirection)
    y_direction = _vector_to_dict(sketch.yDirection)

    supported_curve_set = (
        line_count + arc_count + circle_count + fitted_spline_count > 0
        and control_point_spline_count == 0
        and fixed_spline_count == 0
    )
    if (
        sketch_index == 0
        and line_count == 4
        and width_param
        and height_param
        and arc_count == 0
        and circle_count == 0
        and fitted_spline_count == 0
        and control_point_spline_count == 0
        and fixed_spline_count == 0
    ):
        sketch_type = "origin_rectangle"
    elif supported_curve_set:
        sketch_type = "planar_curve_set"
    else:
        sketch_type = "unknown"
    return {
        "name": sketch.name,
        "type": sketch_type,
        "plane_name": plane_name,
        "origin": origin,
        "x_direction": x_direction,
        "y_direction": y_direction,
        "profile_count": getattr(sketch.profiles, "count", 0),
        "width_param": width_param,
        "height_param": height_param,
        "width_expression": width_expression,
        "height_expression": height_expression,
        "line_count": line_count,
        "arc_count": arc_count,
        "circle_count": circle_count,
        "fitted_spline_count": fitted_spline_count,
        "control_point_spline_count": control_point_spline_count,
        "fixed_spline_count": fixed_spline_count,
        "linked_curve_count": linked_curve_count,
        "reference_curve_count": reference_curve_count,
        "dimension_count": dimension_count,
        "lines": lines,
        "circles": circles,
        "arcs": arcs,
        "fitted_splines": fitted_splines,
    }


def _capture_extrude(extrude: Any, sketches: list[dict[str, Any]]) -> dict[str, Any]:
    extent = getattr(extrude, "extentOne", None)
    distance = getattr(extent, "distance", None) if extent else None
    expression = getattr(distance, "expression", None)
    start_expression = _start_extent_expression(extrude)
    direction = "positive"
    if getattr(extrude, "extentDirection", None) == getattr(
        adsk.fusion.ExtentDirections, "NegativeExtentDirection", None
    ):
        direction = "negative"
    operation = _operation_name(getattr(extrude, "operation", None))
    sketch_name, profile_index = _extrude_profile_reference(extrude, sketches)
    return {
        "name": extrude.name,
        "object_type": getattr(extrude, "objectType", None),
        "distance_param": expression,
        "distance_expression": expression,
        "direction": direction,
        "operation": operation,
        "sketch_name": sketch_name,
        "profile_index": profile_index,
        "start_param": start_expression,
        "start_expression": start_expression,
    }


def _capture_generic_features(collection: Any, feature_kind: str) -> list[dict[str, Any]]:
    if not collection:
        return []
    captured = []
    for index in range(getattr(collection, "count", 0)):
        feature = collection.item(index)
        captured.append(
            {
                "name": getattr(feature, "name", f"{feature_kind}_{index + 1}"),
                "kind": feature_kind,
                "object_type": getattr(feature, "objectType", None),
            }
        )
    return captured


def _unsupported_reasons(
    sketches: list[dict[str, Any]],
    lofts: list[dict[str, Any]],
    fillets: list[dict[str, Any]],
    moves: list[dict[str, Any]],
    scales: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    if not sketches:
        reasons.append("missing_sketches")
    for sketch in sketches:
        if sketch.get("type") not in {"origin_rectangle", "planar_curve_set"}:
            reasons.append(f"unsupported_sketch:{sketch.get('name', 'unknown')}")
        if sketch.get("control_point_spline_count", 0):
            reasons.append(f"sketch_has_control_point_splines:{sketch.get('name', 'unknown')}")
        if sketch.get("fixed_spline_count", 0):
            reasons.append(f"sketch_has_fixed_splines:{sketch.get('name', 'unknown')}")
    if lofts:
        reasons.append("loft_features_present")
    if fillets:
        reasons.append("fillet_features_present")
    if moves:
        reasons.append("move_features_present")
    if scales:
        reasons.append("scale_features_present")
    return sorted(dict.fromkeys(reasons))


def _rebuild_supported_design(design: Any, parameters: dict[str, str], sketches: list[dict[str, Any]], extrudes: list[dict[str, Any]]) -> Any:
    root = design.rootComponent
    _upsert_user_parameters(design, parameters)
    rebuilt_sketches: dict[str, Any] = {}
    rebuilt_sketch_data: dict[str, dict[str, Any]] = {}
    for sketch_data in sketches:
        sketch = _rebuild_sketch(root, design, sketch_data, parameters)
        rebuilt_sketches[sketch.name] = sketch
        rebuilt_sketch_data[sketch.name] = sketch_data

    extrudes_collection = root.features.extrudeFeatures
    default_sketch = next(iter(rebuilt_sketches.values()), None)
    default_sketch_data = rebuilt_sketch_data.get(default_sketch.name) if default_sketch else None
    for target_extrude in extrudes:
        sketch_name = target_extrude.get("sketch_name")
        sketch = rebuilt_sketches.get(sketch_name) if sketch_name else default_sketch
        sketch_data = rebuilt_sketch_data.get(sketch_name) if sketch_name else default_sketch_data
        if not sketch or sketch.profiles.count == 0:
            continue
        profile_index = int(target_extrude.get("profile_index", 0) or 0)
        if profile_index >= sketch.profiles.count:
            profile_index = 0
        profile = sketch.profiles.item(profile_index)
        distance_expression = parameters[target_extrude["distance_param"]]
        value_input = adsk.core.ValueInput.createByString(distance_expression)
        operation = _operation_enum(target_extrude.get("operation"))
        extrude_input = extrudes_collection.createInput(profile, operation)
        start_expression = parameters.get(target_extrude.get("start_param", ""), target_extrude.get("start_expression"))
        if start_expression and not _is_zero_expression(start_expression):
            extrude_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
                adsk.core.ValueInput.createByString(start_expression)
            )
        direction = _rebuilt_extent_direction(sketch, sketch_data, target_extrude.get("direction"))
        extrude_input.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(value_input), direction)
        extrude_feature = extrudes_collection.add(extrude_input)
        extrude_feature.name = target_extrude.get("name", "Extrude")
    return root


def _rebuild_sketch(root: Any, design: Any, sketch_data: dict[str, Any], parameters: dict[str, str]) -> Any:
    sketch = root.sketches.add(_resolve_sketch_plane(root, sketch_data))
    sketch.name = sketch_data.get("name", "Sketch")
    sketch_type = sketch_data.get("type")
    if sketch_type == "origin_rectangle":
        _build_origin_rectangle_sketch(sketch, design, sketch_data, parameters)
        return sketch

    _build_generic_sketch_geometry(sketch, sketch_data)
    return sketch


def _resolve_sketch_plane(root: Any, sketch_data: dict[str, Any]) -> Any:
    plane_name = sketch_data.get("plane_name")
    if plane_name == getattr(root.xYConstructionPlane, "name", None):
        return root.xYConstructionPlane
    if plane_name == getattr(root.xZConstructionPlane, "name", None):
        return root.xZConstructionPlane
    if plane_name == getattr(root.yZConstructionPlane, "name", None):
        return root.yZConstructionPlane
    if sketch_data.get("type") == "planar_curve_set":
        derived_plane = _construction_plane_from_sketch_data(root, sketch_data)
        if derived_plane:
            return derived_plane
    return root.xYConstructionPlane


def _build_origin_rectangle_sketch(sketch: Any, design: Any, sketch_data: dict[str, Any], parameters: dict[str, str]) -> None:
    if sketch_data.get("lines"):
        built_lines = _build_generic_sketch_geometry(sketch, sketch_data)
        if built_lines:
            _apply_rectangle_dimensions(sketch, built_lines, sketch_data, parameters)
            return

    width_expression = parameters[sketch_data["width_param"]]
    height_expression = parameters[sketch_data["height_param"]]
    width_value = design.unitsManager.evaluateExpression(width_expression, design.unitsManager.defaultLengthUnits)
    height_value = design.unitsManager.evaluateExpression(height_expression, design.unitsManager.defaultLengthUnits)

    p0 = sketch.sketchPoints.add(adsk.core.Point3D.create(0, 0, 0))
    p1 = sketch.sketchPoints.add(adsk.core.Point3D.create(width_value, 0, 0))
    p2 = sketch.sketchPoints.add(adsk.core.Point3D.create(width_value, height_value, 0))
    p3 = sketch.sketchPoints.add(adsk.core.Point3D.create(0, height_value, 0))

    lines = sketch.sketchCurves.sketchLines
    bottom = lines.addByTwoPoints(p0, p1)
    right = lines.addByTwoPoints(p1, p2)
    top = lines.addByTwoPoints(p2, p3)
    left = lines.addByTwoPoints(p3, p0)
    constraints = sketch.geometricConstraints
    constraints.addHorizontal(bottom)
    constraints.addHorizontal(top)
    constraints.addVertical(right)
    constraints.addVertical(left)

    dims = sketch.sketchDimensions
    horizontal_orientation = getattr(adsk.fusion.DimensionOrientations, "HorizontalDimensionOrientation")
    vertical_orientation = getattr(adsk.fusion.DimensionOrientations, "VerticalDimensionOrientation")
    width_dim = dims.addDistanceDimension(
        bottom.startSketchPoint,
        bottom.endSketchPoint,
        horizontal_orientation,
        adsk.core.Point3D.create(width_value / 2.0, -max(height_value * 0.15, 0.5), 0),
    )
    height_dim = dims.addDistanceDimension(
        right.startSketchPoint,
        right.endSketchPoint,
        vertical_orientation,
        adsk.core.Point3D.create(width_value + max(width_value * 0.15, 0.5), height_value / 2.0, 0),
    )
    width_dim.parameter.expression = width_expression
    height_dim.parameter.expression = height_expression


def _build_generic_sketch_geometry(sketch: Any, sketch_data: dict[str, Any]) -> list[Any]:
    curves = sketch.sketchCurves
    built_lines = []
    for line_data in sketch_data.get("lines", []):
        built_lines.append(
            curves.sketchLines.addByTwoPoints(
                _model_point_to_sketch_point(sketch, line_data["start"]),
                _model_point_to_sketch_point(sketch, line_data["end"]),
            )
        )

    for circle_data in sketch_data.get("circles", []):
        curves.sketchCircles.addByCenterRadius(
            _model_point_to_sketch_point(sketch, circle_data["center"]),
            float(circle_data["radius"]),
        )

    for arc_data in sketch_data.get("arcs", []):
        curves.sketchArcs.addByCenterStartEnd(
            _model_point_to_sketch_point(sketch, arc_data["center"]),
            _model_point_to_sketch_point(sketch, arc_data["start"]),
            _model_point_to_sketch_point(sketch, arc_data["end"]),
        )

    fitted_splines = getattr(curves, "sketchFittedSplines", None)
    for spline_data in sketch_data.get("fitted_splines", []):
        if not fitted_splines:
            continue
        points = adsk.core.ObjectCollection.create()
        for point in spline_data.get("points", []):
            points.add(_model_point_to_sketch_point(sketch, point))
        if points.count >= 2:
            fitted_splines.add(points)
    return built_lines


def _should_feature_rebuild(
    sketches: list[dict[str, Any]],
    extrudes: list[dict[str, Any]],
    unsupported_reasons: list[str] | None,
) -> bool:
    return bool(sketches) and bool(extrudes) and not (unsupported_reasons or [])


def _upsert_user_parameters(design: Any, parameters: dict[str, str]) -> None:
    for name, expression in parameters.items():
        existing = None
        for index in range(design.userParameters.count):
            candidate = design.userParameters.item(index)
            if candidate and candidate.name == name:
                existing = candidate
                break
        if existing:
            existing.expression = expression
        else:
            units = _expression_unit_suffix(expression)
            value_input = adsk.core.ValueInput.createByString(expression)
            design.userParameters.add(name, value_input, units, "")


def _expression_unit_suffix(expression: str) -> str:
    tokens = expression.strip().split()
    return tokens[-1] if len(tokens) > 1 else ""


def _first_import_result(results: Any) -> Any | None:
    if not results:
        return None
    count = getattr(results, "count", 0)
    for index in range(count):
        item = results.item(index)
        if item:
            return item
    return None


def _as_component(target_geometry: Any, fallback: Any) -> Any:
    if not target_geometry:
        return fallback
    occurrence_type = getattr(adsk.fusion.Occurrence, "classType", lambda: "")()
    component_type = getattr(adsk.fusion.Component, "classType", lambda: "")()
    object_type = getattr(target_geometry, "objectType", "")
    if object_type == occurrence_type:
        return target_geometry.component
    if object_type == component_type:
        return target_geometry
    return fallback


def _operation_name(operation: Any) -> str:
    feature_ops = adsk.fusion.FeatureOperations
    if operation == getattr(feature_ops, "JoinFeatureOperation", None):
        return "join"
    if operation == getattr(feature_ops, "CutFeatureOperation", None):
        return "cut"
    if operation == getattr(feature_ops, "IntersectFeatureOperation", None):
        return "intersect"
    if operation == getattr(feature_ops, "NewComponentFeatureOperation", None):
        return "new_component"
    return "new_body"


def _operation_enum(operation_name: str | None) -> Any:
    feature_ops = adsk.fusion.FeatureOperations
    if operation_name == "join":
        return getattr(feature_ops, "JoinFeatureOperation")
    if operation_name == "cut":
        return getattr(feature_ops, "CutFeatureOperation")
    if operation_name == "intersect":
        return getattr(feature_ops, "IntersectFeatureOperation")
    if operation_name == "new_component":
        return getattr(feature_ops, "NewComponentFeatureOperation")
    return getattr(feature_ops, "NewBodyFeatureOperation")


def _extrude_profile_reference(extrude: Any, sketches: list[dict[str, Any]]) -> tuple[str | None, int]:
    try:
        profile_owner = getattr(extrude, "profile", None)
    except RuntimeError:
        if len(sketches) == 1:
            return sketches[0].get("name"), 0
        return None, 0
    first_profile = None
    if profile_owner:
        if hasattr(profile_owner, "count") and hasattr(profile_owner, "item"):
            if getattr(profile_owner, "count", 0):
                try:
                    first_profile = profile_owner.item(0)
                except RuntimeError:
                    first_profile = None
        else:
            first_profile = profile_owner
    parent_sketch = getattr(first_profile, "parentSketch", None) if first_profile else None
    sketch_name = getattr(parent_sketch, "name", None) if parent_sketch else None
    if not sketch_name and len(sketches) == 1:
        sketch_name = sketches[0].get("name")
    profile_index = _profile_index_in_sketch(parent_sketch, first_profile) if parent_sketch and first_profile else 0
    return sketch_name, profile_index


def _point_to_dict(point: Any) -> dict[str, float]:
    return {"x": float(point.x), "y": float(point.y), "z": float(point.z)}


def _dict_to_point(point: dict[str, Any]) -> Any:
    return adsk.core.Point3D.create(float(point["x"]), float(point["y"]), float(point["z"]))


def _sketch_point_to_dict(sketch_point: Any) -> dict[str, float]:
    world_geometry = getattr(sketch_point, "worldGeometry", None)
    if world_geometry:
        return _point_to_dict(world_geometry)
    return _point_to_dict(sketch_point.geometry)


def _vector_to_dict(vector: Any) -> dict[str, float]:
    return {"x": float(vector.x), "y": float(vector.y), "z": float(vector.z)}


def _profile_index_in_sketch(parent_sketch: Any, target_profile: Any) -> int:
    profiles = getattr(parent_sketch, "profiles", None)
    if not profiles:
        return 0
    target_token = getattr(target_profile, "entityToken", None)
    for index in range(getattr(profiles, "count", 0)):
        candidate = profiles.item(index)
        if candidate is target_profile:
            return index
        if target_token and getattr(candidate, "entityToken", None) == target_token:
            return index
    return 0


def _construction_plane_from_sketch_data(root: Any, sketch_data: dict[str, Any]) -> Any | None:
    frame_plane = _construction_plane_from_frame(root, sketch_data)
    if frame_plane:
        return frame_plane
    points = _plane_seed_points(sketch_data)
    axis_plane = _offset_axis_plane(root, points)
    if axis_plane:
        return axis_plane
    if len(points) < 3:
        return None
    plane_input = root.constructionPlanes.createInput()
    for index_a in range(len(points) - 2):
        for index_b in range(index_a + 1, len(points) - 1):
            for index_c in range(index_b + 1, len(points)):
                point_a = _dict_to_point(points[index_a])
                point_b = _dict_to_point(points[index_b])
                point_c = _dict_to_point(points[index_c])
                if _points_are_collinear(point_a, point_b, point_c):
                    continue
                plane_input.setByThreePoints(point_a, point_b, point_c)
                return root.constructionPlanes.add(plane_input)
    return None


def _construction_plane_from_frame(root: Any, sketch_data: dict[str, Any]) -> Any | None:
    origin = sketch_data.get("origin")
    x_direction = sketch_data.get("x_direction")
    y_direction = sketch_data.get("y_direction")
    if not origin or not x_direction or not y_direction:
        return None

    point_a = _dict_to_point(origin)
    point_b = _dict_to_point(
        {
            "x": float(origin["x"]) + float(x_direction["x"]),
            "y": float(origin["y"]) + float(x_direction["y"]),
            "z": float(origin["z"]) + float(x_direction["z"]),
        }
    )
    point_c = _dict_to_point(
        {
            "x": float(origin["x"]) + float(y_direction["x"]),
            "y": float(origin["y"]) + float(y_direction["y"]),
            "z": float(origin["z"]) + float(y_direction["z"]),
        }
    )
    if _points_are_collinear(point_a, point_b, point_c):
        return None
    plane_input = root.constructionPlanes.createInput()
    try:
        plane_input.setByThreePoints(point_a, point_b, point_c)
        return root.constructionPlanes.add(plane_input)
    except RuntimeError:
        axis_plane = _offset_axis_plane_from_frame(root, origin, x_direction, y_direction)
        if axis_plane:
            return axis_plane
        return None


def _offset_axis_plane_from_frame(root: Any, origin: dict[str, Any], x_direction: dict[str, Any], y_direction: dict[str, Any], tolerance: float = 1e-6) -> Any | None:
    normal = _cross_product(x_direction, y_direction)
    axis = _dominant_axis(normal, tolerance)
    if not axis:
        return None
    plane_by_axis = {
        "x": root.yZConstructionPlane,
        "y": root.xZConstructionPlane,
        "z": root.xYConstructionPlane,
    }
    plane_input = root.constructionPlanes.createInput()
    plane_input.setByOffset(
        plane_by_axis[axis],
        adsk.core.ValueInput.createByReal(float(origin[axis])),
    )
    return root.constructionPlanes.add(plane_input)


def _offset_axis_plane(root: Any, points: list[dict[str, float]], tolerance: float = 1e-6) -> Any | None:
    if not points:
        return None
    axis_offsets = {
        "x": [point["x"] for point in points],
        "y": [point["y"] for point in points],
        "z": [point["z"] for point in points],
    }
    plane_by_axis = {
        "x": root.yZConstructionPlane,
        "y": root.xZConstructionPlane,
        "z": root.xYConstructionPlane,
    }
    for axis, values in axis_offsets.items():
        if max(values) - min(values) <= tolerance:
            plane_input = root.constructionPlanes.createInput()
            plane_input.setByOffset(
                plane_by_axis[axis],
                adsk.core.ValueInput.createByReal(values[0]),
            )
            return root.constructionPlanes.add(plane_input)
    return None


def _cross_product(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
    lx = float(left["x"])
    ly = float(left["y"])
    lz = float(left["z"])
    rx = float(right["x"])
    ry = float(right["y"])
    rz = float(right["z"])
    return {
        "x": ly * rz - lz * ry,
        "y": lz * rx - lx * rz,
        "z": lx * ry - ly * rx,
    }


def _dot_product(left: dict[str, Any], right: dict[str, Any]) -> float:
    return (
        float(left["x"]) * float(right["x"])
        + float(left["y"]) * float(right["y"])
        + float(left["z"]) * float(right["z"])
    )


def _dominant_axis(vector: dict[str, float], tolerance: float = 1e-6) -> str | None:
    components = {axis: abs(value) for axis, value in vector.items()}
    axis, magnitude = max(components.items(), key=lambda item: item[1])
    if magnitude <= tolerance:
        return None
    off_axis = [value for key, value in components.items() if key != axis]
    if max(off_axis, default=0.0) > tolerance:
        return None
    return axis


def _model_point_to_sketch_point(sketch: Any, point: dict[str, Any]) -> Any:
    return sketch.modelToSketchSpace(_dict_to_point(point))


def _apply_rectangle_dimensions(sketch: Any, built_lines: list[Any], sketch_data: dict[str, Any], parameters: dict[str, str]) -> None:
    width_param = sketch_data.get("width_param")
    height_param = sketch_data.get("height_param")
    if not width_param or not height_param or len(built_lines) < 2:
        return
    width_expression = parameters.get(width_param)
    height_expression = parameters.get(height_param)
    if not width_expression or not height_expression:
        return

    width_line = built_lines[0]
    height_line = built_lines[1]
    dims = sketch.sketchDimensions
    horizontal_orientation = getattr(adsk.fusion.DimensionOrientations, "HorizontalDimensionOrientation")
    vertical_orientation = getattr(adsk.fusion.DimensionOrientations, "VerticalDimensionOrientation")

    width_start = width_line.startSketchPoint.geometry
    width_end = width_line.endSketchPoint.geometry
    height_start = height_line.startSketchPoint.geometry
    height_end = height_line.endSketchPoint.geometry

    width_dim = dims.addDistanceDimension(
        width_line.startSketchPoint,
        width_line.endSketchPoint,
        horizontal_orientation,
        adsk.core.Point3D.create(
            (width_start.x + width_end.x) / 2.0,
            min(width_start.y, width_end.y) - 0.5,
            0,
        ),
    )
    height_dim = dims.addDistanceDimension(
        height_line.startSketchPoint,
        height_line.endSketchPoint,
        vertical_orientation,
        adsk.core.Point3D.create(
            max(height_start.x, height_end.x) + 0.5,
            (height_start.y + height_end.y) / 2.0,
            0,
        ),
    )
    width_dim.parameter.expression = width_expression
    height_dim.parameter.expression = height_expression


def _rebuilt_extent_direction(sketch: Any, sketch_data: dict[str, Any] | None, direction_name: str | None) -> Any:
    positive_direction = getattr(adsk.fusion.ExtentDirections, "PositiveExtentDirection")
    negative_direction = getattr(adsk.fusion.ExtentDirections, "NegativeExtentDirection")
    use_negative = direction_name == "negative"
    if not sketch_data:
        return negative_direction if use_negative else positive_direction

    source_x = sketch_data.get("x_direction")
    source_y = sketch_data.get("y_direction")
    if not source_x or not source_y:
        return negative_direction if use_negative else positive_direction

    source_normal = _cross_product(source_x, source_y)
    rebuilt_normal = _cross_product(_vector_to_dict(sketch.xDirection), _vector_to_dict(sketch.yDirection))
    if _dot_product(source_normal, rebuilt_normal) < 0:
        use_negative = not use_negative
    return negative_direction if use_negative else positive_direction


def _plane_seed_points(sketch_data: dict[str, Any]) -> list[dict[str, float]]:
    unique: list[dict[str, float]] = []
    seen: set[tuple[float, float, float]] = set()

    def add_point(point: dict[str, Any]) -> None:
        key = (
            round(float(point["x"]), 6),
            round(float(point["y"]), 6),
            round(float(point["z"]), 6),
        )
        if key in seen:
            return
        seen.add(key)
        unique.append({"x": float(point["x"]), "y": float(point["y"]), "z": float(point["z"])})

    for line_data in sketch_data.get("lines", []):
        add_point(line_data["start"])
        add_point(line_data["end"])
    for arc_data in sketch_data.get("arcs", []):
        add_point(arc_data["center"])
        add_point(arc_data["start"])
        add_point(arc_data["end"])
    for spline_data in sketch_data.get("fitted_splines", []):
        for point in spline_data.get("points", []):
            add_point(point)
    return unique


def _points_are_collinear(point_a: Any, point_b: Any, point_c: Any, tolerance: float = 1e-6) -> bool:
    vector_ab = point_b.vectorTo(point_a)
    vector_cb = point_b.vectorTo(point_c)
    cross = vector_ab.crossProduct(vector_cb)
    return cross.length <= tolerance


def _start_extent_expression(extrude: Any) -> str | None:
    start_extent = getattr(extrude, "startExtent", None)
    if not start_extent:
        return None
    for attribute in ("offset", "distance"):
        value = getattr(start_extent, attribute, None)
        expression = getattr(value, "expression", None) if value else None
        if expression:
            return expression
    return None


def _is_zero_expression(expression: str) -> bool:
    normalized = expression.strip().lower()
    return normalized in {"0", "0 mm", "0 cm", "0 m", "0 in", "0 ft"}
