from __future__ import annotations

import unittest

from tori_fusion.history_package import make_reconstruction_patch


class HistoryPackageTests(unittest.TestCase):
    def test_make_reconstruction_patch_uses_history(self) -> None:
        sidecar = {
            "step_path": "/tmp/model.step",
            "fusion_history": {
                "parameters": [
                    {"name": "width", "expression": "10 mm"},
                    {"name": "depth", "expression": "15 mm"},
                ],
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
                        "direction": "positive",
                        "operation": "new_body",
                        "sketch_name": "Sketch1",
                        "profile_index": 0,
                    }
                ],
            },
        }
        patch = make_reconstruction_patch(sidecar, "/tmp/out.f3d")
        self.assertEqual(patch["input_step"], "/tmp/model.step")
        self.assertEqual(patch["parameters"]["width"], "10 mm")
        self.assertEqual(patch["extrudes"][0]["name"], "Extrude1")
        self.assertEqual(patch["extrudes"][0]["sketch_name"], "Sketch1")

    def test_make_reconstruction_patch_creates_synthetic_params_for_literal_expressions(self) -> None:
        sidecar = {
            "step_path": "/tmp/model.step",
            "fusion_history": {
                "parameters": [],
                "sketches": [
                    {
                        "name": "Sketch1",
                        "type": "origin_rectangle",
                        "width_expression": "10 mm",
                        "height_expression": "12 mm",
                    }
                ],
                "extrudes": [
                    {
                        "name": "Extrude1",
                        "distance_expression": "15 mm",
                        "direction": "positive",
                        "operation": "new_body",
                    }
                ],
            },
        }
        patch = make_reconstruction_patch(sidecar, "/tmp/out.f3d")
        self.assertEqual(patch["parameters"]["width"], "10 mm")
        self.assertEqual(patch["parameters"]["height"], "12 mm")
        self.assertEqual(patch["parameters"]["depth"], "15 mm")
        self.assertEqual(patch["sketches"][0]["width_param"], "width")
        self.assertEqual(patch["extrudes"][0]["distance_param"], "depth")

    def test_make_reconstruction_patch_preserves_generic_sketch_geometry_and_multiple_extrudes(self) -> None:
        sidecar = {
            "step_path": "/tmp/model.step",
            "fusion_history": {
                "parameters": [],
                "unsupported_reasons": [],
                "sketches": [
                    {
                        "name": "Sketch1",
                        "type": "planar_curve_set",
                        "plane_name": "xY Construction Plane",
                        "lines": [{"start": {"x": 0, "y": 0, "z": 0}, "end": {"x": 10, "y": 0, "z": 0}}],
                        "circles": [{"center": {"x": 5, "y": 5, "z": 0}, "radius": 1.5}],
                        "arcs": [],
                        "fitted_splines": [{"points": [{"x": 0, "y": 0, "z": 0}, {"x": 5, "y": 3, "z": 0}]}],
                    }
                ],
                "extrudes": [
                    {
                        "name": "Extrude1",
                        "distance_expression": "10 mm",
                        "direction": "positive",
                        "operation": "new_body",
                        "sketch_name": "Sketch1",
                        "profile_index": 0,
                    },
                    {
                        "name": "Extrude2",
                        "distance_expression": "20 mm",
                        "start_expression": "10 mm",
                        "direction": "positive",
                        "operation": "join",
                        "sketch_name": "Sketch1",
                        "profile_index": 1,
                    },
                ],
            },
        }
        patch = make_reconstruction_patch(sidecar, "/tmp/out.f3d")
        self.assertEqual(patch["sketches"][0]["type"], "planar_curve_set")
        self.assertEqual(patch["sketches"][0]["plane_name"], "xY Construction Plane")
        self.assertEqual(patch["sketches"][0]["circles"][0]["radius"], 1.5)
        self.assertEqual(patch["extrudes"][1]["profile_index"], 1)
        self.assertEqual(patch["extrudes"][1]["operation"], "join")
        self.assertEqual(patch["extrudes"][1]["start_param"], "extrude2_start")
        self.assertEqual(patch["parameters"]["extrude2_start"], "10 mm")
        self.assertEqual(patch["unsupported_reasons"], [])


if __name__ == "__main__":
    unittest.main()
