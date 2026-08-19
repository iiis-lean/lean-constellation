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
from lean_constellation.services.foundation import FoundationContext, ServiceResult, WriteMode
from lean_constellation.services.node import DeclPublicView, NodeContractSnapshot, NodeService


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


def test_scope_close_view_reuses_content_boundary_across_all_gates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, _provider = _prepare_ready_scope(tmp_path)
    interfaces_path = service.runtime.lean_projection.node_projection._interfaces_path(
        tmp_path,
        "Main.Topic",
    )
    before_interfaces = interfaces_path.read_bytes()
    original = service.export.list_committed_content_public_decls
    original_identity = service.interface.check_bound_interface_lean_identities
    original_closure = service.public_statement_closure.check_scope
    original_refresh = (
        service.runtime.lean_projection.node_projection.refresh_interfaces
    )
    original_sync = (
        service.runtime.lean_projection.node_projection.check_interfaces_sync
    )
    calls: list[str] = []
    gates = {"identity": 0, "closure": 0, "refresh": 0, "sync": 0}

    def counted_boundary(repo_root: Path, *, node_path: str):
        calls.append(node_path)
        return original(repo_root, node_path=node_path)

    def counted_identity(*args, **kwargs):
        gates["identity"] += 1
        return original_identity(*args, **kwargs)

    def counted_closure(*args, **kwargs):
        gates["closure"] += 1
        return original_closure(*args, **kwargs)

    def counted_refresh(*args, **kwargs):
        gates["refresh"] += 1
        return original_refresh(*args, **kwargs)

    def counted_sync(*args, **kwargs):
        gates["sync"] += 1
        return original_sync(*args, **kwargs)

    monkeypatch.setattr(
        service.export,
        "list_committed_content_public_decls",
        counted_boundary,
    )
    monkeypatch.setattr(
        service.interface,
        "check_bound_interface_lean_identities",
        counted_identity,
    )
    monkeypatch.setattr(
        service.public_statement_closure,
        "check_scope",
        counted_closure,
    )
    monkeypatch.setattr(
        service.runtime.lean_projection.node_projection,
        "refresh_interfaces",
        counted_refresh,
    )
    monkeypatch.setattr(
        service.runtime.lean_projection.node_projection,
        "check_interfaces_sync",
        counted_sync,
    )

    view = service.get_scope_close_view(tmp_path, scope_path="Main.Topic")

    assert view.ok and view.value is not None
    assert view.value.ready_to_commit is True
    assert calls == ["Main.Topic.Core"]
    assert gates == {"identity": 1, "closure": 1, "refresh": 1, "sync": 1}
    assert interfaces_path.read_bytes() == before_interfaces


def test_scope_commit_reuses_one_local_content_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, _provider = _prepare_ready_scope(tmp_path)
    contract_path = service.runtime.foundation.node_contract_path(
        FoundationContext(repo_root=tmp_path),
        "Main.Topic",
        1,
    )
    loaded = service.runtime.foundation.read_json(
        contract_path,
        NodeContractSnapshot,
    )
    assert loaded.ok and loaded.value is not None
    loaded.value.exports.append(loaded.value.exports[0])
    assert service.runtime.foundation.write_json_atomic(
        contract_path,
        loaded.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    original = service.export.list_committed_content_public_decls
    calls: list[str] = []

    def counted_boundary(repo_root: Path, *, node_path: str):
        calls.append(node_path)
        return original(repo_root, node_path=node_path)

    monkeypatch.setattr(
        service.export,
        "list_committed_content_public_decls",
        counted_boundary,
    )

    committed = service.commit_scope_contract(
        tmp_path,
        scope_path="Main.Topic",
        summary="Commit the ready Scope.",
    )

    assert not committed.ok
    assert committed.issues[0].kind == "scope_export_duplicate"
    assert calls == ["Main.Topic.Core"]


def test_direct_validation_scope_commit_reuses_one_local_content_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, _provider = _prepare_ready_scope(tmp_path)
    original = service.export.list_committed_content_public_decls
    calls: list[str] = []

    def counted_boundary(repo_root: Path, *, node_path: str):
        calls.append(node_path)
        return original(repo_root, node_path=node_path)

    monkeypatch.setattr(
        service.export,
        "list_committed_content_public_decls",
        counted_boundary,
    )

    gate = service.runtime.validation_snapshot.check_scope_commit(
        tmp_path,
        scope_path="Main.Topic",
        summary="Check the ready Scope.",
    )

    assert gate.ok and gate.value is not None and gate.value.passed
    assert calls == ["Main.Topic.Core"]


def test_direct_validation_scope_commit_rejects_wrong_context_before_reads(
    tmp_path: Path,
) -> None:
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    provider = MutablePublicDeclProvider()
    runtime = _runtime_with_provider(provider)
    service = runtime.node
    _create_scope_and_content(service, repo_a)
    _create_scope_and_content(service, repo_b)
    operation = service.export.create_scope_export_operation_context(
        repo_a,
        scope_path="Main.Topic",
    )
    assert operation.ok and operation.value is not None
    before_a = {
        path.relative_to(repo_a).as_posix(): path.read_bytes()
        for path in repo_a.rglob("*")
        if path.is_file()
    }
    before_b = {
        path.relative_to(repo_b).as_posix(): path.read_bytes()
        for path in repo_b.rglob("*")
        if path.is_file()
    }

    wrong_repo = runtime.validation_snapshot.check_scope_commit(
        repo_b,
        scope_path="Main.Topic",
        summary="Check the Scope.",
        scope_export_context=operation.value,
    )
    wrong_scope = runtime.validation_snapshot.check_scope_commit(
        repo_a,
        scope_path="Main.Missing",
        summary="Check the Scope.",
        scope_export_context=operation.value,
    )

    assert not wrong_repo.ok
    assert wrong_repo.issues[0].kind == "scope_export_operation_context_mismatch"
    assert not wrong_scope.ok
    assert wrong_scope.issues[0].kind == "scope_export_operation_context_mismatch"
    assert {
        path.relative_to(repo_a).as_posix(): path.read_bytes()
        for path in repo_a.rglob("*")
        if path.is_file()
    } == before_a
    assert {
        path.relative_to(repo_b).as_posix(): path.read_bytes()
        for path in repo_b.rglob("*")
        if path.is_file()
    } == before_b


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
