"""Repo-local Mathlib index component."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.mathlib import MathlibDeclEntry, MathlibIndex, MathlibModuleEntry
from lean_constellation.services.foundation import FoundationContext, IssueSeverity, ServiceIssue, ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices

EntryKind = Literal["all", "module", "declaration"]

_MAX_SNIPPET_CHARS = 2000


class MathlibSearchHit(StrictModel):
    entry_kind: Literal["module", "declaration"]
    key: str
    module: str | None = None
    name: str | None = None
    kind: str | None = None
    summary: str | None = None
    matched_fields: list[str] = Field(default_factory=list)
    snippet: str | None = None


class MathlibSearchView(StrictModel):
    query: str
    regex: bool = False
    entry_kind: EntryKind = "all"
    limit: int = 20
    hits: list[MathlibSearchHit] = Field(default_factory=list)
    truncated: bool = False
    summary: str


class MathlibModuleEntryView(StrictModel):
    module: str
    summary: str | None = None
    important_decl_names: list[str] = Field(default_factory=list)
    note: str | None = None


class MathlibDeclEntryView(StrictModel):
    name: str
    module: str | None = None
    kind: str | None = None
    signature: str | None = None
    snippet: str | None = None
    summary: str | None = None
    note: str | None = None


class MathlibModuleEntryMutationView(StrictModel):
    module: str
    changed: bool
    summary: str


class MathlibIndexEnsureEffect(StrictModel):
    changed: bool = False
    created_declarations: list[str] = Field(default_factory=list)
    reused_declarations: list[str] = Field(default_factory=list)
    updated_declarations: list[str] = Field(default_factory=list)
    created_modules: list[str] = Field(default_factory=list)


class MathlibIndexComponent:
    """Maintain `.lean_constellation/indexes/mathlib.json`."""

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def search_mathlib_index(
        self,
        repo_root: Path,
        *,
        query: str,
        regex: bool = False,
        entry_kind: str = "all",
        limit: int = 20,
    ) -> ServiceResult[MathlibSearchView]:
        normalized_query = query.strip()
        if not normalized_query:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("mathlib_index_query_empty", "Mathlib index search query must be non-empty.", field="query")
            )
        normalized_kind = self._normalize_entry_kind(entry_kind)
        if normalized_kind is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_index_entry_kind_invalid",
                    f"Unsupported Mathlib index entry kind: {entry_kind}",
                    field="entry_kind",
                    expected="all | module | declaration",
                )
            )
        if limit < 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("mathlib_index_limit_invalid", "Mathlib index search limit must be >= 1.", field="limit")
            )
        matcher_result = self._build_matcher(normalized_query, regex)
        if not matcher_result.ok or matcher_result.value is None:
            return self.runtime.foundation.fail(matcher_result.issues)
        matcher = matcher_result.value
        index_result = self._load_index(repo_root)
        if not index_result.ok or index_result.value is None:
            return self.runtime.foundation.fail(index_result.issues)

        hits: list[MathlibSearchHit] = []
        matched_count = 0
        index = index_result.value
        if normalized_kind in {"all", "module"}:
            for module in sorted(index.modules):
                entry = index.modules[module]
                fields = self._matched_module_fields(entry, matcher)
                if fields:
                    matched_count += 1
                    if len(hits) < limit:
                        hits.append(
                            MathlibSearchHit(
                                entry_kind="module",
                                key=module,
                                module=entry.module,
                                summary=entry.summary,
                                matched_fields=fields,
                                snippet=entry.note,
                            )
                        )
        if normalized_kind in {"all", "declaration"}:
            for name in sorted(index.declarations):
                entry = index.declarations[name]
                fields = self._matched_decl_fields(entry, matcher)
                if fields:
                    matched_count += 1
                    if len(hits) < limit:
                        hits.append(
                            MathlibSearchHit(
                                entry_kind="declaration",
                                key=name,
                                module=entry.module,
                                name=entry.name,
                                kind=entry.kind,
                                summary=entry.summary,
                                matched_fields=fields,
                                snippet=entry.snippet,
                            )
                        )
        return self.runtime.foundation.ok(
            MathlibSearchView(
                query=normalized_query,
                regex=regex,
                entry_kind=normalized_kind,
                limit=limit,
                hits=hits,
                truncated=matched_count > len(hits),
                summary=f"Found {len(hits)} Mathlib index hits" + (" (truncated)." if matched_count > len(hits) else "."),
            )
        )

    def get_mathlib_module_entry(self, repo_root: Path, *, module: str) -> ServiceResult[MathlibModuleEntryView]:
        normalized = self._normalize_module_or_fail(module)
        if not normalized.ok or normalized.value is None:
            return self.runtime.foundation.fail(normalized.issues)
        index = self._load_index(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        entry = index.value.modules.get(normalized.value)
        if entry is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_module_entry_missing",
                    f"Mathlib module entry is not recorded: {normalized.value}",
                    object_ref=normalized.value,
                )
            )
        return self.runtime.foundation.ok(self._module_view(entry))

    def upsert_mathlib_module_entry(
        self,
        repo_root: Path,
        *,
        module: str,
        summary: str | None = None,
        note: str | None = None,
    ) -> ServiceResult[MathlibModuleEntryView]:
        normalized = self._normalize_module_or_fail(module)
        if not normalized.ok or normalized.value is None:
            return self.runtime.foundation.fail(normalized.issues)
        index = self._load_index(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        entry = index.value.modules.get(normalized.value) or MathlibModuleEntry(module=normalized.value)
        if summary is not None:
            entry.summary = self._optional_text(summary)
        if note is not None:
            entry.note = self._optional_text(note)
        index.value.modules[normalized.value] = entry
        saved = self._save_index(repo_root, index.value)
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self._module_view(entry))

    def add_module_important_decl(
        self,
        repo_root: Path,
        *,
        module: str,
        decl_name: str,
    ) -> ServiceResult[MathlibModuleEntryView]:
        normalized_module = self._normalize_module_or_fail(module)
        if not normalized_module.ok or normalized_module.value is None:
            return self.runtime.foundation.fail(normalized_module.issues)
        normalized_decl = self._normalize_decl_or_fail(decl_name)
        if not normalized_decl.ok or normalized_decl.value is None:
            return self.runtime.foundation.fail(normalized_decl.issues)
        warnings: list[ServiceIssue] = []
        index = self._load_index(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        entry = index.value.modules.get(normalized_module.value)
        if entry is None:
            entry = MathlibModuleEntry(module=normalized_module.value)
            index.value.modules[normalized_module.value] = entry
            warnings.append(
                self.runtime.foundation.issue(
                    "mathlib_module_entry_auto_created",
                    f"Created missing Mathlib module entry before adding important declaration: {normalized_module.value}",
                    severity=IssueSeverity.WARNING,
                    object_ref=normalized_module.value,
                )
            )
        if normalized_decl.value in entry.important_decl_names:
            warnings.append(
                self.runtime.foundation.issue(
                    "mathlib_module_important_decl_duplicate",
                    f"Important declaration is already recorded for module {normalized_module.value}: {normalized_decl.value}",
                    severity=IssueSeverity.WARNING,
                    object_ref=normalized_decl.value,
                )
            )
        else:
            entry.important_decl_names.append(normalized_decl.value)
        saved = self._save_index(repo_root, index.value)
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self._module_view(entry), warnings=warnings)

    def get_mathlib_decl_entry(self, repo_root: Path, *, name: str) -> ServiceResult[MathlibDeclEntryView]:
        normalized = self._normalize_decl_or_fail(name)
        if not normalized.ok or normalized.value is None:
            return self.runtime.foundation.fail(normalized.issues)
        index = self._load_index(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        entry = index.value.declarations.get(normalized.value)
        if entry is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_decl_entry_missing",
                    f"Mathlib declaration entry is not recorded: {normalized.value}",
                    object_ref=normalized.value,
                )
            )
        return self.runtime.foundation.ok(self._decl_view(entry))

    def ensure_mathlib_decl_entries(
        self,
        repo_root: Path,
        *,
        entries: list[MathlibDeclEntryView],
        modules: list[str] | None = None,
    ) -> ServiceResult[MathlibIndexEnsureEffect]:
        """Persist canonical verified declaration/module entries in one index write."""

        loaded = self._load_index(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        index = loaded.value.model_copy(deep=True)
        created_declarations: list[str] = []
        reused_declarations: list[str] = []
        updated_declarations: list[str] = []
        created_modules: list[str] = []

        for raw_module in modules or []:
            normalized_module = self._normalize_module_or_fail(raw_module)
            if not normalized_module.ok or normalized_module.value is None:
                return self.runtime.foundation.fail(normalized_module.issues)
            if normalized_module.value not in index.modules:
                index.modules[normalized_module.value] = MathlibModuleEntry(
                    module=normalized_module.value
                )
                created_modules.append(normalized_module.value)

        for candidate in entries:
            normalized_name = self._normalize_decl_or_fail(candidate.name)
            if not normalized_name.ok or normalized_name.value is None:
                return self.runtime.foundation.fail(normalized_name.issues)
            if candidate.module is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "mathlib_decl_module_missing",
                        "A canonical Mathlib declaration entry must include its defining module.",
                        object_ref=candidate.name,
                        field="module",
                    )
                )
            normalized_module = self._normalize_module_or_fail(candidate.module)
            if not normalized_module.ok or normalized_module.value is None:
                return self.runtime.foundation.fail(normalized_module.issues)

            name = normalized_name.value
            module = normalized_module.value
            existing = index.declarations.get(name)
            if existing is not None and existing.module not in {None, module}:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "mathlib_decl_module_conflict",
                        "Canonical Mathlib declaration metadata conflicts with the repo index.",
                        object_ref=name,
                        current=existing.module,
                        expected=module,
                    )
                )

            module_entry = index.modules.get(module)
            if module_entry is None:
                module_entry = MathlibModuleEntry(module=module)
                index.modules[module] = module_entry
                created_modules.append(module)
            if name not in module_entry.important_decl_names:
                module_entry.important_decl_names.append(name)

            if existing is None:
                index.declarations[name] = MathlibDeclEntry(
                    name=name,
                    module=module,
                    kind=candidate.kind,
                    signature=candidate.signature,
                    snippet=candidate.snippet,
                    summary=candidate.summary,
                    note=candidate.note,
                )
                created_declarations.append(name)
                continue

            updated = existing.model_copy(
                update={
                    "module": module,
                    "kind": existing.kind or candidate.kind,
                    "signature": existing.signature or candidate.signature,
                    "snippet": existing.snippet or candidate.snippet,
                }
            )
            if updated != existing:
                index.declarations[name] = updated
                updated_declarations.append(name)
            else:
                reused_declarations.append(name)

        normalized_index = self._normalize_index(index)
        current_normalized = self._normalize_index(loaded.value)
        changed = normalized_index != current_normalized
        if changed:
            saved = self._save_index(repo_root, normalized_index)
            if not saved.ok:
                return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(
            MathlibIndexEnsureEffect(
                changed=changed,
                created_declarations=created_declarations,
                reused_declarations=reused_declarations,
                updated_declarations=updated_declarations,
                created_modules=created_modules,
            )
        )

    def index_path(self, repo_root: Path) -> Path:
        """Return the canonical repo-local MathlibIndex path for transaction composition."""

        return self._index_path(repo_root)

    def upsert_mathlib_decl_entry(
        self,
        repo_root: Path,
        *,
        name: str,
        module: str | None,
        kind: str | None,
        signature: str | None,
        summary: str | None,
        note: str | None,
        snippet: str | None = None,
        replace_missing_metadata: bool = False,
    ) -> ServiceResult[MathlibDeclEntryView]:
        normalized_name = self._normalize_decl_or_fail(name)
        if not normalized_name.ok or normalized_name.value is None:
            return self.runtime.foundation.fail(normalized_name.issues)
        warnings: list[ServiceIssue] = []
        normalized_module: str | None = None
        if module is not None:
            module_result = self._normalize_module_or_fail(module)
            if not module_result.ok:
                return self.runtime.foundation.fail(module_result.issues)
            normalized_module = module_result.value

        index = self._load_index(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        if normalized_module is not None and normalized_module not in index.value.modules:
            warnings.append(
                self.runtime.foundation.issue(
                    "mathlib_decl_module_not_indexed",
                    f"Declaration references a Mathlib module that is not recorded in the index: {normalized_module}",
                    severity=IssueSeverity.WARNING,
                    object_ref=normalized_module,
                    field="module",
                )
            )
        entry = index.value.declarations.get(normalized_name.value) or MathlibDeclEntry(name=normalized_name.value)
        old_module = entry.module
        if module is not None or replace_missing_metadata:
            entry.module = normalized_module
        if kind is not None or replace_missing_metadata:
            entry.kind = self._optional_text(kind)
        if signature is not None or replace_missing_metadata:
            entry.signature = self._optional_text(signature)
        if summary is not None:
            entry.summary = self._optional_text(summary)
        if note is not None:
            entry.note = self._optional_text(note)
        if snippet is not None or replace_missing_metadata:
            normalized_snippet = self._optional_text(snippet)
            if normalized_snippet is not None and len(normalized_snippet) > _MAX_SNIPPET_CHARS:
                normalized_snippet = normalized_snippet[:_MAX_SNIPPET_CHARS]
                warnings.append(
                    self.runtime.foundation.issue(
                        "mathlib_decl_snippet_truncated",
                        f"Mathlib declaration snippet was truncated to {_MAX_SNIPPET_CHARS} characters.",
                        severity=IssueSeverity.WARNING,
                        object_ref=normalized_name.value,
                        field="snippet",
                    )
                )
            entry.snippet = normalized_snippet
        if old_module is not None and old_module != entry.module and old_module in index.value.modules:
            index.value.modules[old_module].important_decl_names = [
                item for item in index.value.modules[old_module].important_decl_names if item != normalized_name.value
            ]
        index.value.declarations[normalized_name.value] = entry
        saved = self._save_index(repo_root, index.value)
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self._decl_view(entry), warnings=warnings)

    def _index_path(self, repo_root: Path) -> Path:
        return self.runtime.foundation.index_cache_path(FoundationContext(repo_root=Path(repo_root)), "mathlib")

    def _load_index(self, repo_root: Path) -> ServiceResult[MathlibIndex]:
        path = self._index_path(repo_root)
        if not path.exists():
            return self.runtime.foundation.ok(MathlibIndex())
        return self.runtime.foundation.read_json(path, MathlibIndex)

    def _save_index(self, repo_root: Path, index: MathlibIndex) -> ServiceResult[MathlibIndex]:
        normalized = self._normalize_index(index)
        write = self.runtime.foundation.write_json_atomic(self._index_path(repo_root), normalized)
        if not write.ok:
            return self.runtime.foundation.fail(write.issues)
        return self.runtime.foundation.ok(normalized)

    def _normalize_index(self, index: MathlibIndex) -> MathlibIndex:
        modules = {entry.module: entry for _, entry in sorted(index.modules.items(), key=lambda item: item[0])}
        declarations = {entry.name: entry for _, entry in sorted(index.declarations.items(), key=lambda item: item[0])}
        return MathlibIndex(modules=modules, declarations=declarations)

    def _module_view(self, entry: MathlibModuleEntry) -> MathlibModuleEntryView:
        return MathlibModuleEntryView(
            module=entry.module,
            summary=entry.summary,
            important_decl_names=list(entry.important_decl_names),
            note=entry.note,
        )

    def _decl_view(self, entry: MathlibDeclEntry) -> MathlibDeclEntryView:
        return MathlibDeclEntryView(
            name=entry.name,
            module=entry.module,
            kind=entry.kind,
            signature=entry.signature,
            snippet=entry.snippet,
            summary=entry.summary,
            note=entry.note,
        )

    def _normalize_module_or_fail(self, module: str) -> ServiceResult[str]:
        normalized = self._normalize_ref_text(module)
        if not normalized:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("mathlib_module_name_empty", "Mathlib module name must be non-empty.", field="module")
            )
        if not self._is_safe_dotted_name(normalized):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_module_name_invalid",
                    f"Invalid Mathlib module name: {module}",
                    field="module",
                    expected="a non-empty dotted Lean module name without whitespace or path separators",
                )
            )
        return self.runtime.foundation.ok(normalized)

    def _normalize_decl_or_fail(self, name: str) -> ServiceResult[str]:
        normalized = self._normalize_ref_text(name)
        if not normalized:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("mathlib_decl_name_empty", "Mathlib declaration name must be non-empty.", field="name")
            )
        if not self._is_safe_dotted_name(normalized):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_decl_name_invalid",
                    f"Invalid Mathlib declaration name: {name}",
                    field="name",
                    expected="a non-empty dotted Lean declaration name without whitespace or path separators",
                )
            )
        return self.runtime.foundation.ok(normalized)

    def _normalize_ref_text(self, value: str) -> str:
        return value.strip()

    def _is_safe_dotted_name(self, value: str) -> bool:
        if not value or any(ch.isspace() for ch in value):
            return False
        if "/" in value or "\\" in value or ".." in value:
            return False
        parts = value.split(".")
        return all(bool(part) and part not in {".", ".."} for part in parts)

    def _optional_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    def _normalize_entry_kind(self, entry_kind: str) -> EntryKind | None:
        normalized = entry_kind.strip().lower()
        if normalized in {"all", "*"}:
            return "all"
        if normalized in {"module", "modules"}:
            return "module"
        if normalized in {"decl", "decls", "declaration", "declarations"}:
            return "declaration"
        return None

    def _build_matcher(self, query: str, regex: bool) -> ServiceResult[re.Pattern[str] | str]:
        if not regex:
            return self.runtime.foundation.ok(query.lower())
        try:
            return self.runtime.foundation.ok(re.compile(query, flags=re.IGNORECASE))
        except re.error as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_index_regex_invalid",
                    f"Invalid Mathlib index regex: {exc}",
                    field="query",
                    details={"regex_error": str(exc)},
                )
            )

    def _matches(self, value: str | None, matcher: re.Pattern[str] | str) -> bool:
        if value is None:
            return False
        if isinstance(matcher, str):
            return matcher in value.lower()
        return matcher.search(value) is not None

    def _matched_module_fields(self, entry: MathlibModuleEntry, matcher: re.Pattern[str] | str) -> list[str]:
        fields: list[str] = []
        if self._matches(entry.module, matcher):
            fields.append("module")
        if self._matches(entry.summary, matcher):
            fields.append("summary")
        if self._matches(entry.note, matcher):
            fields.append("note")
        if any(self._matches(name, matcher) for name in entry.important_decl_names):
            fields.append("important_decl_names")
        return fields

    def _matched_decl_fields(self, entry: MathlibDeclEntry, matcher: re.Pattern[str] | str) -> list[str]:
        fields: list[str] = []
        candidates = {
            "name": entry.name,
            "module": entry.module,
            "kind": entry.kind,
            "signature": entry.signature,
            "summary": entry.summary,
            "note": entry.note,
            "snippet": entry.snippet,
        }
        for field, value in candidates.items():
            if self._matches(value, matcher):
                fields.append(field)
        return fields
