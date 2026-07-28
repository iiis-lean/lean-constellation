from tests.unit_services_helpers import make_runtime, publish_adapter_provider_ready, publish_native_provider_release

from pathlib import Path

import json
import tomllib

from lean_constellation.domain.lake_project import LocalLakePackageCacheConfig, NativeLakeProjectConfig
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode, UpstreamDependencyInput
from lean_constellation.domain.repo import RepoFormat
from lean_constellation.services.external_clients import (
    ExternalCommandResult,
    LeanCheckSummaryView,
    ToolchainCommandView,
)
from lean_constellation.services.repo_workspace import LakeDependencyComponent


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
        if self.update_ok:
            parsed = tomllib.loads(
                (Path(repo_root) / "lakefile.toml").read_text(encoding="utf-8")
            )
            packages = []
            for item in parsed.get("require", []):
                if "git" not in item:
                    continue
                packages.append(
                    {
                        "name": item["name"],
                        "url": item["git"],
                        "rev": item.get("rev"),
                    }
                )
            if packages:
                (Path(repo_root) / "lake-manifest.json").write_text(
                    json.dumps({"packages": packages}),
                    encoding="utf-8",
                )
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


def _lake_component() -> tuple[LakeDependencyComponent, FakeExternal]:
    external = FakeExternal()
    runtime = make_runtime(external_overrides={"lake": external.lake})
    return runtime.repo_workspace.lake_dependency, external


def test_initialize_native_skeleton_and_parse_dependencies(tmp_path: Path) -> None:
    component, external = _lake_component()

    result = component.initialize_native_repo_skeleton(tmp_path, project_name="MyProject", lean_toolchain="lean/test")
    assert result.ok
    assert result.value is not None
    assert result.value.repo_format == RepoFormat.NATIVE
    assert "next_entry_flow" not in result.value.model_dump()
    assert (tmp_path / "lakefile.toml").exists()
    assert (tmp_path / "lean-toolchain").read_text(encoding="utf-8") == "lean/test\n"
    assert (tmp_path / "MyProject.lean").exists()
    assert (tmp_path / "MyProject" / "Main" / "Interfaces.lean").exists()
    assert external.lake.built == [(tmp_path, None)]

    deps = component.parse_lake_dependencies(tmp_path)
    assert deps.ok
    assert deps.value is not None
    assert [(dep.name, dep.source, dep.scope, dep.rev) for dep in deps.value.dependencies] == [
        ("mathlib", "registry", "leanprover-community", "v4.28.0")
    ]


def test_initialize_native_skeleton_can_disable_mathlib(tmp_path: Path) -> None:
    component, external = _lake_component()

    result = component.initialize_native_repo_skeleton(
        tmp_path,
        project_name="NoMathlibProject",
        config=NativeLakeProjectConfig(mathlib_enabled=False),
    )

    assert result.ok
    assert result.value is not None
    assert "import Mathlib" not in (tmp_path / "NoMathlibProject" / "Main" / "Prelude.lean").read_text(encoding="utf-8")
    deps = component.parse_lake_dependencies(tmp_path)
    assert deps.ok and deps.value is not None
    assert deps.value.dependencies == []


def test_initialize_native_skeleton_strictly_fails_when_lake_build_fails(tmp_path: Path) -> None:
    component, external = _lake_component()
    external.lake.build_ok = False

    result = component.initialize_native_repo_skeleton(tmp_path, project_name="BuildFails")

    assert not result.ok
    assert result.issues[0].kind == "lake_build_failed"


def test_parse_lake_dependencies_toml_lean_and_missing_lakefile(tmp_path: Path) -> None:
    component, external = _lake_component()

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


def test_initialize_native_skeleton_links_local_package_cache_and_writes_manifest(tmp_path: Path) -> None:
    component, external = _lake_component()
    template = tmp_path / "template"
    packages = template / ".lake" / "packages"
    (packages / "mathlib").mkdir(parents=True)
    (packages / "aesop").mkdir()
    (template / "lake-manifest.json").write_text(
        json.dumps(
            {
                "version": "1.1.0",
                "packagesDir": ".lake/packages",
                "packages": [
                    {"name": "mathlib", "url": "https://example.com/mathlib", "scope": "leanprover-community"},
                    {"name": "aesop", "url": "https://example.com/aesop", "scope": "leanprover-community"},
                ],
                "name": "template",
                "lakeDir": ".lake",
            }
        ),
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    config = NativeLakeProjectConfig(
        local_package_cache=LocalLakePackageCacheConfig(cache_project_root=template),
    )

    result = component.initialize_native_repo_skeleton(repo, project_name="CachedProject", config=config)

    assert result.ok
    assert result.value is not None
    assert sorted(result.value.linked_packages) == ["aesop", "mathlib"]
    assert (repo / ".lake" / "packages" / "mathlib").is_symlink()
    assert (repo / ".lake" / "packages" / "mathlib").resolve() == (packages / "mathlib").resolve()
    manifest = json.loads((repo / "lake-manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "CachedProject"
    assert [package["name"] for package in manifest["packages"]] == ["mathlib", "aesop"]
    assert result.value.lake_manifest_path == str(repo / "lake-manifest.json")


def test_initialize_native_skeleton_rejects_local_package_cache_conflict(tmp_path: Path) -> None:
    component, external = _lake_component()
    template = tmp_path / "template"
    packages = template / ".lake" / "packages"
    (packages / "mathlib").mkdir(parents=True)
    (template / "lake-manifest.json").write_text(
        json.dumps({"packages": [{"name": "mathlib"}]}),
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    (repo / ".lake" / "packages" / "mathlib").mkdir(parents=True)
    config = NativeLakeProjectConfig(
        local_package_cache=LocalLakePackageCacheConfig(cache_project_root=template),
    )

    result = component.initialize_native_repo_skeleton(repo, project_name="ConflictProject", config=config)

    assert not result.ok
    assert result.issues[0].kind == "local_lake_cache_package_conflict"


def test_attach_workspace_dependency_updates_lakefile_and_runs_update(tmp_path: Path) -> None:
    workspace = tmp_path
    consumer = workspace / "consumer"
    provider = workspace / "provider"
    component, external = _lake_component()
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
    component, external = _lake_component()
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
    assert 'name = "provider"' not in (consumer / "lakefile.toml").read_text(encoding="utf-8")


def test_attach_released_git_dependency_uses_exact_commit_and_canonical_transport(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path
    consumer = workspace / "Consumer"
    provider = workspace / "Provider"
    component, _ = _lake_component()
    runtime = component.runtime
    consumer.mkdir()
    (consumer / "lakefile.toml").write_text(
        'name = "Consumer"\n\n[[lean_lib]]\nname = "Consumer"\n',
        encoding="utf-8",
    )
    release = publish_native_provider_release(
        runtime, provider, release_id="provider_r1"
    )
    commit = runtime.repo_workspace.git_release.resolve_release_commit(
        provider, release_id=release.release_id
    ).value
    calls: list[tuple[list[str] | None, dict[str, str] | None]] = []

    def update(_repo_root, *, packages=None, transport_rewrites=None):  # noqa: ANN001, ANN202
        calls.append((packages, transport_rewrites))
        (consumer / "lake-manifest.json").write_text(
            json.dumps(
                {
                    "packages": [
                        {
                            "name": "Provider",
                            "url": "https://example.invalid/Provider.git",
                            "rev": commit,
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
                summary="updated",
            )
        )

    def build(_repo_root, *, target=None, transport_rewrites=None):  # noqa: ANN001, ANN202
        assert target is None
        assert transport_rewrites == {
            "https://example.invalid/Provider.git": provider.resolve().as_uri()
        }
        return runtime.foundation.ok(
            ToolchainCommandView(
                ok=True,
                command=["lake", "build"],
                exit_code=0,
                summary="built",
            )
        )

    monkeypatch.setattr(component, "run_lake_update", update)
    monkeypatch.setattr(component, "run_lake_build", build)
    attached = component.attach_released_repo_git_dependency(
        consumer,
        provider_repo_key="Provider",
        provider_release_id=release.release_id,
        canonical_git_url="https://example.invalid/Provider.git",
    )

    assert attached.ok and attached.value is not None, attached.issues
    assert attached.value.pin.provider_commit == commit
    assert calls == [
        (
            ["Provider"],
            {
                "https://example.invalid/Provider.git": provider.resolve().as_uri()
            },
        )
    ]
    lakefile = (consumer / "lakefile.toml").read_text(encoding="utf-8")
    assert 'git = "https://example.invalid/Provider.git"' in lakefile
    assert f'rev = "{commit}"' in lakefile


def test_initialize_native_skeleton_validation_and_check_gate(tmp_path: Path) -> None:
    component, external = _lake_component()

    invalid_name = component.initialize_native_repo_skeleton(tmp_path / "invalid", project_name="bad-name")
    assert not invalid_name.ok
    assert invalid_name.issues[0].kind == "invalid_lean_project_name"

    conflict_repo = tmp_path / "conflict"
    component.metadata.ensure_repo_model(conflict_repo, main_node="Other")
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
    component, external = _lake_component()

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
    assert "next_entry_flow" not in result.value.model_dump()
    assert external.lake.updated[-1] == tmp_path
    assert external.lake.checked[-1] == (tmp_path, "Upstream")
    text = (tmp_path / "lakefile.toml").read_text(encoding="utf-8")
    assert 'git = "https://github.com/example/upstream.git"' in text
    assert 'subDir = "lean"' in text


def test_initialize_adapter_validation_and_untrusted_checks(tmp_path: Path) -> None:
    component, external = _lake_component()
    upstream = UpstreamDependencyInput(git_url="https://github.com/example/upstream.git", module_name="Upstream")

    invalid_name = component.initialize_adapter_repo_skeleton(tmp_path / "bad", project_name="bad-name", upstream=upstream)
    assert not invalid_name.ok
    assert invalid_name.issues[0].kind == "invalid_lean_project_name"

    external.lake.update_ok = False
    update_failed = component.initialize_adapter_repo_skeleton(tmp_path / "adapter_update", project_name="Adapter", upstream=upstream)
    assert not update_failed.ok
    assert update_failed.issues[0].kind == "lake_update_failed"

    external.lake.update_ok = True
    external.lake.build_ok = False
    build_failed = component.initialize_adapter_repo_skeleton(tmp_path / "adapter_build", project_name="Adapter", upstream=upstream)
    assert not build_failed.ok
    assert build_failed.issues[0].kind == "lake_build_failed"

    external.lake.build_ok = True
    external.lake.import_ok = False
    import_failed = component.initialize_adapter_repo_skeleton(tmp_path / "adapter_import", project_name="Adapter", upstream=upstream)
    assert not import_failed.ok
    assert import_failed.issues[0].kind == "minimal_import_check_failed"


def test_lake_command_wrapper_failures_and_target(tmp_path: Path) -> None:
    component, external = _lake_component()

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
    service = make_runtime(external_overrides={"lake": external.lake}).repo_workspace
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

    release = publish_native_provider_release(service.runtime, provider, summary="provider ready")
    reconciled = service.runtime.validation_snapshot.reconcile_provider_requirements(
        provider, release_id=release.release_id
    )
    assert reconciled.ok and reconciled.value is not None
    assert reconciled.value.satisfied == ["consumer/need_provider"]

    attach = service.attach_provider_for_requirement(consumer, requirement_name="need_provider")
    assert attach.ok
    assert attach.value is not None
    assert attach.value.attached is True
    handled = service.requirement.get_requirement(consumer, name="need_provider")
    assert handled.ok
    assert handled.value is not None
    assert handled.value.requirement.status == "handled"


def test_attach_provider_rejects_open_waiting_requirement_before_lake_mutation(tmp_path: Path) -> None:
    workspace = tmp_path
    consumer = workspace / "consumer"
    provider = workspace / "provider"
    external = FakeExternal()
    service = make_runtime(external_overrides={"lake": external.lake}).repo_workspace
    assert service.metadata.ensure_repo_model(consumer).ok
    assert service.metadata.ensure_repo_model(provider).ok
    assert service.lake_dependency.initialize_native_repo_skeleton(consumer, project_name="Consumer").ok
    assert service.lake_dependency.initialize_native_repo_skeleton(provider, project_name="Provider").ok
    created = service.requirement.create_requirement(
        consumer,
        name="need_provider",
        target_repo="provider",
        reason="Need provider content.",
    )
    assert created.ok
    waiting = service.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider",
        provider_repo="provider",
    )
    assert waiting.ok
    lakefile_before = (consumer / "lakefile.toml").read_text(encoding="utf-8")

    attached = service.attach_provider_for_requirement(consumer, requirement_name="need_provider")

    assert not attached.ok
    assert attached.issues[0].kind == "requirement_not_satisfied"
    assert (consumer / "lakefile.toml").read_text(encoding="utf-8") == lakefile_before


def test_attach_provider_for_requirement_is_idempotent_after_handled(tmp_path: Path) -> None:
    workspace = tmp_path
    consumer = workspace / "consumer"
    provider = workspace / "provider"
    external = FakeExternal()
    service = make_runtime(external_overrides={"lake": external.lake}).repo_workspace
    assert service.metadata.ensure_repo_model(consumer).ok
    assert service.metadata.ensure_repo_model(provider).ok
    assert service.lake_dependency.initialize_native_repo_skeleton(consumer, project_name="Consumer").ok
    assert service.lake_dependency.initialize_native_repo_skeleton(provider, project_name="Provider").ok
    assert service.requirement.create_requirement(
        consumer,
        name="need_provider",
        target_repo="provider",
        reason="Need provider content.",
    ).ok
    assert service.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider",
        provider_repo="provider",
    ).ok
    publish_native_provider_release(service.runtime, provider, summary="Provider ready.")
    assert service.requirement.mark_requirement_satisfied(
        consumer,
        requirement_name="need_provider",
        provider_repo="provider",
    ).ok
    assert service.mark_requirement_result_observed(consumer, requirement_name="need_provider").ok

    first = service.attach_provider_for_requirement(consumer, requirement_name="need_provider")
    lakefile_after_first = (consumer / "lakefile.toml").read_text(encoding="utf-8")
    second = service.attach_provider_for_requirement(consumer, requirement_name="need_provider")

    assert first.ok and first.value is not None
    assert second.ok and second.value is not None
    assert second.value.handled is True
    assert (consumer / "lakefile.toml").read_text(encoding="utf-8") == lakefile_after_first


def test_attach_ready_workspace_repo_dependency_requires_stable_repo(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    provider = tmp_path / "provider"
    external = FakeExternal()
    service = make_runtime(external_overrides={"lake": external.lake}).repo_workspace
    assert service.lake_dependency.initialize_native_repo_skeleton(consumer, project_name="Consumer").ok
    assert service.lake_dependency.initialize_native_repo_skeleton(provider, project_name="Provider").ok

    developing = service.attach_ready_workspace_repo_dependency(consumer, provider_repo="provider")

    assert not developing.ok
    assert developing.issues[0].kind == "provider_repo_not_ready"
    publish_native_provider_release(service.runtime, provider, summary="Reusable mathematical repo.")

    attached = service.attach_ready_workspace_repo_dependency(consumer, provider_repo="provider")

    assert attached.ok and attached.value is not None
    assert attached.value.pin.provider_repo_key == "provider"
    assert attached.value.dependency.source == "git"
    assert attached.value.dependency.rev == attached.value.pin.provider_commit
    assert 'name = "provider"' in (consumer / "lakefile.toml").read_text(encoding="utf-8")


def test_attach_ready_workspace_repo_dependency_accepts_ready_adapter_without_release(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    provider = tmp_path / "adapter_provider"
    external = FakeExternal()
    service = make_runtime(external_overrides={"lake": external.lake}).repo_workspace
    assert service.lake_dependency.initialize_native_repo_skeleton(consumer, project_name="Consumer").ok
    publish_adapter_provider_ready(service.runtime, provider, summary="Reusable adapter provider.")

    attached = service.attach_ready_workspace_repo_dependency(
        consumer,
        provider_repo="adapter_provider",
    )

    assert attached.ok and attached.value is not None
    assert attached.value.provider_repo_key == "adapter_provider"
    publication = service.metadata.get_repo_publication(provider)
    assert publication.ok and publication.value is not None
    assert publication.value.publication.latest_release_id is None
