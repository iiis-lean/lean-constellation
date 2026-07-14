from __future__ import annotations

from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.repo_run import SourceScope
from lean_constellation.services.material import ResourceMetadataInput
from lean_constellation.services.node import MaterialRefActor


def _write_source(repo_root: Path) -> None:
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "README.md").write_text("# Source corpus\n", encoding="utf-8")
    (source_root / "chapter.md").write_text("alpha definition\nbeta theorem\ngamma proof\n", encoding="utf-8")


def _register_resource(runtime, repo_root: Path) -> str:
    material = runtime.material
    target = material.normalize_resource_target("https://example.com/context-resource")
    assert target.ok and target.value is not None
    temp = repo_root / "resource_tmp"
    (temp / "normalized").mkdir(parents=True, exist_ok=True)
    (temp / "normalized" / "main.md").write_text("resource intro\nresource theorem\n", encoding="utf-8")
    registered = material.register_local_resource(
        repo_root,
        target=target.value,
        temp_dir=temp,
        metadata=ResourceMetadataInput(title="Context resource", source_url="https://example.com/context-resource"),
    )
    assert registered.ok and registered.value is not None
    return registered.value.resource.resource_key


def _create_content_node(runtime, repo_root: Path) -> None:
    tree = runtime.node.node_tree
    assert tree.ensure_root_scope_node(repo_root).ok
    assert tree.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal", boundary="Topic boundary.").ok
    assert tree.create_content_node(
        repo_root,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary.",
        objective="Collect context material.",
        success_criteria="Material refs are readable.",
    ).ok


def _open_source_index(material, repo_root: Path) -> None:  # noqa: ANN001
    resolved = material.resolve_source_scope(repo_root, source_scope=SourceScope(mode="all"))
    assert resolved.ok and resolved.value is not None
    opened = material.open_source_index_update(
        repo_root,
        resolved_scope=resolved.value,
        index_policy="auto",
    )
    assert opened.ok


def _commit_source_index(material, repo_root: Path) -> None:  # noqa: ANN001
    current = material.source_index.get_source_index_model(repo_root).value
    gate = material.validate_source_index_update(
        repo_root,
        baseline_index=None,
        expected_baseline_digest=material.source_index.missing_source_index_digest(),
        resolved_scope=list(current.active_file_scope),
        require_completed=True,
    )
    assert gate.ok and gate.value is not None and gate.value.gate.passed
    assert material.commit_source_index_update(repo_root, validated=gate.value).ok


def test_material_context_view_source_only_with_source_index(tmp_path: Path) -> None:
    runtime = make_runtime()
    material = runtime.material
    _write_source(tmp_path)
    _open_source_index(material, tmp_path)
    block = material.create_source_block(
        tmp_path,
        parent_id="root",
        kind="theorem",
        title="Beta theorem",
        summary="The indexed theorem statement.",
    )
    assert block.ok and block.value is not None
    added_ref = material.add_source_block_ref(
        tmp_path,
        block_id=block.value.block_id,
        path="chapter.md",
        start_line=2,
        end_line=2,
        role="statement",
    )
    assert added_ref.ok and added_ref.value is not None

    context = material.get_material_context_view(tmp_path, include_source=True, include_resources=False)

    assert context.ok and context.value is not None
    assert {item.locator for item in context.value.source_files} == {"README.md", "chapter.md"}
    assert context.value.resources == []
    assert len(context.value.source_blocks) == 1
    assert context.value.source_blocks[0].title == "Beta theorem"
    assert context.value.source_blocks[0].refs[0].reusable_ref_fields == {
        "path": "chapter.md",
        "start_line": 2,
        "end_line": 2,
    }


def test_material_context_committed_source_index_mode_omits_draft_blocks(tmp_path: Path) -> None:
    runtime = make_runtime()
    material = runtime.material
    _write_source(tmp_path)
    _open_source_index(material, tmp_path)
    assert material.set_source_index_overview(
        tmp_path, overview="Draft overview"
    ).ok
    block = material.create_source_block(
        tmp_path,
        parent_id="root",
        kind="theorem",
        title="Draft theorem",
        summary="Draft-only source block.",
    )
    assert block.ok and block.value is not None
    assert material.add_source_block_ref(
        tmp_path,
        block_id=block.value.block_id,
        path="chapter.md",
        start_line=2,
        end_line=2,
        role="statement",
    ).ok

    draft_context = material.get_material_context_view(tmp_path, include_source=True, include_resources=False)
    committed_only_draft = material.get_material_context_view(
        tmp_path,
        include_source=True,
        include_resources=False,
        require_committed_source_index=True,
    )

    assert draft_context.ok and draft_context.value is not None
    assert draft_context.value.source_index_overview == "Draft overview"
    assert len(draft_context.value.source_blocks) == 1
    assert committed_only_draft.ok and committed_only_draft.value is not None
    assert committed_only_draft.value.source_index_overview is None
    assert committed_only_draft.value.source_blocks == []

    assert material.mark_block_refs_done(tmp_path, block_id=block.value.block_id).ok
    assert material.mark_block_links_done(tmp_path, block_id=block.value.block_id).ok
    assert material.mark_block_completed(tmp_path, block_id=block.value.block_id).ok
    assert material.set_file_survey_status(
        tmp_path, path="README.md", status="surveyed", summary="Read."
    ).ok
    assert material.set_file_indexing_status(
        tmp_path, path="README.md", status="indexed"
    ).ok
    assert material.set_file_survey_status(
        tmp_path, path="chapter.md", status="surveyed", summary="Read."
    ).ok
    assert material.set_file_indexing_status(
        tmp_path, path="chapter.md", status="indexed"
    ).ok
    _commit_source_index(material, tmp_path)

    committed_context = material.get_material_context_view(
        tmp_path,
        include_source=True,
        include_resources=False,
        require_committed_source_index=True,
    )

    assert committed_context.ok and committed_context.value is not None
    assert committed_context.value.source_index_overview == "Draft overview"
    assert [item.title for item in committed_context.value.source_blocks] == ["Draft theorem"]


def test_material_context_view_resource_only_and_query_filter(tmp_path: Path) -> None:
    runtime = make_runtime()
    material = runtime.material
    resource_key = _register_resource(runtime, tmp_path)

    context = material.get_material_context_view(
        tmp_path,
        query="Context",
        include_source=False,
        include_resources=True,
    )

    assert context.ok and context.value is not None
    assert context.value.source_files == []
    assert [item.resource_key for item in context.value.resources] == [resource_key]
    assert len(context.value.search_hits) == 0


def test_material_context_view_node_scoped_refs_and_search(tmp_path: Path) -> None:
    runtime = make_runtime()
    material = runtime.material
    _write_source(tmp_path)
    resource_key = _register_resource(runtime, tmp_path)
    _create_content_node(runtime, tmp_path)
    owned = runtime.node.material_ref.add_owned_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="chapter.md",
        start_line=2,
        end_line=2,
        reason="Primary theorem source.",
        actor=MaterialRefActor.COORDINATOR,
    )
    context_ref = runtime.node.material_ref.add_context_resource_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        resource_key=resource_key,
        start_line=2,
        end_line=2,
        reason="Related resource theorem.",
        actor=MaterialRefActor.WORKER,
    )
    assert owned.ok and context_ref.ok

    context = material.get_material_context_view(tmp_path, node_path="Main.Topic.Core", query="theorem")

    assert context.ok and context.value is not None
    assert context.value.node_owned_refs[0].path == "chapter.md"
    assert context.value.node_owned_refs[0].reason == "Primary theorem source."
    assert context.value.node_context_refs[0].resource_key == resource_key
    assert context.value.node_context_refs[0].added_by == "worker"
    assert {hit.material_kind for hit in context.value.search_hits} == {"source", "resource"}


def test_material_context_view_failure_gates(tmp_path: Path) -> None:
    runtime = make_runtime()
    material = runtime.material
    _write_source(tmp_path)
    _create_content_node(runtime, tmp_path)
    added = runtime.node.material_ref.add_owned_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="chapter.md",
        start_line=2,
        end_line=2,
        actor=MaterialRefActor.COORDINATOR,
    )
    assert added.ok

    empty_scope = material.get_material_context_view(tmp_path, include_source=False, include_resources=False)
    bad_regex = material.get_material_context_view(tmp_path, query="[", regex=True)
    unknown_node = material.get_material_context_view(tmp_path, node_path="Main.Missing")
    (tmp_path / ".lean_constellation" / "source" / "chapter.md").unlink()
    stale_ref = material.get_material_context_view(tmp_path, node_path="Main.Topic.Core")

    assert not empty_scope.ok
    assert empty_scope.issues[0].kind == "material_context_empty_scope"
    assert not bad_regex.ok
    assert bad_regex.issues[0].kind == "invalid_search_regex"
    assert not unknown_node.ok
    assert unknown_node.issues[0].kind in {"missing_file", "node_missing", "node_not_found", "node_contract_not_found"}
    assert not stale_ref.ok
    assert stale_ref.issues[0].kind == "node_material_ref_invalid"
