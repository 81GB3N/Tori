from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


DEFAULT_BRIDGE_DIR = Path(__file__).resolve().parents[1] / ".tori-fusion-bridge"
BRIDGE_DIR_ENV = "TORI_FUSION_BRIDGE_DIR"
REQUESTS_DIRNAME = "requests"
RESPONSES_DIRNAME = "responses"
STATE_DIRNAME = "state"
HEARTBEAT_FILENAME = "heartbeat.json"


class BridgeProtocolError(RuntimeError):
    """Raised when bridge requests or responses are invalid."""


@dataclass(frozen=True)
class BridgePaths:
    root: Path
    requests: Path
    responses: Path
    state: Path
    heartbeat: Path


def ensure_bridge_dirs(root: Path | None = None) -> BridgePaths:
    configured_root = root
    if configured_root is None:
        configured = os.environ.get(BRIDGE_DIR_ENV)
        configured_root = Path(configured).expanduser() if configured else DEFAULT_BRIDGE_DIR
    bridge_root = configured_root.expanduser().resolve()
    requests = bridge_root / REQUESTS_DIRNAME
    responses = bridge_root / RESPONSES_DIRNAME
    state = bridge_root / STATE_DIRNAME
    for directory in (bridge_root, requests, responses, state):
        directory.mkdir(parents=True, exist_ok=True)
    return BridgePaths(
        root=bridge_root,
        requests=requests,
        responses=responses,
        state=state,
        heartbeat=state / HEARTBEAT_FILENAME,
    )


def create_request(
    operation: str,
    payload: dict[str, Any],
    bridge_root: Path | None = None,
) -> tuple[str, Path]:
    bridge = ensure_bridge_dirs(bridge_root)
    request_id = uuid4().hex
    body = {
        "request_id": request_id,
        "operation": operation,
        "created_at": int(time.time()),
        "payload": payload,
    }
    request_path = bridge.requests / f"{request_id}.json"
    request_path.write_text(json.dumps(body, indent=2, sort_keys=True))
    return request_id, request_path


def wait_for_response(
    request_id: str,
    timeout_seconds: int = 300,
    bridge_root: Path | None = None,
) -> dict[str, Any]:
    bridge = ensure_bridge_dirs(bridge_root)
    response_path = bridge.responses / f"{request_id}.json"
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if response_path.exists():
            return json.loads(response_path.read_text())
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for Fusion bridge response for request {request_id}.")


def read_request(path: str | Path) -> dict[str, Any]:
    request_path = Path(path).expanduser().resolve()
    payload = json.loads(request_path.read_text())
    if not isinstance(payload, dict):
        raise BridgeProtocolError("Bridge request root must be a JSON object.")
    return payload


def write_response(
    request_id: str,
    payload: dict[str, Any],
    bridge_root: Path | None = None,
) -> Path:
    bridge = ensure_bridge_dirs(bridge_root)
    response_path = bridge.responses / f"{request_id}.json"
    response_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return response_path


def update_heartbeat(
    bridge_root: Path | None = None,
    *,
    status: str,
    details: dict[str, Any] | None = None,
) -> Path:
    bridge = ensure_bridge_dirs(bridge_root)
    heartbeat = {
        "updated_at": int(time.time()),
        "status": status,
        "details": details or {},
    }
    bridge.heartbeat.write_text(json.dumps(heartbeat, indent=2, sort_keys=True))
    return bridge.heartbeat


def bridge_alive(max_age_seconds: int = 30, bridge_root: Path | None = None) -> bool:
    bridge = ensure_bridge_dirs(bridge_root)
    if not bridge.heartbeat.exists():
        return False
    try:
        payload = json.loads(bridge.heartbeat.read_text())
    except json.JSONDecodeError:
        return False
    updated_at = payload.get("updated_at")
    if not isinstance(updated_at, (int, float)):
        return False
    return time.time() - float(updated_at) <= max_age_seconds


def read_heartbeat(bridge_root: Path | None = None) -> dict[str, Any] | None:
    bridge = ensure_bridge_dirs(bridge_root)
    if not bridge.heartbeat.exists():
        return None
    try:
        payload = json.loads(bridge.heartbeat.read_text())
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload
