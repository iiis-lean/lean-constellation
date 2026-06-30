"""Reusable fake ARK runtime harness for Lean Constellation Flow tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_runtime_kit.flow.contexts import StepRunContext
from agent_runtime_kit.flow.models import BaseStep, BaseSubmission, FlowRequest
from agent_runtime_kit.flow.registry import FlowTypeRegistry, StepTypeRegistry
from agent_runtime_kit.flow.services import FlowService, StepService
from agent_runtime_kit.runtime import ARKServices, AppServices

from lean_constellation.flows.registry import register_lean_flow_step_types


@dataclass
class FakeAgent:
    agent_id: str
    scope_id: str
    agent_type: str
    cli_type: str
    home_id: str | None = None


@dataclass
class FakeAgentTurnResult:
    agent_id: str
    prompt: str | None
    env: dict[str, str]
    submitted: bool = False


@dataclass
class FakeAgentStartRecord:
    agent_id: str
    variables: dict[str, Any]
    prompt: str | None
    env: dict[str, str]
    workdir: str | None = None


class FakeAgentService:
    """Small AgentService fake that can accept queued submissions during wait."""

    def __init__(self, *, ark: ARKServices, app: AppServices) -> None:
        self.ark = ark
        self.app = app
        self.agents: dict[str, FakeAgent] = {}
        self.start_records: list[FakeAgentStartRecord] = []
        self.turn_actions: list[BaseSubmission | None] = []
        self._agent_seq = 0

    def create_agent(
        self,
        scope_id: str,
        agent_type: str,
        *,
        cli_type: str = "codex",
        home_id: str | None = None,
    ) -> FakeAgent:
        self._agent_seq += 1
        agent = FakeAgent(
            agent_id=f"agent_{self._agent_seq}",
            scope_id=scope_id,
            agent_type=agent_type,
            cli_type=cli_type,
            home_id=home_id,
        )
        self.agents[agent.agent_id] = agent
        return agent

    def start_agent(
        self,
        agent_id: str,
        *,
        variables: dict[str, Any] | None = None,
        prompt: str | None = None,
        env: dict[str, str] | None = None,
        workdir: str | None = None,
    ) -> None:
        if agent_id not in self.agents:
            raise ValueError(f"unknown fake agent: {agent_id}")
        self.start_records.append(
            FakeAgentStartRecord(
                agent_id=agent_id,
                variables=dict(variables or {}),
                prompt=prompt,
                env=dict(env or {}),
                workdir=workdir,
            )
        )

    def wait_agent(self, agent_id: str, *, timeout_s: float | None = None) -> FakeAgentTurnResult:
        if not self.start_records or self.start_records[-1].agent_id != agent_id:
            raise ValueError(f"fake agent {agent_id} was not started")
        record = self.start_records[-1]
        action = self.turn_actions.pop(0) if self.turn_actions else None
        submitted = False
        if action is not None:
            self._accept_submission(record, action)
            submitted = True
        return FakeAgentTurnResult(agent_id=agent_id, prompt=record.prompt, env=record.env, submitted=submitted)

    def queue_submission(self, submission: BaseSubmission) -> None:
        self.turn_actions.append(submission)

    def queue_incomplete_turn(self) -> None:
        self.turn_actions.append(None)

    def _accept_submission(self, record: FakeAgentStartRecord, submission: BaseSubmission) -> None:
        step_id = record.env["ARK_STEP_ID"]
        flow_id = record.env["ARK_FLOW_ID"]
        agent_id = record.env["ARK_AGENT_ID"]
        flow_service = self.ark.flow_service
        if flow_service is None:
            raise ValueError("fake runtime has no flow_service")
        step = flow_service.get_step(step_id)
        if submission.submitted_by_agent_id is None:
            submission.submitted_by_agent_id = agent_id
        ctx = StepRunContext(ark=self.ark, app=self.app, step_id=step_id, flow_id=flow_id, scope_id=step.scope_id)
        ctx.accept_step_submission(submission, expected_agent_id=agent_id)


@dataclass
class FakeSnapshotService:
    records: list[dict[str, Any]] = field(default_factory=list)

    def record(self, **payload: Any) -> None:
        self.records.append(dict(payload))

    def create_runtime_snapshot(self, repo_root: str | Path, *, scope_ids: list[str], label: str | None = None) -> str:
        snapshot_id = f"fake_snapshot_{len(self.records) + 1}"
        self.record(
            snapshot_id=snapshot_id,
            repo_root=str(repo_root),
            scope_ids=list(scope_ids),
            label=label,
        )
        return snapshot_id


@dataclass
class FakeLeanFlowRuntime:
    root: Path
    ark: ARKServices
    app: AppServices
    flow_registry: FlowTypeRegistry
    step_registry: StepTypeRegistry
    flow_service: FlowService
    step_service: StepService
    agent_service: FakeAgentService
    snapshot_service: FakeSnapshotService

    def start_flow(self, flow_type: str, params: dict[str, Any], *, scope_id: str = "scope") -> str:
        return self.flow_service.start_flow(FlowRequest(flow_type=flow_type, scope_id=scope_id, params=params), enqueue=False)

    def attach_step(self, step: BaseStep) -> str:
        with self.flow_service.store.edit_session(step.scope_id) as tx:
            flow = tx.load_flow_for_update(step.flow_id)
            step_id = tx.add_step(step)
            if step_id not in flow.step_ids:
                flow.step_ids.append(step_id)
            flow.current_step_id = step_id
        return step.step_id

    def run_step(self, step_id: str) -> None:
        self.step_service.run_step(step_id)


def create_fake_lean_flow_runtime(
    root: Path,
    *,
    ark_services: ARKServices | None = None,
    app_services: AppServices | None = None,
) -> FakeLeanFlowRuntime:
    flow_registry = FlowTypeRegistry()
    step_registry = StepTypeRegistry()
    register_lean_flow_step_types(flow_registry=flow_registry, step_registry=step_registry)

    ark = ark_services or ARKServices()
    app = app_services or AppServices()
    flow_service = FlowService(
        root,
        flow_registry=flow_registry,
        step_registry=step_registry,
        ark_services=ark,
        app_services=app,
    )
    step_service = StepService(root, step_registry=step_registry, ark_services=ark, app_services=app)
    agent_service = FakeAgentService(ark=ark, app=app)
    snapshot_service = FakeSnapshotService()
    ark.agent_service = agent_service
    ark.snapshot_service = snapshot_service

    return FakeLeanFlowRuntime(
        root=Path(root),
        ark=ark,
        app=app,
        flow_registry=flow_registry,
        step_registry=step_registry,
        flow_service=flow_service,
        step_service=step_service,
        agent_service=agent_service,
        snapshot_service=snapshot_service,
    )
