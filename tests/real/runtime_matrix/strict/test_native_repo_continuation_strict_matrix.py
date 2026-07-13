from __future__ import annotations

import pytest

from agent_runtime_kit.flow.models import FlowRequest, FlowStatus
from lean_constellation.app import RepoRunRequestInput
from lean_constellation.domain.repo import ProofAvailability, RepoWorkMode
from lean_constellation.domain.repo_run import SourceScope
from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import RuntimeMatrixWorkspace
from tests.real.runtime_matrix.scripted_provider import ScriptedMcpProvider, install_scripted_provider, schedule_until


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_strict_native_repo_continuation_reuses_stable_release_and_hands_off(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
    evidence_recorder: EvidenceRecorder,
) -> None:
    ws = runtime_matrix_workspace
    ws.prepare_provider_ready_repo()
    provider = ScriptedMcpProvider(
        ws.runtime,
        {"CoordinatorAgent": [("submit_repo_ready", {"summary": "Publish strict continuation baseline."})]},
        evidence_recorder=evidence_recorder,
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_home("CoordinatorAgent", cli_type="codex")
    unwrap(ws.admin.resume_runtime())
    release_flow_id = ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(ws.provider_repo),
                "start_mode": "admin_start",
                "start_reason": "Publish strict continuation baseline.",
            },
        )
    )
    schedule_until(
        ws.runtime,
        lambda: ws.runtime.ark.flow_service.get_flow(release_flow_id).status is FlowStatus.COMPLETED,
        limit=80,
    )
    release_flow = ws.runtime.ark.flow_service.get_flow(release_flow_id)
    assert release_flow.result.outcome == "candidate_prepared"
    assert release_flow.result.prepared_release is not None
    release_id = release_flow.result.prepared_release.release.release_id

    started = unwrap(
        ws.admin.continue_native_repo(
            RepoRunRequestInput(
                repo_root=ws.provider_repo,
                repo_key="Provider",
                run_objective="Continue from the stable strict Runtime Matrix release.",
                target_proof_availability=ProofAvailability.PROVED,
                work_mode=RepoWorkMode.PROVED_FULL_GRAPH,
                source_scope=SourceScope(mode="none"),
                index_policy="reuse",
                root_interface_policy="reuse",
                enqueue=True,
            )
        )
    )
    assert started.flow_type == "native_repo_continuation"

    schedule_until(
        ws.runtime,
        lambda: ws.runtime.ark.flow_service.get_flow(started.flow_id).status is FlowStatus.COMPLETED,
        limit=160,
    )

    flow = ws.runtime.ark.flow_service.get_flow(started.flow_id)
    assert flow.result.outcome == "handoff_dispatched"
    assert flow.input.base_release_id == release_id
    assert flow.state.publication_started_stable is True
    assert flow.state.publication_transitioned is True
    coordinator = ws.runtime.ark.flow_service.get_flow(flow.state.coordinator_flow_id)
    assert coordinator.flow_type == "native_repo_coordinator"
    evidence_recorder.record_runtime_state(ws.runtime)

    assert "native_repo_continuation" in evidence_recorder.evidence.flow_types
    assert {
        "prepare_native_run_mutation_step",
        "apply_native_run_step",
        "prepare_continuation_dispatch_step",
        "continuation_handoff_gate_step",
        "decide_root_interface_agent_step",
    }.issubset(evidence_recorder.evidence.logic_step_types)
