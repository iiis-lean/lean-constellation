from __future__ import annotations

from pathlib import Path

from lean_constellation.flows.content_node_task.decl_round.steps import DeclStageReviewerStepState
from lean_constellation.services import create_test_runtime_services
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.tool_facade import ActorContext, DeclStageContextView, NodeContextView, RepoContextView, RuntimeToolContext, ToolExecutionContext
from lean_constellation.tools import build_application_tool_specs
from lean_constellation.tools.args import (
    DeclReviewMarkArgs,
    DeclStageFileCheckArgs,
    NoArgs,
    ProofDeclDepAddArgs,
    ProofMathlibDepAddArgs,
    ProofFormalReviewPassedArgs,
    ProofFormalReviewRejectedArgs,
    ProofNlReviewPassedArgs,
    ProofNlReviewRejectedArgs,
    ProofNlSetArgs,
    ProofResourceOriginAddArgs,
    ProofSourceOriginAddArgs,
    StatementDeclDepAddArgs,
    StatementDepsClearArgs,
    StatementMathlibDepAddArgs,
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
    _record_decl_review,
    _record_proof_formal_review_passed,
    _record_proof_formal_review_rejected,
    _record_statement_formal_review_passed,
    _record_statement_formal_review_rejected,
    _record_proof_nl_review_passed,
    _record_proof_nl_review_rejected,
    _record_statement_nl_review_passed,
    _record_statement_nl_review_rejected,
    _add_proof_decl_dep,
    _add_proof_mathlib_dep,
    _add_proof_resource_origin,
    _add_proof_source_origin,
    _add_statement_decl_dep,
    _add_statement_mathlib_dep,
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
        "add_statement_decl_dep",
        "add_statement_mathlib_dep",
        "remove_statement_dep",
        "clear_statement_deps",
        "set_proof_nl",
        "add_proof_source_origin",
        "add_proof_resource_origin",
        "remove_proof_origin",
        "clear_proof_origins",
        "add_proof_decl_dep",
        "add_proof_mathlib_dep",
        "remove_proof_dep",
        "clear_proof_deps",
        "prepare_statement_formal_file",
        "capture_statement_formal_file",
        "prepare_proof_formal_file",
        "capture_proof_formal_file",
        "check_decl_file_snapshot_sync",
        "check_formal_stage_consistency",
        "record_decl_review",
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
            "add_statement_decl_dep",
            "add_statement_mathlib_dep",
            "remove_statement_dep",
            "clear_statement_deps",
        },
    )
    assert_group_contains(
        "decl_stage_proof_nl_write",
        {
            "set_proof_nl",
            "add_proof_source_origin",
            "add_proof_resource_origin",
            "remove_proof_origin",
            "clear_proof_origins",
            "add_proof_decl_dep",
            "add_proof_mathlib_dep",
            "remove_proof_dep",
            "clear_proof_deps",
        },
    )
    assert_group_contains("decl_stage_statement_formal_file", {"check_decl_file_snapshot_sync", "check_formal_stage_consistency"})
    assert_group_contains("decl_stage_statement_formal_file_write", {"prepare_statement_formal_file", "capture_statement_formal_file"})
    assert_group_contains(
        "decl_stage_statement_formal_dep_write",
        {"add_statement_decl_dep", "add_statement_mathlib_dep", "remove_statement_dep", "clear_statement_deps"},
    )
    assert_group_contains("decl_stage_proof_formal_file", {"check_decl_file_snapshot_sync", "check_formal_stage_consistency"})
    assert_group_contains("decl_stage_proof_formal_file_write", {"prepare_proof_formal_file", "capture_proof_formal_file"})
    assert_group_contains(
        "decl_stage_proof_formal_dep_write",
        {"add_proof_decl_dep", "add_proof_mathlib_dep", "remove_proof_dep", "clear_proof_deps"},
    )
    assert_group_contains("decl_stage_review_mark_write", {"record_decl_review"})
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
    assert_group_contains(
        "statement_formal_diagnostics_read",
        {"run_lean_file_diagnostics", "scan_lean_sorry_axiom", "check_statement_formal_policy"},
    )
    assert_group_contains(
        "proof_formal_diagnostics_read",
        {"run_lean_file_diagnostics", "scan_lean_sorry_axiom", "check_proof_formal_policy"},
    )
    statement_group = runtime.tool_facade.list_registered_tools(group_key="statement_formal_diagnostics_read")
    proof_group = runtime.tool_facade.list_registered_tools(group_key="proof_formal_diagnostics_read")
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
    flow_input = runtime.material.submit_resource_request(
        {"repo_root": repo_root, "node_path": "Main.Topic.Core"},
        target_kind="local_file",
        target=str(target_file),
    )
    assert flow_input.ok and flow_input.value is not None
    draft = runtime.material.allocate_resource_draft(repo_root, target=flow_input.value.normalized_target)
    assert draft.ok and draft.value is not None
    Path(draft.value.readme_path).write_text("Resource notes.\n", encoding="utf-8")
    Path(draft.value.normalized_dir, "main.md").write_text("Proof route support.\n", encoding="utf-8")
    promoted = runtime.material.submit_local_resource_created(
        repo_root,
        flow_input=flow_input.value,
        draft_id=draft.value.draft.draft_id,
        summary="Curated proof resource.",
    )
    assert promoted.ok and promoted.value is not None
    assert promoted.value.resource_key is not None
    return promoted.value.resource_key


def test_statement_nl_typed_tools_write_text_origins_and_deps(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
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
            end_after_state=DeclState.DECLARED,
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
        StatementNlSetArgs(decl_name="main_result", nl="The main result states True.", summary="set statement"),
    )
    assert statement.ok, statement.issues
    assert statement.value.statement_nl == "The main result states True."
    assert statement.value.state == DeclState.PLANNED

    origin = _add_statement_source_origin(
        runtime,
        ctx,
        StatementSourceOriginAddArgs(decl_name="main_result", source_path="notes.md", start_line=2, end_line=4, note="Definition range."),
    )
    assert origin.ok, origin.issues
    assert len(origin.value.statement_origin) == 1
    assert origin.value.statement_origin[0].kind == "source"
    assert origin.value.statement_origin[0].source_path == "notes.md"
    assert origin.value.statement_origin[0].start_line == 2
    assert origin.value.statement_origin[0].end_line == 4
    assert origin.value.statement_origin[0].note == "Definition range."

    dep = _add_statement_decl_dep(
        runtime,
        ctx,
        StatementDeclDepAddArgs(decl_name="main_result", dep_name="supporting_statement", reason="Statement uses supporting notation."),
    )
    assert dep.ok, dep.issues
    assert dep.value.statement_deps == ["supporting_statement"]
    stored = runtime.decl_graph.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name="main_result", revision=1)
    assert stored.ok and stored.value is not None
    assert stored.value.statement.deps[0].kind == "repo_decl"
    assert stored.value.statement.deps[0].reason == "Statement uses supporting notation."
    missing_mathlib = _add_statement_mathlib_dep(
        runtime,
        ctx,
        StatementMathlibDepAddArgs(decl_name="main_result", mathlib_decl_name="Nat.missingName", module="Mathlib.Data.Nat.Basic"),
    )
    assert not missing_mathlib.ok
    assert missing_mathlib.issues[0].kind == "mathlib_decl_entry_missing"
    assert runtime.mathlib.upsert_mathlib_decl_entry(
        tmp_path,
        name="Nat.succ",
        module="Mathlib.Data.Nat.Basic",
        kind="def",
        summary="Successor function.",
    ).ok

    mathlib = _add_statement_mathlib_dep(
        runtime,
        ctx,
        StatementMathlibDepAddArgs(decl_name="main_result", mathlib_decl_name="Nat.succ", module="Mathlib.Data.Nat.Basic", reason="Statement uses successor."),
    )
    assert mathlib.ok, mathlib.issues
    assert mathlib.value.statement_deps == ["Nat.succ", "supporting_statement"]
    stored = runtime.decl_graph.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name="main_result", revision=1)
    assert stored.ok and stored.value is not None
    assert {item.kind for item in stored.value.statement.deps} == {"repo_decl", "mathlib_decl"}


def test_proof_nl_typed_tools_write_text_origins_and_deps(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
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
    for decl_name in ["main_result", "proved_helper"]:
        created = runtime.decl_graph.create_decl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            name=decl_name,
            kind="theorem",
            objective=f"Create {decl_name}",
            summary=decl_name,
            end_after_state=DeclState.PROVED,
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
        assert runtime.decl_graph.write_statement_formal(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            decl_name=decl_name,
            lean_code=f"theorem {decl_name} : True := by trivial",
            lean_check={"status": "passed", "contains_sorry": False, "contains_axiom": False},
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
        ProofNlSetArgs(decl_name="main_result", proof_nl="Use proved_helper and finish by trivial.", summary="set proof"),
    )
    assert proof.ok, proof.issues
    assert proof.value.proof_nl == "Use proved_helper and finish by trivial."
    assert proof.value.state == DeclState.PLANNED

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
    assert len(origin.value.proof_origin) == 1
    assert origin.value.proof_origin[0].kind == "resource"
    assert origin.value.proof_origin[0].resource_key == resource_key

    dep = _add_proof_decl_dep(
        runtime,
        ctx,
        ProofDeclDepAddArgs(decl_name="main_result", dep_name="proved_helper", reason="Main proof uses helper."),
    )
    assert dep.ok, dep.issues
    assert dep.value.proof_deps == ["proved_helper"]
    assert dep.value.proof_dep_refs[0].kind == "repo_decl"
    assert dep.value.proof_dep_refs[0].reason == "Main proof uses helper."
    stored = runtime.decl_graph.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name="main_result", revision=1)
    assert stored.ok and stored.value is not None
    assert stored.value.proof.deps[0].kind == "repo_decl"
    assert stored.value.proof.deps[0].reason == "Main proof uses helper."

    missing_mathlib = _add_proof_mathlib_dep(
        runtime,
        ctx,
        ProofMathlibDepAddArgs(decl_name="main_result", mathlib_decl_name="Nat.missingLemma", module="Mathlib.Data.Nat.Basic"),
    )
    assert not missing_mathlib.ok
    assert missing_mathlib.issues[0].kind == "mathlib_decl_entry_missing"
    assert runtime.mathlib.upsert_mathlib_decl_entry(
        tmp_path,
        name="Nat.succ",
        module="Mathlib.Data.Nat.Basic",
        kind="def",
        summary="Successor function.",
    ).ok
    wrong_module = _add_proof_mathlib_dep(
        runtime,
        ctx,
        ProofMathlibDepAddArgs(decl_name="main_result", mathlib_decl_name="Nat.succ", module="Mathlib.Init", reason="Wrong module."),
    )
    assert not wrong_module.ok
    assert wrong_module.issues[0].kind == "proof_mathlib_dep_module_mismatch"
    mathlib = _add_proof_mathlib_dep(
        runtime,
        ctx,
        ProofMathlibDepAddArgs(decl_name="main_result", mathlib_decl_name="Nat.succ", module="Mathlib.Data.Nat.Basic", reason="Proof uses successor facts."),
    )
    assert mathlib.ok, mathlib.issues
    assert mathlib.value.proof_deps == ["Nat.succ", "proved_helper"]
    assert [item.kind for item in mathlib.value.proof_dep_refs] == ["mathlib_decl", "repo_decl"]
    assert mathlib.value.proof_dep_refs[0].ref.module == "Mathlib.Data.Nat.Basic"


def test_proof_formal_dep_tool_rejects_unproved_same_round_dep(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
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
            end_after_state=DeclState.PROVED,
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
        assert runtime.decl_graph.write_statement_formal(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            decl_name=decl_name,
            lean_code=f"theorem {decl_name} : True := by trivial",
            lean_check={"status": "passed", "contains_sorry": False, "contains_axiom": False},
        ).ok
        assert runtime.decl_graph.set_proof_nl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            decl_name=decl_name,
            nl=f"Proof route for {decl_name}.",
        ).ok

    result = _add_proof_decl_dep(
        runtime,
        _formal_ctx(tmp_path, stage="proof_formal", round_id=round_record.value.round_id, batch_decls=["main_result"]),
        ProofDeclDepAddArgs(decl_name="main_result", dep_name="unfinished_helper", reason="Unfinished same-round helper."),
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


def test_record_decl_review_writes_current_reviewer_step_state(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    runtime.ark.step_service = _FakeStepService()
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
    created = runtime.decl_graph.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        name="main_result",
        kind="theorem",
        objective="Create theorem",
        summary="Theorem",
        end_after_state=DeclState.DECLARED,
    )
    assert created.ok
    assert runtime.decl_graph.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    ctx = _review_ctx(tmp_path, round_id=round_record.value.round_id)

    result = _record_decl_review(
        runtime,
        ctx,
        DeclReviewMarkArgs(
            round_id=round_record.value.round_id,
            decl_name="main_result",
            stage="statement_nl",
            passed=True,
            summary="accepted",
        ),
    )

    assert result.ok, result.issues
    step = runtime.ark.step_service.store.get_step("review_step_1")
    assert [mark.decl_name for mark in step.state.review_marks] == ["main_result"]
    reviews_dir = runtime.decl_graph.graph_store.graph_root(tmp_path, node_path="Main.Topic.Core") / "reviews"
    assert not list(reviews_dir.glob("**/*.json"))


def test_statement_nl_review_tools_record_marks_and_report_status(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    runtime.ark.step_service = _FakeStepService()
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
            end_after_state=DeclState.DECLARED,
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
            end_after_state=DeclState.PROVED,
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
            end_after_state=DeclState.PROVED,
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
            end_after_state=DeclState.DECLARED,
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

    updated = _add_statement_decl_dep(
        runtime,
        ctx,
        StatementDeclDepAddArgs(decl_name="main_result", dep_name="supporting_statement", reason="Needed by formal statement."),
    )

    assert updated.ok, updated.issues
    assert updated.value is not None
    assert updated.value.statement_deps == ["supporting_statement"]
    cleared = _clear_statement_deps(
        runtime,
        ctx,
        StatementDepsClearArgs(decl_name="main_result", reason="No expression deps needed."),
    )
    assert cleared.ok, cleared.issues
    assert cleared.value.statement_deps == []

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
            end_after_state=DeclState.DECLARED,
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
            issue_categories=["missing_hypothesis"],
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
