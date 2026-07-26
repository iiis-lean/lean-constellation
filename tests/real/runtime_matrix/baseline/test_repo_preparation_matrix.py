from __future__ import annotations

import pytest
from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.fixtures import RuntimeMatrixWorkspace
from tests.real.runtime_matrix.scripted_provider import ScriptedMcpProvider, install_scripted_provider, schedule_until


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_native_preparation_prepare_source_rejected_review_then_handoff(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    _write_native_preparation_input(ws, source_mode=SourceCorpusMode.PREPARE, allow_interface_supplement=True)
    _write_source_corpus_entry(ws)
    initialized = ws.runtime.repo_workspace.initialize_repo_as_native(ws.provider_repo, project_name=ws.provider_repo.name)
    assert initialized.ok, initialized.issues
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "SourceCorpusPrepareAgent": [
                (
                    "submit_source_corpus_prepared",
                    {
                        "summary": "Prepared Runtime Matrix source corpus.",
                        "entry_path": "README.md",
                        "overview": "Runtime Matrix source corpus.",
                        "preparation_summary": "Prepared one deterministic source note.",
                    },
                )
            ],
            "SourceIndexBuilderAgent": [
                _complete_source_index_builder_actions("Source index first builder round."),
                ("submit_source_index_builder_round", {"summary": "Source index second builder round."}),
            ],
            "SourceIndexReviewerAgent": [
                (
                    "submit_source_index_review_round",
                    {
                        "approved": False,
                        "summary": "Source index needs one more pass.",
                        "feedback": "Runtime Matrix rejected review branch.",
                    },
                ),
                (
                    "submit_source_index_review_round",
                    {
                        "approved": True,
                        "summary": "Source index approved.",
                        "feedback": None,
                    },
                ),
            ],
            "RootInterfacePrepareAgent": [
                (
                    "submit_root_interface_prepare_ready",
                    {"summary": "Root interfaces are ready for Runtime Matrix handoff."},
                )
            ],
        },
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_homes(
        "SourceCorpusPrepareAgent",
        "SourceIndexBuilderAgent",
        "SourceIndexReviewerAgent",
        "RootInterfacePrepareAgent",
        provider_type="scripted",
    )
    unwrap(ws.admin.resume_runtime())
    flow_id = _start_native_preparation(ws)

    _wait_completed(ws, flow_id)

    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result.outcome == "handoff_dispatched"
    assert [call["tool_name"] for call in provider.calls if call["agent_type"] == "SourceIndexReviewerAgent"] == [
        "submit_source_index_review_round",
        "submit_source_index_review_round",
    ]
    reviewer_calls = [call for call in provider.calls if call["agent_type"] == "SourceIndexReviewerAgent"]
    assert "Runtime Matrix rejected review branch." in reviewer_calls[0]["arguments"].get("feedback", "")
    coordinator_flows = ws.runtime.ark.flow_service.list_flows(flow_type="native_repo_coordinator")
    assert len(coordinator_flows) == 1


def test_native_preparation_source_corpus_blocked_branch(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    _write_native_preparation_input(ws, source_mode=SourceCorpusMode.PREPARE, allow_interface_supplement=True)
    initialized = ws.runtime.repo_workspace.initialize_repo_as_native(ws.provider_repo, project_name=ws.provider_repo.name)
    assert initialized.ok, initialized.issues
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "SourceCorpusPrepareAgent": [
                (
                    "submit_source_corpus_blocked",
                    {
                        "reason": "Runtime Matrix source corpus is intentionally unavailable.",
                        "attempted_targets": ["local"],
                        "missing_materials": ["source note"],
                        "suggested_next_action": "Provide source material.",
                    },
                )
            ]
        },
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_home("SourceCorpusPrepareAgent", provider_type="scripted")
    unwrap(ws.admin.resume_runtime())
    flow_id = _start_native_preparation(ws)

    _wait_completed(ws, flow_id)

    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result.outcome == "blocked"
    assert "intentionally unavailable" in flow.result.blocked_reason
    assert provider.calls[0]["tool_name"] == "submit_source_corpus_blocked"


def test_native_preparation_existing_source_root_interface_direct_ready(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    ws.prepare_provider_native_repo(allow_interface_supplement=False)
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "SourceIndexBuilderAgent": [
                _complete_source_index_builder_actions("Existing source index builder round.", path="source.md"),
            ],
            "SourceIndexReviewerAgent": [
                (
                    "submit_source_index_review_round",
                    {
                        "approved": True,
                        "summary": "Existing source index approved.",
                        "feedback": None,
                    },
                ),
            ],
        },
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_homes("SourceIndexBuilderAgent", "SourceIndexReviewerAgent", provider_type="scripted")
    unwrap(ws.admin.resume_runtime())
    flow_id = _start_native_preparation(ws)

    _wait_completed(ws, flow_id)

    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result.outcome == "handoff_dispatched"
    child_prepare_steps = ws.runtime.ark.flow_service.list_steps(
        flow_id=flow_id, step_type="prepare_native_lifecycle_child_step"
    )
    assert len(child_prepare_steps) == 2
    root_children = ws.runtime.ark.flow_service.list_flows(flow_type="root_interface_preparation")
    assert len(root_children) == 1
    assert root_children[0].status is FlowStatus.COMPLETED
    assert root_children[0].result.outcome == "ready"


def test_adapter_preparation_ready_and_blocked_branches(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    ws.prepare_adapter_truth()
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "AdapterDeclCatalogAgent": [
                (
                    "submit_adapter_catalog_blocked",
                    {
                        "reason": "Runtime Matrix adapter blocked.",
                        "missing_interfaces": ["main_result"],
                        "evidence_summary": "Runtime Matrix blocked path confirms no usable upstream binding for main_result.",
                    },
                ),
            ]
        },
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_home("AdapterDeclCatalogAgent", provider_type="scripted")
    unwrap(ws.admin.resume_runtime())
    blocked_flow_id = _start_adapter_preparation(ws)

    _wait_completed(ws, blocked_flow_id)

    blocked_flow = ws.runtime.ark.flow_service.get_flow(blocked_flow_id)
    assert blocked_flow.result.outcome == "blocked"
    assert blocked_flow.result.missing_interfaces == ["main_result"]

    provider.scripts["AdapterDeclCatalogAgent"].append(_complete_adapter_catalog_actions())
    ready_flow_id = _start_adapter_preparation(ws)
    _wait_completed(ws, ready_flow_id)

    ready_flow = ws.runtime.ark.flow_service.get_flow(ready_flow_id)
    assert ready_flow.result.outcome == "adapter_ready"
    assert ready_flow.result.catalog_decl_count >= 1


def _write_native_preparation_input(
    ws: RuntimeMatrixWorkspace,
    *,
    source_mode: SourceCorpusMode,
    allow_interface_supplement: bool,
) -> None:
    ws.provider_repo.mkdir(parents=True, exist_ok=True)
    assert ws.runtime.repo_workspace.metadata.ensure_repo_model(ws.provider_repo).ok
    written = ws.runtime.repo_workspace.preparation.write_preparation_input(
        ws.provider_repo,
        input=RepoPreparationInput(
            goal="Runtime Matrix native preparation.",
            source_corpus_mode=source_mode,
            source_corpus_relpath=".lean_constellation/source",
            interface_inputs=[],
            allow_interface_supplement=allow_interface_supplement,
        ),
    )
    assert written.ok, written.issues


def _write_source_corpus_entry(ws: RuntimeMatrixWorkspace) -> None:
    source_root = ws.provider_repo / ".lean_constellation" / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "README.md").write_text(
        "Runtime Matrix source corpus.\n"
        "Source provenance: local Runtime Matrix preparation fixture.\n"
        "Reading order: read this README.md entry as the main material.\n"
        "The source supports a tiny theorem whose proof is trivial.\n"
        "This file is fully indexed by the scheduler test.\n"
        "Known gaps and extraction limits: no missing source sections are known.\n",
        encoding="utf-8",
    )


def _complete_source_index_builder_actions(summary: str, *, path: str = "README.md"):
    return [
        (
            "application",
            "create_source_block",
            {
                "parent_id": "root",
                "kind": "theorem",
                "title": "Runtime Matrix theorem source",
                "summary": "A small source block supporting the runtime matrix theorem.",
                "subtype": None,
            },
        ),
        (
            "application",
            "add_source_block_ref",
            {
                "block_id": "b_0001",
                "path": path,
                "start_line": 1,
                "end_line": 3,
                "role": "main",
            },
        ),
        ("application", "mark_block_refs_done", {"block_id": "b_0001"}),
        ("application", "mark_block_links_done", {"block_id": "b_0001"}),
        ("application", "mark_block_completed", {"block_id": "b_0001"}),
        ("application", "mark_block_refs_done", {"block_id": "root"}),
        ("application", "mark_block_links_done", {"block_id": "root"}),
        ("application", "mark_block_completed", {"block_id": "root"}),
        ("application", "set_file_survey_status", {"path": path, "status": "surveyed", "summary": "Read in full."}),
        ("application", "set_file_indexing_status", {"path": path, "status": "indexed"}),
        (
            "application",
            "set_file_survey_status",
            {
                "path": "README.md",
                "status": "skipped" if path != "README.md" else "surveyed",
                "summary": "Entry file only; source.md contains the indexed material.",
            },
        ),
        (
            "application",
            "set_file_indexing_status",
            {"path": "README.md", "status": "skipped" if path != "README.md" else "indexed"},
        ),
        ("submit", "submit_source_index_builder_round", {"summary": summary}),
    ]


def _complete_adapter_catalog_actions():
    return [
        (
            "application",
            "create_adapter_decl",
            {
                "name": "main_result",
                "kind": "theorem",
                "module": "Upstream",
                "lean_decl_name": "upstreamSmoke",
                "summary": "Expose the upstream smoke theorem.",
            },
        ),
        (
            "application",
            "set_adapter_statement_nl",
            {
                "name": "main_result",
                "text": "The upstream smoke theorem states True.\n\nRuntime Matrix adapter fixture.",
            },
        ),
        (
            "application",
            "set_adapter_statement_formal",
            {
                "name": "main_result",
                "code": "theorem upstreamSmoke : True := by\n  trivial",
            },
        ),
        (
            "application",
            "set_adapter_proof_nl",
            {
                "name": "main_result",
                "text": "Use triviality.\n\nThe upstream theorem is already proved by triviality.",
            },
        ),
        (
            "application",
            "set_adapter_proof_formal",
            {
                "name": "main_result",
                "code": "theorem upstreamSmoke : True := by\n  trivial",
            },
        ),
        ("application", "finalize_adapter_decl", {"name": "main_result"}),
        (
            "application",
            "bind_adapter_interface",
            {
                "interface_name": "main_result",
                "decl_name": "main_result",
                "binding_summary": "Runtime Matrix binds required interface to finalized adapter declaration.",
            },
        ),
        ("submit", "submit_adapter_catalog_ready", {"summary": "Adapter catalog is ready."}),
    ]


def _start_native_preparation(ws: RuntimeMatrixWorkspace) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_preparation",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(ws.provider_repo),
                "start_reason": "bootstrap",
                "run_spec": {
                    "run_objective": "Prepare the Provider repository for the runtime matrix scenario.",
                    "completion_mode": "interface_declared",
                    "source_scope": {"mode": "all"},
                    "index_policy": "auto",
                    "root_interface_policy": "auto",
                },
            },
        )
    )


def _start_adapter_preparation(ws: RuntimeMatrixWorkspace) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="adapter_repo_preparation",
            scope_id="repo:Adapter",
            params={
                "repo_key": "Adapter",
                "repo_root": str(ws.adapter_repo),
                "start_reason": "bootstrap",
            },
        )
    )


def _wait_completed(ws: RuntimeMatrixWorkspace, flow_id: str) -> None:
    schedule_until(
        ws.runtime,
        lambda: ws.runtime.ark.flow_service.get_flow(flow_id).status in {FlowStatus.COMPLETED, FlowStatus.FAILED},
        limit=160,
    )
    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED, flow.model_dump_json(indent=2)
