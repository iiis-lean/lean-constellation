from __future__ import annotations

from lean_constellation.flows.content_node_task.decl_round.submissions import DeclRoundDispatchSubmission
from lean_constellation.flows.content_node_task.submissions import (
    ContentNodeBlockedSubmission,
    ContentNodeFailedSubmission,
    ContentNodeReadySubmission,
    ContentPreparationDispatchSubmission,
    ContentResourceRequestSubmission,
)
from tests.unit.flows._submission_family_helpers import assert_roundtrip


def test_content_plan_submissions_roundtrip() -> None:
    assert_roundtrip(
        ContentPreparationDispatchSubmission,
        ContentResourceRequestSubmission,
        DeclRoundDispatchSubmission,
        ContentNodeReadySubmission,
        ContentNodeBlockedSubmission,
        ContentNodeFailedSubmission,
    )
