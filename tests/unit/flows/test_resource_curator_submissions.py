from __future__ import annotations

from lean_constellation.flows.resource_request.submissions import (
    ExternalRepoRequiredSubmission,
    LocalResourceCreatedSubmission,
    ResourceDuplicateSubmission,
    ResourceRejectedSubmission,
)
from tests.unit.flows._submission_family_helpers import assert_roundtrip


def test_resource_curator_submissions_roundtrip() -> None:
    assert_roundtrip(ResourceDuplicateSubmission, LocalResourceCreatedSubmission, ExternalRepoRequiredSubmission, ResourceRejectedSubmission)
