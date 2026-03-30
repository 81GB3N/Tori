from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tori_fusion.inspector import inspect_archive
from test_archive import _build_zip


class InspectorTests(unittest.TestCase):
    def test_decodes_prefixed_properties_and_feature_hints(self) -> None:
        properties = json.dumps(
            {
                "docstruct": {
                    "version": "1.0.0",
                    "type": "part-design",
                    "subtype": "part-standard",
                    "attributes": {},
                }
            }
        ).encode("utf-8")
        properties_blob = len(properties).to_bytes(4, "little") + properties
        design_blob = b"DcSketchMetaType\x00DcExtrudeFeatureMetaType\x00SketchesRoot\x00"
        archive_blob = _build_zip(
            [
                ("Properties.dat", 0, properties_blob, properties_blob),
                ("FusionAssetName[Active]/FusionDesignSegmentType1/", 0, b"", b""),
                ("FusionAssetName[Active]/FusionDesignSegmentType1/BulkStream.dat", 0, design_blob, design_blob),
                ("FusionAssetName[Active]/FusionDesignSegmentType1/MetaStream.dat", 0, b"", b""),
            ]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "inspect.f3d"
            path.write_bytes(archive_blob)
            report = inspect_archive(path)

        self.assertEqual(report["properties"]["type"], "part-design")
        self.assertTrue(report["feature_hints"]["sketch"])
        self.assertTrue(report["feature_hints"]["extrude"])


if __name__ == "__main__":
    unittest.main()
