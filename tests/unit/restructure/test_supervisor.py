from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.app.restructure import RestructureSupervisor
from lean_constellation.domain.restructure import (
    CompletionTarget,
    ContentWork,
    NodeKind,
    NodeRecord,
    RepoPlan,
    RepoSpec,
    RestructureStage,
    WorkspacePlan,
)
from lean_constellation.services.restructure import RestructureService


def _repo_plan(repo_key: str, node_paths: list[str]) -> RepoPlan:
    nodes = {
        "Main": NodeRecord(
            path="Main",
            kind=NodeKind.MAIN,
            summary="root",
            goal="root",
            boundary="all",
            module="Main",
        )
    }
    for node_path in node_paths:
        nodes[node_path] = NodeRecord(
            path=node_path,
            kind=NodeKind.CONTENT,
            parent="Main",
            summary=node_path,
            goal=node_path,
            boundary=node_path,
            module=node_path,
        )
    return RepoPlan(
        repo_key=repo_key,
        directory=repo_key,
        module_root="Result",
        goal=repo_key,
        nodes=nodes,
    )


def _workspace(tmp_path: Path, *, node_paths: dict[str, list[str]], dependencies: dict[str, list[str]] | None = None) -> tuple[RestructureService, WorkspacePlan]:
    repos = {
        repo_key: RepoSpec(
            key=repo_key,
            directory=repo_key,
            module_root="Result",
            goal=repo_key,
            plan=_repo_plan(repo_key, paths),
        )
        for repo_key, paths in node_paths.items()
    }
    plan = WorkspacePlan(
        run_id="run-1",
        workspace_root=str(tmp_path),
        main_repo=next(iter(repos)),
        repos=repos,
        repo_dependencies=dependencies or {},
        completion_target=CompletionTarget.PROVED,
    )
    service = RestructureService(tmp_path)
    service.prepare(plan)
    return service, plan


class _FakeFlow:
    def __init__(self, flow_id: str) -> None:
        self.flow_id = flow_id
        self.status = FlowStatus.RUNNING
        self.result = None
        self.error = None


class _FakeFlowService:
    def __init__(self) -> None:
        self.flows: dict[str, _FakeFlow] = {}
        self.requests = []
        self._next = 0

    def start_flow(self, request, *, enqueue: bool = True) -> str:
        del enqueue
        self._next += 1
        flow_id = f"flow-{self._next}"
        self.requests.append(request)
        self.flows[flow_id] = _FakeFlow(flow_id)
        return flow_id

    def get_flow(self, flow_id: str) -> _FakeFlow:
        return self.flows[flow_id]


def _runtime(flow_service: _FakeFlowService) -> SimpleNamespace:
    return SimpleNamespace(ark=SimpleNamespace(flow_service=flow_service))


def _set_stage(service: RestructureService, repo_key: str, node_path: str, stage: RestructureStage) -> None:
    directory = service.directory_for_repo(repo_key)
    service.store.save_content(
        directory,
        node_path,
        ContentWork(repo_key=repo_key, node_path=node_path, stage=stage),
    )


def test_workspace_frontier_applies_repo_dag_to_declared_and_proved(tmp_path: Path):
    service, _ = _workspace(
        tmp_path,
        node_paths={"provider": ["Main.A"], "consumer": ["Main.B"], "independent": ["Main.C"]},
        dependencies={"consumer": ["provider"]},
    )
    supervisor = RestructureSupervisor(service)

    assert {(item["repo_key"], item["node_path"]) for item in supervisor.workspace_frontier(RestructureStage.DECLARED)} == {
        ("provider", "Main.A"),
        ("independent", "Main.C"),
    }

    _set_stage(service, "provider", "Main.A", RestructureStage.DECLARED)
    assert supervisor.frontier("consumer", RestructureStage.DECLARED) == ["Main.B"]
    assert supervisor.frontier("consumer", RestructureStage.PROVED) == []

    _set_stage(service, "consumer", "Main.B", RestructureStage.DECLARED)
    _set_stage(service, "independent", "Main.C", RestructureStage.DECLARED)
    assert supervisor.frontier("consumer", RestructureStage.PROVED) == ["Main.B"]
    assert {(item["repo_key"], item["node_path"]) for item in supervisor.workspace_frontier(RestructureStage.PROVED)} == {
        ("provider", "Main.A"),
        ("consumer", "Main.B"),
        ("independent", "Main.C"),
    }


def test_frontier_dispatch_is_idempotent_and_retry_invalidates_old_binding(tmp_path: Path):
    service, _ = _workspace(tmp_path, node_paths={"repo": ["Main.A", "Main.B"]})
    supervisor = RestructureSupervisor(service)
    flow_service = _FakeFlowService()
    runtime = _runtime(flow_service)

    first = supervisor.start_workspace_frontier(runtime, stage=RestructureStage.DECLARED, agent_id="agent")
    second = supervisor.start_workspace_frontier(runtime, stage=RestructureStage.DECLARED, agent_id="agent")
    assert len(first) == 2
    assert second == []

    failed = flow_service.flows[first[0]]
    failed.status = FlowStatus.FAILED
    failed.error = SimpleNamespace(message="content failed")
    flow_service.flows[first[1]].status = FlowStatus.RUNNING
    reconciled = supervisor.reconcile(runtime)
    assert reconciled["changed"] == 1
    assert reconciled["blocked_issues"] == ["repo:Main.A:declared: content failed"]
    failed_work, _ = service.content.load("repo", "Main.A")
    assert failed_work.issues == ["repo:Main.A:declared: content failed"]
    try:
        service.content.load("repo", "Main.B")
    except KeyError:
        pass
    else:
        raise AssertionError("the running sibling must not be marked failed")

    old_epoch = failed_work.attempt_epoch
    supervisor.retry_content("repo", "Main.A")
    retried_work, _ = service.content.load("repo", "Main.A")
    assert retried_work.attempt_epoch == old_epoch + 1
    assert service.store.load_run()[0].blocked_issues == []

    restarted = supervisor.start_workspace_frontier(runtime, stage=RestructureStage.DECLARED, agent_id="agent")
    assert len(restarted) == 1
    assert restarted[0] not in first
    assert len(flow_service.requests) == 3


def test_advance_run_uses_declared_barrier_then_parallel_proved_and_final_dag(tmp_path: Path):
    service, _ = _workspace(
        tmp_path,
        node_paths={"provider": ["Main.A"], "consumer": ["Main.B"]},
        dependencies={"consumer": ["provider"]},
    )
    supervisor = RestructureSupervisor(service)
    flow_service = _FakeFlowService()
    runtime = _runtime(flow_service)

    _set_stage(service, "provider", "Main.A", RestructureStage.DECLARED)
    _set_stage(service, "consumer", "Main.B", RestructureStage.DECLARED)
    first = supervisor.advance_run(runtime)
    assert len(first["started"]) == 1  # default max_builds=1
    first_build = first["started"][0]
    flow_service.flows[first_build].status = FlowStatus.COMPLETED
    flow_service.flows[first_build].result = SimpleNamespace(outcome="succeeded", operation_id="declared-first")
    next_tick = supervisor.reconcile(runtime)
    second_build = next_tick["advance"]["started"][0]
    assert second_build != first_build
    flow_service.flows[second_build].status = FlowStatus.COMPLETED
    flow_service.flows[second_build].result = SimpleNamespace(outcome="succeeded", operation_id="declared-second")
    reconciled = supervisor.reconcile(runtime)
    proved_ids = reconciled["advance"]["started"]
    assert len(proved_ids) == 2
    assert {request.params["stage"] for request in flow_service.requests[-2:]} == {"proved"}

    for flow_id, repo_key, node_path in zip(proved_ids, ("provider", "consumer"), ("Main.A", "Main.B"), strict=True):
        flow = flow_service.flows[flow_id]
        flow.status = FlowStatus.COMPLETED
        flow.result = SimpleNamespace(outcome="proved")
        _set_stage(service, repo_key, node_path, RestructureStage.PROVED)

    reconciled = supervisor.reconcile(runtime)
    assert reconciled["changed"] == 2
    final_provider = reconciled["advance"]["started"]
    assert len(final_provider) == 1
    assert flow_service.requests[-1].params["repo_key"] == "provider"
    flow_service.flows[final_provider[0]].status = FlowStatus.COMPLETED
    flow_service.flows[final_provider[0]].result = SimpleNamespace(outcome="succeeded", operation_id="provider-final")

    reconciled = supervisor.reconcile(runtime)
    final_consumer = reconciled["advance"]["started"]
    assert len(final_consumer) == 1
    assert flow_service.requests[-1].params["repo_key"] == "consumer"
    flow_service.flows[final_consumer[0]].status = FlowStatus.COMPLETED
    flow_service.flows[final_consumer[0]].result = SimpleNamespace(outcome="succeeded", operation_id="consumer-final")
    supervisor.reconcile(runtime)
    assert service.store.load_run()[0].status == "completed"


def test_reconcile_consumes_terminal_binding_once_and_rejects_unknown_retry_node(tmp_path: Path):
    service, _ = _workspace(tmp_path, node_paths={"repo": ["Main.A"]})
    supervisor = RestructureSupervisor(service)
    flow_service = _FakeFlowService()
    runtime = _runtime(flow_service)
    supervisor.start_workspace_frontier(runtime, stage=RestructureStage.DECLARED, agent_id="agent")
    flow_id = next(iter(flow_service.flows))
    flow_service.flows[flow_id].status = FlowStatus.FAILED
    flow_service.flows[flow_id].error = SimpleNamespace(message="failed")
    first = supervisor.reconcile(runtime)
    second = supervisor.reconcile(runtime)
    assert first["changed"] == 1
    assert second["changed"] == 0
    import pytest
    with pytest.raises(ValueError, match="unknown Content node"):
        supervisor.retry_content("repo", "Main.Missing")


def test_failed_build_can_be_retried_with_a_new_binding(tmp_path: Path):
    service, _ = _workspace(tmp_path, node_paths={"repo": ["Main.A"]})
    supervisor = RestructureSupervisor(service)
    flow_service = _FakeFlowService()
    runtime = _runtime(flow_service)
    first = supervisor.start_build(runtime, repo_key="repo", stage=RestructureStage.DECLARED)
    flow_service.flows[first].status = FlowStatus.FAILED
    flow_service.flows[first].error = SimpleNamespace(message="build failed")
    supervisor.reconcile(runtime)
    run, _ = service.store.load_run()
    assert run is not None and run.status == "blocked"

    second = supervisor.start_build(runtime, repo_key="repo", stage=RestructureStage.DECLARED)
    assert second != first
    run, _ = service.store.load_run()
    assert run is not None and run.status == "running"
    assert any(binding.stale for binding in run.bindings.values() if binding.flow_id == first)
    assert any(binding.flow_id == second and not binding.stale for binding in run.bindings.values())


def test_completed_failed_build_is_blocked_and_retryable(tmp_path: Path):
    service, _ = _workspace(tmp_path, node_paths={"repo": ["Main.A"]})
    supervisor = RestructureSupervisor(service)
    flow_service = _FakeFlowService()
    runtime = _runtime(flow_service)
    first = supervisor.start_build(runtime, repo_key="repo", stage=RestructureStage.DECLARED)
    flow_service.flows[first].status = FlowStatus.COMPLETED
    flow_service.flows[first].result = SimpleNamespace(outcome="failed", operation_id="build-op")
    result = supervisor.reconcile(runtime)
    assert result["changed"] == 1
    assert result["run"]["status"] == "blocked"
    assert result["blocked_issues"]
    second = supervisor.start_build(runtime, repo_key="repo", stage=RestructureStage.DECLARED)
    assert second != first
