from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.decl_graph import DeclGraphIndex


def _create_content_node(tmp_path: Path, *, node_path: str = "Main.Topic.Core") -> None:
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
        path=node_path,
        goal="Core goal",
        boundary="Core boundary",
        objective="Build the core declarations.",
        success_criteria="The core declarations are ready.",
    ).ok


def test_decl_graph_service_is_wired_by_factory() -> None:
    runtime = make_runtime()

    assert runtime.decl_graph is not None
    assert runtime.app.decl_graph is runtime.decl_graph


def test_ensure_decl_graph_creates_empty_content_node_store(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    runtime = make_runtime()

    ensured = runtime.decl_graph.ensure_decl_graph(tmp_path, node_path="Main.Topic.Core")

    assert ensured.ok
    assert ensured.value is not None
    assert ensured.value.node_path == "Main.Topic.Core"
    assert ensured.value.decl_count == 0
    assert ensured.value.round_count == 0
    assert ensured.value.strategy_count == 0
    assert Path(ensured.value.graph_root).is_dir()
    assert Path(ensured.value.index_path).is_file()
    node = runtime.node.node_tree.node_store.resolve_active_node(tmp_path, path="Main.Topic.Core")
    assert node.ok and node.value is not None
    assert Path(ensured.value.graph_root) == runtime.node.node_tree.node_store.decl_graph_dir(tmp_path, node_id=node.value.node_id)

    index = runtime.decl_graph.get_decl_graph_index(tmp_path, node_path="Main.Topic.Core")
    assert index.ok
    assert index.value is not None
    assert index.value == DeclGraphIndex(
        node_id=node.value.node_id,
        node_path="Main.Topic.Core",
        summary="Empty DeclGraph index for Content node Main.Topic.Core.",
        updated_at=index.value.updated_at,
    )


def test_decl_graph_rejects_scope_node(tmp_path: Path) -> None:
    runtime = make_runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(
        tmp_path,
        path="Main.Topic",
        goal="Topic goal",
        boundary="Topic boundary",
    ).ok

    result = runtime.decl_graph.ensure_decl_graph(tmp_path, node_path="Main.Topic")

    assert not result.ok
    assert result.issues[0].kind == "decl_graph_node_not_content"


def test_decl_graph_reports_corrupt_index_schema(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    runtime = make_runtime()
    ensured = runtime.decl_graph.ensure_decl_graph(tmp_path, node_path="Main.Topic.Core")
    assert ensured.ok
    assert ensured.value is not None
    Path(ensured.value.index_path).write_text('{"node_path": "Main.Topic.Core", "strategy_ids": "bad"}\n', encoding="utf-8")

    result = runtime.decl_graph.get_decl_graph_index(tmp_path, node_path="Main.Topic.Core")

    assert not result.ok
    assert result.issues[0].kind == "schema_validation_failed"


def test_rebuild_decl_graph_index_scans_stable_sorted_store(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    runtime = make_runtime()
    ensured = runtime.decl_graph.ensure_decl_graph(tmp_path, node_path="Main.Topic.Core")
    assert ensured.ok
    assert ensured.value is not None
    root = Path(ensured.value.graph_root)
    for relative in [
        "strategies/b_strategy.json",
        "strategies/a_strategy.json",
        "rounds/round_02.json",
        "rounds/round_01.json",
        "decls/Z_decl/decl.json",
        "decls/A_decl/decl.json",
    ]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")

    rebuilt = runtime.decl_graph.rebuild_decl_graph_index(tmp_path, node_path="Main.Topic.Core")

    assert rebuilt.ok
    assert rebuilt.value is not None
    assert rebuilt.value.strategy_ids == ["a_strategy", "b_strategy"]
    assert rebuilt.value.round_ids == ["round_01", "round_02"]
    assert rebuilt.value.decl_names == ["A_decl", "Z_decl"]

    view = runtime.decl_graph.get_decl_graph_store_view(tmp_path, node_path="Main.Topic.Core")
    assert view.ok
    assert view.value is not None
    assert view.value.strategy_count == 2
    assert view.value.round_count == 2
    assert view.value.decl_count == 2
