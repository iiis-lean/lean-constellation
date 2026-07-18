from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import shutil
from time import monotonic, sleep
from typing import Any

import pytest
from agent_runtime_kit.agent.homes import HomeCreateSpec
from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from lean_constellation.app import (
    AdminFlowAdvanceInput,
    AdminStepStartInput,
    ExternalTakeoverCompleteInput,
    ExternalTakeoverToolCallInput,
    ExternalTakeoverToolListInput,
    LeanAdminApi,
    ManualCheckpointInput,
    SetAgentStepOverrideInput,
    SnapshotCreateInput,
    SnapshotRestoreInput,
    StartFlowInput,
    build_external_takeover_agent_providers,
    call_external_takeover_tool,
    complete_external_takeover_handoff,
    create_app_runtime_services,
    create_test_control_runtime_services,
    initialize_repo_business_truth,
    materialize_agent_home,
)
from lean_constellation.agents import build_agent_type_specs, derive_agent_type_spec
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.flows.testing import (
    CONTROLLED_AGENT_OVERRIDE_ALIASES,
    CONTROLLED_BUSINESS_AGENT_STEP_OVERRIDES,
    ControlledAgentOverrideSpec,
)
from lean_constellation.flows.content_node_task.flows import ContentNodeTaskResult
from lean_constellation.mcp import create_mcp_server
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.external_clients import ExternalCommandResult, LeanCheckSummaryView
from tests.real.runtime_matrix.transport import start_runtime_mcp_http_server
from tests.unit.flows.decl_round._helpers import NODE_PATH, create_round_with_decl, setup_content_node


pytestmark = [pytest.mark.real, pytest.mark.slow]


@dataclass
class _TurnResult:
    final_response: str
    status: str = "succeeded"


@dataclass
class _ProviderResult:
    thread_id: str
    rollout_relpath: str | None
    turn_result: _TurnResult


class _ScriptedMcpProvider:
    def __init__(self, runtime, scripts: dict[str, list[Any]]) -> None:
        self.runtime = runtime
        self.scripts = {key: list(value) for key, value in scripts.items()}
        self.calls: list[dict[str, Any]] = []
        self._counter = 0

    def ensure_home_initialized(self, **kwargs):  # noqa: ANN003
        return {"home_id": kwargs.get("home_id"), "initialized": True}

    def start_thread(self, **kwargs) -> _ProviderResult:  # noqa: ANN003
        return self._run(**kwargs)

    def resume_thread(self, **kwargs) -> _ProviderResult:  # noqa: ANN003
        return self._run(**kwargs)

    def read_latest_turn_result(self, *args, **kwargs) -> _TurnResult:  # noqa: ANN002, ANN003
        return _TurnResult(final_response="scripted provider latest turn")

    def close(self) -> None:
        return None

    def _run(self, **kwargs) -> _ProviderResult:  # noqa: ANN003
        env = dict(kwargs["env"])
        prompt = kwargs.get("prompt")
        agent_type = env["LEAN_CONSTELLATION_AGENT_TYPE"]
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
                last_tool_name = tool_name
                continue
            view_key = env["LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW"] if view_kind == "application" else env["LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW"]
            server = create_mcp_server(self.runtime, view_keys=[view_key])
            assert server.ok and server.value is not None, server.issues
            called = server.value.call_tool(view_key, tool_name, arguments, env=env)
            assert called.ok and called.value is not None, called.issues
            assert called.value.ok is True, called.value
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
        return _ProviderResult(
            thread_id=f"scripted-thread-{self._counter}",
            rollout_relpath=None,
            turn_result=_TurnResult(final_response=f"{agent_type} called {last_tool_name or 'no tool'}"),
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


class _ExternalTakeoverMcpController:
    def __init__(self, runtime, runtime_root: Path, scripts: dict[str, list[Any]]) -> None:
        self.runtime = runtime
        self.runtime_root = Path(runtime_root)
        self.scripts = {key: list(value) for key, value in scripts.items()}
        self.calls: list[dict[str, Any]] = []
        self.completed_handoff_ids: set[str] = set()

    def drain_pending(self, *, wait_s: float = 0) -> int:
        deadline = monotonic() + wait_s
        processed = 0
        while True:
            processed += self._drain_once()
            if processed or wait_s <= 0 or monotonic() >= deadline:
                return processed
            sleep(0.01)

    def _drain_once(self) -> int:
        processed = 0
        for handoff_path in sorted((self.runtime_root / "external_turns").glob("*/handoff.json")):
            completion_path = handoff_path.with_name("completion.json")
            if completion_path.exists():
                continue
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            handoff_id = str(handoff["handoff_id"])
            if handoff_id in self.completed_handoff_ids:
                continue
            self._handle_handoff(handoff)
            self.completed_handoff_ids.add(handoff_id)
            processed += 1
        return processed

    def _handle_handoff(self, handoff: dict[str, Any]) -> None:
        env = dict(handoff["env"])
        agent_type = env["LEAN_CONSTELLATION_AGENT_TYPE"]
        if not self.scripts.get(agent_type):
            raise AssertionError(f"no external takeover action available for {agent_type}")
        actions = _ScriptedMcpProvider(None, {})._normalize_actions(self.scripts[agent_type].pop(0))
        last_tool_name: str | None = None
        for view_kind, tool_name, arguments in actions:
            view_key = env["LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW"] if view_kind == "application" else env["LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW"]
            arguments = self._resolve_arguments(dict(arguments), env)
            called = call_external_takeover_tool(
                self.runtime,
                self.runtime_root,
                ExternalTakeoverToolCallInput(
                    handoff_id=str(handoff["handoff_id"]),
                    view_kind=view_kind,
                    tool_name=tool_name,
                    arguments=dict(arguments),
                ),
            )
            assert called.ok is True, called
            last_tool_name = tool_name
            self.calls.append(
                {
                    "agent_type": agent_type,
                    "tool_name": tool_name,
                    "arguments": dict(arguments),
                    "prompt": handoff.get("prompt"),
                    "view_key": view_key,
                    "handoff_id": handoff["handoff_id"],
                }
            )
        complete_external_takeover_handoff(
            self.runtime_root,
            ExternalTakeoverCompleteInput(
                handoff_id=str(handoff["handoff_id"]),
                final_response=f"{agent_type} externally called {last_tool_name or 'no tool'}",
                thread_id=f"external-thread-{handoff['handoff_id']}",
            ),
        )

    def _resolve_arguments(self, value: Any, env: dict[str, str]) -> Any:
        if isinstance(value, dict):
            return {key: self._resolve_arguments(item, env) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve_arguments(item, env) for item in value]
        if isinstance(value, str) and value.startswith("$"):
            return env[value[1:]]
        return value


class _FakeLakeClient:
    def run_lake_update(self, repo_root: Path) -> ExternalCommandResult:
        return ExternalCommandResult(ok=True, command=["lake", "update"], cwd=str(repo_root), exit_code=0, summary="lake update ok")

    def run_lake_build(self, repo_root: Path, target: str | None = None) -> ExternalCommandResult:
        return ExternalCommandResult(
            ok=True,
            command=["lake", "build"] + ([target] if target else []),
            cwd=str(repo_root),
            exit_code=0,
            summary="lake build ok",
        )

    def run_minimal_import_check(self, repo_root: Path, module: str) -> LeanCheckSummaryView:
        return LeanCheckSummaryView(ok=True, module=module, command=["lean"], summary=f"import {module} ok")

    def summarize_command_result(self, result: ExternalCommandResult):
        from lean_constellation.services.external_clients import LakeCommandSummaryView

        return LakeCommandSummaryView(
            ok=result.ok,
            command=result.command,
            summary=result.summary or "",
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            stderr_excerpt=result.stderr_excerpt,
        )


def _runtime(tmp_path: Path, provider: _ScriptedMcpProvider | None = None):
    runtime = create_app_runtime_services(
        runtime_root=tmp_path / ".agent_runtime",
        external_overrides={"lake": _FakeLakeClient()},
    )
    if provider is not None:
        runtime.ark.agent_service.providers["codex"] = provider
    return runtime


def _external_takeover_runtime(tmp_path: Path, *, extra_agent_type_specs=None, step_type_overrides=None):
    runtime_root = tmp_path / ".agent_runtime"
    runtime = create_app_runtime_services(
        runtime_root=runtime_root,
        external_overrides={"lake": _FakeLakeClient()},
        extra_agent_type_specs=extra_agent_type_specs,
        step_type_overrides=step_type_overrides,
        agent_providers=build_external_takeover_agent_providers(
            runtime_root,
            poll_interval_s=0.01,
            default_timeout_s=10,
        ),
    )
    return runtime, runtime_root


def _install_provider(runtime, provider: _ScriptedMcpProvider) -> None:
    provider.runtime = runtime
    runtime.ark.agent_service.providers["codex"] = provider


def _create_homes(runtime, *agent_types: str) -> None:
    for agent_type in agent_types:
        runtime.ark.agent_service.home_service.create_home(HomeCreateSpec(cli_type="codex", home_id=agent_type))


def _write_bootstrap_preparation(runtime, repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
    written = runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Provide a small dependency.",
            source_corpus_mode=SourceCorpusMode.PREPARE,
            requirement_refs=[{"consumer_repo": "Consumer", "requirement_name": "need_provider"}],
        ),
    )
    assert written.ok, written.issues


def _prepare_content_repo(runtime, repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
    written = runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Formalize core facts.",
            source_corpus_mode=SourceCorpusMode.EXISTING,
            source_corpus_relpath=".lean_constellation/source",
            interface_inputs=[],
        ),
    )
    assert written.ok, written.issues
    (repo_root / ".lean_constellation" / "source").mkdir(parents=True, exist_ok=True)
    assert runtime.node.ensure_native_root_main_contract(repo_root).ok
    created = runtime.node.create_content_node(
        repo_root,
        path="Main.Core",
        goal="Core goal.",
        boundary="Core boundary.",
        objective="Prove core facts.",
        success_criteria="Core facts are complete.",
    )
    assert created.ok, created.issues


def _prepare_native_full_path_repo(runtime, repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
    written = runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Formalize one compactness fact.",
            source_corpus_mode=SourceCorpusMode.EXISTING,
            source_corpus_relpath=".lean_constellation/source",
            interface_inputs=[],
            allow_interface_supplement=False,
        ),
    )
    assert written.ok
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "README.md").write_text(
        "Compactness fact\n"
        "Source provenance: local semireal native full-path fixture.\n"
        "Reading order: read this README.md entry as the main material.\n"
        "Every finite cover has a finite subcover in this toy source.\n"
        "The implementation path intentionally stops after one content task terminal result.\n"
        "Known gaps and extraction limits: no missing source sections are known.\n",
        encoding="utf-8",
    )
    initialized = runtime.repo_workspace.initialize_repo_as_native(repo_root, project_name=repo_root.name)
    assert initialized.ok, initialized.issues


def _write_minimal_lake_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "lakefile.toml").write_text(
        'name = "LeanConstellationTiny"\n'
        'version = "0.1.0"\n'
        'defaultTargets = ["Main"]\n\n'
        '[[lean_lib]]\n'
        'name = "Main"\n',
        encoding="utf-8",
    )
    (repo_root / "Main.lean").write_text(
        "import Main.Topic.Core.Prelude\n",
        encoding="utf-8",
    )
    prelude = repo_root / "Main" / "Topic" / "Core" / "Prelude.lean"
    prelude.parent.mkdir(parents=True, exist_ok=True)
    prelude.write_text("-- Tiny prelude for real Lean gate e2e.\n", encoding="utf-8")


def _schedule_until(runtime, predicate, *, limit: int = 80, step_timeout_s: float = 20) -> None:  # noqa: ANN001
    for _ in range(limit):
        if predicate():
            return
        runtime.ark.schedule_service.rebuild_candidate_queues()
        tick = runtime.ark.schedule_service.schedule_ready()
        for step_id in tick.started_step_ids:
            runtime.ark.step_service.wait_step(step_id, timeout_s=step_timeout_s)
        if predicate():
            return
    raise AssertionError("scheduler did not reach expected state")


def _schedule_external_until(runtime, controller: _ExternalTakeoverMcpController, predicate, *, limit: int = 80) -> None:  # noqa: ANN001
    for _ in range(limit):
        if predicate():
            return
        runtime.ark.schedule_service.rebuild_candidate_queues()
        tick = runtime.ark.schedule_service.schedule_ready()
        if tick.started_step_ids:
            controller.drain_pending(wait_s=2)
        else:
            controller.drain_pending()
        for step_id in tick.started_step_ids:
            runtime.ark.step_service.wait_step(step_id, timeout_s=10)
        controller.drain_pending()
        if predicate():
            return
    raise AssertionError("external takeover scheduler did not reach expected state")


def _start_resource_curation_flow(runtime, repo_root: Path, *, target_kind: str, target: str) -> str:  # noqa: ANN001
    return runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="resource_curation",
            scope_id=f"repo:{repo_root.name}",
            params={
                "repo_key": repo_root.name,
                "repo_root": str(repo_root),
                "target_kind": target_kind,
                "target": target,
                "requested_by": "coordinator",
                "context_summary": "External takeover test resource request.",
            },
        )
    )


def _run_external_step(runtime, controller: _ExternalTakeoverMcpController, step_id: str) -> None:  # noqa: ANN001
    runtime.ark.step_service.start_step(step_id)
    controller.drain_pending(wait_s=2)
    runtime.ark.step_service.wait_step(step_id, timeout_s=10)
    controller.drain_pending()


def _wait_for_admin_pending_handoff(admin: LeanAdminApi):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        pending = admin.list_external_takeovers(status="pending")
        assert pending.ok, pending.issues
        if pending.value:
            return pending.value[0]
        sleep(0.01)
    raise TimeoutError("external takeover handoff was not visible through Admin API")


def _admin_run_external_submit(admin: LeanAdminApi, step_id: str, tool_name: str, arguments: dict[str, Any]) -> None:
    started = admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=False))
    assert started.ok, started.issues
    handoff = _wait_for_admin_pending_handoff(admin)
    called = admin.call_external_takeover_tool(
        ExternalTakeoverToolCallInput(
            handoff_id=handoff.handoff_id,
            view_kind="submit",
            tool_name=tool_name,
            arguments=arguments,
        )
    )
    assert called.ok and called.value is not None
    assert called.value.ok is True
    completed = admin.complete_external_takeover(
        ExternalTakeoverCompleteInput(
            handoff_id=handoff.handoff_id,
            final_response=f"Admin external takeover called {tool_name}.",
            thread_id=f"admin-thread-{handoff.handoff_id}",
        )
    )
    assert completed.ok, completed.issues
    waited = admin.wait_step(AdminStepStartInput(step_id=step_id, timeout_s=10))
    assert waited.ok, waited.issues


def _write_resource_draft_files(draft_root: Path, source_text: str = "resource text\n") -> None:
    (draft_root / "README.md").write_text("# Curated resource\n\nCreated by external takeover test.\n", encoding="utf-8")
    (draft_root / "original" / "raw.txt").write_text(source_text, encoding="utf-8")
    (draft_root / "normalized" / "main.md").write_text(source_text, encoding="utf-8")


def test_semireal_repo_format_discovery_mcp_submit_through_scheduler(tmp_path: Path) -> None:
    provider = _ScriptedMcpProvider(
        None,
        {
            "RepoFormatDiscoveryAgent": [
                ("submit_native_repo_choice", {"summary": "Use native.", "searched_targets": [], "rejected_candidates": []}),
            ],
        },
    )
    runtime = _runtime(tmp_path)
    _install_provider(runtime, provider)
    _create_homes(runtime, "RepoFormatDiscoveryAgent")
    workspace = tmp_path / "workspace"
    repo_root = workspace / "Provider"
    _write_bootstrap_preparation(runtime, repo_root)
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="requirement_group_repo_bootstrap",
            scope_id="repo:Provider",
            params={
                "target_repo": "Provider",
                "repo_root": str(repo_root),
                "workspace_root": str(workspace),
                "requirement_refs": ["Consumer:need_provider"],
            },
        )
    )

    _schedule_until(runtime, lambda: runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.COMPLETED)

    flow = runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result is not None
    assert flow.result.outcome == "native_bootstrap_ready"
    assert provider.calls[0]["tool_name"] == "submit_native_repo_choice"
    agent_steps = runtime.ark.flow_service.list_steps(flow_id=flow_id, step_type="repo_format_discovery_agent_step")
    assert agent_steps[0].submission.submission_type == "repo_format_native_choice"


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_outcome", "expected_submission"),
    [
        (
            "submit_native_repo_choice",
            {"summary": "Use native via external takeover.", "searched_targets": ["strict fixture"], "rejected_candidates": []},
            "native_bootstrap_ready",
            "repo_format_native_choice",
        ),
        (
            "submit_adapter_repo_choice",
            {
                "git_url": "https://github.com/example/upstream.git",
                "revision": "main",
                "subdir": "lean",
                "package_name": "upstream",
                "likely_import_module": "upstream",
                "evidence_summary": "Remote probe fixture found lakefile.lean.",
                "known_risks": [],
            },
            "adapter_bootstrap_ready",
            "repo_format_adapter_choice",
        ),
    ],
)
def test_semireal_external_takeover_repo_format_native_and_adapter(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, Any],
    expected_outcome: str,
    expected_submission: str,
) -> None:
    runtime, runtime_root = _external_takeover_runtime(tmp_path)
    controller = _ExternalTakeoverMcpController(
        runtime,
        runtime_root,
        {"RepoFormatDiscoveryAgent": [(tool_name, arguments)]},
    )
    _create_homes(runtime, "RepoFormatDiscoveryAgent")
    workspace = tmp_path / "workspace"
    repo_name = "AdapterProvider" if expected_submission == "repo_format_adapter_choice" else "Provider"
    repo_root = workspace / repo_name
    _write_bootstrap_preparation(runtime, repo_root)
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="requirement_group_repo_bootstrap",
            scope_id=f"repo:{repo_name}",
            params={
                "target_repo": repo_name,
                "repo_root": str(repo_root),
                "workspace_root": str(workspace),
                "requirement_refs": ["Consumer:need_provider"],
            },
        )
    )

    _schedule_external_until(runtime, controller, lambda: runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.COMPLETED)

    flow = runtime.ark.flow_service.get_flow(flow_id)
    agent_steps = runtime.ark.flow_service.list_steps(flow_id=flow_id, step_type="repo_format_discovery_agent_step")
    assert flow.result is not None
    assert flow.result.outcome == expected_outcome
    assert agent_steps[0].submission.submission_type == expected_submission
    assert controller.calls[0]["tool_name"] == tool_name
    assert controller.calls[0]["view_key"] == "repo_format_discovery_submit"


def test_semireal_external_takeover_resource_request_callback(tmp_path: Path) -> None:
    runtime, runtime_root = _external_takeover_runtime(tmp_path)
    controller = _ExternalTakeoverMcpController(
        runtime,
        runtime_root,
        {
            "CoordinatorAgent": [
                (
                    "submit_resource_request",
                    {
                        "summary": "Curate arxiv source through external takeover.",
                        "target_kind": "arxiv",
                        "target": "2501.12345",
                        "context_summary": "Need a narrow source.",
                    },
                ),
                (
                    "submit_repo_requirement",
                    {
                        "summary": "Wait for provider after callback.",
                        "name": "provider_req",
                        "target_repo": "WeightedSieve",
                        "reason": "Need reusable provider.",
                    },
                ),
            ],
            "ResourceCuratorAgent": [
                (
                    "submit_resource_rejected",
                    {
                        "reason": "No useful source found.",
                        "target_kind": "arxiv",
                        "target": "2501.12345",
                        "details": ["Search exhausted."],
                    },
                )
            ],
        },
    )
    _create_homes(runtime, "CoordinatorAgent", "ResourceCuratorAgent")
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id="repo:Repo",
            params={"repo_key": "Repo", "repo_root": str(repo_root), "start_mode": "admin_start", "start_reason": "external takeover e2e"},
        )
    )

    _schedule_external_until(
        runtime,
        controller,
        lambda: runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.WAITING
        and runtime.ark.flow_service.get_flow(flow_id).state.position.phase == "waiting_requirement",
    )

    flow = runtime.ark.flow_service.get_flow(flow_id)
    coordinator_calls = [call for call in controller.calls if call["agent_type"] == "CoordinatorAgent"]
    assert flow.state.position.phase == "waiting_requirement"
    assert flow.state.waiting_requirement_name == "provider_req"
    assert len(coordinator_calls) == 2
    assert "The child workflows you requested have finished." in coordinator_calls[1]["prompt"]
    assert "No useful source found." in coordinator_calls[1]["prompt"]


def test_semireal_external_takeover_resource_local_created(tmp_path: Path) -> None:
    runtime, runtime_root = _external_takeover_runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    local_source = repo_root / "source.md"
    local_source.write_text("local source material\n", encoding="utf-8")
    _create_homes(runtime, "ResourceCuratorAgent")
    flow_id = _start_resource_curation_flow(runtime, repo_root, target_kind="local_file", target=str(local_source))
    preflight_step_id = runtime.ark.flow_service.advance_flow(flow_id)
    assert preflight_step_id is not None
    runtime.ark.step_service.run_step(preflight_step_id)
    active_draft_id = runtime.ark.flow_service.get_flow(flow_id).state.active_resource_draft_key
    assert active_draft_id is not None
    active_draft = runtime.material.get_resource_draft(repo_root, draft_id=active_draft_id)
    assert active_draft.ok and active_draft.value is not None, active_draft.issues
    _write_resource_draft_files(Path(active_draft.value.draft_root), local_source.read_text(encoding="utf-8"))
    controller = _ExternalTakeoverMcpController(
        runtime,
        runtime_root,
        {
            "ResourceCuratorAgent": [
                (
                    "submit_local_resource_created",
                    {
                        "summary": "Created local resource through external takeover.",
                        "target_kind": "local_file",
                        "target": str(local_source),
                        "draft_id": active_draft_id,
                    },
                )
            ]
        },
    )

    _schedule_external_until(runtime, controller, lambda: runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.COMPLETED)

    flow = runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result is not None
    assert flow.result.outcome == "local_resource_created"
    assert flow.result.resource_key is not None
    assert controller.calls[0]["tool_name"] == "submit_local_resource_created"


def test_semireal_external_takeover_resource_external_repo_required(tmp_path: Path) -> None:
    runtime, runtime_root = _external_takeover_runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    controller = _ExternalTakeoverMcpController(
        runtime,
        runtime_root,
        {
            "ResourceCuratorAgent": [
                (
                    "submit_external_repo_required",
                    {
                        "reason": "The target is a full upstream project.",
                        "target_kind": "web",
                        "target": "https://example.com/upstream",
                        "source_description": "A web-accessible upstream project.",
                        "suggested_repo_name": "upstream_project",
                        "required_interfaces_hint": "Expose the main reusable theorem.",
                    },
                )
            ]
        },
    )
    _create_homes(runtime, "ResourceCuratorAgent")
    flow_id = _start_resource_curation_flow(runtime, repo_root, target_kind="web", target="https://example.com/upstream")

    _schedule_external_until(runtime, controller, lambda: runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.COMPLETED)

    flow = runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result is not None
    assert flow.result.outcome == "external_repo_required"
    assert flow.result.external_repo is not None
    assert flow.result.external_repo.suggested_repo_name == "upstream_project"


def test_semireal_controlled_fresh_same_type_external_takeover_step_binding_only(tmp_path: Path) -> None:
    runtime, runtime_root = _external_takeover_runtime(
        tmp_path,
        step_type_overrides=CONTROLLED_BUSINESS_AGENT_STEP_OVERRIDES,
    )
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    controller = _ExternalTakeoverMcpController(
        runtime,
        runtime_root,
        {
            "ResourceCuratorAgent": [
                (
                    "submit_resource_rejected",
                    {
                        "reason": "Stop after controlled fresh same type.",
                        "target_kind": "web",
                        "target": "https://example.com/source",
                    },
                )
            ]
        },
    )
    _create_homes(runtime, "ResourceCuratorAgent")
    flow_id = _start_resource_curation_flow(runtime, repo_root, target_kind="web", target="https://example.com/source")
    preflight_step_id = runtime.ark.flow_service.advance_flow(flow_id)
    assert preflight_step_id is not None
    runtime.ark.step_service.run_step(preflight_step_id)
    agent_step_id = runtime.ark.flow_service.advance_flow(flow_id)
    assert agent_step_id is not None
    runtime.ark.flow_service.store.update_step_record(
        agent_step_id,
        lambda step: step.state.variables.__setitem__(
            CONTROLLED_AGENT_OVERRIDE_ALIASES[0],
            {"strategy": "fresh_same_agent_type"},
        ),
    )

    _run_external_step(runtime, controller, agent_step_id)

    step = runtime.ark.flow_service.get_step(agent_step_id)
    flow = runtime.ark.flow_service.get_flow(flow_id)
    agent_id = step.agent_bindings.get("resource_curator")
    assert flow.status is FlowStatus.COMPLETED
    assert agent_id is not None
    assert flow.agent_bindings.get("resource_curator") is None
    assert runtime.ark.agent_service.get_agent(agent_id).agent_type == "ResourceCuratorAgent"


def test_semireal_controlled_test_agent_type_external_takeover_inherits_tool_view(tmp_path: Path) -> None:
    controlled = derive_agent_type_spec(
        base_agent_type="ResourceCuratorAgent",
        agent_type="ResourceCuratorControlledTestAgent",
    )
    runtime, runtime_root = _external_takeover_runtime(
        tmp_path,
        extra_agent_type_specs=[controlled],
        step_type_overrides=CONTROLLED_BUSINESS_AGENT_STEP_OVERRIDES,
    )
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    controller = _ExternalTakeoverMcpController(
        runtime,
        runtime_root,
        {
            "ResourceCuratorControlledTestAgent": [
                (
                    "submit_resource_rejected",
                    {
                        "reason": "Stop after controlled test type.",
                        "target_kind": "web",
                        "target": "https://example.com/test-agent",
                    },
                )
            ]
        },
    )
    _create_homes(runtime, "ResourceCuratorControlledTestAgent")
    flow_id = _start_resource_curation_flow(runtime, repo_root, target_kind="web", target="https://example.com/test-agent")
    preflight_step_id = runtime.ark.flow_service.advance_flow(flow_id)
    assert preflight_step_id is not None
    runtime.ark.step_service.run_step(preflight_step_id)
    agent_step_id = runtime.ark.flow_service.advance_flow(flow_id)
    assert agent_step_id is not None
    runtime.ark.flow_service.store.update_step_record(
        agent_step_id,
        lambda step: step.state.variables.__setitem__(
            CONTROLLED_AGENT_OVERRIDE_ALIASES[0],
            {
                "strategy": "fresh_test_agent_type",
                "agent_type_override": "ResourceCuratorControlledTestAgent",
            },
        ),
    )

    _run_external_step(runtime, controller, agent_step_id)

    flow = runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert controller.calls[0]["agent_type"] == "ResourceCuratorControlledTestAgent"
    assert controller.calls[0]["view_key"] == "resource_curator_submit"


def test_semireal_admin_test_control_reaches_repo_format_agent_step_without_starting(tmp_path: Path) -> None:
    runtime = create_test_control_runtime_services(
        runtime_root=tmp_path / ".agent_runtime",
        external_overrides={"lake": _FakeLakeClient()},
    )
    admin = LeanAdminApi(runtime)
    workspace = tmp_path / "workspace"
    repo_root = workspace / "Provider"
    _write_bootstrap_preparation(runtime, repo_root)
    started = admin.start_arbitrary_flow(
        StartFlowInput(
            flow_type="requirement_group_repo_bootstrap",
            scope_id="repo:Provider",
            enqueue=False,
            params={
                "target_repo": "Provider",
                "repo_root": str(repo_root),
                "workspace_root": str(workspace),
                "requirement_refs": ["Consumer:need_provider"],
            },
        )
    )
    assert started.ok and started.value is not None

    validate = admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=started.value.flow_id))
    assert validate.ok and validate.value is not None and validate.value.created_step_id is not None
    validate_step = runtime.ark.step_service.store.get_step(validate.value.created_step_id)
    assert validate_step.step_type == "validate_bootstrap_input_step"
    validate_run = admin.start_step_once(AdminStepStartInput(step_id=validate.value.created_step_id, wait=True, timeout_s=5))
    assert validate_run.ok, validate_run.issues

    agent_step = admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=started.value.flow_id))
    assert agent_step.ok and agent_step.value is not None and agent_step.value.created_step_id is not None
    step = runtime.ark.step_service.store.get_step(agent_step.value.created_step_id)
    assert step.step_type == "repo_format_discovery_agent_step"
    assert step.status.value == "created"
    assert runtime.ark.step_service.list_running_steps() == []
    control_view = admin.get_agent_step_control_view(step.step_id)
    assert control_view.ok and control_view.value is not None
    assert control_view.value.agent_type == "RepoFormatDiscoveryAgent"
    assert control_view.value.tool_view_key == "repo_format_discovery"
    assert runtime.ark.pause_controller.is_paused()


def test_semireal_admin_checkpoint_restore_native_and_adapter_repo_format_branches(tmp_path: Path) -> None:
    runtime = create_test_control_runtime_services(
        runtime_root=tmp_path / ".agent_runtime",
        external_overrides={"lake": _FakeLakeClient()},
    )
    admin = LeanAdminApi(runtime)
    runtime.ark.agent_service.home_service.create_home(
        HomeCreateSpec(cli_type="external_takeover", home_id="RepoFormatDiscoveryControlledTestAgent")
    )
    workspace = tmp_path / "workspace"
    repo_root = workspace / "Provider"
    _write_bootstrap_preparation(runtime, repo_root)
    started = admin.start_arbitrary_flow(
        StartFlowInput(
            flow_type="requirement_group_repo_bootstrap",
            scope_id="repo:Provider",
            enqueue=False,
            params={
                "target_repo": "Provider",
                "repo_root": str(repo_root),
                "workspace_root": str(workspace),
                "requirement_refs": ["Consumer:need_provider"],
            },
        )
    )
    assert started.ok and started.value is not None

    validate = admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=started.value.flow_id))
    assert validate.ok and validate.value is not None and validate.value.created_step_id is not None
    validate_run = admin.start_step_once(AdminStepStartInput(step_id=validate.value.created_step_id, wait=True, timeout_s=5))
    assert validate_run.ok, validate_run.issues
    agent_step = admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=started.value.flow_id))
    assert agent_step.ok and agent_step.value is not None and agent_step.value.created_step_id is not None
    agent_step_id = agent_step.value.created_step_id

    checkpoint = admin.create_manual_test_checkpoint(
        ManualCheckpointInput(
            repo_root=repo_root,
            scope_ids=["repo:Provider"],
            label="before_repo_format_branch",
        )
    )
    assert checkpoint.ok and checkpoint.value is not None

    native_override = admin.set_agent_step_override(
        SetAgentStepOverrideInput(
            step_id=agent_step_id,
            override=ControlledAgentOverrideSpec(
                strategy="fresh_test_agent_type",
                agent_type_override="RepoFormatDiscoveryControlledTestAgent",
                cli_type_override="external_takeover",
            ),
        )
    )
    assert native_override.ok, native_override.issues
    _admin_run_external_submit(
        admin,
        agent_step_id,
        "submit_native_repo_choice",
        {
            "summary": "Use native branch.",
            "searched_targets": ["snapshot fixture"],
            "rejected_candidates": [],
        },
    )
    native_apply = admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=started.value.flow_id))
    assert native_apply.ok and native_apply.value is not None and native_apply.value.created_step_id is not None
    native_apply_run = admin.start_step_once(AdminStepStartInput(step_id=native_apply.value.created_step_id, wait=True, timeout_s=5))
    assert native_apply_run.ok, native_apply_run.issues
    native_flow = runtime.ark.flow_service.get_flow(started.value.flow_id)
    assert native_flow.status is FlowStatus.COMPLETED
    assert native_flow.result is not None
    assert native_flow.result.outcome == "native_bootstrap_ready"

    restored = admin.restore_snapshot(
        SnapshotRestoreInput(
            repo_root=repo_root,
            snapshot_id=checkpoint.value.snapshot_id,
            leave_runtime_paused=True,
            prune_extra_files=True,
        )
    )
    assert restored.ok, restored.issues
    assert runtime.ark.pause_controller.is_paused()
    restored_step = runtime.ark.step_service.store.get_step(agent_step_id)
    assert restored_step.status.value == "created"
    assert "test_override_spec" not in restored_step.state.variables

    adapter_override = admin.set_agent_step_override(
        SetAgentStepOverrideInput(
            step_id=agent_step_id,
            override=ControlledAgentOverrideSpec(
                strategy="fresh_test_agent_type",
                agent_type_override="RepoFormatDiscoveryControlledTestAgent",
                cli_type_override="external_takeover",
            ),
        )
    )
    assert adapter_override.ok, adapter_override.issues
    _admin_run_external_submit(
        admin,
        agent_step_id,
        "submit_adapter_repo_choice",
        {
            "git_url": "https://github.com/leanprover-community/mathlib4",
            "revision": "main",
            "package_name": "mathlib",
            "likely_import_module": "Mathlib",
            "evidence_summary": "Remote probe fixture found mathlib4 lakefile.",
            "known_risks": ["Exact declaration coverage not verified in this test."],
        },
    )
    adapter_apply = admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=started.value.flow_id))
    assert adapter_apply.ok and adapter_apply.value is not None and adapter_apply.value.created_step_id is not None
    adapter_apply_run = admin.start_step_once(AdminStepStartInput(step_id=adapter_apply.value.created_step_id, wait=True, timeout_s=5))
    assert adapter_apply_run.ok, adapter_apply_run.issues
    adapter_flow = runtime.ark.flow_service.get_flow(started.value.flow_id)
    assert adapter_flow.status is FlowStatus.COMPLETED
    assert adapter_flow.result is not None
    assert adapter_flow.result.outcome == "adapter_bootstrap_ready", adapter_flow.result.model_dump(mode="json")


def test_semireal_admin_test_control_external_takeover_no_wait_workflow(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".agent_runtime"
    runtime = create_test_control_runtime_services(
        runtime_root=runtime_root,
        external_overrides={"lake": _FakeLakeClient()},
    )
    admin = LeanAdminApi(runtime)
    runtime.ark.agent_service.home_service.create_home(
        HomeCreateSpec(cli_type="external_takeover", home_id="ResourceCuratorControlledTestAgent")
    )
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    flow_id = _start_resource_curation_flow(runtime, repo_root, target_kind="web", target="https://example.com/admin-no-wait")

    preflight = admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=flow_id))
    assert preflight.ok and preflight.value is not None
    assert preflight.value.created_step_id is not None
    preflight_run = admin.start_step_once(AdminStepStartInput(step_id=preflight.value.created_step_id, wait=True, timeout_s=5))
    assert preflight_run.ok, preflight_run.issues

    agent_step = admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=flow_id))
    assert agent_step.ok and agent_step.value is not None
    assert agent_step.value.created_step_id is not None
    override = admin.set_agent_step_override(
        SetAgentStepOverrideInput(
            step_id=agent_step.value.created_step_id,
            override=ControlledAgentOverrideSpec(
                strategy="fresh_test_agent_type",
                agent_type_override="ResourceCuratorControlledTestAgent",
                cli_type_override="external_takeover",
                prompt_overlay="Call submit_resource_rejected exactly once.",
            ),
        )
    )
    assert override.ok and override.value is not None
    assert override.value.tool_view_key == "resource_curator"

    started = admin.start_step_once(AdminStepStartInput(step_id=agent_step.value.created_step_id, wait=False))
    assert started.ok and started.value is not None
    assert started.value.status in {"created", "running"}
    handoff = _wait_for_admin_pending_handoff(admin)
    runtime_view = admin.get_test_control_runtime_view()
    assert runtime_view.ok and runtime_view.value is not None
    assert [item.handoff_id for item in runtime_view.value.pending_external_handoffs] == [handoff.handoff_id]

    listed_tools = admin.list_external_takeover_tools(
        ExternalTakeoverToolListInput(handoff_id=handoff.handoff_id, view_kind="submit")
    )
    assert listed_tools.ok and listed_tools.value is not None
    assert "submit_resource_rejected" in {tool.name for tool in listed_tools.value}

    called = admin.call_external_takeover_tool(
        ExternalTakeoverToolCallInput(
            handoff_id=handoff.handoff_id,
            view_kind="submit",
            tool_name="submit_resource_rejected",
            arguments={
                "reason": "Stop after Admin no-wait external takeover workflow.",
                "target_kind": "web",
                "target": "https://example.com/admin-no-wait",
            },
        )
    )
    assert called.ok and called.value is not None
    assert called.value.ok is True
    completed = admin.complete_external_takeover(
        ExternalTakeoverCompleteInput(
            handoff_id=handoff.handoff_id,
            final_response="Admin external takeover workflow completed.",
            thread_id=f"admin-thread-{handoff.handoff_id}",
        )
    )
    assert completed.ok and completed.value is not None
    assert completed.value.status == "completed"

    waited = admin.wait_step(AdminStepStartInput(step_id=agent_step.value.created_step_id, timeout_s=10))
    assert waited.ok and waited.value is not None
    flow = runtime.ark.flow_service.get_flow(flow_id)
    step = runtime.ark.step_service.store.get_step(agent_step.value.created_step_id)
    assert step.status.value == "completed"
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    assert flow.result.outcome == "rejected"
    assert step.state.variables["controlled_agent_record"]["agent_type_override"] == "ResourceCuratorControlledTestAgent"
    assert runtime.ark.pause_controller.is_paused()


def test_semireal_admin_manual_checkpoint_restore_reuses_agent_step_branch(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".agent_runtime"
    runtime = create_test_control_runtime_services(
        runtime_root=runtime_root,
        external_overrides={"lake": _FakeLakeClient()},
    )
    admin = LeanAdminApi(runtime)
    runtime.ark.agent_service.home_service.create_home(
        HomeCreateSpec(cli_type="external_takeover", home_id="ResourceCuratorControlledTestAgent")
    )
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    (repo_root / "README.md").write_text("repo for checkpoint branch test\n", encoding="utf-8")
    flow_id = _start_resource_curation_flow(runtime, repo_root, target_kind="web", target="https://example.com/branch")

    preflight = admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=flow_id))
    assert preflight.ok and preflight.value is not None and preflight.value.created_step_id is not None
    preflight_run = admin.start_step_once(AdminStepStartInput(step_id=preflight.value.created_step_id, wait=True, timeout_s=5))
    assert preflight_run.ok, preflight_run.issues
    active_draft_id = runtime.ark.flow_service.get_flow(flow_id).state.active_resource_draft_key
    assert active_draft_id is not None
    active_draft = runtime.material.get_resource_draft(repo_root, draft_id=active_draft_id)
    assert active_draft.ok and active_draft.value is not None, active_draft.issues
    _write_resource_draft_files(Path(active_draft.value.draft_root), "branch resource text\n")
    agent_step = admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=flow_id))
    assert agent_step.ok and agent_step.value is not None and agent_step.value.created_step_id is not None
    agent_step_id = agent_step.value.created_step_id

    checkpoint = admin.create_manual_test_checkpoint(
        ManualCheckpointInput(
            repo_root=repo_root,
            scope_ids=["repo:Repo"],
            label="before_resource_curator_branch",
        )
    )
    assert checkpoint.ok and checkpoint.value is not None

    first_override = admin.set_agent_step_override(
        SetAgentStepOverrideInput(
            step_id=agent_step_id,
            override=ControlledAgentOverrideSpec(
                strategy="fresh_test_agent_type",
                agent_type_override="ResourceCuratorControlledTestAgent",
                cli_type_override="external_takeover",
            ),
        )
    )
    assert first_override.ok, first_override.issues
    _admin_run_external_submit(
        admin,
        agent_step_id,
        "submit_resource_rejected",
        {
            "reason": "First branch rejects the resource.",
            "target_kind": "web",
            "target": "https://example.com/branch",
        },
    )
    first_flow = runtime.ark.flow_service.get_flow(flow_id)
    assert first_flow.status is FlowStatus.COMPLETED
    assert first_flow.result is not None
    assert first_flow.result.outcome == "rejected"

    restored = admin.restore_snapshot(
        SnapshotRestoreInput(
            repo_root=repo_root,
            snapshot_id=checkpoint.value.snapshot_id,
            leave_runtime_paused=True,
            prune_extra_files=True,
        )
    )
    assert restored.ok, restored.issues
    assert runtime.ark.pause_controller.is_paused()
    restored_flow = runtime.ark.flow_service.get_flow(flow_id)
    restored_step = runtime.ark.step_service.store.get_step(agent_step_id)
    assert restored_flow.current_step_id == agent_step_id
    assert restored_step.status.value == "created"
    assert "test_override_spec" not in restored_step.state.variables

    second_override = admin.set_agent_step_override(
        SetAgentStepOverrideInput(
            step_id=agent_step_id,
            override=ControlledAgentOverrideSpec(
                strategy="fresh_test_agent_type",
                agent_type_override="ResourceCuratorControlledTestAgent",
                cli_type_override="external_takeover",
            ),
        )
    )
    assert second_override.ok, second_override.issues
    _admin_run_external_submit(
        admin,
        agent_step_id,
        "submit_external_repo_required",
        {
            "reason": "Second branch requires an upstream repo.",
            "target_kind": "web",
            "target": "https://example.com/branch",
            "source_description": "An upstream web project.",
            "suggested_repo_name": "branch_upstream",
            "required_interfaces_hint": "Expose the reusable theorem.",
        },
    )
    second_flow = runtime.ark.flow_service.get_flow(flow_id)
    assert second_flow.status is FlowStatus.COMPLETED
    assert second_flow.result is not None
    assert second_flow.result.outcome == "external_repo_required"
    assert second_flow.result.external_repo is not None
    assert second_flow.result.external_repo.suggested_repo_name == "branch_upstream"

    restored_again = admin.restore_snapshot(
        SnapshotRestoreInput(
            repo_root=repo_root,
            snapshot_id=checkpoint.value.snapshot_id,
            leave_runtime_paused=True,
            prune_extra_files=True,
        )
    )
    assert restored_again.ok, restored_again.issues
    third_override = admin.set_agent_step_override(
        SetAgentStepOverrideInput(
            step_id=agent_step_id,
            override=ControlledAgentOverrideSpec(
                strategy="fresh_test_agent_type",
                agent_type_override="ResourceCuratorControlledTestAgent",
                cli_type_override="external_takeover",
            ),
        )
    )
    assert third_override.ok, third_override.issues
    _admin_run_external_submit(
        admin,
        agent_step_id,
        "submit_local_resource_created",
        {
            "summary": "Third branch creates a local resource.",
            "target_kind": "web",
            "target": "https://example.com/branch",
            "draft_id": active_draft_id,
        },
    )
    third_flow = runtime.ark.flow_service.get_flow(flow_id)
    assert third_flow.status is FlowStatus.COMPLETED
    assert third_flow.result is not None
    assert third_flow.result.outcome == "local_resource_created"
    assert third_flow.result.resource_key is not None


def test_semireal_external_takeover_snapshot_restore_after_completion(tmp_path: Path) -> None:
    runtime, runtime_root = _external_takeover_runtime(tmp_path)
    controller = _ExternalTakeoverMcpController(
        runtime,
        runtime_root,
        {"RepoFormatDiscoveryAgent": [("submit_native_repo_choice", {"summary": "Use native.", "searched_targets": [], "rejected_candidates": []})]},
    )
    _create_homes(runtime, "RepoFormatDiscoveryAgent")
    workspace = tmp_path / "workspace"
    repo_root = workspace / "Provider"
    _write_bootstrap_preparation(runtime, repo_root)
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="requirement_group_repo_bootstrap",
            scope_id="repo:Provider",
            params={
                "target_repo": "Provider",
                "repo_root": str(repo_root),
                "workspace_root": str(workspace),
                "requirement_refs": ["Consumer:need_provider"],
            },
        )
    )
    _schedule_external_until(runtime, controller, lambda: runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.COMPLETED)
    admin = LeanAdminApi(runtime)

    created = admin.create_snapshot(SnapshotCreateInput(repo_root=repo_root, label="external takeover complete"))
    assert created.ok and created.value is not None
    restored = admin.restore_snapshot(
        SnapshotRestoreInput(repo_root=repo_root, snapshot_id=created.value.snapshot_id, dry_run=True)
    )

    assert restored.ok


def test_semireal_resource_request_dispatch_and_callback_prompt(tmp_path: Path) -> None:
    provider = _ScriptedMcpProvider(
        None,
        {
            "CoordinatorAgent": [
                (
                    "submit_resource_request",
                    {
                        "summary": "Curate arxiv source.",
                        "target_kind": "arxiv",
                        "target": "2501.12345",
                        "context_summary": "Need a narrow source.",
                    },
                ),
                (
                    "submit_repo_requirement",
                    {
                        "summary": "Wait for provider.",
                        "name": "provider_req",
                        "target_repo": "WeightedSieve",
                        "reason": "Need reusable provider.",
                    },
                ),
            ],
            "ResourceCuratorAgent": [
                (
                    "submit_resource_rejected",
                    {
                        "reason": "No useful source found.",
                        "target_kind": "arxiv",
                        "target": "2501.12345",
                        "details": ["Search exhausted."],
                    },
                )
            ],
        },
    )
    runtime = _runtime(tmp_path)
    _install_provider(runtime, provider)
    _create_homes(runtime, "CoordinatorAgent", "ResourceCuratorAgent")
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id="repo:Repo",
            params={"repo_key": "Repo", "repo_root": str(repo_root), "start_mode": "admin_start", "start_reason": "e2e"},
        )
    )

    _schedule_until(
        runtime,
        lambda: runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.WAITING
        and runtime.ark.flow_service.get_flow(flow_id).state.position.phase == "waiting_requirement",
    )

    flow = runtime.ark.flow_service.get_flow(flow_id)
    assert flow.state.position.phase == "waiting_requirement"
    assert flow.state.waiting_requirement_name == "provider_req"
    coordinator_calls = [call for call in provider.calls if call["agent_type"] == "CoordinatorAgent"]
    assert len(coordinator_calls) == 2
    assert "The child workflows you requested have finished." in coordinator_calls[1]["prompt"]
    assert "No useful source found." in coordinator_calls[1]["prompt"]


def test_semireal_content_plan_dispatch_preparation_flow_and_callback(tmp_path: Path) -> None:
    provider = _ScriptedMcpProvider(
        None,
        {
            "ContentPlanAgent": [
                (
                    "submit_content_preparation_recon",
                    {
                        "summary": "Dispatch node dependency recon.",
                        "recon_kind": "node_dir_dependency",
                        "objective": "Check visible node dependencies.",
                    },
                ),
                (
                    "submit_content_node_blocked",
                    {
                        "reason": "Stop after callback for e2e coverage.",
                    },
                ),
            ],
            "NodeDirDependencyReconAgent": [
                (
                    "submit_node_dir_dependency_recon_completed",
                    {
                        "summary": "Node deps found.",
                        "dependency_change_summary": "Added Main.Base.",
                        "checked_boundary_summary": "Checked current content node boundaries.",
                        "useful_findings": ["Main.Base"],
                        "unresolved_within_visible_boundaries": [],
                    },
                )
            ],
        },
    )
    runtime = _runtime(tmp_path)
    _install_provider(runtime, provider)
    _create_homes(runtime, "ContentPlanAgent", "NodeDirDependencyReconAgent")
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(runtime, repo_root)
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="content_node_task",
            scope_id="repo:Repo:node:Main.Core",
            params={
                "repo_key": "Repo",
                "repo_path": str(repo_root),
                "node_path": "Main.Core",
                "contract_version": 1,
                "task_mode": "run",
            },
        )
    )

    _schedule_until(runtime, lambda: runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.COMPLETED)

    flow = runtime.ark.flow_service.get_flow(flow_id)
    assert isinstance(flow.result, ContentNodeTaskResult)
    assert flow.result.outcome == "blocked"
    content_calls = [call for call in provider.calls if call["agent_type"] == "ContentPlanAgent"]
    assert len(content_calls) == 2
    assert "Node deps found." in content_calls[1]["prompt"]


def test_semireal_native_preparation_handoff_to_content_task_terminal_path(tmp_path: Path) -> None:
    provider = _ScriptedMcpProvider(
        None,
        {
            "SourceIndexBuilderAgent": [
                [
                    ("application", "set_source_index_overview", {"overview": "A compactness toy source and proof outline."}),
                    (
                        "application",
                        "create_source_block",
                        {
                            "parent_id": "root",
                            "kind": "statement",
                            "title": "Compactness toy fact",
                            "summary": "Main compactness statement and implementation note.",
                        },
                    ),
                    (
                        "application",
                        "add_source_block_ref",
                        {
                            "block_id": "b_0001",
                            "path": "README.md",
                            "start_line": 1,
                            "end_line": 3,
                            "role": "main",
                        },
                    ),
                    ("application", "mark_block_refs_done", {"block_id": "b_0001"}),
                    ("application", "mark_block_links_done", {"block_id": "b_0001"}),
                    ("application", "mark_block_completed", {"block_id": "b_0001"}),
                    (
                        "application",
                        "set_file_survey_status",
                        {"path": "README.md", "status": "surveyed", "summary": "Read in full."},
                    ),
                    ("application", "set_file_indexing_status", {"path": "README.md", "status": "indexed"}),
                    ("submit", "submit_source_index_builder_round", {"summary": "Source index is complete."}),
                ],
            ],
            "SourceIndexReviewerAgent": [
                (
                    "submit_source_index_review_round",
                    {"approved": True, "summary": "Source index approved.", "feedback": None},
                ),
            ],
            "CoordinatorAgent": [
                [
                    (
                        "application",
                        "create_content_node",
                        {
                            "path": "Main.Core",
                            "goal": "Formalize the compactness toy fact.",
                            "boundary": "Use the indexed source only.",
                            "objective": "Create a minimal content task terminal result.",
                            "success_criteria": "The content task reaches a terminal result.",
                        },
                    ),
                    (
                        "submit",
                        "submit_content_node_tasks",
                        {"summary": "Dispatch one content node.", "node_paths": ["Main.Core"], "task_mode": "run"},
                    ),
                ],
                (
                    "submit_repo_requirement",
                    {
                        "summary": "Stop after content callback for e2e coverage.",
                        "name": "post_content_followup",
                        "target_repo": "ProviderFollowup",
                        "reason": "Content task terminal callback observed.",
                    },
                ),
            ],
            "ContentPlanAgent": [
                (
                    "submit_content_node_blocked",
                    {
                        "reason": "Intentional terminal result for fake-small path e2e.",
                    },
                ),
            ],
        },
    )
    runtime = _runtime(tmp_path)
    _install_provider(runtime, provider)
    _create_homes(runtime, "SourceIndexBuilderAgent", "SourceIndexReviewerAgent", "CoordinatorAgent", "ContentPlanAgent")
    repo_root = tmp_path / "workspace" / "Provider"
    _prepare_native_full_path_repo(runtime, repo_root)
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_preparation",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(repo_root),
                "start_reason": "bootstrap",
                "run_spec": {
                    "run_objective": "Prepare the Provider repository for the MCP runtime e2e scenario.",
                    "target_proof_availability": "declared",
                    "work_mode": "declared_interface",
                    "source_scope": {"mode": "all"},
                    "index_policy": "auto",
                    "root_interface_policy": "auto",
                },
            },
        )
    )

    def reached_content_terminal_callback() -> bool:
        preparation = runtime.ark.flow_service.get_flow(flow_id)
        if preparation.status is not FlowStatus.COMPLETED:
            return False
        coordinators = runtime.ark.flow_service.list_flows(flow_type="native_repo_coordinator")
        if len(coordinators) != 1:
            return False
        coordinator = coordinators[0]
        return coordinator.status is FlowStatus.WAITING and coordinator.state.position.phase == "waiting_requirement"

    _schedule_until(runtime, reached_content_terminal_callback, limit=140)

    preparation = runtime.ark.flow_service.get_flow(flow_id)
    assert preparation.result is not None
    assert preparation.result.outcome == "handoff_dispatched"
    assert runtime.material.get_source_index(repo_root).value.status == "committed"

    coordinators = runtime.ark.flow_service.list_flows(flow_type="native_repo_coordinator")
    coordinator = coordinators[0]
    assert coordinator.state.position.phase == "waiting_requirement"
    assert coordinator.state.completed_content_task_count == 1
    content_flows = runtime.ark.flow_service.list_flows(flow_type="content_node_task")
    assert len(content_flows) == 1
    content = content_flows[0]
    assert content.status is FlowStatus.COMPLETED
    assert isinstance(content.result, ContentNodeTaskResult)
    assert content.result.outcome == "blocked"

    coordinator_calls = [call for call in provider.calls if call["agent_type"] == "CoordinatorAgent" and call["view_kind"] == "submit"]
    assert len(coordinator_calls) == 2
    assert "Intentional terminal result" in coordinator_calls[1]["prompt"]


def test_semireal_decl_stage_worker_submit_gate_through_mcp(tmp_path: Path) -> None:
    provider = _ScriptedMcpProvider(
        None,
        {
            "StatementNLWorkerAgent": [
                (
                    "submit_stage_worker_blocked",
                    {
                        "reason": "Need external lemma before continuing.",
                        "affected_decl_names": ["main_result"],
                    },
                )
            ],
        },
    )
    runtime = _runtime(tmp_path)
    _install_provider(runtime, provider)
    materialized = materialize_agent_home(runtime, "StatementNLWorkerAgent")
    assert materialized.ok and materialized.value is not None
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    setup_content_node(runtime, repo_root)
    strategy_id, round_id, round_index = create_round_with_decl(runtime, repo_root)
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="decl_graph_round",
            scope_id=f"repo:Repo:node:{NODE_PATH}",
            params={
                "repo_key": "Repo",
                "repo_path": str(repo_root),
                "node_path": NODE_PATH,
                "contract_version": 1,
                "strategy_id": strategy_id,
                "round_id": round_id,
                "round_index": round_index,
                "summary": "Run decl stage worker e2e.",
            },
        )
    )

    _schedule_until(runtime, lambda: runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.COMPLETED)

    flow = runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result is not None
    assert flow.result.outcome == "blocked"
    assert flow.result.terminal_reason.code == "worker_blocked"
    assert provider.calls[0]["tool_name"] == "submit_stage_worker_blocked"


def test_semireal_snapshot_restore_pauses_and_resume_continues_scheduler(tmp_path: Path) -> None:
    provider = _ScriptedMcpProvider(
        None,
        {
            "CoordinatorAgent": [
                (
                    "submit_repo_requirement",
                    {
                        "summary": "Wait for provider after restore.",
                        "name": "provider_req",
                        "target_repo": "WeightedSieve",
                        "reason": "Need provider.",
                    },
                )
            ],
        },
    )
    runtime = _runtime(tmp_path)
    _install_provider(runtime, provider)
    _create_homes(runtime, "CoordinatorAgent")
    repo_root = tmp_path / "workspace" / "Repo"
    assert initialize_repo_business_truth(runtime, repo_root).ok
    written = runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(goal="Prepare repo.", source_corpus_mode=SourceCorpusMode.PREPARE),
    )
    assert written.ok
    admin = LeanAdminApi(runtime)
    started = admin.start_arbitrary_flow(
        StartFlowInput(
            flow_type="native_repo_coordinator",
            scope_id="repo:Repo",
            params={"repo_key": "Repo", "repo_root": str(repo_root), "start_mode": "admin_start", "start_reason": "snapshot e2e"},
        )
    )
    assert started.ok and started.value is not None

    created = admin.create_snapshot(
        SnapshotCreateInput(repo_root=repo_root, checkpoint_kind="requirement_bootstrap_terminal", label="e2e")
    )
    assert created.ok and created.value is not None
    restored = admin.restore_snapshot(
        SnapshotRestoreInput(repo_root=repo_root, snapshot_id=created.value.snapshot_id, leave_runtime_paused=True)
    )
    assert restored.ok and restored.value is not None
    assert runtime.ark.pause_controller.is_paused() is True

    resumed = admin.resume_runtime()
    assert resumed.ok
    _schedule_until(runtime, lambda: runtime.ark.flow_service.get_flow(started.value.flow_id).status is FlowStatus.WAITING)

    flow = runtime.ark.flow_service.get_flow(started.value.flow_id)
    assert flow.state.position.phase == "waiting_requirement"
    assert provider.calls[0]["tool_name"] == "submit_repo_requirement"


def _require_real_codex() -> Path:
    if os.environ.get("LEAN_CONSTELLATION_RUN_REAL_CODEX") != "1":
        pytest.skip("Set LEAN_CONSTELLATION_RUN_REAL_CODEX=1 to run real Codex e2e tests.")
    if importlib.util.find_spec("openai_codex") is None:
        pytest.skip("openai_codex Python SDK is required for real Codex e2e tests.")
    if shutil.which("codex") is None:
        pytest.skip("codex CLI is required for real Codex e2e tests.")
    config_home = os.environ.get("LEAN_CONSTELLATION_CODEX_CONFIG_HOME")
    if not config_home:
        pytest.skip("Set LEAN_CONSTELLATION_CODEX_CONFIG_HOME to a Codex config directory.")
    home = Path(config_home).expanduser()
    if not (home / "config.toml").exists() or not (home / "auth.json").exists():
        pytest.skip("LEAN_CONSTELLATION_CODEX_CONFIG_HOME must contain config.toml and auth.json.")
    return home


def _write_noninteractive_codex_base_config(config_home: Path, tmp_path: Path) -> Path:
    """Copy Codex config for unattended real tests without approval review turns."""

    source = config_home / "config.toml"
    target = tmp_path / "codex_noninteractive_config.toml"
    blocked_prefixes = (
        "approval_policy",
        "approvals_reviewer",
        "notify",
    )
    lines: list[str] = []
    inserted_approval_policy = False
    for line in source.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if any(stripped.startswith(prefix) for prefix in blocked_prefixes):
            continue
        if stripped.startswith("[") and not inserted_approval_policy:
            lines.append('approval_policy = "never"')
            lines.append("")
            inserted_approval_policy = True
        if stripped.startswith("model_reasoning_effort"):
            lines.append('model_reasoning_effort = "low"')
            continue
        lines.append(line)
    if not inserted_approval_policy:
        lines.append("")
        lines.append('approval_policy = "never"')
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


@pytest.mark.real_codex
@pytest.mark.mcp_http
def test_real_codex_repo_format_discovery_submit_env_gated(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    config_home = _require_real_codex()
    base_config_path = _write_noninteractive_codex_base_config(config_home, tmp_path)
    runtime = create_app_runtime_services(
        runtime_root=tmp_path / ".agent_runtime",
        external_overrides={"lake": _FakeLakeClient()},
    )
    workspace = tmp_path / "workspace"
    repo_root = workspace / "Provider"
    _write_bootstrap_preparation(runtime, repo_root)
    http_server = start_runtime_mcp_http_server(runtime)
    request.addfinalizer(http_server.close)
    materialized = materialize_agent_home(
        runtime,
        "RepoFormatDiscoveryAgent",
        mcp_http_base_url=http_server.base_url,
        base_config_path=base_config_path,
        auth_json_path=config_home / "auth.json",
    )
    assert materialized.ok and materialized.value is not None
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="requirement_group_repo_bootstrap",
            scope_id="repo:Provider",
            params={
                "target_repo": "Provider",
                "repo_root": str(repo_root),
                "workspace_root": str(workspace),
                "requirement_refs": ["Consumer:need_provider"],
            },
        )
    )

    real_step_timeout = float(os.environ.get("LEAN_CONSTELLATION_REAL_CODEX_STEP_TIMEOUT", "300"))
    _schedule_until(
        runtime,
        lambda: runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.COMPLETED,
        limit=120,
        step_timeout_s=real_step_timeout,
    )

    flow = runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result is not None
    assert flow.result.outcome in {"native_bootstrap_ready", "adapter_bootstrap_ready"}


@pytest.mark.real_codex
@pytest.mark.mcp_http
def test_real_codex_controlled_test_agent_type_mcp_mount_env_gated(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    config_home = _require_real_codex()
    base_config_path = _write_noninteractive_codex_base_config(config_home, tmp_path)
    controlled = derive_agent_type_spec(
        base_agent_type="RepoFormatDiscoveryAgent",
        agent_type="RepoFormatDiscoveryControlledTestAgent",
    )
    specs = build_agent_type_specs(extra_specs=[controlled])
    runtime = create_app_runtime_services(
        runtime_root=tmp_path / ".agent_runtime",
        external_overrides={"lake": _FakeLakeClient()},
        agent_type_specs=specs,
        step_type_overrides=CONTROLLED_BUSINESS_AGENT_STEP_OVERRIDES,
    )
    workspace = tmp_path / "workspace"
    repo_root = workspace / "Provider"
    _write_bootstrap_preparation(runtime, repo_root)
    http_server = start_runtime_mcp_http_server(runtime)
    request.addfinalizer(http_server.close)
    materialized = materialize_agent_home(
        runtime,
        "RepoFormatDiscoveryControlledTestAgent",
        mcp_http_base_url=http_server.base_url,
        base_config_path=base_config_path,
        auth_json_path=config_home / "auth.json",
        agent_type_specs=specs,
    )
    assert materialized.ok and materialized.value is not None
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="requirement_group_repo_bootstrap",
            scope_id="repo:Provider",
            params={
                "target_repo": "Provider",
                "repo_root": str(repo_root),
                "workspace_root": str(workspace),
                "requirement_refs": ["Consumer:need_provider"],
            },
        ),
        enqueue=False,
    )
    validate_step_id = runtime.ark.flow_service.advance_flow(flow_id)
    assert validate_step_id is not None
    runtime.ark.step_service.run_step(validate_step_id)
    agent_step_id = runtime.ark.flow_service.advance_flow(flow_id)
    assert agent_step_id is not None
    runtime.ark.flow_service.store.update_step_record(
        agent_step_id,
        lambda step: step.state.variables.__setitem__(
            CONTROLLED_AGENT_OVERRIDE_ALIASES[0],
            {
                "strategy": "fresh_test_agent_type",
                "agent_type_override": "RepoFormatDiscoveryControlledTestAgent",
                "prompt_overlay": (
                    "For this controlled runtime test, use the inherited submit ToolView and submit "
                    "a native repository choice with a short summary and optional searched_targets."
                ),
            },
        ),
    )

    real_step_timeout = float(os.environ.get("LEAN_CONSTELLATION_REAL_CODEX_STEP_TIMEOUT", "300"))
    runtime.ark.step_service.start_step(agent_step_id)
    runtime.ark.step_service.wait_step(agent_step_id, timeout_s=real_step_timeout)
    _schedule_until(
        runtime,
        lambda: runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.COMPLETED,
        limit=20,
        step_timeout_s=real_step_timeout,
    )

    agent_step = runtime.ark.flow_service.get_step(agent_step_id)
    flow = runtime.ark.flow_service.get_flow(flow_id)
    assert agent_step.submission is not None
    assert agent_step.submission.tool_name in {"submit_native_repo_choice", "submit_adapter_repo_choice"}
    assert flow.result is not None
    assert flow.result.outcome in {"native_bootstrap_ready", "adapter_bootstrap_ready"}


@pytest.mark.real_toolkit
def test_real_lean_decl_stage_worker_submit_gate_env_gated(tmp_path: Path) -> None:
    if os.environ.get("LEAN_CONSTELLATION_RUN_REAL_LEAN") != "1":
        pytest.skip("Set LEAN_CONSTELLATION_RUN_REAL_LEAN=1 with a small Lean repo fixture to run this gate test.")
    if shutil.which("lake") is None or shutil.which("lean") is None:
        pytest.skip("lake and lean are required for the real decl stage worker gate e2e test.")

    repo_root = tmp_path / "workspace" / "TinyLeanRepo"
    _write_minimal_lake_repo(repo_root)
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".agent_runtime")
    setup_content_node(runtime, repo_root)
    initial_build = runtime.external.lake.run_lake_build(repo_root)
    assert initial_build.ok, initial_build.summary
    strategy_id, round_id, round_index = create_round_with_decl(
        runtime,
        repo_root,
        target_state=DeclState.DECLARED,
    )
    decl_path_view = runtime.lean_projection.decl_file.derive_decl_file_path(
        repo_root,
        node_path=NODE_PATH,
        decl_name="main_result",
        kind="theorem",
    )
    assert decl_path_view.ok and decl_path_view.value is not None, decl_path_view.issues
    decl_path = Path(decl_path_view.value.path)

    provider = _ScriptedMcpProvider(
        None,
        {
            "StatementNLWorkerAgent": [
                [
                    (
                        "application",
                        "set_statement_nl",
                        {
                            "decl_name": "main_result",
                            "nl": "The main result states True.",
                        },
                    ),
                    (
                        "submit",
                        "submit_stage_worker_completed",
                        {
                            "summary": "Statement NL completed.",
                        },
                    ),
                ]
            ],
            "StatementNLReviewerAgent": [
                [
                    (
                        "application",
                        "record_statement_nl_review_passed",
                        {
                            "decl_name": "main_result",
                            "summary": "Statement NL is acceptable.",
                        },
                    ),
                    (
                        "submit",
                        "submit_stage_review",
                        {"summary": "Statement NL accepted."},
                    ),
                ]
            ],
            "StatementFormalWorkerAgent": [
                [
                    ("application", "prepare_statement_formal_file", {"decl_name": "main_result"}),
                    (
                        "file_replace",
                        "replace_statement_sorry_with_trivial",
                        {
                            "repo_root": str(repo_root),
                            "path": str(decl_path),
                            "old": "  sorry",
                            "new": "  trivial",
                        },
                    ),
                    ("application", "capture_statement_formal_file", {"decl_name": "main_result"}),
                    (
                        "application",
                        "check_formal_stage_consistency",
                        {"decl_name": "main_result", "stage": "statement"},
                    ),
                    (
                        "submit",
                        "submit_stage_worker_completed",
                        {
                            "summary": "Statement formal completed after real Lean capture.",
                        },
                    ),
                ]
            ],
            "StatementFormalReviewerAgent": [
                [
                    (
                        "application",
                        "record_statement_formal_review_passed",
                        {
                            "decl_name": "main_result",
                            "summary": "Statement formal is synchronized with real Lean capture.",
                        },
                    ),
                    (
                        "submit",
                        "submit_stage_review",
                        {"summary": "Statement formal accepted after real Lean capture."},
                    ),
                ]
            ],
        },
    )
    _install_provider(runtime, provider)
    for agent_type in (
        "StatementNLWorkerAgent",
        "StatementNLReviewerAgent",
        "StatementFormalWorkerAgent",
        "StatementFormalReviewerAgent",
    ):
        materialized = materialize_agent_home(runtime, agent_type)
        assert materialized.ok and materialized.value is not None, materialized.issues

    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="decl_graph_round",
            scope_id=f"repo:TinyLeanRepo:node:{NODE_PATH}",
            params={
                "repo_key": "TinyLeanRepo",
                "repo_path": str(repo_root),
                "node_path": NODE_PATH,
                "contract_version": 1,
                "strategy_id": strategy_id,
                "round_id": round_id,
                "round_index": round_index,
                "summary": "Run real Lean decl stage worker submit gate e2e.",
            },
        )
    )

    _schedule_until(
        runtime,
        lambda: runtime.ark.flow_service.get_flow(flow_id).status in {FlowStatus.COMPLETED, FlowStatus.FAILED},
        limit=120,
    )

    flow = runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED, flow.error
    assert flow.result is not None
    assert flow.result.outcome == "completed"
    assert flow.result.completed_stages == ["statement_nl", "statement_formal"]
    assert flow.result.skipped_stages == ["proof_nl", "proof_formal"]
    revision = runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=NODE_PATH,
        name="main_result",
        revision=1,
    )
    assert revision.ok and revision.value is not None, revision.issues
    assert revision.value.state is DeclState.DECLARED
    assert revision.value.statement_lean_check is not None
    assert revision.value.statement_lean_check["status"] == "passed"
    assert revision.value.statement_lean_check["policy"] == "statement_formal"
    assert str(revision.value.statement_lean_check["contains_sorry"]).lower() == "false"
    assert "trivial" in (revision.value.statement_lean_code or "")

    call_keys = [(call["agent_type"], call["view_kind"], call["tool_name"]) for call in provider.calls]
    assert ("StatementFormalWorkerAgent", "application", "capture_statement_formal_file") in call_keys
    assert ("StatementFormalWorkerAgent", "submit", "submit_stage_worker_completed") in call_keys
    stage_gates = runtime.ark.flow_service.list_steps(flow_id=flow_id, step_type="decl_round_stage_gate_audit_step")
    statement_formal_gates = [step for step in stage_gates if getattr(step.result, "stage", None) == "statement_formal"]
    assert statement_formal_gates
    assert statement_formal_gates[-1].result.outcome == "stage_passed"


@pytest.mark.real_codex
def test_real_full_fake_small_lean_repo_path_env_gated() -> None:
    _require_real_codex()
    if os.environ.get("LEAN_CONSTELLATION_RUN_FULL_FAKE_SMALL_REPO") != "1":
        pytest.skip("Set LEAN_CONSTELLATION_RUN_FULL_FAKE_SMALL_REPO=1 to run the full small repo Codex path.")
    pytest.skip("Full small repo path requires project-specific Codex prompt/profile tuning and is not configured here.")
