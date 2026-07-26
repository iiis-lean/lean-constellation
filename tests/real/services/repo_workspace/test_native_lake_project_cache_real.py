from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from agent_runtime_kit.runtime import ARKServices
from starlette.testclient import TestClient

from tests.real.lean_test_config import (
    configured_test_lean_toolchain,
    configured_test_lean_version,
    configured_test_native_lake_project,
)
from lean_constellation.app import LeanAppConfig, create_production_app_server
from lean_constellation.services import create_test_runtime_services
from lean_constellation.services.external_clients import LakeCommandClient, LakeCommandClientConfig


def _require_template_cache() -> Path:
    for command in ("lake", "lean"):
        if shutil.which(command) is None:
            pytest.skip(f"`{command}` is required for native Lake project cache tests.")
    template_value = os.environ.get("LEAN_CONSTELLATION_LOCAL_LAKE_CACHE_PROJECT_ROOT")
    if not template_value:
        pytest.skip("Set LEAN_CONSTELLATION_LOCAL_LAKE_CACHE_PROJECT_ROOT to run native Lake project cache tests.")
    template = Path(template_value).expanduser()
    if not template.is_dir():
        pytest.skip(f"Local Lake package cache template is missing: {template}")
    if not (template / "lake-manifest.json").is_file():
        pytest.skip(f"Local Lake package cache manifest is missing: {template / 'lake-manifest.json'}")
    if not (template / ".lake" / "packages" / "mathlib").exists():
        pytest.skip(f"Local Lake package cache mathlib package is missing: {template / '.lake' / 'packages' / 'mathlib'}")
    return template


@pytest.mark.real
def test_native_lake_project_uses_local_package_cache_and_builds(tmp_path: Path) -> None:
    template = _require_template_cache()
    timeout = int(os.environ.get("LEAN_CONSTELLATION_REAL_LAKE_TIMEOUT", "180"))
    runtime = create_test_runtime_services(
        ark_services=ARKServices(),
        external_overrides={"lake": LakeCommandClient(LakeCommandClientConfig(timeout_seconds=timeout))},
        native_lake_project_config=configured_test_native_lake_project(template_root=template),
    )
    repo_root = tmp_path / "MainNative"

    initialized = runtime.repo_workspace.initialize_repo_as_native(repo_root, project_name="MainNative")

    assert initialized.ok, initialized.issues
    assert initialized.value is not None
    assert "mathlib" in initialized.value.linked_packages
    assert initialized.value.lake_manifest_path == str(repo_root / "lake-manifest.json")
    assert (repo_root / "lean-toolchain").read_text(encoding="utf-8").strip() == configured_test_lean_toolchain()
    lakefile_text = (repo_root / "lakefile.toml").read_text(encoding="utf-8")
    assert 'name = "mathlib"' in lakefile_text
    assert f'rev = "v{configured_test_lean_version()}"' in lakefile_text

    template_manifest = json.loads((template / "lake-manifest.json").read_text(encoding="utf-8"))
    repo_manifest = json.loads((repo_root / "lake-manifest.json").read_text(encoding="utf-8"))
    template_package_names = [package["name"] for package in template_manifest["packages"]]
    assert [package["name"] for package in repo_manifest["packages"]] == template_package_names
    for package_name in template_package_names:
        link = repo_root / ".lake" / "packages" / package_name
        assert link.is_symlink(), f"Expected package symlink for {package_name}: {link}"
        assert link.resolve() == (template / ".lake" / "packages" / package_name).resolve()

    lean_json = subprocess.run(
        ["lake", "env", "lean", "--json", "MainNative.lean"],
        cwd=repo_root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    assert lean_json.returncode == 0, lean_json.stderr[-2000:]


@pytest.mark.real
def test_production_server_bootstraps_main_native_repo_and_starts_preparation_dry_run(tmp_path: Path) -> None:
    template = _require_template_cache()
    timeout = int(os.environ.get("LEAN_CONSTELLATION_REAL_LAKE_TIMEOUT", "180"))
    workspace = tmp_path / "workspace"
    source_relpath = ".lean_constellation/source"
    config = LeanAppConfig(
        workspace_root=workspace,
        scheduler_enabled=False,
        materialize_agent_homes=False,
        native_lake_project=configured_test_native_lake_project(template_root=template),
    )
    app_result = create_production_app_server(
        config,
        external_overrides={"lake": LakeCommandClient(LakeCommandClientConfig(timeout_seconds=timeout))},
    )

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        shell = client.post(
            "/admin/main-repo/shell",
            json={
                "workspace_root": str(workspace),
                "repo_name": "MainRepo",
                "project_name": "MainRepo",
            },
        )
        assert shell.status_code == 200, shell.text
        repo_root = Path(shell.json()["value"]["repo_root"])
        source_dir = repo_root / source_relpath
        source_dir.mkdir(parents=True)
        (source_dir / "README.md").write_text(
            "# Source corpus\n\n"
            "Source provenance: local production server real-test fixture.\n\n"
            "Reading order: read source.tex after this README.\n\n"
            "Main material: source.tex contains the theorem statement fixture.\n\n"
            "Known gaps/extraction limits: fixture is intentionally minimal and has no extracted proof context.\n",
            encoding="utf-8",
        )
        (source_dir / "source.tex").write_text(
            r"""
\begin{theorem}[Smoke]
For every natural number n, n = n.
\end{theorem}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        written = client.post(
            "/admin/main-repo/preparation-input",
            json={
                "repo_root": str(repo_root),
                "input": {
                    "goal": "Real production-server dry run for a native Lean repo.",
                    "source_corpus_mode": "existing",
                    "source_corpus_relpath": source_relpath,
                    "source_description": "Small TeX source fixture for production runtime readiness.",
                },
            },
        )
        validated = client.post("/admin/main-repo/source-corpus/validate", json={"repo_root": str(repo_root)})
        skeleton = client.post("/admin/main-repo/native-skeleton/init", json={"repo_root": str(repo_root), "project_name": "MainRepo"})
        preparation = client.post(
            "/admin/repos/MainRepo/preparation/native/start",
            json={
                "repo_root": str(repo_root),
                "repo_key": "MainRepo",
                "start_reason": "admin",
                "admin_notes": "Production Runtime Readiness real dry run.",
                "enqueue": False,
            },
        )
        status = client.get("/admin/repos/MainRepo/runtime/status")

    assert written.status_code == 200, written.text
    assert validated.status_code == 200, validated.text
    assert validated.json()["value"]["passed"] is True
    assert validated.json()["value"]["file_count"] == 2
    assert skeleton.status_code == 200, skeleton.text
    assert skeleton.json()["value"]["linked_packages"]
    assert preparation.status_code == 200, preparation.text
    assert preparation.json()["value"]["flow_type"] == "native_repo_preparation"
    assert preparation.json()["value"]["enqueued"] is False
    assert (repo_root / ".lake" / "packages" / "mathlib").is_symlink()
    assert status.status_code == 200
    assert status.json()["value"]["paused"] is True
