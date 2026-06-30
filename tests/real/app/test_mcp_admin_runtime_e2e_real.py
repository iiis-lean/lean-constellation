from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import shutil
import sys
from typing import Any

import pytest
from agent_runtime_kit.agent.homes import HomeCreateSpec
from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from lean_constellation.app import (
    LeanAdminApi,
    SnapshotCreateInput,
    SnapshotRestoreInput,
    StartFlowInput,
    create_app_runtime_services,
    initialize_repo_runtime,
    materialize_agent_home,
)
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.flows.content_node_task.flows import ContentNodeTaskResult
from lean_constellation.mcp import create_mcp_server
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.external_clients import ExternalCommandResult, LeanCheckSummaryView
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
        "Every finite cover has a finite subcover in this toy source.\n"
        "The implementation path intentionally stops after one content task terminal result.\n",
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


def test_semireal_repo_format_discovery_mcp_submit_through_scheduler(tmp_path: Path) -> None:
    provider = _ScriptedMcpProvider(
        None,
        {
            "RepoFormatDiscoveryAgent": [
                ("submit_native_repo_choice", {"summary": "Use native.", "source_corpus_mode": "prepare"}),
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
                        "target_repo": "ProviderRepo",
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
                        "added_node_deps": ["Main.Base"],
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
            params={"repo_key": "Provider", "repo_root": str(repo_root), "start_reason": "bootstrap"},
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
                        "target_repo": "ProviderRepo",
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
    assert initialize_repo_runtime(runtime, repo_root).ok
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
def test_real_codex_repo_format_discovery_submit_env_gated(tmp_path: Path) -> None:
    config_home = _require_real_codex()
    base_config_path = _write_noninteractive_codex_base_config(config_home, tmp_path)
    runtime = create_app_runtime_services(
        runtime_root=tmp_path / ".agent_runtime",
        external_overrides={"lake": _FakeLakeClient()},
    )
    workspace = tmp_path / "workspace"
    repo_root = workspace / "Provider"
    _write_bootstrap_preparation(runtime, repo_root)
    app_config = tmp_path / "lean_constellation.toml"
    app_config.write_text(
        f'workspace_root = "{workspace}"\nruntime_root = "{tmp_path / ".agent_runtime"}"\n',
        encoding="utf-8",
    )
    materialized = materialize_agent_home(
        runtime,
        "RepoFormatDiscoveryAgent",
        mcp_server_command=sys.executable,
        mcp_server_args=["-m", "lean_constellation.mcp.stdio", "--config", str(app_config)],
        mcp_server_env={"PYTHONPATH": str(Path(__file__).resolve().parents[3] / "src")},
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
        end_after_state=DeclState.DECLARED,
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
                        "write_statement_nl",
                        {
                            "decl_name": "main_result",
                            "nl": "The main result states True.",
                            "origin": [{"kind": "real_test", "ref": "tiny_true"}],
                            "deps": [],
                        },
                    ),
                    (
                        "submit",
                        "submit_stage_worker_completed",
                        {
                            "summary": "Statement NL completed.",
                            "completed_decl_names": ["main_result"],
                        },
                    ),
                ]
            ],
            "StatementNLReviewerAgent": [
                [
                    (
                        "application",
                        "record_decl_review",
                        {
                            "round_id": round_id,
                            "stage": "statement_nl",
                            "decl_name": "main_result",
                            "passed": True,
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
                            "completed_decl_names": ["main_result"],
                        },
                    ),
                ]
            ],
            "StatementFormalReviewerAgent": [
                [
                    (
                        "application",
                        "record_decl_review",
                        {
                            "round_id": round_id,
                            "stage": "statement_formal",
                            "decl_name": "main_result",
                            "passed": True,
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
