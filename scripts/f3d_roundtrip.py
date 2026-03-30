#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


WORKFLOW_SCRIPT = ROOT / "scripts" / "tori_fusion_workflow.py"
ARTIFACT_ROOT = ROOT / ".tori-fusion-artifacts"
LOG_ROOT = ROOT / ".tori-fusion-logs"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a Fusion archive to STEP, diff against the previous run, and rehydrate to .f3d."
    )
    parser.add_argument("input_f3d", help="Path to the source .f3d file.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("TORI_FUSION_TIMEOUT", "300")),
        help="Timeout in seconds for each Fusion bridge job. Defaults to TORI_FUSION_TIMEOUT or 300.",
    )
    parser.add_argument(
        "--no-open-step",
        action="store_true",
        help="Do not open the generated STEP file after a successful run.",
    )
    parser.add_argument(
        "--no-open-rehydrated",
        action="store_true",
        help="Do not open the generated rehydrated .f3d after a successful run.",
    )
    args = parser.parse_args()

    input_path = Path(args.input_f3d).expanduser().resolve()
    if not input_path.is_file():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    safe_stem = _safe_name(input_path.stem)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    model_root = ARTIFACT_ROOT / safe_stem
    run_dir = model_root / "runs" / run_id
    current_dir = model_root / "current"
    log_model_root = LOG_ROOT / safe_stem
    log_run_dir = log_model_root / "runs" / run_id
    log_current_dir = log_model_root / "current"
    run_dir.mkdir(parents=True, exist_ok=True)
    current_dir.mkdir(parents=True, exist_ok=True)
    log_run_dir.mkdir(parents=True, exist_ok=True)
    log_current_dir.mkdir(parents=True, exist_ok=True)

    output_step = run_dir / f"{safe_stem}.step"
    output_history = Path(f"{output_step}.history.json")
    output_rehydrated = run_dir / f"{safe_stem}.rehydrated.f3d"
    output_diff = run_dir / f"{safe_stem}.diff.json"
    output_rehydrated_history = run_dir / f"{safe_stem}.rehydrated.history.json"
    output_rehydrated_diff = run_dir / f"{safe_stem}.rehydrated.history.diff.json"
    previous_history = current_dir / "latest.step.history.json"
    summary_path = log_run_dir / "roundtrip.summary.json"
    error_json_path = log_run_dir / "roundtrip.error.json"
    error_log_path = log_run_dir / "roundtrip.error.log"

    try:
        print("Exporting package from:")
        print(f"  {input_path}")
        export_result = _invoke_workflow(
            "export",
            log_run_dir,
            "export-package",
            str(input_path),
            str(output_step),
            "--timeout",
            str(args.timeout),
        )

        if previous_history.exists():
            print("Diffing against previous history package:")
            print(f"  {previous_history}")
            diff_output = _invoke_workflow(
                "diff",
                log_run_dir,
                "diff-package",
                str(previous_history),
                str(output_history),
            ).stdout
            output_diff.write_text(diff_output)
        else:
            output_diff.write_text(json.dumps({"skipped": "no_previous_history_package"}, indent=2) + "\n")
            (log_run_dir / "diff.stdout.log").write_text('{\n  "skipped": "no_previous_history_package"\n}\n')
            (log_run_dir / "diff.stderr.log").write_text("")

        print("Rehydrating package back to Fusion archive:")
        print(f"  {output_rehydrated}")
        rehydrate_result = _invoke_workflow(
            "rehydrate",
            log_run_dir,
            "rehydrate-package",
            str(output_history),
            str(output_rehydrated),
            "--timeout",
            str(args.timeout),
        )

        print("Capturing rebuilt Fusion history:")
        print(f"  {output_rehydrated_history}")
        capture_result = _invoke_workflow(
            "capture",
            log_run_dir,
            "capture-history",
            str(output_rehydrated),
            str(output_rehydrated_history),
            "--timeout",
            str(args.timeout),
        )
        recreated_diff_output = _invoke_workflow(
            "recreated-diff",
            log_run_dir,
            "diff-package",
            str(output_history),
            str(output_rehydrated_history),
        ).stdout
        output_rehydrated_diff.write_text(recreated_diff_output)
    except Exception as exc:
        _write_failure_logs(
            log_run_dir=log_run_dir,
            log_current_dir=log_current_dir,
            summary_path=summary_path,
            error_json_path=error_json_path,
            error_log_path=error_log_path,
            input_path=input_path,
            run_id=run_id,
            run_dir=run_dir,
            exc=exc,
        )
        print()
        print("Round-trip failed.")
        print(f"Error logs saved in: {error_json_path}")
        return 1

    _refresh_link(current_dir / "latest.step", run_dir / f"{safe_stem}.step")
    _refresh_link(current_dir / "latest.step.history.json", run_dir / f"{safe_stem}.step.history.json")
    _refresh_link(current_dir / "latest.rehydrated.f3d", run_dir / f"{safe_stem}.rehydrated.f3d")
    _refresh_link(current_dir / "latest.diff.json", run_dir / f"{safe_stem}.diff.json")
    _refresh_link(current_dir / "latest.rehydrated.history.json", run_dir / f"{safe_stem}.rehydrated.history.json")
    _refresh_link(current_dir / "latest.rehydrated.history.diff.json", run_dir / f"{safe_stem}.rehydrated.history.diff.json")
    _refresh_link(log_current_dir / "latest-export.request.json", log_run_dir / "export-package.request.json")
    _refresh_link(log_current_dir / "latest-export.response.json", log_run_dir / "export-package.response.json")
    _refresh_link(log_current_dir / "latest-rehydrate.request.json", log_run_dir / "rehydrate-package.request.json")
    _refresh_link(log_current_dir / "latest-rehydrate.response.json", log_run_dir / "rehydrate-package.response.json")
    _refresh_link(log_current_dir / "latest-capture.request.json", log_run_dir / "capture_history.request.json")
    _refresh_link(log_current_dir / "latest-capture.response.json", log_run_dir / "capture_history.response.json")
    _refresh_link(log_current_dir / "latest-summary.json", summary_path)
    _refresh_link(log_current_dir / "latest-export.stdout.log", log_run_dir / "export.stdout.log")
    _refresh_link(log_current_dir / "latest-export.stderr.log", log_run_dir / "export.stderr.log")
    _refresh_link(log_current_dir / "latest-rehydrate.stdout.log", log_run_dir / "rehydrate.stdout.log")
    _refresh_link(log_current_dir / "latest-rehydrate.stderr.log", log_run_dir / "rehydrate.stderr.log")
    _refresh_link(log_current_dir / "latest-capture.stdout.log", log_run_dir / "capture.stdout.log")
    _refresh_link(log_current_dir / "latest-capture.stderr.log", log_run_dir / "capture.stderr.log")
    _refresh_link(log_current_dir / "latest-diff.stdout.log", log_run_dir / "diff.stdout.log")
    _refresh_link(log_current_dir / "latest-recreated-diff.stdout.log", log_run_dir / "recreated-diff.stdout.log")
    (log_current_dir / "latest-error.json").unlink(missing_ok=True)
    (log_current_dir / "latest-error.log").unlink(missing_ok=True)

    summary = {
        "input_f3d": str(input_path),
        "run_id": run_id,
        "artifact_dir": str(run_dir),
        "log_dir": str(log_run_dir),
        "step_path": str(output_step),
        "history_path": str(output_history),
        "diff_path": str(output_diff),
        "rehydrated_path": str(output_rehydrated),
        "rehydrated_history_path": str(output_rehydrated_history),
        "rehydrated_history_diff_path": str(output_rehydrated_diff),
        "previous_history": str(previous_history) if previous_history.exists() else None,
        "export_returncode": export_result.returncode,
        "rehydrate_returncode": rehydrate_result.returncode,
        "capture_returncode": capture_result.returncode,
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    if not args.no_open_step:
        _open_path(output_step, log_run_dir / "open-step.log")
    if not args.no_open_rehydrated:
        _open_path(output_rehydrated, log_run_dir / "open-rehydrated.log")

    print()
    print("Pipeline summary:")
    print(f"  1. Source .f3d opened in Fusion: {input_path}")
    print(f"  2. STEP exported:              {output_step}")
    print(f"  3. Source history sidecar:     {output_history}")
    print(f"  4. Previous-run diff:          {output_diff}")
    print(f"  5. Rehydrated .f3d rebuilt:    {output_rehydrated}")
    print(f"  6. Rebuilt history capture:    {output_rehydrated_history}")
    print(f"  7. Rebuild-vs-source diff:     {output_rehydrated_diff}")
    print()
    print("Done.")
    print("Artifacts saved in:")
    print(f"  {run_dir}")
    print("Logs saved in:")
    print(f"  {log_run_dir}")
    print()
    print("Convenience links:")
    print(f"  STEP:        {current_dir / 'latest.step'}")
    print(f"  HISTORY:     {current_dir / 'latest.step.history.json'}")
    print(f"  DIFF:        {current_dir / 'latest.diff.json'}")
    print(f"  REHYDRATED:  {current_dir / 'latest.rehydrated.f3d'}")
    print(f"  REBUILT:     {current_dir / 'latest.rehydrated.history.json'}")
    print(f"  REBUILD DIFF:{current_dir / 'latest.rehydrated.history.diff.json'}")
    print(f"  LOGS:        {log_current_dir / 'latest-summary.json'}")
    return 0


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._ -]+", "_", value).strip(" _")
    return normalized or "model"


def _invoke_workflow(step_name: str, log_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TORI_FUSION_LOG_DIR"] = str(log_dir)
    result = subprocess.run(
        [sys.executable, str(WORKFLOW_SCRIPT), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    (log_dir / f"{step_name}.stdout.log").write_text(result.stdout)
    (log_dir / f"{step_name}.stderr.log").write_text(result.stderr)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


def _refresh_link(link_path: Path, target_path: Path) -> None:
    link_path.unlink(missing_ok=True)
    relative_target = os.path.relpath(target_path, link_path.parent)
    link_path.symlink_to(relative_target)


def _open_path(path: Path, log_path: Path) -> None:
    result = subprocess.run(
        ["open", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    payload = {
        "path": str(path),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    log_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_failure_logs(
    *,
    log_run_dir: Path,
    log_current_dir: Path,
    summary_path: Path,
    error_json_path: Path,
    error_log_path: Path,
    input_path: Path,
    run_id: str,
    run_dir: Path,
    exc: Exception,
) -> None:
    payload = {
        "input_f3d": str(input_path),
        "run_id": run_id,
        "artifact_dir": str(run_dir),
        "log_dir": str(log_run_dir),
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "traceback": traceback.format_exc(),
    }
    if isinstance(exc, subprocess.CalledProcessError):
        payload["command"] = exc.cmd
        payload["returncode"] = exc.returncode
        payload["stdout"] = exc.output
        payload["stderr"] = exc.stderr

    error_json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    error_log_path.write_text(payload["traceback"])
    summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    _refresh_link(log_current_dir / "latest-error.json", error_json_path)
    _refresh_link(log_current_dir / "latest-error.log", error_log_path)
    _refresh_link(log_current_dir / "latest-summary.json", summary_path)


if __name__ == "__main__":
    raise SystemExit(main())
