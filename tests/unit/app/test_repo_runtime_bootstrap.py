from __future__ import annotations

from lean_constellation.app import create_app_runtime_services
from lean_constellation.app.bootstrap import initialize_repo_business_truth


def test_initialize_repo_business_truth_does_not_create_repo_runtime(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".root_runtime")
    repo_root = tmp_path / "Repo"

    first = initialize_repo_business_truth(runtime, repo_root)
    second = initialize_repo_business_truth(runtime, repo_root)

    assert first.ok and first.value is not None
    assert second.ok and second.value is not None
    assert first.value.repo_root == str(repo_root)
    assert (repo_root / ".lean_constellation" / "repo.json").exists()
    assert not (repo_root / ".agent_runtime").exists()
    assert first.value.created is True
    assert second.value.created is False
