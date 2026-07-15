"""Path layout helpers for repository-local Lean Constellation state."""

from __future__ import annotations

import base64
import re
from pathlib import Path

from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel

_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_LEAN_MODULE_SEGMENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class FoundationContext(StrictModel):
    repo_root: Path
    workspace_root: Path | None = None
    current_repo: str | None = None
    current_node: str | None = None
    caller: str | None = None
    reason: str | None = None

    @field_validator("repo_root", "workspace_root")
    @classmethod
    def _expand_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return Path(value).expanduser()


class DeclFileKey(StrictModel):
    node_path: str
    decl_kind: str
    decl_name: str

    @field_validator("decl_name")
    @classmethod
    def _flat_decl_name(cls, value: str) -> str:
        normalized = value.strip()
        if _LEAN_MODULE_SEGMENT_RE.fullmatch(normalized) is None:
            raise ValueError("decl_name must be one flat Lean module segment")
        return normalized


class LayoutPathView(StrictModel):
    path: str
    role: str
    exists: bool
    within_repo: bool = True


class RepoLayoutView(StrictModel):
    repo_root: str
    constellation_root: str
    agent_runtime_root: str
    source_root: str
    resources_root: str
    snapshots_root: str
    warnings: list[str] = Field(default_factory=list)


class PathBoundaryView(StrictModel):
    root: str
    allowed_operations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LayoutComponent:
    """Compute canonical paths without reading or writing files."""

    def constellation_root(self, ctx: FoundationContext) -> Path:
        return self._repo_root(ctx) / ".lean_constellation"

    def agent_runtime_root(self, ctx: FoundationContext) -> Path:
        return self._repo_root(ctx) / ".agent_runtime"

    def repo_metadata_path(self, ctx: FoundationContext) -> Path:
        return self.constellation_root(ctx) / "repo.json"

    def preparation_input_path(self, ctx: FoundationContext) -> Path:
        return self.constellation_root(ctx) / "preparation_input.json"

    def source_corpus_root(self, ctx: FoundationContext, relpath: str = ".lean_constellation/source") -> Path:
        rel = self.ensure_relative_path(relpath)
        path = self._repo_root(ctx) / rel
        self.assert_within(self._repo_root(ctx), path)
        return path

    def source_corpus_entry_path(self, ctx: FoundationContext, relpath: str, entry_path: str) -> Path:
        root = self.source_corpus_root(ctx, relpath)
        entry = root / self.ensure_relative_path(entry_path)
        self.assert_within(root, entry)
        return entry

    def resources_root(self, ctx: FoundationContext) -> Path:
        return self.constellation_root(ctx) / "resources"

    def resource_dir(self, ctx: FoundationContext, resource_key: str) -> Path:
        return self.resources_root(ctx) / "items" / self.ensure_safe_key(resource_key)

    def resource_metadata_path(self, ctx: FoundationContext, resource_key: str) -> Path:
        return self.resource_dir(ctx, resource_key) / "resource.json"

    def resource_raw_dir(self, ctx: FoundationContext, resource_key: str) -> Path:
        return self.resource_dir(ctx, resource_key) / "raw"

    def resource_normalized_dir(self, ctx: FoundationContext, resource_key: str) -> Path:
        return self.resource_dir(ctx, resource_key) / "normalized"

    def resource_temp_dir(self, ctx: FoundationContext, request_key: str) -> Path:
        return self.resources_root(ctx) / "tmp" / self.ensure_safe_key(request_key)

    def requirements_root(self, ctx: FoundationContext) -> Path:
        return self.constellation_root(ctx) / "repo_dependency_requirements"

    def requirement_path(self, ctx: FoundationContext, requirement_name: str) -> Path:
        return self.requirements_root(ctx) / f"{self.ensure_safe_key(requirement_name)}.json"

    def releases_root(self, ctx: FoundationContext) -> Path:
        return self.constellation_root(ctx) / "releases"

    def release_path(self, ctx: FoundationContext, release_id: str) -> Path:
        return self.releases_root(ctx) / f"{self.ensure_safe_key(release_id)}.json"

    def repo_locks_root(self, ctx: FoundationContext) -> Path:
        return self.constellation_root(ctx) / ".locks"

    def repo_lifecycle_lock_path(self, ctx: FoundationContext) -> Path:
        return self.repo_locks_root(ctx) / "repo_lifecycle.lock"

    def nodes_root(self, ctx: FoundationContext) -> Path:
        return self.constellation_root(ctx) / "nodes"

    def node_index_path(self, ctx: FoundationContext) -> Path:
        return self.constellation_root(ctx) / "index" / "nodes.json"

    def node_dir_by_id(self, ctx: FoundationContext, node_id: str) -> Path:
        return self.nodes_root(ctx) / self.ensure_safe_key(node_id)

    def node_metadata_path_by_id(self, ctx: FoundationContext, node_id: str) -> Path:
        return self.node_dir_by_id(ctx, node_id) / "node.json"

    def node_contracts_dir_by_id(self, ctx: FoundationContext, node_id: str) -> Path:
        return self.node_dir_by_id(ctx, node_id) / "contracts"

    def node_contract_path_by_id(self, ctx: FoundationContext, node_id: str, version: int) -> Path:
        if version < 1:
            raise ValueError("version must be >= 1")
        return self.node_contracts_dir_by_id(ctx, node_id) / f"{version}.json"

    def node_decl_graph_dir_by_id(self, ctx: FoundationContext, node_id: str) -> Path:
        return self.node_dir_by_id(ctx, node_id) / "decl_graph"

    def node_dir(self, ctx: FoundationContext, node_path: str) -> Path:
        return self.node_metadata_dir(ctx, node_path)

    def node_metadata_dir(self, ctx: FoundationContext, node_path: str) -> Path:
        return self.nodes_root(ctx) / self.encode_dot_path(node_path)

    def node_contracts_dir(self, ctx: FoundationContext, node_path: str) -> Path:
        return self.node_metadata_dir(ctx, node_path) / "contracts"

    def node_contract_path(self, ctx: FoundationContext, node_path: str, version: int) -> Path:
        if version < 1:
            raise ValueError("version must be >= 1")
        return self.node_contracts_dir(ctx, node_path) / f"{version}.json"

    def node_projection_dir(self, ctx: FoundationContext, node_path: str) -> Path:
        parts = self._dot_path_parts(node_path)
        return self._repo_root(ctx).joinpath(*parts)

    def prelude_path(self, ctx: FoundationContext, node_path: str) -> Path:
        return self.node_projection_dir(ctx, node_path) / "Prelude.lean"

    def interfaces_path(self, ctx: FoundationContext, node_path: str) -> Path:
        return self.node_projection_dir(ctx, node_path) / "Interfaces.lean"

    def adapter_interfaces_path(self, ctx: FoundationContext) -> Path:
        return self._repo_root(ctx) / "Main" / "Interfaces.lean"

    def decl_file_path(self, ctx: FoundationContext, key: DeclFileKey) -> Path:
        kind_dir = self.ensure_safe_key(key.decl_kind)
        return self.node_projection_dir(ctx, key.node_path).joinpath(kind_dir, key.decl_name).with_suffix(".lean")

    def indexes_root(self, ctx: FoundationContext) -> Path:
        return self.constellation_root(ctx) / "indexes"

    def index_cache_path(self, ctx: FoundationContext, index_name: str) -> Path:
        return self.indexes_root(ctx) / f"{self.ensure_safe_key(index_name)}.json"

    def snapshot_root(self, ctx: FoundationContext) -> Path:
        return self.constellation_root(ctx) / "snapshots"

    def repo_layout_view(self, ctx: FoundationContext) -> RepoLayoutView:
        return RepoLayoutView(
            repo_root=str(self._repo_root(ctx)),
            constellation_root=str(self.constellation_root(ctx)),
            agent_runtime_root=str(self.agent_runtime_root(ctx)),
            source_root=str(self.source_corpus_root(ctx)),
            resources_root=str(self.resources_root(ctx)),
            snapshots_root=str(self.snapshot_root(ctx)),
        )

    def ensure_relative_path(self, path: str) -> str:
        if not path or not path.strip():
            raise ValueError("relative path must be non-empty")
        candidate = Path(path)
        if candidate.is_absolute():
            raise ValueError(f"path must be relative: {path}")
        if any(part in {"", ".", ".."} for part in candidate.parts):
            raise ValueError(f"path contains an unsafe segment: {path}")
        return candidate.as_posix()

    def ensure_safe_key(self, key: str) -> str:
        if not key or not key.strip():
            raise ValueError("key must be non-empty")
        key = key.strip()
        if not _SAFE_KEY_RE.fullmatch(key):
            raise ValueError(f"unsafe key: {key}")
        return key

    def encode_dot_path(self, dot_path: str) -> str:
        if not dot_path or not dot_path.strip():
            raise ValueError("dot path must be non-empty")
        parts = self._dot_path_parts(dot_path)
        normalized = ".".join(parts)
        token = base64.urlsafe_b64encode(normalized.encode("utf-8")).decode("ascii").rstrip("=")
        return f"n_{token}"

    def decode_dot_path(self, encoded: str) -> str:
        if not encoded.startswith("n_"):
            raise ValueError("encoded dot path must start with 'n_'")
        token = encoded[2:]
        padding = "=" * (-len(token) % 4)
        try:
            decoded = base64.urlsafe_b64decode(f"{token}{padding}").decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - normalized as ValueError for callers.
            raise ValueError(f"invalid encoded dot path: {encoded}") from exc
        self._dot_path_parts(decoded)
        return decoded

    def assert_within(self, base: Path, path: Path) -> None:
        base_resolved = Path(base).expanduser().resolve(strict=False)
        path_resolved = Path(path).expanduser().resolve(strict=False)
        try:
            path_resolved.relative_to(base_resolved)
        except ValueError as exc:
            raise ValueError(f"path escapes boundary: {path_resolved} is not within {base_resolved}") from exc

    def _repo_root(self, ctx: FoundationContext) -> Path:
        return Path(ctx.repo_root).expanduser().resolve(strict=False)

    def _dot_path_parts(self, dot_path: str) -> list[str]:
        if not dot_path or not dot_path.strip():
            raise ValueError("dot path must be non-empty")
        parts = [part.strip() for part in dot_path.split(".")]
        if any(not part for part in parts):
            raise ValueError(f"invalid dot path: {dot_path}")
        return [self.ensure_safe_key(part) for part in parts]
