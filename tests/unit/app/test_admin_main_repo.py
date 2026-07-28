from __future__ import annotations

from pathlib import Path

from lean_constellation.app import (
    BootstrapMainNativeRepoInput,
    CreateMainRepoShellInput,
    InitializeMainNativeSkeletonInput,
    LeanAdminApi,
    RepoConfigUpdateInput,
    StartPreparationInput,
    ValidateMainSourceCorpusInput,
    WriteMainRepoPreparationInput,
    create_app_runtime_services,
)
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import ProofAvailability, RepoCompletionMode, RepoPublicationStatus
from lean_constellation.domain.publication import RepoPublicationOverride
from lean_constellation.services.external_clients import ExternalCommandResult, LeanCheckSummaryView


class FakeLakeClient:
    def __init__(self) -> None:
        self.built: list[tuple[Path, str | None]] = []

    def run_lake_update(self, repo_root: Path) -> ExternalCommandResult:
        return ExternalCommandResult(ok=True, command=["lake", "update"], cwd=str(repo_root), exit_code=0, summary="lake update ok")

    def run_lake_build(self, repo_root: Path, target: str | None = None) -> ExternalCommandResult:
        self.built.append((Path(repo_root), target))
        return ExternalCommandResult(
            ok=True,
            command=["lake", "build"] + ([target] if target else []),
            cwd=str(repo_root),
            exit_code=0,
            summary="lake build ok",
        )

    def run_minimal_import_check(self, repo_root: Path, module: str) -> LeanCheckSummaryView:
        return LeanCheckSummaryView(ok=True, module=module, command=["lean"], summary=f"import {module} ok")


def _runtime_with_fake_lake(tmp_path: Path):
    lake = FakeLakeClient()
    runtime = create_app_runtime_services(
        runtime_root=tmp_path / ".runtime",
        external_overrides={"lake": lake},
    )
    return runtime, lake


def _main_input() -> RepoPreparationInput:
    return RepoPreparationInput(
        goal="Formalize the target theorem.",
        source_corpus_mode=SourceCorpusMode.EXISTING,
        source_corpus_relpath=".lean_constellation/source",
        source_description="Copied source files for the main theorem.",
    )


def test_admin_main_repo_manual_preparation_flow(tmp_path: Path) -> None:
    runtime, lake = _runtime_with_fake_lake(tmp_path)
    admin = LeanAdminApi(runtime)

    shell = admin.create_main_repo_shell(
        CreateMainRepoShellInput(workspace_root=tmp_path, repo_name="MainRepo", project_name="MainProject")
    )
    assert shell.ok and shell.value is not None
    repo_root = Path(shell.value.repo_root)
    assert repo_root == tmp_path / "MainRepo"
    assert not (repo_root / ".lean_constellation" / "preparation_input.json").exists()

    written = admin.write_main_repo_preparation_input(
        WriteMainRepoPreparationInput(repo_root=repo_root, input=_main_input())
    )
    assert written.ok and written.value is not None

    missing_source = admin.validate_main_source_corpus(ValidateMainSourceCorpusInput(repo_root=repo_root))
    assert not missing_source.ok
    assert missing_source.issues[0].kind == "main_source_corpus_missing"

    source_dir = repo_root / ".lean_constellation" / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "source.tex").write_text("Theorem source.", encoding="utf-8")
    valid_source = admin.validate_main_source_corpus(ValidateMainSourceCorpusInput(repo_root=repo_root))
    assert valid_source.ok and valid_source.value is not None
    assert valid_source.value.file_count == 1

    skeleton = admin.initialize_main_native_skeleton(
        InitializeMainNativeSkeletonInput(repo_root=repo_root, project_name="MainProject")
    )
    assert skeleton.ok and skeleton.value is not None
    assert (repo_root / "lakefile.toml").exists()
    assert lake.built == [(repo_root, None)]

    started = admin.start_native_preparation(StartPreparationInput(repo_root=repo_root, repo_key="MainRepo"))
    assert started.ok and started.value is not None
    assert runtime.ark.flow_service.get_flow(started.value.flow_id).flow_type == "native_repo_preparation"


def test_admin_main_repo_source_corpus_validation_can_run_draft_gate(tmp_path: Path) -> None:
    runtime, _lake = _runtime_with_fake_lake(tmp_path)
    admin = LeanAdminApi(runtime)
    repo_root = tmp_path / "MainRepo"
    source_dir = repo_root / ".lean_constellation" / "source"
    source_dir.mkdir(parents=True)
    assert admin.write_main_repo_preparation_input(
        WriteMainRepoPreparationInput(repo_root=repo_root, input=_main_input())
    ).ok
    (source_dir / "README.md").write_text(
        "# Mathematical source\n\n"
        "## Source provenance\nImported from the supplied paper.\n\n"
        "## Reading order\nRead this main material first.\n\n"
        "## Main material\nThe theorem statement and proof notes are included here.\n\n"
        "## Known gaps and extraction limits\nNo known gaps.\n",
        encoding="utf-8",
    )

    validated = admin.validate_main_source_corpus(
        ValidateMainSourceCorpusInput(
            repo_root=repo_root,
            check_draft_gate=True,
            entry_path="README.md",
        )
    )

    assert validated.ok and validated.value is not None
    assert validated.value.passed is True
    assert validated.value.draft_gate is not None
    assert validated.value.draft_gate.passed is True


def test_admin_main_repo_source_corpus_validation_reports_draft_gate_issues(tmp_path: Path) -> None:
    runtime, _lake = _runtime_with_fake_lake(tmp_path)
    admin = LeanAdminApi(runtime)
    repo_root = tmp_path / "MainRepo"
    source_dir = repo_root / ".lean_constellation" / "source"
    source_dir.mkdir(parents=True)
    assert admin.write_main_repo_preparation_input(
        WriteMainRepoPreparationInput(repo_root=repo_root, input=_main_input())
    ).ok
    (source_dir / "README.md").write_text("# Notes\n", encoding="utf-8")

    validated = admin.validate_main_source_corpus(
        ValidateMainSourceCorpusInput(repo_root=repo_root, check_draft_gate=True)
    )

    assert not validated.ok
    assert "source_corpus_provenance_missing" in {issue.kind for issue in validated.issues}


def test_admin_main_repo_bootstrap_can_skip_source_validation_for_prepare_mode(tmp_path: Path) -> None:
    runtime, lake = _runtime_with_fake_lake(tmp_path)
    admin = LeanAdminApi(runtime)
    input_model = RepoPreparationInput(
        goal="Prepare source through the system.",
        source_corpus_mode=SourceCorpusMode.PREPARE,
        source_corpus_relpath=".lean_constellation/source",
    )

    result = admin.bootstrap_main_native_repo(
        BootstrapMainNativeRepoInput(
            workspace_root=tmp_path,
            repo_name="MainRepo",
            project_name="MainProject",
            preparation_input=input_model,
            validate_source_corpus=False,
        )
    )

    assert result.ok and result.value is not None
    assert result.value.skeleton.project_name == "MainProject"
    assert result.value.preparation_flow.flow_type == "native_repo_preparation"
    assert lake.built == [(tmp_path / "MainRepo", None)]


def test_admin_can_read_update_repo_config_and_publication(tmp_path: Path) -> None:
    runtime, _lake = _runtime_with_fake_lake(tmp_path)
    admin = LeanAdminApi(runtime)
    repo_root = tmp_path / "MainRepo"
    repo_root.mkdir()
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok

    default_config = admin.get_repo_config(repo_root)
    updated_config = admin.update_repo_config(
        RepoConfigUpdateInput(
            repo_root=repo_root,
            completion_mode=RepoCompletionMode.INTERFACE_DECLARED,
            default_requirement_proof_availability=ProofAvailability.PROVED,
            publication=RepoPublicationOverride(
                canonical_fetch_url="https://example.invalid/MainRepo.git"
            ),
        )
    )
    publication = admin.get_repo_publication(repo_root)

    assert default_config.ok and default_config.value is not None
    assert default_config.value.config.completion_mode == RepoCompletionMode.GRAPH_PROVED
    assert updated_config.ok and updated_config.value is not None
    assert updated_config.value.config.completion_mode == RepoCompletionMode.INTERFACE_DECLARED
    assert updated_config.value.config.default_requirement_proof_availability == ProofAvailability.PROVED
    assert updated_config.value.config.publication.canonical_fetch_url == (
        "https://example.invalid/MainRepo.git"
    )
    assert publication.ok and publication.value is not None
    assert publication.value.publication.status == RepoPublicationStatus.DEVELOPING
