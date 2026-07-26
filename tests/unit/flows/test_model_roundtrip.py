from __future__ import annotations

import pytest
from agent_runtime_kit.flow.contexts import FlowBuildContext
from agent_runtime_kit.flow.models import FlowRequest, FlowStepTypeError, FlowStepValidationError
from agent_runtime_kit.flow.registry import FlowTypeRegistry, StepTypeRegistry
from agent_runtime_kit.flow.standard_steps import AgentStepState
from agent_runtime_kit.runtime import ARKServices, AppServices

from lean_constellation.flows.common.agent_steps import BUSINESS_AGENT_STEP_TYPES
from lean_constellation.flows.content_node_task.decl_round.flow import DeclGraphRoundResult
from lean_constellation.flows.content_node_task.flows import ContentNodeTaskResult
from lean_constellation.flows.content_node_task.preparation.mathlib_recon.flow import MathlibReconResult
from lean_constellation.flows.content_node_task.preparation.node_dir_recon.flow import NodeDirDependencyReconResult
from lean_constellation.flows.content_node_task.preparation.resource_recon.flow import ResourceReconResult
from lean_constellation.flows.coordinator.flows import NativeRepoCoordinatorResult
from lean_constellation.flows.registry import BUSINESS_FLOW_TYPES, register_lean_flow_step_types
from lean_constellation.flows.repo_lifecycle.flows import (
    AdapterRepoPreparationResult,
    NativeRepoPreparationResult,
    RequirementGroupRepoBootstrapResult,
)
from lean_constellation.flows.repo_lifecycle.continuation import NativeRepoContinuationResult
from lean_constellation.flows.resource_request.flows import ResourceCurationResult


FLOW_PARAMS = {
    "requirement_group_repo_bootstrap": {
        "target_repo": "Provider",
        "repo_root": "/workspace/Provider",
        "workspace_root": "/workspace",
        "requirement_refs": ["consumer:req"],
        "admin_notes": "unit",
    },
    "native_repo_preparation": {
        "repo_key": "Provider", "start_reason": "bootstrap",
        "run_spec": {"run_objective": "Prepare Provider.", "completion_mode": "graph_proved",
                     "source_scope": {"mode": "all"},
                     "index_policy": "auto", "root_interface_policy": "auto"},
    },
    "native_repo_continuation": {
        "repo_key": "Provider", "repo_root": "/workspace/Provider", "base_release_id": "release-r1",
        "run_spec": {"run_objective": "Continue Provider.", "completion_mode": "graph_proved",
                     "source_scope": {"mode": "none"},
                     "index_policy": "reuse", "root_interface_policy": "reuse"},
    },
    "adapter_repo_preparation": {"repo_key": "Adapter", "start_reason": "bootstrap"},
    "resource_curation": {
        "repo_key": "Repo",
        "target_kind": "arxiv",
        "target": "2501.12345",
        "arxiv_version": "v2",
        "requested_by": "content_plan",
        "context_summary": "Need source.",
        "node_path": "Main.Core",
    },
    "native_repo_coordinator": {"repo_key": "Repo", "start_mode": "admin_start", "start_reason": "unit"},
    "content_node_task": {
        "repo_key": "Repo",
        "node_path": "Main.Core",
        "contract_version": 1,
    },
    "node_dir_dependency_recon": {
        "repo_key": "Repo",
        "node_path": "Main.Core",
        "contract_version": 1,
        "objective": "Check sibling imports.",
    },
    "mathlib_recon": {
        "repo_key": "Repo",
        "node_path": "Main.Core",
        "contract_version": 1,
        "objective": "Find Mathlib lemmas.",
    },
    "resource_recon": {
        "repo_key": "Repo",
        "node_path": "Main.Core",
        "contract_version": 1,
        "objective": "Find source references.",
    },
    "decl_graph_round": {
        "repo_key": "Repo",
        "node_path": "Main.Core",
        "contract_version": 1,
        "strategy_id": "strategy_1",
        "round_id": "round_1",
        "round_index": 1,
        "summary": "Start round.",
    },
}


FLOW_RESULTS = {
    "requirement_group_repo_bootstrap": RequirementGroupRepoBootstrapResult(
        outcome="native_bootstrap_ready",
        repo_key="Provider",
        next_preparation_flow="native_repo_preparation",
        summary="bootstrap ready",
    ),
    "native_repo_preparation": NativeRepoPreparationResult(
        outcome="handoff_dispatched",
        repo_key="Provider",
        summary="handoff dispatched",
    ),
    "native_repo_continuation": NativeRepoContinuationResult(
        outcome="handoff_dispatched", repo_key="Provider", run_objective="Continue Provider.", summary="handoff"
    ),
    "adapter_repo_preparation": AdapterRepoPreparationResult(outcome="adapter_ready", repo_key="Adapter", summary="ready"),
    "resource_curation": ResourceCurationResult(
        outcome="local_resource_created",
        repo_key="Repo",
        resource_key="res_1",
        summary="resource ready",
    ),
    "native_repo_coordinator": NativeRepoCoordinatorResult(outcome="repo_ready", repo_key="Repo", summary="ready"),
    "content_node_task": ContentNodeTaskResult(outcome="ready", repo_key="Repo", node_path="Main.Core", summary="ready"),
    "node_dir_dependency_recon": NodeDirDependencyReconResult(
        outcome="completed",
        repo_key="Repo",
        node_path="Main.Core",
        dependency_change_summary="Added Main.Base.",
        checked_boundary_summary="Checked same-repo visible node boundaries.",
        useful_findings=["Main.Base"],
        unresolved_within_visible_boundaries=[],
        summary="deps updated",
    ),
    "mathlib_recon": MathlibReconResult(
        outcome="completed",
        repo_key="Repo",
        node_path="Main.Core",
        index_update_summary="Recorded Mathlib.Data.Nat.Basic.",
        node_mathlib_hint_summary="Added current-node Mathlib hints.",
        useful_findings=["Mathlib.Data.Nat.Basic"],
        unresolved_in_mathlib=[],
        summary="mathlib updated",
    ),
    "resource_recon": ResourceReconResult(
        outcome="completed",
        repo_key="Repo",
        node_path="Main.Core",
        material_change_summary="Attached res_1.",
        checked_material_summary="Checked material refs.",
        useful_findings=["res_1"],
        unresolved_material_needs=[],
        summary="resources updated",
    ),
    "decl_graph_round": DeclGraphRoundResult(
        outcome="completed",
        repo_key="Repo",
        node_path="Main.Core",
        round_id="round_1",
        completed_stages=["statement_nl"],
        summary="round completed",
    ),
}


def _flow_registry() -> FlowTypeRegistry:
    registry = FlowTypeRegistry()
    register_lean_flow_step_types(flow_registry=registry)
    return registry


def test_business_flow_models_roundtrip_through_registry() -> None:
    registry = _flow_registry()
    assert set(registry.list()) == {flow_cls.flow_type for flow_cls in BUSINESS_FLOW_TYPES}

    for flow_type, params in FLOW_PARAMS.items():
        request = FlowRequest(flow_type=flow_type, scope_id="scope", params=params)
        validated_params = registry.validate_request_params(request)
        ctx = FlowBuildContext(
            ark=ARKServices(),
            app=AppServices(),
            request=request,
            params=validated_params,
            flow_id=f"f_{flow_type}",
            scope_id="scope",
            parent_flow_id=None,
            parent_dispatch_step_id=None,
        )
        flow = registry.get(flow_type).build_from_request(ctx)
        dumped = flow.model_dump(mode="json")

        parsed_input = registry.parse_input(flow_type, dumped["input"])
        parsed_state = registry.parse_state(flow_type, dumped["state"])
        parsed_result = registry.parse_result(flow_type, FLOW_RESULTS[flow_type].model_dump(mode="json"))

        assert type(parsed_input) is type(flow.input)
        assert type(parsed_state) is type(flow.state)
        assert type(parsed_result) is type(FLOW_RESULTS[flow_type])


def test_business_agent_step_state_and_result_roundtrip_through_registry() -> None:
    registry = StepTypeRegistry()
    register_lean_flow_step_types(step_registry=registry)

    result_payload = {
        "result_type": "agent_step_submission",
        "outcome": "submitted",
        "summary": "submitted",
        "agent_id": "agent_1",
        "submission_id": "sub_1",
        "submission_type": "unit_submission",
        "attempts": 1,
    }

    for step_cls in BUSINESS_AGENT_STEP_TYPES:
        state_cls = getattr(step_cls, "State", AgentStepState)
        state_payload = state_cls(agent_role="unit_role").model_dump(mode="json")
        assert isinstance(registry.parse_state(step_cls.step_type, state_payload), AgentStepState)
        parsed_result = registry.parse_result(step_cls.step_type, result_payload)
        assert parsed_result is not None
        assert parsed_result.result_type == "agent_step_submission"


def test_unknown_flow_type_and_missing_discriminator_are_structured_errors() -> None:
    registry = _flow_registry()

    with pytest.raises(FlowStepTypeError):
        registry.get("missing_flow_type")

    with pytest.raises(FlowStepValidationError):
        registry.parse_result("resource_curation", {"summary": "missing result_type"})
