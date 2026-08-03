from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.material import ResourceMetadataInput


@pytest.mark.real
def test_material_resource_library_curation_real_local_file(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    local_source = tmp_path / "local_resource.md"
    local_source.write_text(
        "\n".join(
            [
                "# Local resource",
                "This resource states a useful auxiliary theorem.",
                "The theorem is used by a later node.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    service = make_runtime().material

    prepared_target = service.prepare_resource_target(
        target_kind="local_file",
        target=str(local_source),
    )
    assert prepared_target.ok
    assert prepared_target.value is not None
    assert prepared_target.value.kind == "local_file"

    duplicate_before = service.find_duplicate_resource(repo_root, target=prepared_target.value)
    assert duplicate_before.ok
    assert duplicate_before.value is not None
    assert duplicate_before.value.duplicate is False

    decision = service.resource_curation.decide_local_or_external(
        target=prepared_target.value,
        duplicate=duplicate_before.value,
        repo_root=repo_root,
    )
    assert decision.ok
    assert decision.value is not None
    assert decision.value.decision == "local_resource"

    curated = service.resource_curation.curate_local_resource(
        repo_root,
        target=prepared_target.value,
        temp_root=tmp_path / "curation-work",
        metadata=ResourceMetadataInput(
            title="Local resource fixture",
            source_url=str(local_source),
            notes="Real service local curation fixture.",
        ),
    )
    assert curated.ok
    assert curated.value is not None
    resource_key = curated.value.resource.resource_key
    assert (repo_root / ".lean_constellation" / "resources" / "items" / resource_key / "resource.json").exists()

    curator_result = service.resource_curation.build_curator_result(
        decision.value,
        resource=curated.value,
        classification_reason="The fixture is supporting material for this repository.",
        resource_role="Provide deterministic ResourceLibrary coverage.",
        consumer_formalization_scope="The current repository retains all formal theorem ownership.",
    )
    assert curator_result.ok
    assert curator_result.value is not None
    assert curator_result.value.kind == "local_resource_created"
    assert curator_result.value.resource_key == resource_key

    loaded = service.resource_library.get_resource(repo_root, resource_key=resource_key)
    listed = service.resource_library.list_resources(repo_root, query="fixture")
    duplicate_after = service.find_duplicate_resource(repo_root, target=prepared_target.value)
    preview = service.resource_library.preview_resource(repo_root, resource_key=resource_key)
    structured_preview = service.preview_resource_ref(repo_root, resource_key=resource_key, start_line=2, end_line=3)
    read = service.read_resource_range(repo_root, resource_key=resource_key, start_line=2, end_line=3, context_lines=1)
    search = service.search_material_text(repo_root, query="auxiliary theorem", scope="resource")
    valid = service.validate_resource_range(repo_root, resource_key=resource_key, start_line=2, end_line=3)
    invalid = service.validate_resource_range(repo_root, resource_key=resource_key, start_line=99, end_line=100)

    assert loaded.ok and loaded.value is not None
    assert loaded.value.resource.title == "Local resource fixture"
    assert listed.ok and listed.value is not None
    assert [item.resource_key for item in listed.value] == [resource_key]
    assert duplicate_after.ok and duplicate_after.value is not None
    assert duplicate_after.value.duplicate is True
    assert duplicate_after.value.resource_key == resource_key
    assert preview.ok and preview.value is not None
    assert "2: This resource states a useful auxiliary theorem." in preview.value.text_with_line_numbers
    assert structured_preview.ok and structured_preview.value is not None
    assert structured_preview.value.resource_key == resource_key
    assert "3: The theorem is used by a later node." in structured_preview.value.preview.text_with_line_numbers
    assert read.ok and read.value is not None
    assert "3: The theorem is used by a later node." in read.value.text_with_line_numbers
    assert search.ok and search.value is not None
    assert len(search.value.hits) == 1
    assert search.value.hits[0].material_kind == "resource"
    assert search.value.hits[0].reusable_ref_fields["resource_key"] == resource_key
    assert valid.ok and valid.value is not None and valid.value["valid"] is True
    assert invalid.ok and invalid.value is not None
    assert invalid.value["valid"] is False
    assert invalid.value["issue_code"] == "resource_ref_range_invalid"


@pytest.mark.real
def test_material_resource_curation_real_external_repo_and_rejection_decisions(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    local_dir = tmp_path / "large-resource-dir"
    local_dir.mkdir()
    (local_dir / "README.md").write_text("Directory-shaped resource.\n", encoding="utf-8")
    service = make_runtime().material

    dir_target = service.normalize_resource_target(str(local_dir))
    assert dir_target.ok
    assert dir_target.value is not None

    external_decision = service.resource_curation.decide_local_or_external(
        target=dir_target.value,
        duplicate=None,
        repo_root=repo_root,
    )
    assert external_decision.ok
    assert external_decision.value is not None
    assert external_decision.value.decision == "external_repo_required"

    external_result = service.resource_curation.build_curator_result(
        external_decision.value,
        classification_reason="The directory fixture represents an independent provider boundary.",
        relation_to_current_repo_or_node="The current repository consumes the provider output.",
        consumer_need="A stable reusable interface backed by the external directory.",
        provider_scope="Own the independent formalization represented by the directory.",
    )
    assert external_result.ok
    assert external_result.value is not None
    assert external_result.value.kind == "external_repo_required"
    assert external_result.value.reason == external_decision.value.reason
