from tests.unit_services_helpers import make_runtime

from pathlib import Path

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.foundation import FoundationContext, FoundationService, ServiceResult, WriteMode
from lean_constellation.services.node import DeclPublicView, NodeContractSnapshot, NodeService


class FakePublicDeclProvider:
    def __init__(self, foundation: FoundationService, decls: dict[str, list[DeclPublicView]]) -> None:
        self.foundation = foundation
        self.decls = decls

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        del repo_root
        return self.foundation.ok(self.decls.get(node_path, []))


def _write_preparation_input(tmp_path: Path, *, interfaces: list[DeclInterface] | None = None) -> None:
    runtime = make_runtime()
    foundation = runtime.foundation
    prep = RepoPreparationInput(
        goal="Formalize the requested source material.",
        source_corpus_mode=SourceCorpusMode.PREPARE,
        source_corpus_relpath=".lean_constellation/source",
        source_description="A source description.",
        interface_inputs=interfaces or [],
    )
    path = foundation.layout.preparation_input_path(FoundationContext(repo_root=tmp_path))
    result = foundation.store.write_json_atomic(path, prep)
    assert result.ok


def test_node_service_ensure_native_root_contract_syncs_protected_interfaces(tmp_path: Path) -> None:
    interface = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Expose the main theorem.")
    _write_preparation_input(tmp_path, interfaces=[interface])
    service = make_runtime().node

    root = service.ensure_native_root_main_contract(tmp_path)

    assert root.ok
    assert root.value is not None
    assert root.value.node_path == "Main"
    assert root.value.contract.goal == "Formalize the requested source material."
    assert "native repository" in root.value.contract.boundary
    assert root.value.contract.interfaces[0].name == "main_result"

    gate = service.check_root_main_handoff_interfaces(tmp_path)
    assert gate.ok
    assert gate.value is not None
    assert gate.value.passed is True


def test_node_service_ensure_adapter_root_contract_uses_adapter_defaults(tmp_path: Path) -> None:
    _write_preparation_input(tmp_path)
    service = make_runtime().node

    root = service.ensure_adapter_root_main_contract(tmp_path)

    assert root.ok
    assert root.value is not None
    assert "adapter repository facade" in root.value.contract.boundary
    assert "upstream Lean declarations" in (root.value.contract.objective or "")


def test_node_service_content_admission_batch_and_commit_wrappers(tmp_path: Path) -> None:
    _write_preparation_input(tmp_path)
    service = make_runtime().node
    assert service.ensure_native_root_main_contract(tmp_path).ok
    assert service.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    content = service.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Core ready.",
    )
    assert content.ok

    admission = service.prepare_content_task_admission(tmp_path, node_path="Main.Topic.Core")
    assert admission.ok
    assert admission.value is not None
    assert admission.value.passed is True

    batch = service.submit_content_node_batch_preflight(tmp_path, node_paths=["Main.Topic.Core"])
    assert batch.ok
    assert batch.value is not None
    assert batch.value.passed is True

    committed = service.commit_content_contract(tmp_path, node_path="Main.Topic.Core", summary="Content contract completed.")
    assert committed.ok
    assert committed.value is not None
    assert committed.value.version_status.value == "committed"


def test_node_service_get_public_boundary_for_content_and_scope(tmp_path: Path) -> None:
    _write_preparation_input(tmp_path)
    runtime = make_runtime()
    foundation = runtime.foundation
    provider = FakePublicDeclProvider(
        foundation,
        {
            "Main.Topic.Core": [
                DeclPublicView(
                    ref=DeclRef(repo=None, node="Main.Topic.Core", name="main_result", revision=1),
                    kind="theorem",
                    summary="Main result.",
                )
            ]
        },
    )
    service = NodeService(runtime, public_decl_provider=provider)
    assert service.ensure_native_root_main_contract(tmp_path).ok
    assert service.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    assert service.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Core ready.",
    ).ok

    content = service.get_node_public_boundary(tmp_path, node_path="Main.Topic.Core")
    assert content.ok
    assert content.value is not None
    assert [decl.ref.name for decl in content.value.public_decls] == ["main_result"]

    path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic", 1)
    loaded = foundation.store.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.exports = [DeclRef(repo=None, node="Main.Topic.Core", name="main_result", revision=1)]
    assert foundation.store.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok

    scope = service.get_node_public_boundary(tmp_path, node_path="Main.Topic")
    assert scope.ok
    assert scope.value is not None
    assert [item.ref.name for item in scope.value.exports] == ["main_result"]
