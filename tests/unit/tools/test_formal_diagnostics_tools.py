from __future__ import annotations

from pathlib import Path

from lean_constellation.services import create_test_runtime_services
from lean_constellation.services.tool_facade import RawToolCallContext, RuntimeToolContext


def _runtime(repo_root: Path) -> RuntimeToolContext:
    return RuntimeToolContext(
        flow_id="flow_formal",
        step_id="step_formal",
        agent_id="agent_formal",
        agent_type="statement_formal_worker",
        agent_role="worker",
        expected_view_key="statement_formal_worker",
        repo_root=repo_root,
        node_path="Main.Topic",
        node_kind="content",
        stage="statement_formal",
        round_id="round_1",
        batch_decls=["main_theorem"],
        current_decl="main_theorem",
        decl_kind="theorem",
    )


def test_scan_lean_sorry_axiom_invokes_registered_tool(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    (tmp_path / "Main.lean").write_text(
        "theorem demo : True := by\n  sorry\n-- axiom hidden_in_comment : True\n",
        encoding="utf-8",
    )
    raw = RawToolCallContext(endpoint_view_key="statement_formal_worker", runtime_context=_runtime(tmp_path))

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="scan_lean_sorry_axiom",
        flat_args={"file_path": "Main.lean"},
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is True
    assert result.value.value is not None
    assert result.value.value["contains_sorry"] is True
    assert result.value.value["contains_axiom"] is False


def test_scan_lean_sorry_axiom_rejects_escaping_path(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    raw = RawToolCallContext(endpoint_view_key="statement_formal_worker", runtime_context=_runtime(tmp_path))

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="scan_lean_sorry_axiom",
        flat_args={"file_path": "../outside.lean"},
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind == "tool_file_path_rejected"
