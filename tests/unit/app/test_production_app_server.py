from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from agent_runtime_kit.flow.models import FlowRequest, FlowStatus
from starlette.testclient import TestClient

from lean_constellation.app import (
    LeanAdminApi,
    LeanAppConfig,
    create_app_runtime_services,
    create_production_app_server,
    initialize_repo_runtime,
)
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import ProofAvailability, RepoWorkMode
from lean_constellation.services.external_clients import LeanMcpToolkitClient
from tests.unit_services_helpers import publish_native_provider_release


def _make_repo(workspace: Path, name: str) -> Path:
    repo_root = workspace / name
    (repo_root / ".lean_constellation").mkdir(parents=True)
    return repo_root


def test_production_app_server_exposes_workspace_registry_admin_and_repo_mcp(tmp_path) -> None:
    config = LeanAppConfig(workspace_root=tmp_path / "workspace", scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config, view_keys=["resource_curator"])
    _make_repo(tmp_path / "workspace", "MainRepo")

    assert app_result.ok and app_result.value is not None
    app = app_result.value
    registry = app.state.lean_constellation_registry
    with TestClient(app) as client:
        health = client.get("/health")
        repos = client.get("/admin/workspace/repos")
        workspace_status = client.get("/admin/workspace/status")
        loaded = client.post("/admin/workspace/repos/MainRepo/load")
        status = client.get("/admin/repos/MainRepo/runtime/status")
        resumed = client.post("/admin/repos/MainRepo/runtime/resume", json={})
        mcp_index = client.get("/repos/MainRepo/mcp/views")

    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert health.json()["mcp_base_url"] == "http://127.0.0.1:8766"
    assert repos.status_code == 200
    assert repos.json()["value"]["repos"][0]["repo_key"] == "MainRepo"
    assert workspace_status.status_code == 200
    assert workspace_status.json()["value"]["scheduler_enabled"] is False
    assert workspace_status.json()["value"]["server_start_paused"] is True
    assert workspace_status.json()["value"]["test_control_enabled"] is False
    assert loaded.status_code == 200
    assert loaded.json()["value"]["runtime_root"].endswith("workspace/MainRepo/.agent_runtime")
    assert status.status_code == 200
    assert status.json()["value"]["paused"] is True
    assert resumed.status_code == 200
    assert resumed.json()["value"]["paused"] is False
    assert mcp_index.status_code == 200
    assert mcp_index.json()["views"] == ["resource_curator"]
    assert app.state.lean_constellation_registry is registry


def test_repo_lifecycle_route_rejects_cross_repo_body_identity(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_a = _make_repo(workspace, "RepoA")
    repo_b = _make_repo(workspace, "RepoB")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)
    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        client.post("/admin/workspace/repos/RepoA/load")
        response = client.post("/admin/repos/RepoA/continue", json={
            "repo_key": "RepoB", "repo_root": str(repo_b), "run_objective": "Cross repo mutation",
        })
    assert response.status_code == 422
    assert "must match" in response.json()["issues"][0]["message"]
    assert repo_a != repo_b


def test_repo_lifecycle_route_serializes_concurrent_continuations(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = _make_repo(workspace, "Provider")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)
    assert app_result.ok and app_result.value is not None
    app = app_result.value
    with TestClient(app) as client:
        assert client.post("/admin/workspace/repos/Provider/load").status_code == 200
        loaded = app.state.lean_constellation_registry.get_or_load("Provider", refresh_homes=False)
        assert loaded.ok and loaded.value is not None
        assert initialize_repo_runtime(loaded.value, repo_root).ok
        assert loaded.value.foundation.store.write_json_atomic(
            loaded.value.repo_workspace.metadata._repo_publication_path(repo_root),
            {"status": "stable", "latest_release_id": "release-r1"},
        ).ok
        barrier = Barrier(2)

        def start():
            barrier.wait()
            return client.post(
                "/admin/repos/Provider/continue",
                json={"run_objective": "Continue the Provider repository.", "enqueue": False},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = [future.result() for future in [pool.submit(start), pool.submit(start)]]

    assert sorted(response.status_code for response in responses) == [200, 400]
    failed = next(response for response in responses if response.status_code == 400)
    assert failed.json()["issues"][0]["kind"] == "repo_lifecycle_flow_conflict"


def test_canonical_repo_run_route_rejects_route_owned_and_internal_fields(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "Provider")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)
    assert app_result.ok and app_result.value is not None

    with TestClient(app_result.value) as client:
        assert client.post("/admin/workspace/repos/Provider/load").status_code == 200
        identity = client.post("/admin/repos/Provider/runs/continue", json={
            "repo_root": str(workspace / "Provider"), "run_objective": "Continue.",
        })
        internal = client.post("/admin/repos/Provider/runs/continue", json={
            "run_objective": "Continue.", "base_release_id": "release-r1",
        })

    assert identity.status_code == 422
    assert "route-owned fields" in identity.json()["issues"][0]["message"]
    assert internal.status_code == 422
    assert "base_release_id" in internal.json()["issues"][0]["message"]


def test_canonical_and_compatibility_continue_routes_share_lifecycle_truth(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = _make_repo(workspace, "Provider")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)
    assert app_result.ok and app_result.value is not None
    app = app_result.value

    with TestClient(app) as client:
        assert client.post("/admin/workspace/repos/Provider/load").status_code == 200
        loaded = app.state.lean_constellation_registry.get_or_load("Provider", refresh_homes=False)
        assert loaded.ok and loaded.value is not None
        assert initialize_repo_runtime(loaded.value, repo_root).ok
        assert loaded.value.foundation.store.write_json_atomic(
            loaded.value.repo_workspace.metadata._repo_publication_path(repo_root),
            {"status": "stable", "latest_release_id": "release-r1"},
        ).ok
        canonical = client.post(
            "/admin/repos/Provider/runs/continue",
            json={"run_objective": "Continue through the canonical route.", "enqueue": False},
        )
        compatibility = client.post(
            "/admin/repos/Provider/continue",
            json={"run_objective": "Try the compatibility route.", "enqueue": False},
        )

    assert canonical.status_code == 200
    assert canonical.json()["value"]["flow_type"] == "native_repo_continuation"
    assert compatibility.status_code == 400
    assert compatibility.json()["issues"][0]["kind"] == "repo_lifecycle_flow_conflict"


def test_release_routes_list_show_and_isolate_repo_identity(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = _make_repo(workspace, "Provider")
    _make_repo(workspace, "Other")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)
    assert app_result.ok and app_result.value is not None
    app = app_result.value

    with TestClient(app) as client:
        assert client.post("/admin/workspace/repos/Provider/load").status_code == 200
        loaded = app.state.lean_constellation_registry.get_or_load("Provider", refresh_homes=False)
        assert loaded.ok and loaded.value is not None
        assert initialize_repo_runtime(loaded.value, repo_root).ok
        release = publish_native_provider_release(loaded.value, repo_root, release_id="release-r1")

        listed = client.get("/admin/repos/Provider/releases")
        shown = client.get(f"/admin/repos/Provider/releases/{release.release_id}")
        unsafe = client.get("/admin/repos/Provider/releases/unsafe!release")
        rejected = client.post(f"/admin/repos/Provider/releases/{release.release_id}/restore", json={
            "repo_root": str(workspace / "Other"), "dry_run": True,
        })

    assert listed.status_code == 200
    assert [item["release"]["release_id"] for item in listed.json()["value"]["releases"]] == ["release-r1"]
    assert shown.status_code == 200
    assert shown.json()["value"]["release"]["release_id"] == "release-r1"
    assert unsafe.status_code == 422
    assert rejected.status_code == 422
    assert "route-owned fields" in rejected.json()["issues"][0]["message"]


def test_legacy_adoption_and_cleanup_routes_bind_root_and_reject_owned_or_internal_fields(
    tmp_path, monkeypatch
) -> None:  # noqa: ANN001
    workspace = tmp_path / "workspace"
    repo_root = _make_repo(workspace, "Provider")
    _make_repo(workspace, "Other")
    app_result = create_production_app_server(LeanAppConfig(
        workspace_root=workspace,
        scheduler_enabled=False,
        materialize_agent_homes=False,
    ))
    assert app_result.ok and app_result.value is not None
    calls = []

    def adopt(self, model):  # noqa: ANN001
        calls.append(("adopt", model.repo_root, model.summary, model.dry_run))
        return self.runtime.foundation.ok({"outcome": "eligible"})

    def cleanup(self, model):  # noqa: ANN001
        calls.append(("cleanup", model.repo_root, model.expected_audit_digest))
        return self.runtime.foundation.ok({"changed": False})

    monkeypatch.setattr(LeanAdminApi, "adopt_legacy_stable_repo", adopt)
    monkeypatch.setattr(LeanAdminApi, "cleanup_repo_release_orphans", cleanup)
    digest = "d" * 64
    with TestClient(app_result.value) as client:
        adopted = client.post(
            "/admin/repos/Provider/releases/adopt-legacy",
            json={"summary": "Inspect legacy provider.", "dry_run": True},
        )
        cleaned = client.post(
            "/admin/repos/Provider/releases/cleanup-orphans",
            json={"expected_audit_digest": digest},
        )
        cross_repo = client.post(
            "/admin/repos/Provider/releases/adopt-legacy",
            json={"repo_root": str(workspace / "Other"), "summary": "Wrong root."},
        )
        route_key = client.post(
            "/admin/repos/Provider/releases/cleanup-orphans",
            json={"repo_key": "Other", "expected_audit_digest": digest},
        )
        internal = client.post(
            "/admin/repos/Provider/releases/adopt-legacy",
            json={"summary": "No internal fields.", "scope_ids": ["repo:Other"]},
        )

    assert adopted.status_code == 200 and adopted.json()["value"]["outcome"] == "eligible"
    assert cleaned.status_code == 200 and cleaned.json()["value"]["changed"] is False
    assert calls == [
        ("adopt", repo_root.resolve(), "Inspect legacy provider.", True),
        ("cleanup", repo_root.resolve(), digest),
    ]
    assert cross_repo.status_code == 422 and "route-owned fields" in cross_repo.json()["issues"][0]["message"]
    assert route_key.status_code == 422 and "route-owned fields" in route_key.json()["issues"][0]["message"]
    assert internal.status_code == 422 and "scope_ids" in internal.json()["issues"][0]["message"]


def test_production_app_server_exposes_workspace_external_health(tmp_path) -> None:
    toolkit = LeanMcpToolkitClient(dispatcher=lambda tool, payload: {"ok": True})
    config = LeanAppConfig(
        workspace_root=tmp_path / "workspace",
        scheduler_enabled=False,
        materialize_agent_homes=False,
    )
    app_result = create_production_app_server(
        config,
        external_overrides={"lean_mcp_toolkit": toolkit},
    )

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        canonical = client.get("/admin/workspace/external/health")
        compatibility = client.get("/admin/external/health")

    assert canonical.status_code == 200
    assert canonical.json()["value"]["health"]["lean_toolkit_available"] is True
    assert compatibility.status_code == 200
    assert compatibility.json()["value"]["health"]["lean_toolkit_available"] is True


def test_production_app_server_repo_routes_isolate_flow_state(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_a = _make_repo(workspace, "RepoA")
    _make_repo(workspace, "RepoB")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        started = client.post(
            "/admin/repos/RepoA/flows/start",
            json={
                "flow_type": "requirement_group_repo_bootstrap",
                "scope_id": "repo:RepoA",
                "params": {
                    "target_repo": "RepoA",
                    "repo_root": str(repo_a),
                    "workspace_root": str(workspace),
                    "requirement_refs": [],
                },
            },
        )
        repo_a_tree = client.get("/admin/repos/RepoA/flows/tree")
        repo_b_tree = client.get("/admin/repos/RepoB/flows/tree")

    assert started.status_code == 200
    assert started.json()["value"]["repo_root"] is None
    assert repo_a_tree.status_code == 200
    assert repo_a_tree.json()["value"]["total_flows"] == 1
    assert repo_b_tree.status_code == 200
    assert repo_b_tree.json()["value"]["total_flows"] == 0


def test_production_app_server_exposes_repo_config_routes(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        default_config = client.get("/admin/repos/MainRepo/config")
        updated = client.patch(
            "/admin/repos/MainRepo/config",
            json={
                "target_proof_availability": ProofAvailability.DECLARED.value,
                "work_mode": RepoWorkMode.DECLARED_INTERFACE.value,
            },
        )
        publication = client.get("/admin/repos/MainRepo/publication")

    assert default_config.status_code == 200
    assert default_config.json()["value"]["config"]["target_proof_availability"] == "proved"
    assert updated.status_code == 200
    assert updated.json()["value"]["config"]["target_proof_availability"] == "declared"
    assert updated.json()["value"]["config"]["work_mode"] == "declared_interface"
    assert publication.status_code == 200
    assert publication.json()["value"]["publication"]["status"] == "developing"


def test_production_app_server_workspace_requirement_bootstrap_uses_provider_runtime(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    consumer = _make_repo(workspace, "Consumer")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    registry = app_result.value.state.lean_constellation_registry
    control = registry.workspace_runtime()
    assert control.repo_workspace.metadata.ensure_repo_model(consumer).ok
    assert control.repo_workspace.create_requirement_with_interfaces(
        consumer,
        name="need_provider",
        target_repo="Provider",
        source_description="Need provider source.",
        reason="Expose helper theorem.",
    ).ok

    with TestClient(app_result.value) as client:
        response = client.post(
            "/admin/workspace/requirements/bootstrap",
            json={"workspace_root": str(workspace), "target_repo": "Provider"},
        )
        provider_runtime = registry.try_get_loaded("Provider")
        consumer_runtime = registry.try_get_loaded("Consumer")

    assert response.status_code == 200
    assert response.json()["value"]["flow_type"] == "requirement_group_repo_bootstrap"
    assert provider_runtime is not None
    assert consumer_runtime is None
    provider_flows = provider_runtime.ark.flow_service.list_flows(flow_type="requirement_group_repo_bootstrap")
    assert len(provider_flows) == 1
    assert provider_flows[0].scope_id == "repo:Provider"


def test_production_app_server_workspace_requirement_resume_wakes_consumer_runtime(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    consumer = _make_repo(workspace, "Consumer")
    provider = _make_repo(workspace, "Provider")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    registry = app_result.value.state.lean_constellation_registry
    loaded_consumer = registry.get_or_load("Consumer", refresh_homes=False)
    assert loaded_consumer.ok and loaded_consumer.value is not None
    consumer_runtime = loaded_consumer.value
    assert consumer_runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    assert consumer_runtime.repo_workspace.metadata.ensure_repo_model(provider).ok
    assert consumer_runtime.repo_workspace.create_requirement_with_interfaces(
        consumer,
        name="need_provider",
        target_repo="Provider",
        source_description="Need provider source.",
        reason="Expose helper theorem.",
    ).ok
    assert consumer_runtime.repo_workspace.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider",
        provider_repo="Provider",
        reason="Waiting for provider.",
    ).ok
    assert consumer_runtime.repo_workspace.requirement.mark_requirement_satisfied(
        consumer,
        requirement_name="need_provider",
        provider_repo="Provider",
        note="Provider ready.",
    ).ok
    publish_native_provider_release(consumer_runtime, provider, summary="Provider ready.")
    scope_id = "repo:Consumer"
    original_flow_id = consumer_runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id=scope_id,
            params={
                "repo_key": "Consumer",
                "repo_root": str(consumer),
                "start_mode": "admin_start",
                "start_reason": "HTTP resume test",
            },
        ),
        enqueue=False,
    )
    coordinator = consumer_runtime.ark.agent_service.store.create_agent_record(
        scope_id=scope_id,
        agent_type="CoordinatorAgent",
        cli_type="codex",
        home_id="CoordinatorAgent",
        thread_id="thread-consumer",
    )

    def mark_waiting(flow) -> None:
        flow.status = FlowStatus.WAITING
        flow.state.position.phase = "waiting_requirement"
        flow.state.waiting_requirement_name = "need_provider"
        flow.agent_bindings.by_role["coordinator"] = coordinator.agent_id

    consumer_runtime.ark.flow_service.store.update_flow_record(original_flow_id, mark_waiting)
    assert registry.unload("Consumer", require_stable=False).ok
    assert registry.try_get_loaded("Consumer") is None

    with TestClient(app_result.value) as client:
        response = client.post(
            "/admin/workspace/requirements/resume",
            json={
                "consumer_repo_root": str(consumer),
                "requirement_name": "need_provider",
                "provider_repo": "Provider",
                "admin_note": "Resume provider.",
            },
        )
        consumer_runtime = registry.try_get_loaded("Consumer")

    assert response.status_code == 200
    assert response.json()["value"]["observed"] is True
    assert consumer_runtime is not None
    flows = consumer_runtime.ark.flow_service.list_flows(flow_type="native_repo_coordinator")
    assert len(flows) == 1
    assert flows[0].flow_id == original_flow_id
    assert flows[0].input.start_mode == "admin_start"
    assert flows[0].agent_bindings.get("coordinator") == coordinator.agent_id


def test_production_app_server_workspace_main_repo_admin_routes_create_shell_and_status(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    repo_root = workspace / "MainRepo"
    with TestClient(app_result.value) as client:
        shell = client.post(
            "/admin/workspace/main-repo/shell",
            json={"workspace_root": str(workspace), "repo_name": "MainRepo", "project_name": "MainRepo"},
        )
        written = client.post(
            "/admin/workspace/main-repo/preparation-input",
            json={
                "repo_root": str(repo_root),
                "input": RepoPreparationInput(
                    goal="Prepare main repo.",
                    source_corpus_mode=SourceCorpusMode.EXISTING,
                    source_corpus_relpath=".lean_constellation/source",
                ).model_dump(mode="json"),
            },
        )
        source_root = repo_root / ".lean_constellation" / "source"
        source_root.mkdir(parents=True)
        (source_root / "README.md").write_text(
            "# Mathematical source\n\n"
            "Source provenance: transcribed from the supplied article.\n\n"
            "Reading order: read this main material first.\n\n"
            "Main theorem and definitions are stated here.\n\n"
            "Known gaps and extraction limits: no known gaps.\n",
            encoding="utf-8",
        )
        validated = client.post(
            "/admin/workspace/main-repo/source-corpus/validate",
            json={
                "repo_root": str(repo_root),
                "require_files": True,
                "check_draft_gate": True,
                "entry_path": "README.md",
            },
        )
        status = client.get("/admin/main-repo/status", params={"repo_root": str(repo_root)})

    assert shell.status_code == 200
    assert shell.json()["value"]["repo_name"] == "MainRepo"
    assert written.status_code == 200
    assert validated.status_code == 200
    assert validated.json()["value"]["draft_gate"]["passed"] is True
    assert status.status_code == 200
    assert status.json()["value"]["repo_exists"] is True
    assert status.json()["value"]["preparation_input_exists"] is True


def test_production_app_server_legacy_repo_routes_require_repo_key_when_ambiguous(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "RepoA")
    _make_repo(workspace, "RepoB")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        ambiguous = client.get("/admin/runtime/status")

    assert ambiguous.status_code == 400
    assert ambiguous.json()["issues"][0]["kind"] == "repo_key_required"


def test_production_app_server_legacy_repo_routes_proxy_when_single_repo(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        status = client.get("/admin/runtime/status")
        tree = client.get("/admin/flows/tree")

    assert status.status_code == 200
    assert tree.status_code == 200
    assert tree.json()["value"]["total_flows"] == 0


def test_production_app_server_repo_snapshot_restore_does_not_touch_other_repo(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_a = _make_repo(workspace, "RepoA")
    repo_b = _make_repo(workspace, "RepoB")
    (repo_a / "Marker.txt").write_text("repo-a-before\n", encoding="utf-8")
    (repo_b / "Marker.txt").write_text("repo-b-before\n", encoding="utf-8")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    registry = app_result.value.state.lean_constellation_registry
    runtime_a = registry.get_or_load("RepoA")
    runtime_b = registry.get_or_load("RepoB")
    assert runtime_a.ok and runtime_a.value is not None
    assert runtime_b.ok and runtime_b.value is not None
    assert initialize_repo_runtime(runtime_a.value, repo_a).ok
    assert initialize_repo_runtime(runtime_b.value, repo_b).ok

    with TestClient(app_result.value) as client:
        created = client.post(
            "/admin/repos/RepoA/snapshots/create",
            json={
                "repo_root": str(repo_a),
                "checkpoint_kind": "manual_test_stable_point",
                "label": "repo-a-only",
            },
        )
        assert created.status_code == 200
        snapshot_id = created.json()["value"]["snapshot_id"]
        (repo_a / "Marker.txt").write_text("repo-a-after\n", encoding="utf-8")
        (repo_b / "Marker.txt").write_text("repo-b-after\n", encoding="utf-8")
        restored = client.post(
            "/admin/repos/RepoA/snapshots/restore",
            json={"repo_root": str(repo_a), "snapshot_id": snapshot_id, "leave_runtime_paused": True},
        )

    assert restored.status_code == 200
    assert (repo_a / "Marker.txt").read_text(encoding="utf-8") == "repo-a-before\n"
    assert (repo_b / "Marker.txt").read_text(encoding="utf-8") == "repo-b-after\n"


def test_production_app_server_repo_agent_reports_do_not_cross_repos(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "RepoA")
    _make_repo(workspace, "RepoB")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    registry = app_result.value.state.lean_constellation_registry
    runtime_a = registry.get_or_load("RepoA")
    runtime_b = registry.get_or_load("RepoB")
    assert runtime_a.ok and runtime_a.value is not None
    assert runtime_b.ok and runtime_b.value is not None
    rollout = Path(runtime_a.value.ark.agent_service.runtime_root) / "homes" / "codex" / "CoordinatorAgent" / ".codex" / "sessions" / "trace.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text('{"type":"event_msg","payload":{"type":"task_complete","last_agent_message":"done"}}\n', encoding="utf-8")
    agent = runtime_a.value.ark.agent_service.store.create_agent_record(
        scope_id="repo:RepoA",
        agent_type="CoordinatorAgent",
        cli_type="codex",
        home_id="CoordinatorAgent",
        thread_id="thread-a",
        rollout_relpath="sessions/trace.jsonl",
    )

    with TestClient(app_result.value) as client:
        repo_a_response = client.get(f"/admin/repos/RepoA/agents/{agent.agent_id}/rollout")
        repo_b_response = client.get(f"/admin/repos/RepoB/agents/{agent.agent_id}/rollout")

    assert repo_a_response.status_code == 200
    assert repo_b_response.status_code == 400
    assert repo_b_response.json()["issues"][0]["kind"] == "agent_rollout_info_failed"


def test_production_app_server_materializes_repo_local_production_agent_homes_on_load(tmp_path) -> None:
    base_config = tmp_path / "codex" / "config.toml"
    auth_json = tmp_path / "codex" / "auth.json"
    base_config.parent.mkdir(parents=True)
    base_config.write_text("model = \"gpt-5-codex\"\n", encoding="utf-8")
    auth_json.write_text("{}\n", encoding="utf-8")
    repo_root = _make_repo(tmp_path / "workspace", "MainRepo")
    config = LeanAppConfig(
        workspace_root=tmp_path / "workspace",
        scheduler_enabled=False,
        codex_base_config_path=base_config,
        codex_auth_json_path=auth_json,
        admin_http_port=9123,
    )

    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    loaded = app_result.value.state.lean_constellation_registry.get_or_load("MainRepo")
    assert loaded.ok
    homes = app_result.value.state.lean_constellation_registry.get_status("MainRepo").value.agent_homes
    assert homes is not None
    assert homes.total == len(homes.materialized)
    assert homes.failed == []
    coordinator = next(home for home in homes.materialized if home.agent_type == "CoordinatorAgent")
    codex_config = repo_root / ".agent_runtime" / "homes" / "codex" / "CoordinatorAgent" / ".codex" / "config.toml"
    manifest = repo_root / ".agent_runtime" / "homes" / "codex" / "CoordinatorAgent" / ".agents" / "lean_constellation_home.json"
    assert coordinator.codex_config_path == str(codex_config)
    assert codex_config.exists()
    assert "http://127.0.0.1:9123/repos/MainRepo/mcp/views/" in codex_config.read_text(encoding="utf-8")
    assert json.loads(manifest.read_text(encoding="utf-8"))["mcp_transport"] == "http"


def test_production_app_server_scheduler_lifespan_runs_loop(tmp_path) -> None:
    config = LeanAppConfig(
        workspace_root=tmp_path / "workspace",
        scheduler_enabled=True,
        materialize_agent_homes=False,
        scheduler_tick_interval_s=0.01,
        scheduler_idle_interval_s=0.01,
        scheduler_error_interval_s=0.01,
    )
    app_result = create_production_app_server(config, view_keys=["resource_curator"])

    assert app_result.ok and app_result.value is not None
    app = app_result.value
    with TestClient(app) as client:
        time.sleep(0.05)
        health = client.get("/health")
        scheduler = health.json()["scheduler"]

    assert scheduler["running"] is True
    assert scheduler["tick_count"] >= 1


def test_production_app_server_exposes_test_control_routes_with_guard(tmp_path) -> None:
    config = LeanAppConfig(workspace_root=tmp_path / "workspace", scheduler_enabled=False, materialize_agent_homes=False)
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".agent_runtime")
    app_result = create_production_app_server(config, runtime=runtime)

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        response = client.post("/admin/test-control/flows/run-until-step", json={"flow_id": "flow-1"})
        wait_response = client.post("/admin/test-control/steps/wait", json={"step_id": "step-1"})

    assert response.status_code == 400
    assert response.json()["issues"][0]["kind"] == "test_control_disabled"
    assert wait_response.status_code == 400
    assert wait_response.json()["issues"][0]["kind"] == "test_control_disabled"


def test_production_app_server_exports_agent_trace_report(tmp_path) -> None:
    runtime_root = tmp_path / ".agent_runtime"
    runtime = create_app_runtime_services(runtime_root=runtime_root)
    rollout = runtime_root / "homes" / "codex" / "CoordinatorAgent" / ".codex" / "sessions" / "trace.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {"type": "turn_context", "payload": {"turn_id": "turn-1"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "inspect_workspace_for_coordinator",
                        "call_id": "call-1",
                        "arguments": "{}",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "turn_id": "turn-1", "last_agent_message": "complete"},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    agent = runtime.ark.agent_service.store.create_agent_record(
        scope_id="repo:MainRepo",
        agent_type="CoordinatorAgent",
        cli_type="codex",
        home_id="CoordinatorAgent",
        thread_id="thread-1",
        rollout_relpath="sessions/trace.jsonl",
    )
    report_path = tmp_path / "trace_report.json"
    config = LeanAppConfig(workspace_root=tmp_path / "workspace", scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config, runtime=runtime)

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        response = client.get(
            f"/admin/agents/{agent.agent_id}/trace-report",
            params={"output_path": str(report_path), "format": "json"},
        )

    assert response.status_code == 200
    assert response.json()["value"]["report_path"] == str(report_path)
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["latest_turn"]["final_response"] == "complete"
    assert saved["tool_calls"][0]["tool_name"] == "inspect_workspace_for_coordinator"


def test_production_app_server_rejects_invalid_agent_trace_query(tmp_path) -> None:
    config = LeanAppConfig(workspace_root=tmp_path / "workspace", scheduler_enabled=False, materialize_agent_homes=False)
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".agent_runtime")
    app_result = create_production_app_server(config, runtime=runtime)

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        response = client.get("/admin/agents/agent-1/turn", params={"index": "not-an-int"})

    assert response.status_code == 422
    assert response.json()["issues"][0]["kind"] == "request_validation_failed"
    assert "index" in response.json()["issues"][0]["message"]
