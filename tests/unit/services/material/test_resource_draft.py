from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.material import ResourceDraft, ResourceDraftStatus


def _write_valid_draft_files(draft_root: Path, *, text: str = "alpha\nbeta theorem\n") -> None:
    (draft_root / "README.md").write_text("# Draft resource\n\nCurated material.", encoding="utf-8")
    (draft_root / "original" / "raw.txt").write_text("raw material\n", encoding="utf-8")
    (draft_root / "normalized" / "main.md").write_text(text, encoding="utf-8")


def _load_draft(repo_root: Path, metadata_path: str) -> ResourceDraft:
    loaded = make_runtime().foundation.store.read_json(Path(metadata_path), ResourceDraft)
    assert loaded.ok and loaded.value is not None
    return loaded.value


def test_allocate_resource_draft_creates_metadata_and_work_dirs(tmp_path: Path) -> None:
    service = make_runtime().material

    draft = service.allocate_resource_draft(
        tmp_path,
        target="https://Example.com/math/page/",
        resource_kind="web_url",
        title_hint="Example page",
    )

    assert draft.ok and draft.value is not None
    assert draft.value.draft.status == ResourceDraftStatus.ALLOCATED
    assert draft.value.draft.target.canonical_locator == "https://example.com/math/page"
    assert Path(draft.value.draft_root).is_dir()
    assert Path(draft.value.original_dir).is_dir()
    assert Path(draft.value.normalized_dir).is_dir()
    assert Path(draft.value.metadata_path).is_file()


def test_check_resource_draft_requires_readme_or_manifest_and_normalized_artifact(tmp_path: Path) -> None:
    service = make_runtime().material
    draft = service.allocate_resource_draft(tmp_path, target="https://example.com/missing")
    assert draft.ok and draft.value is not None

    missing = service.check_resource_draft(tmp_path, draft_id=draft.value.draft.draft_id)

    assert missing.ok and missing.value is not None
    assert missing.value.passed is False
    issue_kinds = {issue.kind for issue in missing.value.issues}
    assert "resource_draft_readme_or_manifest_missing" in issue_kinds
    assert "resource_draft_normalized_artifact_missing" in issue_kinds

    _write_valid_draft_files(Path(draft.value.draft_root))
    passed = service.check_resource_draft(tmp_path, draft_id=draft.value.draft.draft_id)

    assert passed.ok and passed.value is not None
    assert passed.value.passed is True
    reloaded = _load_draft(tmp_path, draft.value.metadata_path)
    assert reloaded.status == ResourceDraftStatus.CHECKED
    assert reloaded.checked_at is not None


def test_finalize_resource_draft_promotes_to_resource_library_and_reloads(tmp_path: Path) -> None:
    service = make_runtime().material
    draft = service.allocate_resource_draft(tmp_path, target="https://example.com/final", title_hint="Final resource")
    assert draft.ok and draft.value is not None
    _write_valid_draft_files(Path(draft.value.draft_root))

    finalized = service.finalize_resource_draft(
        tmp_path,
        draft_id=draft.value.draft.draft_id,
        summary="Finalized curated resource.",
    )

    assert finalized.ok and finalized.value is not None
    resource_key = finalized.value.resource.resource_key
    assert finalized.value.resource.title == "Final resource"
    assert finalized.value.resource.normalized_entry == "normalized/main.md"
    assert (tmp_path / ".lean_constellation" / "resources" / "items" / resource_key / "resource.json").is_file()
    assert (tmp_path / ".lean_constellation" / "resources" / "items" / resource_key / "normalized" / "main.md").is_file()
    reloaded_draft = _load_draft(tmp_path, draft.value.metadata_path)
    assert reloaded_draft.status == ResourceDraftStatus.FINALIZED
    assert reloaded_draft.resource_key == resource_key
    listed = service.resource_library.list_resources(tmp_path)
    assert listed.ok and listed.value is not None
    assert [item.resource_key for item in listed.value] == [resource_key]


def test_duplicate_resource_target_cannot_allocate_second_draft(tmp_path: Path) -> None:
    service = make_runtime().material
    first = service.allocate_resource_draft(tmp_path, target="https://example.com/duplicate")
    assert first.ok and first.value is not None
    _write_valid_draft_files(Path(first.value.draft_root))
    assert service.finalize_resource_draft(tmp_path, draft_id=first.value.draft.draft_id, summary="Done.").ok

    duplicate = service.allocate_resource_draft(tmp_path, target="https://example.com/duplicate/")

    assert not duplicate.ok
    assert duplicate.issues[0].kind == "resource_duplicate"


def test_invalid_or_abandoned_draft_cannot_be_finalized(tmp_path: Path) -> None:
    service = make_runtime().material
    invalid = service.check_resource_draft(tmp_path, draft_id="../bad")
    assert not invalid.ok
    assert invalid.issues[0].kind == "invalid_resource_draft_id"

    draft = service.allocate_resource_draft(tmp_path, target="https://example.com/abandoned")
    assert draft.ok and draft.value is not None
    _write_valid_draft_files(Path(draft.value.draft_root))
    abandoned = service.abandon_resource_draft(tmp_path, draft_id=draft.value.draft.draft_id, reason="No longer needed.")
    assert abandoned.ok and abandoned.value is not None
    assert abandoned.value.draft.status == ResourceDraftStatus.ABANDONED

    finalized = service.finalize_resource_draft(tmp_path, draft_id=draft.value.draft.draft_id, summary="Try finalizing.")

    assert not finalized.ok
    assert finalized.issues[0].kind == "resource_draft_abandoned"
