from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.foundation import FoundationContext, FoundationService, ServiceResult
from lean_constellation.services.node import DeclPublicView, NodeService


class FakePublicDeclProvider:
    def __init__(self, foundation: FoundationService, decls: dict[str, list[DeclPublicView]]) -> None:
        self.foundation = foundation
        self.decls = decls

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        del repo_root
        return self.foundation.ok(self.decls.get(node_path, []))


def _write_preparation_input(repo_root: Path, foundation: FoundationService) -> None:
    prep = RepoPreparationInput(
        goal="Formalize the real node service metadata smoke source.",
        source_corpus_mode=SourceCorpusMode.PREPARE,
        source_corpus_relpath=".lean_constellation/source",
        source_description="A small local source corpus for node service metadata real tests.",
        interface_inputs=[
            DeclInterface(
                name="main_result",
                kind=DeclKind.THEOREM,
                summary="Expose the repository main result.",
            )
        ],
    )
    path = foundation.layout.preparation_input_path(FoundationContext(repo_root=repo_root))
    result = foundation.store.write_json_atomic(path, prep)
    assert result.ok


def _write_source_corpus_file(repo_root: Path, foundation: FoundationService) -> None:
    source_root = foundation.layout.source_corpus_root(FoundationContext(repo_root=repo_root))
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "notes.md").write_text(
        "\n".join(
            [
                "# Node metadata smoke source",
                "The core result is used by a consumer node.",
                "The topic scope exports the core result.",
            ]
        ),
        encoding="utf-8",
    )


@pytest.mark.real
def test_node_contract_scope_content_metadata_real(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    runtime = make_runtime()
    foundation = runtime.foundation
    core_ref = DeclRef(repo=None, node="Main.Topic.Core", name="core_result", revision=1)
    provider = FakePublicDeclProvider(
        foundation,
        {
            "Main.Topic.Core": [
                DeclPublicView(
                    ref=core_ref,
                    kind=DeclKind.THEOREM.value,
                    summary="Core result exposed by the fake decl provider.",
                    public=True,
                    ready=True,
                    stale=False,
                    source="real-test-provider",
                )
            ]
        },
    )
    service = NodeService(runtime, public_decl_provider=provider)
    runtime.app.node = service
    runtime.lean_projection.node_projection.contract = service.contract
    runtime.lean_projection.node_projection.export = service.export
    _write_preparation_input(repo_root, foundation)
    _write_source_corpus_file(repo_root, foundation)

    root = service.ensure_native_root_main_contract(repo_root)
    assert root.ok, root.issues
    assert root.value is not None
    assert root.value.node_path == "Main"
    assert root.value.contract.interfaces[0].name == "main_result"

    protected_remove = service.interface.remove_interface(
        repo_root,
        node_path="Main",
        name="main_result",
        actor="coordinator",
    )
    assert not protected_remove.ok
    assert protected_remove.issues[0].kind == "protected_interface_remove_forbidden"

    handoff_gate = service.check_root_main_handoff_interfaces(repo_root)
    assert handoff_gate.ok
    assert handoff_gate.value is not None
    assert handoff_gate.value.passed is True

    assert service.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal", boundary="Topic boundary.").ok
    assert service.create_content_node(
        repo_root,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary.",
        objective="Build and expose the core result.",
        success_criteria="The core result is public and ready.",
    ).ok
    assert service.create_content_node(
        repo_root,
        path="Main.Topic.Consumer",
        goal="Consumer goal",
        boundary="Consumer boundary.",
        objective="Use the core result.",
        success_criteria="The consumer task contract is ready.",
    ).ok

    assert service.interface.add_interface(
        repo_root,
        node_path="Main.Topic.Core",
        name="core_result",
        kind=DeclKind.THEOREM,
        summary="Expose the core result.",
        actor="coordinator",
    ).ok
    bound_core = service.interface.bind_interface_to_decl(
        repo_root,
        node_path="Main.Topic.Core",
        interface_name="core_result",
        decl_name="core_result",
    )
    assert bound_core.ok, bound_core.issues

    core_committed = service.commit_content_contract(
        repo_root,
        node_path="Main.Topic.Core",
        summary="Core content contract exposes the core result.",
    )
    assert core_committed.ok, core_committed.issues
    assert core_committed.value is not None
    assert core_committed.value.status.value == "committed"

    add_worker_dep = service.dependency.add_node_dep(
        repo_root,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Core",
        expected_decl_names=["core_result"],
        reason="Consumer uses the core result.",
        actor="worker",
    )
    assert add_worker_dep.ok, add_worker_dep.issues
    listed_worker_deps = service.dependency.list_node_deps(repo_root, node_path="Main.Topic.Consumer")
    assert listed_worker_deps.ok
    assert listed_worker_deps.value is not None
    worker_dep_index = listed_worker_deps.value.deps[0].index

    removed_worker_dep = service.dependency.remove_node_dep(
        repo_root,
        node_path="Main.Topic.Consumer",
        index=worker_dep_index,
        actor="worker",
    )
    assert removed_worker_dep.ok, removed_worker_dep.issues

    add_coordinator_dep = service.dependency.add_node_dep(
        repo_root,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Core",
        expected_decl_names=["core_result"],
        reason="Coordinator requires the core result.",
        actor="coordinator",
    )
    assert add_coordinator_dep.ok, add_coordinator_dep.issues
    listed_coordinator_deps = service.dependency.list_node_deps(repo_root, node_path="Main.Topic.Consumer")
    assert listed_coordinator_deps.ok
    assert listed_coordinator_deps.value is not None
    coordinator_dep_index = listed_coordinator_deps.value.deps[0].index
    dep_gate = service.dependency.validate_node_deps(repo_root, node_path="Main.Topic.Consumer")
    assert dep_gate.ok
    assert dep_gate.value is not None
    assert dep_gate.value.passed is True
    denied_dep_remove = service.dependency.remove_node_dep(
        repo_root,
        node_path="Main.Topic.Consumer",
        index=coordinator_dep_index,
        actor="worker",
    )
    assert not denied_dep_remove.ok
    assert denied_dep_remove.issues[0].kind == "node_dep_permission_denied"

    assert service.material_ref.add_owned_source_ref(
        repo_root,
        node_path="Main.Topic.Consumer",
        path="notes.md",
        start_line=1,
        end_line=2,
        reason="Coordinator supplied source context.",
        actor="coordinator",
    ).ok
    assert service.material_ref.add_context_source_ref(
        repo_root,
        node_path="Main.Topic.Consumer",
        path="notes.md",
        start_line=2,
        end_line=3,
        reason="Worker found useful context.",
        actor="worker",
    ).ok
    refs = service.material_ref.list_node_material_refs(repo_root, node_path="Main.Topic.Consumer")
    assert refs.ok
    assert refs.value is not None
    owned_ref = refs.value.owned_refs[0].ref
    worker_context_ref = refs.value.context_refs[0].ref
    assert owned_ref is not None
    assert worker_context_ref is not None

    denied_ref_remove = service.material_ref.remove_ref(
        repo_root,
        node_path="Main.Topic.Consumer",
        ref=owned_ref,
        actor="worker",
    )
    assert not denied_ref_remove.ok
    assert denied_ref_remove.issues[0].kind == "material_ref_permission_denied"
    removed_worker_ref = service.material_ref.remove_ref(
        repo_root,
        node_path="Main.Topic.Consumer",
        ref=worker_context_ref,
        actor="worker",
    )
    assert removed_worker_ref.ok, removed_worker_ref.issues

    consumer_admission = service.prepare_content_task_admission(repo_root, node_path="Main.Topic.Consumer")
    assert consumer_admission.ok
    assert consumer_admission.value is not None
    assert consumer_admission.value.passed is True

    assert service.interface.add_interface(
        repo_root,
        node_path="Main.Topic",
        name="topic_result",
        kind=DeclKind.THEOREM,
        summary="Expose the topic result through the core result.",
        actor="coordinator",
    ).ok
    exported = service.export.add_scope_export(
        repo_root,
        scope_path="Main.Topic",
        decl_node="Main.Topic.Core",
        decl_name="core_result",
        bind_interface_name="topic_result",
    )
    assert exported.ok, exported.issues
    assert exported.value is not None
    assert [item.ref.name for item in exported.value.exports] == ["core_result"]

    scope_committed = service.commit_scope_contract(
        repo_root,
        scope_path="Main.Topic",
        summary="Topic scope exports the core result.",
    )
    assert scope_committed.ok, scope_committed.issues
    assert scope_committed.value is not None
    assert scope_committed.value.status.value == "committed"

    boundary = service.get_node_public_boundary(repo_root, node_path="Main.Topic")
    assert boundary.ok
    assert boundary.value is not None
    assert boundary.value.exports[0].ref == core_ref
