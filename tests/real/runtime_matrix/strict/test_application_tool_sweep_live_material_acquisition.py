from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_runtime_kit.flow.models import FlowRequest
from lean_constellation.mcp import create_mcp_server
from lean_constellation.services.tool_facade import RuntimeToolContext
from tests.real.runtime_matrix.admin_helpers import run_next_created_step, unwrap
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import RuntimeMatrixWorkspace
from tests.real.runtime_matrix.strict_helpers import call_tool_with_evidence, checkpoint_with_evidence, restore_with_evidence


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_strict_live_web_and_arxiv_material_acquisition_execute_through_mcp(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
    tmp_path: Path,
) -> None:
    if os.environ.get("LEAN_CONSTELLATION_STRICT_LIVE_MATERIAL_ACQUISITION") != "1":
        pytest.skip("Set LEAN_CONSTELLATION_STRICT_LIVE_MATERIAL_ACQUISITION=1 to run live web/arXiv material acquisition.")

    ws = runtime_matrix_workspace
    ws.prepare_provider_native_repo()
    recorder = EvidenceRecorder()
    recorder.add_note("Runtime Matrix strict live material acquisition used real web and arXiv network downloads.")

    server = unwrap(create_mcp_server(ws.runtime, view_keys=["resource_curator"]))
    web_url = os.environ.get("LEAN_CONSTELLATION_REAL_WEB_URL", "https://example.com/")
    flow_id = _start_resource_curation(ws, target_kind="web", target=web_url)
    run_next_created_step(ws.admin, flow_id)
    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    draft_id = flow.state.active_resource_draft_key
    assert draft_id is not None
    draft = unwrap(ws.runtime.material.get_resource_draft(ws.provider_repo, draft_id=draft_id))
    draft_root = Path(draft.draft_root)
    ctx = RuntimeToolContext(
        flow_id=flow_id,
        step_id="strict_live_material_resource_curator_step",
        agent_id="strict_live_material_resource_curator_agent",
        agent_type="ResourceCuratorAgent",
        agent_role="worker",  # type: ignore[arg-type]
        expected_view_key="resource_curator",
        repo_root=ws.provider_repo,
    )
    checkpoint = checkpoint_with_evidence(
        ws.admin,
        ws.provider_repo,
        scope_ids=["repo:Provider"],
        label="strict_live_material_acquisition",
        recorder=recorder,
    )

    web_acquired = call_tool_with_evidence(
        server,
        "resource_curator",
        "acquire_resource_material",
        {"target": web_url, "preferred_kind": "web_page"},
        runtime_context=ctx,
        recorder=recorder,
        assertion_summary="Live web page acquisition downloaded an HTML artifact through MCP.",
    )
    web_artifact_ref = web_acquired.value["primary_artifact_ref"]
    assert web_artifact_ref.endswith(".html")
    assert (draft_root / web_artifact_ref).exists()

    web_extracted = call_tool_with_evidence(
        server,
        "resource_curator",
        "extract_resource_artifact",
        {"artifact_ref": web_artifact_ref, "extraction_kind": "html_main_text"},
        runtime_context=ctx,
        recorder=recorder,
        assertion_summary="Live web artifact extraction produced readable markdown text through MCP.",
    )
    assert web_extracted.value["primary_material_ref"].endswith(".md")
    assert (draft_root / web_extracted.value["primary_material_ref"]).exists()
    assert "Example Domain" in (web_extracted.value["preview"] or "")

    arxiv_id = os.environ.get("LEAN_CONSTELLATION_REAL_ARXIV_ID", "2401.00001")
    arxiv_acquired = call_tool_with_evidence(
        server,
        "resource_curator",
        "acquire_resource_material",
        {"target": arxiv_id, "preferred_kind": "arxiv_source"},
        runtime_context=ctx,
        recorder=recorder,
        assertion_summary="Live arXiv source acquisition downloaded an e-print artifact through MCP.",
    )
    arxiv_artifact_ref = arxiv_acquired.value["primary_artifact_ref"]
    assert arxiv_artifact_ref
    assert (draft_root / arxiv_artifact_ref).exists()

    arxiv_extracted = call_tool_with_evidence(
        server,
        "resource_curator",
        "extract_resource_artifact",
        {"artifact_ref": arxiv_artifact_ref, "extraction_kind": "tex_source"},
        runtime_context=ctx,
        recorder=recorder,
        assertion_summary="Live arXiv source extraction unpacked TeX material through MCP.",
    )
    assert arxiv_extracted.value["primary_material_ref"]
    assert (draft_root / arxiv_extracted.value["primary_material_ref"]).exists()
    assert arxiv_extracted.value["preview"]

    restore_with_evidence(
        ws.admin,
        ws.provider_repo,
        checkpoint.snapshot_id,
        scope_ids=["repo:Provider"],
        label="strict_live_material_acquisition",
        recorder=recorder,
    )
    assert not (draft_root / web_artifact_ref).exists()
    assert not (draft_root / web_extracted.value["primary_material_ref"]).exists()
    assert not (draft_root / arxiv_artifact_ref).exists()
    assert not (draft_root / arxiv_extracted.value["primary_material_ref"]).exists()

    artifact_dir = tmp_path / "runtime_matrix_evidence"
    recorder.export_json(artifact_dir / "strict_live_material_acquisition.json")
    recorder.export_markdown_summary(artifact_dir / "strict_live_material_acquisition.md")
    assert recorder.evidence.application_tool_names == {"acquire_resource_material", "extract_resource_artifact"}
    assert len(recorder.evidence.application_tool_calls) == 4
    assert any(item.event == "restore" and item.pruned is True for item in recorder.evidence.snapshots)


def _start_resource_curation(ws: RuntimeMatrixWorkspace, *, target_kind: str, target: str) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="resource_curation",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(ws.provider_repo),
                "target_kind": target_kind,
                "target": target,
                "requested_by": "strict_live_material_acquisition",
                "requested_use": "supporting_material",
                "consumer_need": "Readable material for the strict live acquisition probe.",
                "context_summary": "Strict live material acquisition active draft setup.",
                "node_path": "Main.Core",
            },
        )
    )
