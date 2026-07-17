from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.contexts import StableStepTerminalContext
from agent_runtime_kit.flow.models import FlowStatus
from lean_constellation.app.runtime import ApplicationSnapshotRuntime
from lean_constellation.app.config import AutomaticCheckpointAppConfig

from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import ProofAvailability, RepoFormat, RepoPublicationStatus, RepoWorkMode
from lean_constellation.domain.repo_run import RepoRunContext, RepoRunSpec, SourceScope
from lean_constellation.flows.common.flow_requests import build_content_node_task_request, build_resource_curation_request
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.content_node_task.flows import ContentNodeTaskResult
from lean_constellation.flows.coordinator.submissions import (
    CoordinatorContentTasksSubmission,
    CoordinatorRepoReadySubmission,
    CoordinatorRepoRequirementSubmission,
    CoordinatorResourceRequestSubmission,
)
from lean_constellation.flows.resource_request.flows import ResourceCurationResult
from lean_constellation.services.external_clients import ExternalCommandResult, LeanCheckSummaryView
from lean_constellation.services.foundation import FoundationService
from lean_constellation.services.validation_snapshot import RepoCheckpointKind, ValidationSnapshotService
from tests.unit_services_helpers import make_runtime, publish_native_provider_release


class FakeLakeClient:
    def run_lake_update(self, repo_root: Path) -> ExternalCommandResult:
        return ExternalCommandResult(
            ok=True,
            command=["lake", "update"],
            cwd=str(repo_root),
            exit_code=0,
            summary="lake update ok",
        )

    def run_lake_build(self, repo_root: Path, target: str | None = None) -> ExternalCommandResult:
        return ExternalCommandResult(
            ok=True,
            command=["lake", "build"],
            cwd=str(repo_root),
            exit_code=0,
            summary="lake build ok",
        )

    def run_minimal_import_check(self, repo_root: Path, module: str) -> LeanCheckSummaryView:
        return LeanCheckSummaryView(ok=True, module=module, command=["lean"], summary=f"import {module} ok")

    def summarize_command_result(self, result: ExternalCommandResult):
        from lean_constellation.services.external_clients import LakeCommandSummaryView

        return LakeCommandSummaryView(
            ok=result.ok,
            command=result.command,
            summary=result.summary or "",
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            stderr_excerpt=result.stderr_excerpt,
        )


class FakeRuntimeStabilityProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation
        self.calls: list[tuple[RepoCheckpointKind, list[str]]] = []

    def check_repo_stable_point(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind,
        node_paths: list[str] | None = None,
    ):
        del repo_root
        self.calls.append((checkpoint_kind, list(node_paths or [])))
        return self.foundation.ok(self.foundation.gate_passed("runtime_stability", summary="Runtime is stable."))


class FakeArkSnapshotProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation
        self.created: list[tuple[list[str], str | None]] = []

    def create_runtime_snapshot(self, repo_root: Path, *, scope_ids: list[str], label: str | None = None):
        del repo_root
        self.created.append((list(scope_ids), label))
        return self.foundation.ok(f"ark_snapshot_{len(self.created)}")

    def restore_runtime_snapshot(self, repo_root: Path, *, snapshot_id: str, leave_runtime_paused: bool = True):
        del repo_root, leave_runtime_paused
        return self.foundation.ok(
            self.foundation.mutation_view(
                object_ref=f"ark:{snapshot_id}",
                changed=True,
                summary="Restored fake ARK snapshot.",
            )
        )


class FakeConsistencyForReadiness:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def check_source_corpus_consistency(self, repo_root: Path):
        del repo_root
        return self.foundation.ok(self.foundation.gate_passed("source_corpus_consistency", summary="Source corpus consistency passed."))

    def check_source_index_consistency(self, repo_root: Path):
        del repo_root
        return self.foundation.ok(self.foundation.gate_passed("source_index_consistency", summary="Source index consistency passed."))

    def check_projection_sync(self, repo_root: Path, *, scope: str = "repo"):
        del repo_root
        return self.foundation.ok(self.foundation.gate_passed("projection_sync", summary=f"Projection sync passed for {scope}."))


def _runtime(tmp_path: Path) -> tuple[FakeLeanFlowRuntime, object, FakeRuntimeStabilityProvider, FakeArkSnapshotProvider]:
    lean_runtime = make_runtime(external_overrides={"lake": FakeLakeClient()})
    foundation = lean_runtime.foundation
    runtime_stability = FakeRuntimeStabilityProvider(foundation)
    ark_snapshot = FakeArkSnapshotProvider(foundation)
    lean_runtime.app.validation_snapshot = ValidationSnapshotService(
        lean_runtime,
        consistency=FakeConsistencyForReadiness(lean_runtime.foundation),
    )
    lean_runtime.app.snapshot_runtime = ApplicationSnapshotRuntime(
        lean_runtime, ark_snapshot, runtime_stability=runtime_stability
    )
    flow_runtime = create_fake_lean_flow_runtime(
        tmp_path / "ark",
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    return flow_runtime, lean_runtime, runtime_stability, ark_snapshot


def _start_coordinator(
    runtime: FakeLeanFlowRuntime,
    repo_root: Path,
    *,
    max_parallel_content_node_tasks: int | None = None,
) -> str:
    repo_root.mkdir(parents=True, exist_ok=True)
    run_context = None
    if max_parallel_content_node_tasks is not None:
        run_context = RepoRunContext(
            start_kind="initial",
            run_spec=RepoRunSpec(
                run_objective="Run coordinator concurrency test.",
                target_proof_availability=ProofAvailability.PROVED,
                work_mode=RepoWorkMode.PROVED_FULL_GRAPH,
                source_scope=SourceScope(mode="none"),
                index_policy="reuse",
                root_interface_policy="reuse",
                max_parallel_content_node_tasks=max_parallel_content_node_tasks,
            ),
        )
    return runtime.start_flow(
        "native_repo_coordinator",
        {
            "repo_key": repo_root.name,
            "repo_root": str(repo_root),
            "start_mode": "admin_start",
            "start_reason": "unit",
            "run_context": run_context.model_dump(mode="json") if run_context is not None else None,
        },
        scope_id=f"repo:{repo_root.name}",
    )


def _ensure_main_core_node(lean_runtime, repo_root: Path) -> str:
    repo_root.mkdir(parents=True, exist_ok=True)
    assert lean_runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    created = lean_runtime.node.create_content_node(
        repo_root,
        path="Main.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Core ready.",
    )
    assert created.ok and created.value is not None
    return created.value.node_id


def _advance_and_run(runtime: FakeLeanFlowRuntime, flow_id: str) -> str:
    step_id = runtime.flow_service.advance_flow(flow_id)
    assert step_id is not None
    runtime.run_step(step_id)
    return step_id


def _complete_child_flow(runtime: FakeLeanFlowRuntime, child_flow_id: str, result) -> None:
    runtime.flow_service.store.update_flow_record(
        child_flow_id,
        lambda flow: (
            setattr(flow, "result", result),
            setattr(flow, "status", FlowStatus.COMPLETED),
            setattr(flow, "current_step_id", None),
        ),
    )


def _prepare_requirement_resume_gate(runtime: FakeLeanFlowRuntime, lean_runtime, repo_root: Path):
    provider_root = repo_root.parent / "Provider"
    flow_id = _start_coordinator(runtime, repo_root)
    repo_root.mkdir(parents=True, exist_ok=True)
    provider_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "lakefile.toml").write_text('name = "Repo"\n', encoding="utf-8")
    publish_native_provider_release(lean_runtime, provider_root, summary="Provider ready.")
    runtime.agent_service.queue_submission(
        CoordinatorRepoRequirementSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_repo_requirement",
            tool_name="submit_repo_requirement",
            repo_key="Repo",
            requirement_name="provider_req",
            target_repo="Provider",
            reason="Need the provider API.",
            summary="Wait for provider.",
        )
    )
    _advance_and_run(runtime, flow_id)
    assert lean_runtime.repo_workspace.requirement.mark_requirement_satisfied(
        repo_root,
        requirement_name="provider_req",
        provider_repo="Provider",
    ).ok
    assert lean_runtime.repo_workspace.mark_requirement_result_observed(
        repo_root,
        requirement_name="provider_req",
    ).ok
    gate_step_id = runtime.flow_service.advance_flow(flow_id)
    assert gate_step_id is not None
    return flow_id, gate_step_id, provider_root


def test_content_task_dispatch_waiting_snapshot_and_callback(tmp_path: Path) -> None:
    runtime, lean_runtime, runtime_stability, ark_snapshot = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    flow_id = _start_coordinator(runtime, repo_root)
    node_id = _ensure_main_core_node(lean_runtime, repo_root)

    runtime.agent_service.queue_submission(
        CoordinatorContentTasksSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_content_tasks",
            tool_name="submit_content_node_tasks",
            repo_key="Repo",
            node_paths=["Main.Core"],
            requests=[build_content_node_task_request(repo_key="Repo", node_path="Main.Core", scope_id=f"repo:Repo:node:{node_id}")],
            continuation="wait_for_callback",
            summary="Run Main.Core.",
        )
    )
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "before_content_task_dispatch_snapshot"

    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "dispatch_content_tasks"

    dispatch_step_id = _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.WAITING
    assert flow.state.position.phase == "waiting_content_tasks"
    child_flows = runtime.flow_service.store.list_child_flows(parent_flow_id=flow_id, parent_dispatch_step_id=dispatch_step_id)
    assert len(child_flows) == 1

    _complete_child_flow(
        runtime,
        child_flows[0].flow_id,
        ContentNodeTaskResult(outcome="ready", repo_key="Repo", node_path="Main.Core", summary="Content task ready."),
    )
    assert runtime.flow_service.can_advance_flow(flow_id)
    _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.RUNNING
    assert flow.state.position.phase == "coordinator_callback"
    assert runtime_stability.calls == [
        (RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH, ["Main.Core"]),
        (RepoCheckpointKind.AFTER_CONTENT_TASK_BATCH_TERMINAL, ["Main.Core"]),
    ]
    assert ark_snapshot.created[0][0] == ["repo:Repo", f"repo:Repo:node:{node_id}"]
    assert ark_snapshot.created[1][0] == ["repo:Repo", f"repo:Repo:node:{node_id}"]

    runtime.agent_service.queue_submission(
        CoordinatorRepoRequirementSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_repo_requirement",
            tool_name="submit_repo_requirement",
            repo_key="Repo",
            requirement_name="provider_req",
            target_repo="Provider",
            required_proof_availability=ProofAvailability.PROVED,
            reason="Need external provider.",
            summary="Wait for provider.",
        )
    )
    _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.WAITING
    assert flow.state.position.phase == "waiting_requirement"
    assert flow.state.waiting_requirement_name == "provider_req"
    requirement = lean_runtime.repo_workspace.requirement.get_requirement(repo_root, name="provider_req")
    assert requirement.ok and requirement.value is not None
    assert requirement.value.requirement.required_proof_availability == ProofAvailability.PROVED
    assert len(runtime.agent_service.start_records) == 2
    assert runtime.agent_service.start_records[0].agent_id == runtime.agent_service.start_records[1].agent_id
    assert "The child workflows you requested have finished." in (runtime.agent_service.start_records[1].prompt or "")


def test_repo_flow_boundary_group_disabled_skips_coordinator_snapshot_step(tmp_path: Path) -> None:
    runtime, lean_runtime, runtime_stability, ark_snapshot = _runtime(tmp_path)
    lean_runtime.app.automatic_checkpoints = AutomaticCheckpointAppConfig(
        repo_flow_boundaries_enabled=False,
    )
    repo_root = tmp_path / "workspace" / "Repo"
    flow_id = _start_coordinator(runtime, repo_root)
    node_id = _ensure_main_core_node(lean_runtime, repo_root)
    runtime.agent_service.queue_submission(
        CoordinatorContentTasksSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_content_tasks",
            tool_name="submit_content_node_tasks",
            repo_key="Repo",
            node_paths=["Main.Core"],
            requests=[
                build_content_node_task_request(
                    repo_key="Repo",
                    node_path="Main.Core",
                    scope_id=f"repo:Repo:node:{node_id}",
                )
            ],
            continuation="wait_for_callback",
            summary="Run Main.Core.",
        )
    )

    _advance_and_run(runtime, flow_id)
    snapshot_step_id = _advance_and_run(runtime, flow_id)

    step = runtime.flow_service.get_step(snapshot_step_id)
    assert step.result.outcome == "skipped"
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "dispatch_content_tasks"
    assert runtime_stability.calls == []
    assert ark_snapshot.created == []


def test_coordinator_enforces_content_task_batch_parallelism_from_run_context(tmp_path: Path) -> None:
    runtime, lean_runtime, _, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    first_id = _ensure_main_core_node(lean_runtime, repo_root)
    second = lean_runtime.node.create_content_node(
        repo_root,
        path="Main.Other",
        goal="Other goal",
        boundary="Other boundary",
        objective="Build other.",
        success_criteria="Other ready.",
    )
    assert second.ok and second.value is not None
    requests = [
        build_content_node_task_request(
            repo_key="Repo",
            node_path="Main.Core",
            scope_id=f"repo:Repo:node:{first_id}",
            max_parallel_content_node_tasks=2,
        ),
        build_content_node_task_request(
            repo_key="Repo",
            node_path="Main.Other",
            scope_id=f"repo:Repo:node:{second.value.node_id}",
            max_parallel_content_node_tasks=2,
        ),
    ]

    accepted_flow_id = _start_coordinator(runtime, repo_root, max_parallel_content_node_tasks=2)
    runtime.agent_service.queue_submission(
        CoordinatorContentTasksSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_content_tasks",
            tool_name="submit_content_node_tasks",
            repo_key="Repo",
            node_paths=["Main.Core", "Main.Other"],
            requests=requests,
            continuation="wait_for_callback",
            summary="Run two content tasks.",
        )
    )
    _advance_and_run(runtime, accepted_flow_id)
    accepted = runtime.flow_service.get_flow(accepted_flow_id)
    assert accepted.error is None
    assert accepted.state.position.phase == "before_content_task_dispatch_snapshot"

    rejected_flow_id = _start_coordinator(runtime, repo_root, max_parallel_content_node_tasks=1)
    runtime.agent_service.queue_submission(
        CoordinatorContentTasksSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_content_tasks",
            tool_name="submit_content_node_tasks",
            repo_key="Repo",
            node_paths=["Main.Core", "Main.Other"],
            requests=requests,
            continuation="wait_for_callback",
            summary="Run two content tasks.",
        )
    )
    _advance_and_run(runtime, rejected_flow_id)
    rejected = runtime.flow_service.get_flow(rejected_flow_id)
    assert rejected.status is FlowStatus.FAILED
    assert rejected.error.error_type == "content_task_batch_parallelism_exceeded"


def test_resource_request_dispatch_waiting_and_callback(tmp_path: Path) -> None:
    runtime, _, runtime_stability, ark_snapshot = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    flow_id = _start_coordinator(runtime, repo_root)

    runtime.agent_service.queue_submission(
        CoordinatorResourceRequestSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_resource_request",
            tool_name="submit_resource_request",
            repo_key="Repo",
            target_kind="arxiv",
            target="2501.12345",
            requests=[
                build_resource_curation_request(
                    scope_id="repo:Repo",
                    repo_key="Repo",
                    repo_root=str(repo_root),
                    target_kind="arxiv",
                    target="2501.12345",
                    requested_by="coordinator",
                )
            ],
            continuation="wait_for_callback",
            summary="Curate arxiv source.",
        )
    )
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "before_resource_request_dispatch_snapshot"

    before_snapshot_step_id = _advance_and_run(runtime, flow_id)
    before_snapshot_step = runtime.flow_service.get_step(before_snapshot_step_id)
    assert before_snapshot_step.result.checkpoint_kind == "before_resource_request_dispatch"
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "dispatch_resource_request"

    dispatch_step_id = _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.WAITING
    assert flow.state.position.phase == "waiting_resource_request"
    child_flows = runtime.flow_service.store.list_child_flows(parent_flow_id=flow_id, parent_dispatch_step_id=dispatch_step_id)
    assert len(child_flows) == 1

    _complete_child_flow(
        runtime,
        child_flows[0].flow_id,
        ResourceCurationResult(
            outcome="duplicate",
            repo_key="Repo",
            target_summary="arxiv:2501.12345",
            existing_resource_key="res_existing",
            summary="Duplicate resource.",
        ),
    )
    after_snapshot_step_id = _advance_and_run(runtime, flow_id)
    after_snapshot_step = runtime.flow_service.get_step(after_snapshot_step_id)
    assert after_snapshot_step.result.checkpoint_kind == "after_resource_request_terminal"
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.RUNNING
    assert flow.state.position.phase == "coordinator_callback"

    callback_step_id = runtime.flow_service.advance_flow(flow_id)
    assert callback_step_id is not None

    runtime.agent_service.queue_submission(
        CoordinatorRepoRequirementSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_repo_requirement",
            tool_name="submit_repo_requirement",
            repo_key="Repo",
            requirement_name="provider_req",
            target_repo="Provider",
            summary="Wait for provider.",
        )
    )
    runtime.run_step(callback_step_id)
    assert "Duplicate resource." in (runtime.agent_service.start_records[1].prompt or "")
    assert runtime_stability.calls == [
        (RepoCheckpointKind.BEFORE_RESOURCE_REQUEST_DISPATCH, []),
        (RepoCheckpointKind.AFTER_RESOURCE_REQUEST_TERMINAL, []),
    ]
    assert ark_snapshot.created == [
        (["repo:Repo"], "before_resource_request_dispatch for Repo"),
        (["repo:Repo"], "after_resource_request_terminal for Repo"),
    ]


def test_requirement_resume_reuses_flow_agent_and_automatically_attaches_provider(tmp_path: Path) -> None:
    runtime, lean_runtime, _, ark_snapshot = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    provider_root = repo_root.parent / "Provider"
    flow_id = _start_coordinator(runtime, repo_root)
    repo_root.mkdir(parents=True, exist_ok=True)
    provider_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "lakefile.toml").write_text('name = "Repo"\n', encoding="utf-8")
    publish_native_provider_release(lean_runtime, provider_root, summary="Provider ready.")

    runtime.agent_service.queue_submission(
        CoordinatorRepoRequirementSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_repo_requirement",
            tool_name="submit_repo_requirement",
            repo_key="Repo",
            requirement_name="provider_req",
            target_repo="Provider",
            reason="Need the provider API.",
            summary="Wait for provider.",
        )
    )
    _advance_and_run(runtime, flow_id)
    waiting_flow = runtime.flow_service.get_flow(flow_id)
    coordinator_agent_id = waiting_flow.agent_bindings.get("coordinator")
    assert coordinator_agent_id is not None
    runtime.agent_service.agents[coordinator_agent_id].thread_id = "thread-original"
    assert waiting_flow.status is FlowStatus.WAITING
    assert runtime.flow_service.can_advance_flow(flow_id) is False

    assert lean_runtime.repo_workspace.requirement.mark_requirement_satisfied(
        repo_root,
        requirement_name="provider_req",
        provider_repo="Provider",
    ).ok
    assert runtime.flow_service.can_advance_flow(flow_id) is False
    assert lean_runtime.repo_workspace.mark_requirement_result_observed(
        repo_root,
        requirement_name="provider_req",
    ).ok
    assert runtime.flow_service.can_advance_flow(flow_id) is True

    gate_step_id = runtime.flow_service.advance_flow(flow_id)
    assert gate_step_id is not None
    gate_step = runtime.flow_service.get_step(gate_step_id)
    assert gate_step.step_type == "coordinator_requirement_resume_gate_step"
    runtime.run_step(gate_step_id)
    gate_step = runtime.flow_service.get_step(gate_step_id)
    assert gate_step.result.outcome == "resumed"
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.position.phase == "coordinator_requirement_resume"

    requirement = lean_runtime.repo_workspace.requirement.get_requirement(repo_root, name="provider_req")
    assert requirement.ok and requirement.value is not None
    assert requirement.value.requirement.status == "handled"
    dependencies = lean_runtime.repo_workspace.workspace_catalog.list_current_lake_dependency_repos(repo_root)
    assert dependencies.ok and dependencies.value is not None
    assert [dependency.name for dependency in dependencies.value] == ["Provider"]

    resume_step_id = runtime.flow_service.advance_flow(flow_id)
    assert resume_step_id is not None
    resume_step = runtime.flow_service.get_step(resume_step_id)
    assert resume_step.step_type == "coordinator_agent_step"
    assert resume_step.state.prompt_mode == "initial"
    assert resume_step.state.callback_dispatch_step_id is None
    assert resume_step.state.create_agent_if_missing is False
    assert resume_step.state.prompt_override is not None
    assert "provider_req" in resume_step.state.prompt_override
    assert "Provider" in resume_step.state.prompt_override
    assert "automatically attached" in resume_step.state.prompt_override
    assert flow_id not in resume_step.state.prompt_override

    runtime.agent_service.queue_submission(
        CoordinatorRepoRequirementSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_repo_requirement",
            tool_name="submit_repo_requirement",
            repo_key="Repo",
            requirement_name="next_provider_req",
            target_repo="NextProvider",
            reason="Continue normal coordination.",
            summary="Wait for another provider.",
        )
    )
    runtime.run_step(resume_step_id)
    resumed_flow = runtime.flow_service.get_flow(flow_id)
    assert resumed_flow.flow_id == flow_id
    assert resumed_flow.agent_bindings.get("coordinator") == coordinator_agent_id
    assert runtime.agent_service.start_records[-1].agent_id == coordinator_agent_id
    assert runtime.agent_service.get_agent(coordinator_agent_id).thread_id == "thread-original"
    assert resumed_flow.state.position.phase == "waiting_requirement"
    assert len(runtime.flow_service.list_flows(flow_type="native_repo_coordinator")) == 1
    assert len(ark_snapshot.created) == 2


def test_requirement_resume_gate_rechecks_provider_truth_before_attach(tmp_path: Path) -> None:
    runtime, lean_runtime, _, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    flow_id, gate_step_id, provider_root = _prepare_requirement_resume_gate(runtime, lean_runtime, repo_root)
    assert lean_runtime.repo_workspace.metadata.mark_repo_developing(provider_root).ok

    runtime.run_step(gate_step_id)

    gate_step = runtime.flow_service.get_step(gate_step_id)
    assert gate_step.result.outcome == "invalid_requirement"
    assert gate_step.result.issue_code == "provider_repo_not_ready"
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.FAILED
    assert flow.error.error_type == "provider_repo_not_ready"
    dependencies = lean_runtime.repo_workspace.workspace_catalog.list_current_lake_dependency_repos(repo_root)
    assert dependencies.ok and dependencies.value == []


def test_requirement_resume_gate_returns_to_waiting_when_observed_truth_races(tmp_path: Path) -> None:
    runtime, lean_runtime, _, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    flow_id, gate_step_id, _ = _prepare_requirement_resume_gate(runtime, lean_runtime, repo_root)
    assert lean_runtime.repo_workspace.mark_requirement_waiting_for_provider(
        repo_root,
        requirement_name="provider_req",
        provider_repo="Provider",
        reason="Provider result must be observed again.",
    ).ok

    runtime.run_step(gate_step_id)

    gate_step = runtime.flow_service.get_step(gate_step_id)
    assert gate_step.result.outcome == "still_waiting"
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.WAITING
    assert flow.state.position.phase == "waiting_requirement"
    dependencies = lean_runtime.repo_workspace.workspace_catalog.list_current_lake_dependency_repos(repo_root)
    assert dependencies.ok and dependencies.value == []


def test_repo_ready_submission_prepares_and_publishes_native_release(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    runtime, lean_runtime, _, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    lean_runtime.repo_workspace.metadata.ensure_repo_model(repo_root)
    assert lean_runtime.repo_workspace.metadata.set_repo_format(
        repo_root,
        repo_format=RepoFormat.NATIVE,
        reason="native coordinator fixture",
    ).ok
    written = lean_runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Formalize a small source corpus.",
            source_corpus_mode=SourceCorpusMode.EXISTING,
            source_corpus_relpath=".lean_constellation/source",
            interface_inputs=[],
        ),
    )
    assert written.ok
    (repo_root / ".lean_constellation" / "source").mkdir(parents=True, exist_ok=True)
    assert lean_runtime.repo_workspace.initialize_repo_as_native(repo_root, project_name="Repo").ok
    initialized = lean_runtime.node.ensure_native_root_main_contract(repo_root)
    assert initialized.ok
    committed = lean_runtime.node.commit_scope_contract(repo_root, scope_path="Main", summary="Main scope complete.")
    assert committed.ok
    assert lean_runtime.lean_projection.refresh_node_projection(repo_root, node_path="Main").ok
    flow_id = _start_coordinator(runtime, repo_root)

    runtime.agent_service.queue_submission(
        CoordinatorRepoReadySubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_repo_ready",
            tool_name="submit_repo_ready",
            repo_key="Repo",
            summary="Repo exposes a completed small formalization.",
        )
    )
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "mark_repo_ready"

    monkeypatch.setattr(
        lean_runtime.repo_workspace.metadata,
        "set_repo_summary",
        lambda *args, **kwargs: lean_runtime.foundation.fail(
            lean_runtime.foundation.issue("injected_postcommit_summary", "summary save failed")
        ),
    )
    ready_step_id = _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result.outcome == "candidate_prepared"
    assert flow.result.prepared_release is not None
    ready = lean_runtime.repo_workspace.metadata.get_repo_publication(repo_root)
    assert ready.ok and ready.value.publication.status == RepoPublicationStatus.STABLE
    availability = lean_runtime.repo_workspace.provider_availability.check_provider_available(repo_root)
    assert availability.ok and availability.value is not None and availability.value.passed is True
    publication = lean_runtime.repo_workspace.metadata.get_repo_publication(repo_root)
    assert publication.ok and publication.value is not None
    assert publication.value.publication.status == RepoPublicationStatus.STABLE
    assert publication.value.publication.latest_release_id == flow.result.prepared_release.release.release_id
    model = lean_runtime.repo_workspace.metadata.get_repo_model(repo_root)
    assert model.ok and model.value.summary is None

    def _raise_release_read(*args, **kwargs):  # noqa: ANN001, ANN202
        del args, kwargs
        raise OSError("injected committed-release read failure")

    monkeypatch.setattr(lean_runtime.repo_workspace.release, "get_release", _raise_release_read)
    ready_step = runtime.flow_service.get_step(ready_step_id)
    flow.after_step_terminal_stable(
        StableStepTerminalContext(ark=runtime.ark, app=runtime.app, flow=flow, step=ready_step)
    )
    retried = runtime.flow_service.get_flow(flow_id)
    assert retried.status is FlowStatus.COMPLETED
    assert retried.error is None
