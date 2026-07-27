from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.foundation import FoundationContext, WriteMode
from lean_constellation.services.node import NodeContractSnapshot, NodeService


def _create_node_tree(tmp_path: Path, service: NodeService) -> None:
    assert service.node_tree.ensure_root_scope_node(tmp_path).ok
    assert service.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal.", boundary="Topic boundary.").ok
    assert service.create_scope_node(tmp_path, path="Main.Topic.Provider", goal="Provider goal.", boundary="Provider boundary.").ok
    assert service.create_content_node(
        tmp_path,
        path="Main.Topic.Consumer",
        goal="Consumer goal.",
        boundary="Consumer boundary.",
        objective="Use provider and material.",
        success_criteria="Consumer contract is ready.",
    ).ok
    assert service.create_content_node(
        tmp_path,
        path="Main.Topic.WorkerProvider",
        goal="Worker provider goal.",
        boundary="Worker provider boundary.",
        objective="Provide worker-owned dependency target.",
        success_criteria="Worker provider contract is committed.",
    ).ok


def _commit_provider_scope(tmp_path: Path, service: NodeService) -> DeclRef:
    path = service.runtime.foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic.Provider", 1)
    loaded = service.runtime.foundation.store.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    ref = DeclRef(repo=None, node="Main.Topic.Provider", name="helper", revision=1)
    loaded.value.exports = [ref]
    assert service.runtime.foundation.store.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok
    committed = service.commit_scope_contract(tmp_path, scope_path="Main.Topic.Provider", summary="Provider exposes helper.")
    assert committed.ok
    return ref


def _write_source(tmp_path: Path) -> None:
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "notes.md").write_text("first line\nsecond line\nthird line\n", encoding="utf-8")


def test_current_contract_view_aggregates_deps_material_and_mathlib(tmp_path: Path) -> None:
    runtime = make_runtime()
    service = runtime.node
    _create_node_tree(tmp_path, service)
    assert runtime.mathlib.upsert_mathlib_module_entry(
        tmp_path,
        module="Mathlib.Data.Nat.Basic",
    ).ok
    assert runtime.mathlib.add_mathlib_module_use(
        tmp_path,
        node_path="Main.Topic.Consumer",
        module="Mathlib.Data.Nat.Basic",
        reason="Natural number facts.",
        actor="coordinator",
    ).ok

    view = service.get_current_contract_view(tmp_path, node_path="Main.Topic.Consumer")

    assert view.ok
    assert view.value is not None
    assert view.value.node_path == "Main.Topic.Consumer"
    assert view.value.dependencies == []
    assert view.value.materials == []
    assert view.value.mathlib_modules == ["Mathlib.Data.Nat.Basic"]


def test_current_node_dep_wrapper_resolves_expected_public_decl_and_removes(tmp_path: Path) -> None:
    service = make_runtime().node
    _create_node_tree(tmp_path, service)
    ref = _commit_provider_scope(tmp_path, service)

    added = service.add_current_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Provider",
        expected_public_decl_names=["helper"],
        reason="Use the provider helper.",
        actor="coordinator",
    )

    assert added.ok
    assert added.value is not None
    assert added.value.changed is True
    assert len(added.value.added) == 1
    assert added.value.added[0].expected_decl_refs == [ref]
    assert added.value.managed_projection_changed is True
    assert added.value.changed_files
    assert added.value.reread_required is True

    removed = service.remove_current_node_dep(tmp_path, node_path="Main.Topic.Consumer", index=0, actor="coordinator")
    assert removed.ok
    assert removed.value is not None
    assert removed.value.changed is True
    assert removed.value.removed[0].expected_decl_refs == [ref]


def test_current_node_dep_wrapper_preserves_worker_delete_policy(tmp_path: Path) -> None:
    service = make_runtime().node
    _create_node_tree(tmp_path, service)
    _commit_provider_scope(tmp_path, service)

    added = service.add_current_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Provider",
        expected_public_decl_names=[],
        reason="Coordinator-owned provider dependency.",
        actor="coordinator",
    )
    assert added.ok

    denied = service.remove_current_node_dep(tmp_path, node_path="Main.Topic.Consumer", index=0, actor="worker")

    assert not denied.ok
    assert denied.issues[0].kind == "node_dep_permission_denied"


def test_current_material_ref_wrapper_adds_and_removes_refs(tmp_path: Path) -> None:
    service = make_runtime().node
    _create_node_tree(tmp_path, service)
    _write_source(tmp_path)

    added = service.add_current_material_ref(
        tmp_path,
        node_path="Main.Topic.Consumer",
        ref_scope="owned",
        material_kind="source",
        locator="notes.md",
        start_line=1,
        end_line=2,
        reason="Primary source lines.",
        actor="coordinator",
    )

    assert added.ok
    assert added.value is not None
    assert added.value.changed is True
    assert len(added.value.added) == 1
    assert added.value.added[0].ref.ref.path == "notes.md"
    assert added.value.added[0].reason == "Primary source lines."

    removed = service.remove_current_material_ref(
        tmp_path,
        node_path="Main.Topic.Consumer",
        ref_scope="owned",
        index=0,
        actor="coordinator",
    )

    assert removed.ok
    assert removed.value is not None
    assert removed.value.changed is True
    assert removed.value.removed[0].ref.ref.path == "notes.md"


def test_current_material_ref_wrapper_reports_invalid_material_ref(tmp_path: Path) -> None:
    service = make_runtime().node
    _create_node_tree(tmp_path, service)
    _write_source(tmp_path)

    invalid = service.add_current_material_ref(
        tmp_path,
        node_path="Main.Topic.Consumer",
        ref_scope="context",
        material_kind="source",
        locator="notes.md",
        start_line=10,
        end_line=12,
        actor="worker",
    )

    assert not invalid.ok
    assert invalid.issues[0].kind == "source_ref_range_invalid"
