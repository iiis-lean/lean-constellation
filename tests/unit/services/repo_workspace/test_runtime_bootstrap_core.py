from __future__ import annotations

from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.preparation import (
    RepoPreparationInput,
    RepoRuntimeBootstrapView,
    SourceCorpusMode,
)
from lean_constellation.services.foundation import FoundationService


class FakeRuntimeBootstrap:
    def __init__(self, foundation: FoundationService, *, fail: bool = False) -> None:
        self.foundation = foundation
        self.fail = fail
        self.calls: list[tuple[Path, str, str | None]] = []

    def bootstrap_provider_repo_runtime(
        self,
        repo_root: Path,
        *,
        repo_name: str,
        project_name: str | None = None,
    ):
        self.calls.append((Path(repo_root), repo_name, project_name))
        if self.fail:
            return self.foundation.fail(
                self.foundation.issue(
                    "runtime_bootstrap_failed",
                    "Runtime bootstrap provider failed.",
                    object_ref=str(repo_root),
                )
            )
        runtime_root = Path(repo_root) / ".agent_runtime"
        runtime_root.mkdir(parents=True)
        marker = runtime_root / "bootstrap.json"
        marker.write_text('{"bootstrapped": true}\n', encoding="utf-8")
        return self.foundation.ok(
            RepoRuntimeBootstrapView(
                repo_root=str(repo_root),
                runtime_root=str(runtime_root),
                created=True,
                initialized_paths=[str(runtime_root), str(marker)],
                summary="Fake runtime bootstrap completed.",
            )
        )


def _preparation_input() -> RepoPreparationInput:
    return RepoPreparationInput(
        goal="Prepare a provider repo for tests.",
        source_corpus_mode=SourceCorpusMode.PREPARE,
        source_corpus_relpath=".lean_constellation/source",
        source_description="Unit-test source description.",
    )


def test_prepare_provider_repo_runtime_shell_creates_metadata_and_calls_bootstrap(tmp_path: Path) -> None:
    runtime = make_runtime()
    bootstrap = FakeRuntimeBootstrap(runtime.foundation)

    created = runtime.repo_workspace.prepare_provider_repo_runtime_shell(
        tmp_path,
        target_repo="provider_repo",
        project_name="ProviderProject",
        preparation_input=_preparation_input(),
        runtime_bootstrap=bootstrap,
    )

    assert created.ok
    assert created.value is not None
    repo_root = tmp_path / "provider_repo"
    assert created.value.shell.repo_root == str(repo_root)
    assert created.value.shell.repo_name == "provider_repo"
    assert created.value.preparation_input.input.goal == "Prepare a provider repo for tests."
    assert created.value.runtime_bootstrap.runtime_root == str(repo_root / ".agent_runtime")
    assert (repo_root / ".lean_constellation" / "repo.json").exists()
    assert (repo_root / ".lean_constellation" / "preparation_input.json").exists()
    assert (repo_root / ".agent_runtime" / "bootstrap.json").exists()
    assert bootstrap.calls == [(repo_root, "provider_repo", "ProviderProject")]


def test_prepare_provider_repo_runtime_shell_default_bootstrap_creates_runtime_dirs(tmp_path: Path) -> None:
    runtime = make_runtime()

    created = runtime.repo_workspace.prepare_provider_repo_runtime_shell(
        tmp_path,
        target_repo="provider_repo",
        preparation_input=_preparation_input(),
    )

    assert created.ok
    assert created.value is not None
    runtime_root = tmp_path / "provider_repo" / ".agent_runtime"
    assert created.value.runtime_bootstrap.initialized_paths
    for name in ("homes", "scopes", "index", "snapshots"):
        assert (runtime_root / name).is_dir()


def test_prepare_provider_repo_runtime_shell_rejects_existing_and_invalid_target(tmp_path: Path) -> None:
    runtime = make_runtime()
    (tmp_path / "provider_repo").mkdir()

    existing = runtime.repo_workspace.prepare_provider_repo_runtime_shell(
        tmp_path,
        target_repo="provider_repo",
        preparation_input=_preparation_input(),
    )
    invalid = runtime.repo_workspace.prepare_provider_repo_runtime_shell(
        tmp_path,
        target_repo="../bad",
        preparation_input=_preparation_input(),
    )

    assert not existing.ok
    assert existing.issues[0].kind == "target_repo_already_exists"
    assert not invalid.ok
    assert invalid.issues[0].kind == "invalid_target_repo_name"
    assert not (tmp_path / "bad").exists()


def test_prepare_provider_repo_runtime_shell_rolls_back_on_invalid_input(tmp_path: Path) -> None:
    runtime = make_runtime()
    bootstrap = FakeRuntimeBootstrap(runtime.foundation)
    invalid_input = RepoPreparationInput(
        goal="Prepare invalid input.",
        source_corpus_mode=SourceCorpusMode.NONE,
        source_corpus_relpath=".lean_constellation/source",
    )

    created = runtime.repo_workspace.prepare_provider_repo_runtime_shell(
        tmp_path,
        target_repo="provider_repo",
        preparation_input=invalid_input,
        runtime_bootstrap=bootstrap,
    )

    assert not created.ok
    assert created.issues[0].kind == "invalid_source_corpus_mode"
    assert bootstrap.calls == []
    assert not (tmp_path / "provider_repo").exists()


def test_prepare_provider_repo_runtime_shell_rolls_back_on_bootstrap_failure(tmp_path: Path) -> None:
    runtime = make_runtime()
    bootstrap = FakeRuntimeBootstrap(runtime.foundation, fail=True)

    created = runtime.repo_workspace.prepare_provider_repo_runtime_shell(
        tmp_path,
        target_repo="provider_repo",
        preparation_input=_preparation_input(),
        runtime_bootstrap=bootstrap,
    )

    assert not created.ok
    assert created.issues[0].kind == "runtime_bootstrap_failed"
    assert bootstrap.calls == [(tmp_path / "provider_repo", "provider_repo", None)]
    assert not (tmp_path / "provider_repo").exists()
