from __future__ import annotations

from pathlib import Path

from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import RepoCompletionMode, RepoPublicationState, RepoPublicationStatus
from lean_constellation.domain.repo_release import RepoRelease
from lean_constellation.services.decl_graph.models import (
    Decl,
    DeclFormalSection,
    DeclProof,
    DeclRevision,
    DeclRevisionStatus,
    DeclState,
    DeclStatement,
    RepoDeclDep,
)
from lean_constellation.services.foundation import FoundationContext, WriteMode
from lean_constellation.services.node import NodeContractStatus
from tests.unit_services_helpers import lean_check_payload, make_runtime, publish_native_provider_release


def _write_decl(
    repo_root: Path,
    *,
    node_path: str,
    name: str,
    revision: int = 1,
    kind: str = "theorem",
    state: DeclState = DeclState.PROVED,
    statement_deps=(),
    proof_deps=(),
) -> None:
    runtime = make_runtime()
    assert runtime.decl_graph.ensure_decl_graph(repo_root, node_path=node_path).ok
    decl = Decl(
        name=name,
        node_path=node_path,
        kind=kind,
        public=True,
        current_revision=revision,
        revision_ids=list(range(1, revision + 1)),
        module=f"{node_path}.Theorems.{name}",
    )
    revision_value = DeclRevision(
        revision=revision,
        lean_decl_name=f"TestProject.{name}",
        state=state,
        status=DeclRevisionStatus.COMMITTED,
        statement=DeclStatement(
            formal=DeclFormalSection(
                code=f"import Mathlib\n\ntheorem {name} : True := by\n  sorry\n",
                    check=lean_check_payload(contains_sorry=True),
            ),
            deps=[RepoDeclDep(ref=ref) for ref in statement_deps],
        ),
        proof=DeclProof(
            formal=DeclFormalSection(
                code=f"theorem {name} : True := by\n  trivial\n",
                    check=lean_check_payload(),
            ),
            deps=[RepoDeclDep(ref=ref) for ref in proof_deps],
        ),
    )
    assert runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.decl_record_path(repo_root, node_path=node_path, decl_name=name),
        decl,
        mode=WriteMode.OVERWRITE,
    ).ok
    assert runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.revision_path(repo_root, node_path=node_path, decl_name=name, revision=revision),
        revision_value,
        mode=WriteMode.OVERWRITE,
    ).ok


def _prepare_release_repo(repo_root: Path):
    runtime = make_runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.node_tree.create_scope_node(repo_root, path="Main.Foundation", goal="Foundation", boundary="Foundation").ok
    assert runtime.node.node_tree.create_content_node(
        repo_root,
        path="Main.Foundation.Defs",
        goal="Definitions",
        boundary="Definitions",
        objective="Define support.",
        success_criteria="Support exists.",
    ).ok
    assert runtime.node.node_tree.create_content_node(
        repo_root,
        path="Main.Results",
        goal="Results",
        boundary="Results",
        objective="Prove result.",
        success_criteria="Result exists.",
    ).ok
    support_ref = DeclRef(node="Main.Foundation.Defs", name="Support", revision=1)
    proof_only_ref = DeclRef(node="Main.Foundation.Defs", name="ProofHelper", revision=1)
    _write_decl(repo_root, node_path="Main.Foundation.Defs", name="Support")
    _write_decl(repo_root, node_path="Main.Foundation.Defs", name="ProofHelper")
    _write_decl(
        repo_root,
        node_path="Main.Results",
        name="PublicResult",
        statement_deps=(support_ref,),
        proof_deps=(proof_only_ref,),
    )
    versions: dict[str, int] = {}
    for node in runtime.node.node_tree.get_node_tree(repo_root).value.nodes:
        loaded = runtime.node.contract.get_current_contract(repo_root, node_path=node.path)
        assert loaded.ok and loaded.value is not None
        contract = loaded.value.contract
        contract.status = NodeContractStatus.COMMITTED
        contract.committed_at = "2026-07-12T00:00:00Z"
        if node.path == "Main":
            contract.exports = [DeclRef(node="Main.Results", name="PublicResult", revision=1)]
        elif node.path == "Main.Foundation.Defs":
            contract.decl_graph_head = {"Support": 1, "ProofHelper": 1}
        elif node.path == "Main.Results":
            contract.decl_graph_head = {"PublicResult": 1}
        assert runtime.foundation.store.write_json_atomic(
            runtime.node.node_tree.node_store.contract_path(repo_root, node_id=node.node_id, version=contract.version),
            contract,
            mode=WriteMode.UPDATE_EXISTING,
        ).ok
        metadata = runtime.node.node_tree.node_store.load_node_by_id(repo_root, node_id=node.node_id).value
        metadata.active_contract_version = contract.version
        metadata.current_contract_version = contract.version
        metadata.open_contract_version = None
        assert runtime.node.node_tree.node_store.save_node(repo_root, metadata, mode=WriteMode.UPDATE_EXISTING).ok
        versions[node.node_id] = contract.version
    return runtime, versions


def _release(
    release_id: str,
    versions: dict[str, int],
    *,
    parent: str | None = None,
    completion_mode: RepoCompletionMode = RepoCompletionMode.GRAPH_DECLARED,
) -> RepoRelease:
    return RepoRelease(
        release_id=release_id,
        parent_release_id=parent,
        node_contract_versions=versions,
        completion_mode=completion_mode,
        repo_checkpoint_id=f"checkpoint_{release_id}",
        summary=f"Release {release_id}.",
    )


def test_release_store_is_immutable_and_lineage_is_oldest_first(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    assert runtime.repo_workspace.release.create_release(tmp_path, release=_release("r1", versions)).ok
    assert runtime.repo_workspace.release.create_release(tmp_path, release=_release("r2", versions, parent="r1")).ok

    duplicate = runtime.repo_workspace.release.create_release(tmp_path, release=_release("r1", versions))
    missing_parent = runtime.repo_workspace.release.create_release(tmp_path, release=_release("r3", versions, parent="missing"))
    lineage = runtime.repo_workspace.release.resolve_release_lineage(tmp_path, release_id="r2")

    assert not duplicate.ok and duplicate.issues[0].kind == "release_exists"
    assert not missing_parent.ok and missing_parent.issues[0].kind == "release_parent_missing"
    assert lineage.ok and [item.release_id for item in lineage.value] == ["r1", "r2"]
    assert [item.release.release_id for item in runtime.repo_workspace.release.list_releases(tmp_path).value] == ["r1", "r2"]


def test_release_baseline_protects_cross_node_statement_closure_but_not_proof_deps(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    assert runtime.repo_workspace.release.create_release(tmp_path, release=_release("r1", versions)).ok

    baseline = runtime.repo_workspace.release.resolve_release_baseline(tmp_path, release_id="r1")

    assert baseline.ok and baseline.value is not None
    protected = {(item.node_path, item.decl_name) for item in baseline.value.protected_decl_views}
    assert protected == {("Main.Foundation.Defs", "Support"), ("Main.Results", "PublicResult")}
    assert ("Main.Foundation.Defs", "ProofHelper") not in protected
    assert baseline.value.protected_scope_paths == ["Main", "Main.Foundation"]

    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(tmp_path),
        RepoPublicationState(status=RepoPublicationStatus.STABLE, latest_release_id="r1"),
        mode=WriteMode.OVERWRITE,
    ).ok
    protected_status = runtime.repo_workspace.release.get_decl_release_status(
        tmp_path, node_path="Main.Foundation.Defs", decl_name="Support"
    )
    private_status = runtime.repo_workspace.release.get_decl_release_status(
        tmp_path, node_path="Main.Foundation.Defs", decl_name="ProofHelper"
    )
    assert protected_status.ok and protected_status.value.release_protected is True
    assert protected_status.value.released_state == "proved"
    assert private_status.ok and private_status.value.release_protected is False
    assert private_status.value.released_state == "proved"


def test_release_lineage_cycle_is_rejected_on_read(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    first = _release("r1", versions)
    second = _release("r2", versions, parent="r1")
    assert runtime.repo_workspace.release.create_release(tmp_path, release=first).ok
    assert runtime.repo_workspace.release.create_release(tmp_path, release=second).ok
    path = runtime.foundation.layout.release_path(FoundationContext(repo_root=tmp_path), "r1")
    assert runtime.foundation.store.write_json_atomic(
        path,
        first.model_copy(update={"parent_release_id": "r2"}),
        mode=WriteMode.UPDATE_EXISTING,
    ).ok

    result = runtime.repo_workspace.release.resolve_release_lineage(tmp_path, release_id="r2")

    assert not result.ok and result.issues[0].kind == "release_lineage_cycle"


def test_release_creation_rejects_missing_exact_contract(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    broken = dict(versions)
    content_node_id = next(
        node.node_id
        for node in runtime.node.node_tree.get_node_tree(tmp_path).value.nodes
        if node.path == "Main.Results"
    )
    broken[content_node_id] = 99

    result = runtime.repo_workspace.release.create_release(tmp_path, release=_release("broken", broken))

    assert not result.ok and result.issues[0].kind == "release_contract_missing"


def test_release_completion_cannot_regress_from_proved_graph_to_declared_graph(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    assert runtime.repo_workspace.release.create_release(
        tmp_path,
        release=_release("r1", versions, completion_mode=RepoCompletionMode.GRAPH_PROVED),
    ).ok

    result = runtime.repo_workspace.release.create_release(
        tmp_path,
        release=_release("r2", versions, parent="r1"),
    )

    assert not result.ok
    assert result.issues[0].kind == "release_parent_completion_regression"


def test_release_lineage_read_rejects_corrupt_completion_regression(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    first = _release("r1", versions, completion_mode=RepoCompletionMode.GRAPH_PROVED)
    second = _release("r2", versions, parent="r1", completion_mode=RepoCompletionMode.GRAPH_PROVED)
    assert runtime.repo_workspace.release.create_release(tmp_path, release=first).ok
    assert runtime.repo_workspace.release.create_release(tmp_path, release=second).ok
    path = runtime.foundation.layout.release_path(FoundationContext(repo_root=tmp_path), "r2")
    assert runtime.foundation.store.write_json_atomic(
        path,
        second.model_copy(update={"completion_mode": RepoCompletionMode.GRAPH_DECLARED}),
        mode=WriteMode.UPDATE_EXISTING,
    ).ok

    result = runtime.repo_workspace.release.resolve_release_lineage(tmp_path, release_id="r2")

    assert not result.ok
    assert result.issues[0].kind == "release_parent_completion_regression"


def _set_contract_exports(runtime, repo_root: Path, *, node_path: str, exports: list[DeclRef]) -> None:
    current = runtime.node.contract.get_visible_contract(repo_root, node_path=node_path)
    assert current.ok and current.value is not None
    current.value.contract.exports = exports
    path = runtime.node.node_tree.node_store.contract_path(
        repo_root,
        node_id=current.value.node_id,
        version=current.value.contract.version,
    )
    assert runtime.foundation.store.write_json_atomic(
        path,
        current.value.contract,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok


def _add_external_statement_dep(runtime, repo_root: Path, ref: DeclRef) -> None:
    revision = runtime.decl_graph.get_decl_revision(
        repo_root, node_path="Main.Results", name="PublicResult", revision=1
    ).value
    revision.statement.deps.append(RepoDeclDep(ref=ref))
    path = runtime.decl_graph.graph_store.revision_path(
        repo_root, node_path="Main.Results", decl_name="PublicResult", revision=1
    )
    assert runtime.foundation.store.write_json_atomic(
        path, revision, mode=WriteMode.UPDATE_EXISTING
    ).ok


def _prepare_native_provider(workspace: Path, *, exported: bool = True):
    provider_root = workspace / "Provider"
    runtime, _ = _prepare_release_repo(provider_root)
    if not exported:
        _set_contract_exports(runtime, provider_root, node_path="Main", exports=[])
    assert runtime.repo_workspace.metadata.set_repo_format(
        provider_root,
        repo_format="native",
        reason="release dependency fixture",
    ).ok
    publish_native_provider_release(runtime, provider_root, release_id="provider_r1")
    return runtime, provider_root


def test_release_baseline_requires_exact_intermediate_scope_export_chain(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    support = DeclRef(node="Main.Foundation.Defs", name="Support", revision=1)
    _set_contract_exports(runtime, tmp_path, node_path="Main", exports=[support])
    _set_contract_exports(runtime, tmp_path, node_path="Main.Foundation", exports=[support])
    assert runtime.repo_workspace.release.create_release(tmp_path, release=_release("r1", versions)).ok

    result = runtime.repo_workspace.release.resolve_release_baseline(tmp_path, release_id="r1")

    assert result.ok and result.value is not None
    assert {(item.node_path, item.decl_name) for item in result.value.protected_decl_views} == {
        ("Main.Foundation.Defs", "Support")
    }


def test_release_baseline_rejects_missing_intermediate_scope_export(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    support = DeclRef(node="Main.Foundation.Defs", name="Support", revision=1)
    _set_contract_exports(runtime, tmp_path, node_path="Main", exports=[support])
    assert runtime.repo_workspace.release.create_release(tmp_path, release=_release("r1", versions)).ok

    result = runtime.repo_workspace.release.resolve_release_baseline(tmp_path, release_id="r1")

    assert not result.ok
    assert result.issues[0].kind == "release_scope_chain_broken"


def test_release_baseline_rejects_incompatible_intermediate_scope_anchor(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    _write_decl(tmp_path, node_path="Main.Foundation.Defs", name="Support", revision=2)
    changed = runtime.decl_graph.get_decl_revision(
        tmp_path, node_path="Main.Foundation.Defs", name="Support", revision=2
    ).value
    changed.statement.formal.code = "theorem Support : False := by\n  sorry\n"
    revision_path = runtime.decl_graph.graph_store.revision_path(
        tmp_path, node_path="Main.Foundation.Defs", decl_name="Support", revision=2
    )
    assert runtime.foundation.store.write_json_atomic(
        revision_path, changed, mode=WriteMode.UPDATE_EXISTING
    ).ok
    content = runtime.node.contract.get_visible_contract(tmp_path, node_path="Main.Foundation.Defs").value
    content.contract.decl_graph_head["Support"] = 2
    content_path = runtime.node.node_tree.node_store.contract_path(
        tmp_path, node_id=content.node_id, version=content.contract.version
    )
    assert runtime.foundation.store.write_json_atomic(
        content_path, content.contract, mode=WriteMode.UPDATE_EXISTING
    ).ok
    _set_contract_exports(
        runtime,
        tmp_path,
        node_path="Main",
        exports=[DeclRef(node="Main.Foundation.Defs", name="Support", revision=2)],
    )
    _set_contract_exports(
        runtime,
        tmp_path,
        node_path="Main.Foundation",
        exports=[DeclRef(node="Main.Foundation.Defs", name="Support", revision=1)],
    )
    assert runtime.repo_workspace.release.create_release(tmp_path, release=_release("r1", versions)).ok

    result = runtime.repo_workspace.release.resolve_release_baseline(tmp_path, release_id="r1")

    assert not result.ok
    assert result.issues[0].kind == "release_scope_chain_broken"


def test_release_external_statement_dep_requires_native_main_public_export(tmp_path: Path) -> None:
    _prepare_native_provider(tmp_path)
    consumer_root = tmp_path / "Consumer"
    runtime, versions = _prepare_release_repo(consumer_root)
    _add_external_statement_dep(
        runtime,
        consumer_root,
        DeclRef(repo="Provider", node="Main.Results", name="PublicResult", revision=1),
    )
    assert runtime.repo_workspace.release.create_release(
        consumer_root, release=_release("consumer_r1", versions)
    ).ok

    result = runtime.repo_workspace.release.resolve_release_baseline(
        consumer_root, release_id="consumer_r1"
    )

    assert result.ok


def test_release_external_statement_dep_rejects_missing_or_unexported_provider(tmp_path: Path) -> None:
    for repo_key, prepare in [("Missing", False), ("Provider", True)]:
        case_root = tmp_path / repo_key
        case_root.mkdir()
        if prepare:
            _prepare_native_provider(case_root, exported=False)
        consumer_root = case_root / "Consumer"
        runtime, versions = _prepare_release_repo(consumer_root)
        _add_external_statement_dep(
            runtime,
            consumer_root,
            DeclRef(repo=repo_key, node="Main.Results", name="PublicResult", revision=1),
        )
        assert runtime.repo_workspace.release.create_release(
            consumer_root, release=_release("consumer_r1", versions)
        ).ok
        result = runtime.repo_workspace.release.resolve_release_baseline(
            consumer_root, release_id="consumer_r1"
        )
        assert not result.ok
        assert result.issues[0].kind == "release_external_ref_unavailable"


def test_release_external_statement_dep_rejects_unsafe_repo_key(tmp_path: Path) -> None:
    consumer_root = tmp_path / "Consumer"
    runtime, versions = _prepare_release_repo(consumer_root)
    _add_external_statement_dep(
        runtime,
        consumer_root,
        DeclRef(repo="../Provider", node="Main.Results", name="PublicResult", revision=1),
    )
    assert runtime.repo_workspace.release.create_release(
        consumer_root, release=_release("consumer_r1", versions)
    ).ok

    result = runtime.repo_workspace.release.resolve_release_baseline(
        consumer_root, release_id="consumer_r1"
    )

    assert not result.ok
    assert result.issues[0].kind == "release_external_ref_unavailable"


def test_release_external_statement_dep_rejects_changed_native_statement(tmp_path: Path) -> None:
    provider_runtime, provider_root = _prepare_native_provider(tmp_path)
    _write_decl(provider_root, node_path="Main.Results", name="PublicResult", revision=2)
    changed = provider_runtime.decl_graph.get_decl_revision(
        provider_root, node_path="Main.Results", name="PublicResult", revision=2
    ).value
    changed.statement.formal.code = "theorem PublicResult : False := by\n  sorry\n"
    path = provider_runtime.decl_graph.graph_store.revision_path(
        provider_root, node_path="Main.Results", decl_name="PublicResult", revision=2
    )
    assert provider_runtime.foundation.store.write_json_atomic(
        path, changed, mode=WriteMode.UPDATE_EXISTING
    ).ok
    content = provider_runtime.node.contract.get_visible_contract(
        provider_root, node_path="Main.Results"
    ).value
    content.contract.decl_graph_head["PublicResult"] = 2
    assert provider_runtime.foundation.store.write_json_atomic(
        provider_runtime.node.node_tree.node_store.contract_path(
            provider_root, node_id=content.node_id, version=content.contract.version
        ),
        content.contract,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    _set_contract_exports(
        provider_runtime,
        provider_root,
        node_path="Main",
        exports=[DeclRef(node="Main.Results", name="PublicResult", revision=2)],
    )
    publish_native_provider_release(provider_runtime, provider_root, release_id="provider_r2")

    consumer_root = tmp_path / "Consumer"
    runtime, versions = _prepare_release_repo(consumer_root)
    _add_external_statement_dep(
        runtime,
        consumer_root,
        DeclRef(repo="Provider", node="Main.Results", name="PublicResult", revision=1),
    )
    assert runtime.repo_workspace.release.create_release(
        consumer_root, release=_release("consumer_r1", versions)
    ).ok

    result = runtime.repo_workspace.release.resolve_release_baseline(
        consumer_root, release_id="consumer_r1"
    )

    assert not result.ok
    assert result.issues[0].kind == "release_external_ref_unavailable"


def test_release_external_statement_dep_accepts_adapter_public_interface(tmp_path: Path) -> None:
    from lean_constellation.domain.interface import DeclInterface, DeclKind
    from tests.unit.services.adapter.test_adapter_service import _finalize_theorem, _service

    provider_root = tmp_path / "AdapterProvider"
    service = _service(
        provider_root,
        interfaces=[DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Public theorem.")],
    )
    assert service.runtime.node.interface.sync_protected_root_interfaces_from_preparation_input(
        provider_root
    ).ok
    _finalize_theorem(service, provider_root)
    assert service.bind_adapter_interface(
        provider_root,
        interface_name="main_result",
        decl_name="main_result",
        binding_summary="Expose the public theorem.",
    ).ok
    assert service.refresh_adapter_projection(provider_root).ok
    assert service.runtime.repo_workspace.metadata.mark_repo_stable(
        provider_root, summary="Stable adapter provider."
    ).ok

    consumer_root = tmp_path / "Consumer"
    runtime, versions = _prepare_release_repo(consumer_root)
    _add_external_statement_dep(
        runtime,
        consumer_root,
        DeclRef(repo="AdapterProvider", node="Main", name="main_result", revision=1),
    )
    readiness = runtime.decl_graph.check_decl_proof_policy_satisfied(
        consumer_root,
        node_path="Main.Results",
        decl_name="PublicResult",
    )
    assert readiness.ok and readiness.value is not None
    assert readiness.value.ready is True
    assert runtime.repo_workspace.release.create_release(
        consumer_root, release=_release("consumer_r1", versions)
    ).ok

    result = runtime.repo_workspace.release.resolve_release_baseline(
        consumer_root, release_id="consumer_r1"
    )

    assert result.ok
