from __future__ import annotations

import pytest
from pydantic import ValidationError

from lean_constellation.flows.content_node_task.preparation.resource_recon.submissions import ResourceReconRequestResourceSubmission
from lean_constellation.flows.content_node_task.submissions import ContentResourceRequestSubmission
from lean_constellation.flows.coordinator.submissions import CoordinatorResourceRequestSubmission
from lean_constellation.flows.resource_request.flows import ResourceCurationParams
from lean_constellation.tools.submit_args import (
    RepoResourceCandidateArg,
    SubmitRepoResourceDiscoveryResultArgs,
)
from tests.unit.flows._submission_family_helpers import assert_roundtrip


def test_resource_request_dispatch_submissions_roundtrip() -> None:
    assert_roundtrip(CoordinatorResourceRequestSubmission, ContentResourceRequestSubmission, ResourceReconRequestResourceSubmission)


def test_old_material_boundary_schemas_are_rejected() -> None:
    with pytest.raises(ValidationError):
        RepoResourceCandidateArg.model_validate(
            {
                "title": "Old candidate",
                "resource_kind": "paper",
                "canonical_locator": "arxiv:2501.12345",
                "source_urls": ["https://arxiv.org/abs/2501.12345"],
                "relevance": "Relevant.",
                "support_expected": "A theorem.",
                "reliability": "Primary source.",
                "recommendation": "request",
            }
        )

    with pytest.raises(ValidationError):
        ResourceCurationParams.model_validate(
            {
                "repo_key": "Repo",
                "target_kind": "arxiv",
                "target": "2501.12345",
            }
        )


def test_provider_candidate_requires_explicit_provider_scope() -> None:
    with pytest.raises(ValidationError, match="provider_scope"):
        SubmitRepoResourceDiscoveryResultArgs(
            summary="Classified candidate.",
            outcome="completed",
            candidates=[
                RepoResourceCandidateArg(
                    target="arxiv:2501.12345",
                    support_summary="Defines an independent reusable theory with a stable provider theorem.",
                    recommended_handling="provider_requirement",
                    consumer_need="One stable theorem API.",
                )
            ],
        )
