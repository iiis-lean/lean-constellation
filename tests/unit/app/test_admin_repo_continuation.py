from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from agent_runtime_kit.flow.models import FlowRequest

from lean_constellation.app import LeanAdminApi, RepoRunRequestInput, create_app_runtime_services, initialize_repo_runtime
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import RepoPublicationState, RepoPublicationStatus


def test_registry_lock_plus_semantic_start_allows_only_one_concurrent_continuation(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    root = tmp_path / "Provider"
    assert initialize_repo_runtime(runtime, root).ok
    assert runtime.repo_workspace.preparation.write_preparation_input(
        root, input=RepoPreparationInput(goal="Continue.", source_corpus_mode=SourceCorpusMode.EXISTING, interface_inputs=[])
    ).ok
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(root),
        RepoPublicationState(status=RepoPublicationStatus.STABLE, latest_release_id="release-r1"),
    ).ok
    admin = LeanAdminApi(runtime)
    request = RepoRunRequestInput(repo_root=root, run_objective="Continue the proof.", enqueue=False)
    barrier = Barrier(2)

    def start():
        barrier.wait()
        return admin.continue_native_repo(request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in [pool.submit(start), pool.submit(start)]]
    assert sum(result.ok for result in results) == 1
    failed = next(result for result in results if not result.ok)
    assert failed.issues[0].kind in {"repo_lifecycle_flow_conflict", "repo_lifecycle_lock_busy"}
    status = admin.get_repo_run_status(root)
    assert status.ok and status.value is not None
    assert status.value.active_flow_type == "native_repo_continuation"
    assert status.value.run_spec is not None
    assert status.value.run_spec.run_objective == "Continue the proof."


def test_continuation_rejects_any_active_repo_scoped_flow(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    root = tmp_path / "Provider"
    assert initialize_repo_runtime(runtime, root).ok
    active_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="content_node_task",
            scope_id="repo:Provider",
            params={"repo_key": "Provider", "repo_path": str(root), "node_path": "Main.Core"},
        ),
        enqueue=False,
    )
    result = LeanAdminApi(runtime).continue_native_repo(
        RepoRunRequestInput(repo_root=root, run_objective="Continue the proof.", enqueue=False)
    )
    assert not result.ok
    assert result.issues[0].kind == "repo_lifecycle_flow_conflict"
    assert result.issues[0].object_ref == active_id
