from __future__ import annotations

import pytest

from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.strict.surface import current_runtime_surface, strict_missing_report


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_strict_runtime_surface_is_loaded_from_registries() -> None:
    surface = current_runtime_surface()

    assert len(surface.flows) == 10
    assert len(surface.logic_steps) == 24
    assert len(surface.agent_steps) == 14
    assert len(surface.application_tools) == 202
    assert len(surface.submit_tools) == 29


def test_strict_evidence_recorder_reports_missing_surface() -> None:
    surface = current_runtime_surface()
    recorder = EvidenceRecorder()

    report = strict_missing_report(recorder, surface)
    assert report["missing_flows"] == sorted(surface.flows)
    assert report["missing_logic_steps"] == sorted(surface.logic_steps)
    assert report["missing_agent_steps"] == sorted(surface.agent_steps)
    assert report["missing_application_tools"] == sorted(surface.application_tools)
    assert report["missing_submit_tools"] == sorted(surface.submit_tools)
    assert report["env_gated_blocked"] == []
