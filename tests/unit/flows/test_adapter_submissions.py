from __future__ import annotations

from lean_constellation.flows.repo_lifecycle.submissions import AdapterCatalogBlockedSubmission, AdapterCatalogReadySubmission
from tests.unit.flows._submission_family_helpers import assert_roundtrip


def test_adapter_submissions_roundtrip() -> None:
    assert_roundtrip(AdapterCatalogReadySubmission, AdapterCatalogBlockedSubmission)
