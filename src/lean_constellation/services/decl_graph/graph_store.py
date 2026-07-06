"""Decl graph store layout and basic index maintenance."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.repo import RepoFormat
from lean_constellation.services.decl_graph.models import (
    DeclChangeKind,
    DeclGraphIndex,
    DeclGraphRound,
    DeclGraphStoreView,
    DeclRevision,
    DeclRevisionRef,
    DeclRoundResultKind,
    DeclRoundStatus,
)
from lean_constellation.services.foundation import ServiceResult, WriteMode
from lean_constellation.services.node import NodeKind

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class DeclGraphStorageMigrationView(StrictModel):
    migrated_revisions: list[str] = Field(default_factory=list)
    migrated_changes: list[str] = Field(default_factory=list)
    migrated_rounds: list[str] = Field(default_factory=list)
    archived_paths: list[str] = Field(default_factory=list)
    summary: str


class GraphStoreComponent:
    """Own the Content-node-local decl graph directory and index file."""

    INDEX_FILENAME = "index.json"

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def ensure_graph(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphStoreView]:
        content = self._require_content_node(repo_root, node_path)
        if not content.ok:
            return self.runtime.foundation.fail(content.issues)

        paths = self._paths(repo_root, node_path)
        if paths is None:
            return self._invalid_node_path(node_path)

        for directory in (
            paths.graph_root,
            paths.strategies_dir,
            paths.rounds_dir,
            paths.decls_dir,
        ):
            ensured = self.runtime.foundation.store.ensure_dir(directory)
            if not ensured.ok:
                return self.runtime.foundation.fail(ensured.issues)

        if not paths.index_path.exists():
            index = self._empty_index(repo_root, node_path)
            if not index.ok or index.value is None:
                return self.runtime.foundation.fail(index.issues)
            written = self.runtime.foundation.store.write_json_atomic(
                paths.index_path,
                index.value,
                mode=WriteMode.CREATE_ONLY,
            )
            if not written.ok:
                return self.runtime.foundation.fail(written.issues)

        return self.get_store_view(repo_root, node_path=node_path)

    def get_index(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphIndex]:
        content = self._require_content_node(repo_root, node_path)
        if not content.ok:
            return self.runtime.foundation.fail(content.issues)
        paths = self._paths(repo_root, node_path)
        if paths is None:
            return self._invalid_node_path(node_path)
        return self.runtime.foundation.store.read_json(paths.index_path, DeclGraphIndex)

    def get_store_view(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphStoreView]:
        index = self.get_index(repo_root, node_path=node_path)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        paths = self._paths(repo_root, node_path)
        if paths is None:
            return self._invalid_node_path(node_path)
        return self.runtime.foundation.ok(
            DeclGraphStoreView(
                repo_root=str(Path(repo_root).expanduser().resolve(strict=False)),
                node_path=node_path,
                graph_root=str(paths.graph_root),
                index_path=str(paths.index_path),
                strategy_count=len(index.value.strategy_ids),
                round_count=len(index.value.round_ids),
                decl_count=len(index.value.decl_names),
                summary=(
                    f"Decl graph for {node_path}: "
                    f"{len(index.value.decl_names)} decls, "
                    f"{len(index.value.round_ids)} rounds, "
                    f"{len(index.value.strategy_ids)} strategies."
                ),
            )
        )

    def rebuild_index(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphIndex]:
        content = self._require_content_node(repo_root, node_path)
        if not content.ok:
            return self.runtime.foundation.fail(content.issues)
        paths = self._paths(repo_root, node_path)
        if paths is None:
            return self._invalid_node_path(node_path)
        for directory in (
            paths.graph_root,
            paths.strategies_dir,
            paths.rounds_dir,
            paths.decls_dir,
        ):
            ensured = self.runtime.foundation.store.ensure_dir(directory)
            if not ensured.ok:
                return self.runtime.foundation.fail(ensured.issues)

        strategy_ids = sorted(path.stem for path in paths.strategies_dir.glob("*.json") if path.is_file())
        round_ids = sorted(path.stem for path in paths.rounds_dir.glob("*.json") if path.is_file())
        decl_names = (
            sorted(path.name for path in paths.decls_dir.iterdir() if (path / "decl.json").is_file())
            if paths.decls_dir.exists()
            else []
        )
        index = DeclGraphIndex(
            node_id=paths.node_id,
            node_path=node_path,
            strategy_ids=strategy_ids,
            round_ids=round_ids,
            decl_names=decl_names,
            updated_at=utc_now_iso(),
            summary=(
                f"DeclGraph index rebuilt for {node_path}: "
                f"{len(decl_names)} decls, {len(round_ids)} rounds, {len(strategy_ids)} strategies."
            ),
        )
        written = self.runtime.foundation.store.write_json_atomic(paths.index_path, index, mode=WriteMode.OVERWRITE)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(index)

    def migrate_legacy_decl_graph_storage(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphStorageMigrationView]:
        """Rewrite explicit legacy DeclGraph files into the canonical nested storage shape."""

        paths = self._paths(repo_root, node_path)
        if paths is None:
            return self._invalid_node_path(node_path)
        ensured = self.ensure_graph(repo_root, node_path=node_path)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)

        migrated_revisions = self._migrate_flat_revisions(paths)
        if not migrated_revisions.ok or migrated_revisions.value is None:
            return self.runtime.foundation.fail(migrated_revisions.issues)
        change_refs = self._migrate_legacy_changes(paths)
        if not change_refs.ok or change_refs.value is None:
            return self.runtime.foundation.fail(change_refs.issues)
        migrated_rounds = self._migrate_legacy_rounds(paths, change_refs=change_refs.value)
        if not migrated_rounds.ok or migrated_rounds.value is None:
            return self.runtime.foundation.fail(migrated_rounds.issues)

        archived_paths: list[str] = []
        for source_name, archive_name in (("changes", "legacy_changes_archive"), ("reviews", "legacy_reviews_archive")):
            source = paths.graph_root / source_name
            if not source.exists():
                continue
            archive = paths.graph_root / archive_name
            if archive.exists():
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "decl_graph_migration_archive_exists",
                        f"Cannot archive legacy {source_name} directory because target archive already exists.",
                        object_ref=str(archive),
                    )
                )
            shutil.move(str(source), str(archive))
            archived_paths.append(str(archive))

        rebuilt = self.rebuild_index(repo_root, node_path=node_path)
        if not rebuilt.ok:
            return self.runtime.foundation.fail(rebuilt.issues)
        migrated_change_ids = sorted(change_refs.value.keys())
        return self.runtime.foundation.ok(
            DeclGraphStorageMigrationView(
                migrated_revisions=migrated_revisions.value,
                migrated_changes=migrated_change_ids,
                migrated_rounds=migrated_rounds.value,
                archived_paths=archived_paths,
                summary=(
                    f"Migrated {len(migrated_revisions.value)} flat revisions, "
                    f"{len(migrated_change_ids)} legacy changes, "
                    f"{len(migrated_rounds.value)} legacy rounds for {node_path}."
                ),
            )
        )

    def graph_root(self, repo_root: Path, *, node_path: str) -> Path:
        paths = self._paths(repo_root, node_path)
        if paths is None:
            raise ValueError(f"Invalid node path: {node_path}")
        return paths.graph_root

    def index_path(self, repo_root: Path, *, node_path: str) -> Path:
        paths = self._paths(repo_root, node_path)
        if paths is None:
            raise ValueError(f"Invalid node path: {node_path}")
        return paths.index_path

    def strategy_path(self, repo_root: Path, *, node_path: str, strategy_id: str) -> Path:
        paths = self._paths(repo_root, node_path)
        if paths is None:
            raise ValueError(f"Invalid node path: {node_path}")
        return paths.strategies_dir / f"{self.runtime.foundation.layout.ensure_safe_key(strategy_id)}.json"

    def round_path(self, repo_root: Path, *, node_path: str, round_id: str) -> Path:
        paths = self._paths(repo_root, node_path)
        if paths is None:
            raise ValueError(f"Invalid node path: {node_path}")
        return paths.rounds_dir / f"{self.runtime.foundation.layout.ensure_safe_key(round_id)}.json"

    def decl_dir(self, repo_root: Path, *, node_path: str, decl_name: str) -> Path:
        paths = self._paths(repo_root, node_path)
        if paths is None:
            raise ValueError(f"Invalid node path: {node_path}")
        return paths.decls_dir / self.runtime.foundation.layout.ensure_safe_key(decl_name)

    def decl_record_path(self, repo_root: Path, *, node_path: str, decl_name: str) -> Path:
        return self.decl_dir(repo_root, node_path=node_path, decl_name=decl_name) / "decl.json"

    def decl_revisions_dir(self, repo_root: Path, *, node_path: str, decl_name: str) -> Path:
        return self.decl_dir(repo_root, node_path=node_path, decl_name=decl_name) / "revisions"

    def revision_path(self, repo_root: Path, *, node_path: str, decl_name: str, revision: int) -> Path:
        if revision < 1:
            raise ValueError("revision must be >= 1")
        return self.decl_revisions_dir(repo_root, node_path=node_path, decl_name=decl_name) / f"{revision}.json"

    def _migrate_flat_revisions(self, paths: "_GraphPaths") -> ServiceResult[list[str]]:
        migrated: list[str] = []
        for revision_path in sorted(paths.decls_dir.glob("*/revisions/*.json")):
            raw = self._read_raw_json(revision_path)
            if raw is None:
                return self._invalid_json_for_migration(revision_path)
            if not self._looks_like_flat_revision(raw):
                continue
            converted = self._flat_revision_to_nested(raw)
            try:
                revision = DeclRevision.model_validate(converted)
            except Exception as exc:  # noqa: BLE001
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "decl_graph_migration_revision_invalid",
                        f"Legacy revision could not be converted to canonical DeclRevision: {exc}",
                        object_ref=str(revision_path),
                    )
                )
            written = self.runtime.foundation.store.write_json_atomic(revision_path, revision, mode=WriteMode.OVERWRITE)
            if not written.ok:
                return self.runtime.foundation.fail(written.issues)
            migrated.append(f"{revision.decl_name}:{revision.revision}")
        return self.runtime.foundation.ok(migrated)

    def _migrate_legacy_changes(self, paths: "_GraphPaths") -> ServiceResult[dict[str, DeclRevisionRef]]:
        changes_dir = paths.graph_root / "changes"
        change_refs: dict[str, DeclRevisionRef] = {}
        if not changes_dir.exists():
            return self.runtime.foundation.ok(change_refs)
        for change_path in sorted(changes_dir.glob("*.json")):
            raw = self._read_raw_json(change_path)
            if raw is None:
                return self._invalid_json_for_migration(change_path)
            change_id = str(raw.get("change_id") or change_path.stem).strip()
            decl_name = str(raw.get("decl_name") or "").strip()
            target_revision = raw.get("target_revision", raw.get("revision"))
            if not change_id or not decl_name or target_revision is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "decl_graph_migration_change_missing_target",
                        "Legacy change must include change_id, decl_name, and target_revision.",
                        object_ref=str(change_path),
                    )
                )
            try:
                revision_number = int(target_revision)
            except (TypeError, ValueError):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "decl_graph_migration_change_invalid_revision",
                        "Legacy change target_revision must be an integer.",
                        object_ref=str(change_path),
                    )
                )
            revision_path = paths.decls_dir / decl_name / "revisions" / f"{revision_number}.json"
            revision_raw = self._read_raw_json(revision_path)
            if revision_raw is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "decl_graph_migration_change_revision_missing",
                        "Legacy change target revision file is missing or invalid.",
                        object_ref=str(revision_path),
                    )
                )
            if self._looks_like_flat_revision(revision_raw):
                revision_raw = self._flat_revision_to_nested(revision_raw)
            try:
                revision_raw["change"] = self._legacy_change_to_revision_change(raw)
            except ValueError as exc:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "decl_graph_migration_change_invalid_kind",
                        f"Legacy change kind is invalid: {exc}",
                        object_ref=str(change_path),
                    )
                )
            try:
                revision = DeclRevision.model_validate(revision_raw)
            except Exception as exc:  # noqa: BLE001
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "decl_graph_migration_change_invalid",
                        f"Legacy change could not be embedded in DeclRevision: {exc}",
                        object_ref=str(change_path),
                    )
                )
            written = self.runtime.foundation.store.write_json_atomic(revision_path, revision, mode=WriteMode.OVERWRITE)
            if not written.ok:
                return self.runtime.foundation.fail(written.issues)
            change_refs[change_id] = DeclRevisionRef(change_id=change_id, decl_name=decl_name, revision=revision_number)
        return self.runtime.foundation.ok(change_refs)

    def _migrate_legacy_rounds(
        self,
        paths: "_GraphPaths",
        *,
        change_refs: dict[str, DeclRevisionRef],
    ) -> ServiceResult[list[str]]:
        migrated: list[str] = []
        for round_path in sorted(paths.rounds_dir.glob("*.json")):
            raw = self._read_raw_json(round_path)
            if raw is None:
                return self._invalid_json_for_migration(round_path)
            if "change_ids" not in raw and "completed_at" not in raw and raw.get("status") not in {"completed", "blocked", "failed"}:
                continue
            converted = dict(raw)
            change_ids = [str(item).strip() for item in converted.pop("change_ids", [])]
            if change_ids:
                missing = [change_id for change_id in change_ids if change_id not in change_refs]
                if missing:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "decl_graph_migration_round_change_missing",
                            "Legacy round change_ids cannot be resolved to revision_refs.",
                            object_ref=str(round_path),
                            details={"missing_change_ids": ",".join(missing)},
                        )
                    )
                converted["revision_refs"] = [change_refs[change_id].model_dump(mode="json") for change_id in change_ids]
            converted = self._normalize_legacy_round_terminal_fields(converted)
            try:
                round_model = DeclGraphRound.model_validate(converted)
            except Exception as exc:  # noqa: BLE001
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "decl_graph_migration_round_invalid",
                        f"Legacy round could not be converted to canonical DeclGraphRound: {exc}",
                        object_ref=str(round_path),
                    )
                )
            written = self.runtime.foundation.store.write_json_atomic(round_path, round_model, mode=WriteMode.OVERWRITE)
            if not written.ok:
                return self.runtime.foundation.fail(written.issues)
            migrated.append(round_model.round_id)
        return self.runtime.foundation.ok(migrated)

    def _flat_revision_to_nested(self, raw: dict[str, object]) -> dict[str, object]:
        converted = {
            "decl_name": raw.get("decl_name"),
            "revision": raw.get("revision", 1),
            "state": raw.get("state", "planned"),
            "status": raw.get("status") if raw.get("status") in {"open", "committed"} else raw.get("version_status", "open"),
            "module": raw.get("module"),
            "updated_at": raw.get("updated_at", utc_now_iso()),
            "statement": {
                "nl": self._legacy_nl_section(raw.get("statement_nl"), raw.get("statement_origin")),
                "formal": self._legacy_formal_section(raw.get("statement_lean_code"), raw.get("statement_lean_check")),
                "deps": self._legacy_repo_decl_deps(raw.get("statement_deps") or []),
            },
            "proof": self._legacy_proof_section(raw),
        }
        if raw.get("change") is not None:
            converted["change"] = raw["change"]
        elif raw.get("change_kind") is not None:
            converted["change"] = {
                "kind": raw.get("change_kind"),
                "start_before_state": raw.get("start_before_state"),
                "end_after_state": raw.get("end_after_state"),
                "objective": raw.get("objective"),
                "summary": raw.get("summary"),
            }
        else:
            converted["change"] = None
        return {key: value for key, value in converted.items() if value is not None}

    @staticmethod
    def _legacy_nl_section(text: object, origin: object) -> dict[str, object] | None:
        if text is None and not origin:
            return None
        return {
            "text": text,
            "origin": origin or [],
        }

    @staticmethod
    def _legacy_formal_section(code: object, check: object) -> dict[str, object] | None:
        if code is None and check is None:
            return None
        return {
            "code": code,
            "check": check,
        }

    def _legacy_proof_section(self, raw: dict[str, object]) -> dict[str, object] | None:
        proof_nl = raw.get("proof_nl")
        proof_origin = raw.get("proof_origin")
        proof_code = raw.get("proof_lean_code")
        proof_check = raw.get("proof_lean_check")
        proof_deps = raw.get("proof_deps") or []
        if proof_nl is None and not proof_origin and proof_code is None and proof_check is None and not proof_deps:
            return None
        return {
            "nl": self._legacy_nl_section(proof_nl, proof_origin),
            "formal": self._legacy_formal_section(proof_code, proof_check),
            "deps": self._legacy_repo_decl_deps(proof_deps),
        }

    @staticmethod
    def _legacy_repo_decl_deps(value: object) -> list[dict[str, object]]:
        if not isinstance(value, list):
            return []
        deps = []
        seen: set[str] = set()
        for item in value:
            name = str(item).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            deps.append({"kind": "repo_decl", "ref": {"name": name}})
        return deps

    @staticmethod
    def _legacy_change_to_revision_change(raw: dict[str, object]) -> dict[str, object]:
        return {
            "kind": DeclChangeKind(str(raw.get("kind") or raw.get("change_kind"))).value,
            "start_before_state": raw.get("start_before_state"),
            "end_after_state": raw.get("end_after_state"),
            "objective": raw.get("objective"),
            "summary": raw.get("summary"),
        }

    @staticmethod
    def _normalize_legacy_round_terminal_fields(raw: dict[str, object]) -> dict[str, object]:
        status = raw.get("status")
        if status == "completed":
            raw["status"] = DeclRoundStatus.COMMITTED.value
            raw.setdefault("result_kind", DeclRoundResultKind.SUCCESS.value)
        elif status == "blocked":
            raw["status"] = DeclRoundStatus.COMMITTED.value
            raw.setdefault("result_kind", DeclRoundResultKind.BLOCKED.value)
        elif status == "failed":
            raw["status"] = DeclRoundStatus.COMMITTED.value
            raw.setdefault("result_kind", DeclRoundResultKind.FAILED.value)
        if "completed_at" in raw and "committed_at" not in raw:
            raw["committed_at"] = raw.pop("completed_at")
        else:
            raw.pop("completed_at", None)
        return raw

    @staticmethod
    def _looks_like_flat_revision(raw: dict[str, object]) -> bool:
        flat_keys = {
            "version_status",
            "change_kind",
            "statement_nl",
            "statement_origin",
            "statement_deps",
            "statement_lean_code",
            "statement_lean_check",
            "proof_nl",
            "proof_origin",
            "proof_deps",
            "proof_lean_code",
            "proof_lean_check",
            "decl_deps",
        }
        return any(key in raw for key in flat_keys)

    @staticmethod
    def _read_raw_json(path: Path) -> dict[str, object] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _invalid_json_for_migration(self, path: Path) -> ServiceResult[object]:
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "decl_graph_migration_invalid_json",
                "DeclGraph migration could not read a JSON object.",
                object_ref=str(path),
            )
        )

    def _require_content_node(self, repo_root: Path, node_path: str) -> ServiceResult[object]:
        try:
            node = self.runtime.node.node_tree.get_node(repo_root, path=node_path)
        except ValueError:
            return self._invalid_node_path(node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        if node.value.kind == NodeKind.CONTENT:
            return self.runtime.foundation.ok(node.value)
        if self._is_adapter_root_catalog_node(repo_root, node_path=node_path, kind=node.value.kind):
            return self.runtime.foundation.ok(node.value)
        if node.value.kind != NodeKind.CONTENT:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_graph_node_not_content",
                    "Decl graphs can only be attached to Content nodes, except adapter repo Main catalog.",
                    object_ref=node_path,
                    current=node.value.kind.value,
                    expected=f"{NodeKind.CONTENT.value} or adapter Main {NodeKind.SCOPE.value}",
                )
            )
        return self.runtime.foundation.ok(node.value)

    def _is_adapter_root_catalog_node(self, repo_root: Path, *, node_path: str, kind: NodeKind) -> bool:
        if node_path != "Main" or kind != NodeKind.SCOPE:
            return False
        try:
            repo_format = self.runtime.repo_workspace.metadata.get_repo_format(repo_root)
        except RuntimeError:
            return False
        return bool(repo_format.ok and repo_format.value is not None and repo_format.value.repo_format == RepoFormat.ADAPTER)

    def _empty_index(self, repo_root: Path, node_path: str) -> ServiceResult[DeclGraphIndex]:
        node = self.runtime.node.node_tree.node_store.resolve_active_node(repo_root, path=node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        return self.runtime.foundation.ok(DeclGraphIndex(
            node_id=node.value.node_id,
            node_path=node_path,
            summary=f"Empty DeclGraph index for Content node {node_path}.",
        ))

    def _paths(self, repo_root: Path, node_path: str) -> "_GraphPaths | None":
        try:
            node = self.runtime.node.node_tree.node_store.resolve_active_node(repo_root, path=node_path)
            if not node.ok or node.value is None:
                return None
            graph_root = self.runtime.node.node_tree.node_store.decl_graph_dir(repo_root, node_id=node.value.node_id)
        except ValueError:
            return None
        return _GraphPaths(
            node_id=node.value.node_id,
            graph_root=graph_root,
            index_path=graph_root / self.INDEX_FILENAME,
            strategies_dir=graph_root / "strategies",
            rounds_dir=graph_root / "rounds",
            decls_dir=graph_root / "decls",
        )

    def _invalid_node_path(self, node_path: str) -> ServiceResult[object]:
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "decl_graph_node_path_invalid",
                "DeclGraph node path is invalid.",
                object_ref=node_path,
            )
        )


@dataclass(frozen=True)
class _GraphPaths:
    node_id: str
    graph_root: Path
    index_path: Path
    strategies_dir: Path
    rounds_dir: Path
    decls_dir: Path
