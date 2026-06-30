from __future__ import annotations

from lean_constellation.app import create_app_runtime_services, initialize_repo_runtime


def test_initialize_repo_runtime_creates_repo_and_runtime_shell_idempotently(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".root_runtime")
    repo_root = tmp_path / "Repo"

    first = initialize_repo_runtime(runtime, repo_root)
    second = initialize_repo_runtime(runtime, repo_root)

    assert first.ok and first.value is not None
    assert second.ok and second.value is not None
    assert first.value.repo_root == str(repo_root)
    assert (repo_root / ".lean_constellation" / "repo.json").exists()
    for name in ("homes", "scopes", "index", "snapshots"):
        assert (repo_root / ".agent_runtime" / name).is_dir()
    assert first.value.created is True
    assert second.value.created is False
