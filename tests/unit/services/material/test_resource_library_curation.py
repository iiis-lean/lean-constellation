from tests.unit_services_helpers import make_runtime

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from lean_constellation.services.external_clients import AcquiredArtifactResult, MaterialTarget
from lean_constellation.services.material import ResourceMetadataInput, ResourceTargetView
from lean_constellation.services.material.resource_curation import ResourceArtifactView


def _resource_temp(root: Path, text: str = "first\nsecond theorem\nthird\n") -> Path:
    temp = root / "resource_tmp"
    if temp.exists():
        suffix = 1
        while (root / f"resource_tmp_{suffix}").exists():
            suffix += 1
        temp = root / f"resource_tmp_{suffix}"
    (temp / "_work" / "original").mkdir(parents=True)
    (temp / "article").mkdir()
    (temp / "_work" / "original" / "page.html").write_text("<p>raw</p>", encoding="utf-8")
    (temp / "article" / "page.md").write_text(text, encoding="utf-8")
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


def test_finalize_equivalent_resource_drafts_is_idempotent(tmp_path: Path) -> None:
    service = make_runtime().material
    target = service.normalize_resource_target("https://example.com/same-work")
    assert target.ok and target.value is not None

    drafts = []
    for _ in range(2):
        allocated = service.resource_library.allocate_resource_draft(
            tmp_path,
            target=target.value,
            allow_duplicate=True,
        )
        assert allocated.ok and allocated.value is not None
        draft_root = Path(allocated.value.draft_root)
        (draft_root / "README.md").write_text("# Same work\n", encoding="utf-8")
        (draft_root / "article.md").write_text("same exact source\n", encoding="utf-8")
        checked = service.resource_library.check_resource_draft(
            tmp_path,
            draft_id=allocated.value.draft.draft_id,
        )
        assert checked.ok and checked.value is not None and checked.value.passed
        drafts.append(allocated.value.draft.draft_id)

    first = service.resource_library.finalize_resource_draft(
        tmp_path,
        draft_id=drafts[0],
        summary="Same work.",
    )
    second = service.resource_library.finalize_resource_draft(
        tmp_path,
        draft_id=drafts[1],
        summary="Same work.",
    )

    assert first.ok and first.value is not None
    assert second.ok and second.value is not None
    assert second.value.resource.resource_key == first.value.resource.resource_key


def test_finalize_same_resource_identity_rejects_different_canonical_content(tmp_path: Path) -> None:
    service = make_runtime().material
    target = service.normalize_resource_target("https://example.com/conflicting-work")
    assert target.ok and target.value is not None
    draft_ids = []
    for body in ("first canonical source\n", "different canonical source\n"):
        allocated = service.resource_library.allocate_resource_draft(
            tmp_path,
            target=target.value,
            allow_duplicate=True,
        )
        assert allocated.ok and allocated.value is not None
        draft_root = Path(allocated.value.draft_root)
        (draft_root / "README.md").write_text("# Conflicting work\n", encoding="utf-8")
        (draft_root / "article.md").write_text(body, encoding="utf-8")
        checked = service.resource_library.check_resource_draft(
            tmp_path,
            draft_id=allocated.value.draft.draft_id,
        )
        assert checked.ok and checked.value is not None and checked.value.passed
        draft_ids.append(allocated.value.draft.draft_id)

    assert service.resource_library.finalize_resource_draft(
        tmp_path,
        draft_id=draft_ids[0],
        summary="First version.",
    ).ok
    conflict = service.resource_library.finalize_resource_draft(
        tmp_path,
        draft_id=draft_ids[1],
        summary="Conflicting version.",
    )

    assert not conflict.ok
    assert conflict.issues[0].kind == "resource_identity_content_conflict"


def test_finalize_distinct_resource_drafts_merges_concurrent_catalog_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = make_runtime().material
    library = service.resource_library
    drafts = []
    for suffix in ("a", "b"):
        target = service.normalize_resource_target(f"https://example.com/work-{suffix}")
        assert target.ok and target.value is not None
        allocated = library.allocate_resource_draft(tmp_path, target=target.value)
        assert allocated.ok and allocated.value is not None
        draft_root = Path(allocated.value.draft_root)
        (draft_root / "README.md").write_text(f"# Work {suffix}\n", encoding="utf-8")
        (draft_root / "article.md").write_text(f"canonical {suffix}\n", encoding="utf-8")
        checked = library.check_resource_draft(
            tmp_path,
            draft_id=allocated.value.draft.draft_id,
        )
        assert checked.ok and checked.value is not None and checked.value.passed
        drafts.append(allocated.value.draft.draft_id)

    barrier = Barrier(2)
    original_load = library._load_material_manifest

    def synchronized_load(path: Path):
        result = original_load(path)
        if path.name in drafts:
            barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(library, "_load_material_manifest", synchronized_load)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda draft_id: library.finalize_resource_draft(
                    tmp_path,
                    draft_id=draft_id,
                    summary=f"Finalized {draft_id}.",
                ),
                drafts,
            )
        )

    assert all(result.ok for result in results)
    listed = library.list_resources(tmp_path)
    assert listed.ok and listed.value is not None
    assert len(listed.value) == 2


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


@pytest.mark.parametrize(
    "version",
    [None, "v3"],
)
def test_resource_target_normalization_legacy_arxiv_forms(version: str | None) -> None:
    service = make_runtime().material
    suffix = version or ""
    forms = [
        f"math/0702723{suffix}",
        f"arxiv:math/0702723{suffix}",
        f"https://arxiv.org/abs/math/0702723{suffix}",
        f"https://arxiv.org/pdf/math/0702723{suffix}.pdf",
        f"https://arxiv.org/e-print/math/0702723{suffix}",
        f"https://arxiv.org/src/math/0702723{suffix}",
    ]

    normalized = [service.normalize_resource_target(form) for form in forms]

    assert all(result.ok and result.value is not None for result in normalized)
    values = [result.value for result in normalized if result.value is not None]
    assert {(value.kind, value.target, value.version, value.canonical_locator) for value in values} == {
        ("arxiv", "math/0702723", version, f"arxiv:math/0702723{suffix}")
    }
    resource_keys = {
        service.resource_library.resource_key_for_target(value).value
        for value in values
    }
    assert len(resource_keys) == 1


def test_prepare_resource_target_rejects_explicit_arxiv_kind_mismatch() -> None:
    prepared = make_runtime().material.prepare_resource_target(
        target_kind="arxiv",
        target="https://example.com/not-an-arxiv-paper",
    )

    assert not prepared.ok
    assert prepared.issues[0].kind == "invalid_arxiv_target"


def test_prepare_resource_target_reconciles_explicit_arxiv_version_once() -> None:
    service = make_runtime().material

    appended = service.prepare_resource_target(
        target_kind="arxiv",
        target="https://arxiv.org/abs/math/0702723",
        arxiv_version="v3",
    )
    already_versioned = service.prepare_resource_target(
        target_kind="arxiv",
        target="https://arxiv.org/abs/math/0702723v3",
        arxiv_version="v3",
    )
    mismatch = service.prepare_resource_target(
        target_kind="arxiv",
        target="https://arxiv.org/abs/math/0702723v2",
        arxiv_version="v3",
    )
    upper_case = service.prepare_resource_target(
        target_kind="arxiv",
        target="https://arxiv.org/abs/math/0702723v3",
        arxiv_version="V3",
    )
    invalid_empty = service.prepare_resource_target(
        target_kind="arxiv",
        target="https://arxiv.org/abs/math/0702723",
        arxiv_version="",
    )

    assert appended.ok and appended.value is not None
    assert already_versioned.ok and already_versioned.value is not None
    assert appended.value == already_versioned.value
    assert upper_case.ok and upper_case.value == already_versioned.value
    assert appended.value.version == "v3"
    assert appended.value.canonical_locator == "arxiv:math/0702723v3"
    assert not mismatch.ok
    assert mismatch.issues[0].kind == "invalid_arxiv_target"
    assert not invalid_empty.ok
    assert invalid_empty.issues[0].kind == "invalid_arxiv_target"


def test_legacy_arxiv_target_uses_arxiv_source_acquisition_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = make_runtime().material
    calls: list[tuple[str, str, str | None]] = []

    def fetch_arxiv_source(arxiv_id: str, version: str | None, *, output_root: Path) -> AcquiredArtifactResult:
        calls.append(("arxiv", arxiv_id, version))
        return AcquiredArtifactResult(
            ok=True,
            target=MaterialTarget(kind="arxiv", value=arxiv_id, version=version),
            output_root=str(output_root),
            summary="Fetched fake legacy arXiv source.",
        )

    def fetch_web_page(url: str, *, output_root: Path) -> AcquiredArtifactResult:
        calls.append(("web", url, None))
        return AcquiredArtifactResult(
            ok=True,
            target=MaterialTarget(kind="web_url", value=url),
            output_root=str(output_root),
            summary="Unexpected web route.",
        )

    monkeypatch.setattr(service.runtime.external.material, "fetch_arxiv_source", fetch_arxiv_source)
    monkeypatch.setattr(service.runtime.external.material, "fetch_web_page", fetch_web_page)
    prepared = service.prepare_resource_target(
        target_kind="arxiv",
        target="https://arxiv.org/abs/math/0702723v3",
    )
    assert prepared.ok and prepared.value is not None

    acquired = service.resource_curation.acquire_material_artifact(prepared.value, temp_root=tmp_path)

    assert acquired.ok and acquired.value is not None
    assert calls == [("arxiv", "math/0702723", "v3")]


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
    result = service.resource_curation.build_curator_result(
        decision.value,
        resource=curated.value,
        classification_reason="This is supporting material.",
        resource_role="Background reference.",
        consumer_formalization_scope="The current repo owns the theorem proof.",
    )
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

    source_input = tmp_path / "source-input"
    source_input.mkdir()
    (source_input / "README.md").write_text(
        "# Corpus\n\n"
        "Source provenance: local fixture.\n"
        "Reading order: read the arXiv material file.\n"
        "Main material: arxiv:2401.00001.md.\n"
        "Known gaps and extraction limits: none.\n",
        encoding="utf-8",
    )
    (source_input / "arxiv:2401.00001.md").write_text("already in source corpus\n", encoding="utf-8")
    imported_source = service.import_local_source_corpus(
        tmp_path,
        source_dir=source_input,
        entry_path="README.md",
        overview="Source duplicate fixture.",
        preparation_summary="Prepared current SourceCorpus truth for duplicate detection.",
    )
    assert imported_source.ok and imported_source.value is not None, imported_source.issues
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
    external_result = service.resource_curation.build_curator_result(
        external_decision.value,
        classification_reason="The directory is an independent formal project.",
        relation_to_current_repo_or_node="The consumer imports its public theorem.",
        consumer_need="A stable public theorem API.",
        provider_scope="Own the directory's reusable formal theory.",
    )

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
