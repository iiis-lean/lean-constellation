"""One-time live-truth migration for Decl transition metadata.

This module is an offline maintenance entry point.  It is intentionally not
registered as an Agent tool and is not imported by production model loading.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from agent_runtime_kit.flow.registry import FlowTypeRegistry, StepTypeRegistry
from agent_runtime_kit.flow.store import FlowStepStore

from lean_constellation.flows.registry import register_lean_flow_step_types
from lean_constellation.services.decl_graph.models import (
    DeclChangeKind,
    DeclRevision,
    DeclRevisionStatus,
)


OLD_TO_NEW_KEYS = {
    "start_before_state": "reset_to_state",
    "end_after_state": "target_state",
}
REFUSED_PARTS = {
    "snapshots",
    "repo_checkpoints",
    "checkpoints",
    "reports",
    "rollouts",
    "sessions",
    "control",
    ".locks",
}


class DeclTransitionMigrationError(RuntimeError):
    """Raised when the offline migration cannot prove a safe rewrite."""


@dataclass(frozen=True)
class KeyRewrite:
    json_path: str
    old_key: str
    new_key: str


@dataclass(frozen=True)
class FileRewrite:
    relative_path: str
    old_sha256: str
    new_sha256: str
    old_size: int
    new_size: int
    renamed_key_count: int
    key_rewrites: list[KeyRewrite] = field(default_factory=list)
    base_revision_added: bool = False
    added_base_revision: int | None = None


@dataclass
class MigrationReport:
    mode: Literal["dry-run", "apply", "validate"]
    repo_root: str
    changed_files: list[FileRewrite] = field(default_factory=list)
    scanned_json_files: int = 0
    old_structured_key_count: int = 0
    new_structured_key_count: int = 0
    backup_root: str | None = None
    indexes_rebuilt: bool = False
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def migrate_decl_transition_schema(
    repo_root: Path,
    *,
    mode: Literal["dry-run", "apply", "validate"],
    manifest_dir: Path | None = None,
    rebuild_runtime_indexes: bool = False,
) -> MigrationReport:
    """Inspect, apply, or validate the strict transition schema migration."""

    root = Path(repo_root).expanduser().resolve()
    _validate_repo_root(root)
    paths = _live_json_paths(root)
    originals = {path: path.read_bytes() for path in paths}
    transformed: dict[Path, bytes] = {}
    rewrites: list[FileRewrite] = []
    old_count = 0
    new_count = 0

    for path in paths:
        payload = _load_json_bytes(originals[path], path)
        old_count += _count_object_keys(payload, set(OLD_TO_NEW_KEYS))
        migrated, key_rewrites = _rename_object_keys(payload, path=path)
        base_added, added_base = _add_or_validate_base_revision(root, path, migrated)
        new_count += _count_object_keys(migrated, set(OLD_TO_NEW_KEYS.values()))
        if not key_rewrites and not base_added:
            continue
        encoded = _encode_json(migrated)
        transformed[path] = encoded
        rewrites.append(
            FileRewrite(
                relative_path=str(path.relative_to(root)),
                old_sha256=_sha256(originals[path]),
                new_sha256=_sha256(encoded),
                old_size=len(originals[path]),
                new_size=len(encoded),
                renamed_key_count=len(key_rewrites),
                key_rewrites=key_rewrites,
                base_revision_added=base_added,
                added_base_revision=added_base,
            )
        )

    report = MigrationReport(
        mode=mode,
        repo_root=str(root),
        changed_files=rewrites,
        scanned_json_files=len(paths),
        old_structured_key_count=old_count,
        new_structured_key_count=new_count,
    )
    if mode == "dry-run":
        _validate_transformed_payloads(root, paths, originals, transformed)
        report.summary = f"Dry-run would rewrite {len(rewrites)} live JSON files."
        return report
    if mode == "validate":
        if rewrites:
            raise DeclTransitionMigrationError(
                f"live truth is not migrated: {len(rewrites)} files still require rewriting"
            )
        _validate_transformed_payloads(root, paths, originals, {})
        _require_no_old_live_keys(root)
        report.summary = "Live truth validates against the new transition schema."
        return report
    if mode != "apply":
        raise DeclTransitionMigrationError(f"unsupported migration mode: {mode}")
    if manifest_dir is None:
        raise DeclTransitionMigrationError(
            "apply requires an explicit manifest_dir outside protected history"
        )
    if not rewrites:
        _validate_transformed_payloads(root, paths, originals, {})
        _require_no_old_live_keys(root)
        report.summary = "Live truth was already migrated; apply made no changes."
        return report

    backup_root = _prepare_backup(root, Path(manifest_dir), originals, rewrites)
    report.backup_root = str(backup_root)
    staged: dict[Path, Path] = {}
    try:
        for path, encoded in transformed.items():
            staged[path] = _stage_sibling(path, encoded)
        for path in sorted(staged, key=lambda item: str(item)):
            os.replace(staged[path], path)
            _fsync_dir(path.parent)
        current = {path: path.read_bytes() for path in paths}
        _validate_transformed_payloads(root, paths, current, {})
        _require_no_old_live_keys(root)
        if rebuild_runtime_indexes and (root / ".agent_runtime" / "scopes").exists():
            _backup_runtime_indexes(root, backup_root)
            _rebuild_runtime_indexes(root)
            report.indexes_rebuilt = True
        _write_result_manifest(backup_root, report)
    except Exception as exc:
        _restore_backup(root, backup_root, rewrites)
        raise DeclTransitionMigrationError(
            f"migration failed and live JSON was rolled back: {exc}"
        ) from exc
    finally:
        for temp in staged.values():
            temp.unlink(missing_ok=True)
    report.summary = f"Migrated and validated {len(rewrites)} live JSON files."
    return report


def _validate_repo_root(root: Path) -> None:
    if not root.is_dir():
        raise DeclTransitionMigrationError(f"repo root does not exist: {root}")
    if any(part in REFUSED_PARTS for part in root.parts):
        raise DeclTransitionMigrationError(
            f"refusing protected/archive path as repo root: {root}"
        )
    if not (root / ".lean_constellation").is_dir():
        raise DeclTransitionMigrationError(
            f"repo has no .lean_constellation live truth: {root}"
        )


def _live_json_paths(root: Path) -> list[Path]:
    candidates: list[Path] = []
    business = root / ".lean_constellation"
    scopes = root / ".agent_runtime" / "scopes"
    for live_root in (business, scopes):
        if not live_root.exists():
            continue
        for path in live_root.rglob("*.json"):
            relative = path.relative_to(root)
            if any(part in REFUSED_PARTS for part in relative.parts):
                continue
            if path.is_symlink() or not path.is_file():
                raise DeclTransitionMigrationError(
                    f"refusing non-regular live JSON path: {path}"
                )
            candidates.append(path)
    return sorted(set(candidates), key=lambda item: str(item))


def _rename_object_keys(
    value: Any, *, path: Path, json_path: str = "$"
) -> tuple[Any, list[KeyRewrite]]:
    if isinstance(value, list):
        rewrites: list[KeyRewrite] = []
        items = []
        for index, item in enumerate(value):
            migrated, nested = _rename_object_keys(
                item, path=path, json_path=f"{json_path}[{index}]"
            )
            items.append(migrated)
            rewrites.extend(nested)
        return items, rewrites
    if not isinstance(value, dict):
        return value, []
    result: dict[str, Any] = {}
    rewrites: list[KeyRewrite] = []
    for key, item in value.items():
        new_key = OLD_TO_NEW_KEYS.get(key, key)
        if new_key != key and new_key in value:
            raise DeclTransitionMigrationError(
                f"transition key collision at {path}:{json_path}: {key} and {new_key}"
            )
        migrated, nested = _rename_object_keys(
            item, path=path, json_path=f"{json_path}.{new_key}"
        )
        result[new_key] = migrated
        if new_key != key:
            rewrites.append(
                KeyRewrite(
                    json_path=f"{json_path}.{key}",
                    old_key=key,
                    new_key=new_key,
                )
            )
        rewrites.extend(nested)
    return result, rewrites


def _add_or_validate_base_revision(
    root: Path, path: Path, payload: Any
) -> tuple[bool, int | None]:
    revision_number = _decl_revision_number(root, path)
    if revision_number is None or not isinstance(payload, dict):
        return False, None
    change = payload.get("change")
    if not isinstance(change, dict):
        return False, None
    try:
        kind = DeclChangeKind(change.get("kind"))
    except ValueError as exc:
        raise DeclTransitionMigrationError(
            f"invalid change kind in {path}: {change.get('kind')!r}"
        ) from exc
    expected: int | None = None
    if kind in {DeclChangeKind.UPDATE, DeclChangeKind.DELETE}:
        expected = revision_number - 1
        _validate_inferred_base(path, expected)
    existing = change.get("base_revision")
    if "base_revision" in change and existing != expected:
        raise DeclTransitionMigrationError(
            f"base_revision collision in {path}: found {existing!r}, expected {expected!r}"
        )
    if "base_revision" not in change:
        change["base_revision"] = expected
        return True, expected
    return False, None


def _decl_revision_number(root: Path, path: Path) -> int | None:
    relative = path.relative_to(root)
    parts = relative.parts
    if len(parts) < 6 or "decl_graph" not in parts or "revisions" not in parts:
        return None
    if path.parent.name != "revisions":
        return None
    try:
        return int(path.stem)
    except ValueError:
        return None


def _validate_inferred_base(path: Path, base_revision: int) -> None:
    if base_revision < 1:
        raise DeclTransitionMigrationError(
            f"cannot infer a positive base revision for {path}"
        )
    base_path = path.with_name(f"{base_revision}.json")
    if not base_path.is_file():
        raise DeclTransitionMigrationError(
            f"inferred base revision is missing for {path}: {base_path.name}"
        )
    base = _load_json_bytes(base_path.read_bytes(), base_path)
    if (
        base.get("revision") != base_revision
        or base.get("status") != DeclRevisionStatus.COMMITTED.value
    ):
        raise DeclTransitionMigrationError(
            f"inferred base revision for {path} is not the matching committed predecessor"
        )


def _validate_transformed_payloads(
    root: Path,
    paths: list[Path],
    originals: dict[Path, bytes],
    transformed: dict[Path, bytes],
) -> None:
    flow_registry = FlowTypeRegistry()
    step_registry = StepTypeRegistry()
    register_lean_flow_step_types(
        flow_registry=flow_registry, step_registry=step_registry
    )
    for path in paths:
        payload = _load_json_bytes(transformed.get(path, originals[path]), path)
        if _count_object_keys(payload, set(OLD_TO_NEW_KEYS)):
            raise DeclTransitionMigrationError(
                f"old structured transition key remains in live truth: {path}"
            )
        if _decl_revision_number(root, path) is not None:
            DeclRevision.model_validate(payload)
        elif path.name == "flow.json" and payload.get("object_type") == "flow":
            _validate_flow_payload(payload, flow_registry)
        elif path.name == "step.json" and payload.get("object_type") == "step":
            _validate_step_payload(payload, step_registry)


def _validate_flow_payload(payload: dict[str, Any], registry: FlowTypeRegistry) -> None:
    data = {
        key: value
        for key, value in payload.items()
        if key not in {"schema_version", "object_type"}
    }
    flow_type = str(data["flow_type"])
    flow_cls = registry.get(flow_type)
    data["input"] = registry.parse_input(flow_type, data.get("input"))
    data["state"] = registry.parse_state(flow_type, data["state"])
    data["result"] = registry.parse_result(flow_type, data.get("result"))
    data["error"] = registry.parse_error(flow_type, data.get("error"))
    if "flow_type" not in flow_cls.model_fields:
        data.pop("flow_type", None)
    flow_cls.model_validate(data)


def _validate_step_payload(payload: dict[str, Any], registry: StepTypeRegistry) -> None:
    data = {
        key: value
        for key, value in payload.items()
        if key not in {"schema_version", "object_type"}
    }
    step_type = str(data["step_type"])
    step_cls = registry.get(step_type)
    data["state"] = registry.parse_state(step_type, data["state"])
    data["submission"] = registry.parse_submission(step_type, data.get("submission"))
    data["result"] = registry.parse_result(step_type, data.get("result"))
    data["error"] = registry.parse_error(step_type, data.get("error"))
    if "step_type" not in step_cls.model_fields:
        data.pop("step_type", None)
    step_cls.model_validate(data)


def _prepare_backup(
    root: Path,
    manifest_dir: Path,
    originals: dict[Path, bytes],
    rewrites: list[FileRewrite],
) -> Path:
    destination = manifest_dir.expanduser().resolve()
    if destination.is_relative_to(root) and any(
        part in REFUSED_PARTS for part in destination.relative_to(root).parts
    ):
        raise DeclTransitionMigrationError(
            f"manifest_dir cannot be inside protected history: {destination}"
        )
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_root = destination / f"decl_transition_migration_{timestamp}"
    backup_root.mkdir(parents=True, exist_ok=False)
    before_rows: list[dict[str, Any]] = []
    for rewrite in rewrites:
        source = root / rewrite.relative_path
        target = backup_root / "before" / rewrite.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(originals[source])
        before_rows.append(asdict(rewrite))
    _write_json_atomic(
        backup_root / "BEFORE_MANIFEST.json",
        {"repo_root": str(root), "files": before_rows},
    )
    return backup_root


def _backup_runtime_indexes(root: Path, backup_root: Path) -> None:
    runtime_root = root / ".agent_runtime"
    for index in sorted(runtime_root.rglob("*.sqlite")):
        if index.is_file():
            target = backup_root / "before_indexes" / index.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(index, target)


def _restore_backup(root: Path, backup_root: Path, rewrites: list[FileRewrite]) -> None:
    for rewrite in rewrites:
        backup = backup_root / "before" / rewrite.relative_path
        if backup.is_file():
            _write_bytes_atomic(root / rewrite.relative_path, backup.read_bytes())
    index_backup = backup_root / "before_indexes"
    if index_backup.exists():
        for backup in index_backup.rglob("*.sqlite"):
            _write_bytes_atomic(
                root / backup.relative_to(index_backup), backup.read_bytes()
            )


def _rebuild_runtime_indexes(root: Path) -> None:
    flow_registry = FlowTypeRegistry()
    step_registry = StepTypeRegistry()
    register_lean_flow_step_types(
        flow_registry=flow_registry, step_registry=step_registry
    )
    store = FlowStepStore(
        root / ".agent_runtime",
        flow_registry=flow_registry,
        step_registry=step_registry,
    )
    store.rebuild_global_index()
    for scope_id in store.list_scope_ids():
        store.rebuild_scope_index(scope_id)


def _write_result_manifest(backup_root: Path, report: MigrationReport) -> None:
    _write_json_atomic(backup_root / "RESULT_MANIFEST.json", report.to_dict())


def _require_no_old_live_keys(root: Path) -> None:
    for path in _live_json_paths(root):
        payload = _load_json_bytes(path.read_bytes(), path)
        if _count_object_keys(payload, set(OLD_TO_NEW_KEYS)):
            raise DeclTransitionMigrationError(
                f"old structured transition key remains: {path}"
            )


def _stage_sibling(path: Path, data: bytes) -> Path:
    temp = path.with_name(f".{path.name}.decl-transition-migration.tmp")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return temp


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = _stage_sibling(path, data)
    os.replace(temp, path)
    _fsync_dir(path.parent)


def _write_json_atomic(path: Path, payload: Any) -> None:
    _write_bytes_atomic(path, _encode_json(payload))


def _encode_json(payload: Any) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _load_json_bytes(data: bytes, path: Path) -> Any:
    try:
        return json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeclTransitionMigrationError(
            f"invalid JSON in live truth: {path}: {exc}"
        ) from exc


def _count_object_keys(value: Any, keys: set[str]) -> int:
    if isinstance(value, dict):
        return sum(
            int(key in keys) + _count_object_keys(item, keys)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return sum(_count_object_keys(item, keys) for item in value)
    return 0


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "DeclTransitionMigrationError",
    "KeyRewrite",
    "MigrationReport",
    "migrate_decl_transition_schema",
]
