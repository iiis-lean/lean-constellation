from __future__ import annotations

from lean_constellation.flows.coordinator.submissions import (
    CoordinatorContentTasksSubmission,
    CoordinatorRepoReadySubmission,
    CoordinatorRepoRequirementSubmission,
    CoordinatorResourceRequestSubmission,
)
from tests.unit.flows._submission_family_helpers import assert_roundtrip


def test_coordinator_submissions_roundtrip() -> None:
    assert_roundtrip(CoordinatorContentTasksSubmission, CoordinatorResourceRequestSubmission, CoordinatorRepoRequirementSubmission, CoordinatorRepoReadySubmission)
