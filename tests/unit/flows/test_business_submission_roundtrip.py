from __future__ import annotations

from agent_runtime_kit.flow.models import ChildFlowDispatchSubmission, FlowRequest

from lean_constellation.flows.common.agent_steps import BUSINESS_AGENT_STEP_TYPES


def _sample_payload(cls):
    base = {
        "submission_id": f"sub_{cls.__name__}",
        "tool_name": "submit_unit",
        "summary": "unit summary",
        "repo_key": "Repo",
        "node_path": "Main.Core",
    }
    fields = set(cls.model_fields)
    if issubclass(cls, ChildFlowDispatchSubmission):
        base.update(
            {
                "requests": [FlowRequest(flow_type="unit_child", scope_id="scope", params={"ok": True})],
                "continuation": "wait_for_callback",
            }
        )
    extras = {
        "git_url": "https://github.com/example/project",
        "revision": "main",
        "subdir": "lean",
        "package_name": "Project",
        "likely_import_module": "Project",
        "known_risks": [],
        "searched_targets": [],
        "rejected_candidates": [],
        "relpath": ".lean_constellation/source",
        "entry_path": "README.md",
        "overview": "Overview",
        "preparation_summary": "Prepared",
        "reason": "Need more information.",
        "attempted_targets": ["https://example.com"],
        "missing_materials": ["appendix"],
        "approved": True,
        "feedback": None,
        "missing_interfaces": ["main_result"],
        "evidence_summary": "Checked upstream.",
        "target_kind": "web",
        "target": "https://example.com/paper",
        "existing_kind": "source",
        "duplicate_reason": "Already present.",
        "existing_source_path": "README.md",
        "draft_id": "draft_1",
        "resource_key": "res_1",
        "source_description": "External source",
        "node_paths": ["Main.Core"],
        "requirement_name": "req",
        "target_repo": "Provider",
        "interfaces": [],
        "provider_route": {"kind": "auto"},
        "explorations": [{"kind": "mathlib", "objective": "Find imports."}],
        "recon_kind": "mathlib",
        "objective": "Find imports.",
        "context_summary": "Context",
        "requested_use": "supporting_material",
        "consumer_need": "Need supporting source context.",
        "classification_reason": "This is supporting material.",
        "resource_role": "Background reference.",
        "consumer_formalization_scope": "The current repo owns the theorem proof.",
        "relation_to_current_repo_or_node": "Supplies one dependency to Main.Core.",
        "provider_scope": "Own the reusable external theory.",
        "strategy_id": "strategy_1",
        "round_id": "round_1",
        "round_index": 0,
        "dependency_change_summary": "Updated node deps.",
        "checked_boundary_summary": "Checked visible boundaries.",
        "index_update_summary": "Updated Mathlib index.",
        "node_mathlib_hint_summary": "Updated current-node Mathlib hints.",
        "material_change_summary": "Updated material refs.",
        "checked_material_summary": "Checked material refs.",
        "useful_findings": ["Main.Base"],
        "unresolved_within_visible_boundaries": [],
        "unresolved_in_mathlib": [],
        "unresolved_material_needs": [],
        "missing_targets": [],
        "stage": "statement_nl",
        "completed_decl_names": ["main_result"],
        "affected_decl_names": ["main_result"],
        "accepted": True,
        "retry_required": False,
        "details": [],
    }
    for key, value in extras.items():
        if key in fields:
            base[key] = value
    if "feedback" in fields and "reviewed_decl_names" in fields:
        base["feedback"] = []
    return base


def test_all_business_submissions_roundtrip_without_payload_downgrade() -> None:
    submission_types: dict[str, type] = {}
    for step_cls in BUSINESS_AGENT_STEP_TYPES:
        submission_types.update(step_cls.Submissions)

    assert len(submission_types) >= 20
    for submission_type, cls in submission_types.items():
        payload = _sample_payload(cls)
        parsed = cls.model_validate(payload)
        dumped = parsed.model_dump(mode="json")
        reparsed = cls.model_validate(dumped)

        assert type(reparsed) is cls
        assert reparsed.submission_type == submission_type
        if isinstance(reparsed, ChildFlowDispatchSubmission):
            assert reparsed.submission_type != "child_flow_dispatch"
            assert reparsed.requests[0].flow_type == "unit_child"
