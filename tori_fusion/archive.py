from __future__ import annotations

import os
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class F3dArchiveError(RuntimeError):
    """Raised when a .f3d archive cannot be parsed."""


LOCAL_FILE_HEADER_SIGNATURE = 0x04034B50
COMPRESSION_STORED = 0
COMPRESSION_ZSTD = 93
COMMON_ZSTD_PATHS = (
    "/opt/homebrew/bin/zstd",
    "/usr/local/bin/zstd",
)


@dataclass(frozen=True)
class ArchiveEntry:
    name: str
    compression_method: int
    compressed_size: int
    uncompressed_size: int
    crc32: int
    payload_offset: int


class F3dArchive:
    """Minimal .f3d reader that supports stored and ZIP+Zstandard entries."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._blob = self.path.read_bytes()
        self._entries = self._parse_entries()

    def entries(self) -> list[ArchiveEntry]:
        return list(self._entries)

    def iter_entries(self) -> Iterator[ArchiveEntry]:
        yield from self._entries

    def get_entry(self, name: str) -> ArchiveEntry:
        for entry in self._entries:
            if entry.name == name:
                return entry
        raise KeyError(name)

    def read_entry(self, name: str) -> bytes:
        entry = self.get_entry(name)
        payload = self._blob[entry.payload_offset : entry.payload_offset + entry.compressed_size]

        if entry.compression_method == COMPRESSION_STORED:
            return payload
        if entry.compression_method == COMPRESSION_ZSTD:
            return self._decompress_zstd(payload, name)
        raise F3dArchiveError(
            f"Unsupported compression method {entry.compression_method} for {name!r}."
        )

    def _parse_entries(self) -> list[ArchiveEntry]:
        entries: list[ArchiveEntry] = []
        cursor = 0
        blob_size = len(self._blob)

        while cursor + 4 <= blob_size:
            signature = struct.unpack_from("<I", self._blob, cursor)[0]
            if signature != LOCAL_FILE_HEADER_SIGNATURE:
                break

            if cursor + 30 > blob_size:
                raise F3dArchiveError("Truncated local file header.")

            (
                _signature,
                _version_needed,
                _flags,
                compression_method,
                _modified_time,
                _modified_date,
                crc32,
                compressed_size,
                uncompressed_size,
                name_length,
                extra_length,
            ) = struct.unpack_from("<IHHHHHIIIHH", self._blob, cursor)

            header_end = cursor + 30
            name_start = header_end
            name_end = name_start + name_length
            extra_end = name_end + extra_length
            payload_end = extra_end + compressed_size

            if payload_end > blob_size:
                raise F3dArchiveError("Truncated payload in .f3d archive.")

            name = self._blob[name_start:name_end].decode("utf-8", errors="replace")
            entries.append(
                ArchiveEntry(
                    name=name,
                    compression_method=compression_method,
                    compressed_size=compressed_size,
                    uncompressed_size=uncompressed_size,
                    crc32=crc32,
                    payload_offset=extra_end,
                )
            )
            cursor = payload_end

        if not entries:
            raise F3dArchiveError(f"{self.path} does not look like a valid .f3d archive.")

        return entries

    @staticmethod
    def _decompress_zstd(payload: bytes, entry_name: str) -> bytes:
        zstd_binary = _find_zstd_binary()
        if not zstd_binary:
            raise F3dArchiveError(
                "ZIP method 93 requires the `zstd` CLI to be installed and available on PATH, "
                "TORI_ZSTD_BIN, /opt/homebrew/bin/zstd, or /usr/local/bin/zstd."
            )

        try:
            result = subprocess.run(
                [zstd_binary, "-d", "-q", "-c"],
                input=payload,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace").strip()
            raise F3dArchiveError(
                f"Failed to decompress Zstandard payload for {entry_name!r}: {stderr or exc}"
            ) from exc

        return result.stdout


def _find_zstd_binary() -> str | None:
    explicit = os.environ.get("TORI_ZSTD_BIN")
    candidates = []
    if explicit:
        candidates.append(explicit)

    which_candidate = shutil.which("zstd")
    if which_candidate:
        candidates.append(which_candidate)

    candidates.extend(COMMON_ZSTD_PATHS)

    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None
