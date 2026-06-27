from __future__ import annotations

from pathlib import Path

import pytest

from lean_constellation.services.material import MaterialService


def _write_real_source_corpus(repo_root: Path) -> None:
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(
        "\n".join(
            [
                "# Real source corpus",
                "",
                "This corpus states a compact fixed point argument.",
                "Chapter one contains a definition and a theorem.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (source_root / "chapter1.md").write_text(
        "\n".join(
            [
                "Definition: a contraction decreases distances.",
                "Lemma: contractions have controlled iterates.",
                "Theorem: a complete metric space has a fixed point for each contraction.",
                "Proof: combine the lemma with completeness.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (source_root / "appendix.txt").write_text(
        "Appendix: notation conventions.\nSecond appendix line.\n",
        encoding="utf-8",
    )


@pytest.mark.real
def test_material_source_corpus_index_real_lifecycle(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_real_source_corpus(repo_root)
    service = MaterialService()

    scan = service.scan_source_corpus(repo_root)
    assert scan.ok
    assert scan.value is not None
    assert {item.path for item in scan.value.files} == {"README.md", "appendix.txt", "chapter1.md"}

    prepared = service.submit_source_corpus_prepared(
        repo_root,
        entry_path="README.md",
        overview="A real-test source corpus for fixed point material.",
        preparation_summary="Prepared local markdown and text source files.",
    )
    assert prepared.ok
    assert prepared.value is not None
    assert prepared.value.manifest.entry_path == "README.md"
    assert (repo_root / ".lean_constellation" / "source_corpus" / "manifest.json").exists()

    source_read = service.read_source_range(
        repo_root,
        path="chapter1.md",
        start_line=2,
        end_line=3,
        context_lines=1,
    )
    assert source_read.ok
    assert source_read.value is not None
    assert "2: Lemma: contractions have controlled iterates." in source_read.value.text_with_line_numbers
    assert "1: Definition: a contraction decreases distances." in (source_read.value.before_context or "")

    search = service.search_material_text(repo_root, query="fixed point", scope="source")
    assert search.ok
    assert search.value is not None
    assert len(search.value.hits) >= 1
    assert any(hit.locator == "chapter1.md" for hit in search.value.hits)

    created = service.create_draft_source_index(repo_root)
    assert created.ok
    assert created.value is not None
    assert set(created.value.files) == {"README.md", "appendix.txt", "chapter1.md"}

    definition = service.create_source_block(
        repo_root,
        parent_id="root",
        kind="definition",
        title="Contraction definition",
        summary="The source definition describing contractions.",
    )
    theorem = service.create_source_block(
        repo_root,
        parent_id="root",
        kind="theorem",
        title="Fixed point theorem",
        summary="The main fixed point theorem and proof sketch.",
    )
    assert definition.ok and definition.value is not None
    assert theorem.ok and theorem.value is not None

    definition_ref = service.add_source_block_ref(
        repo_root,
        block_id=definition.value.block_id,
        path="chapter1.md",
        start_line=1,
        end_line=1,
        role="definition statement",
    )
    theorem_ref = service.add_source_block_ref(
        repo_root,
        block_id=theorem.value.block_id,
        path="chapter1.md",
        start_line=3,
        end_line=4,
        role="main theorem and proof sketch",
    )
    assert definition_ref.ok and definition_ref.value is not None
    assert theorem_ref.ok and theorem_ref.value is not None

    for block_id in (definition.value.block_id, theorem.value.block_id):
        refs_done = service.mark_block_refs_done(repo_root, block_id=block_id)
        assert refs_done.ok
        assert refs_done.value is not None
        assert refs_done.value.passed

    link = service.create_source_link(
        repo_root,
        source_block_id=theorem.value.block_id,
        target_block_id=definition.value.block_id,
        target_hint=None,
        link_kind="uses-definition",
        evidence_ref_ids=[theorem_ref.value.refs[0].ref_id],
    )
    assert link.ok
    assert link.value is not None

    for block_id in (definition.value.block_id, theorem.value.block_id):
        links_done = service.mark_block_links_done(repo_root, block_id=block_id)
        assert links_done.ok
        assert links_done.value is not None
        assert links_done.value.passed
        completed = service.mark_block_completed(repo_root, block_id=block_id)
        assert completed.ok
        assert completed.value is not None
        assert completed.value.passed

    for path in ("README.md", "appendix.txt", "chapter1.md"):
        surveyed = service.set_file_survey_status(repo_root, path=path, status="surveyed", summary=f"Surveyed {path}.")
        indexed = service.set_file_indexing_status(repo_root, path=path, status="indexed")
        assert surveyed.ok
        assert indexed.ok

    validation = service.validate_source_index(repo_root)
    coverage = service.get_source_index_coverage(repo_root)
    builder_submit = service.submit_source_index_builder_round(
        repo_root,
        summary="Builder completed real-test SourceIndex.",
    )
    reviewer_submit = service.submit_source_index_review_round(
        repo_root,
        approved=True,
        summary="Reviewer approved real-test SourceIndex.",
    )
    commit = service.commit_source_index(repo_root)
    committed = service.get_source_index(repo_root)

    assert validation.ok and validation.value is not None and validation.value.passed
    assert coverage.ok and coverage.value is not None
    assert coverage.value.block_count == 2
    assert coverage.value.completed_block_count == 2
    assert coverage.value.pending_file_paths == []
    assert builder_submit.ok and builder_submit.value is not None
    assert builder_submit.value.coverage is not None
    assert reviewer_submit.ok and reviewer_submit.value is not None
    assert reviewer_submit.value.approved is True
    assert commit.ok and commit.value is not None and commit.value.passed
    assert committed.ok and committed.value is not None
    assert committed.value.status == "committed"
    assert committed.value.links[link.value.link_id].target_block_id == definition.value.block_id

    late_mutation = service.set_source_index_overview(repo_root, overview="This must be rejected.")
    assert not late_mutation.ok
    assert late_mutation.issues[0].kind == "source_index_committed"
