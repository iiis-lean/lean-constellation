from __future__ import annotations

import pytest
from pydantic import ValidationError

from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import RepoCompletionMode, RepoPublicationState, RepoPublicationStatus
from lean_constellation.domain.repo_release import (
    DeclReleaseStatusView,
    ReleasedDeclProtectionView,
    RepoRelease,
    RepoReleaseBaselineView,
    RepoReleaseListView,
    RepoReleaseView,
    ResolvedDeclRefView,
)


def _release() -> RepoRelease:
    return RepoRelease(
        release_id="release_1",
        node_contract_versions={"node_main": 2, "node_content": 3},
        completion_mode=RepoCompletionMode.GRAPH_DECLARED,
        repo_checkpoint_id="checkpoint_1",
        summary="First declared release.",
    )


def test_repo_release_truth_and_views_roundtrip() -> None:
    release = _release()
    protection = ReleasedDeclProtectionView(
        node_id="node_content",
        node_path="Main.Core",
        decl_name="T",
        released_state="declared",
        first_release_id="release_1",
        last_release_id="release_1",
        summary="T is protected by the public statement closure.",
    )
    baseline = RepoReleaseBaselineView(
        release_id="release_1",
        lineage_release_ids=["release_1"],
        released_node_contract_versions=release.node_contract_versions,
        protected_decl_views=[protection],
        protected_node_ids=["node_content"],
        protected_scope_paths=["Main"],
        summary="Release baseline resolved.",
    )
    release_view = RepoReleaseView(repo_root="/workspace/Repo", release=release, summary="Release loaded.")
    list_view = RepoReleaseListView(repo_root="/workspace/Repo", releases=[release_view], summary="One release.")
    status_view = DeclReleaseStatusView(
        current_state="proved",
        released_state="declared",
        release_protected=True,
        summary="The declaration may advance its proof only.",
    )
    resolved = ResolvedDeclRefView(
        anchor=DeclRef(node="Main.Core", name="T", revision=1),
        resolved_revision=2,
        compatible=True,
        current_state="proved",
    )

    for model in (release, protection, baseline, release_view, list_view, status_view, resolved):
        assert type(model).model_validate(model.model_dump(mode="json")) == model


@pytest.mark.parametrize(
    "patch",
    [
        {"release_id": "../release"},
        {"parent_release_id": "release_1"},
        {"repo_checkpoint_id": "bad/checkpoint"},
        {"node_contract_versions": {}},
        {"node_contract_versions": {"node_main": 0}},
        {"summary": " "},
    ],
)
def test_repo_release_rejects_invalid_identity_and_contract_map(patch: dict[str, object]) -> None:
    payload = _release().model_dump(mode="python")
    payload.update(patch)

    with pytest.raises(ValidationError):
        RepoRelease.model_validate(payload)


def test_old_and_adapter_publication_payloads_allow_missing_latest_release() -> None:
    old_stable = RepoPublicationState.model_validate({"status": "stable", "stable_at": "2026-01-01T00:00:00Z"})
    adapter_style = RepoPublicationState(status=RepoPublicationStatus.STABLE)
    developing_native = RepoPublicationState(status=RepoPublicationStatus.DEVELOPING, latest_release_id="release_1")

    assert old_stable.latest_release_id is None
    assert adapter_style.latest_release_id is None
    assert old_stable.status is RepoPublicationStatus.STABLE
    assert developing_native.latest_release_id == "release_1"
    assert developing_native.stable_at is None
