from __future__ import annotations

from pathlib import Path

import pytest

from lean_constellation.mcp import create_mcp_server
from lean_constellation.services.tool_facade import RuntimeToolContext
from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import CONTENT_NODE_PATH, RuntimeMatrixWorkspace
from tests.real.runtime_matrix.strict.test_application_tool_sweep_decl_graph import _field, _seed_ready_decl
from tests.real.runtime_matrix.strict.tool_sweep_partitions import scope_export_tool_sweep_names
from tests.real.runtime_matrix.strict_helpers import call_tool_with_evidence, checkpoint_with_evidence, restore_with_evidence


pytestmark = [pytest.mark.real, pytest.mark.slow]

SCOPE_PATH = "Main.Topic"
DECL_NAME = "scope_export_result"


def test_strict_scope_export_write_tool_cases_execute(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
    evidence_recorder: EvidenceRecorder,
    tmp_path: Path,
) -> None:
    ws = runtime_matrix_workspace
    ws.setup_content_node(repo_root=ws.provider_repo, node_path=CONTENT_NODE_PATH)
    _seed_ready_decl(ws, DECL_NAME, public=True)
    added_interface = ws.runtime.node.interface.add_interface(
        ws.provider_repo,
        node_path=SCOPE_PATH,
        name=DECL_NAME,
        kind="theorem",
        summary="Expose the strict scope export declaration.",
        statement_hint="The exported declaration states True.",
        actor="coordinator",
    )
    assert added_interface.ok, added_interface.issues

    server = unwrap(create_mcp_server(ws.runtime, view_keys=["native_repo_coordinator"]))
    ctx = _ctx(ws)
    checkpoint = checkpoint_with_evidence(
        ws.admin,
        ws.provider_repo,
        scope_ids=["repo:Provider"],
        label="strict_scope_export_tool_sweep",
        recorder=evidence_recorder,
    )

    candidates = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "list_scope_export_candidates",
        {"scope_path": SCOPE_PATH},
        runtime_context=ctx,
        recorder=evidence_recorder,
        assertion_summary="Scope export candidate list contains the ready public declaration.",
    )
    assert any(_field(item, "ref", "name") == DECL_NAME for item in _field(candidates.value, "candidates"))

    added_export = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "add_scope_export",
        {"scope_path": SCOPE_PATH, "decl_node": CONTENT_NODE_PATH, "decl_name": DECL_NAME, "revision": 1},
        runtime_context=ctx,
        recorder=evidence_recorder,
        assertion_summary="Ready public declaration was added to the scope export list.",
    )
    assert _field(added_export.value, "changed") is True
    assert _field(added_export.value, "operation") == "add"
    assert _field(added_export.value, "export", "declaration_name") == DECL_NAME

    bound = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "bind_node_interface",
        {"node_path": SCOPE_PATH, "interface_name": DECL_NAME, "decl_name": DECL_NAME, "decl_node": CONTENT_NODE_PATH},
        runtime_context=ctx,
        recorder=evidence_recorder,
        assertion_summary="Scope interface was bound to the exported declaration.",
    )
    assert DECL_NAME in str(bound.value)

    unbound = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "unbind_node_interface",
        {"node_path": SCOPE_PATH, "name": DECL_NAME},
        runtime_context=ctx,
        recorder=evidence_recorder,
        assertion_summary="Scope interface binding was removed.",
    )
    assert DECL_NAME in str(unbound.value)

    exports = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "list_scope_exports",
        {"scope_path": SCOPE_PATH},
        runtime_context=ctx,
        recorder=evidence_recorder,
        assertion_summary="Scope export list contains the exported declaration before removal.",
    )
    export_items = _field(exports.value, "exports")
    export_index = next(
        _field(item, "index")
        for item in export_items
        if _field(item, "declaration_name") == DECL_NAME
    )

    removed = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "remove_scope_export",
        {"scope_path": SCOPE_PATH, "index": export_index},
        runtime_context=ctx,
        recorder=evidence_recorder,
        assertion_summary="Scope export was removed after interface unbind.",
    )
    assert _field(removed.value, "changed") is True
    assert _field(removed.value, "operation") == "remove"
    assert _field(removed.value, "export", "declaration_name") == DECL_NAME

    restore_with_evidence(
        ws.admin,
        ws.provider_repo,
        checkpoint.snapshot_id,
        scope_ids=["repo:Provider"],
        label="strict_scope_export_tool_sweep",
        recorder=evidence_recorder,
    )
    restored_exports = unwrap(ws.runtime.node.export.list_scope_exports(ws.provider_repo, scope_path=SCOPE_PATH))
    assert restored_exports == []

    assert scope_export_tool_sweep_names() <= evidence_recorder.evidence.application_tool_names
    evidence_recorder.add_note("strict_scope_export_tool_sweep_completed")
    evidence_recorder.export_json(tmp_path / "runtime_matrix_evidence" / "scope_export_tool_sweep.json")
    evidence_recorder.export_markdown_summary(tmp_path / "runtime_matrix_evidence" / "scope_export_tool_sweep.md")


def _ctx(ws: RuntimeMatrixWorkspace) -> RuntimeToolContext:
    return RuntimeToolContext(
        flow_id="strict_runtime_matrix_scope_export",
        step_id="strict_runtime_matrix_scope_export_step",
        agent_id="strict_runtime_matrix_scope_export_agent",
        agent_type="CoordinatorAgent",
        agent_role="coordinator",
        expected_view_key="native_repo_coordinator",
        repo_root=ws.provider_repo,
    )
