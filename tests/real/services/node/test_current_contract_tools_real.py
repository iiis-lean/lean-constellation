from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.refs import DeclRef
from lean_constellation.services import LeanProviderOverrides
from lean_constellation.services.foundation import FoundationService, ServiceResult
from lean_constellation.services.node import DeclPublicView, NodeService


class FakePublicDeclProvider:
    def __init__(self, foundation: FoundationService, decls: dict[tuple[str, str], list[DeclPublicView]]) -> None:
        self.foundation = foundation
        self.decls = decls

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        return self.foundation.ok(self.decls.get((str(Path(repo_root)), node_path), []))


def _runtime_with_public_decls(decls: dict[tuple[str, str], list[DeclPublicView]]):
    base = make_runtime()
    return make_runtime(providers=LeanProviderOverrides(content_public_decl_provider=FakePublicDeclProvider(base.foundation, decls)))


def _create_tree(repo_root: Path, service: NodeService) -> None:
    assert service.node_tree.ensure_root_scope_node(repo_root).ok
    assert service.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal.", boundary="Topic boundary.").ok
    assert service.create_scope_node(repo_root, path="Main.Topic.Provider", goal="Provider goal.", boundary="Provider boundary.").ok
    assert service.create_content_node(
        repo_root,
        path="Main.Topic.Provider.Core",
        goal="Provider core goal.",
        boundary="Provider core boundary.",
        objective="Expose helper.",
        success_criteria="Provider exports helper.",
    ).ok
    assert service.create_content_node(
        repo_root,
        path="Main.Topic.Consumer",
        goal="Consumer goal.",
        boundary="Consumer boundary.",
        objective="Use provider and source material.",
        success_criteria="Consumer contract has the required refs.",
    ).ok


def _commit_provider(repo_root: Path, service: NodeService) -> None:
    added = service.export.add_scope_export(
        repo_root,
        scope_path="Main.Topic.Provider",
        decl_node="Main.Topic.Provider.Core",
        decl_name="helper",
    )
    assert added.ok, added.issues
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
    runtime = _runtime_with_public_decls(
        {
            (str(repo_root), "Main.Topic.Provider.Core"): [
                DeclPublicView(
                    ref=DeclRef(repo=None, node="Main.Topic.Provider.Core", name="helper", revision=1),
                    kind="theorem",
                    summary="Provider helper.",
                    ready=True,
                    stale=False,
                    source="test_provider",
                )
            ]
        }
    )
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
