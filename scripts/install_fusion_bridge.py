#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADDIN_SOURCE = ROOT / "fusion_addin" / "ToriBridge"
ADDIN_DEST = Path.home() / "Library/Application Support/Autodesk/Autodesk Fusion/API/AddIns/ToriBridge"


def main() -> int:
    print(f"Add-in source: {ADDIN_SOURCE}")
    print(f"Suggested install target: {ADDIN_DEST}")
    print("Install with:")
    print(f"mkdir -p '{ADDIN_DEST.parent}'")
    print(f"ln -sfn '{ADDIN_SOURCE}' '{ADDIN_DEST}'")
    print("Then open Fusion -> Utilities -> Scripts and Add-Ins -> Add-Ins -> ToriBridge -> Run.")
    print("Enable 'Run on Startup' once so the shell workflow can work with one command after that.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
