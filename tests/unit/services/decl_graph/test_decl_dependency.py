from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import ProofAvailability
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.decl_graph.models import DeclRevision, RepoDeclDep
from lean_constellation.services.foundation import WriteMode


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
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build the core declarations.",
        success_criteria="The core declarations are ready.",
    ).ok


def _create_round(tmp_path: Path, *, objective: str = "Plan a round.") -> str:
    service = make_runtime().decl_graph
    strategy = service.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = service.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective=objective,
    )
    assert round_record.ok and round_record.value is not None
    return round_record.value.round_id


def _write_revision(tmp_path: Path, revision: DeclRevision) -> None:
    runtime = make_runtime()
    path = runtime.decl_graph.graph_store.revision_path(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name=revision.decl_name,
        revision=revision.revision,
    )
    assert runtime.foundation.store.write_json_atomic(path, revision, mode=WriteMode.UPDATE_EXISTING).ok


def _seed_committed_decl(tmp_path: Path, *, round_id: str, name: str, deps: list[str] | None = None) -> None:
    service = make_runtime().decl_graph
    assert service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        name=name,
        kind="theorem",
        objective=f"Create {name}.",
        summary=f"{name} summary.",
        end_after_state=DeclState.PROVED,
    ).ok
    revision = service.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name=name, revision=1)
    assert revision.ok and revision.value is not None
    revision.value.state = DeclState.PROVED
    revision.value.proof_deps = deps or []
    _write_revision(tmp_path, revision.value)
    assert service.commit_decl_revision(tmp_path, node_path="Main.Topic.Core", name=name, state=DeclState.PROVED).ok


def _seed_chain(tmp_path: Path) -> None:
    round_id = _create_round(tmp_path)
    _seed_committed_decl(tmp_path, round_id=round_id, name="A")
    _seed_committed_decl(tmp_path, round_id=round_id, name="B", deps=["A"])
    _seed_committed_decl(tmp_path, round_id=round_id, name="C", deps=["B"])


def test_compute_dependency_closure_reports_upstream_and_downstream(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _seed_chain(tmp_path)
    service = make_runtime().decl_graph

    closure = service.compute_dependency_closure(tmp_path, node_path="Main.Topic.Core", decl_names=["B"])

    assert closure.ok and closure.value is not None
    assert closure.value.root_decl_names == ["B"]
    assert closure.value.upstream_decl_names == ["A"]
    assert closure.value.downstream_decl_names == ["C"]


def test_dependency_helpers_split_statement_and_proof_policy_requirements(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round(tmp_path)
    service = make_runtime().decl_graph
    assert service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        name="MainResult",
        kind="theorem",
        objective="Create MainResult.",
        summary="MainResult summary.",
        end_after_state=DeclState.PROVED,
    ).ok
    decl = service.get_decl(tmp_path, node_path="Main.Topic.Core", name="MainResult")
    revision = service.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name="MainResult", revision=1)
    assert decl.ok and decl.value is not None
    assert revision.ok and revision.value is not None
    revision.value.statement_deps = ["StatementDep"]
    revision.value.proof_deps = ["ProofDep", "StatementDep"]
    revision.value.proof.deps.append(
        RepoDeclDep(ref=DeclRef(repo="Provider", node="Main.Core", name="ExternalDep", revision=1))
    )

    assert service.statement_dependency_names(revision.value) == ["StatementDep"]
    assert service.proof_dependency_names(revision.value) == ["ExternalDep", "ProofDep", "StatementDep"]
    assert service.all_dependency_names(revision.value) == ["ExternalDep", "ProofDep", "StatementDep"]
    assert service.dependency_requirements_for_proof_policy(
        decl.value,
        revision.value,
        target_proof_availability=ProofAvailability.DECLARED,
    ) == [("StatementDep", ProofAvailability.DECLARED)]
    assert service.dependency_requirements_for_proof_policy(
        decl.value,
        revision.value,
        target_proof_availability=ProofAvailability.PROVED,
    ) == [
        ("ProofDep", ProofAvailability.PROVED),
        ("StatementDep", ProofAvailability.PROVED),
        ("ExternalDep", ProofAvailability.PROVED),
    ]
    ref_requirements = service.dependency_ref_requirements_for_proof_policy(
        decl.value,
        revision.value,
        target_proof_availability=ProofAvailability.PROVED,
    )
    assert [(ref.repo, ref.node, ref.name, required) for ref, required in ref_requirements] == [
        (None, "Main", "ProofDep", ProofAvailability.PROVED),
        (None, "Main", "StatementDep", ProofAvailability.PROVED),
        ("Provider", "Main.Core", "ExternalDep", ProofAvailability.PROVED),
    ]


def test_delete_preflight_requires_downstream_closure(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _seed_chain(tmp_path)
    service = make_runtime().decl_graph

    incomplete = service.check_delete_preflight(tmp_path, node_path="Main.Topic.Core", decl_names=["A"])
    assert incomplete.ok and incomplete.value is not None
    assert incomplete.value.passed is False
    assert incomplete.value.issues[0].kind == "delete_closure_incomplete"

    complete = service.check_delete_preflight(tmp_path, node_path="Main.Topic.Core", decl_names=["A", "B", "C"])
    assert complete.ok and complete.value is not None
    assert complete.value.passed is True


def test_audit_round_dependencies_delegates_round_draft_gate(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _seed_chain(tmp_path)
    service = make_runtime().decl_graph
    update_round_id = _create_round(tmp_path, objective="Update dependent declarations together.")
    assert service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=update_round_id,
        name="A",
        objective="Update A.",
        start_before_state=DeclState.PROVED,
        end_after_state=DeclState.PROVED,
    ).ok
    assert service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=update_round_id,
        name="B",
        objective="Update B.",
        start_before_state=DeclState.PROVED,
        end_after_state=DeclState.PROVED,
    ).ok

    audit = service.audit_round_dependencies(tmp_path, node_path="Main.Topic.Core", round_id=update_round_id)

    assert audit.ok and audit.value is not None
    assert audit.value.passed is False
    assert any(issue.kind == "round_internal_dependency" for issue in audit.value.issues)
