import json
from pathlib import Path

from tests.unit_services_helpers import (
    initialize_native_test_repo,
    lean_check_payload,
    make_runtime,
    write_proof_formal_for_test,
    write_statement_formal_for_test,
)

from lean_constellation.services.decl_graph import DeclState


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
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build the core declarations.",
        success_criteria="The core declarations are ready.",
    ).ok


def _create_running_round_with_decl(tmp_path: Path, *, name: str = "main_result", kind: str = "theorem") -> str:
    runtime = make_runtime()
    service = runtime.decl_graph
    strategy = service.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = service.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round objective.",
    )
    assert round_record.ok and round_record.value is not None
    assert service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        name=name,
        kind=kind,
        objective=f"Create {name}.",
        summary=f"{name} summary.",
        end_after_state=DeclState.PROVED if kind == "theorem" else DeclState.DECLARED,
    ).ok
    assert service.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    return round_record.value.round_id


def test_statement_and_proof_stage_mutations_write_candidates_without_advancing_revision_state(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round_with_decl(tmp_path)
    runtime = make_runtime()
    service = runtime.decl_graph

    statement_nl = service.write_statement_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
        origin=[{"kind": "source", "ref": "source:main"}],
        deps=["supporting_lemma"],
    )
    assert statement_nl.ok and statement_nl.value is not None
    assert statement_nl.value.revision.state == DeclState.PLANNED
    assert statement_nl.value.revision.statement_origin == [{"kind": "source", "ref": "source:main"}]
    assert statement_nl.value.revision.statement_deps == ["supporting_lemma"]

    statement_formal = write_statement_formal_for_test(runtime,
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        lean_code="theorem main_result : True := by sorry",
        lean_check=lean_check_payload(contains_sorry=True),
        deps=["supporting_lemma"],
    )
    assert statement_formal.ok and statement_formal.value is not None
    assert statement_formal.value.state == DeclState.PLANNED
    assert statement_formal.value.statement.formal is not None
    assert statement_formal.value.statement.formal.check is not None
    assert statement_formal.value.statement.formal.check.contains_sorry is True
    assert statement_formal.value.statement_lean_check is not None
    assert statement_formal.value.statement_lean_check["status"] == "passed"
    assert statement_formal.value.statement_lean_check["contains_sorry"] == "True"

    proof_nl = service.write_proof_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        nl="The proof is by triviality.",
        deps=["supporting_lemma", "proof_helper"],
    )
    assert proof_nl.ok and proof_nl.value is not None
    assert proof_nl.value.revision.state == DeclState.PLANNED
    assert proof_nl.value.revision.statement_deps == ["supporting_lemma"]
    assert proof_nl.value.revision.proof_deps == ["proof_helper", "supporting_lemma"]

    proof_formal = write_proof_formal_for_test(runtime,
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        lean_code="by trivial",
        lean_check=lean_check_payload(),
        deps=["proof_helper"],
    )
    assert proof_formal.ok and proof_formal.value is not None
    assert proof_formal.value.state == DeclState.PLANNED
    assert proof_formal.value.proof_lean_code == "by trivial"
    assert proof_formal.value.statement_deps == ["supporting_lemma"]
    assert proof_formal.value.proof_deps == ["proof_helper"]

    revision_path = service.graph_store.revision_path(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
        revision=proof_formal.value.revision,
    )
    payload = json.loads(revision_path.read_text(encoding="utf-8"))
    assert payload["status"] == "open"
    assert payload["change"]["kind"] == "create"
    assert payload["statement"]["nl"]["text"] == "The main result states True."
    assert payload["statement"]["formal"]["code"] == "theorem main_result : True := by sorry"
    assert payload["statement"]["deps"] == [
        {
            "kind": "repo_decl",
            "reason": None,
            "ref": {"name": "supporting_lemma", "node": "Main", "repo": None, "revision": 1},
        }
    ]
    assert payload["proof"]["nl"]["text"] == "The proof is by triviality."
    assert payload["proof"]["formal"]["code"] == "by trivial"
    assert payload["proof"]["deps"] == [
        {
            "kind": "repo_decl",
            "reason": None,
            "ref": {"name": "proof_helper", "node": "Main", "repo": None, "revision": 1},
        }
    ]
    for legacy_field in [
        "version_status",
        "change_kind",
        "statement_nl",
        "statement_origin",
        "statement_deps",
        "statement_lean_code",
        "statement_lean_check",
        "proof_nl",
        "proof_origin",
        "proof_deps",
        "proof_lean_code",
        "proof_lean_check",
        "decl_deps",
    ]:
        assert legacy_field not in payload


def test_statement_formal_requires_statement_nl(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round_with_decl(tmp_path)
    runtime = make_runtime()
    service = runtime.decl_graph

    result = write_statement_formal_for_test(runtime,
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        lean_code="theorem main_result : True := by sorry",
        lean_check=lean_check_payload(),
    )

    assert not result.ok
    assert result.issues[0].kind == "statement_nl_missing"


def test_advance_stage_state_is_explicit_after_candidate_write(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round_with_decl(tmp_path)
    runtime = make_runtime()
    service = runtime.decl_graph

    candidate = service.write_statement_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
    )
    assert candidate.ok and candidate.value is not None
    assert candidate.value.revision.state == DeclState.PLANNED

    advanced = service.advance_stage_state(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        stage="statement_nl",
        decl_names=["main_result"],
    )

    assert advanced.ok and advanced.value is not None
    assert advanced.value == ["main_result"]
    revision = service.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=1,
    )
    assert revision.ok and revision.value is not None
    assert revision.value.state == DeclState.SPECIFIED


def test_advance_stage_state_validates_whole_batch_before_writing(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round_with_decl(tmp_path)
    runtime = make_runtime()
    service = runtime.decl_graph
    candidate = service.write_statement_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
    )
    assert candidate.ok and candidate.value is not None

    advanced = service.advance_stage_state(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        stage="statement_nl",
        decl_names=["main_result", "not_in_round"],
    )

    assert not advanced.ok
    revision = service.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=1,
    )
    assert revision.ok and revision.value is not None
    assert revision.value.state == DeclState.PLANNED


def test_proof_stages_reject_non_theorem_like_decl(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round_with_decl(tmp_path, name="main_def", kind="definition")
    runtime = make_runtime()
    service = runtime.decl_graph
    assert service.write_statement_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_def",
        nl="The definition has type Nat.",
    ).ok
    assert write_statement_formal_for_test(runtime,
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_def",
        lean_code="def main_def : Nat := 0",
        lean_check=lean_check_payload(),
    ).ok

    result = service.write_proof_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_def",
        nl="No proof is needed.",
    )

    assert not result.ok
    assert result.issues[0].kind == "decl_not_theorem_like"


def test_stage_mutation_requires_running_round(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    service = make_runtime().decl_graph
    strategy = service.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = service.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round objective.",
    )
    assert round_record.ok and round_record.value is not None
    assert service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        name="main_result",
        kind="theorem",
        objective="Create main_result.",
        summary="Main result.",
    ).ok

    result = service.write_statement_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        decl_name="main_result",
        nl="The main result states True.",
    )

    assert not result.ok
    assert result.issues[0].kind == "round_not_running"


def test_stage_mutation_rejects_decl_not_in_round(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round_with_decl(tmp_path)
    service = make_runtime().decl_graph

    result = service.write_statement_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="missing_decl",
        nl="Missing declaration.",
    )

    assert not result.ok
    assert result.issues[0].kind == "decl_not_in_round"
