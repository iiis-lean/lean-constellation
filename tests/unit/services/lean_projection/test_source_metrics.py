from __future__ import annotations

import json
from pathlib import Path

import pytest

from lean_constellation.domain.repo import RepoFormat, RepoFormatState
from lean_constellation.services.decl_graph.models import (
    Decl,
    DeclGraphIndex,
    DeclLifecycle,
    DeclRevision,
    DeclState,
)
from lean_constellation.services.lean_projection import (
    LeanSourceStatisticsView,
    build_source_statistics,
    render_source_statistics_markdown,
)
from lean_constellation.services.node.node_store import NodeIndex, NodeIndexEntry


def _write_current_graph(root: Path) -> None:
    node_id = "node_test"
    node_path = "Main.Topic"
    index_root = root / ".lean_constellation" / "index"
    index_root.mkdir(parents=True)
    (index_root / "nodes.json").write_text(
        NodeIndex(
            entries=[
                NodeIndexEntry(
                    active=True,
                    kind="content",
                    lifecycle="active",
                    node_id=node_id,
                    path=node_path,
                )
            ],
            active_path_to_node_id={node_path: node_id},
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    graph_root = root / ".lean_constellation" / "nodes" / node_id / "decl_graph"
    graph_root.mkdir(parents=True)
    (graph_root / "index.json").write_text(
        DeclGraphIndex(
            node_id=node_id,
            node_path=node_path,
            decl_names=["config", "helper", "obsolete", "result"],
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    _write_decl(graph_root, node_path=node_path, name="result", kind="theorem", state=DeclState.PROVED, public=True)
    _write_decl(graph_root, node_path=node_path, name="helper", kind="lemma", state=DeclState.DECLARED, public=False)
    _write_decl(graph_root, node_path=node_path, name="config", kind="definition", state=DeclState.DECLARED, public=True)
    _write_decl(
        graph_root,
        node_path=node_path,
        name="obsolete",
        kind="lemma",
        state=DeclState.PROVED,
        public=False,
        lifecycle=DeclLifecycle.DELETED,
    )


def _write_decl(
    graph_root: Path,
    *,
    node_path: str,
    name: str,
    kind: str,
    state: DeclState,
    public: bool,
    lifecycle: DeclLifecycle = DeclLifecycle.ACTIVE,
) -> None:
    decl_root = graph_root / "decls" / name
    (decl_root / "revisions").mkdir(parents=True)
    (decl_root / "decl.json").write_text(
        Decl(
            kind=kind,
            lifecycle=lifecycle,
            name=name,
            node_path=node_path,
            public=public,
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    (decl_root / "revisions" / "1.json").write_text(
        DeclRevision(revision=1, state=state, status="committed").model_dump_json(indent=2),
        encoding="utf-8",
    )


def _layer(report, name: str):  # noqa: ANN001
    return next(item for item in report.layers if item.layer == name)


def test_source_statistics_partitions_managed_source_and_reads_current_graph(tmp_path: Path) -> None:
    _write_current_graph(tmp_path)
    project = tmp_path / "Example"
    project.mkdir()
    (project / "Result.lean").write_text(
        "-- lean-constellation: managed-imports-begin\n"
        "import Example.Prelude\n"
        "-- lean-constellation: managed-imports-end\n\n"
        "-- lean-constellation: declaration-source-begin\n\n"
        "private def helper : Nat := 1\n\n"
        "/--\n"
        "# lean-constellation target\n"
        "-/\n"
        "theorem result : True := by\n"
        "  trivial\n",
        encoding="utf-8",
    )
    (project / "Support.lean").write_text("import Example.Prelude", encoding="utf-8")
    (project / "Docs.lean").write_text(
        "/-! " + ("ordinary module documentation " * 8) + " -/\n",
        encoding="utf-8",
    )

    report = build_source_statistics(tmp_path)

    assert report.lean_file_count == 3
    assert report.graph_status == "available"
    assert report.nodes is not None
    assert report.nodes.total == 1
    assert report.nodes.by_kind == {"content": 1}
    assert report.decls is not None
    assert report.decls.total == 4
    assert report.decls.by_lifecycle == {"active": 3, "deleted": 1}
    assert report.decls.by_state == {"declared": 2, "proved": 2}
    assert report.decls.by_revision_status == {"committed": 4}
    assert report.decls.by_visibility == {"private": 2, "public": 2}
    assert report.decls.by_kind_and_state == {
        "definition": {"declared": 1},
        "lemma": {"declared": 1, "proved": 1},
        "theorem": {"proved": 1},
    }
    assert report.decls.theorem_like_total == 2
    assert report.decls.theorem_like_proved == 1
    assert report.decls.theorem_like_remaining == 1
    assert report.schema_version == 3

    all_metric = _layer(report, "all_source").metric
    component_layers = [
        _layer(report, name).metric
        for name in (
            "managed_header",
            "support_import_only",
            "managed_docstring",
            "unmanaged_preamble_helpers",
            "primary_declaration",
            "formatting_gap",
        )
    ]
    assert all_metric.byte_count == sum(item.byte_count for item in component_layers)
    assert all_metric.physical_line_count == sum(item.physical_line_count for item in component_layers)
    assert all_metric.nonempty_line_count == sum(item.nonempty_line_count for item in component_layers)
    assert all_metric.physical_line_count == 15
    assert report.rollups.all_source == all_metric
    assert report.rollups.headerless_source.physical_line_count == (
        all_metric.physical_line_count
        - _layer(report, "managed_header").metric.physical_line_count
        - _layer(report, "managed_docstring").metric.physical_line_count
    )
    assert report.rollups.primary_declaration == _layer(report, "primary_declaration").metric
    assert _layer(report, "support_import_only").metric.physical_line_count == 2

    assert _layer(report, "unmanaged_preamble_helpers").metric.nonempty_line_count == 1
    assert _layer(report, "primary_declaration").metric.nonempty_line_count == 2
    assert report.markers.long_docstring_line_count == 1
    assert {item.kind for item in report.markers.long_lines} == {"docstring"}
    markdown = render_source_statistics_markdown(report)
    assert "`headerless_source`" in markdown
    assert "**1/2** proved; **1** remaining" in markdown
    assert "policy-exempt" not in markdown


def test_source_statistics_reports_source_without_graph_truth(tmp_path: Path) -> None:
    (tmp_path / "Main.lean").write_text("theorem result : True := by trivial\n", encoding="utf-8")

    report = build_source_statistics(tmp_path)

    assert report.graph_status == "unavailable"
    assert report.nodes is None
    assert report.decls is None
    assert any("node index is unavailable" in warning for warning in report.warnings)


def test_source_statistics_rejects_previous_report_schema(tmp_path: Path) -> None:
    (tmp_path / "Main.lean").write_text(
        "theorem result : True := by trivial\n",
        encoding="utf-8",
    )
    payload = build_source_statistics(tmp_path).model_dump(mode="json")
    payload["schema_version"] = 2

    with pytest.raises(ValueError):
        LeanSourceStatisticsView.model_validate(payload)


def test_source_statistics_does_not_exempt_indented_fixed_marker_line(
    tmp_path: Path,
) -> None:
    (tmp_path / "Main.lean").write_text(
        "/--\n"
        + (" " * 90)
        + "# lean-constellation target\n"
        + "-/\n"
        + "theorem result : True := by trivial\n",
        encoding="utf-8",
    )

    report = build_source_statistics(tmp_path, max_line_length=100)

    assert report.markers.target_marker_count == 1
    assert report.markers.long_docstring_line_count == 1
    assert len(report.markers.long_lines) == 1
    assert report.markers.long_lines[0].kind == "docstring"
    assert report.markers.long_lines[0].line == 2


def test_source_statistics_rejects_noncurrent_node_index_schema(tmp_path: Path) -> None:
    _write_current_graph(tmp_path)
    index_path = tmp_path / ".lean_constellation" / "index" / "nodes.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    index_path.write_text(json.dumps(payload), encoding="utf-8")

    report = build_source_statistics(tmp_path)

    assert report.graph_status == "invalid"
    assert report.nodes is None
    assert report.decls is None
    assert any("nodes.json" in warning for warning in report.warnings)


@pytest.mark.parametrize(
    "relative_path",
    [
        Path("index/nodes.json"),
        Path("nodes/node_test/decl_graph/index.json"),
    ],
)
def test_source_statistics_rejects_missing_graph_schema_version(
    tmp_path: Path,
    relative_path: Path,
) -> None:
    _write_current_graph(tmp_path)
    path = tmp_path / ".lean_constellation" / relative_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["schema_version"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = build_source_statistics(tmp_path)

    assert report.graph_status == "invalid"
    assert report.nodes is None
    assert report.decls is None
    assert any("does not declare `schema_version`" in warning for warning in report.warnings)


def test_source_statistics_rejects_decl_graph_inventory_drift(tmp_path: Path) -> None:
    _write_current_graph(tmp_path)
    graph_index_path = (
        tmp_path / ".lean_constellation" / "nodes" / "node_test" / "decl_graph" / "index.json"
    )
    payload = json.loads(graph_index_path.read_text(encoding="utf-8"))
    payload["decl_names"].remove("helper")
    graph_index_path.write_text(json.dumps(payload), encoding="utf-8")

    report = build_source_statistics(tmp_path)

    assert report.graph_status == "invalid"
    assert report.nodes is None
    assert report.decls is None
    assert any("inventory does not match" in warning for warning in report.warnings)


def test_source_statistics_reads_adapter_main_scope_catalog(tmp_path: Path) -> None:
    node_id = "node_main"
    constellation = tmp_path / ".lean_constellation"
    (constellation / "index").mkdir(parents=True)
    (constellation / "repo_format.json").write_text(
        RepoFormatState(repo_format=RepoFormat.ADAPTER, reason="Adapter fixture.").model_dump_json(indent=2),
        encoding="utf-8",
    )
    (constellation / "index" / "nodes.json").write_text(
        NodeIndex(
            entries=[
                NodeIndexEntry(
                    active=True,
                    kind="scope",
                    lifecycle="active",
                    node_id=node_id,
                    path="Main",
                )
            ],
            active_path_to_node_id={"Main": node_id},
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    graph_root = constellation / "nodes" / node_id / "decl_graph"
    graph_root.mkdir(parents=True)
    (graph_root / "index.json").write_text(
        DeclGraphIndex(
            node_id=node_id,
            node_path="Main",
            decl_names=["adapted_result"],
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    _write_decl(
        graph_root,
        node_path="Main",
        name="adapted_result",
        kind="theorem",
        state=DeclState.PROVED,
        public=True,
    )

    report = build_source_statistics(tmp_path)

    assert report.graph_status == "available"
    assert report.nodes is not None
    assert report.nodes.by_kind == {"scope": 1}
    assert report.decls is not None
    assert report.decls.total == 1
    assert report.decls.by_visibility == {"public": 1}
    assert report.decls.theorem_like_proved == 1


def test_source_statistics_proof_progress_excludes_inactive_node(tmp_path: Path) -> None:
    _write_current_graph(tmp_path)
    constellation = tmp_path / ".lean_constellation"
    node_index_path = constellation / "index" / "nodes.json"
    node_index = NodeIndex.model_validate_json(node_index_path.read_text(encoding="utf-8"))
    node_index.entries.append(
        NodeIndexEntry(
            active=False,
            kind="content",
            lifecycle="obsolete",
            node_id="node_old",
            path="Main.Old",
        )
    )
    node_index_path.write_text(node_index.model_dump_json(indent=2), encoding="utf-8")
    graph_root = constellation / "nodes" / "node_old" / "decl_graph"
    graph_root.mkdir(parents=True)
    (graph_root / "index.json").write_text(
        DeclGraphIndex(
            node_id="node_old",
            node_path="Main.Old",
            decl_names=["old_result"],
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    _write_decl(
        graph_root,
        node_path="Main.Old",
        name="old_result",
        kind="theorem",
        state=DeclState.PROVED,
        public=True,
    )

    report = build_source_statistics(tmp_path)

    assert report.graph_status == "available"
    assert report.nodes is not None
    assert report.nodes.total == 2
    assert report.decls is not None
    assert report.decls.total == 5
    assert report.decls.theorem_like_total == 2
    assert report.decls.theorem_like_proved == 1
    assert report.decls.theorem_like_remaining == 1


def test_source_statistics_does_not_report_partial_graph_when_current_revision_is_missing(
    tmp_path: Path,
) -> None:
    _write_current_graph(tmp_path)
    missing_revision = (
        tmp_path
        / ".lean_constellation"
        / "nodes"
        / "node_test"
        / "decl_graph"
        / "decls"
        / "result"
        / "revisions"
        / "1.json"
    )
    missing_revision.unlink()

    report = build_source_statistics(tmp_path)

    assert report.graph_status == "invalid"
    assert report.nodes is None
    assert report.decls is None
    assert any(str(missing_revision) in warning for warning in report.warnings)
