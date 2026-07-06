from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import ProofAvailability, RepoWorkMode
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.decl_graph.models import RepoDeclDep
from lean_constellation.services.external_clients import ExternalCommandResult, LeanDiagnosticsResult
from lean_constellation.services.foundation import GateReport, ServiceResult, WriteMode
from lean_constellation.services.runtime import LeanRuntimeServices
from lean_constellation.services.validation_snapshot.readiness_gate import ReadinessGateComponent


NODE_PATH = "Main.Topic.Core"


class FakeToolkit:
    def run_file_diagnostics(self, repo_root: Path, file_path: Path) -> LeanDiagnosticsResult:
        return LeanDiagnosticsResult(
            ok=True,
            repo_root=str(repo_root),
            file_path=str(file_path),
            diagnostics=[],
            summary="fake toolkit diagnostics",
        )


class FakeLake:
    def run_lake_env_lean(
        self,
        *,
        repo_root: Path,
        rel_file: str,
        json: bool = True,
        timeout_seconds: int | None = None,
    ) -> ExternalCommandResult:
        del json, timeout_seconds
        return ExternalCommandResult(
            ok=True,
            command=["lake", "env", "lean", "--json", rel_file],
            cwd=str(repo_root),
            exit_code=0,
            summary="fake lake diagnostics",
        )


class ProjectionPassConsistency:
    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def check_projection_sync(self, repo_root: Path, *, scope: str = "repo") -> ServiceResult[GateReport]:
        del repo_root
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed("projection_sync", summary=f"Projection sync stub passed for {scope}.")
        )


def _runtime() -> LeanRuntimeServices:
    return make_runtime(external_overrides={"lean_mcp_toolkit": FakeToolkit(), "lake": FakeLake()})


def _create_content_node(runtime: LeanRuntimeServices, repo_root: Path) -> None:
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    created = runtime.node.create_content_node(
        repo_root,
        path=NODE_PATH,
        goal="Core goal.",
        boundary="Core declarations only.",
        objective="Validate DeclGraph provider gates.",
        success_criteria="ValidationSnapshot reads real DeclGraph provider output.",
    )
    assert created.ok, created.issues


def _create_round(runtime: LeanRuntimeServices, repo_root: Path, *, objective: str = "Validation round.") -> str:
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=NODE_PATH, objective="Validation strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective=objective,
    )
    assert round_record.ok and round_record.value is not None
    return round_record.value.round_id


def _create_decl(
    runtime: LeanRuntimeServices,
    repo_root: Path,
    *,
    round_id: str,
    name: str,
    public: bool = True,
    end_after_state: DeclState = DeclState.PROVED,
) -> Any:
    created = runtime.decl_graph.create_decl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        name=name,
        kind="theorem",
        objective=f"Create {name}.",
        summary=f"{name} summary.",
        public=public,
        end_after_state=end_after_state,
    )
    assert created.ok and created.value is not None, created.issues
    return created.value


def _seed_ready_public_theorem(runtime: LeanRuntimeServices, repo_root: Path) -> None:
    _create_content_node(runtime, repo_root)
    round_id = _create_round(runtime, repo_root)
    _create_decl(runtime, repo_root, round_id=round_id, name="main_result", public=True)
    assert runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id).ok
    statement = runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
        origin=[{"kind": "unit_test"}],
        deps=[],
    )
    assert statement.ok, statement.issues
    assert runtime.lean_projection.prepare_statement_formal_stage_file(repo_root, node_path=NODE_PATH, decl_name="main_result").ok
    assert runtime.lean_projection.capture_statement_formal(repo_root, node_path=NODE_PATH, decl_name="main_result").ok
    proof_nl = runtime.decl_graph.write_proof_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="Use triviality.",
        origin=[{"kind": "unit_test"}],
        deps=[],
    )
    assert proof_nl.ok, proof_nl.issues
    prepared_proof = runtime.lean_projection.prepare_proof_formal_stage_file(repo_root, node_path=NODE_PATH, decl_name="main_result")
    assert prepared_proof.ok and prepared_proof.value is not None, prepared_proof.issues
    path = Path(prepared_proof.value.path)
    path.write_text(path.read_text(encoding="utf-8").replace("sorry", "trivial"), encoding="utf-8")
    assert runtime.lean_projection.capture_proof_formal(repo_root, node_path=NODE_PATH, decl_name="main_result").ok
    committed = runtime.decl_graph.commit_decl_revision(repo_root, node_path=NODE_PATH, name="main_result", state=DeclState.PROVED)
    assert committed.ok, committed.issues
    projection = runtime.lean_projection.refresh_node_projection(repo_root, node_path=NODE_PATH)
    assert projection.ok, projection.issues


def _seed_declared_public_theorem(runtime: LeanRuntimeServices, repo_root: Path) -> None:
    _create_content_node(runtime, repo_root)
    round_id = _create_round(runtime, repo_root)
    _create_decl(runtime, repo_root, round_id=round_id, name="main_result", public=True, end_after_state=DeclState.DECLARED)
    assert runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id).ok
    assert runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
        origin=[{"kind": "unit_test"}],
        deps=[],
    ).ok
    assert runtime.decl_graph.write_statement_formal(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        lean_code="theorem main_result : True := by\n  sorry",
        lean_check={"status": "passed", "contains_sorry": True, "allow_sorry": True, "contains_axiom": False},
        deps=[],
    ).ok
    committed = runtime.decl_graph.commit_decl_revision(repo_root, node_path=NODE_PATH, name="main_result", state=DeclState.DECLARED)
    assert committed.ok, committed.issues


def _seed_proved_public_theorem_with_provider_dep(runtime: LeanRuntimeServices, repo_root: Path, *, provider_repo: str) -> None:
    _create_content_node(runtime, repo_root)
    round_id = _create_round(runtime, repo_root)
    _create_decl(runtime, repo_root, round_id=round_id, name="consumer_result", public=True)
    assert runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id).ok
    assert runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="consumer_result",
        nl="The consumer result states True.",
        deps=[],
    ).ok
    assert runtime.decl_graph.write_statement_formal(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="consumer_result",
        lean_code="theorem consumer_result : True := by\n  sorry",
        lean_check={"status": "passed", "contains_sorry": True, "allow_sorry": True, "contains_axiom": False},
        deps=[],
    ).ok
    assert runtime.decl_graph.write_proof_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="consumer_result",
        nl="Use the declared provider theorem as an accepted external interface.",
        deps=[],
    ).ok
    assert runtime.decl_graph.write_proof_formal(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="consumer_result",
        lean_code="theorem consumer_result : True := by\n  trivial",
        lean_check={"status": "passed", "contains_sorry": False, "contains_axiom": False},
        deps=[],
    ).ok
    revision = runtime.decl_graph.get_decl_revision(repo_root, node_path=NODE_PATH, name="consumer_result", revision=1)
    assert revision.ok and revision.value is not None, revision.issues
    assert revision.value.proof is not None
    revision.value.proof.deps = [
        RepoDeclDep(
            ref=DeclRef(
                repo=provider_repo,
                node=NODE_PATH,
                name="main_result",
                revision=1,
            ),
            reason="Provider publishes this theorem at declared proof availability.",
        )
    ]
    path = runtime.decl_graph.graph_store.revision_path(
        repo_root,
        node_path=NODE_PATH,
        decl_name="consumer_result",
        revision=1,
    )
    assert runtime.foundation.store.write_json_atomic(path, revision.value, mode=WriteMode.UPDATE_EXISTING).ok
    committed = runtime.decl_graph.commit_decl_revision(repo_root, node_path=NODE_PATH, name="consumer_result", state=DeclState.PROVED)
    assert committed.ok, committed.issues


def test_content_ready_gate_uses_default_decl_graph_provider_pass(tmp_path: Path) -> None:
    runtime = _runtime()
    _seed_ready_public_theorem(runtime, tmp_path)

    ready = runtime.validation_snapshot.check_content_node_ready(tmp_path, node_path=NODE_PATH)

    assert ready.ok, ready.issues
    assert ready.value is not None
    assert ready.value.passed is True
    assert runtime.validation_snapshot.readiness_gate.content_readiness_provider is runtime.decl_graph


def test_content_completion_accepts_declared_theorem_under_declared_target(tmp_path: Path) -> None:
    runtime = _runtime()
    _seed_declared_public_theorem(runtime, tmp_path)
    configured = runtime.repo_workspace.metadata.update_repo_config(
        tmp_path,
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
    )
    assert configured.ok
    gate = ReadinessGateComponent(
        runtime,
        consistency=ProjectionPassConsistency(runtime),
        content_readiness_provider=runtime.decl_graph,
    )

    completion = gate.check_content_node_completion(tmp_path, node_path=NODE_PATH)

    assert completion.ok and completion.value is not None
    assert completion.value.ready_to_submit is True
    assert completion.value.target_proof_availability == ProofAvailability.DECLARED
    assert completion.value.checked_decl_count == 1
    assert completion.value.gate.gate_name == "content_node_completion"


def test_content_completion_rejects_declared_theorem_under_proved_target(tmp_path: Path) -> None:
    runtime = _runtime()
    _seed_declared_public_theorem(runtime, tmp_path)
    configured = runtime.repo_workspace.metadata.update_repo_config(
        tmp_path,
        target_proof_availability=ProofAvailability.PROVED,
        work_mode=RepoWorkMode.PROVED_FULL_GRAPH,
    )
    assert configured.ok
    gate = ReadinessGateComponent(
        runtime,
        consistency=ProjectionPassConsistency(runtime),
        content_readiness_provider=runtime.decl_graph,
    )

    completion = gate.check_content_node_completion(tmp_path, node_path=NODE_PATH)

    assert completion.ok and completion.value is not None
    assert completion.value.ready_to_submit is False
    assert completion.value.target_proof_availability == ProofAvailability.PROVED
    assert "content_decl_proof_policy_unsatisfied" in completion.value.blocking_issue_kinds


def test_content_completion_accepts_stable_declared_provider_dependency(tmp_path: Path) -> None:
    workspace = tmp_path
    consumer = workspace / "Consumer"
    provider = workspace / "Provider"
    consumer.mkdir()
    runtime = _runtime()
    assert runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    created = runtime.repo_workspace.create_requirement_with_interfaces(
        consumer,
        name="need_provider_result",
        target_repo="Provider",
        source_description="Consumer proof uses Provider.main_result as an external interface.",
        reason="Provider theorem is intentionally requested at declared availability.",
        interfaces=[{"name": "main_result", "kind": "theorem", "summary": "Provider theorem interface."}],
    )
    assert created.ok, created.issues
    waiting = runtime.repo_workspace.requirement.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider_result",
        provider_repo="Provider",
        reason="Coordinator submitted the declared provider request.",
    )
    assert waiting.ok, waiting.issues
    shell = runtime.repo_workspace.create_provider_repo_shell_from_group(workspace, target_repo="Provider")
    assert shell.ok, shell.issues
    _seed_declared_public_theorem(runtime, provider)
    marked = runtime.repo_workspace.mark_provider_repo_ready(provider, summary="Provider publishes declared theorem interface.")
    assert marked.ok, marked.issues
    assert runtime.repo_workspace.requirement.mark_requirement_result_observed(
        consumer,
        requirement_name="need_provider_result",
        note="Consumer resumes from stable declared provider.",
    ).ok
    assert runtime.repo_workspace.metadata.update_repo_config(
        consumer,
        target_proof_availability=ProofAvailability.PROVED,
        work_mode=RepoWorkMode.PROVED_FULL_GRAPH,
    ).ok
    _seed_proved_public_theorem_with_provider_dep(runtime, consumer, provider_repo="Provider")
    gate = ReadinessGateComponent(
        runtime,
        consistency=ProjectionPassConsistency(runtime),
        content_readiness_provider=runtime.decl_graph,
    )

    proof_policy = runtime.decl_graph.check_decl_proof_policy_satisfied(
        consumer,
        node_path=NODE_PATH,
        decl_name="consumer_result",
    )
    completion = gate.check_content_node_completion(consumer, node_path=NODE_PATH)

    assert proof_policy.ok and proof_policy.value is not None
    assert proof_policy.value.proof_policy_satisfied is True
    assert proof_policy.value.dependencies_checked == ["Provider:Main.Topic.Core:main_result"]
    assert completion.ok and completion.value is not None
    assert completion.value.ready_to_submit is True
    assert completion.value.target_proof_availability == ProofAvailability.PROVED


def test_content_ready_gate_preserves_decl_graph_not_ready_issue(tmp_path: Path) -> None:
    runtime = _runtime()
    _create_content_node(runtime, tmp_path)
    round_id = _create_round(runtime, tmp_path)
    _create_decl(runtime, tmp_path, round_id=round_id, name="main_result", public=True)
    assert runtime.decl_graph.start_round(tmp_path, node_path=NODE_PATH, round_id=round_id).ok
    statement = runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
        origin=[{"kind": "unit_test"}],
        deps=[],
    )
    assert statement.ok, statement.issues

    ready_gate = ReadinessGateComponent(
        runtime,
        consistency=ProjectionPassConsistency(runtime),  # type: ignore[arg-type]
        content_readiness_provider=runtime.decl_graph,
    )
    ready = ready_gate.check_content_node_ready(tmp_path, node_path=NODE_PATH)

    assert ready.ok, ready.issues
    assert ready.value is not None
    assert ready.value.passed is False
    assert any(issue.kind == "content_public_decl_not_ready" for issue in ready.value.issues)


def test_round_local_audit_uses_default_decl_graph_provider(tmp_path: Path) -> None:
    runtime = _runtime()
    _create_content_node(runtime, tmp_path)
    round_id = _create_round(runtime, tmp_path)
    _create_decl(runtime, tmp_path, round_id=round_id, name="supporting_lemma", public=False)
    _create_decl(runtime, tmp_path, round_id=round_id, name="main_result", public=False)
    assert runtime.decl_graph.start_round(tmp_path, node_path=NODE_PATH, round_id=round_id).ok
    statement = runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The main result depends on a same-round declaration.",
        origin=[{"kind": "unit_test"}],
        deps=["supporting_lemma"],
    )
    assert statement.ok, statement.issues

    audit = runtime.validation_snapshot.run_round_local_audit(tmp_path, node_path=NODE_PATH, round_id=round_id, stage="statement")

    assert audit.ok, audit.issues
    assert audit.value is not None
    assert audit.value.passed is False
    assert audit.value.findings[0].kind == "round_internal_dependency"


def test_delete_sanity_audit_service_wrapper_uses_default_decl_graph_provider(tmp_path: Path) -> None:
    runtime = _runtime()
    _create_content_node(runtime, tmp_path)
    round_id = _create_round(runtime, tmp_path)
    _create_decl(runtime, tmp_path, round_id=round_id, name="supporting_lemma", public=False)
    _create_decl(runtime, tmp_path, round_id=round_id, name="main_result", public=False)
    assert runtime.decl_graph.start_round(tmp_path, node_path=NODE_PATH, round_id=round_id).ok
    statement = runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The main result depends on the support lemma.",
        origin=[{"kind": "unit_test"}],
        deps=["supporting_lemma"],
    )
    assert statement.ok, statement.issues
    assert runtime.decl_graph.commit_decl_revision(tmp_path, node_path=NODE_PATH, name="supporting_lemma", state=DeclState.PLANNED).ok
    assert runtime.decl_graph.commit_decl_revision(tmp_path, node_path=NODE_PATH, name="main_result", state=DeclState.SPECIFIED).ok
    assert runtime.decl_graph.write_decl_change_summary(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        change_id=runtime.decl_graph.get_round(tmp_path, node_path=NODE_PATH, round_id=round_id).value.change_ids[0],  # type: ignore[union-attr]
        summary="Created support.",
    ).ok
    assert runtime.decl_graph.write_decl_change_summary(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        change_id=runtime.decl_graph.get_round(tmp_path, node_path=NODE_PATH, round_id=round_id).value.change_ids[1],  # type: ignore[union-attr]
        summary="Created main.",
    ).ok
    assert runtime.decl_graph.write_round_summary(tmp_path, node_path=NODE_PATH, round_id=round_id, summary="Seeded dependency.").ok
    assert runtime.decl_graph.mark_round_terminal(tmp_path, node_path=NODE_PATH, round_id=round_id, result_kind="success").ok
    delete_round_id = _create_round(runtime, tmp_path, objective="Delete only support.")
    delete = runtime.decl_graph.mark_decl_delete(
        tmp_path,
        node_path=NODE_PATH,
        round_id=delete_round_id,
        name="supporting_lemma",
        objective="Delete support only.",
    )
    assert delete.ok, delete.issues

    audit = runtime.validation_snapshot.run_delete_sanity_audit(tmp_path, node_path=NODE_PATH, round_id=delete_round_id)

    assert audit.ok, audit.issues
    assert audit.value is not None
    assert audit.value.passed is False
    assert audit.value.findings[0].kind == "delete_closure_incomplete"
