from __future__ import annotations

from lean_constellation.flows.content_node_task.decl_round.submissions import (
    DeclStageReviewSubmittedSubmission,
    DeclStageWorkerBlockedSubmission,
    DeclStageWorkerCompletedSubmission,
)
from tests.unit.flows._submission_family_helpers import assert_roundtrip


def test_decl_stage_submissions_roundtrip() -> None:
    assert_roundtrip(DeclStageWorkerCompletedSubmission, DeclStageWorkerBlockedSubmission, DeclStageReviewSubmittedSubmission)
