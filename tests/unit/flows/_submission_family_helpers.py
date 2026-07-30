from __future__ import annotations

from agent_runtime_kit.flow.models import ChildFlowDispatchSubmission, FlowRequest


def instantiate_submission(cls, **overrides):
    fields = set(cls.model_fields)
    payload = {
        "submission_id": "sub_family",
        "tool_name": "submit_family",
        "summary": "family summary",
        "repo_key": "Repo",
        "node_path": "Main.Core",
    }
    if issubclass(cls, ChildFlowDispatchSubmission):
        payload["requests"] = [FlowRequest(flow_type="unit_child", scope_id="scope", params={"ok": True})]
    defaults = {
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
        "reason": "Reason",
        "attempted_targets": [],
        "missing_materials": [],
        "approved": True,
        "feedback": None,
        "missing_interfaces": [],
        "evidence_summary": "Evidence",
        "target_kind": "web",
        "target": "https://example.com",
        "existing_kind": "source",
        "duplicate_reason": "Duplicate",
        "existing_source_path": "README.md",
        "draft_id": "draft_1",
        "resource_key": "res_1",
        "source_description": "Source",
        "node_paths": ["Main.Core"],
        "requirement_name": "req",
        "target_repo": "Provider",
        "interfaces": [],
        "provider_route": {"kind": "auto"},
        "recon_kind": "mathlib",
        "objective": "Objective",
        "context_summary": "Context",
        "explorations": [
            {
                "kind": "resource",
                "objective": "Find a relevant external resource.",
            }
        ],
        "strategy_id": "strategy_1",
        "round_id": "round_1",
        "round_index": 0,
        "dependency_change_summary": "Updated node deps.",
        "checked_boundary_summary": "Checked visible boundaries.",
        "index_update_summary": "Updated Mathlib index.",
        "node_mathlib_hint_summary": "Updated current-node Mathlib hints.",
        "material_change_summary": "Updated material refs.",
        "checked_material_summary": "Checked material refs.",
        "useful_findings": [],
        "unresolved_within_visible_boundaries": [],
        "unresolved_in_mathlib": [],
        "unresolved_material_needs": [],
        "missing_targets": [],
        "stage": "statement_nl",
        "completed_decl_names": [],
        "affected_decl_names": [],
        "accepted": True,
        "retry_required": False,
        "details": [],
    }
    for key, value in defaults.items():
        if key in fields:
            payload[key] = value
    if "feedback" in fields and "reviewed_decl_names" in fields:
        payload["feedback"] = []
    payload.update(overrides)
    return cls.model_validate(payload)


def assert_roundtrip(*classes):
    for cls in classes:
        parsed = instantiate_submission(cls)
        reparsed = cls.model_validate(parsed.model_dump(mode="json"))
        assert type(reparsed) is cls
        if isinstance(reparsed, ChildFlowDispatchSubmission):
            assert reparsed.requests
