#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tori_fusion.inspector import format_inspection_report, inspect_archive


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a Fusion 360 .f3d archive.")
    parser.add_argument("path", help="Absolute or relative path to a .f3d file.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON instead of a human-readable summary.",
    )
    args = parser.parse_args()

    report = inspect_archive(args.path)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_inspection_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
