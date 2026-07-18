"""Scripted MCP Agent provider used by Runtime Matrix flow tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Any

from lean_constellation.flows.content_node_task.decl_round.flow import REVIEWER_AGENT_TYPES, WORKER_AGENT_TYPES
from lean_constellation.mcp import create_mcp_server


@dataclass
class ScriptedTurnResult:
    final_response: str
    status: str = "succeeded"


@dataclass
class ScriptedProviderResult:
    thread_id: str
    rollout_relpath: str | None
    turn_result: ScriptedTurnResult


class ScriptedMcpProvider:
    """Codex-like provider that executes declared MCP tool calls for an AgentType."""

    def __init__(self, runtime, scripts: dict[str, list[Any]] | None = None, *, evidence_recorder: Any | None = None) -> None:
        self.runtime = runtime
        self.scripts = {key: list(value) for key, value in (scripts or {}).items()}
        self.calls: list[dict[str, Any]] = []
        self.evidence_recorder = evidence_recorder
        self._counter = 0

    def ensure_home_initialized(self, **kwargs):  # noqa: ANN003
        return {"home_id": kwargs.get("home_id"), "initialized": True}

    def start_thread(self, **kwargs) -> ScriptedProviderResult:  # noqa: ANN003
        return self._run(**kwargs)

    def resume_thread(self, **kwargs) -> ScriptedProviderResult:  # noqa: ANN003
        return self._run(**kwargs)

    def read_latest_turn_result(self, *args, **kwargs) -> ScriptedTurnResult:  # noqa: ANN002, ANN003
        return ScriptedTurnResult(final_response="scripted provider latest turn")

    def close(self) -> None:
        return None

    def _run(self, **kwargs) -> ScriptedProviderResult:  # noqa: ANN003
        env = dict(kwargs["env"])
        prompt = kwargs.get("prompt")
        agent_type = self._agent_type(env=env, kwargs=kwargs)
        if not self.scripts.get(agent_type):
            raise AssertionError(f"no scripted action available for {agent_type}")
        actions = self._normalize_actions(self.scripts[agent_type].pop(0))
        last_tool_name: str | None = None
        for view_kind, tool_name, arguments in actions:
            if view_kind == "file_replace":
                self._replace_file_text(arguments)
                self.calls.append(
                    {
                        "agent_type": agent_type,
                        "tool_name": tool_name,
                        "arguments": dict(arguments),
                        "prompt": prompt,
                        "view_key": None,
                        "view_kind": view_kind,
                    }
                )
                if self.evidence_recorder is not None:
                    self.evidence_recorder.add_note(f"{agent_type} performed file replacement action {tool_name}.")
                last_tool_name = tool_name
                continue
            view_key = self._view_key(view_kind=view_kind, agent_type=agent_type, env=env)
            server = create_mcp_server(self.runtime, view_keys=[view_key])
            assert server.ok and server.value is not None, server.issues
            called = server.value.call_tool(view_key, tool_name, dict(arguments), env=env)
            assert called.ok and called.value is not None, called.issues
            assert called.value.ok is True, called.value
            if self.evidence_recorder is not None:
                self.evidence_recorder.record_tool_call(
                    tool_name=tool_name,
                    view_key=view_key,
                    view_kind=view_kind,  # type: ignore[arg-type]
                    agent_type=agent_type,
                    step_id=env.get("ARK_STEP_ID"),
                    ok=True,
                    assertion_summary="Scripted provider MCP call succeeded.",
                )
            last_tool_name = tool_name
            self.calls.append(
                {
                    "agent_type": agent_type,
                    "tool_name": tool_name,
                    "arguments": dict(arguments),
                    "prompt": prompt,
                    "view_key": view_key,
                    "view_kind": view_kind,
                }
            )
        self._counter += 1
        return ScriptedProviderResult(
            thread_id=f"runtime-matrix-scripted-thread-{self._counter}",
            rollout_relpath=None,
            turn_result=ScriptedTurnResult(final_response=f"{agent_type} called {last_tool_name or 'no tool'}"),
        )

    def _normalize_actions(self, action: Any) -> list[tuple[str, str, dict[str, Any]]]:
        if isinstance(action, list):
            return [self._normalize_single_action(item) for item in action]
        return [self._normalize_single_action(action)]

    def _normalize_single_action(self, action: Any) -> tuple[str, str, dict[str, Any]]:
        if isinstance(action, tuple) and len(action) == 2:
            tool_name, arguments = action
            return "submit", tool_name, dict(arguments)
        if isinstance(action, tuple) and len(action) == 3:
            view_kind, tool_name, arguments = action
            if view_kind not in {"application", "submit", "file_replace"}:
                raise AssertionError(f"unsupported scripted MCP view kind: {view_kind}")
            return view_kind, tool_name, dict(arguments)
        raise AssertionError(f"unsupported scripted action: {action!r}")

    def _agent_type(self, *, env: dict[str, str], kwargs: dict[str, Any]) -> str:
        agent_type = env.get("LEAN_CONSTELLATION_AGENT_TYPE")
        if agent_type:
            return agent_type
        agent_id = kwargs.get("agent_id") or env.get("ARK_AGENT_ID")
        if agent_id:
            return self.runtime.ark.agent_service.get_agent(agent_id).agent_type
        raise AssertionError("scripted provider cannot infer agent type from env or kwargs")

    def _view_key(self, *, view_kind: str, agent_type: str, env: dict[str, str]) -> str:
        if view_kind == "application":
            return (
                env.get("LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW")
                or env.get("LEAN_CONSTELLATION_EXPECTED_TOOL_VIEW")
                or self._step_expected_view_key(env)
                or self._application_view_for_agent_type(agent_type)
            )
        return env.get("LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW") or self._submit_view_for_agent_type(agent_type)

    def _step_expected_view_key(self, env: dict[str, str]) -> str | None:
        step_id = env.get("ARK_STEP_ID")
        if not step_id:
            return None
        step = self.runtime.ark.step_service.store.get_step(step_id)
        variables = dict(getattr(getattr(step, "state", None), "variables", {}) or {})
        value = variables.get("expected_view_key")
        return str(value) if value else None

    def _application_view_for_agent_type(self, agent_type: str) -> str:
        for stage, worker_agent_type in WORKER_AGENT_TYPES.items():
            if agent_type == worker_agent_type:
                return f"{stage}_worker"
        for stage, reviewer_agent_type in REVIEWER_AGENT_TYPES.items():
            if agent_type == reviewer_agent_type:
                return f"{stage}_reviewer"
        raise AssertionError(f"scripted provider cannot infer application view for {agent_type}")

    def _submit_view_for_agent_type(self, agent_type: str) -> str:
        if agent_type in set(WORKER_AGENT_TYPES.values()):
            return "decl_stage_worker_submit"
        if agent_type in set(REVIEWER_AGENT_TYPES.values()):
            return "decl_stage_reviewer_submit"
        raise AssertionError(f"scripted provider cannot infer submit view for {agent_type}")

    def _replace_file_text(self, arguments: dict[str, Any]) -> None:
        repo_root = Path(arguments["repo_root"]).resolve()
        path = Path(arguments["path"]).resolve()
        try:
            path.relative_to(repo_root)
        except ValueError as exc:
            raise AssertionError(f"scripted file action outside repo root: {path}") from exc
        old = str(arguments["old"])
        new = str(arguments["new"])
        text = path.read_text(encoding="utf-8")
        if old not in text:
            raise AssertionError(f"scripted file replacement target not found in {path}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")


def install_scripted_provider(runtime, provider: ScriptedMcpProvider, *, cli_type: str = "codex") -> None:
    provider.runtime = runtime
    runtime.ark.agent_service.providers[cli_type] = provider


def schedule_until(runtime, predicate, *, limit: int = 100, step_timeout_s: float = 20) -> None:  # noqa: ANN001
    for _ in range(limit):
        if predicate():
            return
        runtime.ark.schedule_service.rebuild_candidate_queues()
        tick = runtime.ark.schedule_service.schedule_ready()
        for step_id in tick.started_step_ids:
            runtime.ark.step_service.wait_step(step_id, timeout_s=step_timeout_s)
        if predicate():
            return
        sleep(0.01)
    flows = [
        f"{flow.flow_type}:{flow.flow_id}:{flow.status}:{getattr(flow.state, 'position', None)}:"
        f"{getattr(flow, 'result', None)}"
        for flow in runtime.ark.flow_service.list_flows()
    ]
    steps = [
        f"{step.step_type}:{step.step_id}:{step.status}:{getattr(step, 'result', None)}:"
        f"{getattr(step, 'error', None)}"
        for step in runtime.ark.flow_service.list_steps()
    ]
    raise AssertionError(
        "scheduler did not reach expected Runtime Matrix state; "
        f"flows={flows}; steps={steps}"
    )
