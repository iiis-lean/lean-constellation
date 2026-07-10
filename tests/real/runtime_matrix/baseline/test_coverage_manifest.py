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
    assert len(snapshot.flows) == 10
    assert len(snapshot.logic_steps) == 24
    assert len(snapshot.agent_steps) == 14
    assert len(snapshot.agent_types) == 20
    assert len(snapshot.application_tools) == 247
    assert len(snapshot.application_tool_groups) == 97
    assert len(snapshot.application_tool_views) == 20
    assert len(snapshot.submit_tools) == 29
    assert len(snapshot.submit_tool_groups) == 16
    assert len(snapshot.submit_tool_views) == 14
    assert registry_fingerprint(snapshot) == EXPECTED_REGISTRY_FINGERPRINT


def test_runtime_matrix_manifest_declares_every_registry_item() -> None:
    missing = sorted(required_registry_tags() - manifest_tags())
    assert missing == []
