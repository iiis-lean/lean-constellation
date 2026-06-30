"""Material acquisition and extraction wrapper."""

from __future__ import annotations

import hashlib
import html
import mimetypes
import re
import shutil
import subprocess
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import Field

from lean_constellation.domain.common import StrictModel


Downloader = Callable[[str, Path, dict[str, str], int], dict[str, str] | None]


class MaterialAcquisitionConfig(StrictModel):
    network_timeout_seconds: int = 120
    max_download_bytes: int | None = None
    user_agent: str = "lean-constellation"
    stdout_excerpt_chars: int = 4000
    text_preview_chars: int = 12000
    headers_summary_chars: int = 2000


class MaterialTarget(StrictModel):
    kind: Literal["arxiv", "web_url", "local_file", "local_dir"]
    value: str
    version: str | None = None


class AcquiredArtifactView(StrictModel):
    ok: bool
    phase: Literal["acquired", "extracted"]
    artifact_kind: str
    artifact_paths: list[str] = Field(default_factory=list)
    primary_artifact_path: str | None = None
    output_root: str
    target_kind: str | None = None
    target_value: str | None = None
    source_url: str | None = None
    local_source_path: str | None = None
    mime_type: str | None = None
    content_hash: str | None = None
    text_preview: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    summary: str | None = None
    issue_code: str | None = None


class AcquiredArtifactResult(StrictModel):
    ok: bool
    target: MaterialTarget
    artifact_paths: list[str] = Field(default_factory=list)
    primary_artifact_path: str | None = None
    artifact_kind: str | None = None
    output_root: str | None = None
    source_url: str | None = None
    local_source_path: str | None = None
    mime_type: str | None = None
    artifact_view: AcquiredArtifactView | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    content_hash: str | None = None
    summary: str | None = None
    issue_code: str | None = None


class ExtractedMaterialResult(StrictModel):
    ok: bool
    source_artifact_path: str
    extracted_paths: list[str] = Field(default_factory=list)
    primary_text_path: str | None = None
    text_preview: str | None = None
    material_kind: str | None = None
    output_root: str | None = None
    mime_type: str | None = None
    artifact_view: AcquiredArtifactView | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    summary: str | None = None
    issue_code: str | None = None


class ReadableTextValidationView(StrictModel):
    ok: bool
    path: str
    summary: str
    issue_code: str | None = None
    line_count: int = 0
    non_ascii_ratio: float = 0.0


class MaterialAcquisitionExtractionClient:
    def __init__(
        self,
        config: MaterialAcquisitionConfig | None = None,
        *,
        downloader: Downloader | None = None,
    ) -> None:
        self.config = config or MaterialAcquisitionConfig()
        self._downloader = downloader or self._default_download

    def normalize_target(self, target: str) -> MaterialTarget:
        target = target.strip()
        if not target:
            raise ValueError("material target must be non-empty")
        arxiv = self._parse_arxiv(target)
        if arxiv:
            arxiv_id, version = arxiv
            return MaterialTarget(kind="arxiv", value=arxiv_id, version=version)
        parsed = urlparse(target)
        if parsed.scheme in {"http", "https"}:
            return MaterialTarget(kind="web_url", value=target)
        path = Path(target).expanduser()
        if path.is_dir():
            return MaterialTarget(kind="local_dir", value=str(path))
        return MaterialTarget(kind="local_file", value=str(path))

    def fetch_arxiv_source(self, arxiv_id: str, version: str | None, temp_root: Path | None = None, output_root: Path | None = None) -> AcquiredArtifactResult:
        output_root = self._output_root(temp_root, output_root)
        target = MaterialTarget(kind="arxiv", value=arxiv_id, version=version)
        locator = f"{arxiv_id}{version or ''}"
        url = f"https://arxiv.org/e-print/{locator}"
        path = output_root / "original" / f"{self._safe_name(locator)}-source"
        return self._download_artifact(target, url, path, "Fetched arXiv source")

    def fetch_arxiv_pdf(self, arxiv_id: str, version: str | None, temp_root: Path | None = None, output_root: Path | None = None) -> AcquiredArtifactResult:
        output_root = self._output_root(temp_root, output_root)
        target = MaterialTarget(kind="arxiv", value=arxiv_id, version=version)
        locator = f"{arxiv_id}{version or ''}"
        url = f"https://arxiv.org/pdf/{locator}.pdf"
        path = output_root / "original" / f"{self._safe_name(locator)}.pdf"
        return self._download_artifact(target, url, path, "Fetched arXiv PDF")

    def fetch_web_page(self, url: str, temp_root: Path | None = None, output_root: Path | None = None) -> AcquiredArtifactResult:
        output_root = self._output_root(temp_root, output_root)
        target = MaterialTarget(kind="web_url", value=url)
        parsed = urlparse(url)
        name = self._safe_name(parsed.netloc + parsed.path) or "page"
        path = output_root / "original" / f"{name}.html"
        return self._download_artifact(target, url, path, "Fetched web page")

    def import_local_file(self, path: Path | None = None, temp_root: Path | None = None, *, source_path: Path | None = None, output_root: Path | None = None) -> AcquiredArtifactResult:
        if source_path is None and path is None:
            target = MaterialTarget(kind="local_file", value="")
            return AcquiredArtifactResult(ok=False, target=target, summary="Local file path is required", issue_code="missing_local_file_path")
        source = Path(source_path or path)  # type: ignore[arg-type]
        output_root = self._output_root(temp_root, output_root)
        target = MaterialTarget(kind="local_file", value=str(source))
        if not source.exists() or not source.is_file():
            return AcquiredArtifactResult(ok=False, target=target, summary=f"Local file not found: {source}", issue_code="missing_local_file")
        dest = output_root / "original" / source.name
        safety_issue = self._ensure_output_path(output_root, dest)
        if safety_issue is not None:
            return self._acquired_failed(target, output_root, "artifact_path_escape", safety_issue, artifact_kind=self._artifact_kind_for_path(source))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        return self._acquired_ok(
            target,
            output_root,
            artifact_paths=[dest],
            primary=dest,
            metadata={"source_path": str(source)},
            summary="Imported local file",
            artifact_kind=self._artifact_kind_for_path(source),
            local_source_path=source,
        )

    def import_local_dir(self, source_path: Path, output_root: Path) -> AcquiredArtifactResult:
        source = Path(source_path)
        target = MaterialTarget(kind="local_dir", value=str(source))
        if not source.exists() or not source.is_dir():
            return AcquiredArtifactResult(ok=False, target=target, summary=f"Local directory not found: {source}", issue_code="missing_local_dir")
        output_root = self._output_root(None, output_root)
        dest = output_root / "original" / source.name
        safety_issue = self._ensure_output_path(output_root, dest)
        if safety_issue is not None:
            return self._acquired_failed(target, output_root, "artifact_path_escape", safety_issue, artifact_kind="directory")
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)
        files = [item for item in sorted(dest.rglob("*")) if item.is_file()]
        return self._acquired_ok(
            target,
            output_root,
            artifact_paths=files,
            primary=dest,
            metadata={"source_path": str(source)},
            summary=f"Imported local directory with {len(files)} files",
            artifact_kind="directory",
            local_source_path=source,
        )

    def extract_pdf_text(self, artifact: Path | None = None, temp_root: Path | None = None, *, pdf_path: Path | None = None, output_root: Path | None = None) -> ExtractedMaterialResult:
        pdf = Path(pdf_path or artifact)  # type: ignore[arg-type]
        output_root = self._output_root(temp_root, output_root)
        if not pdf.exists():
            return self._extraction_failed(pdf, "missing_pdf", f"PDF file not found: {pdf}")
        dest = output_root / "normalized" / f"{pdf.stem}.txt"
        safety_issue = self._ensure_output_path(output_root, dest)
        if safety_issue is not None:
            return self._extraction_failed(pdf, "artifact_path_escape", safety_issue, output_root=output_root, material_kind="text")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(["pdftotext", str(pdf), str(dest)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        except OSError as exc:
            return self._extraction_failed(pdf, "pdf_extractor_unavailable", f"pdftotext unavailable: {exc}")
        if completed.returncode != 0:
            return self._extraction_failed(pdf, "pdf_extract_failed", completed.stderr[: self.config.stdout_excerpt_chars])
        return self._extracted_ok(pdf, [dest], dest, "Extracted PDF text")

    def extract_web_main_text(self, artifact: Path | None = None, temp_root: Path | None = None, *, html_path: Path | None = None, output_root: Path | None = None) -> ExtractedMaterialResult:
        html_path = Path(html_path or artifact)  # type: ignore[arg-type]
        output_root = self._output_root(temp_root, output_root)
        if not html_path.exists():
            return self._extraction_failed(html_path, "missing_html", f"HTML file not found: {html_path}")
        text = html_path.read_text(encoding="utf-8", errors="replace")
        clean = self._html_to_text(text)
        dest = output_root / "normalized" / f"{html_path.stem}.md"
        safety_issue = self._ensure_output_path(output_root, dest)
        if safety_issue is not None:
            return self._extraction_failed(html_path, "artifact_path_escape", safety_issue, output_root=output_root, material_kind="markdown")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(clean, encoding="utf-8")
        return self._extracted_ok(html_path, [dest], dest, "Extracted web main text")

    def extract_arxiv_tex(self, artifact: Path | None = None, temp_root: Path | None = None, *, source_root_or_archive: Path | None = None, output_root: Path | None = None) -> ExtractedMaterialResult:
        source = Path(source_root_or_archive or artifact)  # type: ignore[arg-type]
        output_root = self._output_root(temp_root, output_root)
        if not source.exists():
            return self._extraction_failed(source, "missing_arxiv_source", f"arXiv source not found: {source}")
        extracted_root = output_root / "normalized" / f"{source.stem}-tex"
        safety_issue = self._ensure_output_path(output_root, extracted_root)
        if safety_issue is not None:
            return self._extraction_failed(source, "artifact_path_escape", safety_issue, output_root=output_root, material_kind="tex_source")
        if extracted_root.exists():
            shutil.rmtree(extracted_root)
        extracted_root.mkdir(parents=True, exist_ok=True)
        try:
            if source.is_dir():
                shutil.copytree(source, extracted_root, dirs_exist_ok=True)
            elif tarfile.is_tarfile(source):
                with tarfile.open(source) as archive:
                    self._extract_tar_safely(archive, extracted_root)
            elif zipfile.is_zipfile(source):
                with zipfile.ZipFile(source) as archive:
                    self._extract_zip_safely(archive, extracted_root)
            elif source.suffix == ".tex":
                shutil.copy2(source, extracted_root / source.name)
            else:
                shutil.copy2(source, extracted_root / source.name)
        except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError) as exc:
            return self._extraction_failed(source, "arxiv_tex_extract_failed", f"Failed to extract TeX source: {exc}")
        tex_files = sorted(extracted_root.rglob("*.tex"))
        primary = self._choose_main_tex(tex_files)
        extracted = [path for path in sorted(extracted_root.rglob("*")) if path.is_file()]
        return self._extracted_ok(source, extracted, primary, "Extracted arXiv TeX source")

    def normalize_text_material(self, *, input_path: Path, output_root: Path) -> ExtractedMaterialResult:
        input_path = Path(input_path)
        if not input_path.exists():
            return self._extraction_failed(input_path, "missing_text", f"Text file not found: {input_path}")
        text = input_path.read_text(encoding="utf-8", errors="replace")
        output_root = self._output_root(None, output_root)
        dest = output_root / "normalized" / f"{input_path.stem}.txt"
        safety_issue = self._ensure_output_path(output_root, dest)
        if safety_issue is not None:
            return self._extraction_failed(input_path, "artifact_path_escape", safety_issue, output_root=output_root, material_kind="text")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        return self._extracted_ok(input_path, [dest], dest, "Normalized text material")

    def validate_readable_text(self, path: Path) -> ReadableTextValidationView:
        path = Path(path)
        if not path.exists():
            return ReadableTextValidationView(ok=False, path=str(path), summary="Text file is missing", issue_code="missing_text")
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            return ReadableTextValidationView(ok=False, path=str(path), summary="Text file is empty", issue_code="empty_text")
        line_count = len(text.splitlines())
        non_ascii = sum(1 for char in text if ord(char) > 127)
        ratio = non_ascii / max(len(text), 1)
        if "\ufffd" in text:
            return ReadableTextValidationView(ok=False, path=str(path), summary="Text contains replacement characters", issue_code="decode_replacement", line_count=line_count, non_ascii_ratio=ratio)
        return ReadableTextValidationView(ok=True, path=str(path), summary="Text is readable", line_count=line_count, non_ascii_ratio=ratio)

    def _download_artifact(self, target: MaterialTarget, url: str, path: Path, summary: str) -> AcquiredArtifactResult:
        output_root = path.parents[1] if len(path.parents) > 1 else path.parent
        output_root = self._output_root(None, output_root)
        path = output_root / path.relative_to(output_root)
        safety_issue = self._ensure_output_path(output_root, path)
        if safety_issue is not None:
            return self._acquired_failed(target, output_root, "artifact_path_escape", safety_issue, artifact_kind=self._artifact_kind_for_path(path), source_url=url)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            response_headers = self._downloader(url, path, {"User-Agent": self.config.user_agent}, self.config.network_timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - external boundary.
            return self._acquired_failed(target, output_root, "download_failed", f"Download failed: {exc}", artifact_kind=self._artifact_kind_for_path(path), source_url=url, metadata={"url": url})
        metadata = {"url": url}
        if response_headers:
            metadata["headers_summary"] = self._headers_summary(response_headers)
        return self._acquired_ok(
            target,
            output_root,
            artifact_paths=[path],
            primary=path,
            metadata=metadata,
            summary=summary,
            artifact_kind=self._artifact_kind_for_path(path),
            source_url=url,
            mime_type=self._mime_type_for_path(path, response_headers),
        )

    def _default_download(self, url: str, path: Path, headers: dict[str, str], timeout_seconds: int) -> dict[str, str] | None:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=timeout_seconds) as response:
            total = 0
            with path.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 64)
                    if not chunk:
                        break
                    total += len(chunk)
                    if self.config.max_download_bytes is not None and total > self.config.max_download_bytes:
                        raise ValueError("download exceeds max_download_bytes")
                    handle.write(chunk)
            return {str(key): str(value) for key, value in response.headers.items()}

    def _output_root(self, temp_root: Path | None, output_root: Path | None) -> Path:
        root = Path(output_root or temp_root).expanduser().resolve(strict=False)  # type: ignore[arg-type]
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _parse_arxiv(self, value: str) -> tuple[str, str | None] | None:
        match = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(v[0-9]+)?", value, re.IGNORECASE)
        if match:
            return match.group(1), match.group(2)
        match = re.fullmatch(r"([0-9]{4}\.[0-9]{4,5})(v[0-9]+)?", value)
        if match:
            return match.group(1), match.group(2)
        return None

    def _safe_name(self, value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "artifact"

    def _hash_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _headers_summary(self, headers: dict[str, str]) -> str:
        text = "\n".join(f"{key}: {value}" for key, value in sorted(headers.items()))
        limit = self.config.headers_summary_chars
        return text if len(text) <= limit else text[:limit] + "\n...[truncated]"

    def _html_to_text(self, value: str) -> str:
        value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
        value = re.sub(r"(?i)<br\s*/?>", "\n", value)
        value = re.sub(r"(?i)</p>", "\n\n", value)
        value = re.sub(r"(?s)<[^>]+>", " ", value)
        value = html.unescape(value)
        lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
        return "\n".join(line for line in lines if line).strip() + "\n"

    def _choose_main_tex(self, tex_files: list[Path]) -> Path | None:
        if not tex_files:
            return None
        with_document = []
        for path in tex_files:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "\\begin{document}" in text:
                with_document.append(path)
        candidates = with_document or tex_files
        return max(candidates, key=lambda item: item.stat().st_size)

    def _safe_archive_target(self, root: Path, member_name: str) -> Path:
        member_path = Path(member_name)
        if member_path.is_absolute() or any(part in {"", ".."} for part in member_path.parts):
            raise ValueError(f"Unsafe archive member path: {member_name}")
        root_resolved = root.resolve()
        target = (root / member_path).resolve()
        if not target.is_relative_to(root_resolved):
            raise ValueError(f"Archive member escapes output directory: {member_name}")
        return target

    def _extract_tar_safely(self, archive: tarfile.TarFile, output_root: Path) -> None:
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                raise ValueError(f"Archive link member is not allowed: {member.name}")
            target = self._safe_archive_target(output_root, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError(f"Unsupported tar member type: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"Cannot read tar member: {member.name}")
            with source, target.open("wb") as handle:
                shutil.copyfileobj(source, handle)

    def _extract_zip_safely(self, archive: zipfile.ZipFile, output_root: Path) -> None:
        for info in archive.infolist():
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise ValueError(f"Archive symlink member is not allowed: {info.filename}")
            target = self._safe_archive_target(output_root, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as handle:
                shutil.copyfileobj(source, handle)

    def _extracted_ok(self, source: Path, extracted: list[Path], primary: Path | None, summary: str) -> ExtractedMaterialResult:
        output_root = self._guess_output_root_for_extracted(extracted, primary)
        if output_root is not None:
            safety_issue = self._paths_inside(output_root, [*extracted, *([primary] if primary else [])])
            if safety_issue is not None:
                return self._extraction_failed(source, "artifact_path_escape", safety_issue, output_root=output_root)
        preview = None
        if primary and primary.exists() and primary.is_file():
            preview = primary.read_text(encoding="utf-8", errors="replace")[: self.config.text_preview_chars]
        material_kind = self._material_kind_for_primary(primary)
        mime_type = self._mime_type_for_path(primary) if primary else None
        artifact_view = AcquiredArtifactView(
            ok=True,
            phase="extracted",
            artifact_kind=material_kind,
            artifact_paths=[str(path) for path in extracted],
            primary_artifact_path=str(primary) if primary else None,
            output_root=str(output_root) if output_root else "",
            mime_type=mime_type,
            text_preview=preview,
            summary=summary,
        )
        return ExtractedMaterialResult(
            ok=True,
            source_artifact_path=str(source),
            extracted_paths=[str(path) for path in extracted],
            primary_text_path=str(primary) if primary else None,
            text_preview=preview,
            material_kind=material_kind,
            output_root=str(output_root) if output_root else None,
            mime_type=mime_type,
            artifact_view=artifact_view,
            summary=summary,
        )

    def _extraction_failed(
        self,
        source: Path,
        issue_code: str,
        summary: str,
        *,
        output_root: Path | None = None,
        material_kind: str | None = None,
    ) -> ExtractedMaterialResult:
        artifact_view = None
        if output_root is not None:
            artifact_view = AcquiredArtifactView(
                ok=False,
                phase="extracted",
                artifact_kind=material_kind or "unknown",
                output_root=str(output_root),
                summary=summary,
                issue_code=issue_code,
            )
        return ExtractedMaterialResult(
            ok=False,
            source_artifact_path=str(source),
            material_kind=material_kind,
            output_root=str(output_root) if output_root else None,
            artifact_view=artifact_view,
            summary=summary,
            issue_code=issue_code,
        )

    def _acquired_ok(
        self,
        target: MaterialTarget,
        output_root: Path,
        *,
        artifact_paths: list[Path],
        primary: Path,
        metadata: dict[str, str],
        summary: str,
        artifact_kind: str,
        source_url: str | None = None,
        local_source_path: Path | None = None,
        mime_type: str | None = None,
    ) -> AcquiredArtifactResult:
        safety_issue = self._paths_inside(output_root, [*artifact_paths, primary])
        if safety_issue is not None:
            return self._acquired_failed(target, output_root, "artifact_path_escape", safety_issue, artifact_kind=artifact_kind, source_url=source_url, local_source_path=local_source_path, metadata=metadata)
        content_hash = self._hash_file(primary) if primary.exists() and primary.is_file() else None
        resolved_mime = mime_type or self._mime_type_for_path(primary)
        view = AcquiredArtifactView(
            ok=True,
            phase="acquired",
            artifact_kind=artifact_kind,
            artifact_paths=[str(path) for path in artifact_paths],
            primary_artifact_path=str(primary),
            output_root=str(output_root),
            target_kind=target.kind,
            target_value=target.value,
            source_url=source_url,
            local_source_path=str(local_source_path) if local_source_path else None,
            mime_type=resolved_mime,
            content_hash=content_hash,
            metadata=metadata,
            summary=summary,
        )
        return AcquiredArtifactResult(
            ok=True,
            target=target,
            artifact_paths=[str(path) for path in artifact_paths],
            primary_artifact_path=str(primary),
            artifact_kind=artifact_kind,
            output_root=str(output_root),
            source_url=source_url,
            local_source_path=str(local_source_path) if local_source_path else None,
            mime_type=resolved_mime,
            artifact_view=view,
            metadata=metadata,
            content_hash=content_hash,
            summary=summary,
        )

    def _acquired_failed(
        self,
        target: MaterialTarget,
        output_root: Path,
        issue_code: str,
        summary: str,
        *,
        artifact_kind: str,
        source_url: str | None = None,
        local_source_path: Path | None = None,
        metadata: dict[str, str] | None = None,
    ) -> AcquiredArtifactResult:
        metadata = metadata or {}
        view = AcquiredArtifactView(
            ok=False,
            phase="acquired",
            artifact_kind=artifact_kind,
            output_root=str(output_root),
            target_kind=target.kind,
            target_value=target.value,
            source_url=source_url,
            local_source_path=str(local_source_path) if local_source_path else None,
            metadata=metadata,
            summary=summary,
            issue_code=issue_code,
        )
        return AcquiredArtifactResult(
            ok=False,
            target=target,
            artifact_kind=artifact_kind,
            output_root=str(output_root),
            source_url=source_url,
            local_source_path=str(local_source_path) if local_source_path else None,
            artifact_view=view,
            metadata=metadata,
            summary=summary,
            issue_code=issue_code,
        )

    def _ensure_output_path(self, output_root: Path, path: Path) -> str | None:
        issue = self._paths_inside(output_root, [path])
        if issue is not None:
            return issue
        if path.exists() and path.is_symlink():
            return f"Artifact path must not be a symlink: {path}"
        return None

    def _paths_inside(self, output_root: Path, paths: list[Path]) -> str | None:
        root = Path(output_root).expanduser().resolve(strict=False)
        for path in paths:
            resolved = Path(path).expanduser().resolve(strict=False)
            if not resolved.is_relative_to(root):
                return f"Artifact path escapes output root: {path}"
        return None

    def _guess_output_root_for_extracted(self, extracted: list[Path], primary: Path | None) -> Path | None:
        candidates = [path for path in [*extracted, *([primary] if primary else [])] if path is not None]
        if not candidates:
            return None
        for path in candidates:
            parts = path.parts
            if "normalized" in parts:
                index = parts.index("normalized")
                if index > 0:
                    return Path(*parts[:index])
        return candidates[0].parent

    def _artifact_kind_for_path(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return "pdf"
        if suffix in {".html", ".htm"}:
            return "html"
        if suffix == ".tex":
            return "tex"
        if suffix in {".txt", ".md"}:
            return "text"
        if suffix in {".tar", ".gz", ".tgz", ".zip"}:
            return "archive"
        return "binary"

    def _material_kind_for_primary(self, primary: Path | None) -> str:
        if primary is None:
            return "unknown"
        suffix = primary.suffix.lower()
        if suffix == ".md":
            return "markdown"
        if suffix == ".tex":
            return "tex_source"
        if suffix == ".txt":
            return "text"
        return self._artifact_kind_for_path(primary)

    def _mime_type_for_path(self, path: Path | None, response_headers: dict[str, str] | None = None) -> str | None:
        if response_headers:
            for key, value in response_headers.items():
                if key.lower() == "content-type" and value.strip():
                    return value.split(";", 1)[0].strip().lower()
        if path is None:
            return None
        guessed, _encoding = mimetypes.guess_type(str(path))
        return guessed
