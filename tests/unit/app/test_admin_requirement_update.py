from __future__ import annotations

from lean_constellation.app import LeanAdminApi, LeanAppConfig, RepoRuntimeRegistry
from lean_constellation.app.admin_api import UpdateRepoRequirementInput
from lean_constellation.domain.preparation import (
    AdapterProviderRoute,
    AutoProviderRoute,
    RepoPreparationInput,
    SourceCorpusMode,
)


def _registry(tmp_path):
    return RepoRuntimeRegistry(
        LeanAppConfig(workspace_root=tmp_path, materialize_agent_homes=False)
    )


def test_admin_requirement_update_preview_and_apply_rename_refs(tmp_path) -> None:
    registry = _registry(tmp_path)
    runtime = registry.workspace_runtime()
    consumer = tmp_path / "Consumer"
    provider = tmp_path / "Provider"
    assert runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    assert runtime.repo_workspace.metadata.ensure_repo_model(provider).ok
    created = runtime.repo_workspace.requirement.create_requirement(
        consumer,
        name="need_provider",
        target_repo="Provider",
        reason="Need provider theorem.",
        provider_route=AutoProviderRoute(),
    )
    assert created.ok and created.value is not None
    assert runtime.repo_workspace.preparation.write_preparation_input(
        provider,
        input=RepoPreparationInput(
            goal="Build provider.",
            source_corpus_mode=SourceCorpusMode.PREPARE,
            requirement_refs=[
                {
                    "consumer_repo": "Consumer",
                    "requirement_name": "need_provider",
                }
            ],
        ),
    ).ok
    current = created.value.requirement
    digest = runtime.repo_workspace.requirement.requirement_digest(current)
    replacement = current.model_copy(
        update={
            "name": "need_exact_provider",
            "reason": "Need the exact provider theorem.",
        }
    )
    admin = LeanAdminApi(
        runtime,
        workspace_root=tmp_path,
        repo_runtime_registry=registry,
    )

    preview = admin.update_repo_requirement(
        UpdateRepoRequirementInput(
            consumer_repo="Consumer",
            current_requirement_name="need_provider",
            expected_current_digest=digest,
            replacement=replacement,
            reason="Correct the requirement identity.",
        )
    )

    assert preview.ok and preview.value is not None
    assert preview.value.changed
    assert preview.value.applied is False
    assert preview.value.blockers == []
    assert preview.value.changed_fields == ["name", "reason"]
    assert preview.value.affected_preparation_inputs == [str(provider)]

    applied = admin.update_repo_requirement(
        UpdateRepoRequirementInput(
            consumer_repo="Consumer",
            current_requirement_name="need_provider",
            expected_current_digest=digest,
            replacement=replacement,
            reason="Correct the requirement identity.",
            dry_run=False,
        )
    )

    assert applied.ok and applied.value is not None and applied.value.applied
    assert not runtime.repo_workspace.requirement.get_requirement(
        consumer, name="need_provider"
    ).ok
    updated = runtime.repo_workspace.requirement.get_requirement(
        consumer, name="need_exact_provider"
    )
    assert updated.ok and updated.value is not None
    preparation = runtime.repo_workspace.preparation.get_preparation_input(provider)
    assert preparation.ok and preparation.value is not None
    assert preparation.value.input.requirement_refs[0].requirement_name == (
        "need_exact_provider"
    )


def test_admin_requirement_update_rejects_stale_digest_and_route_conflict(
    tmp_path,
) -> None:
    registry = _registry(tmp_path)
    runtime = registry.workspace_runtime()
    consumer_a = tmp_path / "ConsumerA"
    consumer_b = tmp_path / "ConsumerB"
    for repo in (consumer_a, consumer_b):
        assert runtime.repo_workspace.metadata.ensure_repo_model(repo).ok
    created_a = runtime.repo_workspace.requirement.create_requirement(
        consumer_a,
        name="need_provider_a",
        target_repo="Provider",
        reason="Need provider A.",
        provider_route=AutoProviderRoute(),
    )
    assert created_a.ok and created_a.value is not None
    assert runtime.repo_workspace.requirement.create_requirement(
        consumer_b,
        name="need_provider_b",
        target_repo="Provider",
        reason="Need provider B.",
        provider_route=AdapterProviderRoute(
            git_url="https://github.com/example/provider",
            revision="a" * 40,
            evidence_summary="Exact provider found.",
        ),
    ).ok
    replacement = created_a.value.requirement.model_copy(
        update={
            "provider_route": AdapterProviderRoute(
                git_url="https://github.com/other/provider",
                revision="b" * 40,
                evidence_summary="A conflicting provider was selected.",
            )
        }
    )
    admin = LeanAdminApi(
        runtime,
        workspace_root=tmp_path,
        repo_runtime_registry=registry,
    )

    stale = admin.update_repo_requirement(
        UpdateRepoRequirementInput(
            consumer_repo="ConsumerA",
            current_requirement_name="need_provider_a",
            expected_current_digest="0" * 64,
            replacement=replacement,
            reason="Test stale CAS.",
        )
    )
    assert not stale.ok
    assert stale.issues[0].kind == "requirement_digest_mismatch"

    digest = runtime.repo_workspace.requirement.requirement_digest(
        created_a.value.requirement
    )
    preview = admin.update_repo_requirement(
        UpdateRepoRequirementInput(
            consumer_repo="ConsumerA",
            current_requirement_name="need_provider_a",
            expected_current_digest=digest,
            replacement=replacement,
            reason="Preview conflicting route.",
        )
    )
    assert preview.ok and preview.value is not None
    assert {
        blocker.kind for blocker in preview.value.blockers
    } == {"requirement_provider_route_conflict"}


def test_admin_requirement_update_loaded_runtime_checkpoints_before_apply(
    tmp_path,
) -> None:
    registry = RepoRuntimeRegistry(
        LeanAppConfig(
            workspace_root=tmp_path,
            materialize_agent_homes=False,
            server_start_paused=True,
        )
    )
    loaded_runtime = registry.initialize_and_load(
        "Consumer",
        refresh_homes=False,
    )
    assert loaded_runtime.ok and loaded_runtime.value is not None
    runtime = registry.workspace_runtime()
    consumer = tmp_path / "Consumer"
    created = runtime.repo_workspace.requirement.create_requirement(
        consumer,
        name="need_provider",
        target_repo="Provider",
        reason="Need provider theorem.",
        provider_route=AutoProviderRoute(),
    )
    assert created.ok and created.value is not None
    current = created.value.requirement
    replacement = current.model_copy(
        update={"reason": "Need the verified provider theorem."}
    )
    admin = LeanAdminApi(
        runtime,
        workspace_root=tmp_path,
        repo_runtime_registry=registry,
    )

    applied = admin.update_repo_requirement(
        UpdateRepoRequirementInput(
            consumer_repo="Consumer",
            current_requirement_name="need_provider",
            expected_current_digest=(
                runtime.repo_workspace.requirement.requirement_digest(current)
            ),
            replacement=replacement,
            reason="Clarify the provider requirement.",
            dry_run=False,
        )
    )

    assert applied.ok and applied.value is not None
    assert applied.value.applied
    assert applied.value.checkpoint_required
    assert applied.value.checkpoint_id is not None
    snapshots = runtime.validation_snapshot.list_repo_checkpoint_snapshots(
        consumer,
        checkpoint_kind="manual_test_stable_point",
    )
    assert snapshots.ok and snapshots.value is not None
    assert applied.value.checkpoint_id in {
        snapshot.snapshot_id for snapshot in snapshots.value
    }
