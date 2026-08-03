from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.material import ResourceDraftStatus


@pytest.mark.real
def test_material_resource_draft_curation_real_lifecycle(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    local_source = tmp_path / "draft-source.md"
    local_source.write_text(
        "\n".join(
            [
                "# Draft source",
                "A normalized resource can carry an auxiliary theorem.",
                "The theorem is precise enough to cite from a later node.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    service = make_runtime().material

    draft = service.allocate_resource_draft(
        repo_root,
        target=str(local_source),
        resource_kind="local_file",
        title_hint="Draft curation fixture",
    )
    assert draft.ok and draft.value is not None
    draft_root = Path(draft.value.draft_root)
    assert draft.value.draft.status == ResourceDraftStatus.ALLOCATED

    (draft_root / "README.md").write_text(
        "# Draft curation fixture\n\nCurated from a local markdown source.\n",
        encoding="utf-8",
    )
    (draft_root / "original" / "draft-source.md").write_text(local_source.read_text(encoding="utf-8"), encoding="utf-8")
    (draft_root / "normalized" / "main.md").write_text(local_source.read_text(encoding="utf-8"), encoding="utf-8")

    check = service.check_resource_draft(repo_root, draft_id=draft.value.draft.draft_id)
    finalized = service.finalize_resource_draft(
        repo_root,
        draft_id=draft.value.draft.draft_id,
        summary="Finalized draft curation fixture.",
    )

    assert check.ok and check.value is not None and check.value.passed
    assert finalized.ok and finalized.value is not None
    resource_key = finalized.value.resource.resource_key
    assert finalized.value.resource.title == "Draft curation fixture"
    assert finalized.value.resource.normalized_entry == "normalized/main.md"

    reloaded = make_runtime().material
    loaded = reloaded.resource_library.get_resource(repo_root, resource_key=resource_key)
    listed = reloaded.resource_library.list_resources(repo_root, query="curation fixture")
    preview = reloaded.preview_resource_ref(repo_root, resource_key=resource_key, start_line=2, end_line=3)
    read = reloaded.read_resource_range(repo_root, resource_key=resource_key, start_line=2, end_line=3, context_lines=0)
    search = reloaded.search_material_text(repo_root, query="auxiliary theorem", scope="resource")
    valid = reloaded.validate_resource_range(repo_root, resource_key=resource_key, start_line=2, end_line=3)

    assert loaded.ok and loaded.value is not None
    assert loaded.value.resource.resource_key == resource_key
    assert listed.ok and listed.value is not None
    assert [item.resource_key for item in listed.value] == [resource_key]
    assert preview.ok and preview.value is not None
    assert "2: A normalized resource can carry an auxiliary theorem." in preview.value.preview.text_with_line_numbers
    assert read.ok and read.value is not None
    assert "3: The theorem is precise enough to cite from a later node." in read.value.text_with_line_numbers
    assert search.ok and search.value is not None
    assert len(search.value.hits) == 1
    assert search.value.hits[0].reusable_ref_fields["resource_key"] == resource_key
    assert valid.ok and valid.value is not None and valid.value["valid"] is True


@pytest.mark.real
def test_material_resource_curation_submit_outcomes_real_local_and_duplicate(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    local_source = tmp_path / "outcome-source.md"
    local_source.write_text("outcome resource\nwith theorem text\n", encoding="utf-8")
    service = make_runtime().material

    prepared_target = service.prepare_resource_target(
        target_kind="local_file",
        target=str(local_source),
    )
    assert prepared_target.ok and prepared_target.value is not None

    draft = service.allocate_resource_draft(
        repo_root,
        target=prepared_target.value,
        resource_kind="local_file",
        title_hint="Outcome resource",
    )
    assert draft.ok and draft.value is not None
    draft_root = Path(draft.value.draft_root)
    (draft_root / "README.md").write_text("# Outcome resource\n\nCreated through submit gate.\n", encoding="utf-8")
    (draft_root / "original" / "outcome-source.md").write_text(local_source.read_text(encoding="utf-8"), encoding="utf-8")
    (draft_root / "normalized" / "main.md").write_text(local_source.read_text(encoding="utf-8"), encoding="utf-8")

    local_result = service.submit_local_resource_created(
        repo_root,
        target=prepared_target.value,
        draft_id=draft.value.draft.draft_id,
        summary="Created through ResourceCurator local submit gate.",
        classification_reason="The local fixture is supporting material for this repository.",
        resource_role="Provide deterministic real material-service coverage.",
        consumer_formalization_scope="The current repository retains all formal theorem ownership.",
    )
    assert local_result.ok and local_result.value is not None
    assert local_result.value.kind == "local_resource_created"
    assert local_result.value.resource_key is not None

    duplicate_result = service.submit_resource_duplicate(
        repo_root,
        target=prepared_target.value,
        existing_kind="resource",
        existing_resource_key=local_result.value.resource_key,
        duplicate_reason="The same local file target has already been curated.",
    )
    assert duplicate_result.ok and duplicate_result.value is not None
    assert duplicate_result.value.kind == "duplicate"
    assert duplicate_result.value.duplicate_resource_key == local_result.value.resource_key
