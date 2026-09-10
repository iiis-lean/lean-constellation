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
from lean_constellation.domain.repo_release import RepoRelease
from lean_constellation.services.foundation import ServiceResult, classify_repo_path

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class OmittedSourceFile(StrictModel):
    path: str
    reason: str


class PortableExportReceipt(StrictModel):
    schema_version: int = 1
    release_id: str
    source_commit: str
    include_source_corpus: bool = True
    source_corpus_path: str = ".lean_constellation/source"
    source_materialization: Literal["included", "partial", "omitted", "absent"]
    omitted_source_files: list[OmittedSourceFile] = Field(default_factory=list)
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
        omit_source_files: dict[str, str] | None = None,
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
        requested_omissions: dict[str, str] = {}
        for raw_path, raw_reason in (omit_source_files or {}).items():
            path = PurePosixPath(raw_path)
            reason = raw_reason.strip()
            if (
                path.is_absolute()
                or path.parts[:2] != (".lean_constellation", "source")
                or any(part in {"", ".", ".."} for part in path.parts)
                or not reason
            ):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "portable_export_source_omission_invalid",
                        "Source omission requires a canonical source file path and non-empty reason.",
                        object_ref=raw_path,
                    )
                )
            requested_omissions[path.as_posix()] = reason
        resolved = self.runtime.repo_workspace.git_release.resolve_release_commit(
            repo_root, release_id=release_id
        )
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        commit = resolved.value
        release_manifest_path = (
            f".lean_constellation/releases/{release_id}.json"
        )
        manifest = self._git(repo_root, "show", f"{commit}:{release_manifest_path}")
        if manifest.returncode != 0:
            return self._git_failure(
                "portable_export_release_manifest_missing",
                "Release commit does not contain its exact immutable manifest.",
                manifest,
                object_ref=release_manifest_path,
            )
        try:
            release = RepoRelease.model_validate_json(manifest.stdout)
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "portable_export_release_manifest_invalid",
                    f"Release commit contains an invalid manifest: {exc}",
                    object_ref=release_manifest_path,
                )
            )
        if release.release_id != release_id:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "portable_export_release_identity_mismatch",
                    "Release manifest identity does not match the requested Release ref.",
                    object_ref=release_id,
                    current=release.release_id,
                )
            )
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
        omitted_source_files: list[OmittedSourceFile] = []
        source_seen = False
        included_source_seen = False
        try:
            for record in listed.stdout.split(b"\0"):
                if not record:
                    continue
                metadata, separator, raw_path = record.partition(b"\t")
                if not separator:
                    return self._invalid_tree(staging, release_id, "malformed tree record")
                try:
                    mode, object_type, object_id = metadata.decode("ascii").split(" ", 2)
                    relative = PurePosixPath(raw_path.decode("utf-8"))
                except (UnicodeError, ValueError):
                    return self._invalid_tree(
                        staging, release_id, "non-portable tree record"
                    )
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
                    omission_reason = requested_omissions.get(relpath)
                    if not include_source_corpus or omission_reason is not None:
                        omitted_source_files.append(
                            OmittedSourceFile(
                                path=relpath,
                                reason=(
                                    omission_reason
                                    or "Source Corpus inclusion was explicitly disabled."
                                ),
                            )
                        )
                        continue
                    included_source_seen = True
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
            missing_omissions = sorted(
                set(requested_omissions)
                - {item.path for item in omitted_source_files}
            )
            if missing_omissions:
                return self._invalid_tree(
                    staging,
                    release_id,
                    "unknown source omissions: " + ", ".join(missing_omissions),
                )
            materialization: Literal["included", "partial", "omitted", "absent"] = (
                "partial"
                if included_source_seen and omitted_source_files
                else "included"
                if included_source_seen
                else "omitted"
                if source_seen
                else "absent"
            )
            receipt = PortableExportReceipt(
                release_id=release_id,
                source_commit=commit,
                include_source_corpus=include_source_corpus,
                source_materialization=materialization,
                omitted_source_files=sorted(
                    omitted_source_files, key=lambda item: item.path
                ),
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
    "OmittedSourceFile",
    "PortableExportComponent",
    "PortableExportReceipt",
    "PortableExportView",
]
