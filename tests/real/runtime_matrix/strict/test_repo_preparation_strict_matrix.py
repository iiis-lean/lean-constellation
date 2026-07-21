from __future__ import annotations

import pytest
from agent_runtime_kit.flow.models import FlowPosition

from lean_constellation.app import AdminFlowAdvanceInput, AdminStepStartInput
from lean_constellation.domain.preparation import SourceCorpusMode
from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import RuntimeMatrixWorkspace
from tests.real.runtime_matrix.scripted_provider import ScriptedMcpProvider, install_scripted_provider
from tests.real.runtime_matrix.baseline.test_repo_preparation_matrix import (
    _complete_adapter_catalog_actions,
    _complete_source_index_builder_actions,
    _start_adapter_preparation,
    _start_native_preparation,
    _wait_completed,
    _write_native_preparation_input,
    _write_source_corpus_entry,
)


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_strict_native_preparation_prepare_source_review_retry_and_handoff_evidence(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
    evidence_recorder: EvidenceRecorder,
) -> None:
    ws = runtime_matrix_workspace
    _write_native_preparation_input(ws, source_mode=SourceCorpusMode.PREPARE, allow_interface_supplement=True)
    _write_source_corpus_entry(ws)
    initialized = ws.runtime.repo_workspace.initialize_repo_as_native(ws.provider_repo, project_name=ws.provider_repo.name)
    assert initialized.ok, initialized.issues
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "SourceCorpusPrepareAgent": [
                (
                    "submit_source_corpus_prepared",
                    {
                        "summary": "Prepared strict source corpus.",
                        "entry_path": "README.md",
                        "overview": "Strict source corpus.",
                        "preparation_summary": "Prepared one deterministic source note.",
                    },
                )
            ],
            "SourceIndexBuilderAgent": [
                _complete_source_index_builder_actions("Strict source index first builder round."),
                ("submit_source_index_builder_round", {"summary": "Strict source index second builder round."}),
            ],
            "SourceIndexReviewerAgent": [
                (
                    "submit_source_index_review_round",
                    {
                        "approved": False,
                        "summary": "Strict source index needs one more pass.",
                        "feedback": "Strict Runtime Matrix rejected review branch.",
                    },
                ),
                (
                    "submit_source_index_review_round",
                    {
                        "approved": True,
                        "summary": "Strict source index approved.",
                        "feedback": None,
                    },
                ),
            ],
            "RootInterfacePrepareAgent": [
                (
                    "submit_root_interface_prepare_ready",
                    {"summary": "Strict root interfaces are ready for handoff."},
                )
            ],
        },
        evidence_recorder=evidence_recorder,
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_homes(
        "SourceCorpusPrepareAgent",
        "SourceIndexBuilderAgent",
        "SourceIndexReviewerAgent",
        "RootInterfacePrepareAgent",
        provider_type="scripted",
    )
    unwrap(ws.admin.resume_runtime())
    flow_id = _start_native_preparation(ws)

    _wait_completed(ws, flow_id)

    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result.outcome == "handoff_dispatched"
    assert len(ws.runtime.ark.flow_service.list_flows(flow_type="native_repo_coordinator")) == 1
    evidence_recorder.record_runtime_state(ws.runtime)
    assert "native_repo_preparation" in evidence_recorder.evidence.flow_types
    assert "native_repo_coordinator" in evidence_recorder.evidence.flow_types
    assert {
            "validate_initialize_native_preparation_step",
            "validate_source_index_run_step",
            "open_source_index_update_step",
            "validate_commit_source_index_update_step",
            "native_handoff_gate_step",
        "prepare_coordinator_dispatch_step",
    }.issubset(evidence_recorder.evidence.logic_step_types)
    assert {
        "source_corpus_prepare_agent_step",
        "source_index_builder_agent_step",
        "source_index_reviewer_agent_step",
        "root_interface_prepare_agent_step",
    }.issubset(evidence_recorder.evidence.agent_step_types)
    assert {
        "submit_source_corpus_prepared",
        "submit_source_index_builder_round",
        "submit_source_index_review_round",
        "submit_root_interface_prepare_ready",
    }.issubset(evidence_recorder.evidence.submit_tool_names)


def test_strict_native_preparation_blocked_and_direct_ready_evidence(
    tmp_path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    from tests.real.runtime_matrix.fixtures import create_runtime_matrix_workspace

    blocked_ws = create_runtime_matrix_workspace(tmp_path / "blocked")
    _write_native_preparation_input(blocked_ws, source_mode=SourceCorpusMode.PREPARE, allow_interface_supplement=True)
    initialized = blocked_ws.runtime.repo_workspace.initialize_repo_as_native(
        blocked_ws.provider_repo,
        project_name=blocked_ws.provider_repo.name,
    )
    assert initialized.ok, initialized.issues
    blocked_provider = ScriptedMcpProvider(
        blocked_ws.runtime,
        {
            "SourceCorpusPrepareAgent": [
                (
                    "submit_source_corpus_blocked",
                    {
                        "reason": "Strict source corpus is unavailable.",
                        "attempted_targets": ["local"],
                        "missing_materials": ["source note"],
                        "suggested_next_action": "Provide source material.",
                    },
                )
            ]
        },
        evidence_recorder=evidence_recorder,
    )
    install_scripted_provider(blocked_ws.runtime, blocked_provider)
    blocked_ws.create_home("SourceCorpusPrepareAgent", provider_type="scripted")
    unwrap(blocked_ws.admin.resume_runtime())
    blocked_flow_id = _start_native_preparation(blocked_ws)
    _wait_completed(blocked_ws, blocked_flow_id)
    blocked_flow = blocked_ws.runtime.ark.flow_service.get_flow(blocked_flow_id)
    assert blocked_flow.result.outcome == "blocked"
    evidence_recorder.record_runtime_state(blocked_ws.runtime)

    direct_ws = create_runtime_matrix_workspace(tmp_path / "direct")
    direct_ws.prepare_provider_native_repo(allow_interface_supplement=False)
    direct_provider = ScriptedMcpProvider(
        direct_ws.runtime,
        {
            "SourceIndexBuilderAgent": [
                _complete_source_index_builder_actions("Strict existing source index builder round.", path="source.md"),
            ],
            "SourceIndexReviewerAgent": [
                (
                    "submit_source_index_review_round",
                    {
                        "approved": True,
                        "summary": "Strict existing source index approved.",
                        "feedback": None,
                    },
                ),
            ],
        },
        evidence_recorder=evidence_recorder,
    )
    install_scripted_provider(direct_ws.runtime, direct_provider)
    direct_ws.create_homes("SourceIndexBuilderAgent", "SourceIndexReviewerAgent", provider_type="scripted")
    unwrap(direct_ws.admin.resume_runtime())
    direct_flow_id = _start_native_preparation(direct_ws)
    _wait_completed(direct_ws, direct_flow_id)
    direct_flow = direct_ws.runtime.ark.flow_service.get_flow(direct_flow_id)
    assert direct_flow.result.outcome == "handoff_dispatched"

    # Exercise the legacy monolithic phase retained for checkpoint compatibility.
    legacy_flow_id = _start_native_preparation(direct_ws)
    direct_ws.runtime.ark.flow_service.store.update_flow_record(
        legacy_flow_id,
        lambda flow: (
            setattr(flow.state, "position", FlowPosition(phase="root_interface_prepare")),
            setattr(flow.state, "allow_interface_supplement", False),
        ),
    )
    legacy_advanced = unwrap(
        direct_ws.admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=legacy_flow_id))
    )
    assert legacy_advanced.created_step_id is not None
    legacy_started = unwrap(
        direct_ws.admin.start_step_once(
            AdminStepStartInput(step_id=legacy_advanced.created_step_id, wait=True)
        )
    )
    assert legacy_started.status == "completed"
    evidence_recorder.record_runtime_state(direct_ws.runtime)

    assert {
        "validate_initialize_native_preparation_step",
        "existing_source_corpus_scan_step",
        "prepare_native_lifecycle_child_step",
        "root_interface_direct_ready_step",
    }.issubset(evidence_recorder.evidence.logic_step_types)
    assert {
        "source_corpus_prepare_agent_step",
        "source_index_builder_agent_step",
        "source_index_reviewer_agent_step",
    }.issubset(evidence_recorder.evidence.agent_step_types)
    assert {
        "submit_source_corpus_blocked",
        "submit_source_index_builder_round",
        "submit_source_index_review_round",
    }.issubset(evidence_recorder.evidence.submit_tool_names)


def test_strict_adapter_preparation_ready_and_blocked_evidence(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
    evidence_recorder: EvidenceRecorder,
) -> None:
    ws = runtime_matrix_workspace
    ws.prepare_adapter_truth()
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "AdapterDeclCatalogAgent": [
                (
                    "submit_adapter_catalog_blocked",
                    {
                        "reason": "Strict adapter blocked.",
                        "missing_interfaces": ["main_result"],
                        "evidence_summary": "Strict blocked path confirms the required root interface has no usable upstream binding.",
                    },
                ),
            ]
        },
        evidence_recorder=evidence_recorder,
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_home("AdapterDeclCatalogAgent", provider_type="scripted")
    unwrap(ws.admin.resume_runtime())
    blocked_flow_id = _start_adapter_preparation(ws)
    _wait_completed(ws, blocked_flow_id)
    blocked_flow = ws.runtime.ark.flow_service.get_flow(blocked_flow_id)
    assert blocked_flow.result.outcome == "blocked"

    provider.scripts["AdapterDeclCatalogAgent"].append(_complete_adapter_catalog_actions())
    ready_flow_id = _start_adapter_preparation(ws)
    _wait_completed(ws, ready_flow_id)
    ready_flow = ws.runtime.ark.flow_service.get_flow(ready_flow_id)
    assert ready_flow.result.outcome == "adapter_ready"
    assert ready_flow.result.catalog_decl_count >= 1

    evidence_recorder.record_runtime_state(ws.runtime)
    assert "adapter_repo_preparation" in evidence_recorder.evidence.flow_types
    assert {
        "validate_adapter_preparation_input_step",
        "ensure_adapter_main_catalog_step",
        "finalize_adapter_ready_step",
        "mark_adapter_provider_ready_step",
    }.issubset(evidence_recorder.evidence.logic_step_types)
    assert "adapter_decl_catalog_agent_step" in evidence_recorder.evidence.agent_step_types
    assert {
        "submit_adapter_catalog_blocked",
        "submit_adapter_catalog_ready",
    }.issubset(evidence_recorder.evidence.submit_tool_names)
