from __future__ import annotations

from pathlib import Path

from lean_constellation.flows.content_node_task.decl_round.steps import DeclStageReviewerStepState
from lean_constellation.services import create_test_runtime_services
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.tool_facade import ActorContext, DeclStageContextView, NodeContextView, RepoContextView, RuntimeToolContext, ToolExecutionContext
from lean_constellation.tools import build_application_tool_specs
from tests.unit_services_helpers import initialize_native_test_repo, lean_check_payload, write_statement_formal_for_test
from tests.unit.services.lean_projection.test_formal_stage_sync import (
    _runtime as _formal_runtime,
    _setup_theorem_round,
    _write_statement_target,
)
from lean_constellation.tools.args import (
    DeclStageFileCheckArgs,
    MathlibDeclDependencyAddArgs,
    MathlibDeclDependencyInput,
    MathlibDeclDependenciesAddArgs,
    NoArgs,
    ProofFormalReviewPassedArgs,
    ProofFormalReviewRejectedArgs,
    ProofNlReviewPassedArgs,
    ProofNlReviewRejectedArgs,
    ProofNlSetArgs,
    ProofResourceOriginAddArgs,
    ProofSourceOriginAddArgs,
    RepoDeclDependencyInput,
    RepoDeclDependenciesAddArgs,
    StatementDepsClearArgs,
    StatementFormalReviewPassedArgs,
    StatementFormalReviewRejectedArgs,
    StatementNlReviewPassedArgs,
    StatementNlReviewRejectedArgs,
    StatementNlSetArgs,
    StatementSourceOriginAddArgs,
)
from lean_constellation.tools.internal.decl_stage import (
    _check_file_capture_sync,
    _check_formal_stage_consistency,
    _inspect_current_stage_review_status,
    _record_proof_formal_review_passed,
    _record_proof_formal_review_rejected,
    _record_statement_formal_review_passed,
    _record_statement_formal_review_rejected,
    _record_proof_nl_review_passed,
    _record_proof_nl_review_rejected,
    _record_statement_nl_review_passed,
    _record_statement_nl_review_rejected,
    _add_proof_mathlib_dependencies,
    _add_proof_repo_dependencies,
    _add_proof_resource_origin,
    _add_proof_source_origin,
    _add_statement_mathlib_dependencies,
    _add_statement_mathlib_dependency,
    _add_statement_repo_dependencies,
    _add_statement_source_origin,
    _set_proof_nl,
    _clear_statement_deps,
    _set_statement_nl,
)
from tests.unit.tools._family_helpers import assert_group_contains, assert_tools_registered


def test_decl_stage_tools_are_registered() -> None:
    expected = {
        "set_statement_nl",
        "add_statement_source_origin",
        "add_statement_resource_origin",
        "remove_statement_origin",
        "clear_statement_origins",
        "list_statement_dependencies",
        "add_statement_repo_dependency",
        "add_statement_repo_dependencies",
        "add_statement_mathlib_dependency",
        "add_statement_mathlib_dependencies",
        "remove_statement_dep",
        "clear_statement_deps",
        "set_proof_nl",
        "add_proof_source_origin",
        "add_proof_resource_origin",
        "remove_proof_origin",
        "clear_proof_origins",
        "list_proof_dependencies",
        "add_proof_repo_dependency",
        "add_proof_repo_dependencies",
        "add_proof_mathlib_dependency",
        "add_proof_mathlib_dependencies",
        "remove_proof_dep",
        "clear_proof_deps",
        "prepare_statement_formal_file",
        "capture_statement_formal_file",
        "prepare_proof_formal_file",
        "capture_proof_formal_file",
        "check_decl_file_snapshot_sync",
        "check_formal_stage_consistency",
        "run_lean_file_diagnostics",
        "scan_lean_sorry_axiom",
        "check_statement_formal_policy",
        "check_proof_formal_policy",
        "inspect_current_stage_review_status",
        "record_statement_nl_review_passed",
        "record_statement_nl_review_rejected",
        "record_statement_formal_review_passed",
        "record_statement_formal_review_rejected",
        "record_proof_nl_review_passed",
        "record_proof_nl_review_rejected",
        "record_proof_formal_review_passed",
        "record_proof_formal_review_rejected",
    }

    assert_tools_registered(expected)


def test_decl_stage_projection_reset_delete_tools_are_not_application_specs() -> None:
    names = {spec.name for spec in build_application_tool_specs()}

    assert "sync_decl_file_after_revision_reset" not in names
    assert "remove_decl_file_for_delete" not in names


def test_legacy_generic_review_tool_is_not_application_spec() -> None:
    names = {spec.name for spec in build_application_tool_specs()}

    assert "record_decl_review" not in names
    assert "record_statement_nl_review_passed" in names
    assert "record_statement_formal_review_passed" in names
    assert "record_proof_nl_review_passed" in names
    assert "record_proof_formal_review_passed" in names


def test_decl_stage_groups_expose_expected_tools() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    assert_group_contains(
        "decl_stage_statement_nl_write",
        {
            "set_statement_nl",
            "add_statement_source_origin",
            "add_statement_resource_origin",
            "remove_statement_origin",
            "clear_statement_origins",
        },
    )
    assert_group_contains(
        "decl_statement_dependency_read",
        {"list_statement_dependencies"},
    )
    assert_group_contains(
        "decl_statement_repo_dependency_write",
        {
            "add_statement_repo_dependency",
            "add_statement_repo_dependencies",
            "remove_statement_dep",
            "clear_statement_deps",
        },
    )
    assert_group_contains(
        "decl_statement_mathlib_dependency_write",
        {"add_statement_mathlib_dependency", "add_statement_mathlib_dependencies"},
    )
    assert_group_contains(
        "decl_stage_proof_nl_write",
        {
            "set_proof_nl",
            "add_proof_source_origin",
            "add_proof_resource_origin",
            "remove_proof_origin",
            "clear_proof_origins",
        },
    )
    assert_group_contains(
        "decl_proof_dependency_read",
        {"list_proof_dependencies"},
    )
    assert_group_contains(
        "decl_proof_repo_dependency_write",
        {
            "add_proof_repo_dependency",
            "add_proof_repo_dependencies",
            "remove_proof_dep",
            "clear_proof_deps",
        },
    )
    assert_group_contains(
        "decl_proof_mathlib_dependency_write",
        {"add_proof_mathlib_dependency", "add_proof_mathlib_dependencies"},
    )
    assert_group_contains(
        "decl_formal_consistency_read",
        {"check_decl_file_snapshot_sync", "check_formal_stage_consistency"},
    )
    assert_group_contains("decl_stage_statement_formal_file_write", {"prepare_statement_formal_file", "capture_statement_formal_file"})
    assert_group_contains("decl_stage_proof_formal_file_write", {"prepare_proof_formal_file", "capture_proof_formal_file"})
    assert_group_contains("decl_stage_review_status_read", {"inspect_current_stage_review_status"})
    assert_group_contains(
        "decl_stage_statement_nl_review_mark_write",
        {"record_statement_nl_review_passed", "record_statement_nl_review_rejected"},
    )
    assert_group_contains(
        "decl_stage_statement_formal_review_mark_write",
        {"record_statement_formal_review_passed", "record_statement_formal_review_rejected"},
    )
    assert_group_contains(
        "decl_stage_proof_nl_review_mark_write",
        {"record_proof_nl_review_passed", "record_proof_nl_review_rejected"},
    )
    assert_group_contains(
        "decl_stage_proof_formal_review_mark_write",
        {"record_proof_formal_review_passed", "record_proof_formal_review_rejected"},
    )
    assert_group_contains("lean_file_diagnostics_read", {"run_lean_file_diagnostics", "scan_lean_sorry_axiom"})
    assert_group_contains("statement_formal_policy_read", {"check_statement_formal_policy"})
    assert_group_contains("proof_formal_policy_read", {"check_proof_formal_policy"})
    statement_group = runtime.tool_facade.list_registered_tools(group_key="statement_formal_policy_read")
    proof_group = runtime.tool_facade.list_registered_tools(group_key="proof_formal_policy_read")
    assert statement_group.ok and statement_group.value is not None
    assert proof_group.ok and proof_group.value is not None
    assert "check_proof_formal_policy" not in {tool.name for tool in statement_group.value}
    assert "check_statement_formal_policy" not in {tool.name for tool in proof_group.value}


class _FakeReviewerStep:
    step_id = "review_step_1"
    step_type = "decl_stage_reviewer_agent_step"

    def __init__(self, *, stage: str = "statement_nl", agent_type: str = "StatementNLReviewerAgent", agent_role: str = "statement_nl_reviewer") -> None:
        self.state = DeclStageReviewerStepState(
            agent_role=agent_role,
            agent_type=agent_type,
            stage=stage,
        )


class _FakeStepStore:
    def __init__(self, step: _FakeReviewerStep | None = None) -> None:
        self.step = step or _FakeReviewerStep()

    def get_step(self, step_id: str):
        assert step_id == "review_step_1"
        return self.step

    def update_step_record(self, step_id: str, mutator):
        assert step_id == "review_step_1"
        mutator(self.step)
        return self.step


class _FakeStepService:
    def __init__(self, step: _FakeReviewerStep | None = None) -> None:
        self.store = _FakeStepStore(step)


def _review_ctx(repo_root: Path, *, round_id: str, batch_decls: list[str] | None = None, stage: str = "statement_nl") -> ToolExecutionContext:
    batch_decls = batch_decls if batch_decls is not None else ["main_result"]
    agent_type_by_stage = {
        "statement_nl": "StatementNLReviewerAgent",
        "statement_formal": "StatementFormalReviewerAgent",
        "proof_nl": "ProofNLReviewerAgent",
        "proof_formal": "ProofFormalReviewerAgent",
    }
    view_by_stage = {
        "statement_nl": "statement_nl_reviewer",
        "statement_formal": "statement_formal_reviewer",
        "proof_nl": "proof_nl_reviewer",
        "proof_formal": "proof_formal_reviewer",
    }
    agent_type = agent_type_by_stage[stage]
    view = view_by_stage[stage]
    return ToolExecutionContext(
        runtime=RuntimeToolContext(
            flow_id="flow_1",
            step_id="review_step_1",
            agent_id="agent_1",
            agent_type=agent_type,
            agent_role="reviewer",
            expected_view_key=view,
            repo_root=repo_root,
            node_path="Main.Topic.Core",
            stage=stage,
            round_id=round_id,
            batch_decls=batch_decls,
        ),
        endpoint_view_key=view,
        expected_view_key=view,
        repo_root=repo_root,
        repo=RepoContextView(repo_key=repo_root.name, summary="repo"),
        node=NodeContextView(node_path="Main.Topic.Core", node_kind="content", summary="node"),
        decl_stage=DeclStageContextView(stage=stage, round_id=round_id, batch_decls=batch_decls, summary="stage"),
        actor=ActorContext(agent_type=agent_type, role="reviewer", added_by="worker", summary="reviewer"),
    )


def _formal_ctx(repo_root: Path, *, stage: str, role: str = "worker", batch_decls: list[str] | None = None, round_id: str = "round_1") -> ToolExecutionContext:
    agent_type_by_stage = {
        "statement_formal": "StatementFormalWorkerAgent",
        "proof_formal": "ProofFormalWorkerAgent",
        "statement_nl": "StatementNLWorkerAgent",
        "proof_nl": "ProofNLWorkerAgent",
    }
    worker_view_by_stage = {
        "statement_formal": "statement_formal_worker",
        "proof_formal": "proof_formal_worker",
        "statement_nl": "statement_nl_worker",
        "proof_nl": "proof_nl_worker",
    }
    reviewer_view_by_stage = {
        "statement_formal": "statement_formal_reviewer",
        "proof_formal": "proof_formal_reviewer",
        "proof_nl": "proof_nl_reviewer",
    }
    agent_type = agent_type_by_stage[stage]
    view = reviewer_view_by_stage[stage] if role == "reviewer" else worker_view_by_stage[stage]
    return ToolExecutionContext(
        runtime=RuntimeToolContext(
            flow_id="flow_1",
            step_id="step_1",
            agent_id="agent_1",
            agent_type=agent_type,
            agent_role=role,  # type: ignore[arg-type]
            expected_view_key=view,
            repo_root=repo_root,
            node_path="Main.Topic.Core",
            stage=stage,
            round_id=round_id,
            batch_decls=batch_decls if batch_decls is not None else ["main_result"],
        ),
        endpoint_view_key=view,
        expected_view_key=view,
        repo_root=repo_root,
        repo=RepoContextView(repo_key=repo_root.name, summary="repo"),
        node=NodeContextView(node_path="Main.Topic.Core", node_kind="content", summary="node"),
        decl_stage=DeclStageContextView(stage=stage, round_id=round_id, batch_decls=batch_decls if batch_decls is not None else ["main_result"], summary="stage"),
        actor=ActorContext(agent_type=agent_type, role=role, added_by="worker" if role == "reviewer" else role, summary=role),
    )


def _formal_review_ctx(repo_root: Path, *, round_id: str, batch_decls: list[str] | None = None) -> ToolExecutionContext:
    batch_decls = batch_decls if batch_decls is not None else ["main_result"]
    return ToolExecutionContext(
        runtime=RuntimeToolContext(
            flow_id="flow_1",
            step_id="review_step_1",
            agent_id="agent_1",
            agent_type="StatementFormalReviewerAgent",
            agent_role="reviewer",
            expected_view_key="statement_formal_reviewer",
            repo_root=repo_root,
            node_path="Main.Topic.Core",
            stage="statement_formal",
            round_id=round_id,
            batch_decls=batch_decls,
        ),
        endpoint_view_key="statement_formal_reviewer",
        expected_view_key="statement_formal_reviewer",
        repo_root=repo_root,
        repo=RepoContextView(repo_key=repo_root.name, summary="repo"),
        node=NodeContextView(node_path="Main.Topic.Core", node_kind="content", summary="node"),
        decl_stage=DeclStageContextView(stage="statement_formal", round_id=round_id, batch_decls=batch_decls, summary="stage"),
        actor=ActorContext(agent_type="StatementFormalReviewerAgent", role="reviewer", added_by="worker", summary="reviewer"),
    )


def _create_local_resource(runtime, repo_root: Path) -> str:
    target_file = repo_root / "proof-resource.md"
    target_file.write_text("Proof resource.\n", encoding="utf-8")
    target = runtime.material.prepare_resource_target(
        target_kind="local_file",
        target=str(target_file),
    )
    assert target.ok and target.value is not None
    draft = runtime.material.allocate_resource_draft(repo_root, target=target.value)
    assert draft.ok and draft.value is not None
    Path(draft.value.readme_path).write_text("Resource notes.\n", encoding="utf-8")
    Path(draft.value.normalized_dir, "main.md").write_text("Proof route support.\n", encoding="utf-8")
    promoted = runtime.material.submit_local_resource_created(
        repo_root,
        target=target.value,
        draft_id=draft.value.draft.draft_id,
        summary="Curated proof resource.",
        classification_reason="This file is supporting proof material.",
        resource_role="Proof background.",
        consumer_formalization_scope="The current repo owns the formal proof.",
    )
    assert promoted.ok and promoted.value is not None
    assert promoted.value.resource_key is not None
    return promoted.value.resource_key


def test_statement_nl_typed_tools_write_text_origins_and_deps(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    initialize_native_test_repo(tmp_path)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(tmp_path, path="Main.Topic", goal="Topic", boundary="Topic boundary").ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core",
        boundary="Core boundary",
        objective="Objective",
        success_criteria="Ready",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Strategy")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round",
    )
    assert round_record.ok and round_record.value is not None
    for decl_name in ["main_result", "supporting_statement"]:
        created = runtime.decl_graph.create_decl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            name=decl_name,
            kind="theorem",
            objective=f"Create {decl_name}",
            summary=decl_name,
            target_state=DeclState.DECLARED,
        )
        assert created.ok
    assert runtime.decl_graph.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    advanced = runtime.decl_graph.advance_stage_state(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        stage="statement_formal",
        decl_names=["supporting_statement"],
    )
    assert advanced.ok, advanced.issues
    ctx = _formal_ctx(tmp_path, stage="statement_nl", round_id=round_record.value.round_id, batch_decls=["main_result"])

    statement = _set_statement_nl(
        runtime,
        ctx,
        StatementNlSetArgs(decl_name="main_result", text="The main result states True."),
    )
    assert statement.ok, statement.issues
    assert statement.value is not None
    assert statement.value.model_dump(mode="json") == {
        "target": "current node / main_result / Statement NL",
        "operation": "set",
        "changed": True,
    }
    prepared = runtime.lean_projection.prepare_statement_formal_stage_file(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    )
    assert prepared.ok, prepared.issues
    assert prepared.value is not None
    prepared_path = Path(prepared.value.path)
    before_nl_metadata = prepared_path.read_bytes()

    origin = _add_statement_source_origin(
        runtime,
        ctx,
        StatementSourceOriginAddArgs(decl_name="main_result", source_path="notes.md", start_line=2, end_line=4, note="Definition range."),
    )
    assert origin.ok, origin.issues
    assert origin.value is not None
    assert origin.value.changed is True
    assert origin.value.added[0].kind == "source"
    assert origin.value.managed_projection is None
    assert prepared_path.read_bytes() == before_nl_metadata

    dep = _add_statement_repo_dependencies(
        runtime,
        ctx,
        RepoDeclDependenciesAddArgs(
            decl_name="main_result",
            dependencies=[
                RepoDeclDependencyInput(
                    name="supporting_statement",
                    reason="Statement uses supporting notation.",
                )
            ],
        ),
    )
    assert dep.ok, dep.issues
    assert dep.value is not None
    assert dep.value.added[0].ref.name == "supporting_statement"
    assert dep.value.managed_projection is None
    assert prepared_path.read_bytes() == before_nl_metadata
    stored = runtime.decl_graph.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name="main_result", revision=1)
    assert stored.ok and stored.value is not None
    assert stored.value.statement.deps[0].kind == "repo_decl"
    assert stored.value.statement.deps[0].reason == "Statement uses supporting notation."
    missing_mathlib = _add_statement_mathlib_dependencies(
        runtime,
        ctx,
        MathlibDeclDependenciesAddArgs(
            decl_name="main_result",
            dependencies=[
                MathlibDeclDependencyInput(name="Nat.missingName", module="Mathlib.Data.Nat.Basic")
            ],
        ),
    )
    assert not missing_mathlib.ok
    assert missing_mathlib.issues[0].kind == "toolkit_unavailable"
    assert runtime.mathlib.upsert_mathlib_module_entry(
        tmp_path,
        module="Mathlib.Data.Nat.Basic",
        summary="Natural number basics.",
    ).ok
    assert runtime.mathlib.upsert_mathlib_decl_entry(
        tmp_path,
        name="Nat.succ",
        module="Mathlib.Data.Nat.Basic",
        kind="def",
        signature="Nat → Nat",
        summary="Successor function.",
    ).ok

    mathlib = _add_statement_mathlib_dependencies(
        runtime,
        ctx,
        MathlibDeclDependenciesAddArgs(
            decl_name="main_result",
            dependencies=[
                MathlibDeclDependencyInput(
                    name="Nat.succ",
                    module="Mathlib.Data.Nat.Basic",
                    reason="Statement uses successor.",
                )
            ],
        ),
    )
    assert mathlib.ok, mathlib.issues
    assert mathlib.value is not None
    assert mathlib.value.added[0].ref.name == "Nat.succ"
    assert mathlib.value.managed_projection is None
    assert prepared_path.read_bytes() == before_nl_metadata
    stored = runtime.decl_graph.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name="main_result", revision=1)
    assert stored.ok and stored.value is not None
    assert {item.kind for item in stored.value.statement.deps} == {"repo_decl", "mathlib_decl"}

    refreshed = runtime.lean_projection.prepare_statement_formal_stage_file(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    )
    assert refreshed.ok and refreshed.value is not None
    refreshed_text = prepared_path.read_text(encoding="utf-8")
    assert "notes.md" in refreshed_text
    assert "supporting_statement" in refreshed_text
    assert "Nat.succ" in refreshed_text


def test_statement_nl_reviewer_can_add_only_a_verified_mathlib_dependency(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    initialize_native_test_repo(tmp_path)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(
        tmp_path,
        path="Main.Topic",
        goal="Topic",
        boundary="Topic boundary",
    ).ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core",
        boundary="Core boundary",
        objective="Objective",
        success_criteria="Ready",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        objective="Strategy",
    )
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round",
    )
    assert round_record.ok and round_record.value is not None
    created = runtime.decl_graph.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        name="main_result",
        kind="theorem",
        objective="Create main_result",
        summary="Main result.",
        target_state=DeclState.DECLARED,
    )
    assert created.ok
    assert runtime.decl_graph.start_round(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
    ).ok
    assert runtime.mathlib.upsert_mathlib_module_entry(
        tmp_path,
        module="Mathlib.Data.Nat.Basic",
        summary="Natural number basics.",
    ).ok
    assert runtime.mathlib.upsert_mathlib_decl_entry(
        tmp_path,
        name="Nat.succ",
        module="Mathlib.Data.Nat.Basic",
        kind="def",
        signature="Nat → Nat",
        summary="Successor.",
    ).ok

    reviewer_ctx = _review_ctx(
        tmp_path,
        stage="statement_nl",
        round_id=round_record.value.round_id,
        batch_decls=["main_result"],
    )
    added = _add_statement_mathlib_dependency(
        runtime,
        reviewer_ctx,
        MathlibDeclDependencyAddArgs(
            decl_name="main_result",
            name="Nat.succ",
            module="Mathlib.Data.Nat.Basic",
            reason="The reviewer found one missing exact Mathlib dependency.",
        ),
    )
    assert added.ok, added.issues
    assert added.value is not None
    assert added.value.dependency_stage == "statement"
    assert added.value.added[0].ref.name == "Nat.succ"
    assert added.value.mathlib_index is not None

    rejected = _add_statement_mathlib_dependency(
        runtime,
        reviewer_ctx,
        MathlibDeclDependencyAddArgs(
            decl_name="outside_batch",
            name="Nat.succ",
            module="Mathlib.Data.Nat.Basic",
        ),
    )
    assert not rejected.ok
    assert rejected.issues[0].kind == "decl_stage_dependency_repair_rejected"


def test_statement_formal_reviewer_dependency_add_recaptures_managed_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = _formal_runtime()
    round_id = _setup_theorem_round(tmp_path, runtime)
    prepared = runtime.lean_projection.prepare_statement_formal_stage_file(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    )
    assert prepared.ok and prepared.value is not None
    _write_statement_target(Path(prepared.value.path))
    captured = runtime.lean_projection.capture_statement_formal(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    )
    assert captured.ok, captured.issues
    assert runtime.mathlib.upsert_mathlib_decl_entry(
        tmp_path,
        name="Nat.succ",
        module="Mathlib.Data.Nat.Basic",
        kind="def",
        signature="Nat → Nat",
        summary="Successor.",
    ).ok

    added = _add_statement_mathlib_dependency(
        runtime,
        _review_ctx(
            tmp_path,
            stage="statement_formal",
            round_id=round_id,
            batch_decls=["main_result"],
        ),
        MathlibDeclDependencyAddArgs(
            decl_name="main_result",
            name="Nat.succ",
            module="Mathlib.Data.Nat.Basic",
            reason="The formal statement uses successor.",
        ),
    )

    assert added.ok, added.issues
    assert added.value is not None
    assert added.value.formal_capture_refreshed is True
    sync = runtime.lean_projection.check_decl_file_snapshot_sync(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
        stage="statement",
    )
    assert sync.ok and sync.value is not None and sync.value.passed
    revision = runtime.decl_graph.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=1,
    )
    assert revision.ok and revision.value is not None
    assert revision.value.statement.formal is not None
    assert revision.value.statement.formal.code == Path(prepared.value.path).read_text(
        encoding="utf-8"
    )

    def fail_unexpected_recapture(*args, **kwargs):
        del args, kwargs
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "unexpected_recapture",
                "An already-present dependency must not trigger another capture.",
            )
        )

    monkeypatch.setattr(
        runtime.lean_projection.safe_apply,
        "_capture",
        fail_unexpected_recapture,
    )
    already_present = _add_statement_mathlib_dependency(
        runtime,
        _review_ctx(
            tmp_path,
            stage="statement_formal",
            round_id=round_id,
            batch_decls=["main_result"],
        ),
        MathlibDeclDependencyAddArgs(
            decl_name="main_result",
            name="Nat.succ",
            module="Mathlib.Data.Nat.Basic",
            reason="The formal statement uses successor.",
        ),
    )
    assert already_present.ok, already_present.issues
    assert already_present.value is not None
    assert already_present.value.formal_capture_refreshed is None


def test_statement_formal_worker_dependency_add_remains_explicit_capture(
    tmp_path: Path,
) -> None:
    runtime = _formal_runtime()
    round_id = _setup_theorem_round(tmp_path, runtime)
    prepared = runtime.lean_projection.prepare_statement_formal_stage_file(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    )
    assert prepared.ok and prepared.value is not None
    _write_statement_target(Path(prepared.value.path))
    assert runtime.lean_projection.capture_statement_formal(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    ).ok
    assert runtime.mathlib.upsert_mathlib_decl_entry(
        tmp_path,
        name="Nat.succ",
        module="Mathlib.Data.Nat.Basic",
        kind="def",
        signature="Nat → Nat",
        summary="Successor.",
    ).ok

    added = _add_statement_mathlib_dependency(
        runtime,
        _formal_ctx(
            tmp_path,
            stage="statement_formal",
            round_id=round_id,
        ),
        MathlibDeclDependencyAddArgs(
            decl_name="main_result",
            name="Nat.succ",
            module="Mathlib.Data.Nat.Basic",
            reason="The formal statement uses successor.",
        ),
    )

    assert added.ok, added.issues
    assert added.value is not None
    assert added.value.formal_capture_refreshed is None
    sync = runtime.lean_projection.check_decl_file_snapshot_sync(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
        stage="statement",
    )
    assert sync.ok and sync.value is not None
    assert not sync.value.passed


def test_proof_nl_worker_repo_dependency_add_defers_projection_until_formal_prepare(
    tmp_path: Path,
) -> None:
    runtime = _formal_runtime()
    initialize_native_test_repo(tmp_path)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(
        tmp_path,
        path="Main.Topic",
        goal="Topic",
        boundary="Topic boundary",
    ).ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core",
        boundary="Core boundary",
        objective="Objective",
        success_criteria="Ready",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        objective="Strategy",
    )
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round",
    )
    assert round_record.ok and round_record.value is not None
    for decl_name in ["main_result", "proved_helper"]:
        created = runtime.decl_graph.create_decl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            name=decl_name,
            kind="theorem",
            objective=f"Create {decl_name}",
            summary=decl_name,
            target_state=DeclState.PROVED,
        )
        assert created.ok
    assert runtime.decl_graph.start_round(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
    ).ok
    for decl_name in ["main_result", "proved_helper"]:
        assert runtime.decl_graph.write_statement_nl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            decl_name=decl_name,
            nl=f"{decl_name} states True.",
        ).ok
    helper_statement_file = runtime.lean_projection.prepare_statement_formal_stage_file(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="proved_helper",
    )
    assert helper_statement_file.ok and helper_statement_file.value is not None
    helper_path = Path(helper_statement_file.value.path)
    helper_path.write_text(
        helper_path.read_text(encoding="utf-8")
        + "theorem provedHelper : True := by\n  sorry\n",
        encoding="utf-8",
    )
    assert runtime.lean_projection.capture_statement_formal(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="proved_helper",
    ).ok
    assert runtime.decl_graph.set_proof_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        decl_name="proved_helper",
        nl="Trivial.",
    ).ok
    helper_proof_file = runtime.lean_projection.prepare_proof_formal_stage_file(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="proved_helper",
    )
    assert helper_proof_file.ok and helper_proof_file.value is not None
    helper_path.write_text(
        helper_path.read_text(encoding="utf-8").replace("sorry", "trivial"),
        encoding="utf-8",
    )
    assert runtime.lean_projection.capture_proof_formal(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="proved_helper",
    ).ok
    assert runtime.decl_graph.advance_stage_state(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        stage="proof_formal",
        decl_names=["proved_helper"],
    ).ok

    statement_file = runtime.lean_projection.prepare_statement_formal_stage_file(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    )
    assert statement_file.ok and statement_file.value is not None
    _write_statement_target(Path(statement_file.value.path))
    assert runtime.lean_projection.capture_statement_formal(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    ).ok
    assert runtime.decl_graph.set_proof_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        decl_name="main_result",
        nl="Use proved_helper.",
    ).ok
    statement_path = Path(statement_file.value.path)
    before_nl_dependency = statement_path.read_bytes()

    added = _add_proof_repo_dependencies(
        runtime,
        _formal_ctx(
            tmp_path,
            stage="proof_nl",
            round_id=round_record.value.round_id,
            batch_decls=["main_result"],
        ),
        RepoDeclDependenciesAddArgs(
            decl_name="main_result",
            dependencies=[
                RepoDeclDependencyInput(
                    name="proved_helper",
                    reason="The route uses the proved helper.",
                )
            ],
        ),
    )

    assert added.ok, added.issues
    assert added.value is not None
    assert added.value.formal_capture_refreshed is None
    assert added.value.managed_projection is None
    assert statement_path.read_bytes() == before_nl_dependency
    statement_sync = runtime.lean_projection.check_decl_file_snapshot_sync(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
        stage="statement",
    )
    assert statement_sync.ok and statement_sync.value is not None
    assert statement_sync.value.passed

    proof_file = runtime.lean_projection.prepare_proof_formal_stage_file(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    )
    assert proof_file.ok and proof_file.value is not None
    prepared_text = Path(proof_file.value.path).read_text(encoding="utf-8")
    assert "Use proved_helper." in prepared_text
    assert "proved_helper" in prepared_text


def test_statement_formal_reviewer_dependency_recapture_rolls_back_on_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = _formal_runtime()
    round_id = _setup_theorem_round(tmp_path, runtime)
    prepared = runtime.lean_projection.prepare_statement_formal_stage_file(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    )
    assert prepared.ok and prepared.value is not None
    path = Path(prepared.value.path)
    _write_statement_target(path)
    assert runtime.lean_projection.capture_statement_formal(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    ).ok
    assert runtime.mathlib.upsert_mathlib_decl_entry(
        tmp_path,
        name="Nat.succ",
        module="Mathlib.Data.Nat.Basic",
        kind="def",
        signature="Nat → Nat",
        summary="Successor.",
    ).ok
    before_file = path.read_bytes()
    before_revision = runtime.decl_graph.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=1,
    )
    assert before_revision.ok and before_revision.value is not None
    before_revision_json = before_revision.value.model_dump(mode="json")

    def fail_capture(*args, **kwargs):
        del args, kwargs
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "injected_capture_failure",
                "Injected capture failure.",
            )
        )

    monkeypatch.setattr(
        runtime.lean_projection.safe_apply,
        "_capture",
        fail_capture,
    )
    added = _add_statement_mathlib_dependency(
        runtime,
        _review_ctx(
            tmp_path,
            stage="statement_formal",
            round_id=round_id,
            batch_decls=["main_result"],
        ),
        MathlibDeclDependencyAddArgs(
            decl_name="main_result",
            name="Nat.succ",
            module="Mathlib.Data.Nat.Basic",
            reason="The formal statement uses successor.",
        ),
    )

    assert not added.ok
    assert {issue.kind for issue in added.issues} == {"injected_capture_failure"}
    assert path.read_bytes() == before_file
    after_revision = runtime.decl_graph.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=1,
    )
    assert after_revision.ok and after_revision.value is not None
    assert after_revision.value.model_dump(mode="json") == before_revision_json


def test_proof_formal_reviewer_dependency_add_recaptures_managed_projection(
    tmp_path: Path,
) -> None:
    runtime = _formal_runtime()
    round_id = _setup_theorem_round(tmp_path, runtime)
    statement = runtime.lean_projection.prepare_statement_formal_stage_file(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    )
    assert statement.ok and statement.value is not None
    _write_statement_target(Path(statement.value.path))
    assert runtime.lean_projection.capture_statement_formal(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    ).ok
    assert runtime.decl_graph.write_proof_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        nl="Use triviality.",
        origin=[{"kind": "unit_test"}],
        deps=[],
    ).ok
    proof = runtime.lean_projection.prepare_proof_formal_stage_file(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    )
    assert proof.ok and proof.value is not None
    proof_path = Path(proof.value.path)
    proof_path.write_text(
        proof_path.read_text(encoding="utf-8").replace("sorry", "trivial"),
        encoding="utf-8",
    )
    assert runtime.lean_projection.capture_proof_formal(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    ).ok
    assert runtime.mathlib.upsert_mathlib_decl_entry(
        tmp_path,
        name="True.intro",
        module="Mathlib.Init.Logic",
        kind="theorem",
        signature="True",
        summary="Constructor for True.",
    ).ok

    added = _add_proof_mathlib_dependencies(
        runtime,
        _review_ctx(
            tmp_path,
            stage="proof_formal",
            round_id=round_id,
            batch_decls=["main_result"],
        ),
        MathlibDeclDependenciesAddArgs(
            decl_name="main_result",
            dependencies=[
                MathlibDeclDependencyInput(
                    name="True.intro",
                    module="Mathlib.Init.Logic",
                    reason="The proof closes the trivial goal.",
                )
            ],
        ),
    )

    assert added.ok, added.issues
    assert added.value is not None
    assert added.value.formal_capture_refreshed is True
    sync = runtime.lean_projection.check_decl_file_snapshot_sync(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
        stage="proof",
    )
    assert sync.ok and sync.value is not None and sync.value.passed


def test_proof_nl_typed_tools_write_text_origins_and_deps(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    initialize_native_test_repo(tmp_path)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(tmp_path, path="Main.Topic", goal="Topic", boundary="Topic boundary").ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core",
        boundary="Core boundary",
        objective="Objective",
        success_criteria="Ready",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Strategy")
    assert strategy.ok and strategy.value is not None
    definition_round = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Create a definition used by the later proof round.",
    )
    assert definition_round.ok and definition_round.value is not None
    definition = runtime.decl_graph.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=definition_round.value.round_id,
        name="declared_definition",
        kind="definition",
        objective="Create declared_definition",
        summary="A definition accepted before the theorem proof round.",
        target_state=DeclState.DECLARED,
    )
    assert definition.ok and definition.value is not None
    assert runtime.decl_graph.start_round(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=definition_round.value.round_id,
    ).ok
    assert runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=definition_round.value.round_id,
        decl_name="declared_definition",
        nl="declared_definition is a unit-valued definition.",
    ).ok
    assert write_statement_formal_for_test(
        runtime,
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=definition_round.value.round_id,
        decl_name="declared_definition",
        lean_code="def declared_definition : Unit := ()",
        lean_check=lean_check_payload(),
    ).ok
    assert runtime.decl_graph.advance_stage_state(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=definition_round.value.round_id,
        stage="statement_formal",
        decl_names=["declared_definition"],
    ).ok
    assert runtime.decl_graph.write_decl_change_summary(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=definition_round.value.round_id,
        change_id=definition.value.change_id,
        summary="Created the accepted definition.",
    ).ok
    assert runtime.decl_graph.write_round_summary(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=definition_round.value.round_id,
        summary="The definition is available to later proof rounds.",
    ).ok
    assert runtime.decl_graph.record_round_execution_result(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=definition_round.value.round_id,
        outcome="completed",
    ).ok
    assert runtime.decl_graph.closeout_round_by_plan(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=definition_round.value.round_id,
        result_kind="success",
        acknowledged_by="test-content-plan",
    ).ok
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round",
    )
    assert round_record.ok and round_record.value is not None
    for decl_name in ["main_result", "proved_helper"]:
        created = runtime.decl_graph.create_decl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            name=decl_name,
            kind="theorem",
            objective=f"Create {decl_name}",
            summary=decl_name,
            target_state=DeclState.PROVED,
        )
        assert created.ok
    assert runtime.decl_graph.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    for decl_name in ["main_result", "proved_helper"]:
        assert runtime.decl_graph.write_statement_nl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            decl_name=decl_name,
            nl=f"{decl_name} statement.",
        ).ok
        assert write_statement_formal_for_test(runtime,
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            decl_name=decl_name,
            lean_code=f"theorem {decl_name} : True := by trivial",
            lean_check=lean_check_payload(),
        ).ok
    advanced = runtime.decl_graph.advance_stage_state(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        stage="proof_formal",
        decl_names=["proved_helper"],
    )
    assert advanced.ok, advanced.issues
    ctx = _formal_ctx(tmp_path, stage="proof_nl", round_id=round_record.value.round_id, batch_decls=["main_result"])

    proof = _set_proof_nl(
        runtime,
        ctx,
        ProofNlSetArgs(decl_name="main_result", text="Use proved_helper and finish by trivial."),
    )
    assert proof.ok, proof.issues
    assert proof.value is not None
    assert proof.value.model_dump(mode="json") == {
        "target": "current node / main_result / Proof NL",
        "operation": "set",
        "changed": True,
    }

    missing_source = _add_proof_source_origin(
        runtime,
        ctx,
        ProofSourceOriginAddArgs(decl_name="main_result", source_path="proof.md", start_line=3, end_line=5, note="Proof argument."),
    )
    assert not missing_source.ok
    assert missing_source.issues[0].kind == "proof_origin_source_index_missing"
    resource_key = _create_local_resource(runtime, tmp_path)
    origin = _add_proof_resource_origin(
        runtime,
        ctx,
        ProofResourceOriginAddArgs(decl_name="main_result", resource_key=resource_key, start_locator="main.md:1", note="Proof resource."),
    )
    assert origin.ok, origin.issues
    assert origin.value is not None
    assert origin.value.added[0].kind == "resource"
    assert origin.value.added[0].resource_key == resource_key

    dep = _add_proof_repo_dependencies(
        runtime,
        ctx,
        RepoDeclDependenciesAddArgs(
            decl_name="main_result",
            dependencies=[
                RepoDeclDependencyInput(name="proved_helper", reason="Main proof uses helper.")
            ],
        ),
    )
    assert dep.ok, dep.issues
    assert dep.value is not None
    assert dep.value.added[0].kind == "repo_decl"
    assert dep.value.added[0].reason == "Main proof uses helper."
    stored = runtime.decl_graph.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name="main_result", revision=1)
    assert stored.ok and stored.value is not None
    assert stored.value.proof.deps[0].kind == "repo_decl"
    assert stored.value.proof.deps[0].reason == "Main proof uses helper."

    definition_dep = _add_proof_repo_dependencies(
        runtime,
        ctx,
        RepoDeclDependenciesAddArgs(
            decl_name="main_result",
            dependencies=[
                RepoDeclDependencyInput(
                    name="declared_definition",
                    reason="Main proof uses the accepted definition.",
                )
            ],
        ),
    )
    assert definition_dep.ok, definition_dep.issues
    assert definition_dep.value is not None
    assert definition_dep.value.added[0].ref.name == "declared_definition"

    missing_mathlib = _add_proof_mathlib_dependencies(
        runtime,
        ctx,
        MathlibDeclDependenciesAddArgs(
            decl_name="main_result",
            dependencies=[
                MathlibDeclDependencyInput(name="Nat.missingLemma", module="Mathlib.Data.Nat.Basic")
            ],
        ),
    )
    assert not missing_mathlib.ok
    assert missing_mathlib.issues[0].kind == "toolkit_unavailable"
    assert runtime.mathlib.upsert_mathlib_module_entry(
        tmp_path,
        module="Mathlib.Data.Nat.Basic",
        summary="Natural number basics.",
    ).ok
    assert runtime.mathlib.upsert_mathlib_decl_entry(
        tmp_path,
        name="Nat.succ",
        module="Mathlib.Data.Nat.Basic",
        kind="def",
        signature="Nat → Nat",
        summary="Successor function.",
    ).ok
    wrong_module = _add_proof_mathlib_dependencies(
        runtime,
        ctx,
        MathlibDeclDependenciesAddArgs(
            decl_name="main_result",
            dependencies=[
                MathlibDeclDependencyInput(
                    name="Nat.succ",
                    module="Mathlib.Init",
                    reason="Wrong module.",
                )
            ],
        ),
    )
    assert not wrong_module.ok
    assert wrong_module.issues[0].kind == "mathlib_decl_module_conflict"
    mathlib = _add_proof_mathlib_dependencies(
        runtime,
        ctx,
        MathlibDeclDependenciesAddArgs(
            decl_name="main_result",
            dependencies=[
                MathlibDeclDependencyInput(
                    name="Nat.succ",
                    module="Mathlib.Data.Nat.Basic",
                    reason="Proof uses successor facts.",
                )
            ],
        ),
    )
    assert mathlib.ok, mathlib.issues
    assert mathlib.value is not None
    assert mathlib.value.added[0].kind == "mathlib_decl"
    assert mathlib.value.added[0].ref.module == "Mathlib.Data.Nat.Basic"


def test_proof_formal_dep_tool_rejects_unproved_same_round_dep(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    initialize_native_test_repo(tmp_path)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(tmp_path, path="Main.Topic", goal="Topic", boundary="Topic boundary").ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core",
        boundary="Core boundary",
        objective="Objective",
        success_criteria="Ready",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Strategy")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round",
    )
    assert round_record.ok and round_record.value is not None
    for decl_name in ["main_result", "unfinished_helper"]:
        created = runtime.decl_graph.create_decl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            name=decl_name,
            kind="theorem",
            objective=f"Create {decl_name}",
            summary=decl_name,
            target_state=DeclState.PROVED,
        )
        assert created.ok
    assert runtime.decl_graph.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    for decl_name in ["main_result", "unfinished_helper"]:
        assert runtime.decl_graph.write_statement_nl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            decl_name=decl_name,
            nl=f"{decl_name} statement.",
        ).ok
        assert write_statement_formal_for_test(runtime,
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            decl_name=decl_name,
            lean_code=f"theorem {decl_name} : True := by trivial",
            lean_check=lean_check_payload(),
        ).ok
        assert runtime.decl_graph.set_proof_nl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            decl_name=decl_name,
            nl=f"Proof route for {decl_name}.",
        ).ok

    result = _add_proof_repo_dependencies(
        runtime,
        _formal_ctx(tmp_path, stage="proof_formal", round_id=round_record.value.round_id, batch_decls=["main_result"]),
        RepoDeclDependenciesAddArgs(
            decl_name="main_result",
            dependencies=[
                RepoDeclDependencyInput(
                    name="unfinished_helper",
                    reason="Unfinished same-round helper.",
                )
            ],
        ),
    )

    assert not result.ok
    assert result.issues[0].kind == "proof_dep_same_round_not_proved"


class _FakeFormalDeclGraph:
    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self.calls: list[tuple[str, str]] = []

    def check_formal_stage_consistency(self, repo_root: Path, *, node_path: str, decl_name: str, stage: str):
        del repo_root, node_path
        self.calls.append((decl_name, stage))
        return self.runtime.foundation.ok({"decl_name": decl_name, "stage": stage, "passed": True})


class _FakeLeanProjection:
    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self.calls: list[tuple[str, str]] = []

    def check_decl_file_snapshot_sync(self, repo_root: Path, *, node_path: str, decl_name: str, stage: str):
        del repo_root, node_path
        self.calls.append((decl_name, stage))
        return self.runtime.foundation.ok({"decl_name": decl_name, "stage": stage, "passed": True})


def test_formal_stage_consistency_rejects_cross_stage_checks(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()

    statement = _check_formal_stage_consistency(
        runtime,
        _formal_ctx(tmp_path, stage="statement_formal"),
        DeclStageFileCheckArgs(decl_name="main_result", stage="proof"),
    )
    proof = _check_formal_stage_consistency(
        runtime,
        _formal_ctx(tmp_path, stage="proof_formal"),
        DeclStageFileCheckArgs(decl_name="main_result", stage="statement"),
    )

    assert not statement.ok
    assert statement.issues[0].kind == "decl_stage_formal_read_rejected"
    assert not proof.ok
    assert proof.issues[0].kind == "decl_stage_formal_read_rejected"


def test_formal_file_sync_rejects_cross_stage_reviewer_checks(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()

    statement = _check_file_capture_sync(
        runtime,
        _formal_ctx(tmp_path, stage="statement_formal", role="reviewer"),
        DeclStageFileCheckArgs(decl_name="main_result", stage="proof"),
    )
    proof = _check_file_capture_sync(
        runtime,
        _formal_ctx(tmp_path, stage="proof_formal", role="reviewer"),
        DeclStageFileCheckArgs(decl_name="main_result", stage="statement"),
    )

    assert not statement.ok
    assert statement.issues[0].kind == "decl_stage_formal_read_rejected"
    assert not proof.ok
    assert proof.issues[0].kind == "decl_stage_formal_read_rejected"


def test_formal_read_checks_reject_non_formal_stage_and_out_of_batch_decl(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()

    non_formal = _check_formal_stage_consistency(
        runtime,
        _formal_ctx(tmp_path, stage="statement_nl"),
        DeclStageFileCheckArgs(decl_name="main_result", stage="statement"),
    )
    out_of_batch = _check_file_capture_sync(
        runtime,
        _formal_ctx(tmp_path, stage="statement_formal", batch_decls=["other_decl"]),
        DeclStageFileCheckArgs(decl_name="main_result", stage="statement"),
    )

    assert not non_formal.ok
    assert non_formal.issues[0].kind == "decl_stage_formal_read_rejected"
    assert not out_of_batch.ok
    assert out_of_batch.issues[0].kind == "decl_stage_formal_read_rejected"


def test_formal_read_checks_normalize_stage_and_call_underlying_services(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    fake_decl_graph = _FakeFormalDeclGraph(runtime)
    fake_projection = _FakeLeanProjection(runtime)
    runtime.app.decl_graph = fake_decl_graph  # type: ignore[assignment]
    runtime.app.lean_projection = fake_projection  # type: ignore[assignment]

    consistency = _check_formal_stage_consistency(
        runtime,
        _formal_ctx(tmp_path, stage="statement_formal"),
        DeclStageFileCheckArgs(decl_name="main_result", stage="statement_formal"),
    )
    sync = _check_file_capture_sync(
        runtime,
        _formal_ctx(tmp_path, stage="proof_formal"),
        DeclStageFileCheckArgs(decl_name="main_result", stage="proof_formal"),
    )

    assert consistency.ok
    assert sync.ok
    assert fake_decl_graph.calls == [("main_result", "statement")]
    assert fake_projection.calls == [("main_result", "proof")]


def test_formal_read_checks_allow_reviewer_role_on_reviewed_formal_stage(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    fake_projection = _FakeLeanProjection(runtime)
    runtime.app.lean_projection = fake_projection  # type: ignore[assignment]

    sync = _check_file_capture_sync(
        runtime,
        _formal_ctx(tmp_path, stage="statement_formal", role="reviewer"),
        DeclStageFileCheckArgs(decl_name="main_result", stage="statement"),
    )

    assert sync.ok
    assert fake_projection.calls == [("main_result", "statement")]


def test_statement_nl_review_tools_record_marks_and_report_status(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    runtime.ark.step_service = _FakeStepService()
    initialize_native_test_repo(tmp_path)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(tmp_path, path="Main.Topic", goal="Topic", boundary="Topic boundary").ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core",
        boundary="Core boundary",
        objective="Objective",
        success_criteria="Ready",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Strategy")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round",
    )
    assert round_record.ok and round_record.value is not None
    for decl_name in ["main_result", "helper_def"]:
        created = runtime.decl_graph.create_decl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            name=decl_name,
            kind="theorem" if decl_name == "main_result" else "definition",
            objective=f"Create {decl_name}",
            summary=decl_name,
            target_state=DeclState.DECLARED,
        )
        assert created.ok
    assert runtime.decl_graph.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    ctx = _review_ctx(tmp_path, round_id=round_record.value.round_id, batch_decls=["main_result", "helper_def"])

    passed = _record_statement_nl_review_passed(
        runtime,
        ctx,
        StatementNlReviewPassedArgs(decl_name="main_result", summary="Main statement is acceptable."),
    )
    assert passed.ok, passed.issues
    status = _inspect_current_stage_review_status(runtime, ctx, NoArgs())
    assert status.ok and status.value is not None
    assert status.value["passed_decl_names"] == ["main_result"]
    assert status.value["missing_decl_names"] == ["helper_def"]
    assert status.value["ready_to_submit"] is False

    rejected = _record_statement_nl_review_rejected(
        runtime,
        ctx,
        StatementNlReviewRejectedArgs(
            decl_name="helper_def",
            summary="Helper definition statement is underspecified.",
            issue_categories=["origin_gap"],
            required_changes=["Attach the exact source origin and quantify the output."],
        ),
    )
    assert rejected.ok, rejected.issues
    status = _inspect_current_stage_review_status(runtime, ctx, NoArgs())
    assert status.ok and status.value is not None
    assert status.value["passed_decl_names"] == ["main_result"]
    assert status.value["failed_decl_names"] == ["helper_def"]
    assert status.value["missing_decl_names"] == []
    assert status.value["ready_to_submit"] is True


def test_proof_nl_review_tools_record_marks_and_report_status(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    runtime.ark.step_service = _FakeStepService(
        _FakeReviewerStep(stage="proof_nl", agent_type="ProofNLReviewerAgent", agent_role="proof_nl_reviewer")
    )
    initialize_native_test_repo(tmp_path)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(tmp_path, path="Main.Topic", goal="Topic", boundary="Topic boundary").ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core",
        boundary="Core boundary",
        objective="Objective",
        success_criteria="Ready",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Strategy")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round",
    )
    assert round_record.ok and round_record.value is not None
    for decl_name in ["main_result", "helper_result"]:
        created = runtime.decl_graph.create_decl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            name=decl_name,
            kind="theorem",
            objective=f"Create {decl_name}",
            summary=decl_name,
            target_state=DeclState.PROVED,
        )
        assert created.ok
    assert runtime.decl_graph.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    ctx = _review_ctx(tmp_path, round_id=round_record.value.round_id, batch_decls=["main_result", "helper_result"], stage="proof_nl")

    passed = _record_proof_nl_review_passed(
        runtime,
        ctx,
        ProofNlReviewPassedArgs(decl_name="main_result", summary="Route proves the formal statement."),
    )
    assert passed.ok, passed.issues
    rejected = _record_proof_nl_review_rejected(
        runtime,
        ctx,
        ProofNlReviewRejectedArgs(
            decl_name="helper_result",
            summary="Helper route omits a case.",
            issue_categories=["missing_case"],
            required_changes=["Cover the zero case explicitly."],
            recommended_next_action="worker_repairable",
        ),
    )
    assert rejected.ok, rejected.issues
    status = _inspect_current_stage_review_status(runtime, ctx, NoArgs())
    assert status.ok and status.value is not None
    assert status.value["passed_decl_names"] == ["main_result"]
    assert status.value["failed_decl_names"] == ["helper_result"]
    assert status.value["missing_decl_names"] == []
    assert status.value["ready_to_submit"] is True

    invalid = _record_proof_nl_review_rejected(
        runtime,
        ctx,
        ProofNlReviewRejectedArgs(
            decl_name="helper_result",
            summary="Invalid category.",
            issue_categories=["not_a_category"],
            required_changes=["Use a supported category."],
        ),
    )
    assert not invalid.ok
    assert invalid.issues[0].kind == "proof_nl_review_issue_category_invalid"


def test_proof_formal_review_tools_record_marks_with_next_action(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    runtime.ark.step_service = _FakeStepService(
        _FakeReviewerStep(stage="proof_formal", agent_type="ProofFormalReviewerAgent", agent_role="proof_formal_reviewer")
    )
    initialize_native_test_repo(tmp_path)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(tmp_path, path="Main.Topic", goal="Topic", boundary="Topic boundary").ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core",
        boundary="Core boundary",
        objective="Objective",
        success_criteria="Ready",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Strategy")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round",
    )
    assert round_record.ok and round_record.value is not None
    for decl_name in ["main_result", "helper_result"]:
        created = runtime.decl_graph.create_decl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            name=decl_name,
            kind="theorem",
            objective=f"Create {decl_name}",
            summary=decl_name,
            target_state=DeclState.PROVED,
        )
        assert created.ok
    assert runtime.decl_graph.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    ctx = _review_ctx(tmp_path, round_id=round_record.value.round_id, batch_decls=["main_result", "helper_result"], stage="proof_formal")

    passed = _record_proof_formal_review_passed(
        runtime,
        ctx,
        ProofFormalReviewPassedArgs(decl_name="main_result", summary="Formal proof implements the reviewed route."),
    )
    assert passed.ok, passed.issues
    rejected = _record_proof_formal_review_rejected(
        runtime,
        ctx,
        ProofFormalReviewRejectedArgs(
            decl_name="helper_result",
            summary="Formal proof skips a major proof route step.",
            issue_categories=["proof_not_aligned_with_proof_nl"],
            required_changes=["Implement the route's case split explicitly."],
            recommended_next_action="worker_repairable",
        ),
    )
    assert rejected.ok, rejected.issues
    assert rejected.value.recommended_next_action == "worker_repairable"
    status = _inspect_current_stage_review_status(runtime, ctx, NoArgs())
    assert status.ok and status.value is not None
    assert status.value["passed_decl_names"] == ["main_result"]
    assert status.value["failed_decl_names"] == ["helper_result"]
    assert status.value["missing_decl_names"] == []
    assert status.value["ready_to_submit"] is True
    assert status.value["marks"][1]["recommended_next_action"] == "worker_repairable"

    invalid = _record_proof_formal_review_rejected(
        runtime,
        ctx,
        ProofFormalReviewRejectedArgs(
            decl_name="helper_result",
            summary="Invalid next action.",
            issue_categories=["proof_not_aligned_with_proof_nl"],
            required_changes=["Use a supported route."],
            recommended_next_action="not_a_route",
        ),
    )
    assert not invalid.ok
    assert invalid.issues[0].kind == "proof_formal_review_next_action_invalid"


def test_statement_formal_deps_tool_updates_only_current_batch_statement_deps(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    initialize_native_test_repo(tmp_path)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(tmp_path, path="Main.Topic", goal="Topic", boundary="Topic boundary").ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core",
        boundary="Core boundary",
        objective="Objective",
        success_criteria="Ready",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Strategy")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round",
    )
    assert round_record.ok and round_record.value is not None
    for decl_name in ["main_result", "supporting_statement"]:
        created = runtime.decl_graph.create_decl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            name=decl_name,
            kind="theorem",
            objective=f"Create {decl_name}",
            summary=decl_name,
            target_state=DeclState.DECLARED,
        )
        assert created.ok
    assert runtime.decl_graph.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    for decl_name in ["main_result", "supporting_statement"]:
        assert runtime.decl_graph.write_statement_nl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            decl_name=decl_name,
            nl=f"{decl_name} states True.",
        ).ok
    advanced = runtime.decl_graph.advance_stage_state(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        stage="statement_formal",
        decl_names=["supporting_statement"],
    )
    assert advanced.ok, advanced.issues
    ctx = _formal_ctx(tmp_path, stage="statement_formal", round_id=round_record.value.round_id)

    updated = _add_statement_repo_dependencies(
        runtime,
        ctx,
        RepoDeclDependenciesAddArgs(
            decl_name="main_result",
            dependencies=[
                RepoDeclDependencyInput(
                    name="supporting_statement",
                    reason="Needed by formal statement.",
                )
            ],
        ),
    )

    assert updated.ok, updated.issues
    assert updated.value is not None
    assert updated.value.added[0].ref.name == "supporting_statement"
    cleared = _clear_statement_deps(
        runtime,
        ctx,
        StatementDepsClearArgs(decl_name="main_result", reason="No expression deps needed."),
    )
    assert cleared.ok, cleared.issues
    assert cleared.value is not None
    assert cleared.value.removed[0].ref.name == "supporting_statement"

    out_of_batch = _clear_statement_deps(
        runtime,
        _formal_ctx(tmp_path, stage="statement_formal", round_id=round_record.value.round_id, batch_decls=["other_decl"]),
        StatementDepsClearArgs(decl_name="main_result"),
    )

    assert not out_of_batch.ok
    assert out_of_batch.issues[0].kind == "decl_stage_mutation_rejected"


def test_statement_formal_review_tools_record_stage_specific_marks_and_validate_categories(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    runtime.ark.step_service = _FakeStepService(
        _FakeReviewerStep(
            stage="statement_formal",
            agent_type="StatementFormalReviewerAgent",
            agent_role="statement_formal_reviewer",
        )
    )
    initialize_native_test_repo(tmp_path)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(tmp_path, path="Main.Topic", goal="Topic", boundary="Topic boundary").ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core",
        boundary="Core boundary",
        objective="Objective",
        success_criteria="Ready",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Strategy")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round",
    )
    assert round_record.ok and round_record.value is not None
    for decl_name in ["main_result", "helper_def"]:
        created = runtime.decl_graph.create_decl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            name=decl_name,
            kind="theorem" if decl_name == "main_result" else "definition",
            objective=f"Create {decl_name}",
            summary=decl_name,
            target_state=DeclState.DECLARED,
        )
        assert created.ok
    assert runtime.decl_graph.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    ctx = _formal_review_ctx(tmp_path, round_id=round_record.value.round_id, batch_decls=["main_result", "helper_def"])

    passed = _record_statement_formal_review_passed(
        runtime,
        ctx,
        StatementFormalReviewPassedArgs(decl_name="main_result", summary="Formal statement matches the accepted NL."),
    )
    assert passed.ok, passed.issues

    invalid = _record_statement_formal_review_rejected(
        runtime,
        ctx,
        StatementFormalReviewRejectedArgs(
            decl_name="helper_def",
            summary="Unsupported category.",
            issue_categories=["random_problem"],
            required_changes=["Use a supported issue category."],
        ),
    )
    assert not invalid.ok
    assert invalid.issues[0].kind == "statement_formal_review_issue_category_invalid"

    rejected = _record_statement_formal_review_rejected(
        runtime,
        ctx,
        StatementFormalReviewRejectedArgs(
            decl_name="helper_def",
            summary="The formal statement drops an assumption.",
            issue_categories=["missing_hypothesis", "unavailable_repo_decl_dependency", "unresolved_mathlib_dependency"],
            required_changes=["Add the missing hypothesis from the accepted NL statement."],
        ),
    )
    assert rejected.ok, rejected.issues
    status = _inspect_current_stage_review_status(runtime, ctx, NoArgs())
    assert status.ok and status.value is not None
    assert status.value["stage"] == "statement_formal"
    assert status.value["passed_decl_names"] == ["main_result"]
    assert status.value["failed_decl_names"] == ["helper_def"]
    assert status.value["ready_to_submit"] is True
    failed_mark = next(mark for mark in status.value["marks"] if mark["decl_name"] == "helper_def")
    assert failed_mark["issue_categories"] == [
        "missing_hypothesis",
        "unavailable_repo_decl_dependency",
        "unresolved_mathlib_dependency",
    ]
