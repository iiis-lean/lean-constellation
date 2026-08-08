from pathlib import Path

from lean_constellation.domain.repo import RepoFormat, RepoPublicationState, RepoPublicationStatus
from lean_constellation.services.foundation import WriteMode
from tests.unit.services.repo_workspace.test_repo_release import (
    _prepare_adapter_release_repo,
    _prepare_release_repo,
    _release,
)
from tests.unit_services_helpers import make_runtime


def _write_publication(runtime, repo_root: Path, *, latest_release_id: str | None) -> None:
    path = runtime.repo_workspace.metadata._repo_publication_path(repo_root)
    assert runtime.foundation.store.write_json_atomic(
        path,
        RepoPublicationState(status=RepoPublicationStatus.STABLE, latest_release_id=latest_release_id),
        mode=WriteMode.OVERWRITE,
    ).ok


def _publish_git_release(runtime, repo_root: Path, release) -> None:  # noqa: ANN001
    initialized = runtime.repo_workspace.git_release.ensure_independent_repo(repo_root)
    assert initialized.ok and initialized.value is not None
    candidate_files = [
        path.relative_to(repo_root).as_posix()
        for path in runtime.validation_snapshot.release_finalizer._candidate_files(repo_root)
    ]
    committed = runtime.repo_workspace.git_release.commit_release(
        repo_root,
        release=release,
        candidate_files=candidate_files,
        expected_head=initialized.value.head_commit,
    )
    assert committed.ok


def test_native_provider_requires_git_backed_release(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    assert runtime.repo_workspace.metadata.set_repo_format(tmp_path, repo_format=RepoFormat.NATIVE, reason="native").ok
    release = _release("r1", versions)
    assert runtime.repo_workspace.release.create_release(tmp_path, release=release).ok
    _write_publication(runtime, tmp_path, latest_release_id="r1")

    missing_git_release = runtime.repo_workspace.provider_availability.check_provider_available(tmp_path)
    assert not missing_git_release.ok
    assert missing_git_release.issues[0].kind == "provider_native_git_release_invalid"

    _publish_git_release(runtime, tmp_path, release)
    available = runtime.repo_workspace.provider_availability.check_provider_available(tmp_path)
    assert available.ok and available.value.passed is True


def test_native_without_stable_release_and_unknown_are_rejected(tmp_path: Path) -> None:
    runtime = make_runtime()
    assert runtime.repo_workspace.metadata.ensure_repo_model(tmp_path).ok
    assert runtime.repo_workspace.metadata.set_repo_format(tmp_path, repo_format=RepoFormat.NATIVE, reason="native").ok
    _write_publication(runtime, tmp_path, latest_release_id=None)

    unavailable = runtime.repo_workspace.provider_availability.check_provider_available(tmp_path)
    assert unavailable.ok and unavailable.value.passed is False
    assert unavailable.value.issues[0].kind == "provider_native_stable_release_missing"

    other = tmp_path / "unknown"
    assert runtime.repo_workspace.metadata.ensure_repo_model(other).ok
    _write_publication(runtime, other, latest_release_id=None)
    unknown = runtime.repo_workspace.provider_availability.check_provider_available(other)
    assert unknown.ok and unknown.value.passed is False
    assert unknown.value.issues[0].kind == "provider_format_unknown"


def test_adapter_provider_requires_git_backed_release_and_ready_gate(monkeypatch, tmp_path: Path) -> None:
    runtime, versions = _prepare_adapter_release_repo(tmp_path)
    _write_publication(runtime, tmp_path, latest_release_id=None)
    monkeypatch.setattr(
        runtime.adapter,
        "check_adapter_ready",
        lambda repo_root: runtime.foundation.ok(
            runtime.foundation.gate_passed("adapter_ready", summary="Adapter ready fixture.")
        ),
    )

    missing_release = runtime.repo_workspace.provider_availability.check_provider_available(tmp_path)
    assert missing_release.ok and missing_release.value.passed is False
    assert missing_release.value.issues[0].kind == "provider_adapter_stable_release_missing"

    release = _release("adapter_r1", versions)
    assert runtime.repo_workspace.release.create_release(tmp_path, release=release).ok
    _write_publication(runtime, tmp_path, latest_release_id=release.release_id)
    missing_git = runtime.repo_workspace.provider_availability.check_provider_available(tmp_path)
    assert not missing_git.ok
    assert missing_git.issues[0].kind == "provider_adapter_git_release_invalid"

    _publish_git_release(runtime, tmp_path, release)
    available = runtime.repo_workspace.provider_availability.check_provider_available(tmp_path)
    assert available.ok and available.value.passed is True

    monkeypatch.setattr(
        runtime.adapter,
        "check_adapter_ready",
        lambda repo_root: runtime.foundation.ok(
            runtime.foundation.gate_failed(
                "adapter_ready",
                runtime.foundation.issue("adapter_catalog_incomplete", "Adapter catalog is incomplete."),
            )
        ),
    )
    blocked = runtime.repo_workspace.provider_availability.check_provider_available(tmp_path)
    assert blocked.ok and blocked.value.passed is False
    assert blocked.value.issues[0].kind == "provider_adapter_not_ready"
