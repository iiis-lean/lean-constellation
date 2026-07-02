"""Repo runtime and Agent home bootstrap helpers."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.agents import AgentHomeBootstrapSpec, build_agent_home_bootstrap_spec
from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.preparation import RepoRuntimeBootstrapView
from lean_constellation.services.foundation import FoundationContext, ServiceResult
from lean_constellation.services.repo_workspace.repo_preparation import DefaultProviderRepoRuntimeBootstrap
from lean_constellation.services.runtime import LeanRuntimeServices

if TYPE_CHECKING:
    from lean_constellation.agents import AgentTypeSpec


class RepoRuntimeInitView(StrictModel):
    repo_root: str
    runtime_root: str
    constellation_root: str
    initialized_paths: list[str] = Field(default_factory=list)
    created: bool
    summary: str


class AgentHomeMaterializationView(StrictModel):
    agent_type: str
    home_id: str
    home_root: str
    instruction_path: str
    skill_paths: dict[str, str] = Field(default_factory=dict)
    mcp_server_names: list[str] = Field(default_factory=list)
    codex_config_path: str | None = None
    summary: str


def initialize_repo_runtime(
    runtime: LeanRuntimeServices,
    repo_root: Path | str,
    *,
    repo_name: str | None = None,
    main_node: str = "Main",
) -> ServiceResult[RepoRuntimeInitView]:
    """Initialize repo-local Lean and ARK runtime shell idempotently."""

    root = Path(repo_root).expanduser()
    ensure_repo = runtime.foundation.store.ensure_dir(root)
    if not ensure_repo.ok:
        return runtime.foundation.fail(ensure_repo.issues)
    model = runtime.repo_workspace.metadata.ensure_repo_model(root, main_node=main_node)
    if not model.ok:
        return runtime.foundation.fail(model.issues)
    constellation_root = runtime.foundation.layout.constellation_root(FoundationContext(repo_root=root))
    ensure_constellation = runtime.foundation.store.ensure_dir(constellation_root)
    if not ensure_constellation.ok:
        return runtime.foundation.fail(ensure_constellation.issues)
    bootstrap = DefaultProviderRepoRuntimeBootstrap(runtime).bootstrap_provider_repo_runtime(
        root,
        repo_name=repo_name or root.name,
        project_name=None,
    )
    if not bootstrap.ok or bootstrap.value is None:
        return runtime.foundation.fail(bootstrap.issues)
    created = bool(model.value and model.value.created) or bootstrap.value.created
    paths = sorted({str(constellation_root), *bootstrap.value.initialized_paths})
    return runtime.foundation.ok(
        RepoRuntimeInitView(
            repo_root=str(root),
            runtime_root=bootstrap.value.runtime_root,
            constellation_root=str(constellation_root),
            initialized_paths=paths,
            created=created,
            summary="Initialized repo runtime shell.",
        )
    )


def materialize_agent_home(
    runtime: LeanRuntimeServices,
    agent_type: str,
    *,
    mcp_server_url: str | None = None,
    mcp_http_base_url: str | None = None,
    mcp_server_command: str | None = None,
    mcp_server_args: list[str] | None = None,
    mcp_server_env: dict[str, str] | None = None,
    home_id: str | None = None,
    base_config_path: Path | str | None = None,
    auth_json_path: Path | str | None = None,
    fixed_env: dict[str, str] | None = None,
    required_env: set[str] | None = None,
    agent_type_specs: Sequence["AgentTypeSpec"] | None = None,
) -> ServiceResult[AgentHomeMaterializationView]:
    """Materialize one AgentType home through ARK HomeService."""

    try:
        spec = build_agent_home_bootstrap_spec(
            agent_type,
            home_id=home_id,
            mcp_server_url=mcp_server_url,
            mcp_http_base_url=mcp_http_base_url,
            mcp_server_command=mcp_server_command,
            mcp_server_args=mcp_server_args,
            mcp_server_env=mcp_server_env,
            fixed_env=fixed_env,
            required_env=required_env,
            specs=agent_type_specs,
        )
        ark_spec = replace(
            spec.ark_home_create_spec,
            base_config_path=Path(base_config_path).expanduser() if base_config_path is not None else None,
            auth_json_path=Path(auth_json_path).expanduser() if auth_json_path is not None else None,
        )
        home_service = getattr(runtime.ark.agent_service, "home_service", None)
        if home_service is None:
            return runtime.foundation.fail(
                runtime.foundation.issue("home_service_missing", "ARK AgentService does not expose a HomeService.")
            )
        record = home_service.create_home(ark_spec)
        home_root = home_service.resolve_home_root(record.cli_type, record.home_id)
        instruction_path = _write_agent_instruction(home_root, spec)
        manifest_path = _write_agent_home_manifest(home_root, spec)
    except Exception as exc:  # noqa: BLE001 - bootstrap boundary.
        return runtime.foundation.fail(runtime.foundation.issue("agent_home_materialization_failed", f"Agent home materialization failed: {exc}"))

    skill_paths = {
        key: str(home_root / ".agents" / "skills" / key)
        for key in sorted(spec.skill_specs)
    }
    codex_config = home_root / ".codex" / "config.toml"
    return runtime.foundation.ok(
        AgentHomeMaterializationView(
            agent_type=spec.agent_type,
            home_id=record.home_id,
            home_root=str(home_root),
            instruction_path=str(instruction_path),
            skill_paths=skill_paths,
            mcp_server_names=[server.name for server in spec.mcp_servers],
            codex_config_path=str(codex_config) if codex_config.exists() else None,
            summary=f"Materialized Agent home {record.home_id}; manifest: {manifest_path.name}.",
        )
    )


def _write_agent_instruction(home_root: Path, spec: AgentHomeBootstrapSpec) -> Path:
    path = home_root / ".agents" / "instructions" / "developer.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(spec.developer_instructions, encoding="utf-8")
    return path


def _write_agent_home_manifest(home_root: Path, spec: AgentHomeBootstrapSpec) -> Path:
    path = home_root / ".agents" / "lean_constellation_home.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "agent_type": spec.agent_type,
        "home_id": spec.home_id,
        "tool_view_config": spec.tool_view_config.model_dump(mode="json"),
        "fixed_env": dict(sorted(spec.fixed_env.items())),
        "required_env": sorted(spec.required_env),
        "mcp_servers": [server.name for server in spec.mcp_servers],
        "mcp_transport": _mcp_transport_summary(spec),
        "mcp_server_specs": [_mcp_server_manifest(server) for server in spec.mcp_servers],
        "skill_keys": sorted(spec.skill_specs),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _mcp_transport_summary(spec: AgentHomeBootstrapSpec) -> str | None:
    transports = sorted({server.transport for server in spec.mcp_servers})
    if not transports:
        return None
    if len(transports) == 1:
        return transports[0]
    return "+".join(transports)


def _mcp_server_manifest(server) -> dict[str, object]:  # noqa: ANN001 - ARK dataclass boundary.
    return {
        "name": server.name,
        "transport": server.transport,
        "url": server.url,
        "command": server.command,
        "args": list(server.args),
        "env_keys": sorted(server.env),
        "env_vars": list(server.env_vars),
        "http_header_keys": sorted(server.http_headers),
        "env_http_header_keys": sorted(server.env_http_headers),
    }
