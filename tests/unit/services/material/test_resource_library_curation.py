from tests.unit_services_helpers import make_runtime

from pathlib import Path

from lean_constellation.services.material import ResourceMetadataInput, ResourceTargetView
from lean_constellation.services.material.resource_curation import ResourceArtifactView


def _resource_temp(root: Path, text: str = "first\nsecond theorem\nthird\n") -> Path:
    temp = root / "resource_tmp"
    if temp.exists():
        suffix = 1
        while (root / f"resource_tmp_{suffix}").exists():
            suffix += 1
        temp = root / f"resource_tmp_{suffix}"
    (temp / "original").mkdir(parents=True)
    (temp / "normalized").mkdir()
    (temp / "original" / "page.html").write_text("<p>raw</p>", encoding="utf-8")
    (temp / "normalized" / "page.md").write_text(text, encoding="utf-8")
    return temp


def test_resource_library_register_duplicate_preview_and_validate(tmp_path: Path) -> None:
    service = make_runtime().material
    target = service.normalize_resource_target("https://Example.com/math/page/")
    assert target.ok
    assert target.value is not None
    assert target.value.canonical_locator == "https://example.com/math/page"

    temp = _resource_temp(tmp_path)

    registered = service.register_local_resource(
        tmp_path,
        target=target.value,
        temp_dir=temp,
        metadata=ResourceMetadataInput(title="Example page", source_url="https://example.com/math/page"),
    )
    assert registered.ok
    assert registered.value is not None
    resource_key = registered.value.resource.resource_key
    assert (tmp_path / ".lean_constellation" / "resources" / "items" / resource_key / "resource.json").exists()

    duplicate = service.find_duplicate_resource(tmp_path, target=target.value)
    assert duplicate.ok
    assert duplicate.value is not None
    assert duplicate.value.duplicate
    assert duplicate.value.resource_key == resource_key

    preview = service.resource_library.preview_resource(tmp_path, resource_key=resource_key)
    assert preview.ok
    assert preview.value is not None
    assert "2: second theorem" in preview.value.text_with_line_numbers

    valid = service.resource_library.validate_resource_ref(
        tmp_path,
        resource_key=resource_key,
        start_line=2,
        end_line=3,
    )
    assert valid.ok
    assert valid.value is not None
    assert valid.value["valid"] is True

    search = service.search_material_text(tmp_path, query="theorem", scope="resource")
    assert search.ok
    assert search.value is not None
    assert search.value.hits[0].reusable_ref_fields["resource_key"] == resource_key


def test_resource_target_normalization_arxiv_url_and_local(tmp_path: Path) -> None:
    service = make_runtime().material
    local_file = tmp_path / "note.txt"
    local_file.write_text("note\n", encoding="utf-8")

    arxiv = service.normalize_resource_target("2401.00001v2")
    url = service.normalize_resource_target("https://Example.com/math/page/")
    local = service.normalize_resource_target(str(local_file))
    invalid = service.normalize_resource_target(" ")

    assert arxiv.ok and arxiv.value is not None
    assert arxiv.value.kind == "arxiv"
    assert arxiv.value.canonical_locator == "arxiv:2401.00001v2"
    assert url.ok and url.value is not None
    assert url.value.canonical_locator == "https://example.com/math/page"
    assert local.ok and local.value is not None
    assert local.value.kind == "local_file"
    assert local.value.canonical_locator.startswith("local_file:")
    assert not invalid.ok
    assert invalid.issues[0].kind == "invalid_resource_target"


def test_resource_duplicate_uses_canonical_locator_and_metadata_source_url(tmp_path: Path) -> None:
    service = make_runtime().material
    local_file = tmp_path / "downloaded.md"
    local_file.write_text("downloaded\n", encoding="utf-8")
    local_target = service.normalize_resource_target(str(local_file))
    assert local_target.ok and local_target.value is not None
    registered = service.register_local_resource(
        tmp_path,
        target=local_target.value,
        temp_dir=_resource_temp(tmp_path),
        metadata=ResourceMetadataInput(title="Downloaded copy", source_url="https://example.com/source/"),
    )
    assert registered.ok and registered.value is not None

    same_local = service.find_duplicate_resource(tmp_path, target=local_target.value)
    web_target = service.normalize_resource_target("https://example.com/source")
    assert web_target.ok and web_target.value is not None
    metadata_duplicate = service.find_duplicate_resource(tmp_path, target=web_target.value)
    absent = service.normalize_resource_target("https://example.com/other")
    assert absent.ok and absent.value is not None
    no_duplicate = service.find_duplicate_resource(tmp_path, target=absent.value)

    assert same_local.ok and same_local.value is not None
    assert same_local.value.duplicate
    assert metadata_duplicate.ok and metadata_duplicate.value is not None
    assert metadata_duplicate.value.duplicate
    assert metadata_duplicate.value.resource_key == registered.value.resource.resource_key
    assert no_duplicate.ok and no_duplicate.value is not None
    assert not no_duplicate.value.duplicate


def test_resource_register_get_list_preview_and_validation_failures(tmp_path: Path) -> None:
    service = make_runtime().material
    target = service.normalize_resource_target("https://example.com/a")
    assert target.ok and target.value is not None

    unreadable_temp = tmp_path / "unreadable"
    (unreadable_temp / "normalized").mkdir(parents=True)
    (unreadable_temp / "normalized" / "empty.txt").write_text("", encoding="utf-8")
    unreadable = service.register_local_resource(
        tmp_path,
        target=target.value,
        temp_dir=unreadable_temp,
        metadata=ResourceMetadataInput(title="Empty"),
    )
    assert not unreadable.ok
    assert unreadable.issues[0].kind == "resource_not_readable"

    registered = service.register_local_resource(
        tmp_path,
        target=target.value,
        temp_dir=_resource_temp(tmp_path, text="alpha\nbeta\n"),
        metadata=ResourceMetadataInput(title="Alpha resource", notes="searchable note"),
    )
    assert registered.ok and registered.value is not None
    duplicate_register = service.register_local_resource(
        tmp_path,
        target=target.value,
        temp_dir=_resource_temp(tmp_path, text="other\n"),
        metadata=ResourceMetadataInput(title="Duplicate"),
    )
    loaded = service.resource_library.get_resource(tmp_path, resource_key=registered.value.resource.resource_key)
    missing = service.resource_library.get_resource(tmp_path, resource_key="missing")
    invalid_key = service.resource_library.get_resource(tmp_path, resource_key="../bad")
    query_hit = service.resource_library.list_resources(tmp_path, query="searchable")
    query_miss = service.resource_library.list_resources(tmp_path, query="absent")
    preview_missing = service.resource_library.preview_resource(tmp_path, resource_key="missing")
    valid_ref = service.resource_library.validate_resource_ref(
        tmp_path,
        resource_key=registered.value.resource.resource_key,
        start_line=1,
        end_line=2,
    )
    invalid_ref = service.resource_library.validate_resource_ref(
        tmp_path,
        resource_key=registered.value.resource.resource_key,
        start_line=99,
        end_line=100,
    )

    assert not duplicate_register.ok
    assert duplicate_register.issues[0].kind == "resource_duplicate"
    assert loaded.ok and loaded.value is not None
    assert loaded.value.resource.title == "Alpha resource"
    assert not missing.ok
    assert missing.issues[0].kind == "resource_not_found"
    assert not invalid_key.ok
    assert invalid_key.issues[0].kind == "invalid_resource_key"
    assert query_hit.ok and query_hit.value is not None
    assert [item.resource_key for item in query_hit.value] == [registered.value.resource.resource_key]
    assert query_miss.ok and query_miss.value == []
    assert not preview_missing.ok
    assert preview_missing.issues[0].kind == "resource_not_found"
    assert valid_ref.ok and valid_ref.value is not None
    assert valid_ref.value["valid"] is True
    assert invalid_ref.ok and invalid_ref.value is not None
    assert invalid_ref.value["valid"] is False
    assert invalid_ref.value["issue_code"] == "resource_ref_range_invalid"


def test_resource_curation_local_file_and_external_decisions(tmp_path: Path) -> None:
    service = make_runtime().material
    local_file = tmp_path / "note.txt"
    local_file.write_text("important resource\n", encoding="utf-8")
    target = service.resource_curation.prepare_resource_target(
        target_kind="local_file",
        target=str(local_file),
    )
    assert target.ok
    assert target.value is not None
    assert target.value.kind == "local_file"

    duplicate = service.find_duplicate_resource(tmp_path, target=target.value)
    assert duplicate.ok
    assert duplicate.value is not None
    decision = service.resource_curation.decide_local_or_external(
        target=target.value,
        duplicate=duplicate.value,
        repo_root=tmp_path,
    )
    assert decision.ok
    assert decision.value is not None
    assert decision.value.decision == "local_resource"

    curated = service.resource_curation.curate_local_resource(
        tmp_path,
        target=target.value,
        temp_root=tmp_path / "curated",
    )
    assert curated.ok
    assert curated.value is not None
    result = service.resource_curation.build_curator_result(decision.value, resource=curated.value)
    assert result.ok
    assert result.value is not None
    assert result.value.kind == "local_resource_created"
    assert result.value.resource_key == curated.value.resource.resource_key

    dir_target = service.normalize_resource_target(str(tmp_path))
    assert dir_target.ok
    assert dir_target.value is not None
    dir_decision = service.resource_curation.decide_local_or_external(
        target=dir_target.value,
        duplicate=None,
        repo_root=tmp_path,
    )
    assert dir_decision.ok
    assert dir_decision.value is not None
    assert dir_decision.value.decision == "external_repo_required"


def test_resource_curation_target_validation_is_context_free(tmp_path: Path) -> None:
    service = make_runtime().material
    local_file = tmp_path / "note.txt"
    local_file.write_text("note\n", encoding="utf-8")

    target = service.resource_curation.prepare_resource_target(
        target_kind="local_file",
        target=str(local_file),
    )
    missing_target = service.resource_curation.prepare_resource_target(target_kind="local_file", target=" ")
    invalid_kind = service.resource_curation.prepare_resource_target(target_kind="ftp", target="x")  # type: ignore[arg-type]

    assert target.ok and target.value is not None
    assert target.value.kind == "local_file"
    assert target.value.target == str(local_file)
    assert not missing_target.ok
    assert missing_target.issues[0].kind == "missing_resource_target"
    assert not invalid_kind.ok
    assert invalid_kind.issues[0].kind == "invalid_resource_target_kind"


def test_resource_curation_acquire_extract_and_failure_branches(tmp_path: Path) -> None:
    service = make_runtime().material
    local_file = tmp_path / "note.txt"
    local_file.write_text("curated text\n", encoding="utf-8")
    target = service.normalize_resource_target(str(local_file))
    assert target.ok and target.value is not None

    artifact = service.resource_curation.acquire_material_artifact(target.value, temp_root=tmp_path / "curation")
    extracted = service.resource_curation.extract_readable_material(artifact.value, temp_root=tmp_path / "curation") if artifact.value else None

    missing_target = service.normalize_resource_target(str(tmp_path / "missing.txt"))
    assert missing_target.ok and missing_target.value is not None
    missing = service.resource_curation.acquire_material_artifact(missing_target.value, temp_root=tmp_path / "missing-curation")
    invalid_kind = service.resource_curation.acquire_material_artifact(
        ResourceTargetView(kind="unknown", target="x", canonical_locator="unknown:x", summary="bad"),
        temp_root=tmp_path / "invalid",
    )
    missing_primary = service.resource_curation.extract_readable_material(
        ResourceArtifactView(
            ok=True,
            target=target.value,
            artifact_paths=[],
            primary_artifact_path=None,
            summary="No primary artifact.",
        ),
        temp_root=tmp_path / "missing-primary",
    )

    assert artifact.ok and artifact.value is not None
    assert artifact.value.primary_artifact_path is not None
    assert extracted is not None and extracted.ok and extracted.value is not None
    assert extracted.value.primary_text_path is not None
    assert not missing.ok
    assert missing.issues[0].kind == "missing_local_file"
    assert not invalid_kind.ok
    assert invalid_kind.issues[0].kind == "invalid_resource_target_kind"
    assert not missing_primary.ok
    assert missing_primary.issues[0].kind == "resource_artifact_missing"


def test_resource_curation_decision_duplicate_source_duplicate_and_rejected(tmp_path: Path) -> None:
    service = make_runtime().material
    target = service.normalize_resource_target("https://example.com/dup")
    assert target.ok and target.value is not None
    registered = service.register_local_resource(
        tmp_path,
        target=target.value,
        temp_dir=_resource_temp(tmp_path),
        metadata=ResourceMetadataInput(title="Duplicate"),
    )
    assert registered.ok and registered.value is not None
    duplicate = service.find_duplicate_resource(tmp_path, target=target.value)
    resource_duplicate_decision = service.resource_curation.decide_local_or_external(
        target=target.value,
        duplicate=duplicate.value,
        repo_root=tmp_path,
    )
    resource_duplicate_result = service.resource_curation.build_curator_result(resource_duplicate_decision.value)

    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "arxiv:2401.00001.md").write_text("already in source corpus\n", encoding="utf-8")
    arxiv_target = service.normalize_resource_target("2401.00001")
    assert arxiv_target.ok and arxiv_target.value is not None
    source_duplicate_decision = service.resource_curation.decide_local_or_external(
        target=arxiv_target.value,
        duplicate=None,
        repo_root=tmp_path,
    )

    rejected = service.resource_curation.decide_local_or_external(
        target=ResourceTargetView(kind="unknown", target="x", canonical_locator="unknown:x", summary="bad"),
        duplicate=None,
    )
    external = service.resource_curation.decide_local_or_external(
        target=arxiv_target.value,
        duplicate=None,
        prefer_external_repo=True,
    )

    assert resource_duplicate_decision.ok and resource_duplicate_decision.value is not None
    assert resource_duplicate_decision.value.decision == "duplicate"
    assert resource_duplicate_decision.value.duplicate_resource_key == registered.value.resource.resource_key
    assert resource_duplicate_result.ok and resource_duplicate_result.value is not None
    assert resource_duplicate_result.value.kind == "duplicate"
    assert source_duplicate_decision.ok and source_duplicate_decision.value is not None
    assert source_duplicate_decision.value.decision == "duplicate"
    assert source_duplicate_decision.value.duplicate_source_paths == ["arxiv:2401.00001.md"]
    assert rejected.ok and rejected.value is not None
    assert rejected.value.decision == "rejected"
    assert external.ok and external.value is not None
    assert external.value.decision == "external_repo_required"


def test_resource_curation_result_branches_and_curate_failure(tmp_path: Path) -> None:
    service = make_runtime().material
    target = service.normalize_resource_target("https://example.com/result")
    assert target.ok and target.value is not None

    duplicate_decision = service.resource_curation.decide_local_or_external(
        target=target.value,
        duplicate=service.resource_library.find_duplicate_resource(tmp_path, target=target.value).value,
    )
    assert duplicate_decision.ok and duplicate_decision.value is not None
    local_missing_resource = service.resource_curation.build_curator_result(duplicate_decision.value)

    rejected_decision = service.resource_curation.decide_local_or_external(
        target=ResourceTargetView(kind="unknown", target="x", canonical_locator="unknown:x", summary="bad"),
        duplicate=None,
    )
    assert rejected_decision.ok and rejected_decision.value is not None
    rejected_result = service.resource_curation.build_curator_result(rejected_decision.value)

    dir_target = service.normalize_resource_target(str(tmp_path))
    assert dir_target.ok and dir_target.value is not None
    external_decision = service.resource_curation.decide_local_or_external(target=dir_target.value, duplicate=None)
    assert external_decision.ok and external_decision.value is not None
    external_result = service.resource_curation.build_curator_result(external_decision.value)

    duplicate_view = service.resource_library.find_duplicate_resource(tmp_path, target=target.value)
    assert duplicate_view.ok and duplicate_view.value is not None
    duplicate_decision_direct = service.resource_curation.decide_local_or_external(
        target=target.value,
        duplicate=duplicate_view.value,
    )
    assert duplicate_decision_direct.ok and duplicate_decision_direct.value is not None

    missing_local = service.normalize_resource_target(str(tmp_path / "missing.txt"))
    assert missing_local.ok and missing_local.value is not None
    curate_failure = service.resource_curation.curate_local_resource(
        tmp_path,
        target=missing_local.value,
        temp_root=tmp_path / "missing-curation",
    )

    assert not local_missing_resource.ok
    assert local_missing_resource.issues[0].kind == "resource_required"
    assert rejected_result.ok and rejected_result.value is not None
    assert rejected_result.value.kind == "rejected"
    assert external_result.ok and external_result.value is not None
    assert external_result.value.kind == "external_repo_required"
    assert duplicate_decision_direct.value.decision == "local_resource"
    assert not curate_failure.ok
    assert curate_failure.issues[0].kind == "missing_local_file"
