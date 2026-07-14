from __future__ import annotations

from pathlib import Path

from lean_constellation.domain.repo_run import SourceScope
from lean_constellation.services.foundation import FoundationContext
from tests.unit_services_helpers import make_runtime, publish_native_provider_release


def test_release_availability_and_scoped_source_index_share_one_runtime_without_read_writes(tmp_path: Path) -> None:
    runtime = make_runtime()
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(
        "# Entry\n\n"
        "Source provenance: local fixture.\n"
        "Reading order: read chapter.md.\n"
        "Main material: chapter.md.\n"
        "Known gaps and extraction limits: none.\n",
        encoding="utf-8",
    )
    (source_root / "chapter.md").write_text("Definition A.\nTheorem B.\n", encoding="utf-8")
    assert runtime.material.submit_source_corpus_prepared(
        tmp_path,
        entry_path="README.md",
        overview="W1 integration source.",
        preparation_summary="Prepared W1 source.",
    ).ok

    scope = runtime.material.resolve_source_scope(
        tmp_path,
        source_scope=SourceScope(mode="selected", selectors=["chapter.md"]),
    )
    assert scope.ok and scope.value is not None
    opened = runtime.material.open_source_index_update(
        tmp_path,
        resolved_scope=scope.value,
        index_policy="auto",
    )
    assert opened.ok and opened.value is not None and opened.value.outcome == "opened"
    block = runtime.material.create_source_block(
        tmp_path,
        parent_id="root",
        kind="statement",
        title="Theorem B",
        summary="The chapter theorem.",
    )
    assert block.ok and block.value is not None
    assert runtime.material.add_source_block_ref(
        tmp_path,
        block_id=block.value.block_id,
        path="chapter.md",
        start_line=1,
        end_line=2,
        role="primary",
    ).ok
    assert runtime.material.mark_block_refs_done(
        tmp_path,
        block_id=block.value.block_id,
    ).value.passed
    assert runtime.material.mark_block_links_done(
        tmp_path,
        block_id=block.value.block_id,
    ).value.passed
    assert runtime.material.mark_block_completed(
        tmp_path,
        block_id=block.value.block_id,
    ).value.passed
    assert runtime.material.set_file_survey_status(
        tmp_path,
        path="chapter.md",
        status="surveyed",
        summary="Surveyed chapter.",
    ).ok
    assert runtime.material.set_file_indexing_status(
        tmp_path,
        path="chapter.md",
        status="indexed",
    ).ok
    gate = runtime.material.validate_source_index_update(
        tmp_path,
        baseline_index=None,
        expected_baseline_digest=opened.value.baseline_digest,
        resolved_scope=["chapter.md"],
        require_completed=True,
    )
    assert gate.ok and gate.value is not None and gate.value.gate.passed
    assert runtime.material.commit_source_index_update(
        tmp_path,
        validated=gate.value,
    ).ok

    release = publish_native_provider_release(runtime, tmp_path, summary="W1 integration release.")
    index_path = tmp_path / ".lean_constellation" / "source_index" / "index.json"
    publication_path = runtime.repo_workspace.metadata._repo_publication_path(tmp_path)
    release_path = runtime.foundation.layout.release_path(
        FoundationContext(repo_root=tmp_path),
        release.release_id,
    )
    before = {path: path.read_bytes() for path in (index_path, publication_path, release_path)}

    committed = runtime.material.get_committed_source_index(tmp_path)
    coverage = runtime.material.get_committed_source_index_coverage(tmp_path)
    availability = runtime.repo_workspace.provider_availability.check_provider_available(tmp_path)
    baseline = runtime.repo_workspace.release.resolve_release_baseline(tmp_path)

    assert committed.ok and committed.value is not None and committed.value.schema_version == 3
    assert coverage.ok and coverage.value is not None and coverage.value.pending_file_paths == ["README.md"]
    assert availability.ok and availability.value is not None and availability.value.passed
    assert baseline.ok and baseline.value is not None and baseline.value.release_id == release.release_id
    assert not (tmp_path / ".lean_constellation" / "provider_ready.json").exists()
    assert {path: path.read_bytes() for path in before} == before
