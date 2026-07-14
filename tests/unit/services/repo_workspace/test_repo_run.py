from __future__ import annotations

from lean_constellation.app import create_app_runtime_services
from lean_constellation.app.bootstrap import initialize_repo_business_truth
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import ProofAvailability, RepoWorkMode
from lean_constellation.domain.repo import RepoPublicationState, RepoPublicationStatus
from lean_constellation.domain.repo_release import RepoRelease
from lean_constellation.services.foundation import FoundationContext
from lean_constellation.services.validation_snapshot import RepoCheckpointKind
from lean_constellation.services.validation_snapshot.snapshot_restore import (
    RepoCheckpointSnapshotManifest,
    SnapshotFileEntry,
    SnapshotFilesManifest,
)
from lean_constellation.domain.repo_run import SourceScope


def _repo(tmp_path):
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    root = tmp_path / "Provider"
    assert initialize_repo_business_truth(runtime, root).ok
    assert runtime.repo_workspace.preparation.write_preparation_input(
        root,
        input=RepoPreparationInput(
            goal="Build the declared provider interface.",
            source_corpus_mode=SourceCorpusMode.EXISTING,
            interface_inputs=[],
        ),
    ).ok
    assert runtime.repo_workspace.metadata.set_repo_format(root, repo_format="native", reason="test").ok
    return runtime, root


def test_initial_and_continuation_resolvers_have_distinct_scope_defaults(tmp_path) -> None:
    runtime, root = _repo(tmp_path)
    initial = runtime.repo_workspace.run.resolve_initial_repo_run_spec(
        root, max_parallel_content_node_tasks=3
    )
    continuation = runtime.repo_workspace.run.resolve_continuation_repo_run_spec(
        root,
        run_objective="Prove the released declarations.",
        max_parallel_content_node_tasks=5,
    )
    assert initial.ok and initial.value.source_scope == SourceScope(mode="all")
    assert continuation.ok and continuation.value.source_scope == SourceScope(mode="none")
    assert initial.value.max_parallel_content_node_tasks == 3
    assert continuation.value.max_parallel_content_node_tasks == 5
    assert not runtime.repo_workspace.run.resolve_continuation_repo_run_spec(root).ok


def test_apply_run_config_preserves_unrelated_fields_and_checks_base(tmp_path) -> None:
    runtime, root = _repo(tmp_path)
    assert runtime.repo_workspace.metadata.update_repo_config(
        root, default_requirement_proof_availability=ProofAvailability.PROVED,
    ).ok
    spec = runtime.repo_workspace.run.resolve_continuation_repo_run_spec(
        root,
        run_objective="Declare the selected interface.",
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
        max_parallel_content_node_tasks=7,
    ).value
    applied = runtime.repo_workspace.run.apply_repo_run_config(root, run_spec=spec, expected_base_release_id=None)
    assert applied.ok and applied.value is not None
    assert spec.max_parallel_content_node_tasks == 7
    assert "max_parallel_content_node_tasks" not in applied.value.config.model_dump()
    assert applied.value.config.default_requirement_proof_availability == ProofAvailability.PROVED
    assert not runtime.repo_workspace.run.apply_repo_run_config(
        root, run_spec=spec, expected_base_release_id="release-drift"
    ).ok


def test_transition_allows_target_upgrade_and_rejects_downgrade(tmp_path) -> None:
    runtime, root = _repo(tmp_path)
    release = RepoRelease(
        release_id="release-r1", node_contract_versions={"main": 1},
        target_proof_availability=ProofAvailability.PROVED,
        repo_checkpoint_id="repo-r1", summary="R1",
    )
    ctx = FoundationContext(repo_root=root)
    assert runtime.foundation.store.write_json_atomic(runtime.foundation.layout.release_path(ctx, "release-r1"), release).ok
    snapshot_root = runtime.foundation.layout.snapshot_root(ctx) / "repo_checkpoints" / "repo-r1"
    runtime.foundation.store.ensure_dir(snapshot_root)
    assert runtime.foundation.store.write_json_atomic(
        snapshot_root / "snapshot.json",
        RepoCheckpointSnapshotManifest(
            snapshot_id="repo-r1", checkpoint_kind=RepoCheckpointKind.BEFORE_NATIVE_RUN_MUTATION,
            created_at="2026-07-13T00:00:00Z", repo_root=str(root), ark_runtime_snapshot_id="ark-r1",
            files_manifest_relpath="files_manifest.json", summary="R1 checkpoint",
        ),
    ).ok
    assert runtime.foundation.store.write_json_atomic(
        snapshot_root / "files_manifest.json",
        SnapshotFilesManifest(summary="empty fixture archive"),
    ).ok
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(root),
        RepoPublicationState(status=RepoPublicationStatus.STABLE, latest_release_id="release-r1"),
    ).ok
    downgrade = runtime.repo_workspace.run.resolve_continuation_repo_run_spec(
        root, run_objective="Downgrade.", target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
    ).value
    report = runtime.repo_workspace.run.validate_repo_run_transition(
        root, run_spec=downgrade, start_kind="continuation", base_release_id="release-r1"
    )
    assert report.ok and report.value is not None and not report.value.passed
    assert {issue.kind for issue in report.value.issues} == {"repo_run_target_downgrade"}
    assert runtime.repo_workspace.metadata.mark_repo_developing(root).ok
    resume = runtime.repo_workspace.run.resolve_continuation_repo_run_spec(
        root, run_objective="Resume after standalone preprocessing."
    ).value
    resumed = runtime.repo_workspace.run.validate_repo_run_transition(
        root, run_spec=resume, start_kind="continuation", base_release_id="release-r1"
    )
    assert resumed.ok and resumed.value is not None and resumed.value.passed

    assert runtime.foundation.store.write_json_atomic(
        snapshot_root / "files_manifest.json",
        SnapshotFilesManifest(
            entries=[SnapshotFileEntry(
                source_relpath="lakefile.toml",
                archive_relpath="lakefile.toml",
                file_size=1,
                sha256="0" * 64,
            )],
            summary="corrupted archive fixture",
        ),
    ).ok
    corrupted = runtime.repo_workspace.run.validate_repo_run_transition(
        root, run_spec=resume, start_kind="continuation", base_release_id="release-r1"
    )
    assert corrupted.ok and corrupted.value is not None and not corrupted.value.passed
    assert {issue.kind for issue in corrupted.value.issues} == {"release_baseline_corrupt"}
