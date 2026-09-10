from __future__ import annotations

from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest
from agent_runtime_kit.flow.models import FlowRequest

from lean_constellation.app import LeanAppConfig, RepoRuntimeRegistry, create_app_runtime_services
from lean_constellation.services.concurrency import (
    RepoActivityComponent,
    RepoActivityConflictError,
    RepoActivityRecoveryRequiredError,
    RepoRuntimeWriterLease,
)


def test_repo_runtime_writer_lease_rejects_second_writer_and_releases(tmp_path: Path) -> None:
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    first = RepoRuntimeWriterLease(repo_root)
    second = RepoRuntimeWriterLease(repo_root)

    assert first.path == second.path
    assert repo_root not in first.path.parents

    first.acquire()
    try:
        with pytest.raises(RepoActivityConflictError, match="already owns repository root"):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()


def test_repo_runtime_writer_lease_is_shared_across_workspace_configurations(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "canonical" / "Repo"
    repo_root.mkdir(parents=True)
    first_workspace = tmp_path / "lane-a"
    second_workspace = tmp_path / "lane-b"
    first_workspace.symlink_to(repo_root.parent, target_is_directory=True)
    second_workspace.symlink_to(repo_root.parent, target_is_directory=True)

    first = RepoRuntimeWriterLease(first_workspace / "Repo")
    second = RepoRuntimeWriterLease(second_workspace / "Repo")

    assert first_workspace != second_workspace
    assert first.repo_root == repo_root.resolve()
    assert second.repo_root == repo_root.resolve()
    assert first.path == second.path
    first.acquire()
    try:
        with pytest.raises(RepoActivityConflictError, match="already owns repository root"):
            second.acquire()
    finally:
        first.release()


def test_repo_runtime_registries_report_single_writer_conflict(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = workspace / "Repo"
    (repo_root / ".lean_constellation").mkdir(parents=True)
    config = LeanAppConfig(
        workspace_root=workspace,
        materialize_agent_homes=False,
        server_start_paused=True,
    )
    first = RepoRuntimeRegistry(config)
    second = RepoRuntimeRegistry(config)

    loaded = first.get_or_load("Repo")
    assert loaded.ok and loaded.value is not None
    rejected = second.get_or_load("Repo")
    assert not rejected.ok
    assert rejected.issues[0].kind == "repo_runtime_writer_conflict"

    assert first.unload("Repo", require_stable=False).ok
    reloaded = second.get_or_load("Repo")
    assert reloaded.ok and reloaded.value is not None
    second.shutdown_all()


def test_repo_activity_reserves_exact_nodes_and_excludes_maintenance(tmp_path: Path) -> None:
    activity = RepoActivityComponent()
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()

    reservation = activity.reserve_content_batch(
        repo_root,
        batch_id="batch-a",
        node_paths=["Main.B", "Main.A", "Main.A"],
    )
    assert reservation.node_paths == ("Main.A", "Main.B")
    assert activity.reserve_content_batch(
        repo_root,
        batch_id="batch-a",
        node_paths=["Main.A", "Main.B"],
    ) == reservation
    assert activity.batch_for_node(repo_root, "Main.A") == reservation

    with pytest.raises(RepoActivityConflictError, match="active owners"):
        activity.reserve_content_batch(
            repo_root,
            batch_id="batch-b",
            node_paths=["Main.B", "Main.C"],
        )
    with pytest.raises(RepoActivityConflictError, match="blocks repository maintenance"):
        with activity.maintenance(repo_root, owner="release"):
            pass

    activity.release_content_batch(repo_root, batch_id="batch-a")
    with activity.maintenance(repo_root, owner="release"):
        with pytest.raises(RepoActivityConflictError, match="blocks content batch admission"):
            activity.reserve_content_batch(
                repo_root,
                batch_id="batch-c",
                node_paths=["Main.C"],
            )

    assert activity.active_batches(repo_root) == ()


def test_repo_activity_tracks_short_transactions_without_serializing_distinct_nodes(
    tmp_path: Path,
) -> None:
    activity = RepoActivityComponent()
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()

    assert activity.has_active_transactions(repo_root) is False
    with activity.node_write(repo_root, "Main.B", "Main.A"):
        assert activity.has_active_transactions(repo_root) is True
        with activity.catalog_write(repo_root, "mathlib"):
            assert activity.has_active_transactions(repo_root) is True
    assert activity.has_active_transactions(repo_root) is False


def test_repo_maintenance_and_short_transactions_are_bidirectionally_exclusive(tmp_path: Path) -> None:
    activity = RepoActivityComponent()
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()

    with activity.catalog_write(repo_root, "mathlib"):
        with pytest.raises(RepoActivityConflictError, match="transactions block"):
            with activity.maintenance(repo_root, owner="snapshot"):
                pass
    with activity.build_cache_write(repo_root):
        with pytest.raises(RepoActivityConflictError, match="transactions block"):
            with activity.maintenance(repo_root, owner="snapshot"):
                pass

    with activity.maintenance(repo_root, owner="snapshot"):
        with activity.catalog_write(repo_root, "mathlib"):
            pass
        with activity.node_write(repo_root, "Main.Core"):
            pass
        with activity.build_cache_write(repo_root):
            pass

        conflicts: list[Exception] = []

        def contend() -> None:
            try:
                with activity.catalog_write(repo_root, "mathlib"):
                    pass
            except Exception as exc:  # noqa: BLE001 - capture cross-thread result.
                conflicts.append(exc)

        thread = Thread(target=contend)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert len(conflicts) == 1
        assert isinstance(conflicts[0], RepoActivityConflictError)
        assert "blocks new transactions" in str(conflicts[0])


def test_repo_maintenance_nested_depth_keeps_other_threads_excluded(tmp_path: Path) -> None:
    activity = RepoActivityComponent()
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()

    def maintenance_conflict() -> Exception | None:
        conflicts: list[Exception] = []

        def contend() -> None:
            try:
                with activity.maintenance(repo_root, owner="contender"):
                    pass
            except Exception as exc:  # noqa: BLE001 - capture cross-thread result.
                conflicts.append(exc)

        thread = Thread(target=contend)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        return conflicts[0] if conflicts else None

    with activity.maintenance(repo_root, owner="outer"):
        with activity.maintenance(repo_root, owner="inner"):
            assert isinstance(maintenance_conflict(), RepoActivityConflictError)
        assert isinstance(maintenance_conflict(), RepoActivityConflictError)

    assert maintenance_conflict() is None


def test_repo_activity_rehydrates_persisted_content_batch_after_runtime_restart(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    coordinator_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id="repo:Repo",
            params={
                "repo_key": "Repo",
                "repo_root": str(repo_root),
                "start_mode": "admin_start",
            },
        ),
        enqueue=False,
    )

    def mark_persisted_batch(flow) -> None:  # noqa: ANN001
        flow.state.position = flow.state.position.model_copy(
            update={"phase": "waiting_content_tasks"}
        )
        flow.state.pending_dispatch_kind = "content_tasks"
        flow.state.pending_dispatch_source_step_id = "coordinator-callback"
        flow.state.pending_dispatch_source_submission_id = "sub-batch"
        flow.state.pending_content_node_paths = ["Main.A", "Main.B"]
        flow.state.waiting_dispatch_step_id = "dispatch-batch"

    runtime.ark.flow_service.store.update_flow_record(coordinator_id, mark_persisted_batch)
    runtime.repo_activity.release_all_content_batches()

    batches = runtime.repo_activity.active_batches(repo_root)

    assert len(batches) == 1
    assert batches[0].node_paths == ("Main.A", "Main.B")
    assert runtime.repo_activity.batch_for_node(repo_root, "Main.A") == batches[0]
    with pytest.raises(RepoActivityConflictError, match="active owners"):
        runtime.repo_activity.reserve_content_batch(
            repo_root,
            batch_id="different-batch",
            node_paths=["Main.A"],
        )


def test_repo_activity_fails_closed_for_custom_runtime_root_when_flow_service_is_lost(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime_root = tmp_path / "custom-runtime"
    runtime = create_app_runtime_services(runtime_root=runtime_root)
    runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id="repo:Repo",
            params={
                "repo_key": "Repo",
                "repo_root": str(repo_root),
                "start_mode": "admin_start",
            },
        ),
        enqueue=False,
    )
    assert any(path.is_file() for path in runtime_root.rglob("*"))
    assert not (repo_root / ".agent_runtime").exists()
    runtime.ark.flow_service = None

    with pytest.raises(RepoActivityRecoveryRequiredError, match="recovery"):
        runtime.repo_activity.active_batches(repo_root)


class _BrokenFrontierRuntime:
    def __init__(self, *, flow_service: object | None) -> None:
        self.ark = SimpleNamespace(flow_service=flow_service)

    def list_flows(self, **filters):  # noqa: ANN003, ANN201
        del filters
        raise RuntimeError("frontier unavailable")


@pytest.mark.parametrize("flow_service", [None, object()])
def test_repo_activity_fails_closed_when_persisted_frontier_is_unavailable(
    tmp_path: Path,
    flow_service: object | None,
) -> None:
    activity = RepoActivityComponent(_BrokenFrontierRuntime(flow_service=flow_service))
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()

    with pytest.raises(RepoActivityRecoveryRequiredError, match="recovery"):
        activity.active_batches(repo_root)
    with pytest.raises(RepoActivityRecoveryRequiredError, match="recovery"):
        with activity.maintenance(repo_root, owner="snapshot"):
            pass


def test_repo_activity_fails_closed_for_incomplete_persisted_batch_identity(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    flow = SimpleNamespace(
        flow_id="coordinator-incomplete",
        status="running",
        input=SimpleNamespace(repo_root=str(repo_root)),
        state=SimpleNamespace(
            position=SimpleNamespace(phase="waiting_content_tasks"),
            pending_dispatch_kind="content_tasks",
            pending_content_node_paths=["Main.A"],
            pending_dispatch_source_step_id=None,
            pending_dispatch_source_submission_id="sub-a",
        ),
    )

    class Runtime:
        ark = SimpleNamespace(flow_service=object())

        @staticmethod
        def list_flows(**filters):  # noqa: ANN003, ANN201
            del filters
            return [flow]

    activity = RepoActivityComponent(Runtime())
    with pytest.raises(RepoActivityRecoveryRequiredError, match="incomplete"):
        activity.active_batches(repo_root)


def test_repo_activity_fails_closed_when_callback_step_cannot_be_read(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    flow = SimpleNamespace(
        flow_id="coordinator-callback",
        current_step_id="callback-step",
        status="running",
        input=SimpleNamespace(repo_root=str(repo_root)),
        state=SimpleNamespace(
            position=SimpleNamespace(phase="coordinator_callback"),
            pending_dispatch_kind="content_tasks",
            pending_content_node_paths=["Main.A"],
            pending_dispatch_source_step_id="source-step",
            pending_dispatch_source_submission_id="source-submission",
        ),
    )

    class Runtime:
        ark = SimpleNamespace(flow_service=object())

        @staticmethod
        def list_flows(**filters):  # noqa: ANN003, ANN201
            del filters
            return [flow]

        @staticmethod
        def get_step(step_id: str):  # noqa: ANN201
            raise RuntimeError(f"cannot read {step_id}")

    activity = RepoActivityComponent(Runtime())
    with pytest.raises(RepoActivityRecoveryRequiredError, match="callback Step cannot be read"):
        activity.active_batches(repo_root)


def test_repo_activity_validates_batch_identity_before_callback_release(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    flow = SimpleNamespace(
        flow_id="coordinator-callback",
        current_step_id="callback-step",
        status="running",
        input=SimpleNamespace(repo_root=str(repo_root)),
        state=SimpleNamespace(
            position=SimpleNamespace(phase="coordinator_callback"),
            pending_dispatch_kind="content_tasks",
            pending_content_node_paths=[],
            pending_dispatch_source_step_id=None,
            pending_dispatch_source_submission_id=None,
        ),
    )

    class Runtime:
        ark = SimpleNamespace(flow_service=object())

        @staticmethod
        def list_flows(**filters):  # noqa: ANN003, ANN201
            del filters
            return [flow]

        @staticmethod
        def get_step(step_id: str):  # noqa: ANN201
            return SimpleNamespace(
                step_id=step_id,
                step_type="coordinator_agent_step",
                state=SimpleNamespace(prompt_mode="callback"),
            )

    activity = RepoActivityComponent(Runtime())
    with pytest.raises(RepoActivityRecoveryRequiredError, match="identity is incomplete"):
        activity.active_batches(repo_root)
