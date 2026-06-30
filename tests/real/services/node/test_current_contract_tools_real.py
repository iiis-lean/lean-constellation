from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.foundation import FoundationContext, WriteMode
from lean_constellation.services.node import NodeContractSnapshot, NodeService


def _create_tree(repo_root: Path, service: NodeService) -> None:
    assert service.node_tree.ensure_root_scope_node(repo_root).ok
    assert service.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal.", boundary="Topic boundary.").ok
    assert service.create_scope_node(repo_root, path="Main.Topic.Provider", goal="Provider goal.", boundary="Provider boundary.").ok
    assert service.create_content_node(
        repo_root,
        path="Main.Topic.Consumer",
        goal="Consumer goal.",
        boundary="Consumer boundary.",
        objective="Use provider and source material.",
        success_criteria="Consumer contract has the required refs.",
    ).ok


def _commit_provider(repo_root: Path, service: NodeService) -> None:
    path = service.runtime.foundation.layout.node_contract_path(FoundationContext(repo_root=repo_root), "Main.Topic.Provider", 1)
    loaded = service.runtime.foundation.store.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.exports = [DeclRef(repo=None, node="Main.Topic.Provider", name="helper", revision=1)]
    assert service.runtime.foundation.store.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok
    committed = service.commit_scope_contract(repo_root, scope_path="Main.Topic.Provider", summary="Provider exposes helper.")
    assert committed.ok, committed.issues


def _write_source(repo_root: Path) -> None:
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "notes.md").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")


@pytest.mark.real
def test_current_contract_tool_wrappers_persist_and_reload_real(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    runtime = make_runtime()
    service = runtime.node
    _create_tree(repo_root, service)
    _commit_provider(repo_root, service)
    _write_source(repo_root)

    dep = service.add_current_node_dep(
        repo_root,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Provider",
        expected_public_decl_names=["helper"],
        reason="Use the provider helper.",
        actor="coordinator",
    )
    assert dep.ok, dep.issues
    material = service.add_current_material_ref(
        repo_root,
        node_path="Main.Topic.Consumer",
        ref_scope="owned",
        material_kind="source",
        locator="notes.md",
        start_line=1,
        end_line=2,
        reason="Primary source range.",
        actor="worker",
    )
    assert material.ok, material.issues

    reloaded = make_runtime().node.get_current_contract_view(repo_root, node_path="Main.Topic.Consumer")

    assert reloaded.ok, reloaded.issues
    assert reloaded.value is not None
    assert reloaded.value.deps.deps[0].target_node == "Main.Topic.Provider"
    assert reloaded.value.deps.deps[0].expected_decl_names == ["helper"]
    assert reloaded.value.material_refs.owned_refs[0].path == "notes.md"
    assert reloaded.value.material_refs.owned_refs[0].added_by.value == "worker"
