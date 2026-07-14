from __future__ import annotations

from pathlib import Path

from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from tests.unit_services_helpers import make_runtime


def _preparation_input() -> RepoPreparationInput:
    return RepoPreparationInput(
        goal="Prepare a provider repo for tests.",
        source_corpus_mode=SourceCorpusMode.PREPARE,
        source_corpus_relpath=".lean_constellation/source",
        source_description="Unit-test source description.",
    )


def test_prepare_provider_repo_shell_creates_only_business_truth(tmp_path: Path) -> None:
    runtime = make_runtime()

    created = runtime.repo_workspace.preparation.prepare_provider_repo_shell(
        tmp_path,
        target_repo="provider_repo",
        project_name="ProviderProject",
        preparation_input=_preparation_input(),
    )

    assert created.ok and created.value is not None
    repo_root = tmp_path / "provider_repo"
    assert created.value.shell.repo_root == str(repo_root)
    assert created.value.shell.repo_name == "provider_repo"
    assert created.value.preparation_input.input.goal == "Prepare a provider repo for tests."
    assert (repo_root / ".lean_constellation" / "repo.json").exists()
    assert (repo_root / ".lean_constellation" / "preparation_input.json").exists()
    assert not (repo_root / ".agent_runtime").exists()


def test_prepare_provider_repo_shell_rejects_existing_and_invalid_target(tmp_path: Path) -> None:
    runtime = make_runtime()
    (tmp_path / "provider_repo").mkdir()

    existing = runtime.repo_workspace.preparation.prepare_provider_repo_shell(
        tmp_path,
        target_repo="provider_repo",
        preparation_input=_preparation_input(),
    )
    invalid = runtime.repo_workspace.preparation.prepare_provider_repo_shell(
        tmp_path,
        target_repo="../bad",
        preparation_input=_preparation_input(),
    )

    assert not existing.ok
    assert existing.issues[0].kind == "target_repo_already_exists"
    assert not invalid.ok
    assert invalid.issues[0].kind == "invalid_target_repo_name"
    assert not (tmp_path / "bad").exists()


def test_prepare_provider_repo_shell_rolls_back_on_invalid_input(tmp_path: Path) -> None:
    runtime = make_runtime()
    invalid_input = RepoPreparationInput(
        goal="Prepare invalid input.",
        source_corpus_mode=SourceCorpusMode.NONE,
        source_corpus_relpath=".lean_constellation/source",
    )

    created = runtime.repo_workspace.preparation.prepare_provider_repo_shell(
        tmp_path,
        target_repo="provider_repo",
        preparation_input=invalid_input,
    )

    assert not created.ok
    assert created.issues[0].kind == "invalid_source_corpus_mode"
    assert not (tmp_path / "provider_repo").exists()
