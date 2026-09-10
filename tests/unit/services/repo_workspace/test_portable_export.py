from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from tests.unit.services.repo_workspace.test_repo_release import _prepare_release_repo


def _freeze_release(
    repo_root: Path,
    *,
    release_id: str,
    manifest_release_id: str | None = None,
    write_manifest: bool = True,
) -> str:
    if write_manifest:
        manifest_path = repo_root / f".lean_constellation/releases/{release_id}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "release_id": manifest_release_id or release_id,
                    "node_contract_versions": {"node_fixture": 1},
                    "completion_mode": "graph_proved",
                    "semantic_manifest_digest": "1" * 64,
                    "dependency_lock_digest": "2" * 64,
                    "summary": "Portable export test Release.",
                }
            )
            + "\n",
            encoding="utf-8",
        )
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
    second_destination = tmp_path / "portable-second"
    repeated = runtime.repo_workspace.portable_export.export_release(
        repo_root,
        release_id="release_test",
        destination=second_destination,
    )
    assert repeated.ok
    assert (second_destination / "lc-export.json").read_bytes() == (
        destination / "lc-export.json"
    ).read_bytes()


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
    assert [
        item.model_dump(mode="json")
        for item in exported.value.receipt.omitted_source_files
    ] == [
        {
            "path": ".lean_constellation/source/paper.tex",
            "reason": "Source Corpus inclusion was explicitly disabled.",
        }
    ]
    assert not (destination / ".lean_constellation/source").exists()


def test_portable_export_supports_reasoned_selective_source_omission(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "SourceRepo"
    destination = tmp_path / "portable"
    runtime, _ = _prepare_release_repo(repo_root)
    source_root = repo_root / ".lean_constellation/source"
    source_root.mkdir(parents=True)
    (source_root / "public.tex").write_text("public\n", encoding="utf-8")
    (source_root / "restricted.pdf").write_bytes(b"restricted")
    assert runtime.repo_workspace.publication.prepare_publication(repo_root).ok
    _freeze_release(repo_root, release_id="release_test")

    exported = runtime.repo_workspace.portable_export.export_release(
        repo_root,
        release_id="release_test",
        destination=destination,
        omit_source_files={
            ".lean_constellation/source/restricted.pdf": "Redistribution not authorized."
        },
    )

    assert exported.ok and exported.value is not None, exported.issues
    assert exported.value.receipt.source_materialization == "partial"
    assert (destination / ".lean_constellation/source/public.tex").is_file()
    assert not (destination / ".lean_constellation/source/restricted.pdf").exists()
    assert exported.value.receipt.omitted_source_files[0].reason == (
        "Redistribution not authorized."
    )


def test_portable_export_rejects_release_manifest_identity_mismatch(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "SourceRepo"
    destination = tmp_path / "portable"
    runtime, _ = _prepare_release_repo(repo_root)
    assert runtime.repo_workspace.publication.prepare_publication(repo_root).ok
    _freeze_release(
        repo_root,
        release_id="release_test",
        manifest_release_id="release_other",
    )

    exported = runtime.repo_workspace.portable_export.export_release(
        repo_root,
        release_id="release_test",
        destination=destination,
    )

    assert not exported.ok
    assert exported.issues[0].kind == "portable_export_release_identity_mismatch"
    assert not destination.exists()


def test_portable_export_rejects_release_without_manifest(tmp_path: Path) -> None:
    repo_root = tmp_path / "SourceRepo"
    destination = tmp_path / "portable"
    runtime, _ = _prepare_release_repo(repo_root)
    assert runtime.repo_workspace.publication.prepare_publication(repo_root).ok
    _freeze_release(
        repo_root,
        release_id="release_test",
        write_manifest=False,
    )

    exported = runtime.repo_workspace.portable_export.export_release(
        repo_root,
        release_id="release_test",
        destination=destination,
    )

    assert not exported.ok
    assert exported.issues[0].kind == "portable_export_release_manifest_missing"
    assert not destination.exists()
