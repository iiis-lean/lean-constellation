from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from lean_constellation.mcp import create_mcp_server
from lean_constellation.services.tool_facade import RuntimeToolContext, ToolSpec
from lean_constellation.tools import build_application_tool_specs
from tests.real.runtime_matrix.admin_helpers import checkpoint_branch, restore_branch, unwrap
from tests.real.runtime_matrix.fixtures import RuntimeMatrixWorkspace


pytestmark = [pytest.mark.real, pytest.mark.slow]


@dataclass(frozen=True)
class ToolCoverageMode:
    tool_name: str
    mode: str
    reason: str


CALLED_READ_ONLY_TOOLS = {
    "get_preparation_input",
    "normalize_resource_target",
    "search_mathlib_index",
    "get_current_node_contract",
    "list_current_node_deps",
}

CALLED_CHECKPOINTED_WRITE_TOOLS = {
    "add_root_interface",
}


def test_application_tool_sweep_classifies_every_registered_tool() -> None:
    specs = build_application_tool_specs()
    assert len(specs) == 259
    classified = {item.tool_name: item for item in (_classify_tool(spec) for spec in specs)}
    assert set(classified) == {spec.name for spec in specs}
    assert {classified[name].mode for name in CALLED_READ_ONLY_TOOLS} == {"called_success"}
    assert {classified[name].mode for name in CALLED_CHECKPOINTED_WRITE_TOOLS} == {"checkpointed_write"}
    assert any(item.mode == "env_gated" for item in classified.values())
    assert any(item.mode == "schema_only_with_reason" for item in classified.values())
    assert all(item.reason for item in classified.values())


def test_read_only_application_tool_sweep_representatives(runtime_matrix_workspace: RuntimeMatrixWorkspace) -> None:
    ws = runtime_matrix_workspace
    ws.prepare_provider_native_repo()
    _ensure_content_node(ws)
    server = unwrap(
        create_mcp_server(
            ws.runtime,
            view_keys=["repo_format_discovery", "resource_curator", "mathlib_recon", "content_plan"],
        )
    )

    prep = unwrap(
        server.call_tool(
            "repo_format_discovery",
            "get_preparation_input",
            {},
            runtime_context=_ctx(ws.provider_repo, view="repo_format_discovery", agent_type="RepoFormatDiscoveryAgent", role="coordinator"),
        )
    )
    assert prep.ok is True, prep.issues
    assert prep.value["input"]["goal"]

    normalized = unwrap(
        server.call_tool(
            "resource_curator",
            "normalize_resource_target",
            {"target": ws.resources.web_url},
            runtime_context=_ctx(ws.provider_repo, view="resource_curator", agent_type="ResourceCuratorAgent"),
        )
    )
    assert normalized.ok is True, normalized.issues
    assert normalized.value["kind"] == "web_url"

    mathlib = unwrap(
        server.call_tool(
            "mathlib_recon",
            "search_mathlib_index",
            {"query": "Nat", "limit": 5},
            runtime_context=_ctx(
                ws.provider_repo,
                view="mathlib_recon",
                agent_type="MathlibReconAgent",
                node_path="Main.Core",
            ),
        )
    )
    assert mathlib.ok is True, mathlib.issues
    assert mathlib.value["query"] == "Nat"

    contract = unwrap(
        server.call_tool(
            "content_plan",
            "get_current_node_contract",
            {},
            runtime_context=_ctx(
                ws.provider_repo,
                view="content_plan",
                agent_type="ContentPlanAgent",
                role="plan",
                node_path="Main.Core",
            ),
        )
    )
    assert contract.ok is True, contract.issues
    assert contract.value["node_path"] == "Main.Core"

    deps = unwrap(
        server.call_tool(
            "content_plan",
            "list_current_node_deps",
            {},
            runtime_context=_ctx(
                ws.provider_repo,
                view="content_plan",
                agent_type="ContentPlanAgent",
                role="plan",
                node_path="Main.Core",
            ),
        )
    )
    assert deps.ok is True, deps.issues
    assert deps.value["node_path"] == "Main.Core"


def test_checkpointed_write_application_tool_sweep_restore(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    ws.provider_repo.mkdir(parents=True, exist_ok=True)
    ws.write_bootstrap_preparation(ws.provider_repo)
    assert ws.runtime.node.ensure_native_root_main_contract(ws.provider_repo).ok
    checkpoint = checkpoint_branch(
        ws.admin,
        ws.provider_repo,
        scope_ids=["repo:Provider"],
        label="runtime_matrix_tool_sweep_write",
    )
    server = unwrap(create_mcp_server(ws.runtime, view_keys=["root_interface_prepare"]))
    added = unwrap(
        server.call_tool(
            "root_interface_prepare",
            "add_root_interface",
            {"name": "baseline_tool_sweep_iface", "kind": "theorem", "summary": "Baseline ToolSweep interface."},
            runtime_context=_ctx(ws.provider_repo, view="root_interface_prepare", agent_type="RootInterfacePrepareAgent"),
        )
    )
    assert added.ok is True, added.issues
    assert any(item["name"] == "baseline_tool_sweep_iface" for item in added.value["contract"]["interfaces"])

    restore_branch(ws.admin, ws.provider_repo, checkpoint.snapshot_id)
    restored = unwrap(
        server.call_tool(
            "root_interface_prepare",
            "list_root_interfaces",
            {},
            runtime_context=_ctx(ws.provider_repo, view="root_interface_prepare", agent_type="RootInterfacePrepareAgent"),
        )
    )
    assert restored.ok is True, restored.issues
    assert all(item["name"] != "baseline_tool_sweep_iface" for item in restored.value["interfaces"])


def _ctx(
    repo_root: Path,
    *,
    view: str,
    agent_type: str,
    role: str = "worker",
    node_path: str | None = None,
) -> RuntimeToolContext:
    return RuntimeToolContext(
        flow_id=f"runtime_matrix_{view}",
        step_id=f"runtime_matrix_{view}_step",
        agent_id=f"runtime_matrix_{view}_agent",
        agent_type=agent_type,
        agent_role=role,  # type: ignore[arg-type]
        expected_view_key=view,
        repo_root=repo_root,
        node_path=node_path,
        node_kind="content" if node_path else None,
        contract_version=1 if node_path else None,
    )


def _ensure_content_node(ws: RuntimeMatrixWorkspace) -> None:
    assert ws.runtime.node.ensure_native_root_main_contract(ws.provider_repo).ok
    created = ws.runtime.node.create_content_node(
        ws.provider_repo,
        path="Main.Core",
        goal="Runtime Matrix content goal.",
        boundary="Use local runtime matrix fixtures.",
        objective="Exercise application tool sweep read-only tools.",
        success_criteria="Read-only tools return structured views.",
    )
    if not created.ok:
        assert any(issue.kind == "node_path_exists" for issue in created.issues), created.issues


def _classify_tool(spec: ToolSpec) -> ToolCoverageMode:
    name = spec.name
    groups = set(spec.tool_groups)
    if name in CALLED_READ_ONLY_TOOLS:
        return ToolCoverageMode(name, "called_success", "Representative read-only Runtime Matrix MCP call.")
    if name in CALLED_CHECKPOINTED_WRITE_TOOLS:
        return ToolCoverageMode(name, "checkpointed_write", "Representative write call covered by checkpoint/restore.")
    if groups & {
        "upstream_navigation",
        "mathlib_semantic_search",
        "mathlib_navigation",
        "external_resource_discovery",
        "upstream_repo_search",
        "source_acquisition",
        "resource_acquisition",
    }:
        return ToolCoverageMode(name, "env_gated", "Requires live Toolkit, real network, GitHub, arXiv, or external repo visibility.")
    if any(token in name for token in ("write", "set_", "add_", "create_", "delete", "finalize", "record_", "bind_", "refresh", "capture", "prepare_")):
        return ToolCoverageMode(name, "schema_only_with_reason", "Context-sensitive mutation covered by flow-specific tests or checkpointed representatives.")
    return ToolCoverageMode(name, "schema_only_with_reason", "Safe schema registration is covered; direct call needs a narrower business fixture.")
