"""Lean MCP Toolkit ingestion for repo-local Mathlib index."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.services.foundation import FoundationContext, IssueSeverity, ServiceIssue, ServiceResult
from lean_constellation.services.mathlib.mathlib_index import MathlibDeclEntryView, MathlibIndexComponent, MathlibModuleEntryView

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices

_MAX_RAW_EXCERPT_CHARS = 1600
_CHECK_TOOL_NAMES = ("check_mathlib_name", "lsp.run_snippet", "run_snippet")


class MathlibCandidateView(StrictModel):
    candidate_id: str
    name: str | None = None
    module: str | None = None
    kind: str | None = None
    signature: str | None = None
    summary: str | None = None
    snippet: str | None = None
    source_kind: str = "mathlib_search"
    search_query: str | None = None
    raw_excerpt: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)

    @field_validator("candidate_id")
    @classmethod
    def _non_empty_candidate_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("candidate_id must be non-empty")
        return value

    @field_validator("name", "module", "kind", "signature", "summary", "snippet", "source_kind", "search_query", "raw_excerpt")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class MathlibCandidateCache(StrictModel):
    candidates: dict[str, MathlibCandidateView] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=utc_now_iso)


class MathlibExternalSearchView(StrictModel):
    query: str
    search_kinds: list[str] = Field(default_factory=list)
    limit: int = 20
    candidates: list[MathlibCandidateView] = Field(default_factory=list)
    summary: str


class MathlibSemanticSearchView(StrictModel):
    query: str
    limit: int = 20
    candidates: list[MathlibCandidateView] = Field(default_factory=list)
    summary: str


class MathlibNavigationView(StrictModel):
    decl_name: str
    module: str | None = None
    kind: str | None = None
    signature: str | None = None
    code_excerpt: str | None = None
    context: str | None = None
    summary: str


class MathlibModuleNavigationView(StrictModel):
    module: str
    pattern: str | None = None
    imports: list[str] = Field(default_factory=list)
    important_decl_hints: list[str] = Field(default_factory=list)
    declarations: list[MathlibCandidateView] = Field(default_factory=list)
    summary_excerpt: str | None = None
    summary: str


class MathlibCheckView(StrictModel):
    module: str | None = None
    decl_name: str
    passed: bool
    diagnostics: list[str] = Field(default_factory=list)
    checked_code: str
    toolkit_tool: str
    summary: str


class MathlibAccessCheckView(StrictModel):
    target: str
    target_kind: Literal["declaration", "module"]
    module: str | None = None
    passed: bool
    diagnostics: list[str] = Field(default_factory=list)
    checked_code: str
    toolkit_tool: str
    summary: str


class MathlibBatchRecordView(StrictModel):
    modules: list[MathlibModuleEntryView] = Field(default_factory=list)
    declarations: list[MathlibDeclEntryView] = Field(default_factory=list)
    checked_code: str
    toolkit_tool: str
    summary: str


class ToolkitIngestionComponent:
    """Convert toolkit search/navigation results into stable MathlibIndex entries."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        mathlib_index: MathlibIndexComponent,
    ) -> None:
        self.runtime = runtime
        self.mathlib_index = mathlib_index

    def search_external_mathlib(
        self,
        repo_root: Path,
        *,
        query: str,
        search_kinds: list[str],
        limit: int = 20,
    ) -> ServiceResult[MathlibExternalSearchView]:
        normalized_query = query.strip()
        if not normalized_query:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("mathlib_external_query_empty", "External Mathlib search query must be non-empty.", field="query")
            )
        if limit < 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("mathlib_external_limit_invalid", "External Mathlib search limit must be >= 1.", field="limit")
            )
        kinds = [kind.strip() for kind in search_kinds if kind.strip()]
        result = self.runtime.external.lean_toolchain.search_mathlib_declarations(normalized_query, kinds=kinds, limit=limit)
        if not result.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    result.issue_code or "mathlib_external_search_failed",
                    result.summary,
                    details={"query": normalized_query},
                )
            )
        candidates = [self._candidate_from_search_item(normalized_query, item, index) for index, item in enumerate(result.items[:limit])]
        cache_result = self._load_candidate_cache(repo_root)
        if not cache_result.ok or cache_result.value is None:
            return self.runtime.foundation.fail(cache_result.issues)
        for candidate in candidates:
            cache_result.value.candidates[candidate.candidate_id] = candidate
        saved = self._save_candidate_cache(repo_root, cache_result.value)
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(
            MathlibExternalSearchView(
                query=normalized_query,
                search_kinds=kinds,
                limit=limit,
                candidates=candidates,
                summary=f"Found {len(candidates)} external Mathlib candidates.",
            )
        )

    def search_mathlib_declarations(
        self,
        repo_root: Path,
        *,
        query: str,
        limit: int = 20,
    ) -> ServiceResult[MathlibSemanticSearchView]:
        result = self.search_external_mathlib(repo_root, query=query, search_kinds=["declaration"], limit=limit)
        if not result.ok or result.value is None:
            return self.runtime.foundation.fail(result.issues)
        return self.runtime.foundation.ok(
            MathlibSemanticSearchView(
                query=result.value.query,
                limit=result.value.limit,
                candidates=result.value.candidates,
                summary=f"Found {len(result.value.candidates)} semantic Mathlib declaration candidates.",
            )
        )

    def inspect_mathlib_search_candidate(self, repo_root: Path, *, candidate_id: str) -> ServiceResult[MathlibCandidateView]:
        normalized_id = candidate_id.strip()
        if not normalized_id:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("mathlib_candidate_id_empty", "Mathlib candidate id must be non-empty.", field="candidate_id")
            )
        cache = self._load_candidate_cache(repo_root)
        if not cache.ok or cache.value is None:
            return self.runtime.foundation.fail(cache.issues)
        candidate = cache.value.candidates.get(normalized_id)
        if candidate is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_candidate_unknown",
                    f"Mathlib candidate is not in the temporary cache: {normalized_id}",
                    object_ref=normalized_id,
                )
            )
        warnings: list[ServiceIssue] = []
        if candidate.name:
            navigation = self.inspect_mathlib_declaration(repo_root, decl_name=candidate.name)
            if navigation.ok and navigation.value is not None:
                candidate = candidate.model_copy(
                    update={
                        "module": candidate.module or navigation.value.module,
                        "kind": candidate.kind or navigation.value.kind,
                        "signature": candidate.signature or navigation.value.signature,
                        "snippet": candidate.snippet or navigation.value.code_excerpt,
                        "raw_excerpt": candidate.raw_excerpt or navigation.value.context,
                    }
                )
            else:
                warnings.append(
                    self.runtime.foundation.issue(
                        "mathlib_candidate_navigation_unavailable",
                        "Mathlib candidate navigation could not enrich the cached search candidate.",
                        severity=IssueSeverity.WARNING,
                        object_ref=normalized_id,
                        details={"navigation_issues": "; ".join(issue.message for issue in navigation.issues)},
                    )
                )
        return self.runtime.foundation.ok(candidate, warnings=warnings)

    def inspect_mathlib_declaration(self, repo_root: Path, *, decl_name: str) -> ServiceResult[MathlibNavigationView]:
        del repo_root
        normalized_name = decl_name.strip()
        if not normalized_name:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("mathlib_decl_name_empty", "Mathlib declaration name must be non-empty.", field="decl_name")
            )
        result = self.runtime.external.lean_toolchain.inspect_mathlib_declaration(normalized_name)
        if not result.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    result.issue_code or "mathlib_decl_navigation_failed",
                    result.summary,
                    object_ref=normalized_name,
                )
            )
        kind, signature = self._parse_decl_header(result.code)
        return self.runtime.foundation.ok(
            MathlibNavigationView(
                decl_name=normalized_name,
                module=result.module,
                kind=kind,
                signature=signature,
                code_excerpt=self._excerpt(result.code),
                context=result.raw_excerpt,
                summary=f"Inspected Mathlib declaration {normalized_name}.",
            )
        )

    def inspect_mathlib_module(self, repo_root: Path, *, module: str, pattern: str | None = None) -> ServiceResult[MathlibModuleNavigationView]:
        del repo_root
        normalized_module = module.strip()
        if not normalized_module:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("mathlib_module_name_empty", "Mathlib module name must be non-empty.", field="module")
            )
        result = self.runtime.external.lean_toolchain.inspect_mathlib_module(normalized_module)
        if not result.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    result.issue_code or "mathlib_module_navigation_failed",
                    result.summary,
                    object_ref=normalized_module,
                )
            )
        candidates = [self._candidate_from_module_decl(normalized_module, item, index) for index, item in enumerate(result.declarations)]
        normalized_pattern = pattern.strip() if pattern else None
        if normalized_pattern:
            candidates = [candidate for candidate in candidates if self._candidate_matches_pattern(candidate, normalized_pattern)]
        hints = [candidate.name for candidate in candidates if candidate.name]
        return self.runtime.foundation.ok(
            MathlibModuleNavigationView(
                module=normalized_module,
                pattern=normalized_pattern,
                imports=list(result.imports),
                important_decl_hints=hints,
                declarations=candidates,
                summary_excerpt=result.raw_excerpt,
                summary=(
                    f"Inspected Mathlib module {normalized_module} with {len(candidates)} declaration hints"
                    + (f" matching {normalized_pattern!r}." if normalized_pattern else ".")
                ),
            )
        )

    def check_mathlib_name(
        self,
        repo_root: Path,
        *,
        module: str | None,
        decl_name: str,
    ) -> ServiceResult[MathlibCheckView]:
        normalized_decl = decl_name.strip()
        normalized_module = module.strip() if module else None
        if not normalized_decl:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("mathlib_decl_name_empty", "Mathlib declaration name must be non-empty.", field="decl_name")
            )
        code = self._check_code(normalized_module, normalized_decl)
        result = self.runtime.external.lean_toolchain.check_mathlib_name(repo_root, module=normalized_module, decl_name=normalized_decl)
        if not result.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    result.issue_code or "mathlib_check_unavailable",
                    result.summary,
                    object_ref=normalized_decl,
                    details={"provider": result.provider, "fallback_reason": result.fallback_reason or ""},
                )
            )
        passed = bool(result.passed if result.passed is not None else result.ok)
        diagnostics = self._diagnostics_from_excerpt(result.diagnostics_excerpt)
        return self.runtime.foundation.ok(
            MathlibCheckView(
                module=normalized_module,
                decl_name=normalized_decl,
                passed=passed,
                diagnostics=diagnostics,
                checked_code=code,
                toolkit_tool=result.toolkit_tool or result.provider,
                summary=(
                    f"Mathlib name check passed for {normalized_decl}."
                    if passed
                    else f"Mathlib name check failed for {normalized_decl}."
                ),
            )
        )

    def check_mathlib_accessible(
        self,
        repo_root: Path,
        *,
        name_or_module: str,
        module: str | None = None,
        target_kind: Literal["declaration", "module"] = "declaration",
    ) -> ServiceResult[MathlibAccessCheckView]:
        normalized_target = name_or_module.strip()
        if not normalized_target:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("mathlib_access_target_empty", "Mathlib access check target must be non-empty.", field="name_or_module")
            )
        if target_kind == "declaration":
            checked = self.check_mathlib_name(repo_root, module=module, decl_name=normalized_target)
            if not checked.ok or checked.value is None:
                return self.runtime.foundation.fail(checked.issues)
            return self.runtime.foundation.ok(
                MathlibAccessCheckView(
                    target=normalized_target,
                    target_kind="declaration",
                    module=checked.value.module,
                    passed=checked.value.passed,
                    diagnostics=checked.value.diagnostics,
                    checked_code=checked.value.checked_code,
                    toolkit_tool=checked.value.toolkit_tool,
                    summary=checked.value.summary,
                )
            )
        if target_kind != "module":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_access_target_kind_invalid",
                    "Mathlib access check target_kind must be 'declaration' or 'module'.",
                    field="target_kind",
                    current=str(target_kind),
                )
            )
        code = f"import {normalized_target}\n#check True\n"
        result = self.runtime.external.lean_toolchain.check_mathlib_module(repo_root, module=normalized_target)
        if not result.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    result.issue_code or "mathlib_module_check_unavailable",
                    result.summary,
                    object_ref=normalized_target,
                    details={"provider": result.provider, "fallback_reason": result.fallback_reason or ""},
                )
            )
        passed = bool(result.passed if result.passed is not None else result.ok)
        return self.runtime.foundation.ok(
            MathlibAccessCheckView(
                target=normalized_target,
                target_kind="module",
                module=normalized_target,
                passed=passed,
                diagnostics=self._diagnostics_from_excerpt(result.diagnostics_excerpt),
                checked_code=code,
                toolkit_tool=result.toolkit_tool or result.provider,
                summary=(
                    f"Mathlib module access check passed for {normalized_target}."
                    if passed
                    else f"Mathlib module access check failed for {normalized_target}."
                ),
            )
        )

    def record_mathlib_module_checked(
        self,
        repo_root: Path,
        *,
        module_name: str,
        summary: str | None = None,
        source: str | None = None,
    ) -> ServiceResult[MathlibModuleEntryView]:
        check = self.check_mathlib_accessible(repo_root, name_or_module=module_name, target_kind="module")
        if not check.ok or check.value is None:
            return self.runtime.foundation.fail(check.issues)
        if not check.value.passed:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_module_access_check_failed",
                    f"Mathlib module is not accessible from the current repo: {module_name}",
                    object_ref=module_name,
                    details={"diagnostics": "\n".join(check.value.diagnostics), "checked_code": check.value.checked_code},
                )
            )
        return self.mathlib_index.upsert_mathlib_module_entry(repo_root, module=module_name, summary=summary, note=source)

    def record_mathlib_decl_checked(
        self,
        repo_root: Path,
        *,
        decl_name: str,
        module_name: str | None = None,
        summary: str | None = None,
        source: str | None = None,
        kind: str | None = None,
        signature: str | None = None,
        snippet: str | None = None,
    ) -> ServiceResult[MathlibDeclEntryView]:
        normalized_decl = decl_name.strip()
        if not normalized_decl:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("mathlib_decl_name_empty", "Mathlib declaration name must be non-empty.", field="decl_name")
            )
        module = module_name.strip() if module_name else None
        navigation_issues: list[str] = []
        if module is None or kind is None or signature is None or snippet is None:
            navigation = self.inspect_mathlib_declaration(repo_root, decl_name=normalized_decl)
            if navigation.ok and navigation.value is not None:
                module = module or navigation.value.module
                kind = kind or navigation.value.kind
                signature = signature or navigation.value.signature
                snippet = snippet or navigation.value.code_excerpt
            else:
                navigation_issues = [issue.message for issue in navigation.issues]

        existing = self.mathlib_index.get_mathlib_decl_entry(repo_root, name=normalized_decl)
        if existing.ok and existing.value is not None and existing.value.module and module and existing.value.module != module:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_decl_module_conflict",
                    f"Mathlib declaration is already recorded with a different module: {normalized_decl}",
                    object_ref=normalized_decl,
                    current=existing.value.module,
                    expected=module,
                )
            )

        check = self.check_mathlib_accessible(repo_root, name_or_module=normalized_decl, module=module, target_kind="declaration")
        if not check.ok or check.value is None:
            return self.runtime.foundation.fail(check.issues)
        if not check.value.passed:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_decl_access_check_failed",
                    f"Mathlib declaration is not accessible from the current repo: {normalized_decl}",
                    object_ref=normalized_decl,
                    details={
                        "diagnostics": "\n".join(check.value.diagnostics),
                        "checked_code": check.value.checked_code,
                        "navigation_issues": "\n".join(navigation_issues),
                    },
                )
            )

        return self._record_mathlib_decl_unchecked(
            repo_root,
            decl_name=normalized_decl,
            module=module,
            kind=kind,
            signature=signature,
            summary=summary,
            source=source,
            snippet=snippet,
        )

    def record_mathlib_batch_checked(
        self,
        repo_root: Path,
        *,
        modules: list[dict[str, Any]],
        declarations: list[dict[str, Any]],
    ) -> ServiceResult[MathlibBatchRecordView]:
        if not modules and not declarations:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_batch_empty",
                    "At least one Mathlib module or declaration is required.",
                )
            )
        if len(modules) + len(declarations) > 25:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_batch_too_large",
                    "At most 25 Mathlib entries may be recorded in one batch.",
                )
            )

        prepared_modules: list[dict[str, Any]] = []
        seen_modules: set[str] = set()
        for item in modules:
            module_name = str(item.get("module_name") or "").strip()
            if not module_name:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "mathlib_module_name_empty",
                        "Mathlib module name must be non-empty.",
                        field="module_name",
                    )
                )
            if module_name in seen_modules:
                continue
            seen_modules.add(module_name)
            prepared_modules.append({**item, "module_name": module_name})

        prepared_decls: list[dict[str, Any]] = []
        seen_decls: set[str] = set()
        for item in declarations:
            prepared = dict(item)
            decl_name = str(prepared.get("decl_name") or "").strip()
            if not decl_name:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "mathlib_decl_name_empty",
                        "Mathlib declaration name must be non-empty.",
                        field="decl_name",
                    )
                )
            if decl_name in seen_decls:
                continue
            seen_decls.add(decl_name)
            module = str(prepared.get("module_name") or "").strip() or None
            if any(prepared.get(field) is None for field in ("kind", "signature", "snippet")):
                navigation = self.inspect_mathlib_declaration(repo_root, decl_name=decl_name)
                if navigation.ok and navigation.value is not None:
                    module = module or navigation.value.module
                    prepared["kind"] = prepared.get("kind") or navigation.value.kind
                    prepared["signature"] = prepared.get("signature") or navigation.value.signature
                    prepared["snippet"] = prepared.get("snippet") or navigation.value.code_excerpt
            existing = self.mathlib_index.get_mathlib_decl_entry(repo_root, name=decl_name)
            if (
                existing.ok
                and existing.value is not None
                and existing.value.module
                and module
                and existing.value.module != module
            ):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "mathlib_decl_module_conflict",
                        f"Mathlib declaration is already recorded with a different module: {decl_name}",
                        object_ref=decl_name,
                        current=existing.value.module,
                        expected=module,
                    )
                )
            prepared["decl_name"] = decl_name
            prepared["module_name"] = module
            prepared_decls.append(prepared)

        imports = [item["module_name"] for item in prepared_modules]
        imports.extend(
            item["module_name"] for item in prepared_decls if item.get("module_name")
        )
        decl_names = [item["decl_name"] for item in prepared_decls]
        checked = self.runtime.external.lean_toolchain.check_mathlib_batch(
            repo_root,
            imports=imports,
            decl_names=decl_names,
        )
        if not checked.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    checked.issue_code or "mathlib_batch_check_unavailable",
                    checked.summary,
                    details={"diagnostics": checked.diagnostics_excerpt or ""},
                )
            )
        if checked.passed is False:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_batch_access_check_failed",
                    "One or more Mathlib batch entries are not accessible from the current repo.",
                    details={"diagnostics": checked.diagnostics_excerpt or ""},
                )
            )

        warnings: list[ServiceIssue] = []
        recorded_modules: list[MathlibModuleEntryView] = []
        for item in prepared_modules:
            recorded = self.mathlib_index.upsert_mathlib_module_entry(
                repo_root,
                module=item["module_name"],
                summary=item.get("summary"),
                note=item.get("source"),
            )
            if not recorded.ok or recorded.value is None:
                return self.runtime.foundation.fail(recorded.issues)
            warnings.extend(recorded.issues)
            recorded_modules.append(recorded.value)

        recorded_decls: list[MathlibDeclEntryView] = []
        for item in prepared_decls:
            recorded = self._record_mathlib_decl_unchecked(
                repo_root,
                decl_name=item["decl_name"],
                module=item.get("module_name"),
                summary=item.get("summary"),
                source=item.get("source"),
                kind=item.get("kind"),
                signature=item.get("signature"),
                snippet=item.get("snippet"),
            )
            if not recorded.ok or recorded.value is None:
                return self.runtime.foundation.fail(recorded.issues)
            warnings.extend(recorded.issues)
            recorded_decls.append(recorded.value)

        checked_code = "\n".join(
            [
                *(f"import {module}" for module in dict.fromkeys(imports)),
                *(f"#check {name}" for name in decl_names),
                *([] if decl_names else ["#check True"]),
                "",
            ]
        )
        return self.runtime.foundation.ok(
            MathlibBatchRecordView(
                modules=recorded_modules,
                declarations=recorded_decls,
                checked_code=checked_code,
                toolkit_tool=checked.toolkit_tool or checked.provider,
                summary=(
                    f"Verified and recorded {len(recorded_modules)} Mathlib modules and "
                    f"{len(recorded_decls)} declarations in one Lean check."
                ),
            ),
            warnings=warnings,
        )

    def _record_mathlib_decl_unchecked(
        self,
        repo_root: Path,
        *,
        decl_name: str,
        module: str | None,
        summary: str | None,
        source: str | None,
        kind: str | None,
        signature: str | None,
        snippet: str | None,
    ) -> ServiceResult[MathlibDeclEntryView]:
        warnings: list[ServiceIssue] = []
        if module is not None:
            module_result = self.mathlib_index.upsert_mathlib_module_entry(
                repo_root,
                module=module,
            )
            if not module_result.ok:
                return self.runtime.foundation.fail(module_result.issues)
            warnings.extend(module_result.issues)
            important = self.mathlib_index.add_module_important_decl(
                repo_root,
                module=module,
                decl_name=decl_name,
            )
            if not important.ok:
                return self.runtime.foundation.fail(important.issues)
            warnings.extend(important.issues)
        recorded = self.mathlib_index.upsert_mathlib_decl_entry(
            repo_root,
            name=decl_name,
            module=module,
            kind=kind,
            signature=signature,
            summary=summary,
            note=source,
            snippet=snippet,
        )
        if not recorded.ok or recorded.value is None:
            return self.runtime.foundation.fail(recorded.issues)
        warnings.extend(recorded.issues)
        return self.runtime.foundation.ok(recorded.value, warnings=warnings)

    def ingest_mathlib_candidate(
        self,
        repo_root: Path,
        *,
        candidate_id: str,
        summary: str,
        note: str | None = None,
    ) -> ServiceResult[MathlibDeclEntryView]:
        normalized_summary = summary.strip()
        if not normalized_summary:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("mathlib_candidate_summary_empty", "Mathlib candidate ingestion requires a summary.", field="summary")
            )
        cache = self._load_candidate_cache(repo_root)
        if not cache.ok or cache.value is None:
            return self.runtime.foundation.fail(cache.issues)
        candidate = cache.value.candidates.get(candidate_id.strip())
        if candidate is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_candidate_unknown",
                    f"Mathlib candidate is not in the temporary cache: {candidate_id}",
                    object_ref=candidate_id,
                )
            )
        if not candidate.name:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_candidate_missing_decl_name",
                    f"Mathlib candidate has no declaration name: {candidate_id}",
                    object_ref=candidate_id,
                )
            )
        module = candidate.module
        if module is None:
            navigation = self.inspect_mathlib_declaration(repo_root, decl_name=candidate.name)
            if navigation.ok and navigation.value is not None:
                module = navigation.value.module
            if module is None:
                details: dict[str, str] = {}
                if not navigation.ok:
                    details["navigation_issues"] = "; ".join(issue.message for issue in navigation.issues)
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "mathlib_candidate_module_missing",
                        f"Mathlib candidate has no module and declaration navigation did not recover one: {candidate.name}",
                        object_ref=candidate.name,
                        suggested_action="Inspect the declaration/module again before ingesting this Mathlib candidate.",
                        details=details,
                    )
                )
        check = self.check_mathlib_name(repo_root, module=module, decl_name=candidate.name)
        if not check.ok or check.value is None:
            return self.runtime.foundation.fail(check.issues)
        if not check.value.passed:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_candidate_check_failed",
                    f"Mathlib candidate failed name check: {candidate.name}",
                    object_ref=candidate.name,
                    details={"diagnostics": "\n".join(check.value.diagnostics)},
                )
            )
        if module is not None:
            module_result = self.mathlib_index.upsert_mathlib_module_entry(repo_root, module=module)
            if not module_result.ok:
                return self.runtime.foundation.fail(module_result.issues)
            important = self.mathlib_index.add_module_important_decl(repo_root, module=module, decl_name=candidate.name)
            if not important.ok:
                return self.runtime.foundation.fail(important.issues)
        return self.mathlib_index.upsert_mathlib_decl_entry(
            repo_root,
            name=candidate.name,
            module=module,
            kind=candidate.kind,
            signature=candidate.signature,
            summary=normalized_summary,
            note=note,
            snippet=candidate.snippet,
        )

    def _candidate_cache_path(self, repo_root: Path) -> Path:
        return self.runtime.foundation.index_cache_path(FoundationContext(repo_root=Path(repo_root)), "mathlib_candidates")

    def _load_candidate_cache(self, repo_root: Path) -> ServiceResult[MathlibCandidateCache]:
        path = self._candidate_cache_path(repo_root)
        if not path.exists():
            return self.runtime.foundation.ok(MathlibCandidateCache())
        return self.runtime.foundation.read_json(path, MathlibCandidateCache)

    def _save_candidate_cache(self, repo_root: Path, cache: MathlibCandidateCache) -> ServiceResult[MathlibCandidateCache]:
        cache.updated_at = utc_now_iso()
        write = self.runtime.foundation.write_json_atomic(self._candidate_cache_path(repo_root), cache)
        if not write.ok:
            return self.runtime.foundation.fail(write.issues)
        return self.runtime.foundation.ok(cache)

    def _candidate_from_search_item(self, query: str, item: dict[str, Any], index: int) -> MathlibCandidateView:
        name = self._first_text(item, "name", "decl_name", "declaration", "full_name", "constant")
        module = self._first_text(item, "module", "module_name", "import")
        kind = self._first_text(item, "kind", "decl_kind")
        signature = self._first_text(item, "signature", "type", "statement")
        summary = self._first_text(item, "summary", "docstring", "description", "informalization")
        snippet = self._first_text(item, "snippet", "source", "source_text", "code")
        source_kind = self._first_text(item, "source_kind", "source_tool", "source", "tool") or "mathlib_search"
        return MathlibCandidateView(
            candidate_id=self._candidate_id(query, index, item),
            name=name,
            module=module,
            kind=kind,
            signature=signature,
            summary=summary,
            snippet=snippet,
            source_kind=source_kind,
            search_query=query,
            raw_excerpt=self._excerpt(repr(item)),
        )

    def _candidate_from_module_decl(self, module: str, item: dict[str, Any], index: int) -> MathlibCandidateView:
        candidate = self._candidate_from_search_item(f"module:{module}", item, index)
        if candidate.module is None:
            candidate.module = module
        return candidate

    def _candidate_matches_pattern(self, candidate: MathlibCandidateView, pattern: str) -> bool:
        needle = pattern.lower()
        fields = [candidate.name, candidate.module, candidate.kind, candidate.signature, candidate.summary, candidate.snippet]
        return any(needle in field.lower() for field in fields if field)

    def _candidate_id(self, query: str, index: int, item: dict[str, Any]) -> str:
        name = self._first_text(item, "name", "decl_name", "declaration", "full_name", "constant") or ""
        module = self._first_text(item, "module", "module_name", "import") or ""
        digest = hashlib.sha1(f"{query}\0{index}\0{name}\0{module}\0{repr(item)}".encode("utf-8")).hexdigest()
        return f"mc_{digest[:16]}"

    def _first_text(self, item: dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = item.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    def _parse_decl_header(self, code: str | None) -> tuple[str | None, str | None]:
        if not code:
            return None, None
        for line in code.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--") or stripped.startswith("/-"):
                continue
            if stripped.startswith("@[") or stripped.startswith("namespace ") or stripped.startswith("section ") or stripped.startswith("open "):
                continue
            if stripped.startswith("import "):
                continue
            kind_match = re.match(r"(theorem|lemma|def|abbrev|instance|class|structure|inductive|axiom|opaque)\b", stripped)
            kind = kind_match.group(1) if kind_match else None
            header = stripped.split(":=", 1)[0].strip()
            return kind, header[:300]
        return None, None

    def _check_code(self, module: str | None, decl_name: str) -> str:
        imports = f"import {module}\n" if module else ""
        return f"{imports}#check {decl_name}\n"

    def _check_payload(self, tool_name: str, repo_root: Path, module: str | None, decl_name: str, code: str) -> dict[str, Any]:
        if tool_name == "check_mathlib_name":
            return {"repo_root": str(repo_root), "module": module, "decl_name": decl_name, "code": code}
        return {"repo_root": str(repo_root), "code": code, "include_diagnostics": True}

    def _diagnostics_from_excerpt(self, excerpt: str | None) -> list[str]:
        if not excerpt:
            return []
        return [line.strip() for line in excerpt.splitlines() if line.strip()]

    def _check_passed_from_value(self, value: dict[str, Any] | list[Any] | str | None) -> tuple[bool, list[str]]:
        if not isinstance(value, dict):
            return True, []
        diagnostics = self._diagnostics_from_value(value)
        if "passed" in value:
            return bool(value["passed"]), diagnostics
        if "success" in value:
            return bool(value["success"]), diagnostics
        if "ok" in value:
            return bool(value["ok"]) and not self._has_error_diagnostic(value), diagnostics
        return not self._has_error_diagnostic(value), diagnostics

    def _diagnostics_from_value(self, value: dict[str, Any]) -> list[str]:
        raw = value.get("diagnostics") or value.get("errors") or []
        if not isinstance(raw, list):
            raw = [raw]
        diagnostics: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                message = item.get("message") or item.get("text") or item.get("data") or repr(item)
                severity = item.get("severity")
                diagnostics.append(f"{severity}: {message}" if severity else str(message))
            else:
                diagnostics.append(str(item))
        return diagnostics

    def _has_error_diagnostic(self, value: dict[str, Any]) -> bool:
        raw = value.get("diagnostics") or value.get("errors") or []
        if not isinstance(raw, list):
            raw = [raw]
        for item in raw:
            if isinstance(item, dict):
                severity = str(item.get("severity") or "").lower()
                if severity in {"error", "errors"}:
                    return True
            elif item:
                return True
        return False

    def _excerpt(self, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) <= _MAX_RAW_EXCERPT_CHARS:
            return value
        return value[:_MAX_RAW_EXCERPT_CHARS] + "\n...[truncated]"
