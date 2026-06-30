from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.decl_graph import DeclReadinessReason, DeclState
from lean_constellation.services.lean_projection.lean_check import (
    LeanCheckView,
    LeanDiagnosticsView,
    SorryAxiomScanView,
)


NODE_PATH = "Main.Topic.Core"


def _create_content_node(tmp_path: Path) -> None:
    runtime = make_runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(
        tmp_path,
        path="Main.Topic",
        goal="Topic goal",
        boundary="Topic boundary",
    ).ok
    assert runtime.node.create_content_node(
        tmp_path,
        path=NODE_PATH,
        goal="Core goal",
        boundary="Core boundary",
        objective="Build the core declarations.",
        success_criteria="The core declarations are ready.",
    ).ok


def _create_round_draft(tmp_path: Path) -> str:
    runtime = make_runtime()
    strategy = runtime.decl_graph.ensure_open_strategy(tmp_path, node_path=NODE_PATH, objective="Strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Round objective.",
    )
    assert round_record.ok and round_record.value is not None
    return round_record.value.round_id


def _start_round(tmp_path: Path, round_id: str) -> None:
    started = make_runtime().decl_graph.start_round(tmp_path, node_path=NODE_PATH, round_id=round_id)
    assert started.ok


def _create_decl(
    tmp_path: Path,
    *,
    round_id: str,
    name: str,
    kind: str = "theorem",
    public: bool = False,
    end_after_state: DeclState = DeclState.PROVED,
) -> None:
    runtime = make_runtime()
    created = runtime.decl_graph.create_decl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        name=name,
        kind=kind,
        objective=f"Create {name}.",
        summary=f"{name} summary.",
        public=public,
        end_after_state=end_after_state,
    )
    assert created.ok


def _prove_theorem(tmp_path: Path, *, round_id: str, name: str, deps: list[str] | None = None) -> None:
    runtime = make_runtime()
    assert runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        nl=f"{name} states True.",
        deps=[],
    ).ok
    assert runtime.decl_graph.write_statement_formal(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        lean_code=f"theorem {name} : True := by\n  sorry",
        lean_check={"status": "passed", "contains_sorry": True, "contains_axiom": False},
        deps=[],
    ).ok
    assert runtime.decl_graph.write_proof_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        nl="The proof is by triviality.",
        deps=deps or [],
    ).ok
    assert runtime.decl_graph.write_proof_formal(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        lean_code=f"theorem {name} : True := by\n  trivial",
        lean_check={"status": "passed", "contains_sorry": False, "contains_axiom": False},
        deps=deps or [],
    ).ok
    assert runtime.decl_graph.commit_decl_revision(tmp_path, node_path=NODE_PATH, name=name, state=DeclState.PROVED).ok


def _declare_definition(tmp_path: Path, *, round_id: str, name: str) -> None:
    runtime = make_runtime()
    assert runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        nl=f"{name} is a unit-valued definition.",
    ).ok
    assert runtime.decl_graph.write_statement_formal(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        lean_code=f"def {name} : Unit := ()",
        lean_check={"status": "passed", "contains_sorry": False, "contains_axiom": False},
    ).ok
    assert runtime.decl_graph.commit_decl_revision(tmp_path, node_path=NODE_PATH, name=name, state=DeclState.DECLARED).ok


def _passed_check() -> LeanCheckView:
    return LeanCheckView(
        status="passed",
        policy="test",
        allow_sorry=False,
        contains_sorry=False,
        contains_axiom=False,
        message="Lean check passed.",
        diagnostics=LeanDiagnosticsView(
            repo_root="/tmp/repo",
            file_path=None,
            passed=True,
            diagnostics=[],
            summary="Diagnostics passed.",
        ),
        scan=SorryAxiomScanView(
            contains_sorry=False,
            contains_admit=False,
            contains_axiom=False,
            contains_opaque=False,
            contains_unsafe=False,
            sorry_count=0,
            admit_count=0,
            axiom_count=0,
            opaque_count=0,
            unsafe_count=0,
            occurrences=[],
            summary="sorry=0, admit=0, axiom=0, opaque=0, unsafe=0",
            limitation="unit test fixture",
        ),
    )


def test_theorem_ready_recurses_through_ready_dependencies(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="supporting_lemma")
    _create_decl(tmp_path, round_id=round_id, name="main_result", public=True)
    _start_round(tmp_path, round_id)
    _prove_theorem(tmp_path, round_id=round_id, name="supporting_lemma")
    _prove_theorem(tmp_path, round_id=round_id, name="main_result", deps=["supporting_lemma"])

    runtime = make_runtime()
    report = runtime.decl_graph.check_decl_ready(tmp_path, node_path=NODE_PATH, decl_name="main_result")

    assert report.ok and report.value is not None
    assert report.value.ready is True
    assert report.value.dependencies_checked == ["supporting_lemma"]


def test_definition_declared_with_statement_check_is_ready(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(
        tmp_path,
        round_id=round_id,
        name="main_def",
        kind="definition",
        public=True,
        end_after_state=DeclState.DECLARED,
    )
    _start_round(tmp_path, round_id)
    _declare_definition(tmp_path, round_id=round_id, name="main_def")

    report = make_runtime().decl_graph.check_decl_ready(tmp_path, node_path=NODE_PATH, decl_name="main_def")

    assert report.ok and report.value is not None
    assert report.value.ready is True


def test_dependency_not_ready_blocks_recursive_readiness(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="supporting_lemma")
    _create_decl(tmp_path, round_id=round_id, name="main_result")
    _start_round(tmp_path, round_id)
    _prove_theorem(tmp_path, round_id=round_id, name="main_result", deps=["supporting_lemma"])

    report = make_runtime().decl_graph.check_decl_ready(tmp_path, node_path=NODE_PATH, decl_name="main_result")

    assert report.ok and report.value is not None
    assert report.value.ready is False
    assert report.value.reason == DeclReadinessReason.DEPENDENCY_NOT_READY
    assert report.value.failed_dependencies == ["supporting_lemma"]


def test_cycle_is_reported_as_not_ready(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="a")
    _create_decl(tmp_path, round_id=round_id, name="b")
    _start_round(tmp_path, round_id)
    _prove_theorem(tmp_path, round_id=round_id, name="a", deps=["b"])
    _prove_theorem(tmp_path, round_id=round_id, name="b", deps=["a"])

    report = make_runtime().decl_graph.check_decl_ready(tmp_path, node_path=NODE_PATH, decl_name="a")

    assert report.ok and report.value is not None
    assert report.value.ready is False
    assert report.value.reason == DeclReadinessReason.CYCLE_DETECTED
    assert report.value.failed_dependencies == ["b"]


def test_default_public_decl_provider_uses_decl_graph(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="public_result", public=True)
    _create_decl(tmp_path, round_id=round_id, name="private_result", public=False)
    _start_round(tmp_path, round_id)
    _prove_theorem(tmp_path, round_id=round_id, name="public_result")
    _prove_theorem(tmp_path, round_id=round_id, name="private_result")

    runtime = make_runtime()
    public = runtime.node.export.list_content_public_decls(tmp_path, node_path=NODE_PATH)

    assert public.ok and public.value is not None
    assert [item.ref.name for item in public.value] == ["public_result"]
    assert public.value[0].source == "decl_graph"
    assert public.value[0].ready is True


def test_decl_graph_is_default_lean_projection_revision_provider(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="main_result")
    _start_round(tmp_path, round_id)
    runtime = make_runtime()
    assert runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
    ).ok

    saved = runtime.lean_projection.decl_file.revision_provider.save_statement_formal_snapshot(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
        code="theorem main_result : True := by\n  sorry",
        check=_passed_check(),
    )

    assert saved.ok and saved.value is not None
    assert saved.value.state == DeclState.DECLARED
    assert saved.value.statement_lean_check is not None
    assert saved.value.statement_lean_check["status"] == "passed"


def test_default_validation_providers_delegate_to_decl_graph(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="public_result", public=True)
    _start_round(tmp_path, round_id)
    _prove_theorem(tmp_path, round_id=round_id, name="public_result")

    runtime = make_runtime()
    content_gate = runtime.validation_snapshot.readiness_gate.content_readiness_provider.check_content_node_ready(
        tmp_path,
        node_path=NODE_PATH,
    )
    formal_gate = runtime.validation_snapshot.consistency.formal_stage_provider.check_formal_stage_consistency(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="public_result",
        stage="proof",
    )
    audit = runtime.validation_snapshot.audit.decl_graph_provider.run_round_local_audit(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        stage="proof_formal",
    )

    assert content_gate.ok and content_gate.value is not None
    assert content_gate.value.passed is True
    assert formal_gate.ok and formal_gate.value is not None
    assert formal_gate.value.passed is True
    assert audit.ok and audit.value is not None
    assert audit.value.audit_name == "round_local_audit"
