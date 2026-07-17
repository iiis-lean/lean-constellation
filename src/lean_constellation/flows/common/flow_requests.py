"""FlowRequest builders used by submit handlers."""

from __future__ import annotations

from typing import Literal

from agent_runtime_kit.flow.models import FlowRequest


def repo_scope_id(repo_key: str, fallback_scope_id: str | None = None) -> str:
    return fallback_scope_id or f"repo:{repo_key}"


def node_scope_id(repo_key: str, node_id: str, fallback_scope_id: str | None = None) -> str:
    return fallback_scope_id or f"repo:{repo_key}:node:{node_id}"


def build_resource_curation_request(
    *,
    scope_id: str,
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"],
    target: str,
    arxiv_version: str | None = None,
    requested_by: str | None = None,
    context_summary: str | None = None,
    repo_key: str | None = None,
    repo_root: str | None = None,
    node_path: str | None = None,
) -> FlowRequest:
    params = {
        "target_kind": target_kind,
        "target": target,
        "arxiv_version": arxiv_version,
        "requested_by": requested_by,
        "context_summary": context_summary,
    }
    if repo_key is not None:
        params["repo_key"] = repo_key
    if repo_root is not None:
        params["repo_root"] = repo_root
    if node_path is not None:
        params["node_path"] = node_path
    return FlowRequest(
        flow_type="resource_curation",
        scope_id=scope_id,
        params=params,
    )


def build_content_node_task_request(
    *,
    repo_key: str,
    node_path: str,
    scope_id: str,
    repo_path: str | None = None,
    contract_version: int | None = None,
    task_mode: str = "run",
    max_parallel_content_node_tasks: int = 1,
) -> FlowRequest:
    params = {
        "repo_key": repo_key,
        "node_path": node_path,
        "contract_version": contract_version,
        "task_mode": task_mode,
        "max_parallel_content_node_tasks": max_parallel_content_node_tasks,
    }
    if repo_path is not None:
        params["repo_path"] = repo_path
    return FlowRequest(
        flow_type="content_node_task",
        scope_id=scope_id,
        params=params,
    )


PREPARATION_RECON_FLOW_TYPES: dict[str, str] = {
    "node_dir_dependency": "node_dir_dependency_recon",
    "node_dir_dependency_recon": "node_dir_dependency_recon",
    "mathlib": "mathlib_recon",
    "mathlib_recon": "mathlib_recon",
    "resource": "resource_recon",
    "resource_recon": "resource_recon",
}


def build_preparation_recon_request(
    *,
    recon_kind: str,
    repo_key: str,
    node_path: str,
    scope_id: str,
    repo_path: str | None = None,
    contract_version: int | None = None,
    objective: str | None = None,
    context_summary: str | None = None,
) -> FlowRequest:
    flow_type = PREPARATION_RECON_FLOW_TYPES[recon_kind]
    params = {
        "repo_key": repo_key,
        "node_path": node_path,
        "objective": objective,
        "context_summary": context_summary,
    }
    if repo_path is not None:
        params["repo_path"] = repo_path
    if contract_version is not None:
        params["contract_version"] = contract_version
    return FlowRequest(
        flow_type=flow_type,
        scope_id=scope_id,
        params=params,
    )


def build_decl_round_request(
    *,
    repo_key: str,
    node_path: str,
    scope_id: str,
    strategy_id: str,
    round_id: str,
    repo_path: str | None = None,
    contract_version: int | None = None,
    round_index: int | None = None,
    summary: str | None = None,
) -> FlowRequest:
    params = {
        "repo_key": repo_key,
        "node_path": node_path,
        "strategy_id": strategy_id,
        "round_id": round_id,
        "round_index": round_index,
        "summary": summary,
    }
    if repo_path is not None:
        params["repo_path"] = repo_path
    if contract_version is not None:
        params["contract_version"] = contract_version
    return FlowRequest(
        flow_type="decl_graph_round",
        scope_id=scope_id,
        params=params,
    )
