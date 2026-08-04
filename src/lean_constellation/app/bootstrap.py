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
from agent_runtime_kit.agent.provider_contracts import BaseConfigSource, ModelBackendIdentity
from agent_runtime_kit.agent.providers import OpenCodeHomeOptions
from agent_runtime_kit.agent.providers.codex_home import CodexHomeOptions

from lean_constellation.agents import (
    AgentHomeBootstrapSpec,
    agent_type_permission_names,
    build_agent_home_bootstrap_spec,
    build_agent_type_specs,
    get_agent_type_spec,
)
from lean_constellation.app.agent_provider_config import (
    codex_native_config_defaults,
    model_identity_from_override,
    opencode_native_tool_defaults,
    provider_options_from_override,
)
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
    provider_type: str
    home_id: str
    home_root: str
    instruction_path: str
    skill_paths: dict[str, str] = Field(default_factory=dict)
    mcp_server_names: list[str] = Field(default_factory=list)
    effective_model: str | None = None
    effective_reasoning_effort: str | None = None
    summary: str


class ProductionAgentHomesView(StrictModel):
    total: int
    materialized: list[AgentHomeMaterializationView] = Field(default_factory=list)
    failed: list[dict[str, str]] = Field(default_factory=list)
    mcp_http_base_url: str
    provider_types: dict[str, str] = Field(default_factory=dict)
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
    provider_type: str | None = None,
    model_config: ModelBackendIdentity | None = None,
    config_overrides: dict[str, object] | None = None,
    auth_refs: tuple[str, ...] = (),
    provider_options: object | None = None,
    codex_force_full_access: bool = False,
) -> ServiceResult[AgentHomeMaterializationView]:
    """Materialize one AgentType home through ARK HomeService."""

    try:
        resolved_specs = list(agent_type_specs or build_agent_type_specs())
        resolved_agent_spec = get_agent_type_spec(agent_type, specs=resolved_specs)
        permission_names = agent_type_permission_names(agent_type, specs=resolved_specs)
        resolved_provider_type = provider_type or resolved_agent_spec.home_type
        resolved_provider_options = provider_options
        if resolved_provider_type == "codex":
            if resolved_provider_options is not None and not isinstance(
                resolved_provider_options, CodexHomeOptions
            ):
                raise TypeError("codex provider_options must be CodexHomeOptions")
            codex_options = resolved_provider_options or CodexHomeOptions()
            resolved_provider_options = replace(
                codex_options,
                auth_json_path=(
                    Path(auth_json_path).expanduser()
                    if auth_json_path is not None
                    else codex_options.auth_json_path
                ),
            )
            config_overrides = _codex_scoped_config_overrides(
                config_overrides,
                permission_names=permission_names,
                force_full_access=codex_force_full_access,
            )
        elif resolved_provider_type == "opencode":
            if resolved_provider_options is not None and not isinstance(
                resolved_provider_options, OpenCodeHomeOptions
            ):
                raise TypeError("opencode provider_options must be OpenCodeHomeOptions")
            opencode_options = resolved_provider_options or OpenCodeHomeOptions()
            resolved_provider_options = replace(
                opencode_options,
                auth_json_path=(
                    Path(auth_json_path).expanduser()
                    if auth_json_path is not None
                    else opencode_options.auth_json_path
                ),
            )
            config_overrides = _opencode_scoped_config_overrides(
                config_overrides,
                permission_names=permission_names,
            )
        spec = build_agent_home_bootstrap_spec(
            agent_type,
            home_id=home_id,
            mcp_http_base_url=mcp_http_base_url,
            mcp_server_command=mcp_server_command,
            mcp_server_args=mcp_server_args,
            mcp_server_env=mcp_server_env,
            fixed_env=fixed_env,
            required_env=required_env,
            provider_type=resolved_provider_type,  # type: ignore[arg-type]
            base_config=(
                BaseConfigSource(path=str(Path(base_config_path).expanduser()))
                if base_config_path is not None
                else None
            ),
            config_overrides=config_overrides,
            model_config=model_config,
            auth_refs=auth_refs,
            provider_options=resolved_provider_options,
            specs=agent_type_specs,
        )
        ark_spec = spec.provider_home_spec
        home_service = getattr(runtime.ark.agent_service, "home_service", None)
        if home_service is None:
            return runtime.foundation.fail(
                runtime.foundation.issue("home_service_missing", "ARK AgentService does not expose a HomeService.")
            )
        record = home_service.create_home(ark_spec)
        home_root = home_service.resolve_home_root(record.provider_type, record.home_id)
        if record.provider_type == "codex":
            _disable_global_codex_features(home_root / ".codex" / "config.toml")
        instruction_path = _write_agent_instruction(home_root, spec)
        effective_model, effective_reasoning_effort = _effective_model_values(record, home_root)
        manifest_path = _write_agent_home_manifest(
            home_root,
            spec,
            effective_model=effective_model,
            effective_reasoning_effort=effective_reasoning_effort,
        )
        seal_materialization = getattr(home_service, "seal_home_materialization", None)
        if callable(seal_materialization):
            # ARK Home v2 verifies provider-managed files at run time. LC's
            # deliberate post-processing of Codex feature flags must therefore
            # be explicitly sealed; later unsealed mutations still fail closed.
            seal_materialization(record.provider_type, record.home_id)
    except Exception as exc:  # noqa: BLE001 - bootstrap boundary.
        return runtime.foundation.fail(runtime.foundation.issue("agent_home_materialization_failed", f"Agent home materialization failed: {exc}"))

    skill_paths = _materialized_skill_paths(home_root, spec)
    return runtime.foundation.ok(
        AgentHomeMaterializationView(
            agent_type=spec.agent_type,
            provider_type=record.provider_type,
            home_id=record.home_id,
            home_root=str(home_root),
            instruction_path=str(instruction_path),
            skill_paths=skill_paths,
            mcp_server_names=[server.name for server in spec.mcp_servers],
            effective_model=effective_model,
            effective_reasoning_effort=effective_reasoning_effort,
            summary=f"Materialized Agent home {record.home_id}; manifest: {manifest_path.name}.",
        )
    )


def _opencode_scoped_config_overrides(
    config_overrides: dict[str, object] | None,
    *,
    permission_names: set[str],
) -> dict[str, object]:
    """Enforce the repository filesystem boundary for Lean Constellation OpenCode Homes."""

    result = dict(config_overrides or {})
    configured = result.get("permission")
    if configured is None:
        permission: dict[str, object] = {}
    elif isinstance(configured, dict):
        permission = dict(configured)
    else:
        raise TypeError("OpenCode permission override must be a mapping")
    permission["external_directory"] = "deny"
    result["permission"] = permission
    configured_tools = result.get("tools")
    if configured_tools is None:
        tools: dict[str, object] = {}
    elif isinstance(configured_tools, dict):
        tools = dict(configured_tools)
    else:
        raise TypeError("OpenCode tools override must be a mapping")
    tools.update(opencode_native_tool_defaults(permission_names))
    result["tools"] = tools
    return result


def _codex_scoped_config_overrides(
    config_overrides: dict[str, object] | None,
    *,
    permission_names: set[str],
    force_full_access: bool,
) -> dict[str, object]:
    result = codex_native_config_defaults(permission_names)
    result.update(config_overrides or {})
    if force_full_access:
        result["sandbox_mode"] = "danger-full-access"
    return result


def materialize_production_agent_homes(
    runtime: LeanRuntimeServices,
    *,
    mcp_http_base_url: str,
    base_config_path: Path | str | None,
    auth_json_path: Path | str | None,
    shared_elan_home: Path | str | None = None,
    agent_type_specs: Sequence["AgentTypeSpec"] | None = None,
    agent_home_overrides: dict[str, object] | None = None,
    codex_force_full_access: bool = False,
) -> ServiceResult[ProductionAgentHomesView]:
    """Materialize all production Agent homes for a long-running runtime."""

    base_config = Path(base_config_path).expanduser() if base_config_path is not None else None
    auth_json = Path(auth_json_path).expanduser() if auth_json_path is not None else None
    specs = list(agent_type_specs) if agent_type_specs is not None else build_agent_type_specs()
    codex_required = any(spec.home_type == "codex" for spec in specs)
    missing: list[dict[str, str]] = []
    if codex_required and (base_config is None or not base_config.exists()):
        missing.append({"field": "codex_base_config_path", "path": str(base_config) if base_config else ""})
    if codex_required and (auth_json is None or not auth_json.exists()):
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

    materialized: list[AgentHomeMaterializationView] = []
    failed: list[dict[str, str]] = []
    for spec in specs:
        try:
            configured_override = (agent_home_overrides or {}).get(spec.agent_type)
            provider_type = spec.home_type
            home_config_overrides = dict(
                getattr(configured_override, "config_overrides", {}) or {}
            )
            if provider_type == "codex" and configured_override is not None:
                if getattr(configured_override, "model", None) is not None:
                    home_config_overrides["model"] = configured_override.model
                if getattr(configured_override, "model_reasoning_effort", None) is not None:
                    home_config_overrides["model_reasoning_effort"] = (
                        configured_override.model_reasoning_effort
                    )
            result = materialize_agent_home(
                runtime,
                spec.agent_type,
                home_id=spec.agent_type,
                mcp_http_base_url=mcp_http_base_url,
                base_config_path=(
                    getattr(configured_override, "base_config_path", None)
                    if provider_type != "codex"
                    else base_config
                ),
                auth_json_path=auth_json if provider_type == "codex" else None,
                fixed_env={"ELAN_HOME": str(elan_home)},
                required_env=set(getattr(configured_override, "required_env", ()) or ()),
                agent_type_specs=specs,
                provider_type=provider_type,
                model_config=model_identity_from_override(configured_override),
                config_overrides=home_config_overrides,
                auth_refs=tuple(getattr(configured_override, "auth_refs", ()) or ()),
                provider_options=provider_options_from_override(provider_type, configured_override),
                codex_force_full_access=codex_force_full_access,
            )
        except Exception as exc:  # noqa: BLE001 - one Home must not hide other failures.
            failed.append({"agent_type": spec.agent_type, "message": str(exc)})
            continue
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
        provider_types={spec.agent_type: spec.home_type for spec in specs},
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
        "provider_type": spec.home_type,
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


def _effective_model_values(record, home_root: Path) -> tuple[str | None, str | None]:  # noqa: ANN001
    if record.provider_type == "codex":
        return _read_effective_model_config(home_root / ".codex" / "config.toml")
    defaults = record.resolved_defaults or {}
    model = defaults.get("requested_model") or defaults.get("resolved_model")
    reasoning = defaults.get("reasoning_effort")
    return (
        str(model) if isinstance(model, str) else None,
        str(reasoning) if isinstance(reasoning, str) else None,
    )


def _materialized_skill_paths(home_root: Path, spec: AgentHomeBootstrapSpec) -> dict[str, str]:
    roots = (
        home_root / ".agents" / "skills",
        home_root / ".claude" / "skills",
        home_root / ".pi" / "skills",
        home_root / "skills",
    )
    paths: dict[str, str] = {}
    for skill_key in spec.skill_specs:
        for root in roots:
            candidate = root / skill_key / "SKILL.md"
            if candidate.exists():
                paths[skill_key] = str(candidate.parent)
                break
    return paths


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
