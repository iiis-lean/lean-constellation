from pathlib import Path

from tests.unit_services_helpers import (
    initialize_native_test_repo,
    lean_check_payload,
    make_runtime,
    write_proof_formal_for_test,
    write_statement_formal_for_test,
)

from lean_constellation.domain.repo import ProofAvailability, RepoCompletionMode
from lean_constellation.services.decl_graph import DeclReadinessReason, DeclState
from lean_constellation.services.lean_projection.lean_check import (
    LeanCheckView,
    LeanDiagnosticsView,
    SorryAxiomScanView,
)


NODE_PATH = "Main.Topic.Core"


def _create_content_node(tmp_path: Path) -> None:
    initialize_native_test_repo(tmp_path)
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
    target_state: DeclState = DeclState.PROVED,
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
        target_state=target_state,
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
    assert write_statement_formal_for_test(runtime,
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        lean_code=f"theorem {name} : True := by\n  sorry",
        lean_check=lean_check_payload(contains_sorry=True),
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
    assert write_proof_formal_for_test(runtime,
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        lean_code=f"theorem {name} : True := by\n  trivial",
        lean_check=lean_check_payload(),
        deps=deps or [],
    ).ok
    assert runtime.decl_graph.commit_decl_revision(tmp_path, node_path=NODE_PATH, name=name, state=DeclState.PROVED).ok


def _declare_theorem(tmp_path: Path, *, round_id: str, name: str, deps: list[str] | None = None) -> None:
    runtime = make_runtime()
    assert runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        nl=f"{name} states True.",
        deps=deps or [],
    ).ok
    assert write_statement_formal_for_test(runtime,
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        lean_code=f"theorem {name} : True := by\n  sorry",
        lean_check=lean_check_payload(contains_sorry=True),
        deps=deps or [],
    ).ok
    assert runtime.decl_graph.commit_decl_revision(tmp_path, node_path=NODE_PATH, name=name, state=DeclState.DECLARED).ok


def _declare_definition(tmp_path: Path, *, round_id: str, name: str) -> None:
    runtime = make_runtime()
    assert runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        nl=f"{name} is a unit-valued definition.",
    ).ok
    assert write_statement_formal_for_test(runtime,
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        lean_code=f"def {name} : Unit := ()",
        lean_check=lean_check_payload(),
    ).ok
    assert runtime.decl_graph.commit_decl_revision(tmp_path, node_path=NODE_PATH, name=name, state=DeclState.DECLARED).ok


def _publish_committed_heads(tmp_path: Path, names: list[str]) -> None:
    runtime = make_runtime()
    for round_record in runtime.decl_graph.list_rounds(tmp_path, node_path=NODE_PATH).value:
        if round_record.status.value in {"draft", "running"}:
            for ref in round_record.revision_refs:
                assert runtime.decl_graph.write_decl_change_summary(
                    tmp_path,
                    node_path=NODE_PATH,
                    round_id=round_record.round_id,
                    change_id=ref.change_id,
                    summary=f"Completed {ref.decl_name}.",
                ).ok
            assert runtime.decl_graph.write_round_summary(
                tmp_path,
                node_path=NODE_PATH,
                round_id=round_record.round_id,
                summary="Completed readiness fixture round.",
            ).ok
            assert runtime.decl_graph.strategy_round.record_round_execution_result(
                tmp_path,
                node_path=NODE_PATH,
                round_id=round_record.round_id,
                result_kind="blocked",
                reason="Test fixture committed revisions before round closeout.",
            ).ok
            assert runtime.decl_graph.strategy_round.persist_round_closeout(
                tmp_path,
                node_path=NODE_PATH,
                round_id=round_record.round_id,
                result_kind="blocked",
                reason="Test fixture committed revisions before round closeout.",
                acknowledged_by="test-fixture",
            ).ok
    for name in names:
        assert runtime.lean_projection.sync_decl_file_after_revision_reset(
            tmp_path, node_path=NODE_PATH, decl_name=name
        ).ok
    assert runtime.node.commit_content_contract(
        tmp_path,
        node_path=NODE_PATH,
        summary="Publish committed dependency heads for readiness.",
    ).ok


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
    _publish_committed_heads(tmp_path, ["supporting_lemma", "main_result"])

    runtime = make_runtime()
    report = runtime.decl_graph.check_decl_ready(tmp_path, node_path=NODE_PATH, decl_name="main_result")

    assert report.ok and report.value is not None
    assert report.value.ready is True
    assert report.value.blocker is None


def test_definition_declared_with_statement_check_is_ready(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(
        tmp_path,
        round_id=round_id,
        name="main_def",
        kind="definition",
        public=True,
        target_state=DeclState.DECLARED,
    )
    _start_round(tmp_path, round_id)
    _declare_definition(tmp_path, round_id=round_id, name="main_def")

    report = make_runtime().decl_graph.check_decl_ready(tmp_path, node_path=NODE_PATH, decl_name="main_def")

    assert report.ok and report.value is not None
    assert report.value.ready is True


def test_declared_policy_accepts_declared_theorem_with_satisfied_statement_deps(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(
        tmp_path,
        round_id=round_id,
        name="supporting_def",
        kind="definition",
        public=True,
        target_state=DeclState.DECLARED,
    )
    _create_decl(tmp_path, round_id=round_id, name="main_result", public=True, target_state=DeclState.DECLARED)
    _start_round(tmp_path, round_id)
    _declare_definition(tmp_path, round_id=round_id, name="supporting_def")
    _declare_theorem(tmp_path, round_id=round_id, name="main_result", deps=["supporting_def"])
    _publish_committed_heads(tmp_path, ["supporting_def", "main_result"])

    runtime = make_runtime()
    configured = runtime.repo_workspace.metadata.update_repo_config(
        tmp_path,
        completion_mode=RepoCompletionMode.INTERFACE_DECLARED,
    )
    assert configured.ok
    declared = runtime.decl_graph.check_decl_proof_policy_satisfied(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
    )
    legacy_ready = runtime.decl_graph.check_decl_ready(tmp_path, node_path=NODE_PATH, decl_name="main_result")

    assert declared.ok and declared.value is not None
    assert declared.value.ready is True
    assert declared.value.required_availability == ProofAvailability.DECLARED
    assert declared.value.blocker is None
    assert legacy_ready.ok and legacy_ready.value is not None
    assert legacy_ready.value.ready is False
    assert legacy_ready.value.blocker is not None
    assert legacy_ready.value.blocker.reason == DeclReadinessReason.STATE_TOO_LOW


def test_proved_policy_checks_proof_deps_but_declared_policy_ignores_them(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="supporting_lemma", target_state=DeclState.DECLARED)
    _create_decl(tmp_path, round_id=round_id, name="main_result", public=True)
    _start_round(tmp_path, round_id)
    _declare_theorem(tmp_path, round_id=round_id, name="supporting_lemma")
    _prove_theorem(tmp_path, round_id=round_id, name="main_result", deps=["supporting_lemma"])

    runtime = make_runtime()
    declared = runtime.decl_graph.check_decl_proof_policy_satisfied(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
        target_proof_availability=ProofAvailability.DECLARED,
    )
    proved = runtime.decl_graph.check_decl_proof_policy_satisfied(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
        target_proof_availability=ProofAvailability.PROVED,
    )

    assert declared.ok and declared.value is not None
    assert declared.value.ready is True
    assert declared.value.blocker is None
    assert proved.ok and proved.value is not None
    assert proved.value.ready is False
    assert proved.value.blocker is not None
    assert proved.value.blocker.reason == DeclReadinessReason.DEPENDENCY_NOT_READY
    assert proved.value.blocker.blocking_decl is not None
    assert proved.value.blocker.blocking_decl.name == "supporting_lemma"


def test_strict_proved_audit_rejects_declared_only_public_theorem(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="public_result", public=True, target_state=DeclState.DECLARED)
    _start_round(tmp_path, round_id)
    _declare_theorem(tmp_path, round_id=round_id, name="public_result")

    runtime = make_runtime()
    configured = runtime.repo_workspace.metadata.update_repo_config(
        tmp_path,
        completion_mode=RepoCompletionMode.INTERFACE_DECLARED,
    )
    assert configured.ok
    policy = runtime.decl_graph.check_decl_proof_policy_satisfied(tmp_path, node_path=NODE_PATH, decl_name="public_result")
    audit = runtime.decl_graph.run_strict_proved_audit(tmp_path, node_path=NODE_PATH)

    assert policy.ok and policy.value is not None
    assert policy.value.ready is True
    assert audit.ok and audit.value is not None
    assert audit.value.passed is False
    assert audit.value.audit_name == "strict_proved_audit"
    assert audit.value.findings[0].kind == "strict_proved_decl_not_satisfied"
    assert audit.value.checked_items == [f"{NODE_PATH}:public_result"]


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
    assert report.value.blocker is not None
    assert report.value.blocker.reason == DeclReadinessReason.DEPENDENCY_NOT_READY
    assert report.value.blocker.blocking_decl is not None
    assert report.value.blocker.blocking_decl.name == "supporting_lemma"


def test_cycle_is_reported_as_not_ready(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round_draft(tmp_path)
    _create_decl(tmp_path, round_id=round_id, name="a")
    _create_decl(tmp_path, round_id=round_id, name="b")
    _start_round(tmp_path, round_id)
    _prove_theorem(tmp_path, round_id=round_id, name="a", deps=["b"])
    _prove_theorem(tmp_path, round_id=round_id, name="b", deps=["a"])
    _publish_committed_heads(tmp_path, ["a", "b"])

    report = make_runtime().decl_graph.check_decl_ready(tmp_path, node_path=NODE_PATH, decl_name="a")

    assert report.ok and report.value is not None
    assert report.value.ready is False
    assert report.value.blocker is not None
    assert report.value.blocker.reason == DeclReadinessReason.CYCLE_DETECTED
    assert report.value.blocker.blocking_decl is not None
    assert report.value.blocker.blocking_decl.name == "a"


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

    saved = runtime.lean_projection.decl_file.revision_provider.save_statement_formal_capture(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
        code="theorem main_result : True := by\n  sorry",
        check=_passed_check(),
        lean_decl_name="TestProject.main_result",
    )

    assert saved.ok and saved.value is not None
    assert saved.value.state == DeclState.PLANNED
    assert saved.value.statement.formal is not None
    assert saved.value.statement.formal.check is not None
    assert saved.value.statement.formal.check.status == "passed"


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
