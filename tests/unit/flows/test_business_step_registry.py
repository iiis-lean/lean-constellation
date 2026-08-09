from __future__ import annotations

from agent_runtime_kit.flow.registry import StepTypeRegistry
from agent_runtime_kit.flow.standard_steps import DispatchStep

from lean_constellation.flows.common.agent_steps import BUSINESS_AGENT_STEP_TYPES
from lean_constellation.flows.repo_exploration import REPO_EXPLORATION_AGENT_STEP_TYPES
from lean_constellation.flows.registry import BUSINESS_LOGIC_STEP_TYPES, register_lean_flow_step_types


def test_business_agent_step_shells_register_and_parse_submissions() -> None:
    registry = StepTypeRegistry()
    registered = register_lean_flow_step_types(step_registry=registry)

    assert set(registered) == {
        DispatchStep.step_type,
        *(step_cls.step_type for step_cls in BUSINESS_LOGIC_STEP_TYPES),
        *(step_cls.step_type for step_cls in BUSINESS_AGENT_STEP_TYPES),
        *(step_cls.step_type for step_cls in REPO_EXPLORATION_AGENT_STEP_TYPES),
    }

    for step_cls in (*BUSINESS_AGENT_STEP_TYPES, *REPO_EXPLORATION_AGENT_STEP_TYPES):
        for submission_type, submission_cls in step_cls.Submissions.items():
            fields = set(submission_cls.model_fields)
            payload = {
                "submission_id": "sub_1",
                "submission_type": submission_type,
                "tool_name": next(iter(step_cls.SubmitTools or {"submit_unit"})),
                "summary": "ok",
            }
            if "requests" in fields:
                payload["requests"] = [{"flow_type": "unit_child", "scope_id": "scope", "params": {}}]
            for key in fields:
                if key in payload or key in {"submitted_by_agent_id", "submitted_at", "repo_key", "node_path", "arxiv_version", "summary", "continuation"}:
                    continue
                if key == "provider_route":
                    payload[key] = {"kind": "auto"}
                elif key == "verified_route":
                    payload[key] = {
                        "git_url": "git_url_value",
                        "revision": "a" * 40,
                        "subdir": "subdir_value",
                        "package_name": "package_value",
                        "likely_import_module": "module_value",
                        "lean_toolchain": "leanprover/lean4:v4.28.0",
                        "expected_lean_toolchain": "leanprover/lean4:v4.28.0",
                        "expected_mathlib_revision": "v4.28.0",
                        "revision_resolution": "explicit",
                        "candidates_checked": ["a" * 40],
                        "evidence_summary": "Verified route.",
                    }
                elif key == "revision":
                    payload[key] = "a" * 40
                elif key == "searched_targets":
                    payload[key] = ["provider theorem search"]
                elif key == "explorations":
                    payload[key] = [{"kind": "mathlib", "objective": "Find imports."}]
                elif key.endswith("_names") or key.endswith("_deps") or key.endswith("_refs") or key in {
                    "interfaces",
                    "candidates",
                    "created_modules",
                    "reused_modules",
                    "created_declarations",
                    "reused_declarations",
                    "unresolved",
                    "usage_notes",
                    "attempted_targets",
                    "missing_materials",
                    "missing_interfaces",
                    "details",
                    "missing_targets",
                    "node_paths",
                    "useful_findings",
                        "unresolved_within_visible_boundaries",
                        "unresolved_in_mathlib",
                        "unresolved_material_needs",
                        "blocked_needs",
                        "known_risks",
                        "rejected_candidates",
                    }:
                        payload[key] = []
                elif key == "feedback" and "reviewed_decl_names" in fields:
                    payload[key] = []
                elif key in {"approved", "accepted", "retry_required"}:
                    payload[key] = True
                elif key == "outcome" and submission_type.endswith("_result"):
                    payload[key] = "completed"
                elif key in {"round_index"}:
                    payload[key] = 0
                elif key in {"target_kind"}:
                    payload[key] = "web"
                elif key == "requested_use":
                    payload[key] = "supporting_material"
                elif key in {"existing_kind"}:
                    payload[key] = "source"
                elif key in {"recon_kind"}:
                    payload[key] = "mathlib"
                elif key in {"source_corpus_mode"}:
                    payload[key] = "prepare"
                elif key in {"required_proof_availability"}:
                    payload[key] = "declared"
                else:
                    payload[key] = f"{key}_value"

            if submission_type in {
                "repo_resource_discovery_result",
                "repo_lean_provider_discovery_result",
            }:
                payload["outcome"] = "no_useful_findings"

            parsed = registry.parse_submission(step_cls.step_type, payload)
            assert isinstance(parsed, submission_cls)
