from __future__ import annotations

from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode, UpstreamDependencyInput
from lean_constellation.domain.repo import RepoFormat
from lean_constellation.services.external_clients import ExternalCommandResult, LeanCheckSummaryView


class FakeLakeClient:
    def run_lake_update(self, repo_root: Path) -> ExternalCommandResult:
        return ExternalCommandResult(
            ok=True,
            command=["lake", "update"],
            cwd=str(repo_root),
            exit_code=0,
            summary="lake update ok",
        )

    def run_lake_build(self, repo_root: Path, target: str | None = None) -> ExternalCommandResult:
        command = ["lake", "build"] + ([target] if target else [])
        return ExternalCommandResult(
            ok=True,
            command=command,
            cwd=str(repo_root),
            exit_code=0,
            summary="lake build ok",
        )

    def run_minimal_import_check(self, repo_root: Path, module: str) -> LeanCheckSummaryView:
        return LeanCheckSummaryView(
            ok=True,
            module=module,
            command=["lean"],
            summary=f"import {module} ok",
        )

    def summarize_command_result(self, result: ExternalCommandResult):
        from lean_constellation.services.external_clients import LakeCommandSummaryView

        return LakeCommandSummaryView(
            ok=result.ok,
            command=result.command,
            summary=result.summary or "",
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            stderr_excerpt=result.stderr_excerpt,
        )


def _runtime():
    return make_runtime(external_overrides={"lake": FakeLakeClient()})


def test_native_preparation_start_preflight_ready(tmp_path: Path) -> None:
    runtime = _runtime()
    repo_root = tmp_path / "native_repo"
    source_root = repo_root / ".lean_constellation" / "source"
    runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Prepare native repo.",
            source_corpus_mode=SourceCorpusMode.EXISTING,
            source_corpus_relpath=".lean_constellation/source",
        ),
    )
    source_root.mkdir(parents=True)
    initialized = runtime.repo_workspace.initialize_repo_as_native(repo_root, project_name="NativeProject")

    preflight = runtime.repo_workspace.get_preparation_start_preflight(
        repo_root,
        expected_format=RepoFormat.NATIVE,
    )

    assert initialized.ok
    assert preflight.ok
    assert preflight.value is not None
    assert preflight.value.passed is True
    assert preflight.value.repo_format == RepoFormat.NATIVE
    assert preflight.value.preparation_input_exists is True
    assert preflight.value.source_corpus_exists is True
    assert preflight.value.lake_skeleton_present is True
    assert preflight.value.issues == []
    assert preflight.value.warnings == []


def test_adapter_preparation_start_preflight_ready(tmp_path: Path) -> None:
    runtime = _runtime()
    repo_root = tmp_path / "adapter_repo"
    runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Prepare adapter repo.",
            source_corpus_mode=SourceCorpusMode.NONE,
            source_corpus_relpath=None,
        ),
    )
    initialized = runtime.repo_workspace.initialize_repo_as_adapter(
        repo_root,
        upstream=UpstreamDependencyInput(
            git_url="https://github.com/example/upstream.git",
            package_name="UpstreamPkg",
            module_name="Upstream",
            evidence_summary="Existing upstream Lean repo.",
        ),
    )
    upstream = runtime.adapter.write_adapter_upstream_metadata(
        repo_root,
        git_url="https://github.com/example/upstream.git",
        package_name="UpstreamPkg",
        dependency_name="UpstreamPkg",
        evidence_summary="Existing upstream Lean repo.",
        setup_summary="Adapter skeleton initialized.",
    )

    preflight = runtime.repo_workspace.get_preparation_start_preflight(
        repo_root,
        expected_format=RepoFormat.ADAPTER,
    )

    assert initialized.ok
    assert upstream.ok
    assert preflight.ok
    assert preflight.value is not None
    assert preflight.value.passed is True
    assert preflight.value.repo_format == RepoFormat.ADAPTER
    assert preflight.value.preparation_input_exists is True
    assert preflight.value.lake_skeleton_present is True
    assert preflight.value.adapter_upstream_metadata_exists is True
    assert preflight.value.issues == []


def test_preparation_start_preflight_reports_missing_input(tmp_path: Path) -> None:
    runtime = _runtime()
    repo_root = tmp_path / "repo"
    runtime.repo_workspace.metadata.ensure_repo_model(repo_root)
    runtime.repo_workspace.metadata.set_repo_format(
        repo_root,
        repo_format=RepoFormat.NATIVE,
        reason="test",
    )

    preflight = runtime.repo_workspace.get_preparation_start_preflight(
        repo_root,
        expected_format=RepoFormat.NATIVE,
    )

    assert preflight.ok
    assert preflight.value is not None
    assert preflight.value.passed is False
    assert "preparation_input_missing" in {issue.kind for issue in preflight.value.issues}


def test_preparation_start_preflight_reports_wrong_format(tmp_path: Path) -> None:
    runtime = _runtime()
    repo_root = tmp_path / "repo"
    runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Prepare adapter repo.",
            source_corpus_mode=SourceCorpusMode.NONE,
            source_corpus_relpath=None,
        ),
    )
    runtime.repo_workspace.metadata.set_repo_format(
        repo_root,
        repo_format=RepoFormat.ADAPTER,
        reason="test",
    )

    preflight = runtime.repo_workspace.get_preparation_start_preflight(
        repo_root,
        expected_format=RepoFormat.NATIVE,
    )

    assert preflight.ok
    assert preflight.value is not None
    assert preflight.value.passed is False
    assert "preparation_start_repo_format_mismatch" in {issue.kind for issue in preflight.value.issues}


def test_preparation_start_preflight_warns_missing_existing_source(tmp_path: Path) -> None:
    runtime = _runtime()
    repo_root = tmp_path / "native_repo"
    runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Prepare native repo.",
            source_corpus_mode=SourceCorpusMode.EXISTING,
            source_corpus_relpath=".lean_constellation/source",
        ),
    )
    initialized = runtime.repo_workspace.initialize_repo_as_native(repo_root, project_name="NativeProject")

    preflight = runtime.repo_workspace.get_preparation_start_preflight(
        repo_root,
        expected_format=RepoFormat.NATIVE,
    )

    assert initialized.ok
    assert preflight.ok
    assert preflight.value is not None
    assert preflight.value.passed is True
    assert preflight.value.source_corpus_exists is False
    assert preflight.value.issues == []
    assert [warning.kind for warning in preflight.value.warnings] == [
        "preparation_start_source_corpus_missing"
    ]
