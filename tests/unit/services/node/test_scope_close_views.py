from pathlib import Path

from tests.unit_services_helpers import initialize_native_test_repo, make_runtime

from lean_constellation.domain.interface import DeclKind
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services import LeanProviderOverrides
from lean_constellation.services.external_clients import ExternalCommandResult
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.node import DeclPublicView, NodeService


class MutablePublicDeclProvider:
    def __init__(self, decls: dict[str, list[DeclPublicView]] | None = None) -> None:
        self.decls = decls or {}

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        del repo_root
        return ServiceResult(ok=True, value=self.decls.get(node_path, []))


def _runtime_with_provider(provider: MutablePublicDeclProvider):
    class FakeLake:
        def run_lake_build(self, repo_root: Path, target: str | None = None, targets=None, timeout_seconds=None):  # noqa: ANN001, ANN201
            del targets, timeout_seconds
            return ExternalCommandResult(ok=True, command=["lake", "build", target or ""], cwd=str(repo_root), exit_code=0, summary="built")

    return make_runtime(
        providers=LeanProviderOverrides(content_public_decl_provider=provider),
        external_overrides={"lake": FakeLake()},
    )


def _create_scope_and_content(service: NodeService, tmp_path: Path, *, content_path: str = "Main.Topic.Core") -> None:
    initialize_native_test_repo(tmp_path, project_name="TestProject")
    assert service.node_tree.ensure_root_scope_node(tmp_path).ok
    assert service.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal.", boundary="Topic boundary.").ok
    assert service.create_content_node(
        tmp_path,
        path=content_path,
        goal=f"{content_path} goal.",
        boundary=f"{content_path} boundary.",
        objective=f"Build {content_path}.",
        success_criteria=f"{content_path} is ready.",
    ).ok


def _public_decl(content_path: str = "Main.Topic.Core", *, ready: bool = True, stale: bool = False) -> DeclPublicView:
    return DeclPublicView(
        ref=DeclRef(repo=None, node=content_path, name="core_result", revision=1),
        kind=DeclKind.THEOREM.value,
        module=f"TestProject.{content_path}.Theorems.core_result",
        summary="Core result.",
        public=True,
        ready=ready,
        stale=stale,
        source="test-provider",
    )


def _prepare_ready_scope(tmp_path: Path):
    provider = MutablePublicDeclProvider({"Main.Topic.Core": [_public_decl()]})
    runtime = _runtime_with_provider(provider)
    service = runtime.node
    _create_scope_and_content(service, tmp_path)
    assert service.interface.add_interface(
        tmp_path,
        node_path="Main.Topic",
        name="core_iface",
        kind=DeclKind.THEOREM,
        summary="Expose core result.",
        actor="coordinator",
    ).ok
    assert service.commit_content_contract(tmp_path, node_path="Main.Topic.Core", summary="Core content ready.").ok
    exported = service.export.add_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        decl_node="Main.Topic.Core",
        decl_name="core_result",
        bind_interface_name="core_iface",
    )
    assert exported.ok, exported.issues
    return service, provider


def test_scope_close_view_all_clear(tmp_path: Path) -> None:
    service, _provider = _prepare_ready_scope(tmp_path)

    view = service.get_scope_close_view(tmp_path, scope_path="Main.Topic")

    assert view.ok
    assert view.value is not None
    assert view.value.ready_to_commit is True
    assert view.value.child_readiness_gate.passed is True
    assert view.value.scope_commit_gate.passed is True
    assert [child.path for child in view.value.children] == ["Main.Topic.Core"]
    assert view.value.exports[0].valid is True
    assert view.value.interfaces.interfaces[0].bound_decl is not None


def test_scope_close_view_reports_uncommitted_content_child(tmp_path: Path) -> None:
    provider = MutablePublicDeclProvider()
    runtime = _runtime_with_provider(provider)
    service = runtime.node
    _create_scope_and_content(service, tmp_path)

    view = service.get_scope_close_view(tmp_path, scope_path="Main.Topic")

    assert view.ok
    assert view.value is not None
    assert view.value.ready_to_commit is False
    assert view.value.children[0].ready_for_scope_close is False
    assert view.value.child_readiness_gate.passed is False
    assert view.value.child_readiness_gate.issues[0].kind == "content_child_not_ready"


def test_scope_close_view_reports_unbound_interface(tmp_path: Path) -> None:
    provider = MutablePublicDeclProvider()
    runtime = _runtime_with_provider(provider)
    service = runtime.node
    assert service.node_tree.ensure_root_scope_node(tmp_path).ok
    assert service.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal.", boundary="Topic boundary.").ok
    assert service.interface.add_interface(
        tmp_path,
        node_path="Main.Topic",
        name="missing_binding",
        kind=DeclKind.THEOREM,
        summary="Unbound interface.",
        actor="coordinator",
    ).ok

    view = service.get_scope_close_view(tmp_path, scope_path="Main.Topic")

    assert view.ok
    assert view.value is not None
    assert view.value.scope_commit_gate.passed is False
    assert any(issue.kind == "interface_unbound" for issue in view.value.scope_commit_gate.issues)


def test_scope_close_view_reports_stale_export(tmp_path: Path) -> None:
    service, provider = _prepare_ready_scope(tmp_path)
    provider.decls["Main.Topic.Core"] = [_public_decl(stale=True)]

    view = service.get_scope_close_view(tmp_path, scope_path="Main.Topic")

    assert view.ok
    assert view.value is not None
    assert view.value.ready_to_commit is False
    assert view.value.exports[0].valid is False
    assert any(issue.kind == "scope_export_decl_not_ready" for issue in view.value.scope_commit_gate.issues)


def test_scope_close_view_orders_direct_children(tmp_path: Path) -> None:
    provider = MutablePublicDeclProvider()
    runtime = _runtime_with_provider(provider)
    service = runtime.node
    assert service.node_tree.ensure_root_scope_node(tmp_path).ok
    assert service.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal.", boundary="Topic boundary.").ok
    assert service.create_content_node(
        tmp_path,
        path="Main.Topic.B",
        goal="B goal.",
        boundary="B boundary.",
        objective="Build B.",
        success_criteria="B ready.",
    ).ok
    assert service.create_content_node(
        tmp_path,
        path="Main.Topic.A",
        goal="A goal.",
        boundary="A boundary.",
        objective="Build A.",
        success_criteria="A ready.",
    ).ok

    view = service.get_scope_close_view(tmp_path, scope_path="Main.Topic")

    assert view.ok
    assert view.value is not None
    assert [child.path for child in view.value.children] == ["Main.Topic.A", "Main.Topic.B"]
    assert [issue.message.rsplit(": ", 1)[-1] for issue in view.value.child_readiness_gate.issues] == ["Main.Topic.A", "Main.Topic.B"]
