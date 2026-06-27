"""Lean MCP Toolkit ingestion for repo-local Mathlib index."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.services.external_clients import ExternalClientService
from lean_constellation.services.foundation import FoundationContext, FoundationService, IssueSeverity, ServiceIssue, ServiceResult
from lean_constellation.services.mathlib.mathlib_index import MathlibDeclEntryView, MathlibIndexComponent

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


class ToolkitIngestionComponent:
    """Convert toolkit search/navigation results into stable MathlibIndex entries."""

    def __init__(
        self,
        foundation: FoundationService,
        external: ExternalClientService,
        mathlib_index: MathlibIndexComponent,
    ) -> None:
        self.foundation = foundation
        self.external = external
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
            return self.foundation.fail(
                self.foundation.issue("mathlib_external_query_empty", "External Mathlib search query must be non-empty.", field="query")
            )
        if limit < 1:
            return self.foundation.fail(
                self.foundation.issue("mathlib_external_limit_invalid", "External Mathlib search limit must be >= 1.", field="limit")
            )
        kinds = [kind.strip() for kind in search_kinds if kind.strip()]
        result = self.external.lean_mcp_toolkit.search_mathlib(normalized_query, kinds, limit)
        if not result.ok:
            return self.foundation.fail(
                self.foundation.issue(
                    result.issue_code or "mathlib_external_search_failed",
                    result.summary,
                    details={"query": normalized_query},
                )
            )
        candidates = [self._candidate_from_search_item(normalized_query, item, index) for index, item in enumerate(result.items[:limit])]
        cache_result = self._load_candidate_cache(repo_root)
        if not cache_result.ok or cache_result.value is None:
            return self.foundation.fail(cache_result.issues)
        for candidate in candidates:
            cache_result.value.candidates[candidate.candidate_id] = candidate
        saved = self._save_candidate_cache(repo_root, cache_result.value)
        if not saved.ok:
            return self.foundation.fail(saved.issues)
        return self.foundation.ok(
            MathlibExternalSearchView(
                query=normalized_query,
                search_kinds=kinds,
                limit=limit,
                candidates=candidates,
                summary=f"Found {len(candidates)} external Mathlib candidates.",
            )
        )

    def inspect_mathlib_declaration(self, repo_root: Path, *, decl_name: str) -> ServiceResult[MathlibNavigationView]:
        del repo_root
        normalized_name = decl_name.strip()
        if not normalized_name:
            return self.foundation.fail(
                self.foundation.issue("mathlib_decl_name_empty", "Mathlib declaration name must be non-empty.", field="decl_name")
            )
        result = self.external.lean_mcp_toolkit.inspect_mathlib_decl(normalized_name)
        if not result.ok:
            return self.foundation.fail(
                self.foundation.issue(
                    result.issue_code or "mathlib_decl_navigation_failed",
                    result.summary,
                    object_ref=normalized_name,
                )
            )
        kind, signature = self._parse_decl_header(result.code)
        return self.foundation.ok(
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

    def inspect_mathlib_module(self, repo_root: Path, *, module: str) -> ServiceResult[MathlibModuleNavigationView]:
        del repo_root
        normalized_module = module.strip()
        if not normalized_module:
            return self.foundation.fail(
                self.foundation.issue("mathlib_module_name_empty", "Mathlib module name must be non-empty.", field="module")
            )
        result = self.external.lean_mcp_toolkit.inspect_mathlib_module(normalized_module)
        if not result.ok:
            return self.foundation.fail(
                self.foundation.issue(
                    result.issue_code or "mathlib_module_navigation_failed",
                    result.summary,
                    object_ref=normalized_module,
                )
            )
        candidates = [self._candidate_from_module_decl(normalized_module, item, index) for index, item in enumerate(result.declarations)]
        hints = [candidate.name for candidate in candidates if candidate.name]
        return self.foundation.ok(
            MathlibModuleNavigationView(
                module=normalized_module,
                imports=list(result.imports),
                important_decl_hints=hints,
                declarations=candidates,
                summary_excerpt=result.raw_excerpt,
                summary=f"Inspected Mathlib module {normalized_module} with {len(candidates)} declaration hints.",
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
            return self.foundation.fail(
                self.foundation.issue("mathlib_decl_name_empty", "Mathlib declaration name must be non-empty.", field="decl_name")
            )
        code = self._check_code(normalized_module, normalized_decl)
        failures: list[ServiceIssue] = []
        for tool_name in _CHECK_TOOL_NAMES:
            payload = self._check_payload(tool_name, repo_root, normalized_module, normalized_decl, code)
            result = self.external.lean_mcp_toolkit.call_tool(tool_name, payload)
            if not result.ok:
                failures.append(
                    self.foundation.issue(
                        result.issue_code or "mathlib_check_tool_failed",
                        result.summary or f"Toolkit check tool failed: {tool_name}",
                        severity=IssueSeverity.WARNING,
                        details={"tool": tool_name},
                    )
                )
                continue
            passed, diagnostics = self._check_passed_from_value(result.value)
            return self.foundation.ok(
                MathlibCheckView(
                    module=normalized_module,
                    decl_name=normalized_decl,
                    passed=passed,
                    diagnostics=diagnostics,
                    checked_code=code,
                    toolkit_tool=tool_name,
                    summary=(
                        f"Mathlib name check passed for {normalized_decl}."
                        if passed
                        else f"Mathlib name check failed for {normalized_decl}."
                    ),
                )
            )
        return self.foundation.fail(
            self.foundation.issue(
                "mathlib_check_unavailable",
                f"No usable toolkit check tool was available for {normalized_decl}.",
                details={"attempted_tools": ", ".join(_CHECK_TOOL_NAMES), "failures": "; ".join(issue.message for issue in failures)},
            )
        )

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
            return self.foundation.fail(
                self.foundation.issue("mathlib_candidate_summary_empty", "Mathlib candidate ingestion requires a summary.", field="summary")
            )
        cache = self._load_candidate_cache(repo_root)
        if not cache.ok or cache.value is None:
            return self.foundation.fail(cache.issues)
        candidate = cache.value.candidates.get(candidate_id.strip())
        if candidate is None:
            return self.foundation.fail(
                self.foundation.issue(
                    "mathlib_candidate_unknown",
                    f"Mathlib candidate is not in the temporary cache: {candidate_id}",
                    object_ref=candidate_id,
                )
            )
        if not candidate.name:
            return self.foundation.fail(
                self.foundation.issue(
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
                return self.foundation.fail(
                    self.foundation.issue(
                        "mathlib_candidate_module_missing",
                        f"Mathlib candidate has no module and declaration navigation did not recover one: {candidate.name}",
                        object_ref=candidate.name,
                        suggested_action="Inspect the declaration/module again before ingesting this Mathlib candidate.",
                        details=details,
                    )
                )
        check = self.check_mathlib_name(repo_root, module=module, decl_name=candidate.name)
        if not check.ok or check.value is None:
            return self.foundation.fail(check.issues)
        if not check.value.passed:
            return self.foundation.fail(
                self.foundation.issue(
                    "mathlib_candidate_check_failed",
                    f"Mathlib candidate failed name check: {candidate.name}",
                    object_ref=candidate.name,
                    details={"diagnostics": "\n".join(check.value.diagnostics)},
                )
            )
        if module is not None:
            module_result = self.mathlib_index.upsert_mathlib_module_entry(repo_root, module=module)
            if not module_result.ok:
                return self.foundation.fail(module_result.issues)
            important = self.mathlib_index.add_module_important_decl(repo_root, module=module, decl_name=candidate.name)
            if not important.ok:
                return self.foundation.fail(important.issues)
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
        return self.foundation.index_cache_path(FoundationContext(repo_root=Path(repo_root)), "mathlib_candidates")

    def _load_candidate_cache(self, repo_root: Path) -> ServiceResult[MathlibCandidateCache]:
        path = self._candidate_cache_path(repo_root)
        if not path.exists():
            return self.foundation.ok(MathlibCandidateCache())
        return self.foundation.read_json(path, MathlibCandidateCache)

    def _save_candidate_cache(self, repo_root: Path, cache: MathlibCandidateCache) -> ServiceResult[MathlibCandidateCache]:
        cache.updated_at = utc_now_iso()
        write = self.foundation.write_json_atomic(self._candidate_cache_path(repo_root), cache)
        if not write.ok:
            return self.foundation.fail(write.issues)
        return self.foundation.ok(cache)

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
