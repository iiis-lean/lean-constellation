from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode, UpstreamDependencyInput
from lean_constellation.domain.repo import RepoFormat
from lean_constellation.services.external_clients import LakeCommandClient, LakeCommandClientConfig


def _require_real_lake() -> int:
    for command in ("lake", "lean", "git"):
        if shutil.which(command) is None:
            pytest.skip(f"`{command}` is required for real provider bootstrap tests.")
    return int(os.environ.get("LEAN_CONSTELLATION_REAL_LAKE_TIMEOUT", "180"))


def _runtime(timeout: int):
    return make_runtime(
        external_overrides={"lake": LakeCommandClient(LakeCommandClientConfig(timeout_seconds=timeout))}
    )


def _provider_input(*, source_corpus_mode: SourceCorpusMode) -> RepoPreparationInput:
    return RepoPreparationInput(
        goal="Prepare a provider repo for a real bootstrap smoke test.",
        source_corpus_mode=source_corpus_mode,
        source_corpus_relpath=(
            None
            if source_corpus_mode == SourceCorpusMode.NONE
            else ".lean_constellation/source"
        ),
        source_description="Real provider bootstrap fixture.",
    )


def _assert_runtime_shell_created_without_flows(repo_root: Path) -> None:
    runtime_root = repo_root / ".agent_runtime"
    assert runtime_root.is_dir()
    for name in ("homes", "scopes", "index", "snapshots"):
        assert (runtime_root / name).is_dir()
    assert list((runtime_root / "homes").iterdir()) == []
    assert list((runtime_root / "scopes").iterdir()) == []


def _create_local_upstream_git_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True)
    (repo_root / "lakefile.toml").write_text(
        'name = "UpstreamPkg"\n'
        'version = "0.1.0"\n'
        'defaultTargets = ["Upstream"]\n\n'
        '[[lean_lib]]\n'
        'name = "Upstream"\n',
        encoding="utf-8",
    )
    (repo_root / "Upstream.lean").write_text("def upstreamBootstrapSmoke := 1\n", encoding="utf-8")
    (repo_root / ".gitignore").write_text(".lake/\n", encoding="utf-8")
    subprocess.run(["lake", "build"], cwd=repo_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    subprocess.run(["git", "init"], cwd=repo_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    subprocess.run(
        ["git", "config", "user.email", "lean-constellation-real-test@example.invalid"],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Lean Constellation Real Test"],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    subprocess.run(
        ["git", "add", ".gitignore", "lakefile.toml", "lake-manifest.json", "Upstream.lean"],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial upstream fixture"],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.mark.real
def test_provider_runtime_shell_can_be_initialized_as_native_without_starting_flow(tmp_path: Path) -> None:
    runtime = _runtime(_require_real_lake())
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = runtime.repo_workspace.prepare_provider_repo_runtime_shell(
        workspace,
        target_repo="ProviderNative",
        preparation_input=_provider_input(source_corpus_mode=SourceCorpusMode.PREPARE),
        project_name="ProviderNative",
    )
    assert created.ok, created.issues
    assert created.value is not None
    provider = workspace / "ProviderNative"
    _assert_runtime_shell_created_without_flows(provider)

    initialized = runtime.repo_workspace.initialize_repo_as_native(provider, project_name="ProviderNative")
    preflight = runtime.repo_workspace.get_preparation_start_preflight(
        provider,
        expected_format=RepoFormat.NATIVE,
    )

    assert initialized.ok, initialized.issues
    assert initialized.value is not None
    assert initialized.value.repo_format == RepoFormat.NATIVE
    assert preflight.ok
    assert preflight.value is not None
    assert preflight.value.passed is True
    assert preflight.value.lake_skeleton_present is True
    assert preflight.value.issues == []


@pytest.mark.real
def test_provider_runtime_shell_can_be_initialized_as_adapter_without_network(tmp_path: Path) -> None:
    runtime = _runtime(_require_real_lake())
    workspace = tmp_path / "workspace"
    upstream = workspace / "upstream-src"
    workspace.mkdir()
    _create_local_upstream_git_repo(upstream)
    upstream_uri = upstream.resolve().as_uri()

    created = runtime.repo_workspace.prepare_provider_repo_runtime_shell(
        workspace,
        target_repo="ProviderAdapter",
        preparation_input=_provider_input(source_corpus_mode=SourceCorpusMode.NONE),
        project_name="ProviderAdapter",
    )
    assert created.ok, created.issues
    assert created.value is not None
    provider = workspace / "ProviderAdapter"
    _assert_runtime_shell_created_without_flows(provider)

    initialized = runtime.repo_workspace.initialize_repo_as_adapter(
        provider,
        upstream=UpstreamDependencyInput(
            git_url=upstream_uri,
            package_name="UpstreamPkg",
            module_name="Upstream",
            evidence_summary="Local git upstream fixture for provider bootstrap real test.",
        ),
    )
    upstream_metadata = runtime.adapter.write_adapter_upstream_metadata(
        provider,
        git_url=upstream_uri,
        package_name="UpstreamPkg",
        dependency_name="UpstreamPkg",
        evidence_summary="Local git upstream fixture for provider bootstrap real test.",
        setup_summary="Adapter provider shell initialized from local git upstream.",
    )
    preflight = runtime.repo_workspace.get_preparation_start_preflight(
        provider,
        expected_format=RepoFormat.ADAPTER,
    )

    assert initialized.ok, initialized.issues
    assert initialized.value is not None
    assert initialized.value.repo_format == RepoFormat.ADAPTER
    assert initialized.value.trusted_build is True, initialized.value.lake_check_summary
    assert upstream_metadata.ok, upstream_metadata.issues
    assert preflight.ok
    assert preflight.value is not None
    assert preflight.value.passed is True
    assert preflight.value.lake_skeleton_present is True
    assert preflight.value.adapter_upstream_metadata_exists is True
    assert preflight.value.issues == []
