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


def test_source_index_repair_updates_refs_and_links_in_place(tmp_path: Path) -> None:
    runtime = make_runtime()
    service = runtime.material
    _prepare_source(tmp_path)
    assert service.submit_source_corpus_prepared(
        tmp_path,
        entry_path="README.md",
        overview="Repair fixture.",
        preparation_summary="Prepared repair fixture.",
    ).ok
    scope = service.resolve_source_scope(tmp_path, source_scope=SourceScope(mode="all"))
    assert scope.ok and scope.value is not None
    assert service.open_source_index_update(
        tmp_path,
        resolved_scope=scope.value,
        index_policy="auto",
    ).ok

    source = service.create_source_block(
        tmp_path,
        parent_id="root",
        kind="proof",
        title="Proof block",
        summary="Proof evidence to repair.",
    )
    target = service.create_source_block(
        tmp_path,
        parent_id="root",
        kind="theorem",
        title="Target theorem",
        summary="Correct target theorem.",
    )
    assert source.ok and source.value is not None
    assert target.ok and target.value is not None
    added = service.add_source_block_ref(
        tmp_path,
        block_id=source.value.block_id,
        path="chapter.md",
        start_line=1,
        end_line=1,
        role="primary",
    )
    assert added.ok and added.value is not None
    ref_id = added.value.refs[0].ref_id
    created = service.create_source_link(
        tmp_path,
        source_block_id=source.value.block_id,
        target_block_id=None,
        target_hint="Incorrect provisional target.",
        link_kind="supports",
        evidence_ref_ids=[ref_id],
    )
    assert created.ok and created.value is not None

    other = service.create_source_block(
        tmp_path,
        parent_id="root",
        kind="remark",
        title="Independent block",
        summary="An independent block using the same source range.",
    )
    assert other.ok and other.value is not None
    other_ref = service.add_source_block_ref(
        tmp_path,
        block_id=other.value.block_id,
        path="chapter.md",
        start_line=1,
        end_line=1,
        role="primary",
    )
    assert other_ref.ok and other_ref.value is not None
    other_link = service.create_source_link(
        tmp_path,
        source_block_id=other.value.block_id,
        target_block_id=None,
        target_hint="Independent target.",
        link_kind="supports",
        evidence_ref_ids=[other_ref.value.refs[0].ref_id],
    )
    assert other_link.ok and other_link.value is not None

    updated_ref = service.update_source_block_ref(
        tmp_path,
        block_id=source.value.block_id,
        ref_id=ref_id,
        path="chapter.md",
        start_line=2,
        end_line=3,
        role="proof",
    )
    assert updated_ref.ok and updated_ref.value is not None
    ref_view = next(item for item in updated_ref.value.refs if item.ref_id == ref_id)
    assert (ref_view.start_line, ref_view.end_line, ref_view.role) == (2, 3, "proof")
    after_ref = service.get_source_index(tmp_path)
    assert after_ref.ok and after_ref.value is not None
    link_after_ref = after_ref.value.links[created.value.link_id]
    assert link_after_ref.evidence_ref_ids == [ref_id]
    assert link_after_ref.evidence_refs[0].ref == SourceRef(
        path="chapter.md",
        start_line=2,
        end_line=3,
    )
    assert after_ref.value.links[other_link.value.link_id].evidence_refs[0].ref == SourceRef(
        path="chapter.md",
        start_line=1,
        end_line=1,
    )

    updated_link = service.update_source_link(
        tmp_path,
        link_id=created.value.link_id,
        target_block_id=target.value.block_id,
        target_hint=None,
        link_kind="proves",
        evidence_ref_ids=[ref_id],
    )
    assert updated_link.ok and updated_link.value is not None
    assert updated_link.value.link_id == created.value.link_id
    assert updated_link.value.target_block_id == target.value.block_id
    assert updated_link.value.target_hint is None
    assert updated_link.value.link_kind == "proves"
    repaired = service.get_source_index(tmp_path)
    assert repaired.ok and repaired.value is not None
    assert sorted(repaired.value.links) == sorted(
        [created.value.link_id, other_link.value.link_id]
    )
    assert repaired.value.links[created.value.link_id].target_block_id == target.value.block_id
    assert repaired.value.links[other_link.value.link_id].target_hint == "Independent target."


def test_remove_source_block_ref_rejects_in_use_ref_without_writing(tmp_path: Path) -> None:
    runtime = make_runtime()
    service = runtime.material
    _prepare_source(tmp_path)
    assert service.submit_source_corpus_prepared(
        tmp_path,
        entry_path="README.md",
        overview="Removal fixture.",
        preparation_summary="Prepared removal fixture.",
    ).ok
    scope = service.resolve_source_scope(tmp_path, source_scope=SourceScope(mode="all"))
    assert scope.ok and scope.value is not None
    assert service.open_source_index_update(
        tmp_path,
        resolved_scope=scope.value,
        index_policy="auto",
    ).ok
    block = service.create_source_block(
        tmp_path,
        parent_id="root",
        kind="proof",
        title="Removable proof",
        summary="Proof with removable relation.",
    )
    assert block.ok and block.value is not None
    added = service.add_source_block_ref(
        tmp_path,
        block_id=block.value.block_id,
        path="chapter.md",
        start_line=1,
        end_line=2,
        role="primary",
    )
    assert added.ok and added.value is not None
    ref_id = added.value.refs[0].ref_id
    assert service.mark_block_refs_done(tmp_path, block_id=block.value.block_id).value.passed
    linked = service.create_source_link(
        tmp_path,
        source_block_id=block.value.block_id,
        target_block_id=None,
        target_hint="A theorem.",
        link_kind="proves",
        evidence_ref_ids=[ref_id],
    )
    assert linked.ok and linked.value is not None
    linked_second = service.create_source_link(
        tmp_path,
        source_block_id=block.value.block_id,
        target_block_id=None,
        target_hint="Another theorem.",
        link_kind="supports",
        evidence_ref_ids=[ref_id],
    )
    assert linked_second.ok and linked_second.value is not None
    assert service.mark_block_links_done(tmp_path, block_id=block.value.block_id).value.passed
    assert service.mark_block_completed(tmp_path, block_id=block.value.block_id).value.passed

    other = service.create_source_block(
        tmp_path,
        parent_id="root",
        kind="remark",
        title="Independent use",
        summary="The same source range owned by another block.",
    )
    assert other.ok and other.value is not None
    other_ref = service.add_source_block_ref(
        tmp_path,
        block_id=other.value.block_id,
        path="chapter.md",
        start_line=1,
        end_line=2,
        role="primary",
    )
    assert other_ref.ok and other_ref.value is not None
    other_link = service.create_source_link(
        tmp_path,
        source_block_id=other.value.block_id,
        target_block_id=None,
        target_hint="Independent target.",
        link_kind="supports",
        evidence_ref_ids=[other_ref.value.refs[0].ref_id],
    )
    assert other_link.ok and other_link.value is not None

    index_path = tmp_path / ".lean_constellation" / "source_index" / "index.json"
    before = index_path.read_bytes()
    rejected = service.remove_source_block_ref(
        tmp_path,
        block_id=block.value.block_id,
        ref_id=ref_id,
    )
    assert not rejected.ok
    assert rejected.issues[0].kind == "source_ref_in_use"
    assert rejected.issues[0].current == ", ".join(
        sorted([linked.value.link_id, linked_second.value.link_id])
    )
    assert index_path.read_bytes() == before

    removed_link = service.remove_source_link(tmp_path, link_id=linked.value.link_id)
    assert removed_link.ok and removed_link.value is not None
    assert removed_link.value.link_id == linked.value.link_id
    removed_second = service.remove_source_link(tmp_path, link_id=linked_second.value.link_id)
    assert removed_second.ok and removed_second.value is not None
    after_link = service.get_source_index(tmp_path)
    assert after_link.ok and after_link.value is not None
    assert linked.value.link_id not in after_link.value.links
    assert linked_second.value.link_id not in after_link.value.links
    source_after_link = after_link.value.blocks[block.value.block_id]
    assert linked.value.link_id not in source_after_link.link_ids
    assert source_after_link.lifecycle_status == "refs_done"

    removed_ref = service.remove_source_block_ref(
        tmp_path,
        block_id=block.value.block_id,
        ref_id=ref_id,
    )
    assert removed_ref.ok and removed_ref.value is not None
    assert removed_ref.value.refs == []
    assert removed_ref.value.lifecycle_status == "draft"
    final_index = service.get_source_index(tmp_path)
    assert final_index.ok and final_index.value is not None
    assert other_link.value.link_id in final_index.value.links


def test_remove_source_link_clears_source_index_and_reopens_links_gate(tmp_path: Path) -> None:
    runtime = make_runtime()
    service = runtime.material
    _prepare_source(tmp_path)
    assert service.submit_source_corpus_prepared(
        tmp_path,
        entry_path="README.md",
        overview="Link removal fixture.",
        preparation_summary="Prepared link removal fixture.",
    ).ok
    scope = service.resolve_source_scope(tmp_path, source_scope=SourceScope(mode="all"))
    assert scope.ok and scope.value is not None
    assert service.open_source_index_update(
        tmp_path,
        resolved_scope=scope.value,
        index_policy="auto",
    ).ok
    block = service.create_source_block(
        tmp_path,
        parent_id="root",
        kind="proof",
        title="Proof",
        summary="Proof relation.",
    )
    assert block.ok and block.value is not None
    added = service.add_source_block_ref(
        tmp_path,
        block_id=block.value.block_id,
        path="chapter.md",
        start_line=1,
        end_line=1,
        role="primary",
    )
    assert added.ok and added.value is not None
    linked = service.create_source_link(
        tmp_path,
        source_block_id=block.value.block_id,
        target_block_id=None,
        target_hint="Target.",
        link_kind="supports",
        evidence_ref_ids=[added.value.refs[0].ref_id],
    )
    assert linked.ok and linked.value is not None
    assert service.mark_block_links_done(tmp_path, block_id=block.value.block_id).value.passed

    removed = service.remove_source_link(tmp_path, link_id=linked.value.link_id)
    assert removed.ok and removed.value is not None
    detail = service.get_source_block(
        tmp_path,
        block_id=block.value.block_id,
        require_committed=False,
    )
    assert detail.ok and detail.value is not None
    assert detail.value.block.lifecycle_status == "refs_done"
    assert detail.value.block.link_ids == []
    assert detail.value.adjacent_links == []


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
