"""MCP call context helpers."""

from __future__ import annotations

import json
from typing import Any

from lean_constellation.services.tool_facade import RawToolCallContext, RuntimeToolContext


RUNTIME_ENV_KEYS: dict[str, str] = {
    "flow_id": "ARK_FLOW_ID",
    "step_id": "ARK_STEP_ID",
    "agent_id": "ARK_AGENT_ID",
    "scope_id": "ARK_SCOPE_ID",
    "agent_type": "LEAN_CONSTELLATION_AGENT_TYPE",
    "agent_role": "LEAN_CONSTELLATION_AGENT_ROLE",
    "expected_view_key": "LEAN_CONSTELLATION_EXPECTED_TOOL_VIEW",
    "workspace_root": "LEAN_CONSTELLATION_WORKSPACE_ROOT",
    "repo_root": "LEAN_CONSTELLATION_REPO_ROOT",
    "node_path": "LEAN_CONSTELLATION_NODE_PATH",
    "node_kind": "LEAN_CONSTELLATION_NODE_KIND",
    "contract_version": "LEAN_CONSTELLATION_CONTRACT_VERSION",
    "stage": "LEAN_CONSTELLATION_STAGE",
    "round_id": "LEAN_CONSTELLATION_ROUND_ID",
    "batch_decls": "LEAN_CONSTELLATION_BATCH_DECLS",
    "current_decl": "LEAN_CONSTELLATION_CURRENT_DECL",
    "decl_kind": "LEAN_CONSTELLATION_DECL_KIND",
    "retry_attempt": "LEAN_CONSTELLATION_RETRY_ATTEMPT",
    "successful_submission_count": "LEAN_CONSTELLATION_SUCCESSFUL_SUBMISSION_COUNT",
    "successful_submission_kind": "LEAN_CONSTELLATION_SUCCESSFUL_SUBMISSION_KIND",
}


def build_raw_tool_call_context(
    *,
    endpoint_view_key: str | None = None,
    headers: dict[str, str] | None = None,
    env: dict[str, str] | None = None,
    runtime_context: Any | None = None,
) -> RawToolCallContext:
    """Create the RawToolCallContext consumed by ToolFacade MCP invocation."""

    return RawToolCallContext(
        endpoint_view_key=endpoint_view_key,
        headers=dict(headers or {}),
        env=dict(env or {}),
        runtime_context=runtime_context,
    )


def runtime_context_to_env(runtime_context: RuntimeToolContext) -> dict[str, str]:
    """Serialize a RuntimeToolContext into ARK-style MCP endpoint env."""

    data = runtime_context.model_dump(mode="json")
    env: dict[str, str] = {}
    for field, env_key in RUNTIME_ENV_KEYS.items():
        value = data.get(field)
        if value is None:
            continue
        if isinstance(value, list):
            env[env_key] = json.dumps(value)
        else:
            env[env_key] = str(value)
    return env
