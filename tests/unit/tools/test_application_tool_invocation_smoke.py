from __future__ import annotations

from pathlib import Path

import pytest

from lean_constellation.flows.content_node_task.decl_round.steps import DeclStageReviewerStepState
from lean_constellation.flows.content_node_task.flows import ContentNodeTaskResult
from lean_constellation.services import create_test_runtime_services
from lean_constellation.domain.repo import ProofAvailability, RepoFormat, RepoWorkMode
from lean_constellation.domain.preparation import RepoPreparationInput, RepoRequirementRef, SourceCorpusMode
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services import LeanProviderOverrides
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.external_clients import LeanMcpToolkitClient
from lean_constellation.services.foundation import FoundationContext, ServiceResult, WriteMode
from lean_constellation.services.material import ResourceMetadataInput
from lean_constellation.services.node import DeclPublicView, NodeContractSnapshot
from lean_constellation.services.tool_facade import RawToolCallContext, RuntimeToolContext


def _raw(
    repo_root: Path,
    *,
    view: str,
    agent_type: str,
    role: str = "worker",
    node_path: str | None = None,
    stage: str | None = None,
    round_id: str | None = None,
    batch_decls: list[str] | None = None,
) -> RawToolCallContext:
    return RawToolCallContext(
        endpoint_view_key=view,
        runtime_context=RuntimeToolContext(
            flow_id=f"flow_{view}",
            step_id=f"step_{view}",
            agent_id=f"agent_{view}",
            agent_type=agent_type,
            agent_role=role,  # type: ignore[arg-type]
            expected_view_key=view,
            repo_root=repo_root,
            node_path=node_path,
            node_kind="content" if node_path else None,
            contract_version=1 if node_path else None,
            stage=stage,
            round_id=round_id,
            batch_decls=batch_decls or [],
        ),
    )


def _unwrap_tool_result(result):
    assert result.ok
    assert result.value is not None
    assert result.value.ok is True, result.value.issues
    assert result.value.value is not None
    return result.value.value


def _unwrap_tool_failure(result):
    assert result.ok
    assert result.value is not None
    assert result.value.ok is False
    return result.value.issues


def _create_scope_with_public_decl(runtime, repo_root: Path) -> DeclRef:
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.create_scope_node(
        repo_root,
        path="Main.Provider",
        goal="Provider goal.",
        boundary="Provider boundary.",
    ).ok
    assert runtime.node.create_content_node(
        repo_root,
        path="Main.Consumer",
        goal="Consumer goal.",
        boundary="Consumer boundary.",
        objective="Use provider.",
        success_criteria="Consumer is ready.",
    ).ok
    contract_path = runtime.foundation.node_contract_path(
        FoundationContext(repo_root=repo_root),
        "Main.Provider",
        1,
    )
    loaded = runtime.foundation.store.read_json(contract_path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    ref = DeclRef(repo=None, node="Main.Provider", name="helper", revision=1)
    loaded.value.exports = [ref]
    assert runtime.foundation.store.write_json_atomic(contract_path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok
    assert runtime.node.commit_scope_contract(repo_root, scope_path="Main.Provider", summary="Expose helper.").ok
    return ref


class _FakePublicDeclProvider:
    def __init__(self, runtime, decls: dict[tuple[str, str], list[DeclPublicView]]) -> None:
        self.runtime = runtime
        self.decls = decls

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        return self.runtime.foundation.ok(self.decls.get((str(Path(repo_root)), node_path), []))


def _create_public_decl(
    runtime,
    repo_root: Path,
    *,
    node_path: str,
    name: str,
    kind: str = "definition",
    public: bool = True,
):
    assert runtime.decl_graph.ensure_decl_graph(repo_root, node_path=node_path).ok
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=node_path, objective=f"Create {name}.")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=node_path,
        strategy_id=strategy.value.strategy_id,
        objective=f"Create {name}.",
    )
    assert round_record.ok and round_record.value is not None
    created = runtime.decl_graph.create_decl_revision_view(
        repo_root,
        node_path=node_path,
        round_id=round_record.value.round_id,
        name=name,
        kind=kind,
        objective=f"Create {name}.",
        summary=f"{name} summary.",
        public=public,
        end_after_state=DeclState.DECLARED,
    )
    assert created.ok and created.value is not None
    return created.value


class _FakeCallbackStep:
    step_type = "coordinator_agent_step"

    def __init__(self, *, dispatch_step_id: str) -> None:
        self.state = type("State", (), {"callback_dispatch_step_id": dispatch_step_id})()


class _FakeNonCallbackStep:
    step_type = "coordinator_agent_step"

    def __init__(self) -> None:
        self.state = type("State", (), {})()


class _FakeChildFlow:
    flow_type = "content_node_task"

    def __init__(self, *, parent_flow_id: str, parent_dispatch_step_id: str, result: ContentNodeTaskResult) -> None:
        self.parent_flow_id = parent_flow_id
        self.parent_dispatch_step_id = parent_dispatch_step_id
        self.result = result


class _FakeCallbackFlowService:
    def __init__(self, *, flow_id: str, step_id: str, dispatch_step_id: str, results: list[ContentNodeTaskResult]) -> None:
        self.step_id = step_id
        self.step = _FakeCallbackStep(dispatch_step_id=dispatch_step_id)
        self.flows = [
            _FakeChildFlow(parent_flow_id=flow_id, parent_dispatch_step_id=dispatch_step_id, result=result)
            for result in results
        ]

    def get_step(self, step_id: str):
        assert step_id == self.step_id
        return self.step

    def list_flows(self):
        return list(self.flows)


class _FakeNonCallbackFlowService:
    def __init__(self, *, step_id: str) -> None:
        self.step_id = step_id
        self.step = _FakeNonCallbackStep()

    def get_step(self, step_id: str):
        assert step_id == self.step_id
        return self.step

    def list_flows(self):
        return []


def test_get_current_repo_work_config_tool_reads_repo_config(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
    updated = runtime.repo_workspace.metadata.update_repo_config(
        repo_root,
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
    )
    assert updated.ok

    value = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            _raw(repo_root, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator"),
            tool_name="get_current_repo_work_config",
            flat_args={},
        )
    )

    assert value["repo_key"] == "Repo"
    assert value["target_proof_availability"] == "declared"
    assert value["work_mode"] == "declared_interface"


def test_get_current_repo_requirement_is_read_only_for_coordinator(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
    assert runtime.repo_workspace.create_requirement_with_interfaces(
        repo_root,
        name="need_provider",
        target_repo="Provider",
        reason="Need provider API.",
        interfaces=[
            {
                "name": "main_result",
                "kind": "theorem",
                "summary": "Main provider theorem.",
                "expected_statement_lean_code": "theorem main_result : True := by trivial",
            }
        ],
    ).ok
    before = runtime.repo_workspace.requirement.get_requirement(repo_root, name="need_provider")
    assert before.ok and before.value is not None

    value = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            _raw(repo_root, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator"),
            tool_name="get_current_repo_requirement",
            flat_args={"requirement_name": "need_provider"},
        )
    )

    assert value["requirement"]["name"] == "need_provider"
    assert value["requirement"]["interfaces"][0]["expected_statement_lean_code"].startswith("theorem main_result")
    after = runtime.repo_workspace.requirement.get_requirement(repo_root, name="need_provider")
    assert after.ok and after.value is not None
    assert after.value.requirement == before.value.requirement

    issues = _unwrap_tool_failure(
        runtime.tool_facade.invoke_agent_tool(
            _raw(repo_root, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator"),
            tool_name="attach_requirement_provider_dependency",
            flat_args={"requirement_name": "need_provider"},
        )
    )
    assert issues[0].kind in {"tool_not_in_view", "tool_role_not_allowed", "role_not_allowed"}

    for tool_name, flat_args in (
        ("list_requirement_resume_candidates", {"provider_repo": "Provider"}),
        ("mark_requirement_result_observed", {"requirement_name": "need_provider"}),
    ):
        control_issues = _unwrap_tool_failure(
            runtime.tool_facade.invoke_agent_tool(
                _raw(repo_root, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator"),
                tool_name=tool_name,
                flat_args=flat_args,
            )
        )
        assert control_issues[0].kind in {"tool_not_in_view", "tool_role_not_allowed", "role_not_allowed"}


def test_coordinator_can_read_all_decls_in_any_current_repo_node(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.create_content_node(
        repo_root,
        path="Main.Core",
        goal="Build internal declarations.",
        boundary="Core internal boundary.",
        objective="Create an internal helper.",
        success_criteria="The helper is declared.",
    ).ok
    _create_public_decl(runtime, repo_root, node_path="Main.Core", name="internal_helper", public=False)
    raw = _raw(repo_root, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator")

    index = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="get_node_decl_graph_index",
            flat_args={"node_path": "Main.Core"},
        )
    )
    listed = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="list_node_decls",
            flat_args={"node_path": "Main.Core"},
        )
    )
    inspected = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="inspect_node_decl",
            flat_args={"node_path": "Main.Core", "decl_name": "internal_helper"},
        )
    )

    assert index["node_path"] == "Main.Core"
    assert listed["items"][0]["name"] == "internal_helper"
    assert listed["items"][0]["public"] is False
    assert inspected["decl_name"] == "internal_helper"


def test_coordinator_content_task_result_tools_read_callback_results(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    flow_id = "flow_native_repo_coordinator"
    step_id = "step_native_repo_coordinator"
    dispatch_step_id = "dispatch_content_tasks"
    runtime.ark.flow_service = _FakeCallbackFlowService(
        flow_id=flow_id,
        step_id=step_id,
        dispatch_step_id=dispatch_step_id,
        results=[
            ContentNodeTaskResult(
                outcome="ready",
                repo_key="Repo",
                node_path="Main.Core",
                contract_version=2,
                summary="Core ready.",
            ),
            ContentNodeTaskResult(
                outcome="blocked",
                repo_key="Repo",
                node_path="Main.Blocked",
                contract_version=1,
                reason="Need provider.",
                summary="Blocked on provider.",
            ),
        ],
    )

    list_value = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            _raw(tmp_path, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator"),
            tool_name="list_recent_content_task_results",
            flat_args={"limit": 5},
        )
    )
    inspect_value = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            _raw(tmp_path, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator"),
            tool_name="inspect_content_task_result",
            flat_args={"node_path": "Main.Core", "contract_version": 2},
        )
    )

    assert list_value["count"] == 2
    assert [item["node_path"] for item in list_value["items"]] == ["Main.Blocked", "Main.Core"]
    assert inspect_value["result"]["node_path"] == "Main.Core"
    assert inspect_value["result"]["contract_version"] == 2


def test_coordinator_content_task_result_tools_require_callback_context(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    step_id = "step_native_repo_coordinator"
    runtime.ark.flow_service = _FakeNonCallbackFlowService(step_id=step_id)

    issues = _unwrap_tool_failure(
        runtime.tool_facade.invoke_agent_tool(
            _raw(tmp_path, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator"),
            tool_name="list_recent_content_task_results",
            flat_args={"limit": 5},
        )
    )

    assert issues[0].kind == "content_task_callback_context_missing"


def test_inspect_content_task_result_reports_missing_match(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    flow_id = "flow_native_repo_coordinator"
    step_id = "step_native_repo_coordinator"
    dispatch_step_id = "dispatch_content_tasks"
    runtime.ark.flow_service = _FakeCallbackFlowService(
        flow_id=flow_id,
        step_id=step_id,
        dispatch_step_id=dispatch_step_id,
        results=[
            ContentNodeTaskResult(
                outcome="ready",
                repo_key="Repo",
                node_path="Main.Core",
                contract_version=2,
                summary="Core ready.",
            )
        ],
    )

    issues = _unwrap_tool_failure(
        runtime.tool_facade.invoke_agent_tool(
            _raw(tmp_path, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator"),
            tool_name="inspect_content_task_result",
            flat_args={"node_path": "Main.Core", "contract_version": 99},
        )
    )

    assert issues[0].kind == "content_task_result_not_found"


def test_commit_content_contract_tool_binds_latest_callback_result(tmp_path: Path, monkeypatch) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    flow_id = "flow_native_repo_coordinator"
    step_id = "step_native_repo_coordinator"
    dispatch_step_id = "dispatch_content_tasks"
    runtime.ark.flow_service = _FakeCallbackFlowService(
        flow_id=flow_id,
        step_id=step_id,
        dispatch_step_id=dispatch_step_id,
        results=[
            ContentNodeTaskResult(
                outcome="ready",
                repo_key="Repo",
                node_path="Main.Core",
                contract_version=3,
                summary="Core ready.",
            )
        ],
    )
    captured: dict[str, object] = {}

    def _fake_finalize(repo_root: Path, *, node_path: str, task_result: ContentNodeTaskResult, coordinator_summary: str):
        captured.update(
            {
                "repo_root": repo_root,
                "node_path": node_path,
                "contract_version": task_result.contract_version,
                "coordinator_summary": coordinator_summary,
            }
        )
        return runtime.foundation.ok({"node_path": node_path, "contract_version": task_result.contract_version, "summary": coordinator_summary})

    monkeypatch.setattr(runtime.node, "finalize_content_task_result", _fake_finalize)

    value = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            _raw(tmp_path, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator"),
            tool_name="commit_content_contract",
            flat_args={"node_path": "Main.Core", "summary": "Coordinator accepts core."},
        )
    )

    assert captured["node_path"] == "Main.Core"
    assert captured["contract_version"] == 3
    assert value["summary"] == "Coordinator accepts core."


def test_commit_content_contract_requires_callback_context(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    step_id = "step_native_repo_coordinator"
    runtime.ark.flow_service = _FakeNonCallbackFlowService(step_id=step_id)

    issues = _unwrap_tool_failure(
        runtime.tool_facade.invoke_agent_tool(
            _raw(tmp_path, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator"),
            tool_name="commit_content_contract",
            flat_args={"node_path": "Main.Core", "summary": "Coordinator accepts core."},
        )
    )

    assert issues[0].kind == "content_task_callback_context_missing"


def test_commit_content_contract_reports_missing_callback_result(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    flow_id = "flow_native_repo_coordinator"
    step_id = "step_native_repo_coordinator"
    dispatch_step_id = "dispatch_content_tasks"
    runtime.ark.flow_service = _FakeCallbackFlowService(
        flow_id=flow_id,
        step_id=step_id,
        dispatch_step_id=dispatch_step_id,
        results=[
            ContentNodeTaskResult(
                outcome="ready",
                repo_key="Repo",
                node_path="Main.Core",
                contract_version=2,
                summary="Core ready.",
            )
        ],
    )

    issues = _unwrap_tool_failure(
        runtime.tool_facade.invoke_agent_tool(
            _raw(tmp_path, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator"),
            tool_name="commit_content_contract",
            flat_args={"node_path": "Main.Other", "summary": "Coordinator accepts other."},
        )
    )

    assert issues[0].kind == "content_task_result_not_found"


def test_commit_scope_contract_tool_invokes_node_service(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(
        tmp_path,
        path="Main.Topic",
        goal="Topic goal.",
        boundary="Topic boundary.",
        objective="Close topic scope.",
    ).ok

    value = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            _raw(tmp_path, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator"),
            tool_name="commit_scope_contract",
            flat_args={"node_path": "Main.Topic", "summary": "Topic scope complete."},
        )
    )

    assert value["node_path"] == "Main.Topic"
    assert value["version"] == 1
    assert value["status"] == "committed"


class _FakeMathlibToolkit:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, tool_name: str, payload: dict):
        self.calls.append((tool_name, payload))
        if tool_name == "lsp.run_snippet":
            return {"diagnostics": []}
        raise KeyError(tool_name)


class _FakeReviewerStep:
    step_type = "decl_stage_reviewer_agent_step"

    def __init__(self, *, step_id: str) -> None:
        self.step_id = step_id
        self.state = DeclStageReviewerStepState(
            agent_role="statement_nl_reviewer",
            agent_type="StatementNLReviewerAgent",
        )


class _FakeStepStore:
    def __init__(self, *, step_id: str) -> None:
        self.step_id = step_id
        self.step = _FakeReviewerStep(step_id=step_id)

    def get_step(self, step_id: str):
        assert step_id == self.step_id
        return self.step

    def update_step_record(self, step_id: str, mutator):
        assert step_id == self.step_id
        mutator(self.step)
        return self.step


class _FakeStepService:
    def __init__(self, *, step_id: str) -> None:
        self.store = _FakeStepStore(step_id=step_id)


def _source_readme_text() -> str:
    return (
        "Source overview.\n"
        "Source provenance: local source fixture.\n"
        "Reading order: use this README as the entry and main material.\n"
        "Main material: this entry contains the source material used by the test.\n"
        "Known gaps and extraction limits: no missing source sections are known.\n"
    )


def test_source_corpus_tool_invokes_material_service(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(_source_readme_text(), encoding="utf-8")

    result = runtime.tool_facade.invoke_agent_tool(
        _raw(tmp_path, view="source_corpus_prepare", agent_type="source_corpus_prepare"),
        tool_name="scan_source_corpus",
        flat_args={"relpath": ".lean_constellation/source"},
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is True
    assert result.value.value is not None
    assert result.value.value["files"][0]["path"] == "README.md"


def test_source_index_reviewer_can_read_source_corpus(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(_source_readme_text(), encoding="utf-8")
    raw = _raw(tmp_path, view="source_index_reviewer", agent_type="SourceIndexReviewerAgent", role="reviewer")

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="scan_source_corpus",
        flat_args={"relpath": ".lean_constellation/source"},
    )
    gate = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="check_source_corpus_draft",
        flat_args={"relpath": ".lean_constellation/source", "entry_path": "README.md"},
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is True
    assert result.value.value is not None
    assert result.value.value["files"][0]["path"] == "README.md"
    assert gate.ok
    assert gate.value is not None
    assert gate.value.ok is True
    assert gate.value.value is not None
    assert gate.value.value["passed"] is True


def test_source_range_validation_and_preview_tools_invoke_material_service(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "source.md").write_text("line one\nline two\nline three\n", encoding="utf-8")

    raw = _raw(tmp_path, view="source_index_builder", agent_type="SourceIndexBuilderAgent")
    validated = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="validate_source_range",
            flat_args={"path": "source.md", "start_line": 1, "end_line": 2},
        )
    )
    preview = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="preview_source_ref",
            flat_args={"path": "source.md", "start_line": 2, "end_line": 2, "context_lines": 1},
        )
    )
    invalid = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="validate_source_range",
            flat_args={"path": "source.md", "start_line": 2, "end_line": 99},
        )
    )

    assert validated["path"] == "source.md"
    assert preview["material_kind"] == "source"
    assert preview["preview"]["path"] == "source.md"
    assert invalid["valid"] is False
    assert invalid["issue_code"] == "source_ref_range_invalid"


def test_source_and_resource_text_search_tools_enforce_material_boundary(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "source.md").write_text("shared needle from source\n", encoding="utf-8")

    target = runtime.material.normalize_resource_target("https://example.com/resource")
    assert target.ok and target.value is not None
    temp = tmp_path / "resource_tmp"
    (temp / "normalized").mkdir(parents=True)
    (temp / "normalized" / "resource.md").write_text("shared needle from resource\n", encoding="utf-8")
    registered = runtime.material.register_local_resource(
        tmp_path,
        target=target.value,
        temp_dir=temp,
        metadata=ResourceMetadataInput(title="Resource", source_url="https://example.com/resource"),
    )
    assert registered.ok and registered.value is not None

    source_raw = _raw(tmp_path, view="source_index_builder", agent_type="SourceIndexBuilderAgent")
    resource_raw = _raw(tmp_path, view="resource_curator", agent_type="ResourceCuratorAgent")
    source_hits = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            source_raw,
            tool_name="search_source_text",
            flat_args={"query": "shared needle", "regex": False, "limit": 10},
        )
    )
    resource_hits = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            resource_raw,
            tool_name="search_resource_text",
            flat_args={"query": "shared needle", "regex": False, "limit": 10},
        )
    )

    assert {hit["material_kind"] for hit in source_hits["hits"]} == {"source"}
    assert {hit["material_kind"] for hit in resource_hits["hits"]} == {"resource"}
    assert source_hits["hits"][0]["line_text"] == "shared needle from source"
    assert resource_hits["hits"][0]["line_text"] == "shared needle from resource"


def test_resource_target_tool_invokes_material_service(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    result = runtime.tool_facade.invoke_agent_tool(
        _raw(tmp_path, view="resource_curator", agent_type="resource_curator"),
        tool_name="normalize_resource_target",
        flat_args={"target": "https://example.com/paper"},
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is True
    assert result.value.value is not None
    assert result.value.value["kind"] == "web_url"


def test_mathlib_index_tool_invokes_mathlib_service(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    result = runtime.tool_facade.invoke_agent_tool(
        _raw(tmp_path, view="mathlib_recon", agent_type="mathlib_recon", node_path="Main.Topic"),
        tool_name="search_mathlib_index",
        flat_args={"query": "Nat", "limit": 5},
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is True
    assert result.value.value is not None
    assert result.value.value["query"] == "Nat"


def test_current_node_and_decl_graph_tools_invoke_context_handlers(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Topic",
        goal="Topic goal.",
        boundary="Topic boundary.",
        objective="Plan topic decls.",
        success_criteria="Ready content.",
    ).ok
    raw = _raw(tmp_path, view="content_plan", agent_type="content_plan", role="plan", node_path="Main.Topic")

    contract = runtime.tool_facade.invoke_agent_tool(raw, tool_name="get_current_node_contract", flat_args={})
    graph = runtime.tool_facade.invoke_agent_tool(raw, tool_name="ensure_current_decl_graph", flat_args={})

    assert contract.ok
    assert contract.value is not None
    assert contract.value.ok is True
    assert contract.value.value is not None
    assert contract.value.value["node_path"] == "Main.Topic"
    assert graph.ok
    assert graph.value is not None
    assert graph.value.ok is True


def test_current_node_decl_read_tools_invoke_decl_graph(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Topic",
        goal="Topic goal.",
        boundary="Topic boundary.",
        objective="Plan topic decls.",
        success_criteria="Ready content.",
    ).ok
    _create_public_decl(runtime, tmp_path, node_path="Main.Topic", name="topic_def")
    raw = _raw(tmp_path, view="content_plan", agent_type="content_plan", role="plan", node_path="Main.Topic")

    listed = _unwrap_tool_result(runtime.tool_facade.invoke_agent_tool(raw, tool_name="list_current_node_decls", flat_args={}))
    inspected = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="inspect_current_node_decl",
            flat_args={"decl_name": "topic_def"},
        )
    )

    assert [decl["name"] for decl in listed["items"]] == ["topic_def"]
    assert listed["items"][0]["state"] == "planned"
    assert listed["items"][0]["proof_policy_satisfied"] is False
    assert inspected["decl_name"] == "topic_def"
    assert inspected["public"] is True
    assert inspected["proof_policy_satisfied"] is False
    assert "statement_nl" not in inspected
    assert "statement_lean_code" not in inspected
    included = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="inspect_current_node_decl",
            flat_args={"decl_name": "topic_def", "include_statement_nl": True, "include_statement_formal": True},
        )
    )
    assert "statement_nl" in included
    assert "statement_lean_code" in included


def test_public_decl_boundary_tools_invoke_node_access_resolver(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(
        tmp_path,
        path="Main.Provider",
        goal="Provider scope goal.",
        boundary="Provider scope boundary.",
    ).ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Provider.Core",
        goal="Provider goal.",
        boundary="Provider boundary.",
        objective="Expose helper.",
        success_criteria="Provider ready.",
    ).ok
    contract_path = runtime.foundation.node_contract_path(
        FoundationContext(repo_root=tmp_path),
        "Main.Provider",
        1,
    )
    loaded = runtime.foundation.store.read_json(contract_path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.exports = [DeclRef(repo=None, node="Main.Provider.Core", name="helper", revision=1)]
    assert runtime.foundation.store.write_json_atomic(contract_path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Consumer",
        goal="Consumer goal.",
        boundary="Consumer boundary.",
        objective="Use helper.",
        success_criteria="Consumer ready.",
    ).ok
    _create_public_decl(runtime, tmp_path, node_path="Main.Provider.Core", name="helper")
    assert runtime.node.commit_scope_contract(tmp_path, scope_path="Main.Provider", summary="Expose helper.").ok
    assert runtime.node.add_current_node_dep(
        tmp_path,
        node_path="Main.Consumer",
        target_node="Main.Provider",
        expected_public_decl_names=["helper"],
        reason="Use provider helper.",
        actor="coordinator",
    ).ok
    raw = _raw(tmp_path, view="content_plan", agent_type="content_plan", role="plan", node_path="Main.Consumer")

    visible = _unwrap_tool_result(runtime.tool_facade.invoke_agent_tool(raw, tool_name="list_visible_nodes", flat_args={}))
    current_public = _unwrap_tool_result(runtime.tool_facade.invoke_agent_tool(raw, tool_name="list_current_node_public_decls", flat_args={}))
    provider_public = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="list_node_public_decls",
            flat_args={"node_path": "Main.Provider"},
        )
    )
    inspected = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="inspect_node_public_decl",
            flat_args={"node_path": "Main.Provider", "decl_name": "helper"},
        )
    )

    assert {node["node_path"] for node in visible["nodes"]} == {"Main.Consumer", "Main.Provider"}
    assert current_public["items"] == []
    assert provider_public["items"][0]["ref"]["name"] == "helper"
    assert "ready" not in provider_public["items"][0]
    assert "stale" not in provider_public["items"][0]
    assert provider_public["items"][0]["proof_policy_satisfied"] is False
    assert inspected["decl_name"] == "helper"


def test_repo_public_decl_tools_read_stable_provider_repo(tmp_path: Path) -> None:
    workspace = tmp_path
    consumer = workspace / "Consumer"
    provider = workspace / "Provider"
    consumer.mkdir()
    provider.mkdir()
    base_runtime = create_test_runtime_services(register_application_tools=False)
    assert base_runtime.node.node_tree.ensure_root_scope_node(consumer).ok
    assert base_runtime.node.node_tree.ensure_root_scope_node(provider).ok
    assert base_runtime.node.create_content_node(
        provider,
        path="Main.Core",
        goal="Provider core goal.",
        boundary="Provider core boundary.",
        objective="Expose provider result.",
        success_criteria="Provider ready.",
    ).ok
    _create_public_decl(base_runtime, provider, node_path="Main.Core", name="provider_result", kind="theorem")
    provider_decl = DeclPublicView(
        ref=DeclRef(repo=None, node="Main.Core", name="provider_result", revision=1),
        kind="theorem",
        summary="Provider result.",
        ready=True,
        stale=False,
        source="test_provider",
    )
    runtime = create_test_runtime_services(
        register_application_tools=True,
        providers=LeanProviderOverrides(
            content_public_decl_provider=_FakePublicDeclProvider(base_runtime, {(str(provider), "Main.Core"): [provider_decl]})
        ),
    )
    assert runtime.node.export.add_scope_export(provider, scope_path="Main", decl_node="Main.Core", decl_name="provider_result").ok
    assert runtime.repo_workspace.metadata.ensure_repo_model(provider).ok
    assert runtime.repo_workspace.metadata.mark_repo_stable(provider, summary="Provider stable.").ok
    raw = _raw(consumer, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator")

    candidates = _unwrap_tool_result(runtime.tool_facade.invoke_agent_tool(raw, tool_name="list_ready_provider_repos", flat_args={}))
    public = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(raw, tool_name="list_repo_public_decls", flat_args={"repo_key": "Provider"})
    )
    inspected = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="inspect_repo_public_decl",
            flat_args={"repo_key": "Provider", "decl_name": "provider_result"},
        )
    )

    assert [repo.repo_key for repo in candidates["items"]] == ["Provider"]
    assert public["items"][0]["ref"]["repo"] == "Provider"
    assert "ready" not in public["items"][0]
    assert "stale" not in public["items"][0]
    assert inspected["decl_name"] == "provider_result"


def test_current_node_dependency_and_material_tools_invoke_mutation_wrappers(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    ref = _create_scope_with_public_decl(runtime, tmp_path)
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "notes.md").write_text("line 1\nline 2\n", encoding="utf-8")
    raw = _raw(
        tmp_path,
        view="node_dir_dependency_recon",
        agent_type="node_dir_dependency_recon",
        role="worker",
        node_path="Main.Consumer",
    )

    added_dep = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="add_current_node_dep",
            flat_args={
                "target_node": "Main.Provider",
                "expected_public_decl_names": ["helper"],
                "reason": "Need the provider helper.",
            },
        )
    )

    assert added_dep["deps"]["deps"][0]["expected_decl_refs"] == [ref.model_dump(mode="json")]

    material = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            _raw(tmp_path, view="content_plan", agent_type="content_plan", role="plan", node_path="Main.Consumer"),
            tool_name="add_current_material_ref",
            flat_args={
                "ref_scope": "owned",
                "material_kind": "source",
                "locator": "notes.md",
                "start_line": 1,
                "end_line": 2,
                "reason": "Use source notes.",
            },
        )
    )

    assert material["material_refs"]["owned_refs"][0]["path"] == "notes.md"


def test_coordinator_node_contract_write_tools_invoke_path_based_mutation_wrappers(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    ref = _create_scope_with_public_decl(runtime, tmp_path)
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "notes.md").write_text("line 1\nline 2\n", encoding="utf-8")
    assert runtime.mathlib.upsert_mathlib_module_entry(tmp_path, module="Mathlib.Data.Nat.Basic").ok
    assert runtime.mathlib.upsert_mathlib_decl_entry(
        tmp_path,
        name="Nat.succ_eq_add_one",
        module="Mathlib.Data.Nat.Basic",
        kind="theorem",
        summary="Successor as adding one.",
    ).ok
    raw = _raw(tmp_path, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator")

    added_dep = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="add_node_dep",
            flat_args={
                "node_path": "Main.Consumer",
                "target_node": "Main.Provider",
                "expected_public_decl_names": ["helper"],
                "reason": "Need the provider helper.",
            },
        )
    )
    added_material = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="add_node_material_ref",
            flat_args={
                "node_path": "Main.Consumer",
                "ref_scope": "context",
                "material_kind": "source",
                "locator": "notes.md",
                "start_line": 1,
                "end_line": 2,
                "reason": "Use source notes.",
            },
        )
    )
    added_hint = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="add_node_mathlib_module_hint",
            flat_args={
                "node_path": "Main.Consumer",
                "module": "Mathlib.Data.Nat.Basic",
                "reason": "Natural number facts.",
            },
        )
    )
    added_decl_hint = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="add_node_mathlib_decl_hint",
            flat_args={
                "node_path": "Main.Consumer",
                "decl_name": "Nat.succ_eq_add_one",
                "reason": "Successor rewrite.",
            },
        )
    )

    assert added_dep["deps"]["deps"][0]["expected_decl_refs"] == [ref.model_dump(mode="json")]
    assert added_material["material_refs"]["context_refs"][0]["path"] == "notes.md"
    assert added_hint["hints"]["modules"][0]["module"] == "Mathlib.Data.Nat.Basic"
    assert added_decl_hint["hints"]["declarations"][0]["name"] == "Nat.succ_eq_add_one"

    removed_material = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="remove_node_material_ref",
            flat_args={"node_path": "Main.Consumer", "ref_scope": "context", "index": 0, "reason": "No longer needed."},
        )
    )
    removed_dep = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="remove_node_dep",
            flat_args={"node_path": "Main.Consumer", "index": 0, "reason": "No longer needed."},
        )
    )
    removed_module_hint = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="remove_node_mathlib_module_hint",
            flat_args={"node_path": "Main.Consumer", "module": "Mathlib.Data.Nat.Basic", "reason": "No longer needed."},
        )
    )
    removed_decl_hint = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="remove_node_mathlib_decl_hint",
            flat_args={"node_path": "Main.Consumer", "decl_name": "Nat.succ_eq_add_one", "reason": "No longer needed."},
        )
    )

    assert removed_material["material_refs"]["context_refs"] == []
    assert removed_dep["deps"]["deps"] == []
    assert removed_module_hint["hints"]["modules"] == []
    assert removed_decl_hint["hints"]["declarations"] == []


def test_coordinator_source_index_read_requires_committed_index(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(_source_readme_text(), encoding="utf-8")
    assert runtime.material.submit_source_corpus_prepared(
        tmp_path,
        entry_path="README.md",
        overview="Source overview.",
        preparation_summary="Prepared source.",
    ).ok
    draft = runtime.material.create_draft_source_index(tmp_path)
    assert draft.ok and draft.value is not None
    assert draft.value.status == "draft"

    raw = _raw(tmp_path, view="native_repo_coordinator", agent_type="CoordinatorAgent", role="coordinator")
    draft_read = _unwrap_tool_failure(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="get_source_index",
            flat_args={},
        )
    )
    coverage_read = _unwrap_tool_failure(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="get_source_index_coverage",
            flat_args={},
        )
    )

    assert draft_read[0].kind == "source_index_not_committed"
    assert coverage_read[0].kind == "source_index_not_committed"


@pytest.mark.parametrize(
    ("view", "agent_type", "role", "stage"),
    [
        ("statement_formal_reviewer", "StatementFormalReviewerAgent", "reviewer", "statement_formal"),
        ("proof_nl_worker", "ProofNLWorkerAgent", "worker", "proof_nl"),
        ("proof_nl_reviewer", "ProofNLReviewerAgent", "reviewer", "proof_nl"),
        ("proof_formal_worker", "ProofFormalWorkerAgent", "worker", "proof_formal"),
        ("proof_formal_reviewer", "ProofFormalReviewerAgent", "reviewer", "proof_formal"),
    ],
)
def test_decl_stage_source_index_reads_require_committed_index(
    tmp_path: Path,
    view: str,
    agent_type: str,
    role: str,
    stage: str,
) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(_source_readme_text(), encoding="utf-8")
    assert runtime.material.submit_source_corpus_prepared(
        tmp_path,
        entry_path="README.md",
        overview="Source overview.",
        preparation_summary="Prepared source.",
    ).ok
    assert runtime.material.create_draft_source_index(tmp_path).ok

    raw = _raw(
        tmp_path,
        view=view,
        agent_type=agent_type,
        role=role,
        node_path="Main.Topic",
        stage=stage,
        round_id="round_1",
        batch_decls=["target_decl"],
    )

    draft_read = _unwrap_tool_failure(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="get_source_index",
            flat_args={},
        )
    )
    coverage_read = _unwrap_tool_failure(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="get_source_index_coverage",
            flat_args={},
        )
    )

    assert draft_read[0].kind == "source_index_not_committed"
    assert coverage_read[0].kind == "source_index_not_committed"


def test_root_interface_prepare_tools_are_root_scoped_and_worker_callable(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.repo_workspace.preparation.write_preparation_input(
        tmp_path,
        input=RepoPreparationInput(
            goal="Prepare root interfaces.",
            source_corpus_mode=SourceCorpusMode.EXISTING,
            interface_inputs=[],
        ),
    ).ok
    raw = _raw(tmp_path, view="root_interface_prepare", agent_type="RootInterfacePrepareAgent")

    added = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="add_root_interface",
            flat_args={"name": "core_definition", "kind": "definition", "summary": "Expose the core definition."},
        )
    )
    listed = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="list_root_interfaces",
            flat_args={},
        )
    )
    updated = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="update_root_interface",
            flat_args={"name": "core_definition", "summary": "Updated core definition."},
        )
    )
    no_op_update = _unwrap_tool_failure(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="update_root_interface",
            flat_args={"name": "core_definition"},
        )
    )
    removed = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="remove_root_interface",
            flat_args={"name": "core_definition"},
        )
    )

    assert added["node_path"] == "Main"
    assert listed["node_path"] == "Main"
    assert listed["interfaces"][0]["name"] == "core_definition"
    assert updated["contract"]["interfaces"][0]["summary"] == "Updated core definition."
    assert no_op_update[0].kind == "interface_update_field_required"
    assert removed["contract"]["interfaces"] == []


def test_resource_draft_read_and_mathlib_write_tools_invoke_services(tmp_path: Path) -> None:
    dispatcher = _FakeMathlibToolkit()
    runtime = create_test_runtime_services(
        register_application_tools=True,
        external_overrides={"lean_mcp_toolkit": LeanMcpToolkitClient(dispatcher=dispatcher)},
    )
    allocated = runtime.material.allocate_resource_draft(
        tmp_path,
        target="https://example.com/resource",
        title_hint="Example resource",
    )
    assert allocated.ok and allocated.value is not None

    draft = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            _raw(tmp_path, view="resource_curator", agent_type="resource_curator"),
            tool_name="get_resource_draft",
            flat_args={"draft_id": allocated.value.draft.draft_id},
        )
    )
    assert draft["draft"]["target"]["canonical_locator"] == "https://example.com/resource"

    module = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            _raw(tmp_path, view="mathlib_recon", agent_type="mathlib_recon", node_path="Main.Topic"),
            tool_name="record_mathlib_module",
            flat_args={
                "module_name": "Mathlib.Data.Nat.Basic",
                "summary": "Natural number basics.",
                "source": "smoke test",
            },
        )
    )

    assert module["module"] == "Mathlib.Data.Nat.Basic"
    assert dispatcher.calls[0][0] == "lsp.run_snippet"


def test_decl_stage_nl_tool_invokes_stage_mutation_with_context(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Topic",
        goal="Topic goal.",
        boundary="Topic boundary.",
        objective="Create declarations.",
        success_criteria="Decls ready.",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(tmp_path, node_path="Main.Topic", objective="Strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path="Main.Topic",
        strategy_id=strategy.value.strategy_id,
        objective="Create main_result.",
    )
    assert round_record.ok and round_record.value is not None
    assert runtime.decl_graph.create_decl(
        tmp_path,
        node_path="Main.Topic",
        round_id=round_record.value.round_id,
        name="main_result",
        kind="theorem",
        objective="Create main_result.",
        summary="Main result.",
        end_after_state=DeclState.PROVED,
    ).ok
    assert runtime.decl_graph.start_round(tmp_path, node_path="Main.Topic", round_id=round_record.value.round_id).ok

    view = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            _raw(
                tmp_path,
                view="statement_nl_worker",
                agent_type="statement_nl_worker",
                node_path="Main.Topic",
                stage="statement_nl",
                round_id=round_record.value.round_id,
                batch_decls=["main_result"],
            ),
            tool_name="set_statement_nl",
            flat_args={
                "decl_name": "main_result",
                "nl": "The main result states True.",
            },
        )
    )
    view = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            _raw(
                tmp_path,
                view="statement_nl_worker",
                agent_type="statement_nl_worker",
                node_path="Main.Topic",
                stage="statement_nl",
                round_id=round_record.value.round_id,
                batch_decls=["main_result"],
            ),
            tool_name="add_statement_source_origin",
            flat_args={
                "decl_name": "main_result",
                "source_path": "notes.md",
                "start_line": 1,
                "end_line": 1,
                "note": "statement source",
            },
        )
    )

    assert view["state"] == "planned"
    assert view["statement_origin"][0]["kind"] == "source"
    assert view["statement_origin"][0]["source_path"] == "notes.md"
    assert view["statement_deps"] == []
    assert "statement" not in view
    assert "decl_deps" not in view


def test_decl_stage_review_mark_tool_invokes_review_gate_with_context(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Topic",
        goal="Topic goal.",
        boundary="Topic boundary.",
        objective="Create declarations.",
        success_criteria="Decls ready.",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(tmp_path, node_path="Main.Topic", objective="Strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path="Main.Topic",
        strategy_id=strategy.value.strategy_id,
        objective="Create main_result.",
    )
    assert round_record.ok and round_record.value is not None
    assert runtime.decl_graph.create_decl(
        tmp_path,
        node_path="Main.Topic",
        round_id=round_record.value.round_id,
        name="main_result",
        kind="theorem",
        objective="Create main_result.",
        summary="Main result.",
        end_after_state=DeclState.DECLARED,
    ).ok
    assert runtime.decl_graph.start_round(tmp_path, node_path="Main.Topic", round_id=round_record.value.round_id).ok
    assert runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path="Main.Topic",
        round_id=round_record.value.round_id,
        decl_name="main_result",
        nl="The main result states True.",
    ).ok

    runtime.ark.step_service = _FakeStepService(step_id="step_statement_nl_reviewer")
    view = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            _raw(
                tmp_path,
                view="statement_nl_reviewer",
                agent_type="statement_nl_reviewer",
                role="reviewer",
                node_path="Main.Topic",
                stage="statement_nl",
                round_id=round_record.value.round_id,
                batch_decls=["main_result"],
            ),
            tool_name="record_statement_nl_review_passed",
            flat_args={
                "decl_name": "main_result",
                "summary": "Statement is clear.",
            },
        )
    )

    assert view["decl_name"] == "main_result"
    assert view["passed"] is True
    step = runtime.ark.step_service.store.get_step("step_statement_nl_reviewer")
    review = runtime.decl_graph.aggregate_stage_review_marks(
        tmp_path,
        node_path="Main.Topic",
        round_id=round_record.value.round_id,
        stage="statement_nl",
        summary="All statements accepted.",
        marks=list(step.state.review_marks),
    )
    assert review.ok and review.value is not None
    assert review.value.passed is True


def test_adapter_decl_catalog_tool_invokes_adapter_service(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    workspace = tmp_path / "workspace"
    provider = workspace / "Provider"
    consumer = workspace / "Consumer"
    provider.mkdir(parents=True)
    consumer.mkdir(parents=True)
    assert runtime.repo_workspace.metadata.ensure_repo_model(provider).ok
    assert runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    assert runtime.repo_workspace.metadata.set_repo_format(
        provider,
        repo_format=RepoFormat.ADAPTER,
        reason="adapter tool smoke",
    ).ok
    assert runtime.repo_workspace.requirement.create_requirement(
        consumer,
        name="need_provider",
        target_repo="Provider",
        source_description="Need a provider theorem.",
        reason="The adapter catalog should expose this theorem.",
    ).ok
    assert runtime.repo_workspace.requirement.create_requirement(
        consumer,
        name="other_need",
        target_repo="Provider",
        source_description="Requirement outside current preparation refs.",
        reason=None,
    ).ok
    assert runtime.repo_workspace.preparation.write_preparation_input(
        provider,
        input=RepoPreparationInput(
            goal="Expose an upstream theorem through an adapter repo.",
            source_corpus_mode=SourceCorpusMode.NONE,
            source_corpus_relpath=None,
            requirement_refs=[RepoRequirementRef(consumer_repo="Consumer", requirement_name="need_provider")],
        ),
    ).ok
    assert runtime.node.node_tree.ensure_root_scope_node(provider).ok
    assert runtime.adapter.write_adapter_upstream_metadata(
        provider,
        git_url="https://github.com/example/upstream.git",
        package_name="upstream",
        dependency_name="upstream",
        evidence_summary="Adapter tool smoke upstream.",
        visible_modules=["Upstream.Basic"],
    ).ok
    assert runtime.adapter.mark_upstream_build_trusted(provider, summary="Adapter tool smoke trusted build.").ok
    raw = _raw(provider, view="adapter_repo_import", agent_type="AdapterDeclCatalogAgent")

    requirements = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(raw, tool_name="list_preparation_requirements", flat_args={})
    )
    requirement = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="get_preparation_requirement",
            flat_args={"consumer_repo": "Consumer", "requirement_name": "need_provider"},
        )
    )
    denied_requirement = _unwrap_tool_failure(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="get_preparation_requirement",
            flat_args={"consumer_repo": "Consumer", "requirement_name": "other_need"},
        )
    )
    root_interfaces = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(raw, tool_name="list_root_interfaces", flat_args={})
    )

    created = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="create_adapter_decl",
            flat_args={
                "name": "main_result",
                "kind": "theorem",
                "module": "Upstream.Basic",
                "plan_summary": "Expose the upstream main result.",
            },
        )
    )

    formal = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="set_adapter_statement_formal",
            flat_args={
                "name": "main_result",
                "code": "theorem main_result : True := by\n  sorry",
                "upstream_decl_name": "upstreamSmoke",
            },
        )
    )
    matches = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="find_adapter_decl_by_upstream",
            flat_args={"module": "Upstream.Basic", "upstream_decl_name": "upstreamSmoke"},
        )
    )
    preflight = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(raw, tool_name="check_adapter_catalog_ready_preflight", flat_args={})
    )

    assert [item["requirement"]["name"] for item in requirements["requirements"]] == ["need_provider"]
    assert requirement["requirement"]["name"] == "need_provider"
    assert denied_requirement[0].kind == "preparation_requirement_ref_not_allowed"
    assert root_interfaces["node_path"] == "Main"
    assert created["name"] == "main_result"
    assert created["decl"]["name"] == "main_result"
    assert formal["revision"]["statement"]["formal"]["upstream_decl_name"] == "upstreamSmoke"
    assert [item["name"] for item in matches["matches"]] == ["main_result"]
    assert preflight["passed"] is False
    inspected = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="inspect_adapter_decl",
            flat_args={"name": "main_result"},
        )
    )
    assert inspected["module"] == "Upstream.Basic"
    assert inspected["revision"]["module"] == "Upstream.Basic"
