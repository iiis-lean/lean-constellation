from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit_services_helpers import initialize_native_test_repo, lean_check_payload, make_runtime, write_statement_formal_for_test

from lean_constellation.domain.refs import DeclRef, MathlibRef
from lean_constellation.services.decl_graph import DeclState, MathlibDeclDep, RepoDeclDep
from lean_constellation.services.decl_graph.models import DeclOriginRef


NODE_PATH = "Main.Topic.Core"


def _create_content_node(repo_root: Path) -> None:
    initialize_native_test_repo(repo_root)
    runtime = make_runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    created = runtime.node.create_content_node(
        repo_root,
        path=NODE_PATH,
        goal="Core goal.",
        boundary="Core declarations only.",
        objective="Prepare Decl-owned Lean files from DeclGraph revisions.",
        success_criteria="Lean file projection can read current open Decl revisions.",
    )
    assert created.ok, created.issues


def _create_round(repo_root: Path) -> str:
    runtime = make_runtime()
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=NODE_PATH, objective="Projection strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Projection provider integration round.",
    )
    assert round_record.ok and round_record.value is not None
    return round_record.value.round_id


def _create_decl_with_statement(repo_root: Path, *, round_id: str, name: str, kind: str = "theorem") -> None:
    runtime = make_runtime()
    created = runtime.decl_graph.create_decl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        name=name,
        kind=kind,
        objective=f"Create {name}.",
        summary=f"{name} summary.",
        public=True,
        target_state=DeclState.PROVED if kind == "theorem" else DeclState.DECLARED,
    )
    assert created.ok, created.issues
    started = runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id)
    assert started.ok, started.issues
    statement = runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=name,
        nl=f"{name} states True.",
        origin=[{"kind": "unit_test"}],
        deps=[],
    )
    assert statement.ok, statement.issues


def test_default_decl_graph_revision_provider_prepares_statement_file(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round(tmp_path)
    _create_decl_with_statement(tmp_path, round_id=round_id, name="main_result")
    runtime = make_runtime()

    provider_view = runtime.decl_graph.get_current_decl_revision(tmp_path, node_path=NODE_PATH, decl_name="main_result")
    assert provider_view.ok and provider_view.value is not None
    assert provider_view.value.kind == "theorem"
    assert provider_view.value.statement.nl.text == "main_result states True."
    assert runtime.lean_projection.decl_file.revision_provider is runtime.decl_graph

    prepared = runtime.lean_projection.prepare_statement_formal_stage_file(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
    )

    assert prepared.ok, prepared.issues
    assert prepared.value is not None
    path = Path(prepared.value.path)
    text = path.read_text(encoding="utf-8")
    assert prepared.value.module == "TestProject.Main.Topic.Core.Theorems.main_result"
    assert "import TestProject.Main.Topic.Core.Prelude" in text
    assert "# lean-constellation target: `main_result`" in text
    assert "main_result states True." in text
    assert "theorem main_result" not in text


def test_stage_mutation_refreshes_managed_projection_and_preserves_agent_source(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round(tmp_path)
    _create_decl_with_statement(tmp_path, round_id=round_id, name="main_result")
    runtime = make_runtime()
    prepared = runtime.lean_projection.prepare_statement_formal_stage_file(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
    )
    assert prepared.ok and prepared.value is not None, prepared.issues
    path = Path(prepared.value.path)
    agent_source = "\ntheorem actualResult : True := by\n  sorry\n"
    path.write_text(path.read_text(encoding="utf-8") + agent_source, encoding="utf-8")

    updated = runtime.decl_graph.set_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The refreshed statement remains true.",
    )

    assert updated.ok and updated.value is not None, updated.issues
    assert updated.value.changed is True
    assert updated.value.managed_projection is not None
    assert updated.value.managed_projection.reread_required
    assert updated.value.managed_projection.changed_files == [str(path)]
    current = path.read_text(encoding="utf-8")
    assert "The refreshed statement remains true." in current
    assert current.endswith(agent_source)


def test_stage_mutation_rolls_truth_and_file_back_when_projection_refresh_fails(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round(tmp_path)
    _create_decl_with_statement(tmp_path, round_id=round_id, name="main_result")
    runtime = make_runtime()
    prepared = runtime.lean_projection.prepare_statement_formal_stage_file(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
    )
    assert prepared.ok and prepared.value is not None, prepared.issues
    path = Path(prepared.value.path)
    corrupted = path.read_text(encoding="utf-8").replace(
        "-- lean-constellation: managed-imports-end",
        "-- managed imports marker removed",
    )
    path.write_text(corrupted, encoding="utf-8")

    failed = runtime.decl_graph.set_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="This mutation must be rolled back.",
    )

    assert not failed.ok
    assert failed.issues[0].kind == "decl_managed_region_invalid"
    revision = runtime.decl_graph.get_current_decl_revision(tmp_path, node_path=NODE_PATH, decl_name="main_result")
    assert revision.ok and revision.value is not None
    assert revision.value.statement.nl.text == "main_result states True."
    assert path.read_text(encoding="utf-8") == corrupted


@pytest.mark.parametrize(
    "mutation_kind",
    [
        "statement_origin_add",
        "statement_origin_clear",
        "statement_repo_dep_add",
        "statement_mathlib_dep_add",
        "statement_dep_remove",
        "statement_dep_clear",
        "proof_mathlib_dep_add",
    ],
)
def test_origin_and_dependency_mutations_roll_truth_and_file_back_atomically(
    tmp_path: Path,
    mutation_kind: str,
) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round(tmp_path)
    runtime = make_runtime()
    for name in ("main_result", "helper_result"):
        created = runtime.decl_graph.create_decl(
            tmp_path,
            node_path=NODE_PATH,
            round_id=round_id,
            name=name,
            kind="theorem",
            objective=f"Create {name}.",
            summary=f"{name} summary.",
            public=True,
            target_state=DeclState.PROVED,
        )
        assert created.ok, created.issues
    assert runtime.decl_graph.start_round(tmp_path, node_path=NODE_PATH, round_id=round_id).ok
    for name in ("main_result", "helper_result"):
        written = runtime.decl_graph.write_statement_nl(
            tmp_path,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name=name,
            nl=f"{name} states True.",
            origin=[{"kind": "unit_test"}],
            deps=[],
        )
        assert written.ok, written.issues

    mathlib_dep = MathlibDeclDep(ref=MathlibRef(name="Nat.succ", module="Mathlib.Data.Nat.Basic"))
    if mutation_kind in {"statement_dep_remove", "statement_dep_clear"}:
        seeded = runtime.decl_graph.add_statement_dep(
            tmp_path,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name="main_result",
            dep=mathlib_dep,
        )
        assert seeded.ok, seeded.issues

    if mutation_kind == "proof_mathlib_dep_add":
        prepared_statement = runtime.lean_projection.prepare_statement_formal_stage_file(
            tmp_path,
            node_path=NODE_PATH,
            decl_name="main_result",
        )
        assert prepared_statement.ok and prepared_statement.value is not None, prepared_statement.issues
        statement_path = Path(prepared_statement.value.path)
        statement_path.write_text(
            statement_path.read_text(encoding="utf-8") + "theorem actualResult : True := by\n  sorry\n",
            encoding="utf-8",
        )
        captured = write_statement_formal_for_test(runtime,
            tmp_path,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name="main_result",
            lean_code=statement_path.read_text(encoding="utf-8"),
            lean_check=lean_check_payload(contains_sorry=True),
        )
        assert captured.ok, captured.issues
        proof_nl = runtime.decl_graph.set_proof_nl(
            tmp_path,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name="main_result",
            nl="Finish by triviality.",
        )
        assert proof_nl.ok, proof_nl.issues
        prepared = runtime.lean_projection.prepare_proof_formal_stage_file(
            tmp_path,
            node_path=NODE_PATH,
            decl_name="main_result",
        )
    else:
        prepared = runtime.lean_projection.prepare_statement_formal_stage_file(
            tmp_path,
            node_path=NODE_PATH,
            decl_name="main_result",
        )
    assert prepared.ok and prepared.value is not None, prepared.issues
    path = Path(prepared.value.path)
    corrupted = path.read_text(encoding="utf-8").replace(
        "-- lean-constellation: managed-imports-end",
        "-- managed imports marker removed",
    )
    path.write_text(corrupted, encoding="utf-8")
    before = runtime.decl_graph.get_current_decl_revision(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
    )
    assert before.ok and before.value is not None

    if mutation_kind == "statement_origin_add":
        failed = runtime.decl_graph.add_statement_origin(
            tmp_path,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name="main_result",
            origin=DeclOriginRef(kind="unit_test", note="new origin"),
        )
    elif mutation_kind == "statement_origin_clear":
        failed = runtime.decl_graph.clear_statement_origins(
            tmp_path,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name="main_result",
        )
    elif mutation_kind == "statement_repo_dep_add":
        failed = runtime.decl_graph.add_statement_dep(
            tmp_path,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name="main_result",
            dep=RepoDeclDep(ref=DeclRef(node=NODE_PATH, name="helper_result", revision=1)),
        )
    elif mutation_kind == "statement_mathlib_dep_add":
        failed = runtime.decl_graph.add_statement_dep(
            tmp_path,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name="main_result",
            dep=mathlib_dep,
        )
    elif mutation_kind == "statement_dep_remove":
        failed = runtime.decl_graph.remove_statement_dep(
            tmp_path,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name="main_result",
            index=0,
        )
    elif mutation_kind == "statement_dep_clear":
        failed = runtime.decl_graph.clear_statement_deps(
            tmp_path,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name="main_result",
        )
    else:
        failed = runtime.decl_graph.add_proof_dep(
            tmp_path,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name="main_result",
            dep=mathlib_dep,
        )

    assert not failed.ok
    assert failed.issues[0].kind == "decl_managed_region_invalid"
    after = runtime.decl_graph.get_current_decl_revision(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
    )
    assert after.ok and after.value is not None
    assert after.value.model_dump(mode="json") == before.value.model_dump(mode="json")
    assert path.read_text(encoding="utf-8") == corrupted


def test_prepare_statement_file_rejects_committed_decl_graph_revision(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round(tmp_path)
    _create_decl_with_statement(tmp_path, round_id=round_id, name="main_result")
    committed = make_runtime().decl_graph.commit_decl_revision(
        tmp_path,
        node_path=NODE_PATH,
        name="main_result",
        state=DeclState.SPECIFIED,
    )
    assert committed.ok, committed.issues

    result = make_runtime().lean_projection.prepare_statement_formal_stage_file(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
    )

    assert not result.ok
    assert result.issues[0].kind == "decl_revision_not_open"
    assert not (tmp_path / "TestProject" / "Main" / "Topic" / "Core" / "Theorems" / "main_result.lean").exists()


def test_native_catalog_rejects_unsupported_decl_kind(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round(tmp_path)
    result = make_runtime().decl_graph.create_decl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        name="main_result",
        kind="custom_kind",
        objective="Reject unsupported kind.",
        summary="Unsupported kind.",
        public=True,
        target_state=DeclState.DECLARED,
    )

    assert not result.ok
    assert result.issues[0].kind == "native_decl_module_invalid"
