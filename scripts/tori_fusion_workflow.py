#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tori_fusion.bridge_protocol import (
    bridge_alive,
    create_request,
    ensure_bridge_dirs,
    read_heartbeat,
    wait_for_response,
)
from tori_fusion.bridge_version import BRIDGE_VERSION
from tori_fusion.history_package import (
    make_reconstruction_patch,
    read_history_sidecar,
    write_history_report,
    write_history_sidecar,
)
from tori_fusion.package_diff import diff_history_packages


FUSION_APP = Path.home() / "Applications/Autodesk Fusion.app"
LOG_DIR_ENV = "TORI_FUSION_LOG_DIR"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Tori Fusion sidecar workflow.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export-package",
        help="Export a .f3d to STEP and preserve rough history in a sidecar JSON.",
    )
    export_parser.add_argument("input_f3d")
    export_parser.add_argument("output_step")
    export_parser.add_argument("--timeout", type=int, default=300)

    diff_parser = subparsers.add_parser(
        "diff-package",
        help="Diff two history sidecar JSON files.",
    )
    diff_parser.add_argument("left_history")
    diff_parser.add_argument("right_history")

    rehydrate_parser = subparsers.add_parser(
        "rehydrate-package",
        help="Rebuild or reimport a STEP + history sidecar back into .f3d.",
    )
    rehydrate_parser.add_argument("history_json")
    rehydrate_parser.add_argument("output_f3d")
    rehydrate_parser.add_argument("--timeout", type=int, default=300)

    capture_parser = subparsers.add_parser(
        "capture-history",
        help="Open an .f3d in Fusion and capture rough feature history to JSON.",
    )
    capture_parser.add_argument("input_f3d")
    capture_parser.add_argument("output_json")
    capture_parser.add_argument("--timeout", type=int, default=300)

    args = parser.parse_args()
    if args.command == "export-package":
        return _export_package(args.input_f3d, args.output_step, args.timeout)
    if args.command == "diff-package":
        return _diff_package(args.left_history, args.right_history)
    if args.command == "rehydrate-package":
        return _rehydrate_package(args.history_json, args.output_f3d, args.timeout)
    if args.command == "capture-history":
        return _capture_history_report(args.input_f3d, args.output_json, args.timeout)
    return 2


def _export_package(input_f3d: str, output_step: str, timeout: int) -> int:
    response = _submit_job(
        "export_package",
        {
            "input_f3d": str(Path(input_f3d).expanduser().resolve()),
            "output_step": str(Path(output_step).expanduser().resolve()),
        },
        timeout=timeout,
    )
    if response.get("ok"):
        result = response.get("result", {})
        if isinstance(result, dict):
            sidecar_paths = write_history_sidecar(
                source_f3d=result.get("source_f3d", input_f3d),
                output_step=result.get("step_path", output_step),
                fusion_history=result.get("fusion_history", {}),
            )
            result["history_path"] = str(sidecar_paths.history_path)
    return _print_response(response)


def _rehydrate_package(history_json: str, output_f3d: str, timeout: int) -> int:
    sidecar = read_history_sidecar(history_json)
    patch = make_reconstruction_patch(sidecar, output_f3d)
    response = _submit_job("rehydrate_package", patch, timeout=timeout)
    return _print_response(response)


def _capture_history_report(input_f3d: str, output_json: str, timeout: int) -> int:
    response = _submit_job(
        "capture_history",
        {"input_f3d": str(Path(input_f3d).expanduser().resolve())},
        timeout=timeout,
    )
    if response.get("ok"):
        result = response.get("result", {})
        if isinstance(result, dict):
            report_path = write_history_report(
                output_json,
                source_f3d=result.get("source_f3d", input_f3d),
                fusion_history=result.get("fusion_history", {}),
            )
            result["history_report_path"] = str(report_path)
    return _print_response(response)


def _diff_package(left_history: str, right_history: str) -> int:
    left = read_history_sidecar(left_history)
    right = read_history_sidecar(right_history)
    print(json.dumps(diff_history_packages(left, right), indent=2, sort_keys=True))
    return 0


def _submit_job(operation: str, payload: dict[str, str], timeout: int) -> dict[str, object]:
    _assert_bridge_supports(operation)
    request_id, request_path = create_request(operation, payload)
    _write_log_json(f"{operation}.request.json", _read_json_file(request_path))
    _write_log_json(f"{operation}.heartbeat.before.json", _read_heartbeat_json())
    _launch_fusion()
    response = wait_for_response(request_id, timeout_seconds=timeout)
    _write_log_json(f"{operation}.response.json", response)
    _write_log_json(f"{operation}.heartbeat.after.json", _read_heartbeat_json())
    return response


def _launch_fusion() -> None:
    if bridge_alive():
        return

    if not FUSION_APP.exists():
        raise FileNotFoundError(f"Fusion app not found at {FUSION_APP}")

    launch_command = ["open", str(FUSION_APP)]
    subprocess.run(launch_command, check=True)

    if not bridge_alive():
        print(
            "Waiting for the Fusion bridge add-in. If this is the first run, install the add-in and enable Run on Startup.",
            file=sys.stderr,
        )


def _assert_bridge_supports(operation: str) -> None:
    heartbeat = read_heartbeat()
    if not heartbeat:
        return
    details = heartbeat.get("details", {})
    if not isinstance(details, dict):
        return
    bridge_version = details.get("bridge_version")
    if bridge_version != BRIDGE_VERSION:
        raise RuntimeError(
            f"Fusion bridge version {bridge_version or 'unknown'} is loaded, but this workspace expects {BRIDGE_VERSION}. "
            "Quit Fusion, reopen it, and run the refreshed ToriBridge add-in."
        )
    capabilities = details.get("capabilities")
    if not isinstance(capabilities, list):
        if operation == "capture_history":
            raise RuntimeError(
                "Fusion bridge is running an older build without capability metadata. "
                "Quit Fusion, reopen it, and run the refreshed ToriBridge add-in."
            )
        return
    if operation not in capabilities:
        raise RuntimeError(
            f"Fusion bridge version {bridge_version} does not support {operation}. "
            "Quit Fusion, reopen it, and run the refreshed ToriBridge add-in."
        )


def _print_response(response: dict[str, object]) -> int:
    if response.get("ok"):
        print(json.dumps(response.get("result", {}), indent=2, sort_keys=True))
        return 0
    print(response.get("error", "Fusion bridge failed."), file=sys.stderr)
    return 1


def _read_json_file(path: Path) -> dict[str, object] | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _read_heartbeat_json() -> dict[str, object] | None:
    bridge = ensure_bridge_dirs()
    if not bridge.heartbeat.exists():
        return None
    return _read_json_file(bridge.heartbeat)


def _write_log_json(name: str, payload: dict[str, object] | None) -> None:
    log_dir = os.environ.get(LOG_DIR_ENV)
    if not log_dir:
        return
    path = Path(log_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    target = path / name
    if payload is None:
        target.write_text("null\n")
        return
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
