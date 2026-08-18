import json
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


def test_node_index_rebuild_is_byte_identical_and_timestamp_free(tmp_path: Path) -> None:
    runtime = make_runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    path = runtime.foundation.layout.node_index_path(FoundationContext(repo_root=tmp_path))

    first = runtime.node.node_tree.node_store.rebuild_index(tmp_path)
    first_bytes = path.read_bytes()
    second = runtime.node.node_tree.node_store.rebuild_index(tmp_path)

    assert first.ok and second.ok
    assert path.read_bytes() == first_bytes
    payload = json.loads(first_bytes)
    assert payload["schema_version"] == 2
    assert "rebuilt_at" not in payload
    assert "summary" not in payload


def test_node_store_does_not_rebuild_an_existing_invalid_index(tmp_path: Path) -> None:
    runtime = make_runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    ctx = FoundationContext(repo_root=tmp_path)
    index_path = runtime.foundation.layout.node_index_path(ctx)
    index_path.write_text('{"schema_version": 2, "removed_entries": []}', encoding="utf-8")

    loaded = runtime.node.node_tree.node_store.read_index(tmp_path)

    assert not loaded.ok
    assert loaded.issues[0].kind == "schema_validation_failed"
    assert "removed_entries" in index_path.read_text(encoding="utf-8")


def test_node_index_rejects_previous_schema_version_and_removed_fields(tmp_path: Path) -> None:
    runtime = make_runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    path = runtime.foundation.layout.node_index_path(FoundationContext(repo_root=tmp_path))
    current = {
        "schema_version": 2,
        "entries": [],
        "active_path_to_node_id": {},
    }

    missing = {key: value for key, value in current.items() if key != "schema_version"}
    path.write_text(json.dumps(missing), encoding="utf-8")
    missing_loaded = runtime.node.node_tree.node_store.read_index(tmp_path)

    previous = {**current, "schema_version": 1}
    path.write_text(json.dumps(previous), encoding="utf-8")
    previous_loaded = runtime.node.node_tree.node_store.read_index(tmp_path)

    removed = {**current, "rebuilt_at": "2026-08-19T00:00:00Z", "summary": "old derived fields"}
    path.write_text(json.dumps(removed), encoding="utf-8")
    removed_loaded = runtime.node.node_tree.node_store.read_index(tmp_path)

    assert not missing_loaded.ok
    assert missing_loaded.issues[0].kind == "node_index_schema_version_invalid"
    assert not previous_loaded.ok
    assert previous_loaded.issues[0].kind == "node_index_schema_version_invalid"
    assert not removed_loaded.ok
    assert removed_loaded.issues[0].kind == "schema_validation_failed"
    assert json.loads(path.read_text(encoding="utf-8")) == removed


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
