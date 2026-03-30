from __future__ import annotations

import unittest

from tori_fusion.package_diff import diff_history_packages


class PackageDiffTests(unittest.TestCase):
    def test_diff_history_packages_detects_changed_parameter(self) -> None:
        left = {
            "fusion_history": {
                "parameters": [{"name": "depth", "expression": "10 mm"}],
                "lofts": [],
                "fillets": [],
                "moves": [],
                "scales": [],
                "unsupported_reasons": [],
                "sketches": [],
                "extrudes": [],
            },
            "feature_hints": {"extrude": True},
        }
        right = {
            "fusion_history": {
                "parameters": [{"name": "depth", "expression": "20 mm"}],
                "lofts": [{"name": "Loft1", "kind": "loft"}],
                "fillets": [],
                "moves": [],
                "scales": [],
                "unsupported_reasons": ["loft_features_present"],
                "sketches": [],
                "extrudes": [],
            },
            "feature_hints": {"extrude": True},
        }
        diff = diff_history_packages(left, right)
        self.assertEqual(diff["parameters"]["changed"][0]["name"], "depth")
        self.assertEqual(diff["lofts"]["only_right"], ["Loft1"])
        self.assertEqual(diff["unsupported_reasons"]["right"], ["loft_features_present"])


if __name__ == "__main__":
    unittest.main()
