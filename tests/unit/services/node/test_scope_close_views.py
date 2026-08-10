from pathlib import Path

from tests.unit_services_helpers import initialize_native_test_repo, lean_check_payload, make_runtime

from lean_constellation.domain.interface import DeclKind
from lean_constellation.domain.lean_check import LeanCheck
from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import RepoCompletionMode
from lean_constellation.services import LeanProviderOverrides
from lean_constellation.services.decl_graph import DeclRoundResultKind, DeclState
from lean_constellation.services.decl_graph.models import DeclFormalSection, DeclStatement
from lean_constellation.services.external_clients import ExternalCommandResult
from lean_constellation.services.foundation import ServiceResult, WriteMode
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
    strategy = runtime.decl_graph.ensure_open_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        objective="Create the public result.",
    )
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Create the public result.",
    )
    assert round_record.ok and round_record.value is not None
    created = runtime.decl_graph.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        name="core_result",
        kind="theorem",
        objective="Prove the core result.",
        summary="Core result.",
        public=True,
        target_state=DeclState.DECLARED,
    )
    assert created.ok and created.value is not None
    revision = runtime.decl_graph.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="core_result",
        revision=1,
    )
    assert revision.ok and revision.value is not None
    revision.value.state = DeclState.DECLARED
    revision.value.lean_decl_name = "core_result"
    revision.value.statement = DeclStatement(
        formal=DeclFormalSection(
            code="theorem core_result : True := by trivial",
            check=LeanCheck.model_validate(lean_check_payload()),
        )
    )
    assert runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.revision_path(
            tmp_path,
            node_path="Main.Topic.Core",
            decl_name="core_result",
            revision=1,
        ),
        revision.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    assert runtime.decl_graph.start_round(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
    ).ok
    assert runtime.decl_graph.write_decl_change_summary(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        change_id=created.value.change_id,
        summary="Created the core result.",
    ).ok
    assert runtime.decl_graph.write_round_summary(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        summary="Created the public result.",
    ).ok
    assert runtime.decl_graph.strategy_round.record_round_execution_result(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        result_kind=DeclRoundResultKind.SUCCESS,
    ).ok
    assert runtime.decl_graph.mark_round_terminal(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        result_kind=DeclRoundResultKind.SUCCESS,
    ).ok
    committed = runtime.node.contract._commit_content_contract_with_head(
        tmp_path,
        node_path="Main.Topic.Core",
        summary="Core content ready.",
        decl_graph_head={"core_result": 1},
    )
    assert committed.ok, committed.issues
    assert service.interface.add_interface(
        tmp_path,
        node_path="Main.Topic",
        name="core_iface",
        kind=DeclKind.THEOREM,
        summary="Expose core result.",
        actor="coordinator",
    ).ok
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


def test_scope_close_view_rejects_committed_partial_content_child(
    tmp_path: Path,
) -> None:
    provider = MutablePublicDeclProvider()
    runtime = _runtime_with_provider(provider)
    service = runtime.node
    _create_scope_and_content(service, tmp_path)
    lowered = service.contract.set_task_completion_mode_receipt(
        tmp_path,
        node_path="Main.Topic.Core",
        task_completion_mode=RepoCompletionMode.GRAPH_DECLARED,
    )
    assert lowered.ok, lowered.issues
    assert service.commit_content_contract(
        tmp_path,
        node_path="Main.Topic.Core",
        summary="Declared-only task complete.",
    ).ok

    view = service.get_scope_close_view(tmp_path, scope_path="Main.Topic")

    assert view.ok and view.value is not None
    assert view.value.ready_to_commit is False
    assert view.value.children[0].contract_version_status.value == "committed"
    assert view.value.children[0].ready_for_scope_close is False
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
