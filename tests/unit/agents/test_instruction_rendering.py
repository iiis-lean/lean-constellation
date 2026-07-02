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


def test_all_runtime_instructions_are_english_and_tool_refs_resolve() -> None:
    for spec in build_agent_type_specs():
        text = render_agent_instruction(spec)

        assert re.search(r"[\u3400-\u9fff]", text) is None, spec.agent_type
        assert not _unknown_tool_refs(text), spec.agent_type


def test_runtime_instruction_tool_refs_are_visible_to_each_agent() -> None:
    reports = build_agent_surface_reports()

    for spec in build_agent_type_specs():
        refs = _tool_refs(render_agent_instruction(spec)) & _registered_tool_names()
        report = reports[spec.agent_type]
        visible = {tool.name for tool in report.application_tools} | {tool.name for tool in report.submit_tools}

        assert refs <= visible, spec.agent_type
