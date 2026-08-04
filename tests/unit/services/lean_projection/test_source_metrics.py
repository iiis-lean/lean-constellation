from __future__ import annotations

import json
from pathlib import Path

from lean_constellation.services.lean_projection import build_source_statistics


def _write_current_graph(root: Path) -> None:
    node_id = "node_test"
    index_root = root / ".lean_constellation" / "index"
    index_root.mkdir(parents=True)
    (index_root / "nodes.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": [
                    {
                        "active": True,
                        "kind": "content",
                        "lifecycle": "active",
                        "node_id": node_id,
                        "path": "Main.Topic",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    decl_root = root / ".lean_constellation" / "nodes" / node_id / "decl_graph" / "decls" / "result"
    (decl_root / "revisions").mkdir(parents=True)
    (decl_root / "decl.json").write_text(
        json.dumps(
            {
                "kind": "theorem",
                "lifecycle": "active",
                "current_revision": 1,
                "name": "result",
                "node_path": "Main.Topic",
            }
        ),
        encoding="utf-8",
    )
    (decl_root / "revisions" / "1.json").write_text(
        json.dumps({"revision": 1, "state": "proved", "status": "committed"}),
        encoding="utf-8",
    )


def _layer(report, name: str):  # noqa: ANN001
    return next(item for item in report.layers if item.layer == name)


def test_source_statistics_partitions_managed_source_and_reads_current_graph(tmp_path: Path) -> None:
    _write_current_graph(tmp_path)
    project = tmp_path / "Example"
    project.mkdir()
    marker_name = "result_" + ("x" * 100)
    (project / "Result.lean").write_text(
        "-- lean-constellation: managed-imports-begin\n"
        "import Example.Prelude\n"
        "-- lean-constellation: managed-imports-end\n\n"
        "-- lean-constellation: declaration-source-begin\n\n"
        "private def helper : Nat := 1\n\n"
        "/--\n"
        f"# lean-constellation target: `{marker_name}`\n"
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
    assert report.decls.total == 1
    assert report.decls.by_state == {"proved": 1}
    assert report.decls.by_revision_status == {"committed": 1}

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
    assert _layer(report, "support_import_only").metric.physical_line_count == 2

    assert _layer(report, "unmanaged_preamble_helpers").metric.nonempty_line_count == 1
    assert _layer(report, "primary_declaration").metric.nonempty_line_count == 2
    assert report.markers.long_target_marker_count == 1
    assert report.markers.max_target_marker_length > 100
    assert report.markers.long_docstring_line_count == 1
    assert any(item.kind == "docstring" and not item.policy_exempt for item in report.markers.long_lines)
    assert any(item.kind == "target_marker" and item.policy_exempt for item in report.markers.long_lines)


def test_source_statistics_reports_source_without_graph_truth(tmp_path: Path) -> None:
    (tmp_path / "Main.lean").write_text("theorem result : True := by trivial\n", encoding="utf-8")

    report = build_source_statistics(tmp_path)

    assert report.graph_status == "unavailable"
    assert report.nodes is None
    assert report.decls is None
    assert any("node index is unavailable" in warning for warning in report.warnings)
