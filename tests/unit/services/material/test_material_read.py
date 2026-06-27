from pathlib import Path

from lean_constellation.services.material import MaterialService, ResourceMetadataInput


def _prepare_source(repo_root: Path) -> None:
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text("# Source\n", encoding="utf-8")
    (source_root / "chapter.md").write_text("alpha\nbeta theorem\ngamma theorem\n", encoding="utf-8")
    (source_root / "image.bin").write_bytes(b"\x00\x01")


def _register_resource(service: MaterialService, repo_root: Path) -> str:
    target = service.normalize_resource_target("https://example.com/resource")
    assert target.ok and target.value is not None
    temp = repo_root / "resource_tmp"
    (temp / "normalized").mkdir(parents=True)
    (temp / "normalized" / "resource.md").write_text("resource alpha\nresource theorem\n", encoding="utf-8")
    registered = service.register_local_resource(
        repo_root,
        target=target.value,
        temp_dir=temp,
        metadata=ResourceMetadataInput(title="Resource", source_url="https://example.com/resource"),
    )
    assert registered.ok and registered.value is not None
    return registered.value.resource.resource_key


def test_list_material_files_source_resource_all_and_invalid_kind(tmp_path: Path) -> None:
    service = MaterialService()
    _prepare_source(tmp_path)
    resource_key = _register_resource(service, tmp_path)

    source = service.material_read.list_material_files(tmp_path, material_kind="source")
    resource = service.material_read.list_material_files(tmp_path, material_kind="resource")
    all_files = service.material_read.list_material_files(tmp_path, material_kind="all")
    invalid = service.material_read.list_material_files(tmp_path, material_kind="unknown")

    assert source.ok and source.value is not None
    assert {item.locator for item in source.value.files} == {"README.md", "chapter.md", "image.bin"}
    assert any(not item.readable for item in source.value.files if item.locator == "image.bin")
    assert resource.ok and resource.value is not None
    assert resource.value.files[0].locator == f"{resource_key}:normalized/resource.md"
    assert all_files.ok and all_files.value is not None
    assert {item.kind for item in all_files.value.files} == {"source", "resource"}
    assert not invalid.ok
    assert invalid.issues[0].kind == "invalid_material_kind"


def test_read_source_and_resource_range_boundaries(tmp_path: Path) -> None:
    service = MaterialService()
    _prepare_source(tmp_path)
    resource_key = _register_resource(service, tmp_path)

    source = service.read_source_range(tmp_path, path="chapter.md", start_line=2, end_line=3, context_lines=1)
    resource = service.read_resource_range(tmp_path, resource_key=resource_key, start_line=2, end_line=2, context_lines=0)
    source_escape = service.read_source_range(tmp_path, path="../outside.md", start_line=1, end_line=1)
    bad_context = service.read_source_range(tmp_path, path="chapter.md", start_line=1, end_line=1, context_lines=-1)
    missing_resource = service.read_resource_range(tmp_path, resource_key="missing", start_line=1, end_line=1)
    invalid_resource_key = service.read_resource_range(tmp_path, resource_key="../bad", start_line=1, end_line=1)

    assert source.ok and source.value is not None
    assert "2: beta theorem" in source.value.text_with_line_numbers
    assert "1: alpha" in (source.value.before_context or "")
    assert resource.ok and resource.value is not None
    assert "2: resource theorem" in resource.value.text_with_line_numbers
    assert not source_escape.ok
    assert source_escape.issues[0].kind == "source_material_path_invalid"
    assert not bad_context.ok
    assert bad_context.issues[0].kind == "invalid_context_lines"
    assert not missing_resource.ok
    assert missing_resource.issues[0].kind == "resource_not_found"
    assert not invalid_resource_key.ok
    assert invalid_resource_key.issues[0].kind == "invalid_resource_key"


def test_search_material_text_literal_regex_limit_and_errors(tmp_path: Path) -> None:
    service = MaterialService()
    _prepare_source(tmp_path)
    _register_resource(service, tmp_path)

    literal = service.search_material_text(tmp_path, query="theorem", scope="all", limit=2)
    regex = service.search_material_text(tmp_path, query=r"resource\s+theorem", scope="resource", regex=True)
    empty = service.search_material_text(tmp_path, query=" ")
    bad_regex = service.search_material_text(tmp_path, query="[", regex=True)
    bad_limit = service.search_material_text(tmp_path, query="theorem", limit=0)
    bad_scope = service.search_material_text(tmp_path, query="theorem", scope="bad")

    assert literal.ok and literal.value is not None
    assert len(literal.value.hits) == 2
    assert literal.value.truncated
    assert regex.ok and regex.value is not None
    assert len(regex.value.hits) == 1
    assert regex.value.hits[0].ref_kind == "resource"
    assert not empty.ok
    assert empty.issues[0].kind == "empty_query"
    assert not bad_regex.ok
    assert bad_regex.issues[0].kind == "invalid_search_regex"
    assert not bad_limit.ok
    assert bad_limit.issues[0].kind == "invalid_search_limit"
    assert not bad_scope.ok
    assert bad_scope.issues[0].kind == "invalid_material_kind"


def test_validate_and_preview_material_ref_source_resource_and_malformed(tmp_path: Path) -> None:
    service = MaterialService()
    _prepare_source(tmp_path)
    resource_key = _register_resource(service, tmp_path)

    source_valid = service.material_read.validate_material_ref(
        tmp_path,
        ref_kind="source",
        locator="chapter.md",
        start_line=1,
        end_line=2,
    )
    resource_valid = service.material_read.validate_material_ref(
        tmp_path,
        ref_kind="resource",
        locator=resource_key,
        start_line=1,
        end_line=2,
    )
    invalid_kind = service.material_read.validate_material_ref(
        tmp_path,
        ref_kind="unknown",
        locator="chapter.md",
        start_line=1,
        end_line=1,
    )
    invalid_resource_key = service.material_read.validate_material_ref(
        tmp_path,
        ref_kind="resource",
        locator="../bad",
        start_line=1,
        end_line=1,
    )

    source_preview = service.material_read.preview_material_ref(
        tmp_path,
        ref={"kind": "source", "ref": {"path": "chapter.md", "start_line": 2, "end_line": 2}},
    )
    resource_preview = service.read_material_ref(
        tmp_path,
        ref={"kind": "resource", "resource_key": resource_key, "start_line": 2, "end_line": 2},
    )
    malformed_object = service.material_read.preview_material_ref(tmp_path, ref=None)
    missing_locator = service.material_read.preview_material_ref(tmp_path, ref={"kind": "source"})
    bad_range = service.material_read.preview_material_ref(
        tmp_path,
        ref={"kind": "source", "path": "chapter.md", "start_line": "abc"},
    )

    assert source_valid.ok and source_valid.value is not None
    assert source_valid.value.valid
    assert resource_valid.ok and resource_valid.value is not None
    assert resource_valid.value["valid"] is True
    assert not invalid_kind.ok
    assert invalid_kind.issues[0].kind == "invalid_ref_kind"
    assert not invalid_resource_key.ok
    assert invalid_resource_key.issues[0].kind == "invalid_resource_key"
    assert source_preview.ok and source_preview.value is not None
    assert "2: beta theorem" in source_preview.value.preview.text_with_line_numbers
    assert resource_preview.ok and resource_preview.value is not None
    assert "2: resource theorem" in resource_preview.value.text_with_line_numbers
    assert not malformed_object.ok
    assert malformed_object.issues[0].kind == "invalid_material_ref"
    assert not missing_locator.ok
    assert missing_locator.issues[0].kind == "invalid_material_ref"
    assert not bad_range.ok
    assert bad_range.issues[0].kind == "invalid_material_ref_range"
