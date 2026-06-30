"""Helpers for ARK external takeover provider handoffs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from agent_runtime_kit.agent.providers import ExternalTakeoverProvider
from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.mcp import create_mcp_server

if TYPE_CHECKING:
    from lean_constellation.mcp.schemas import McpToolRegistration
    from lean_constellation.services.foundation import ToolResultView
    from lean_constellation.services.runtime import LeanRuntimeServices


ExternalTakeoverCompletionStatus = Literal["completed", "failed", "cancelled"]


class ExternalTakeoverCompleteInput(StrictModel):
    handoff_id: str
    status: ExternalTakeoverCompletionStatus = "completed"
    final_response: str | None = None
    error: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    rollout_relpath: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    handoff_dirname: str = "external_turns"

    @field_validator("handoff_id", "handoff_dirname")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must be non-empty")
        return value


class ExternalTakeoverHandoffView(StrictModel):
    handoff_id: str
    status: str
    handoff_path: str
    completion_path: str
    agent_id: str | None = None
    home_id: str | None = None
    thread_id: str | None = None
    summary: str


class ExternalTakeoverToolListInput(StrictModel):
    handoff_id: str
    view_kind: Literal["application", "submit"] = "submit"
    handoff_dirname: str = "external_turns"

    @field_validator("handoff_id", "handoff_dirname")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must be non-empty")
        return value


class ExternalTakeoverToolCallInput(ExternalTakeoverToolListInput):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_name")
    @classmethod
    def _tool_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("tool_name must be non-empty")
        return value


def build_external_takeover_agent_providers(
    runtime_root: Path | str,
    *,
    cli_type: str = "codex",
    poll_interval_s: float = 0.1,
    default_timeout_s: float | None = None,
    handoff_dirname: str = "external_turns",
) -> dict[str, ExternalTakeoverProvider]:
    """Build AgentService providers that hand codex turns to external files."""

    provider = ExternalTakeoverProvider(
        runtime_root=Path(runtime_root),
        poll_interval_s=poll_interval_s,
        default_timeout_s=default_timeout_s,
        handoff_dirname=handoff_dirname,
    )
    return {cli_type: provider}


def complete_external_takeover_handoff(
    runtime_root: Path | str,
    input_model: ExternalTakeoverCompleteInput,
) -> ExternalTakeoverHandoffView:
    """Write completion.json for a pending ARK external takeover handoff."""

    handoff_dir = _handoff_dir(Path(runtime_root), input_model.handoff_dirname, input_model.handoff_id)
    handoff_path = handoff_dir / "handoff.json"
    completion_path = handoff_dir / "completion.json"
    if not handoff_path.exists():
        raise FileNotFoundError(f"external handoff is not found: {input_model.handoff_id}")
    handoff = _read_json(handoff_path)
    if completion_path.exists():
        completion = _read_json(completion_path)
        return _handoff_view(handoff, completion, handoff_path, completion_path, summary="External handoff was already completed.")

    completion: dict[str, Any] = {
        "schema_version": 1,
        "handoff_id": input_model.handoff_id,
        "status": input_model.status,
        "completed_at": _utc_now_iso(),
        "metadata": dict(input_model.metadata),
    }
    if input_model.final_response is not None:
        completion["final_response"] = input_model.final_response
    if input_model.error is not None:
        completion["error"] = input_model.error
    if input_model.thread_id is not None:
        completion["thread_id"] = input_model.thread_id
    if input_model.turn_id is not None:
        completion["turn_id"] = input_model.turn_id
    if input_model.rollout_relpath is not None:
        completion["rollout_relpath"] = input_model.rollout_relpath
    _write_json_atomic(completion_path, completion)
    return _handoff_view(handoff, completion, handoff_path, completion_path, summary="External handoff completion was written.")


def list_external_takeover_handoffs(
    runtime_root: Path | str,
    *,
    handoff_dirname: str = "external_turns",
    status: str | None = None,
) -> list[ExternalTakeoverHandoffView]:
    """List external takeover handoffs recorded under runtime_root."""

    root = Path(runtime_root) / handoff_dirname
    if not root.exists():
        return []
    views: list[ExternalTakeoverHandoffView] = []
    for handoff_path in sorted(root.glob("*/handoff.json")):
        completion_path = handoff_path.with_name("completion.json")
        handoff = _read_json(handoff_path)
        completion = _read_json(completion_path) if completion_path.exists() else None
        view = _handoff_view(
            handoff,
            completion,
            handoff_path,
            completion_path,
            summary="External handoff is completed." if completion else "External handoff is pending.",
        )
        if status is None or view.status == status:
            views.append(view)
    return views


def list_external_takeover_tools(
    runtime: "LeanRuntimeServices",
    runtime_root: Path | str,
    input_model: ExternalTakeoverToolListInput,
) -> list["McpToolRegistration"]:
    """List MCP tools available through a handoff's recorded env."""

    handoff, _ = _load_handoff(runtime_root, input_model.handoff_dirname, input_model.handoff_id)
    view_key = _view_key_for_handoff(handoff, input_model.view_kind)
    server = create_mcp_server(runtime, view_keys=[view_key])
    if not server.ok or server.value is None:
        message = "; ".join(issue.message for issue in server.issues) or "failed to create MCP view server"
        raise RuntimeError(message)
    listed = server.value.list_tools(view_key)
    if not listed.ok or listed.value is None:
        message = "; ".join(issue.message for issue in listed.issues) or f"failed to list tools for {view_key}"
        raise RuntimeError(message)
    return list(listed.value)


def call_external_takeover_tool(
    runtime: "LeanRuntimeServices",
    runtime_root: Path | str,
    input_model: ExternalTakeoverToolCallInput,
) -> "ToolResultView":
    """Invoke an MCP tool using a handoff's recorded runtime env."""

    handoff, _ = _load_handoff(runtime_root, input_model.handoff_dirname, input_model.handoff_id)
    view_key = _view_key_for_handoff(handoff, input_model.view_kind)
    env = dict(handoff.get("env") or {})
    server = create_mcp_server(runtime, view_keys=[view_key])
    if not server.ok or server.value is None:
        message = "; ".join(issue.message for issue in server.issues) or "failed to create MCP view server"
        raise RuntimeError(message)
    called = server.value.call_tool(view_key, input_model.tool_name, dict(input_model.arguments), env=env)
    if not called.ok or called.value is None:
        message = "; ".join(issue.message for issue in called.issues) or f"failed to call tool {input_model.tool_name}"
        raise RuntimeError(message)
    return called.value


def _handoff_dir(runtime_root: Path, handoff_dirname: str, handoff_id: str) -> Path:
    return runtime_root / handoff_dirname / handoff_id


def _load_handoff(
    runtime_root: Path | str,
    handoff_dirname: str,
    handoff_id: str,
) -> tuple[dict[str, Any], Path]:
    handoff_path = _handoff_dir(Path(runtime_root), handoff_dirname, handoff_id) / "handoff.json"
    if not handoff_path.exists():
        raise FileNotFoundError(f"external handoff is not found: {handoff_id}")
    return _read_json(handoff_path), handoff_path


def _view_key_for_handoff(handoff: dict[str, Any], view_kind: str) -> str:
    env = dict(handoff.get("env") or {})
    env_key = "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW" if view_kind == "application" else "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW"
    view_key = str(env.get(env_key) or "").strip()
    if not view_key:
        raise ValueError(f"handoff env does not contain {env_key}")
    return view_key


def _handoff_view(
    handoff: dict[str, Any],
    completion: dict[str, Any] | None,
    handoff_path: Path,
    completion_path: Path,
    *,
    summary: str,
) -> ExternalTakeoverHandoffView:
    return ExternalTakeoverHandoffView(
        handoff_id=str(handoff.get("handoff_id") or completion_path.parent.name),
        status=str(completion.get("status")) if completion else str(handoff.get("status") or "pending"),
        handoff_path=str(handoff_path),
        completion_path=str(completion_path),
        agent_id=_optional_str(handoff.get("agent_id")),
        home_id=_optional_str(handoff.get("home_id")),
        thread_id=_optional_str((completion or {}).get("thread_id") or handoff.get("thread_id")),
        summary=summary,
    )


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object at {path}")
    return data


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(path)


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


__all__ = [
    "ExternalTakeoverCompleteInput",
    "ExternalTakeoverCompletionStatus",
    "ExternalTakeoverHandoffView",
    "ExternalTakeoverToolCallInput",
    "ExternalTakeoverToolListInput",
    "build_external_takeover_agent_providers",
    "call_external_takeover_tool",
    "complete_external_takeover_handoff",
    "list_external_takeover_handoffs",
    "list_external_takeover_tools",
]
