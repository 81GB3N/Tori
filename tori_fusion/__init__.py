"""Utilities for inspecting and patching Fusion 360 .f3d files."""

from .archive import ArchiveEntry, F3dArchive, F3dArchiveError
from .inspector import inspect_archive
from .patch_schema import (
    ExtrudePatch,
    FusionPatch,
    FusionPatchError,
    SketchPatch,
    load_patch_file,
)

__all__ = [
    "ArchiveEntry",
    "ExtrudePatch",
    "F3dArchive",
    "F3dArchiveError",
    "FusionPatch",
    "FusionPatchError",
    "SketchPatch",
    "inspect_archive",
    "load_patch_file",
]
