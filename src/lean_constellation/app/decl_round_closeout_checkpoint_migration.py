"""Offline immutable-clone migration for declaration-round closeout truth.

The migration is operator-only.  It derives pending legacy closeout rounds from
the archived runtime Flow lineage, clones the selected repo/runtime/scope
checkpoint, and never loads or resumes the production scheduler.
"""

from __future__ import annotations

import os
import shutil
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from lean_constellation.app.repo_completion_mode_migration import (
    AgentResourceContract,
    RepoCompletionModeMigrationError,
    _agent_resource_contract,
    _copy_fresh,
    _delete_runtime_index_row,
    _delete_scope_index_row,
    _encode_json,
    _fsync_dir,
    _load_json,
    _load_source_lineage,
    _preview_repo_archive,
    _preview_scope_archive,
    _provider_artifact_manifest_hashes,
    _register_runtime_snapshot,
    _register_scope_snapshot,
    _repo_checkpoint_dir,
    _require_runtime_index_row,
    _require_scope_index_row,
    _resolve_repo_root,
    _resolve_report_dir,
    _runtime_snapshot_dir,
    _safe_key,
    _scope_file_entries,
    _scope_snapshot_dir,
    _sha256,
    _sha256_file,
    _utc_now,
    _validate_repo_files_manifest,
    _write_json_atomic,
)
from lean_constellation.services.decl_graph.models import (
    DeclGraphRound,
    DeclRoundResultKind,
    DeclRoundStatus,
)
from lean_constellation.services.validation_snapshot.snapshot_restore import (
    SnapshotFilesManifest,
)


_MIGRATION_ACTOR = "checkpoint_migration:decl_closeout_v1"
_FINAL_FLOW_STATUSES = {"completed", "failed", "cancelled"}
_FLOW_OUTCOME_TO_RESULT = {
    "completed": DeclRoundResultKind.SUCCESS,
    "blocked": DeclRoundResultKind.BLOCKED,
    "failed": DeclRoundResultKind.FAILED,
}


class DeclRoundCloseoutCheckpointMigrationError(RepoCompletionModeMigrationError):
    """Raised when declaration-round closeout migration cannot be proven safe."""


@dataclass(frozen=True)
class PendingRoundEvidence:
    round_id: str
    node_path: str
    content_flow_id: str
    child_flow_id: str
    child_outcome: str
    scope_id: str


@dataclass(frozen=True)
class RoundRewrite:
    relative_path: str
    round_id: str
    node_path: str
    result_kind: str
    pending_plan_closeout: bool
    old_sha256: str
    new_sha256: str
    old_size: int
    new_size: int


@dataclass
class DeclRoundCloseoutMigrationReport:
    mode: Literal["preview", "apply", "validate"]
    repo_root: str
    source_repo_checkpoint_id: str
    source_runtime_snapshot_id: str
    source_scope_snapshot_ids: dict[str, str]
    source_manifest_hashes: dict[str, str]
    provider_artifact_manifest_hashes: dict[str, str]
    agent_resources: AgentResourceContract
    recovery_token: str
    round_count: int
    round_status_counts: dict[str, int]
    pending_rounds: list[PendingRoundEvidence]
    rewrites: list[RoundRewrite]
    flow_count: int
    step_count: int
    agent_count: int
    provider_artifact_count: int
    new_repo_checkpoint_id: str | None = None
    new_runtime_snapshot_id: str | None = None
    new_scope_snapshot_ids: dict[str, str] = field(default_factory=dict)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def preview_decl_round_closeout_checkpoint(
    repo_root: Path,
    *,
    checkpoint_id: str,
) -> DeclRoundCloseoutMigrationReport:
    """Preview one immutable checkpoint lineage without writing any truth."""

    root = _resolve_repo_root(repo_root)
    checkpoint_id = _safe_key(checkpoint_id, "checkpoint_id")
    source = _load_source_lineage(root, checkpoint_id)
    completion_mapping, completion_rewrites = _preview_repo_archive(
        root,
        checkpoint_id,
        source["repo_dir"],
        require_new=True,
    )
    if completion_rewrites:
        raise DeclRoundCloseoutCheckpointMigrationError(
            "checkpoint still contains legacy completion fields; migrate completion mode first"
        )
    del completion_mapping
    pending = _pending_round_evidence(root, source["scope_snapshot_ids"])
    pending_ids = {item.round_id for item in pending}
    rewrites, round_count, status_counts = _preview_round_rewrites(
        source["repo_dir"],
        pending_round_ids=pending_ids,
    )
    counts = _scope_counts(root, source["scope_snapshot_ids"])
    resources = _agent_resource_contract()
    source_hashes = _source_manifest_hashes(root, source)
    artifact_hashes = _provider_artifact_manifest_hashes(
        root,
        source["scope_snapshot_ids"],
    )
    contract = {
        "source_repo_checkpoint_id": checkpoint_id,
        "source_runtime_snapshot_id": source["runtime_snapshot_id"],
        "source_scope_snapshot_ids": source["scope_snapshot_ids"],
        "source_manifest_hashes": source_hashes,
        "provider_artifact_manifest_hashes": artifact_hashes,
        "agent_resource_digest": resources.digest,
        "pending_rounds": [asdict(item) for item in pending],
        "rewrites": [asdict(item) for item in rewrites],
        "round_count": round_count,
        "round_status_counts": status_counts,
        "counts": counts,
    }
    token = _sha256(_encode_json(contract))
    return DeclRoundCloseoutMigrationReport(
        mode="preview",
        repo_root=str(root),
        source_repo_checkpoint_id=checkpoint_id,
        source_runtime_snapshot_id=source["runtime_snapshot_id"],
        source_scope_snapshot_ids=source["scope_snapshot_ids"],
        source_manifest_hashes=source_hashes,
        provider_artifact_manifest_hashes=artifact_hashes,
        agent_resources=resources,
        recovery_token=token,
        round_count=round_count,
        round_status_counts=status_counts,
        pending_rounds=pending,
        rewrites=rewrites,
        flow_count=counts["flow"],
        step_count=counts["step"],
        agent_count=counts["agent"],
        provider_artifact_count=counts["provider_artifact"],
        summary=(
            f"Previewed {round_count} declaration rounds in {checkpoint_id}; "
            f"{len(rewrites)} files require closeout migration and "
            f"{len(pending)} round(s) remain pending ContentPlan acknowledgement."
        ),
    )


def apply_decl_round_closeout_checkpoint(
    repo_root: Path,
    *,
    checkpoint_id: str,
    expected_token: str,
    report_dir: Path,
) -> DeclRoundCloseoutMigrationReport:
    """Create and validate a fresh immutable checkpoint lineage."""

    preview = preview_decl_round_closeout_checkpoint(
        repo_root,
        checkpoint_id=checkpoint_id,
    )
    if expected_token != preview.recovery_token:
        raise DeclRoundCloseoutCheckpointMigrationError(
            "preview/apply token mismatch; checkpoint lineage or Agent resources changed"
        )
    root = Path(preview.repo_root)
    reports = _resolve_report_dir(root, report_dir)
    result_path = (
        reports
        / "decl_round_closeout_checkpoint_migration"
        / preview.recovery_token
        / "apply.json"
    )
    if result_path.is_file():
        report = _report_from_dict(_load_json(result_path))
        validate_decl_round_closeout_checkpoint(
            root,
            checkpoint_id=str(report.new_repo_checkpoint_id),
            expected_source_checkpoint_id=checkpoint_id,
        )
        return report

    source = _load_source_lineage(root, checkpoint_id)
    new_scope_ids = {
        scope_id: f"ss_{uuid.uuid4().hex}"
        for scope_id in source["scope_snapshot_ids"]
    }
    new_runtime_id = f"rs_{uuid.uuid4().hex}"
    new_repo_id = f"repo_cp_{uuid.uuid4().hex}"
    staging_paths: list[Path] = []
    published_paths: list[Path] = []
    registered_scope_ids: list[str] = []
    runtime_registered = False
    try:
        for scope_id, source_scope_id in source["scope_snapshot_ids"].items():
            target_scope_id = new_scope_ids[scope_id]
            target = _scope_snapshot_dir(root, target_scope_id)
            staging = target.parent / ".migration_staging" / target_scope_id
            staging_paths.append(staging)
            _copy_fresh(_scope_snapshot_dir(root, source_scope_id), staging)
            _rewrite_scope_identity(
                staging,
                snapshot_id=target_scope_id,
                expected_scope_id=scope_id,
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, target)
            _fsync_dir(target.parent)
            published_paths.append(target)
            _register_scope_snapshot(root, target)
            registered_scope_ids.append(target_scope_id)

        runtime_target = _runtime_snapshot_dir(root, new_runtime_id)
        runtime_staging = runtime_target.parent / ".migration_staging" / new_runtime_id
        staging_paths.append(runtime_staging)
        _copy_fresh(source["runtime_dir"], runtime_staging)
        runtime_payload = _load_json(runtime_staging / "snapshot.json")
        runtime_payload["snapshot_id"] = new_runtime_id
        runtime_payload["created_at"] = _utc_now()
        runtime_payload["scope_snapshot_ids"] = new_scope_ids
        _write_json_atomic(runtime_staging / "snapshot.json", runtime_payload)
        os.replace(runtime_staging, runtime_target)
        _fsync_dir(runtime_target.parent)
        published_paths.append(runtime_target)
        _register_runtime_snapshot(root, runtime_target)
        runtime_registered = True

        repo_target = _repo_checkpoint_dir(root, new_repo_id)
        repo_staging = repo_target.parent / ".migration_staging" / new_repo_id
        staging_paths.append(repo_staging)
        _copy_fresh(source["repo_dir"], repo_staging)
        _rewrite_repo_rounds(
            repo_staging,
            snapshot_id=new_repo_id,
            runtime_snapshot_id=new_runtime_id,
            source_checkpoint_id=checkpoint_id,
            pending_round_ids={item.round_id for item in preview.pending_rounds},
        )
        os.replace(repo_staging, repo_target)
        _fsync_dir(repo_target.parent)
        published_paths.append(repo_target)

        validated = validate_decl_round_closeout_checkpoint(
            root,
            checkpoint_id=new_repo_id,
            expected_source_checkpoint_id=checkpoint_id,
        )
    except Exception as exc:
        if runtime_registered:
            _delete_runtime_index_row(root, new_runtime_id)
        for scope_snapshot_id in registered_scope_ids:
            _delete_scope_index_row(root, scope_snapshot_id)
        for path in reversed(published_paths):
            shutil.rmtree(path, ignore_errors=True)
        for path in staging_paths:
            shutil.rmtree(path, ignore_errors=True)
        raise DeclRoundCloseoutCheckpointMigrationError(
            f"immutable closeout checkpoint clone failed; new artifacts were rolled back: {exc}"
        ) from exc

    if (
        validated.round_count != preview.round_count
        or validated.flow_count != preview.flow_count
        or validated.step_count != preview.step_count
        or validated.agent_count != preview.agent_count
        or validated.provider_artifact_count != preview.provider_artifact_count
    ):
        raise DeclRoundCloseoutCheckpointMigrationError(
            "validated clone object counts differ from the source preview"
        )
    preview.mode = "apply"
    preview.new_repo_checkpoint_id = new_repo_id
    preview.new_runtime_snapshot_id = new_runtime_id
    preview.new_scope_snapshot_ids = new_scope_ids
    preview.summary = (
        f"Created immutable closeout checkpoint clone {new_repo_id} from "
        f"{checkpoint_id}; source archives were not modified."
    )
    _write_json_atomic(result_path, preview.to_dict())
    return preview


def validate_decl_round_closeout_checkpoint(
    repo_root: Path,
    *,
    checkpoint_id: str,
    expected_source_checkpoint_id: str | None = None,
) -> DeclRoundCloseoutMigrationReport:
    """Validate a migrated closeout checkpoint without loading the runtime."""

    root = _resolve_repo_root(repo_root)
    checkpoint_id = _safe_key(checkpoint_id, "checkpoint_id")
    source = _load_source_lineage(root, checkpoint_id)
    _, completion_rewrites = _preview_repo_archive(
        root,
        checkpoint_id,
        source["repo_dir"],
        require_new=True,
    )
    if completion_rewrites:
        raise DeclRoundCloseoutCheckpointMigrationError(
            "checkpoint still contains legacy completion fields"
        )
    pending = _pending_round_evidence(root, source["scope_snapshot_ids"])
    pending_ids = {item.round_id for item in pending}
    rewrites, round_count, status_counts = _preview_round_rewrites(
        source["repo_dir"],
        pending_round_ids=pending_ids,
        require_new=True,
    )
    if rewrites:
        raise DeclRoundCloseoutCheckpointMigrationError(
            "checkpoint still contains legacy declaration-round closeout truth"
        )
    counts = _scope_counts(root, source["scope_snapshot_ids"])
    for scope_id, scope_snapshot_id in source["scope_snapshot_ids"].items():
        _require_scope_index_row(root, scope_snapshot_id, scope_id)
    _require_runtime_index_row(root, source["runtime_snapshot_id"])
    manifest = _load_json(source["repo_dir"] / "snapshot.json")
    if expected_source_checkpoint_id is not None:
        expected_marker = f"cloned from {expected_source_checkpoint_id}"
        if expected_marker not in str(manifest.get("summary") or ""):
            raise DeclRoundCloseoutCheckpointMigrationError(
                "new checkpoint manifest does not identify the expected source checkpoint"
            )
    resources = _agent_resource_contract()
    source_hashes = _source_manifest_hashes(root, source)
    artifact_hashes = _provider_artifact_manifest_hashes(
        root,
        source["scope_snapshot_ids"],
    )
    return DeclRoundCloseoutMigrationReport(
        mode="validate",
        repo_root=str(root),
        source_repo_checkpoint_id=checkpoint_id,
        source_runtime_snapshot_id=source["runtime_snapshot_id"],
        source_scope_snapshot_ids=source["scope_snapshot_ids"],
        source_manifest_hashes=source_hashes,
        provider_artifact_manifest_hashes=artifact_hashes,
        agent_resources=resources,
        recovery_token="",
        round_count=round_count,
        round_status_counts=status_counts,
        pending_rounds=pending,
        rewrites=[],
        flow_count=counts["flow"],
        step_count=counts["step"],
        agent_count=counts["agent"],
        provider_artifact_count=counts["provider_artifact"],
        new_repo_checkpoint_id=checkpoint_id,
        new_runtime_snapshot_id=source["runtime_snapshot_id"],
        new_scope_snapshot_ids=source["scope_snapshot_ids"],
        summary=(
            f"Checkpoint {checkpoint_id} validates against the current "
            "declaration-round closeout and Agent resource schema."
        ),
    )


def _pending_round_evidence(
    root: Path,
    scope_snapshot_ids: dict[str, str],
) -> list[PendingRoundEvidence]:
    flows: dict[str, tuple[str, dict[str, Any]]] = {}
    for scope_id, snapshot_id in scope_snapshot_ids.items():
        files_root = _scope_snapshot_dir(root, snapshot_id) / "files"
        for path in sorted(files_root.rglob("flow.json")):
            payload = _load_json(path)
            if payload.get("object_type") == "flow":
                flows[str(payload.get("flow_id") or "")] = (scope_id, payload)
    pending: list[PendingRoundEvidence] = []
    for content_flow_id, (scope_id, payload) in flows.items():
        if payload.get("flow_type") != "content_node_task":
            continue
        state = payload.get("state")
        if not isinstance(state, dict):
            continue
        position = state.get("position")
        phase = position.get("phase") if isinstance(position, dict) else None
        child_id = str(state.get("completed_child_flow_id") or "")
        if (
            phase != "callback_plan_agent"
            or state.get("waiting_child_kind") != "decl_graph_round"
            or not child_id
        ):
            continue
        child_entry = flows.get(child_id)
        if child_entry is None:
            raise DeclRoundCloseoutCheckpointMigrationError(
                f"Content Flow {content_flow_id} references missing completed child {child_id}"
            )
        _, child = child_entry
        if (
            child.get("flow_type") != "decl_graph_round"
            or child.get("status") not in _FINAL_FLOW_STATUSES
        ):
            raise DeclRoundCloseoutCheckpointMigrationError(
                f"Content Flow {content_flow_id} callback child is not a terminal Decl round"
            )
        child_input = child.get("input")
        child_result = child.get("result")
        if not isinstance(child_input, dict) or not isinstance(child_result, dict):
            raise DeclRoundCloseoutCheckpointMigrationError(
                f"Decl round child {child_id} lacks durable input/result truth"
            )
        outcome = str(child_result.get("outcome") or "")
        if outcome not in _FLOW_OUTCOME_TO_RESULT:
            raise DeclRoundCloseoutCheckpointMigrationError(
                f"Decl round child {child_id} has unsupported outcome: {outcome!r}"
            )
        pending.append(
            PendingRoundEvidence(
                round_id=str(child_input["round_id"]),
                node_path=str(child_input["node_path"]),
                content_flow_id=content_flow_id,
                child_flow_id=child_id,
                child_outcome=outcome,
                scope_id=scope_id,
            )
        )
    round_ids = [item.round_id for item in pending]
    if len(round_ids) != len(set(round_ids)):
        raise DeclRoundCloseoutCheckpointMigrationError(
            "multiple Content Flows claim the same pending declaration round"
        )
    return sorted(pending, key=lambda item: (item.node_path, item.round_id))


def _preview_round_rewrites(
    repo_dir: Path,
    *,
    pending_round_ids: set[str],
    require_new: bool = False,
) -> tuple[list[RoundRewrite], int, dict[str, int]]:
    rounds: dict[str, dict[str, Any]] = {}
    rewrites: list[RoundRewrite] = []
    status_counts: dict[str, int] = {}
    for path in _round_paths(repo_dir):
        payload = _load_json(path)
        round_id = str(payload.get("round_id") or "")
        if not round_id or round_id in rounds:
            raise DeclRoundCloseoutCheckpointMigrationError(
                f"invalid or duplicate round id in checkpoint: {round_id!r}"
            )
        transformed = deepcopy(payload)
        _migrate_round_payload(
            transformed,
            pending_plan_closeout=round_id in pending_round_ids,
            require_new=require_new,
        )
        DeclGraphRound.model_validate(transformed)
        rounds[round_id] = transformed
        status = str(transformed["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
        if transformed != payload:
            old = path.read_bytes()
            new = _encode_json(transformed)
            rewrites.append(
                RoundRewrite(
                    relative_path=path.relative_to(repo_dir).as_posix(),
                    round_id=round_id,
                    node_path=str(transformed["node_path"]),
                    result_kind=str(transformed["result_kind"]),
                    pending_plan_closeout=round_id in pending_round_ids,
                    old_sha256=_sha256(old),
                    new_sha256=_sha256(new),
                    old_size=len(old),
                    new_size=len(new),
                )
            )
    missing_pending = pending_round_ids - set(rounds)
    if missing_pending:
        raise DeclRoundCloseoutCheckpointMigrationError(
            "runtime callback references declaration rounds absent from repo checkpoint: "
            + ", ".join(sorted(missing_pending))
        )
    return rewrites, len(rounds), status_counts


def _migrate_round_payload(
    payload: dict[str, Any],
    *,
    pending_plan_closeout: bool,
    require_new: bool,
) -> None:
    status = DeclRoundStatus(str(payload.get("status")))
    if status != DeclRoundStatus.COMMITTED:
        if pending_plan_closeout:
            raise DeclRoundCloseoutCheckpointMigrationError(
                f"pending callback round is not committed: {payload.get('round_id')}"
            )
        return
    result_kind = DeclRoundResultKind(str(payload.get("result_kind")))
    committed_at = str(payload.get("committed_at") or "")
    if not committed_at:
        raise DeclRoundCloseoutCheckpointMigrationError(
            f"committed round lacks committed_at: {payload.get('round_id')}"
        )
    expected = {
        "execution_result_kind": result_kind.value,
        "execution_reason": payload.get("result_reason"),
        "execution_completed_at": committed_at,
    }
    for key, value in expected.items():
        if key in payload and payload[key] != value:
            raise DeclRoundCloseoutCheckpointMigrationError(
                f"round closeout field conflicts at {payload.get('round_id')}.{key}"
            )
        if key not in payload:
            if require_new:
                raise DeclRoundCloseoutCheckpointMigrationError(
                    f"round closeout field is missing at {payload.get('round_id')}.{key}"
                )
            payload[key] = value
    acknowledged_at = payload.get("plan_closeout_acknowledged_at")
    acknowledged_by = payload.get("plan_closeout_acknowledged_by")
    if pending_plan_closeout:
        if acknowledged_at is not None or acknowledged_by is not None:
            raise DeclRoundCloseoutCheckpointMigrationError(
                f"pending callback round is already acknowledged: {payload.get('round_id')}"
            )
        payload.setdefault("plan_closeout_acknowledged_at", None)
        payload.setdefault("plan_closeout_acknowledged_by", None)
        return
    if (acknowledged_at is None) != (acknowledged_by is None):
        raise DeclRoundCloseoutCheckpointMigrationError(
            f"partial Plan closeout acknowledgement: {payload.get('round_id')}"
        )
    if acknowledged_at is None:
        if require_new:
            raise DeclRoundCloseoutCheckpointMigrationError(
                f"historical committed round lacks migration acknowledgement: {payload.get('round_id')}"
            )
        payload["plan_closeout_acknowledged_at"] = committed_at
        payload["plan_closeout_acknowledged_by"] = _MIGRATION_ACTOR


def _round_paths(repo_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in (repo_dir / "files").rglob("*.json")
        if "decl_graph" in path.parts and "rounds" in path.parts
    )


def _rewrite_scope_identity(
    scope_dir: Path,
    *,
    snapshot_id: str,
    expected_scope_id: str,
) -> None:
    manifest = _load_json(scope_dir / "snapshot.json")
    artifacts = deepcopy(manifest.get("provider_artifacts"))
    manifest["snapshot_id"] = snapshot_id
    manifest["scope_id"] = expected_scope_id
    manifest["created_at"] = _utc_now()
    manifest["files"] = _scope_file_entries(scope_dir / "files")
    if manifest.get("provider_artifacts") != artifacts:
        raise DeclRoundCloseoutCheckpointMigrationError(
            "provider artifact manifests changed during scope clone"
        )
    _write_json_atomic(scope_dir / "snapshot.json", manifest)
    rewrites, _ = _preview_scope_archive(
        scope_dir,
        snapshot_id=snapshot_id,
        expected_scope_id=expected_scope_id,
        require_new=True,
    )
    if rewrites:
        raise DeclRoundCloseoutCheckpointMigrationError(
            "scope clone contains legacy completion fields"
        )


def _rewrite_repo_rounds(
    repo_dir: Path,
    *,
    snapshot_id: str,
    runtime_snapshot_id: str,
    source_checkpoint_id: str,
    pending_round_ids: set[str],
) -> None:
    rewrites, _, _ = _preview_round_rewrites(
        repo_dir,
        pending_round_ids=pending_round_ids,
    )
    for rewrite in rewrites:
        path = repo_dir / rewrite.relative_path
        payload = _load_json(path)
        _migrate_round_payload(
            payload,
            pending_plan_closeout=rewrite.pending_plan_closeout,
            require_new=False,
        )
        _write_json_atomic(path, payload)
    manifest_path = repo_dir / "snapshot.json"
    manifest = _load_json(manifest_path)
    manifest["snapshot_id"] = snapshot_id
    manifest["ark_runtime_snapshot_id"] = runtime_snapshot_id
    manifest["created_at"] = _utc_now()
    manifest["label"] = f"decl-closeout-migrated-from-{source_checkpoint_id}"
    manifest["summary"] = (
        f"Immutable declaration-round closeout migration cloned from "
        f"{source_checkpoint_id}."
    )
    _write_json_atomic(manifest_path, manifest)
    files_manifest_path = repo_dir / str(manifest["files_manifest_relpath"])
    files_manifest = _load_json(files_manifest_path)
    for entry in files_manifest.get("entries", []):
        archive = repo_dir / "files" / str(entry["archive_relpath"])
        entry["file_size"] = archive.stat().st_size
        entry["sha256"] = _sha256_file(archive)
    _write_json_atomic(files_manifest_path, files_manifest)
    _validate_repo_files_manifest(
        Path(str(manifest["repo_root"])),
        repo_dir,
        SnapshotFilesManifest.model_validate(files_manifest),
    )
    post_rewrites, _, _ = _preview_round_rewrites(
        repo_dir,
        pending_round_ids=pending_round_ids,
        require_new=True,
    )
    if post_rewrites:
        raise DeclRoundCloseoutCheckpointMigrationError(
            "repo clone still contains legacy declaration-round closeout truth"
        )


def _scope_counts(
    root: Path,
    scope_snapshot_ids: dict[str, str],
) -> dict[str, int]:
    counts = {"flow": 0, "step": 0, "agent": 0, "provider_artifact": 0}
    for scope_id, snapshot_id in scope_snapshot_ids.items():
        rewrites, scope_counts = _preview_scope_archive(
            _scope_snapshot_dir(root, snapshot_id),
            snapshot_id=snapshot_id,
            expected_scope_id=scope_id,
            require_new=True,
        )
        if rewrites:
            raise DeclRoundCloseoutCheckpointMigrationError(
                f"scope {snapshot_id} contains legacy completion fields"
            )
        for key, value in scope_counts.items():
            counts[key] += value
    return counts


def _source_manifest_hashes(
    root: Path,
    source: dict[str, Any],
) -> dict[str, str]:
    hashes = {
        "repo_snapshot": _sha256_file(source["repo_dir"] / "snapshot.json"),
        "repo_files_manifest": _sha256_file(
            source["repo_dir"] / "files_manifest.json"
        ),
        "runtime_snapshot": _sha256_file(source["runtime_dir"] / "snapshot.json"),
    }
    for scope_id, snapshot_id in sorted(source["scope_snapshot_ids"].items()):
        hashes[f"scope:{scope_id}"] = _sha256_file(
            _scope_snapshot_dir(root, snapshot_id) / "snapshot.json"
        )
    return hashes


def _report_from_dict(
    payload: dict[str, Any],
) -> DeclRoundCloseoutMigrationReport:
    return DeclRoundCloseoutMigrationReport(
        mode=payload["mode"],
        repo_root=payload["repo_root"],
        source_repo_checkpoint_id=payload["source_repo_checkpoint_id"],
        source_runtime_snapshot_id=payload["source_runtime_snapshot_id"],
        source_scope_snapshot_ids=dict(payload["source_scope_snapshot_ids"]),
        source_manifest_hashes=dict(payload["source_manifest_hashes"]),
        provider_artifact_manifest_hashes=dict(
            payload["provider_artifact_manifest_hashes"]
        ),
        agent_resources=AgentResourceContract(**payload["agent_resources"]),
        recovery_token=payload["recovery_token"],
        round_count=int(payload["round_count"]),
        round_status_counts=dict(payload["round_status_counts"]),
        pending_rounds=[
            PendingRoundEvidence(**item) for item in payload["pending_rounds"]
        ],
        rewrites=[RoundRewrite(**item) for item in payload["rewrites"]],
        flow_count=int(payload["flow_count"]),
        step_count=int(payload["step_count"]),
        agent_count=int(payload["agent_count"]),
        provider_artifact_count=int(payload["provider_artifact_count"]),
        new_repo_checkpoint_id=payload.get("new_repo_checkpoint_id"),
        new_runtime_snapshot_id=payload.get("new_runtime_snapshot_id"),
        new_scope_snapshot_ids=dict(payload.get("new_scope_snapshot_ids", {})),
        summary=str(payload.get("summary", "")),
    )


__all__ = [
    "DeclRoundCloseoutCheckpointMigrationError",
    "DeclRoundCloseoutMigrationReport",
    "PendingRoundEvidence",
    "RoundRewrite",
    "apply_decl_round_closeout_checkpoint",
    "preview_decl_round_closeout_checkpoint",
    "validate_decl_round_closeout_checkpoint",
]
