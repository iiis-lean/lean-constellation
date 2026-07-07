from tests.unit_services_helpers import make_runtime

import inspect
from pathlib import Path

import pytest

from lean_constellation.services.material import MaterialService


def test_material_service_source_index_wrappers_have_explicit_signatures() -> None:
    signature = inspect.signature(MaterialService.update_source_block)
    assert all(parameter.kind is not inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())
    assert set(signature.parameters) == {"self", "repo_root", "block_id", "title", "summary", "kind", "subtype"}


def _prepare_source(service: MaterialService, repo_root: Path) -> None:
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text("# Entry\n\nMain source overview.\n", encoding="utf-8")
    (source_root / "chapter.md").write_text("Definition A.\nLemma B.\nTheorem C.\n", encoding="utf-8")
    prepared = service.submit_source_corpus_prepared(
        repo_root,
        entry_path="README.md",
        overview="Indexed source corpus.",
        preparation_summary="Prepared source files.",
    )
    assert prepared.ok


def _create_block_with_ref(service: MaterialService, repo_root: Path, *, title: str = "Chapter theorem"):
    block = service.create_source_block(
        repo_root,
        parent_id="root",
        kind="section",
        subtype=None,
        title=title,
        summary="The part of the source containing Definition A, Lemma B, and Theorem C.",
    )
    assert block.ok
    assert block.value is not None
    ref = service.add_source_block_ref(
        repo_root,
        block_id=block.value.block_id,
        path="chapter.md",
        start_line=1,
        end_line=3,
        role="primary",
    )
    assert ref.ok
    assert ref.value is not None
    return ref.value


def test_source_index_lifecycle_submit_and_commit(tmp_path: Path) -> None:
    service = make_runtime().material
    _prepare_source(service, tmp_path)

    created = service.create_draft_source_index(tmp_path)
    assert created.ok
    assert created.value is not None
    assert created.value.status == "draft"
    assert set(created.value.files) == {"README.md", "chapter.md"}

    early_submit = service.submit_source_index_builder_round(tmp_path, summary="Done.")
    assert not early_submit.ok
    assert early_submit.issues[0].kind == "source_index_no_blocks"

    block = service.create_source_block(
        tmp_path,
        parent_id="root",
        kind="section",
        subtype=None,
        title="Chapter theorem",
        summary="The part of the source containing Definition A, Lemma B, and Theorem C.",
    )
    assert block.ok
    assert block.value is not None

    ref = service.add_source_block_ref(
        tmp_path,
        block_id=block.value.block_id,
        path="chapter.md",
        start_line=1,
        end_line=3,
        role="primary",
    )
    assert ref.ok
    assert ref.value is not None
    assert ref.value.refs[0].ref_id == "ref_0001"

    refs_done = service.mark_block_refs_done(tmp_path, block_id=block.value.block_id)
    assert refs_done.ok
    assert refs_done.value is not None
    assert refs_done.value.passed

    links_done = service.mark_block_links_done(tmp_path, block_id=block.value.block_id)
    assert links_done.ok
    assert links_done.value is not None
    assert links_done.value.passed

    completed = service.mark_block_completed(tmp_path, block_id=block.value.block_id)
    assert completed.ok
    assert completed.value is not None
    assert completed.value.passed

    for path in ["README.md", "chapter.md"]:
        surveyed = service.set_file_survey_status(tmp_path, path=path, status="surveyed", summary=f"Surveyed {path}.")
        assert surveyed.ok
        indexed = service.set_file_indexing_status(tmp_path, path=path, status="indexed")
        assert indexed.ok

    validation = service.validate_source_index(tmp_path)
    assert validation.ok
    assert validation.value is not None
    assert validation.value.passed

    coverage = service.get_source_index_coverage(tmp_path)
    assert coverage.ok
    assert coverage.value is not None
    assert coverage.value.completed_block_count == 1
    assert coverage.value.pending_file_paths == []

    committed_before_commit = service.get_committed_source_index(tmp_path)
    assert not committed_before_commit.ok
    assert committed_before_commit.issues[0].kind == "source_index_not_committed"
    committed_coverage_before_commit = service.get_committed_source_index_coverage(tmp_path)
    assert not committed_coverage_before_commit.ok
    assert committed_coverage_before_commit.issues[0].kind == "source_index_not_committed"

    builder_submit = service.submit_source_index_builder_round(tmp_path, summary="Builder completed the source index.")
    assert builder_submit.ok
    assert builder_submit.value is not None
    assert builder_submit.value.coverage is not None

    review = service.submit_source_index_review_round(tmp_path, approved=True, summary="Source index is accurate.")
    assert review.ok
    assert review.value is not None
    assert review.value.approved is True

    commit = service.commit_source_index(tmp_path)
    assert commit.ok
    assert commit.value is not None
    assert commit.value.passed

    committed = service.get_source_index(tmp_path)
    assert committed.ok
    assert committed.value is not None
    assert committed.value.status == "committed"
    committed_read = service.get_committed_source_index(tmp_path)
    assert committed_read.ok
    assert committed_read.value is not None
    assert committed_read.value.status == "committed"
    committed_coverage = service.get_committed_source_index_coverage(tmp_path)
    assert committed_coverage.ok
    assert committed_coverage.value is not None
    assert committed_coverage.value.completed_block_count == 1

    mutation = service.set_source_index_overview(tmp_path, overview="Should fail.")
    assert not mutation.ok
    assert mutation.issues[0].kind == "source_index_committed"


def test_source_index_ref_and_review_gates(tmp_path: Path) -> None:
    service = make_runtime().material
    _prepare_source(service, tmp_path)
    service.create_draft_source_index(tmp_path)
    block = service.create_source_block(
        tmp_path,
        parent_id="root",
        kind="section",
        subtype=None,
        title="Bad ref block",
        summary="Block with a bad ref.",
    )
    assert block.ok
    assert block.value is not None

    bad_ref = service.add_source_block_ref(
        tmp_path,
        block_id=block.value.block_id,
        path="chapter.md",
        start_line=99,
        end_line=100,
        role="primary",
    )
    assert not bad_ref.ok
    assert bad_ref.issues[0].kind == "source_ref_range_invalid"

    rejected_review = service.submit_source_index_review_round(
        tmp_path,
        approved=False,
        summary="Needs repair.",
    )
    assert not rejected_review.ok
    assert rejected_review.issues[0].kind == "missing_review_feedback"


def test_source_index_create_get_and_overview_boundaries(tmp_path: Path) -> None:
    service = make_runtime().material

    missing = service.get_source_index(tmp_path)
    assert not missing.ok
    assert missing.issues[0].kind == "source_index_missing"

    _prepare_source(service, tmp_path)
    created = service.create_draft_source_index(tmp_path)
    idempotent = service.create_draft_source_index(tmp_path)
    updated = service.set_source_index_overview(tmp_path, overview="Refined source index overview.")
    empty = service.set_source_index_overview(tmp_path, overview=" ")

    assert created.ok and created.value is not None
    assert idempotent.ok and idempotent.value is not None
    assert idempotent.value.root_block_id == created.value.root_block_id
    assert updated.ok and updated.value is not None
    assert updated.value.overview == "Refined source index overview."
    assert not empty.ok
    assert empty.issues[0].kind == "source_index_overview_empty"


def test_source_index_block_create_update_and_ref_gates(tmp_path: Path) -> None:
    service = make_runtime().material
    _prepare_source(service, tmp_path)
    service.create_draft_source_index(tmp_path)

    parent_missing = service.create_source_block(
        tmp_path,
        parent_id="missing",
        kind="section",
        subtype=None,
        title="Missing parent",
        summary="Missing parent.",
    )
    empty_title = service.create_source_block(
        tmp_path,
        parent_id="root",
        kind="section",
        subtype=None,
        title=" ",
        summary="Missing title.",
    )
    block = _create_block_with_ref(service, tmp_path)
    updated = service.update_source_block(
        tmp_path,
        block_id=block.block_id,
        title="Updated title",
        summary="Updated summary.",
        kind="theorem_cluster",
    )
    missing_update = service.update_source_block(tmp_path, block_id="missing", title="Title")
    empty_update = service.update_source_block(tmp_path, block_id=block.block_id, summary=" ")
    with pytest.raises(TypeError):
        service.update_source_block(tmp_path, block_id=block.block_id, parent_id="root")  # type: ignore[call-arg]
    empty_role = service.add_source_block_ref(
        tmp_path,
        block_id=block.block_id,
        path="chapter.md",
        start_line=1,
        end_line=1,
        role=" ",
    )
    remove_missing = service.remove_source_block_ref(tmp_path, block_id=block.block_id, ref_id="missing")

    assert not parent_missing.ok
    assert parent_missing.issues[0].kind == "source_block_parent_missing"
    assert not empty_title.ok
    assert empty_title.issues[0].kind == "source_block_field_empty"
    assert updated.ok and updated.value is not None
    assert updated.value.title == "Updated title"
    assert updated.value.kind == "theorem_cluster"
    assert not missing_update.ok
    assert missing_update.issues[0].kind == "source_block_missing"
    assert not empty_update.ok
    assert empty_update.issues[0].kind == "source_block_field_empty"
    assert not empty_role.ok
    assert empty_role.issues[0].kind == "source_ref_role_empty"
    assert not remove_missing.ok
    assert remove_missing.issues[0].kind == "source_ref_missing"


def test_source_index_lifecycle_order_and_file_status_gates(tmp_path: Path) -> None:
    service = make_runtime().material
    _prepare_source(service, tmp_path)
    service.create_draft_source_index(tmp_path)
    block = service.create_source_block(
        tmp_path,
        parent_id="root",
        kind="section",
        subtype=None,
        title="No refs block",
        summary="Needs refs.",
    )
    assert block.ok and block.value is not None

    refs_done_without_refs = service.mark_block_refs_done(tmp_path, block_id=block.value.block_id)
    links_done_too_early = service.mark_block_links_done(tmp_path, block_id=block.value.block_id)
    completed_too_early = service.mark_block_completed(tmp_path, block_id=block.value.block_id)
    missing_file = service.set_file_survey_status(tmp_path, path="missing.md", status="surveyed")
    invalid_survey = service.set_file_survey_status(tmp_path, path="README.md", status="done")  # type: ignore[arg-type]
    invalid_indexing = service.set_file_indexing_status(tmp_path, path="README.md", status="done")  # type: ignore[arg-type]

    assert refs_done_without_refs.ok and refs_done_without_refs.value is not None
    assert not refs_done_without_refs.value.passed
    assert refs_done_without_refs.value.issues[0].kind == "source_block_refs_missing"
    assert links_done_too_early.ok and links_done_too_early.value is not None
    assert not links_done_too_early.value.passed
    assert links_done_too_early.value.issues[0].kind == "source_block_refs_not_done"
    assert completed_too_early.ok and completed_too_early.value is not None
    assert not completed_too_early.value.passed
    assert completed_too_early.value.issues[0].kind == "source_block_links_not_done"
    assert not missing_file.ok
    assert missing_file.issues[0].kind == "source_file_missing"
    assert not invalid_survey.ok
    assert invalid_survey.issues[0].kind == "invalid_source_file_survey_status"
    assert not invalid_indexing.ok
    assert invalid_indexing.issues[0].kind == "invalid_source_file_indexing_status"


def test_source_index_link_gates_and_validation_after_ref_removal(tmp_path: Path) -> None:
    service = make_runtime().material
    _prepare_source(service, tmp_path)
    service.create_draft_source_index(tmp_path)
    source = _create_block_with_ref(service, tmp_path, title="Source block")
    target = _create_block_with_ref(service, tmp_path, title="Target block")
    ref_id = source.refs[0].ref_id

    target_missing = service.create_source_link(
        tmp_path,
        source_block_id=source.block_id,
        target_block_id="missing",
        target_hint=None,
        link_kind="supports",
        evidence_ref_ids=[ref_id],
    )
    evidence_empty = service.create_source_link(
        tmp_path,
        source_block_id=source.block_id,
        target_block_id=target.block_id,
        target_hint=None,
        link_kind="supports",
        evidence_ref_ids=[],
    )
    evidence_missing = service.create_source_link(
        tmp_path,
        source_block_id=source.block_id,
        target_block_id=target.block_id,
        target_hint=None,
        link_kind="supports",
        evidence_ref_ids=["missing"],
    )
    kind_empty = service.create_source_link(
        tmp_path,
        source_block_id=source.block_id,
        target_block_id=target.block_id,
        target_hint=None,
        link_kind=" ",
        evidence_ref_ids=[ref_id],
    )
    invalid_target = service.create_source_link(
        tmp_path,
        source_block_id=source.block_id,
        target_block_id=None,
        target_hint=None,
        link_kind="supports",
        evidence_ref_ids=[ref_id],
    )
    hint_link = service.create_source_link(
        tmp_path,
        source_block_id=source.block_id,
        target_block_id=None,
        target_hint="Later block",
        link_kind="supports",
        evidence_ref_ids=[ref_id],
    )
    service.remove_source_block_ref(tmp_path, block_id=source.block_id, ref_id=ref_id)
    validation = service.validate_source_index(tmp_path)

    assert not target_missing.ok
    assert target_missing.issues[0].kind == "source_link_target_missing"
    assert not evidence_empty.ok
    assert evidence_empty.issues[0].kind == "source_link_evidence_empty"
    assert not evidence_missing.ok
    assert evidence_missing.issues[0].kind == "source_link_evidence_missing"
    assert not kind_empty.ok
    assert kind_empty.issues[0].kind == "source_link_kind_empty"
    assert not invalid_target.ok
    assert invalid_target.issues[0].kind == "source_link_invalid"
    assert hint_link.ok and hint_link.value is not None
    assert validation.ok and validation.value is not None
    assert "source_link_evidence_empty" in {issue.kind for issue in validation.value.issues}


def test_source_index_builder_review_commit_failure_and_repeat_commit(tmp_path: Path) -> None:
    service = make_runtime().material
    _prepare_source(service, tmp_path)
    service.create_draft_source_index(tmp_path)
    block = _create_block_with_ref(service, tmp_path)
    service.mark_block_refs_done(tmp_path, block_id=block.block_id)
    service.mark_block_links_done(tmp_path, block_id=block.block_id)
    service.mark_block_completed(tmp_path, block_id=block.block_id)

    missing_summary = service.submit_source_index_builder_round(tmp_path, summary=" ")
    commit_with_pending_files = service.commit_source_index(tmp_path)
    rejected = service.submit_source_index_review_round(
        tmp_path,
        approved=False,
        summary="Needs more detail.",
        feedback="Mark files as surveyed and indexed.",
    )
    approved = service.submit_source_index_review_round(tmp_path, approved=True, summary="Approved after file status fix.")
    for path in ["README.md", "chapter.md"]:
        service.set_file_survey_status(tmp_path, path=path, status="surveyed", summary=f"Surveyed {path}.")
        service.set_file_indexing_status(tmp_path, path=path, status="indexed")
    committed = service.commit_source_index(tmp_path)
    repeat = service.commit_source_index(tmp_path)

    assert not missing_summary.ok
    assert missing_summary.issues[0].kind == "missing_submission_summary"
    assert commit_with_pending_files.ok and commit_with_pending_files.value is not None
    assert not commit_with_pending_files.value.passed
    assert "source_file_pending" in {issue.kind for issue in commit_with_pending_files.value.issues}
    assert rejected.ok and rejected.value is not None
    assert rejected.value.approved is False
    assert approved.ok and approved.value is not None
    assert approved.value.approved is True
    assert committed.ok and committed.value is not None and committed.value.passed
    assert not repeat.ok
    assert repeat.issues[0].kind == "source_index_committed"
