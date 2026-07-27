from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent_runtime_kit.flow.models import FlowPosition, FlowStatus

from lean_constellation.app.decl_round_closeout_checkpoint_migration import (
    DeclRoundCloseoutCheckpointMigrationError,
    apply_decl_round_closeout_checkpoint,
    preview_decl_round_closeout_checkpoint,
    validate_decl_round_closeout_checkpoint,
)
from lean_constellation.flows.content_node_task.decl_round.flow import (
    DeclGraphRoundFlow,
    DeclGraphRoundInput,
    DeclGraphRoundResult,
    DeclGraphRoundState,
)
from lean_constellation.flows.content_node_task.flows import (
    ContentNodeTaskFlow,
    ContentNodeTaskInput,
    ContentNodeTaskState,
)
from lean_constellation.services.decl_graph.models import (
    DeclGraphRound,
    DeclRoundResultKind,
    DeclRoundStatus,
)
from tests.unit.app.test_repo_completion_mode_migration import (
    _build_legacy_checkpoint,
    _sha256,
    _write_json,
)


_TIMESTAMP = "2026-01-01T00:00:00Z"


def _flow_payload(flow) -> dict[str, object]:  # noqa: ANN001
    payload = flow.model_dump(mode="json")
    payload["object_type"] = "flow"
    payload["schema_version"] = 1
    payload["flow_type"] = flow.flow_type
    return payload


def _build_closeout_checkpoint(tmp_path: Path) -> tuple[Path, str]:
    repo_root, checkpoint_id = _build_legacy_checkpoint(tmp_path)
    checkpoint = (
        repo_root
        / ".lean_constellation"
        / "snapshots"
        / "repo_checkpoints"
        / checkpoint_id
    )
    current_config = {
        "completion_mode": "graph_proved",
        "default_requirement_proof_availability": "declared",
    }
    live_config = repo_root / ".lean_constellation" / "repo_config.json"
    archived_config = (
        checkpoint / "files" / "lean_constellation" / "repo_config.json"
    )
    _write_json(live_config, current_config)
    _write_json(archived_config, current_config)

    rounds = [
        DeclGraphRound(
            round_id="round_historical",
            node_path="Main.Topic",
            strategy_id="strategy_fixture",
            round_index=1,
            status=DeclRoundStatus.COMMITTED,
            objective="Historical round.",
            summary="Historical round complete.",
            result_kind=DeclRoundResultKind.SUCCESS,
            result_reason=None,
            created_at=_TIMESTAMP,
            started_at=_TIMESTAMP,
            committed_at=_TIMESTAMP,
        ),
        DeclGraphRound(
            round_id="round_pending",
            node_path="Main.Topic",
            strategy_id="strategy_fixture",
            round_index=2,
            status=DeclRoundStatus.COMMITTED,
            objective="Pending callback round.",
            summary="Pending callback round complete.",
            result_kind=DeclRoundResultKind.SUCCESS,
            result_reason=None,
            created_at=_TIMESTAMP,
            started_at=_TIMESTAMP,
            committed_at=_TIMESTAMP,
        ),
    ]
    round_paths: list[Path] = []
    for round_record in rounds:
        path = (
            checkpoint
            / "files"
            / "lean_constellation"
            / "nodes"
            / "node_topic"
            / "decl_graph"
            / "rounds"
            / f"{round_record.round_id}.json"
        )
        payload = round_record.model_dump(
            mode="json",
            exclude={
                "execution_result_kind",
                "execution_reason",
                "execution_completed_at",
                "plan_closeout_acknowledged_at",
                "plan_closeout_acknowledged_by",
            },
        )
        _write_json(path, payload)
        round_paths.append(path)

    files_manifest_path = checkpoint / "files_manifest.json"
    files_manifest = json.loads(files_manifest_path.read_text(encoding="utf-8"))
    config_entry = files_manifest["entries"][0]
    config_entry["file_size"] = archived_config.stat().st_size
    config_entry["sha256"] = _sha256(archived_config)
    for path in round_paths:
        archive_relpath = path.relative_to(checkpoint / "files").as_posix()
        files_manifest["entries"].append(
            {
                "source_relpath": f".lean_constellation/{archive_relpath.removeprefix('lean_constellation/')}",
                "archive_relpath": archive_relpath,
                "file_size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    _write_json(files_manifest_path, files_manifest)

    scope = (
        repo_root
        / ".agent_runtime"
        / "snapshots"
        / "scopes"
        / "ss_source"
    )
    files_root = scope / "files"
    content_flow = ContentNodeTaskFlow(
        flow_id="f_content",
        scope_id="repo:Repo",
        status=FlowStatus.RUNNING,
        input=ContentNodeTaskInput(
            summary="Fixture content flow.",
            repo_key="Repo",
            repo_path=str(repo_root),
            node_path="Main.Topic",
            contract_version=1,
        ),
        state=ContentNodeTaskState(
            position=FlowPosition(phase="callback_plan_agent"),
            waiting_child_kind="decl_graph_round",
            completed_child_flow_id="f_round",
            completed_child_outcome="completed",
        ),
    )
    child_flow = DeclGraphRoundFlow(
        flow_id="f_round",
        scope_id="repo:Repo",
        status=FlowStatus.COMPLETED,
        input=DeclGraphRoundInput(
            summary="Fixture Decl round.",
            repo_key="Repo",
            repo_path=str(repo_root),
            node_path="Main.Topic",
            strategy_id="strategy_fixture",
            round_id="round_pending",
            round_index=2,
        ),
        state=DeclGraphRoundState(
            position=FlowPosition(phase="completed"),
        ),
        result=DeclGraphRoundResult(
            summary="Fixture round completed.",
            outcome="completed",
            repo_key="Repo",
            node_path="Main.Topic",
            strategy_id="strategy_fixture",
            round_id="round_pending",
            round_index=2,
        ),
        finished_at=_TIMESTAMP,
    )
    flow_paths: list[Path] = []
    for flow in (content_flow, child_flow):
        path = (
            files_root
            / "scopes"
            / "repo_Repo"
            / "flows"
            / flow.flow_id
            / "flow.json"
        )
        _write_json(path, _flow_payload(flow))
        flow_paths.append(path)
    scope_manifest_path = scope / "snapshot.json"
    scope_manifest = json.loads(scope_manifest_path.read_text(encoding="utf-8"))
    scope_manifest["files"] = [
        {
            "relpath": path.relative_to(files_root).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in flow_paths
    ]
    _write_json(scope_manifest_path, scope_manifest)
    return repo_root, checkpoint_id


def test_decl_round_closeout_checkpoint_migration_clones_and_validates(
    tmp_path: Path,
) -> None:
    repo_root, checkpoint_id = _build_closeout_checkpoint(tmp_path)
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

    preview = preview_decl_round_closeout_checkpoint(
        repo_root,
        checkpoint_id=checkpoint_id,
    )
    applied = apply_decl_round_closeout_checkpoint(
        repo_root,
        checkpoint_id=checkpoint_id,
        expected_token=preview.recovery_token,
        report_dir=tmp_path / "reports",
    )
    repeated = apply_decl_round_closeout_checkpoint(
        repo_root,
        checkpoint_id=checkpoint_id,
        expected_token=preview.recovery_token,
        report_dir=tmp_path / "reports",
    )
    validated = validate_decl_round_closeout_checkpoint(
        repo_root,
        checkpoint_id=str(applied.new_repo_checkpoint_id),
        expected_source_checkpoint_id=checkpoint_id,
    )

    assert preview.round_count == 2
    assert len(preview.rewrites) == 2
    assert [item.round_id for item in preview.pending_rounds] == ["round_pending"]
    assert repeated.new_repo_checkpoint_id == applied.new_repo_checkpoint_id
    assert validated.round_count == 2
    assert [item.round_id for item in validated.pending_rounds] == ["round_pending"]
    assert {
        path.relative_to(source_checkpoint): _sha256(path)
        for path in source_checkpoint.rglob("*")
        if path.is_file()
    } == source_hashes

    migrated_round_root = (
        repo_root
        / ".lean_constellation"
        / "snapshots"
        / "repo_checkpoints"
        / str(applied.new_repo_checkpoint_id)
        / "files"
        / "lean_constellation"
        / "nodes"
        / "node_topic"
        / "decl_graph"
        / "rounds"
    )
    historical = json.loads(
        (migrated_round_root / "round_historical.json").read_text(encoding="utf-8")
    )
    pending = json.loads(
        (migrated_round_root / "round_pending.json").read_text(encoding="utf-8")
    )
    assert historical["execution_result_kind"] == "success"
    assert historical["plan_closeout_acknowledged_at"] == _TIMESTAMP
    assert historical["plan_closeout_acknowledged_by"] == (
        "checkpoint_migration:decl_closeout_v1"
    )
    assert pending["execution_result_kind"] == "success"
    assert pending["plan_closeout_acknowledged_at"] is None
    assert pending["plan_closeout_acknowledged_by"] is None


def test_decl_round_closeout_apply_rejects_stale_token(tmp_path: Path) -> None:
    repo_root, checkpoint_id = _build_closeout_checkpoint(tmp_path)

    with pytest.raises(
        DeclRoundCloseoutCheckpointMigrationError,
        match="token mismatch",
    ):
        apply_decl_round_closeout_checkpoint(
            repo_root,
            checkpoint_id=checkpoint_id,
            expected_token="0" * 64,
            report_dir=tmp_path / "reports",
        )
