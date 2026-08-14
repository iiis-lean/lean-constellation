"""Source corpus manifest, acquisition, extraction, and submit gates."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.refs import SourceRef
from lean_constellation.services.external_clients import (
    AcquiredArtifactResult,
    ExtractedMaterialResult,
)
from lean_constellation.services.foundation import FoundationContext, GateReport, ServiceIssue, ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class SourceCorpusFileView(StrictModel):
    path: str
    size_bytes: int
    readable_text: bool
    line_count: int = 0
    sha256: str | None = None


class SourceCorpusManifestView(StrictModel):
    schema_version: Literal[2] = 2
    relpath: str = ".lean_constellation/source"
    overview: str | None = None
    entry_path: str | None = None
    created_from_mode: str = "scan"
    generated_at: str = Field(default_factory=utc_now_iso)
    files: list[SourceCorpusFileView] = Field(default_factory=list)
    summary: str

class SourceAcquisitionView(StrictModel):
    ok: bool
    target: str
    artifact_refs: list[str] = Field(default_factory=list)
    primary_artifact_ref: str | None = None
    acquisition_kind: str | None = None
    mime_type: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    content_hash: str | None = None
    summary: str
    issue_code: str | None = None


class SourceExtractionView(StrictModel):
    ok: bool
    artifact_ref: str
    material_refs: list[str] = Field(default_factory=list)
    primary_material_ref: str | None = None
    resolved_artifact_kind: Literal[
        "pdf",
        "html",
        "tex_source_archive",
        "plain_text",
        "directory",
        "unknown_binary",
    ]
    extraction_kind: Literal["pdf_text", "html_main_text", "tex_source", "text_normalize"]
    preview: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    summary: str
    issue_code: str | None = None


class SourceCorpusPreparedView(StrictModel):
    prepared: bool
    manifest: SourceCorpusManifestView
    preparation_summary: str
    summary: str


class SourceCorpusImportView(StrictModel):
    prepared: SourceCorpusPreparedView
    manifest_digest: str
    replaced_existing: bool
    summary: str


class SourceCorpusBlockedSubmitView(StrictModel):
    blocked: bool
    reason: str
    attempted_targets: list[str] = Field(default_factory=list)
    missing_materials: list[str] = Field(default_factory=list)
    suggested_next_action: str | None = None
    summary: str


class SourceRefValidationView(StrictModel):
    valid: bool
    path: str
    start_line: int
    end_line: int
    line_count: int = 0
    summary: str
    issue_code: str | None = None


class SourceCorpusDuplicateView(StrictModel):
    duplicate: bool
    canonical_locator: str
    matching_paths: list[str] = Field(default_factory=list)
    summary: str


class SourcePdfPagePreviewView(StrictModel):
    source_path: str
    source_sha256: str
    page_number: int
    page_count: int
    dpi: int
    image_path: str
    image_sha256: str
    width: int
    height: int
    cache_reused: bool = False
    summary: str


class SourceCorpusComponent:
    """Manage the configured source corpus root and its manifest."""

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def initialize_source_corpus_draft(self, repo_root: Path) -> ServiceResult[str]:
        draft_root = self._draft_root(repo_root)
        work_root = self._work_root(repo_root)
        if self._source_root(repo_root).exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_corpus_already_exists",
                    "Source prepare mode requires an empty canonical SourceCorpus destination.",
                    object_ref=str(self._source_root(repo_root)),
                )
            )
        draft_root.mkdir(parents=True, exist_ok=True)
        work_root.mkdir(parents=True, exist_ok=True)
        return self.runtime.foundation.ok(str(draft_root))

    def scan_source_corpus(
        self,
        repo_root: Path,
        *,
        relpath: str = ".lean_constellation/source",
        overview: str | None = None,
        entry_path: str | None = None,
        created_from_mode: str = "scan",
    ) -> ServiceResult[SourceCorpusManifestView]:
        relpath = self._effective_relpath(repo_root, relpath)
        ctx = FoundationContext(repo_root=Path(repo_root))
        root = self.runtime.foundation.layout.source_corpus_root(ctx, relpath)
        if not root.exists() or not root.is_dir():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("source_corpus_missing", f"Source corpus directory does not exist: {root}")
            )
        tree_issues = self._source_tree_issues(root)
        if tree_issues:
            return self.runtime.foundation.fail(tree_issues)
        files = [self._file_view(root, item) for item in sorted(root.rglob("*")) if item.is_file()]
        default_entry = entry_path or self._default_entry(root, files)
        manifest = SourceCorpusManifestView(
            relpath=relpath,
            overview=overview,
            entry_path=default_entry,
            created_from_mode=created_from_mode,
            files=files,
            summary=f"Scanned {len(files)} source files.",
        )
        return self.runtime.foundation.ok(manifest)

    def scan_source_corpus_draft(
        self,
        repo_root: Path,
        *,
        overview: str | None = None,
        entry_path: str | None = None,
    ) -> ServiceResult[SourceCorpusManifestView]:
        root = self._draft_root(repo_root)
        if not root.exists() or not root.is_dir():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_corpus_draft_missing",
                    f"Source corpus draft directory does not exist: {root}",
                )
            )
        tree_issues = self._source_tree_issues(root, ignored_top_level={"_work"})
        if tree_issues:
            return self.runtime.foundation.fail(tree_issues)
        files = [
            self._file_view(root, item)
            for item in sorted(root.rglob("*"))
            if item.is_file() and item.relative_to(root).parts[0] != "_work"
        ]
        manifest = SourceCorpusManifestView(
            relpath=self._source_relpath(repo_root),
            overview=overview,
            entry_path=entry_path or self._default_entry(root, files),
            created_from_mode="prepared",
            files=files,
            summary=f"Scanned {len(files)} source draft candidate files; _work was excluded.",
        )
        return self.runtime.foundation.ok(manifest)

    def render_source_pdf_page(
        self,
        repo_root: Path,
        *,
        path: str,
        page_number: int,
        dpi: int = 160,
    ) -> ServiceResult[SourcePdfPagePreviewView]:
        repo_root = Path(repo_root).resolve(strict=False)
        requested = Path(path)
        if requested.is_absolute() or ".." in requested.parts:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_pdf_preview_path_escape",
                    "PDF preview path must be a SourceCorpus-relative path without parent traversal.",
                    object_ref=path,
                )
            )
        normalized = requested.as_posix()
        if normalized.startswith("./"):
            normalized = normalized[2:]
        draft_root = self._draft_root(repo_root).resolve(strict=False)
        if not Path(normalized).parts or Path(normalized).parts[0] != "_work":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_pdf_preview_path_outside_work",
                    "PDF preview path must be the draft-relative _work artifact reference returned by acquisition.",
                    object_ref=path,
                )
            )
        source = (draft_root / normalized).resolve(strict=False)
        try:
            source.relative_to(self._work_root(repo_root).resolve(strict=False))
        except ValueError:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_pdf_preview_path_escape",
                    "PDF preview path escapes the current Source draft _work root.",
                    object_ref=path,
                )
            )
        if not source.is_file():
            pdf_magic = b""
        else:
            with source.open("rb") as handle:
                pdf_magic = handle.read(5)
        if pdf_magic != b"%PDF-":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_pdf_type_mismatch",
                    "SourceCorpus page preview requires a real PDF artifact.",
                    object_ref=path,
                )
            )
        if page_number < 1 or dpi < 96 or dpi > 240:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_pdf_page_out_of_range" if page_number < 1 else "source_pdf_dpi_out_of_range",
                    "PDF page_number must be positive and dpi must be between 96 and 240.",
                    object_ref=path,
                )
            )
        try:
            info = subprocess.run(
                ["pdfinfo", str(source)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=20,
            )
        except FileNotFoundError:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("source_pdf_renderer_unavailable", "pdfinfo is unavailable.")
            )
        except subprocess.TimeoutExpired:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("source_pdf_render_failed", "PDF metadata inspection timed out.")
            )
        match = re.search(r"^Pages:\s+(\d+)\s*$", info.stdout, re.MULTILINE)
        if info.returncode != 0 or match is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_pdf_render_failed",
                    "Could not inspect PDF page count.",
                    object_ref=path,
                    details={"stderr": info.stderr[:500]},
                )
            )
        page_count = int(match.group(1))
        if page_number > page_count:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_pdf_page_out_of_range",
                    "Requested PDF page does not exist.",
                    object_ref=path,
                    current=str(page_number),
                    expected=f"1-{page_count}",
                )
            )
        source_sha = self._hash_file(source)
        cache_root = self._work_root(repo_root).resolve(strict=False) / "previews" / source_sha
        output = cache_root / f"page_{page_number:04d}_{dpi}dpi.png"
        reused = output.is_file()
        if not reused:
            cache_root.mkdir(parents=True, exist_ok=True)
            temp_output = cache_root / f".{output.stem}.{uuid.uuid4().hex}.png"
            try:
                rendered = subprocess.run(
                    [
                        "pdftoppm", "-f", str(page_number), "-l", str(page_number),
                        "-singlefile", "-png", "-r", str(dpi), str(source), str(temp_output.with_suffix("")),
                    ],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=30,
                )
            except FileNotFoundError:
                temp_output.unlink(missing_ok=True)
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("source_pdf_renderer_unavailable", "pdftoppm is unavailable.")
                )
            except subprocess.TimeoutExpired:
                temp_output.unlink(missing_ok=True)
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("source_pdf_render_failed", "PDF page rendering timed out.")
                )
            if rendered.returncode != 0 or not temp_output.is_file():
                temp_output.unlink(missing_ok=True)
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "source_pdf_render_failed",
                        "PDF page renderer did not produce a PNG.",
                        object_ref=path,
                        details={"stderr": rendered.stderr[:500]},
                    )
                )
            os.replace(temp_output, output)
        image = output.read_bytes()
        if len(image) < 24 or image[:8] != b"\x89PNG\r\n\x1a\n":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("source_pdf_render_failed", "Rendered page is not a valid PNG.")
            )
        width, height = struct.unpack(">II", image[16:24])
        return self.runtime.foundation.ok(
            SourcePdfPagePreviewView(
                source_path=normalized,
                source_sha256=source_sha,
                page_number=page_number,
                page_count=page_count,
                dpi=dpi,
                image_path=str(output),
                image_sha256=hashlib.sha256(image).hexdigest(),
                width=width,
                height=height,
                cache_reused=reused,
                summary=(
                    f"Rendered Source draft PDF page {page_number}/{page_count}. The exact image_path is authorized "
                    "for image inspection; do not traverse its parent cache directory."
                ),
            )
        )

    def import_local_source_corpus(
        self,
        repo_root: Path,
        *,
        source_dir: Path,
        entry_path: str,
        overview: str,
        preparation_summary: str,
        replace_existing: bool = False,
        expected_manifest_digest: str | None = None,
    ) -> ServiceResult[SourceCorpusImportView]:
        """Gate and atomically promote a trusted local directory into SourceCorpus truth."""

        repo_root = Path(repo_root).resolve(strict=False)
        requested_source_dir = Path(source_dir).expanduser()
        if requested_source_dir.is_symlink():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_corpus_import_symlink_unsupported",
                    "Source corpus local-dir import rejects symbolic links.",
                    object_ref=str(requested_source_dir),
                )
            )
        source_dir = requested_source_dir.resolve(strict=False)
        if not source_dir.exists() or not source_dir.is_dir():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_corpus_import_directory_missing",
                    f"Source corpus import directory does not exist: {source_dir}",
                    object_ref=str(source_dir),
                )
            )
        if source_dir.is_symlink() or any(path.is_symlink() for path in source_dir.rglob("*")):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_corpus_import_symlink_unsupported",
                    "Source corpus local-dir import rejects symbolic links.",
                    object_ref=str(source_dir),
                )
            )
        canonical_relpath = self._source_relpath(repo_root)
        canonical_root = self.runtime.foundation.layout.source_corpus_root(
            FoundationContext(repo_root=repo_root),
            canonical_relpath,
        )
        try:
            source_dir.relative_to(canonical_root)
        except ValueError:
            pass
        else:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_corpus_import_source_inside_destination",
                    "Source corpus import directory cannot be inside the canonical corpus.",
                    object_ref=str(source_dir),
                )
            )
        try:
            canonical_root.relative_to(source_dir)
        except ValueError:
            pass
        else:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_corpus_import_destination_inside_source",
                    "Source corpus import directory cannot contain the destination repository.",
                    object_ref=str(source_dir),
                )
            )

        existing = canonical_root.exists()
        previous_manifest_bytes: bytes | None = None
        manifest_path = self._manifest_path(repo_root)
        if manifest_path.exists():
            try:
                previous_manifest_bytes = manifest_path.read_bytes()
            except OSError as exc:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "source_corpus_manifest_read_failed",
                        str(exc),
                        object_ref=str(manifest_path),
                    )
                )
        if existing:
            if not replace_existing:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "source_corpus_already_exists",
                        "Canonical SourceCorpus already exists; replacement must be explicit.",
                        object_ref=str(canonical_root),
                    )
                )
            if expected_manifest_digest is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "source_corpus_expected_manifest_digest_required",
                        "Replacing SourceCorpus requires the current canonical manifest digest.",
                        object_ref=str(canonical_root),
                    )
                )
            current_manifest = self.scan_source_corpus(
                repo_root,
                relpath=canonical_relpath,
            )
            if not current_manifest.ok or current_manifest.value is None:
                return self.runtime.foundation.fail(current_manifest.issues)
            current_digest = self.canonical_manifest_digest(current_manifest.value)
            if current_digest != expected_manifest_digest:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "source_corpus_manifest_digest_mismatch",
                        "Canonical SourceCorpus changed before replacement.",
                        current=current_digest,
                        expected=expected_manifest_digest,
                    )
                )
        elif expected_manifest_digest is not None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_corpus_manifest_digest_unexpected",
                    "A manifest digest cannot be supplied when no canonical SourceCorpus exists.",
                    expected=self.missing_manifest_digest(),
                    current=expected_manifest_digest,
                )
            )

        staging_parent = (
            self.runtime.foundation.layout.constellation_root(FoundationContext(repo_root=repo_root))
            / ".source_corpus_staging"
        )
        allocated = self.runtime.foundation.store.allocate_uuid(
            lambda candidate: (staging_parent / candidate).exists(),
            prefix="source_import",
        )
        if not allocated.ok or allocated.value is None:
            return self.runtime.foundation.fail(allocated.issues)
        transaction_root = staging_parent / allocated.value
        staged_root = transaction_root / "corpus"
        backup_root = transaction_root / "previous"
        manifest_recovery_path = transaction_root / "previous_manifest.json"
        staged_relpath = staged_root.relative_to(repo_root).as_posix()
        promoted = False
        backed_up = False
        preserve_recovery_artifacts = False
        try:
            staging_parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_dir, staged_root, copy_function=shutil.copy2)
            if previous_manifest_bytes is not None:
                manifest_recovery_path.write_bytes(previous_manifest_bytes)
            checked = self.check_source_corpus_prepared(
                repo_root,
                entry_path=entry_path,
                overview=overview,
                preparation_summary=preparation_summary,
                relpath=staged_relpath,
            )
            if not checked.ok or checked.value is None:
                return self.runtime.foundation.fail(checked.issues)
            canonical_root.parent.mkdir(parents=True, exist_ok=True)
            if existing:
                os.replace(canonical_root, backup_root)
                backed_up = True
            os.replace(staged_root, canonical_root)
            promoted = True
            canonical_manifest = self.scan_source_corpus(
                repo_root,
                relpath=canonical_relpath,
                overview=overview.strip(),
                entry_path=entry_path,
                created_from_mode="operator_local_dir",
            )
            if not canonical_manifest.ok or canonical_manifest.value is None:
                raise OSError("failed to scan promoted SourceCorpus")
            written = self.runtime.foundation.store.write_json_atomic(
                manifest_path,
                canonical_manifest.value,
            )
            if not written.ok:
                raise OSError("; ".join(issue.message for issue in written.issues))
            prepared = SourceCorpusPreparedView(
                prepared=True,
                manifest=canonical_manifest.value,
                preparation_summary=preparation_summary.strip(),
                summary="Source corpus prepared and atomically promoted.",
            )
            return self.runtime.foundation.ok(
                SourceCorpusImportView(
                    prepared=prepared,
                    manifest_digest=self.canonical_manifest_digest(canonical_manifest.value),
                    replaced_existing=existing,
                    summary="Imported local directory as canonical SourceCorpus.",
                )
            )
        except Exception as exc:  # noqa: BLE001 - rollback and normalize transaction failures.
            rollback_issues = []
            if promoted:
                try:
                    shutil.rmtree(canonical_root)
                except OSError as rollback_exc:
                    rollback_issues.append(
                        self.runtime.foundation.issue(
                            "source_corpus_import_rollback_canonical_remove_failed",
                            f"Failed to remove the promoted SourceCorpus during rollback: {rollback_exc}",
                            object_ref=str(canonical_root),
                        )
                    )
            if backed_up and backup_root.exists():
                if canonical_root.exists():
                    rollback_issues.append(
                        self.runtime.foundation.issue(
                            "source_corpus_import_rollback_destination_busy",
                            "The promoted SourceCorpus still occupies the canonical destination.",
                            object_ref=str(canonical_root),
                        )
                    )
                else:
                    try:
                        os.replace(backup_root, canonical_root)
                    except OSError as rollback_exc:
                        rollback_issues.append(
                            self.runtime.foundation.issue(
                                "source_corpus_import_rollback_backup_restore_failed",
                                f"Failed to restore the previous SourceCorpus backup: {rollback_exc}",
                                object_ref=str(backup_root),
                                suggested_action=(
                                    "Recover the previous corpus from the preserved transaction directory."
                                ),
                            )
                        )
            try:
                if previous_manifest_bytes is None:
                    manifest_path.unlink(missing_ok=True)
                else:
                    manifest_path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(manifest_recovery_path, manifest_path)
            except OSError as rollback_exc:
                rollback_issues.append(
                    self.runtime.foundation.issue(
                        "source_corpus_import_rollback_manifest_restore_failed",
                        f"Failed to restore the previous SourceCorpus manifest: {rollback_exc}",
                        object_ref=str(manifest_recovery_path),
                        suggested_action=(
                            "Recover the manifest from the preserved transaction directory."
                        ),
                    )
                )
            primary_issue = self.runtime.foundation.issue(
                "source_corpus_import_failed",
                f"Failed to import SourceCorpus atomically: {exc}",
                object_ref=str(source_dir),
            )
            if rollback_issues:
                preserve_recovery_artifacts = True
                return self.runtime.foundation.fail([primary_issue, *rollback_issues])
            return self.runtime.foundation.fail(primary_issue)
        finally:
            if not preserve_recovery_artifacts:
                shutil.rmtree(transaction_root, ignore_errors=True)
                try:
                    staging_parent.rmdir()
                except OSError:
                    pass

    def acquire_source_material(
        self,
        repo_root: Path,
        *,
        target: str,
        preferred_kind: Literal["arxiv_source", "arxiv_pdf", "web_page", "local_file", "local_dir"] | None = None,
    ) -> ServiceResult[SourceAcquisitionView]:
        root = self._work_root(repo_root)
        root.mkdir(parents=True, exist_ok=True)
        try:
            normalized = self.runtime.external.material.normalize_target(target)
        except Exception as exc:  # noqa: BLE001
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_material_target", str(exc)))

        mismatch = self._preferred_kind_mismatch(preferred_kind, normalized.kind)
        if mismatch is not None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "preferred_kind_target_mismatch",
                    f"preferred_kind={preferred_kind} cannot be used with target kind {normalized.kind}.",
                    details={"preferred_kind": str(preferred_kind), "target_kind": normalized.kind},
                )
            )

        if preferred_kind == "arxiv_source" or (preferred_kind is None and normalized.kind == "arxiv"):
            result = self.runtime.external.material.fetch_arxiv_source(normalized.value, normalized.version, output_root=root)
        elif preferred_kind == "arxiv_pdf":
            result = self.runtime.external.material.fetch_arxiv_pdf(normalized.value, normalized.version, output_root=root)
        elif preferred_kind == "web_page" or (preferred_kind is None and normalized.kind == "web_url"):
            result = self.runtime.external.material.fetch_web_page(normalized.value, output_root=root)
        elif preferred_kind == "local_dir" or normalized.kind == "local_dir":
            result = self.runtime.external.material.import_local_dir(Path(normalized.value), root)
        else:
            result = self.runtime.external.material.import_local_file(source_path=Path(normalized.value), output_root=root)
        view = self._acquisition_view(target, result, self._draft_root(repo_root))
        if not result.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(result.issue_code or "source_acquisition_failed", result.summary or "Source acquisition failed")
            )
        return self.runtime.foundation.ok(view)

    def extract_source_artifact(
        self,
        repo_root: Path,
        *,
        artifact_ref: str,
        extraction_kind: Literal["pdf_text", "html_main_text", "tex_source", "text_normalize"] | None = None,
        acquisition_kind: str | None = None,
        mime_type: str | None = None,
    ) -> ServiceResult[SourceExtractionView]:
        draft_root = self._draft_root(repo_root)
        work_root = self._work_root(repo_root)
        try:
            artifact = self._resolve_inside(draft_root, artifact_ref)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_artifact_ref_invalid", str(exc)))
        resolution = self.runtime.external.material.resolve_artifact_kind(
            artifact,
            acquisition_kind=acquisition_kind,
            mime_type=mime_type,
            requested_extraction_kind=extraction_kind,
        )
        if not resolution.compatible or resolution.extraction_kind is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    resolution.issue_code or "source_artifact_kind_unknown",
                    resolution.summary,
                    object_ref=artifact_ref,
                    details={"evidence": resolution.evidence},
                )
            )
        extraction_kind = resolution.extraction_kind
        if extraction_kind == "pdf_text":
            result = self.runtime.external.material.extract_pdf_text(pdf_path=artifact, output_root=work_root)
        elif extraction_kind == "html_main_text":
            result = self.runtime.external.material.extract_web_main_text(
                html_path=artifact,
                output_root=work_root,
                acquisition_kind=acquisition_kind,
                mime_type=mime_type,
            )
        elif extraction_kind == "tex_source":
            result = self.runtime.external.material.extract_arxiv_tex(source_root_or_archive=artifact, output_root=work_root)
        else:
            result = self.runtime.external.material.normalize_text_material(input_path=artifact, output_root=work_root)
        view = self._extraction_view(
            artifact_ref,
            result,
            draft_root,
            resolved_artifact_kind=resolution.kind,
            extraction_kind=extraction_kind,
        )
        if not result.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(result.issue_code or "source_extraction_failed", result.summary or "Source extraction failed")
            )
        return self.runtime.foundation.ok(view)

    def import_source_material(
        self,
        repo_root: Path,
        *,
        source_path: str,
        as_name: str | None = None,
    ) -> ServiceResult[SourceAcquisitionView]:
        source = Path(source_path).expanduser().resolve(strict=False)
        if not source.exists() or not source.is_file():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("missing_local_file", f"Local source material not found: {source}")
            )
        root = self._work_root(repo_root)
        try:
            dest_name = self.runtime.foundation.layout.ensure_safe_key(as_name) if as_name else self._safe_source_filename(source.name)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("unsafe_source_filename", str(exc)))
        dest = root / "original" / dest_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        result = AcquiredArtifactResult(
            ok=True,
            target=self.runtime.external.material.normalize_target(str(source)),
            artifact_paths=[str(dest)],
            primary_artifact_path=str(dest),
            metadata={"source_path": str(source)},
            content_hash=self._hash_file(dest),
            summary="Imported local source material.",
        )
        return self.runtime.foundation.ok(self._acquisition_view(str(source), result, self._draft_root(repo_root)))

    def normalize_source_text_material(self, repo_root: Path, *, material_ref: str) -> ServiceResult[SourceExtractionView]:
        draft_root = self._draft_root(repo_root)
        work_root = self._work_root(repo_root)
        try:
            source = self._resolve_inside(draft_root, material_ref)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_material_ref_invalid", str(exc)))
        result = self.runtime.external.material.normalize_text_material(input_path=source, output_root=work_root)
        view = self._extraction_view(
            material_ref,
            result,
            draft_root,
            resolved_artifact_kind="plain_text",
            extraction_kind="text_normalize",
        )
        if not result.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(result.issue_code or "text_normalization_failed", result.summary or "Text normalization failed")
            )
        return self.runtime.foundation.ok(view)

    def check_source_corpus_draft(
        self,
        repo_root: Path,
        *,
        relpath: str = ".lean_constellation/source",
        entry_path: str | None = None,
    ) -> ServiceResult[GateReport]:
        is_active_draft = relpath == ".lean_constellation/source_draft"
        if is_active_draft:
            scan = self.scan_source_corpus_draft(repo_root, entry_path=entry_path)
            root = self._draft_root(repo_root)
        else:
            relpath = self._effective_relpath(repo_root, relpath)
            scan = self.scan_source_corpus(repo_root, relpath=relpath, entry_path=entry_path)
            root = self.runtime.foundation.layout.source_corpus_root(FoundationContext(repo_root=Path(repo_root)), relpath)
        if not scan.ok or scan.value is None:
            return self.runtime.foundation.ok(self.runtime.foundation.gate_failed("source_corpus_draft", scan.issues, summary="Source corpus scan failed."))
        manifest = scan.value
        issues: list[ServiceIssue] = []
        if not manifest.files:
            issues.append(self.runtime.foundation.issue("source_corpus_empty", "Source corpus contains no files."))
        readable = [item for item in manifest.files if item.readable_text and item.line_count > 0]
        if not readable:
            issues.append(self.runtime.foundation.issue("source_corpus_no_readable_text", "Source corpus has no readable text files."))
        if is_active_draft and not any(
            item.readable_text and item.line_count > 0 and Path(item.path).name.lower() != "readme.md"
            for item in manifest.files
        ):
            issues.append(
                self.runtime.foundation.issue(
                    "source_corpus_material_missing",
                    "Source draft requires readable source material in addition to README.md.",
                )
            )
        entry = manifest.entry_path or entry_path
        entry_view: SourceCorpusFileView | None = None
        if not entry:
            issues.append(self.runtime.foundation.issue("source_corpus_entry_missing", "Source corpus needs an entry file such as README.md."))
        elif entry not in {item.path for item in manifest.files}:
            issues.append(self.runtime.foundation.issue("source_corpus_entry_not_found", f"Entry file not found: {entry}"))
        else:
            entry_view = next(item for item in manifest.files if item.path == entry)
            if not entry_view.readable_text or entry_view.line_count <= 0:
                issues.append(
                    self.runtime.foundation.issue(
                        "source_corpus_entry_not_readable",
                        f"Entry file must be readable non-empty UTF-8 text: {entry}",
                    )
                )
        readme = root / "README.md"
        if not readme.is_file():
            issues.append(
                self.runtime.foundation.issue(
                    "source_corpus_readme_missing",
                    "SourceCorpus requires a readable root README.md for its stable material identity and organization.",
                    object_ref="README.md",
                )
            )
            readme_text = ""
        else:
            readme_validation = self.runtime.external.material.validate_readable_text(readme)
            if not readme_validation.ok:
                issues.append(
                    self.runtime.foundation.issue(
                        readme_validation.issue_code or "source_corpus_readme_unreadable",
                        readme_validation.summary,
                        object_ref="README.md",
                    )
                )
                readme_text = ""
            else:
                readme_text = readme.read_text(encoding="utf-8")
        if readme_text:
            if is_active_draft:
                if "_work/" in readme_text or str(self._work_root(repo_root)) in readme_text:
                    issues.append(
                        self.runtime.foundation.issue(
                            "source_corpus_work_reference_forbidden",
                            "Canonical SourceCorpus documentation must not require downstream readers to inspect _work.",
                            object_ref="README.md",
                        )
                    )
                if re.search(r"\b(?:nodetree|declgraph|mathlib plan|formalization plan|proof plan)\b", readme_text, flags=re.IGNORECASE):
                    issues.append(
                        self.runtime.foundation.issue(
                            "source_corpus_agent_planning_forbidden",
                            "SourceCorpus README must not contain formalization or repository-planning content.",
                            object_ref="README.md",
                        )
                    )
        roles = self._source_input_roles(repo_root) if is_active_draft else set()
        for item in manifest.files:
            candidate = root / item.path
            resolved = self.runtime.external.material.resolve_artifact_kind(candidate)
            if self._contains_forbidden_control_chars(candidate):
                issues.append(
                    self.runtime.foundation.issue(
                        "source_corpus_control_characters_forbidden",
                        "Readable SourceCorpus material contains forbidden control characters.",
                        object_ref=item.path,
                    )
                )
            if resolved.kind in {"pdf", "html", "tex_source_archive"} or (
                resolved.kind == "unknown_binary" and "asset" not in roles
            ):
                issues.append(
                    self.runtime.foundation.issue(
                        "source_corpus_raw_container_forbidden",
                        "Final Source candidate cannot contain raw textual containers or unknown binary artifacts; keep them under _work.",
                        object_ref=item.path,
                        current=resolved.kind,
                    )
                )
        if is_active_draft:
            role_paths = {
                "formal_target": [item.path for item in manifest.files if Path(item.path).name == "formal_target.lean"],
                "solution": [
                    item.path
                    for item in manifest.files
                    if re.search(r"(?:^|/)(?:solution|expected_answer)(?:[._/-]|$)", item.path, flags=re.IGNORECASE)
                ],
                "proof_reference": [
                    item.path
                    for item in manifest.files
                    if re.search(r"(?:^|/)(?:proof_reference|expected_proof)(?:[._/-]|$)", item.path, flags=re.IGNORECASE)
                ],
                "asset": [item.path for item in manifest.files if Path(item.path).parts[:1] == ("assets",)],
            }
            for role, paths in role_paths.items():
                if paths and role not in roles:
                    issues.append(
                        self.runtime.foundation.issue(
                            f"source_corpus_{role}_unauthorized",
                            f"Candidate paths {paths} require a structured source material input with role={role}.",
                            object_ref=paths[0],
                            details={"paths": paths},
                        )
                    )
        if entry and not entry_path and entry_view is not None and not self._is_canonical_entry(entry):
            issues.append(
                self.runtime.foundation.issue(
                    "source_corpus_entry_not_explanatory",
                    "Source corpus entry must be explicit or use README.md, README.txt, index.md, or main.md.",
                    field="entry_path",
                    current=entry,
                )
            )
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed("source_corpus_draft", issues, summary=f"{len(issues)} source corpus checks failed.")
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed("source_corpus_draft", summary="Source corpus draft is usable.")
        )

    def check_source_corpus_prepared(
        self,
        repo_root: Path,
        *,
        entry_path: str,
        overview: str,
        preparation_summary: str,
        relpath: str = ".lean_constellation/source",
    ) -> ServiceResult[SourceCorpusPreparedView]:
        relpath = self._effective_relpath(repo_root, relpath)
        if not overview.strip() or not preparation_summary.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("missing_summary", "overview and preparation_summary are required."))
        gate = self.check_source_corpus_draft(repo_root, relpath=relpath, entry_path=entry_path)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        if not gate.value.passed:
            return self.runtime.foundation.fail(gate.value.issues)
        manifest = self.scan_source_corpus(
            repo_root,
            relpath=relpath,
            overview=overview.strip(),
            entry_path=entry_path,
            created_from_mode="prepared",
        )
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(manifest.issues)
        return self.runtime.foundation.ok(
            SourceCorpusPreparedView(
                prepared=True,
                manifest=manifest.value,
                preparation_summary=preparation_summary.strip(),
                summary="Source corpus prepared and manifest validated.",
            )
        )

    def finalize_source_corpus_prepared(
        self,
        repo_root: Path,
        *,
        entry_path: str,
        overview: str,
        preparation_summary: str,
        relpath: str = ".lean_constellation/source",
    ) -> ServiceResult[SourceCorpusPreparedView]:
        gate = self.check_source_corpus_draft(
            repo_root,
            relpath=".lean_constellation/source_draft",
            entry_path=entry_path,
        )
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        if not gate.value.passed:
            return self.runtime.foundation.fail(gate.value.issues)
        draft_root = self._draft_root(repo_root)
        projection_root = self._work_root(repo_root) / f"projection_{uuid.uuid4().hex}"
        projection_root.mkdir(parents=True, exist_ok=False)
        try:
            for item in draft_root.iterdir():
                if item.name == "_work":
                    continue
                destination = projection_root / item.name
                if item.is_dir():
                    shutil.copytree(item, destination, copy_function=shutil.copy2)
                else:
                    shutil.copy2(item, destination)
            imported = self.import_local_source_corpus(
                repo_root,
                source_dir=projection_root,
                entry_path=entry_path,
                overview=overview,
                preparation_summary=preparation_summary,
                replace_existing=False,
            )
            if not imported.ok or imported.value is None:
                return self.runtime.foundation.fail(imported.issues)
            return self.runtime.foundation.ok(imported.value.prepared)
        finally:
            shutil.rmtree(projection_root, ignore_errors=True)

    def submit_source_corpus_prepared(
        self,
        repo_root: Path,
        *,
        entry_path: str,
        overview: str,
        preparation_summary: str,
        relpath: str = ".lean_constellation/source",
    ) -> ServiceResult[SourceCorpusPreparedView]:
        prepared = self.check_source_corpus_prepared(
            repo_root,
            entry_path=entry_path,
            overview=overview,
            preparation_summary=preparation_summary,
            relpath=relpath,
        )
        if not prepared.ok or prepared.value is None:
            return self.runtime.foundation.fail(prepared.issues)
        written = self.runtime.foundation.store.write_json_atomic(
            self._manifest_path(repo_root),
            prepared.value.manifest,
        )
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(prepared.value)

    def submit_source_corpus_blocked(
        self,
        repo_root: Path,
        *,
        reason: str,
        attempted_targets: list[str] | None = None,
        missing_materials: list[str] | None = None,
        suggested_next_action: str | None = None,
    ) -> ServiceResult[SourceCorpusBlockedSubmitView]:
        if not reason.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("missing_blocked_reason", "Blocked submit requires a reason."))
        return self.runtime.foundation.ok(
            SourceCorpusBlockedSubmitView(
                blocked=True,
                reason=reason.strip(),
                attempted_targets=attempted_targets or [],
                missing_materials=missing_materials or [],
                suggested_next_action=suggested_next_action,
                summary="Source corpus preparation reported blocked.",
            )
        )

    def get_source_corpus_manifest(self, repo_root: Path) -> ServiceResult[SourceCorpusManifestView]:
        path = self._manifest_path(repo_root)
        if path.exists():
            loaded = self.runtime.foundation.store.read_json(path, SourceCorpusManifestView)
            if not loaded.ok or loaded.value is None:
                return self.runtime.foundation.fail(loaded.issues)
            schema_issues = [
                issue
                for issue in loaded.issues
                if issue.kind in {"schema_version_missing", "schema_version_mismatch"}
            ]
            if schema_issues:
                return self.runtime.foundation.fail([self._as_schema_error(issue) for issue in schema_issues])
            return loaded
        return self.scan_source_corpus(repo_root, relpath=self._source_relpath(repo_root))

    def refresh_source_corpus_manifest(self, repo_root: Path) -> ServiceResult[SourceCorpusManifestView]:
        """Fresh-scan and persist the manifest while preserving descriptive metadata."""

        existing = self.get_source_corpus_manifest(repo_root)
        if not existing.ok or existing.value is None:
            return self.runtime.foundation.fail(existing.issues)
        scanned = self.scan_source_corpus(
            repo_root,
            relpath=existing.value.relpath,
            overview=existing.value.overview,
            entry_path=existing.value.entry_path,
            created_from_mode=existing.value.created_from_mode,
        )
        if not scanned.ok or scanned.value is None:
            return self.runtime.foundation.fail(scanned.issues)
        written = self.runtime.foundation.store.write_json_atomic(self._manifest_path(repo_root), scanned.value)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(scanned.value)

    @staticmethod
    def canonical_manifest_digest(manifest: SourceCorpusManifestView) -> str:
        payload = {
            "relpath": manifest.relpath,
            "files": [
                {
                    "path": item.path,
                    "size_bytes": item.size_bytes,
                    "readable_text": item.readable_text,
                    "line_count": item.line_count,
                    "sha256": item.sha256,
                }
                for item in sorted(manifest.files, key=lambda value: value.path)
            ],
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def missing_manifest_digest() -> str:
        return hashlib.sha256(b"lean-constellation:source-corpus:missing").hexdigest()

    def validate_source_ref(
        self,
        repo_root: Path,
        *,
        path: str,
        start_line: int,
        end_line: int,
    ) -> ServiceResult[SourceRefValidationView]:
        manifest = self.get_source_corpus_manifest(repo_root)
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(manifest.issues)
        manifest_by_path = {item.path: item for item in manifest.value.files}
        return self.runtime.foundation.ok(
            self._validate_source_ref_against_manifest(
                repo_root,
                manifest_by_path=manifest_by_path,
                line_count_by_path={},
                path=path,
                start_line=start_line,
                end_line=end_line,
            )
        )

    def validate_source_refs(
        self,
        repo_root: Path,
        *,
        refs: list[SourceRef],
    ) -> ServiceResult[list[SourceRefValidationView]]:
        """Validate a batch against one manifest read and one canonical policy."""

        manifest = self.get_source_corpus_manifest(repo_root)
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(manifest.issues)
        manifest_by_path = {item.path: item for item in manifest.value.files}
        line_count_by_path: dict[str, int] = {}
        return self.runtime.foundation.ok(
            [
                self._validate_source_ref_against_manifest(
                    repo_root,
                    manifest_by_path=manifest_by_path,
                    line_count_by_path=line_count_by_path,
                    path=ref.path,
                    start_line=ref.start_line,
                    end_line=ref.end_line,
                )
                for ref in refs
            ]
        )

    def _validate_source_ref_against_manifest(
        self,
        repo_root: Path,
        *,
        manifest_by_path: dict[str, SourceCorpusFileView],
        line_count_by_path: dict[str, int],
        path: str,
        start_line: int,
        end_line: int,
    ) -> SourceRefValidationView:
        root = self._source_root(repo_root)
        try:
            target = self._resolve_inside(root, path)
        except ValueError as exc:
            return SourceRefValidationView(
                valid=False,
                path=path,
                start_line=start_line,
                end_line=end_line,
                summary=str(exc),
                issue_code="source_ref_outside_root",
            )
        canonical_path = target.relative_to(root).as_posix()
        if not target.exists() or not target.is_file():
            return SourceRefValidationView(
                valid=False,
                path=path,
                start_line=start_line,
                end_line=end_line,
                summary="Source file not found.",
                issue_code="source_ref_file_missing",
            )
        manifest_entry = manifest_by_path.get(canonical_path)
        if manifest_entry is None:
            return SourceRefValidationView(
                valid=False,
                path=canonical_path,
                start_line=start_line,
                end_line=end_line,
                summary="Source file is not recorded in the current SourceCorpus manifest.",
                issue_code="source_ref_not_in_corpus",
            )
        if not manifest_entry.readable_text:
            return SourceRefValidationView(
                valid=False,
                path=canonical_path,
                start_line=start_line,
                end_line=end_line,
                line_count=manifest_entry.line_count,
                summary="Source file is not readable UTF-8 text in the current SourceCorpus manifest.",
                issue_code="source_ref_not_readable",
            )
        if canonical_path not in line_count_by_path:
            line_count_by_path[canonical_path] = self._line_count(target)
        line_count = line_count_by_path[canonical_path]
        valid = 1 <= start_line <= end_line <= line_count
        return SourceRefValidationView(
            valid=valid,
            path=canonical_path,
            start_line=start_line,
            end_line=end_line,
            line_count=line_count,
            summary="Source ref is valid." if valid else "Source ref line range is invalid.",
            issue_code=None if valid else "source_ref_range_invalid",
        )

    def check_target_in_source_corpus(
        self,
        repo_root: Path,
        *,
        canonical_locator: str,
    ) -> ServiceResult[SourceCorpusDuplicateView]:
        manifest = self.get_source_corpus_manifest(repo_root)
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(manifest.issues)
        matches = [item.path for item in manifest.value.files if canonical_locator in item.path or canonical_locator == item.sha256]
        return self.runtime.foundation.ok(
            SourceCorpusDuplicateView(
                duplicate=bool(matches),
                canonical_locator=canonical_locator,
                matching_paths=matches,
                summary=("Source corpus already contains target." if matches else "No source corpus duplicate found."),
            )
        )

    def _source_root(self, repo_root: Path) -> Path:
        repo_root = Path(repo_root)
        return self.runtime.foundation.layout.source_corpus_root(FoundationContext(repo_root=repo_root), self._source_relpath(repo_root))

    def _draft_root(self, repo_root: Path) -> Path:
        return self.runtime.foundation.layout.source_corpus_draft_root(
            FoundationContext(repo_root=Path(repo_root))
        )

    def _work_root(self, repo_root: Path) -> Path:
        return self.runtime.foundation.layout.source_corpus_work_root(
            FoundationContext(repo_root=Path(repo_root))
        )

    def _source_relpath(self, repo_root: Path) -> str:
        preparation = self.runtime.repo_workspace.preparation.get_preparation_input(Path(repo_root))
        if preparation.ok and preparation.value is not None:
            return preparation.value.input.source_corpus_relpath or ".lean_constellation/source"
        return ".lean_constellation/source"

    def _effective_relpath(self, repo_root: Path, relpath: str) -> str:
        if relpath == ".lean_constellation/source":
            return self._source_relpath(repo_root)
        return relpath

    def _manifest_path(self, repo_root: Path) -> Path:
        return self.runtime.foundation.layout.constellation_root(FoundationContext(repo_root=Path(repo_root))) / "source_corpus" / "manifest.json"

    def _file_view(self, root: Path, path: Path) -> SourceCorpusFileView:
        readable, line_count = self._readable_line_count(path)
        return SourceCorpusFileView(
            path=path.relative_to(root).as_posix(),
            size_bytes=path.stat().st_size,
            readable_text=readable,
            line_count=line_count,
            sha256=self._hash_file(path),
        )

    def _readable_line_count(self, path: Path) -> tuple[bool, int]:
        validation = self.runtime.external.material.validate_readable_text(path)
        return validation.ok, validation.line_count if validation.ok else 0

    @staticmethod
    def _line_count(path: Path) -> int:
        try:
            return len(path.read_text(encoding="utf-8").splitlines())
        except UnicodeDecodeError:
            return 0

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _default_entry(root: Path, files: list[SourceCorpusFileView]) -> str | None:
        for name in ("README.md", "README.txt", "index.md", "main.md"):
            if (root / name).exists():
                return name
        readable = [item for item in files if item.readable_text]
        return readable[0].path if readable else None

    @staticmethod
    def _is_canonical_entry(path: str) -> bool:
        return Path(path).name in {"README.md", "README.txt", "index.md", "main.md"}

    def _source_tree_issues(
        self,
        root: Path,
        *,
        ignored_top_level: set[str] | None = None,
    ) -> list[ServiceIssue]:
        issues: list[ServiceIssue] = []
        ignored_top_level = ignored_top_level or set()
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if relative.parts and relative.parts[0] in ignored_top_level:
                continue
            if path.is_symlink():
                issues.append(
                    self.runtime.foundation.issue(
                        "source_corpus_symlink_forbidden",
                        "SourceCorpus must not contain symbolic links.",
                        object_ref=relative.as_posix(),
                    )
                )
            if self._is_forbidden_source_artifact(relative):
                issues.append(
                    self.runtime.foundation.issue(
                        "source_corpus_artifact_forbidden",
                        "SourceCorpus must not contain runtime, cache, credential, or interpreter artifacts.",
                        object_ref=relative.as_posix(),
                    )
                )
        return issues

    def _source_input_roles(self, repo_root: Path) -> set[str]:
        preparation = self.runtime.repo_workspace.preparation.get_preparation_input(Path(repo_root))
        if not preparation.ok or preparation.value is None:
            return set()
        return {item.role for item in preparation.value.input.source_material_inputs}

    @staticmethod
    def _contains_forbidden_control_chars(path: Path) -> bool:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        return any(
            (ord(char) < 32 and char not in {"\n", "\r", "\t"})
            or 127 <= ord(char) <= 159
            for char in text
        )

    @staticmethod
    def _is_forbidden_source_artifact(relative: Path) -> bool:
        forbidden_parts = {".git", ".agent_runtime", ".codex", ".cache", "__pycache__"}
        if any(part in forbidden_parts for part in relative.parts):
            return True
        name = relative.name.lower()
        return (
            name == "auth.json"
            or name == ".env"
            or name.startswith(".env.")
            or relative.suffix.lower() == ".pyc"
        )

    def _as_schema_error(self, issue: ServiceIssue) -> ServiceIssue:
        return self.runtime.foundation.issue(
            issue.kind,
            issue.message,
            object_ref=issue.object_ref,
            field=issue.field,
            current=issue.current,
            expected=issue.expected,
            suggested_action=issue.suggested_action,
            details=issue.details,
        )

    def _resolve_inside(self, root: Path, ref: str) -> Path:
        relative = self.runtime.foundation.layout.ensure_relative_path(ref)
        path = root / relative
        self.runtime.foundation.layout.assert_within(root, path)
        return path

    @staticmethod
    def _preferred_kind_mismatch(
        preferred_kind: Literal["arxiv_source", "arxiv_pdf", "web_page", "local_file", "local_dir"] | None,
        target_kind: str,
    ) -> str | None:
        if preferred_kind is None:
            return None
        expected = {
            "arxiv_source": "arxiv",
            "arxiv_pdf": "arxiv",
            "web_page": "web_url",
            "local_file": "local_file",
            "local_dir": "local_dir",
        }[preferred_kind]
        return None if expected == target_kind else expected

    def _safe_source_filename(self, filename: str) -> str:
        try:
            return self.runtime.foundation.layout.ensure_safe_key(filename)
        except ValueError:
            sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename.strip())
            sanitized = sanitized.strip("._-")
            if not sanitized:
                sanitized = f"source_{hashlib.sha256(filename.encode('utf-8')).hexdigest()[:12]}"
            if not sanitized[0].isalnum():
                sanitized = f"source_{sanitized}"
            return self.runtime.foundation.layout.ensure_safe_key(sanitized)

    def _acquisition_view(self, target: str, result: AcquiredArtifactResult, root: Path) -> SourceAcquisitionView:
        return SourceAcquisitionView(
            ok=result.ok,
            target=target,
            artifact_refs=[self._rel_or_abs(root, Path(path)) for path in result.artifact_paths],
            primary_artifact_ref=self._rel_or_abs(root, Path(result.primary_artifact_path)) if result.primary_artifact_path else None,
            acquisition_kind=result.artifact_kind,
            mime_type=result.mime_type,
            metadata=result.metadata,
            content_hash=result.content_hash,
            summary=result.summary or "",
            issue_code=result.issue_code,
        )

    def _extraction_view(
        self,
        artifact_ref: str,
        result: ExtractedMaterialResult,
        root: Path,
        *,
        resolved_artifact_kind: Literal[
            "pdf",
            "html",
            "tex_source_archive",
            "plain_text",
            "directory",
            "unknown_binary",
        ],
        extraction_kind: Literal["pdf_text", "html_main_text", "tex_source", "text_normalize"],
    ) -> SourceExtractionView:
        return SourceExtractionView(
            ok=result.ok,
            artifact_ref=artifact_ref,
            material_refs=[self._rel_or_abs(root, Path(path)) for path in result.extracted_paths],
            primary_material_ref=self._rel_or_abs(root, Path(result.primary_text_path)) if result.primary_text_path else None,
            resolved_artifact_kind=resolved_artifact_kind,
            extraction_kind=extraction_kind,
            preview=result.text_preview,
            metadata=result.metadata,
            summary=result.summary or "",
            issue_code=result.issue_code,
        )

    @staticmethod
    def _rel_or_abs(root: Path, path: Path) -> str:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return str(path)
