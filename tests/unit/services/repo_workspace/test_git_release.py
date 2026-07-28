from __future__ import annotations

import subprocess
from pathlib import Path

from lean_constellation.domain.repo import RepoCompletionMode
from lean_constellation.domain.repo_release import RepoRelease
from tests.unit_services_helpers import make_runtime


def _release(release_id: str, *, parent: str | None = None) -> RepoRelease:
    return RepoRelease(
        release_id=release_id,
        parent_release_id=parent,
        node_contract_versions={"node_main": 1},
        completion_mode=RepoCompletionMode.GRAPH_PROVED,
        semantic_manifest_digest="1" * 64,
        dependency_lock_digest="2" * 64,
        summary=f"Release {release_id}.",
    )


def _write_release_candidate(repo_root: Path, release: RepoRelease, *, body: str) -> list[str]:
    manifest = repo_root / ".lean_constellation" / "releases" / f"{release.release_id}.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(release.model_dump_json(indent=2) + "\n", encoding="utf-8")
    source = repo_root / "Main.lean"
    source.write_text(body, encoding="utf-8")
    gitignore = repo_root / ".gitignore"
    gitignore.write_text("/.runtime/\n", encoding="utf-8")
    return [manifest.relative_to(repo_root).as_posix(), ".gitignore", "Main.lean"]


def _git(repo_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def test_git_release_commit_is_exact_atomic_and_validated(tmp_path: Path) -> None:
    runtime = make_runtime()
    service = runtime.repo_workspace.git_release
    initialized = service.ensure_independent_repo(tmp_path)

    assert initialized.ok and initialized.value is not None
    assert initialized.value.independent
    first = _release("release_1")
    first_files = _write_release_candidate(tmp_path, first, body="def one : Nat := 1\n")
    ignored = tmp_path / ".runtime" / "trace.json"
    ignored.parent.mkdir(parents=True)
    ignored.write_text("{}\n", encoding="utf-8")
    committed = service.commit_release(
        tmp_path,
        release=first,
        candidate_files=first_files,
        expected_head=None,
    )

    assert committed.ok and committed.value is not None
    assert _git(tmp_path, "rev-parse", "HEAD") == committed.value.commit
    assert _git(tmp_path, "rev-parse", "refs/lean-constellation/releases/release_1") == committed.value.commit
    assert _git(tmp_path, "ls-tree", "-r", "--name-only", committed.value.commit).splitlines() == sorted(first_files)
    assert ".runtime/trace.json" not in committed.value.published_files
    assert service.validate_release(tmp_path, release=first).ok
    assert not service.commit_release(
        tmp_path,
        release=first,
        candidate_files=first_files,
        expected_head=committed.value.commit,
    ).ok

    second = _release("release_2", parent="release_1")
    second_files = _write_release_candidate(tmp_path, second, body="def one : Nat := 2\n")
    second_files.append(".lean_constellation/releases/release_1.json")
    committed_second = service.commit_release(
        tmp_path,
        release=second,
        candidate_files=second_files,
        expected_head=committed.value.commit,
    )

    assert committed_second.ok and committed_second.value is not None
    assert committed_second.value.parent_commit == committed.value.commit
    assert _git(tmp_path, "status", "--porcelain=v1") == ""
    assert service.validate_release(tmp_path, release=second).ok


def test_git_release_rejects_staged_and_excluded_paths(tmp_path: Path) -> None:
    runtime = make_runtime()
    service = runtime.repo_workspace.git_release
    assert service.ensure_independent_repo(tmp_path).ok
    release = _release("release_1")
    files = _write_release_candidate(tmp_path, release, body="def one : Nat := 1\n")
    subprocess.run(["git", "add", "Main.lean"], cwd=tmp_path, check=True)

    staged = service.commit_release(
        tmp_path,
        release=release,
        candidate_files=files,
        expected_head=None,
    )

    assert not staged.ok
    assert staged.issues[0].kind == "git_index_not_clean"
    subprocess.run(["git", "rm", "--cached", "Main.lean"], cwd=tmp_path, check=True)
    runtime_path = tmp_path / ".runtime" / "trace.json"
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_text("{}\n", encoding="utf-8")
    excluded = service.commit_release(
        tmp_path,
        release=release,
        candidate_files=[*files, ".runtime/trace.json"],
        expected_head=None,
    )

    assert not excluded.ok
    assert excluded.issues[0].kind == "git_release_path_excluded"


def test_nested_workspace_repo_can_be_initialized_independently(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
        cwd=tmp_path,
        check=True,
    )
    nested = tmp_path / "Uniform"
    nested.mkdir()
    runtime = make_runtime()
    service = runtime.repo_workspace.git_release

    before = service.inspect_repo(nested)
    after = service.ensure_independent_repo(nested)

    assert before.ok and before.value is not None and not before.value.independent
    assert after.ok and after.value is not None and after.value.independent
    assert Path(_git(nested, "rev-parse", "--show-toplevel")) == nested


def test_git_release_restore_requires_exact_preview_token(tmp_path: Path) -> None:
    runtime = make_runtime()
    service = runtime.repo_workspace.git_release
    assert service.ensure_independent_repo(tmp_path).ok
    first = _release("release_1")
    first_files = _write_release_candidate(
        tmp_path, first, body="def one : Nat := 1\n"
    )
    committed_first = service.commit_release(
        tmp_path,
        release=first,
        candidate_files=first_files,
        expected_head=None,
    )
    assert committed_first.ok and committed_first.value is not None
    second = _release("release_2", parent="release_1")
    second_files = _write_release_candidate(
        tmp_path, second, body="def one : Nat := 2\n"
    )
    second_files.append(".lean_constellation/releases/release_1.json")
    committed_second = service.commit_release(
        tmp_path,
        release=second,
        candidate_files=second_files,
        expected_head=committed_first.value.commit,
    )
    assert committed_second.ok and committed_second.value is not None

    preview = service.preview_restore_release(
        tmp_path,
        release_id=first.release_id,
    )
    assert preview.ok and preview.value is not None
    subprocess.run(
        ["git", "checkout", "--detach", committed_first.value.commit],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
    )
    historical_manifest = service.read_release_manifest(
        tmp_path,
        release_id=second.release_id,
    )
    assert historical_manifest.ok
    assert historical_manifest.value == second
    stale = service.apply_restore_release(
        tmp_path,
        preview=preview.value,
        expected_recovery_token=preview.value.recovery_token,
    )
    assert not stale.ok
    assert stale.issues[0].kind == "git_release_restore_token_mismatch"

    subprocess.run(
        ["git", "checkout", "--detach", committed_second.value.commit],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
    )
    refreshed = service.preview_restore_release(
        tmp_path,
        release_id=first.release_id,
    )
    assert refreshed.ok and refreshed.value is not None
    restored = service.apply_restore_release(
        tmp_path,
        preview=refreshed.value,
        expected_recovery_token=refreshed.value.recovery_token,
    )
    assert restored.ok and restored.value is not None
    assert restored.value.previous_head == committed_second.value.commit
    assert _git(tmp_path, "rev-parse", "HEAD") == committed_first.value.commit
