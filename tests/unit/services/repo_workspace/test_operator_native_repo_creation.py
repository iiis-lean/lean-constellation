from __future__ import annotations

from pathlib import Path

import pytest

from lean_constellation.domain.lake_project import NativeLakeProjectConfig
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import ProofAvailability, RepoWorkMode
from lean_constellation.services.external_clients import ExternalCommandResult
from lean_constellation.services.repo_workspace.repo_lifecycle_lock import (
    RepoLifecycleLockBusyError,
)
from tests.unit_services_helpers import make_runtime


class _Lake:
    def run_lake_build(self, repo_root: Path, target: str | None = None) -> ExternalCommandResult:
        return ExternalCommandResult(
            ok=True,
            command=["lake", "build"],
            cwd=str(repo_root),
            exit_code=0,
            summary="ok",
        )

    def summarize_command_result(self, result: ExternalCommandResult):  # noqa: ANN201
        from lean_constellation.services.external_clients import LakeCommandSummaryView

        return LakeCommandSummaryView(
            ok=result.ok,
            command=result.command,
            exit_code=result.exit_code,
            summary=result.summary or "",
        )


def _input() -> RepoPreparationInput:
    return RepoPreparationInput(
        goal="Create a declared provider.",
        source_corpus_mode=SourceCorpusMode.PREPARE,
    )


def test_create_native_repo_uses_explicit_config_and_no_runtime_truth(tmp_path: Path) -> None:
    runtime = make_runtime(external_overrides={"lake": _Lake()})

    created = runtime.repo_workspace.create_native_repo(
        tmp_path,
        repo_key="ProviderRepo",
        project_name="ProviderProject",
        preparation_input=_input(),
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
        default_requirement_proof_availability=ProofAvailability.DECLARED,
        native_config=NativeLakeProjectConfig(mathlib_enabled=False),
    )

    assert created.ok and created.value is not None, created.issues
    assert created.value.config.config.target_proof_availability == ProofAvailability.DECLARED
    assert created.value.config.config.work_mode == RepoWorkMode.DECLARED_INTERFACE
    repo_root = tmp_path / "ProviderRepo"
    assert (repo_root / "lakefile.toml").is_file()
    assert not (repo_root / ".agent_runtime").exists()


def test_create_native_repo_rolls_back_on_skeleton_failure(tmp_path: Path, monkeypatch) -> None:
    runtime = make_runtime(external_overrides={"lake": _Lake()})

    monkeypatch.setattr(
        runtime.repo_workspace.lake_dependency,
        "initialize_native_repo_skeleton",
        lambda *args, **kwargs: runtime.foundation.fail(
            runtime.foundation.issue("injected_skeleton_failure", "injected")
        ),
    )
    created = runtime.repo_workspace.create_native_repo(
        tmp_path,
        repo_key="ProviderRepo",
        project_name="ProviderProject",
        preparation_input=_input(),
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
        default_requirement_proof_availability=ProofAvailability.DECLARED,
        native_config=NativeLakeProjectConfig(mathlib_enabled=False),
    )

    assert not created.ok
    assert created.issues[0].kind == "injected_skeleton_failure"
    assert not (tmp_path / "ProviderRepo").exists()


def test_workspace_creation_lock_rejects_concurrent_repo_creation(tmp_path: Path) -> None:
    runtime = make_runtime(external_overrides={"lake": _Lake()})

    with runtime.repo_workspace.workspace_creation_lock.locked(tmp_path):
        with pytest.raises(RepoLifecycleLockBusyError):
            with runtime.repo_workspace.workspace_creation_lock.locked(tmp_path):
                pass


def test_create_native_repo_rolls_back_unexpected_service_exception(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = make_runtime(external_overrides={"lake": _Lake()})

    def explode(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("injected exception")

    monkeypatch.setattr(runtime.repo_workspace.metadata, "update_repo_config", explode)
    created = runtime.repo_workspace.create_native_repo(
        tmp_path,
        repo_key="ProviderRepo",
        project_name="ProviderProject",
        preparation_input=_input(),
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
        default_requirement_proof_availability=ProofAvailability.DECLARED,
        native_config=NativeLakeProjectConfig(mathlib_enabled=False),
    )

    assert not created.ok
    assert created.issues[0].kind == "native_repo_creation_failed"
    assert not (tmp_path / "ProviderRepo").exists()
