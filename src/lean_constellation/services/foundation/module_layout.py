"""Map logical node modules to collision-free native project modules."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path


_MODULE_SEGMENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

_DECL_KIND_DIRS = {
    "definition": "Defs",
    "def": "Defs",
    "defs": "Defs",
    "abbrev": "Defs",
    "type": "Types",
    "types": "Types",
    "structure": "Types",
    "class": "Types",
    "inductive": "Types",
    "instance": "Instances",
    "instances": "Instances",
    "lemma": "Lemmas",
    "lemmas": "Lemmas",
    "theorem": "Theorems",
    "theorems": "Theorems",
    "proposition": "Theorems",
    "corollary": "Theorems",
    "notation": "Defs",
    "axiom": "Defs",
}


class NativeModuleLayoutError(ValueError):
    """Raised when a repo marked native lacks a valid Lake module root."""


def native_project_name(repo_root: Path) -> str | None:
    """Return the native Lake project name, or ``None`` for non-native repos."""

    root = Path(repo_root).expanduser().resolve(strict=False)
    format_path = root / ".lean_constellation" / "repo_format.json"
    lakefile = root / "lakefile.toml"
    try:
        format_payload = json.loads(format_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if format_payload.get("repo_format") != "native":
        return None
    try:
        lake_payload = tomllib.loads(lakefile.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise NativeModuleLayoutError("Native repo lakefile.toml is missing or invalid.") from exc
    name = lake_payload.get("name")
    if not isinstance(name, str) or _MODULE_SEGMENT_RE.fullmatch(name) is None:
        raise NativeModuleLayoutError("Native repo Lake project name is missing or is not a valid module segment.")
    return name


def local_module_name(repo_root: Path, logical_module: str) -> str:
    """Qualify a logical local module with the native project root."""

    project_name = native_project_name(repo_root)
    if project_name is None or logical_module == project_name or logical_module.startswith(f"{project_name}."):
        return logical_module
    return f"{project_name}.{logical_module}"


def local_projection_path(repo_root: Path, logical_path: Path) -> Path:
    """Place a logical projection path below the native project module root."""

    root = Path(repo_root).expanduser().resolve(strict=False)
    logical = Path(logical_path).expanduser().resolve(strict=False)
    relative = logical.relative_to(root)
    project_name = native_project_name(root)
    if project_name is None or (relative.parts and relative.parts[0] == project_name):
        return logical
    return root / project_name / relative


def decl_kind_dir(kind: str) -> str:
    """Return the canonical Decl-owned directory for a declaration kind."""

    normalized = kind.strip().lower()
    kind_dir = _DECL_KIND_DIRS.get(normalized)
    if kind_dir is None:
        raise NativeModuleLayoutError(f"Unsupported declaration kind for native module layout: {kind}")
    return kind_dir


def validate_module_segment(value: str, *, label: str = "module segment") -> str:
    """Return one valid, flat Lean module segment or raise a layout error."""

    if _MODULE_SEGMENT_RE.fullmatch(value) is None:
        raise NativeModuleLayoutError(f"{label} must be one flat Lean module segment.")
    return value


def native_decl_module(repo_root: Path, *, node_path: str, kind: str, decl_name: str) -> str:
    """Derive the stable native module for one flat Constellation Decl key."""

    project_name = native_project_name(repo_root)
    if project_name is None:
        raise NativeModuleLayoutError("Native declaration creation requires an initialized native Lake project.")
    validate_module_segment(decl_name, label="Native Decl.name")
    node_segments = node_path.split(".")
    if not node_segments or any(_MODULE_SEGMENT_RE.fullmatch(segment) is None for segment in node_segments):
        raise NativeModuleLayoutError("Native node path must contain valid Lean module segments.")
    return ".".join([project_name, *node_segments, decl_kind_dir(kind), decl_name])


__all__ = [
    "NativeModuleLayoutError",
    "decl_kind_dir",
    "local_module_name",
    "local_projection_path",
    "native_decl_module",
    "native_project_name",
    "validate_module_segment",
]
