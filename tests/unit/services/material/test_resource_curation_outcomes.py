from __future__ import annotations

from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.material import ResourceDraftStatus, ResourceMetadataInput


def _write_draft_files(draft_root: Path) -> None:
    (draft_root / "README.md").write_text("# Curated resource\n\nReadable resource.", encoding="utf-8")
    (draft_root / "original" / "raw.txt").write_text("raw\n", encoding="utf-8")
    (draft_root / "normalized" / "main.md").write_text("line one\nline two theorem\n", encoding="utf-8")


def _resource_temp(root: Path) -> Path:
    temp = root / "registered-resource"
    (temp / "normalized").mkdir(parents=True)
    (temp / "normalized" / "main.md").write_text("registered duplicate\n", encoding="utf-8")
    return temp


def test_resource_curation_submit_outcome_happy_paths(tmp_path: Path) -> None:
    service = make_runtime().material
    target = service.resource_curation.prepare_resource_target(target_kind="web", target="https://example.com/resource")
    assert target.ok and target.value is not None

    registered = service.register_local_resource(
        tmp_path,
        target=target.value,
        temp_dir=_resource_temp(tmp_path),
        metadata=ResourceMetadataInput(title="Existing resource"),
    )
    assert registered.ok and registered.value is not None
    duplicate_resource = service.resource_curation.submit_resource_duplicate(
        tmp_path,
        target=target.value,
        existing_kind="resource",
        existing_resource_key=registered.value.resource.resource_key,
        duplicate_reason="The requested URL is already curated.",
    )
    assert duplicate_resource.ok and duplicate_resource.value is not None
    assert duplicate_resource.value.kind == "duplicate"
    assert duplicate_resource.value.duplicate_resource_key == registered.value.resource.resource_key

    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "source_duplicate.md").write_text("source duplicate\n", encoding="utf-8")
    source_duplicate = service.resource_curation.submit_resource_duplicate(
        tmp_path,
        target=target.value,
        existing_kind="source",
        existing_source_path="source_duplicate.md",
        duplicate_reason="The source corpus already contains equivalent material.",
    )
    assert source_duplicate.ok and source_duplicate.value is not None
    assert source_duplicate.value.kind == "duplicate"
    assert source_duplicate.value.duplicate_source_paths == ["source_duplicate.md"]

    local_file = tmp_path / "local.md"
    local_file.write_text("local resource\n", encoding="utf-8")
    local_target = service.resource_curation.prepare_resource_target(target_kind="local_file", target=str(local_file))
    assert local_target.ok and local_target.value is not None
    draft = service.allocate_resource_draft(tmp_path, target=local_target.value, title_hint="Local resource")
    assert draft.ok and draft.value is not None
    _write_draft_files(Path(draft.value.draft_root))
    local = service.resource_curation.submit_local_resource_created(
        tmp_path,
        target=local_target.value,
        draft_id=draft.value.draft.draft_id,
        summary="Created a curated local resource.",
    )
    assert local.ok and local.value is not None
    assert local.value.kind == "local_resource_created"
    assert local.value.resource_key is not None
    reloaded_draft = service.get_resource_draft(tmp_path, draft_id=draft.value.draft.draft_id)
    assert reloaded_draft.ok and reloaded_draft.value is not None
    assert reloaded_draft.value.draft.status == ResourceDraftStatus.FINALIZED

    external = service.resource_curation.submit_external_repo_required(
        tmp_path,
        target=target.value,
        reason="The target is a full upstream project rather than a small resource.",
        source_description="A web-accessible upstream project with many Lean files.",
        suggested_repo_name="project_resource",
        required_interfaces_hint="Expose the main fixed point theorem.",
    )
    assert external.ok and external.value is not None
    assert external.value.kind == "external_repo_required"
    assert external.value.suggested_repo_name == "project_resource"
    assert external.value.source_description == "A web-accessible upstream project with many Lean files."

    rejected = service.resource_curation.submit_resource_rejected(
        tmp_path,
        target=target.value,
        reason="The target is unrelated.",
    )
    assert rejected.ok and rejected.value is not None
    assert rejected.value.kind == "rejected"


def test_resource_curation_submit_outcome_gates(tmp_path: Path) -> None:
    service = make_runtime().material
    first_target = service.resource_curation.prepare_resource_target(target_kind="web", target="https://example.com/first")
    second_target = service.resource_curation.prepare_resource_target(target_kind="web", target="https://example.com/second")
    assert first_target.ok and first_target.value is not None
    assert second_target.ok and second_target.value is not None

    missing_duplicate_reason = service.resource_curation.submit_resource_duplicate(
        tmp_path,
        target=first_target.value,
        existing_kind="resource",
        existing_resource_key="missing",
        duplicate_reason=" ",
    )
    missing_duplicate_key = service.resource_curation.submit_resource_duplicate(
        tmp_path,
        target=first_target.value,
        existing_kind="resource",
        duplicate_reason="Duplicate.",
    )
    missing_source_path = service.resource_curation.submit_resource_duplicate(
        tmp_path,
        target=first_target.value,
        existing_kind="source",
        existing_source_path="missing.md",
        duplicate_reason="Duplicate.",
    )

    assert not missing_duplicate_reason.ok
    assert missing_duplicate_reason.issues[0].kind == "resource_duplicate_reason_required"
    assert not missing_duplicate_key.ok
    assert missing_duplicate_key.issues[0].kind == "resource_duplicate_key_required"
    assert not missing_source_path.ok
    assert missing_source_path.issues[0].kind == "source_corpus_missing"

    draft = service.allocate_resource_draft(tmp_path, target=first_target.value)
    assert draft.ok and draft.value is not None
    not_ready = service.resource_curation.submit_local_resource_created(
        tmp_path,
        target=first_target.value,
        draft_id=draft.value.draft.draft_id,
        summary="Try too early.",
    )
    assert not not_ready.ok
    assert {issue.kind for issue in not_ready.issues} >= {
        "resource_draft_readme_or_manifest_missing",
        "resource_draft_normalized_artifact_missing",
    }

    _write_draft_files(Path(draft.value.draft_root))
    mismatch = service.resource_curation.submit_local_resource_created(
        tmp_path,
        target=second_target.value,
        draft_id=draft.value.draft.draft_id,
        summary="Wrong request.",
    )
    assert not mismatch.ok
    assert mismatch.issues[0].kind == "resource_request_target_mismatch"

    missing_external_reason = service.resource_curation.submit_external_repo_required(
        tmp_path,
        target=first_target.value,
        reason=" ",
        source_description="A project.",
    )
    missing_external_description = service.resource_curation.submit_external_repo_required(
        tmp_path,
        target=first_target.value,
        reason="Needs provider repo.",
        source_description=" ",
    )
    invalid_repo_hint = service.resource_curation.submit_external_repo_required(
        tmp_path,
        target=first_target.value,
        reason="Needs provider repo.",
        source_description="A project.",
        suggested_repo_name="../bad",
    )
    missing_rejected_reason = service.resource_curation.submit_resource_rejected(
        tmp_path,
        target=first_target.value,
        reason=" ",
    )

    assert not missing_external_reason.ok
    assert missing_external_reason.issues[0].kind == "resource_external_reason_required"
    assert not missing_external_description.ok
    assert missing_external_description.issues[0].kind == "resource_external_source_description_required"
    assert not invalid_repo_hint.ok
    assert invalid_repo_hint.issues[0].kind == "invalid_suggested_repo_name"
    assert not missing_rejected_reason.ok
    assert missing_rejected_reason.issues[0].kind == "resource_rejected_reason_required"
