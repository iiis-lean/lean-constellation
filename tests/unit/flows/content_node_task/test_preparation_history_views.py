from __future__ import annotations

from types import SimpleNamespace

from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.flows.content_node_task.context_brief import (
    build_prior_preparation_prompt_context,
    get_preparation_result,
    list_preparation_results,
)


class _FlowService:
    def __init__(self, flows):
        self.flows = flows

    def list_flows(self, *, scope_id: str):
        assert scope_id == "repo:Demo:node:Main.Core"
        return list(self.flows)

    def get_flow(self, flow_id: str):
        return next(flow for flow in self.flows if flow.flow_id == flow_id)


def _fields(**values):
    return SimpleNamespace(agent_fields=lambda: values)


def _flow(
    *,
    flow_id: str,
    flow_type: str,
    created_at: str,
    input_fields: dict,
    result_fields: dict,
    parent_flow_id: str = "content_1",
):
    return SimpleNamespace(
        flow_id=flow_id,
        flow_type=flow_type,
        parent_flow_id=parent_flow_id,
        created_at=created_at,
        status=FlowStatus.COMPLETED,
        input=_fields(**input_fields),
        result=_fields(**result_fields),
    )


def test_preparation_history_uses_compact_index_and_selected_detail() -> None:
    flows = [
        _flow(
            flow_id="mathlib_1",
            flow_type="mathlib_recon",
            created_at="2026-01-01T00:00:00Z",
            input_fields={"objective": "Find finite-set declarations."},
            result_fields={
                "outcome": "completed",
                "node_mathlib_hint_summary": "Verified the first finite-set route.",
                "useful_findings": ["Finset.card_union_of_disjoint is available."],
                "unresolved_in_mathlib": ["A subset-choice lemma remains."],
            },
        ),
        _flow(
            flow_id="mathlib_2",
            flow_type="mathlib_recon",
            created_at="2026-01-02T00:00:00Z",
            input_fields={"objective": "Close the subset-choice gap."},
            result_fields={
                "outcome": "completed",
                "node_mathlib_hint_summary": "Verified the complete finite-set route.",
                "useful_findings": ["Finset.exists_subset_card_eq closes the gap."],
                "unresolved_in_mathlib": [],
            },
        ),
    ]
    ctx = SimpleNamespace(
        ark=SimpleNamespace(flow_service=_FlowService(flows)),
        scope_id="repo:Demo:node:Main.Core",
    )

    index = list_preparation_results(
        ctx,
        content_flow_id="content_1",
        kind="mathlib",
    )
    latest = get_preparation_result(
        ctx,
        content_flow_id="content_1",
        kind="mathlib",
    )
    first = get_preparation_result(
        ctx,
        content_flow_id="content_1",
        kind="mathlib",
        attempt=1,
    )

    assert [item.attempt for item in index.results] == [1, 2]
    assert index.results[0].unresolved_count == 1
    assert "flow_id" not in index.model_dump(mode="json")
    assert latest is not None
    assert latest.attempt == 2
    assert latest.objective == "Close the subset-choice gap."
    assert latest.useful_findings == [
        "Finset.exists_subset_card_eq closes the gap."
    ]
    assert first is not None
    assert first.unresolved_items == ["A subset-choice lemma remains."]
    assert "created_at" not in latest.model_dump(mode="json")


def test_prior_preparation_prompt_includes_only_two_recent_same_kind_receipts() -> None:
    content_flows = [
        SimpleNamespace(
            flow_id=f"content_{index}",
            flow_type="content_node_task",
            parent_flow_id="coordinator",
            created_at=f"2026-01-0{index}T00:00:00Z",
            status=FlowStatus.COMPLETED,
            input=_fields(node_path="Main.Core"),
            result=None,
        )
        for index in range(1, 5)
    ]
    historical_recon = [
        _flow(
            flow_id=f"mathlib_{index}",
            flow_type="mathlib_recon",
            created_at=f"2026-01-0{index}T01:00:00Z",
            parent_flow_id=f"content_{index}",
            input_fields={"objective": f"Mathlib objective {index}"},
            result_fields={
                "outcome": "completed",
                "index_update_summary": f"Recorded canonical entries {index}.",
                "useful_findings": [f"Verified declaration {index}."],
                "unresolved_in_mathlib": (
                    [f"Unresolved item {index}."] if index == 3 else []
                ),
                "tool_trace": "must not be copied",
            },
        )
        for index in range(1, 4)
    ]
    current_recon = SimpleNamespace(
        flow_id="mathlib_current",
        flow_type="mathlib_recon",
        parent_flow_id="content_4",
        scope_id="repo:Demo:node:Main.Core",
        created_at="2026-01-04T01:00:00Z",
    )
    service = _FlowService([*content_flows, *historical_recon, current_recon])
    ctx = SimpleNamespace(
        ark=SimpleNamespace(flow_service=service),
        scope_id="repo:Demo:node:Main.Core",
    )

    prompt = build_prior_preparation_prompt_context(ctx, current_recon)

    assert "Mathlib objective 2" in prompt
    assert "Mathlib objective 3" in prompt
    assert "Mathlib objective 1" not in prompt
    assert "Recorded canonical entries 3." in prompt
    assert "Verified declaration 3." in prompt
    assert "Unresolved item 3." in prompt
    assert "mathlib_3" not in prompt
    assert "tool_trace" not in prompt
