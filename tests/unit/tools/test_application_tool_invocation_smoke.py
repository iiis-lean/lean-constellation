from __future__ import annotations

from pathlib import Path

from lean_constellation.services import create_test_runtime_services
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.external_clients import LeanMcpToolkitClient
from lean_constellation.services.foundation import FoundationContext, WriteMode
from lean_constellation.services.node import NodeContractSnapshot
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
    contract_path = runtime.foundation.layout.node_contract_path(
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


class _FakeMathlibToolkit:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, tool_name: str, payload: dict):
        self.calls.append((tool_name, payload))
        if tool_name == "lsp.run_snippet":
            return {"diagnostics": []}
        raise KeyError(tool_name)


def test_source_corpus_tool_invokes_material_service(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text("source text\n", encoding="utf-8")

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


def test_resource_draft_and_mathlib_write_tools_invoke_services(tmp_path: Path) -> None:
    dispatcher = _FakeMathlibToolkit()
    runtime = create_test_runtime_services(
        register_application_tools=True,
        external_overrides={"lean_mcp_toolkit": LeanMcpToolkitClient(dispatcher=dispatcher)},
    )

    draft = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            _raw(tmp_path, view="resource_curator", agent_type="resource_curator"),
            tool_name="allocate_resource_draft",
            flat_args={"target": "https://example.com/resource", "title_hint": "Example resource"},
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
            tool_name="write_statement_nl",
            flat_args={
                "decl_name": "main_result",
                "nl": "The main result states True.",
                "origin": [{"kind": "source", "ref": "notes.md"}],
                "deps": ["helper"],
            },
        )
    )

    assert view["state"] == "specified"
    assert view["statement_origin"] == [{"kind": "source", "ref": "notes.md"}]
    assert view["decl_deps"] == ["helper"]


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
            tool_name="record_decl_review",
            flat_args={
                "round_id": round_record.value.round_id,
                "stage": "statement_nl",
                "decl_name": "main_result",
                "passed": True,
                "summary": "Statement is clear.",
            },
        )
    )

    assert view["decl_name"] == "main_result"
    assert view["passed"] is True
    review = runtime.decl_graph.submit_stage_review(
        tmp_path,
        node_path="Main.Topic",
        round_id=round_record.value.round_id,
        stage="statement_nl",
        summary="All statements accepted.",
    )
    assert review.ok and review.value is not None
    assert review.value.passed is True


def test_adapter_decl_catalog_tool_invokes_adapter_service(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    created = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            _raw(tmp_path, view="adapter_repo_import", agent_type="adapter_repo_import"),
            tool_name="create_adapter_decl",
            flat_args={
                "name": "main_result",
                "kind": "theorem",
                "module": "Upstream.Basic",
                "plan_summary": "Expose the upstream main result.",
            },
        )
    )

    assert created["record"]["name"] == "main_result"
    inspected = _unwrap_tool_result(
        runtime.tool_facade.invoke_agent_tool(
            _raw(tmp_path, view="adapter_repo_import", agent_type="adapter_repo_import"),
            tool_name="inspect_adapter_decl",
            flat_args={"name": "main_result"},
        )
    )
    assert inspected["record"]["module"] == "Upstream.Basic"
