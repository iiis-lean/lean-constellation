from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.refs import DeclRef, MaterialRef, NodeRef, ResourceRef, SourceRef
from lean_constellation.domain.repo import RepoCompletionMode, RepoFormat, RepoModel, RepoPublicationStatus
from lean_constellation.domain.repo_release import RepoRelease
from lean_constellation.services.external_clients import ToolchainCommandView
from lean_constellation.services.foundation import FoundationContext
from lean_constellation.services.foundation import WriteMode
from lean_constellation.services.node import NodeKind
from lean_constellation.services.node.contract_fields import ContractMaterialRef, NodeDep
from lean_constellation.services.validation_snapshot import (
    CandidateReleaseGateView,
    PreparedRepoReleaseView,
    RepoCheckpointKind,
    RepoCheckpointSnapshotManifest,
    ValidationSnapshotService,
)
from tests.unit.services.repo_workspace.test_repo_release import _prepare_release_repo


def _prepared_repo(repo_root: Path):
    runtime, versions = _prepare_release_repo(repo_root)
    runtime.ark.flow_service = SimpleNamespace(list_flows=lambda **_filters: [])
    runtime.ark.step_service = SimpleNamespace(list_steps=lambda **_filters: [])
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
    runtime.app.validation_snapshot = ValidationSnapshotService(runtime)
    release = RepoRelease(
        release_id="release_r1",
        node_contract_versions=versions,
        completion_mode=RepoCompletionMode.GRAPH_DECLARED,
        semantic_manifest_digest=runtime.validation_snapshot.release_finalizer.compute_semantic_manifest_digest(
            repo_root
        ),
        dependency_lock_digest=runtime.validation_snapshot.release_finalizer.compute_dependency_lock_digest(
            repo_root
        ),
        summary="Release one.",
    )
    publication = runtime.repo_workspace.metadata.get_repo_publication(repo_root).value.publication.model_copy(
        update={"status": RepoPublicationStatus.STABLE, "latest_release_id": release.release_id}
    )
    prepared = PreparedRepoReleaseView(
        release=release,
        publication=publication,
        candidate_digest=runtime.validation_snapshot.release_finalizer.compute_candidate_digest(repo_root),
        build=ToolchainCommandView(ok=True, command=["lake", "build"], summary="built", exit_code=0),
        gate=runtime.foundation.gate_passed("candidate_repo_release", summary="passed"),
        summary="prepared",
    )
    return runtime, prepared, None


def test_release_commit_publishes_final_truth_checkpoint_and_unique_latest(tmp_path: Path) -> None:
    runtime, prepared, snapshots = _prepared_repo(tmp_path)

    finalized = runtime.validation_snapshot.release_finalizer.commit_prepared_release(
        tmp_path, prepared=prepared
    )

    assert finalized.ok and finalized.value is not None
    assert finalized.value.git_release.release_id == prepared.release.release_id
    assert finalized.value.checkpoint.ark_runtime_snapshot_id is None
    publication = runtime.repo_workspace.metadata.get_repo_publication(tmp_path).value.publication
    assert publication.status == RepoPublicationStatus.STABLE
    assert publication.latest_release_id == prepared.release.release_id
    assert runtime.repo_workspace.release.get_release(tmp_path, release_id=prepared.release.release_id).ok
    checkpoint = Path(finalized.value.checkpoint.root)
    archived_publication = runtime.foundation.store.read_json(
        checkpoint / "files" / "lean_constellation" / "repo_publication.json",
        type(publication),
    )
    archived_release = runtime.foundation.store.read_json(
        checkpoint / "files" / "lean_constellation" / "releases" / f"{prepared.release.release_id}.json",
        RepoRelease,
    )
    archived_model = runtime.foundation.store.read_json(
        checkpoint / "files" / "lean_constellation" / "repo.json", RepoModel
    )
    assert archived_publication.ok and archived_publication.value == publication
    assert archived_release.ok and archived_release.value == prepared.release
    assert archived_model.ok and archived_model.value.summary == prepared.release.summary
    assert runtime.repo_workspace.git_release.validate_release(
        tmp_path,
        release=prepared.release,
    ).ok
    audit = runtime.validation_snapshot.audit_repo_release_storage(tmp_path)
    assert audit.ok and audit.value is not None and audit.value.passed


def test_publication_commit_failure_leaves_no_release_or_checkpoint(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    runtime, prepared, _ = _prepared_repo(tmp_path)
    publication_path = runtime.repo_workspace.metadata._repo_publication_path(tmp_path)
    original = runtime.foundation.store.write_json_atomic

    def fail_publication(path, value, **kwargs):  # noqa: ANN001
        if Path(path) == publication_path and getattr(value, "latest_release_id", None) == prepared.release.release_id:
            return runtime.foundation.fail(runtime.foundation.issue("injected_replace_failure", "replace failed"))
        return original(path, value, **kwargs)

    monkeypatch.setattr(runtime.foundation.store, "write_json_atomic", fail_publication)
    finalized = runtime.validation_snapshot.release_finalizer.commit_prepared_release(
        tmp_path, prepared=prepared
    )

    assert not finalized.ok
    release_path = runtime.foundation.layout.release_path(
        FoundationContext(repo_root=tmp_path), prepared.release.release_id
    )
    assert not release_path.exists()
    assert runtime.repo_workspace.git_release.resolve_release_commit(
        tmp_path,
        release_id=prepared.release.release_id,
    ).ok is False
    publication = runtime.repo_workspace.metadata.get_repo_publication(tmp_path).value.publication
    assert publication.status == RepoPublicationStatus.DEVELOPING
    assert publication.latest_release_id is None


@pytest.mark.parametrize("commit_failure", ["release_create", "publication_replace"])
def test_repo_commit_boundary_failures_never_leave_dangling_latest(
    tmp_path: Path, monkeypatch, commit_failure: str
) -> None:  # noqa: ANN001
    runtime, prepared, _ = _prepared_repo(tmp_path)
    if commit_failure == "release_create":
        monkeypatch.setattr(
            runtime.repo_workspace.release,
            "create_release",
            lambda *args, **kwargs: runtime.foundation.fail(
                runtime.foundation.issue("injected_release_create", "create")
            ),
        )
    else:
        import lean_constellation.services.foundation.store as store_module

        publication_path = runtime.repo_workspace.metadata._repo_publication_path(tmp_path)
        original_replace = store_module.os.replace

        def fail_publication_replace(source, target):  # noqa: ANN001
            if Path(target) == publication_path:
                raise OSError("publication replace")
            return original_replace(source, target)

        monkeypatch.setattr(store_module.os, "replace", fail_publication_replace)

    result = runtime.validation_snapshot.release_finalizer.commit_prepared_release(
        tmp_path, prepared=prepared
    )
    assert not result.ok
    publication = runtime.repo_workspace.metadata.get_repo_publication(tmp_path).value.publication
    assert publication.status == RepoPublicationStatus.DEVELOPING
    assert publication.latest_release_id is None


@pytest.mark.parametrize("release_write_failure", ["before_replace", "after_replace_parent_fsync"])
def test_release_create_durability_failures_cleanup_exact_artifacts(
    tmp_path: Path, monkeypatch, release_write_failure: str
) -> None:  # noqa: ANN001
    runtime, prepared, _ = _prepared_repo(tmp_path)
    release_path = runtime.foundation.layout.release_path(
        FoundationContext(repo_root=tmp_path), prepared.release.release_id
    )
    if release_write_failure == "before_replace":
        import lean_constellation.services.foundation.store as store_module

        original_replace = store_module.os.replace

        def fail_release_replace(source, target):  # noqa: ANN001
            if Path(target) == release_path:
                raise OSError("release replace")
            return original_replace(source, target)

        monkeypatch.setattr(store_module.os, "replace", fail_release_replace)
    else:
        original_fsync = runtime.foundation.store._fsync_parent

        def fail_release_fsync(path):  # noqa: ANN001
            if Path(path) == release_path:
                raise OSError("release parent fsync")
            return original_fsync(path)

        monkeypatch.setattr(runtime.foundation.store, "_fsync_parent", fail_release_fsync)
    result = runtime.validation_snapshot.release_finalizer.commit_prepared_release(
        tmp_path, prepared=prepared
    )
    assert not result.ok
    assert not release_path.exists()
    assert not runtime.repo_workspace.git_release.resolve_release_commit(
        tmp_path,
        release_id=prepared.release.release_id,
    ).ok
    publication = runtime.repo_workspace.metadata.get_repo_publication(tmp_path).value.publication
    assert publication.status == RepoPublicationStatus.DEVELOPING
    assert publication.latest_release_id is None


def test_release_create_conflicting_existing_payload_is_never_deleted(
    tmp_path: Path,
) -> None:
    runtime, prepared, _ = _prepared_repo(tmp_path)
    release_path = runtime.foundation.layout.release_path(
        FoundationContext(repo_root=tmp_path), prepared.release.release_id
    )
    conflicting = prepared.release.model_copy(update={"summary": "pre-existing conflict"})
    assert runtime.foundation.store.write_json_atomic(release_path, conflicting).ok
    prepared = prepared.model_copy(update={
        "candidate_digest": runtime.validation_snapshot.release_finalizer.compute_candidate_digest(tmp_path)
    })
    result = runtime.validation_snapshot.release_finalizer.commit_prepared_release(
        tmp_path, prepared=prepared
    )
    assert not result.ok
    assert result.issues[0].kind == "release_identity_conflict"
    assert runtime.foundation.store.read_json(release_path, RepoRelease).value == conflicting
    assert not runtime.repo_workspace.git_release.resolve_release_commit(
        tmp_path,
        release_id=prepared.release.release_id,
    ).ok
    assert runtime.repo_workspace.metadata.get_repo_publication(tmp_path).value.publication.latest_release_id is None


def test_notification_failure_does_not_rollback_and_retry_is_idempotent(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    runtime, prepared, snapshots = _prepared_repo(tmp_path)
    finalizer = runtime.validation_snapshot.release_finalizer
    original = finalizer.reconcile_provider_requirements
    monkeypatch.setattr(
        finalizer,
        "reconcile_provider_requirements",
        lambda *args, **kwargs: runtime.foundation.fail(
            runtime.foundation.issue("injected_consumer_notify_failure", "notify failed")
        ),
    )

    first = finalizer.commit_prepared_release(
        tmp_path, prepared=prepared
    )
    assert first.ok and first.value is not None and first.value.notification_pending
    assert runtime.repo_workspace.metadata.get_repo_publication(tmp_path).value.publication.latest_release_id == "release_r1"

    monkeypatch.setattr(finalizer, "reconcile_provider_requirements", original)
    retried = finalizer.commit_prepared_release(
        tmp_path, prepared=prepared
    )
    assert retried.ok and retried.value is not None
    assert len(runtime.repo_workspace.release.list_releases(tmp_path).value) == 1


@pytest.mark.parametrize("retry_failure", ["release", "model", "publication", "reconciliation"])
def test_committed_retry_read_failures_return_pending_success(
    tmp_path: Path, monkeypatch, retry_failure: str
) -> None:  # noqa: ANN001
    runtime, prepared, _ = _prepared_repo(tmp_path)
    first = runtime.validation_snapshot.release_finalizer.commit_prepared_release(
        tmp_path, prepared=prepared
    )
    assert first.ok
    if retry_failure == "release":
        monkeypatch.setattr(
            runtime.repo_workspace.release,
            "get_release",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("release read")),
        )
    elif retry_failure == "model":
        monkeypatch.setattr(
            runtime.repo_workspace.metadata,
            "get_repo_model",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("model read")),
        )
    elif retry_failure == "publication":
        original = runtime.repo_workspace.metadata.get_repo_publication
        calls = 0

        def fail_second(*args, **kwargs):  # noqa: ANN001
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError("publication read")
            return original(*args, **kwargs)

        monkeypatch.setattr(runtime.repo_workspace.metadata, "get_repo_publication", fail_second)
    else:
        monkeypatch.setattr(
            runtime.validation_snapshot.release_finalizer,
            "reconcile_provider_requirements",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("reconcile")),
        )
    retried = runtime.validation_snapshot.release_finalizer.commit_prepared_release(
        tmp_path, prepared=prepared
    )
    assert retried.ok and retried.value is not None
    assert runtime.foundation.store.read_json(
        runtime.repo_workspace.metadata._repo_publication_path(tmp_path), type(prepared.publication)
    ).value.latest_release_id == prepared.release.release_id


def test_committed_retry_rejects_readable_release_payload_conflict(tmp_path: Path) -> None:
    runtime, prepared, _ = _prepared_repo(tmp_path)
    assert runtime.validation_snapshot.release_finalizer.commit_prepared_release(
        tmp_path, prepared=prepared
    ).ok
    release_path = runtime.foundation.layout.release_path(
        FoundationContext(repo_root=tmp_path), prepared.release.release_id
    )
    conflicting = prepared.release.model_copy(update={"summary": "conflicting live payload"})
    assert runtime.foundation.store.write_json_atomic(
        release_path, conflicting, mode=WriteMode.OVERWRITE
    ).ok
    retried = runtime.validation_snapshot.release_finalizer.commit_prepared_release(
        tmp_path, prepared=prepared
    )
    assert not retried.ok
    assert retried.issues[0].kind == "release_identity_conflict"


def test_git_release_manifest_readback_detects_live_truth_conflict(tmp_path: Path) -> None:
    runtime, prepared, _ = _prepared_repo(tmp_path)
    assert runtime.validation_snapshot.release_finalizer.commit_prepared_release(
        tmp_path,
        prepared=prepared,
    ).ok
    conflicting_release = prepared.release.model_copy(update={"summary": "Different release summary."})
    conflict = runtime.repo_workspace.git_release.validate_release(
        tmp_path,
        release=conflicting_release,
    )

    assert not conflict.ok
    assert conflict.issues[0].kind == "git_release_manifest_mismatch"


@pytest.mark.parametrize(
    "postcommit_failure", ["git_readback", "checkpoint"]
)
def test_postcommit_failures_retain_stable_release(
    tmp_path: Path, monkeypatch, postcommit_failure: str
) -> None:  # noqa: ANN001
    runtime, prepared, _ = _prepared_repo(tmp_path)
    if postcommit_failure == "git_readback":
        monkeypatch.setattr(
            runtime.repo_workspace.git_release,
            "validate_release",
            lambda *args, **kwargs: runtime.foundation.fail(
                runtime.foundation.issue("injected_git_readback", "readback")
            ),
        )
    else:
        monkeypatch.setattr(
            runtime.validation_snapshot.release_finalizer,
            "_create_post_release_checkpoint",
            lambda *args, **kwargs: runtime.foundation.fail(
                runtime.foundation.issue("injected_checkpoint_failure", "checkpoint")
            ),
        )

    result = runtime.validation_snapshot.release_finalizer.commit_prepared_release(
        tmp_path, prepared=prepared
    )
    assert result.ok and result.value is not None
    publication = runtime.foundation.store.read_json(
        runtime.repo_workspace.metadata._repo_publication_path(tmp_path), type(prepared.publication)
    )
    assert publication.ok and publication.value.latest_release_id == prepared.release.release_id
    assert runtime.foundation.layout.release_path(
        FoundationContext(repo_root=tmp_path), prepared.release.release_id
    ).exists()


def test_lake_build_failure_does_not_allocate_release_truth(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    runtime, prepared, _ = _prepared_repo(tmp_path)
    finalizer = runtime.validation_snapshot.release_finalizer
    preview = CandidateReleaseGateView(
        candidate_node_contract_versions=prepared.release.node_contract_versions,
        completion_mode=RepoCompletionMode.GRAPH_DECLARED,
        build=ToolchainCommandView(
            ok=False,
            command=["lake", "build"],
            summary="failed",
            exit_code=1,
            stderr_excerpt="compile error",
            issue_code="lake_build_failed",
        ),
        gate=runtime.foundation.gate_failed(
            "candidate_repo_release",
            runtime.foundation.issue(
                "release_lake_build_failed",
                "The candidate repository failed the required Lake build.",
            ),
            summary="failed",
        ),
        blocking_issue_kinds=["release_lake_build_failed"],
        summary="failed",
    )
    monkeypatch.setattr(finalizer, "preview_candidate_release", lambda *args, **kwargs: runtime.foundation.ok(preview))
    result = finalizer.prepare_candidate_release(
        tmp_path, base_release_id=None, summary="candidate"
    )

    assert result.ok and result.value is not None and result.value.outcome == "blocked"
    assert result.value.blocking_issue_kinds == ["release_lake_build_failed"]
    assert runtime.repo_workspace.release.list_releases(tmp_path).value == []


def test_candidate_gate_aggregates_non_main_contract_tree_and_material_findings(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    runtime, _ = _prepare_release_repo(tmp_path)
    build_targets: list[str | None] = []
    original_build = runtime.external.lean_toolchain.run_lake_build

    def record_build(repo_root, target=None, **kwargs):  # noqa: ANN001
        build_targets.append(target)
        return original_build(repo_root, target=target, **kwargs)

    monkeypatch.setattr(
        runtime.external.lean_toolchain,
        "run_lake_build",
        record_build,
    )
    assert runtime.repo_workspace.metadata.set_repo_format(
        tmp_path, repo_format=RepoFormat.NATIVE, reason="candidate test"
    ).ok
    monkeypatch.setattr(
        runtime.validation_snapshot.readiness_gate,
        "check_repo_ready",
        lambda *args, **kwargs: runtime.foundation.ok(
            runtime.foundation.gate_passed("repo_ready", summary="ordinary pass")
        ),
    )
    content = runtime.node.contract.get_visible_contract(tmp_path, node_path="Main.Foundation.Defs").value
    content.contract.deps = [
        NodeDep(dep_id="missing", target=NodeRef(node="Main.Missing"), reason="invalid"),
        NodeDep(
            dep_id="external_missing",
            target=NodeRef(repo="MissingProvider", node="Main"),
            reason="invalid Lake dependency",
        ),
    ]
    content.contract.owned_refs = [
        ContractMaterialRef(ref_id="source", ref=MaterialRef(kind="source", ref=SourceRef(path="missing.tex"))),
        ContractMaterialRef(ref_id="resource", ref=MaterialRef(kind="resource", ref=ResourceRef(resource_key="missing"))),
    ]
    content_path = runtime.node.node_tree.node_store.contract_path(
        tmp_path, node_id=content.node_id, version=content.version
    )
    assert runtime.foundation.store.write_json_atomic(
        content_path, content.contract, mode=WriteMode.UPDATE_EXISTING
    ).ok
    main = runtime.node.contract.get_visible_contract(tmp_path, node_path="Main").value
    missing_ref = DeclRef(node="Main.Results", name="Missing", revision=1)
    main.contract.exports.append(missing_ref)
    main.contract.interfaces.append(DeclInterface(
        name="missing", kind=DeclKind.THEOREM, summary="Missing.", bound_decl=missing_ref
    ))
    main_path = runtime.node.node_tree.node_store.contract_path(
        tmp_path, node_id=main.node_id, version=main.version
    )
    assert runtime.foundation.store.write_json_atomic(
        main_path, main.contract, mode=WriteMode.UPDATE_EXISTING
    ).ok
    foundation = runtime.node.node_tree.node_store.resolve_active_node(tmp_path, path="Main.Foundation").value
    foundation.kind = NodeKind.CONTENT
    assert runtime.node.node_tree.node_store.save_node(
        tmp_path, foundation, mode=WriteMode.UPDATE_EXISTING
    ).ok

    preview = runtime.validation_snapshot.release_finalizer.preview_candidate_release(
        tmp_path, base_release_id=None, summary="candidate"
    )

    assert preview.ok and preview.value is not None and not preview.value.gate.passed
    assert build_targets == [None]
    kinds = set(preview.value.blocking_issue_kinds)
    assert "node_dep_target_missing" in kinds
    assert "node_dep_external_lake_dependency_missing" in kinds
    assert "material_ref_invalid" in kinds
    assert "content_node_has_children" in kinds or "node_parent_not_scope" in kinds
    assert kinds & {
        "scope_export_decl_not_ready", "decl_not_found", "interfaces_projection_stale",
        "scope_export_candidate_missing", "interface_binding_not_exported", "scope_export_not_public",
    }, kinds


def test_release_business_closeout_does_not_inspect_runtime(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    runtime, _ = _prepare_release_repo(tmp_path)
    finalizer = runtime.validation_snapshot.release_finalizer
    monkeypatch.setattr(runtime, "list_flows", lambda: (_ for _ in ()).throw(AssertionError("ARK accessed")))
    monkeypatch.setattr(runtime, "list_steps", lambda: (_ for _ in ()).throw(AssertionError("ARK accessed")))

    closeout = finalizer._check_requirement_closeout(tmp_path)

    assert closeout.ok and closeout.value is not None and closeout.value.passed


def test_audit_finds_and_cleanup_removes_unreachable_release(tmp_path: Path) -> None:
    runtime, prepared, _ = _prepared_repo(tmp_path)
    committed = runtime.validation_snapshot.release_finalizer.commit_prepared_release(
        tmp_path, prepared=prepared
    )
    assert committed.ok
    orphan = RepoRelease(
        release_id="release_orphan",
        node_contract_versions=prepared.release.node_contract_versions,
        completion_mode=RepoCompletionMode.GRAPH_DECLARED,
        semantic_manifest_digest="3" * 64,
        dependency_lock_digest="4" * 64,
        summary="Orphan.",
    )
    assert runtime.repo_workspace.release.create_release(tmp_path, release=orphan).ok

    audit = runtime.validation_snapshot.audit_repo_release_storage(tmp_path)
    assert audit.ok and audit.value is not None and not audit.value.passed
    assert audit.value.orphan_release_ids == ["release_orphan"]
    cleaned = runtime.validation_snapshot.cleanup_unpublished_release_artifacts(
        tmp_path, release_id="release_orphan"
    )
    assert cleaned.ok and cleaned.value is not None and cleaned.value.changed
    assert not runtime.repo_workspace.release.get_release(tmp_path, release_id="release_orphan").ok
    assert runtime.validation_snapshot.audit_repo_release_storage(tmp_path).value.passed


def test_release_audit_ignores_non_release_checkpoints(tmp_path: Path) -> None:
    runtime, _, _ = _prepared_repo(tmp_path)
    checkpoint_root = runtime.validation_snapshot.snapshot_restore._snapshot_root(tmp_path)
    ordinary = checkpoint_root / "repo_cp_ordinary"
    ordinary.mkdir(parents=True)
    assert runtime.foundation.store.write_json_atomic(
        ordinary / "snapshot.json",
        RepoCheckpointSnapshotManifest(
            snapshot_id=ordinary.name,
            checkpoint_kind=RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH,
            created_at="2026-07-17T00:00:00Z",
            repo_root=str(tmp_path),
            ark_runtime_snapshot_id=None,
            files_manifest_relpath="files_manifest.json",
            summary="Ordinary automatic checkpoint.",
        ),
    ).ok

    audit = runtime.validation_snapshot.audit_repo_release_storage(tmp_path)

    assert audit.ok and audit.value is not None and audit.value.passed
    assert audit.value.orphan_checkpoint_ids == []


def test_digest_guarded_bulk_cleanup_only_removes_unreferenced_checkpoint_and_staging(
    tmp_path: Path,
) -> None:
    runtime, prepared, _ = _prepared_repo(tmp_path)
    assert runtime.validation_snapshot.release_finalizer.commit_prepared_release(
        tmp_path, prepared=prepared
    ).ok
    orphan_release = RepoRelease(
        release_id="release_orphan",
        node_contract_versions=prepared.release.node_contract_versions,
        completion_mode=RepoCompletionMode.GRAPH_DECLARED,
        semantic_manifest_digest="3" * 64,
        dependency_lock_digest="4" * 64,
        summary="Orphan committed release retained by bulk cleanup.",
    )
    assert runtime.repo_workspace.release.create_release(tmp_path, release=orphan_release).ok
    checkpoint_root = runtime.validation_snapshot.snapshot_restore._snapshot_root(tmp_path)
    staging = checkpoint_root / ".staging" / "interrupted"
    staging.mkdir(parents=True)

    audit = runtime.validation_snapshot.audit_repo_release_storage(tmp_path)
    assert audit.ok and audit.value is not None
    assert audit.value.orphan_release_ids == ["release_orphan"]
    assert audit.value.orphan_checkpoint_ids == []

    (checkpoint_root / ".staging" / "concurrent").mkdir()
    stale = runtime.validation_snapshot.cleanup_repo_release_orphans(
        tmp_path, expected_audit_digest=audit.value.audit_digest
    )
    assert not stale.ok and stale.issues[0].kind == "release_audit_digest_mismatch"
    assert staging.exists()

    current = runtime.validation_snapshot.audit_repo_release_storage(tmp_path).value
    cleaned = runtime.validation_snapshot.cleanup_repo_release_orphans(
        tmp_path, expected_audit_digest=current.audit_digest
    )
    assert cleaned.ok and cleaned.value is not None and cleaned.value.changed
    assert not staging.exists()
    assert runtime.repo_workspace.release.get_release(
        tmp_path, release_id="release_orphan"
    ).ok


def test_cleanup_failure_and_concurrent_lock_are_reported(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    runtime, prepared, _ = _prepared_repo(tmp_path)
    orphan = RepoRelease(
        release_id="release_orphan",
        node_contract_versions=prepared.release.node_contract_versions,
        completion_mode=RepoCompletionMode.GRAPH_DECLARED,
        semantic_manifest_digest="3" * 64,
        dependency_lock_digest="4" * 64,
        summary="Orphan.",
    )
    assert runtime.repo_workspace.release.create_release(tmp_path, release=orphan).ok
    checkpoint = runtime.validation_snapshot.snapshot_restore._snapshot_dir(tmp_path, "checkpoint_orphan")
    checkpoint.mkdir(parents=True)
    monkeypatch.setattr(
        runtime.validation_snapshot.release_finalizer,
        "_remove_unpublished_release",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("cleanup")),
    )
    failed = runtime.validation_snapshot.cleanup_unpublished_release_artifacts(
        tmp_path, release_id="release_orphan"
    )
    assert not failed.ok and failed.issues[0].kind == "release_cleanup_failed"
    assert checkpoint.exists()

    with runtime.repo_workspace.lifecycle_lock.locked(tmp_path):
        concurrent = runtime.validation_snapshot.cleanup_unpublished_release_artifacts(
            tmp_path, checkpoint_id="checkpoint_orphan"
        )
    assert not concurrent.ok and concurrent.issues[0].kind == "release_cleanup_failed"


def test_actual_consumer_requirement_save_failure_is_postcommit_pending(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    provider = tmp_path / "Provider"
    consumer = tmp_path / "Consumer"
    runtime, prepared, _ = _prepared_repo(provider)
    assert runtime.repo_workspace.metadata.set_repo_format(
        provider, repo_format=RepoFormat.NATIVE, reason="test"
    ).ok
    assert runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    requirement = runtime.repo_workspace.requirement.create_requirement(
        consumer, name="need_provider", target_repo="Provider", source_description="Need provider.", reason="test"
    )
    assert requirement.ok
    assert runtime.repo_workspace.preparation.write_preparation_input(
        provider,
        input=RepoPreparationInput(
            goal="Provide dependency.",
            source_corpus_mode=SourceCorpusMode.PREPARE,
            requirement_refs=[{"consumer_repo": "Consumer", "requirement_name": "need_provider"}],
        ),
    ).ok
    prepared = prepared.model_copy(
        update={
            "candidate_digest": runtime.validation_snapshot.release_finalizer.compute_candidate_digest(
                provider
            ),
            "release": prepared.release.model_copy(
                update={
                    "semantic_manifest_digest": (
                        runtime.validation_snapshot.release_finalizer.compute_semantic_manifest_digest(
                            provider
                        )
                    ),
                    "dependency_lock_digest": (
                        runtime.validation_snapshot.release_finalizer.compute_dependency_lock_digest(
                            provider
                        )
                    ),
                }
            ),
        }
    )
    requirement_path = runtime.foundation.layout.requirement_path(
        FoundationContext(repo_root=consumer), "need_provider"
    )
    original_write = runtime.foundation.store.write_json_atomic

    def fail_satisfied_save(path, value, **kwargs):  # noqa: ANN001
        if Path(path) == requirement_path and getattr(value, "status", None) == "satisfied":
            return runtime.foundation.fail(runtime.foundation.issue("injected_requirement_save", "save"))
        return original_write(path, value, **kwargs)

    monkeypatch.setattr(runtime.foundation.store, "write_json_atomic", fail_satisfied_save)
    result = runtime.validation_snapshot.release_finalizer.commit_prepared_release(
        provider, prepared=prepared
    )
    assert result.ok and result.value is not None and result.value.notification_pending
    publication = runtime.repo_workspace.metadata.get_repo_publication(provider).value.publication
    assert publication.status == RepoPublicationStatus.STABLE
    assert publication.latest_release_id == prepared.release.release_id
