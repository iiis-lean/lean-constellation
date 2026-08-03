"""Helpers for constructing application ToolSpec objects."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from lean_constellation.services.tool_facade import ActorContext, SubmitBehavior, ToolCapability, ToolExecutionContext, ToolSpec


ToolHandler = Callable[..., Any]
StringKey = str | StrEnum


def key_set(values: set[StringKey]) -> set[str]:
    return {value.value if isinstance(value, StrEnum) else str(value) for value in values}


def direct_tool(
    *,
    name: str,
    description: str,
    args_model: type[BaseModel],
    capability: ToolCapability,
    backing_service: str,
    backing_method: str,
    result_view: str,
    groups: set[StringKey],
    roles: set[str],
    required_agent_capabilities: set[str] | None = None,
    backing_component: str | None = None,
    required_context: set[str] | None = None,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        args_model=args_model,
        capability=capability,
        backing_service=backing_service,
        backing_component=backing_component,
        backing_method=backing_method,
        result_view=result_view,
        required_context={"repo"} if required_context is None else required_context,
        tool_groups=key_set(groups),
        allowed_roles=set(roles),
        required_agent_capabilities=set(required_agent_capabilities or ()),
    )


def handler_tool(
    *,
    name: str,
    description: str,
    args_model: type[BaseModel],
    capability: ToolCapability,
    result_view: str,
    groups: set[StringKey],
    roles: set[str],
    required_agent_capabilities: set[str] | None = None,
    handler: ToolHandler,
    backing_service: str = "handler",
    backing_method: str = "handler",
    required_context: set[str] | None = None,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        args_model=args_model,
        capability=capability,
        backing_service=backing_service,
        backing_method=backing_method,
        result_view=result_view,
        required_context={"repo"} if required_context is None else required_context,
        tool_groups=key_set(groups),
        allowed_roles=set(roles),
        required_agent_capabilities=set(required_agent_capabilities or ()),
        backing_handler=handler,
    )


def submit_handler_tool(
    *,
    name: str,
    description: str,
    args_model: type[BaseModel],
    result_view: str,
    groups: set[StringKey],
    roles: set[str],
    required_agent_capabilities: set[str] | None = None,
    handler: ToolHandler,
    submit_behavior: SubmitBehavior,
    backing_service: str = "submit_handler",
    backing_method: str = "submit_handler",
    required_context: set[str] | None = None,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        args_model=args_model,
        capability=ToolCapability.SUBMIT,
        backing_service=backing_service,
        backing_method=backing_method,
        result_view=result_view,
        required_context={"repo"} if required_context is None else required_context,
        tool_groups=key_set(groups),
        allowed_roles=set(roles),
        required_agent_capabilities=set(required_agent_capabilities or ()),
        submit_behavior=submit_behavior,
        backing_handler=handler,
    )


def current_node_path(ctx: ToolExecutionContext) -> str:
    if ctx.node is None:
        raise ValueError("Current tool context is not bound to a node.")
    return ctx.node.node_path


def actor_for_write(ctx: ToolExecutionContext) -> str:
    return ctx.actor.added_by or ctx.actor.role


def actor_role(ctx: ToolExecutionContext) -> ActorContext:
    return ctx.actor
