"""Unified material file reading and text search."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.refs import MaterialRef, ResourceRef, SourceRef
from lean_constellation.services.foundation import FoundationContext, ServiceResult
from lean_constellation.services.material.ref_codec import format_material_ref

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class MaterialFileEntry(StrictModel):
    kind: str
    locator: str
    resource_key: str | None = None
    resource_locator: str | None = None
    line_count: int
    readable: bool


class MaterialFileTreeView(StrictModel):
    repo_root: str
    material_kind: str
    files: list[MaterialFileEntry] = Field(default_factory=list)
    summary: str


class MaterialRangeView(StrictModel):
    material_kind: Literal["source", "resource"]
    path: str | None = None
    resource_key: str | None = None
    start_line: int
    end_line: int
    text_with_line_numbers: str
    before_context: str | None = None
    after_context: str | None = None
    reusable_ref_fields: dict[str, str | int] = Field(default_factory=dict)


class MaterialSearchHit(StrictModel):
    material_kind: Literal["source", "resource"]
    ref: str
    path: str | None = None
    resource_key: str | None = None
    resource_locator: str | None = None
    line_number: int
    line_text: str
    reusable_ref_fields: dict[str, str | int] = Field(default_factory=dict)


class MaterialSearchView(StrictModel):
    query: str
    scope: str
    regex: bool
    hits: list[MaterialSearchHit] = Field(default_factory=list)
    total_matching_count: int = 0
    truncated: bool = False
    summary: str


class MaterialRefPreviewView(StrictModel):
    material_kind: Literal["source", "resource"]
    path: str | None = None
    resource_key: str | None = None
    preview: MaterialRangeView
    summary: str


class MaterialReadComponent:
    """Read line ranges and search source/resource text files."""

    def __init__(self, runtime: LeanRuntimeServices, source_corpus: Any = None, resource_library: Any = None) -> None:
        self.runtime = runtime
        self.source_corpus = source_corpus
        self.resource_library = resource_library

    def list_material_files(
        self,
        repo_root: Path,
        *,
        material_kind: str,
        _count_resource_lines: bool = True,
    ) -> ServiceResult[MaterialFileTreeView]:
        material_kind = material_kind.lower()
        if material_kind not in {"source", "resource", "all"}:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_material_kind", "material_kind must be source, resource, or all."))
        files: list[MaterialFileEntry] = []
        if material_kind in {"source", "all"}:
            if self.source_corpus is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "source_corpus_unavailable",
                        "SourceCorpusComponent is not configured.",
                    )
                )
            manifest = self.source_corpus.get_source_corpus_manifest(repo_root)
            if not manifest.ok or manifest.value is None:
                return self.runtime.foundation.fail(manifest.issues)
            files.extend(
                MaterialFileEntry(
                    kind="source",
                    locator=item.path,
                    line_count=item.line_count,
                    readable=item.readable_text,
                )
                for item in sorted(manifest.value.files, key=lambda value: value.path)
            )
        if material_kind in {"resource", "all"}:
            if self.resource_library is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "resource_library_unavailable",
                        "ResourceLibraryComponent is not configured.",
                    )
                )
            resources = self.resource_library.list_resources(repo_root)
            if not resources.ok or resources.value is None:
                return self.runtime.foundation.fail(resources.issues)
            for resource in resources.value:
                manifest = self.resource_library.get_resource_material_manifest(
                    repo_root,
                    resource_key=resource.resource_key,
                )
                if not manifest.ok or manifest.value is None:
                    return self.runtime.foundation.fail(manifest.issues)
                for item in sorted(manifest.value.files, key=lambda value: value.path):
                    if item.readable_kind is None:
                        continue
                    resolved = self._resource_file_path(
                        repo_root,
                        resource_key=resource.resource_key,
                        resource_locator=item.path,
                    )
                    if not resolved.ok or resolved.value is None:
                        return self.runtime.foundation.fail(resolved.issues)
                    readable, line_count = (
                        self._readable_line_count(resolved.value)
                        if _count_resource_lines
                        else (True, 0)
                    )
                    files.append(
                        MaterialFileEntry(
                            kind="resource",
                            locator=f"{resource.resource_key}:{item.path}",
                            resource_key=resource.resource_key,
                            resource_locator=item.path,
                            line_count=line_count,
                            readable=readable,
                        )
                    )
        return self.runtime.foundation.ok(
            MaterialFileTreeView(
                repo_root=str(Path(repo_root)),
                material_kind=material_kind,
                files=files,
                summary=f"Listed {len(files)} material files.",
            )
        )

    def read_source_range(
        self,
        repo_root: Path,
        *,
        path: str,
        start_line: int,
        end_line: int,
        context_lines: int = 0,
    ) -> ServiceResult[MaterialRangeView]:
        validation = self.validate_source_range(
            repo_root,
            path=path,
            start_line=start_line,
            end_line=end_line,
        )
        if not validation.ok or validation.value is None:
            return self.runtime.foundation.fail(validation.issues)
        if not validation.value.valid:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    validation.value.issue_code or "source_ref_invalid",
                    validation.value.summary,
                    object_ref=path,
                    current=f"{start_line}-{end_line}",
                )
            )
        root = self._source_root(repo_root)
        try:
            target = self._resolve_inside(root, path)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_material_path_invalid", str(exc), object_ref=path))
        return self._read_range(
            target,
            material_kind="source",
            source_path=target.relative_to(root).as_posix(),
            resource_key=None,
            start_line=start_line,
            end_line=end_line,
            context_lines=context_lines,
            reusable={"path": target.relative_to(root).as_posix()},
        )

    def read_resource_range(
        self,
        repo_root: Path,
        *,
        resource_key: str,
        start_line: int,
        end_line: int,
        context_lines: int = 2,
    ) -> ServiceResult[MaterialRangeView]:
        if self.resource_library is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("resource_library_unavailable", "ResourceLibraryComponent is not configured."))
        if not isinstance(resource_key, str) or not resource_key.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_resource_key", "resource_key must be non-empty."))
        try:
            entry = self.resource_library.canonical_entry_path(repo_root, resource_key)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_resource_key", str(exc), object_ref=str(resource_key)))
        if not entry.ok or entry.value is None:
            return self.runtime.foundation.fail(entry.issues)
        return self._read_range(
            entry.value,
            material_kind="resource",
            source_path=None,
            resource_key=resource_key,
            start_line=start_line,
            end_line=end_line,
            context_lines=context_lines,
            reusable={"resource_key": resource_key},
        )

    def search_material_text(
        self,
        repo_root: Path,
        *,
        query: str,
        scope: str = "all",
        regex: bool = False,
        limit: int | None = 20,
    ) -> ServiceResult[MaterialSearchView]:
        query = query.strip()
        if not query:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("empty_query", "Search query must be non-empty."))
        if limit is not None and limit < 1:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_search_limit", "Search limit must be >= 1."))
        files = self.list_material_files(
            repo_root,
            material_kind=scope,
            _count_resource_lines=False,
        )
        if not files.ok or files.value is None:
            return self.runtime.foundation.fail(files.issues)
        try:
            pattern = re.compile(query) if regex else None
        except re.error as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_search_regex", str(exc)))
        hits: list[MaterialSearchHit] = []
        for item in files.value.files:
            if not item.readable:
                continue
            try:
                if item.kind == "source":
                    path = self._source_root(repo_root) / item.locator
                    reusable_key = {"path": item.locator}
                    resource_key = None
                    resource_locator = None
                else:
                    resource_key = item.resource_key
                    resource_locator = item.resource_locator
                    if resource_key is None or resource_locator is None:
                        continue
                    resolved = self._resource_file_path(
                        repo_root,
                        resource_key=resource_key,
                        resource_locator=resource_locator,
                    )
                    if not resolved.ok or resolved.value is None:
                        continue
                    path = resolved.value
                    reusable_key = {
                        "resource_key": resource_key,
                        "locator": resource_locator,
                    }
                    ref = MaterialRef(
                        kind="resource",
                        ref=ResourceRef(
                            resource_key=resource_key,
                            locator=resource_locator,
                        ),
                    )
                for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                    matched = bool(pattern.search(line)) if pattern else query.lower() in line.lower()
                    if matched:
                        if item.kind == "source":
                            ref = MaterialRef(
                                kind="source",
                                ref=SourceRef(
                                    path=item.locator,
                                    start_line=line_number,
                                    end_line=line_number,
                                ),
                            )
                        else:
                            ref = ref.model_copy(
                                update={
                                    "ref": ref.ref.model_copy(
                                        update={
                                            "start_line": line_number,
                                            "end_line": line_number,
                                        }
                                    )
                                }
                            )
                        hits.append(
                            MaterialSearchHit(
                                material_kind=item.kind,  # type: ignore[arg-type]
                                ref=format_material_ref(ref),
                                path=item.locator if item.kind == "source" else None,
                                resource_key=resource_key if item.kind == "resource" else None,
                                resource_locator=resource_locator if item.kind == "resource" else None,
                                line_number=line_number,
                                line_text=line,
                                reusable_ref_fields={**reusable_key, "start_line": line_number, "end_line": line_number},
                            )
                        )
            except (UnicodeDecodeError, OSError):
                continue
        total = len(hits)
        selected = hits if limit is None else hits[:limit]
        return self.runtime.foundation.ok(
            MaterialSearchView(
                query=query,
                scope=scope,
                regex=regex,
                hits=selected,
                total_matching_count=total,
                truncated=len(selected) < total,
                summary=f"Found {total} hits; returned {len(selected)}.",
            )
        )

    def validate_source_range(
        self,
        repo_root: Path,
        *,
        path: str,
        start_line: int,
        end_line: int,
    ) -> ServiceResult[Any]:
        if self.source_corpus is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_corpus_unavailable", "SourceCorpusComponent is not configured."))
        return self.source_corpus.validate_source_ref(repo_root, path=path, start_line=start_line, end_line=end_line)

    def validate_resource_range(
        self,
        repo_root: Path,
        *,
        resource_key: str,
        start_line: int,
        end_line: int,
    ) -> ServiceResult[Any]:
        if self.resource_library is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("resource_library_unavailable", "ResourceLibraryComponent is not configured."))
        try:
            return self.resource_library.validate_resource_ref(repo_root, resource_key=resource_key, start_line=start_line, end_line=end_line)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_resource_key", str(exc), object_ref=resource_key))

    def validate_material_ref(
        self,
        repo_root: Path,
        *,
        ref_kind: str,
        locator: str,
        start_line: int,
        end_line: int,
    ) -> ServiceResult[Any]:
        if ref_kind == "source":
            return self.validate_source_range(repo_root, path=locator, start_line=start_line, end_line=end_line)
        if ref_kind == "resource":
            return self.validate_resource_range(repo_root, resource_key=locator, start_line=start_line, end_line=end_line)
        return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_ref_kind", "ref_kind must be source or resource."))

    def preview_source_ref(
        self,
        repo_root: Path,
        *,
        path: str,
        start_line: int,
        end_line: int,
        context_lines: int = 2,
    ) -> ServiceResult[MaterialRefPreviewView]:
        preview = self.read_source_range(repo_root, path=path, start_line=start_line, end_line=end_line, context_lines=context_lines)
        if not preview.ok or preview.value is None:
            return self.runtime.foundation.fail(preview.issues)
        return self.runtime.foundation.ok(
            MaterialRefPreviewView(
                material_kind="source",
                path=path,
                preview=preview.value,
                summary="Previewed source material ref.",
            )
        )

    def preview_resource_ref(
        self,
        repo_root: Path,
        *,
        resource_key: str,
        start_line: int,
        end_line: int,
        context_lines: int = 2,
    ) -> ServiceResult[MaterialRefPreviewView]:
        preview = self.read_resource_range(repo_root, resource_key=resource_key, start_line=start_line, end_line=end_line, context_lines=context_lines)
        if not preview.ok or preview.value is None:
            return self.runtime.foundation.fail(preview.issues)
        return self.runtime.foundation.ok(
            MaterialRefPreviewView(
                material_kind="resource",
                resource_key=resource_key,
                preview=preview.value,
                summary="Previewed resource material ref.",
            )
        )

    def preview_material_ref(self, repo_root: Path, *, ref: Any) -> ServiceResult[MaterialRefPreviewView]:
        if isinstance(ref, dict):
            data = ref
        elif hasattr(ref, "model_dump"):
            data = ref.model_dump()
        else:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_material_ref", "Material ref must be a mapping or pydantic model."))
        kind = data.get("kind") or data.get("ref_kind")
        nested = data.get("ref") if isinstance(data.get("ref"), dict) else {}
        raw_start_line = data.get("start_line") or nested.get("start_line")
        raw_end_line = data.get("end_line") or nested.get("end_line")
        if kind == "source" and (raw_start_line is None or raw_end_line is None):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_ref_range_required",
                    "Source material refs require explicit start_line and end_line.",
                )
            )
        try:
            start_line = int(raw_start_line or 1)
            end_line = int(raw_end_line or start_line)
        except (TypeError, ValueError) as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_material_ref_range", str(exc)))
        if kind == "source":
            locator = data.get("path") or data.get("locator") or nested.get("path")
            if not isinstance(locator, str) or not locator.strip():
                return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_material_ref", "Source material ref requires path or locator."))
            return self.preview_source_ref(repo_root, path=locator, start_line=start_line, end_line=end_line)
        elif kind == "resource":
            locator = data.get("resource_key") or data.get("locator") or nested.get("resource_key")
            if not isinstance(locator, str) or not locator.strip():
                return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_material_ref", "Resource material ref requires resource_key or locator."))
            return self.preview_resource_ref(repo_root, resource_key=locator, start_line=start_line, end_line=end_line)
        else:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_material_ref", "Material ref kind must be source or resource."))

    def _read_range(
        self,
        file_path: Path,
        *,
        material_kind: Literal["source", "resource"],
        source_path: str | None,
        resource_key: str | None,
        start_line: int,
        end_line: int,
        context_lines: int,
        reusable: dict[str, str],
    ) -> ServiceResult[MaterialRangeView]:
        if context_lines < 0:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_context_lines", "context_lines must be >= 0."))
        if not file_path.exists() or not file_path.is_file():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("material_not_found", f"Material file not found: {file_path}"))
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("material_not_readable", f"Material file is not UTF-8 text: {file_path}"))
        if not (1 <= start_line <= end_line <= len(lines)):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "material_range_invalid",
                    "Line range is invalid.",
                    current=f"{start_line}-{end_line}",
                    expected=f"1-{len(lines)}",
                )
            )
        selected = self._format_lines(lines, start_line, end_line)
        before_start = max(1, start_line - context_lines)
        before = self._format_lines(lines, before_start, start_line - 1) if before_start < start_line else None
        after_end = min(len(lines), end_line + context_lines)
        after = self._format_lines(lines, end_line + 1, after_end) if after_end > end_line else None
        return self.runtime.foundation.ok(
            MaterialRangeView(
                material_kind=material_kind,
                path=source_path,
                resource_key=resource_key,
                start_line=start_line,
                end_line=end_line,
                text_with_line_numbers=selected,
                before_context=before,
                after_context=after,
                reusable_ref_fields={**reusable, "start_line": start_line, "end_line": end_line},
            )
        )

    def _source_root(self, repo_root: Path) -> Path:
        return self.runtime.foundation.layout.source_corpus_root(FoundationContext(repo_root=Path(repo_root)))

    def _resource_file_path(
        self,
        repo_root: Path,
        *,
        resource_key: str,
        resource_locator: str,
    ) -> ServiceResult[Path]:
        if self.resource_library is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_library_unavailable",
                    "ResourceLibraryComponent is not configured.",
                )
            )
        resource = self.resource_library.get_resource(repo_root, resource_key=resource_key)
        if not resource.ok or resource.value is None:
            return self.runtime.foundation.fail(resource.issues)
        try:
            target = self._resolve_inside(Path(resource.value.resource_root), resource_locator)
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_material_path_invalid",
                    str(exc),
                    object_ref=f"{resource_key}:{resource_locator}",
                )
            )
        if not target.is_file():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_material_file_missing",
                    "Resource manifest file is missing from the finalized package.",
                    object_ref=f"{resource_key}:{resource_locator}",
                )
            )
        return self.runtime.foundation.ok(target)

    def _resolve_inside(self, root: Path, path: str) -> Path:
        relative = self.runtime.foundation.layout.ensure_relative_path(path)
        target = root / relative
        self.runtime.foundation.layout.assert_within(root, target)
        return target

    @staticmethod
    def _readable_line_count(path: Path) -> tuple[bool, int]:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return False, 0
        except OSError:
            return False, 0
        if "\x00" in text:
            return False, 0
        return True, len(text.splitlines())

    @staticmethod
    def _format_lines(lines: list[str], start: int, end: int) -> str:
        if end < start:
            return ""
        return "\n".join(f"{line_no}: {lines[line_no - 1]}" for line_no in range(start, end + 1))
