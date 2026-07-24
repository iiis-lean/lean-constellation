from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import pytest
from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from lean_constellation.app import (
    AdminStepStartInput,
    SetAgentStepOverrideInput,
)
from lean_constellation.domain.repo import ProofAvailability, RepoWorkMode
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.external_clients import LakeCommandClient, LakeCommandClientConfig
from lean_constellation.flows.testing import ControlledAgentOverrideSpec
from tests.real.runtime_matrix.admin_helpers import (
    run_next_created_step,
    run_until_step_created,
    set_scripted_provider_override,
    unwrap,
)
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import DeclRoundFixture, RuntimeMatrixWorkspace, create_runtime_matrix_workspace
from tests.real.runtime_matrix.strict_helpers import run_scripted_actions_with_evidence
from tests.real.runtime_matrix.strict.real_codex_helpers import (
    materialize_strict_codex_home,
    require_real_codex,
    strict_controlled_agent_specs,
    write_noninteractive_codex_base_config,
)


pytestmark = [pytest.mark.real, pytest.mark.slow, pytest.mark.real_codex]


def test_strict_real_codex_coordinator_resources_tools_and_submit(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    config_home = require_real_codex()
    base_config_path = write_noninteractive_codex_base_config(config_home, tmp_path)
    agent_type = "CoordinatorControlledTestAgent"
    agent_specs = strict_controlled_agent_specs("CoordinatorAgent")
    ws = create_runtime_matrix_workspace(tmp_path)
    ws.prepare_provider_ready_repo()
    home_root = materialize_strict_codex_home(
        ws,
        agent_type=agent_type,
        config_home=config_home,
        base_config_path=base_config_path,
        agent_type_specs=agent_specs,
    )

    prompt_marker = "RTCODEX_PROMPT_MARKER_COORDINATOR_STRICT_20260630"
    developer_marker_prefix = "RTCODEX_DEV_MARKER_COORDINATOR_STRICT_"
    developer_marker = f"{developer_marker_prefix}20260630"
    artifact_path = ws.provider_repo / ".lean_constellation" / "runtime_matrix_artifacts" / "coordinator_resource_report.json"
    flow_id = _start_coordinator(ws)
    step_id = run_until_step_created(ws.admin, flow_id, "coordinator_agent_step")
    view = unwrap(
        ws.admin.set_agent_step_override(
            SetAgentStepOverrideInput(
                step_id=step_id,
                override=ControlledAgentOverrideSpec(
                    strategy="fresh_test_agent_type",
                    agent_type_override=agent_type,
                    provider_type_override="codex",
                    prompt_overlay=_coordinator_resource_probe_prompt(prompt_marker),
                    developer_instructions_overlay=(
                        "\n\nRuntime Matrix strict resource probe developer marker:\n"
                        f"{developer_marker}\n"
                        "When asked for a developer marker, copy this exact marker from developer instructions.\n"
                    ),
                    env_overrides={
                        "LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH": str(artifact_path),
                    },
                    metadata={"runtime_matrix_case": "strict_real_codex_coordinator_resource_probe"},
                ),
            )
        )
    )
    assert view.override is not None

    real_step_timeout = float(os.environ.get("LEAN_CONSTELLATION_REAL_CODEX_STEP_TIMEOUT", "300"))
    started = unwrap(ws.admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=real_step_timeout)))
    assert started.status == "completed", started
    mark_ready_step_id = run_next_created_step(ws.admin, flow_id, timeout_s=20)
    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    assert flow.result.outcome == "candidate_prepared"
    assert flow.result.prepared_release is not None

    data = _read_artifact(artifact_path)
    assert data["prompt_marker_seen"] == prompt_marker
    assert data["developer_marker_seen"] == developer_marker
    assert data["artifact_home_root"] == str(home_root)
    assert "coordinator-content-result-closeout" in data["skill_keys_seen"]
    assert data["private_consumer_guidance_seen"] is True
    assert data["dependent_ready_guidance_seen"] is True
    tools_called = set(data["application_tools_called"])
    assert {"inspect_workspace_for_coordinator", "get_node_tree"}.issubset(tools_called)
    assert data["submit_tool_called"] == "submit_repo_ready"
    step = ws.runtime.ark.flow_service.get_step(step_id)
    assert step.submission is not None
    assert step.submission.tool_name == "submit_repo_ready"

    evidence_recorder.record_runtime_state(ws.runtime)
    for tool_name in sorted(tools_called):
        evidence_recorder.record_tool_call(
            tool_name=tool_name,
            view_key="native_repo_coordinator",
            view_kind="application",
            agent_type=agent_type,
            step_id=step_id,
            ok=True,
            assertion_summary="Called by real Codex controlled Coordinator probe.",
        )
    evidence_recorder.record_tool_call(
        tool_name="submit_repo_ready",
        view_key="native_repo_coordinator_submit",
        view_kind="submit",
        agent_type=agent_type,
        step_id=step_id,
        ok=True,
        assertion_summary="Accepted from real Codex controlled Coordinator probe.",
    )
    _record_real_codex_artifact(
        evidence_recorder,
        ws=ws,
        agent_type=agent_type,
        step_id=step_id,
        artifact_path=artifact_path,
        started=started,
        data=data,
        prompt_marker_seen=data["prompt_marker_seen"] == prompt_marker,
        instruction_marker_seen=data["developer_marker_seen"] == developer_marker,
        skill_markers_seen=list(data["skill_keys_seen"]),
        tools_called=[*sorted(tools_called), "submit_repo_ready"],
    )
    assert mark_ready_step_id


def test_strict_real_codex_resource_curator_resources_tools_and_submit(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    config_home = require_real_codex()
    base_config_path = write_noninteractive_codex_base_config(config_home, tmp_path)
    agent_type = "ResourceCuratorControlledTestAgent"
    agent_specs = strict_controlled_agent_specs("ResourceCuratorAgent")
    ws = create_runtime_matrix_workspace(tmp_path)
    ws.prepare_provider_native_repo()
    home_root = materialize_strict_codex_home(
        ws,
        agent_type=agent_type,
        config_home=config_home,
        base_config_path=base_config_path,
        agent_type_specs=agent_specs,
    )

    target = ws.resources.web_url
    prompt_marker = "RTCODEX_PROMPT_MARKER_RESOURCE_CURATOR_STRICT_20260630"
    developer_marker_prefix = "RTCODEX_DEV_MARKER_RESOURCE_CURATOR_STRICT_"
    developer_marker = f"{developer_marker_prefix}20260630"
    artifact_path = (
        ws.provider_repo
        / ".lean_constellation"
        / "resources"
        / "runtime_matrix_artifacts"
        / "resource_curator_report.json"
    )
    flow_id = _start_resource_curation(ws, target_kind="web", target=target)
    preflight_step_id = run_next_created_step(ws.admin, flow_id, timeout_s=20)
    preflight_step = ws.runtime.ark.flow_service.get_step(preflight_step_id)
    assert preflight_step.result is not None
    assert preflight_step.result.outcome == "continue_to_curator"
    step_id = run_until_step_created(ws.admin, flow_id, "resource_curator_agent_step")
    view = unwrap(
        ws.admin.set_agent_step_override(
            SetAgentStepOverrideInput(
                step_id=step_id,
                override=ControlledAgentOverrideSpec(
                    strategy="fresh_test_agent_type",
                    agent_type_override=agent_type,
                    provider_type_override="codex",
                    prompt_overlay=_resource_curator_probe_prompt(prompt_marker, target),
                    developer_instructions_overlay=(
                        "\n\nRuntime Matrix strict resource probe developer marker:\n"
                        f"{developer_marker}\n"
                        "When asked for a developer marker, copy this exact marker from developer instructions.\n"
                    ),
                    env_overrides={
                        "LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH": str(artifact_path),
                    },
                    metadata={"runtime_matrix_case": "strict_real_codex_resource_curator_probe"},
                ),
            )
        )
    )
    assert view.override is not None

    real_step_timeout = float(os.environ.get("LEAN_CONSTELLATION_REAL_CODEX_STEP_TIMEOUT", "300"))
    started = unwrap(ws.admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=real_step_timeout)))
    assert started.status == "completed", started
    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    assert flow.result.outcome == "local_resource_created"
    assert flow.result.resource_key is not None

    data = _read_artifact(artifact_path)
    assert data["prompt_marker_seen"] == prompt_marker
    assert data["developer_marker_seen"] == developer_marker
    assert data["artifact_home_root"] == str(home_root)
    assert "resource-draft-curation" in data["skill_keys_seen"]
    tools_called = set(data["application_tools_called"])
    assert {"normalize_resource_target", "get_resource_draft", "check_resource_draft"}.issubset(tools_called)
    assert "allocate_resource_draft" not in tools_called
    assert data["submit_tool_called"] == "submit_local_resource_created"
    assert data["draft_id"] == flow.state.active_resource_draft_key
    step = ws.runtime.ark.flow_service.get_step(step_id)
    assert step.submission is not None
    assert step.submission.tool_name == "submit_local_resource_created"
    loaded = ws.runtime.material.resource_library.get_resource(ws.provider_repo, resource_key=flow.result.resource_key)
    assert loaded.ok and loaded.value is not None, loaded.issues

    evidence_recorder.record_runtime_state(ws.runtime)
    for tool_name in sorted(tools_called):
        evidence_recorder.record_tool_call(
            tool_name=tool_name,
            view_key="resource_curator",
            view_kind="application",
            agent_type=agent_type,
            step_id=step_id,
            ok=True,
            assertion_summary="Called by real Codex controlled ResourceCurator probe.",
        )
    evidence_recorder.record_tool_call(
        tool_name="submit_local_resource_created",
        view_key="resource_curator_submit",
        view_kind="submit",
        agent_type=agent_type,
        step_id=step_id,
        ok=True,
        assertion_summary="Accepted from real Codex controlled ResourceCurator probe.",
    )
    _record_real_codex_artifact(
        evidence_recorder,
        ws=ws,
        agent_type=agent_type,
        step_id=step_id,
        artifact_path=artifact_path,
        started=started,
        data=data,
        prompt_marker_seen=data["prompt_marker_seen"] == prompt_marker,
        instruction_marker_seen=data["developer_marker_seen"] == developer_marker,
        skill_markers_seen=list(data["skill_keys_seen"]),
        tools_called=[*sorted(tools_called), "submit_local_resource_created"],
    )


def test_strict_real_codex_content_plan_work_config_and_completion_gate_smoke(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    config_home = require_real_codex()
    base_config_path = write_noninteractive_codex_base_config(config_home, tmp_path)
    agent_type = "ContentPlanControlledTestAgent"
    agent_specs = strict_controlled_agent_specs("ContentPlanAgent")
    ws = create_runtime_matrix_workspace(tmp_path)
    ws.setup_content_node()
    updated = ws.runtime.repo_workspace.metadata.update_repo_config(
        ws.provider_repo,
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
    )
    assert updated.ok, updated.issues
    home_root = materialize_strict_codex_home(
        ws,
        agent_type=agent_type,
        config_home=config_home,
        base_config_path=base_config_path,
        agent_type_specs=agent_specs,
    )

    prompt_marker = "RTCODEX_PROMPT_MARKER_CONTENT_PLAN_MATURITY_SMOKE_20260706"
    developer_marker_prefix = "RTCODEX_DEV_MARKER_CONTENT_PLAN_MATURITY_SMOKE_"
    developer_marker = f"{developer_marker_prefix}20260706"
    artifact_path = ws.provider_repo / ".lean_constellation" / "runtime_matrix_artifacts" / "content_plan_maturity_smoke.json"
    flow_id = _start_content_task(ws)
    admission_step_id = run_next_created_step(ws.admin, flow_id, timeout_s=20)
    assert ws.runtime.ark.flow_service.get_step(admission_step_id).result.outcome == "accepted"
    step_id = run_until_step_created(ws.admin, flow_id, "content_plan_agent_step")
    view = unwrap(
        ws.admin.set_agent_step_override(
            SetAgentStepOverrideInput(
                step_id=step_id,
                override=ControlledAgentOverrideSpec(
                    strategy="fresh_test_agent_type",
                    agent_type_override=agent_type,
                    provider_type_override="codex",
                    prompt_overlay=_content_plan_maturity_probe_prompt(prompt_marker),
                    developer_instructions_overlay=(
                        "\n\nRuntime Matrix content plan maturity smoke developer marker:\n"
                        f"{developer_marker}\n"
                        "When asked for a developer marker, copy this exact marker from developer instructions.\n"
                    ),
                    env_overrides={
                        "LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH": str(artifact_path),
                    },
                    metadata={"runtime_matrix_case": "strict_real_codex_content_plan_maturity_smoke"},
                ),
            )
        )
    )
    assert view.override is not None

    real_step_timeout = float(os.environ.get("LEAN_CONSTELLATION_REAL_CODEX_STEP_TIMEOUT", "300"))
    started = unwrap(ws.admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=real_step_timeout)))
    assert started.status == "completed", started
    step = ws.runtime.ark.flow_service.get_step(step_id)
    assert step.submission is not None
    assert step.submission.tool_name == "submit_content_node_blocked"

    data = _read_artifact(artifact_path)
    assert data["prompt_marker_seen"] == prompt_marker
    assert data["developer_marker_seen"] == developer_marker
    assert data["artifact_home_root"] == str(home_root)
    assert "content-plan-declared-interface-mode" in data["skill_keys_seen"]
    tools_called = set(data["application_tools_called"])
    assert {"get_current_repo_work_config", "check_current_content_node_completion"}.issubset(tools_called)
    assert data["submit_tool_called"] == "submit_content_node_blocked"
    tool_results = data["tool_results"]
    assert isinstance(tool_results, dict)
    work_config = tool_results["get_current_repo_work_config"]
    assert work_config["target_proof_availability"] == "declared"
    assert work_config["work_mode"] == "declared_interface"
    completion = tool_results["check_current_content_node_completion"]
    assert completion["target_proof_availability"] == "declared"

    evidence_recorder.record_runtime_state(ws.runtime)
    for tool_name in sorted(tools_called):
        evidence_recorder.record_tool_call(
            tool_name=tool_name,
            view_key="content_plan",
            view_kind="application",
            agent_type=agent_type,
            step_id=step_id,
            ok=True,
            assertion_summary="Called by real Codex controlled ContentPlan maturity smoke.",
        )
    evidence_recorder.record_tool_call(
        tool_name="submit_content_node_blocked",
        view_key="content_plan_submit",
        view_kind="submit",
        agent_type=agent_type,
        step_id=step_id,
        ok=True,
        assertion_summary="Accepted from real Codex controlled ContentPlan maturity smoke.",
    )
    _record_real_codex_artifact(
        evidence_recorder,
        ws=ws,
        agent_type=agent_type,
        step_id=step_id,
        artifact_path=artifact_path,
        started=started,
        data=data,
        prompt_marker_seen=data["prompt_marker_seen"] == prompt_marker,
        instruction_marker_seen=data["developer_marker_seen"] == developer_marker,
        skill_markers_seen=list(data["skill_keys_seen"]),
        tools_called=[*sorted(tools_called), "submit_content_node_blocked"],
    )


def test_strict_real_codex_statement_formal_worker_resources_tools_and_submit(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    _require_lake_and_lean()
    config_home = require_real_codex()
    base_config_path = write_noninteractive_codex_base_config(config_home, tmp_path)
    agent_type = "StatementFormalWorkerControlledTestAgent"
    agent_specs = strict_controlled_agent_specs("StatementFormalWorkerAgent")
    ws = create_runtime_matrix_workspace(
        tmp_path,
        lake_client=LakeCommandClient(LakeCommandClientConfig(timeout_seconds=120)),
    )
    initial_build = ws.lake.run_lake_build(ws.provider_repo, timeout_seconds=120)
    assert initial_build.ok, initial_build
    round_fixture = ws.create_decl_round(target_state=DeclState.PROVED)
    ws.create_home("StatementNLWorkerControlledTestAgent")
    ws.create_home("StatementNLReviewerControlledTestAgent")
    home_root = materialize_strict_codex_home(
        ws,
        agent_type=agent_type,
        config_home=config_home,
        base_config_path=base_config_path,
        agent_type_specs=agent_specs,
    )

    prompt_marker = "RTCODEX_PROMPT_MARKER_STATEMENT_FORMAL_STRICT_20260630"
    developer_marker_prefix = "RTCODEX_DEV_MARKER_STATEMENT_FORMAL_STRICT_"
    developer_marker = f"{developer_marker_prefix}20260630"
    artifact_path = ws.provider_repo / ".lean_constellation" / "runtime_matrix_artifacts" / "statement_formal_report.json"
    flow_id = _start_decl_round(ws, round_fixture)

    run_next_created_step(ws.admin, flow_id, timeout_s=20)
    run_next_created_step(ws.admin, flow_id, timeout_s=20)
    run_next_created_step(ws.admin, flow_id, timeout_s=20)
    statement_nl_worker_id = run_until_step_created(ws.admin, flow_id, "decl_stage_worker_agent_step", max_advances=5)
    _assert_decl_stage_step(ws, statement_nl_worker_id, stage="statement_nl")
    set_scripted_provider_override(
        ws.admin,
        statement_nl_worker_id,
        agent_type="StatementNLWorkerControlledTestAgent",
        prompt_overlay="Strict Runtime Matrix: write statement NL and submit completed.",
        env_overrides={
            "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": "statement_nl_worker",
            "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "decl_stage_worker_submit",
        },
    )
    run_scripted_actions_with_evidence(
        ws.admin,
        statement_nl_worker_id,
        [
            (
                "application",
                "set_statement_nl",
                {
                    "decl_name": round_fixture.decl_name,
                    "text": "The strict real Codex statement formal theorem states True.",
                },
            ),
            (
                "submit",
                "submit_stage_worker_completed",
                {
                    "summary": "Statement NL completed before strict real Codex statement formal worker.",
                },
            ),
        ],
        recorder=evidence_recorder,
        timeout_s=20,
    )

    statement_nl_reviewer_id = run_until_step_created(ws.admin, flow_id, "decl_stage_reviewer_agent_step", max_advances=5)
    _assert_decl_stage_step(ws, statement_nl_reviewer_id, stage="statement_nl")
    set_scripted_provider_override(
        ws.admin,
        statement_nl_reviewer_id,
        agent_type="StatementNLReviewerControlledTestAgent",
        prompt_overlay="Strict Runtime Matrix: approve statement NL and submit review.",
        env_overrides={
            "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": "statement_nl_reviewer",
            "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "decl_stage_reviewer_submit",
        },
    )
    run_scripted_actions_with_evidence(
        ws.admin,
        statement_nl_reviewer_id,
        [
            (
                "application",
                "record_statement_nl_review_passed",
                {
                    "decl_name": round_fixture.decl_name,
                    "summary": "Statement NL accepted before strict real Codex statement formal worker.",
                },
            ),
            ("submit", "submit_stage_review", {"summary": "Statement NL accepted before strict real Codex statement formal worker."}),
        ],
        recorder=evidence_recorder,
        timeout_s=20,
    )

    run_next_created_step(ws.admin, flow_id, timeout_s=20)
    run_next_created_step(ws.admin, flow_id, timeout_s=20)
    step_id = run_until_step_created(ws.admin, flow_id, "decl_stage_worker_agent_step", max_advances=5)
    _assert_decl_stage_step(ws, step_id, stage="statement_formal")
    view = unwrap(
        ws.admin.set_agent_step_override(
            SetAgentStepOverrideInput(
                step_id=step_id,
                override=ControlledAgentOverrideSpec(
                    strategy="fresh_test_agent_type",
                    agent_type_override=agent_type,
                    provider_type_override="codex",
                    prompt_overlay=_statement_formal_probe_prompt(prompt_marker, round_fixture.decl_name),
                    developer_instructions_overlay=(
                        "\n\nRuntime Matrix strict resource probe developer marker:\n"
                        f"{developer_marker}\n"
                        "When asked for a developer marker, copy this exact marker from developer instructions.\n"
                    ),
                    env_overrides={
                        "LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH": str(artifact_path),
                    },
                    metadata={"runtime_matrix_case": "strict_real_codex_statement_formal_probe"},
                ),
            )
        )
    )
    assert view.override is not None

    real_step_timeout = float(os.environ.get("LEAN_CONSTELLATION_REAL_CODEX_STEP_TIMEOUT", "300"))
    started = unwrap(ws.admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=real_step_timeout)))
    assert started.status == "completed", started
    step = ws.runtime.ark.flow_service.get_step(step_id)
    assert step.submission is not None
    assert step.submission.tool_name == "submit_stage_worker_completed"
    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is not FlowStatus.FAILED
    assert flow.state.position.phase == "stage_reviewer"

    data = _read_artifact(artifact_path)
    assert data["prompt_marker_seen"] == prompt_marker
    assert data["developer_marker_seen"] == developer_marker
    assert data["artifact_home_root"] == str(home_root)
    assert "lean-statement-formalization" in data["skill_keys_seen"]
    tools_called = set(data["application_tools_called"])
    assert {
        "prepare_statement_formal_file",
        "scan_lean_sorry_axiom",
        "capture_statement_formal_file",
        "check_formal_stage_consistency",
    }.issubset(tools_called)
    # The AgentStep submission is the source of truth for submit-tool execution.
    # Some Codex runs write the artifact just before the final submit call.
    assert data["submit_tool_called"] in {"submit_stage_worker_completed", None}
    assert data["decl_name"] == round_fixture.decl_name
    assert data["lean_file_path"]
    tool_results = data.get("tool_results")
    assert isinstance(tool_results, dict), data
    assert tool_results["capture_statement_formal_file"]["ok"] is True
    assert tool_results["check_formal_stage_consistency"]["passed"] is True
    revision = ws.runtime.decl_graph.get_decl_revision(
        ws.provider_repo,
        node_path=round_fixture.node_path,
        name=round_fixture.decl_name,
        revision=1,
    )
    assert revision.ok and revision.value is not None, revision.issues
    assert revision.value.statement_lean_check["status"] == "passed"

    evidence_recorder.record_runtime_state(ws.runtime)
    for tool_name in sorted(tools_called):
        evidence_recorder.record_tool_call(
            tool_name=tool_name,
            view_key="statement_formal_worker",
            view_kind="application",
            agent_type=agent_type,
            step_id=step_id,
            ok=True,
            assertion_summary="Called by real Codex controlled StatementFormalWorker probe.",
        )
    evidence_recorder.record_tool_call(
        tool_name="submit_stage_worker_completed",
        view_key="decl_stage_worker_submit",
        view_kind="submit",
        agent_type=agent_type,
        step_id=step_id,
        ok=True,
        assertion_summary="Accepted from real Codex controlled StatementFormalWorker probe.",
    )
    _record_real_codex_artifact(
        evidence_recorder,
        ws=ws,
        agent_type=agent_type,
        step_id=step_id,
        artifact_path=artifact_path,
        started=started,
        data=data,
        prompt_marker_seen=data["prompt_marker_seen"] == prompt_marker,
        instruction_marker_seen=data["developer_marker_seen"] == developer_marker,
        skill_markers_seen=list(data["skill_keys_seen"]),
        tools_called=[*sorted(tools_called), "submit_stage_worker_completed"],
    )


def test_strict_real_codex_proof_formal_worker_resources_tools_and_submit(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    _require_lake_and_lean()
    config_home = require_real_codex()
    base_config_path = write_noninteractive_codex_base_config(config_home, tmp_path)
    agent_type = "ProofFormalWorkerControlledTestAgent"
    agent_specs = strict_controlled_agent_specs("ProofFormalWorkerAgent")
    ws = create_runtime_matrix_workspace(
        tmp_path,
        lake_client=LakeCommandClient(LakeCommandClientConfig(timeout_seconds=120)),
    )
    initial_build = ws.lake.run_lake_build(ws.provider_repo, timeout_seconds=120)
    assert initial_build.ok, initial_build
    round_fixture = ws.create_decl_round(target_state=DeclState.PROVED)
    ws.create_homes(
        "StatementNLWorkerControlledTestAgent",
        "StatementNLReviewerControlledTestAgent",
        "StatementFormalWorkerControlledTestAgent",
        "StatementFormalReviewerControlledTestAgent",
        "ProofNLWorkerControlledTestAgent",
        "ProofNLReviewerControlledTestAgent",
    )
    home_root = materialize_strict_codex_home(
        ws,
        agent_type=agent_type,
        config_home=config_home,
        base_config_path=base_config_path,
        agent_type_specs=agent_specs,
    )

    prompt_marker = "RTCODEX_PROMPT_MARKER_PROOF_FORMAL_STRICT_20260701"
    developer_marker_prefix = "RTCODEX_DEV_MARKER_PROOF_FORMAL_STRICT_"
    developer_marker = f"{developer_marker_prefix}20260701"
    artifact_path = ws.provider_repo / ".lean_constellation" / "runtime_matrix_artifacts" / "proof_formal_report.json"
    flow_id = _start_decl_round(ws, round_fixture)

    _complete_statement_nl_stage_for_real_codex(ws, flow_id, round_fixture, evidence_recorder)
    _complete_statement_formal_stage_for_real_codex(ws, flow_id, round_fixture, evidence_recorder)
    _complete_proof_nl_stage_for_real_codex(ws, flow_id, round_fixture, evidence_recorder)

    run_next_created_step(ws.admin, flow_id, timeout_s=20)
    run_next_created_step(ws.admin, flow_id, timeout_s=20)
    step_id = run_until_step_created(ws.admin, flow_id, "decl_stage_worker_agent_step", max_advances=5)
    _assert_decl_stage_step(ws, step_id, stage="proof_formal")
    view = unwrap(
        ws.admin.set_agent_step_override(
            SetAgentStepOverrideInput(
                step_id=step_id,
                override=ControlledAgentOverrideSpec(
                    strategy="fresh_test_agent_type",
                    agent_type_override=agent_type,
                    provider_type_override="codex",
                    prompt_overlay=_proof_formal_probe_prompt(prompt_marker, round_fixture.decl_name),
                    developer_instructions_overlay=(
                        "\n\nRuntime Matrix strict resource probe developer marker:\n"
                        f"{developer_marker}\n"
                        "When asked for a developer marker, copy this exact marker from developer instructions.\n"
                    ),
                    env_overrides={
                        "LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH": str(artifact_path),
                    },
                    metadata={"runtime_matrix_case": "strict_real_codex_proof_formal_probe"},
                ),
            )
        )
    )
    assert view.override is not None

    real_step_timeout = float(os.environ.get("LEAN_CONSTELLATION_REAL_CODEX_STEP_TIMEOUT", "360"))
    started = unwrap(ws.admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=real_step_timeout)))
    assert started.status == "completed", started
    step = ws.runtime.ark.flow_service.get_step(step_id)
    assert step.submission is not None
    assert step.submission.tool_name == "submit_stage_worker_completed"
    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is not FlowStatus.FAILED
    assert flow.state.position.phase == "stage_reviewer"

    data = _read_artifact(artifact_path)
    assert data["prompt_marker_seen"] == prompt_marker
    assert data["developer_marker_seen"] == developer_marker
    assert data["artifact_home_root"] == str(home_root)
    assert "lean-proof-formalization" in data["skill_keys_seen"]
    tools_called = set(data["application_tools_called"])
    assert {
        "prepare_proof_formal_file",
        "check_proof_formal_policy",
        "scan_lean_sorry_axiom",
        "capture_proof_formal_file",
        "check_formal_stage_consistency",
    }.issubset(tools_called)
    assert data["submit_tool_called"] == "submit_stage_worker_completed"
    assert data["decl_name"] == round_fixture.decl_name
    assert data["lean_file_path"]
    tool_results = data.get("tool_results")
    assert isinstance(tool_results, dict), data
    assert tool_results["capture_proof_formal_file"]["ok"] is True
    assert tool_results["check_formal_stage_consistency"]["passed"] is True
    revision = ws.runtime.decl_graph.get_decl_revision(
        ws.provider_repo,
        node_path=round_fixture.node_path,
        name=round_fixture.decl_name,
        revision=1,
    )
    assert revision.ok and revision.value is not None, revision.issues
    assert revision.value.proof_lean_check["status"] == "passed"

    evidence_recorder.record_runtime_state(ws.runtime)
    for tool_name in sorted(tools_called):
        evidence_recorder.record_tool_call(
            tool_name=tool_name,
            view_key="proof_formal_worker",
            view_kind="application",
            agent_type=agent_type,
            step_id=step_id,
            ok=True,
            assertion_summary="Called by real Codex controlled ProofFormalWorker probe.",
        )
    evidence_recorder.record_tool_call(
        tool_name="submit_stage_worker_completed",
        view_key="decl_stage_worker_submit",
        view_kind="submit",
        agent_type=agent_type,
        step_id=step_id,
        ok=True,
        assertion_summary="Accepted from real Codex controlled ProofFormalWorker probe.",
    )
    _record_real_codex_artifact(
        evidence_recorder,
        ws=ws,
        agent_type=agent_type,
        step_id=step_id,
        artifact_path=artifact_path,
        started=started,
        data=data,
        prompt_marker_seen=data["prompt_marker_seen"] == prompt_marker,
        instruction_marker_seen=data["developer_marker_seen"] == developer_marker,
        skill_markers_seen=list(data["skill_keys_seen"]),
        tools_called=[*sorted(tools_called), "submit_stage_worker_completed"],
    )


def test_strict_real_codex_adapter_decl_catalog_resources_tools_and_submit(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    config_home = require_real_codex()
    base_config_path = write_noninteractive_codex_base_config(config_home, tmp_path)
    agent_type = "AdapterDeclCatalogControlledTestAgent"
    agent_specs = strict_controlled_agent_specs("AdapterDeclCatalogAgent")
    ws = create_runtime_matrix_workspace(tmp_path)
    ws.prepare_adapter_truth()
    home_root = materialize_strict_codex_home(
        ws,
        agent_type=agent_type,
        config_home=config_home,
        base_config_path=base_config_path,
        agent_type_specs=agent_specs,
    )

    prompt_marker = "RTCODEX_PROMPT_MARKER_ADAPTER_DECL_CATALOG_STRICT_20260701"
    developer_marker_prefix = "RTCODEX_DEV_MARKER_ADAPTER_DECL_CATALOG_STRICT_"
    developer_marker = f"{developer_marker_prefix}20260701"
    artifact_path = ws.adapter_repo / ".lean_constellation" / "runtime_matrix_artifacts" / "adapter_decl_catalog_report.json"
    flow_id = _start_adapter_preparation(ws)
    validate_step_id = run_next_created_step(ws.admin, flow_id, timeout_s=20)
    validate_step = ws.runtime.ark.flow_service.get_step(validate_step_id)
    assert validate_step.result is not None
    assert validate_step.result.outcome == "passed"
    ensure_step_id = run_next_created_step(ws.admin, flow_id, timeout_s=20)
    ensure_step = ws.runtime.ark.flow_service.get_step(ensure_step_id)
    assert ensure_step.result is not None
    assert ensure_step.result.outcome == "ready"
    step_id = run_until_step_created(ws.admin, flow_id, "adapter_decl_catalog_agent_step", max_advances=5)
    view = unwrap(
        ws.admin.set_agent_step_override(
            SetAgentStepOverrideInput(
                step_id=step_id,
                override=ControlledAgentOverrideSpec(
                    strategy="fresh_test_agent_type",
                    agent_type_override=agent_type,
                    provider_type_override="codex",
                    prompt_overlay=_adapter_decl_catalog_probe_prompt(prompt_marker),
                    developer_instructions_overlay=(
                        "\n\nRuntime Matrix strict resource probe developer marker:\n"
                        f"{developer_marker}\n"
                        "When asked for a developer marker, copy this exact marker from developer instructions.\n"
                    ),
                    env_overrides={
                        "LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH": str(artifact_path),
                    },
                    metadata={"runtime_matrix_case": "strict_real_codex_adapter_decl_catalog_probe"},
                ),
            )
        )
    )
    assert view.override is not None

    real_step_timeout = float(os.environ.get("LEAN_CONSTELLATION_REAL_CODEX_STEP_TIMEOUT", "420"))
    started = unwrap(ws.admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=real_step_timeout)))
    assert started.status == "completed", started
    _finish_flow_with_created_steps(ws, flow_id, timeout_s=20)
    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    assert flow.result.outcome == "adapter_ready"
    assert flow.result.catalog_decl_count >= 1

    data = _read_artifact(artifact_path)
    assert data["prompt_marker_seen"] == prompt_marker
    assert data["developer_marker_seen"] == developer_marker
    assert data["artifact_home_root"] == str(home_root)
    assert isinstance(data["skill_keys_seen"], list)
    tools_called = set(data["application_tools_called"])
    assert {
        "inspect_adapter_input",
        "list_preparation_requirements",
        "list_root_interfaces",
        "get_adapter_upstream_metadata",
        "get_adapter_upstream_status",
        "create_adapter_decl",
        "set_adapter_statement_nl",
        "set_adapter_statement_formal",
        "find_adapter_decl_by_upstream",
        "set_adapter_proof_nl",
        "set_adapter_proof_formal",
        "finalize_adapter_decl",
        "bind_adapter_interface",
        "check_adapter_catalog_ready_preflight",
    }.issubset(tools_called)
    assert data["submit_tool_called"] == "submit_adapter_catalog_ready"
    assert data["decl_name"] == "main_result"
    step = ws.runtime.ark.flow_service.get_step(step_id)
    assert step.submission is not None
    assert step.submission.tool_name == "submit_adapter_catalog_ready"

    evidence_recorder.record_runtime_state(ws.runtime)
    for tool_name in sorted(tools_called):
        evidence_recorder.record_tool_call(
            tool_name=tool_name,
            view_key="adapter_repo_import",
            view_kind="application",
            agent_type=agent_type,
            step_id=step_id,
            ok=True,
            assertion_summary="Called by real Codex controlled AdapterDeclCatalog probe.",
        )
    evidence_recorder.record_tool_call(
        tool_name="submit_adapter_catalog_ready",
        view_key="adapter_repo_import_submit",
        view_kind="submit",
        agent_type=agent_type,
        step_id=step_id,
        ok=True,
        assertion_summary="Accepted from real Codex controlled AdapterDeclCatalog probe.",
    )
    _record_real_codex_artifact(
        evidence_recorder,
        ws=ws,
        agent_type=agent_type,
        step_id=step_id,
        artifact_path=artifact_path,
        started=started,
        data=data,
        prompt_marker_seen=data["prompt_marker_seen"] == prompt_marker,
        instruction_marker_seen=data["developer_marker_seen"] == developer_marker,
        skill_markers_seen=list(data["skill_keys_seen"]),
        tools_called=[*sorted(tools_called), "submit_adapter_catalog_ready"],
    )


def test_strict_real_codex_mathlib_recon_resources_tools_and_submit(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    config_home = require_real_codex()
    base_config_path = write_noninteractive_codex_base_config(config_home, tmp_path)
    agent_type = "MathlibReconControlledTestAgent"
    agent_specs = strict_controlled_agent_specs("MathlibReconAgent")
    ws = create_runtime_matrix_workspace(tmp_path)
    ws.setup_content_node()
    home_root = materialize_strict_codex_home(
        ws,
        agent_type=agent_type,
        config_home=config_home,
        base_config_path=base_config_path,
        agent_type_specs=agent_specs,
    )

    prompt_marker = "RTCODEX_PROMPT_MARKER_MATHLIB_RECON_STRICT_20260701"
    developer_marker_prefix = "RTCODEX_DEV_MARKER_MATHLIB_RECON_STRICT_"
    developer_marker = f"{developer_marker_prefix}20260701"
    artifact_path = ws.provider_repo / ".lean_constellation" / "runtime_matrix_artifacts" / "mathlib_recon_report.json"
    flow_id = _start_mathlib_recon(ws)
    step_id = run_until_step_created(ws.admin, flow_id, "mathlib_recon_agent_step", max_advances=5)
    view = unwrap(
        ws.admin.set_agent_step_override(
            SetAgentStepOverrideInput(
                step_id=step_id,
                override=ControlledAgentOverrideSpec(
                    strategy="fresh_test_agent_type",
                    agent_type_override=agent_type,
                    provider_type_override="codex",
                    prompt_overlay=_mathlib_recon_probe_prompt(prompt_marker),
                    developer_instructions_overlay=(
                        "\n\nRuntime Matrix strict resource probe developer marker:\n"
                        f"{developer_marker}\n"
                        "When asked for a developer marker, copy this exact marker from developer instructions.\n"
                    ),
                    env_overrides={
                        "LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH": str(artifact_path),
                    },
                    metadata={"runtime_matrix_case": "strict_real_codex_mathlib_recon_probe"},
                ),
            )
        )
    )
    assert view.override is not None

    real_step_timeout = float(os.environ.get("LEAN_CONSTELLATION_REAL_CODEX_STEP_TIMEOUT", "420"))
    started = unwrap(ws.admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=real_step_timeout)))
    assert started.status == "completed", started
    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    assert flow.result.outcome == "completed"
    assert "Init" in flow.result.index_update_summary
    assert "True.intro" in flow.result.useful_findings

    data = _read_artifact(artifact_path)
    assert data["prompt_marker_seen"] == prompt_marker
    assert data["developer_marker_seen"] == developer_marker
    assert data["artifact_home_root"] == str(home_root)
    assert any(str(key).startswith("mathlib-") for key in data["skill_keys_seen"])
    tools_called = set(data["application_tools_called"])
    assert {
        "get_current_node_mathlib_hints",
        "record_mathlib_module",
        "record_mathlib_decl",
        "add_mathlib_module_important_decl",
        "search_mathlib_index",
        "get_mathlib_module_entry",
        "get_mathlib_decl_entry",
        "add_current_mathlib_hints",
        "validate_current_node_mathlib_hints",
    }.issubset(tools_called)
    # The step submission is the source of truth for submit-tool execution. The
    # artifact is written before the final submit call in some Codex runs.
    assert data["submit_tool_called"] in {"submit_mathlib_recon_completed", False}
    tool_results = data.get("tool_results")
    assert isinstance(tool_results, dict), data
    for tool_name in (
        "record_mathlib_module",
        "record_mathlib_decl",
        "add_mathlib_module_important_decl",
        "search_mathlib_index",
        "get_mathlib_module_entry",
        "get_mathlib_decl_entry",
        "add_current_mathlib_hints",
        "validate_current_node_mathlib_hints",
    ):
        assert tool_results[tool_name]["ok"] is True, (tool_name, tool_results[tool_name])
    assert tool_results["validate_current_node_mathlib_hints"]["passed"] is True
    step = ws.runtime.ark.flow_service.get_step(step_id)
    assert step.submission is not None
    assert step.submission.tool_name == "submit_mathlib_recon_completed"

    module_entry = unwrap(ws.runtime.mathlib.get_mathlib_module_entry(ws.provider_repo, module="Init"))
    assert "True.intro" in module_entry.important_decl_names
    decl_entry = unwrap(ws.runtime.mathlib.get_mathlib_decl_entry(ws.provider_repo, name="True.intro"))
    assert decl_entry.module == "Init"
    hints = unwrap(ws.runtime.mathlib.get_node_mathlib_hint_view(ws.provider_repo, node_path="Main.Topic.Core"))
    assert [entry.module for entry in hints.modules] == ["Init"]
    assert [entry.name for entry in hints.declarations] == ["True.intro"]

    evidence_recorder.record_runtime_state(ws.runtime)
    for tool_name in sorted(tools_called):
        evidence_recorder.record_tool_call(
            tool_name=tool_name,
            view_key="mathlib_recon",
            view_kind="application",
            agent_type=agent_type,
            step_id=step_id,
            ok=True,
            assertion_summary="Called by real Codex controlled MathlibRecon probe.",
        )
    evidence_recorder.record_tool_call(
        tool_name="submit_mathlib_recon_completed",
        view_key="mathlib_recon_submit",
        view_kind="submit",
        agent_type=agent_type,
        step_id=step_id,
        ok=True,
        assertion_summary="Accepted from real Codex controlled MathlibRecon probe.",
    )
    _record_real_codex_artifact(
        evidence_recorder,
        ws=ws,
        agent_type=agent_type,
        step_id=step_id,
        artifact_path=artifact_path,
        started=started,
        data=data,
        prompt_marker_seen=data["prompt_marker_seen"] == prompt_marker,
        instruction_marker_seen=data["developer_marker_seen"] == developer_marker,
        skill_markers_seen=list(data["skill_keys_seen"]),
        tools_called=[*sorted(tools_called), "submit_mathlib_recon_completed"],
    )


def _coordinator_resource_probe_prompt(prompt_marker: str) -> str:
    return f"""
Runtime Matrix strict real Codex resource probe.

Prompt marker: {prompt_marker}

You are inside a controlled Coordinator AgentStep. This is a scheduling/resource wiring test, not an autonomous planning task.

Do these exact actions:
1. Read the developer instructions and find the first token that starts with RTCODEX_DEV_MARKER_COORDINATOR_STRICT_.
2. Inspect the real Codex home on disk. HOME points at the agent home root. Read "$HOME/.agents/lean_constellation_home.json", then read the actual "$HOME/.agents/skills/coordinator-content-result-closeout/SKILL.md". Report that exact skill key. Set private_consumer_guidance_seen=true only if the Skill makes private consumer inspection mandatory for boundary-external blocked work and names inspect_node_decl. Set dependent_ready_guidance_seen=true only if the Skill requires comparing an actual bound public declaration with the original private consumer before treating another node dependency as satisfied.
3. Call the application MCP tools "inspect_workspace_for_coordinator" and "get_node_tree".
4. Write JSON to the path in LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH with exactly these keys:
   prompt_marker_seen, developer_marker_seen, artifact_home_root, skill_keys_seen, private_consumer_guidance_seen, dependent_ready_guidance_seen, application_tools_called, submit_tool_called.
   Use the exact prompt marker string above for prompt_marker_seen. Use the exact developer marker from developer instructions for developer_marker_seen. Use HOME for artifact_home_root. Use arrays for skill_keys_seen and application_tools_called.
5. Call submit tool "submit_repo_ready" with summary "Strict real Codex Coordinator resource probe marks repo ready."

Keep the final response short and mention the artifact path.
""".strip()


def _statement_formal_probe_prompt(prompt_marker: str, decl_name: str) -> str:
    return f"""
Runtime Matrix strict real Codex statement formal worker probe.

Prompt marker: {prompt_marker}
Declaration: {decl_name}

You are inside a controlled StatementFormalWorker AgentStep. This is a scheduling/resource wiring test, not an autonomous proof task.

Do these exact actions:
1. Read the developer instructions and find the first token that starts with RTCODEX_DEV_MARKER_STATEMENT_FORMAL_STRICT_.
2. Inspect the real Codex home on disk. HOME points at the agent home root. Read "$HOME/.agents/lean_constellation_home.json" and inspect "$HOME/.agents/skills". Do not guess skill names; report the actual skill key "lean-statement-formalization" only if it exists on disk.
3. Call application MCP tool "prepare_statement_formal_file" with decl_name "{decl_name}". Save the returned file path.
4. Call application MCP tool "scan_lean_sorry_axiom" on the returned file path relative to the repo root and confirm it reports the expected prepared "sorry".
5. Edit the prepared Lean file by replacing the first line containing exactly two spaces followed by "sorry" with two spaces followed by "trivial".
6. Call application MCP tool "scan_lean_sorry_axiom" again on the same relative file path and continue only if the second scan has sorry_count 0.
7. Call application MCP tool "capture_statement_formal_file" with decl_name "{decl_name}". Treat this as failed unless the returned top-level ok is true. This capture is the real Lean check for this controlled Codex test.
8. Call application MCP tool "check_formal_stage_consistency" with decl_name "{decl_name}" and stage "statement". Treat this as failed unless the returned top-level ok is true and value.passed is true.
9. Write JSON to the path in LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH with exactly these keys:
    prompt_marker_seen, developer_marker_seen, artifact_home_root, skill_keys_seen, application_tools_called, submit_tool_called, decl_name, lean_file_path, tool_results.
    Use the exact prompt marker string above for prompt_marker_seen. Use the exact developer marker from developer instructions for developer_marker_seen. Use HOME for artifact_home_root. Use arrays for skill_keys_seen and application_tools_called.
    tool_results must be an object with entries for capture_statement_formal_file and check_formal_stage_consistency. For capture_statement_formal_file include at least ok and summary. For check_formal_stage_consistency include at least ok, passed, and summary.
10. If capture_statement_formal_file ok is true and check_formal_stage_consistency passed is true, call submit tool "submit_stage_worker_completed" with summary "Strict real Codex StatementFormalWorker probe completed statement formalization.". If either one failed, call submit tool "submit_stage_worker_blocked" with summary explaining the failed tool instead.

Keep the final response short and mention the artifact path.
""".strip()


def _resource_curator_probe_prompt(prompt_marker: str, target: str) -> str:
    return f"""
Runtime Matrix strict real Codex resource curator probe.

Prompt marker: {prompt_marker}
Target kind: web
Target: {target}

You are inside a controlled ResourceCurator AgentStep. This is a scheduling/resource wiring test, not an autonomous curation task.

Do these exact actions:
1. Read the developer instructions and find the first token that starts with RTCODEX_DEV_MARKER_RESOURCE_CURATOR_STRICT_.
2. Inspect the real Codex home on disk. HOME points at the agent home root. Read "$HOME/.agents/lean_constellation_home.json" and inspect "$HOME/.agents/skills". Do not guess skill names; report the actual skill key "resource-draft-curation" only if it exists on disk.
3. Call application MCP tool "normalize_resource_target" with target "{target}".
4. Read the active draft id from environment variable LEAN_CONSTELLATION_RESOURCE_DRAFT_ID, then call application MCP tool "get_resource_draft" with that draft_id.
5. From the get_resource_draft result, read the returned draft_id and draft_root. Create these draft files under draft_root:
   - README.md with a short title and summary.
   - original/raw.txt with text explaining that this is a strict real Codex resource curator probe.
   - normalized/main.md with normalized text explaining that the target supports a tiny True theorem.
6. Call application MCP tool "check_resource_draft" with the draft_id and verify it passes.
7. Write JSON to the path in LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH with exactly these keys:
   prompt_marker_seen, developer_marker_seen, artifact_home_root, skill_keys_seen, application_tools_called, submit_tool_called, draft_id.
   Use the exact prompt marker string above for prompt_marker_seen. Use the exact developer marker from developer instructions for developer_marker_seen. Use HOME for artifact_home_root. Use arrays for skill_keys_seen and application_tools_called.
8. Call submit tool "submit_local_resource_created" with target_kind "web", target "{target}", the draft_id, and summary "Strict real Codex ResourceCurator probe created a local resource."

Keep the final response short and mention the artifact path.
""".strip()


def _content_plan_maturity_probe_prompt(prompt_marker: str) -> str:
    return f"""
Runtime Matrix strict real Codex ContentPlan maturity smoke.

Prompt marker: {prompt_marker}

You are inside a controlled ContentPlan AgentStep. This is a repo maturity/resource wiring test, not an autonomous planning task.

Do these exact actions:
1. Read the developer instructions and find the first token that starts with RTCODEX_DEV_MARKER_CONTENT_PLAN_MATURITY_SMOKE_.
2. Inspect the real Codex home on disk. HOME points at the agent home root. Read "$HOME/.agents/lean_constellation_home.json" and inspect "$HOME/.agents/skills". Do not guess skill names; report the actual skill key "content-plan-declared-interface-mode" only if it exists on disk.
3. Call application MCP tool "get_current_repo_work_config".
4. Call application MCP tool "check_current_content_node_completion".
5. Write JSON to the path in LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH with exactly these keys:
   prompt_marker_seen, developer_marker_seen, artifact_home_root, skill_keys_seen, application_tools_called, submit_tool_called, tool_results.
   Use the exact prompt marker string above for prompt_marker_seen. Use the exact developer marker from developer instructions for developer_marker_seen. Use HOME for artifact_home_root. Use arrays for skill_keys_seen and application_tools_called.
   tool_results must be an object with entries for get_current_repo_work_config and check_current_content_node_completion. For get_current_repo_work_config include target_proof_availability and work_mode. For check_current_content_node_completion include at least ok, ready_to_submit, target_proof_availability, and summary.
6. Call submit tool "submit_content_node_blocked" with reason "Strict real Codex ContentPlan maturity smoke stops after config and completion gate probe."

Keep the final response short and mention the artifact path.
""".strip()


def _proof_formal_probe_prompt(prompt_marker: str, decl_name: str) -> str:
    return f"""
Runtime Matrix strict real Codex proof formal worker probe.

Prompt marker: {prompt_marker}
Declaration: {decl_name}

You are inside a controlled ProofFormalWorker AgentStep. This is a scheduling/resource wiring test, not an autonomous proof search task.

Do these exact actions:
1. Read the developer instructions and find the first token that starts with RTCODEX_DEV_MARKER_PROOF_FORMAL_STRICT_.
2. Inspect the real Codex home on disk. HOME points at the agent home root. Read "$HOME/.agents/lean_constellation_home.json" and inspect "$HOME/.agents/skills". Do not guess skill names; report the actual skill key "lean-proof-formalization" only if it exists on disk.
3. Call application MCP tool "prepare_proof_formal_file" with decl_name "{decl_name}". Save the returned file path.
4. Call application MCP tool "check_proof_formal_policy" on the returned file path relative to the repo root. If it reports a sorry/admit/axiom problem, edit the Lean file by replacing the first line containing exactly two spaces followed by "sorry" with two spaces followed by "trivial".
5. Call application MCP tool "scan_lean_sorry_axiom" on the same relative file path and continue only if it reports sorry_count 0 and axiom_count 0.
6. Call application MCP tool "capture_proof_formal_file" with decl_name "{decl_name}". Treat this as failed unless the returned top-level ok is true.
7. Call application MCP tool "check_formal_stage_consistency" with decl_name "{decl_name}" and stage "proof". Treat this as failed unless the returned top-level ok is true and value.passed is true.
8. Write JSON to the path in LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH with exactly these keys:
    prompt_marker_seen, developer_marker_seen, artifact_home_root, skill_keys_seen, application_tools_called, submit_tool_called, decl_name, lean_file_path, tool_results.
    Use the exact prompt marker string above for prompt_marker_seen. Use the exact developer marker from developer instructions for developer_marker_seen. Use HOME for artifact_home_root. Use arrays for skill_keys_seen and application_tools_called.
    tool_results must be an object with entries for capture_proof_formal_file and check_formal_stage_consistency. For capture_proof_formal_file include at least ok and summary. For check_formal_stage_consistency include at least ok, passed, and summary.
9. If capture_proof_formal_file ok is true and check_formal_stage_consistency passed is true, set submit_tool_called in the JSON artifact to "submit_stage_worker_completed", then call submit tool "submit_stage_worker_completed" with summary "Strict real Codex ProofFormalWorker probe completed proof formalization.". If either one failed, set submit_tool_called in the JSON artifact to "submit_stage_worker_blocked", then call submit tool "submit_stage_worker_blocked" with summary explaining the failed tool instead.

Keep the final response short and mention the artifact path.
""".strip()


def _adapter_decl_catalog_probe_prompt(prompt_marker: str) -> str:
    return f"""
Runtime Matrix strict real Codex adapter declaration catalog probe.

Prompt marker: {prompt_marker}

You are inside a controlled AdapterDeclCatalog AgentStep. This is a scheduling/resource wiring test, not an autonomous adapter design task.

Do these exact actions:
1. Read the developer instructions and find the first token that starts with RTCODEX_DEV_MARKER_ADAPTER_DECL_CATALOG_STRICT_.
2. Inspect the real Codex home on disk. HOME points at the agent home root. Read "$HOME/.agents/lean_constellation_home.json" and inspect "$HOME/.agents/skills". Do not guess skill names; report the actual skill directory names you see, even if the list is empty.
3. Call application MCP tools "inspect_adapter_input", "list_preparation_requirements", "list_root_interfaces", "get_adapter_upstream_metadata", and "get_adapter_upstream_status".
4. Call application MCP tool "create_adapter_decl" with name "main_result", kind "theorem", module "Upstream", lean_decl_name "upstreamSmoke", and summary "Expose the upstream smoke theorem."
5. Call application MCP tool "set_adapter_statement_nl" for "main_result" with text exactly "The upstream smoke theorem states True.\\n\\nRuntime Matrix real Codex adapter probe."
6. Call application MCP tool "set_adapter_statement_formal" for "main_result" with code exactly "theorem upstreamSmoke : True := by\\n  trivial".
7. Call application MCP tool "find_adapter_decl_by_upstream" with module "Upstream", lean_decl_name "upstreamSmoke", and adapter_name_query null.
8. Call application MCP tool "set_adapter_proof_nl" for "main_result" with text exactly "Use triviality.\\n\\nThe upstream theorem is already proved by triviality."
9. Call application MCP tool "set_adapter_proof_formal" for "main_result" with code exactly "theorem upstreamSmoke : True := by\\n  trivial".
10. Call application MCP tool "finalize_adapter_decl" with name "main_result".
11. Call application MCP tool "bind_adapter_interface" with interface_name "main_result", decl_name "main_result", and binding_summary "Runtime Matrix real Codex binds required interface to finalized adapter declaration."
12. Call application MCP tool "check_adapter_catalog_ready_preflight".
13. Write JSON to the path in LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH with exactly these keys:
    prompt_marker_seen, developer_marker_seen, artifact_home_root, skill_keys_seen, application_tools_called, submit_tool_called, decl_name.
    Use the exact prompt marker string above for prompt_marker_seen. Use the exact developer marker from developer instructions for developer_marker_seen. Use HOME for artifact_home_root. Use arrays for skill_keys_seen and application_tools_called. Use "main_result" for decl_name.
14. Call submit tool "submit_adapter_catalog_ready" with summary "Strict real Codex AdapterDeclCatalog probe completed adapter catalog."

Keep the final response short and mention the artifact path.
""".strip()


def _mathlib_recon_probe_prompt(prompt_marker: str) -> str:
    return f"""
Runtime Matrix strict real Codex Mathlib recon probe.

Prompt marker: {prompt_marker}
Fixture module: Init
Fixture declaration: True.intro

You are inside a controlled MathlibRecon AgentStep. This is a scheduling/resource wiring test, not an autonomous Mathlib search task.

Do these exact actions:
1. Read the developer instructions and find the first token that starts with RTCODEX_DEV_MARKER_MATHLIB_RECON_STRICT_.
2. Inspect the real Codex home on disk. HOME points at the agent home root. Read "$HOME/.agents/lean_constellation_home.json" and inspect "$HOME/.agents/skills". Do not guess skill names; report the actual skill keys whose directory names start with "mathlib-".
3. Call application MCP tool "get_current_node_mathlib_hints".
4. Call application MCP tool "record_mathlib_module" with module_name "Init", summary "Runtime Matrix built-in Lean fixture module used by MathlibRecon probe.", and source "Strict real Codex local Lean fixture."
5. Call application MCP tool "record_mathlib_decl" with decl_name "True.intro", module_name "Init", summary "Runtime Matrix built-in theorem constructor.", source "Strict real Codex local Lean fixture.", kind "theorem", signature "True.intro : True", and snippet "example : True := True.intro".
6. Call application MCP tool "add_mathlib_module_important_decl" with module "Init" and decl_name "True.intro".
7. Call application MCP tool "search_mathlib_index" with query "True" and limit 5.
8. Call application MCP tool "get_mathlib_module_entry" with module "Init".
9. Call application MCP tool "get_mathlib_decl_entry" with name "True.intro".
10. Call application MCP tool "add_current_mathlib_hints" once with modules [{{"name":"Init","reason":"Strict real Codex MathlibRecon probe module hint."}}] and declarations [{{"name":"True.intro","reason":"Strict real Codex MathlibRecon probe declaration hint."}}].
11. Call application MCP tool "validate_current_node_mathlib_hints". Treat this as failed unless the returned top-level ok is true and value.passed is true.
12. Write JSON to the path in LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH with exactly these keys:
    prompt_marker_seen, developer_marker_seen, artifact_home_root, skill_keys_seen, application_tools_called, submit_tool_called, tool_results.
    Use the exact prompt marker string above for prompt_marker_seen. Use the exact developer marker from developer instructions for developer_marker_seen. Use HOME for artifact_home_root. Use arrays for skill_keys_seen and application_tools_called.
    tool_results must include every application tool listed above with at least ok and summary. For validate_current_node_mathlib_hints also include passed.
13. If every application tool listed above has ok true and validate_current_node_mathlib_hints passed is true, set submit_tool_called in the JSON artifact to "submit_mathlib_recon_completed", then call submit tool "submit_mathlib_recon_completed" with summary "Strict real Codex MathlibRecon probe completed local index and hint wiring.", index_update_summary "Recorded Init and True.intro in the local Mathlib index.", node_mathlib_hint_summary "Recorded Init and True.intro as current-node Mathlib hints.", useful_findings ["Init", "True.intro"], and unresolved_in_mathlib []. If any application tool fails, do not call submit_mathlib_recon_completed; instead write the artifact with submit_tool_called false and the failed tool result, then stop.

Keep the final response short and mention the artifact path.
""".strip()


def _read_artifact(path: Path) -> dict[str, object]:
    assert path.exists(), f"real Codex artifact was not written: {path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    required = {
        "prompt_marker_seen",
        "developer_marker_seen",
        "artifact_home_root",
        "skill_keys_seen",
        "application_tools_called",
        "submit_tool_called",
    }
    assert required.issubset(data), data
    assert isinstance(data["skill_keys_seen"], list), data
    assert isinstance(data["application_tools_called"], list), data
    return data


def _record_real_codex_artifact(
    evidence_recorder: EvidenceRecorder,
    *,
    ws: RuntimeMatrixWorkspace,
    agent_type: str,
    step_id: str,
    artifact_path: Path,
    started: object,
    data: dict[str, object],
    prompt_marker_seen: bool,
    instruction_marker_seen: bool,
    skill_markers_seen: list[str],
    tools_called: list[str],
) -> None:
    transport_info = _mcp_transport_info_from_artifact(data)
    transcript_path = _write_real_codex_transcript(
        ws=ws,
        agent_type=agent_type,
        step_id=step_id,
        artifact_path=artifact_path,
        started=started,
        data=data,
        transport_info=transport_info,
    )
    evidence_recorder.record_codex_artifact(
        agent_type=agent_type,
        step_id=step_id,
        artifact_path=artifact_path,
        transcript_path=transcript_path,
        prompt_marker_seen=prompt_marker_seen,
        instruction_marker_seen=instruction_marker_seen,
        skill_markers_seen=skill_markers_seen,
        tools_called=tools_called,
        mcp_transport=transport_info["mcp_transport"],
        mcp_server_urls=transport_info["mcp_server_urls"],
    )


def _write_real_codex_transcript(
    *,
    ws: RuntimeMatrixWorkspace,
    agent_type: str,
    step_id: str,
    artifact_path: Path,
    started: object,
    data: dict[str, object],
    transport_info: dict[str, object],
) -> Path:
    step = ws.runtime.ark.flow_service.get_step(step_id)
    agent_id = _agent_id_for_step(step)
    agent_service = ws.runtime.ark.agent_service
    agent = agent_service.get_agent(agent_id) if agent_id is not None else None
    trace_report = None
    trace_report_error = None
    if agent_id is not None:
        try:
            trace_report = agent_service.build_trace_report(agent_id, artifact_path=artifact_path)
        except Exception as exc:  # noqa: BLE001 - transcript evidence should preserve lookup failures.
            trace_report_error = str(exc)
    trace_payload = _jsonable(trace_report) if trace_report is not None else {}
    turn_payloads = trace_payload.get("turns", []) if isinstance(trace_payload, dict) else []
    latest_turn_view = turn_payloads[-1] if turn_payloads else {}
    latest_turn_payload = latest_turn_view.get("result") if isinstance(latest_turn_view, dict) else None
    latest_turn_payload = latest_turn_payload if isinstance(latest_turn_payload, dict) else {}
    event_payloads = trace_payload.get("events", []) if isinstance(trace_payload, dict) else []
    latest_event = event_payloads[-1] if event_payloads else None
    final_response = latest_turn_payload.get("final_text")
    transcript = {
        "agent_type": agent_type,
        "step_id": step_id,
        "agent_id": agent_id,
        "artifact_path": str(artifact_path),
        "mcp_transport": transport_info["mcp_transport"],
        "mcp_server_urls": transport_info["mcp_server_urls"],
        "codex_artifact": data,
        "admin_start_result": _jsonable(started),
        "step": {
            "status": str(getattr(step, "status", "")),
            "step_type": step.step_type,
            "flow_id": step.flow_id,
            "scope_id": step.scope_id,
            "result": _jsonable(step.result),
            "submission": _jsonable(step.submission),
        },
        "agent": _jsonable(agent),
        "latest_turn": {
            "id": (latest_turn_view.get("locator") or {}).get("turn_id"),
            "status": latest_turn_payload.get("status"),
            "error": None,
            "started_at": latest_turn_payload.get("started_at"),
            "completed_at": latest_turn_payload.get("completed_at"),
            "duration_ms": latest_turn_payload.get("duration_ms"),
            "final_response": final_response,
            "usage": latest_turn_payload.get("usage"),
            "read_error": trace_report_error,
        },
        "provider_artifact": {
            "locator": _jsonable(getattr(agent, "artifact_locator", None)),
            "event_count": len(event_payloads),
            "last_event": latest_event,
        },
        "trace_report": trace_payload,
        "response_texts": [
            item.get("result", {}).get("final_text")
            for item in turn_payloads
            if isinstance(item, dict) and isinstance(item.get("result"), dict) and item["result"].get("final_text")
        ],
        "tool_calls": trace_payload.get("tool_calls", []) if isinstance(trace_payload, dict) else [],
        "usage": trace_payload.get("usage") if isinstance(trace_payload, dict) else None,
    }
    transcript_path = artifact_path.with_name(f"{artifact_path.stem}_transcript.json")
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(json.dumps(transcript, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert isinstance(final_response, str) and final_response.strip(), transcript
    assert transcript["provider_artifact"]["locator"]["session_id"], transcript
    assert transcript["provider_artifact"]["event_count"] > 0, transcript
    return transcript_path


def _mcp_transport_info_from_artifact(data: dict[str, object]) -> dict[str, object]:
    home_root = data.get("artifact_home_root")
    if not isinstance(home_root, str) or not home_root:
        return {"mcp_transport": None, "mcp_server_urls": []}
    manifest_path = Path(home_root) / ".agents" / "lean_constellation_home.json"
    if not manifest_path.exists():
        return {"mcp_transport": None, "mcp_server_urls": []}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    server_specs = manifest.get("mcp_server_specs", [])
    urls = [
        str(item["url"])
        for item in server_specs
        if isinstance(item, dict) and item.get("url")
    ]
    transport = manifest.get("mcp_transport")
    return {
        "mcp_transport": str(transport) if transport is not None else None,
        "mcp_server_urls": urls,
    }


def _agent_id_for_step(step: object) -> str | None:
    result = getattr(step, "result", None)
    agent_id = getattr(result, "agent_id", None)
    if agent_id:
        return str(agent_id)
    state = getattr(step, "state", None)
    role = getattr(state, "agent_role", None)
    bindings = getattr(step, "agent_bindings", None)
    if role is not None and hasattr(bindings, "get"):
        bound = bindings.get(role)
        if bound:
            return str(bound)
    return None


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return {
            str(key): _jsonable(item)
            for key, item in vars(value).items()
            if not key.startswith("_") and key not in {"thread"}
        }
    return str(value)


def _start_coordinator(ws: RuntimeMatrixWorkspace) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(ws.provider_repo),
                "start_mode": "admin_start",
                "start_reason": "Strict Runtime Matrix real Codex Coordinator resource test.",
            },
        )
    )


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
                "requested_by": "content_plan",
                "context_summary": "Strict Runtime Matrix real Codex ResourceCurator resource test.",
                "node_path": "Main.Core",
            },
        )
    )


def _start_content_task(ws: RuntimeMatrixWorkspace) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="content_node_task",
            scope_id="repo:Provider:node:Main.Topic.Core",
            params={
                "repo_key": "Provider",
                "repo_path": str(ws.provider_repo),
                "node_path": "Main.Topic.Core",
                "contract_version": 1,
                "task_mode": "run",
            },
        )
    )


def _start_adapter_preparation(ws: RuntimeMatrixWorkspace) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="adapter_repo_preparation",
            scope_id="repo:Adapter",
            params={
                "repo_key": "Adapter",
                "repo_root": str(ws.adapter_repo),
                "start_reason": "bootstrap",
                "admin_notes": "Strict Runtime Matrix real Codex AdapterDeclCatalog resource test.",
            },
        )
    )


def _start_mathlib_recon(ws: RuntimeMatrixWorkspace) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="mathlib_recon",
            scope_id="repo:Provider:node:Main.Topic.Core",
            params={
                "repo_key": "Provider",
                "repo_path": str(ws.provider_repo),
                "node_path": "Main.Topic.Core",
                "contract_version": 1,
                "objective": "Find small built-in Lean fixture dependencies for the Runtime Matrix theorem.",
                "context_summary": "Strict Runtime Matrix real Codex MathlibRecon resource test.",
            },
        )
    )


def _start_decl_round(ws: RuntimeMatrixWorkspace, round_fixture: DeclRoundFixture) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="decl_graph_round",
            scope_id=f"repo:Provider:node:{round_fixture.node_path}",
            params={
                "repo_key": "Provider",
                "repo_path": str(ws.provider_repo),
                "node_path": round_fixture.node_path,
                "contract_version": 1,
                "strategy_id": round_fixture.strategy_id,
                "round_id": round_fixture.round_id,
                "round_index": round_fixture.round_index,
                "summary": "Strict Runtime Matrix real Codex formal worker test.",
            },
        )
    )


def _finish_flow_with_created_steps(ws: RuntimeMatrixWorkspace, flow_id: str, *, timeout_s: float) -> None:
    for _ in range(10):
        flow = ws.runtime.ark.flow_service.get_flow(flow_id)
        if flow.status is FlowStatus.COMPLETED:
            return
        run_next_created_step(ws.admin, flow_id, timeout_s=timeout_s)
    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED, flow


def _complete_statement_nl_stage_for_real_codex(
    ws: RuntimeMatrixWorkspace,
    flow_id: str,
    round_fixture: DeclRoundFixture,
    evidence_recorder: EvidenceRecorder,
) -> None:
    run_next_created_step(ws.admin, flow_id, timeout_s=20)
    run_next_created_step(ws.admin, flow_id, timeout_s=20)
    run_next_created_step(ws.admin, flow_id, timeout_s=20)
    statement_nl_worker_id = run_until_step_created(ws.admin, flow_id, "decl_stage_worker_agent_step", max_advances=5)
    _assert_decl_stage_step(ws, statement_nl_worker_id, stage="statement_nl")
    set_scripted_provider_override(
        ws.admin,
        statement_nl_worker_id,
        agent_type="StatementNLWorkerControlledTestAgent",
        prompt_overlay="Strict Runtime Matrix: write statement NL and submit completed.",
        env_overrides={
            "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": "statement_nl_worker",
            "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "decl_stage_worker_submit",
        },
    )
    run_scripted_actions_with_evidence(
        ws.admin,
        statement_nl_worker_id,
        [
            (
                "application",
                "set_statement_nl",
                {
                    "decl_name": round_fixture.decl_name,
                    "text": "The strict real Codex proof formal theorem states True.",
                },
            ),
            (
                "submit",
                "submit_stage_worker_completed",
                {
                    "summary": "Statement NL completed before strict real Codex proof formal worker.",
                },
            ),
        ],
        recorder=evidence_recorder,
        timeout_s=20,
    )

    statement_nl_reviewer_id = run_until_step_created(ws.admin, flow_id, "decl_stage_reviewer_agent_step", max_advances=5)
    _assert_decl_stage_step(ws, statement_nl_reviewer_id, stage="statement_nl")
    _complete_review_stage(
        ws,
        statement_nl_reviewer_id,
        agent_type="StatementNLReviewerControlledTestAgent",
        app_view="statement_nl_reviewer",
        round_fixture=round_fixture,
        stage="statement_nl",
        summary="Statement NL accepted before strict real Codex proof formal worker.",
        evidence_recorder=evidence_recorder,
    )


def _complete_statement_formal_stage_for_real_codex(
    ws: RuntimeMatrixWorkspace,
    flow_id: str,
    round_fixture: DeclRoundFixture,
    evidence_recorder: EvidenceRecorder,
) -> None:
    run_next_created_step(ws.admin, flow_id, timeout_s=20)
    run_next_created_step(ws.admin, flow_id, timeout_s=20)
    statement_formal_worker_id = run_until_step_created(ws.admin, flow_id, "decl_stage_worker_agent_step", max_advances=5)
    _assert_decl_stage_step(ws, statement_formal_worker_id, stage="statement_formal")
    _complete_formal_stage_with_scripted_file_edit(
        ws,
        statement_formal_worker_id,
        agent_type="StatementFormalWorkerControlledTestAgent",
        app_view="statement_formal_worker",
        prepare_tool="prepare_statement_formal_file",
        capture_tool="capture_statement_formal_file",
        consistency_stage="statement",
        decl_name=round_fixture.decl_name,
        summary="Statement formal completed before strict real Codex proof formal worker.",
        evidence_recorder=evidence_recorder,
    )

    statement_formal_reviewer_id = run_until_step_created(ws.admin, flow_id, "decl_stage_reviewer_agent_step", max_advances=5)
    _assert_decl_stage_step(ws, statement_formal_reviewer_id, stage="statement_formal")
    _complete_review_stage(
        ws,
        statement_formal_reviewer_id,
        agent_type="StatementFormalReviewerControlledTestAgent",
        app_view="statement_formal_reviewer",
        round_fixture=round_fixture,
        stage="statement_formal",
        summary="Statement formal accepted before strict real Codex proof formal worker.",
        evidence_recorder=evidence_recorder,
    )


def _complete_proof_nl_stage_for_real_codex(
    ws: RuntimeMatrixWorkspace,
    flow_id: str,
    round_fixture: DeclRoundFixture,
    evidence_recorder: EvidenceRecorder,
) -> None:
    run_next_created_step(ws.admin, flow_id, timeout_s=20)
    run_next_created_step(ws.admin, flow_id, timeout_s=20)
    proof_nl_worker_id = run_until_step_created(ws.admin, flow_id, "decl_stage_worker_agent_step", max_advances=5)
    _assert_decl_stage_step(ws, proof_nl_worker_id, stage="proof_nl")
    set_scripted_provider_override(
        ws.admin,
        proof_nl_worker_id,
        agent_type="ProofNLWorkerControlledTestAgent",
        prompt_overlay="Strict Runtime Matrix: write proof NL and submit completed.",
        env_overrides={
            "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": "proof_nl_worker",
            "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "decl_stage_worker_submit",
        },
    )
    run_scripted_actions_with_evidence(
        ws.admin,
        proof_nl_worker_id,
        [
            (
                "application",
                "set_proof_nl",
                {
                    "decl_name": round_fixture.decl_name,
                    "text": "Use triviality.",
                },
            ),
            (
                "submit",
                "submit_stage_worker_completed",
                {
                    "summary": "Proof NL completed before strict real Codex proof formal worker.",
                },
            ),
        ],
        recorder=evidence_recorder,
        timeout_s=20,
    )

    proof_nl_reviewer_id = run_until_step_created(ws.admin, flow_id, "decl_stage_reviewer_agent_step", max_advances=5)
    _assert_decl_stage_step(ws, proof_nl_reviewer_id, stage="proof_nl")
    _complete_review_stage(
        ws,
        proof_nl_reviewer_id,
        agent_type="ProofNLReviewerControlledTestAgent",
        app_view="proof_nl_reviewer",
        round_fixture=round_fixture,
        stage="proof_nl",
        summary="Proof NL accepted before strict real Codex proof formal worker.",
        evidence_recorder=evidence_recorder,
    )


def _complete_review_stage(
    ws: RuntimeMatrixWorkspace,
    step_id: str,
    *,
    agent_type: str,
    app_view: str,
    round_fixture: DeclRoundFixture,
    stage: str,
    summary: str,
    evidence_recorder: EvidenceRecorder,
) -> None:
    set_scripted_provider_override(
        ws.admin,
        step_id,
        agent_type=agent_type,
        prompt_overlay=f"Strict Runtime Matrix: approve {stage} and submit review.",
        env_overrides={
            "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": app_view,
            "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "decl_stage_reviewer_submit",
        },
    )
    if stage == "statement_nl":
        review_action = (
            "application",
            "record_statement_nl_review_passed",
            {"decl_name": round_fixture.decl_name, "summary": summary},
        )
    elif stage == "statement_formal":
        review_action = (
            "application",
            "record_statement_formal_review_passed",
            {"decl_name": round_fixture.decl_name, "summary": summary},
        )
    elif stage == "proof_nl":
        review_action = (
            "application",
            "record_proof_nl_review_passed",
            {"decl_name": round_fixture.decl_name, "summary": summary},
        )
    elif stage == "proof_formal":
        review_action = (
            "application",
            "record_proof_formal_review_passed",
            {"decl_name": round_fixture.decl_name, "summary": summary},
        )
    else:
        raise AssertionError(f"unsupported decl review stage: {stage}")
    run_scripted_actions_with_evidence(
        ws.admin,
        step_id,
        [
            review_action,
            ("submit", "submit_stage_review", {"summary": summary}),
        ],
        recorder=evidence_recorder,
        timeout_s=20,
    )


def _complete_formal_stage_with_scripted_file_edit(
    ws: RuntimeMatrixWorkspace,
    step_id: str,
    *,
    agent_type: str,
    app_view: str,
    prepare_tool: str,
    capture_tool: str,
    consistency_stage: str,
    decl_name: str,
    summary: str,
    evidence_recorder: EvidenceRecorder,
) -> None:
    set_scripted_provider_override(
        ws.admin,
        step_id,
        agent_type=agent_type,
        prompt_overlay=f"Strict Runtime Matrix: complete {consistency_stage} formal file and submit.",
        env_overrides={
            "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": app_view,
            "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "decl_stage_worker_submit",
        },
    )
    run_scripted_actions_with_evidence(
        ws.admin,
        step_id,
        [
            ("application", prepare_tool, {"decl_name": decl_name}),
            (
                "file_replace_last_result",
                "replace_formal_placeholder",
                {"repo_root": str(ws.provider_repo), "old": "  sorry", "new": "  trivial"},
            ),
            ("application", capture_tool, {"decl_name": decl_name}),
            (
                "application",
                "check_formal_stage_consistency",
                {"decl_name": decl_name, "stage": consistency_stage},
            ),
            ("submit", "submit_stage_worker_completed", {"summary": summary}),
        ],
        recorder=evidence_recorder,
        timeout_s=20,
    )


def _field(value: object, *path: str):
    current = value
    for key in path:
        if isinstance(current, dict):
            current = current[key]
        else:
            current = getattr(current, key)
    return current


def _assert_decl_stage_step(ws: RuntimeMatrixWorkspace, step_id: str, *, stage: str) -> None:
    step = ws.runtime.ark.flow_service.get_step(step_id)
    assert step.step_type in {"decl_stage_worker_agent_step", "decl_stage_reviewer_agent_step"}
    assert step.state.variables["stage"] == stage


def _require_lake_and_lean() -> None:
    for command in ("lake", "lean"):
        if shutil.which(command) is None:
            pytest.skip(f"`{command}` is required for strict real Codex formal worker tests.")
