from __future__ import annotations

import binascii
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from tori_fusion.archive import COMPRESSION_STORED, COMPRESSION_ZSTD, F3dArchive


def _build_zip(entries: list[tuple[str, int, bytes, bytes]]) -> bytes:
    local_parts: list[bytes] = []
    central_parts: list[bytes] = []
    cursor = 0

    for name, method, uncompressed, compressed in entries:
        encoded_name = name.encode("utf-8")
        crc32 = binascii.crc32(uncompressed) & 0xFFFFFFFF
        local_header = struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50,
            20,
            0,
            method,
            0,
            0,
            crc32,
            len(compressed),
            len(uncompressed),
            len(encoded_name),
            0,
        )
        local_parts.append(local_header + encoded_name + compressed)

        central_header = struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50,
            20,
            20,
            0,
            method,
            0,
            0,
            crc32,
            len(compressed),
            len(uncompressed),
            len(encoded_name),
            0,
            0,
            0,
            0,
            0,
            cursor,
        )
        central_parts.append(central_header + encoded_name)
        cursor += len(local_header) + len(encoded_name) + len(compressed)

    central_directory = b"".join(central_parts)
    end_of_central_directory = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        len(entries),
        len(entries),
        len(central_directory),
        cursor,
        0,
    )
    return b"".join(local_parts) + central_directory + end_of_central_directory


class F3dArchiveTests(unittest.TestCase):
    def test_reads_stored_entries(self) -> None:
        archive_blob = _build_zip(
            [
                ("Manifest.dat", COMPRESSION_STORED, b"manifest", b"manifest"),
                ("Properties.dat", COMPRESSION_STORED, b"{\"docstruct\": {}}", b"{\"docstruct\": {}}"),
            ]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "simple.f3d"
            path.write_bytes(archive_blob)
            archive = F3dArchive(path)
            self.assertEqual(len(archive.entries()), 2)
            self.assertEqual(archive.read_entry("Manifest.dat"), b"manifest")

    @unittest.skipUnless(shutil.which("zstd"), "zstd CLI is required for ZIP method 93 tests")
    def test_reads_zip_zstd_entries(self) -> None:
        source = b"hello from zstd"
        compressed = subprocess.run(
            [shutil.which("zstd"), "-q", "-c"],  # type: ignore[list-item]
            input=source,
            stdout=subprocess.PIPE,
            check=True,
        ).stdout
        archive_blob = _build_zip([("Manifest.dat", COMPRESSION_ZSTD, source, compressed)])

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "zstd.f3d"
            path.write_bytes(archive_blob)
            archive = F3dArchive(path)
            self.assertEqual(archive.read_entry("Manifest.dat"), source)


if __name__ == "__main__":
    unittest.main()
