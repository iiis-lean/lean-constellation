from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.decl_graph import DeclRoundResultKind, DeclStage, DeclState


NODE_PATH = "Main.Topic.Core"


def _create_repo_nodes(repo_root: Path) -> None:
    runtime = make_runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.create_scope_node(
        repo_root,
        path="Main.Topic",
        goal="Topic goal",
        boundary="Topic boundary.",
    ).ok
    assert runtime.node.create_content_node(
        repo_root,
        path=NODE_PATH,
        goal="Core goal",
        boundary="Core boundary.",
        objective="Build the core DeclGraph lifecycle declarations.",
        success_criteria="Public declarations are ready.",
    ).ok


def _passed_statement_check(*, allow_sorry: bool = True) -> dict[str, object]:
    return {
        "status": "passed",
        "policy": "statement_formal",
        "allow_sorry": allow_sorry,
        "contains_sorry": allow_sorry,
        "contains_axiom": False,
        "message": "Fake statement check passed.",
    }


def _passed_proof_check() -> dict[str, object]:
    return {
        "status": "passed",
        "policy": "proof_formal",
        "allow_sorry": False,
        "contains_sorry": False,
        "contains_axiom": False,
        "message": "Fake proof check passed.",
    }


def _create_round(repo_root: Path, *, objective: str) -> tuple[str, str]:
    runtime = make_runtime()
    strategy = runtime.decl_graph.ensure_open_strategy(
        repo_root,
        node_path=NODE_PATH,
        objective="Build the file-backed DeclGraph lifecycle smoke strategy.",
        rationale="Exercise strategy, round, decl, revision, stage, review and readiness truth.",
    )
    assert strategy.ok, strategy.issues
    assert strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective=objective,
    )
    assert round_record.ok, round_record.issues
    assert round_record.value is not None
    return strategy.value.strategy_id, round_record.value.round_id


def _complete_theorem_round(
    repo_root: Path,
    *,
    round_id: str,
    decl_name: str,
    public: bool,
    proof_deps: list[str] | None = None,
) -> None:
    runtime = make_runtime()
    created = runtime.decl_graph.create_decl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        name=decl_name,
        kind="theorem",
        objective=f"Create and prove {decl_name}.",
        summary=f"{decl_name} theorem.",
        public=public,
        end_after_state=DeclState.PROVED,
    )
    assert created.ok, created.issues
    assert created.value is not None

    started = runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id)
    assert started.ok, started.issues

    assert runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=decl_name,
        nl=f"{decl_name} states True.",
        origin=[{"kind": "real_test", "ref": decl_name}],
        deps=[],
    ).ok
    assert runtime.decl_graph.write_statement_formal(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=decl_name,
        lean_code=f"theorem {decl_name} : True := by\n  sorry",
        lean_check=_passed_statement_check(),
        deps=[],
    ).ok
    assert runtime.decl_graph.write_proof_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=decl_name,
        nl="The proof is by triviality.",
        origin=[{"kind": "real_test", "ref": f"{decl_name}:proof"}],
        deps=proof_deps or [],
    ).ok
    assert runtime.decl_graph.write_proof_formal(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=decl_name,
        lean_code=f"theorem {decl_name} : True := by\n  trivial",
        lean_check=_passed_proof_check(),
        deps=proof_deps or [],
    ).ok

    reviewed = runtime.decl_graph.record_decl_review(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        stage=DeclStage.PROOF_FORMAL,
        decl_name=decl_name,
        passed=True,
        summary=f"{decl_name} proof formal review passed.",
    )
    assert reviewed.ok, reviewed.issues
    assert reviewed.value is not None
    stage_review = runtime.decl_graph.aggregate_stage_review_marks(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        stage=DeclStage.PROOF_FORMAL,
        summary="Proof formal stage accepted.",
        marks=[reviewed.value],
    )
    assert stage_review.ok, stage_review.issues
    assert stage_review.value is not None
    assert stage_review.value.passed is True

    committed = runtime.decl_graph.commit_decl_revision(repo_root, node_path=NODE_PATH, name=decl_name, state=DeclState.PROVED)
    assert committed.ok, committed.issues
    assert committed.value is not None
    assert committed.value.version_status == "committed"

    assert runtime.decl_graph.write_decl_change_summary(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        change_id=created.value.change_id,
        summary=f"{decl_name} created and proved.",
    ).ok
    assert runtime.decl_graph.write_round_summary(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        summary=f"Round completed {decl_name}.",
    ).ok
    terminal = runtime.decl_graph.mark_round_terminal(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        result_kind=DeclRoundResultKind.SUCCESS,
    )
    assert terminal.ok, terminal.issues
    assert terminal.value is not None
    assert terminal.value.status.value == "committed"
    assert terminal.value.result_kind == DeclRoundResultKind.SUCCESS


@pytest.mark.real
def test_decl_graph_file_backed_content_node_lifecycle_real(tmp_path: Path) -> None:
    repo_root = tmp_path / "DeclGraphLifecycle"
    repo_root.mkdir()
    _create_repo_nodes(repo_root)

    strategy_id, helper_round_id = _create_round(repo_root, objective="Create the helper lemma.")
    _complete_theorem_round(
        repo_root,
        round_id=helper_round_id,
        decl_name="supporting_lemma",
        public=False,
    )
    _, main_round_id = _create_round(repo_root, objective="Create the public main theorem.")
    _complete_theorem_round(
        repo_root,
        round_id=main_round_id,
        decl_name="main_result",
        public=True,
        proof_deps=["supporting_lemma"],
    )

    runtime = make_runtime()
    report = runtime.decl_graph.check_decl_ready(repo_root, node_path=NODE_PATH, decl_name="main_result")
    public = runtime.node.export.list_content_public_decls(repo_root, node_path=NODE_PATH)
    store_view = runtime.decl_graph.get_decl_graph_store_view(repo_root, node_path=NODE_PATH)

    assert report.ok, report.issues
    assert report.value is not None
    assert report.value.ready is True
    assert report.value.dependencies_checked == ["supporting_lemma"]
    assert public.ok, public.issues
    assert public.value is not None
    assert [decl.ref.name for decl in public.value] == ["main_result"]
    assert store_view.ok
    assert store_view.value is not None
    assert Path(store_view.value.graph_root).is_dir()

    reloaded = make_runtime()
    index = reloaded.decl_graph.get_decl_graph_index(repo_root, node_path=NODE_PATH)
    strategies = reloaded.decl_graph.list_strategies(repo_root, node_path=NODE_PATH)
    rounds = reloaded.decl_graph.list_rounds(repo_root, node_path=NODE_PATH)
    main_decl = reloaded.decl_graph.get_decl(repo_root, node_path=NODE_PATH, name="main_result")
    main_revision = reloaded.decl_graph.get_decl_revision(repo_root, node_path=NODE_PATH, name="main_result", revision=1)
    reloaded_ready = reloaded.decl_graph.check_decl_ready(repo_root, node_path=NODE_PATH, decl_name="main_result")

    assert index.ok and index.value is not None
    assert index.value.decl_names == ["main_result", "supporting_lemma"]
    assert strategies.ok and strategies.value is not None
    assert [strategy.strategy_id for strategy in strategies.value] == [strategy_id]
    assert rounds.ok and rounds.value is not None
    assert [round_record.round_id for round_record in rounds.value] == [helper_round_id, main_round_id]
    assert all(round_record.status.value == "committed" for round_record in rounds.value)
    assert all(round_record.result_kind == DeclRoundResultKind.SUCCESS for round_record in rounds.value)
    assert main_decl.ok and main_decl.value is not None
    assert main_decl.value.public is True
    assert main_revision.ok and main_revision.value is not None
    assert main_revision.value.version_status == "committed"
    assert main_revision.value.proof_deps == ["supporting_lemma"]
    assert main_revision.value.decl_deps == ["supporting_lemma"]
    assert reloaded_ready.ok and reloaded_ready.value is not None
    assert reloaded_ready.value.ready is True
