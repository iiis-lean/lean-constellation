import shutil
from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.foundation import FoundationContext
from lean_constellation.services.node import NodeLifecycle


def test_node_store_writes_node_id_layout_and_active_index(tmp_path: Path) -> None:
    runtime = make_runtime()
    created = runtime.node.node_tree.ensure_root_scope_node(tmp_path)
    assert created.ok and created.value is not None

    ctx = FoundationContext(repo_root=tmp_path)
    node_id = created.value.node_id
    assert (runtime.foundation.layout.node_metadata_path_by_id(ctx, node_id)).is_file()
    assert (runtime.foundation.layout.node_contract_path_by_id(ctx, node_id, 1)).is_file()
    assert not (runtime.foundation.layout.node_metadata_dir(ctx, "Main") / "node.json").exists()

    index = runtime.node.node_tree.node_store.read_index(tmp_path)
    assert index.ok and index.value is not None
    assert index.value.active_path_to_node_id == {"Main": node_id}
    assert index.value.entries[0].node_id == node_id


def test_node_store_rebuilds_index_from_truth(tmp_path: Path) -> None:
    runtime = make_runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok

    ctx = FoundationContext(repo_root=tmp_path)
    runtime.foundation.layout.node_index_path(ctx).unlink()

    rebuilt = runtime.node.node_tree.node_store.read_index(tmp_path)

    assert rebuilt.ok and rebuilt.value is not None
    assert set(rebuilt.value.active_path_to_node_id) == {"Main", "Main.Topic"}
    assert runtime.foundation.layout.node_index_path(ctx).is_file()


def test_node_store_migrates_legacy_path_layout(tmp_path: Path) -> None:
    runtime = make_runtime()
    created = runtime.node.node_tree.ensure_root_scope_node(tmp_path)
    assert created.ok and created.value is not None
    ctx = FoundationContext(repo_root=tmp_path)
    node_id_dir = runtime.foundation.layout.node_dir_by_id(ctx, created.value.node_id)
    legacy_dir = runtime.foundation.layout.node_metadata_dir(ctx, "Main")
    assert node_id_dir.exists()
    assert not legacy_dir.exists()
    legacy_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(node_id_dir), str(legacy_dir))
    runtime.foundation.layout.node_index_path(ctx).unlink()

    migrated = runtime.node.node_tree.node_store.migrate_path_layout_to_node_id_layout(tmp_path)

    assert migrated.ok
    assert migrated.value is not None
    assert migrated.value.migrated_nodes == [created.value.node_id]
    assert node_id_dir.exists()
    assert not legacy_dir.exists()
    index = runtime.node.node_tree.node_store.read_index(tmp_path)
    assert index.ok and index.value is not None
    assert index.value.active_path_to_node_id == {"Main": created.value.node_id}


def test_delete_and_recreate_same_path_uses_new_node_id(tmp_path: Path) -> None:
    runtime = make_runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    first = runtime.node.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary")
    assert first.ok and first.value is not None
    assert runtime.node.commit_scope_contract(
        tmp_path, scope_path="Main.Topic", summary="Topic scope complete."
    ).ok

    deleted = runtime.node.mark_node_deleted(tmp_path, path="Main.Topic", reason="Replace topic.")
    assert deleted.ok
    second = runtime.node.create_scope_node(tmp_path, path="Main.Topic", goal="New topic goal", boundary="New topic boundary")
    assert second.ok and second.value is not None

    assert second.value.node_id != first.value.node_id
    nodes = runtime.node.node_tree.node_store.list_nodes(tmp_path)
    assert nodes.ok and nodes.value is not None
    states = {(node.node_id, node.lifecycle) for node in nodes.value if node.path == "Main.Topic"}
    assert (first.value.node_id, NodeLifecycle.OBSOLETE) in states
    assert (second.value.node_id, NodeLifecycle.ACTIVE) in states

    index = runtime.node.node_tree.node_store.read_index(tmp_path)
    assert index.ok and index.value is not None
    assert index.value.active_path_to_node_id["Main.Topic"] == second.value.node_id
