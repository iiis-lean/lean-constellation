from __future__ import annotations

import anyio
import json
import os
from pathlib import Path
import threading
from typing import Any
import urllib.request

import pytest
from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.app import (
    AdminStepStartInput,
    LeanAdminApi,
    LeanAppConfig,
    SetAgentStepOverrideInput,
    create_production_app_server,
    materialize_agent_home,
)
from lean_constellation.flows.testing import ControlledAgentOverrideSpec
from tests.real.runtime_matrix.admin_helpers import run_next_created_step, run_until_step_created, unwrap
from tests.real.runtime_matrix.fixtures import ResourceFixture, RuntimeMatrixFakeLakeClient, RuntimeMatrixWorkspace
from tests.real.runtime_matrix.strict.real_codex_helpers import (
    require_real_codex,
    strict_controlled_agent_specs,
    write_noninteractive_codex_base_config,
)
from tests.real.runtime_matrix.strict.test_real_codex_agent_resource_matrix import (
    _agent_id_for_step,
    _coordinator_resource_probe_prompt,
    _read_artifact,
    _start_coordinator,
)
from tests.real.runtime_matrix.transport import RuntimeMcpHttpTestServer
from tests.real.runtime_matrix.transport import _free_local_port, _wait_for_http_health


pytestmark = [pytest.mark.real, pytest.mark.slow, pytest.mark.real_codex]


def test_real_codex_coordinator_uses_repo_prefixed_production_mcp(tmp_path: Path) -> None:
    config_home = require_real_codex()
    base_config_path = write_noninteractive_codex_base_config(config_home, tmp_path)
    port = _free_local_port()
    workspace = tmp_path / "workspace"
    provider_repo = workspace / "Provider"
    (provider_repo / ".lean_constellation").mkdir(parents=True)
    fake_lake = RuntimeMatrixFakeLakeClient()
    config = LeanAppConfig(
        workspace_root=workspace,
        admin_http_host="127.0.0.1",
        admin_http_port=port,
        mcp_http_base_url=f"http://127.0.0.1:{port}",
        scheduler_enabled=False,
        server_start_paused=True,
        materialize_agent_homes=False,
        test_control_enabled=True,
    )
    app_result = create_production_app_server(
        config,
        external_overrides={"lake": fake_lake},
        materialize_agent_homes=False,
    )
    assert app_result.ok and app_result.value is not None, app_result.issues
    server = _start_app_server(app_result.value, port)
    try:
        registry = app_result.value.state.lean_constellation_registry
        loaded = registry.get_or_load("Provider", refresh_homes=False)
        assert loaded.ok and loaded.value is not None, loaded.issues
        runtime = loaded.value
        admin = LeanAdminApi(runtime, workspace_root=workspace)
        ws = RuntimeMatrixWorkspace(
            tmp_path=tmp_path,
            runtime_root=provider_repo / ".agent_runtime",
            workspace_root=workspace,
            provider_repo=provider_repo,
            consumer_repo=workspace / "Consumer",
            adapter_repo=workspace / "Adapter",
            upstream_repo=workspace / "Upstream",
            resource_root=workspace / "resources",
            runtime=runtime,
            admin=admin,
            lake=fake_lake,
            resources=ResourceFixture(
                local_file=workspace / "resources" / "local.txt",
                web_url="https://example.com/runtime-matrix-resource",
                arxiv_id="2501.00001",
            ),
            live_toolkit_base_url=None,
            live_toolkit_visible_repo=None,
            mathlib_template_root=None,
        )
        ws.prepare_provider_ready_repo()
        agent_type = "CoordinatorControlledTestAgent"
        agent_specs = strict_controlled_agent_specs("CoordinatorAgent")
        base_home = materialize_agent_home(
            runtime,
            "CoordinatorAgent",
            mcp_http_base_url=registry.repo_mcp_http_base_url("Provider"),
            base_config_path=base_config_path,
            auth_json_path=config_home / "auth.json",
            agent_type_specs=agent_specs,
        )
        assert base_home.ok and base_home.value is not None, base_home.issues
        materialized = materialize_agent_home(
            runtime,
            agent_type,
            mcp_http_base_url=registry.repo_mcp_http_base_url("Provider"),
            base_config_path=base_config_path,
            auth_json_path=config_home / "auth.json",
            agent_type_specs=agent_specs,
        )
        assert materialized.ok and materialized.value is not None, materialized.issues
        home_root = Path(materialized.value.home_root)
        manifest = json.loads((home_root / ".agents" / "lean_constellation_home.json").read_text(encoding="utf-8"))
        server_urls = [
            server_spec["url"]
            for server_spec in manifest["mcp_server_specs"]
            if isinstance(server_spec, dict) and "url" in server_spec
        ]
        assert server_urls
        assert all("/repos/Provider/mcp/views/" in url for url in server_urls)

        prompt_marker = "RTCODEX_PROMPT_MARKER_COORDINATOR_REPO_LOCAL_20260704"
        developer_marker = "RTCODEX_DEV_MARKER_COORDINATOR_STRICT_REPO_LOCAL_20260704"
        artifact_path = provider_repo / ".lean_constellation" / "runtime_matrix_artifacts" / "repo_local_coordinator.json"
        flow_id = _start_coordinator(ws)
        step_id = run_until_step_created(admin, flow_id, "coordinator_agent_step")
        view = unwrap(
            admin.set_agent_step_override(
                SetAgentStepOverrideInput(
                    step_id=step_id,
                    override=ControlledAgentOverrideSpec(
                        strategy="fresh_test_agent_type",
                        agent_type_override=agent_type,
                        cli_type_override="codex",
                        prompt_overlay=_coordinator_resource_probe_prompt(prompt_marker),
                        developer_instructions_overlay=(
                            "\n\nRuntime Matrix strict resource probe developer marker:\n"
                            f"{developer_marker}\n"
                            "When asked for a developer marker, copy this exact marker from developer instructions.\n"
                        ),
                        env_overrides={"LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH": str(artifact_path)},
                        metadata={"runtime_matrix_case": "repo_local_real_codex_coordinator_resource_probe"},
                    ),
                )
            )
        )
        assert view.override is not None
        real_step_timeout = float(os.environ.get("LEAN_CONSTELLATION_REAL_CODEX_STEP_TIMEOUT", "300"))
        started = unwrap(admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=real_step_timeout)))
        assert started.status == "completed", started
        assert run_next_created_step(admin, flow_id, timeout_s=20)
        flow = runtime.ark.flow_service.get_flow(flow_id)
        assert flow.status is FlowStatus.COMPLETED
        assert flow.result is not None
        assert flow.result.outcome == "repo_ready"
        step = runtime.ark.flow_service.get_step(step_id)
        assert step.submission is not None
        assert step.submission.tool_name == "submit_repo_ready"
        data = _read_artifact(artifact_path)
        assert data["prompt_marker_seen"] == prompt_marker
        assert data["developer_marker_seen"] == developer_marker
        assert data["artifact_home_root"] == str(home_root)
        assert {"inspect_workspace_for_coordinator", "get_node_tree"}.issubset(set(data["application_tools_called"]))
        agent_id = _agent_id_for_step(step)
        assert agent_id is not None
        report = runtime.ark.agent_service.build_trace_report(agent_id, artifact_path=artifact_path)
        assert report.latest_turn is not None
        report_index = admin.get_agent_report_index(agent_id)
        assert report_index.ok and report_index.value is not None
        assert str(provider_repo / ".agent_runtime") in report_index.value.reports_root
    finally:
        server.close()


def _start_app_server(app: Any, port: int) -> RuntimeMcpHttpTestServer:
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on", ws="wsproto")
    )
    thread = threading.Thread(target=lambda: anyio.run(server.serve), name=f"production-app-{port}", daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    _wait_for_http_health(f"{base_url}/health", server=server)
    return RuntimeMcpHttpTestServer(base_url=base_url, server=server, thread=thread)
