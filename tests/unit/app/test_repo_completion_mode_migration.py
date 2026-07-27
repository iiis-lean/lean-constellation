from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from lean_constellation.app.repo_completion_mode_migration import (
    RepoCompletionModeMigrationError,
    _agent_resource_contract,
    _transform_flow_payload,
    _transform_step_payload,
    apply_repo_completion_checkpoint,
    preview_repo_completion_checkpoint,
    validate_repo_completion_checkpoint,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_legacy_checkpoint(tmp_path: Path) -> tuple[Path, str]:
    repo_root = tmp_path / "Repo"
    constellation = repo_root / ".lean_constellation"
    runtime_root = repo_root / ".agent_runtime"
    checkpoint_id = "repo_cp_source"
    runtime_id = "rs_source"
    scope_id = "repo:Repo"
    scope_snapshot_id = "ss_source"

    old_config = {
        "default_requirement_proof_availability": "declared",
        "target_proof_availability": "proved",
        "work_mode": "proved_full_graph",
    }
    live_config = constellation / "repo_config.json"
    _write_json(live_config, old_config)

    checkpoint = (
        constellation
        / "snapshots"
        / "repo_checkpoints"
        / checkpoint_id
    )
    archived_config = (
        checkpoint
        / "files"
        / "lean_constellation"
        / "repo_config.json"
    )
    _write_json(archived_config, old_config)
    _write_json(
        checkpoint / "files_manifest.json",
        {
            "entries": [
                {
                    "source_relpath": ".lean_constellation/repo_config.json",
                    "archive_relpath": "lean_constellation/repo_config.json",
                    "file_size": archived_config.stat().st_size,
                    "sha256": _sha256(archived_config),
                }
            ],
            "excluded_top_level": [".agent_runtime"],
            "summary": "legacy fixture",
        },
    )
    _write_json(
        checkpoint / "snapshot.json",
        {
            "snapshot_id": checkpoint_id,
            "checkpoint_kind": "manual_test_stable_point",
            "label": "legacy",
            "created_at": "2026-01-01T00:00:00Z",
            "repo_root": str(repo_root),
            "ark_runtime_snapshot_id": runtime_id,
            "files_manifest_relpath": "files_manifest.json",
            "summary": "legacy fixture",
        },
    )

    scope = runtime_root / "snapshots" / "scopes" / scope_snapshot_id
    (scope / "files").mkdir(parents=True)
    _write_json(
        scope / "snapshot.json",
        {
            "object_type": "scope_snapshot",
            "schema_version": 3,
            "snapshot_id": scope_snapshot_id,
            "scope_id": scope_id,
            "scope_key": "repo_Repo",
            "created_at": "2026-01-01T00:00:00Z",
            "files": [],
            "provider_artifacts": [],
        },
    )
    runtime = runtime_root / "snapshots" / "runtime" / runtime_id
    _write_json(
        runtime / "snapshot.json",
        {
            "object_type": "runtime_snapshot",
            "schema_version": 1,
            "snapshot_id": runtime_id,
            "created_at": "2026-01-01T00:00:00Z",
            "scope_snapshot_ids": {scope_id: scope_snapshot_id},
        },
    )

    scopes_index = runtime_root / "snapshots" / "scopes" / "index.sqlite"
    with sqlite3.connect(scopes_index) as connection:
        connection.executescript(
            """
            create table scope_snapshots(
              snapshot_id text primary key,
              scope_id text not null,
              scope_key text not null,
              status text not null,
              snapshot_relpath text not null,
              created_at text not null
            );
            insert into scope_snapshots values(
              'ss_source', 'repo:Repo', 'repo_Repo', 'created',
              'snapshots/scopes/ss_source', '2026-01-01T00:00:00Z'
            );
            """
        )
    runtime_index = runtime_root / "snapshots" / "runtime" / "index.sqlite"
    with sqlite3.connect(runtime_index) as connection:
        connection.executescript(
            """
            create table runtime_snapshots(
              snapshot_id text primary key,
              status text not null,
              snapshot_relpath text not null,
              created_at text not null,
              scope_count integer not null
            );
            insert into runtime_snapshots values(
              'rs_source', 'created', 'snapshots/runtime/rs_source',
              '2026-01-01T00:00:00Z', 1
            );
            """
        )
    return repo_root, checkpoint_id


@pytest.mark.parametrize(
    ("target", "work_mode", "expected"),
    [
        ("declared", "declared_interface", "interface_declared"),
        ("declared", "declared_full_graph", "graph_declared"),
        ("proved", "proved_full_graph", "graph_proved"),
    ],
)
def test_flow_transform_maps_only_legal_completion_pairs(
    target: str,
    work_mode: str,
    expected: str,
) -> None:
    payload = {
        "object_type": "flow",
        "flow_type": "native_repo_preparation",
        "input": {
            "run_spec": {
                "target_proof_availability": target,
                "work_mode": work_mode,
            }
        },
    }

    migrated, rewrites = _transform_flow_payload(payload)

    assert migrated["input"]["run_spec"] == {"completion_mode": expected}
    assert len(rewrites) == 1
    assert rewrites[0].operation == "completion_pair_to_mode"


def test_flow_transform_rejects_illegal_completion_pair() -> None:
    payload = {
        "object_type": "flow",
        "flow_type": "native_repo_preparation",
        "input": {
            "run_spec": {
                "target_proof_availability": "proved",
                "work_mode": "declared_full_graph",
            }
        },
    }

    with pytest.raises(RepoCompletionModeMigrationError, match="illegal legacy completion pair"):
        _transform_flow_payload(payload)


def test_typed_transform_removes_content_and_source_index_compatibility() -> None:
    content_flow, content_rewrites = _transform_flow_payload(
        {
            "object_type": "flow",
            "flow_type": "content_node_task",
            "input": {"task_mode": "proved", "node_path": "Main"},
        }
    )
    source_flow, source_rewrites = _transform_flow_payload(
        {
            "object_type": "flow",
            "flow_type": "source_index_build",
            "input": {
                "target_proof_availability": "proved",
                "work_mode": "proved_full_graph",
                "repo_root": "/repo",
            },
        }
    )
    plan_step, plan_rewrites = _transform_step_payload(
        {
            "object_type": "step",
            "step_type": "content_plan_agent_step",
            "state": {
                "variables": {
                    "task_mode": "proved",
                    "context_brief": {"task_mode": "proved"},
                }
            },
        }
    )

    assert content_flow["input"] == {"node_path": "Main"}
    assert source_flow["input"] == {"repo_root": "/repo"}
    assert plan_step["state"]["variables"] == {"context_brief": {}}
    assert [item.operation for item in content_rewrites] == ["remove_task_mode"]
    assert [item.operation for item in source_rewrites] == ["remove_source_index_mode"]
    assert [item.operation for item in plan_rewrites] == [
        "remove_task_mode",
        "remove_task_mode",
    ]


def test_agent_resource_contract_covers_current_tool_surface() -> None:
    resources = _agent_resource_contract()

    assert resources.agent_type_count == 20
    assert resources.application_tool_count == 252
    assert resources.application_group_count == 99
    assert resources.application_view_count == 20
    assert resources.submit_tool_count == 29
    assert resources.submit_group_count == 16
    assert resources.submit_view_count == 14
    assert len(resources.digest) == 64


def test_checkpoint_migration_clones_source_and_is_idempotent(tmp_path: Path) -> None:
    repo_root, checkpoint_id = _build_legacy_checkpoint(tmp_path)
    report_dir = tmp_path / "reports"
    source_checkpoint = (
        repo_root
        / ".lean_constellation"
        / "snapshots"
        / "repo_checkpoints"
        / checkpoint_id
    )
    source_hashes = {
        path.relative_to(source_checkpoint): _sha256(path)
        for path in source_checkpoint.rglob("*")
        if path.is_file()
    }

    preview = preview_repo_completion_checkpoint(
        repo_root,
        checkpoint_id=checkpoint_id,
    )
    applied = apply_repo_completion_checkpoint(
        repo_root,
        checkpoint_id=checkpoint_id,
        expected_token=preview.recovery_token,
        report_dir=report_dir,
    )
    repeated = apply_repo_completion_checkpoint(
        repo_root,
        checkpoint_id=checkpoint_id,
        expected_token=preview.recovery_token,
        report_dir=report_dir,
    )
    validated = validate_repo_completion_checkpoint(
        repo_root,
        checkpoint_id=str(applied.new_repo_checkpoint_id),
        expected_source_checkpoint_id=checkpoint_id,
    )

    assert preview.repo_config_mapping == {
        "old_target": "proved",
        "old_work_mode": "proved_full_graph",
        "new_completion_mode": "graph_proved",
    }
    assert len(preview.rewrites) == 1
    assert applied.new_repo_checkpoint_id != checkpoint_id
    assert applied.new_runtime_snapshot_id != "rs_source"
    assert repeated.new_repo_checkpoint_id == applied.new_repo_checkpoint_id
    assert validated.repo_config_mapping == {"new_completion_mode": "graph_proved"}
    assert {
        path.relative_to(source_checkpoint): _sha256(path)
        for path in source_checkpoint.rglob("*")
        if path.is_file()
    } == source_hashes
    migrated_config = json.loads(
        (
            repo_root
            / ".lean_constellation"
            / "snapshots"
            / "repo_checkpoints"
            / str(applied.new_repo_checkpoint_id)
            / "files"
            / "lean_constellation"
            / "repo_config.json"
        ).read_text(encoding="utf-8")
    )
    assert migrated_config == {
        "completion_mode": "graph_proved",
        "default_requirement_proof_availability": "declared",
    }


def test_checkpoint_apply_rejects_stale_token(tmp_path: Path) -> None:
    repo_root, checkpoint_id = _build_legacy_checkpoint(tmp_path)

    with pytest.raises(RepoCompletionModeMigrationError, match="token mismatch"):
        apply_repo_completion_checkpoint(
            repo_root,
            checkpoint_id=checkpoint_id,
            expected_token="0" * 64,
            report_dir=tmp_path / "reports",
        )
