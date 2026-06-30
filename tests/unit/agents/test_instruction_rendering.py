from __future__ import annotations

import re

from lean_constellation.agents import get_agent_type_spec, render_agent_instruction


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
