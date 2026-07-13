from __future__ import annotations

from pathlib import Path
from functools import partial
from types import SimpleNamespace

import anyio
import pytest

from lean_constellation.app.scheduler_loop import run_registry_scheduler_loop
from lean_constellation.app import LeanAppConfig, RepoRuntimeRegistry
from lean_constellation.domain.repo import (
    RepoFormat,
    RepoFormatState,
    RepoPublicationState,
    RepoPublicationStatus,
)
from lean_constellation.services.validation_snapshot import RepoReleaseStorageAuditView


def _make_repo(workspace: Path, name: str) -> Path:
    repo_root = workspace / name
    (repo_root / ".lean_constellation").mkdir(parents=True)
    return repo_root


def test_repo_runtime_registry_rejects_path_like_repo_keys(tmp_path) -> None:
    config = LeanAppConfig(workspace_root=tmp_path / "workspace", materialize_agent_homes=False)
    registry = RepoRuntimeRegistry(config)

    assert not registry.normalize_repo_key("").ok
    assert not registry.normalize_repo_key("../repo").ok
    assert not registry.normalize_repo_key("nested/repo").ok
    assert registry.normalize_repo_key("MainRepo").value == "MainRepo"


def test_repo_runtime_registry_discovers_initialized_workspace_repos(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    (workspace / "NotRepo").mkdir()
    config = LeanAppConfig(workspace_root=workspace, materialize_agent_homes=False)
    registry = RepoRuntimeRegistry(config)

    listed = registry.list_status()

    assert listed.ok and listed.value is not None
    assert [repo.repo_key for repo in listed.value.repos] == ["MainRepo"]
    assert listed.value.repos[0].runtime_root == str(workspace / "MainRepo" / ".agent_runtime")
    assert listed.value.repos[0].loaded is False


def test_repo_runtime_registry_loads_repo_local_runtime_root(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = _make_repo(workspace, "MainRepo")
    config = LeanAppConfig(
        workspace_root=workspace,
        materialize_agent_homes=False,
        server_start_paused=True,
    )
    registry = RepoRuntimeRegistry(config)

    loaded = registry.get_or_load("MainRepo")
    status = registry.list_status(discover=False)

    assert loaded.ok and loaded.value is not None
    assert (repo_root / ".agent_runtime").is_dir()
    assert loaded.value.ark.pause_controller is not None
    assert loaded.value.ark.pause_controller.is_paused()
    assert status.ok and status.value is not None
    assert status.value.repos[0].state == "paused"
    assert status.value.repos[0].loaded is True
    assert loaded.value.ark.flow_service.runtime_root == repo_root / ".agent_runtime"


def test_repo_runtime_registry_startup_release_audit_is_read_only_and_reports_legacy_native(
    tmp_path,
) -> None:
    workspace = tmp_path / "workspace"
    repo_root = _make_repo(workspace, "LegacyNative")
    format_path = repo_root / ".lean_constellation" / "repo_format.json"
    publication_path = repo_root / ".lean_constellation" / "repo_publication.json"
    format_path.write_text(
        RepoFormatState(repo_format=RepoFormat.NATIVE, reason="legacy").model_dump_json(),
        encoding="utf-8",
    )
    publication_path.write_text(
        RepoPublicationState(status=RepoPublicationStatus.STABLE).model_dump_json(),
        encoding="utf-8",
    )
    before = publication_path.read_bytes()
    registry = RepoRuntimeRegistry(LeanAppConfig(
        workspace_root=workspace,
        materialize_agent_homes=False,
        server_start_paused=True,
    ))

    loaded = registry.get_or_load("LegacyNative")
    status = registry.get_status("LegacyNative")

    assert loaded.ok and status.ok and status.value is not None
    assert "legacy_native_release_adoption_required" in status.value.startup_warnings
    assert publication_path.read_bytes() == before


@pytest.mark.parametrize(
    ("issue", "staging_paths"),
    [
        ("release_parent_missing", []),
        ("release_checkpoint_manifest_invalid", []),
        ("release_prepared_without_publication_commit", ["prepared-interrupted"]),
        ("release_requirement_notification_pending", []),
        ("legacy_native_release_adoption_required", []),
    ],
)
def test_startup_release_audit_classifies_findings_without_writing_or_cleanup(
    tmp_path, monkeypatch, issue: str, staging_paths: list[str]
) -> None:  # noqa: ANN001
    workspace = tmp_path / "workspace"
    repo_root = _make_repo(workspace, "AuditRepo")
    sentinel = repo_root / ".lean_constellation" / "sentinel.json"
    sentinel.write_text('{"truth":"unchanged"}\n', encoding="utf-8")
    registry = RepoRuntimeRegistry(LeanAppConfig(
        workspace_root=workspace,
        materialize_agent_homes=False,
        server_start_paused=True,
    ))
    loaded = registry.get_or_load("AuditRepo")
    assert loaded.ok and loaded.value is not None
    record = registry._records["AuditRepo"]
    before = {path.relative_to(repo_root): path.read_bytes() for path in repo_root.rglob("*") if path.is_file()}
    audit = RepoReleaseStorageAuditView(
        passed=False,
        staging_paths=staging_paths,
        issues=[issue],
        audit_digest="a" * 64,
        summary="Injected startup classification.",
    )
    monkeypatch.setattr(
        loaded.value.validation_snapshot,
        "audit_repo_release_storage",
        lambda _root: loaded.value.foundation.ok(audit),
    )
    monkeypatch.setattr(
        loaded.value.validation_snapshot,
        "cleanup_repo_release_orphans",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("startup must not clean")),
    )

    registry._audit_release_state(record)

    assert issue in record.startup_warnings
    if staging_paths:
        assert "release_orphan_staging: 1 staging path(s) require explicit cleanup" in record.startup_warnings
    after = {path.relative_to(repo_root): path.read_bytes() for path in repo_root.rglob("*") if path.is_file()}
    assert after == before


def test_repo_runtime_registry_resume_rebuilds_and_marks_active(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    config = LeanAppConfig(workspace_root=workspace, materialize_agent_homes=False, server_start_paused=True)
    registry = RepoRuntimeRegistry(config)
    loaded = registry.get_or_load("MainRepo")
    assert loaded.ok and loaded.value is not None

    resumed = registry.resume("MainRepo")

    assert resumed.ok and resumed.value is not None
    assert resumed.value.state == "active"
    assert loaded.value.ark.pause_controller is not None
    assert not loaded.value.ark.pause_controller.is_paused()


def test_repo_runtime_registry_unload_requires_stable_runtime(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    config = LeanAppConfig(workspace_root=workspace, materialize_agent_homes=False)
    registry = RepoRuntimeRegistry(config)
    loaded = registry.get_or_load("MainRepo")
    assert loaded.ok and loaded.value is not None

    monkeypatch.setattr(loaded.value.ark.step_service, "list_running_steps", lambda: ["step-1"])

    blocked = registry.unload("MainRepo")
    forced = registry.unload("MainRepo", require_stable=False)

    assert not blocked.ok
    assert blocked.issues[0].kind == "repo_runtime_not_stable"
    assert forced.ok and forced.value is not None
    assert forced.value.loaded is False
    assert forced.value.state == "unloaded"


def test_repo_runtime_registry_repo_mcp_base_url_is_repo_prefixed(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    config = LeanAppConfig(
        workspace_root=workspace,
        materialize_agent_homes=False,
        admin_http_port=9123,
    )
    registry = RepoRuntimeRegistry(config)

    assert registry.repo_mcp_http_base_url("MainRepo") == "http://127.0.0.1:9123/repos/MainRepo"


def test_registry_scheduler_loop_ticks_active_loaded_repos(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    config = LeanAppConfig(workspace_root=workspace, materialize_agent_homes=False, server_start_paused=False)
    registry = RepoRuntimeRegistry(config)
    loaded = registry.get_or_load("MainRepo")
    assert loaded.ok and loaded.value is not None
    calls = {"count": 0}

    def schedule_ready():
        calls["count"] += 1
        return SimpleNamespace(advanced_flow_ids=[], started_step_ids=[], model_dump=lambda mode="json": {"ok": True})

    monkeypatch.setattr(loaded.value.ark.schedule_service, "schedule_ready", schedule_ready)

    async def run_loop_briefly() -> None:
        state: dict[str, object] = {}
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(
                partial(
                    run_registry_scheduler_loop,
                    registry,
                    tick_interval_s=0.001,
                    idle_interval_s=0.001,
                    error_interval_s=0.001,
                    state=state,
                )
            )
            await anyio.sleep(0.01)
            task_group.cancel_scope.cancel()
        assert state["running"] is False

    anyio.run(run_loop_briefly)

    assert calls["count"] >= 1


def test_registry_scheduler_loop_marks_failed_repo_without_stopping_other_repos(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "RepoA")
    _make_repo(workspace, "RepoB")
    config = LeanAppConfig(workspace_root=workspace, materialize_agent_homes=False, server_start_paused=False)
    registry = RepoRuntimeRegistry(config)
    repo_a = registry.get_or_load("RepoA")
    repo_b = registry.get_or_load("RepoB")
    assert repo_a.ok and repo_a.value is not None
    assert repo_b.ok and repo_b.value is not None
    calls = {"repo_b": 0}

    def fail_schedule():
        raise RuntimeError("boom")

    def repo_b_schedule_ready():
        calls["repo_b"] += 1
        return SimpleNamespace(advanced_flow_ids=[], started_step_ids=[], model_dump=lambda mode="json": {"ok": True})

    monkeypatch.setattr(repo_a.value.ark.schedule_service, "schedule_ready", fail_schedule)
    monkeypatch.setattr(repo_b.value.ark.schedule_service, "schedule_ready", repo_b_schedule_ready)

    async def run_loop_briefly() -> None:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(
                partial(
                    run_registry_scheduler_loop,
                    registry,
                    tick_interval_s=0.001,
                    idle_interval_s=0.001,
                    error_interval_s=0.001,
                )
            )
            await anyio.sleep(0.02)
            task_group.cancel_scope.cancel()

    anyio.run(run_loop_briefly)

    status_a = registry.get_status("RepoA")
    status_b = registry.get_status("RepoB")
    assert status_a.ok and status_a.value is not None
    assert status_a.value.state == "failed"
    assert status_a.value.last_error == "boom"
    assert status_b.ok and status_b.value is not None
    assert status_b.value.state == "active"
    assert calls["repo_b"] >= 1
