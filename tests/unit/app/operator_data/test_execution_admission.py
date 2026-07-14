from __future__ import annotations

from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from agent_runtime_kit.flow.models import FlowStatus
from lean_constellation.app.operator_data.execution import OperatorExecutionService
from lean_constellation.services.repo_workspace.repo_lifecycle_lock import RepoLifecycleLockBusyError

from tests.unit.app.operator_data._helpers import (
    MUTATION_OPERATION,
    READ_OPERATION,
    SELF_MANAGED_OPERATION,
    make_registry,
    make_repo,
)


def _write_action(path: Path):
    def action(ctx):  # noqa: ANN001
        path.write_text(ctx.admission.management_state, encoding="utf-8")
        return ctx.runtime.foundation.ok(ctx.admission)

    return action


def test_no_history_mutation_uses_data_services_without_creating_runtime(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = make_repo(workspace)
    executor = OperatorExecutionService(make_registry(workspace))
    sentinel = repo_root / "result.txt"

    result = executor.execute("MainRepo", MUTATION_OPERATION, _write_action(sentinel))

    assert result.ok and result.value is not None
    assert result.value.management_state == "data_only"
    assert sentinel.read_text(encoding="utf-8") == "data_only"
    assert not (repo_root / ".agent_runtime").exists()


def test_read_uses_workspace_runtime_without_creating_runtime(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = make_repo(workspace)
    registry = make_registry(workspace)
    executor = OperatorExecutionService(registry)

    result = executor.execute(
        "MainRepo",
        READ_OPERATION,
        lambda ctx: ctx.runtime.foundation.ok(ctx.runtime is registry.workspace_runtime()),
    )

    assert result.ok and result.value is True
    assert not (repo_root / ".agent_runtime").exists()


def test_history_unloaded_mutation_fails_closed_without_truth_change(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = make_repo(workspace)
    history = repo_root / ".agent_runtime" / "flows" / "flow.json"
    history.parent.mkdir(parents=True)
    history.write_text("{}", encoding="utf-8")
    executor = OperatorExecutionService(make_registry(workspace))
    sentinel = repo_root / "result.txt"

    result = executor.execute("MainRepo", MUTATION_OPERATION, _write_action(sentinel))

    assert not result.ok
    assert result.issues[0].kind == "operator_repo_runtime_history_unloaded"
    assert not sentinel.exists()


def test_executor_rejects_path_like_repo_key_before_discovery(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    make_repo(workspace)
    result = OperatorExecutionService(make_registry(workspace)).execute(
        "../MainRepo",
        READ_OPERATION,
        lambda ctx: ctx.runtime.foundation.ok(True),
    )

    assert not result.ok
    assert result.issues[0].kind == "operator_repo_key_invalid"


def test_prepare_management_atomically_loads_paused_runtime(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    make_repo(workspace)
    registry = make_registry(workspace, server_start_paused=False)
    executor = OperatorExecutionService(registry)
    import lean_constellation.app.repo_runtime_registry as registry_module

    real_factory = registry_module.create_app_runtime_services
    observations: list[tuple[bool, bool]] = []

    def observing_factory(**kwargs):  # noqa: ANN003, ANN202
        record = registry._records["MainRepo"]
        observations.append((kwargs["start_paused"], record.lock._is_owned()))  # noqa: SLF001
        runtime = real_factory(**kwargs)
        assert runtime.ark.pause_controller.is_paused()
        return runtime

    monkeypatch.setattr(registry_module, "create_app_runtime_services", observing_factory)

    prepared = executor.prepare_repo_management("MainRepo")

    assert prepared.ok and prepared.value is not None
    assert prepared.value.management_state == "paused_runtime"
    assert observations == [(True, True)]
    assert registry.get_status("MainRepo").value.state == "paused"


def test_loaded_unpaused_runtime_is_rejected_without_truth_change(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = make_repo(workspace)
    registry = make_registry(workspace, server_start_paused=False)
    assert registry.get_or_load("MainRepo", refresh_homes=False).ok
    executor = OperatorExecutionService(registry)
    sentinel = repo_root / "result.txt"

    result = executor.execute("MainRepo", MUTATION_OPERATION, _write_action(sentinel))

    assert not result.ok
    assert result.issues[0].kind == "operator_repo_runtime_not_paused"
    assert not sentinel.exists()


@pytest.mark.parametrize("busy_kind", ["agent", "step", "flow", "advance"])
def test_loaded_paused_runtime_with_active_work_is_rejected(tmp_path, monkeypatch, busy_kind: str) -> None:
    workspace = tmp_path / "workspace"
    repo_root = make_repo(workspace)
    registry = make_registry(workspace, server_start_paused=True)
    loaded = registry.get_or_load_paused("MainRepo")
    assert loaded.ok and loaded.value is not None
    runtime = loaded.value
    if busy_kind == "agent":
        monkeypatch.setattr(runtime.ark.agent_service, "list_running_agents", lambda: ["agent"])
    elif busy_kind == "step":
        monkeypatch.setattr(runtime.ark.step_service, "list_running_steps", lambda: ["step"])
    elif busy_kind == "flow":
        monkeypatch.setattr(
            runtime.ark.flow_service,
            "list_flows",
            lambda: [SimpleNamespace(status=FlowStatus.WAITING)],
        )
    else:
        runtime.ark.schedule_service.active_flow_advances.add("flow")
    sentinel = repo_root / "result.txt"

    result = OperatorExecutionService(registry).execute(
        "MainRepo", MUTATION_OPERATION, _write_action(sentinel)
    )

    assert not result.ok
    assert result.issues[0].kind == "operator_repo_runtime_busy"
    assert not sentinel.exists()


def test_runtime_inspection_failure_is_rejected_without_truth_change(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    repo_root = make_repo(workspace)
    registry = make_registry(workspace, server_start_paused=True)
    loaded = registry.get_or_load_paused("MainRepo")
    assert loaded.ok and loaded.value is not None

    def fail_inspection():
        raise RuntimeError("inspection failed")

    monkeypatch.setattr(loaded.value.ark.flow_service, "list_flows", fail_inspection)
    sentinel = repo_root / "result.txt"

    result = OperatorExecutionService(registry).execute(
        "MainRepo", MUTATION_OPERATION, _write_action(sentinel)
    )

    assert not result.ok
    assert result.issues[0].kind == "operator_runtime_inspection_failed"
    assert not sentinel.exists()


def test_mutation_is_serialized_by_repo_record_lock(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    make_repo(workspace)
    registry = make_registry(workspace)
    record = registry.discover_repo("MainRepo").value
    assert record is not None
    executor = OperatorExecutionService(registry)
    started = Event()
    finished = Event()

    def run() -> None:
        started.set()
        executor.execute("MainRepo", MUTATION_OPERATION, lambda ctx: ctx.runtime.foundation.ok(True))
        finished.set()

    with record.lock:
        thread = Thread(target=run)
        thread.start()
        assert started.wait(2)
        assert not finished.wait(0.05)
    thread.join(2)
    assert finished.is_set()


def test_operator_policy_holds_lifecycle_lock_and_self_managed_does_not(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    make_repo(workspace)
    registry = make_registry(workspace)
    executor = OperatorExecutionService(registry)

    def assert_outer_lock(ctx):  # noqa: ANN001
        with pytest.raises(RepoLifecycleLockBusyError):
            with ctx.runtime.repo_workspace.lifecycle_lock.locked(ctx.repo_root):
                pass
        return ctx.runtime.foundation.ok(True)

    def acquire_own_lock(ctx):  # noqa: ANN001
        with ctx.runtime.repo_workspace.lifecycle_lock.locked(ctx.repo_root):
            return ctx.runtime.foundation.ok(True)

    operator_result = executor.execute("MainRepo", MUTATION_OPERATION, assert_outer_lock)
    self_managed_result = executor.execute("MainRepo", SELF_MANAGED_OPERATION, acquire_own_lock)

    assert operator_result.ok and operator_result.value is True
    assert self_managed_result.ok and self_managed_result.value is True


def test_operator_policy_maps_busy_lifecycle_lock_without_calling_action(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = make_repo(workspace)
    registry = make_registry(workspace)
    executor = OperatorExecutionService(registry)
    called = False

    def action(ctx):  # noqa: ANN001
        nonlocal called
        called = True
        return ctx.runtime.foundation.ok(True)

    with registry.workspace_runtime().repo_workspace.lifecycle_lock.locked(repo_root):
        result = executor.execute("MainRepo", MUTATION_OPERATION, action)

    assert not result.ok
    assert result.issues[0].kind == "operator_lifecycle_lock_failed"
    assert called is False
