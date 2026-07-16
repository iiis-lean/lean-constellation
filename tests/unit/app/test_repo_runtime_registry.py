from __future__ import annotations

from pathlib import Path
from functools import partial
from types import SimpleNamespace

import anyio
import pytest

from agent_runtime_kit.flow import SchedulerRunBudget
from lean_constellation.app.scheduler_loop import run_registry_scheduler_loop
from lean_constellation.app import LeanAppConfig, RepoRuntimeRegistry
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


def test_workspace_runtime_forwards_workspace_config_without_runtime_history(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = _make_repo(workspace, "MainRepo")
    config = LeanAppConfig(workspace_root=workspace, materialize_agent_homes=False)
    registry = RepoRuntimeRegistry(config)

    runtime = registry.workspace_runtime()

    assert runtime.repo_workspace.workspace_config == config.workspace_config
    assert not (repo_root / ".agent_runtime").exists()


def test_runtime_history_requires_a_persisted_file(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    registry = RepoRuntimeRegistry(LeanAppConfig(workspace_root=workspace, materialize_agent_homes=False))
    record = registry.discover_repo("MainRepo").value
    assert record is not None
    record.runtime_root.mkdir()

    assert not registry.runtime_history_exists(record)
    (record.runtime_root / "flows").mkdir()
    assert not registry.runtime_history_exists(record)
    (record.runtime_root / "flows" / "flow.json").write_text("{}", encoding="utf-8")
    assert registry.runtime_history_exists(record)


def test_registry_initializes_business_truth_before_loading_runtime(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    config = LeanAppConfig(
        workspace_root=workspace,
        materialize_agent_homes=False,
        server_start_paused=True,
    )
    registry = RepoRuntimeRegistry(config)

    loaded = registry.initialize_and_load("MainRepo", refresh_homes=False)

    assert loaded.ok and loaded.value is not None
    repo_root = workspace / "MainRepo"
    assert (repo_root / ".lean_constellation" / "repo.json").is_file()
    assert (repo_root / ".agent_runtime").is_dir()
    assert registry.get_status("MainRepo").value.loaded is True


@pytest.mark.parametrize(
    ("issue", "staging_paths"),
    [
        ("release_parent_missing", []),
        ("release_checkpoint_manifest_invalid", []),
        ("release_prepared_without_publication_commit", ["prepared-interrupted"]),
        ("release_requirement_notification_pending", []),
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


def test_repo_runtime_registry_bounded_resume_is_atomic_and_visible_in_status(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    registry = RepoRuntimeRegistry(LeanAppConfig(
        workspace_root=workspace,
        materialize_agent_homes=False,
        server_start_paused=True,
    ))
    loaded = registry.get_or_load("MainRepo")
    assert loaded.ok and loaded.value is not None
    controller = loaded.value.ark.pause_controller
    scheduler = loaded.value.ark.schedule_service
    events: list[str] = []
    original_configure = scheduler.configure_run_budget
    original_rebuild = scheduler.rebuild_candidate_queues
    original_resume = controller.resume

    def configure(budget):  # noqa: ANN001
        assert controller.is_paused(None)
        events.append("configure")
        return original_configure(budget)

    def rebuild(*, scope_id=None):  # noqa: ANN001
        assert controller.is_paused(None)
        events.append("rebuild")
        return original_rebuild(scope_id=scope_id)

    def resume(scope_id=None):  # noqa: ANN001
        events.append("resume")
        return original_resume(scope_id)

    monkeypatch.setattr(scheduler, "configure_run_budget", configure)
    monkeypatch.setattr(scheduler, "rebuild_candidate_queues", rebuild)
    monkeypatch.setattr(controller, "resume", resume)

    resumed = registry.resume(
        "MainRepo",
        budget=SchedulerRunBudget(flow_advances=1, step_starts=0),
    )

    assert resumed.ok and resumed.value is not None
    assert events == ["configure", "rebuild", "resume"]
    assert resumed.value.state == "active"
    assert resumed.value.run_control is not None
    assert resumed.value.run_control.mode == "bounded"
    assert resumed.value.run_control.remaining_flow_advances == 1


def test_repo_runtime_registry_bounded_resume_fails_closed_when_active_or_busy(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    registry = RepoRuntimeRegistry(LeanAppConfig(
        workspace_root=workspace,
        materialize_agent_homes=False,
        server_start_paused=True,
    ))
    loaded = registry.get_or_load("MainRepo")
    assert loaded.ok and loaded.value is not None
    budget = SchedulerRunBudget(flow_advances=0, step_starts=1)
    controller = loaded.value.ark.pause_controller
    scheduler = loaded.value.ark.schedule_service
    controller.resume(None)

    active = registry.resume("MainRepo", budget=budget)

    assert not active.ok
    assert active.issues[0].kind == "bounded_resume_requires_global_pause"
    assert scheduler.get_run_control_view().requested_step_starts is None
    controller.pause(None)
    monkeypatch.setattr(loaded.value.ark.step_service, "list_running_steps", lambda: ["step-1"])

    busy = registry.resume("MainRepo", budget=budget)

    assert not busy.ok
    assert busy.issues[0].kind == "bounded_resume_runtime_busy"
    assert scheduler.get_run_control_view().requested_step_starts is None
    assert controller.is_paused(None)


def test_registry_scheduler_loop_syncs_auto_paused_repo_state(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    registry = RepoRuntimeRegistry(LeanAppConfig(
        workspace_root=workspace,
        materialize_agent_homes=False,
        server_start_paused=True,
    ))
    loaded = registry.get_or_load("MainRepo")
    assert loaded.ok and loaded.value is not None
    resumed = registry.resume(
        "MainRepo",
        budget=SchedulerRunBudget(flow_advances=0, step_starts=1),
    )
    assert resumed.ok

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
            for _ in range(50):
                status = registry.get_status("MainRepo")
                if status.ok and status.value is not None and status.value.state == "paused":
                    break
                await anyio.sleep(0.001)
            task_group.cancel_scope.cancel()

    anyio.run(run_loop_briefly)

    status = registry.get_status("MainRepo")
    assert status.ok and status.value is not None
    assert status.value.state == "paused"
    assert status.value.paused is True
    assert status.value.run_control is not None
    assert status.value.run_control.pause_reason == "no_runnable_candidate"


def test_repo_runtime_registry_reload_does_not_persist_bounded_lease(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    registry = RepoRuntimeRegistry(LeanAppConfig(
        workspace_root=workspace,
        materialize_agent_homes=False,
        server_start_paused=True,
    ))
    loaded = registry.get_or_load("MainRepo")
    assert loaded.ok and loaded.value is not None
    assert registry.resume(
        "MainRepo",
        budget=SchedulerRunBudget(flow_advances=2, step_starts=1),
    ).ok
    assert registry.pause("MainRepo").ok
    assert registry.unload("MainRepo").ok

    reloaded = registry.get_or_load("MainRepo")

    assert reloaded.ok and reloaded.value is not None
    assert reloaded.value.ark.pause_controller.is_paused(None)
    view = reloaded.value.ark.schedule_service.get_run_control_view()
    assert view.mode == "paused"
    assert view.requested_flow_advances is None
    assert view.remaining_step_starts is None


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
