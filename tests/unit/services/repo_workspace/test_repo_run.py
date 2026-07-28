from __future__ import annotations

from lean_constellation.app import create_app_runtime_services
from lean_constellation.app.bootstrap import initialize_repo_business_truth
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import ProofAvailability, RepoCompletionMode
from lean_constellation.domain.repo import RepoPublicationState, RepoPublicationStatus
from lean_constellation.domain.repo_release import RepoRelease
from lean_constellation.services.foundation import FoundationContext
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
        completion_mode=RepoCompletionMode.INTERFACE_DECLARED,
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
        completion_mode=RepoCompletionMode.GRAPH_PROVED,
        semantic_manifest_digest="1" * 64,
        dependency_lock_digest="2" * 64,
        summary="R1",
    )
    ctx = FoundationContext(repo_root=root)
    assert runtime.foundation.store.write_json_atomic(runtime.foundation.layout.release_path(ctx, "release-r1"), release).ok
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(root),
        RepoPublicationState(status=RepoPublicationStatus.STABLE, latest_release_id="release-r1"),
    ).ok
    initialized = runtime.repo_workspace.git_release.ensure_independent_repo(root)
    assert initialized.ok and initialized.value is not None
    committed = runtime.repo_workspace.git_release.commit_release(
        root,
        release=release,
        candidate_files=[
            path.relative_to(root).as_posix()
            for path in runtime.validation_snapshot.release_finalizer._candidate_files(root)
        ],
        expected_head=initialized.value.head_commit,
    )
    assert committed.ok
    downgrade = runtime.repo_workspace.run.resolve_continuation_repo_run_spec(
        root,
        run_objective="Downgrade.",
        completion_mode=RepoCompletionMode.INTERFACE_DECLARED,
    ).value
    report = runtime.repo_workspace.run.validate_repo_run_transition(
        root, run_spec=downgrade, start_kind="continuation", base_release_id="release-r1"
    )
    assert report.ok and report.value is not None and not report.value.passed
    assert {issue.kind for issue in report.value.issues} == {"repo_run_completion_downgrade"}
    assert runtime.repo_workspace.metadata.mark_repo_developing(root).ok
    resume = runtime.repo_workspace.run.resolve_continuation_repo_run_spec(
        root, run_objective="Resume after standalone preprocessing."
    ).value
    resumed = runtime.repo_workspace.run.validate_repo_run_transition(
        root, run_spec=resume, start_kind="continuation", base_release_id="release-r1"
    )
    assert resumed.ok and resumed.value is not None and resumed.value.passed

    deleted = runtime.repo_workspace.git_release.delete_release_ref(
        root,
        release_id=release.release_id,
    )
    assert deleted.ok and deleted.value is True
    corrupted = runtime.repo_workspace.run.validate_repo_run_transition(
        root, run_spec=resume, start_kind="continuation", base_release_id="release-r1"
    )
    assert corrupted.ok and corrupted.value is not None and not corrupted.value.passed
    assert {issue.kind for issue in corrupted.value.issues} == {"release_baseline_corrupt"}
