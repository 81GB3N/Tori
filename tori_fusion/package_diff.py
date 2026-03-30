from __future__ import annotations

from typing import Any


def diff_history_packages(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_history = left.get("fusion_history", {})
    right_history = right.get("fusion_history", {})
    return {
        "parameters": _diff_named_lists(
            left_history.get("parameters", []),
            right_history.get("parameters", []),
        ),
        "sketches": _diff_named_lists(
            left_history.get("sketches", []),
            right_history.get("sketches", []),
        ),
        "extrudes": _diff_named_lists(
            left_history.get("extrudes", []),
            right_history.get("extrudes", []),
        ),
        "lofts": _diff_named_lists(
            left_history.get("lofts", []),
            right_history.get("lofts", []),
        ),
        "fillets": _diff_named_lists(
            left_history.get("fillets", []),
            right_history.get("fillets", []),
        ),
        "moves": _diff_named_lists(
            left_history.get("moves", []),
            right_history.get("moves", []),
        ),
        "scales": _diff_named_lists(
            left_history.get("scales", []),
            right_history.get("scales", []),
        ),
        "feature_hints": {
            "left": left.get("feature_hints", {}),
            "right": right.get("feature_hints", {}),
        },
        "unsupported_reasons": {
            "left": left_history.get("unsupported_reasons", []),
            "right": right_history.get("unsupported_reasons", []),
        },
    }


def _diff_named_lists(left_items: list[dict[str, Any]], right_items: list[dict[str, Any]]) -> dict[str, Any]:
    left_map = {item.get("name"): item for item in left_items if item.get("name")}
    right_map = {item.get("name"): item for item in right_items if item.get("name")}
    names = sorted(set(left_map) | set(right_map))
    changed = []
    only_left = []
    only_right = []
    for name in names:
        if name not in right_map:
            only_left.append(name)
        elif name not in left_map:
            only_right.append(name)
        elif left_map[name] != right_map[name]:
            changed.append({"name": name, "left": left_map[name], "right": right_map[name]})
    return {
        "only_left": only_left,
        "only_right": only_right,
        "changed": changed,
    }
