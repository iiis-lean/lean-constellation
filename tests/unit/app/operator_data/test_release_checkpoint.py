from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from lean_constellation.app.operator_data.api import OperatorDataApi
from lean_constellation.app.operator_data.release import (
    CheckpointCreateInput,
    CheckpointIdInput,
    CheckpointListInput,
    CheckpointRestoreInput,
    ReleaseCandidateInput,
    ReleaseRestoreInput,
)
from lean_constellation.domain.repo import (
    ProofAvailability,
    RepoPublicationState,
    RepoPublicationStatus,
    RepoPublicationView,
)
from lean_constellation.domain.repo_release import RepoRelease, RepoReleaseView
from lean_constellation.services.external_clients import ToolchainCommandView
from lean_constellation.services.validation_snapshot import (
    CandidateReleasePreparationView,
    PreparedRepoReleaseView,
    ProviderRequirementReconciliationView,
    RepoCheckpointKind,
    RepoCheckpointSnapshotView,
    RepoReleaseFinalizeView,
)

from tests.unit.app.operator_data._helpers import make_registry, make_repo


def _api(tmp_path):  # noqa: ANN001, ANN202
    workspace = tmp_path / "workspace"
    repo_root = make_repo(workspace)
    registry = make_registry(workspace)
    return OperatorDataApi(registry), registry.workspace_runtime(), repo_root


def test_lc_checkpoint_create_validate_list_and_restore_are_sanitized(tmp_path) -> None:
    api, _, repo_root = _api(tmp_path)
    source = repo_root / "Main.lean"
    source.write_text("def original : Nat := 1\n", encoding="utf-8")
    managed = api.prepare_repo_management("MainRepo")
    assert managed.ok and managed.value is not None
    runtime_sentinel = repo_root / ".agent_runtime" / "operator-sentinel.txt"
    runtime_sentinel.write_text("ARK runtime truth is outside LC restore.\n", encoding="utf-8")

    created = api.release_checkpoint.create_checkpoint(
        "MainRepo",
        CheckpointCreateInput(
            checkpoint_kind=RepoCheckpointKind.MANUAL_TEST_STABLE_POINT,
            label="before edit",
        ),
    )
    assert created.ok and created.value is not None, created.issues
    public_payload = created.value.model_dump(mode="json")
    assert "ark_runtime_snapshot_id" not in public_payload
    assert "root" not in public_payload

    checked = api.release_checkpoint.validate_checkpoint(
        "MainRepo", CheckpointIdInput(snapshot_id=created.value.snapshot_id)
    )
    listed = api.release_checkpoint.list_checkpoints("MainRepo", CheckpointListInput())
    assert checked.ok and checked.value is not None
    assert checked.value.snapshot_id == created.value.snapshot_id
    assert listed.ok and listed.value is not None
    assert [item.snapshot_id for item in listed.value] == [created.value.snapshot_id]

    source.write_text("def changed : Nat := 2\n", encoding="utf-8")
    extra = repo_root / "Extra.lean"
    extra.write_text("def extra : Nat := 3\n", encoding="utf-8")
    preview = api.release_checkpoint.restore_checkpoint(
        "MainRepo",
        CheckpointRestoreInput(
            snapshot_id=created.value.snapshot_id,
            dry_run=True,
            prune_extra_files=True,
        ),
    )
    assert preview.ok and preview.value is not None, preview.issues
    assert preview.value.would_prune_files == ["Extra.lean"]
    assert extra.exists()
    restored = api.release_checkpoint.restore_checkpoint(
        "MainRepo",
        CheckpointRestoreInput(
            snapshot_id=created.value.snapshot_id,
            prune_extra_files=True,
        ),
    )
    assert restored.ok and restored.value is not None, restored.issues
    assert source.read_text(encoding="utf-8") == "def original : Nat := 1\n"
    assert not extra.exists()
    assert runtime_sentinel.read_text(encoding="utf-8") == "ARK runtime truth is outside LC restore.\n"
    assert "ark_runtime_snapshot_id" not in restored.value.model_dump(mode="json")


def test_operator_checkpoint_rejects_ark_composite_and_release_owned_kind(tmp_path) -> None:
    api, runtime, repo_root = _api(tmp_path)
    composite = runtime.validation_snapshot.create_repo_checkpoint_archive(
        repo_root,
        checkpoint_kind=RepoCheckpointKind.MANUAL_TEST_STABLE_POINT,
        snapshot_id="composite",
        ark_runtime_snapshot_id="ark-internal",
    )
    assert composite.ok and composite.value is not None

    rejected = api.release_checkpoint.validate_checkpoint(
        "MainRepo", CheckpointIdInput(snapshot_id="composite")
    )
    listed = api.release_checkpoint.list_checkpoints("MainRepo", CheckpointListInput())

    assert not rejected.ok
    assert rejected.issues[0].kind == "operator_checkpoint_contains_ark_runtime"
    assert listed.ok and listed.value == []
    with pytest.raises(ValidationError):
        CheckpointCreateInput.model_validate(
            {
                "checkpoint_kind": "manual_test_stable_point",
                "ark_runtime_snapshot_id": "forged",
            }
        )
    with pytest.raises(ValidationError):
        CheckpointCreateInput(checkpoint_kind=RepoCheckpointKind.REPO_RELEASE)


def test_release_restore_rejects_composite_checkpoint_before_project_mutation(tmp_path) -> None:
    api, runtime, repo_root = _api(tmp_path)
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.commit_scope_contract(
        repo_root,
        scope_path="Main",
        summary="Committed root.",
    ).ok
    root = runtime.node.node_tree.get_node(repo_root, path="Main").value
    assert root is not None and root.active_contract_version is not None
    composite = runtime.validation_snapshot.create_repo_checkpoint_archive(
        repo_root,
        checkpoint_kind=RepoCheckpointKind.MANUAL_TEST_STABLE_POINT,
        snapshot_id="composite-release-checkpoint",
        ark_runtime_snapshot_id="ark-internal",
    )
    assert composite.ok
    release = RepoRelease(
        release_id="composite_release",
        node_contract_versions={root.node_id: root.active_contract_version},
        target_proof_availability=ProofAvailability.PROVED,
        repo_checkpoint_id="composite-release-checkpoint",
        summary="Composite checkpoint must be rejected.",
    )
    assert runtime.repo_workspace.release.create_release(repo_root, release=release).ok
    marker = repo_root / "marker.txt"
    marker.write_text("working tree remains unchanged\n", encoding="utf-8")

    rejected = api.release_checkpoint.restore_repo_release(
        "MainRepo",
        ReleaseRestoreInput(release_id=release.release_id),
    )

    assert not rejected.ok
    assert rejected.issues[0].kind == "operator_checkpoint_contains_ark_runtime"
    assert marker.read_text(encoding="utf-8") == "working tree remains unchanged\n"


def test_publish_is_one_call_self_managed_and_never_exposes_prepared_payload(
    tmp_path, monkeypatch
) -> None:
    api, runtime, repo_root = _api(tmp_path)
    release = RepoRelease(
        release_id="release_one",
        node_contract_versions={"node_main": 1},
        target_proof_availability=ProofAvailability.DECLARED,
        repo_checkpoint_id="checkpoint_one",
        summary="First release.",
    )
    publication = RepoPublicationState(
        status=RepoPublicationStatus.STABLE,
        latest_release_id=release.release_id,
    )
    gate = runtime.foundation.gate_passed("candidate_repo_release", summary="passed")
    prepared = PreparedRepoReleaseView(
        release=release,
        publication=publication,
        candidate_digest="private-candidate-digest",
        build=ToolchainCommandView(
            ok=True,
            command=["lake", "build"],
            exit_code=0,
            summary="built",
        ),
        gate=gate,
        summary="private prepared candidate",
    )
    monkeypatch.setattr(
        runtime.validation_snapshot,
        "prepare_candidate_release",
        lambda *args, **kwargs: runtime.foundation.ok(
            CandidateReleasePreparationView(
                outcome="prepared",
                gate=gate,
                build=prepared.build,
                prepared_release=prepared,
                summary="prepared",
            )
        ),
    )

    def commit_with_own_lock(*args, **kwargs):  # noqa: ANN002, ANN003
        with runtime.repo_workspace.lifecycle_lock.locked(repo_root):
            return runtime.foundation.ok(
                RepoReleaseFinalizeView(
                    release=RepoReleaseView(
                        repo_root=str(repo_root), release=release, summary="published"
                    ),
                    checkpoint=RepoCheckpointSnapshotView(
                        snapshot_id=release.repo_checkpoint_id,
                        checkpoint_kind=RepoCheckpointKind.REPO_RELEASE,
                        root=str(repo_root / ".lean_constellation/snapshots/internal"),
                        ark_runtime_snapshot_id=None,
                        file_count=3,
                        summary="release checkpoint",
                    ),
                    publication=RepoPublicationView(
                        repo_root=str(repo_root), publication=publication
                    ),
                    reconciliation=ProviderRequirementReconciliationView(
                        release_id=release.release_id,
                        summary="no requirements",
                    ),
                    summary="published",
                )
            )

    monkeypatch.setattr(
        runtime.validation_snapshot,
        "commit_prepared_release",
        commit_with_own_lock,
    )

    result = api.release_checkpoint.publish_repo_release(
        "MainRepo", ReleaseCandidateInput(summary="First release.")
    )

    assert result.ok and result.value is not None, result.issues
    payload = json.dumps(result.value.model_dump(mode="json"), sort_keys=True)
    assert result.value.outcome == "published"
    assert "private-candidate-digest" not in payload
    assert "prepared_release" not in payload
    assert "candidate_digest" not in payload
    assert "ark_runtime_snapshot_id" not in payload
    assert "snapshots/internal" not in payload


def test_publish_propagates_stale_candidate_failure_without_private_payload(
    tmp_path, monkeypatch
) -> None:
    api, runtime, _ = _api(tmp_path)
    release = RepoRelease(
        release_id="release_stale",
        node_contract_versions={"node_main": 1},
        target_proof_availability=ProofAvailability.DECLARED,
        repo_checkpoint_id="checkpoint_stale",
        summary="Stale release.",
    )
    publication = RepoPublicationState(status=RepoPublicationStatus.STABLE, latest_release_id=release.release_id)
    gate = runtime.foundation.gate_passed("candidate_repo_release", summary="passed")
    prepared = PreparedRepoReleaseView(
        release=release,
        publication=publication,
        candidate_digest="private-stale-digest",
        build=ToolchainCommandView(ok=True, command=["lake", "build"], exit_code=0, summary="built"),
        gate=gate,
        summary="prepared",
    )
    monkeypatch.setattr(
        runtime.validation_snapshot,
        "prepare_candidate_release",
        lambda *args, **kwargs: runtime.foundation.ok(
            CandidateReleasePreparationView(
                outcome="prepared",
                gate=gate,
                build=prepared.build,
                prepared_release=prepared,
                summary="prepared",
            )
        ),
    )
    monkeypatch.setattr(
        runtime.validation_snapshot,
        "commit_prepared_release",
        lambda *args, **kwargs: runtime.foundation.fail(
            runtime.foundation.issue("release_candidate_drift", "Candidate truth changed.")
        ),
    )

    result = api.release_checkpoint.publish_repo_release(
        "MainRepo", ReleaseCandidateInput(summary="Stale release.")
    )

    assert not result.ok
    payload = json.dumps(result.model_dump(mode="json"), sort_keys=True)
    assert result.issues[0].kind == "release_candidate_drift"
    assert "private-stale-digest" not in payload
    with pytest.raises(ValidationError):
        ReleaseCandidateInput.model_validate(
            {"summary": "forged", "candidate_digest": "caller-controlled"}
        )
