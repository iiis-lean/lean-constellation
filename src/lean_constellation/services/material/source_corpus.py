"""Source corpus manifest, acquisition, extraction, and submit gates."""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.services.external_clients import (
    AcquiredArtifactResult,
    ExternalClientService,
    ExtractedMaterialResult,
)
from lean_constellation.services.foundation import FoundationContext, FoundationService, GateReport, ServiceResult, WriteMode


class SourceCorpusFileView(StrictModel):
    path: str
    size_bytes: int
    readable_text: bool
    line_count: int = 0
    sha256: str | None = None


class SourceCorpusManifestView(StrictModel):
    repo_root: str
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
    metadata: dict[str, str] = Field(default_factory=dict)
    content_hash: str | None = None
    summary: str
    issue_code: str | None = None


class SourceExtractionView(StrictModel):
    ok: bool
    artifact_ref: str
    material_refs: list[str] = Field(default_factory=list)
    primary_material_ref: str | None = None
    preview: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    summary: str
    issue_code: str | None = None


class SourceCorpusPreparedView(StrictModel):
    prepared: bool
    manifest: SourceCorpusManifestView
    preparation_summary: str
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


class SourceCorpusComponent:
    """Manage `.lean_constellation/source` and its manifest."""

    def __init__(self, foundation: FoundationService, external: ExternalClientService) -> None:
        self.foundation = foundation
        self.external = external

    def scan_source_corpus(
        self,
        repo_root: Path,
        *,
        relpath: str = ".lean_constellation/source",
        overview: str | None = None,
        entry_path: str | None = None,
        created_from_mode: str = "scan",
    ) -> ServiceResult[SourceCorpusManifestView]:
        ctx = FoundationContext(repo_root=Path(repo_root))
        root = self.foundation.layout.source_corpus_root(ctx, relpath)
        if not root.exists() or not root.is_dir():
            return self.foundation.fail(
                self.foundation.issue("source_corpus_missing", f"Source corpus directory does not exist: {root}")
            )
        files = [self._file_view(root, item) for item in sorted(root.rglob("*")) if item.is_file()]
        default_entry = entry_path or self._default_entry(root, files)
        manifest = SourceCorpusManifestView(
            repo_root=str(Path(repo_root)),
            relpath=relpath,
            overview=overview,
            entry_path=default_entry,
            created_from_mode=created_from_mode,
            files=files,
            summary=f"Scanned {len(files)} source files.",
        )
        return self.foundation.ok(manifest)

    def acquire_source_material(
        self,
        repo_root: Path,
        *,
        target: str,
        preferred_kind: Literal["arxiv_source", "arxiv_pdf", "web_page", "local_file", "local_dir"] | None = None,
    ) -> ServiceResult[SourceAcquisitionView]:
        root = self._source_root(repo_root)
        root.mkdir(parents=True, exist_ok=True)
        try:
            normalized = self.external.material.normalize_target(target)
        except Exception as exc:  # noqa: BLE001
            return self.foundation.fail(self.foundation.issue("invalid_material_target", str(exc)))

        mismatch = self._preferred_kind_mismatch(preferred_kind, normalized.kind)
        if mismatch is not None:
            return self.foundation.fail(
                self.foundation.issue(
                    "preferred_kind_target_mismatch",
                    f"preferred_kind={preferred_kind} cannot be used with target kind {normalized.kind}.",
                    details={"preferred_kind": str(preferred_kind), "target_kind": normalized.kind},
                )
            )

        if preferred_kind == "arxiv_source" or (preferred_kind is None and normalized.kind == "arxiv"):
            result = self.external.material.fetch_arxiv_source(normalized.value, normalized.version, output_root=root)
        elif preferred_kind == "arxiv_pdf":
            result = self.external.material.fetch_arxiv_pdf(normalized.value, normalized.version, output_root=root)
        elif preferred_kind == "web_page" or (preferred_kind is None and normalized.kind == "web_url"):
            result = self.external.material.fetch_web_page(normalized.value, output_root=root)
        elif preferred_kind == "local_dir" or normalized.kind == "local_dir":
            result = self.external.material.import_local_dir(Path(normalized.value), root)
        else:
            result = self.external.material.import_local_file(source_path=Path(normalized.value), output_root=root)
        view = self._acquisition_view(target, result, root)
        if not result.ok:
            return self.foundation.fail(
                self.foundation.issue(result.issue_code or "source_acquisition_failed", result.summary or "Source acquisition failed")
            )
        return self.foundation.ok(view)

    def extract_source_artifact(
        self,
        repo_root: Path,
        *,
        artifact_ref: str,
        extraction_kind: Literal["pdf_text", "html_main_text", "tex_source", "text_normalize"] | None = None,
    ) -> ServiceResult[SourceExtractionView]:
        root = self._source_root(repo_root)
        try:
            artifact = self._resolve_inside(root, artifact_ref)
        except ValueError as exc:
            return self.foundation.fail(self.foundation.issue("source_artifact_ref_invalid", str(exc)))
        if extraction_kind is None:
            extraction_kind = self._guess_extraction_kind(artifact)
        if extraction_kind == "pdf_text":
            result = self.external.material.extract_pdf_text(pdf_path=artifact, output_root=root)
        elif extraction_kind == "html_main_text":
            result = self.external.material.extract_web_main_text(html_path=artifact, output_root=root)
        elif extraction_kind == "tex_source":
            result = self.external.material.extract_arxiv_tex(source_root_or_archive=artifact, output_root=root)
        else:
            result = self.external.material.normalize_text_material(input_path=artifact, output_root=root)
        view = self._extraction_view(artifact_ref, result, root)
        if not result.ok:
            return self.foundation.fail(
                self.foundation.issue(result.issue_code or "source_extraction_failed", result.summary or "Source extraction failed")
            )
        return self.foundation.ok(view)

    def import_source_material(
        self,
        repo_root: Path,
        *,
        source_path: str,
        as_name: str | None = None,
    ) -> ServiceResult[SourceAcquisitionView]:
        source = Path(source_path).expanduser().resolve(strict=False)
        if not source.exists() or not source.is_file():
            return self.foundation.fail(
                self.foundation.issue("missing_local_file", f"Local source material not found: {source}")
            )
        root = self._source_root(repo_root)
        try:
            dest_name = self.foundation.layout.ensure_safe_key(as_name) if as_name else self._safe_source_filename(source.name)
        except ValueError as exc:
            return self.foundation.fail(self.foundation.issue("unsafe_source_filename", str(exc)))
        dest = root / "original" / dest_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        result = AcquiredArtifactResult(
            ok=True,
            target=self.external.material.normalize_target(str(source)),
            artifact_paths=[str(dest)],
            primary_artifact_path=str(dest),
            metadata={"source_path": str(source)},
            content_hash=self._hash_file(dest),
            summary="Imported local source material.",
        )
        return self.foundation.ok(self._acquisition_view(str(source), result, root))

    def normalize_source_text_material(self, repo_root: Path, *, material_ref: str) -> ServiceResult[SourceExtractionView]:
        root = self._source_root(repo_root)
        try:
            source = self._resolve_inside(root, material_ref)
        except ValueError as exc:
            return self.foundation.fail(self.foundation.issue("source_material_ref_invalid", str(exc)))
        result = self.external.material.normalize_text_material(input_path=source, output_root=root)
        view = self._extraction_view(material_ref, result, root)
        if not result.ok:
            return self.foundation.fail(
                self.foundation.issue(result.issue_code or "text_normalization_failed", result.summary or "Text normalization failed")
            )
        return self.foundation.ok(view)

    def check_source_corpus_draft(
        self,
        repo_root: Path,
        *,
        relpath: str = ".lean_constellation/source",
        entry_path: str | None = None,
    ) -> ServiceResult[GateReport]:
        scan = self.scan_source_corpus(repo_root, relpath=relpath, entry_path=entry_path)
        if not scan.ok or scan.value is None:
            return self.foundation.ok(self.foundation.gate_failed("source_corpus_draft", scan.issues, summary="Source corpus scan failed."))
        manifest = scan.value
        issues = []
        if not manifest.files:
            issues.append(self.foundation.issue("source_corpus_empty", "Source corpus contains no files."))
        readable = [item for item in manifest.files if item.readable_text and item.line_count > 0]
        if not readable:
            issues.append(self.foundation.issue("source_corpus_no_readable_text", "Source corpus has no readable text files."))
        entry = manifest.entry_path or entry_path
        if not entry:
            issues.append(self.foundation.issue("source_corpus_entry_missing", "Source corpus needs an entry file such as README.md."))
        elif entry not in {item.path for item in manifest.files}:
            issues.append(self.foundation.issue("source_corpus_entry_not_found", f"Entry file not found: {entry}"))
        else:
            entry_view = next(item for item in manifest.files if item.path == entry)
            if not entry_view.readable_text or entry_view.line_count <= 0:
                issues.append(
                    self.foundation.issue(
                        "source_corpus_entry_not_readable",
                        f"Entry file must be readable non-empty UTF-8 text: {entry}",
                    )
                )
        if issues:
            return self.foundation.ok(
                self.foundation.gate_failed("source_corpus_draft", issues, summary=f"{len(issues)} source corpus checks failed.")
            )
        return self.foundation.ok(
            self.foundation.gate_passed("source_corpus_draft", summary="Source corpus draft is usable.")
        )

    def submit_source_corpus_prepared(
        self,
        repo_root: Path,
        *,
        entry_path: str,
        overview: str,
        preparation_summary: str,
        relpath: str = ".lean_constellation/source",
    ) -> ServiceResult[SourceCorpusPreparedView]:
        if not overview.strip() or not preparation_summary.strip():
            return self.foundation.fail(self.foundation.issue("missing_summary", "overview and preparation_summary are required."))
        gate = self.check_source_corpus_draft(repo_root, relpath=relpath, entry_path=entry_path)
        if not gate.ok or gate.value is None:
            return self.foundation.fail(gate.issues)
        if not gate.value.passed:
            return self.foundation.fail(gate.value.issues)
        manifest = self.scan_source_corpus(
            repo_root,
            relpath=relpath,
            overview=overview.strip(),
            entry_path=entry_path,
            created_from_mode="prepared",
        )
        if not manifest.ok or manifest.value is None:
            return self.foundation.fail(manifest.issues)
        write = self.foundation.store.write_json_atomic(self._manifest_path(repo_root), manifest.value)
        if not write.ok:
            return self.foundation.fail(write.issues)
        return self.foundation.ok(
            SourceCorpusPreparedView(
                prepared=True,
                manifest=manifest.value,
                preparation_summary=preparation_summary.strip(),
                summary="Source corpus prepared and manifest written.",
            )
        )

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
            return self.foundation.fail(self.foundation.issue("missing_blocked_reason", "Blocked submit requires a reason."))
        return self.foundation.ok(
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
            return self.foundation.store.read_json(path, SourceCorpusManifestView)
        return self.scan_source_corpus(repo_root, relpath=".lean_constellation/source")

    def validate_source_ref(
        self,
        repo_root: Path,
        *,
        path: str,
        start_line: int,
        end_line: int,
    ) -> ServiceResult[SourceRefValidationView]:
        root = self._source_root(repo_root)
        try:
            target = self._resolve_inside(root, path)
        except ValueError as exc:
            return self.foundation.ok(
                SourceRefValidationView(valid=False, path=path, start_line=start_line, end_line=end_line, summary=str(exc), issue_code="source_ref_outside_root")
            )
        if not target.exists() or not target.is_file():
            return self.foundation.ok(
                SourceRefValidationView(valid=False, path=path, start_line=start_line, end_line=end_line, summary="Source file not found.", issue_code="source_ref_file_missing")
            )
        line_count = self._line_count(target)
        valid = 1 <= start_line <= end_line <= line_count
        return self.foundation.ok(
            SourceRefValidationView(
                valid=valid,
                path=target.relative_to(root).as_posix(),
                start_line=start_line,
                end_line=end_line,
                line_count=line_count,
                summary="Source ref is valid." if valid else "Source ref line range is invalid.",
                issue_code=None if valid else "source_ref_range_invalid",
            )
        )

    def check_target_in_source_corpus(
        self,
        repo_root: Path,
        *,
        canonical_locator: str,
    ) -> ServiceResult[SourceCorpusDuplicateView]:
        manifest = self.get_source_corpus_manifest(repo_root)
        if not manifest.ok or manifest.value is None:
            return self.foundation.fail(manifest.issues)
        matches = [item.path for item in manifest.value.files if canonical_locator in item.path or canonical_locator == item.sha256]
        return self.foundation.ok(
            SourceCorpusDuplicateView(
                duplicate=bool(matches),
                canonical_locator=canonical_locator,
                matching_paths=matches,
                summary=("Source corpus already contains target." if matches else "No source corpus duplicate found."),
            )
        )

    def _source_root(self, repo_root: Path) -> Path:
        return self.foundation.layout.source_corpus_root(FoundationContext(repo_root=Path(repo_root)))

    def _manifest_path(self, repo_root: Path) -> Path:
        return self.foundation.layout.constellation_root(FoundationContext(repo_root=Path(repo_root))) / "source_corpus" / "manifest.json"

    def _file_view(self, root: Path, path: Path) -> SourceCorpusFileView:
        readable, line_count = self._readable_line_count(path)
        return SourceCorpusFileView(
            path=path.relative_to(root).as_posix(),
            size_bytes=path.stat().st_size,
            readable_text=readable,
            line_count=line_count,
            sha256=self._hash_file(path),
        )

    @staticmethod
    def _readable_line_count(path: Path) -> tuple[bool, int]:
        try:
            data = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return False, 0
        except OSError:
            return False, 0
        if "\x00" in data:
            return False, 0
        return True, len(data.splitlines())

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

    def _resolve_inside(self, root: Path, ref: str) -> Path:
        relative = self.foundation.layout.ensure_relative_path(ref)
        path = root / relative
        self.foundation.layout.assert_within(root, path)
        return path

    @staticmethod
    def _guess_extraction_kind(path: Path) -> Literal["pdf_text", "html_main_text", "tex_source", "text_normalize"]:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return "pdf_text"
        if suffix in {".html", ".htm"}:
            return "html_main_text"
        if suffix in {".tex", ".gz", ".zip", ".tar"}:
            return "tex_source"
        return "text_normalize"

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
            return self.foundation.layout.ensure_safe_key(filename)
        except ValueError:
            sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename.strip())
            sanitized = sanitized.strip("._-")
            if not sanitized:
                sanitized = f"source_{hashlib.sha256(filename.encode('utf-8')).hexdigest()[:12]}"
            if not sanitized[0].isalnum():
                sanitized = f"source_{sanitized}"
            return self.foundation.layout.ensure_safe_key(sanitized)

    def _acquisition_view(self, target: str, result: AcquiredArtifactResult, root: Path) -> SourceAcquisitionView:
        return SourceAcquisitionView(
            ok=result.ok,
            target=target,
            artifact_refs=[self._rel_or_abs(root, Path(path)) for path in result.artifact_paths],
            primary_artifact_ref=self._rel_or_abs(root, Path(result.primary_artifact_path)) if result.primary_artifact_path else None,
            metadata=result.metadata,
            content_hash=result.content_hash,
            summary=result.summary or "",
            issue_code=result.issue_code,
        )

    def _extraction_view(self, artifact_ref: str, result: ExtractedMaterialResult, root: Path) -> SourceExtractionView:
        return SourceExtractionView(
            ok=result.ok,
            artifact_ref=artifact_ref,
            material_refs=[self._rel_or_abs(root, Path(path)) for path in result.extracted_paths],
            primary_material_ref=self._rel_or_abs(root, Path(result.primary_text_path)) if result.primary_text_path else None,
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
