"""Repo runtime and Agent home bootstrap helpers."""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field
from agent_runtime_kit.agent.homes import ModelConfigOverrides

from lean_constellation.agents import AgentHomeBootstrapSpec, build_agent_home_bootstrap_spec, build_agent_type_specs
from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import FoundationContext, ServiceResult
from lean_constellation.services.runtime import LeanRuntimeServices

if TYPE_CHECKING:
    from lean_constellation.agents import AgentTypeSpec


class RepoBusinessInitView(StrictModel):
    repo_root: str
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
    effective_model: str | None = None
    effective_reasoning_effort: str | None = None
    summary: str


class ProductionAgentHomesView(StrictModel):
    total: int
    materialized: list[AgentHomeMaterializationView] = Field(default_factory=list)
    failed: list[dict[str, str]] = Field(default_factory=list)
    mcp_http_base_url: str
    codex_base_config_path: str | None = None
    codex_auth_json_path: str | None = None
    summary: str


def initialize_repo_business_truth(
    runtime: LeanRuntimeServices,
    repo_root: Path | str,
    *,
    main_node: str = "Main",
) -> ServiceResult[RepoBusinessInitView]:
    """Initialize repo business truth without creating repo-local ARK runtime state."""

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
    created = bool(model.value and model.value.created)
    return runtime.foundation.ok(
        RepoBusinessInitView(
            repo_root=str(root),
            constellation_root=str(constellation_root),
            initialized_paths=[str(constellation_root)] if created else [],
            created=created,
            summary="Initialized repo business truth.",
        )
    )


def materialize_agent_home(
    runtime: LeanRuntimeServices,
    agent_type: str,
    *,
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
    model_config_overrides: ModelConfigOverrides | None = None,
) -> ServiceResult[AgentHomeMaterializationView]:
    """Materialize one AgentType home through ARK HomeService."""

    try:
        spec = build_agent_home_bootstrap_spec(
            agent_type,
            home_id=home_id,
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
            model_config_overrides=model_config_overrides,
        )
        home_service = getattr(runtime.ark.agent_service, "home_service", None)
        if home_service is None:
            return runtime.foundation.fail(
                runtime.foundation.issue("home_service_missing", "ARK AgentService does not expose a HomeService.")
            )
        record = home_service.create_home(ark_spec)
        home_root = home_service.resolve_home_root(record.cli_type, record.home_id)
        _disable_global_codex_features(home_root / ".codex" / "config.toml")
        instruction_path = _write_agent_instruction(home_root, spec)
        effective_model, effective_reasoning_effort = _read_effective_model_config(
            home_root / ".codex" / "config.toml"
        )
        manifest_path = _write_agent_home_manifest(
            home_root,
            spec,
            effective_model=effective_model,
            effective_reasoning_effort=effective_reasoning_effort,
        )
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
            effective_model=effective_model,
            effective_reasoning_effort=effective_reasoning_effort,
            summary=f"Materialized Agent home {record.home_id}; manifest: {manifest_path.name}.",
        )
    )


def materialize_production_agent_homes(
    runtime: LeanRuntimeServices,
    *,
    mcp_http_base_url: str,
    base_config_path: Path | str | None,
    auth_json_path: Path | str | None,
    shared_elan_home: Path | str | None = None,
    agent_type_specs: Sequence["AgentTypeSpec"] | None = None,
    agent_home_overrides: dict[str, object] | None = None,
) -> ServiceResult[ProductionAgentHomesView]:
    """Materialize all production Agent homes for a long-running runtime."""

    base_config = Path(base_config_path).expanduser() if base_config_path is not None else None
    auth_json = Path(auth_json_path).expanduser() if auth_json_path is not None else None
    missing: list[dict[str, str]] = []
    if base_config is None or not base_config.exists():
        missing.append({"field": "codex_base_config_path", "path": str(base_config) if base_config else ""})
    if auth_json is None or not auth_json.exists():
        missing.append({"field": "codex_auth_json_path", "path": str(auth_json) if auth_json else ""})
    if missing:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "codex_config_missing",
                "Production Agent home materialization requires readable Codex base config and auth.json.",
                details={"missing": missing},
            )
        )

    elan_home = _resolve_shared_elan_home(shared_elan_home)
    if elan_home is None:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "shared_elan_home_missing",
                "Production Agent home materialization requires an existing shared ELAN_HOME.",
            )
        )

    specs = list(agent_type_specs) if agent_type_specs is not None else build_agent_type_specs()
    materialized: list[AgentHomeMaterializationView] = []
    failed: list[dict[str, str]] = []
    for spec in specs:
        configured_override = (agent_home_overrides or {}).get(spec.agent_type)
        model_override = None
        if configured_override is not None:
            model_override = ModelConfigOverrides(
                model=getattr(configured_override, "model", None),
                reasoning_effort=getattr(configured_override, "model_reasoning_effort", None),
            )
        result = materialize_agent_home(
            runtime,
            spec.agent_type,
            home_id=spec.agent_type,
            mcp_http_base_url=mcp_http_base_url,
            base_config_path=base_config,
            auth_json_path=auth_json,
            fixed_env={"ELAN_HOME": str(elan_home)},
            agent_type_specs=specs,
            model_config_overrides=model_override,
        )
        if result.ok and result.value is not None:
            materialized.append(result.value)
            continue
        failed.append(
            {
                "agent_type": spec.agent_type,
                "message": "; ".join(issue.message for issue in result.issues) or "Agent home materialization failed.",
            }
        )
    view = ProductionAgentHomesView(
        total=len(specs),
        materialized=materialized,
        failed=failed,
        mcp_http_base_url=mcp_http_base_url.rstrip("/"),
        codex_base_config_path=str(base_config),
        codex_auth_json_path=str(auth_json),
        summary=f"Materialized {len(materialized)}/{len(specs)} production Agent homes.",
    )
    if failed:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "production_agent_home_materialization_failed",
                view.summary,
                details={"failed": failed},
            )
        )
    return runtime.foundation.ok(view)


def _resolve_shared_elan_home(configured: Path | str | None) -> Path | None:
    candidates = [
        Path(configured).expanduser() if configured is not None else None,
        Path(os.environ["ELAN_HOME"]).expanduser() if os.environ.get("ELAN_HOME") else None,
        Path.home() / ".elan",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_dir():
            return candidate.resolve()
    return None


def _disable_global_codex_features(config_path: Path) -> None:
    """Override global Codex discovery features without rewriting unrelated config."""

    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    lines = text.splitlines()
    section_start: int | None = None
    section_end = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[features]":
            section_start = index
            continue
        if section_start is not None and index > section_start and stripped.startswith("[") and stripped.endswith("]"):
            section_end = index
            break

    overrides = {"apps": "false", "plugins": "false", "tool_suggest": "false"}
    if section_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[features]", *(f"{key} = {value}" for key, value in overrides.items())])
    else:
        found: set[str] = set()
        for index in range(section_start + 1, section_end):
            key = lines[index].split("=", 1)[0].strip() if "=" in lines[index] else ""
            if key in overrides:
                lines[index] = f"{key} = {overrides[key]}"
                found.add(key)
        insert_at = section_end
        for key, value in overrides.items():
            if key not in found:
                lines.insert(insert_at, f"{key} = {value}")
                insert_at += 1
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _write_agent_instruction(home_root: Path, spec: AgentHomeBootstrapSpec) -> Path:
    path = home_root / ".agents" / "instructions" / "developer.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(spec.developer_instructions, encoding="utf-8")
    return path


def _write_agent_home_manifest(
    home_root: Path,
    spec: AgentHomeBootstrapSpec,
    *,
    effective_model: str | None,
    effective_reasoning_effort: str | None,
) -> Path:
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
        "effective_model": effective_model,
        "effective_reasoning_effort": effective_reasoning_effort,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _read_effective_model_config(config_path: Path) -> tuple[str | None, str | None]:
    if not config_path.exists():
        return None, None
    payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    model = payload.get("model")
    reasoning = payload.get("model_reasoning_effort")
    return (
        str(model) if isinstance(model, str) else None,
        str(reasoning) if isinstance(reasoning, str) else None,
    )


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
