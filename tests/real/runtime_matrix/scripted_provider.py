"""Provider-neutral scripted Agent backend used by Runtime Matrix tests."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
from time import sleep
from typing import Any, Mapping
import uuid

from agent_runtime_kit.agent.models import to_jsonable
from agent_runtime_kit.agent.provider_contracts import (
    AgentArtifactLocator,
    AgentEvent,
    AgentProviderBundle,
    AgentSessionUsage,
    AgentSessionView,
    AgentToolCall,
    AgentTurnUsage,
    AgentTurnView,
    ArtifactCaptureRequest,
    ArtifactDescribeRequest,
    ArtifactRestoreRequest,
    ArtifactStabilityRequest,
    ArtifactStabilityResult,
    CapabilityKey,
    CapabilityStatus,
    CapabilitySupport,
    HomeInitializationResult,
    HomeMaterializationResult,
    HomeValidationResult,
    Page,
    ProviderArtifactEntry,
    ProviderArtifactManifest,
    ProviderArtifactRestoreResult,
    ProviderArtifactSnapshot,
    ProviderCapabilities,
    ProviderControlAction,
    ProviderControlRequest,
    ProviderControlResult,
    ProviderDescriptor,
    ProviderEventBatch,
    ProviderExecutionContext,
    ProviderExecutionKind,
    ProviderForkResult,
    ProviderHomeKind,
    ProviderRegistry,
    ProviderRunRequest,
    ProviderRunState,
    ProviderSessionListQuery,
    ProviderSessionLocator,
    ProviderSessionQuery,
    ProviderToolQuery,
    ProviderTurnLocator,
    ProviderTurnQuery,
    ProviderTurnResult,
    ProviderUsageQuery,
    TokenUsage,
)
from agent_runtime_kit.agent.store_utils import utc_now_iso

from lean_constellation.flows.content_node_task.decl_round.flow import REVIEWER_AGENT_TYPES, WORKER_AGENT_TYPES
from lean_constellation.mcp import create_mcp_server


PROVIDER_TYPE = "scripted"


def initial_exploration_no_findings_scripts() -> dict[str, list[Any]]:
    """Return one current-schema terminal action for each fixed initial explorer."""

    return {
        "RepoResourceDiscoveryAgent": [
            (
                "submit_repo_resource_discovery_result",
                {
                    "outcome": "no_useful_findings",
                    "summary": "Strict Runtime Matrix found no additional resource candidate.",
                },
            )
        ],
        "RepoLeanProviderDiscoveryAgent": [
            (
                "submit_repo_lean_provider_discovery_result",
                {
                    "outcome": "no_useful_findings",
                    "summary": "Strict Runtime Matrix found no additional Lean provider candidate.",
                },
            )
        ],
        "RepoMathlibReconAgent": [
            (
                "submit_repo_mathlib_recon_result",
                {
                    "outcome": "no_useful_findings",
                    "summary": "Strict Runtime Matrix found no additional Mathlib entry.",
                },
            )
        ],
    }


class ScriptedHomeRenderer:
    provider_type = PROVIDER_TYPE

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = Path(runtime_root)

    def validate(self, spec) -> HomeValidationResult:  # noqa: ANN001
        valid = spec.provider_type == self.provider_type
        return HomeValidationResult(valid=valid, errors=() if valid else ("provider_type mismatch",))

    def materialize(self, spec, home_root) -> HomeMaterializationResult:  # noqa: ANN001
        del home_root
        return HomeMaterializationResult(
            provider_type=self.provider_type,
            home_id=spec.home_id,
            renderer_version="runtime-matrix-scripted-v1",
            manifest_schema_version=1,
            manifest_hash=hashlib.sha256(f"{self.provider_type}:{spec.home_id}".encode()).hexdigest(),
            effective_capabilities=_capabilities(),
        )

    def refresh_materialization(self, home, home_root) -> HomeMaterializationResult:  # noqa: ANN001
        return self.materialize(type("Spec", (), {"provider_type": self.provider_type, "home_id": home.home_id}), home_root)

    def initialize(self, home, ctx) -> HomeInitializationResult:  # noqa: ANN001
        del home, ctx
        return HomeInitializationResult(initialized=True)

    def build_execution_context(self, home, *, run_env=None, workdir=None) -> ProviderExecutionContext:  # noqa: ANN001
        env = dict(os.environ)
        env.update(home.fixed_env)
        env.update(run_env or {})
        return ProviderExecutionContext(
            provider_type=self.provider_type,
            home_id=home.home_id,
            home_root=self.runtime_root / home.home_relpath,
            process_environment=env,
            workdir=workdir,
        )


class ScriptedRunHandle:
    def __init__(
        self,
        request: ProviderRunRequest,
        *,
        session: ProviderSessionLocator,
        turn: ProviderTurnLocator,
        result: ProviderTurnResult,
        events: tuple[AgentEvent, ...],
    ) -> None:
        self.request = request
        self._session = session
        self._turn = turn
        self._result = result
        self._events = events

    @property
    def run_id(self) -> str:
        return self._result.run_id

    def session_locator(self) -> ProviderSessionLocator:
        return self._session

    def turn_locator(self) -> ProviderTurnLocator:
        return self._turn

    def poll_state(self) -> ProviderRunState:
        return self._result.status

    def drain_events(self, after_cursor: str | None = None) -> ProviderEventBatch:
        start = int(after_cursor or 0)
        return ProviderEventBatch(
            events=self._events[start:],
            next_cursor=str(len(self._events)),
            terminal=True,
        )

    def wait_terminal(self, timeout_s: float | None = None) -> ProviderTurnResult:
        del timeout_s
        return self._result

    def interrupt(self, timeout_s: float | None = None) -> ProviderControlResult:
        del timeout_s
        return _terminal_control(ProviderControlAction.INTERRUPT, self._result, accepted=False)

    def control(self, request: ProviderControlRequest) -> ProviderControlResult:
        return _terminal_control(request.action, self._result, accepted=False, requested_at=request.requested_at)

    def close(self) -> None:
        return None


class ScriptedMcpProvider:
    """Standard Provider bundle whose runs execute deterministic MCP actions."""

    provider_type = PROVIDER_TYPE

    def __init__(
        self,
        runtime=None,
        scripts: dict[str, list[Any]] | None = None,
        *,
        evidence_recorder: Any | None = None,
    ) -> None:
        self.runtime = runtime
        self.scripts = {key: list(value) for key, value in (scripts or {}).items()}
        self.calls: list[dict[str, Any]] = []
        self.evidence_recorder = evidence_recorder
        self._counter = 0
        self._results: dict[str, list[ProviderTurnResult]] = {}
        self._events: dict[str, list[AgentEvent]] = {}
        self.runtime_root: Path | None = None

    def enqueue(self, agent_type: str, action: Any) -> None:
        self.scripts.setdefault(agent_type, []).append(action)

    def start(self, request: ProviderRunRequest) -> ScriptedRunHandle:
        if request.session_locator is not None:
            raise ValueError("start requires no existing session")
        session = ProviderSessionLocator(
            provider_type=self.provider_type,
            session_id=f"scripted-session-{uuid.uuid4().hex}",
            home_id=request.home_id,
            created_at=utc_now_iso(),
        )
        return self._run(request, session)

    def resume(self, request: ProviderRunRequest) -> ScriptedRunHandle:
        if request.session_locator is None:
            raise ValueError("resume requires a session locator")
        return self._run(request, request.session_locator)

    def fork(self, request) -> ProviderForkResult:  # noqa: ANN001
        target = replace(
            request.source_session,
            session_id=f"scripted-session-{uuid.uuid4().hex}",
            home_id=request.target_home_id,
            created_at=utc_now_iso(),
        )
        return ProviderForkResult(
            source_session=request.source_session,
            source_turn=request.source_turn,
            target_session=target,
            status="forked",
            limitations=("Runtime Matrix scripted fork copies session identity only.",),
        )

    def control(self, request: ProviderControlRequest) -> ProviderControlResult:
        return ProviderControlResult(
            action=request.action,
            accepted=False,
            terminal_confirmed=False,
            requested_at=request.requested_at,
            completed_at=utc_now_iso(),
            reason="no active scripted run",
        )

    def close_session(self, locator: ProviderSessionLocator) -> ProviderControlResult:
        return ProviderControlResult(
            action=ProviderControlAction.ARCHIVE_SESSION,
            accepted=True,
            terminal_confirmed=True,
            requested_at=utc_now_iso(),
            completed_at=utc_now_iso(),
            resulting_state=ProviderRunState.COMPLETED,
            session_locator=locator,
        )

    def close(self) -> None:
        return None

    def _run(self, request: ProviderRunRequest, session: ProviderSessionLocator) -> ScriptedRunHandle:
        if self.runtime is None:
            raise RuntimeError("scripted provider is not installed")
        agent_type = request.agent_type
        if not self.scripts.get(agent_type):
            raise AssertionError(f"no scripted action available for {agent_type}")
        actions = self._normalize_actions(self.scripts[agent_type].pop(0))
        started_at = utc_now_iso()
        turn_index = len(self._results.get(session.session_id, ()))
        turn = ProviderTurnLocator(
            session=session,
            turn_id=f"scripted-turn-{uuid.uuid4().hex}",
            sequence=turn_index,
        )
        events = [
            AgentEvent(
                provider_type=self.provider_type,
                session_id=session.session_id,
                turn_id=turn.turn_id,
                sequence=len(self._events.get(session.session_id, ())),
                timestamp=started_at,
                kind="turn.started",
            )
        ]
        tool_calls: list[AgentToolCall] = []
        last_tool_name: str | None = None
        last_tool_result: object | None = None
        env = dict(request.environment)
        for view_kind, tool_name, arguments in actions:
            if view_kind == "file_replace":
                self._replace_file_text(arguments)
                view_key = None
            elif view_kind == "file_replace_last_result":
                if last_tool_result is None:
                    raise AssertionError("file replacement requires a preceding tool result")
                field_name = str(arguments.get("path_field", "path"))
                path = (
                    last_tool_result[field_name]
                    if isinstance(last_tool_result, Mapping)
                    else getattr(last_tool_result, field_name)
                )
                self._replace_file_text({**arguments, "path": path})
                view_key = None
            else:
                view_key = self._view_key(view_kind=view_kind, agent_type=agent_type, env=env)
                server = create_mcp_server(self.runtime, view_keys=[view_key])
                assert server.ok and server.value is not None, server.issues
                called = server.value.call_tool(view_key, tool_name, dict(arguments), env=env)
                assert called.ok and called.value is not None, called.issues
                assert called.value.ok is True, called.value
                last_tool_result = called.value
            call_id = f"scripted-call-{uuid.uuid4().hex}"
            tool_calls.append(
                AgentToolCall(
                    call_id=call_id,
                    tool_name=tool_name,
                    tool_kind="file" if view_kind.startswith("file_replace") else "mcp",
                    status="completed",
                    turn_id=turn.turn_id,
                    arguments=dict(arguments),
                )
            )
            self.calls.append(
                {
                    "agent_type": agent_type,
                    "tool_name": tool_name,
                    "arguments": dict(arguments),
                    "prompt": request.prompt,
                    "env": env,
                    "view_key": view_key,
                    "view_kind": view_kind,
                }
            )
            if self.evidence_recorder is not None:
                if view_kind.startswith("file_replace"):
                    self.evidence_recorder.add_note(f"{agent_type} performed file replacement action {tool_name}.")
                else:
                    self.evidence_recorder.record_tool_call(
                        tool_name=tool_name,
                        view_key=view_key,
                        view_kind=view_kind,
                        agent_type=agent_type,
                        step_id=env.get("ARK_STEP_ID"),
                        ok=True,
                        assertion_summary="Scripted provider MCP call succeeded.",
                    )
            last_tool_name = tool_name
        completed_at = utc_now_iso()
        events.append(
            AgentEvent(
                provider_type=self.provider_type,
                session_id=session.session_id,
                turn_id=turn.turn_id,
                sequence=events[0].sequence + 1,
                timestamp=completed_at,
                kind="terminal.completed",
                terminal=True,
            )
        )
        result = ProviderTurnResult(
            provider_type=self.provider_type,
            run_id=f"scripted-run-{uuid.uuid4().hex}",
            session_locator=session,
            turn_locator=turn,
            status=ProviderRunState.COMPLETED,
            started_at=started_at,
            completed_at=completed_at,
            final_text=f"{agent_type} called {last_tool_name or 'no tool'}",
            tool_calls=tuple(tool_calls),
            event_cursor=str(events[-1].sequence + 1),
            artifact_locator=_artifact_locator(session),
        )
        self._results.setdefault(session.session_id, []).append(result)
        self._events.setdefault(session.session_id, []).extend(events)
        self._persist_session(session)
        if request.event_sink is not None:
            for event in events:
                request.event_sink(event)
        self._counter += 1
        return ScriptedRunHandle(request, session=session, turn=turn, result=result, events=tuple(events))

    def _persist_session(self, session: ProviderSessionLocator) -> None:
        path = self.session_path(session)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "session": to_jsonable(session),
                    "results": [to_jsonable(item) for item in self._results[session.session_id]],
                    "events": [to_jsonable(item) for item in self._events[session.session_id]],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def session_path(self, session: ProviderSessionLocator) -> Path:
        if self.runtime_root is None:
            raise RuntimeError("scripted provider has no runtime_root")
        return self.runtime_root / _native_ref(session)

    def results(self, session: ProviderSessionLocator) -> tuple[ProviderTurnResult, ...]:
        return tuple(self._results.get(session.session_id, ()))

    def events(self, session: ProviderSessionLocator) -> tuple[AgentEvent, ...]:
        return tuple(self._events.get(session.session_id, ()))

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
            if view_kind not in {"application", "submit", "file_replace", "file_replace_last_result"}:
                raise AssertionError(f"unsupported scripted MCP view kind: {view_kind}")
            return view_kind, tool_name, dict(arguments)
        raise AssertionError(f"unsupported scripted action: {action!r}")

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


class ScriptedQueryAdapter:
    provider_type = PROVIDER_TYPE

    def __init__(self, provider: ScriptedMcpProvider) -> None:
        self.provider = provider

    def list_sessions(self, query: ProviderSessionListQuery) -> Page:
        sessions = [items[-1].session_locator for items in self.provider._results.values() if items]
        if query.home_id is not None:
            sessions = [item for item in sessions if item.home_id == query.home_id]
        start = int(query.cursor or 0)
        selected = sessions[start : start + query.limit]
        views = tuple(AgentSessionView(locator=item, status="completed") for item in selected)
        end = start + len(selected)
        return Page(items=views, next_cursor=str(end) if end < len(sessions) else None)

    def read_session(self, query: ProviderSessionQuery) -> AgentSessionView:
        turns = self._turn_views(query.locator) if query.include_turns else ()
        return AgentSessionView(locator=query.locator, status="completed", turns=turns)

    def list_turns(self, query: ProviderTurnQuery) -> Page:
        turns = list(self._turn_views(query.session))
        if query.turn is not None:
            turns = [item for item in turns if item.locator.turn_id == query.turn.turn_id]
        if query.latest:
            turns = turns[-1:]
        start = int(query.cursor or 0)
        selected = turns[start : start + query.limit]
        end = start + len(selected)
        return Page(items=tuple(selected), next_cursor=str(end) if end < len(turns) else None)

    def read_turn(self, query: ProviderTurnQuery) -> AgentTurnView | None:
        page = self.list_turns(replace(query, limit=1, latest=query.latest or query.turn is None))
        return page.items[0] if page.items else None  # type: ignore[return-value]

    def list_events(self, query) -> Page:  # noqa: ANN001
        events = list(self.provider.events(query.session))
        if query.turn is not None:
            events = [item for item in events if item.turn_id == query.turn.turn_id]
        if getattr(query, "kind", None) is not None:
            events = [item for item in events if item.kind == query.kind]
        if query.latest:
            events = events[-1:]
        start = int(query.cursor or 0)
        selected = events[start : start + query.limit]
        end = start + len(selected)
        return Page(items=tuple(selected), next_cursor=str(end) if end < len(events) else None)

    def list_tool_calls(self, query: ProviderToolQuery) -> Page:
        turns = self._turn_views(query.session)
        calls = [call for turn in turns for call in turn.tool_calls]
        if query.turn is not None:
            calls = [call for call in calls if call.turn_id == query.turn.turn_id]
        if query.call_id is not None:
            calls = [call for call in calls if call.call_id == query.call_id]
        start = int(query.cursor or 0)
        selected = calls[start : start + query.limit]
        end = start + len(selected)
        return Page(items=tuple(selected), next_cursor=str(end) if end < len(calls) else None)

    def read_usage(self, query: ProviderUsageQuery) -> AgentTurnUsage | AgentSessionUsage:
        empty = AgentTurnUsage(request_count=0, requests=(), token_usage=TokenUsage(), aggregate_complete=True)
        if query.include_session_aggregate:
            return AgentSessionUsage(
                turn_count=len(self.provider.results(query.session)),
                request_count=0,
                token_usage=TokenUsage(),
                turns=(),
                aggregate_complete=True,
            )
        return empty

    def _turn_views(self, session: ProviderSessionLocator) -> tuple[AgentTurnView, ...]:
        return tuple(
            AgentTurnView(
                locator=item.turn_locator,
                result=item,
                tool_calls=item.tool_calls,
                usage=item.turn_usage,
            )
            for item in self.provider.results(session)
            if item.turn_locator is not None
        )


class ScriptedArtifactAdapter:
    provider_type = PROVIDER_TYPE
    adapter_version = "1"

    def __init__(self, provider: ScriptedMcpProvider) -> None:
        self.provider = provider

    def wait_quiescent(self, request: ArtifactStabilityRequest) -> ArtifactStabilityResult:
        return ArtifactStabilityResult(
            stable=self.provider.session_path(request.session).is_file(),
            observed_at=utc_now_iso(),
        )

    def describe(self, request: ArtifactDescribeRequest) -> ProviderArtifactManifest:
        return self._manifest(request.session, captured_path=None)

    def capture(self, request: ArtifactCaptureRequest) -> ProviderArtifactSnapshot:
        source = self.provider.session_path(request.session)
        target = Path(request.snapshot_root) / _native_ref(request.session)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return ProviderArtifactSnapshot(
            manifest=self._manifest(request.session, captured_path=target),
            captured_at=utc_now_iso(),
            snapshot_root=request.snapshot_root,
        )

    def prepare_restore(self, request: ArtifactRestoreRequest) -> None:
        target = self._target(request)
        if target.is_file():
            target.unlink()

    def restore(self, request: ArtifactRestoreRequest) -> ProviderArtifactRestoreResult:
        entry = request.manifest.entries[0]
        if entry.snapshot_relpath is None:
            raise RuntimeError("scripted snapshot artifact has no path")
        source = Path(request.snapshot_root) / entry.snapshot_relpath
        target = self._target(request)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if entry.sha256 and _sha256(target) != entry.sha256:
            raise RuntimeError("scripted snapshot checksum mismatch")
        return ProviderArtifactRestoreResult(restored=True, restored_at=utc_now_iso())

    def rebuild_after_restore(self, request: ArtifactRestoreRequest) -> None:
        del request

    def _manifest(self, session: ProviderSessionLocator, *, captured_path: Path | None) -> ProviderArtifactManifest:
        native_ref = _native_ref(session)
        source = captured_path or self.provider.session_path(session)
        exists = source.is_file()
        return ProviderArtifactManifest(
            provider_type=self.provider_type,
            home_id=session.home_id,
            session_id=session.session_id,
            adapter_version=self.adapter_version,
            stable=exists,
            entries=(
                ProviderArtifactEntry(
                    artifact_id=f"scripted-session:{session.session_id}",
                    kind="session_state",
                    authority="ark_owned",
                    capture_strategy="copy_file",
                    native_ref=native_ref,
                    snapshot_relpath=native_ref,
                    sha256=_sha256(source) if exists else None,
                    size_bytes=source.stat().st_size if exists else None,
                    required_for_resume=True,
                ),
            ),
            locator=_artifact_locator(session),
        )

    def _target(self, request: ArtifactRestoreRequest) -> Path:
        home_id = request.target_home_id or request.manifest.home_id
        session = ProviderSessionLocator(
            provider_type=self.provider_type,
            session_id=request.manifest.session_id,
            home_id=home_id,
            created_at=utc_now_iso(),
        )
        return self.provider.session_path(session)


def build_scripted_provider_bundle(provider: ScriptedMcpProvider, *, runtime_root: Path) -> AgentProviderBundle:
    provider.runtime_root = Path(runtime_root)
    return AgentProviderBundle(
        descriptor=ProviderDescriptor(
            provider_type=PROVIDER_TYPE,
            display_name="Runtime Matrix Scripted Provider",
            adapter_version="1",
            execution_kind=ProviderExecutionKind.PYTHON_LIBRARY,
            home_kind=ProviderHomeKind.ARK_OWNED,
            static_capabilities=_capabilities(),
        ),
        runtime=provider,
        home_renderer=ScriptedHomeRenderer(Path(runtime_root)),
        query=ScriptedQueryAdapter(provider),
        artifacts=ScriptedArtifactAdapter(provider),
    )


def install_scripted_provider(runtime, provider: ScriptedMcpProvider) -> None:  # noqa: ANN001
    provider.runtime = runtime
    bundle = build_scripted_provider_bundle(provider, runtime_root=runtime.ark.agent_service.runtime_root)
    registry: ProviderRegistry = runtime.ark.agent_service.provider_registry
    registry.replace(bundle) if PROVIDER_TYPE in registry else registry.register(bundle)
    runtime.ark.agent_service.home_service.renderers[PROVIDER_TYPE] = bundle.home_renderer
    for agent_type in runtime.ark.agent_service.agent_types.list():
        agent_type.provider_type = PROVIDER_TYPE
    setattr(runtime, "scripted_provider", provider)


def get_or_install_scripted_provider(runtime) -> ScriptedMcpProvider:  # noqa: ANN001
    provider = getattr(runtime, "scripted_provider", None)
    if isinstance(provider, ScriptedMcpProvider):
        return provider
    provider = ScriptedMcpProvider(runtime)
    install_scripted_provider(runtime, provider)
    return provider


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
    raise AssertionError(f"scheduler did not reach expected Runtime Matrix state; flows={flows}; steps={steps}")


def _capabilities() -> ProviderCapabilities:
    available = {
        CapabilityKey.SESSION_CREATE,
        CapabilityKey.SESSION_RESUME,
        CapabilityKey.SESSION_READ,
        CapabilityKey.SESSION_LIST,
        CapabilityKey.RUN_WAIT_TERMINAL,
        CapabilityKey.QUERY_TURNS,
        CapabilityKey.QUERY_EVENTS,
        CapabilityKey.QUERY_TOOL_CALLS,
        CapabilityKey.QUERY_SESSION_USAGE,
        CapabilityKey.ARTIFACT_SNAPSHOT,
        CapabilityKey.ARTIFACT_RESTORE,
    }
    return ProviderCapabilities(
        provider_type=PROVIDER_TYPE,
        supports={
            key: CapabilitySupport(capability=key, status=CapabilityStatus.ARK_OWNED, available=True)
            for key in available
        },
    )


def _terminal_control(
    action: ProviderControlAction,
    result: ProviderTurnResult,
    *,
    accepted: bool,
    requested_at: str | None = None,
) -> ProviderControlResult:
    return ProviderControlResult(
        action=action,
        accepted=accepted,
        terminal_confirmed=True,
        requested_at=requested_at or utc_now_iso(),
        completed_at=utc_now_iso(),
        resulting_state=result.status,
        session_locator=result.session_locator,
        turn_locator=result.turn_locator,
        reason=None if accepted else "scripted run already completed",
    )


def _artifact_locator(session: ProviderSessionLocator) -> AgentArtifactLocator:
    return AgentArtifactLocator(
        provider_type=PROVIDER_TYPE,
        home_id=session.home_id,
        session_id=session.session_id,
        adapter_version="1",
        native_primary_ref=_native_ref(session),
    )


def _native_ref(session: ProviderSessionLocator) -> str:
    return f"homes/{PROVIDER_TYPE}/{session.home_id}/sessions/{session.session_id}.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
