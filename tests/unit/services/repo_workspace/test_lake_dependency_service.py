from pathlib import Path

from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode, UpstreamDependencyInput
from lean_constellation.domain.repo import RepoFormat
from lean_constellation.services.external_clients import ExternalCommandResult, LeanCheckSummaryView
from lean_constellation.services.foundation import FoundationService
from lean_constellation.services.repo_workspace import (
    LakeDependencyComponent,
    RepoMetadataComponent,
    RepoRequirementComponent,
    RepoWorkspaceService,
)


class FakeLakeClient:
    def __init__(self) -> None:
        self.updated: list[Path] = []
        self.built: list[tuple[Path, str | None]] = []
        self.checked: list[tuple[Path, str]] = []
        self.update_ok = True
        self.build_ok = True
        self.import_ok = True

    def run_lake_update(self, repo_root: Path) -> ExternalCommandResult:
        self.updated.append(Path(repo_root))
        return ExternalCommandResult(
            ok=self.update_ok,
            command=["lake", "update"],
            cwd=str(repo_root),
            exit_code=0 if self.update_ok else 1,
            stderr_excerpt=None if self.update_ok else "update failed",
            summary="lake update ok" if self.update_ok else "lake update failed",
        )

    def run_lake_build(self, repo_root: Path, target: str | None = None) -> ExternalCommandResult:
        self.built.append((Path(repo_root), target))
        command = ["lake", "build"] + ([target] if target else [])
        return ExternalCommandResult(
            ok=self.build_ok,
            command=command,
            cwd=str(repo_root),
            exit_code=0 if self.build_ok else 1,
            stderr_excerpt=None if self.build_ok else "build failed",
            summary="lake build ok" if self.build_ok else "lake build failed",
        )

    def run_minimal_import_check(self, repo_root: Path, module: str) -> LeanCheckSummaryView:
        self.checked.append((Path(repo_root), module))
        return LeanCheckSummaryView(
            ok=self.import_ok,
            module=module,
            command=["lean"],
            diagnostics_excerpt=None if self.import_ok else "unknown module",
            summary=f"import {module} ok" if self.import_ok else f"import {module} failed",
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


class FakeExternal:
    def __init__(self) -> None:
        self.lake = FakeLakeClient()


def test_initialize_native_skeleton_and_parse_dependencies(tmp_path: Path) -> None:
    foundation = FoundationService()
    metadata = RepoMetadataComponent(foundation)
    external = FakeExternal()
    component = LakeDependencyComponent(foundation, external, metadata)  # type: ignore[arg-type]

    result = component.initialize_native_repo_skeleton(tmp_path, project_name="MyProject", lean_toolchain="lean/test")
    assert result.ok
    assert result.value is not None
    assert result.value.repo_format == RepoFormat.NATIVE
    assert (tmp_path / "lakefile.toml").exists()
    assert (tmp_path / "lean-toolchain").read_text(encoding="utf-8") == "lean/test\n"
    assert (tmp_path / "MyProject.lean").exists()
    assert (tmp_path / "MyProject" / "Main" / "Interfaces.lean").exists()
    assert external.lake.built == [(tmp_path, None)]

    deps = component.parse_lake_dependencies(tmp_path)
    assert deps.ok
    assert deps.value is not None
    assert deps.value.dependencies == []


def test_parse_lake_dependencies_toml_lean_and_missing_lakefile(tmp_path: Path) -> None:
    foundation = FoundationService()
    metadata = RepoMetadataComponent(foundation)
    external = FakeExternal()
    component = LakeDependencyComponent(foundation, external, metadata)  # type: ignore[arg-type]

    missing = component.parse_lake_dependencies(tmp_path / "missing")
    assert missing.ok
    assert missing.value is not None
    assert missing.value.dependencies == []

    toml_repo = tmp_path / "toml_repo"
    toml_repo.mkdir()
    (toml_repo / "lakefile.toml").write_text(
        'name = "toml_repo"\n\n'
        '[[require]]\nname = "path_dep"\npath = "../path_dep"\n\n'
        '[[require]]\nname = "git_dep"\ngit = "https://example.com/git_dep.git"\nrev = "main"\nsubDir = "lean"\n',
        encoding="utf-8",
    )
    toml = component.parse_lake_dependencies(toml_repo)
    assert toml.ok
    assert toml.value is not None
    assert [(dep.name, dep.source, dep.path, dep.git, dep.rev, dep.subdir) for dep in toml.value.dependencies] == [
        ("path_dep", "path", "../path_dep", None, None, None),
        ("git_dep", "git", None, "https://example.com/git_dep.git", "main", "lean"),
    ]

    lean_repo = tmp_path / "lean_repo"
    lean_repo.mkdir()
    (lean_repo / "lakefile.lean").write_text(
        'require alpha from git "https://example.com/alpha.git" @ "main"\n'
        'require beta from path "../beta"\n',
        encoding="utf-8",
    )
    lean = component.parse_lake_dependencies(lean_repo)
    assert lean.ok
    assert lean.value is not None
    assert [(dep.name, dep.source, dep.git, dep.path) for dep in lean.value.dependencies] == [
        ("alpha", "git", "https://example.com/alpha.git", None),
        ("beta", "path", None, "../beta"),
    ]


def test_attach_workspace_dependency_updates_lakefile_and_runs_update(tmp_path: Path) -> None:
    workspace = tmp_path
    consumer = workspace / "consumer"
    provider = workspace / "provider"
    foundation = FoundationService()
    metadata = RepoMetadataComponent(foundation)
    external = FakeExternal()
    component = LakeDependencyComponent(foundation, external, metadata)  # type: ignore[arg-type]
    component.initialize_native_repo_skeleton(consumer, project_name="Consumer")
    component.initialize_native_repo_skeleton(provider, project_name="Provider")

    result = component.attach_workspace_repo_dependency(consumer, provider_repo_key="provider")
    assert result.ok
    assert result.value is not None
    assert result.value.changed is True
    text = (consumer / "lakefile.toml").read_text(encoding="utf-8")
    assert 'name = "provider"' in text
    assert 'path = "../provider"' in text

    duplicate = component.attach_workspace_repo_dependency(consumer, provider_repo_key="provider")
    assert not duplicate.ok
    assert duplicate.issues[0].kind == "dependency_already_attached"


def test_attach_workspace_dependency_failure_branches(tmp_path: Path) -> None:
    workspace = tmp_path
    consumer = workspace / "consumer"
    provider = workspace / "provider"
    foundation = FoundationService()
    metadata = RepoMetadataComponent(foundation)
    external = FakeExternal()
    component = LakeDependencyComponent(foundation, external, metadata)  # type: ignore[arg-type]
    consumer.mkdir()

    missing_provider = component.attach_workspace_repo_dependency(consumer, provider_repo_key="provider")
    assert not missing_provider.ok
    assert missing_provider.issues[0].kind == "provider_repo_not_found"

    provider.mkdir()
    missing_lakefile = component.attach_workspace_repo_dependency(consumer, provider_repo_key="provider")
    assert not missing_lakefile.ok
    assert missing_lakefile.issues[0].kind == "lakefile_not_found"

    component.initialize_native_repo_skeleton(consumer, project_name="Consumer")
    external.lake.update_ok = False
    update_failed = component.attach_workspace_repo_dependency(consumer, provider_repo_key="provider")
    assert not update_failed.ok
    assert update_failed.issues[0].kind == "lake_update_failed"


def test_initialize_native_skeleton_validation_and_check_gate(tmp_path: Path) -> None:
    foundation = FoundationService()
    metadata = RepoMetadataComponent(foundation)
    external = FakeExternal()
    component = LakeDependencyComponent(foundation, external, metadata)  # type: ignore[arg-type]

    invalid_name = component.initialize_native_repo_skeleton(tmp_path / "invalid", project_name="bad-name")
    assert not invalid_name.ok
    assert invalid_name.issues[0].kind == "invalid_lean_project_name"

    conflict_repo = tmp_path / "conflict"
    metadata.ensure_repo_model(conflict_repo, main_node="Other")
    conflict = component.initialize_native_repo_skeleton(conflict_repo, project_name="MainProject")
    assert not conflict.ok
    assert conflict.issues[0].kind == "repo_model_conflict"

    repo = tmp_path / "native"
    initialized = component.initialize_native_repo_skeleton(repo, project_name="MainProject")
    assert initialized.ok
    passed = component.check_native_repo_skeleton(repo)
    assert passed.ok
    assert passed.value is not None
    assert passed.value.passed is True

    (repo / "MainProject.lean").unlink()
    failed = component.check_native_repo_skeleton(repo)
    assert failed.ok
    assert failed.value is not None
    assert failed.value.passed is False
    assert failed.value.issues[0].kind == "native_skeleton_missing_file"


def test_initialize_adapter_skeleton_records_upstream_and_checks_import(tmp_path: Path) -> None:
    foundation = FoundationService()
    metadata = RepoMetadataComponent(foundation)
    external = FakeExternal()
    component = LakeDependencyComponent(foundation, external, metadata)  # type: ignore[arg-type]

    upstream = UpstreamDependencyInput(
        git_url="https://github.com/example/upstream.git",
        revision="main",
        subdir="lean",
        package_name="UpstreamPkg",
        module_name="Upstream",
        evidence_summary="Existing Lean repo.",
    )
    result = component.initialize_adapter_repo_skeleton(tmp_path, project_name="Adapter", upstream=upstream)
    assert result.ok
    assert result.value is not None
    assert result.value.repo_format == RepoFormat.ADAPTER
    assert result.value.trusted_build is True
    assert external.lake.updated[-1] == tmp_path
    assert external.lake.checked[-1] == (tmp_path, "Upstream")
    text = (tmp_path / "lakefile.toml").read_text(encoding="utf-8")
    assert 'git = "https://github.com/example/upstream.git"' in text
    assert 'subDir = "lean"' in text


def test_initialize_adapter_validation_and_untrusted_checks(tmp_path: Path) -> None:
    foundation = FoundationService()
    metadata = RepoMetadataComponent(foundation)
    external = FakeExternal()
    component = LakeDependencyComponent(foundation, external, metadata)  # type: ignore[arg-type]
    upstream = UpstreamDependencyInput(git_url="https://github.com/example/upstream.git", module_name="Upstream")

    invalid_name = component.initialize_adapter_repo_skeleton(tmp_path / "bad", project_name="bad-name", upstream=upstream)
    assert not invalid_name.ok
    assert invalid_name.issues[0].kind == "invalid_lean_project_name"

    external.lake.update_ok = False
    untrusted = component.initialize_adapter_repo_skeleton(tmp_path / "adapter", project_name="Adapter", upstream=upstream)
    assert untrusted.ok
    assert untrusted.value is not None
    assert untrusted.value.trusted_build is False


def test_lake_command_wrapper_failures_and_target(tmp_path: Path) -> None:
    foundation = FoundationService()
    metadata = RepoMetadataComponent(foundation)
    external = FakeExternal()
    component = LakeDependencyComponent(foundation, external, metadata)  # type: ignore[arg-type]

    build_target = component.run_lake_build(tmp_path, target="Main")
    assert build_target.ok
    assert external.lake.built[-1] == (tmp_path, "Main")

    external.lake.update_ok = False
    update = component.run_lake_update(tmp_path)
    assert not update.ok
    assert update.issues[0].kind == "lake_update_failed"

    external.lake.build_ok = False
    build = component.run_lake_build(tmp_path)
    assert not build.ok
    assert build.issues[0].kind == "lake_build_failed"

    external.lake.import_ok = False
    import_check = component.run_minimal_import_check(tmp_path, module="Missing")
    assert not import_check.ok
    assert import_check.issues[0].kind == "minimal_import_check_failed"


def test_repo_workspace_service_marks_provider_ready_and_attach(tmp_path: Path) -> None:
    workspace = tmp_path
    consumer = workspace / "consumer"
    provider = workspace / "provider"
    external = FakeExternal()
    service = RepoWorkspaceService(external=external)  # type: ignore[arg-type]
    service.metadata.ensure_repo_model(consumer)
    service.metadata.ensure_repo_model(provider)
    service.lake_dependency.initialize_native_repo_skeleton(consumer, project_name="Consumer")
    service.lake_dependency.initialize_native_repo_skeleton(provider, project_name="Provider")
    created = service.requirement.create_requirement(
        consumer,
        name="need_provider",
        target_repo="provider",
        source_description="Need provider content.",
        reason=None,
    )
    assert created.ok
    service.preparation.write_preparation_input(
        provider,
        input=RepoPreparationInput(
            goal="Provide dependency.",
            source_corpus_mode=SourceCorpusMode.PREPARE,
            requirement_refs=[{"consumer_repo": "consumer", "requirement_name": "need_provider"}],
        ),
    )

    ready = service.mark_provider_repo_ready(provider, summary="provider ready")
    assert ready.ok
    assert ready.value is not None
    assert ready.value.satisfied_requirement_count == 1

    attach = service.attach_provider_for_requirement(consumer, requirement_name="need_provider")
    assert attach.ok
    assert attach.value is not None
    assert attach.value.attached is True
    handled = service.requirement.get_requirement(consumer, name="need_provider")
    assert handled.ok
    assert handled.value is not None
    assert handled.value.requirement.status == "handled"
