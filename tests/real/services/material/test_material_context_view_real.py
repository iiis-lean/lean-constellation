from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.repo_run import SourceScope
from lean_constellation.services.material import ResourceMetadataInput
from lean_constellation.services.node import MaterialRefActor


@pytest.mark.real
def test_material_context_view_real_small_source_resource_node_fixture(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    runtime = make_runtime()
    material = runtime.material

    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(
        "# Real context source\n\n"
        "Source provenance: local real material context fixture.\n"
        "Reading order: read this entry, then read chapter.md as the main material.\n"
        "Main material: chapter.md contains the definition, theorem, and proof lines.\n"
        "Known gaps and extraction limits: no missing source sections are known.\n",
        encoding="utf-8",
    )
    (source_root / "chapter.md").write_text("definition line\ntheorem line\nproof line\n", encoding="utf-8")
    assert material.submit_source_corpus_prepared(
        repo_root,
        entry_path="README.md",
        overview="Real context view source corpus.",
        preparation_summary="Prepared source files.",
    ).ok
    update_id = "real-material-context-source-index"
    resolved = material.resolve_source_scope(repo_root, source_scope=SourceScope(mode="all"))
    assert resolved.ok and resolved.value is not None, resolved.issues
    opened = material.open_source_index_update(
        repo_root,
        update_id=update_id,
        resolved_scope=resolved.value,
        index_policy="auto",
    )
    assert opened.ok and opened.value is not None, opened.issues
    block = material.create_source_block(
        repo_root,
        parent_id="root",
        kind="theorem",
        title="Real theorem",
        summary="The source-indexed theorem.",
        expected_update_id=update_id,
    )
    assert block.ok and block.value is not None
    assert material.add_source_block_ref(
        repo_root,
        block_id=block.value.block_id,
        path="chapter.md",
        start_line=2,
        end_line=2,
        role="statement",
        expected_update_id=update_id,
    ).ok

    target = material.normalize_resource_target("https://example.com/real-context-resource")
    assert target.ok and target.value is not None
    temp = repo_root / "resource_tmp"
    (temp / "normalized").mkdir(parents=True)
    (temp / "normalized" / "main.md").write_text("resource theorem line\n", encoding="utf-8")
    registered = material.register_local_resource(
        repo_root,
        target=target.value,
        temp_dir=temp,
        metadata=ResourceMetadataInput(title="Real theorem context resource"),
    )
    assert registered.ok and registered.value is not None

    tree = runtime.node.node_tree
    assert tree.ensure_root_scope_node(repo_root).ok
    assert tree.create_scope_node(repo_root, path="Main.Topic", goal="Topic", boundary="Topic boundary.").ok
    assert tree.create_content_node(
        repo_root,
        path="Main.Topic.Core",
        goal="Core",
        boundary="Core boundary.",
        objective="Use material context.",
        success_criteria="Context refs are readable.",
    ).ok
    assert runtime.node.material_ref.add_owned_source_ref(
        repo_root,
        node_path="Main.Topic.Core",
        path="chapter.md",
        start_line=2,
        end_line=2,
        actor=MaterialRefActor.COORDINATOR,
    ).ok
    assert runtime.node.material_ref.add_context_resource_ref(
        repo_root,
        node_path="Main.Topic.Core",
        resource_key=registered.value.resource.resource_key,
        start_line=1,
        end_line=1,
        actor=MaterialRefActor.WORKER,
    ).ok

    context = material.get_material_context_view(repo_root, node_path="Main.Topic.Core", query="theorem")

    assert context.ok and context.value is not None
    assert context.value.source_blocks[0].title == "Real theorem"
    assert context.value.resources[0].resource_key == registered.value.resource.resource_key
    assert context.value.node_owned_refs[0].path == "chapter.md"
    assert context.value.node_context_refs[0].resource_key == registered.value.resource.resource_key
    assert {hit.material_kind for hit in context.value.search_hits} == {"source", "resource"}
