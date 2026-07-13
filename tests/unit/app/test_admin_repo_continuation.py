from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from lean_constellation.app import (
    LeanAdminApi,
    RepoRunRequestInput,
    StartPreparationInput,
    create_app_runtime_services,
    initialize_repo_runtime,
)
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import RepoPublicationState, RepoPublicationStatus
from lean_constellation.domain.repo import ProofAvailability, RepoWorkMode
from lean_constellation.domain.repo_run import RepoRunSpec, SourceScope


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


def test_nonterminal_legacy_native_preparation_requires_checkpointed_initial_restart(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    root = tmp_path / "Provider"
    assert initialize_repo_runtime(runtime, root).ok
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_preparation",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(root),
                "run_spec": RepoRunSpec(
                    run_objective="Temporary modern payload.",
                    target_proof_availability=ProofAvailability.DECLARED,
                    work_mode=RepoWorkMode.DECLARED_INTERFACE,
                    source_scope=SourceScope(mode="all"),
                    index_policy="auto",
                    root_interface_policy="auto",
                ).model_dump(mode="json"),
            },
        ),
        enqueue=False,
    )
    runtime.ark.flow_service.store.update_flow_record(
        flow_id, lambda record: setattr(record.input, "run_spec", None)
    )
    flow = runtime.ark.flow_service.get_flow(flow_id)
    before_phase = flow.state.position.phase

    result = LeanAdminApi(runtime).continue_native_repo(
        RepoRunRequestInput(repo_root=root, run_objective="Continue.", enqueue=False)
    )

    assert not result.ok
    assert result.issues[0].kind == "legacy_native_preparation_restart_required"
    assert flow.input.run_spec is None and flow.state.position.phase == before_phase


def _remove_serialized_run_spec(runtime, flow_id: str) -> tuple[Path, bytes]:  # noqa: ANN001
    path = runtime.ark.flow_service.store.resolve_flow_path(flow_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["input"].pop("run_spec", None)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path, path.read_bytes()


def test_serialized_nonterminal_legacy_preparation_is_restart_required_without_rewrite(tmp_path) -> None:
    runtime_root = tmp_path / ".runtime"
    repo_root = tmp_path / "Provider"
    runtime = create_app_runtime_services(runtime_root=runtime_root)
    assert initialize_repo_runtime(runtime, repo_root).ok
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_preparation",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(repo_root),
                "run_spec": RepoRunSpec(
                    run_objective="Legacy serialized run.",
                    target_proof_availability=ProofAvailability.DECLARED,
                    work_mode=RepoWorkMode.DECLARED_INTERFACE,
                    source_scope=SourceScope(mode="all"),
                    index_policy="auto",
                    root_interface_policy="auto",
                ).model_dump(mode="json"),
            },
        ),
        enqueue=False,
    )
    flow_path, before = _remove_serialized_run_spec(runtime, flow_id)
    before_hash = hashlib.sha256(before).hexdigest()
    restarted = create_app_runtime_services(runtime_root=runtime_root)
    loaded = restarted.ark.flow_service.get_flow(flow_id)
    assert loaded.input.run_spec is None
    assert loaded.status not in {FlowStatus.COMPLETED, FlowStatus.FAILED}

    result = LeanAdminApi(restarted).continue_native_repo(
        RepoRunRequestInput(repo_root=repo_root, run_objective="Continue.", enqueue=False)
    )

    assert not result.ok and result.issues[0].kind == "legacy_native_preparation_restart_required"
    assert flow_path.read_bytes() == before
    assert hashlib.sha256(flow_path.read_bytes()).hexdigest() == before_hash


def test_serialized_terminal_legacy_preparation_remains_immutable_historical_truth(tmp_path) -> None:
    runtime_root = tmp_path / ".runtime"
    repo_root = tmp_path / "Provider"
    runtime = create_app_runtime_services(runtime_root=runtime_root)
    assert initialize_repo_runtime(runtime, repo_root).ok
    assert runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Start a new run.",
            source_corpus_mode=SourceCorpusMode.EXISTING,
            interface_inputs=[],
        ),
    ).ok
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_preparation",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(repo_root),
                "run_spec": RepoRunSpec(
                    run_objective="Historical legacy run.",
                    target_proof_availability=ProofAvailability.DECLARED,
                    work_mode=RepoWorkMode.DECLARED_INTERFACE,
                    source_scope=SourceScope(mode="all"),
                    index_policy="auto",
                    root_interface_policy="auto",
                ).model_dump(mode="json"),
            },
        ),
        enqueue=False,
    )
    runtime.ark.flow_service.store.update_flow_record(
        flow_id, lambda record: setattr(record, "status", FlowStatus.COMPLETED)
    )
    flow_path, before = _remove_serialized_run_spec(runtime, flow_id)
    before_hash = hashlib.sha256(before).hexdigest()
    restarted = create_app_runtime_services(runtime_root=runtime_root)
    historical = restarted.ark.flow_service.get_flow(flow_id)
    assert historical.input.run_spec is None and historical.status is FlowStatus.COMPLETED

    started = LeanAdminApi(restarted).start_native_preparation(
        StartPreparationInput(
            repo_root=repo_root,
            repo_key="Provider",
            enqueue=False,
        )
    )

    assert started.ok and started.value is not None and started.value.flow_id != flow_id
    assert flow_path.read_bytes() == before
    assert hashlib.sha256(flow_path.read_bytes()).hexdigest() == before_hash


def test_release_preview_derives_latest_baseline_inside_admin_boundary(tmp_path, monkeypatch) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    root = tmp_path / "Provider"
    assert initialize_repo_runtime(runtime, root).ok
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(root),
        RepoPublicationState(status=RepoPublicationStatus.STABLE, latest_release_id="release-r7"),
    ).ok
    calls = []

    def fake_preview(repo_root, *, base_release_id, summary, owner_flow_id=None):  # noqa: ANN001
        calls.append((repo_root, base_release_id, summary, owner_flow_id))
        return runtime.foundation.ok({"passed": True})

    monkeypatch.setattr(
        runtime.validation_snapshot.release_finalizer,
        "preview_candidate_release",
        fake_preview,
    )

    result = LeanAdminApi(runtime).preview_repo_release(root, summary="Preview release seven.")

    assert result.ok
    assert calls == [(root, "release-r7", "Preview release seven.", None)]
