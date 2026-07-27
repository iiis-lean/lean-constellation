"""Offline immutable-clone migration for repository completion checkpoints.

This operator-only module migrates one selected repo checkpoint and its linked
ARK runtime/scope snapshots.  It never mutates live truth, starts the scheduler,
or registers an Agent-facing tool.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from agent_runtime_kit.flow.registry import FlowTypeRegistry, StepTypeRegistry

from lean_constellation.agents import (
    build_agent_surface_reports,
    build_agent_type_specs,
    build_skill_specs,
    render_agent_instruction,
    validate_agent_resources,
)
from lean_constellation.domain.repo import RepoCompletionMode, RepoConfig
from lean_constellation.domain.repo_release import RepoRelease
from lean_constellation.flows.registry import register_lean_flow_step_types
from lean_constellation.services.validation_snapshot.snapshot_restore import (
    RepoCheckpointSnapshotManifest,
    SnapshotFilesManifest,
)
from lean_constellation.tools import (
    build_application_tool_groups,
    build_application_tool_specs,
    build_application_tool_views,
    build_submit_tool_groups,
    build_submit_tool_specs,
    build_submit_tool_views,
)


_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_OLD_COMPLETION_KEYS = {"target_proof_availability", "work_mode"}
_OLD_COMPLETION_MAPPING = {
    ("declared", "declared_interface"): RepoCompletionMode.INTERFACE_DECLARED,
    ("declared", "declared_full_graph"): RepoCompletionMode.GRAPH_DECLARED,
    ("proved", "proved_full_graph"): RepoCompletionMode.GRAPH_PROVED,
}
_FINAL_STEP_STATUSES = {"completed", "failed", "cancelled"}
_FINAL_AGENT_STATUSES = {"idle", "completed", "failed", "cancelled"}


class RepoCompletionModeMigrationError(RuntimeError):
    """Raised when an immutable checkpoint migration cannot be proven safe."""


@dataclass(frozen=True)
class FieldRewrite:
    json_path: str
    operation: Literal["completion_pair_to_mode", "remove_task_mode", "remove_source_index_mode"]
    old_value: object
    new_value: object | None = None


@dataclass(frozen=True)
class FileRewrite:
    archive_kind: Literal["repo", "scope"]
    snapshot_id: str
    relative_path: str
    old_sha256: str
    new_sha256: str
    old_size: int
    new_size: int
    field_rewrites: list[FieldRewrite] = field(default_factory=list)


@dataclass(frozen=True)
class AgentResourceContract:
    digest: str
    agent_type_count: int
    application_tool_count: int
    application_group_count: int
    application_view_count: int
    submit_tool_count: int
    submit_group_count: int
    submit_view_count: int


@dataclass
class RepoCompletionMigrationReport:
    mode: Literal["preview", "apply", "validate"]
    repo_root: str
    source_repo_checkpoint_id: str
    source_runtime_snapshot_id: str
    source_scope_snapshot_ids: dict[str, str]
    repo_config_mapping: dict[str, str]
    rewrites: list[FileRewrite]
    source_manifest_hashes: dict[str, str]
    provider_artifact_manifest_hashes: dict[str, str]
    agent_resources: AgentResourceContract
    recovery_token: str
    flow_files_to_rewrite: int = 0
    step_files_to_rewrite: int = 0
    business_files_to_rewrite: int = 0
    new_repo_checkpoint_id: str | None = None
    new_runtime_snapshot_id: str | None = None
    new_scope_snapshot_ids: dict[str, str] = field(default_factory=dict)
    flow_count: int = 0
    step_count: int = 0
    agent_count: int = 0
    provider_artifact_count: int = 0
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def preview_repo_completion_checkpoint(
    repo_root: Path,
    *,
    checkpoint_id: str,
) -> RepoCompletionMigrationReport:
    """Validate and preview one legacy checkpoint lineage without writing it."""

    root = _resolve_repo_root(repo_root)
    checkpoint_id = _safe_key(checkpoint_id, "checkpoint_id")
    source = _load_source_lineage(root, checkpoint_id)
    resources = _agent_resource_contract()
    rewrites: list[FileRewrite] = []
    counts = {"flow": 0, "step": 0, "agent": 0, "provider_artifact": 0}

    repo_mapping, repo_rewrites = _preview_repo_archive(
        root,
        checkpoint_id,
        source["repo_dir"],
    )
    rewrites.extend(repo_rewrites)
    for scope_id, scope_snapshot_id in source["scope_snapshot_ids"].items():
        scope_dir = _scope_snapshot_dir(root, scope_snapshot_id)
        scope_rewrites, scope_counts = _preview_scope_archive(
            scope_dir,
            snapshot_id=scope_snapshot_id,
            expected_scope_id=scope_id,
        )
        rewrites.extend(scope_rewrites)
        for key, value in scope_counts.items():
            counts[key] += value

    source_hashes = {
        "repo_snapshot": _sha256_file(source["repo_dir"] / "snapshot.json"),
        "repo_files_manifest": _sha256_file(source["repo_dir"] / "files_manifest.json"),
        "runtime_snapshot": _sha256_file(source["runtime_dir"] / "snapshot.json"),
    }
    for scope_id, scope_snapshot_id in sorted(source["scope_snapshot_ids"].items()):
        source_hashes[f"scope:{scope_id}"] = _sha256_file(
            _scope_snapshot_dir(root, scope_snapshot_id) / "snapshot.json"
        )
    artifact_hashes = _provider_artifact_manifest_hashes(
        root,
        source["scope_snapshot_ids"],
    )
    contract = {
        "source_repo_checkpoint_id": checkpoint_id,
        "source_runtime_snapshot_id": source["runtime_snapshot_id"],
        "source_scope_snapshot_ids": source["scope_snapshot_ids"],
        "repo_config_mapping": repo_mapping,
        "rewrites": [asdict(item) for item in rewrites],
        "source_manifest_hashes": source_hashes,
        "provider_artifact_manifest_hashes": artifact_hashes,
        "agent_resource_digest": resources.digest,
        "counts": counts,
    }
    token = _sha256(_encode_json(contract))
    return RepoCompletionMigrationReport(
        mode="preview",
        repo_root=str(root),
        source_repo_checkpoint_id=checkpoint_id,
        source_runtime_snapshot_id=source["runtime_snapshot_id"],
        source_scope_snapshot_ids=source["scope_snapshot_ids"],
        repo_config_mapping=repo_mapping,
        rewrites=rewrites,
        source_manifest_hashes=source_hashes,
        provider_artifact_manifest_hashes=artifact_hashes,
        agent_resources=resources,
        recovery_token=token,
        flow_files_to_rewrite=sum(
            item.relative_path.endswith("/flow.json") for item in rewrites
        ),
        step_files_to_rewrite=sum(
            item.relative_path.endswith("/step.json") for item in rewrites
        ),
        business_files_to_rewrite=sum(item.archive_kind == "repo" for item in rewrites),
        flow_count=counts["flow"],
        step_count=counts["step"],
        agent_count=counts["agent"],
        provider_artifact_count=counts["provider_artifact"],
        summary=(
            f"Preview validated immutable checkpoint {checkpoint_id}; "
            f"{len(rewrites)} archived JSON files require migration."
        ),
    )


def apply_repo_completion_checkpoint(
    repo_root: Path,
    *,
    checkpoint_id: str,
    expected_token: str,
    report_dir: Path,
) -> RepoCompletionMigrationReport:
    """Create a new repo/runtime/scope checkpoint lineage without changing the source."""

    preview = preview_repo_completion_checkpoint(
        repo_root,
        checkpoint_id=checkpoint_id,
    )
    if expected_token != preview.recovery_token:
        raise RepoCompletionModeMigrationError(
            "preview/apply token mismatch; checkpoint lineage or current Agent resources changed"
        )
    root = Path(preview.repo_root)
    reports = _resolve_report_dir(root, report_dir)
    result_path = (
        reports
        / "repo_completion_mode_migration"
        / preview.recovery_token
        / "apply.json"
    )
    if result_path.is_file():
        existing = _load_json(result_path)
        report = _report_from_dict(existing)
        validate_repo_completion_checkpoint(
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
            _rewrite_scope_clone(
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
        _rewrite_repo_clone(
            root,
            repo_staging,
            snapshot_id=new_repo_id,
            runtime_snapshot_id=new_runtime_id,
            source_checkpoint_id=checkpoint_id,
        )
        os.replace(repo_staging, repo_target)
        _fsync_dir(repo_target.parent)
        published_paths.append(repo_target)

        validated = validate_repo_completion_checkpoint(
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
        raise RepoCompletionModeMigrationError(
            f"immutable checkpoint clone failed; new artifacts were rolled back: {exc}"
        ) from exc

    preview.mode = "apply"
    preview.new_scope_snapshot_ids = new_scope_ids
    preview.new_runtime_snapshot_id = new_runtime_id
    preview.new_repo_checkpoint_id = new_repo_id
    preview.summary = (
        f"Created and validated immutable checkpoint clone {new_repo_id} "
        f"from {checkpoint_id}; source archives were not modified."
    )
    if (
        validated.flow_count != preview.flow_count
        or validated.step_count != preview.step_count
        or validated.agent_count != preview.agent_count
    ):
        raise RepoCompletionModeMigrationError(
            "validated clone object counts differ from the source preview"
        )
    _write_json_atomic(result_path, preview.to_dict())
    return preview


def validate_repo_completion_checkpoint(
    repo_root: Path,
    *,
    checkpoint_id: str,
    expected_source_checkpoint_id: str | None = None,
) -> RepoCompletionMigrationReport:
    """Validate one new-schema immutable checkpoint lineage."""

    root = _resolve_repo_root(repo_root)
    checkpoint_id = _safe_key(checkpoint_id, "checkpoint_id")
    source = _load_source_lineage(root, checkpoint_id)
    resources = _agent_resource_contract()
    repo_mapping, repo_rewrites = _preview_repo_archive(
        root,
        checkpoint_id,
        source["repo_dir"],
        require_new=True,
    )
    if repo_rewrites:
        raise RepoCompletionModeMigrationError(
            f"repo checkpoint {checkpoint_id} still contains legacy completion truth"
        )
    counts = {"flow": 0, "step": 0, "agent": 0, "provider_artifact": 0}
    for scope_id, scope_snapshot_id in source["scope_snapshot_ids"].items():
        rewrites, scope_counts = _preview_scope_archive(
            _scope_snapshot_dir(root, scope_snapshot_id),
            snapshot_id=scope_snapshot_id,
            expected_scope_id=scope_id,
            require_new=True,
        )
        if rewrites:
            raise RepoCompletionModeMigrationError(
                f"scope snapshot {scope_snapshot_id} still contains legacy typed fields"
            )
        for key, value in scope_counts.items():
            counts[key] += value
        _require_scope_index_row(root, scope_snapshot_id, scope_id)
    _require_runtime_index_row(root, source["runtime_snapshot_id"])
    manifest = RepoCheckpointSnapshotManifest.model_validate(
        _load_json(source["repo_dir"] / "snapshot.json")
    )
    if expected_source_checkpoint_id is not None:
        expected_marker = f"cloned from {expected_source_checkpoint_id}"
        if expected_marker not in manifest.summary:
            raise RepoCompletionModeMigrationError(
                "new checkpoint manifest does not identify the expected source checkpoint"
            )
    artifact_hashes = _provider_artifact_manifest_hashes(
        root,
        source["scope_snapshot_ids"],
    )
    source_hashes = {
        "repo_snapshot": _sha256_file(source["repo_dir"] / "snapshot.json"),
        "repo_files_manifest": _sha256_file(source["repo_dir"] / "files_manifest.json"),
        "runtime_snapshot": _sha256_file(source["runtime_dir"] / "snapshot.json"),
    }
    return RepoCompletionMigrationReport(
        mode="validate",
        repo_root=str(root),
        source_repo_checkpoint_id=checkpoint_id,
        source_runtime_snapshot_id=source["runtime_snapshot_id"],
        source_scope_snapshot_ids=source["scope_snapshot_ids"],
        repo_config_mapping=repo_mapping,
        rewrites=[],
        source_manifest_hashes=source_hashes,
        provider_artifact_manifest_hashes=artifact_hashes,
        agent_resources=resources,
        recovery_token="",
        flow_count=counts["flow"],
        step_count=counts["step"],
        agent_count=counts["agent"],
        provider_artifact_count=counts["provider_artifact"],
        new_repo_checkpoint_id=checkpoint_id,
        new_runtime_snapshot_id=source["runtime_snapshot_id"],
        new_scope_snapshot_ids=source["scope_snapshot_ids"],
        summary=f"Checkpoint {checkpoint_id} validates against the current completion and Agent resource schema.",
    )


def _load_source_lineage(root: Path, checkpoint_id: str) -> dict[str, Any]:
    repo_dir = _repo_checkpoint_dir(root, checkpoint_id)
    if not repo_dir.is_dir():
        raise RepoCompletionModeMigrationError(
            f"repo checkpoint does not exist: {checkpoint_id}"
        )
    repo_manifest = RepoCheckpointSnapshotManifest.model_validate(
        _load_json(repo_dir / "snapshot.json")
    )
    if repo_manifest.snapshot_id != checkpoint_id:
        raise RepoCompletionModeMigrationError("repo checkpoint manifest identity mismatch")
    if Path(repo_manifest.repo_root).resolve() != root:
        raise RepoCompletionModeMigrationError("repo checkpoint belongs to a different repo root")
    files_manifest = SnapshotFilesManifest.model_validate(
        _load_json(repo_dir / repo_manifest.files_manifest_relpath)
    )
    _validate_repo_files_manifest(root, repo_dir, files_manifest)
    runtime_snapshot_id = _safe_key(
        repo_manifest.ark_runtime_snapshot_id or "",
        "ark_runtime_snapshot_id",
    )
    runtime_dir = _runtime_snapshot_dir(root, runtime_snapshot_id)
    runtime_payload = _load_json(runtime_dir / "snapshot.json")
    if runtime_payload.get("snapshot_id") != runtime_snapshot_id:
        raise RepoCompletionModeMigrationError("runtime snapshot manifest identity mismatch")
    scope_snapshot_ids = {
        str(scope_id): _safe_key(str(snapshot_id), "scope_snapshot_id")
        for scope_id, snapshot_id in dict(runtime_payload.get("scope_snapshot_ids") or {}).items()
    }
    if not scope_snapshot_ids:
        raise RepoCompletionModeMigrationError("runtime snapshot contains no scopes")
    return {
        "repo_dir": repo_dir,
        "runtime_snapshot_id": runtime_snapshot_id,
        "runtime_dir": runtime_dir,
        "scope_snapshot_ids": scope_snapshot_ids,
    }


def _preview_repo_archive(
    root: Path,
    snapshot_id: str,
    repo_dir: Path,
    *,
    require_new: bool = False,
) -> tuple[dict[str, str], list[FileRewrite]]:
    config_path = repo_dir / "files" / "lean_constellation" / "repo_config.json"
    payload = _load_json(config_path)
    rewrites: list[FileRewrite] = []
    if "completion_mode" in payload:
        RepoConfig.model_validate(payload)
        mapping = {
            "new_completion_mode": str(payload["completion_mode"]),
        }
    else:
        migrated = deepcopy(payload)
        field_rewrites: list[FieldRewrite] = []
        mode = _rewrite_completion_pair(
            migrated,
            json_path="$.repo_config",
            field_rewrites=field_rewrites,
        )
        RepoConfig.model_validate(migrated)
        mapping = {
            "old_target": str(payload["target_proof_availability"]),
            "old_work_mode": str(payload["work_mode"]),
            "new_completion_mode": mode.value,
        }
        rewrites.append(
            _file_rewrite(
                "repo",
                snapshot_id,
                config_path.relative_to(repo_dir).as_posix(),
                config_path.read_bytes(),
                _encode_json(migrated),
                field_rewrites,
            )
        )
    if require_new and rewrites:
        return mapping, rewrites
    releases_root = repo_dir / "files" / "lean_constellation" / "releases"
    if releases_root.is_dir():
        for release_path in sorted(releases_root.glob("*.json")):
            release = _load_json(release_path)
            if "completion_mode" in release:
                RepoRelease.model_validate(release)
                continue
            old_target = str(release.get("target_proof_availability") or "")
            release_checkpoint = _safe_key(
                str(release.get("repo_checkpoint_id") or ""),
                "release.repo_checkpoint_id",
            )
            release_config = _load_json(
                _repo_checkpoint_dir(root, release_checkpoint)
                / "files"
                / "lean_constellation"
                / "repo_config.json"
            )
            pair = (
                old_target,
                str(release_config.get("work_mode") or ""),
            )
            mode = _OLD_COMPLETION_MAPPING.get(pair)
            if mode is None:
                raise RepoCompletionModeMigrationError(
                    f"release {release_path.stem} lacks exact checkpoint evidence for completion mode"
                )
            migrated = deepcopy(release)
            del migrated["target_proof_availability"]
            migrated["completion_mode"] = mode.value
            RepoRelease.model_validate(migrated)
            rewrites.append(
                _file_rewrite(
                    "repo",
                    snapshot_id,
                    release_path.relative_to(repo_dir).as_posix(),
                    release_path.read_bytes(),
                    _encode_json(migrated),
                    [
                        FieldRewrite(
                            json_path="$.target_proof_availability",
                            operation="completion_pair_to_mode",
                            old_value=old_target,
                            new_value=mode.value,
                        )
                    ],
                )
            )
    return mapping, rewrites


def _preview_scope_archive(
    scope_dir: Path,
    *,
    snapshot_id: str,
    expected_scope_id: str,
    require_new: bool = False,
    allow_unstarted_created_steps: bool = False,
) -> tuple[list[FileRewrite], dict[str, int]]:
    manifest = _load_json(scope_dir / "snapshot.json")
    if manifest.get("snapshot_id") != snapshot_id:
        raise RepoCompletionModeMigrationError("scope snapshot manifest identity mismatch")
    if manifest.get("scope_id") != expected_scope_id:
        raise RepoCompletionModeMigrationError("runtime/scope snapshot linkage mismatch")
    _validate_scope_files_manifest(scope_dir, manifest)
    provider_artifacts = manifest.get("provider_artifacts")
    if not isinstance(provider_artifacts, list):
        raise RepoCompletionModeMigrationError("scope provider artifact manifest is invalid")
    counts = {
        "flow": 0,
        "step": 0,
        "agent": 0,
        "provider_artifact": len(provider_artifacts),
    }
    rewrites: list[FileRewrite] = []
    files_root = scope_dir / "files"
    for path in sorted(files_root.rglob("*.json")):
        payload = _load_json(path)
        object_type = payload.get("object_type")
        if object_type == "flow" and path.name == "flow.json":
            counts["flow"] += 1
            migrated, field_rewrites = _transform_flow_payload(payload)
            _validate_flow_payload(migrated)
        elif object_type == "step" and path.name == "step.json":
            counts["step"] += 1
            status = str(payload.get("status") or "")
            safe_unstarted_step = (
                allow_unstarted_created_steps
                and status == "created"
                and payload.get("started_at") is None
                and payload.get("finished_at") is None
                and payload.get("submission") is None
                and payload.get("result") is None
                and payload.get("error") is None
                and not (payload.get("agent_bindings") or {}).get("by_role")
            )
            if status not in _FINAL_STEP_STATUSES and not safe_unstarted_step:
                raise RepoCompletionModeMigrationError(
                    f"selected checkpoint contains nonterminal Step {payload.get('step_id')}: {status}"
                )
            migrated, field_rewrites = _transform_step_payload(payload)
            _validate_step_payload(migrated)
        elif object_type == "agent" and path.name == "agent.json":
            counts["agent"] += 1
            status = str(payload.get("status") or "")
            if status not in _FINAL_AGENT_STATUSES:
                raise RepoCompletionModeMigrationError(
                    f"selected checkpoint contains active Agent {payload.get('agent_id')}: {status}"
                )
            migrated, field_rewrites = payload, []
        else:
            continue
        if require_new and _contains_legacy_typed_fields(migrated):
            raise RepoCompletionModeMigrationError(
                f"legacy typed field remains in {path.relative_to(scope_dir)}"
            )
        if field_rewrites:
            encoded = _encode_json(migrated)
            rewrites.append(
                _file_rewrite(
                    "scope",
                    snapshot_id,
                    path.relative_to(scope_dir).as_posix(),
                    path.read_bytes(),
                    encoded,
                    field_rewrites,
                )
            )
    return rewrites, counts


def _transform_flow_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[FieldRewrite]]:
    migrated = deepcopy(payload)
    rewrites: list[FieldRewrite] = []
    flow_type = str(migrated.get("flow_type"))
    if flow_type in {"native_repo_preparation", "native_repo_continuation"}:
        _rewrite_completion_at(migrated, ("input", "run_spec"), rewrites)
    elif flow_type in {"native_repo_coordinator", "root_interface_preparation"}:
        _rewrite_completion_at(
            migrated,
            ("input", "run_context", "run_spec"),
            rewrites,
        )
    elif flow_type == "source_index_build":
        _remove_source_index_pair(migrated, ("input",), rewrites)
    elif flow_type == "content_node_task":
        _remove_task_mode(migrated, ("input",), rewrites)
    return migrated, rewrites


def _transform_step_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[FieldRewrite]]:
    migrated = deepcopy(payload)
    rewrites: list[FieldRewrite] = []
    step_type = str(migrated.get("step_type"))
    if step_type == "content_plan_agent_step":
        _remove_task_mode(migrated, ("state", "variables"), rewrites)
        _remove_task_mode(
            migrated,
            ("state", "variables", "context_brief"),
            rewrites,
        )
    elif step_type == "content_progress_checkpoint_step":
        _remove_task_mode(migrated, (), rewrites)
        _remove_task_mode(migrated, ("result",), rewrites)
    elif step_type == "coordinator_agent_step":
        _remove_task_mode(migrated, ("submission",), rewrites)
        _remove_task_mode(migrated, ("result", "content_tasks"), rewrites)
        _rewrite_requests(migrated, ("submission", "requests"), rewrites)
    elif step_type in {
        "dispatch_step",
        "prepare_coordinator_dispatch_step",
        "prepare_native_lifecycle_child_step",
    }:
        request_roots = (
            (("state", "requests"),)
            if step_type == "dispatch_step"
            else (("submission", "requests"),)
        )
        for request_root in request_roots:
            _rewrite_requests(migrated, request_root, rewrites)
    return migrated, rewrites


def _rewrite_requests(
    payload: dict[str, Any],
    path: tuple[str, ...],
    rewrites: list[FieldRewrite],
) -> None:
    requests = _get_path(payload, path)
    if requests is None:
        return
    if not isinstance(requests, list):
        raise RepoCompletionModeMigrationError(
            f"expected request list at {_json_path(path)}"
        )
    for index, request in enumerate(requests):
        if not isinstance(request, dict):
            raise RepoCompletionModeMigrationError("dispatch request is not an object")
        flow_type = str(request.get("flow_type") or "")
        params = request.get("params")
        if not isinstance(params, dict):
            continue
        request_path = (*path, str(index), "params")
        if flow_type == "content_node_task":
            _remove_task_mode_from_object(params, _json_path(request_path), rewrites)
        elif flow_type == "source_index_build":
            _remove_source_index_pair_from_object(
                params,
                _json_path(request_path),
                rewrites,
            )
        elif flow_type in {"native_repo_coordinator", "root_interface_preparation"}:
            run_context = params.get("run_context")
            if isinstance(run_context, dict) and isinstance(run_context.get("run_spec"), dict):
                _rewrite_completion_pair(
                    run_context["run_spec"],
                    json_path=f"{_json_path(request_path)}.run_context.run_spec",
                    field_rewrites=rewrites,
                )


def _rewrite_completion_at(
    payload: dict[str, Any],
    path: tuple[str, ...],
    rewrites: list[FieldRewrite],
) -> None:
    target = _get_path(payload, path)
    if target is None:
        return
    if not isinstance(target, dict):
        raise RepoCompletionModeMigrationError(
            f"expected completion object at {_json_path(path)}"
        )
    _rewrite_completion_pair(
        target,
        json_path=_json_path(path),
        field_rewrites=rewrites,
    )


def _rewrite_completion_pair(
    target: dict[str, Any],
    *,
    json_path: str,
    field_rewrites: list[FieldRewrite],
) -> RepoCompletionMode:
    if "completion_mode" in target:
        if _OLD_COMPLETION_KEYS & set(target):
            raise RepoCompletionModeMigrationError(
                f"completion schema collision at {json_path}"
            )
        return RepoCompletionMode(str(target["completion_mode"]))
    pair = (
        str(target.get("target_proof_availability") or ""),
        str(target.get("work_mode") or ""),
    )
    mode = _OLD_COMPLETION_MAPPING.get(pair)
    if mode is None:
        raise RepoCompletionModeMigrationError(
            f"illegal legacy completion pair at {json_path}: {pair}"
        )
    del target["target_proof_availability"]
    del target["work_mode"]
    target["completion_mode"] = mode.value
    field_rewrites.append(
        FieldRewrite(
            json_path=json_path,
            operation="completion_pair_to_mode",
            old_value={
                "target_proof_availability": pair[0],
                "work_mode": pair[1],
            },
            new_value=mode.value,
        )
    )
    return mode


def _remove_source_index_pair(
    payload: dict[str, Any],
    path: tuple[str, ...],
    rewrites: list[FieldRewrite],
) -> None:
    target = _get_path(payload, path)
    if target is None:
        return
    if not isinstance(target, dict):
        raise RepoCompletionModeMigrationError(
            f"expected SourceIndex input at {_json_path(path)}"
        )
    _remove_source_index_pair_from_object(target, _json_path(path), rewrites)


def _remove_source_index_pair_from_object(
    target: dict[str, Any],
    json_path: str,
    rewrites: list[FieldRewrite],
) -> None:
    present = _OLD_COMPLETION_KEYS & set(target)
    if not present:
        return
    if present != _OLD_COMPLETION_KEYS:
        raise RepoCompletionModeMigrationError(
            f"partial legacy SourceIndex completion pair at {json_path}"
        )
    pair = {
        "target_proof_availability": target.pop("target_proof_availability"),
        "work_mode": target.pop("work_mode"),
    }
    if (str(pair["target_proof_availability"]), str(pair["work_mode"])) not in _OLD_COMPLETION_MAPPING:
        raise RepoCompletionModeMigrationError(
            f"illegal legacy SourceIndex completion pair at {json_path}: {pair}"
        )
    rewrites.append(
        FieldRewrite(
            json_path=json_path,
            operation="remove_source_index_mode",
            old_value=pair,
        )
    )


def _remove_task_mode(
    payload: dict[str, Any],
    path: tuple[str, ...],
    rewrites: list[FieldRewrite],
) -> None:
    target = _get_path(payload, path)
    if target is None:
        return
    if not isinstance(target, dict):
        raise RepoCompletionModeMigrationError(
            f"expected task object at {_json_path(path)}"
        )
    _remove_task_mode_from_object(target, _json_path(path), rewrites)


def _remove_task_mode_from_object(
    target: dict[str, Any],
    json_path: str,
    rewrites: list[FieldRewrite],
) -> None:
    if "task_mode" not in target:
        return
    old = target.pop("task_mode")
    if old not in {"declared", "proved"}:
        raise RepoCompletionModeMigrationError(
            f"unknown legacy Content task_mode at {json_path}: {old!r}"
        )
    rewrites.append(
        FieldRewrite(
            json_path=f"{json_path}.task_mode",
            operation="remove_task_mode",
            old_value=old,
        )
    )


def _rewrite_scope_clone(
    scope_dir: Path,
    *,
    snapshot_id: str,
    expected_scope_id: str,
) -> None:
    manifest = _load_json(scope_dir / "snapshot.json")
    source_artifacts = deepcopy(manifest.get("provider_artifacts"))
    files_root = scope_dir / "files"
    for path in sorted(files_root.rglob("*.json")):
        payload = _load_json(path)
        if payload.get("object_type") == "flow" and path.name == "flow.json":
            migrated, _ = _transform_flow_payload(payload)
            _validate_flow_payload(migrated)
            if migrated != payload:
                _write_json_atomic(path, migrated)
        elif payload.get("object_type") == "step" and path.name == "step.json":
            migrated, _ = _transform_step_payload(payload)
            _validate_step_payload(migrated)
            if migrated != payload:
                _write_json_atomic(path, migrated)
    manifest["snapshot_id"] = snapshot_id
    manifest["scope_id"] = expected_scope_id
    manifest["created_at"] = _utc_now()
    manifest["files"] = _scope_file_entries(files_root)
    if manifest.get("provider_artifacts") != source_artifacts:
        raise RepoCompletionModeMigrationError("provider artifact manifests changed during scope clone")
    _write_json_atomic(scope_dir / "snapshot.json", manifest)
    _preview_scope_archive(
        scope_dir,
        snapshot_id=snapshot_id,
        expected_scope_id=expected_scope_id,
        require_new=True,
    )


def _rewrite_repo_clone(
    root: Path,
    repo_dir: Path,
    *,
    snapshot_id: str,
    runtime_snapshot_id: str,
    source_checkpoint_id: str,
) -> None:
    _, rewrites = _preview_repo_archive(root, source_checkpoint_id, repo_dir)
    for rewrite in rewrites:
        path = repo_dir / rewrite.relative_path
        payload = _load_json(path)
        if path.name == "repo_config.json":
            _rewrite_completion_pair(
                payload,
                json_path="$.repo_config",
                field_rewrites=[],
            )
        elif "releases" in path.parts:
            old_target = str(payload.pop("target_proof_availability"))
            release_checkpoint = str(payload["repo_checkpoint_id"])
            release_config = _load_json(
                _repo_checkpoint_dir(root, release_checkpoint)
                / "files"
                / "lean_constellation"
                / "repo_config.json"
            )
            mode = _OLD_COMPLETION_MAPPING[
                (old_target, str(release_config["work_mode"]))
            ]
            payload["completion_mode"] = mode.value
        _write_json_atomic(path, payload)
    manifest_path = repo_dir / "snapshot.json"
    manifest = _load_json(manifest_path)
    manifest["snapshot_id"] = snapshot_id
    manifest["ark_runtime_snapshot_id"] = runtime_snapshot_id
    manifest["created_at"] = _utc_now()
    manifest["label"] = f"completion-mode-migrated-from-{source_checkpoint_id}"
    manifest["summary"] = (
        f"Immutable completion/tool-surface migration cloned from {source_checkpoint_id}."
    )
    _write_json_atomic(manifest_path, manifest)
    files_manifest_path = repo_dir / str(manifest["files_manifest_relpath"])
    files_manifest = _load_json(files_manifest_path)
    for entry in files_manifest.get("entries", []):
        archive = repo_dir / "files" / str(entry["archive_relpath"])
        entry["file_size"] = archive.stat().st_size
        entry["sha256"] = _sha256_file(archive)
    _write_json_atomic(files_manifest_path, files_manifest)
    _preview_repo_archive(root, snapshot_id, repo_dir, require_new=True)
    _validate_repo_files_manifest(
        root,
        repo_dir,
        SnapshotFilesManifest.model_validate(files_manifest),
    )


def _agent_resource_contract() -> AgentResourceContract:
    specs = build_agent_type_specs()
    validation = validate_agent_resources(specs)
    if not validation.ok:
        issues = ", ".join(f"{item.code}:{item.resource_key}" for item in validation.issues)
        raise RepoCompletionModeMigrationError(
            f"current Agent resources are inconsistent: {issues}"
        )
    application_specs = build_application_tool_specs()
    application_groups = build_application_tool_groups(application_specs)
    application_views = build_application_tool_views(application_groups)
    submit_specs = build_submit_tool_specs()
    submit_groups = build_submit_tool_groups(submit_specs)
    submit_views = build_submit_tool_views(submit_groups)
    surfaces = build_agent_surface_reports(
        specs=specs,
        application_tool_specs=application_specs,
        submit_tool_specs=submit_specs,
        application_groups=application_groups,
        submit_groups=submit_groups,
        application_views=application_views,
        submit_views=submit_views,
    )
    skills = build_skill_specs()
    payload = {
        "agent_types": [item.model_dump(mode="json") for item in specs],
        "surfaces": {
            key: value.model_dump(mode="json")
            for key, value in sorted(surfaces.items())
        },
        "instructions": {
            item.agent_type: render_agent_instruction(item)
            for item in specs
        },
        "skills": {
            key: {
                "description": value.description,
                "body": value.body,
            }
            for key, value in sorted(skills.items())
        },
    }
    return AgentResourceContract(
        digest=_sha256(_encode_json(payload)),
        agent_type_count=len(specs),
        application_tool_count=len(application_specs),
        application_group_count=len(application_groups),
        application_view_count=len(application_views),
        submit_tool_count=len(submit_specs),
        submit_group_count=len(submit_groups),
        submit_view_count=len(submit_views),
    )


def _validate_flow_payload(payload: dict[str, Any]) -> None:
    flow_registry, _ = _registries()
    data = {
        key: value
        for key, value in payload.items()
        if key not in {"schema_version", "object_type"}
    }
    flow_type = str(data["flow_type"])
    flow_cls = flow_registry.get(flow_type)
    data["input"] = flow_registry.parse_input(flow_type, data.get("input"))
    data["state"] = flow_registry.parse_state(flow_type, data["state"])
    data["result"] = flow_registry.parse_result(flow_type, data.get("result"))
    data["error"] = flow_registry.parse_error(flow_type, data.get("error"))
    if "flow_type" not in flow_cls.model_fields:
        data.pop("flow_type", None)
    flow_cls.model_validate(data)


def _validate_step_payload(payload: dict[str, Any]) -> None:
    _, step_registry = _registries()
    data = {
        key: value
        for key, value in payload.items()
        if key not in {"schema_version", "object_type"}
    }
    step_type = str(data["step_type"])
    step_cls = step_registry.get(step_type)
    data["state"] = step_registry.parse_state(step_type, data["state"])
    data["submission"] = step_registry.parse_submission(
        step_type,
        data.get("submission"),
    )
    data["result"] = step_registry.parse_result(step_type, data.get("result"))
    data["error"] = step_registry.parse_error(step_type, data.get("error"))
    if "step_type" not in step_cls.model_fields:
        data.pop("step_type", None)
    step_cls.model_validate(data)


_REGISTRIES: tuple[FlowTypeRegistry, StepTypeRegistry] | None = None


def _registries() -> tuple[FlowTypeRegistry, StepTypeRegistry]:
    global _REGISTRIES
    if _REGISTRIES is None:
        flow_registry = FlowTypeRegistry()
        step_registry = StepTypeRegistry()
        register_lean_flow_step_types(
            flow_registry=flow_registry,
            step_registry=step_registry,
        )
        _REGISTRIES = flow_registry, step_registry
    return _REGISTRIES


def _contains_legacy_typed_fields(payload: dict[str, Any]) -> bool:
    object_type = payload.get("object_type")
    if object_type == "flow":
        migrated, rewrites = _transform_flow_payload(payload)
    elif object_type == "step":
        migrated, rewrites = _transform_step_payload(payload)
    else:
        return False
    return bool(rewrites or migrated != payload)


def _provider_artifact_manifest_hashes(
    root: Path,
    scope_snapshot_ids: dict[str, str],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for scope_id, snapshot_id in sorted(scope_snapshot_ids.items()):
        manifest = _load_json(_scope_snapshot_dir(root, snapshot_id) / "snapshot.json")
        artifacts = manifest.get("provider_artifacts")
        if not isinstance(artifacts, list):
            raise RepoCompletionModeMigrationError("invalid provider artifact manifest")
        result[scope_id] = _sha256(_encode_json(artifacts))
    return result


def _validate_scope_files_manifest(scope_dir: Path, manifest: dict[str, Any]) -> None:
    files_root = scope_dir / "files"
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise RepoCompletionModeMigrationError("scope snapshot files manifest is invalid")
    for entry in entries:
        if not isinstance(entry, dict):
            raise RepoCompletionModeMigrationError("scope snapshot file entry is invalid")
        relative = _safe_relative(str(entry.get("relpath") or ""))
        path = files_root / relative
        if not path.is_file():
            raise RepoCompletionModeMigrationError(f"scope snapshot file is missing: {relative}")
        if path.stat().st_size != int(entry.get("size", -1)):
            raise RepoCompletionModeMigrationError(f"scope snapshot file size mismatch: {relative}")
        if _sha256_file(path) != str(entry.get("sha256") or ""):
            raise RepoCompletionModeMigrationError(f"scope snapshot file hash mismatch: {relative}")


def _validate_repo_files_manifest(
    root: Path,
    repo_dir: Path,
    manifest: SnapshotFilesManifest,
) -> None:
    files_root = repo_dir / "files"
    for entry in manifest.entries:
        archive_relative = _safe_relative(entry.archive_relpath)
        source_relative = _safe_relative(entry.source_relpath)
        archive = files_root / archive_relative
        if not archive.is_file():
            raise RepoCompletionModeMigrationError(
                f"repo checkpoint file is missing: {archive_relative}"
            )
        if archive.stat().st_size != entry.file_size:
            raise RepoCompletionModeMigrationError(
                f"repo checkpoint file size mismatch: {archive_relative}"
            )
        if entry.sha256 is not None and _sha256_file(archive) != entry.sha256:
            raise RepoCompletionModeMigrationError(
                f"repo checkpoint file hash mismatch: {archive_relative}"
            )
        if (root / source_relative).resolve().is_relative_to(root) is False:
            raise RepoCompletionModeMigrationError(
                f"repo checkpoint source path is unsafe: {source_relative}"
            )


def _scope_file_entries(files_root: Path) -> list[dict[str, object]]:
    return [
        {
            "relpath": path.relative_to(files_root).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path in sorted(files_root.rglob("*"))
        if path.is_file()
    ]


def _file_rewrite(
    archive_kind: Literal["repo", "scope"],
    snapshot_id: str,
    relative_path: str,
    old: bytes,
    new: bytes,
    field_rewrites: list[FieldRewrite],
) -> FileRewrite:
    return FileRewrite(
        archive_kind=archive_kind,
        snapshot_id=snapshot_id,
        relative_path=relative_path,
        old_sha256=_sha256(old),
        new_sha256=_sha256(new),
        old_size=len(old),
        new_size=len(new),
        field_rewrites=field_rewrites,
    )


def _get_path(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _json_path(path: tuple[str, ...]) -> str:
    return "$" + "".join(
        f"[{key}]" if key.isdigit() else f".{key}"
        for key in path
    )


def _resolve_repo_root(repo_root: Path) -> Path:
    root = Path(repo_root).expanduser().resolve()
    if not root.is_dir() or not (root / ".lean_constellation").is_dir():
        raise RepoCompletionModeMigrationError(f"invalid repo root: {root}")
    return root


def _resolve_report_dir(root: Path, report_dir: Path) -> Path:
    resolved = Path(report_dir).expanduser().resolve()
    runtime_root = (root / ".agent_runtime").resolve()
    if resolved.is_relative_to(root) or resolved.is_relative_to(runtime_root):
        raise RepoCompletionModeMigrationError(
            "report_dir must be outside repo live truth and snapshot history"
        )
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _repo_checkpoint_dir(root: Path, snapshot_id: str) -> Path:
    return (
        root
        / ".lean_constellation"
        / "snapshots"
        / "repo_checkpoints"
        / _safe_key(snapshot_id, "repo_checkpoint_id")
    )


def _scope_snapshot_dir(root: Path, snapshot_id: str) -> Path:
    return (
        root
        / ".agent_runtime"
        / "snapshots"
        / "scopes"
        / _safe_key(snapshot_id, "scope_snapshot_id")
    )


def _runtime_snapshot_dir(root: Path, snapshot_id: str) -> Path:
    return (
        root
        / ".agent_runtime"
        / "snapshots"
        / "runtime"
        / _safe_key(snapshot_id, "runtime_snapshot_id")
    )


def _safe_key(value: str, field_name: str) -> str:
    normalized = str(value).strip()
    if not _SAFE_KEY.fullmatch(normalized):
        raise RepoCompletionModeMigrationError(
            f"{field_name} is not a safe non-empty key: {value!r}"
        )
    return normalized


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RepoCompletionModeMigrationError(f"unsafe archived relative path: {value!r}")
    return path


def _copy_fresh(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise RepoCompletionModeMigrationError(f"source snapshot is missing: {source}")
    if target.exists():
        raise RepoCompletionModeMigrationError(f"staging path already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)


def _register_scope_snapshot(root: Path, scope_dir: Path) -> None:
    manifest = _load_json(scope_dir / "snapshot.json")
    index = root / ".agent_runtime" / "snapshots" / "scopes" / "index.sqlite"
    with sqlite3.connect(index) as connection:
        connection.execute(
            """
            insert into scope_snapshots(
              snapshot_id, scope_id, scope_key, status, snapshot_relpath, created_at
            ) values (?, ?, ?, ?, ?, ?)
            """,
            (
                manifest["snapshot_id"],
                manifest["scope_id"],
                manifest["scope_key"],
                "created",
                str(scope_dir.relative_to(root / ".agent_runtime")),
                manifest["created_at"],
            ),
        )


def _register_runtime_snapshot(root: Path, runtime_dir: Path) -> None:
    manifest = _load_json(runtime_dir / "snapshot.json")
    index = root / ".agent_runtime" / "snapshots" / "runtime" / "index.sqlite"
    with sqlite3.connect(index) as connection:
        connection.execute(
            """
            insert into runtime_snapshots(
              snapshot_id, status, snapshot_relpath, created_at, scope_count
            ) values (?, ?, ?, ?, ?)
            """,
            (
                manifest["snapshot_id"],
                "created",
                str(runtime_dir.relative_to(root / ".agent_runtime")),
                manifest["created_at"],
                len(manifest["scope_snapshot_ids"]),
            ),
        )


def _delete_scope_index_row(root: Path, snapshot_id: str) -> None:
    index = root / ".agent_runtime" / "snapshots" / "scopes" / "index.sqlite"
    with sqlite3.connect(index) as connection:
        connection.execute(
            "delete from scope_snapshots where snapshot_id = ?",
            (snapshot_id,),
        )


def _delete_runtime_index_row(root: Path, snapshot_id: str) -> None:
    index = root / ".agent_runtime" / "snapshots" / "runtime" / "index.sqlite"
    with sqlite3.connect(index) as connection:
        connection.execute(
            "delete from runtime_snapshots where snapshot_id = ?",
            (snapshot_id,),
        )


def _require_scope_index_row(root: Path, snapshot_id: str, scope_id: str) -> None:
    index = root / ".agent_runtime" / "snapshots" / "scopes" / "index.sqlite"
    with sqlite3.connect(index) as connection:
        row = connection.execute(
            "select scope_id from scope_snapshots where snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
    if row is None or str(row[0]) != scope_id:
        raise RepoCompletionModeMigrationError(
            f"scope snapshot index is missing or inconsistent: {snapshot_id}"
        )


def _require_runtime_index_row(root: Path, snapshot_id: str) -> None:
    index = root / ".agent_runtime" / "snapshots" / "runtime" / "index.sqlite"
    with sqlite3.connect(index) as connection:
        row = connection.execute(
            "select snapshot_id from runtime_snapshots where snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
    if row is None:
        raise RepoCompletionModeMigrationError(
            f"runtime snapshot index is missing: {snapshot_id}"
        )


def _report_from_dict(payload: dict[str, Any]) -> RepoCompletionMigrationReport:
    return RepoCompletionMigrationReport(
        mode=payload["mode"],
        repo_root=payload["repo_root"],
        source_repo_checkpoint_id=payload["source_repo_checkpoint_id"],
        source_runtime_snapshot_id=payload["source_runtime_snapshot_id"],
        source_scope_snapshot_ids=dict(payload["source_scope_snapshot_ids"]),
        repo_config_mapping=dict(payload["repo_config_mapping"]),
        rewrites=[
            FileRewrite(
                **{
                    **item,
                    "field_rewrites": [
                        FieldRewrite(**field_rewrite)
                        for field_rewrite in item.get("field_rewrites", [])
                    ],
                }
            )
            for item in payload["rewrites"]
        ],
        source_manifest_hashes=dict(payload["source_manifest_hashes"]),
        provider_artifact_manifest_hashes=dict(payload["provider_artifact_manifest_hashes"]),
        agent_resources=AgentResourceContract(**payload["agent_resources"]),
        recovery_token=payload["recovery_token"],
        flow_files_to_rewrite=int(payload.get("flow_files_to_rewrite", 0)),
        step_files_to_rewrite=int(payload.get("step_files_to_rewrite", 0)),
        business_files_to_rewrite=int(payload.get("business_files_to_rewrite", 0)),
        new_repo_checkpoint_id=payload.get("new_repo_checkpoint_id"),
        new_runtime_snapshot_id=payload.get("new_runtime_snapshot_id"),
        new_scope_snapshot_ids=dict(payload.get("new_scope_snapshot_ids", {})),
        flow_count=int(payload.get("flow_count", 0)),
        step_count=int(payload.get("step_count", 0)),
        agent_count=int(payload.get("agent_count", 0)),
        provider_artifact_count=int(payload.get("provider_artifact_count", 0)),
        summary=str(payload.get("summary", "")),
    )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RepoCompletionModeMigrationError(f"invalid JSON archive {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RepoCompletionModeMigrationError(f"JSON archive is not an object: {path}")
    return value


def _write_json_atomic(path: Path, payload: Any) -> None:
    data = _encode_json(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.repo-completion-migration.tmp")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    _fsync_dir(path.parent)


def _encode_json(payload: Any) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "AgentResourceContract",
    "FieldRewrite",
    "FileRewrite",
    "RepoCompletionMigrationReport",
    "RepoCompletionModeMigrationError",
    "apply_repo_completion_checkpoint",
    "preview_repo_completion_checkpoint",
    "validate_repo_completion_checkpoint",
]
