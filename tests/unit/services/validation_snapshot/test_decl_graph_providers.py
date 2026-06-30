from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.external_clients import ExternalCommandResult, LeanDiagnosticsResult
from lean_constellation.services.foundation import GateReport, ServiceResult
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
        end_after_state=DeclState.PROVED,
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


def test_content_ready_gate_uses_default_decl_graph_provider_pass(tmp_path: Path) -> None:
    runtime = _runtime()
    _seed_ready_public_theorem(runtime, tmp_path)

    ready = runtime.validation_snapshot.check_content_node_ready(tmp_path, node_path=NODE_PATH)

    assert ready.ok, ready.issues
    assert ready.value is not None
    assert ready.value.passed is True
    assert runtime.validation_snapshot.readiness_gate.content_readiness_provider is runtime.decl_graph


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
