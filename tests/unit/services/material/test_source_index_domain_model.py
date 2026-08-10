from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.repo_run import SourceScope
from lean_constellation.domain.refs import MaterialRef, SourceRef
from lean_constellation.services.material.source_corpus import SourceCorpusManifestView
from lean_constellation.services.material.source_index import (
    SourceBlock,
    SourceBlockRef,
    SourceFileIndex,
    SourceIndex,
    SourceIndexView,
)


def _prepare_source(repo_root: Path) -> None:
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(
        "# Entry\n\n"
        "Source provenance: local markdown fixture.\n"
        "Reading order: start here, then read `chapter.md` as the main material.\n"
        "Main material: `chapter.md` contains the indexed definitions and lemmas.\n"
        "Known gaps and extraction limits: no missing source sections are known.\n",
        encoding="utf-8",
    )
    (source_root / "chapter.md").write_text("Definition A.\nLemma B.\nTheorem C.\n", encoding="utf-8")


def test_material_views_reject_legacy_absolute_root_schema() -> None:
    with pytest.raises(ValidationError):
        SourceCorpusManifestView.model_validate(
            {
                "schema_version": 1,
                "repo_root": "/legacy/Repo",
                "summary": "Legacy source corpus.",
            }
        )
    with pytest.raises(ValidationError):
        SourceIndexView.model_validate(
            {
                "schema_version": 3,
                "repo_root": "/legacy/Repo",
                "summary": "Legacy source index view.",
            }
        )


def test_source_index_persists_domain_model_and_returns_view(tmp_path: Path) -> None:
    runtime = make_runtime()
    service = runtime.material
    _prepare_source(tmp_path)
    prepared = service.submit_source_corpus_prepared(
        tmp_path,
        entry_path="README.md",
        overview="Indexed source corpus.",
        preparation_summary="Prepared source files.",
    )
    assert prepared.ok
    scope = service.resolve_source_scope(tmp_path, source_scope=SourceScope(mode="all"))
    assert scope.ok and scope.value is not None
    assert service.open_source_index_update(
        tmp_path,
        resolved_scope=scope.value,
        index_policy="auto",
    ).ok
    overview_receipt = service.set_source_index_overview(
        tmp_path, overview="Compact indexed source."
    )
    assert overview_receipt.ok and overview_receipt.value is not None
    assert overview_receipt.value.changed
    assert overview_receipt.value.previous_overview == "Indexed source corpus."

    block = service.create_source_block(
        tmp_path,
        parent_id="root",
        kind="section",
        subtype=None,
        title="Chapter theorem",
        summary="The part of the source containing Definition A, Lemma B, and Theorem C.",
    )
    assert block.ok and block.value is not None
    ref = service.add_source_block_ref(
        tmp_path,
        block_id=block.value.block_id,
        path="chapter.md",
        start_line=1,
        end_line=3,
        role="primary",
    )
    assert ref.ok and ref.value is not None
    link = service.create_source_link(
        tmp_path,
        source_block_id=block.value.block_id,
        target_block_id=None,
        target_hint="The theorem statement.",
        link_kind="supports",
        evidence_ref_ids=[ref.value.refs[0].ref_id],
    )
    assert link.ok and link.value is not None

    index_json = tmp_path / ".lean_constellation" / "source_index" / "index.json"
    persisted = json.loads(index_json.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == 3
    assert "active_update_id" not in persisted
    assert persisted["active_file_scope"] == ["README.md", "chapter.md"]
    assert persisted["files"]["chapter.md"]["source_sha256"] is not None
    assert persisted["files"]["chapter.md"]["committed"] is False
    assert "repo_root" not in persisted
    persisted_ref = persisted["blocks"][block.value.block_id]["refs"][0]
    assert "path" not in persisted_ref
    assert persisted_ref["material_ref"] == {
        "kind": "source",
        "ref": {"path": "chapter.md", "start_line": 1, "end_line": 3},
    }
    persisted_link = persisted["links"][link.value.link_id]
    assert "evidence_ref_ids" not in persisted_link
    assert persisted_link["evidence_refs"] == [persisted_ref["material_ref"]]

    view = service.get_source_index(tmp_path)
    assert view.ok and view.value is not None
    view_ref = view.value.blocks[block.value.block_id].refs[0]
    assert view_ref.path == "chapter.md"
    assert view_ref.start_line == 1
    assert view_ref.end_line == 3
    assert view.value.links[link.value.link_id].evidence_ref_ids == [view_ref.ref_id]

    overview = service.get_source_index_overview(tmp_path)
    files = service.list_source_index_files(tmp_path)
    blocks = service.list_source_blocks(tmp_path, query="theorem", path="chapter.md")
    detail = service.get_source_block(tmp_path, block_id=block.value.block_id)

    assert overview.ok and overview.value is not None
    assert overview.value.overview == "Compact indexed source."
    assert overview.value.block_count == 1
    assert files.ok and files.value is not None
    assert [item.path for item in files.value.files] == ["README.md", "chapter.md"]
    assert blocks.ok and blocks.value is not None
    assert [item.block_id for item in blocks.value.blocks] == [block.value.block_id]
    assert blocks.value.blocks[0].ref_count == 1
    assert detail.ok and detail.value is not None
    assert detail.value.block.refs[0].path == "chapter.md"
    assert detail.value.adjacent_links[0].direction == "outgoing"
    assert detail.value.adjacent_links[0].evidence_ref_ids == [view_ref.ref_id]

    limited = service.list_source_blocks(tmp_path, limit=0)
    assert not limited.ok


def test_source_index_coverage_reports_compact_uncovered_ranges() -> None:
    runtime = make_runtime()

    def source_ref(ref_id: str, path: str, start_line: int, end_line: int) -> SourceBlockRef:
        return SourceBlockRef(
            ref_id=ref_id,
            material_ref=MaterialRef(
                kind="source",
                ref=SourceRef(path=path, start_line=start_line, end_line=end_line),
            ),
            role="primary",
        )

    index = SourceIndex(
        files={
            "chapter.md": SourceFileIndex(path="chapter.md", line_count=10, readable_text=True),
            "notes.md": SourceFileIndex(path="notes.md", line_count=3, readable_text=True),
            "artifact.bin": SourceFileIndex(path="artifact.bin", line_count=4, readable_text=False),
        },
        blocks={
            "active": SourceBlock(
                block_id="active",
                kind="section",
                title="Active",
                summary="Active refs.",
                refs=[
                    source_ref("ref_1", "chapter.md", 2, 4),
                    source_ref("ref_2", "chapter.md", 4, 5),
                    source_ref("ref_3", "chapter.md", 7, 7),
                    source_ref("ref_invalid", "chapter.md", 11, 12),
                ],
            ),
            "inactive": SourceBlock(
                block_id="inactive",
                kind="section",
                title="Inactive",
                summary="Inactive refs do not count.",
                refs=[source_ref("ref_inactive", "chapter.md", 8, 10)],
                active=False,
            ),
        },
    )

    coverage = runtime.material.source_index._source_index_coverage(index)

    assert coverage.ok and coverage.value is not None
    assert coverage.value.uncovered_file_count == 2
    assert [item.path for item in coverage.value.file_coverage] == ["chapter.md", "notes.md"]
    chapter = coverage.value.file_coverage[0]
    assert chapter.covered_line_count == 5
    assert chapter.uncovered_line_count == 5
    assert chapter.uncovered_range_count == 3
    assert [(item.start_line, item.end_line) for item in chapter.uncovered_ranges] == [
        (1, 1),
        (6, 6),
        (8, 10),
    ]
    notes = coverage.value.file_coverage[1]
    assert notes.covered_line_count == 0
    assert notes.uncovered_line_count == 3
    assert [(item.start_line, item.end_line) for item in notes.uncovered_ranges] == [(1, 3)]

    scoped = runtime.material.source_index._source_index_coverage(index, scope=["notes.md"])
    assert scoped.ok and scoped.value is not None
    assert [item.path for item in scoped.value.file_coverage] == ["notes.md"]


def test_source_index_coverage_truncates_only_materialized_gap_ranges() -> None:
    runtime = make_runtime()
    refs = [
        SourceBlockRef(
            ref_id=f"ref_{line}",
            material_ref=MaterialRef(
                kind="source",
                ref=SourceRef(path="fragmented.md", start_line=line, end_line=line),
            ),
            role="primary",
        )
        for line in range(1, 202, 2)
    ]
    index = SourceIndex(
        files={
            "fragmented.md": SourceFileIndex(
                path="fragmented.md",
                line_count=202,
                readable_text=True,
            )
        },
        blocks={
            "active": SourceBlock(
                block_id="active",
                kind="section",
                title="Fragmented",
                summary="Many small refs.",
                refs=refs,
            )
        },
    )

    coverage = runtime.material.source_index._source_index_coverage(index)

    assert coverage.ok and coverage.value is not None
    item = coverage.value.file_coverage[0]
    assert item.covered_line_count == 101
    assert item.uncovered_line_count == 101
    assert item.uncovered_range_count == 101
    assert len(item.uncovered_ranges) == 100
    assert item.uncovered_ranges_truncated
