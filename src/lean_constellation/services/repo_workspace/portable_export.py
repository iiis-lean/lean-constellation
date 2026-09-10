"""Materialize one immutable Release as a portable directory."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import ServiceResult, classify_repo_path

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class PortableExportReceipt(StrictModel):
    schema_version: int = 1
    release_id: str
    source_commit: str
    include_source_corpus: bool = True
    source_corpus_path: str = ".lean_constellation/source"
    source_materialization: Literal["included", "omitted", "absent"]
    omitted_source_files: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)
    summary: str


class PortableExportView(StrictModel):
    destination: str
    receipt: PortableExportReceipt
    summary: str


class PortableExportComponent:
    """Export regular, publication-eligible files from an exact Release commit."""

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def export_release(
        self,
        repo_root: Path,
        *,
        release_id: str,
        destination: Path,
        include_source_corpus: bool = True,
    ) -> ServiceResult[PortableExportView]:
        repo_root = Path(repo_root).resolve()
        destination = Path(destination).resolve(strict=False)
        try:
            destination.relative_to(repo_root)
        except ValueError:
            pass
        else:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "portable_export_destination_inside_repo",
                    "Portable export destination must be independent of the source repository.",
                    object_ref=str(destination),
                )
            )
        if destination.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "portable_export_destination_exists",
                    "Portable export destination must not already exist.",
                    object_ref=str(destination),
                )
            )
        resolved = self.runtime.repo_workspace.git_release.resolve_release_commit(
            repo_root, release_id=release_id
        )
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        commit = resolved.value
        listed = self._git(repo_root, "ls-tree", "-r", "-z", "--full-tree", commit)
        if listed.returncode != 0:
            return self._git_failure(
                "portable_export_tree_unreadable",
                "Could not enumerate the immutable Release tree.",
                listed,
                object_ref=release_id,
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}.lc-export-", dir=destination.parent)
        )
        files: dict[str, str] = {}
        omitted_source_files: list[str] = []
        source_seen = False
        try:
            for record in listed.stdout.split(b"\0"):
                if not record:
                    continue
                metadata, separator, raw_path = record.partition(b"\t")
                if not separator:
                    return self._invalid_tree(staging, release_id, "malformed tree record")
                mode, object_type, object_id = metadata.decode("ascii").split(" ", 2)
                relative = PurePosixPath(raw_path.decode("utf-8"))
                if (
                    relative.is_absolute()
                    or not relative.parts
                    or any(part in {"", ".", ".."} for part in relative.parts)
                    or object_type != "blob"
                    or not mode.startswith("100")
                ):
                    return self._invalid_tree(staging, release_id, relative.as_posix())
                relpath = relative.as_posix()
                classification = classify_repo_path(relative)
                if classification.requires_migration:
                    return self._invalid_tree(staging, release_id, relpath)
                if not classification.publication_eligible or relpath == "lc-export.json":
                    continue
                in_source = relative.parts[:2] == (".lean_constellation", "source")
                if in_source:
                    source_seen = True
                    if not include_source_corpus:
                        omitted_source_files.append(relpath)
                        continue
                blob = self._git(repo_root, "cat-file", "blob", object_id)
                if blob.returncode != 0:
                    return self._git_failure(
                        "portable_export_blob_unreadable",
                        "Could not read a Release blob during portable export.",
                        blob,
                        object_ref=relpath,
                        cleanup=staging,
                    )
                target = staging.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob.stdout)
                files[relpath] = hashlib.sha256(blob.stdout).hexdigest()
            materialization: Literal["included", "omitted", "absent"] = (
                "included"
                if source_seen and include_source_corpus
                else "omitted" if source_seen else "absent"
            )
            receipt = PortableExportReceipt(
                release_id=release_id,
                source_commit=commit,
                include_source_corpus=include_source_corpus,
                source_materialization=materialization,
                omitted_source_files=sorted(omitted_source_files),
                files={path: files[path] for path in sorted(files)},
                summary=(
                    f"Exported {len(files)} regular files from Release {release_id}; "
                    f"Source Corpus is {materialization}."
                ),
            )
            (staging / "lc-export.json").write_text(
                json.dumps(
                    receipt.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(staging, destination)
        except OSError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "portable_export_write_failed",
                    f"Portable export could not be materialized: {exc}",
                    object_ref=str(destination),
                )
            )
        return self.runtime.foundation.ok(
            PortableExportView(
                destination=str(destination),
                receipt=receipt,
                summary=receipt.summary,
            )
        )

    @staticmethod
    def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _invalid_tree(
        self, staging: Path, release_id: str, object_ref: str
    ) -> ServiceResult[PortableExportView]:
        shutil.rmtree(staging, ignore_errors=True)
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "portable_export_tree_entry_invalid",
                "Release tree contains a non-regular, unsafe, or legacy path.",
                object_ref=object_ref,
                details={"release_id": release_id},
            )
        )

    def _git_failure(
        self,
        kind: str,
        message: str,
        result: subprocess.CompletedProcess[bytes],
        *,
        object_ref: str,
        cleanup: Path | None = None,
    ) -> ServiceResult[PortableExportView]:
        if cleanup is not None:
            shutil.rmtree(cleanup, ignore_errors=True)
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                kind,
                message,
                object_ref=object_ref,
                details={"stderr": result.stderr.decode("utf-8", errors="replace")},
            )
        )


__all__ = [
    "PortableExportComponent",
    "PortableExportReceipt",
    "PortableExportView",
]
