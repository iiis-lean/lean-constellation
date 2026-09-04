from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
from threading import Barrier, Event, Lock, Thread
from time import monotonic
from typing import Callable

import pytest
from agent_runtime_kit.agent.models import AgentProviderTurnFailed
from agent_runtime_kit.flow.models import BaseSubmission, FlowRequest, StepStatus

from lean_constellation.app import (
    LeanAdminApi,
    RecoverAgentStepInput,
    RuntimeSemanticAdvanceInput,
    SnapshotCreateInput,
    SnapshotRestoreInput,
    create_app_runtime_services,
    initialize_repo_business_truth,
)
from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import RepoCompletionMode, RepoFormat
from lean_constellation.domain.repo_run import RepoRunContext, RepoRunSpec, SourceScope
from lean_constellation.flows.common.flow_requests import build_content_node_task_request
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeAgentService, FakeAgentTurnResult
from lean_constellation.flows.content_node_task.submissions import (
    ContentNodeBlockedSubmission,
    ContentNodeReadySubmission,
)
from lean_constellation.flows.coordinator.submissions import CoordinatorContentTasksSubmission
from lean_constellation.services.decl_graph.models import (
    DeclNaturalLanguageSection,
    DeclRevisionStatus,
    DeclState,
)
from lean_constellation.services.foundation import WriteMode
from lean_constellation.services.validation_snapshot import ValidationSnapshotService
from tests.unit.flows.coordinator.test_coordinator_flow_loop import (
    FakeConsistencyForReadiness,
    FakeLakeClient,
)
from tests.unit.services.lean_projection.test_service import (
    FakeLake as ProjectionFakeLake,
    FakeToolkit,
)
from tests.unit.services.repo_workspace.test_repo_release import _write_decl
from tests.unit_services_helpers import initialize_native_test_repo, make_runtime


_PROVIDER_NODES = (
    "Main.ProviderA",
    "Main.ProviderB",
    "Main.ProviderC",
    "Main.ProviderD",
)


class _CanaryLake(ProjectionFakeLake, FakeLakeClient):
    pass


class _BatchAgentService(FakeAgentService):
    """Script decisions and hold actual ContentPlan Steps concurrently."""

    def __init__(self, *, runtime) -> None:  # noqa: ANN001 - test bundle.
        super().__init__(ark=runtime.ark, app=runtime.app)
        self.runtime = runtime
        self.coordinator_actions: list[CoordinatorContentTasksSubmission] = []
        self.outcomes: dict[str, str] = {}
        self.ready_actions: dict[str, Callable[[], None]] = {}
        self.barrier: Barrier | None = None
        self.all_started = Event()
        self.release = Event()
        self.lock = Lock()
        self.active = 0
        self.max_active = 0
        self.intervals: dict[str, tuple[float, float]] = {}

    def configure_wave(
        self,
        outcomes: dict[str, str],
        *,
        hold: bool = False,
        ready_actions: dict[str, Callable[[], None]] | None = None,
    ) -> None:
        self.outcomes = dict(outcomes)
        self.ready_actions = dict(ready_actions or {})
        self.barrier = Barrier(len(outcomes))
        self.all_started = Event()
        self.release = Event()
        if not hold:
            self.release.set()
        self.active = 0
        self.max_active = 0
        self.intervals = {}

    def queue_coordinator(self, submission: CoordinatorContentTasksSubmission) -> None:
        self.coordinator_actions.append(submission)

    def wait_agent(
        self,
        agent_id: str,
        *,
        timeout_s: float | None = None,
    ) -> FakeAgentTurnResult:
        del timeout_s
        record = next(item for item in reversed(self.start_records) if item.agent_id == agent_id)
        flow = self.runtime.ark.flow_service.get_flow(record.env["ARK_FLOW_ID"])
        if flow.flow_type == "native_repo_coordinator":
            submission = self.coordinator_actions.pop(0)
            self._accept_submission(record, submission)
            self.agents[agent_id].status = "idle"
            return FakeAgentTurnResult(
                agent_id=agent_id,
                prompt=record.prompt,
                env=record.env,
                submitted=True,
            )

        node_path = flow.input.node_path
        started = monotonic()
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == len(self.outcomes):
                self.all_started.set()
        try:
            assert self.barrier is not None
            self.barrier.wait(timeout=10)
            self.release.wait(timeout=10)
            outcome = self.outcomes[node_path]
            submission: BaseSubmission
            if outcome == "provider_suspended":
                raise AgentProviderTurnFailed(
                    provider_type="scripted",
                    provider_error_type="provider_rate_limit",
                    code="429",
                    retryable=True,
                    run_id=f"run-{node_path}",
                    session_id=f"session-{node_path}",
                    turn_id=f"turn-{node_path}",
                )
            if outcome == "ready":
                action = self.ready_actions.get(node_path)
                if action is not None:
                    action()
                submission = ContentNodeReadySubmission(
                    submission_id=new_submission_id("sub"),
                    submission_type="content_node_ready",
                    tool_name="submit_content_node_ready",
                    repo_key=flow.input.repo_key,
                    node_path=node_path,
                    summary=f"{node_path} ready.",
                )
            else:
                submission = ContentNodeBlockedSubmission(
                    submission_id=new_submission_id("sub"),
                    submission_type="content_node_blocked",
                    tool_name="submit_content_node_blocked",
                    repo_key=flow.input.repo_key,
                    node_path=node_path,
                    reason="Injected business failure.",
                    summary=f"{node_path} blocked.",
                )
            self._accept_submission(record, submission)
            return FakeAgentTurnResult(
                agent_id=agent_id,
                prompt=record.prompt,
                env=record.env,
                submitted=True,
            )
        finally:
            finished = monotonic()
            with self.lock:
                self.active -= 1
                self.intervals[node_path] = (started, finished)
            self.agents[agent_id].status = "idle"


def _prepare_batch_runtime(
    tmp_path: Path,
    *,
    width: int = 4,
    existing_repo_root: Path | None = None,
):
    runtime = create_app_runtime_services(
        runtime_root=tmp_path / ".runtime",
        max_concurrent_flow_advances=width,
        max_concurrent_steps=width,
        start_paused=True,
        external_overrides={
            "lake": _CanaryLake(),
            "lean_mcp_toolkit": FakeToolkit(),
        },
    )
    repo_root = existing_repo_root or tmp_path / "Repo"
    nodes: dict[str, str] = {}
    if existing_repo_root is None:
        assert initialize_repo_business_truth(runtime, repo_root).ok
        assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
        for node_path in _PROVIDER_NODES:
            created = runtime.node.create_content_node(
                repo_root,
                path=node_path,
                goal=f"Build {node_path}.",
                boundary=f"Own {node_path} declarations.",
                objective=f"Complete {node_path}.",
                success_criteria=f"{node_path} is ready.",
            )
            assert created.ok and created.value is not None
            nodes[node_path] = created.value.node_id
    else:
        for node_path in ("Main.Base", *_PROVIDER_NODES, "Main.Consumer"):
            node = runtime.node.node_tree.get_node(repo_root, path=node_path)
            assert node.ok and node.value is not None
            nodes[node_path] = node.value.node_id

    service = _BatchAgentService(runtime=runtime)
    runtime.ark.agent_service = service
    run_context = RepoRunContext(
        start_kind="initial",
        run_spec=RepoRunSpec(
            run_objective="Run deterministic content batch canary.",
            completion_mode=RepoCompletionMode.GRAPH_PROVED,
            source_scope=SourceScope(mode="none"),
            index_policy="reuse",
            root_interface_policy="reuse",
            max_parallel_content_node_tasks=width,
        ),
    )
    coordinator_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id="repo:Repo",
            params={
                "repo_key": "Repo",
                "repo_root": str(repo_root),
                "start_mode": "admin_start",
                "start_reason": "canary",
                "run_context": run_context.model_dump(mode="json"),
            },
        ),
        enqueue=False,
    )
    return runtime, LeanAdminApi(runtime), service, repo_root, coordinator_id, nodes


def _batch_submission(
    *,
    repo_root: Path,
    nodes: dict[str, str],
    selected: tuple[str, ...],
    width: int,
    contract_versions: dict[str, int] | None = None,
) -> CoordinatorContentTasksSubmission:
    return CoordinatorContentTasksSubmission(
        submission_id=new_submission_id("sub"),
        submission_type="coordinator_content_tasks",
        tool_name="submit_content_node_tasks",
        repo_key="Repo",
        node_paths=list(selected),
        requests=[
            build_content_node_task_request(
                repo_key="Repo",
                node_path=node_path,
                scope_id=f"repo:Repo:node:{nodes[node_path]}",
                repo_path=str(repo_root),
                contract_version=(contract_versions or {}).get(node_path, 1),
                max_parallel_content_node_tasks=width,
            )
            for node_path in selected
        ],
        continuation="wait_for_callback",
        summary=f"Run {len(selected)} deterministic canary tasks.",
    )


def _drive_until_paused(runtime, *, timeout_s: float = 20) -> None:  # noqa: ANN001
    deadline = monotonic() + timeout_s
    while not runtime.ark.pause_controller.is_paused(None):
        runtime.ark.schedule_service.schedule_ready()
        if monotonic() >= deadline:
            raise TimeoutError("semantic canary did not reach an auto-pause boundary")
        Event().wait(0.005)


def _start_batch(
    runtime,
    admin: LeanAdminApi,
    service: _BatchAgentService,
    *,
    coordinator_id: str,
    submission: CoordinatorContentTasksSubmission,
    outcomes: dict[str, str],
    hold: bool = False,
    ready_actions: dict[str, Callable[[], None]] | None = None,
):
    if runtime.ark.flow_service.get_flow(coordinator_id).current_step_id is None:
        created = admin.semantic_advance(
            RuntimeSemanticAdvanceInput(
                granularity="step",
                action="logic",
                scope_id="repo:Repo",
            )
        )
        assert created.ok, created.issues
        _drive_until_paused(runtime)
    service.queue_coordinator(submission)
    coordinator_step = runtime.ark.flow_service.get_flow(coordinator_id).current_step_id
    assert coordinator_step is not None
    decided = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(
            granularity="step",
            action="agent",
            step_id=coordinator_step,
        )
    )
    assert decided.ok, decided.issues
    _drive_until_paused(runtime)
    service.configure_wave(outcomes, hold=hold, ready_actions=ready_actions)
    started = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(
            granularity="content_batch",
            repo_key="Repo",
            coordinator_flow_id=coordinator_id,
            expected_source_submission_id=submission.submission_id,
        )
    )
    assert started.ok and started.value is not None, started.issues
    return started.value.lease_id


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_c0_c2_product_content_batch_lease_has_real_overlap(
    tmp_path: Path, width: int
) -> None:
    runtime, admin, service, repo_root, coordinator_id, nodes = _prepare_batch_runtime(
        tmp_path, width=width
    )
    selected = _PROVIDER_NODES[:width]
    submission = _batch_submission(
        repo_root=repo_root, nodes=nodes, selected=selected, width=width
    )
    lease_id = _start_batch(
        runtime,
        admin,
        service,
        coordinator_id=coordinator_id,
        submission=submission,
        outcomes={node_path: "ready" for node_path in selected},
    )
    _drive_until_paused(runtime)

    assert service.max_active == width
    assert max(start for start, _ in service.intervals.values()) <= min(
        finish for _, finish in service.intervals.values()
    )
    lease = runtime.ark.schedule_service.get_run_lease(lease_id)
    assert lease.status == "terminal"
    assert lease.terminal_reason.startswith("content_batch_checkpointed:")
    coordinator = runtime.ark.flow_service.get_flow(coordinator_id)
    callback = runtime.ark.flow_service.get_step(coordinator.current_step_id)
    assert callback.status is StepStatus.CREATED
    assert callback.step_type == "coordinator_agent_step"
    replayed = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(
            granularity="content_batch",
            repo_key="Repo",
            coordinator_flow_id=coordinator_id,
            expected_source_submission_id=submission.submission_id,
        )
    )
    assert replayed.ok, replayed.issues
    _drive_until_paused(runtime)
    replayed_coordinator = runtime.ark.flow_service.get_flow(coordinator_id)
    assert replayed_coordinator.current_step_id == callback.step_id
    callback_steps = [
        runtime.ark.flow_service.get_step(step_id)
        for step_id in replayed_coordinator.step_ids
        if runtime.ark.flow_service.get_step(step_id).step_type
        == "coordinator_agent_step"
        and getattr(
            runtime.ark.flow_service.get_step(step_id).state,
            "prompt_mode",
            None,
        )
        == "callback"
    ]
    assert len(callback_steps) == 1


def test_c3_business_failure_retries_only_failed_node_in_same_runtime(tmp_path: Path) -> None:
    runtime, admin, service, repo_root, coordinator_id, nodes = _prepare_batch_runtime(
        tmp_path, width=4
    )
    first = _batch_submission(
        repo_root=repo_root, nodes=nodes, selected=_PROVIDER_NODES, width=4
    )
    _start_batch(
        runtime,
        admin,
        service,
        coordinator_id=coordinator_id,
        submission=first,
        outcomes={
            node_path: ("blocked" if node_path == "Main.ProviderB" else "ready")
            for node_path in _PROVIDER_NODES
        },
    )
    _drive_until_paused(runtime)
    first_dispatch = runtime.ark.flow_service.get_flow(coordinator_id).state.waiting_dispatch_step_id
    first_children = runtime.ark.flow_service.store.list_child_flows(
        parent_flow_id=coordinator_id,
        parent_dispatch_step_id=first_dispatch,
    )
    stable_hashes = {
        child.input.node_path: hashlib.sha256(
            runtime.ark.flow_service.store.resolve_flow_path(child.flow_id).read_bytes()
        ).hexdigest()
        for child in first_children
        if child.input.node_path != "Main.ProviderB"
    }

    second = _batch_submission(
        repo_root=repo_root,
        nodes=nodes,
        selected=("Main.ProviderB",),
        width=4,
    )
    service.queue_coordinator(second)
    callback_id = runtime.ark.flow_service.get_flow(coordinator_id).current_step_id
    resumed = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(granularity="step", action="agent", step_id=callback_id)
    )
    assert resumed.ok, resumed.issues
    _drive_until_paused(runtime)
    service.configure_wave({"Main.ProviderB": "ready"})
    started = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(
            granularity="content_batch",
            repo_key="Repo",
            coordinator_flow_id=coordinator_id,
            expected_source_submission_id=second.submission_id,
        )
    )
    assert started.ok, started.issues
    _drive_until_paused(runtime)

    assert service.max_active == 1
    assert stable_hashes == {
        child.input.node_path: hashlib.sha256(
            runtime.ark.flow_service.store.resolve_flow_path(child.flow_id).read_bytes()
        ).hexdigest()
        for child in first_children
        if child.input.node_path != "Main.ProviderB"
    }
    all_children = runtime.ark.flow_service.store.list_child_flows(parent_flow_id=coordinator_id)
    assert [child.input.node_path for child in all_children].count("Main.ProviderB") == 2
    assert all(
        [child.input.node_path for child in all_children].count(node_path) == 1
        for node_path in ("Main.ProviderA", "Main.ProviderC", "Main.ProviderD")
    )


def test_c4_c5_suspension_and_active_batch_reject_physical_snapshot(
    tmp_path: Path,
) -> None:
    runtime, admin, service, repo_root, coordinator_id, nodes = _prepare_batch_runtime(
        tmp_path, width=2
    )
    selected = _PROVIDER_NODES[:2]
    submission = _batch_submission(
        repo_root=repo_root, nodes=nodes, selected=selected, width=2
    )
    marker = repo_root / "BatchMarker.txt"
    marker.write_text("before batch\n", encoding="utf-8")
    lease_id = _start_batch(
        runtime,
        admin,
        service,
        coordinator_id=coordinator_id,
        submission=submission,
        outcomes={selected[0]: "provider_suspended", selected[1]: "blocked"},
        hold=True,
    )
    driver = Thread(target=_drive_until_paused, args=(runtime,), daemon=True)
    driver.start()
    assert service.all_started.wait(timeout=10)
    active_view = admin.get_runtime_lease(lease_id)
    assert active_view.ok and active_view.value is not None
    assert active_view.value.content_batch_bookmark is not None
    assert active_view.value.content_batch_bookmark.snapshot_eligible is False
    assert {
        child.node_path
        for child in active_view.value.content_batch_bookmark.children
    } == set(selected)
    before = list(runtime.validation_snapshot.list_repo_checkpoint_snapshots(repo_root).value or [])
    rejected = admin.create_snapshot(
        SnapshotCreateInput(repo_root=repo_root, checkpoint_kind="manual_test_stable_point")
    )
    after = list(runtime.validation_snapshot.list_repo_checkpoint_snapshots(repo_root).value or [])
    assert not rejected.ok
    assert rejected.issues[0].kind == "active_content_batch_snapshot_ineligible"
    assert len(after) == len(before)
    service.release.set()
    driver.join(timeout=20)
    assert not driver.is_alive()
    lease = runtime.ark.schedule_service.get_run_lease(lease_id)
    assert lease.terminal_reason.startswith("content_batch_recovery_required:")
    suspended = [
        step
        for step in runtime.ark.step_service.store.list_steps()
        if step.status is StepStatus.SUSPENDED
    ]
    assert len(suspended) == 1
    recovery = admin.inspect_agent_step_recovery(suspended[0].step_id)
    assert recovery.ok and recovery.value is not None
    assert recovery.value.recovery.available_actions == ["resume_suspended"]
    recovered = admin.recover_agent_step(
        RecoverAgentStepInput(
            step_id=suspended[0].step_id,
            expected_status="suspended",
            expected_recovery_token=recovery.value.recovery.recovery_token,
            action="resume_suspended",
            agent_mode="fresh",
        )
    )
    assert recovered.ok and recovered.value is not None, recovered.issues
    assert recovered.value.replacement_step_id is not None
    service.configure_wave({selected[0]: "blocked"})
    resumed = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(
            granularity="content_batch",
            repo_key="Repo",
            coordinator_flow_id=coordinator_id,
            expected_source_submission_id=submission.submission_id,
        )
    )
    assert resumed.ok, resumed.issues
    _drive_until_paused(runtime)
    assert runtime.ark.flow_service.get_step(
        recovered.value.replacement_step_id
    ).status is StepStatus.COMPLETED
    final_coordinator = runtime.ark.flow_service.get_flow(coordinator_id)
    assert runtime.ark.flow_service.get_step(
        final_coordinator.current_step_id
    ).step_type == "coordinator_agent_step"
    snapshots = runtime.validation_snapshot.list_repo_checkpoint_snapshots(repo_root)
    assert snapshots.ok and snapshots.value is not None
    assert len(snapshots.value) == 2
    pre_batch = next(
        snapshot
        for snapshot in snapshots.value
        if snapshot.checkpoint_kind.value == "before_content_task_dispatch"
    )
    marker.write_text("after batch\n", encoding="utf-8")
    dry_run = admin.restore_snapshot(
        SnapshotRestoreInput(
            repo_root=repo_root,
            snapshot_id=pre_batch.snapshot_id,
            dry_run=True,
        )
    )
    assert dry_run.ok and dry_run.value is not None
    restored = admin.restore_snapshot(
        SnapshotRestoreInput(repo_root=repo_root, snapshot_id=pre_batch.snapshot_id)
    )
    assert restored.ok and restored.value is not None, restored.issues
    assert marker.read_text(encoding="utf-8") == "before batch\n"
    assert runtime.ark.pause_controller.is_paused(None)


def _write_lean_fixture(repo_root: Path) -> list[str]:
    """Add only project entry files around the LC-generated projection."""

    (repo_root / "Canary.lean").write_text(
        "import Canary.Main.Interfaces\n",
        encoding="utf-8",
    )
    (repo_root / "lean-toolchain").write_text("leanprover/lean4:v4.32.0\n", encoding="utf-8")
    (repo_root / "lakefile.toml").write_text(
        'name = "Canary"\nversion = "0.1.0"\ndefaultTargets = ["Canary"]\n\n[[lean_lib]]\nname = "Canary"\n',
        encoding="utf-8",
    )
    return sorted(
        [
            path.relative_to(repo_root).as_posix()
            for path in (repo_root / "Canary").rglob("*.lean")
        ]
        + ["Canary.lean", "lakefile.toml", "lean-toolchain"]
    )


def _create_decl_artifact(
    runtime,
    repo_root: Path,
    *,
    node_path: str,
    name: str,
    statement_deps=(),
    proof_deps=(),
    proposition: str = "True",
    proof_term: str = "trivial",
    revision_number: int = 1,
    prove: bool = True,
    reuse_statement_from_revision: int | None = None,
) -> DeclRef:  # noqa: ANN001
    reused_statement = None
    reused_lean_decl_name = None
    if reuse_statement_from_revision is not None:
        previous = runtime.decl_graph.get_decl_revision(
            repo_root,
            node_path=node_path,
            name=name,
            revision=reuse_statement_from_revision,
        )
        assert previous.ok and previous.value is not None
        reused_statement = previous.value.statement.model_copy(deep=True)
        reused_lean_decl_name = previous.value.lean_decl_name
    _write_decl(
        repo_root,
        node_path=node_path,
        name=name,
        revision=revision_number,
        state=DeclState.PROVED if prove else DeclState.DECLARED,
        statement_deps=statement_deps,
        proof_deps=proof_deps,
    )
    decl = runtime.decl_graph.get_decl(repo_root, node_path=node_path, name=name).value
    decl.module = f"Canary.{node_path}.Theorems.{name}"
    assert runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.decl_record_path(
            repo_root, node_path=node_path, decl_name=name
        ),
        decl,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    revision_path = runtime.decl_graph.graph_store.revision_path(
        repo_root,
        node_path=node_path,
        decl_name=name,
        revision=revision_number,
    )
    revision = runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=node_path,
        name=name,
        revision=revision_number,
    ).value
    revision.status = DeclRevisionStatus.OPEN
    if reused_statement is not None:
        revision.statement = reused_statement
        revision.lean_decl_name = reused_lean_decl_name
    else:
        revision.statement.nl = DeclNaturalLanguageSection(
            text=f"{name} is the public theorem."
        )
    if prove:
        assert revision.proof is not None
        revision.proof.nl = DeclNaturalLanguageSection(text="The claim follows directly.")
    else:
        revision.proof = None
    assert runtime.foundation.store.write_json_atomic(
        revision_path, revision, mode=WriteMode.UPDATE_EXISTING
    ).ok
    if reused_statement is None:
        prepared_statement = runtime.lean_projection.prepare_statement_formal_stage_file(
            repo_root, node_path=node_path, decl_name=name
        )
        assert prepared_statement.ok and prepared_statement.value is not None
        statement_path = Path(prepared_statement.value.path)
        statement_path.write_text(
            statement_path.read_text(encoding="utf-8")
            + f"theorem {name} : {proposition} := by\n  sorry\n",
            encoding="utf-8",
        )
        assert runtime.lean_projection.capture_statement_formal(
            repo_root, node_path=node_path, decl_name=name
        ).ok
    if prove:
        prepared_proof = runtime.lean_projection.prepare_proof_formal_stage_file(
            repo_root, node_path=node_path, decl_name=name
        )
        assert prepared_proof.ok and prepared_proof.value is not None
        proof_path = Path(prepared_proof.value.path)
        proof_path.write_text(
            proof_path.read_text(encoding="utf-8").replace("sorry", proof_term),
            encoding="utf-8",
        )
        assert runtime.lean_projection.capture_proof_formal(
            repo_root, node_path=node_path, decl_name=name
        ).ok
    revision = runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=node_path,
        name=name,
        revision=revision_number,
    ).value
    revision.state = DeclState.PROVED if prove else DeclState.DECLARED
    revision.status = DeclRevisionStatus.COMMITTED
    assert runtime.foundation.store.write_json_atomic(
        revision_path, revision, mode=WriteMode.UPDATE_EXISTING
    ).ok
    return DeclRef(node=node_path, name=name, revision=revision_number)


def _set_open_contract_exports(runtime, repo_root: Path, *, node_path: str, exports: list[DeclRef]) -> None:  # noqa: ANN001
    current = runtime.node.contract.get_edit_contract(repo_root, node_path=node_path)
    assert current.ok and current.value is not None
    current.value.contract.exports = exports
    assert runtime.foundation.store.write_json_atomic(
        runtime.node.node_tree.node_store.contract_path(
            repo_root,
            node_id=current.value.node_id,
            version=current.value.contract.version,
        ),
        current.value.contract,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok


def _seed_release_repo(seed_root: Path) -> Path:
    runtime = make_runtime()
    repo_root = seed_root / "Repo"
    assert initialize_repo_business_truth(runtime, repo_root).ok
    initialize_native_test_repo(repo_root, project_name="Canary")
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
    assert runtime.repo_workspace.metadata.set_repo_format(
        repo_root, repo_format=RepoFormat.NATIVE, reason="canary fixture"
    ).ok
    assert runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Prove the deterministic content batch canary.",
            source_corpus_mode=SourceCorpusMode.NONE,
            source_corpus_relpath=None,
        ),
    ).ok
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    for node_path in ("Main.Base", *_PROVIDER_NODES, "Main.Consumer"):
        created = runtime.node.create_content_node(
            repo_root,
            path=node_path,
            goal=f"Build {node_path}.",
            boundary=f"Own {node_path} public declaration.",
            objective=f"Prove {node_path} result.",
            success_criteria=f"{node_path} result is proved.",
        )
        assert created.ok and created.value is not None
    return repo_root


def _release_and_clean_build(
    work_root: Path,
    clone_root: Path,
    *,
    width: int,
) -> dict[str, str]:
    repo_root = work_root / "Repo"
    runtime, admin, service, repo_root, coordinator_id, nodes = _prepare_batch_runtime(
        work_root,
        width=width,
        existing_repo_root=repo_root,
    )
    all_nodes = ("Main.Base", *_PROVIDER_NODES, "Main.Consumer")

    base_ref = _create_decl_artifact(
        runtime, repo_root, node_path="Main.Base", name="BaseResult"
    )
    _set_open_contract_exports(runtime, repo_root, node_path="Main.Base", exports=[base_ref])
    assert runtime.node.commit_content_contract(
        repo_root, node_path="Main.Base", summary="Commit Base canary contract."
    ).ok
    for node_path in _PROVIDER_NODES:
        dep = runtime.node.add_current_node_dep(
            repo_root,
            node_path=node_path,
            target_node="Main.Base",
            reason="Use the shared base declaration.",
            actor="coordinator",
            expected_public_decl_names=["BaseResult"],
        )
        assert dep.ok, dep.issues
        if node_path in {"Main.ProviderB", "Main.ProviderC"}:
            shallow = runtime.node.contract.set_task_completion_mode_receipt(
                repo_root,
                node_path=node_path,
                task_completion_mode=RepoCompletionMode.GRAPH_DECLARED,
            )
            assert shallow.ok and shallow.value is not None
            assert shallow.value.remaining_repo_gap is True

    initial_waves = (
        tuple((node_path,) for node_path in _PROVIDER_NODES)
        if width == 1
        else (_PROVIDER_NODES,)
    )
    for selected in initial_waves:
        ready_actions: dict[str, Callable[[], None]] = {}
        for node_path in selected:
            def write_initial_provider(node_path: str = node_path) -> None:
                name = f"{node_path.rsplit('.', 1)[-1]}Result"
                prove = node_path not in {"Main.ProviderB", "Main.ProviderC"}
                ref = _create_decl_artifact(
                    runtime,
                    repo_root,
                    node_path=node_path,
                    name=name,
                    statement_deps=(base_ref,),
                    prove=prove,
                )
                _set_open_contract_exports(
                    runtime,
                    repo_root,
                    node_path=node_path,
                    exports=[ref],
                )
                committed = runtime.node.commit_content_contract(
                    repo_root,
                    node_path=node_path,
                    summary=f"Commit {node_path} initial task contract.",
                )
                assert committed.ok, committed.issues

            ready_actions[node_path] = write_initial_provider
        submission = _batch_submission(
            repo_root=repo_root,
            nodes=nodes,
            selected=selected,
            width=width,
        )
        _start_batch(
            runtime,
            admin,
            service,
            coordinator_id=coordinator_id,
            submission=submission,
            outcomes={node_path: "ready" for node_path in selected},
            ready_actions=ready_actions,
        )
        _drive_until_paused(runtime)
        assert service.max_active == len(selected)

    provider_refs_by_node = {
        node_path: DeclRef(
            node=node_path,
            name=f"{node_path.rsplit('.', 1)[-1]}Result",
            revision=1,
        )
        for node_path in _PROVIDER_NODES
    }
    for node_path in ("Main.ProviderB", "Main.ProviderC"):
        revision = runtime.decl_graph.get_decl_revision(
            repo_root,
            node_path=node_path,
            name=provider_refs_by_node[node_path].name,
            revision=1,
        )
        assert revision.ok and revision.value is not None
        assert revision.value.state is DeclState.DECLARED
        assert revision.value.statement.formal is not None
        assert revision.value.proof is None

    visible_before_successors = runtime.node.dependency.list_visible_node_boundaries(
        repo_root,
        node_path="Main.Consumer",
    )
    assert visible_before_successors.ok and visible_before_successors.value is not None
    visible_paths = {
        boundary.node_path
        for boundary in visible_before_successors.value.boundaries
    }
    assert {"Main.ProviderA", "Main.ProviderD"} <= visible_paths
    assert {"Main.ProviderB", "Main.ProviderC"}.isdisjoint(visible_paths)
    rejected_partial_provider = runtime.node.add_current_node_dep(
        repo_root,
        node_path="Main.Consumer",
        target_node="Main.ProviderB",
        reason="A shallow task must not satisfy the Consumer dependency.",
        actor="coordinator",
        expected_public_decl_names=["ProviderBResult"],
    )
    assert not rejected_partial_provider.ok
    assert rejected_partial_provider.issues[0].kind == "node_dep_target_not_visible"

    for node_path in ("Main.ProviderB", "Main.ProviderC"):
        proof_provider = (
            provider_refs_by_node["Main.ProviderA"]
            if node_path == "Main.ProviderB"
            else provider_refs_by_node["Main.ProviderB"]
        )
        proof_node_dep = runtime.node.add_current_node_dep(
            repo_root,
            node_path=node_path,
            target_node=proof_provider.node,
            reason="Use the predecessor provider proof in the proved successor.",
            actor="coordinator",
            expected_public_decl_names=[proof_provider.name],
        )
        assert proof_node_dep.ok, proof_node_dep.issues
        promoted_mode = runtime.node.contract.set_task_completion_mode_receipt(
            repo_root,
            node_path=node_path,
            task_completion_mode=RepoCompletionMode.GRAPH_PROVED,
        )
        assert promoted_mode.ok and promoted_mode.value is not None
        successor = runtime.node.contract.ensure_open_contract(
            repo_root,
            node_path=node_path,
        )
        assert successor.ok and successor.value is not None
        assert (
            successor.value.contract.task_completion_mode
            is RepoCompletionMode.GRAPH_PROVED
        )
        promoted_ref: dict[str, DeclRef] = {}

        def write_proved_successor() -> None:
            ref = _create_decl_artifact(
                runtime,
                repo_root,
                node_path=node_path,
                name=provider_refs_by_node[node_path].name,
                revision_number=2,
                statement_deps=(base_ref,),
                proof_deps=(proof_provider,),
                reuse_statement_from_revision=1,
            )
            _set_open_contract_exports(
                runtime,
                repo_root,
                node_path=node_path,
                exports=[ref],
            )
            committed = runtime.node.commit_content_contract(
                repo_root,
                node_path=node_path,
                summary=f"Commit {node_path} proved successor contract.",
            )
            assert committed.ok, committed.issues
            promoted_ref["value"] = ref

        submission = _batch_submission(
            repo_root=repo_root,
            nodes=nodes,
            selected=(node_path,),
            width=width,
            contract_versions={node_path: successor.value.contract.version},
        )
        _start_batch(
            runtime,
            admin,
            service,
            coordinator_id=coordinator_id,
            submission=submission,
            outcomes={node_path: "ready"},
            ready_actions={node_path: write_proved_successor},
        )
        _drive_until_paused(runtime)
        assert "value" in promoted_ref
        proved_revision = runtime.decl_graph.get_decl_revision(
            repo_root,
            node_path=node_path,
            name=promoted_ref["value"].name,
            revision=2,
        )
        assert proved_revision.ok and proved_revision.value is not None
        assert proved_revision.value.state is DeclState.PROVED
        assert proved_revision.value.proof is not None
        assert [dep.ref for dep in proved_revision.value.proof.deps] == [
            proof_provider
        ]
        declared_revision = runtime.decl_graph.get_decl_revision(
            repo_root,
            node_path=node_path,
            name=promoted_ref["value"].name,
            revision=1,
        )
        assert declared_revision.ok and declared_revision.value is not None
        assert declared_revision.value.state is DeclState.DECLARED
        assert declared_revision.value.proof is None
        provider_refs_by_node[node_path] = promoted_ref["value"]

    provider_refs = [provider_refs_by_node[node_path] for node_path in _PROVIDER_NODES]
    for provider_ref in provider_refs:
        dep = runtime.node.add_current_node_dep(
            repo_root,
            node_path="Main.Consumer",
            target_node=provider_ref.node,
            reason="Consume the provider result.",
            actor="coordinator",
            expected_public_decl_names=[provider_ref.name],
        )
        assert dep.ok, dep.issues
    consumer_ref_box: dict[str, DeclRef] = {}

    def write_consumer() -> None:
        consumer_ref = _create_decl_artifact(
            runtime,
            repo_root,
            node_path="Main.Consumer",
            name="ConsumerResult",
            statement_deps=tuple(provider_refs),
            proof_deps=tuple(provider_refs),
            proposition="True ∧ True ∧ True ∧ True",
            proof_term=(
                "exact ⟨ProviderAResult, ProviderBResult, "
                "ProviderCResult, ProviderDResult⟩"
            ),
        )
        _set_open_contract_exports(
            runtime,
            repo_root,
            node_path="Main.Consumer",
            exports=[consumer_ref],
        )
        committed = runtime.node.commit_content_contract(
            repo_root,
            node_path="Main.Consumer",
            summary="Commit Consumer canary contract.",
        )
        assert committed.ok, committed.issues
        consumer_ref_box["value"] = consumer_ref

    consumer_submission = _batch_submission(
        repo_root=repo_root,
        nodes=nodes,
        selected=("Main.Consumer",),
        width=width,
    )
    _start_batch(
        runtime,
        admin,
        service,
        coordinator_id=coordinator_id,
        submission=consumer_submission,
        outcomes={"Main.Consumer": "ready"},
        ready_actions={"Main.Consumer": write_consumer},
    )
    _drive_until_paused(runtime)
    assert "value" in consumer_ref_box
    consumer_ref = consumer_ref_box["value"]
    _set_open_contract_exports(
        runtime,
        repo_root,
        node_path="Main",
        exports=[base_ref, *provider_refs, consumer_ref],
    )
    assert runtime.node.commit_scope_contract(
        repo_root, scope_path="Main", summary="Commit canary root interface."
    ).ok
    for node_path in ("Main", *all_nodes):
        assert runtime.lean_projection.refresh_node_projection(
            repo_root, node_path=node_path
        ).ok
    runtime.app.validation_snapshot = ValidationSnapshotService(
        runtime,
        consistency=FakeConsistencyForReadiness(runtime.foundation),
    )
    files = _write_lean_fixture(repo_root)
    assert files
    consumer_source = (
        repo_root
        / "Canary/Main/Consumer/Theorems/ConsumerResult.lean"
    ).read_text(encoding="utf-8")
    assert all(ref.name in consumer_source for ref in provider_refs)
    assert "trivial" not in consumer_source
    preview = runtime.validation_snapshot.preview_candidate_release(
        repo_root,
        base_release_id=None,
        summary="Audit deterministic content batch canary.",
    )
    assert preview.ok and preview.value is not None, preview.issues
    assert preview.value.gate.passed, preview.value.gate.issues
    prepared = runtime.validation_snapshot.prepare_candidate_release(
        repo_root,
        base_release_id=None,
        summary="Release deterministic content batch canary.",
        audited=preview.value,
    )
    assert prepared.ok and prepared.value is not None, prepared.issues
    assert prepared.value.outcome == "prepared"
    assert prepared.value.prepared_release is not None
    released = runtime.validation_snapshot.commit_prepared_release(
        repo_root,
        prepared=prepared.value.prepared_release,
    )
    assert released.ok and released.value is not None, released.issues
    assert (
        released.value.release.release.completion_mode
        is RepoCompletionMode.GRAPH_PROVED
    )
    subprocess.run(["git", "clone", "--quiet", str(repo_root), str(clone_root)], check=True, timeout=30)
    subprocess.run(
        ["lake", "build"],
        cwd=clone_root,
        check=True,
        timeout=180,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    root_contract = runtime.node.contract.get_visible_contract(
        repo_root, node_path="Main"
    )
    assert root_contract.ok and root_contract.value is not None
    public_identity = sorted(
        f"{ref.node}:{ref.name}@{ref.revision}"
        for ref in root_contract.value.contract.exports
    )
    source_digest = hashlib.sha256(
        b"".join(
            (repo_root / path).read_bytes()
            for path in sorted(files)
        )
    ).hexdigest()
    return {
        "public_identity": hashlib.sha256(
            "\n".join(public_identity).encode("utf-8")
        ).hexdigest(),
        "source_digest": source_digest,
        "semantic_manifest_digest": released.value.release.release.semantic_manifest_digest,
        "dependency_lock_digest": released.value.release.release.dependency_lock_digest,
        "completion_mode": released.value.release.release.completion_mode.value,
    }


def test_c7_serial_and_four_way_order_release_to_equivalent_clean_builds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN206
            return cls(2026, 9, 4, 12, 0, tzinfo=tz or UTC)

    monkeypatch.setattr("lean_constellation.domain.common.datetime", _FrozenDateTime)
    seed_repo = _seed_release_repo(tmp_path / "seed")
    shutil.copytree(seed_repo, tmp_path / "serial-work" / "Repo")
    shutil.copytree(seed_repo, tmp_path / "parallel-work" / "Repo")
    serial_tree = _release_and_clean_build(
        tmp_path / "serial-work",
        tmp_path / "serial-clone",
        width=1,
    )
    parallel_tree = _release_and_clean_build(
        tmp_path / "parallel-work",
        tmp_path / "parallel-clone",
        width=4,
    )
    assert serial_tree == parallel_tree
