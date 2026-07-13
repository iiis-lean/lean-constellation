"""Actual execution evidence helpers for Runtime Matrix strict coverage tests."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Literal

from agent_runtime_kit.flow.standard_steps import AgentStep


ToolViewKind = Literal["application", "submit"]
SnapshotEventKind = Literal["checkpoint", "restore"]


@dataclass
class FlowEvidence:
    flow_id: str
    flow_type: str
    status: str
    outcome: str | None
    scope_id: str


@dataclass
class StepEvidence:
    step_id: str
    flow_id: str
    step_type: str
    status: str
    result_outcome: str | None
    is_agent_step: bool


@dataclass
class ToolCallEvidence:
    tool_name: str
    view_key: str
    view_kind: ToolViewKind
    agent_type: str | None
    step_id: str | None
    ok: bool
    expected_failure: bool = False
    assertion_summary: str = ""


@dataclass
class SnapshotEvidence:
    event: SnapshotEventKind
    snapshot_id: str
    scope_ids: tuple[str, ...]
    label: str
    pruned: bool | None = None


@dataclass
class CodexArtifactEvidence:
    agent_type: str
    step_id: str
    artifact_path: str
    prompt_marker_seen: bool
    instruction_marker_seen: bool
    skill_markers_seen: tuple[str, ...]
    tools_called: tuple[str, ...]
    transcript_path: str | None = None
    mcp_transport: str | None = None
    mcp_server_urls: tuple[str, ...] = ()


@dataclass
class RuntimeMatrixEvidence:
    flows: list[FlowEvidence] = field(default_factory=list)
    steps: list[StepEvidence] = field(default_factory=list)
    application_tool_calls: list[ToolCallEvidence] = field(default_factory=list)
    submit_tool_calls: list[ToolCallEvidence] = field(default_factory=list)
    snapshots: list[SnapshotEvidence] = field(default_factory=list)
    codex_artifacts: list[CodexArtifactEvidence] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def flow_types(self) -> set[str]:
        return {item.flow_type for item in self.flows}

    @property
    def logic_step_types(self) -> set[str]:
        return {item.step_type for item in self.steps if not item.is_agent_step and item.status in {"completed", "failed"}}

    @property
    def agent_step_types(self) -> set[str]:
        return {item.step_type for item in self.steps if item.is_agent_step}

    @property
    def application_tool_names(self) -> set[str]:
        return {item.tool_name for item in self.application_tool_calls if item.ok or item.expected_failure}

    @property
    def submit_tool_names(self) -> set[str]:
        return {item.tool_name for item in self.submit_tool_calls if item.ok or item.expected_failure}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvidenceRecorder:
    """Collects proof that strict Runtime Matrix tests executed real surfaces."""

    def __init__(self) -> None:
        self.evidence = RuntimeMatrixEvidence()

    def merge_from(self, other: "EvidenceRecorder") -> None:
        self.evidence.flows.extend(other.evidence.flows)
        self.evidence.steps.extend(other.evidence.steps)
        self.evidence.application_tool_calls.extend(other.evidence.application_tool_calls)
        self.evidence.submit_tool_calls.extend(other.evidence.submit_tool_calls)
        self.evidence.snapshots.extend(other.evidence.snapshots)
        self.evidence.codex_artifacts.extend(other.evidence.codex_artifacts)
        self.evidence.notes.extend(other.evidence.notes)

    def record_runtime_state(self, runtime: Any) -> None:
        for flow in self._list_flows(runtime):
            flow_id = str(getattr(flow, "flow_id", ""))
            flow_type = str(getattr(flow, "flow_type", ""))
            self.evidence.flows.append(
                FlowEvidence(
                    flow_id=flow_id,
                    flow_type=flow_type,
                    status=self._status_value(getattr(flow, "status", "")),
                    outcome=self._outcome(getattr(flow, "result", None)),
                    scope_id=str(getattr(flow, "scope_id", "")),
                )
            )
            for step in self._list_steps(runtime, flow_id=flow_id):
                step_id = str(getattr(step, "step_id", ""))
                step_type = str(getattr(step, "step_type", ""))
                self.evidence.steps.append(
                    StepEvidence(
                        step_id=step_id,
                        flow_id=flow_id,
                        step_type=step_type,
                        status=self._status_value(getattr(step, "status", "")),
                        result_outcome=self._outcome(getattr(step, "result", None)),
                        is_agent_step=isinstance(step, AgentStep),
                    )
                )

    def record_tool_call(
        self,
        *,
        tool_name: str,
        view_key: str,
        view_kind: ToolViewKind,
        agent_type: str | None,
        step_id: str | None,
        ok: bool,
        expected_failure: bool = False,
        assertion_summary: str = "",
    ) -> None:
        item = ToolCallEvidence(
            tool_name=tool_name,
            view_key=view_key,
            view_kind=view_kind,
            agent_type=agent_type,
            step_id=step_id,
            ok=ok,
            expected_failure=expected_failure,
            assertion_summary=assertion_summary,
        )
        if view_kind == "application":
            self.evidence.application_tool_calls.append(item)
        else:
            self.evidence.submit_tool_calls.append(item)

    def record_checkpoint(self, *, snapshot_id: str, scope_ids: list[str], label: str) -> None:
        self.evidence.snapshots.append(
            SnapshotEvidence(
                event="checkpoint",
                snapshot_id=snapshot_id,
                scope_ids=tuple(scope_ids),
                label=label,
            )
        )

    def record_restore(self, *, snapshot_id: str, scope_ids: list[str], label: str, pruned: bool) -> None:
        self.evidence.snapshots.append(
            SnapshotEvidence(
                event="restore",
                snapshot_id=snapshot_id,
                scope_ids=tuple(scope_ids),
                label=label,
                pruned=pruned,
            )
        )

    def record_codex_artifact(
        self,
        *,
        agent_type: str,
        step_id: str,
        artifact_path: Path | str,
        transcript_path: Path | str | None = None,
        prompt_marker_seen: bool,
        instruction_marker_seen: bool,
        skill_markers_seen: list[str],
        tools_called: list[str],
        mcp_transport: str | None = None,
        mcp_server_urls: list[str] | None = None,
    ) -> None:
        self.evidence.codex_artifacts.append(
            CodexArtifactEvidence(
                agent_type=agent_type,
                step_id=step_id,
                artifact_path=str(artifact_path),
                transcript_path=str(transcript_path) if transcript_path is not None else None,
                prompt_marker_seen=prompt_marker_seen,
                instruction_marker_seen=instruction_marker_seen,
                skill_markers_seen=tuple(skill_markers_seen),
                tools_called=tuple(tools_called),
                mcp_transport=mcp_transport,
                mcp_server_urls=tuple(mcp_server_urls or ()),
            )
        )

    def add_note(self, note: str) -> None:
        self.evidence.notes.append(note)

    def export_json(self, path: Path | str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.evidence.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target

    def export_markdown_summary(self, path: Path | str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Runtime Matrix Evidence Summary",
            "",
            f"- flows: {len(self.evidence.flows)}",
            f"- steps: {len(self.evidence.steps)}",
            f"- application tool calls: {len(self.evidence.application_tool_calls)}",
            f"- submit tool calls: {len(self.evidence.submit_tool_calls)}",
            f"- snapshots: {len(self.evidence.snapshots)}",
            f"- codex artifacts: {len(self.evidence.codex_artifacts)}",
            "",
            "## Flow Types",
            "",
            *[f"- `{name}`" for name in sorted(self.evidence.flow_types)],
            "",
            "## Application Tools",
            "",
            *[f"- `{name}`" for name in sorted(self.evidence.application_tool_names)],
            "",
            "## Submit Tools",
            "",
            *[f"- `{name}`" for name in sorted(self.evidence.submit_tool_names)],
        ]
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target

    def missing_flows(self, required: set[str]) -> set[str]:
        return set(required) - self.evidence.flow_types

    def missing_logic_steps(self, required: set[str]) -> set[str]:
        return set(required) - self.evidence.logic_step_types

    def missing_agent_steps(self, required: set[str]) -> set[str]:
        return set(required) - self.evidence.agent_step_types

    def missing_application_tools(self, required: set[str]) -> set[str]:
        return set(required) - self.evidence.application_tool_names

    def missing_submit_tools(self, required: set[str]) -> set[str]:
        return set(required) - self.evidence.submit_tool_names

    def _list_flows(self, runtime: Any) -> list[Any]:
        flow_service = runtime.ark.flow_service
        if hasattr(flow_service, "list_flows"):
            return list(flow_service.list_flows())
        return []

    def _list_steps(self, runtime: Any, *, flow_id: str) -> list[Any]:
        flow_service = runtime.ark.flow_service
        if not hasattr(flow_service, "list_steps"):
            return []
        return list(flow_service.list_steps(flow_id=flow_id))

    def _status_value(self, value: Any) -> str:
        return str(getattr(value, "value", value))

    def _outcome(self, result: Any) -> str | None:
        if result is None:
            return None
        value = getattr(result, "outcome", None)
        return str(value) if value is not None else None
