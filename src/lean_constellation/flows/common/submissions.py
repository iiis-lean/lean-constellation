"""Shared ARK submission helpers for Lean Constellation business steps."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from agent_runtime_kit.flow.models import BaseSubmission, ChildFlowDispatchSubmission
from pydantic import BaseModel, Field


def new_submission_id(prefix: str = "sub") -> str:
    """Allocate a short, stable submission id."""

    return f"{prefix}_{uuid4().hex}"


class LeanBaseSubmission(BaseSubmission):
    """Common metadata for non-dispatch Lean Constellation submissions."""

    repo_key: str | None = None
    node_path: str | None = None


class LeanDispatchSubmission(ChildFlowDispatchSubmission):
    """Common metadata for dispatching Lean Constellation child flows."""

    repo_key: str | None = None
    node_path: str | None = None


def submission_agent_id(ctx: object) -> str | None:
    runtime = getattr(ctx, "runtime", None)
    agent_id = getattr(runtime, "agent_id", None)
    return str(agent_id) if agent_id else None


def dump_submission_for_view(submission: BaseSubmission) -> dict[str, Any]:
    """Return an Agent-readable submission view without internal object ids."""

    dumped = submission.model_dump(mode="json")
    for hidden in ("submitted_by_agent_id",):
        dumped.pop(hidden, None)
    return dumped


class SimpleSubmissionView(BaseModel):
    """Small value useful in unit tests and debug traces."""

    submission_type: str
    tool_name: str
    summary: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
