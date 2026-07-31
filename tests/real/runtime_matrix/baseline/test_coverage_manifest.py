from __future__ import annotations

import pytest

from tests.real.runtime_matrix.baseline.coverage_static import (
    EXPECTED_REGISTRY_FINGERPRINT,
    current_registry_snapshot,
    manifest_tags,
    registry_fingerprint,
    required_registry_tags,
)


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_runtime_matrix_registry_fingerprint_is_pinned() -> None:
    snapshot = current_registry_snapshot()
    assert len(snapshot.flows) == 16
    assert len(snapshot.logic_steps) == 41
    assert len(snapshot.agent_steps) == 14
    assert len(snapshot.agent_types) == 23
    assert len(snapshot.application_tools) == 261
    assert len(snapshot.application_tool_groups) == 104
    assert len(snapshot.application_tool_views) == 23
    assert len(snapshot.submit_tools) == 35
    assert len(snapshot.submit_tool_groups) == 19
    assert len(snapshot.submit_tool_views) == 17
    assert registry_fingerprint(snapshot) == EXPECTED_REGISTRY_FINGERPRINT


def test_runtime_matrix_manifest_declares_every_registry_item() -> None:
    missing = sorted(required_registry_tags() - manifest_tags())
    assert missing == []
