from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_runtime_kit.flow.models import FlowRequest
from lean_constellation.mcp import create_mcp_server
from lean_constellation.services.external_clients import LeanMcpToolkitClient
from lean_constellation.services.tool_facade import RuntimeToolContext
from lean_constellation.tools import build_application_tool_specs
from tests.unit_services_helpers import publish_native_provider_release
from tests.real.runtime_matrix.admin_helpers import run_next_created_step, run_until_step_created, unwrap
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import RuntimeMatrixWorkspace, _write_minimal_lake_repo
from tests.real.runtime_matrix.strict.tool_cases import build_tool_cases, implemented_tool_cases, pending_tool_cases
from tests.real.runtime_matrix.strict.tool_sweep_partitions import core_tool_sweep_names
from tests.real.runtime_matrix.strict_helpers import call_tool_with_evidence, checkpoint_with_evidence, restore_with_evidence


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_strict_tool_case_table_declares_every_application_tool() -> None:
    registered = {spec.name for spec in build_application_tool_specs()}
    cases = build_tool_cases()

    assert set(cases) == registered
    assert len(cases) == 259
    assert len(implemented_tool_cases()) == 196
    assert len(pending_tool_cases()) == 63
    assert all(case.reason for case in cases.values())
    assert all(case.status != "implemented" for case in pending_tool_cases().values())


def test_strict_implemented_application_tool_cases_execute_with_evidence(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
    evidence_recorder: EvidenceRecorder,
    tmp_path: Path,
) -> None:
    ws = runtime_matrix_workspace
    ws.prepare_provider_native_repo()
    ws.prepare_adapter_truth()
    _ensure_content_node(ws)
    _ensure_workspace_requirements(ws)
    provider_preparation = unwrap(ws.runtime.repo_workspace.preparation.get_preparation_input(ws.provider_repo))
    assert ws.runtime.repo_workspace.preparation.write_preparation_input(
        ws.provider_repo,
        input=provider_preparation.input.model_copy(update={"requirement_refs": []}),
    ).ok
    active_resource_key = ws.create_active_resource(target_kind="local_file", target=str(ws.resources.local_file))
    existing_draft_id = ws.allocate_resource_branch_draft(target_kind="web", target=ws.resources.web_url)
    _ensure_node_tool_fixture(ws)
    server = unwrap(
        create_mcp_server(
            ws.runtime,
            view_keys=[
                "repo_format_discovery",
                "root_interface_prepare",
                "native_repo_coordinator",
                "resource_curator",
                "source_corpus_prepare",
                "source_index_builder",
                "mathlib_recon",
                "content_plan",
                "adapter_repo_import",
            ],
        )
    )
    _run_adapter_tool_sweep(ws, server, evidence_recorder)

    prep = call_tool_with_evidence(
        server,
        "repo_format_discovery",
        "get_preparation_input",
        {},
        runtime_context=_ctx(ws.provider_repo, view="repo_format_discovery", agent_type="RepoFormatDiscoveryAgent", role="coordinator"),
        recorder=evidence_recorder,
        assertion_summary="Preparation input returned the repo goal.",
    )
    assert prep.value["input"]["goal"]
    requirement_refs = prep.value["input"].get("requirement_refs") or []

    scoped_requirements = call_tool_with_evidence(
        server,
        "repo_format_discovery",
        "list_preparation_requirements",
        {},
        runtime_context=_ctx(ws.provider_repo, view="repo_format_discovery", agent_type="RepoFormatDiscoveryAgent", role="coordinator"),
        recorder=evidence_recorder,
        assertion_summary="Scoped preparation requirements returned only current refs.",
    )
    assert len(scoped_requirements.value["requirements"]) == len(requirement_refs)
    if requirement_refs:
        ref = requirement_refs[0]
        requirement_detail = call_tool_with_evidence(
            server,
            "repo_format_discovery",
            "get_preparation_requirement",
            {"consumer_repo": ref["consumer_repo"], "requirement_name": ref["requirement_name"]},
            runtime_context=_ctx(ws.provider_repo, view="repo_format_discovery", agent_type="RepoFormatDiscoveryAgent", role="coordinator"),
            recorder=evidence_recorder,
            assertion_summary="Scoped preparation requirement detail returned one current ref.",
        )
        assert requirement_detail.value["requirement"]["name"] == ref["requirement_name"]
    else:
        call_tool_with_evidence(
            server,
            "repo_format_discovery",
            "get_preparation_requirement",
            {"consumer_repo": "Consumer", "requirement_name": "need_provider"},
            runtime_context=_ctx(
                ws.provider_repo,
                view="repo_format_discovery",
                agent_type="RepoFormatDiscoveryAgent",
                role="coordinator",
            ),
            recorder=evidence_recorder,
            expected_failure=True,
            assertion_summary="Preparation requirement lookup rejected a ref outside the current preparation input.",
        )

    preflight = call_tool_with_evidence(
        server,
        "repo_format_discovery",
        "get_preparation_start_preflight",
        {"expected_format": "native"},
        runtime_context=_ctx(ws.provider_repo, view="repo_format_discovery", agent_type="RepoFormatDiscoveryAgent", role="coordinator"),
        recorder=evidence_recorder,
        assertion_summary="Preparation start preflight passed.",
    )
    assert preflight.value["passed"] is True

    handoff_gate = call_tool_with_evidence(
        server,
        "root_interface_prepare",
        "check_root_main_handoff_interfaces",
        {},
        runtime_context=_ctx(ws.provider_repo, view="root_interface_prepare", agent_type="RootInterfacePrepareAgent"),
        recorder=evidence_recorder,
        assertion_summary="Root Main handoff interface gate passed.",
    )
    assert handoff_gate.value["passed"] is True

    root_interfaces = call_tool_with_evidence(
        server,
        "root_interface_prepare",
        "list_root_interfaces",
        {},
        runtime_context=_ctx(ws.provider_repo, view="root_interface_prepare", agent_type="RootInterfacePrepareAgent"),
        recorder=evidence_recorder,
        assertion_summary="Root Main interfaces were listed.",
    )
    assert root_interfaces.value["node_path"] == "Main"

    root_interface_checkpoint = checkpoint_with_evidence(
        ws.admin,
        ws.provider_repo,
        scope_ids=["repo:Provider"],
        label="strict_tool_sweep_root_interfaces",
        recorder=evidence_recorder,
    )
    added_root_interface = call_tool_with_evidence(
        server,
        "root_interface_prepare",
        "add_root_interface",
        {"name": "strict_supplement", "kind": "theorem", "summary": "Strict supplement interface."},
        runtime_context=_ctx(ws.provider_repo, view="root_interface_prepare", agent_type="RootInterfacePrepareAgent"),
        recorder=evidence_recorder,
        assertion_summary="Root supplement interface was added.",
    )
    assert any(item["name"] == "strict_supplement" for item in added_root_interface.value["contract"]["interfaces"])

    call_tool_with_evidence(
        server,
        "root_interface_prepare",
        "update_root_interface",
        {"name": "strict_supplement", "summary": "Updated strict supplement interface."},
        runtime_context=_ctx(ws.provider_repo, view="root_interface_prepare", agent_type="RootInterfacePrepareAgent"),
        recorder=evidence_recorder,
        expected_failure=True,
        assertion_summary="Root preparation rejected non-append interface mutation.",
    )

    call_tool_with_evidence(
        server,
        "root_interface_prepare",
        "remove_root_interface",
        {"name": "strict_supplement"},
        runtime_context=_ctx(ws.provider_repo, view="root_interface_prepare", agent_type="RootInterfacePrepareAgent"),
        recorder=evidence_recorder,
        expected_failure=True,
        assertion_summary="Root preparation rejected supplement interface removal.",
    )
    restore_with_evidence(
        ws.admin,
        ws.provider_repo,
        root_interface_checkpoint.snapshot_id,
        scope_ids=["repo:Provider"],
        label="strict_tool_sweep_root_interfaces",
        recorder=evidence_recorder,
    )
    ready_analysis_repo = ws.workspace_root / "ReadyAnalysis"
    _write_minimal_lake_repo(ready_analysis_repo, module_name="Main")
    release = publish_native_provider_release(
        ws.runtime,
        ready_analysis_repo,
        summary="Strict ToolSweep provider ready before coordinator checks.",
        release_id="strict_tool_sweep_r1",
    )
    assert release.parent_release_id is None

    coordinator_ctx = _ctx(
        ws.consumer_repo,
        view="native_repo_coordinator",
        agent_type="CoordinatorAgent",
        role="coordinator",
    )
    workspace = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "inspect_workspace_for_coordinator",
        {},
        runtime_context=coordinator_ctx,
        recorder=evidence_recorder,
        assertion_summary="Coordinator workspace inspection saw ready provider.",
    )
    assert any(item["repo_key"] == "ReadyAnalysis" for item in workspace.value["ready_provider_repos"])

    ready_providers = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "list_ready_provider_repos",
        {},
        runtime_context=coordinator_ctx,
        recorder=evidence_recorder,
        assertion_summary="Ready provider list returned ReadyAnalysis.",
    )
    assert any(item.repo_key == "ReadyAnalysis" for item in ready_providers.value["items"])

    open_groups = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "list_open_requirement_groups",
        {},
        runtime_context=coordinator_ctx,
        recorder=evidence_recorder,
        assertion_summary="Open requirement groups returned OpenAnalysis.",
    )
    assert any(item.target_repo == "OpenAnalysis" for item in open_groups.value["items"])

    group = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "get_requirement_group",
        {"target_repo": "OpenAnalysis"},
        runtime_context=coordinator_ctx,
        recorder=evidence_recorder,
        assertion_summary="OpenAnalysis requirement group was loaded.",
    )
    assert group.value["target_repo"] == "OpenAnalysis"

    deps_before = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "list_current_lake_dependencies",
        {},
        runtime_context=coordinator_ctx,
        recorder=evidence_recorder,
        assertion_summary="Initial Lake dependency list was empty.",
    )
    assert deps_before.value["items"] == []

    available_before_resume = ws.runtime.repo_workspace.provider_availability.check_provider_available(
        ready_analysis_repo
    )
    assert available_before_resume.ok and available_before_resume.value is not None
    assert available_before_resume.value.passed
    assert ws.runtime.repo_workspace.mark_requirement_waiting_for_provider(
        ws.consumer_repo,
        requirement_name="need_provider",
        provider_repo="ReadyAnalysis",
        reason="Strict ToolSweep waits for provider callback.",
    ).ok
    assert ws.runtime.repo_workspace.requirement.mark_requirement_satisfied(
        ws.consumer_repo,
        requirement_name="need_provider",
        provider_repo="ReadyAnalysis",
        note="Strict ToolSweep provider ready.",
    ).ok
    resume_candidates = unwrap(
        ws.runtime.repo_workspace.list_resume_candidates_for_requirement(
            ws.consumer_repo.parent,
            provider_repo="ReadyAnalysis",
        )
    )
    assert any(item.requirement_name == "need_provider" for item in resume_candidates)

    requirement_checkpoint = checkpoint_with_evidence(
        ws.admin,
        ws.consumer_repo,
        scope_ids=["repo:Consumer"],
        label="strict_tool_sweep_requirement_observe_attach",
        recorder=evidence_recorder,
    )
    observed = unwrap(
        ws.runtime.repo_workspace.mark_requirement_result_observed(
            ws.consumer_repo,
            requirement_name="need_provider",
            note="Strict ToolSweep observed provider result.",
        )
    )
    assert observed.result_observed is True

    attached = unwrap(
        ws.runtime.repo_workspace.attach_provider_for_requirement(
            ws.consumer_repo,
            requirement_name="need_provider",
        )
    )
    assert attached.attached is True
    assert attached.handled is True

    deps_after = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "list_current_lake_dependencies",
        {},
        runtime_context=coordinator_ctx,
        recorder=evidence_recorder,
        assertion_summary="Lake dependency list included attached provider.",
    )
    assert any(item.name == "ReadyAnalysis" for item in deps_after.value["items"])

    restore_with_evidence(
        ws.admin,
        ws.consumer_repo,
        requirement_checkpoint.snapshot_id,
        scope_ids=["repo:Consumer"],
        label="strict_tool_sweep_requirement_observe_attach",
        recorder=evidence_recorder,
    )
    restored_requirement = unwrap(
        ws.runtime.repo_workspace.requirement.get_requirement(ws.consumer_repo, name="need_provider")
    )
    assert restored_requirement.requirement.provider_result_observed_at is None

    node_ctx = _ctx(
        ws.provider_repo,
        view="native_repo_coordinator",
        agent_type="CoordinatorAgent",
        role="coordinator",
    )
    plan_ctx = _ctx(ws.provider_repo, view="content_plan", agent_type="ContentPlanAgent", role="plan", node_path="Main.Topic.Core")
    node_tree = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "get_node_tree",
        {},
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Node tree returned active Topic nodes.",
    )
    assert any(item["path"] == "Main.Topic.Core" for item in node_tree.value["nodes"])

    node = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "get_node",
        {"node_path": "Main.Topic.Core"},
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Node metadata lookup returned Main.Topic.Core.",
    )
    assert node.value["path"] == "Main.Topic.Core"

    node_contract = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "get_node_contract",
        {"node_path": "Main.Topic.Core"},
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Node contract lookup returned Main.Topic.Core.",
    )
    assert node_contract.value["node_path"] == "Main.Topic.Core"

    runnable = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "list_runnable_content_nodes",
        {"max_count": 10},
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Runnable content node list returned Topic content nodes.",
    )
    assert any(item["path"] == "Main.Topic.Core" for item in runnable.value["candidates"])

    node_checkpoint = checkpoint_with_evidence(
        ws.admin,
        ws.provider_repo,
        scope_ids=["repo:Provider"],
        label="strict_tool_sweep_node_contract",
        recorder=evidence_recorder,
    )
    created_scope = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "create_scope_node",
        {
            "path": "Main.ToolSweep",
            "goal": "Strict ToolSweep scope goal.",
            "boundary": "Strict ToolSweep scope boundary.",
            "objective": "Exercise node tools.",
            "constraints": None,
            "success_criteria": "Node tools are covered.",
        },
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Temporary scope node was created.",
    )
    assert created_scope.value["path"] == "Main.ToolSweep"

    created_content = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "create_content_node",
        {
            "path": "Main.ToolSweep.Item",
            "goal": "Strict ToolSweep content goal.",
            "boundary": "Strict ToolSweep content boundary.",
            "objective": "Exercise content node tools.",
            "success_criteria": "Content node tools are covered.",
            "constraints": None,
        },
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Temporary content node was created.",
    )
    assert created_content.value["path"] == "Main.ToolSweep.Item"

    updated_contract = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "update_node_contract_text",
        {
            "node_path": "Main.ToolSweep.Item",
            "goal": "Strict ToolSweep updated content goal.",
            "boundary": None,
            "objective": None,
            "success_criteria": None,
            "constraints": None,
        },
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Temporary content node contract text changed.",
    )
    assert updated_contract.value["contract"]["goal"] == "Strict ToolSweep updated content goal."
    committed_temporary = ws.runtime.node.commit_content_contract(
        ws.provider_repo,
        node_path="Main.ToolSweep.Item",
        summary="Close temporary ToolSweep work before delete preview.",
    )
    assert committed_temporary.ok, committed_temporary.issues

    delete_preview = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "preview_delete_node",
        {"node_path": "Main.ToolSweep.Item"},
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Temporary content node delete preview passed.",
    )
    assert delete_preview.value["deletable"] is True

    deleted = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "delete_node",
        {"node_path": "Main.ToolSweep.Item", "reason": "Strict ToolSweep delete rollback check."},
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Temporary content node was soft-deleted.",
    )
    assert deleted.value["changed"] is True

    visible_nodes = call_tool_with_evidence(
        server,
        "content_plan",
        "list_visible_nodes",
        {},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Visible node list returned committed Helper boundary.",
    )
    assert any(item["node_path"] == "Main.Topic.Helper" for item in visible_nodes.value["nodes"])

    added_dep = call_tool_with_evidence(
        server,
        "content_plan",
        "add_current_node_dep",
        {
            "target_node": "Main.Topic.Helper",
            "reason": "Strict ToolSweep dependency.",
            "target_repo": None,
            "expected_public_decl_names": None,
        },
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Current node dependency was added.",
    )
    assert len(added_dep.value["deps"]["deps"]) == 1

    deps_after_add = call_tool_with_evidence(
        server,
        "content_plan",
        "list_current_node_deps",
        {},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Current node dependency list returned added dependency.",
    )
    assert deps_after_add.value["deps"][0]["target_node"] == "Main.Topic.Helper"

    removed_dep = call_tool_with_evidence(
        server,
        "content_plan",
        "remove_current_node_dep",
        {"index": 0},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Current node dependency was removed.",
    )
    assert removed_dep.value["deps"]["deps"] == []

    added_material = call_tool_with_evidence(
        server,
        "content_plan",
        "add_current_material_ref",
        {
            "ref_scope": "owned",
            "material_kind": "resource",
            "locator": active_resource_key,
            "start_line": 1,
            "end_line": 1,
            "reason": "Strict ToolSweep material ref.",
        },
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Current node material ref was added.",
    )
    assert len(added_material.value["material_refs"]["owned_refs"]) == 1

    material_refs = call_tool_with_evidence(
        server,
        "content_plan",
        "list_current_node_material_refs",
        {},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Node material refs returned added resource ref.",
    )
    assert material_refs.value["owned_refs"][0]["resource_key"] == active_resource_key

    coordinator_material_refs = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "list_node_material_refs",
        {"node_path": "Main.Topic.Core"},
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Coordinator node material refs returned added resource ref.",
    )
    assert coordinator_material_refs.value["owned_refs"][0]["resource_key"] == active_resource_key

    removed_material = call_tool_with_evidence(
        server,
        "content_plan",
        "remove_current_material_ref",
        {"ref_scope": "owned", "index": 0, "reason": "Strict ToolSweep material ref removal."},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Current node material ref was removed.",
    )
    assert removed_material.value["material_refs"]["owned_refs"] == []

    interfaces_before = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "list_node_interfaces",
        {"node_path": "Main.Topic.Core"},
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Node interface list returned an empty list before writes.",
    )
    assert interfaces_before.value["interfaces"] == []

    added_interface = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "add_node_interface",
        {
            "node_path": "Main.Topic.Core",
            "name": "tool_sweep_iface",
            "kind": "theorem",
            "summary": "Strict ToolSweep interface.",
            "statement_hint": "True",
        },
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Node interface was added.",
    )
    assert added_interface.value["contract"]["interfaces"][0]["name"] == "tool_sweep_iface"

    updated_interface = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "update_node_interface",
        {
            "node_path": "Main.Topic.Core",
            "name": "tool_sweep_iface",
            "summary": "Strict ToolSweep interface updated.",
            "statement_hint": "True",
        },
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Node interface was updated.",
    )
    assert updated_interface.value["contract"]["interfaces"][0]["summary"] == "Strict ToolSweep interface updated."

    removed_interface = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "remove_node_interface",
        {"node_path": "Main.Topic.Core", "name": "tool_sweep_iface"},
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Node interface was removed.",
    )
    assert removed_interface.value["contract"]["interfaces"] == []

    export_candidates = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "list_scope_export_candidates",
        {"scope_path": "Main.Topic"},
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Scope export candidate list returned a structured result.",
    )
    assert export_candidates.value["scope_path"] == "Main.Topic"

    exports = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "list_scope_exports",
        {"scope_path": "Main.Topic"},
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Scope export list returned a structured result.",
    )
    assert exports.value["items"] == []

    scope_close = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "get_scope_close_view",
        {"scope_path": "Main.Topic"},
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Scope close view returned readiness gates.",
    )
    assert scope_close.value["scope_path"] == "Main.Topic"

    coordinator_flow_id = ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(ws.provider_repo),
                "start_mode": "admin_start",
                "start_reason": "Strict ToolSweep release-preview context.",
            },
        )
    )
    repo_ready_ctx = _ctx(
        ws.provider_repo,
        view="native_repo_coordinator",
        agent_type="CoordinatorAgent",
        role="coordinator",
        flow_id=coordinator_flow_id,
    )
    repo_ready = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "get_repo_ready_node_view",
        {},
        runtime_context=repo_ready_ctx,
        recorder=evidence_recorder,
        assertion_summary="Repo ready node view returned readiness gates.",
    )
    assert "candidate_gate" in repo_ready.value

    admission = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "check_content_task_admission",
        {"node_path": "Main.Topic.Core"},
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Content task admission gate passed.",
    )
    assert admission.value["passed"] is True

    batch = call_tool_with_evidence(
        server,
        "native_repo_coordinator",
        "check_content_node_batch",
        {"node_paths": ["Main.Topic.Core"]},
        runtime_context=node_ctx,
        recorder=evidence_recorder,
        assertion_summary="Content node batch gate passed.",
    )
    assert batch.value["passed"] is True

    restore_with_evidence(
        ws.admin,
        ws.provider_repo,
        node_checkpoint.snapshot_id,
        scope_ids=["repo:Provider"],
        label="strict_tool_sweep_node_contract",
        recorder=evidence_recorder,
    )
    restored_tool_sweep_node = ws.runtime.node.node_tree.get_node(ws.provider_repo, path="Main.ToolSweep")
    assert not restored_tool_sweep_node.ok

    normalized = call_tool_with_evidence(
        server,
        "resource_curator",
        "normalize_resource_target",
        {"target": ws.resources.web_url},
        runtime_context=_ctx(ws.provider_repo, view="resource_curator", agent_type="ResourceCuratorAgent"),
        recorder=evidence_recorder,
        assertion_summary="Web URL target normalized.",
    )
    assert normalized.value["kind"] == "web_url"

    material_context = call_tool_with_evidence(
        server,
        "resource_curator",
        "get_material_context",
        {"query": "Runtime Matrix", "include_source": True, "include_resources": True, "regex": False, "limit": 10},
        runtime_context=_ctx(ws.provider_repo, view="resource_curator", agent_type="ResourceCuratorAgent", node_path="Main.Core"),
        recorder=evidence_recorder,
        assertion_summary="Material context returned resource/source context.",
    )
    assert material_context.value["repo_root"] == str(ws.provider_repo)

    duplicate = call_tool_with_evidence(
        server,
        "resource_curator",
        "find_duplicate_resource",
        {"target": str(ws.resources.local_file)},
        runtime_context=_ctx(ws.provider_repo, view="resource_curator", agent_type="ResourceCuratorAgent"),
        recorder=evidence_recorder,
        assertion_summary="Existing local file resource was detected as duplicate.",
    )
    assert duplicate.value["duplicate"] is True

    resources = call_tool_with_evidence(
        server,
        "resource_curator",
        "list_resources",
        {},
        runtime_context=_ctx(ws.provider_repo, view="resource_curator", agent_type="ResourceCuratorAgent"),
        recorder=evidence_recorder,
        assertion_summary="Resource library list returned active resource.",
    )
    assert any(item.resource_key == active_resource_key for item in resources.value["items"])

    resource = call_tool_with_evidence(
        server,
        "resource_curator",
        "get_resource",
        {"resource_key": active_resource_key},
        runtime_context=_ctx(ws.provider_repo, view="resource_curator", agent_type="ResourceCuratorAgent"),
        recorder=evidence_recorder,
        assertion_summary="Resource library lookup returned active resource.",
    )
    assert resource.value["resource"]["resource_key"] == active_resource_key

    resource_range = call_tool_with_evidence(
        server,
        "resource_curator",
        "read_resource_range",
        {"resource_key": active_resource_key, "start_line": 1, "end_line": 1, "context_lines": 0},
        runtime_context=_ctx(ws.provider_repo, view="resource_curator", agent_type="ResourceCuratorAgent"),
        recorder=evidence_recorder,
        assertion_summary="Normalized resource range returned text.",
    )
    assert "runtime matrix" in resource_range.value["text_with_line_numbers"].lower()

    resource_hits = call_tool_with_evidence(
        server,
        "resource_curator",
        "search_resource_text",
        {"query": "Runtime Matrix", "regex": False, "limit": 5},
        runtime_context=_ctx(ws.provider_repo, view="resource_curator", agent_type="ResourceCuratorAgent"),
        recorder=evidence_recorder,
        assertion_summary="Resource text search found normalized resource text.",
    )
    assert resource_hits.value["hits"]

    draft_view = call_tool_with_evidence(
        server,
        "resource_curator",
        "get_resource_draft",
        {"draft_id": existing_draft_id},
        runtime_context=_ctx(ws.provider_repo, view="resource_curator", agent_type="ResourceCuratorAgent"),
        recorder=evidence_recorder,
        assertion_summary="Resource draft lookup returned draft.",
    )
    assert draft_view.value["draft"]["draft_id"] == existing_draft_id

    draft_gate = call_tool_with_evidence(
        server,
        "resource_curator",
        "check_resource_draft",
        {"draft_id": existing_draft_id},
        runtime_context=_ctx(ws.provider_repo, view="resource_curator", agent_type="ResourceCuratorAgent"),
        recorder=evidence_recorder,
        assertion_summary="Resource draft validation passed.",
    )
    assert draft_gate.value["passed"] is True

    source_ctx = _ctx(ws.provider_repo, view="source_index_builder", agent_type="SourceIndexBuilderAgent")
    source_scan = call_tool_with_evidence(
        server,
        "source_index_builder",
        "scan_source_corpus",
        {},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source corpus scan returned canonical README.md entry.",
    )
    assert source_scan.value["entry_path"] == "README.md"

    source_gate = call_tool_with_evidence(
        server,
        "source_index_builder",
        "check_source_corpus_draft",
        {"entry_path": "source.md"},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source corpus draft gate passed.",
    )
    assert source_gate.value["passed"] is True

    source_checkpoint = checkpoint_with_evidence(
        ws.admin,
        ws.provider_repo,
        scope_ids=["repo:Provider"],
        label="strict_tool_sweep_source_index",
        recorder=evidence_recorder,
    )
    source_index_flow_id = ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="source_index_build",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(ws.provider_repo),
                "run_objective": "Exercise strict SourceIndex builder tools.",
                "target_proof_availability": "declared",
                "work_mode": "declared_interface",
                "source_scope": {"mode": "all"},
                "index_policy": "auto",
                "start_reason": "admin_preprocess",
                "max_review_rounds": 2,
            },
        )
    )
    for _ in range(4):
        run_next_created_step(ws.admin, source_index_flow_id)
    source_builder_step_id = run_until_step_created(
        ws.admin,
        source_index_flow_id,
        "source_index_builder_agent_step",
        max_advances=1,
    )
    source_ctx = _ctx(
        ws.provider_repo,
        view="source_index_builder",
        agent_type="SourceIndexBuilderAgent",
        flow_id=source_index_flow_id,
        step_id=source_builder_step_id,
    )

    loaded_source_index = call_tool_with_evidence(
        server,
        "source_index_builder",
        "get_source_index",
        {},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source index draft was loaded.",
    )
    assert loaded_source_index.value["root_block_id"] == "root"

    overview = call_tool_with_evidence(
        server,
        "source_index_builder",
        "set_source_index_overview",
        {"overview": "Strict source overview."},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source index overview changed.",
    )
    assert overview.value["overview"] == "Strict source overview."

    block = call_tool_with_evidence(
        server,
        "source_index_builder",
        "create_source_block",
        {
            "parent_id": "root",
            "kind": "theorem",
            "title": "Strict theorem",
            "summary": "Strict theorem summary.",
            "subtype": None,
        },
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source block was created.",
    )
    assert block.value["block_id"] == "b_0001"

    updated_block = call_tool_with_evidence(
        server,
        "source_index_builder",
        "update_source_block",
        {
            "block_id": "b_0001",
            "title": "Strict theorem updated",
            "summary": "Strict updated summary.",
            "kind": None,
            "subtype": None,
        },
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source block was updated.",
    )
    assert updated_block.value["title"] == "Strict theorem updated"

    block_with_ref = call_tool_with_evidence(
        server,
        "source_index_builder",
        "add_source_block_ref",
        {"block_id": "b_0001", "path": "source.md", "start_line": 1, "end_line": 2, "role": "main"},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source block ref was added.",
    )
    assert block_with_ref.value["refs"][0]["ref_id"] == "ref_0001"

    link = call_tool_with_evidence(
        server,
        "source_index_builder",
        "create_source_link",
        {
            "source_block_id": "b_0001",
            "link_kind": "supports",
            "evidence_ref_ids": ["ref_0001"],
            "target_block_id": None,
            "target_hint": "strict target",
        },
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source link was created.",
    )
    assert link.value["link_id"] == "link_0001"

    refs_done = call_tool_with_evidence(
        server,
        "source_index_builder",
        "mark_block_refs_done",
        {"block_id": "b_0001"},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source block refs gate passed.",
    )
    assert refs_done.value["passed"] is True

    links_done = call_tool_with_evidence(
        server,
        "source_index_builder",
        "mark_block_links_done",
        {"block_id": "b_0001"},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source block links gate passed.",
    )
    assert links_done.value["passed"] is True

    block_done = call_tool_with_evidence(
        server,
        "source_index_builder",
        "mark_block_completed",
        {"block_id": "b_0001"},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source block completion gate passed.",
    )
    assert block_done.value["passed"] is True

    survey = call_tool_with_evidence(
        server,
        "source_index_builder",
        "set_file_survey_status",
        {"path": "source.md", "status": "surveyed", "summary": "Read in full."},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source file survey status changed.",
    )
    assert survey.value["survey_status"] == "surveyed"

    indexing = call_tool_with_evidence(
        server,
        "source_index_builder",
        "set_file_indexing_status",
        {"path": "source.md", "status": "indexed"},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source file indexing status changed.",
    )
    assert indexing.value["indexing_status"] == "indexed"

    readme_survey = call_tool_with_evidence(
        server,
        "source_index_builder",
        "set_file_survey_status",
        {"path": "README.md", "status": "skipped", "summary": "Entry file only; source.md contains indexed material."},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source README survey status was skipped.",
    )
    assert readme_survey.value["survey_status"] == "skipped"

    readme_indexing = call_tool_with_evidence(
        server,
        "source_index_builder",
        "set_file_indexing_status",
        {"path": "README.md", "status": "skipped"},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source README indexing status was skipped.",
    )
    assert readme_indexing.value["indexing_status"] == "skipped"

    valid_index = call_tool_with_evidence(
        server,
        "source_index_builder",
        "validate_source_index",
        {},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source index validation passed.",
    )
    assert valid_index.value["passed"] is True

    coverage = call_tool_with_evidence(
        server,
        "source_index_builder",
        "get_source_index_coverage",
        {},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source index coverage returned completed block and file counts.",
    )
    assert coverage.value["completed_block_count"] == 1
    assert coverage.value["indexed_file_count"] == 2

    removed_ref = call_tool_with_evidence(
        server,
        "source_index_builder",
        "remove_source_block_ref",
        {"block_id": "b_0001", "ref_id": "ref_0001"},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source block ref was removed.",
    )
    assert removed_ref.value["refs"] == []

    restore_with_evidence(
        ws.admin,
        ws.provider_repo,
        source_checkpoint.snapshot_id,
        scope_ids=["repo:Provider"],
        label="strict_tool_sweep_source_index",
        recorder=evidence_recorder,
    )
    missing_source_index = ws.runtime.material.get_source_index(ws.provider_repo)
    assert not missing_source_index.ok
    assert any(issue.kind == "source_index_missing" for issue in missing_source_index.issues)

    material_hits = call_tool_with_evidence(
        server,
        "source_index_builder",
        "search_source_text",
        {"query": "Runtime Matrix", "regex": False, "limit": 5},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source material search found source text.",
    )
    assert material_hits.value["hits"]

    source_range = call_tool_with_evidence(
        server,
        "source_index_builder",
        "read_source_range",
        {"path": "source.md", "start_line": 1, "end_line": 2, "context_lines": 0},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source range returned line-numbered text.",
    )
    assert "Runtime Matrix" in source_range.value["text_with_line_numbers"]

    valid_source_range = call_tool_with_evidence(
        server,
        "source_index_builder",
        "validate_source_range",
        {"path": "source.md", "start_line": 1, "end_line": 2},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source range validation accepted an existing range.",
    )
    assert valid_source_range.value["path"] == "source.md"

    source_ref_preview = call_tool_with_evidence(
        server,
        "source_index_builder",
        "preview_source_ref",
        {"path": "source.md", "start_line": 1, "end_line": 2, "context_lines": 0},
        runtime_context=source_ctx,
        recorder=evidence_recorder,
        assertion_summary="Source ref preview returned source context.",
    )
    assert source_ref_preview.value["material_kind"] == "source"

    _run_local_acquisition_tool_sweep(ws, server, evidence_recorder)

    _install_fake_mathlib_toolkit(ws)
    mathlib_ctx = _ctx(ws.provider_repo, view="mathlib_recon", agent_type="MathlibReconAgent", node_path="Main.Topic.Core")
    mathlib_plan_ctx = _ctx(
        ws.provider_repo,
        view="content_plan",
        agent_type="ContentPlanAgent",
        role="plan",
        node_path="Main.Topic.Core",
    )
    mathlib_checkpoint = checkpoint_with_evidence(
        ws.admin,
        ws.provider_repo,
        scope_ids=["repo:Provider"],
        label="strict_tool_sweep_mathlib_index_hints",
        recorder=evidence_recorder,
    )
    seeded_candidates = unwrap(
        ws.runtime.mathlib.search_external_mathlib(
            ws.provider_repo,
            query="Nat.mul",
            search_kinds=["theorem"],
            limit=3,
        )
    )
    assert seeded_candidates.candidates
    candidate_id = seeded_candidates.candidates[0].candidate_id
    recorded_module = call_tool_with_evidence(
        server,
        "mathlib_recon",
        "record_mathlib_module",
        {
            "module_name": "Mathlib.Data.Nat.Basic",
            "summary": "Strict ToolSweep natural number basics.",
            "source": "Runtime Matrix fake toolkit/lake access check.",
        },
        runtime_context=mathlib_ctx,
        recorder=evidence_recorder,
        assertion_summary="Checked Mathlib module entry was recorded.",
    )
    assert recorded_module.value["module"] == "Mathlib.Data.Nat.Basic"

    recorded_decl = call_tool_with_evidence(
        server,
        "mathlib_recon",
        "record_mathlib_decl",
        {
            "decl_name": "Nat.add_assoc",
            "module_name": "Mathlib.Data.Nat.Basic",
            "summary": "Strict ToolSweep associativity theorem.",
            "source": "Runtime Matrix checked declaration fixture.",
            "kind": "theorem",
            "signature": "Nat.add_assoc : (n m k : Nat) -> n + m + k = n + (m + k)",
            "snippet": "theorem Nat.add_assoc (n m k : Nat) : n + m + k = n + (m + k) := by omega",
        },
        runtime_context=mathlib_ctx,
        recorder=evidence_recorder,
        assertion_summary="Checked Mathlib declaration entry was recorded.",
    )
    assert recorded_decl.value["name"] == "Nat.add_assoc"

    important_decl = call_tool_with_evidence(
        server,
        "mathlib_recon",
        "add_mathlib_module_important_decl",
        {"module": "Mathlib.Data.Nat.Basic", "decl_name": "Nat.add_comm"},
        runtime_context=mathlib_ctx,
        recorder=evidence_recorder,
        assertion_summary="Important declaration was added to the Mathlib module entry.",
    )
    assert "Nat.add_comm" in important_decl.value["important_decl_names"]

    ingested = call_tool_with_evidence(
        server,
        "mathlib_recon",
        "ingest_mathlib_candidate",
        {
            "candidate_id": candidate_id,
            "summary": "Strict ToolSweep candidate ingestion theorem.",
            "note": "Seeded by fake toolkit search during strict ToolSweep setup.",
        },
        runtime_context=mathlib_ctx,
        recorder=evidence_recorder,
        assertion_summary="Cached Mathlib candidate was ingested into the local index.",
    )
    assert ingested.value["name"] == "Nat.mul_comm"

    module_entry = call_tool_with_evidence(
        server,
        "mathlib_recon",
        "get_mathlib_module_entry",
        {"module": "Mathlib.Data.Nat.Basic"},
        runtime_context=mathlib_ctx,
        recorder=evidence_recorder,
        assertion_summary="Mathlib module entry read returned recorded declarations.",
    )
    assert {"Nat.add_assoc", "Nat.add_comm", "Nat.mul_comm"} <= set(module_entry.value["important_decl_names"])

    decl_entry = call_tool_with_evidence(
        server,
        "mathlib_recon",
        "get_mathlib_decl_entry",
        {"name": "Nat.add_assoc"},
        runtime_context=mathlib_ctx,
        recorder=evidence_recorder,
        assertion_summary="Mathlib declaration entry read returned recorded declaration.",
    )
    assert decl_entry.value["module"] == "Mathlib.Data.Nat.Basic"

    mathlib = call_tool_with_evidence(
        server,
        "mathlib_recon",
        "search_mathlib_index",
        {"query": "Nat", "limit": 5},
        runtime_context=mathlib_ctx,
        recorder=evidence_recorder,
        assertion_summary="Local Mathlib index search returned recorded entries.",
    )
    assert mathlib.value["query"] == "Nat"
    assert mathlib.value["hits"]

    hints_before = call_tool_with_evidence(
        server,
        "content_plan",
        "get_current_node_mathlib_hints",
        {},
        runtime_context=mathlib_plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Current node Mathlib hint view was initially empty.",
    )
    assert hints_before.value["modules"] == []
    assert hints_before.value["declarations"] == []

    module_hint = call_tool_with_evidence(
        server,
        "content_plan",
        "add_current_mathlib_module_hint",
        {"module": "Mathlib.Data.Nat.Basic", "reason": "Strict ToolSweep module hint."},
        runtime_context=mathlib_plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Current node Mathlib module hint was added.",
    )
    assert module_hint.value["contract"]["mathlib_modules"][0]["module"] == "Mathlib.Data.Nat.Basic"

    decl_hint = call_tool_with_evidence(
        server,
        "content_plan",
        "add_current_mathlib_decl_hint",
        {"decl_name": "Nat.add_assoc", "reason": "Strict ToolSweep declaration hint."},
        runtime_context=mathlib_plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Current node Mathlib declaration hint was added.",
    )
    assert decl_hint.value["contract"]["mathlib_decls"][0]["name"] == "Nat.add_assoc"

    validated_hints = call_tool_with_evidence(
        server,
        "content_plan",
        "validate_current_node_mathlib_hints",
        {},
        runtime_context=mathlib_plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Current node Mathlib hints validated against the local index.",
    )
    assert validated_hints.value["passed"] is True
    assert validated_hints.value["issues"] == []

    removed_module_hint = call_tool_with_evidence(
        server,
        "content_plan",
        "remove_current_mathlib_module_hint",
        {"module": "Mathlib.Data.Nat.Basic"},
        runtime_context=mathlib_plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Current node Mathlib module hint was removed.",
    )
    assert removed_module_hint.value["contract"]["mathlib_modules"] == []

    removed_decl_hint = call_tool_with_evidence(
        server,
        "content_plan",
        "remove_current_mathlib_decl_hint",
        {"decl_name": "Nat.add_assoc"},
        runtime_context=mathlib_plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Current node Mathlib declaration hint was removed.",
    )
    assert removed_decl_hint.value["contract"]["mathlib_decls"] == []

    # The candidate cache is setup-only data for ingest_mathlib_candidate and
    # uses a different schema than repo-level IndexBundle files under indexes/.
    candidate_cache_path = ws.provider_repo / ".lean_constellation" / "indexes" / "mathlib_candidates.json"
    candidate_cache_path.unlink(missing_ok=True)
    restore_with_evidence(
        ws.admin,
        ws.provider_repo,
        mathlib_checkpoint.snapshot_id,
        scope_ids=["repo:Provider"],
        label="strict_tool_sweep_mathlib_index_hints",
        recorder=evidence_recorder,
    )
    restored_module_entry = ws.runtime.mathlib.get_mathlib_module_entry(ws.provider_repo, module="Mathlib.Data.Nat.Basic")
    assert not restored_module_entry.ok
    restored_hints = unwrap(ws.runtime.mathlib.get_node_mathlib_hint_view(ws.provider_repo, node_path="Main.Topic.Core"))
    assert restored_hints.modules == []
    assert restored_hints.declarations == []

    contract = call_tool_with_evidence(
        server,
        "content_plan",
        "get_current_node_contract",
        {},
        runtime_context=_ctx(ws.provider_repo, view="content_plan", agent_type="ContentPlanAgent", role="plan", node_path="Main.Core"),
        recorder=evidence_recorder,
        assertion_summary="Current node contract returned.",
    )
    assert contract.value["node_path"] == "Main.Core"

    deps = call_tool_with_evidence(
        server,
        "content_plan",
        "list_current_node_deps",
        {},
        runtime_context=_ctx(ws.provider_repo, view="content_plan", agent_type="ContentPlanAgent", role="plan", node_path="Main.Core"),
        recorder=evidence_recorder,
        assertion_summary="Current node dependency list returned.",
    )
    assert deps.value["node_path"] == "Main.Core"

    evidence_recorder.record_runtime_state(ws.runtime)
    assert core_tool_sweep_names() <= evidence_recorder.evidence.application_tool_names
    evidence_recorder.export_json(tmp_path / "runtime_matrix_evidence" / "tool_sweep_subset.json")
    evidence_recorder.export_markdown_summary(tmp_path / "runtime_matrix_evidence" / "tool_sweep_subset.md")


def _ctx(
    repo_root: Path,
    *,
    view: str,
    agent_type: str,
    role: str = "worker",
    node_path: str | None = None,
    flow_id: str | None = None,
    step_id: str | None = None,
) -> RuntimeToolContext:
    return RuntimeToolContext(
        flow_id=flow_id or f"strict_runtime_matrix_{view}",
        step_id=step_id or f"strict_runtime_matrix_{view}_step",
        agent_id=f"strict_runtime_matrix_{view}_agent",
        agent_type=agent_type,
        agent_role=role,  # type: ignore[arg-type]
        expected_view_key=view,
        repo_root=repo_root,
        node_path=node_path,
        node_kind="content" if node_path else None,
        contract_version=1 if node_path else None,
    )


def _install_fake_mathlib_toolkit(ws: RuntimeMatrixWorkspace) -> None:
    def dispatch(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "lean_explore.find":
            query = str(payload.get("query") or "")
            if "Nat.mul" in query:
                return {
                    "results": [
                        {
                            "name": "Nat.mul_comm",
                            "module": "Mathlib.Data.Nat.Basic",
                            "kind": "theorem",
                            "type": "Nat.mul_comm : (n m : Nat) -> n * m = m * n",
                            "docstring": "Commutativity of natural number multiplication.",
                            "source_text": "theorem Nat.mul_comm (n m : Nat) : n * m = m * n := by omega",
                        }
                    ]
                }
            return {"results": []}
        if tool_name == "mathlib_nav.file_outline":
            return {
                "imports": ["Mathlib.Init"],
                "declarations": [
                    {
                        "name": "Nat.add_assoc",
                        "kind": "theorem",
                        "signature": "Nat.add_assoc : (n m k : Nat) -> n + m + k = n + (m + k)",
                    },
                    {
                        "name": "Nat.mul_comm",
                        "kind": "theorem",
                        "signature": "Nat.mul_comm : (n m : Nat) -> n * m = m * n",
                    },
                ],
            }
        if tool_name == "check_mathlib_name":
            return {"passed": True, "diagnostics": []}
        if tool_name in {"lsp.run_snippet", "run_snippet"}:
            return {"diagnostics": []}
        raise KeyError(tool_name)

    toolkit = LeanMcpToolkitClient(dispatcher=dispatch)
    ws.runtime.external.lean_mcp_toolkit = toolkit
    ws.runtime.external.lean_toolkit = toolkit
    ws.runtime.external.lean_toolchain.toolkit = toolkit


def _run_local_acquisition_tool_sweep(ws: RuntimeMatrixWorkspace, server: Any, recorder: EvidenceRecorder) -> None:
    source_root = ws.provider_repo / ".lean_constellation" / "source"
    local_source = str(ws.resources.local_file)
    resource_flow_id = _start_resource_curation_for_tool_sweep(ws, target_kind="local_file", target=local_source)
    run_next_created_step(ws.admin, resource_flow_id)
    resource_flow = ws.runtime.ark.flow_service.get_flow(resource_flow_id)
    resource_draft_id = resource_flow.state.active_resource_draft_key
    assert resource_draft_id is not None
    resource_draft = unwrap(ws.runtime.material.get_resource_draft(ws.provider_repo, draft_id=resource_draft_id))
    resource_draft_root = Path(resource_draft.draft_root)
    source_prepare_ctx = _ctx(
        ws.provider_repo,
        view="source_corpus_prepare",
        agent_type="SourceCorpusPrepareAgent",
        role="worker",
    )
    resource_ctx = _ctx(
        ws.provider_repo,
        view="resource_curator",
        agent_type="ResourceCuratorAgent",
        role="worker",
        flow_id=resource_flow_id,
    )
    checkpoint = checkpoint_with_evidence(
        ws.admin,
        ws.provider_repo,
        scope_ids=["repo:Provider"],
        label="strict_tool_sweep_local_acquisition",
        recorder=recorder,
    )

    acquired_source = call_tool_with_evidence(
        server,
        "source_corpus_prepare",
        "acquire_source_material",
        {"target": local_source, "preferred_kind": "local_file"},
        runtime_context=source_prepare_ctx,
        recorder=recorder,
        assertion_summary="Local source material acquisition copied a local file into source draft originals.",
    )
    source_artifact_ref = acquired_source.value["primary_artifact_ref"]
    assert source_artifact_ref
    assert (source_root / source_artifact_ref).exists()

    extracted_source = call_tool_with_evidence(
        server,
        "source_corpus_prepare",
        "extract_source_artifact",
        {"artifact_ref": source_artifact_ref, "extraction_kind": "text_normalize"},
        runtime_context=source_prepare_ctx,
        recorder=recorder,
        assertion_summary="Local source artifact extraction normalized text material.",
    )
    assert "Local Runtime Matrix Note" in (extracted_source.value["preview"] or "")
    assert (source_root / extracted_source.value["primary_material_ref"]).exists()

    imported_source = call_tool_with_evidence(
        server,
        "source_corpus_prepare",
        "import_source_material",
        {"source_path": local_source, "as_name": "strict_source_import.md"},
        runtime_context=source_prepare_ctx,
        recorder=recorder,
        assertion_summary="Local source material import created a named source draft artifact.",
    )
    imported_source_ref = imported_source.value["primary_artifact_ref"]
    assert imported_source_ref == "original/strict_source_import.md"

    normalized_source = call_tool_with_evidence(
        server,
        "source_corpus_prepare",
        "normalize_source_text_material",
        {"material_ref": imported_source_ref},
        runtime_context=source_prepare_ctx,
        recorder=recorder,
        assertion_summary="Local source material normalization produced readable text.",
    )
    assert normalized_source.value["primary_material_ref"] == "normalized/strict_source_import.txt"

    acquired_resource = call_tool_with_evidence(
        server,
        "resource_curator",
        "acquire_resource_material",
        {"target": local_source, "preferred_kind": "local_file"},
        runtime_context=resource_ctx,
        recorder=recorder,
        assertion_summary="Local resource acquisition copied a local file into the active resource draft originals.",
    )
    resource_artifact_ref = acquired_resource.value["primary_artifact_ref"]
    assert resource_artifact_ref
    assert (resource_draft_root / resource_artifact_ref).exists()

    extracted_resource = call_tool_with_evidence(
        server,
        "resource_curator",
        "extract_resource_artifact",
        {"artifact_ref": resource_artifact_ref, "extraction_kind": "text_normalize"},
        runtime_context=resource_ctx,
        recorder=recorder,
        assertion_summary="Local resource artifact extraction normalized text material in the active draft.",
    )
    assert "Local Runtime Matrix Note" in (extracted_resource.value["preview"] or "")

    imported_resource = call_tool_with_evidence(
        server,
        "resource_curator",
        "import_resource_material",
        {"source_path": local_source, "as_name": "strict_resource_import.md"},
        runtime_context=resource_ctx,
        recorder=recorder,
        assertion_summary="Local resource import created a named artifact in the active draft.",
    )
    imported_resource_ref = imported_resource.value["primary_artifact_ref"]
    assert imported_resource_ref == "original/strict_resource_import.md"

    normalized_resource = call_tool_with_evidence(
        server,
        "resource_curator",
        "normalize_resource_text_material",
        {"material_ref": imported_resource_ref},
        runtime_context=resource_ctx,
        recorder=recorder,
        assertion_summary="Local resource normalization produced readable text in the active draft.",
    )
    assert normalized_resource.value["primary_material_ref"] == "normalized/strict_resource_import.txt"

    restore_with_evidence(
        ws.admin,
        ws.provider_repo,
        checkpoint.snapshot_id,
        scope_ids=["repo:Provider"],
        label="strict_tool_sweep_local_acquisition",
        recorder=recorder,
    )
    assert not (source_root / "original" / "strict_source_import.md").exists()
    assert not (source_root / "normalized" / "strict_source_import.txt").exists()
    assert not (resource_draft_root / "original" / "strict_resource_import.md").exists()
    assert not (resource_draft_root / "normalized" / "strict_resource_import.txt").exists()


def _start_resource_curation_for_tool_sweep(ws: RuntimeMatrixWorkspace, *, target_kind: str, target: str) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="resource_curation",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(ws.provider_repo),
                "target_kind": target_kind,
                "target": target,
                "requested_by": "strict_tool_sweep",
                "context_summary": "Strict ToolSweep active resource draft setup.",
                "node_path": "Main.Core",
            },
        )
    )


def _item_field(item: Any, field: str) -> Any:
    if isinstance(item, dict):
        return item[field]
    return getattr(item, field)


def _run_adapter_tool_sweep(ws: RuntimeMatrixWorkspace, server: Any, recorder: EvidenceRecorder) -> None:
    adapter_ctx = _ctx(ws.adapter_repo, view="adapter_repo_import", agent_type="AdapterDeclCatalogAgent", role="worker")
    inspected_input = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "inspect_adapter_input",
        {},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Adapter preparation input was inspected.",
    )
    assert inspected_input.value["goal"]

    upstream_metadata = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "get_adapter_upstream_metadata",
        {},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Adapter upstream metadata was loaded.",
    )
    assert upstream_metadata.value["dependency_name"] == "upstream"

    upstream_status = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "get_adapter_upstream_status",
        {},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Adapter upstream status was loaded.",
    )
    assert upstream_status.value["trusted_build"] is True

    checkpoint = checkpoint_with_evidence(
        ws.admin,
        ws.adapter_repo,
        scope_ids=["repo:Adapter"],
        label="strict_tool_sweep_adapter_core",
        recorder=recorder,
    )

    upstream_decl_search = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "search_upstream_declarations",
        {"query": "upstreamSmoke", "kind_filter": "theorem", "module_filter": "Upstream", "limit": 5},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Local upstream declaration search returned upstreamSmoke.",
    )
    assert any(
        _item_field(item, "lean_decl_name") == "upstreamSmoke"
        for item in upstream_decl_search.value["items"]
    )

    upstream_modules = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "search_upstream_modules",
        {"query": "Upstream", "limit": 5},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Local upstream module search returned Upstream.",
    )
    assert any(_item_field(item, "module") == "Upstream" for item in upstream_modules.value["items"])

    upstream_module_decls = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "list_upstream_module_declarations",
        {"module": "Upstream", "kind_filter": "theorem"},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Local upstream module declaration list returned both fixture theorems.",
    )
    assert {"upstreamSmoke", "upstreamAddZero"} <= {
        _item_field(item, "lean_decl_name")
        for item in upstream_module_decls.value["declarations"]
    }

    upstream_decl = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "inspect_upstream_declaration",
        {"module": "Upstream", "lean_decl_name": "upstreamSmoke"},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Local upstream declaration inspection returned fixture code.",
    )
    assert "theorem upstreamSmoke" in upstream_decl.value["code_excerpt"]

    upstream_context = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "read_upstream_source_context",
        {"module": "Upstream", "lean_decl_name": "upstreamSmoke", "line_window": 2},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Local upstream source context read returned line-numbered fixture text.",
    )
    assert "upstreamSmoke" in upstream_context.value["text"]

    upstream_capture = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "capture_upstream_declaration_code",
        {"module": "Upstream", "lean_decl_name": "upstreamSmoke", "capture_mode": "full_declaration"},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Local upstream declaration capture returned policy-clean code.",
    )
    assert "theorem upstreamSmoke" in upstream_capture.value["code"]
    assert upstream_capture.value["scan"]["contains_sorry"] is False

    upstream_imports = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "inspect_upstream_module_imports",
        {"module": "Upstream"},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Local upstream module import inspection returned package hints.",
    )
    assert "upstream" in upstream_imports.value["package_hints"]

    _create_complete_adapter_decl(
        server,
        adapter_ctx,
        recorder,
        name="support",
        statement="theorem support : True := by\n  trivial",
        summary="Strict ToolSweep support theorem.",
    )
    finalized_support = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "finalize_adapter_decl",
        {"name": "support"},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Support adapter declaration was finalized.",
    )
    assert finalized_support.value["finalized"] is True

    created_main = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "create_adapter_decl",
        {
            "name": "main_result",
            "kind": "theorem",
            "module": "Upstream",
            "lean_decl_name": "upstreamSmoke",
            "summary": "Expose the upstream smoke theorem.",
        },
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Main adapter declaration was created.",
    )
    assert created_main.value["name"] == "main_result"

    statement_formal = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "set_adapter_statement_formal",
        {
            "name": "main_result",
            "code": "theorem upstreamSmoke : True := by\n  sorry",
        },
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Main adapter formal statement was written.",
    )
    assert statement_formal.value["revision"]["statement"]["formal"]["code"]
    matched_by_upstream = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "find_adapter_decl_by_upstream",
        {"module": "Upstream", "lean_decl_name": "upstreamSmoke", "adapter_name_query": None},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Adapter duplicate lookup found the main declaration by upstream declaration.",
    )
    assert any(_item_field(item, "name") == "main_result" for item in matched_by_upstream.value["matches"])

    statement_nl = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "set_adapter_statement_nl",
        {
            "name": "main_result",
            "text": "The upstream smoke theorem states True.\n\nRuntime Matrix adapter ToolSweep.",
        },
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Main adapter natural-language statement was written.",
    )
    assert statement_nl.value["revision"]["statement"]["nl"]["text"]

    statement_origin = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "add_adapter_statement_origin",
        {
            "name": "main_result",
            "origin_text": "Upstream smoke theorem docstring.",
            "source_hint": "Upstream",
        },
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Statement origin text was recorded.",
    )
    assert statement_origin.value["revision"]["statement"]["nl"]["origin"]

    statement_dep = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "add_adapter_statement_dep",
        {"name": "main_result", "dep_name": "support", "reason": "Support theorem used by statement."},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Statement dependency was added.",
    )
    assert "support" in str(statement_dep.value["revision"]["statement"]["deps"])

    removed_statement_dep = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "remove_adapter_statement_dep",
        {"name": "main_result", "dep_name": "support"},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Statement dependency was removed.",
    )
    assert "support" not in str(removed_statement_dep.value["revision"]["statement"]["deps"])

    proof_formal = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "set_adapter_proof_formal",
        {
            "name": "main_result",
            "code": "theorem upstreamSmoke : True := by\n  trivial",
        },
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Main adapter formal proof was written.",
    )
    assert proof_formal.value["revision"]["proof"]["formal"]["code"]

    proof_nl = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "set_adapter_proof_nl",
        {"name": "main_result", "text": "The theorem follows by triviality."},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Main adapter natural-language proof was written.",
    )
    assert proof_nl.value["revision"]["proof"]["nl"]["text"]

    proof_origin = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "add_adapter_proof_origin",
        {"name": "main_result", "origin_text": "Upstream proof by triviality.", "source_hint": "Upstream"},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Proof origin text was recorded.",
    )
    assert proof_origin.value["revision"]["proof"]["nl"]["origin"]

    proof_dep = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "add_adapter_proof_dep",
        {"name": "main_result", "dep_name": "support", "reason": "Support theorem used by proof."},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Proof dependency was added.",
    )
    assert "support" in str(proof_dep.value["revision"]["proof"]["deps"])

    removed_proof_dep = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "remove_adapter_proof_dep",
        {"name": "main_result", "dep_name": "support"},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Proof dependency was removed.",
    )
    assert "support" not in str(removed_proof_dep.value["revision"]["proof"]["deps"])

    decls = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "list_adapter_decls",
        {"module_filter": None, "kind_filter": None, "name_query": None},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Adapter declaration list returned support and main declarations.",
    )
    assert {_item_field(item, "name") for item in decls.value["items"]} >= {"support", "main_result"}

    inspected_decl = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "inspect_adapter_decl",
        {"name": "main_result"},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Adapter declaration inspection returned main_result.",
    )
    assert inspected_decl.value["name"] == "main_result"

    modules = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "list_registered_adapter_modules",
        {},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Registered adapter modules included Upstream.",
    )
    assert any(item["module"] == "Upstream" for item in modules.value["modules"])

    completeness = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "check_adapter_decl_completeness",
        {"name": "main_result"},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Adapter declaration completeness gate passed.",
    )
    assert completeness.value["complete"] is True

    finalized_main = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "finalize_adapter_decl",
        {"name": "main_result"},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Main adapter declaration was finalized.",
    )
    assert finalized_main.value["finalized"] is True

    unbound_before = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "list_unbound_adapter_interfaces",
        {},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Adapter unbound interface list returned main_result.",
    )
    assert "main_result" in unbound_before.value["interfaces"]

    binding_gate_before = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "validate_adapter_interface_bindings",
        {},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Adapter interface binding gate reported missing binding.",
    )
    assert binding_gate_before.value["passed"] is False

    bound = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "bind_adapter_interface",
        {
            "interface_name": "main_result",
            "decl_name": "main_result",
            "binding_summary": "Strict ToolSweep binds main interface.",
        },
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Adapter interface was bound to finalized declaration.",
    )
    assert bound.value["bound_decl"]["name"] == "main_result"

    unbound = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "unbind_adapter_interface",
        {"interface_name": "main_result", "reason": "Strict ToolSweep unbind branch."},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Adapter interface was unbound.",
    )
    assert unbound.value["changed"] is True

    rebound = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "bind_adapter_interface",
        {
            "interface_name": "main_result",
            "decl_name": "main_result",
            "binding_summary": "Strict ToolSweep rebinds main interface for ready gate.",
        },
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Adapter interface was rebound for ready gate.",
    )
    assert rebound.value["bound_decl"]["name"] == "main_result"

    binding_gate_after = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "validate_adapter_interface_bindings",
        {},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Adapter interface binding gate passed after rebind.",
    )
    assert binding_gate_after.value["passed"] is True

    import_preview = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "preview_adapter_import_modules",
        {},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Adapter import preview returned Upstream.",
    )
    assert any(item["module"] == "Upstream" for item in import_preview.value["modules"])

    projection_before = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "check_adapter_projection",
        {},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Adapter projection gate reported missing projection before refresh.",
    )
    assert projection_before.value["passed"] is False

    catalog_preflight = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "check_adapter_catalog_ready_preflight",
        {},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Adapter catalog preflight passed before Flow-owned projection refresh.",
    )
    assert catalog_preflight.value["passed"] is True

    ready_before_projection = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "check_adapter_ready",
        {},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary="Full adapter ready gate remains blocked until Flow-owned projection refresh.",
    )
    assert ready_before_projection.value["passed"] is False

    restore_with_evidence(
        ws.admin,
        ws.adapter_repo,
        checkpoint.snapshot_id,
        scope_ids=["repo:Adapter"],
        label="strict_tool_sweep_adapter_core",
        recorder=recorder,
    )
    assert not (ws.adapter_repo / "Main" / "Interfaces.lean").exists()
    restored_decls = ws.runtime.adapter.list_adapter_decls(ws.adapter_repo)
    assert not restored_decls.ok or restored_decls.value is None or restored_decls.value == []


def _create_complete_adapter_decl(
    server: Any,
    adapter_ctx: RuntimeToolContext,
    recorder: EvidenceRecorder,
    *,
    name: str,
    statement: str,
    summary: str,
) -> None:
    created = call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "create_adapter_decl",
        {"name": name, "kind": "theorem", "module": "Upstream", "lean_decl_name": name, "summary": summary},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary=f"Adapter declaration {name} was created.",
    )
    assert created.value["name"] == name
    assert call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "set_adapter_statement_formal",
        {"name": name, "code": statement},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary=f"Adapter declaration {name} formal statement was written.",
    ).ok
    assert call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "set_adapter_statement_nl",
        {"name": name, "text": f"Statement for {name}."},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary=f"Adapter declaration {name} statement summary was written.",
    ).ok
    assert call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "set_adapter_proof_formal",
        {"name": name, "code": statement},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary=f"Adapter declaration {name} formal proof was written.",
    ).ok
    assert call_tool_with_evidence(
        server,
        "adapter_repo_import",
        "set_adapter_proof_nl",
        {"name": name, "text": f"Proof for {name}."},
        runtime_context=adapter_ctx,
        recorder=recorder,
        assertion_summary=f"Adapter declaration {name} proof summary was written.",
    ).ok


def _ensure_content_node(ws: RuntimeMatrixWorkspace) -> None:
    assert ws.runtime.node.ensure_native_root_main_contract(ws.provider_repo).ok
    created = ws.runtime.node.create_content_node(
        ws.provider_repo,
        path="Main.Core",
        goal="Strict Runtime Matrix content goal.",
        boundary="Use local strict runtime matrix fixtures.",
        objective="Exercise implemented ToolSweep cases.",
        success_criteria="Implemented ToolSweep cases return structured views.",
    )
    if not created.ok:
        assert any(issue.kind == "node_path_exists" for issue in created.issues), created.issues


def _ensure_node_tool_fixture(ws: RuntimeMatrixWorkspace) -> None:
    ws.setup_content_node(node_path="Main.Topic.Core")
    ws.setup_content_node(node_path="Main.Topic.Helper")
    committed = ws.runtime.node.commit_content_contract(
        ws.provider_repo,
        node_path="Main.Topic.Helper",
        summary="Strict ToolSweep helper boundary ready.",
    )
    if not committed.ok:
        assert any(issue.kind in {"node_contract_already_committed", "invalid_state"} for issue in committed.issues), committed.issues


def _ensure_workspace_requirements(ws: RuntimeMatrixWorkspace) -> None:
    assert ws.runtime.repo_workspace.metadata.ensure_repo_model(ws.consumer_repo).ok
    created = ws.runtime.repo_workspace.create_requirement_with_interfaces(
        ws.consumer_repo,
        name="need_provider",
        target_repo="ReadyAnalysis",
        source_description="Strict ToolSweep provider requirement.",
        reason="Exercise requirement tools.",
        interfaces=[],
    )
    assert created.ok, created.issues
    open_created = ws.runtime.repo_workspace.create_requirement_with_interfaces(
        ws.consumer_repo,
        name="need_open_provider",
        target_repo="OpenAnalysis",
        source_description="Strict ToolSweep open provider requirement.",
        reason="Exercise open requirement group tools.",
        interfaces=[],
    )
    assert open_created.ok, open_created.issues
