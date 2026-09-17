from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_runtime_kit.flow.models import BaseSubmission
from pydantic import ValidationError

from lean_constellation.services import LeanProviderOverrides, create_test_runtime_services
from lean_constellation.domain.repo_run import RepoRunWorkflowControls
from lean_constellation.flows.content_node_task.flows import ContentNodeTaskInput
from lean_constellation.services.decl_graph import DeclGraphRound, DeclState
from lean_constellation.services.foundation import WriteMode
from lean_constellation.services.tool_facade import RawToolCallContext, RuntimeToolContext, SubmitBehavior
from lean_constellation.tools import register_submit_tooling
from lean_constellation.tools.submit_args import SubmitCurrentDeclRoundArgs
from tests.unit.tools._submit_family_helpers import submit_specs
from tests.unit_services_helpers import initialize_native_test_repo


class _RecordingSubmissionGateway:
    def __init__(self) -> None:
        self.accepted: list[BaseSubmission] = []

    def accept_step_submission(self, ctx, submission: BaseSubmission):  # noqa: ANN001
        del ctx
        self.accepted.append(submission)
        return {"accepted": True}


def _prepare_round(
    tmp_path: Path,
    *,
    workflow_controls: RepoRunWorkflowControls | None = None,
):
    initialize_native_test_repo(tmp_path)
    gateway = _RecordingSubmissionGateway()
    runtime = create_test_runtime_services(providers=LeanProviderOverrides(submission_gateway=gateway))
    content_input = ContentNodeTaskInput(
        repo_key="Repo",
        repo_path=str(tmp_path),
        node_path="Main.Topic.Core",
        contract_version=1,
        workflow_controls=workflow_controls or RepoRunWorkflowControls(),
    )
    runtime.ark.flow_service = SimpleNamespace(
        get_flow=lambda flow_id: SimpleNamespace(
            flow_id=flow_id,
            flow_type="content_node_task",
            input=content_input,
        )
    )
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(
        tmp_path,
        path="Main.Topic",
        goal="Topic goal.",
        boundary="Topic boundary.",
    ).ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal.",
        boundary="Core boundary.",
        objective="Build core declarations.",
        success_criteria="Core declarations are ready.",
    ).ok
    assert register_submit_tooling(runtime).ok
    strategy = runtime.decl_graph.ensure_open_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        objective="Build the core theorem.",
    )
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Create the core theorem.",
    )
    assert round_record.ok and round_record.value is not None
    created = runtime.decl_graph.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        name="core_result",
        kind="theorem",
        objective="Create the core theorem.",
        summary="Core theorem.",
        target_state=DeclState.DECLARED,
    )
    assert created.ok
    raw = RawToolCallContext(
        endpoint_view_key="content_plan_submit",
        runtime_context=RuntimeToolContext(
            flow_id="flow_content",
            step_id="step_plan",
            agent_id="agent_plan",
            scope_id="repo:Repo:node:Main.Topic.Core",
            agent_type="ContentPlanAgent",
            agent_role="plan",
            expected_view_key="content_plan_submit",
            repo_root=tmp_path,
            node_path="Main.Topic.Core",
            node_kind="content",
            contract_version=1,
        ),
    )
    return runtime, gateway, raw, strategy.value, round_record.value


def test_content_plan_dispatch_submit_tools_registered() -> None:
    specs = submit_specs()
    assert specs["submit_content_preparation_recon"].submit_behavior == SubmitBehavior.DISPATCH_CHILD_FLOWS
    assert specs["submit_current_decl_round"].submit_behavior == SubmitBehavior.DISPATCH_CHILD_FLOWS
    assert specs["submit_resource_request"].submit_behavior == SubmitBehavior.DISPATCH_CHILD_FLOWS


def test_submit_current_decl_round_schema_rejects_agent_supplied_identity() -> None:
    parsed = SubmitCurrentDeclRoundArgs.model_validate({"summary": "Dispatch the current draft."})
    assert parsed.summary == "Dispatch the current draft."
    for stale_field, stale_value in (
        ("strategy_id", "strategy_forged"),
        ("round_id", "round_forged"),
        ("round_index", 99),
    ):
        with pytest.raises(ValidationError):
            SubmitCurrentDeclRoundArgs.model_validate(
                {"summary": "Dispatch the current draft.", stale_field: stale_value}
            )


def test_submit_current_decl_round_derives_exact_relation_from_current_draft(tmp_path: Path) -> None:
    runtime, gateway, raw, strategy, round_record = _prepare_round(tmp_path)

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_current_decl_round",
        flat_args={"summary": "Dispatch the current draft."},
    )

    assert result.ok and result.value is not None
    assert result.value.ok is True, result.value.issues
    assert len(gateway.accepted) == 1
    submission = gateway.accepted[0]
    assert submission.strategy_id == strategy.strategy_id
    assert submission.round_id == round_record.round_id
    assert submission.round_index == round_record.round_index
    assert len(submission.requests) == 1
    request = submission.requests[0]
    assert request.params["strategy_id"] == strategy.strategy_id
    assert request.params["round_id"] == round_record.round_id
    assert request.params["round_index"] == round_record.round_index


def test_content_plan_tools_preserve_controls_and_reject_disabled_recon(tmp_path: Path) -> None:
    controls = RepoRunWorkflowControls(
        content_mathlib_recon=False,
        statement_formal_review=False,
    )
    runtime, gateway, raw, _strategy, _round_record = _prepare_round(
        tmp_path,
        workflow_controls=controls,
    )

    disabled = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_content_preparation_recon",
        flat_args={
            "recon_kind": "mathlib",
            "objective": "Check one missing declaration.",
            "summary": "Run Mathlib recon.",
        },
    )
    dispatched = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_current_decl_round",
        flat_args={"summary": "Dispatch the current draft."},
    )

    assert disabled.ok and disabled.value is not None and disabled.value.ok is False
    assert disabled.value.issues[0].kind == "content_preparation_recon_disabled"
    assert dispatched.ok and dispatched.value is not None and dispatched.value.ok is True
    assert len(gateway.accepted) == 1
    assert gateway.accepted[0].requests[0].params["workflow_controls"] == controls.model_dump(mode="json")


def test_content_plan_workflow_controls_fail_closed_for_wrong_flow_type(tmp_path: Path) -> None:
    runtime, gateway, raw, _strategy, _round_record = _prepare_round(tmp_path)
    runtime.ark.flow_service = SimpleNamespace(
        get_flow=lambda flow_id: SimpleNamespace(
            flow_id=flow_id,
            flow_type="native_repo_coordinator",
            input=SimpleNamespace(),
        )
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_content_preparation_recon",
        flat_args={
            "recon_kind": "mathlib",
            "objective": "Check one declaration.",
            "summary": "Run Mathlib recon.",
        },
    )

    assert result.ok and result.value is not None and result.value.ok is False
    assert result.value.issues[0].kind == "content_task_workflow_controls_unavailable"
    assert gateway.accepted == []


def test_submit_current_decl_round_fails_closed_for_missing_or_ambiguous_draft(tmp_path: Path) -> None:
    runtime, gateway, raw, strategy, round_record = _prepare_round(tmp_path)
    first_path = runtime.decl_graph.graph_store.round_path(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.round_id,
    )
    assert runtime.foundation.store.delete_json(first_path).ok
    missing = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_current_decl_round",
        flat_args={"summary": "Dispatch the current draft."},
    )
    assert missing.ok and missing.value is not None
    assert missing.value.ok is False
    assert missing.value.issues[0].kind == "current_draft_round_missing"
    assert gateway.accepted == []

    for index in (1, 2):
        corrupt = DeclGraphRound(
            round_id=f"round_corrupt_{index}",
            node_path="Main.Topic.Core",
            strategy_id=strategy.strategy_id,
            round_index=index,
            objective=f"Corrupt draft {index}.",
        )
        assert runtime.foundation.store.write_json_atomic(
            runtime.decl_graph.graph_store.round_path(
                tmp_path,
                node_path="Main.Topic.Core",
                round_id=corrupt.round_id,
            ),
            corrupt,
            mode=WriteMode.CREATE_ONLY,
        ).ok
    ambiguous = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_current_decl_round",
        flat_args={"summary": "Dispatch the current draft."},
    )
    assert ambiguous.ok and ambiguous.value is not None
    assert ambiguous.value.ok is False
    assert ambiguous.value.issues[0].kind == "current_draft_round_ambiguous"
    assert gateway.accepted == []
