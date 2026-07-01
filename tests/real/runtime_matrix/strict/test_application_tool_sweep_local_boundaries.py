from __future__ import annotations

from pathlib import Path

import pytest

from lean_constellation.mcp import create_mcp_server
from lean_constellation.services.external_clients import LakeCommandClient, LakeCommandClientConfig
from lean_constellation.services.tool_facade import RuntimeToolContext
from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import create_runtime_matrix_workspace
from tests.real.runtime_matrix.strict_helpers import call_tool_with_evidence


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_strict_real_lake_mathlib_name_tool_executes_with_evidence(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    ws = create_runtime_matrix_workspace(
        tmp_path,
        lake_client=LakeCommandClient(LakeCommandClientConfig(timeout_seconds=120)),
    )
    initial_build = ws.lake.run_lake_build(ws.provider_repo, timeout_seconds=120)
    assert initial_build.ok, initial_build.summary
    server = unwrap(create_mcp_server(ws.runtime, view_keys=["mathlib_recon"]))
    ctx = RuntimeToolContext(
        flow_id="strict_runtime_matrix_mathlib_local_boundary",
        step_id="strict_runtime_matrix_mathlib_local_boundary_step",
        agent_id="strict_runtime_matrix_mathlib_local_boundary_agent",
        agent_type="MathlibReconAgent",
        agent_role="worker",
        expected_view_key="mathlib_recon",
        repo_root=ws.provider_repo,
    )

    checked = call_tool_with_evidence(
        server,
        "mathlib_recon",
        "check_mathlib_name",
        {
            "module": "Main.Topic.Core.Prelude",
            "decl_name": "Main.Topic.Core.seedTrue",
        },
        runtime_context=ctx,
        recorder=evidence_recorder,
        assertion_summary="Real Lake-backed Mathlib name check passed for a local Lean declaration.",
    )
    assert checked.value["passed"] is True
    assert checked.value["toolkit_tool"] == "lake_command"
    assert "#check Main.Topic.Core.seedTrue" in checked.value["checked_code"]
