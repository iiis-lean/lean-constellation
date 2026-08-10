from pathlib import Path

from tests.unit_services_helpers import (
    initialize_native_test_repo,
    lean_check_payload,
    make_runtime,
    publish_adapter_provider_release,
    publish_native_provider_release,
)

from lean_constellation.domain.interface import DeclInterface
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services import LeanProviderOverrides
from lean_constellation.services.decl_graph.models import (
    Decl,
    DeclFormalSection,
    DeclProof,
    DeclRevision,
    DeclRevisionStatus,
    DeclState,
    DeclStatement,
)
from lean_constellation.services.foundation import FoundationService, ServiceResult, WriteMode
from lean_constellation.services.node import DeclPublicView


class FakePublicDeclProvider:
    def __init__(self, foundation: FoundationService, decls: dict[tuple[str, str], list[DeclPublicView]]) -> None:
        self.foundation = foundation
        self.decls = decls

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        return self.foundation.ok(self.decls.get((str(Path(repo_root)), node_path), []))


def _runtime_with_public_decls(decls: dict[tuple[str, str], list[DeclPublicView]]):
    base = make_runtime()
    return make_runtime(providers=LeanProviderOverrides(content_public_decl_provider=FakePublicDeclProvider(base.foundation, decls)))


def _create_consumer_tree(repo_root: Path) -> None:
    service = make_runtime().node
    assert service.node_tree.ensure_root_scope_node(repo_root).ok
    assert service.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal.", boundary="Topic boundary.").ok
    assert service.create_content_node(
        repo_root,
        path="Main.Topic.Consumer",
        goal="Consumer goal.",
        boundary="Consumer boundary.",
        objective="Use visible public declarations.",
        success_criteria="Consumer is ready.",
    ).ok
    assert service.create_content_node(
        repo_root,
        path="Main.Topic.Hidden",
        goal="Hidden goal.",
        boundary="Hidden boundary.",
        objective="Build hidden content.",
        success_criteria="Hidden is ready.",
    ).ok


def _create_provider_repo(
    provider_root: Path,
    *,
    provider_name: str = "Provider",
    interface_name: str | None = None,
    decl_kind: str = "theorem",
) -> None:
    initialize_native_test_repo(provider_root, project_name=provider_name)
    runtime = make_runtime()
    service = runtime.node
    if interface_name is not None:
        assert runtime.repo_workspace.metadata.ensure_repo_model(provider_root).ok
        assert runtime.repo_workspace.preparation.write_preparation_input(
            provider_root,
            input=RepoPreparationInput(
                goal="Provide a released theorem interface.",
                source_corpus_mode=SourceCorpusMode.PREPARE,
                interface_inputs=[
                    DeclInterface(
                        name=interface_name,
                        kind="theorem",
                        summary="Provider theorem interface.",
                    )
                ],
            ),
        ).ok
    assert service.node_tree.ensure_root_scope_node(provider_root).ok
    if interface_name is not None:
        assert runtime.node.interface.sync_protected_root_interfaces_from_preparation_input(
            provider_root
        ).ok
    assert service.create_content_node(
        provider_root,
        path="Main.Core",
        goal="Provider core goal.",
        boundary="Provider core boundary.",
        objective="Expose provider declarations.",
        success_criteria="Provider core is ready.",
    ).ok
    assert runtime.decl_graph.ensure_decl_graph(provider_root, node_path="Main.Core").ok
    decl_record = Decl(
        name="provider_result",
        node_path="Main.Core",
        kind=decl_kind,
        public=True,
        current_revision=1,
        revision_ids=[1],
        module=f"{provider_name}.Main.Core.Theorems.provider_result",
    )
    revision = DeclRevision(
        revision=1,
        lean_decl_name=f"{provider_name}.provider_result",
        state=DeclState.PROVED,
        status=DeclRevisionStatus.COMMITTED,
        statement=DeclStatement(
            formal=DeclFormalSection(
                code="import Mathlib\n\ntheorem provider_result : True := by\n  sorry\n",
                check=lean_check_payload(contains_sorry=True),
            )
        ),
        proof=DeclProof(
            formal=DeclFormalSection(
                code="theorem provider_result : True := by\n  trivial\n",
                check=lean_check_payload(),
            )
        ),
    )
    assert runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.decl_record_path(
            provider_root,
            node_path="Main.Core",
            decl_name="provider_result",
        ),
        decl_record,
        mode=WriteMode.OVERWRITE,
    ).ok
    assert runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.revision_path(
            provider_root,
            node_path="Main.Core",
            decl_name="provider_result",
            revision=1,
        ),
        revision,
        mode=WriteMode.OVERWRITE,
    ).ok
    committed = runtime.node.contract._commit_content_contract_with_head(
        provider_root,
        node_path="Main.Core",
        summary="Commit the provider Content boundary.",
        decl_graph_head={"provider_result": 1},
    )
    assert committed.ok, committed.issues
    assert runtime.node.export.add_scope_export(provider_root, scope_path="Main", decl_node="Main.Core", decl_name="provider_result").ok
    if interface_name is not None:
        assert runtime.node.interface.bind_interface_to_decl(
            provider_root,
            node_path="Main",
            interface_name=interface_name,
            decl_name="provider_result",
            decl_node="Main.Core",
        ).ok
    publish_native_provider_release(runtime, provider_root, summary=f"{provider_name} stable.")


def test_coordinator_can_list_current_repo_nodes(tmp_path: Path) -> None:
    _create_consumer_tree(tmp_path)

    visible = make_runtime().node.public_decl_access.list_visible_nodes(
        tmp_path,
        actor_role="coordinator",
    )

    assert visible.ok and visible.value is not None
    assert [item.node_path for item in visible.value.nodes] == ["Main", "Main.Topic", "Main.Topic.Consumer", "Main.Topic.Hidden"]


def test_node_scoped_actor_can_only_read_current_or_visible_node_public_decls(tmp_path: Path) -> None:
    _create_consumer_tree(tmp_path)
    runtime = _runtime_with_public_decls(
        {
            (str(tmp_path), "Main.Topic.Consumer"): [
                DeclPublicView(
                    ref=DeclRef(repo=None, node="Main.Topic.Consumer", name="consumer_result", revision=1),
                    kind="theorem",
                    summary="Consumer result.",
                )
            ],
            (str(tmp_path), "Main.Topic.Hidden"): [
                DeclPublicView(
                    ref=DeclRef(repo=None, node="Main.Topic.Hidden", name="hidden_result", revision=1),
                    kind="theorem",
                    summary="Hidden result.",
                )
            ],
        }
    )

    current = runtime.node.public_decl_access.list_node_public_decls(
        tmp_path,
        node_path="Main.Topic.Consumer",
        actor_role="plan",
        current_node_path="Main.Topic.Consumer",
    )
    hidden = runtime.node.public_decl_access.list_node_public_decls(
        tmp_path,
        node_path="Main.Topic.Hidden",
        actor_role="plan",
        current_node_path="Main.Topic.Consumer",
    )

    assert current.ok and current.value is not None
    assert [decl.ref.name for decl in current.value] == ["consumer_result"]
    assert not hidden.ok
    assert hidden.issues[0].kind == "node_public_decl_not_visible"


def test_coordinator_reads_stable_provider_repo_main_exports(tmp_path: Path) -> None:
    workspace = tmp_path
    consumer = workspace / "Consumer"
    provider = workspace / "Provider"
    consumer.mkdir()
    provider.mkdir()
    _create_consumer_tree(consumer)
    _create_provider_repo(provider)

    runtime = make_runtime()
    imported = runtime.node.public_decl_access.list_imported_repos(
        consumer,
        actor_role="coordinator",
    )
    public = runtime.node.public_decl_access.list_repo_public_decls(
        consumer,
        repo_key="Provider",
        actor_role="coordinator",
    )

    assert imported.ok and imported.value is not None
    assert [item.repo_key for item in imported.value.repos] == ["Provider"]
    assert imported.value.repos[0].source == "workspace_stable_provider"
    assert "publishes graph_proved" in imported.value.repos[0].summary
    assert public.ok and public.value is not None
    assert [(decl.ref.repo, decl.ref.node, decl.ref.name) for decl in public.value] == [
        ("Provider", "Main.Core", "provider_result")
    ]


def test_stable_native_provider_public_decls_cache_by_release_but_recheck_visibility(
    tmp_path: Path,
    monkeypatch,
) -> None:
    consumer = tmp_path / "Consumer"
    provider = tmp_path / "Provider"
    consumer.mkdir()
    provider.mkdir()
    _create_consumer_tree(consumer)
    _create_provider_repo(provider)
    resolver = make_runtime().node.public_decl_access
    visibility_calls = 0
    parse_calls = 0
    original_visible = resolver.assert_repo_visible
    original_list = resolver.runtime.decl_graph.ref_compatibility.list_public_decl_refs

    def counted_visible(*args, **kwargs):
        nonlocal visibility_calls
        visibility_calls += 1
        return original_visible(*args, **kwargs)

    def counted_list(*args, **kwargs):
        nonlocal parse_calls
        parse_calls += 1
        return original_list(*args, **kwargs)

    monkeypatch.setattr(resolver, "assert_repo_visible", counted_visible)
    monkeypatch.setattr(
        resolver.runtime.decl_graph.ref_compatibility,
        "list_public_decl_refs",
        counted_list,
    )

    first = resolver.list_repo_public_decls(
        consumer,
        repo_key="Provider",
        actor_role="coordinator",
    )
    second = resolver.list_repo_public_decls(
        consumer,
        repo_key="Provider",
        actor_role="coordinator",
    )

    assert first.ok and second.ok
    assert first.value == second.value
    assert visibility_calls == 2
    assert parse_calls == 1


def test_node_scoped_actor_only_sees_attached_available_provider_boundary(tmp_path: Path) -> None:
    consumer = tmp_path / "Consumer"
    provider = tmp_path / "Provider"
    consumer.mkdir()
    provider.mkdir()
    _create_consumer_tree(consumer)
    _create_provider_repo(provider)
    runtime = make_runtime()

    unattached = runtime.node.public_decl_access.list_imported_repos(
        consumer,
        actor_role="plan",
        current_node_path="Main.Topic.Consumer",
    )
    denied = runtime.node.public_decl_access.list_repo_public_decls(
        consumer,
        repo_key="Provider",
        actor_role="plan",
        current_node_path="Main.Topic.Consumer",
    )
    assert unattached.ok and unattached.value is not None and unattached.value.repos == []
    assert not denied.ok and denied.issues[0].kind == "repo_public_decl_not_visible"

    (consumer / "lakefile.toml").write_text(
        'name = "Consumer"\n\n[[require]]\nname = "Provider"\npath = "../Provider"\n',
        encoding="utf-8",
    )
    attached = runtime.node.public_decl_access.list_imported_repos(
        consumer,
        actor_role="plan",
        current_node_path="Main.Topic.Consumer",
    )
    public = runtime.node.public_decl_access.list_repo_public_decls(
        consumer,
        repo_key="Provider",
        actor_role="plan",
        current_node_path="Main.Topic.Consumer",
    )

    assert attached.ok and attached.value is not None
    assert [item.repo_key for item in attached.value.repos] == ["Provider"]
    assert public.ok and public.value is not None
    assert [(item.ref.repo, item.ref.name) for item in public.value] == [("Provider", "provider_result")]


def test_coordinator_reads_all_ready_adapter_main_exports(tmp_path: Path) -> None:
    from lean_constellation.domain.interface import DeclInterface, DeclKind
    from tests.unit.services.adapter.test_adapter_service import _finalize_theorem, _service

    consumer = tmp_path / "Consumer"
    provider = tmp_path / "AdapterProvider"
    consumer.mkdir()
    _create_consumer_tree(consumer)
    service = _service(
        provider,
        interfaces=[DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Public theorem.")],
    )
    assert service.runtime.node.interface.sync_protected_root_interfaces_from_preparation_input(provider).ok
    _finalize_theorem(service, provider)
    _finalize_theorem(
        service,
        provider,
        name="supporting_result",
        module="Upstream.Basic",
    )
    assert service.bind_adapter_interface(
        provider,
        interface_name="main_result",
        decl_name="main_result",
        binding_summary="Expose the public theorem.",
    ).ok
    assert service.sync_adapter_public_exports(provider).ok
    assert service.refresh_adapter_projection(provider).ok
    publish_adapter_provider_release(
        service.runtime,
        provider,
        summary="Stable adapter provider.",
    )

    public = make_runtime().node.public_decl_access.list_repo_public_decls(
        consumer,
        repo_key="AdapterProvider",
        actor_role="coordinator",
    )

    assert public.ok and public.value is not None
    assert [item.ref for item in public.value] == [
        DeclRef(repo="AdapterProvider", node="Main", name="main_result", revision=1),
        DeclRef(repo="AdapterProvider", node="Main", name="supporting_result", revision=1),
    ]
    assert all(item.resolved_revision == 1 for item in public.value)
    assert all(item.ready for item in public.value)
