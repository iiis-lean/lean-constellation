from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from lean_constellation.domain.preparation import UpstreamDependencyInput
from lean_constellation.domain.repo import RepoFormat
from lean_constellation.services.external_clients import ExternalClientService, LakeCommandClient, LakeCommandClientConfig
from lean_constellation.services.repo_workspace import RepoWorkspaceService


def _require_real_lake() -> int:
    for command in ("lake", "lean", "git"):
        if shutil.which(command) is None:
            pytest.skip(f"`{command}` is required for real repo workspace Lake tests.")
    return int(os.environ.get("LEAN_CONSTELLATION_REAL_LAKE_TIMEOUT", "180"))


def _make_buildable_without_mathlib(repo_root: Path, project_name: str) -> None:
    main_dir = repo_root / project_name / "Main"
    (main_dir / "Prelude.lean").write_text("-- real test prelude without Mathlib\n", encoding="utf-8")
    (main_dir / "Interfaces.lean").write_text(f"import {project_name}.Main.Prelude\n", encoding="utf-8")
    (main_dir / "Basic.lean").write_text(
        f"import {project_name}.Main.Prelude\n\ndef {project_name.lower()}BasicSmoke := 1\n",
        encoding="utf-8",
    )


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
    (repo_root / "Upstream.lean").write_text("def upstreamSmoke := 1\n", encoding="utf-8")
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
def test_repo_workspace_real_lake_native_adapter_and_workspace_dependency(tmp_path: Path) -> None:
    timeout = _require_real_lake()
    workspace = tmp_path / "workspace"
    consumer = workspace / "Consumer"
    provider = workspace / "Provider"
    adapter = workspace / "Adapter"
    upstream = workspace / "upstream-src"
    workspace.mkdir()

    external = ExternalClientService(
        lake=LakeCommandClient(LakeCommandClientConfig(timeout_seconds=timeout)),
    )
    service = RepoWorkspaceService(external=external)

    consumer_init = service.initialize_repo_as_native(consumer, project_name="Consumer")
    provider_init = service.initialize_repo_as_native(provider, project_name="Provider")
    assert consumer_init.ok, consumer_init.issues
    assert provider_init.ok, provider_init.issues
    assert consumer_init.value is not None
    assert provider_init.value is not None
    assert consumer_init.value.repo_format == RepoFormat.NATIVE
    assert provider_init.value.repo_format == RepoFormat.NATIVE
    assert (consumer / "lakefile.toml").exists()
    assert (provider / "Provider" / "Main" / "Interfaces.lean").exists()

    for repo, module in ((consumer, "Consumer"), (provider, "Provider")):
        _make_buildable_without_mathlib(repo, module)
        skeleton_gate = service.lake_dependency.check_native_repo_skeleton(repo)
        assert skeleton_gate.ok
        assert skeleton_gate.value is not None
        assert skeleton_gate.value.passed is True
        build = service.lake_dependency.run_lake_build(repo)
        assert build.ok, build.issues
        import_check = service.lake_dependency.run_minimal_import_check(repo, module=module)
        assert import_check.ok, import_check.issues

    attached = service.lake_dependency.attach_workspace_repo_dependency(consumer, provider_repo_key="Provider")
    assert attached.ok, attached.issues
    assert attached.value is not None
    assert attached.value.changed is True
    assert attached.value.dependency.path == "../Provider"
    deps = service.lake_dependency.parse_lake_dependencies(consumer)
    assert deps.ok
    assert deps.value is not None
    assert [(dep.name, dep.source, dep.path) for dep in deps.value.dependencies] == [("Provider", "path", "../Provider")]

    consumer_build_after_attach = service.lake_dependency.run_lake_build(consumer)
    assert consumer_build_after_attach.ok, consumer_build_after_attach.issues

    _create_local_upstream_git_repo(upstream)
    adapter_init = service.initialize_repo_as_adapter(
        adapter,
        upstream=UpstreamDependencyInput(
            git_url=upstream.resolve().as_uri(),
            package_name="UpstreamPkg",
            module_name="Upstream",
            evidence_summary="Local git upstream fixture for real Lake test.",
        ),
    )
    assert adapter_init.ok, adapter_init.issues
    assert adapter_init.value is not None
    assert adapter_init.value.repo_format == RepoFormat.ADAPTER
    assert adapter_init.value.trusted_build is True, adapter_init.value.lake_check_summary
    adapter_format = service.metadata.get_repo_format(adapter)
    assert adapter_format.ok
    assert adapter_format.value is not None
    assert adapter_format.value.repo_format == RepoFormat.ADAPTER
