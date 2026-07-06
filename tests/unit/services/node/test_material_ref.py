from tests.unit_services_helpers import make_runtime

from pathlib import Path

from lean_constellation.services.foundation import FoundationContext, FoundationService
from lean_constellation.services.material import MaterialService, ResourceMetadataInput
from lean_constellation.services.node import MaterialRefActor, MaterialRefComponent, NodeContractSnapshot, NodeTreeComponent


def _create_content_node(tmp_path: Path) -> None:
    tree = make_runtime().node.node_tree
    assert tree.ensure_root_scope_node(tmp_path).ok
    assert tree.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary.").ok
    assert tree.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary.",
        objective="Collect core material.",
        success_criteria="Core material refs are ready.",
    ).ok


def _write_source(tmp_path: Path, relative_path: str = "notes.md", text: str = "first line\nsecond theorem\nthird proof\n") -> None:
    source_root = tmp_path / ".lean_constellation" / "source"
    (source_root / Path(relative_path).parent).mkdir(parents=True, exist_ok=True)
    (source_root / relative_path).write_text(text, encoding="utf-8")


def _register_resource(tmp_path: Path, service: MaterialService) -> str:
    target = service.normalize_resource_target("https://example.com/material/page")
    assert target.ok and target.value is not None
    temp = tmp_path / "resource_tmp"
    (temp / "normalized").mkdir(parents=True)
    (temp / "original").mkdir()
    (temp / "normalized" / "page.md").write_text("resource first\nresource second\n", encoding="utf-8")
    (temp / "original" / "page.html").write_text("<p>resource</p>", encoding="utf-8")
    registered = service.register_local_resource(
        tmp_path,
        target=target.value,
        temp_dir=temp,
        metadata=ResourceMetadataInput(title="Example material", source_url="https://example.com/material/page"),
    )
    assert registered.ok and registered.value is not None
    return registered.value.resource.resource_key


def _component() -> MaterialRefComponent:
    return make_runtime().node.material_ref


def test_add_source_and_resource_refs_and_list_view(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _write_source(tmp_path)
    component = _component()
    resource_key = _register_resource(tmp_path, component.runtime.material)

    owned = component.add_owned_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="notes.md",
        start_line=1,
        end_line=2,
        reason="Primary statement source.",
        actor=MaterialRefActor.COORDINATOR,
    )
    assert owned.ok
    assert owned.value is not None
    assert len(owned.value.contract.owned_refs) == 1
    owned_ref = owned.value.contract.owned_refs[0]
    assert owned_ref.added_by == MaterialRefActor.COORDINATOR
    assert owned_ref.reason == "Primary statement source."
    assert owned_ref.ref.ref.path == "notes.md"

    context = component.add_context_resource_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        resource_key=resource_key,
        start_line=2,
        end_line=2,
        reason="Background web note.",
        actor="worker",
    )
    assert context.ok
    assert context.value is not None
    assert context.value.contract.context_refs[0].added_by == MaterialRefActor.WORKER
    assert context.value.contract.context_refs[0].ref.ref.start_line == 2

    listed = component.list_node_material_refs(tmp_path, node_path="Main.Topic.Core")
    assert listed.ok
    assert listed.value is not None
    assert listed.value.owned_refs[0].index == 0
    assert listed.value.owned_refs[0].path == "notes.md"
    assert listed.value.owned_refs[0].valid is True
    assert listed.value.context_refs[0].resource_key == resource_key
    assert listed.value.context_refs[0].added_by == MaterialRefActor.WORKER
    assert listed.value.context_refs[0].preview_summary == "Previewed resource material ref."


def test_invalid_range_is_rejected_before_contract_write(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _write_source(tmp_path)
    component = _component()

    result = component.add_owned_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="notes.md",
        start_line=9,
        end_line=12,
        actor="coordinator",
    )

    assert not result.ok
    assert result.issues[0].kind == "source_ref_range_invalid"
    foundation = make_runtime().foundation
    path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic.Core", 1)
    loaded = foundation.store.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    assert loaded.value.owned_refs == []


def test_duplicate_add_is_idempotent_warning(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _write_source(tmp_path)
    component = _component()
    first = component.add_owned_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="notes.md",
        start_line=1,
        end_line=1,
        actor="coordinator",
    )
    assert first.ok

    duplicate = component.add_owned_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="notes.md",
        start_line=1,
        end_line=1,
        actor="coordinator",
    )

    assert duplicate.ok
    assert duplicate.value is not None
    assert duplicate.issues[0].kind == "material_ref_duplicate"
    assert len(duplicate.value.contract.owned_refs) == 1


def test_context_duplicate_add_is_idempotent_warning(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _write_source(tmp_path)
    component = _component()
    first = component.add_context_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="notes.md",
        start_line=2,
        end_line=2,
        actor="worker",
    )
    assert first.ok

    duplicate = component.add_context_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="notes.md",
        start_line=2,
        end_line=2,
        actor="worker",
    )

    assert duplicate.ok
    assert duplicate.value is not None
    assert duplicate.issues[0].kind == "material_ref_duplicate"
    assert len(duplicate.value.contract.context_refs) == 1


def test_refs_without_range_validate_first_line_and_store_open_range(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _write_source(tmp_path)
    component = _component()
    resource_key = _register_resource(tmp_path, component.runtime.material)

    source = component.add_owned_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="notes.md",
        actor="coordinator",
    )
    resource = component.add_context_resource_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        resource_key=resource_key,
        actor="worker",
    )

    assert source.ok, source.issues
    assert resource.ok, resource.issues
    listed = component.list_node_material_refs(tmp_path, node_path="Main.Topic.Core")
    assert listed.ok
    assert listed.value is not None
    assert listed.value.owned_refs[0].start_line is None
    assert listed.value.owned_refs[0].end_line is None
    assert listed.value.owned_refs[0].valid is True
    assert listed.value.context_refs[0].start_line is None
    assert listed.value.context_refs[0].end_line is None
    assert listed.value.context_refs[0].valid is True


def test_ref_without_range_rejects_empty_source_file(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _write_source(tmp_path, relative_path="empty.md", text="")
    component = _component()

    result = component.add_owned_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="empty.md",
        actor="coordinator",
    )

    assert not result.ok
    assert result.issues[0].kind == "source_ref_range_invalid"


def test_worker_delete_permission_and_missing_ref(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _write_source(tmp_path)
    component = _component()
    coordinator_ref = component.add_owned_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="notes.md",
        start_line=1,
        end_line=1,
        actor="coordinator",
    )
    assert coordinator_ref.ok and coordinator_ref.value is not None

    denied = component.remove_owned_ref(tmp_path, node_path="Main.Topic.Core", index=0, actor="worker")
    assert not denied.ok
    assert denied.issues[0].kind == "material_ref_permission_denied"

    missing = component.remove_owned_ref(tmp_path, node_path="Main.Topic.Core", index=99, actor="coordinator")
    assert not missing.ok
    assert missing.issues[0].kind == "material_ref_index_out_of_range"

    worker_ref = component.add_context_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="notes.md",
        start_line=2,
        end_line=2,
        actor="worker",
    )
    assert worker_ref.ok and worker_ref.value is not None

    removed = component.remove_context_ref(tmp_path, node_path="Main.Topic.Core", index=0, actor="worker")
    assert removed.ok
    assert removed.value is not None
    assert removed.value.contract.context_refs == []


def test_coordinator_can_remove_owned_ref_by_index_and_bad_index_is_rejected(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _write_source(tmp_path)
    component = _component()
    added = component.add_owned_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="notes.md",
        start_line=1,
        end_line=1,
        actor="coordinator",
    )
    assert added.ok and added.value is not None

    negative = component.remove_owned_ref(tmp_path, node_path="Main.Topic.Core", index=-1, actor="coordinator")
    assert not negative.ok
    assert negative.issues[0].kind == "material_ref_index_out_of_range"

    removed = component.remove_owned_ref(tmp_path, node_path="Main.Topic.Core", index=0, actor="coordinator")
    assert removed.ok
    assert removed.value is not None
    assert removed.value.contract.owned_refs == []


def test_context_remove_permission_and_missing_ref(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _write_source(tmp_path)
    component = _component()
    added = component.add_context_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="notes.md",
        start_line=1,
        end_line=1,
        actor="coordinator",
    )
    assert added.ok and added.value is not None

    denied = component.remove_context_ref(tmp_path, node_path="Main.Topic.Core", index=0, actor="worker")
    assert not denied.ok
    assert denied.issues[0].kind == "material_ref_permission_denied"

    missing = component.remove_context_ref(tmp_path, node_path="Main.Topic.Core", index=99, actor="coordinator")
    assert not missing.ok
    assert missing.issues[0].kind == "material_ref_index_out_of_range"


def test_actor_and_range_shape_validation(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _write_source(tmp_path)
    component = _component()

    bad_actor = component.add_context_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="notes.md",
        actor="reviewer",
    )
    assert not bad_actor.ok
    assert bad_actor.issues[0].kind == "material_ref_actor_invalid"

    incomplete_range = component.add_context_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="notes.md",
        start_line=1,
        actor="coordinator",
    )
    assert not incomplete_range.ok
    assert incomplete_range.issues[0].kind == "material_ref_range_incomplete"

    missing_resource_key = component.add_context_resource_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        resource_key=" ",
        actor="coordinator",
    )
    assert not missing_resource_key.ok
    assert missing_resource_key.issues[0].kind == "material_ref_locator_required"

    missing_locator = component.add_context_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path=" ",
        actor="coordinator",
    )
    assert not missing_locator.ok
    assert missing_locator.issues[0].kind == "material_ref_locator_required"


def test_list_view_reports_invalid_preview_without_revalidating_gate(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _write_source(tmp_path)
    component = _component()
    added = component.add_owned_source_ref(
        tmp_path,
        node_path="Main.Topic.Core",
        path="notes.md",
        start_line=1,
        end_line=1,
        actor="coordinator",
    )
    assert added.ok
    (tmp_path / ".lean_constellation" / "source" / "notes.md").unlink()

    listed = component.list_node_material_refs(tmp_path, node_path="Main.Topic.Core")

    assert listed.ok
    assert listed.value is not None
    assert listed.value.owned_refs[0].valid is False
    assert "Material file not found" in (listed.value.owned_refs[0].preview_summary or "")
