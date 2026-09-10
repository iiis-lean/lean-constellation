from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from tests.unit.services.repo_workspace.test_repo_release import _prepare_release_repo


def _freeze_release(repo_root: Path, *, release_id: str) -> str:
    subprocess.run(["git", "init", "-q"], cwd=repo_root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.invalid"],
        cwd=repo_root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "LC Tests"], cwd=repo_root, check=True
    )
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "test release"], cwd=repo_root, check=True
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "update-ref",
            f"refs/lean-constellation/releases/{release_id}",
            commit,
        ],
        cwd=repo_root,
        check=True,
    )
    return commit


def test_portable_export_uses_frozen_release_and_materializes_source_corpus(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "SourceRepo"
    destination = tmp_path / "portable"
    runtime, _ = _prepare_release_repo(repo_root)
    source = repo_root / ".lean_constellation/source/paper/input.tex"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"frozen source\n")
    (repo_root / "binary.bin").write_bytes(b"\x00\xff\x10")
    assert runtime.repo_workspace.publication.prepare_publication(repo_root).ok
    commit = _freeze_release(repo_root, release_id="release_test")
    source.write_bytes(b"dirty worktree value\n")

    exported = runtime.repo_workspace.portable_export.export_release(
        repo_root,
        release_id="release_test",
        destination=destination,
    )

    assert exported.ok and exported.value is not None, exported.issues
    receipt = json.loads((destination / "lc-export.json").read_text())
    assert receipt["schema_version"] == 1
    assert receipt["release_id"] == "release_test"
    assert receipt["source_commit"] == commit
    assert receipt["source_materialization"] == "included"
    assert receipt["source_corpus_path"] == ".lean_constellation/source"
    assert (destination / ".lean_constellation/source/paper/input.tex").read_bytes() == (
        b"frozen source\n"
    )
    assert (destination / "binary.bin").read_bytes() == b"\x00\xff\x10"
    assert receipt["files"]["binary.bin"] == hashlib.sha256(b"\x00\xff\x10").hexdigest()
    assert "lc-export.json" not in receipt["files"]
    assert not (destination / ".git").exists()


def test_portable_export_can_explicitly_omit_source_corpus(tmp_path: Path) -> None:
    repo_root = tmp_path / "SourceRepo"
    destination = tmp_path / "portable"
    runtime, _ = _prepare_release_repo(repo_root)
    source = repo_root / ".lean_constellation/source/paper.tex"
    source.parent.mkdir(parents=True)
    source.write_text("source\n", encoding="utf-8")
    assert runtime.repo_workspace.publication.prepare_publication(repo_root).ok
    _freeze_release(repo_root, release_id="release_test")

    exported = runtime.repo_workspace.portable_export.export_release(
        repo_root,
        release_id="release_test",
        destination=destination,
        include_source_corpus=False,
    )

    assert exported.ok and exported.value is not None, exported.issues
    assert exported.value.receipt.source_materialization == "omitted"
    assert exported.value.receipt.omitted_source_files == [
        ".lean_constellation/source/paper.tex"
    ]
    assert not (destination / ".lean_constellation/source").exists()
