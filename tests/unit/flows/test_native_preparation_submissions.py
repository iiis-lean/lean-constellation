from __future__ import annotations

from lean_constellation.flows.repo_lifecycle.submissions import (
    RootInterfacePrepareReadySubmission,
    SourceCorpusBlockedSubmission,
    SourceCorpusPreparedSubmission,
    SourceIndexBuilderRoundSubmission,
    SourceIndexReviewerRoundSubmission,
)
from tests.unit.flows._submission_family_helpers import assert_roundtrip


def test_native_preparation_submissions_roundtrip() -> None:
    assert_roundtrip(
        SourceCorpusPreparedSubmission,
        SourceCorpusBlockedSubmission,
        SourceIndexBuilderRoundSubmission,
        SourceIndexReviewerRoundSubmission,
        RootInterfacePrepareReadySubmission,
    )
