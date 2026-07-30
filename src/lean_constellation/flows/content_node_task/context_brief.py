"""Derived compact context for ContentNodeTask business agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from agent_runtime_kit.flow.models import FlowStatus
from pydantic import Field

from lean_constellation.domain.common import StrictModel


PreparationKind = Literal["node_dir_dependency", "mathlib", "resource"]

_PREPARATION_FLOW_KINDS: dict[str, PreparationKind] = {
    "node_dir_dependency_recon": "node_dir_dependency",
    "mathlib_recon": "mathlib",
    "resource_recon": "resource",
}
_ALL_PREPARATION_KINDS: tuple[PreparationKind, ...] = (
    "node_dir_dependency",
    "mathlib",
    "resource",
)
_MAX_TEXT = 500


class PreparationFindingView(StrictModel):
    kind: PreparationKind
    attempt: int
    outcome: str
    summary: str | None = None
    findings: list[str] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)


class PreparationResultIndexItem(StrictModel):
    kind: PreparationKind
    attempt: int
    outcome: str
    summary: str | None = None
    unresolved_count: int = 0


class PreparationResultIndexView(StrictModel):
    results: list[PreparationResultIndexItem] = Field(default_factory=list)
    summary: str


class PreparationResultDetailView(StrictModel):
    kind: PreparationKind
    attempt: int
    outcome: str
    objective: str | None = None
    summary: str | None = None
    useful_findings: list[str] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)


class PriorPreparationReceiptView(StrictModel):
    kind: PreparationKind
    objective: str | None = None
    outcome: str
    mutation_summary: str | None = None
    verified_findings: list[str] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)


class PreparationContextBrief(StrictModel):
    available_kinds: list[PreparationKind] = Field(default_factory=list)
    missing_kinds: list[PreparationKind] = Field(default_factory=list)
    findings: list[PreparationFindingView] = Field(default_factory=list)

    def render_index(self) -> str:
        lines = [
            f"- Available: {', '.join(self.available_kinds) or 'none'}",
            f"- Not run: {', '.join(self.missing_kinds) or 'none'}",
        ]
        for item in self.findings:
            suffix = f" attempt {item.attempt}" if item.attempt > 1 else ""
            lines.append(f"- {item.kind}{suffix}: {item.outcome}; {item.summary or 'no summary'}")
            if item.unresolved_items:
                lines.append(f"  Unresolved items: {len(item.unresolved_items)}")
        return "\n".join(lines)


class StrategyRoundContextBrief(StrictModel):
    strategy_id: str
    strategy_objective: str | None = None
    strategy_rationale: str | None = None
    round_id: str
    round_index: int | None = None
    round_objective: str | None = None
    round_status: str | None = None

    def render(self) -> str:
        return "\n".join(
            [
                f"Strategy {self.strategy_id} objective: {self.strategy_objective or '(unavailable)'}",
                f"Strategy rationale: {self.strategy_rationale or '(not provided)'}",
                (
                    f"Round {self.round_id} (index={self.round_index or 'unknown'}, "
                    f"status={self.round_status or 'unknown'}) objective: "
                    f"{self.round_objective or '(unavailable)'}"
                ),
            ]
        )


class ContentPlanContextBrief(StrictModel):
    preparation: PreparationContextBrief
    active_strategy_round: StrategyRoundContextBrief | None = None
    decl_round_count: int = 0

    def render(self) -> str:
        lines = [
            "Current node state",
            "",
            f"- Preparations completed: {', '.join(self.preparation.available_kinds) or 'none'}",
            f"- Preparations not run: {', '.join(self.preparation.missing_kinds) or 'none'}",
            f"- Completed declaration rounds: {self.decl_round_count}",
        ]
        if self.active_strategy_round is not None:
            lines.extend(
                [
                    f"- Active declaration strategy: {self.active_strategy_round.strategy_objective or 'open'}",
                    (
                        "- Active declaration round: "
                        f"{self.active_strategy_round.round_status or 'open'}"
                    ),
                ]
            )
        else:
            lines.extend(
                [
                    "- Active declaration strategy: none",
                    "- Active declaration round: none",
                ]
            )
        lines.append(
            "- Next action: choose one declaration round, request a missing preparation, "
            "or finish the content task"
        )
        return "\n".join(lines)


def build_preparation_context_brief(
    ctx,
    *,
    content_flow_id: str | None,
    exclude_flow_id: str | None = None,
) -> PreparationContextBrief:
    findings: list[PreparationFindingView] = []
    attempts: dict[PreparationKind, int] = {}
    flow_service = getattr(ctx.ark, "flow_service", None)
    if flow_service is not None and content_flow_id:
        scope_id = getattr(ctx, "scope_id", None)
        if scope_id is None and getattr(ctx, "flow", None) is not None:
            scope_id = ctx.flow.scope_id
        if scope_id is None:
            content_flow = flow_service.get_flow(content_flow_id)
            scope_id = content_flow.scope_id
        children = [
            flow
            for flow in flow_service.list_flows(scope_id=scope_id)
            if flow.parent_flow_id == content_flow_id
            and flow.flow_id != exclude_flow_id
            and flow.flow_type in _PREPARATION_FLOW_KINDS
            and flow.status in {FlowStatus.COMPLETED, FlowStatus.FAILED}
        ]
        children.sort(key=lambda item: (item.created_at, item.flow_id))
        for child in children:
            kind = _PREPARATION_FLOW_KINDS[child.flow_type]
            result_fields = _agent_fields(child.result)
            attempts[kind] = attempts.get(kind, 0) + 1
            findings.append(
                PreparationFindingView(
                    kind=kind,
                    attempt=attempts[kind],
                    outcome=(
                        "failed"
                        if child.status is FlowStatus.FAILED
                        else _text(result_fields.get("outcome")) or "completed"
                    ),
                    summary=_first_text(
                        result_fields,
                        (
                            "dependency_change_summary",
                            "index_update_summary",
                            "material_change_summary",
                            "checked_boundary_summary",
                            "node_mathlib_hint_summary",
                            "checked_material_summary",
                            "reason",
                        ),
                    ),
                    findings=_bounded_text_list(result_fields.get("useful_findings")),
                    unresolved_items=_bounded_text_list(
                        result_fields.get("unresolved_within_visible_boundaries")
                        or result_fields.get("unresolved_in_mathlib")
                        or result_fields.get("unresolved_material_needs")
                        or result_fields.get("missing_targets")
                    ),
                )
            )
    available = list(dict.fromkeys(item.kind for item in findings))
    return PreparationContextBrief(
        available_kinds=available,
        missing_kinds=[kind for kind in _ALL_PREPARATION_KINDS if kind not in available],
        findings=findings,
    )


def list_preparation_results(
    ctx,
    *,
    content_flow_id: str,
    kind: PreparationKind | None = None,
) -> PreparationResultIndexView:
    brief = build_preparation_context_brief(ctx, content_flow_id=content_flow_id)
    results = [
        PreparationResultIndexItem(
            kind=item.kind,
            attempt=item.attempt,
            outcome=item.outcome,
            summary=item.summary,
            unresolved_count=len(item.unresolved_items),
        )
        for item in brief.findings
        if kind is None or item.kind == kind
    ]
    return PreparationResultIndexView(
        results=results,
        summary=f"Loaded {len(results)} terminal content preparation results.",
    )


def get_preparation_result(
    ctx,
    *,
    content_flow_id: str,
    kind: PreparationKind,
    attempt: int | None = None,
) -> PreparationResultDetailView | None:
    flow_service = getattr(ctx.ark, "flow_service", None)
    if flow_service is None:
        return None
    scope_id = getattr(ctx, "scope_id", None)
    if scope_id is None and getattr(ctx, "flow", None) is not None:
        scope_id = ctx.flow.scope_id
    if scope_id is None:
        content_flow = flow_service.get_flow(content_flow_id)
        scope_id = content_flow.scope_id
    flow_type = next(
        flow_type
        for flow_type, preparation_kind in _PREPARATION_FLOW_KINDS.items()
        if preparation_kind == kind
    )
    children = [
        flow
        for flow in flow_service.list_flows(scope_id=scope_id)
        if flow.parent_flow_id == content_flow_id
        and flow.flow_type == flow_type
        and flow.status in {FlowStatus.COMPLETED, FlowStatus.FAILED}
    ]
    children.sort(key=lambda item: (item.created_at, item.flow_id))
    if not children:
        return None
    selected_index = len(children) - 1 if attempt is None else attempt - 1
    if selected_index < 0 or selected_index >= len(children):
        return None
    child = children[selected_index]
    input_fields = _agent_fields(child.input)
    result_fields = _agent_fields(child.result)
    unresolved = _bounded_text_list(
        result_fields.get("unresolved_within_visible_boundaries")
        or result_fields.get("unresolved_in_mathlib")
        or result_fields.get("unresolved_material_needs")
        or result_fields.get("missing_targets")
    )
    return PreparationResultDetailView(
        kind=kind,
        attempt=selected_index + 1,
        outcome=(
            "failed"
            if child.status is FlowStatus.FAILED
            else _text(result_fields.get("outcome")) or "completed"
        ),
        objective=_first_text(
            input_fields,
            ("objective", "recon_objective", "material_objective"),
        ),
        summary=_first_text(
            result_fields,
            (
                "dependency_change_summary",
                "index_update_summary",
                "material_change_summary",
                "checked_boundary_summary",
                "node_mathlib_hint_summary",
                "checked_material_summary",
                "reason",
            ),
        ),
        useful_findings=_bounded_text_list(result_fields.get("useful_findings")),
        unresolved_items=unresolved,
    )


def build_content_plan_context_brief(ctx, flow, input_model, state) -> ContentPlanContextBrief:
    preparation = build_preparation_context_brief(ctx, content_flow_id=flow.flow_id)
    repo_root = Path(input_model.repo_path) if input_model.repo_path else None
    active = _latest_strategy_round_brief(ctx, flow.flow_id, repo_root, input_model.node_path)
    return ContentPlanContextBrief(
        preparation=preparation,
        active_strategy_round=active,
        decl_round_count=state.decl_round_count,
    )


def build_prior_preparation_prompt_context(ctx, flow) -> str:
    brief = build_preparation_context_brief(
        ctx,
        content_flow_id=flow.parent_flow_id,
        exclude_flow_id=flow.flow_id,
    )
    historical = _prior_same_node_preparation_receipts(ctx, flow, limit=2)
    if not brief.findings and not historical:
        return "No verified prior preparation is available."
    lines = ["Verified preparation already available", ""]
    if brief.findings:
        lines.append("Current content task")
        lines.extend(
            (
                f"- {item.kind}: {item.summary or item.outcome}"
                + (
                    f" Unresolved items: {len(item.unresolved_items)}."
                    if item.unresolved_items
                    else ""
                )
            )
            for item in brief.findings
        )
    if historical:
        if brief.findings:
            lines.append("")
        lines.append("Recent same-node results for this preparation kind")
        for receipt in historical:
            lines.append(
                f"- Objective: {receipt.objective or '(not recorded)'}; "
                f"outcome: {receipt.outcome}."
            )
            if receipt.mutation_summary:
                lines.append(f"  Mutation summary: {receipt.mutation_summary}")
            if receipt.verified_findings:
                lines.append(
                    "  Verified findings: "
                    + "; ".join(receipt.verified_findings)
                )
            if receipt.unresolved_items:
                lines.append(
                    "  Unresolved items: "
                    + "; ".join(receipt.unresolved_items)
                )
    lines.extend(
        [
            "",
            "Read current node truth before searching. Independently verify only unresolved claims.",
        ]
    )
    return "\n".join(lines)


def _prior_same_node_preparation_receipts(
    ctx,
    flow,
    *,
    limit: int,
) -> list[PriorPreparationReceiptView]:
    flow_service = getattr(ctx.ark, "flow_service", None)
    if (
        flow_service is None
        or flow.flow_type not in _PREPARATION_FLOW_KINDS
        or not flow.parent_flow_id
    ):
        return []
    current_content = flow_service.get_flow(flow.parent_flow_id)
    current_input = _agent_fields(current_content.input)
    node_path = _text(current_input.get("node_path"))
    if not node_path:
        return []
    historical_content = [
        candidate
        for candidate in flow_service.list_flows(scope_id=flow.scope_id)
        if candidate.flow_type == "content_node_task"
        and candidate.flow_id != current_content.flow_id
        and _text(_agent_fields(candidate.input).get("node_path")) == node_path
        and (candidate.created_at, candidate.flow_id)
        < (current_content.created_at, current_content.flow_id)
    ]
    historical_content.sort(key=lambda item: (item.created_at, item.flow_id))
    content_ids = {candidate.flow_id for candidate in historical_content}
    matching = [
        candidate
        for candidate in flow_service.list_flows(scope_id=flow.scope_id)
        if candidate.parent_flow_id in content_ids
        and candidate.flow_type == flow.flow_type
        and candidate.status in {FlowStatus.COMPLETED, FlowStatus.FAILED}
    ]
    matching.sort(key=lambda item: (item.created_at, item.flow_id))
    return [_preparation_receipt(candidate) for candidate in matching[-limit:]]


def _preparation_receipt(flow) -> PriorPreparationReceiptView:
    kind = _PREPARATION_FLOW_KINDS[flow.flow_type]
    input_fields = _agent_fields(flow.input)
    result_fields = _agent_fields(flow.result)
    return PriorPreparationReceiptView(
        kind=kind,
        objective=_first_text(
            input_fields,
            ("objective", "recon_objective", "material_objective"),
        ),
        outcome=(
            "failed"
            if flow.status is FlowStatus.FAILED
            else _text(result_fields.get("outcome")) or "completed"
        ),
        mutation_summary=_first_text(
            result_fields,
            (
                "dependency_change_summary",
                "index_update_summary",
                "material_change_summary",
                "node_mathlib_hint_summary",
            ),
        ),
        verified_findings=_bounded_text_list(
            result_fields.get("useful_findings")
        ),
        unresolved_items=_bounded_text_list(
            result_fields.get("unresolved_within_visible_boundaries")
            or result_fields.get("unresolved_in_mathlib")
            or result_fields.get("unresolved_material_needs")
            or result_fields.get("missing_targets")
        ),
    )


def _latest_strategy_round_brief(
    ctx,
    content_flow_id: str,
    repo_root: Path | None,
    node_path: str,
) -> StrategyRoundContextBrief | None:
    flow_service = getattr(ctx.ark, "flow_service", None)
    if flow_service is None:
        return None
    scope_id = getattr(ctx, "scope_id", None)
    if scope_id is None:
        scope_id = ctx.flow.scope_id
    rounds = [
        flow
        for flow in flow_service.list_flows(scope_id=scope_id)
        if flow.parent_flow_id == content_flow_id and flow.flow_type == "decl_graph_round"
    ]
    if not rounds:
        return None
    rounds.sort(key=lambda item: (item.created_at, item.flow_id))
    latest = rounds[-1]
    input_fields = _agent_fields(latest.input)
    strategy_id = _text(input_fields.get("strategy_id"))
    round_id = _text(input_fields.get("round_id"))
    if strategy_id is None or round_id is None:
        return None
    return _strategy_round_brief(
        ctx.app,
        repo_root=repo_root,
        node_path=node_path,
        strategy_id=strategy_id,
        round_id=round_id,
        round_index=input_fields.get("round_index"),
    )


def _strategy_round_brief(
    app,
    *,
    repo_root: Path | None,
    node_path: str,
    strategy_id: str,
    round_id: str,
    round_index: int | None,
) -> StrategyRoundContextBrief:
    strategy_objective = None
    strategy_rationale = None
    round_objective = None
    round_status = None
    graph = getattr(app, "decl_graph", None)
    if repo_root is not None and graph is not None:
        strategy = graph.get_strategy(repo_root, node_path=node_path, strategy_id=strategy_id)
        if strategy.ok and strategy.value is not None:
            strategy_objective = strategy.value.objective
            strategy_rationale = strategy.value.rationale
        round_record = graph.get_round(repo_root, node_path=node_path, round_id=round_id)
        if round_record.ok and round_record.value is not None:
            round_objective = round_record.value.objective
            round_status = round_record.value.status.value
            round_index = round_record.value.round_index
    return StrategyRoundContextBrief(
        strategy_id=strategy_id,
        strategy_objective=strategy_objective,
        strategy_rationale=strategy_rationale,
        round_id=round_id,
        round_index=round_index,
        round_objective=round_objective,
        round_status=round_status,
    )


def _agent_fields(value: object | None) -> dict[str, Any]:
    if value is None:
        return {}
    renderer = getattr(value, "agent_fields", None)
    if callable(renderer):
        fields = renderer()
        return fields if isinstance(fields, dict) else {}
    dumper = getattr(value, "model_dump", None)
    if callable(dumper):
        fields = dumper(mode="json")
        return fields if isinstance(fields, dict) else {}
    return {}


def _first_text(fields: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _text(fields.get(key))
        if value:
            return value
    return None


def _bounded_text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _text(item))]


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:_MAX_TEXT]


__all__ = [
    "ContentPlanContextBrief",
    "PreparationContextBrief",
    "PreparationFindingView",
    "StrategyRoundContextBrief",
    "build_content_plan_context_brief",
    "build_preparation_context_brief",
    "build_prior_preparation_prompt_context",
]
