from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil

import pytest
from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from lean_constellation.app import create_app_runtime_services, materialize_agent_home
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from tests.real.runtime_matrix.fixtures import RuntimeMatrixFakeLakeClient
from tests.real.runtime_matrix.scripted_provider import schedule_until
from tests.real.runtime_matrix.transport import (
    codex_force_full_access_enabled,
    start_runtime_mcp_http_server,
)


pytestmark = [pytest.mark.real, pytest.mark.slow, pytest.mark.real_codex]


def test_real_codex_repo_format_submit_smoke_env_gated(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    config_home = _require_real_codex()
    base_config_path = _write_noninteractive_codex_base_config(config_home, tmp_path)
    runtime_root = tmp_path / ".agent_runtime"
    runtime = create_app_runtime_services(
        runtime_root=runtime_root,
        external_overrides={"lake": RuntimeMatrixFakeLakeClient()},
    )
    workspace = tmp_path / "workspace"
    repo_root = workspace / "Provider"
    _write_bootstrap_preparation(runtime, repo_root)
    http_server = start_runtime_mcp_http_server(runtime)
    request.addfinalizer(http_server.close)
    materialized = materialize_agent_home(
        runtime,
        "RepoFormatDiscoveryAgent",
        mcp_http_base_url=http_server.base_url,
        base_config_path=base_config_path,
        auth_json_path=config_home / "auth.json",
        codex_force_full_access=codex_force_full_access_enabled(),
    )
    assert materialized.ok and materialized.value is not None, materialized.issues
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="requirement_group_repo_bootstrap",
            scope_id="repo:Provider",
            params={
                "target_repo": "Provider",
                "repo_root": str(repo_root),
                "workspace_root": str(workspace),
                "requirement_refs": ["Consumer:need_provider"],
            },
        )
    )

    real_step_timeout = float(os.environ.get("LEAN_CONSTELLATION_REAL_CODEX_STEP_TIMEOUT", "300"))
    schedule_until(
        runtime,
        lambda: runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.COMPLETED,
        limit=120,
        step_timeout_s=real_step_timeout,
    )

    flow = runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result is not None
    assert flow.result.outcome in {"native_bootstrap_ready", "adapter_bootstrap_ready"}


def _require_real_codex() -> Path:
    if os.environ.get("LEAN_CONSTELLATION_RUN_REAL_CODEX") != "1":
        pytest.skip("Set LEAN_CONSTELLATION_RUN_REAL_CODEX=1 to run real Codex Runtime Matrix smoke tests.")
    if importlib.util.find_spec("openai_codex") is None:
        pytest.skip("openai_codex Python SDK is required for real Codex Runtime Matrix smoke tests.")
    if shutil.which("codex") is None:
        pytest.skip("codex CLI is required for real Codex Runtime Matrix smoke tests.")
    config_home = os.environ.get("LEAN_CONSTELLATION_CODEX_CONFIG_HOME")
    if not config_home:
        pytest.skip("Set LEAN_CONSTELLATION_CODEX_CONFIG_HOME to a Codex config directory.")
    home = Path(config_home).expanduser()
    if not (home / "config.toml").exists() or not (home / "auth.json").exists():
        pytest.skip("LEAN_CONSTELLATION_CODEX_CONFIG_HOME must contain config.toml and auth.json.")
    return home


def _write_noninteractive_codex_base_config(config_home: Path, tmp_path: Path) -> Path:
    configured_source = os.environ.get("LEAN_CONSTELLATION_CODEX_BASE_CONFIG_PATH")
    source = (
        Path(configured_source).expanduser()
        if configured_source
        else config_home / "config.toml"
    )
    if not source.is_file():
        pytest.skip(f"Codex base config does not exist: {source}")
    target = tmp_path / "codex_noninteractive_config.toml"
    blocked_prefixes = ("approval_policy", "approvals_reviewer", "notify")
    lines: list[str] = []
    inserted_approval_policy = False
    for line in source.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if any(stripped.startswith(prefix) for prefix in blocked_prefixes):
            continue
        if stripped.startswith("request_rule"):
            lines.append("request_rule = false")
            continue
        if stripped.startswith("[") and not inserted_approval_policy:
            lines.append('approval_policy = "never"')
            lines.append("")
            inserted_approval_policy = True
        if stripped.startswith("model_reasoning_effort"):
            lines.append('model_reasoning_effort = "low"')
            continue
        lines.append(line)
    if not inserted_approval_policy:
        lines.append("")
        lines.append('approval_policy = "never"')
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _write_bootstrap_preparation(runtime, repo_root: Path) -> None:  # noqa: ANN001
    repo_root.mkdir(parents=True, exist_ok=True)
    ensured = runtime.repo_workspace.metadata.ensure_repo_model(repo_root)
    assert ensured.ok, ensured.issues
    written = runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Runtime Matrix real Codex smoke provider.",
            source_corpus_mode=SourceCorpusMode.PREPARE,
            requirement_refs=[{"consumer_repo": "Consumer", "requirement_name": "need_provider"}],
        ),
    )
    assert written.ok, written.issues
