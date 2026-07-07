from __future__ import annotations

import re

from lean_constellation.agents import build_agent_type_specs, get_agent_type_spec, render_agent_instruction
from lean_constellation.agents.surface import build_agent_surface_reports
from lean_constellation.tools import build_application_tool_specs, build_submit_tool_specs


_CODE_REF_RE = re.compile(r"`([^`]+)`")
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]+$")


def _registered_tool_names() -> set[str]:
    return {spec.name for spec in build_application_tool_specs()} | {spec.name for spec in build_submit_tool_specs()}


def _unknown_tool_refs(text: str) -> list[str]:
    tool_names = _registered_tool_names()
    return sorted(_tool_refs(text) - tool_names)


def _tool_refs(text: str) -> set[str]:
    unknown: list[str] = []
    for ref in _CODE_REF_RE.findall(text):
        if _TOOL_NAME_RE.fullmatch(ref) and "_" in ref:
            unknown.append(ref)
    return set(unknown)


def test_instruction_renderer_combines_public_and_agent_specific_fragments() -> None:
    text = render_agent_instruction("CoordinatorAgent")

    assert "## Operating Contract" in text
    assert "## Native Repository Coordinator" in text
    assert "get_current_repo_work_config" in text
    assert "matching Coordinator mode skill" in text
    assert "coordinator-node-decomposition" in text
    assert "Do not write DeclGraph artifacts" in text


def test_instruction_renderer_deduplicates_public_fragments() -> None:
    spec = get_agent_type_spec("ContentPlanAgent")
    duplicate = spec.model_copy(
        update={
            "instruction_fragment_keys": [
                "common.runtime_contract",
                *spec.instruction_fragment_keys,
                "common.runtime_contract",
            ]
        }
    )

    text = render_agent_instruction(duplicate)

    assert text.count("## Operating Contract") == 1


def test_runtime_instruction_output_is_english() -> None:
    text = render_agent_instruction("ProofFormalWorkerAgent")

    assert re.search(r"[\u3400-\u9fff]", text) is None
    assert "## Proof Formal Worker" in text


def test_content_plan_instruction_spells_out_operational_flow_and_tools() -> None:
    text = render_agent_instruction("ContentPlanAgent")

    assert "After every callback, re-read current truth" in text
    assert "get_current_repo_work_config" in text
    assert "matching the current work_mode" in text
    assert "submit_content_preparation_recon" in text
    assert "write_decl_change_summary" in text
    assert "mark_decl_round_terminal" in text
    assert "validate_decl_round_draft" in text
    assert "submit_current_decl_round" in text
    assert "check_current_content_node_completion" in text
    assert "submit_content_node_ready" in text
    assert "Do not replace NodeDirDependencyReconFlow, MathlibReconFlow, or ResourceReconFlow" in text


def test_all_runtime_instructions_are_english_and_tool_refs_resolve() -> None:
    for spec in build_agent_type_specs():
        text = render_agent_instruction(spec)

        assert re.search(r"[\u3400-\u9fff]", text) is None, spec.agent_type
        assert not _unknown_tool_refs(text), spec.agent_type


def test_formal_stage_instructions_match_stage_specific_tool_boundaries() -> None:
    statement_worker = render_agent_instruction("StatementFormalWorkerAgent")
    statement_reviewer = render_agent_instruction("StatementFormalReviewerAgent")
    proof_worker = render_agent_instruction("ProofFormalWorkerAgent")
    proof_reviewer = render_agent_instruction("ProofFormalReviewerAgent")

    assert "rewrites the working file" in statement_worker
    assert "check_statement_formal_policy" in statement_worker
    assert "check_proof_formal_policy" not in statement_worker
    assert "prepare_statement_formal_file" not in statement_reviewer
    assert "capture_statement_formal_file" not in statement_reviewer
    assert "check_statement_formal_policy" in statement_reviewer
    assert "check_proof_formal_policy" not in statement_reviewer

    assert "discards uncaptured proof edits" in proof_worker
    assert "check_proof_formal_policy" in proof_worker
    assert "check_statement_formal_policy" not in proof_worker
    assert "prepare_proof_formal_file" not in proof_reviewer
    assert "capture_proof_formal_file" not in proof_reviewer
    assert "check_proof_formal_policy" in proof_reviewer
    assert "check_statement_formal_policy" not in proof_reviewer


def test_runtime_instruction_tool_refs_are_visible_to_each_agent() -> None:
    reports = build_agent_surface_reports()

    for spec in build_agent_type_specs():
        refs = _tool_refs(render_agent_instruction(spec)) & _registered_tool_names()
        report = reports[spec.agent_type]
        visible = {tool.name for tool in report.application_tools} | {tool.name for tool in report.submit_tools}

        assert refs <= visible, spec.agent_type
