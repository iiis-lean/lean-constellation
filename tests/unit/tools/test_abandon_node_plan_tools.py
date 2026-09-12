from types import SimpleNamespace

import pytest

from lean_constellation.flows.common.flow_requests import node_scope_id
from lean_constellation.services import create_test_runtime_services
from tests.unit.tools.test_application_tool_invocation_smoke import _raw, _unwrap_tool_result, _unwrap_tool_failure


def _setup(root):
    runtime = create_test_runtime_services(register_application_tools=True)
    assert runtime.node.node_tree.ensure_root_scope_node(root).ok
    node = runtime.node.create_content_node(root, path="Main.Topic", goal="Topic", boundary="Topic", objective="Develop", success_criteria="Done").value
    runtime.ark.flow_service = SimpleNamespace(list_flows=lambda **kw: [])
    runtime.ark.step_service = SimpleNamespace(list_steps=lambda **kw: [])
    runtime.ark.agent_service = SimpleNamespace(list_agents=lambda **kw: [])
    raw = _raw(root, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator")
    return runtime, node, raw


def _call(runtime, raw, name, **extra):
    return runtime.tool_facade.invoke_agent_tool(raw, tool_name=name, flat_args={"node_path": "Main.Topic", **extra})


def test_abandon_tool_withdraws_initial_plan(tmp_path):
    runtime, node, raw = _setup(tmp_path)
    assert _unwrap_tool_result(_call(runtime, raw, "preview_abandon_node_plan"))["deletable"]
    _unwrap_tool_result(_call(runtime, raw, "abandon_node_plan", reason="Split plan"))
    assert not runtime.node.node_tree.get_node(tmp_path, path=node.path).ok


@pytest.mark.parametrize("status", ["created", "running", "waiting", "completed", "failed"])
def test_abandon_tool_rejects_any_flow_history(tmp_path, status):
    runtime, node, raw = _setup(tmp_path)
    runtime.ark.flow_service.list_flows = lambda **kw: [SimpleNamespace(status=status)]
    preview = _unwrap_tool_result(_call(runtime, raw, "preview_abandon_node_plan"))
    assert "node_plan_runtime_history:flows" in preview["blocking_reasons"]
    assert _unwrap_tool_failure(_call(runtime, raw, "abandon_node_plan", reason="Reject"))[0].kind == "node_plan_abandon_blocked"


@pytest.mark.parametrize("kind", ["step", "agent"])
def test_abandon_tool_rejects_non_flow_runtime_history(tmp_path, kind):
    runtime, node, raw = _setup(tmp_path)
    service = getattr(runtime.ark, kind + "_service")
    setattr(service, "list_" + kind + "s", lambda **kw: [SimpleNamespace(status="completed")])
    assert not _unwrap_tool_result(_call(runtime, raw, "preview_abandon_node_plan"))["deletable"]


def test_abandon_tool_rechecks_runtime_after_preview(tmp_path):
    runtime, node, raw = _setup(tmp_path)
    calls = []
    def flows(**kw):
        calls.append(kw)
        return [] if len(calls) == 1 else [SimpleNamespace(status="created")]
    runtime.ark.flow_service.list_flows = flows
    _unwrap_tool_failure(_call(runtime, raw, "abandon_node_plan", reason="Race"))
    assert runtime.node.node_tree.get_node(tmp_path, path=node.path).ok


def test_abandon_tool_runtime_scan_failure_is_closed(tmp_path):
    runtime, node, raw = _setup(tmp_path)
    runtime.ark.flow_service = SimpleNamespace()
    preview = _unwrap_tool_result(_call(runtime, raw, "preview_abandon_node_plan"))
    assert any(x.startswith("runtime_inspection_failed:") for x in preview["blocking_reasons"])
    _unwrap_tool_failure(_call(runtime, raw, "abandon_node_plan", reason="Reject"))


def test_abandon_runtime_history_is_scoped_to_current_node_identity(tmp_path):
    runtime, old, raw = _setup(tmp_path)
    _unwrap_tool_result(_call(runtime, raw, "abandon_node_plan", reason="Replan"))
    new = runtime.node.create_scope_node(tmp_path, path=old.path, goal="Group", boundary="Group").value
    old_scope = node_scope_id(tmp_path.name, old.node_id)
    new_scope = node_scope_id(tmp_path.name, new.node_id)
    records = {old_scope: [SimpleNamespace(status="completed")]}
    runtime.ark.flow_service.list_flows = lambda **kw: records.get(kw["scope_id"], [])
    assert _unwrap_tool_result(_call(runtime, raw, "preview_abandon_node_plan"))["deletable"]
    records[new_scope] = [SimpleNamespace(status="created")]
    assert not _unwrap_tool_result(_call(runtime, raw, "preview_abandon_node_plan"))["deletable"]


def test_content_plan_cannot_abandon_nodes(tmp_path):
    runtime, node, raw = _setup(tmp_path)
    raw = _raw(tmp_path, view="content_plan", agent_type="ContentPlanAgent", role="plan", node_path=node.path)
    _unwrap_tool_failure(_call(runtime, raw, "abandon_node_plan", reason="Wrong owner"))
    assert runtime.node.node_tree.get_node(tmp_path, path=node.path).ok
