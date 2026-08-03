import hashlib
import json
from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.material import ResourceDraft, ResourceDraftStatus, ResourceMaterialManifest


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


def test_check_resource_draft_requires_readme_manifest_and_normalized_artifact(tmp_path: Path) -> None:
    service = make_runtime().material
    draft = service.allocate_resource_draft(tmp_path, target="https://example.com/missing")
    assert draft.ok and draft.value is not None

    missing = service.check_resource_draft(tmp_path, draft_id=draft.value.draft.draft_id)

    assert missing.ok and missing.value is not None
    assert missing.value.passed is False
    issue_kinds = {issue.kind for issue in missing.value.issues}
    assert "resource_draft_readme_missing" in issue_kinds
    assert "resource_draft_normalized_artifact_missing" in issue_kinds

    _write_valid_draft_files(Path(draft.value.draft_root))
    passed = service.check_resource_draft(tmp_path, draft_id=draft.value.draft.draft_id)

    assert passed.ok and passed.value is not None
    assert passed.value.passed is True
    assert Path(draft.value.manifest_path).is_file()
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


def test_resource_manifest_requires_explicit_canonical_when_outputs_are_ambiguous(tmp_path: Path) -> None:
    service = make_runtime().material
    draft = service.allocate_resource_draft(tmp_path, target="https://example.com/ambiguous")
    assert draft.ok and draft.value is not None
    root = Path(draft.value.draft_root)
    (root / "README.md").write_text("# Ambiguous\n", encoding="utf-8")
    (root / "normalized" / "a.md").write_text("first entry\n", encoding="utf-8")
    (root / "normalized" / "b.md").write_text("selected entry\n", encoding="utf-8")

    ambiguous = service.check_resource_draft(tmp_path, draft_id=draft.value.draft.draft_id)
    selected = service.refresh_resource_draft_manifest(
        tmp_path,
        draft_id=draft.value.draft.draft_id,
        canonical_normalized_entry="normalized/b.md",
    )
    checked = service.check_resource_draft(tmp_path, draft_id=draft.value.draft.draft_id)
    finalized = service.finalize_resource_draft(
        tmp_path,
        draft_id=draft.value.draft.draft_id,
        summary="Selected explicit canonical entry.",
    )

    assert ambiguous.ok and ambiguous.value is not None and not ambiguous.value.passed
    assert "resource_manifest_canonical_entry_ambiguous" in {
        issue.kind for issue in ambiguous.value.issues
    }
    assert selected.ok and selected.value is not None
    assert selected.value.canonical_normalized_entry == "normalized/b.md"
    assert checked.ok and checked.value is not None and checked.value.passed
    assert finalized.ok and finalized.value is not None
    assert finalized.value.resource.normalized_entry == "normalized/b.md"
    assert finalized.value.resource.content_hash == hashlib.sha256(b"selected entry\n").hexdigest()


def test_resource_manifest_rejects_old_schema_and_binary_normalized_entry(tmp_path: Path) -> None:
    service = make_runtime().material
    old = service.allocate_resource_draft(tmp_path, target="https://example.com/old-manifest")
    assert old.ok and old.value is not None
    old_root = Path(old.value.draft_root)
    (old_root / "README.md").write_text("# Old\n", encoding="utf-8")
    (old_root / "normalized" / "main.md").write_text("readable\n", encoding="utf-8")
    Path(old.value.manifest_path).write_text(json.dumps({"normalized_entry": "normalized/main.md"}), encoding="utf-8")

    old_checked = service.check_resource_draft(tmp_path, draft_id=old.value.draft.draft_id)

    binary = service.allocate_resource_draft(tmp_path, target="https://example.com/binary")
    assert binary.ok and binary.value is not None
    binary_root = Path(binary.value.draft_root)
    (binary_root / "README.md").write_text("# Binary\n", encoding="utf-8")
    (binary_root / "normalized" / "paper.txt").write_bytes(b"%PDF-1.4\nfixture")
    binary_checked = service.check_resource_draft(tmp_path, draft_id=binary.value.draft.draft_id)

    assert old_checked.ok and old_checked.value is not None and not old_checked.value.passed
    assert any(issue.kind == "schema_version_missing" for issue in old_checked.value.issues)
    assert binary_checked.ok and binary_checked.value is not None and not binary_checked.value.passed
    assert "resource_draft_normalized_artifact_missing" in {
        issue.kind for issue in binary_checked.value.issues
    }


def test_resource_manifest_records_file_truth_and_matches_final_metadata(tmp_path: Path) -> None:
    service = make_runtime().material
    draft = service.allocate_resource_draft(tmp_path, target="https://example.com/truth")
    assert draft.ok and draft.value is not None
    _write_valid_draft_files(Path(draft.value.draft_root), text="canonical bytes\n")

    refreshed = service.refresh_resource_draft_manifest(tmp_path, draft_id=draft.value.draft.draft_id)
    finalized = service.finalize_resource_draft(
        tmp_path,
        draft_id=draft.value.draft.draft_id,
        summary="Manifest truth.",
    )

    assert refreshed.ok and refreshed.value is not None
    assert {item.path for item in refreshed.value.files} == {
        "README.md",
        "normalized/main.md",
        "original/raw.txt",
    }
    assert finalized.ok and finalized.value is not None
    manifest_loaded = make_runtime().foundation.store.read_json(
        Path(finalized.value.resource_root) / "manifest.json", ResourceMaterialManifest
    )
    assert manifest_loaded.ok and manifest_loaded.value is not None
    canonical = next(
        item
        for item in manifest_loaded.value.files
        if item.path == manifest_loaded.value.canonical_normalized_entry
    )
    assert finalized.value.resource.normalized_entry == manifest_loaded.value.canonical_normalized_entry
    assert finalized.value.resource.content_hash == canonical.sha256
