"""Tool registry, groups, and view compilation."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator, model_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import GateReport, MutationSummaryView, ServiceResult
from lean_constellation.services.tool_facade.context_resolver import ActorRole, ToolExecutionContext

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class ToolCapability(StrEnum):
    READ = "read"
    WRITE = "write"
    SUBMIT = "submit"
    ADMIN = "admin"


class SubmitBehavior(StrEnum):
    NONE = "none"
    TERMINAL = "terminal"
    DISPATCH_CHILD_FLOWS = "dispatch_child_flows"


ToolHandler = Callable[..., Any]


class ToolSpec(StrictModel):
    name: str
    description: str
    args_model: type[BaseModel]
    capability: ToolCapability
    backing_service: str
    backing_component: str | None = None
    backing_method: str
    result_view: str
    required_context: set[str] = Field(default_factory=set)
    tool_groups: set[str] = Field(default_factory=set)
    allowed_roles: set[ActorRole] = Field(default_factory=set)
    submit_behavior: SubmitBehavior = SubmitBehavior.NONE
    toolkit_proxy_name: str | None = None
    backing_handler: ToolHandler | None = Field(default=None, exclude=True)

    @field_validator("name", "description", "backing_service", "backing_method", "result_view")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must be non-empty")
        return value

    @model_validator(mode="after")
    def _validate_submit_naming(self) -> "ToolSpec":
        if self.submit_behavior == SubmitBehavior.NONE and self.name.startswith("submit_"):
            raise ValueError("submit_* tools must declare a submit behavior")
        if self.submit_behavior != SubmitBehavior.NONE and not self.name.startswith("submit_"):
            raise ValueError("tools with submit behavior must use submit_* naming")
        if self.capability == ToolCapability.SUBMIT and self.submit_behavior == SubmitBehavior.NONE:
            raise ValueError("submit capability requires submit_behavior")
        return self


class ToolSpecView(StrictModel):
    name: str
    description: str
    capability: ToolCapability
    args_schema: dict[str, Any] = Field(default_factory=dict)
    backing_service: str
    backing_component: str | None = None
    backing_method: str
    result_view: str
    required_context: list[str] = Field(default_factory=list)
    tool_groups: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    submit_behavior: SubmitBehavior = SubmitBehavior.NONE


class ToolGroupSpec(StrictModel):
    key: str
    tool_names: list[str]
    skill_keys: list[str] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def _key_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("group key must be non-empty")
        return value


class ToolViewSpec(StrictModel):
    key: str
    group_keys: list[str]
    extra_tool_names: list[str] = Field(default_factory=list)
    allowed_agent_types: list[str]
    flow_kind: str | None = None
    stage: str | None = None

    @field_validator("key")
    @classmethod
    def _key_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("view key must be non-empty")
        return value


class ToolViewValidationReport(StrictModel):
    view_key: str
    valid: bool
    tool_names: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    summary: str


class ToolViewComponent:
    """Maintain code-first ToolSpec, ToolGroupSpec, and ToolViewSpec registries."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        agent_skill_keys: Mapping[str, Sequence[str]] | None = None,
        agent_type_permission_names: Callable[[str], set[str]] | None = None,
    ) -> None:
        self.runtime = runtime
        self._tools: dict[str, ToolSpec] = {}
        self._groups: dict[str, ToolGroupSpec] = {}
        self._views: dict[str, ToolViewSpec] = {}
        self._agent_skill_keys: dict[str, set[str]] = {
            str(agent): {str(skill) for skill in skills}
            for agent, skills in (agent_skill_keys or {}).items()
        }
        self._agent_type_permission_names = agent_type_permission_names or (lambda agent_type: {agent_type})

    def register_tool(self, tool_spec: ToolSpec) -> ServiceResult[MutationSummaryView]:
        if tool_spec.name in self._tools:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("tool_already_registered", "Tool is already registered.", object_ref=tool_spec.name)
            )
        if not tool_spec.description.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("tool_description_missing", "Tool description is required.", object_ref=tool_spec.name)
            )
        self._tools[tool_spec.name] = tool_spec
        return self.runtime.foundation.ok(
            self.runtime.foundation.mutation_view(
                object_ref=tool_spec.name,
                changed=True,
                summary=f"Registered tool {tool_spec.name}.",
                changed_items=["tool_registry"],
            )
        )

    def register_tool_groups(self, group_specs: Sequence[ToolGroupSpec]) -> ServiceResult[MutationSummaryView]:
        changed: list[str] = []
        for group in group_specs:
            if group.key in self._groups:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("tool_group_already_registered", "Tool group is already registered.", object_ref=group.key)
                )
            duplicates = self._duplicates(group.tool_names)
            if duplicates:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "tool_group_duplicate_tool",
                        "Tool group contains duplicate tool names.",
                        object_ref=group.key,
                        details={"duplicates": ",".join(duplicates)},
                    )
                )
            missing = [name for name in group.tool_names if name not in self._tools]
            if missing:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "tool_group_unknown_tool",
                        "Tool group references unknown tools.",
                        object_ref=group.key,
                        details={"missing": ",".join(missing)},
                    )
                )
            self._groups[group.key] = group
            changed.append(group.key)
        return self.runtime.foundation.ok(
            self.runtime.foundation.mutation_view(
                object_ref="tool_groups",
                changed=bool(changed),
                summary=f"Registered {len(changed)} tool groups.",
                changed_items=changed,
            )
        )

    def register_tool_views(self, view_specs: Sequence[ToolViewSpec]) -> ServiceResult[MutationSummaryView]:
        changed: list[str] = []
        for view in view_specs:
            if view.key in self._views:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("tool_view_already_registered", "Tool view is already registered.", object_ref=view.key)
                )
            missing_groups = [key for key in view.group_keys if key not in self._groups]
            if missing_groups:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "tool_view_unknown_group",
                        "Tool view references unknown groups.",
                        object_ref=view.key,
                        details={"missing": ",".join(missing_groups)},
                    )
                )
            missing_tools = [name for name in view.extra_tool_names if name not in self._tools]
            if missing_tools:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "tool_view_unknown_tool",
                        "Tool view references unknown extra tools.",
                        object_ref=view.key,
                        details={"missing": ",".join(missing_tools)},
                    )
                )
            overlap = self._view_overlap(view)
            if overlap:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "tool_view_group_overlap",
                        "Tool view contains overlapping tool names across groups or extras.",
                        object_ref=view.key,
                        details={"duplicates": ",".join(overlap)},
                    )
                )
            self._views[view.key] = view
            changed.append(view.key)
        return self.runtime.foundation.ok(
            self.runtime.foundation.mutation_view(
                object_ref="tool_views",
                changed=bool(changed),
                summary=f"Registered {len(changed)} tool views.",
                changed_items=changed,
            )
        )

    def get_tool_view(
        self,
        agent_type: str,
        *,
        flow_kind: str | None = None,
        stage: str | None = None,
    ) -> ServiceResult[ToolViewSpec]:
        try:
            permission_names = self._resolve_agent_type_permission_names(agent_type)
        except Exception as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "agent_type_permission_resolution_failed",
                    "AgentType permission names could not be resolved.",
                    object_ref=agent_type,
                    details={"error": str(exc)},
                )
            )
        candidates = [
            view for view in self._views.values()
            if not permission_names.isdisjoint(view.allowed_agent_types)
            and (flow_kind is None or view.flow_kind in {None, flow_kind})
            and (stage is None or view.stage in {None, stage})
        ]
        candidates.sort(key=lambda view: ((view.flow_kind is None), (view.stage is None), view.key))
        if not candidates:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("tool_view_not_found", "No tool view is registered for this agent type.", object_ref=agent_type)
            )
        return self.runtime.foundation.ok(candidates[0])

    def get_tool_view_by_key(self, view_key: str) -> ServiceResult[ToolViewSpec]:
        view = self._views.get(view_key)
        if view is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("tool_view_not_found", "Tool view is not registered.", object_ref=view_key)
            )
        return self.runtime.foundation.ok(view)

    def get_tool(self, tool_name: str) -> ServiceResult[ToolSpec]:
        tool = self._tools.get(tool_name)
        if tool is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("tool_not_registered", "Tool is not registered.", object_ref=tool_name)
            )
        return self.runtime.foundation.ok(tool)

    def list_tools_for_agent(self, agent_type: str) -> ServiceResult[list[ToolSpecView]]:
        view = self.get_tool_view(agent_type)
        if not view.ok or view.value is None:
            return self.runtime.foundation.fail(view.issues)
        return self.list_tools_for_view(view.value.key)

    def list_tools_for_view(self, view_key: str) -> ServiceResult[list[ToolSpecView]]:
        expanded = self._expand_view(view_key)
        if not expanded.ok or expanded.value is None:
            return self.runtime.foundation.fail(expanded.issues)
        return self.runtime.foundation.ok([self._tool_view(self._tools[name]) for name in expanded.value])

    def validate_tool_skill_alignment(self, agent_type: str) -> ServiceResult[GateReport]:
        """Return a compatibility gate without enforcing ToolGroup -> Skill binding.

        Agent tool visibility is defined by ToolView group membership. Skill
        required-tool-group coverage is validated at the AgentType registry
        layer in the forward direction: Agent skills -> required groups.
        """

        view = self.get_tool_view(agent_type)
        if not view.ok or view.value is None:
            return self.runtime.foundation.fail(view.issues)
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "tool_skill_alignment",
                summary="ToolGroup-to-Skill reverse alignment is not enforced.",
            )
        )

    def validate_step_expected_view(self, ctx: ToolExecutionContext) -> ServiceResult[None]:
        if ctx.endpoint_view_key != ctx.expected_view_key:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "tool_view_mismatch",
                    "Endpoint view does not match expected view.",
                    current=ctx.endpoint_view_key,
                    expected=ctx.expected_view_key,
                )
            )
        view = self.get_tool_view_by_key(ctx.expected_view_key)
        if not view.ok or view.value is None:
            return self.runtime.foundation.fail(view.issues)
        if ctx.actor.agent_type and ctx.actor.agent_type not in view.value.allowed_agent_types:
            try:
                permission_names = self._resolve_agent_type_permission_names(ctx.actor.agent_type)
            except Exception as exc:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "agent_type_permission_resolution_failed",
                        "AgentType permission names could not be resolved.",
                        object_ref=ctx.actor.agent_type,
                        details={"error": str(exc)},
                    )
                )
        else:
            permission_names = {ctx.actor.agent_type} if ctx.actor.agent_type else set()
        if ctx.actor.agent_type and permission_names.isdisjoint(view.value.allowed_agent_types):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "agent_type_not_allowed_for_view",
                    "Current agent type is not allowed to use this tool view.",
                    object_ref=ctx.actor.agent_type,
                    current=",".join(sorted(permission_names)),
                    expected=",".join(view.value.allowed_agent_types),
                )
            )
        return self.runtime.foundation.ok(None)

    def tool_names_for_view(self, view_key: str) -> ServiceResult[list[str]]:
        return self._expand_view(view_key)

    def _expand_view(self, view_key: str) -> ServiceResult[list[str]]:
        view_result = self.get_tool_view_by_key(view_key)
        if not view_result.ok or view_result.value is None:
            return self.runtime.foundation.fail(view_result.issues)
        view = view_result.value
        names: list[str] = []
        for group_key in view.group_keys:
            group = self._groups.get(group_key)
            if group is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("tool_view_unknown_group", "Tool view references an unknown group.", object_ref=group_key)
                )
            names.extend(group.tool_names)
        names.extend(view.extra_tool_names)
        duplicates = self._duplicates(names)
        if duplicates:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "tool_view_group_overlap",
                    "Tool view contains duplicate tool names.",
                    object_ref=view_key,
                    details={"duplicates": ",".join(duplicates)},
                )
            )
        missing = [name for name in names if name not in self._tools]
        if missing:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "tool_view_unknown_tool",
                    "Tool view references unknown tools.",
                    object_ref=view_key,
                    details={"missing": ",".join(missing)},
                )
            )
        return self.runtime.foundation.ok(sorted(names))

    def _view_overlap(self, view: ToolViewSpec) -> list[str]:
        names: list[str] = []
        for group_key in view.group_keys:
            group = self._groups[group_key]
            names.extend(group.tool_names)
        names.extend(view.extra_tool_names)
        return self._duplicates(names)

    def _duplicates(self, values: Sequence[str]) -> list[str]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for value in values:
            if value in seen:
                duplicates.add(value)
            seen.add(value)
        return sorted(duplicates)

    def _resolve_agent_type_permission_names(self, agent_type: str) -> set[str]:
        names = {str(name).strip() for name in self._agent_type_permission_names(agent_type)}
        return {name for name in names if name}

    def _tool_view(self, spec: ToolSpec) -> ToolSpecView:
        schema: dict[str, Any]
        try:
            schema = spec.args_model.model_json_schema()
        except Exception:
            schema = {}
        return ToolSpecView(
            name=spec.name,
            description=spec.description,
            capability=spec.capability,
            args_schema=schema,
            backing_service=spec.backing_service,
            backing_component=spec.backing_component,
            backing_method=spec.backing_method,
            result_view=spec.result_view,
            required_context=sorted(spec.required_context),
            tool_groups=sorted(spec.tool_groups),
            allowed_roles=sorted(spec.allowed_roles),
            submit_behavior=spec.submit_behavior,
        )
