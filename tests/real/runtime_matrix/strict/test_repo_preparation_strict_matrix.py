from __future__ import annotations

import pytest
from agent_runtime_kit.flow.models import FlowPosition

from lean_constellation.domain.preparation import SourceCorpusMode
from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import RuntimeMatrixWorkspace
from tests.real.runtime_matrix.scripted_provider import ScriptedMcpProvider, install_scripted_provider, schedule_until
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
        cli_type="codex",
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
    blocked_ws.create_home("SourceCorpusPrepareAgent", cli_type="codex")
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
    direct_ws.create_homes("SourceIndexBuilderAgent", "SourceIndexReviewerAgent", cli_type="codex")
    unwrap(direct_ws.admin.resume_runtime())
    direct_flow_id = _start_native_preparation(direct_ws)
    _wait_completed(direct_ws, direct_flow_id)
    direct_flow = direct_ws.runtime.ark.flow_service.get_flow(direct_flow_id)
    assert direct_flow.result.outcome == "handoff_dispatched"
    evidence_recorder.record_runtime_state(direct_ws.runtime)

    assert {
        "validate_initialize_native_preparation_step",
        "existing_source_corpus_scan_step",
        "prepare_native_lifecycle_child_step",
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


def test_strict_serialized_inline_native_preparation_compatibility_steps(
    tmp_path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    """Exercise the retained inline path used when restoring pre-child-Flow state."""

    from tests.real.runtime_matrix.fixtures import create_runtime_matrix_workspace

    create_ws = create_runtime_matrix_workspace(tmp_path / "inline_create")
    create_ws.prepare_provider_native_repo(allow_interface_supplement=False)
    unwrap(create_ws.admin.resume_runtime())
    create_flow_id = _start_native_preparation(create_ws)
    create_ws.runtime.ark.flow_service.store.update_flow_record(
        create_flow_id,
        lambda flow: setattr(flow.state, "use_reusable_preparation_children", False),
    )

    schedule_until(
        create_ws.runtime,
        lambda: _step_completed(create_ws, create_flow_id, "create_draft_source_index_step"),
        limit=40,
    )
    evidence_recorder.record_runtime_state(create_ws.runtime)

    commit_ws = create_runtime_matrix_workspace(tmp_path / "inline_commit")
    commit_ws.prepare_provider_native_repo(allow_interface_supplement=False)
    unwrap(commit_ws.admin.resume_runtime())
    commit_flow_id = _start_native_preparation(commit_ws)

    def restore_commit_phase(flow) -> None:  # noqa: ANN001
        flow.state.use_reusable_preparation_children = False
        flow.state.last_source_index_review_approved = True
        flow.state.position = FlowPosition(phase="source_index_commit")

    commit_ws.runtime.ark.flow_service.store.update_flow_record(commit_flow_id, restore_commit_phase)
    schedule_until(
        commit_ws.runtime,
        lambda: _step_completed(commit_ws, commit_flow_id, "commit_source_index_step"),
        limit=20,
    )
    evidence_recorder.record_runtime_state(commit_ws.runtime)

    root_ws = create_runtime_matrix_workspace(tmp_path / "inline_root")
    root_ws.prepare_provider_native_repo(allow_interface_supplement=False)
    unwrap(root_ws.admin.resume_runtime())
    root_flow_id = _start_native_preparation(root_ws)

    def restore_root_phase(flow) -> None:  # noqa: ANN001
        flow.state.use_reusable_preparation_children = False
        flow.state.allow_interface_supplement = False
        flow.state.position = FlowPosition(phase="root_interface_prepare")

    root_ws.runtime.ark.flow_service.store.update_flow_record(root_flow_id, restore_root_phase)
    schedule_until(
        root_ws.runtime,
        lambda: _step_completed(root_ws, root_flow_id, "root_interface_direct_ready_step"),
        limit=20,
    )
    evidence_recorder.record_runtime_state(root_ws.runtime)

    assert {
        "create_draft_source_index_step",
        "commit_source_index_step",
        "root_interface_direct_ready_step",
    }.issubset(evidence_recorder.evidence.logic_step_types)


def _step_completed(ws: RuntimeMatrixWorkspace, flow_id: str, step_type: str) -> bool:
    return any(
        step.status.value == "completed"
        for step in ws.runtime.ark.flow_service.list_steps(flow_id=flow_id, step_type=step_type)
    )


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
    ws.create_home("AdapterDeclCatalogAgent", cli_type="codex")
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
