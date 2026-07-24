"""Derived, bounded context briefs for ContentNodeTask business agents."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from agent_runtime_kit.flow.models import FlowStatus
from pydantic import Field

from lean_constellation.domain.common import StrictModel, utc_now_iso


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
_MAX_ITEMS = 8
_MAX_TEXT = 500


class PreparationFindingView(StrictModel):
    kind: PreparationKind
    objective: str | None = None
    summary: str | None = None
    findings: list[str] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)
    source_flow_id: str


class PreparationContextBrief(StrictModel):
    available_kinds: list[PreparationKind] = Field(default_factory=list)
    missing_kinds: list[PreparationKind] = Field(default_factory=list)
    findings: list[PreparationFindingView] = Field(default_factory=list)
    generated_at: str = Field(default_factory=utc_now_iso)
    digest: str

    def render(self) -> str:
        lines = [
            f"Preparation brief digest: {self.digest}",
            f"Available kinds: {', '.join(self.available_kinds) or 'none'}.",
            f"Not run: {', '.join(self.missing_kinds) or 'none'}.",
        ]
        for item in self.findings:
            lines.append(
                f"- {item.kind} ({item.source_flow_id}): "
                f"objective={item.objective or '(not provided)'}; "
                f"summary={item.summary or '(not provided)'}"
            )
            if item.findings:
                lines.append(f"  verified findings: {'; '.join(item.findings)}")
            if item.unresolved_items:
                lines.append(f"  unresolved: {'; '.join(item.unresolved_items)}")
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
    repo_key: str
    node_path: str
    contract_version: int | None = None
    task_mode: str
    contract_summary: str | None = None
    preparation: PreparationContextBrief
    active_strategy_round: StrategyRoundContextBrief | None = None
    latest_child_delta: str | None = None
    used_preparation_kinds: list[PreparationKind] = Field(default_factory=list)
    decl_round_count: int = 0
    generated_at: str = Field(default_factory=utc_now_iso)
    digest: str

    def render(self) -> str:
        lines = [
            f"ContentPlan context brief digest: {self.digest}",
            (
                f"Identity: repo={self.repo_key}; node={self.node_path}; "
                f"contract_version={self.contract_version}; task_mode={self.task_mode}."
            ),
            f"Current contract: {self.contract_summary or '(unavailable)' }",
            (
                "Task progress: used preparations="
                f"{', '.join(self.used_preparation_kinds) or 'none'}; rounds={self.decl_round_count}."
            ),
            self.preparation.render(),
        ]
        if self.active_strategy_round is not None:
            lines.append(self.active_strategy_round.render())
        if self.latest_child_delta:
            lines.append(f"Latest child delta: {self.latest_child_delta}")
        lines.append(
            "Use this brief to avoid broad rediscovery. Read exact current truth before planning or "
            "performing a mutation, and resolve explicitly marked unresolved items."
        )
        return "\n".join(lines)


def build_preparation_context_brief(
    ctx,
    *,
    content_flow_id: str | None,
    exclude_flow_id: str | None = None,
) -> PreparationContextBrief:
    findings: list[PreparationFindingView] = []
    flow_service = getattr(ctx.ark, "flow_service", None)
    if flow_service is not None and content_flow_id:
        children = [
            flow
            for flow in flow_service.list_flows(scope_id=ctx.flow.scope_id)
            if flow.parent_flow_id == content_flow_id
            and flow.flow_id != exclude_flow_id
            and flow.flow_type in _PREPARATION_FLOW_KINDS
            and flow.status in {FlowStatus.COMPLETED, FlowStatus.FAILED}
        ]
        children.sort(key=lambda item: (item.created_at, item.flow_id))
        for child in children:
            kind = _PREPARATION_FLOW_KINDS[child.flow_type]
            input_fields = _agent_fields(child.input)
            result_fields = _agent_fields(child.result)
            findings.append(
                PreparationFindingView(
                    kind=kind,
                    objective=_text(input_fields.get("objective")),
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
                    source_flow_id=child.flow_id,
                )
            )
    available = list(dict.fromkeys(item.kind for item in findings))
    payload = {
        "available_kinds": available,
        "findings": [item.model_dump(mode="json") for item in findings],
    }
    return PreparationContextBrief(
        available_kinds=available,
        missing_kinds=[kind for kind in _ALL_PREPARATION_KINDS if kind not in available],
        findings=findings,
        digest=_digest(payload),
    )


def build_content_plan_context_brief(ctx, flow, input_model, state) -> ContentPlanContextBrief:
    preparation = build_preparation_context_brief(ctx, content_flow_id=flow.flow_id)
    contract_summary = None
    repo_root = Path(input_model.repo_path) if input_model.repo_path else None
    node_service = getattr(ctx.app, "node", None)
    if repo_root is not None and node_service is not None:
        contract = node_service.get_current_contract_view(repo_root, node_path=input_model.node_path)
        if contract.ok and contract.value is not None:
            contract_summary = _text(contract.value.summary)
    active = _latest_strategy_round_brief(ctx, flow.flow_id, repo_root, input_model.node_path)
    payload = {
        "repo_key": input_model.repo_key,
        "node_path": input_model.node_path,
        "contract_version": input_model.contract_version,
        "task_mode": input_model.task_mode,
        "contract_summary": contract_summary,
        "preparation_digest": preparation.digest,
        "active_strategy_round": active.model_dump(mode="json") if active else None,
        "latest_child_delta": state.latest_callback_summary,
        "used_preparation_kinds": list(state.used_preparation_kinds),
        "decl_round_count": state.decl_round_count,
    }
    return ContentPlanContextBrief(
        repo_key=input_model.repo_key,
        node_path=input_model.node_path,
        contract_version=input_model.contract_version,
        task_mode=input_model.task_mode,
        contract_summary=contract_summary,
        preparation=preparation,
        active_strategy_round=active,
        latest_child_delta=state.latest_callback_summary,
        used_preparation_kinds=list(state.used_preparation_kinds),
        decl_round_count=state.decl_round_count,
        digest=_digest(payload),
    )


def build_prior_preparation_prompt_context(ctx, flow) -> str:
    brief = build_preparation_context_brief(
        ctx,
        content_flow_id=flow.parent_flow_id,
        exclude_flow_id=flow.flow_id,
    )
    return brief.render()


def _latest_strategy_round_brief(
    ctx,
    content_flow_id: str,
    repo_root: Path | None,
    node_path: str,
) -> StrategyRoundContextBrief | None:
    flow_service = getattr(ctx.ark, "flow_service", None)
    if flow_service is None:
        return None
    rounds = [
        flow
        for flow in flow_service.list_flows(scope_id=ctx.flow.scope_id)
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
    return [text for item in value[:_MAX_ITEMS] if (text := _text(item))]


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:_MAX_TEXT]


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "ContentPlanContextBrief",
    "PreparationContextBrief",
    "PreparationFindingView",
    "StrategyRoundContextBrief",
    "build_content_plan_context_brief",
    "build_preparation_context_brief",
    "build_prior_preparation_prompt_context",
]
