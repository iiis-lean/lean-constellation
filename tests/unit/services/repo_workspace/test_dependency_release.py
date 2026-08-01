from __future__ import annotations

import json
from pathlib import Path

from lean_constellation.domain.repo import RepoPublicationState, RepoPublicationStatus
from lean_constellation.domain.repo_release import (
    DeclAvailabilityIndex,
    RepoDependencyChangeKind,
    RepoRelease,
    RepoReleaseKind,
)
from lean_constellation.services.external_clients import ToolchainCommandView
from lean_constellation.services.foundation import FoundationContext, WriteMode
from lean_constellation.services.repo_workspace.dependency_release import (
    DependencyReleaseMode,
)
from tests.unit_services_helpers import make_runtime, publish_native_provider_release


def _write_consumer_base(
    runtime,  # noqa: ANN001
    consumer: Path,
    *,
    provider_commit: str,
) -> RepoRelease:
    first = publish_native_provider_release(
        runtime, consumer, release_id="consumer_r1"
    )
    lakefile = consumer / "lakefile.toml"
    lakefile.write_text(
        lakefile.read_text(encoding="utf-8")
        + "\n[[require]]\n"
        'name = "Provider"\n'
        'git = "../Provider"\n'
        f'rev = "{provider_commit}"\n',
        encoding="utf-8",
    )
    second = RepoRelease(
        release_id="consumer_r2",
        parent_release_id=first.release_id,
        node_contract_versions=first.node_contract_versions,
        completion_mode=first.completion_mode,
        semantic_manifest_digest=(
            runtime.validation_snapshot.release_finalizer.compute_semantic_manifest_digest(
                consumer
            )
        ),
        dependency_lock_digest=(
            runtime.validation_snapshot.release_finalizer.compute_dependency_lock_digest(
                consumer
            )
        ),
        summary="Consumer with local Git provider pin.",
    )
    assert runtime.repo_workspace.release.create_release(
        consumer, release=second
    ).ok
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(consumer),
        RepoPublicationState(
            status=RepoPublicationStatus.STABLE,
            latest_release_id=second.release_id,
        ),
        mode=WriteMode.OVERWRITE,
    ).ok
    state = runtime.repo_workspace.git_release.inspect_repo(consumer).value
    committed = runtime.repo_workspace.git_release.commit_release(
        consumer,
        release=second,
        candidate_files=[
            path.relative_to(consumer).as_posix()
            for path in runtime.validation_snapshot.release_finalizer._candidate_files(
                consumer
            )
        ],
        expected_head=state.head_commit,
    )
    assert committed.ok, committed.issues
    return second


def test_locator_rebind_dependency_maintenance_release(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = make_runtime()
    provider = tmp_path / "Provider"
    consumer = tmp_path / "Consumer"
    provider_release = publish_native_provider_release(
        runtime, provider, release_id="provider_r1"
    )
    provider_commit = runtime.repo_workspace.git_release.resolve_release_commit(
        provider, release_id=provider_release.release_id
    ).value
    consumer_base = _write_consumer_base(
        runtime, consumer, provider_commit=provider_commit
    )

    preview = runtime.repo_workspace.dependency_release.preview(
        consumer,
        provider_repo_key="Provider",
        target_provider_release_id=provider_release.release_id,
        target_git_url="https://example.invalid/Provider.git",
        release_mode=DependencyReleaseMode.DEPENDENCY_MAINTENANCE,
    )
    assert preview.ok and preview.value is not None, preview.issues
    assert preview.value.change.kind == RepoDependencyChangeKind.LOCATOR_REBIND
    assert (
        preview.value.expected_dependency_lock_digest
        == consumer_base.dependency_lock_digest
    )

    def update(_repo_root, *, packages=None, transport_rewrites=None):  # noqa: ANN001, ANN202
        assert packages == ["Provider"]
        assert transport_rewrites == {
            "https://example.invalid/Provider.git": provider.resolve().as_uri()
        }
        (consumer / "lake-manifest.json").write_text(
            json.dumps(
                {
                    "packages": [
                        {
                            "name": "Provider",
                            "url": "https://example.invalid/Provider.git",
                            "rev": provider_commit,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return runtime.foundation.ok(
            ToolchainCommandView(
                ok=True,
                command=["lake", "update", "Provider"],
                exit_code=0,
                summary="targeted update passed",
            )
        )

    def build(_repo_root, *, target=None, transport_rewrites=None):  # noqa: ANN001, ANN202
        del target
        assert transport_rewrites
        return runtime.foundation.ok(
            ToolchainCommandView(
                ok=True,
                command=["lake", "build"],
                exit_code=0,
                summary="full build passed",
            )
        )

    monkeypatch.setattr(runtime.repo_workspace.lake_dependency, "run_lake_update", update)
    monkeypatch.setattr(runtime.repo_workspace.lake_dependency, "run_lake_build", build)
    applied = runtime.repo_workspace.dependency_release.apply(
        consumer,
        preview=preview.value,
        expected_recovery_token=preview.value.recovery_token,
    )

    assert applied.ok and applied.value is not None, applied.issues
    assert applied.value.finalized_release_id is not None
    latest = runtime.repo_workspace.release.get_latest_release(consumer)
    assert latest.ok and latest.value is not None
    assert latest.value.release.release_kind == RepoReleaseKind.DEPENDENCY_MAINTENANCE
    assert (
        latest.value.release.semantic_manifest_digest
        == consumer_base.semantic_manifest_digest
    )
    assert (
        latest.value.release.dependency_lock_digest
        == consumer_base.dependency_lock_digest
    )
    validated = runtime.repo_workspace.git_release.validate_release(
        consumer, release=latest.value.release
    )
    assert validated.ok, validated.issues
    relative_index = runtime.foundation.layout.release_decl_availability_path(
        FoundationContext(repo_root=consumer),
        latest.value.release.release_id,
    ).relative_to(consumer).as_posix()
    captured_index = runtime.repo_workspace.git_release.read_release_file(
        consumer,
        release_id=latest.value.release.release_id,
        relative_path=relative_index,
    )
    assert captured_index.ok and captured_index.value is not None
    assert DeclAvailabilityIndex.model_validate_json(captured_index.value).entries == []
    assert runtime.repo_workspace.git_release.list_worktree_changes(consumer).value == []


def test_dependency_change_token_is_cas_bound(tmp_path: Path) -> None:
    runtime = make_runtime()
    provider = tmp_path / "Provider"
    consumer = tmp_path / "Consumer"
    provider_release = publish_native_provider_release(
        runtime, provider, release_id="provider_r1"
    )
    provider_commit = runtime.repo_workspace.git_release.resolve_release_commit(
        provider, release_id=provider_release.release_id
    ).value
    _write_consumer_base(runtime, consumer, provider_commit=provider_commit)
    preview = runtime.repo_workspace.dependency_release.preview(
        consumer,
        provider_repo_key="Provider",
        target_provider_release_id=provider_release.release_id,
        target_git_url="https://example.invalid/Provider.git",
        release_mode=DependencyReleaseMode.DEFER,
    )
    assert preview.ok and preview.value is not None
    lakefile = consumer / "lakefile.toml"
    lakefile.write_text(
        lakefile.read_text(encoding="utf-8") + "\n# drift\n",
        encoding="utf-8",
    )

    rejected = runtime.repo_workspace.dependency_release.apply(
        consumer,
        preview=preview.value,
        expected_recovery_token=preview.value.recovery_token,
    )

    assert not rejected.ok
    assert rejected.issues[0].kind == "dependency_change_token_mismatch"
