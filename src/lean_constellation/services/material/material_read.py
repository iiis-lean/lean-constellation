"""Unified material file reading and text search."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import FoundationContext, FoundationService, ServiceResult


class MaterialFileEntry(StrictModel):
    kind: str
    locator: str
    line_count: int
    readable: bool


class MaterialFileTreeView(StrictModel):
    repo_root: str
    material_kind: str
    files: list[MaterialFileEntry] = Field(default_factory=list)
    summary: str


class MaterialRangeView(StrictModel):
    ref_kind: str
    locator: str
    start_line: int
    end_line: int
    text_with_line_numbers: str
    before_context: str | None = None
    after_context: str | None = None
    reusable_ref_fields: dict[str, str | int] = Field(default_factory=dict)


class MaterialSearchHit(StrictModel):
    ref_kind: str
    locator: str
    line_number: int
    line_text: str
    reusable_ref_fields: dict[str, str | int] = Field(default_factory=dict)


class MaterialSearchView(StrictModel):
    query: str
    scope: str
    regex: bool
    hits: list[MaterialSearchHit] = Field(default_factory=list)
    truncated: bool = False
    summary: str


class MaterialRefPreviewView(StrictModel):
    ref_kind: str
    locator: str
    preview: MaterialRangeView
    summary: str


class MaterialReadComponent:
    """Read line ranges and search source/resource text files."""

    def __init__(self, foundation: FoundationService, source_corpus: Any = None, resource_library: Any = None) -> None:
        self.foundation = foundation
        self.source_corpus = source_corpus
        self.resource_library = resource_library

    def list_material_files(self, repo_root: Path, *, material_kind: str) -> ServiceResult[MaterialFileTreeView]:
        material_kind = material_kind.lower()
        if material_kind not in {"source", "resource", "all"}:
            return self.foundation.fail(self.foundation.issue("invalid_material_kind", "material_kind must be source, resource, or all."))
        files: list[MaterialFileEntry] = []
        if material_kind in {"source", "all"}:
            source_root = self._source_root(repo_root)
            if source_root.exists():
                for path in sorted(source_root.rglob("*")):
                    if path.is_file():
                        readable, line_count = self._readable_line_count(path)
                        files.append(
                            MaterialFileEntry(
                                kind="source",
                                locator=path.relative_to(source_root).as_posix(),
                                line_count=line_count,
                                readable=readable,
                            )
                        )
        if material_kind in {"resource", "all"}:
            resource_root = self.foundation.layout.resources_root(FoundationContext(repo_root=Path(repo_root))) / "items"
            if resource_root.exists():
                for path in sorted(resource_root.glob("*/normalized/**/*")):
                    if path.is_file():
                        readable, line_count = self._readable_line_count(path)
                        key = path.relative_to(resource_root).parts[0]
                        locator = f"{key}:{path.relative_to(resource_root / key).as_posix()}"
                        files.append(MaterialFileEntry(kind="resource", locator=locator, line_count=line_count, readable=readable))
        return self.foundation.ok(
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
        context_lines: int = 2,
    ) -> ServiceResult[MaterialRangeView]:
        root = self._source_root(repo_root)
        try:
            target = self._resolve_inside(root, path)
        except ValueError as exc:
            return self.foundation.fail(self.foundation.issue("source_material_path_invalid", str(exc), object_ref=path))
        return self._read_range(
            target,
            ref_kind="source",
            locator=target.relative_to(root).as_posix(),
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
            return self.foundation.fail(self.foundation.issue("resource_library_unavailable", "ResourceLibraryComponent is not configured."))
        if not isinstance(resource_key, str) or not resource_key.strip():
            return self.foundation.fail(self.foundation.issue("invalid_resource_key", "resource_key must be non-empty."))
        try:
            entry = self.resource_library.normalized_entry_path(repo_root, resource_key)
        except ValueError as exc:
            return self.foundation.fail(self.foundation.issue("invalid_resource_key", str(exc), object_ref=str(resource_key)))
        if not entry.ok or entry.value is None:
            return self.foundation.fail(entry.issues)
        return self._read_range(
            entry.value,
            ref_kind="resource",
            locator=resource_key,
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
        limit: int = 20,
    ) -> ServiceResult[MaterialSearchView]:
        query = query.strip()
        if not query:
            return self.foundation.fail(self.foundation.issue("empty_query", "Search query must be non-empty."))
        if limit < 1:
            return self.foundation.fail(self.foundation.issue("invalid_search_limit", "Search limit must be >= 1."))
        files = self.list_material_files(repo_root, material_kind=scope)
        if not files.ok or files.value is None:
            return self.foundation.fail(files.issues)
        try:
            pattern = re.compile(query) if regex else None
        except re.error as exc:
            return self.foundation.fail(self.foundation.issue("invalid_search_regex", str(exc)))
        hits: list[MaterialSearchHit] = []
        for item in files.value.files:
            if not item.readable:
                continue
            try:
                if item.kind == "source":
                    path = self._source_root(repo_root) / item.locator
                    reusable_key = {"path": item.locator}
                else:
                    resource_key = item.locator.split(":", 1)[0]
                    if self.resource_library is None:
                        continue
                    entry = self.resource_library.normalized_entry_path(repo_root, resource_key)
                    if not entry.ok or entry.value is None:
                        continue
                    path = entry.value
                    reusable_key = {"resource_key": resource_key}
                for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                    matched = bool(pattern.search(line)) if pattern else query.lower() in line.lower()
                    if matched:
                        hits.append(
                            MaterialSearchHit(
                                ref_kind=item.kind,
                                locator=item.locator,
                                line_number=line_number,
                                line_text=line,
                                reusable_ref_fields={**reusable_key, "start_line": line_number, "end_line": line_number},
                            )
                        )
                        if len(hits) >= limit:
                            return self.foundation.ok(
                                MaterialSearchView(query=query, scope=scope, regex=regex, hits=hits, truncated=True, summary=f"Found at least {len(hits)} hits.")
                            )
            except (UnicodeDecodeError, OSError):
                continue
        return self.foundation.ok(
            MaterialSearchView(query=query, scope=scope, regex=regex, hits=hits, summary=f"Found {len(hits)} hits.")
        )

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
            if self.source_corpus is None:
                return self.foundation.fail(self.foundation.issue("source_corpus_unavailable", "SourceCorpusComponent is not configured."))
            return self.source_corpus.validate_source_ref(repo_root, path=locator, start_line=start_line, end_line=end_line)
        if ref_kind == "resource":
            if self.resource_library is None:
                return self.foundation.fail(self.foundation.issue("resource_library_unavailable", "ResourceLibraryComponent is not configured."))
            try:
                return self.resource_library.validate_resource_ref(repo_root, resource_key=locator, start_line=start_line, end_line=end_line)
            except ValueError as exc:
                return self.foundation.fail(self.foundation.issue("invalid_resource_key", str(exc), object_ref=locator))
        return self.foundation.fail(self.foundation.issue("invalid_ref_kind", "ref_kind must be source or resource."))

    def preview_material_ref(self, repo_root: Path, *, ref: Any) -> ServiceResult[MaterialRefPreviewView]:
        if isinstance(ref, dict):
            data = ref
        elif hasattr(ref, "model_dump"):
            data = ref.model_dump()
        else:
            return self.foundation.fail(self.foundation.issue("invalid_material_ref", "Material ref must be a mapping or pydantic model."))
        kind = data.get("kind") or data.get("ref_kind")
        nested = data.get("ref") if isinstance(data.get("ref"), dict) else {}
        try:
            start_line = int(data.get("start_line") or nested.get("start_line") or 1)
            end_line = int(data.get("end_line") or nested.get("end_line") or start_line)
        except (TypeError, ValueError) as exc:
            return self.foundation.fail(self.foundation.issue("invalid_material_ref_range", str(exc)))
        if kind == "source":
            locator = data.get("path") or data.get("locator") or nested.get("path")
            if not isinstance(locator, str) or not locator.strip():
                return self.foundation.fail(self.foundation.issue("invalid_material_ref", "Source material ref requires path or locator."))
            preview = self.read_source_range(repo_root, path=locator, start_line=start_line, end_line=end_line)
        elif kind == "resource":
            locator = data.get("resource_key") or data.get("locator") or nested.get("resource_key")
            if not isinstance(locator, str) or not locator.strip():
                return self.foundation.fail(self.foundation.issue("invalid_material_ref", "Resource material ref requires resource_key or locator."))
            preview = self.read_resource_range(repo_root, resource_key=locator, start_line=start_line, end_line=end_line)
        else:
            return self.foundation.fail(self.foundation.issue("invalid_material_ref", "Material ref kind must be source or resource."))
        if not preview.ok or preview.value is None:
            return self.foundation.fail(preview.issues)
        return self.foundation.ok(
            MaterialRefPreviewView(
                ref_kind=kind,
                locator=locator,
                preview=preview.value,
                summary=f"Previewed {kind} material ref.",
            )
        )

    def _read_range(
        self,
        path: Path,
        *,
        ref_kind: str,
        locator: str,
        start_line: int,
        end_line: int,
        context_lines: int,
        reusable: dict[str, str],
    ) -> ServiceResult[MaterialRangeView]:
        if context_lines < 0:
            return self.foundation.fail(self.foundation.issue("invalid_context_lines", "context_lines must be >= 0."))
        if not path.exists() or not path.is_file():
            return self.foundation.fail(self.foundation.issue("material_not_found", f"Material file not found: {path}"))
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            return self.foundation.fail(self.foundation.issue("material_not_readable", f"Material file is not UTF-8 text: {path}"))
        if not (1 <= start_line <= end_line <= len(lines)):
            return self.foundation.fail(
                self.foundation.issue(
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
        return self.foundation.ok(
            MaterialRangeView(
                ref_kind=ref_kind,
                locator=locator,
                start_line=start_line,
                end_line=end_line,
                text_with_line_numbers=selected,
                before_context=before,
                after_context=after,
                reusable_ref_fields={**reusable, "start_line": start_line, "end_line": end_line},
            )
        )

    def _source_root(self, repo_root: Path) -> Path:
        return self.foundation.layout.source_corpus_root(FoundationContext(repo_root=Path(repo_root)))

    def _resolve_inside(self, root: Path, path: str) -> Path:
        relative = self.foundation.layout.ensure_relative_path(path)
        target = root / relative
        self.foundation.layout.assert_within(root, target)
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
