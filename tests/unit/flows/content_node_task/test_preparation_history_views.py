from __future__ import annotations

from types import SimpleNamespace

from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.flows.content_node_task.context_brief import (
    get_preparation_result,
    list_preparation_results,
)


class _FlowService:
    def __init__(self, flows):
        self.flows = flows

    def list_flows(self, *, scope_id: str):
        assert scope_id == "repo:Demo:node:Main.Core"
        return list(self.flows)


def _fields(**values):
    return SimpleNamespace(agent_fields=lambda: values)


def _flow(
    *,
    flow_id: str,
    flow_type: str,
    created_at: str,
    input_fields: dict,
    result_fields: dict,
):
    return SimpleNamespace(
        flow_id=flow_id,
        flow_type=flow_type,
        parent_flow_id="content_1",
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
