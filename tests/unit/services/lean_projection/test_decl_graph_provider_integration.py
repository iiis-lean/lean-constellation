from __future__ import annotations

from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.decl_graph import DeclState


NODE_PATH = "Main.Topic.Core"


def _create_content_node(repo_root: Path) -> None:
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
        end_after_state=DeclState.PROVED if kind == "theorem" else DeclState.DECLARED,
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
    assert provider_view.value.statement["nl"]["text"] == "main_result states True."
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
    assert prepared.value.module == "Main.Topic.Core.Theorems.main_result"
    assert "import Main.Topic.Core.Prelude" in text
    assert "lean-constellation target: main_result" in text
    assert "stage: statement" in text
    assert "main_result states True." in text
    assert "theorem main_result : True := by" in text
    assert "sorry" in text


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
    assert not (tmp_path / "Main" / "Topic" / "Core" / "Theorems" / "main_result.lean").exists()


def test_prepare_statement_file_rejects_unsupported_decl_kind_from_decl_graph(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_round(tmp_path)
    _create_decl_with_statement(tmp_path, round_id=round_id, name="main_result", kind="custom_kind")

    result = make_runtime().lean_projection.prepare_statement_formal_stage_file(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
    )

    assert not result.ok
    assert result.issues[0].kind == "decl_kind_unsupported"
    assert not (tmp_path / "Main" / "Topic" / "Core" / "Defs" / "main_result.lean").exists()
