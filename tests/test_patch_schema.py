from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tori_fusion.patch_schema import FusionPatchError, load_patch_file


class PatchSchemaTests(unittest.TestCase):
    def test_loads_valid_patch(self) -> None:
        payload = {
            "input": "/tmp/input.f3d",
            "output": "/tmp/output.f3d",
            "parameters": {"width": "10 mm", "depth": "15 mm"},
            "sketches": [
                {
                    "name": "Sketch1",
                    "type": "origin_rectangle",
                    "width_param": "width",
                    "height_param": "width",
                }
            ],
            "extrudes": [
                {
                    "name": "Extrude1",
                    "distance_param": "depth",
                    "operation": "new_body",
                    "direction": "positive",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            patch_path = Path(tmpdir) / "patch.json"
            patch_path.write_text(json.dumps(payload))
            patch = load_patch_file(patch_path)

        self.assertEqual(patch.parameters["width"], "10 mm")
        self.assertEqual(patch.extrudes[0].direction, "positive")

    def test_rejects_same_input_and_output(self) -> None:
        payload = {"input": "/tmp/input.f3d", "output": "/tmp/input.f3d"}

        with tempfile.TemporaryDirectory() as tmpdir:
            patch_path = Path(tmpdir) / "patch.json"
            patch_path.write_text(json.dumps(payload))
            with self.assertRaises(FusionPatchError):
                load_patch_file(patch_path)

    def test_rejects_unsupported_sketch_type(self) -> None:
        payload = {
            "input": "/tmp/input.f3d",
            "output": "/tmp/output.f3d",
            "sketches": [
                {
                    "name": "Sketch1",
                    "type": "circle",
                    "width_param": "width",
                    "height_param": "height",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            patch_path = Path(tmpdir) / "patch.json"
            patch_path.write_text(json.dumps(payload))
            with self.assertRaises(FusionPatchError):
                load_patch_file(patch_path)


if __name__ == "__main__":
    unittest.main()
