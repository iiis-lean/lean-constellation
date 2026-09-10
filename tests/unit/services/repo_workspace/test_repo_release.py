from __future__ import annotations

from pathlib import Path

from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import ProofAvailability, RepoCompletionMode, RepoPublicationState, RepoPublicationStatus
from lean_constellation.domain.repo_release import DeclAvailabilityEntry, DeclAvailabilityIndex, RepoRelease
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
from lean_constellation.services.node import NodeContract, NodeContractStatus
from tests.unit_services_helpers import (
    initialize_native_test_repo,
    lean_check_payload,
    make_runtime,
    publish_adapter_provider_release,
    publish_native_provider_release,
)


def test_release_decl_availability_reader_is_strict_and_lru_cached(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = make_runtime()
    payload = DeclAvailabilityIndex(
        entries=[
            DeclAvailabilityEntry(
                node="Main.Topic.Core",
                name="result",
                revision=2,
                decl_state="proved",
                availability=ProofAvailability.PROVED,
                main_export=True,
            )
        ]
    ).model_dump_json()
    reads = 0

    def read_release_file(*args, **kwargs):
        nonlocal reads
        reads += 1
        return runtime.foundation.ok(payload)

    monkeypatch.setattr(runtime.repo_workspace.git_release, "read_release_file", read_release_file)

    first = runtime.repo_workspace.release.lookup_decl_availability(
        tmp_path,
        release_id="release_test",
        node_path="Main.Topic.Core",
        decl_name="result",
        revision=2,
    )
    second = runtime.repo_workspace.release.lookup_decl_availability(
        tmp_path,
        release_id="release_test",
        node_path="Main.Topic.Core",
        decl_name="result",
        revision=2,
    )

    assert first.ok and first.value is not None
    assert second.ok and second.value == first.value
    assert reads == 1


def test_release_decl_availability_reader_rejects_missing_and_malformed_sidecar(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = make_runtime()
    monkeypatch.setattr(
        runtime.repo_workspace.git_release,
        "read_release_file",
        lambda *args, **kwargs: runtime.foundation.ok("{not-json"),
    )

    malformed = runtime.repo_workspace.release.lookup_decl_availability(
        tmp_path,
        release_id="release_test",
        node_path="Main.Topic.Core",
        decl_name="result",
        revision=1,
    )

    assert not malformed.ok
    assert malformed.issues[0].kind == "release_decl_availability_invalid"

    runtime.repo_workspace.release._decl_availability_cache.clear()
    monkeypatch.setattr(
        runtime.repo_workspace.git_release,
        "read_release_file",
        lambda *args, **kwargs: runtime.foundation.fail(
            runtime.foundation.issue(
                "read_failed",
                "Injected missing Release availability sidecar.",
            )
        ),
    )
    missing = runtime.repo_workspace.release.lookup_decl_availability(
        tmp_path,
        release_id="release_test",
        node_path="Main.Topic.Core",
        decl_name="result",
        revision=1,
    )

    assert not missing.ok
    assert missing.issues[0].kind == "read_failed"


def test_release_decl_availability_lookup_distinguishes_missing_entry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = make_runtime()
    payload = DeclAvailabilityIndex(entries=[]).model_dump_json()
    monkeypatch.setattr(
        runtime.repo_workspace.git_release,
        "read_release_file",
        lambda *args, **kwargs: runtime.foundation.ok(payload),
    )

    loaded = runtime.repo_workspace.release.lookup_decl_availability(
        tmp_path,
        release_id="release_test",
        node_path="Main.Topic.Core",
        decl_name="result",
        revision=1,
    )

    assert loaded.ok
    assert loaded.value is None


def test_release_decl_availability_writer_uses_one_batch_and_marks_main_exports(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    batch_calls = 0
    original_batch = runtime.decl_graph.readiness.check_decl_proof_policy_batch

    def count_batch(*args, **kwargs):
        nonlocal batch_calls
        batch_calls += 1
        return original_batch(*args, **kwargs)

    monkeypatch.setattr(
        runtime.decl_graph.readiness,
        "check_decl_proof_policy_batch",
        count_batch,
    )

    built = runtime.decl_graph.build_release_decl_availability_index(tmp_path)

    assert built.ok and built.value is not None
    assert batch_calls == 1
    assert [(entry.node, entry.name) for entry in built.value.entries] == [
        ("Main.Foundation.Defs", "ProofHelper"),
        ("Main.Foundation.Defs", "Support"),
        ("Main.Results", "PublicResult"),
    ]
    assert all(entry.availability == ProofAvailability.PROVED for entry in built.value.entries)
    assert {
        (entry.node, entry.name)
        for entry in built.value.entries
        if entry.main_export
    } == {("Main.Results", "PublicResult")}


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
    public: bool = True,
) -> None:
    runtime = make_runtime()
    assert runtime.decl_graph.ensure_decl_graph(repo_root, node_path=node_path).ok
    decl = Decl(
        name=name,
        node_path=node_path,
        kind=kind,
        public=public,
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
    initialize_native_test_repo(repo_root, project_name=repo_root.name or "TestProject")
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
    assert runtime.repo_workspace.metadata.set_repo_format(
        repo_root,
        repo_format="native",
        reason="native release fixture",
    ).ok
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


def _prepare_adapter_release_repo(repo_root: Path):
    runtime = make_runtime()
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
    assert runtime.repo_workspace.metadata.set_repo_format(
        repo_root,
        repo_format="adapter",
        reason="adapter release fixture",
    ).ok
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    support_ref = DeclRef(node="Main", name="Support", revision=1)
    _write_decl(repo_root, node_path="Main", name="Support")
    _write_decl(
        repo_root,
        node_path="Main",
        name="PublicResult",
        statement_deps=(support_ref,),
    )
    main = runtime.node.node_tree.get_node(repo_root, path="Main").value
    loaded = runtime.node.contract.get_current_contract(repo_root, node_path="Main")
    assert loaded.ok and loaded.value is not None
    contract = loaded.value.contract
    contract.status = NodeContractStatus.COMMITTED
    contract.committed_at = "2026-08-08T00:00:00Z"
    contract.exports = [DeclRef(node="Main", name="PublicResult", revision=1)]
    assert runtime.foundation.store.write_json_atomic(
        runtime.node.node_tree.node_store.contract_path(
            repo_root,
            node_id=main.node_id,
            version=contract.version,
        ),
        contract,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    metadata = runtime.node.node_tree.node_store.load_node_by_id(
        repo_root,
        node_id=main.node_id,
    ).value
    metadata.active_contract_version = contract.version
    metadata.current_contract_version = contract.version
    metadata.open_contract_version = None
    assert runtime.node.node_tree.node_store.save_node(
        repo_root,
        metadata,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    return runtime, {main.node_id: contract.version}


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
        semantic_manifest_digest="1" * 64,
        dependency_lock_digest="2" * 64,
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


def test_release_status_batch_freezes_one_lineage_and_rebuilds_after_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    assert runtime.repo_workspace.release.create_release(
        tmp_path,
        release=_release("r1", versions),
    ).ok
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(tmp_path),
        RepoPublicationState(
            status=RepoPublicationStatus.STABLE,
            latest_release_id="r1",
        ),
        mode=WriteMode.OVERWRITE,
    ).ok
    component = runtime.repo_workspace.release
    lineage_calls = 0
    original_lineage = component.resolve_release_lineage

    def count_lineage(*args, **kwargs):  # noqa: ANN001, ANN202
        nonlocal lineage_calls
        lineage_calls += 1
        return original_lineage(*args, **kwargs)

    monkeypatch.setattr(component, "resolve_release_lineage", count_lineage)
    requested = [
        ("Main.Foundation.Defs", "Support"),
        ("Main.Foundation.Defs", "ProofHelper"),
        ("Main.Results", "PublicResult"),
        ("Main.Foundation.Defs", "Support"),
    ]

    first = component.get_decl_release_status_batch(tmp_path, decls=requested)

    assert first.ok and first.value is not None
    assert lineage_calls == 1
    assert [item.model_dump() for item in first.value] == [
        {
            "current_state": "proved",
            "released_state": "proved",
            "release_protected": True,
            "summary": "Declaration is release protected.",
        },
        {
            "current_state": "proved",
            "released_state": "proved",
            "release_protected": False,
            "summary": "Declaration is not release protected.",
        },
        {
            "current_state": "proved",
            "released_state": "proved",
            "release_protected": True,
            "summary": "Declaration is release protected.",
        },
        {
            "current_state": "proved",
            "released_state": "proved",
            "release_protected": True,
            "summary": "Declaration is release protected.",
        },
    ]

    _write_decl(
        tmp_path,
        node_path="Main.Foundation.Defs",
        name="ProofHelper",
        revision=2,
        state=DeclState.DECLARED,
    )
    second = component.get_decl_release_status_batch(
        tmp_path,
        decls=[("Main.Foundation.Defs", "ProofHelper")],
    )

    assert second.ok and second.value is not None
    assert lineage_calls == 2
    assert second.value[0].current_state == "declared"
    assert second.value[0].released_state == "proved"
    assert second.value[0].release_protected is False


def test_release_audit_context_uses_current_node_identity_after_recreated_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    created = runtime.node.create_content_node(
        tmp_path,
        path="Main.Experimental",
        goal="Historical experiment.",
        boundary="Private historical content.",
        objective="Record private history.",
        success_criteria="Private history is recorded.",
    )
    assert created.ok and created.value is not None
    old_node_id = created.value.node_id
    _write_decl(
        tmp_path,
        node_path="Main.Experimental",
        name="PrivateResult",
    )
    current = runtime.node.contract.get_current_contract(
        tmp_path,
        node_path="Main.Experimental",
    )
    assert current.ok and current.value is not None
    current.value.contract.status = NodeContractStatus.COMMITTED
    current.value.contract.committed_at = "2026-08-19T00:00:00Z"
    current.value.contract.decl_graph_head = {"PrivateResult": 1}
    assert runtime.foundation.store.write_json_atomic(
        runtime.node.node_tree.node_store.contract_path(
            tmp_path,
            node_id=old_node_id,
            version=current.value.contract.version,
        ),
        current.value.contract,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    metadata = runtime.node.node_tree.node_store.load_node_by_id(
        tmp_path,
        node_id=old_node_id,
    ).value
    metadata.active_contract_version = current.value.contract.version
    metadata.current_contract_version = current.value.contract.version
    metadata.open_contract_version = None
    assert runtime.node.node_tree.node_store.save_node(
        tmp_path,
        metadata,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    versions[old_node_id] = current.value.contract.version
    assert runtime.repo_workspace.release.create_release(
        tmp_path,
        release=_release("r1", versions),
    ).ok
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(tmp_path),
        RepoPublicationState(
            status=RepoPublicationStatus.STABLE,
            latest_release_id="r1",
        ),
        mode=WriteMode.OVERWRITE,
    ).ok
    assert runtime.node.mark_node_deleted(
        tmp_path,
        path="Main.Experimental",
        reason="Replace the private experiment.",
    ).ok
    recreated = runtime.node.create_content_node(
        tmp_path,
        path="Main.Experimental",
        goal="Replacement experiment.",
        boundary="New private content.",
        objective="Record replacement work.",
        success_criteria="Replacement work is recorded.",
    )
    assert recreated.ok and recreated.value is not None
    assert recreated.value.node_id != old_node_id
    _write_decl(
        tmp_path,
        node_path="Main.Experimental",
        name="PrivateResult",
        state=DeclState.DECLARED,
    )
    node_store = runtime.node.node_tree.node_store
    monkeypatch.setattr(
        node_store,
        "_scan_nodes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("current index must not rescan node metadata")
        ),
    )

    context = runtime.repo_workspace.release.create_release_audit_context(tmp_path)
    assert context.ok and context.value is not None
    assert context.value.node_ids_by_path["Main.Experimental"] == recreated.value.node_id
    status = runtime.repo_workspace.release.get_decl_release_status_batch(
        tmp_path,
        decls=[("Main.Experimental", "PrivateResult")],
        audit_context=context.value,
    )

    assert status.ok and status.value is not None
    assert status.value[0].current_state == "declared"
    assert status.value[0].released_state is None
    assert status.value[0].release_protected is False


def test_release_scope_chain_resolves_matching_exports_as_one_batch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    support = DeclRef(node="Main.Foundation.Defs", name="Support", revision=1)
    public_result = DeclRef(node="Main.Results", name="PublicResult", revision=1)
    for path, exports in (
        ("Main", [support, public_result]),
        ("Main.Foundation", [support, support]),
    ):
        node = runtime.node.node_tree.get_node(tmp_path, path=path).value
        contract_path = runtime.node.node_tree.node_store.contract_path(
            tmp_path,
            node_id=node.node_id,
            version=versions[node.node_id],
        )
        contract = runtime.foundation.store.read_json(
            contract_path,
            NodeContract,
        ).value
        contract.exports = exports
        assert runtime.foundation.store.write_json_atomic(
            contract_path,
            contract,
            mode=WriteMode.UPDATE_EXISTING,
        ).ok
    batch_sizes: list[int] = []
    resolver = runtime.decl_graph.ref_compatibility
    original_batch = resolver.resolve_decl_refs_batch

    def count_batch(*args, **kwargs):  # noqa: ANN001, ANN202
        batch_sizes.append(len(kwargs["refs"]))
        return original_batch(*args, **kwargs)

    monkeypatch.setattr(resolver, "resolve_decl_refs_batch", count_batch)
    assert runtime.repo_workspace.release.create_release(
        tmp_path,
        release=_release("r1", versions),
    ).ok
    contract_reads = 0
    component = runtime.repo_workspace.release
    original_load_contract = component._load_contract

    def count_contract(*args, **kwargs):  # noqa: ANN001, ANN202
        nonlocal contract_reads
        contract_reads += 1
        return original_load_contract(*args, **kwargs)

    monkeypatch.setattr(component, "_load_contract", count_contract)

    baseline = component.resolve_release_baseline(
        tmp_path,
        release_id="r1",
    )

    assert baseline.ok and baseline.value is not None
    assert [(item.node_path, item.decl_name) for item in baseline.value.protected_decl_views] == [
        ("Main.Foundation.Defs", "Support"),
        ("Main.Results", "PublicResult"),
    ]
    assert batch_sizes.count(2) >= 2
    assert contract_reads == len(versions)


def test_adapter_release_baseline_uses_flat_main_exports_and_statement_closure(
    tmp_path: Path,
) -> None:
    runtime, versions = _prepare_adapter_release_repo(tmp_path)
    assert runtime.repo_workspace.release.create_release(
        tmp_path,
        release=_release("adapter_r1", versions),
    ).ok

    baseline = runtime.repo_workspace.release.resolve_release_baseline(
        tmp_path,
        release_id="adapter_r1",
    )

    assert baseline.ok and baseline.value is not None
    assert {
        (item.node_path, item.decl_name)
        for item in baseline.value.protected_decl_views
    } == {("Main", "PublicResult"), ("Main", "Support")}
    assert baseline.value.protected_scope_paths == ["Main"]
    assert baseline.value.protected_node_ids == list(versions)


def test_adapter_release_availability_index_reads_flat_main_catalog(
    tmp_path: Path,
) -> None:
    runtime, _versions = _prepare_adapter_release_repo(tmp_path)

    built = runtime.decl_graph.build_release_decl_availability_index(tmp_path)

    assert built.ok and built.value is not None
    assert [
        (entry.node, entry.name, entry.main_export)
        for entry in built.value.entries
    ] == [
        ("Main", "PublicResult", True),
        ("Main", "Support", False),
    ]


def test_adapter_stable_public_resolution_uses_release_availability_sidecar(
    tmp_path: Path,
) -> None:
    from lean_constellation.domain.interface import DeclInterface, DeclKind
    from tests.unit.services.adapter.test_adapter_service import (
        _finalize_theorem,
        _service,
    )

    provider_root = tmp_path / "Provider"
    service = _service(
        provider_root,
        interfaces=[
            DeclInterface(
                name="main_result",
                kind=DeclKind.THEOREM,
                summary="Public theorem.",
            )
        ],
    )
    runtime = service.runtime
    assert runtime.node.interface.sync_protected_root_interfaces_from_preparation_input(
        provider_root
    ).ok
    _finalize_theorem(service, provider_root)
    assert service.bind_adapter_interface(
        provider_root,
        interface_name="main_result",
        decl_name="main_result",
        binding_summary="Expose the public theorem.",
    ).ok
    assert service.sync_adapter_public_exports(provider_root).ok
    assert service.refresh_adapter_projection(provider_root).ok
    release = publish_adapter_provider_release(
        runtime,
        provider_root,
        release_id="adapter_r1",
    )
    current = runtime.decl_graph.get_decl_revision(
        provider_root,
        node_path="Main",
        name="main_result",
        revision=1,
    )
    assert current.ok and current.value is not None, current.issues
    current.value.state = DeclState.DECLARED
    assert runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.revision_path(
            provider_root,
            node_path="Main",
            decl_name="main_result",
            revision=1,
        ),
        current.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    consumer_root = tmp_path / "Consumer"
    consumer_root.mkdir()
    ref = DeclRef(
        repo="Provider",
        node="Main",
        name="main_result",
        revision=1,
    )

    declared = runtime.decl_graph.ref_compatibility.resolve_public_decl_refs_batch(
        consumer_root,
        refs=[ref],
        required_availability=ProofAvailability.DECLARED,
    )
    proved = runtime.decl_graph.ref_compatibility.resolve_public_decl_refs_batch(
        consumer_root,
        refs=[ref],
        required_availability=ProofAvailability.PROVED,
    )

    assert release.release_id == "adapter_r1"
    assert declared.ok and declared.value is not None
    assert proved.ok and proved.value is not None
    assert declared.value[0].compatible is True
    assert proved.value[0].compatible is True
    assert declared.value[0].current_state == DeclState.PROVED.value
    assert proved.value[0].current_state == DeclState.PROVED.value


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


def _prepare_native_provider_with_two_exports(workspace: Path):
    runtime, provider_root = _prepare_native_provider(workspace)
    _write_decl(
        provider_root,
        node_path="Main.Results",
        name="PublicSecond",
    )
    results = runtime.node.contract.get_visible_contract(
        provider_root,
        node_path="Main.Results",
    )
    assert results.ok and results.value is not None
    results.value.contract.decl_graph_head["PublicSecond"] = 1
    assert runtime.foundation.store.write_json_atomic(
        runtime.node.node_tree.node_store.contract_path(
            provider_root,
            node_id=results.value.node_id,
            version=results.value.version,
        ),
        results.value.contract,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    refs = [
        DeclRef(
            repo="Provider",
            node="Main.Results",
            name="PublicResult",
            revision=1,
        ),
        DeclRef(
            repo="Provider",
            node="Main.Results",
            name="PublicSecond",
            revision=1,
        ),
    ]
    _set_contract_exports(
        runtime,
        provider_root,
        node_path="Main",
        exports=[ref.model_copy(update={"repo": None}) for ref in refs],
    )
    publish_native_provider_release(runtime, provider_root, release_id="provider_r2")
    return runtime, provider_root, refs


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


def test_release_scope_chain_tolerant_fallback_uses_batch_of_one(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    support = DeclRef(node="Main.Foundation.Defs", name="Support", revision=1)
    malformed = support.model_copy(update={"revision": 999})
    _set_contract_exports(runtime, tmp_path, node_path="Main", exports=[support])
    _set_contract_exports(
        runtime,
        tmp_path,
        node_path="Main.Foundation",
        exports=[malformed, support],
    )
    resolver = runtime.decl_graph.ref_compatibility
    original_batch = resolver.resolve_decl_refs_batch
    batch_sizes: list[int] = []
    failed_multi = False

    def fail_scope_multi_batch(*args, **kwargs):  # noqa: ANN001, ANN202
        nonlocal failed_multi
        size = len(kwargs["refs"])
        batch_sizes.append(size)
        if size > 1 and not failed_multi:
            failed_multi = True
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "decl_ref_batch_fixture_failure",
                    "Exercise the tolerant release scope fallback.",
                )
            )
        return original_batch(*args, **kwargs)

    monkeypatch.setattr(resolver, "resolve_decl_refs_batch", fail_scope_multi_batch)
    monkeypatch.setattr(
        resolver,
        "resolve_decl_ref",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fallback must use the batch-of-one core")
        ),
    )

    created = runtime.repo_workspace.release.create_release(
        tmp_path,
        release=_release("r1", versions),
    )
    assert created.ok
    result = runtime.repo_workspace.release.resolve_release_baseline(
        tmp_path,
        release_id="r1",
    )

    assert result.ok
    multi_index = batch_sizes.index(2)
    assert batch_sizes[multi_index + 1 : multi_index + 3] == [1, 1]


def test_release_scope_chain_all_malformed_scope_alternatives_are_typed(
    tmp_path: Path,
) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    support = DeclRef(node="Main.Foundation.Defs", name="Support", revision=1)
    malformed = support.model_copy(update={"revision": 999})
    _set_contract_exports(runtime, tmp_path, node_path="Main", exports=[support])
    _set_contract_exports(
        runtime,
        tmp_path,
        node_path="Main.Foundation",
        exports=[malformed],
    )

    created = runtime.repo_workspace.release.create_release(
        tmp_path,
        release=_release("r1", versions),
    )
    assert created.ok
    result = runtime.repo_workspace.release.resolve_release_baseline(
        tmp_path,
        release_id="r1",
    )

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


def test_native_release_external_closure_batches_shared_provider_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _provider_runtime, _provider_root, refs = (
        _prepare_native_provider_with_two_exports(tmp_path)
    )
    consumer_root = tmp_path / "Consumer"
    runtime, versions = _prepare_release_repo(consumer_root)
    for ref in refs:
        _add_external_statement_dep(runtime, consumer_root, ref)
    assert runtime.repo_workspace.release.create_release(
        consumer_root,
        release=_release("consumer_r1", versions),
    ).ok
    resolver = runtime.decl_graph.ref_compatibility
    original_batch = resolver.resolve_public_decl_refs_batch
    original_boundary = resolver._load_public_boundary_context
    batch_sizes: list[int] = []
    context_ids: list[int] = []
    boundary_reads = 0

    def record_batch(*args, **kwargs):  # noqa: ANN001, ANN202
        batch_sizes.append(len(kwargs["refs"]))
        context_ids.append(id(kwargs["operation_context"]))
        return original_batch(*args, **kwargs)

    def count_boundary(*args, **kwargs):  # noqa: ANN001, ANN202
        nonlocal boundary_reads
        boundary_reads += 1
        return original_boundary(*args, **kwargs)

    monkeypatch.setattr(resolver, "resolve_public_decl_refs_batch", record_batch)
    monkeypatch.setattr(resolver, "_load_public_boundary_context", count_boundary)
    monkeypatch.setattr(
        resolver,
        "resolve_public_decl_ref",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("release closure must not call the public single wrapper")
        ),
    )

    result = runtime.repo_workspace.release.resolve_release_baseline(
        consumer_root,
        release_id="consumer_r1",
    )

    assert result.ok
    assert batch_sizes == [2]
    assert len(set(context_ids)) == 1
    assert boundary_reads == 1


def test_native_release_external_closure_hard_batch_failure_replays_in_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _provider_runtime, _provider_root, refs = (
        _prepare_native_provider_with_two_exports(tmp_path)
    )
    consumer_root = tmp_path / "Consumer"
    runtime, versions = _prepare_release_repo(consumer_root)
    for ref in refs:
        _add_external_statement_dep(runtime, consumer_root, ref)
    assert runtime.repo_workspace.release.create_release(
        consumer_root,
        release=_release("consumer_r1", versions),
    ).ok
    resolver = runtime.decl_graph.ref_compatibility
    original_batch = resolver.resolve_public_decl_refs_batch
    batch_sizes: list[int] = []
    context_ids: list[int] = []
    failed_multi = False

    def fail_first_multi(*args, **kwargs):  # noqa: ANN001, ANN202
        nonlocal failed_multi
        size = len(kwargs["refs"])
        batch_sizes.append(size)
        context_ids.append(id(kwargs["operation_context"]))
        if size > 1 and not failed_multi:
            failed_multi = True
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "public_decl_batch_fixture_failure",
                    "Exercise same-context public fallback.",
                )
            )
        return original_batch(*args, **kwargs)

    monkeypatch.setattr(resolver, "resolve_public_decl_refs_batch", fail_first_multi)
    monkeypatch.setattr(
        resolver,
        "resolve_public_decl_ref",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("hard-batch fallback must use the batch-of-one core")
        ),
    )

    result = runtime.repo_workspace.release.resolve_release_baseline(
        consumer_root,
        release_id="consumer_r1",
    )

    assert result.ok
    assert batch_sizes == [2, 1, 1]
    assert len(set(context_ids)) == 1


def test_adapter_release_external_closure_batches_shared_provider_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _provider_runtime, _provider_root, refs = (
        _prepare_native_provider_with_two_exports(tmp_path)
    )
    consumer_root = tmp_path / "AdapterConsumer"
    runtime, versions = _prepare_adapter_release_repo(consumer_root)
    revision = runtime.decl_graph.get_decl_revision(
        consumer_root,
        node_path="Main",
        name="PublicResult",
        revision=1,
    )
    assert revision.ok and revision.value is not None
    revision.value.statement.deps.extend(RepoDeclDep(ref=ref) for ref in refs)
    assert runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.revision_path(
            consumer_root,
            node_path="Main",
            decl_name="PublicResult",
            revision=1,
        ),
        revision.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    assert runtime.repo_workspace.release.create_release(
        consumer_root,
        release=_release("consumer_r1", versions),
    ).ok
    resolver = runtime.decl_graph.ref_compatibility
    original_batch = resolver.resolve_public_decl_refs_batch
    batch_sizes: list[int] = []
    context_ids: list[int] = []

    def record_batch(*args, **kwargs):  # noqa: ANN001, ANN202
        batch_sizes.append(len(kwargs["refs"]))
        context_ids.append(id(kwargs["operation_context"]))
        return original_batch(*args, **kwargs)

    monkeypatch.setattr(resolver, "resolve_public_decl_refs_batch", record_batch)
    monkeypatch.setattr(
        resolver,
        "resolve_public_decl_ref",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("adapter closure must not call the public single wrapper")
        ),
    )

    result = runtime.repo_workspace.release.resolve_release_baseline(
        consumer_root,
        release_id="consumer_r1",
    )

    assert result.ok
    assert batch_sizes == [2]
    assert len(set(context_ids)) == 1


def test_adapter_release_external_closure_hard_batch_failure_replays_first_issue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _provider_runtime, _provider_root, refs = (
        _prepare_native_provider_with_two_exports(tmp_path)
    )
    consumer_root = tmp_path / "AdapterConsumer"
    runtime, versions = _prepare_adapter_release_repo(consumer_root)
    revision = runtime.decl_graph.get_decl_revision(
        consumer_root,
        node_path="Main",
        name="PublicResult",
        revision=1,
    )
    assert revision.ok and revision.value is not None
    revision.value.statement.deps.extend(RepoDeclDep(ref=ref) for ref in refs)
    assert runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.revision_path(
            consumer_root,
            node_path="Main",
            decl_name="PublicResult",
            revision=1,
        ),
        revision.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    assert runtime.repo_workspace.release.create_release(
        consumer_root,
        release=_release("consumer_r1", versions),
    ).ok
    resolver = runtime.decl_graph.ref_compatibility
    batch_sizes: list[int] = []
    context_ids: list[int] = []

    def fail_multi_then_first_ref(*args, **kwargs):  # noqa: ANN001, ANN202
        batch_refs = kwargs["refs"]
        batch_sizes.append(len(batch_refs))
        context_ids.append(id(kwargs["operation_context"]))
        if len(batch_refs) > 1:
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "public_decl_batch_fixture_failure",
                    "Exercise Adapter same-context public fallback.",
                )
            )
        assert batch_refs == [refs[0]]
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "adapter_public_decl_fixture_failure",
                "Preserve the first Adapter dependency failure.",
                object_ref="fixture:first",
                current="fixture-current",
            )
        )

    monkeypatch.setattr(
        resolver,
        "resolve_public_decl_refs_batch",
        fail_multi_then_first_ref,
    )
    monkeypatch.setattr(
        resolver,
        "resolve_public_decl_ref",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("adapter hard fallback must not call the public single wrapper")
        ),
    )

    result = runtime.repo_workspace.release.resolve_release_baseline(
        consumer_root,
        release_id="consumer_r1",
    )

    assert not result.ok
    assert batch_sizes == [2, 1]
    assert len(set(context_ids)) == 1
    assert result.issues[0].kind == "release_external_ref_unavailable"
    assert result.issues[0].object_ref == (
        f"{refs[0].repo}:{refs[0].node}:{refs[0].name}@{refs[0].revision}"
    )
    assert result.issues[0].current == "adapter_public_decl_fixture_failure"


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
    assert service.sync_adapter_public_exports(provider_root).ok
    assert service.refresh_adapter_projection(provider_root).ok
    publish_adapter_provider_release(
        service.runtime,
        provider_root,
        summary="Stable adapter provider.",
    )

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
