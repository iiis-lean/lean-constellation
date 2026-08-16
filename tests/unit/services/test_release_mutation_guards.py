from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import RepoPublicationState, RepoPublicationStatus
from lean_constellation.services.decl_graph.models import DeclLifecycle, DeclState
from lean_constellation.services.foundation import WriteMode
from tests.unit.services.repo_workspace.test_repo_release import (
    _prepare_adapter_release_repo,
    _prepare_release_repo,
    _release,
    _write_decl,
)


def _publish_latest(runtime, repo_root: Path, versions: dict[str, int]) -> None:
    assert runtime.repo_workspace.release.create_release(repo_root, release=_release("r1", versions)).ok
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(repo_root),
        RepoPublicationState(status=RepoPublicationStatus.STABLE, latest_release_id="r1"),
        mode=WriteMode.OVERWRITE,
    ).ok


def _draft_round(runtime, repo_root: Path, *, node_path: str) -> str:
    strategy = runtime.decl_graph.ensure_open_strategy(
        repo_root, node_path=node_path, objective="Exercise release mutation guards."
    )
    assert strategy.ok and strategy.value is not None
    round_result = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=node_path,
        strategy_id=strategy.value.strategy_id,
        objective="Update a released declaration safely.",
    )
    assert round_result.ok and round_result.value is not None
    return round_result.value.round_id


def test_released_statement_reset_is_blocked_but_proof_reset_is_allowed(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    _publish_latest(runtime, tmp_path, versions)
    round_id = _draft_round(runtime, tmp_path, node_path="Main.Results")

    blocked = runtime.decl_graph.open_decl_update(
        tmp_path,
        node_path="Main.Results",
        round_id=round_id,
        name="PublicResult",
        objective="Illegally reopen the released statement.",
        start_stage="statement_formal",
        target_state=DeclState.PROVED,
    )
    assert not blocked.ok
    assert blocked.issues[0].kind == "release_protected_statement_floor"

    allowed = runtime.decl_graph.open_decl_update(
        tmp_path,
        node_path="Main.Results",
        round_id=round_id,
        name="PublicResult",
        objective="Replace only the proof.",
        start_stage="proof_nl",
        target_state=DeclState.PROVED,
    )
    assert allowed.ok
    revision = runtime.decl_graph.get_decl_revision(
        tmp_path, node_path="Main.Results", name="PublicResult", revision=2
    )
    assert revision.ok and revision.value is not None
    assert revision.value.statement.formal is not None
    assert revision.value.proof is None


def test_released_declaration_cannot_be_deleted_synchronously(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    _publish_latest(runtime, tmp_path, versions)

    deleted = runtime.decl_graph.delete_decls(
        tmp_path,
        node_path="Main.Results",
        decl_names=["PublicResult"],
    )

    assert not deleted.ok
    assert deleted.issues[0].kind == "release_protected_decl_delete"


def test_released_decl_restore_rejects_changed_declared_api_without_mutation(
    tmp_path: Path,
) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    _publish_latest(runtime, tmp_path, versions)
    _write_decl(tmp_path, node_path="Main.Results", name="PublicResult", revision=2)
    incompatible = runtime.decl_graph.get_decl_revision(
        tmp_path,
        node_path="Main.Results",
        name="PublicResult",
        revision=2,
    )
    assert incompatible.ok and incompatible.value is not None
    assert incompatible.value.statement.formal is not None
    incompatible.value.statement.formal.code = "theorem PublicResult : False := by\n  sorry\n"
    revision_path = runtime.decl_graph.graph_store.revision_path(
        tmp_path,
        node_path="Main.Results",
        decl_name="PublicResult",
        revision=2,
    )
    assert runtime.foundation.store.write_json_atomic(
        revision_path,
        incompatible.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    decl_path = runtime.decl_graph.graph_store.decl_record_path(
        tmp_path,
        node_path="Main.Results",
        decl_name="PublicResult",
    )
    projection_path = Path(
        runtime.lean_projection.decl_file.derive_decl_file_path(
            tmp_path,
            node_path="Main.Results",
            decl_name="PublicResult",
            kind="theorem",
        ).value.path
    )
    projection_path.parent.mkdir(parents=True, exist_ok=True)
    projection_path.write_text("theorem PublicResult : False := by\n  trivial\n", encoding="utf-8")
    before_decl = decl_path.read_bytes()
    before_projection = projection_path.read_bytes()

    restored = runtime.decl_graph.restore_decl_revision(
        tmp_path,
        node_path="Main.Results",
        decl_name="PublicResult",
        source_revision=2,
    )

    assert not restored.ok
    assert restored.issues[0].kind == "release_protected_declared_api_changed"
    assert decl_path.read_bytes() == before_decl
    assert projection_path.read_bytes() == before_projection
    assert not runtime.decl_graph.graph_store.revision_path(
        tmp_path,
        node_path="Main.Results",
        decl_name="PublicResult",
        revision=3,
    ).exists()


def test_released_scope_boundary_mutation_is_rejected(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    _publish_latest(runtime, tmp_path, versions)

    main = runtime.node.node_tree.node_store.resolve_active_node(tmp_path, path="Main")
    assert main.ok and main.value is not None
    contract_path = runtime.node.node_tree.node_store.contract_path(
        tmp_path, node_id=main.value.node_id, version=main.value.current_contract_version
    )
    node_path = runtime.node.node_tree.node_store.node_file(tmp_path, node_id=main.value.node_id)
    before_contract = contract_path.read_bytes()
    before_node = node_path.read_bytes()
    current = runtime.node.contract.get_visible_contract(tmp_path, node_path="Main")
    assert current.ok and current.value is not None
    target = current.value.contract.exports[0]

    removed = runtime.node.export.remove_scope_export(tmp_path, scope_path="Main", ref=target)

    assert not removed.ok
    assert removed.issues[0].kind == "released_scope_export_removed"
    assert contract_path.read_bytes() == before_contract
    assert node_path.read_bytes() == before_node
    assert main.value.open_contract_version is None


def test_released_scope_rejects_invalid_revision_even_when_identity_matches(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    _publish_latest(runtime, tmp_path, versions)
    current = runtime.node.contract.get_visible_contract(tmp_path, node_path="Main")
    assert current.ok and current.value is not None
    candidate = deepcopy(current.value.contract)
    candidate.exports = [DeclRef(node="Main.Results", name="PublicResult", revision=999)]

    guarded = runtime.node.release_guard.check_scope_contract_candidate(
        tmp_path, scope_path="Main", candidate=candidate
    )

    assert not guarded.ok
    assert guarded.issues[0].kind == "released_scope_export_removed"


def test_released_scope_rejects_removing_historical_unbound_interface(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    main = runtime.node.node_tree.node_store.resolve_active_node(tmp_path, path="Main")
    assert main.ok and main.value is not None
    contract_path = runtime.node.node_tree.node_store.contract_path(
        tmp_path, node_id=main.value.node_id, version=main.value.current_contract_version
    )
    current = runtime.node.contract.get_visible_contract(tmp_path, node_path="Main")
    assert current.ok and current.value is not None
    historical = deepcopy(current.value.contract)
    historical.interfaces = [
        DeclInterface(
            name="OptionalResult",
            kind=DeclKind.THEOREM,
            summary="A deliberately unbound historical interface fixture.",
        )
    ]
    assert runtime.foundation.store.write_json_atomic(
        contract_path, historical, mode=WriteMode.UPDATE_EXISTING
    ).ok
    _publish_latest(runtime, tmp_path, versions)
    candidate = deepcopy(historical)
    candidate.interfaces = []

    guarded = runtime.node.release_guard.check_scope_contract_candidate(
        tmp_path, scope_path="Main", candidate=candidate
    )

    assert not guarded.ok
    assert guarded.issues[0].kind == "released_scope_interface_changed"


def test_released_adapter_main_public_boundary_cannot_shrink_or_rebind(
    tmp_path: Path,
) -> None:
    runtime, versions = _prepare_adapter_release_repo(tmp_path)
    current = runtime.node.contract.get_visible_contract(tmp_path, node_path="Main")
    assert current.ok and current.value is not None
    historical = deepcopy(current.value.contract)
    public_ref = historical.exports[0]
    historical.interfaces = [
        DeclInterface(
            name="public_result",
            kind=DeclKind.THEOREM,
            summary="Released Adapter interface alias.",
            bound_decl=public_ref,
        )
    ]
    contract_path = runtime.node.node_tree.node_store.contract_path(
        tmp_path,
        node_id=current.value.node_id,
        version=current.value.version,
    )
    assert runtime.foundation.store.write_json_atomic(
        contract_path,
        historical,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    _publish_latest(runtime, tmp_path, versions)

    removed = deepcopy(historical)
    removed.exports = []
    removed_result = runtime.node.release_guard.check_scope_contract_candidate(
        tmp_path,
        scope_path="Main",
        candidate=removed,
    )
    assert not removed_result.ok
    assert removed_result.issues[0].kind == "released_scope_export_removed"

    rebound = deepcopy(historical)
    rebound.interfaces[0].bound_decl = DeclRef(
        node="Main",
        name="Support",
        revision=1,
    )
    rebound_result = runtime.node.release_guard.check_scope_contract_candidate(
        tmp_path,
        scope_path="Main",
        candidate=rebound,
    )
    assert not rebound_result.ok
    assert rebound_result.issues[0].kind == "released_scope_interface_changed"


def test_content_head_rejects_deleted_proof_dependency(tmp_path: Path) -> None:
    runtime, _versions = _prepare_release_repo(tmp_path)
    helper = runtime.decl_graph.get_decl(
        tmp_path, node_path="Main.Foundation.Defs", name="ProofHelper"
    )
    assert helper.ok and helper.value is not None
    helper.value.lifecycle = DeclLifecycle.DELETED
    assert runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.decl_record_path(
            tmp_path, node_path="Main.Foundation.Defs", decl_name="ProofHelper"
        ),
        helper.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    assert runtime.lean_projection.sync_decl_file_after_revision_reset(
        tmp_path, node_path="Main.Results", decl_name="PublicResult"
    ).ok
    assert runtime.node.contract.ensure_open_contract(tmp_path, node_path="Main.Results").ok

    committed = runtime.node.commit_content_contract(
        tmp_path, node_path="Main.Results", summary="Attempt commit with deleted proof helper."
    )

    assert not committed.ok
    assert committed.issues[0].kind == "content_head_proof_dependency_invalid"


def test_node_delete_uses_release_and_current_truth_and_path_can_be_recreated(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    _publish_latest(runtime, tmp_path, versions)

    protected = runtime.node.preview_delete_node(tmp_path, path="Main.Results")
    assert protected.ok and protected.value is not None
    assert not protected.value.deletable
    assert "release_protected" in protected.value.blocking_reasons

    created = runtime.node.create_content_node(
        tmp_path,
        path="Main.Experimental",
        goal="Try an internal experiment.",
        boundary="Private experimental content.",
        objective="Build a disposable experiment.",
        success_criteria="The experiment is complete.",
    )
    assert created.ok and created.value is not None
    old_node_id = created.value.node_id
    committed = runtime.node.commit_content_contract(
        tmp_path, node_path="Main.Experimental", summary="Empty private experiment is complete."
    )
    assert committed.ok and committed.value is not None
    assert committed.value.contract.decl_graph_head == {}
    deleted = runtime.node.mark_node_deleted(tmp_path, path="Main.Experimental", reason="Replace experiment.")
    assert deleted.ok
    recreated = runtime.node.create_content_node(
        tmp_path,
        path="Main.Experimental",
        goal="Try a replacement experiment.",
        boundary="Private replacement content.",
        objective="Build a new experiment.",
        success_criteria="The replacement is complete.",
    )
    assert recreated.ok and recreated.value is not None
    assert recreated.value.node_id != old_node_id


def test_private_historical_node_is_warning_only_and_protected_count_is_closure_based(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    experimental = runtime.node.create_content_node(
        tmp_path,
        path="Main.Experimental",
        goal="Historical private experiment.",
        boundary="Private content.",
        objective="Complete private work.",
        success_criteria="Private work complete.",
    )
    assert experimental.ok and experimental.value is not None
    committed = runtime.node.commit_content_contract(
        tmp_path, node_path="Main.Experimental", summary="Private historical node complete."
    )
    assert committed.ok and committed.value is not None
    versions[experimental.value.node_id] = committed.value.version
    _publish_latest(runtime, tmp_path, versions)

    private_preview = runtime.node.preview_delete_node(tmp_path, path="Main.Experimental")
    scope_preview = runtime.node.preview_delete_node(tmp_path, path="Main.Foundation")

    assert private_preview.ok and private_preview.value is not None
    assert private_preview.value.deletable
    assert private_preview.value.public_decl_count == 0
    assert "historical:r1:node" in private_preview.value.inbound_refs
    assert scope_preview.ok and scope_preview.value is not None
    assert scope_preview.value.public_decl_count == 1
