"""Unified Lean/Lake/toolkit external boundary."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.external_clients.lake_command import LakeCommandClient, LeanCheckSummaryView
from lean_constellation.services.external_clients.lean_mcp_toolkit import (
    LeanDiagnosticsResult,
    LeanMcpToolkitClient,
    MathlibSearchResult,
    ToolkitCallResult,
    ToolkitDeclarationView,
    ToolkitModuleView,
    ToolkitResponseWarning,
)
from lean_constellation.services.external_clients.process import ExternalCommandResult


class LeanToolchainProviderPolicy(StrictModel):
    diagnostics_prefer_toolkit: bool = True
    diagnostics_fallback_to_lake: bool = True
    mathlib_check_prefer_lake_project: bool = True
    mathlib_check_fallback_to_toolkit: bool = True


class LeanToolchainClientConfig(StrictModel):
    provider_policy: LeanToolchainProviderPolicy = Field(default_factory=LeanToolchainProviderPolicy)
    raw_excerpt_chars: int = 12000


class ToolchainCommandView(StrictModel):
    ok: bool
    provider: str = "lake_command"
    fallback_provider: str | None = None
    fallback_reason: str | None = None
    command: list[str] = Field(default_factory=list)
    summary: str
    exit_code: int | None = None
    timed_out: bool = False
    stderr_excerpt: str | None = None
    raw_excerpt: str | None = None
    warnings: list[ToolkitResponseWarning] = Field(default_factory=list)
    issue_code: str | None = None


class ToolchainLeanCheckView(StrictModel):
    ok: bool
    passed: bool | None = None
    provider: str
    toolkit_tool: str | None = None
    fallback_provider: str | None = None
    fallback_reason: str | None = None
    module: str | None = None
    command: list[str] = Field(default_factory=list)
    summary: str
    diagnostics_excerpt: str | None = None
    raw_excerpt: str | None = None
    warnings: list[ToolkitResponseWarning] = Field(default_factory=list)
    issue_code: str | None = None


class ToolchainDiagnosticsView(StrictModel):
    ok: bool
    provider: str
    fallback_provider: str | None = None
    fallback_reason: str | None = None
    repo_root: str
    file_path: str | None = None
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    summary: str
    warnings: list[ToolkitResponseWarning] = Field(default_factory=list)
    raw_excerpt: str | None = None
    issue_code: str | None = None


class ToolchainDeclarationView(StrictModel):
    ok: bool
    provider: str
    fallback_provider: str | None = None
    fallback_reason: str | None = None
    name: str
    code: str | None = None
    module: str | None = None
    kind: str | None = None
    signature: str | None = None
    decl_start_pos: dict[str, Any] | None = None
    decl_end_pos: dict[str, Any] | None = None
    warnings: list[ToolkitResponseWarning] = Field(default_factory=list)
    summary: str
    raw_excerpt: str | None = None
    issue_code: str | None = None


class ToolchainModuleView(StrictModel):
    ok: bool
    provider: str
    fallback_provider: str | None = None
    fallback_reason: str | None = None
    module: str
    imports: list[str] = Field(default_factory=list)
    declarations: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[ToolkitResponseWarning] = Field(default_factory=list)
    summary: str
    raw_excerpt: str | None = None
    issue_code: str | None = None


class ToolchainMathlibSearchView(StrictModel):
    ok: bool
    provider: str
    fallback_provider: str | None = None
    fallback_reason: str | None = None
    query: str
    items: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[ToolkitResponseWarning] = Field(default_factory=list)
    summary: str
    raw_excerpt: str | None = None
    issue_code: str | None = None


class ToolchainToolCallView(StrictModel):
    ok: bool
    provider: str = "lean_mcp_toolkit"
    fallback_provider: str | None = None
    fallback_reason: str | None = None
    toolkit_tool: str
    payload: dict[str, Any] = Field(default_factory=dict)
    value: dict[str, Any] | list[Any] | str | None = None
    warnings: list[ToolkitResponseWarning] = Field(default_factory=list)
    summary: str | None = None
    raw_excerpt: str | None = None
    issue_code: str | None = None


class ToolchainPolicyOccurrenceView(StrictModel):
    kind: Literal["sorry", "admit", "axiom", "opaque", "unsafe"]
    line: int
    column: int
    excerpt: str


class ToolchainPolicyScanView(StrictModel):
    ok: bool
    provider: str = "local_policy_scan"
    fallback_provider: str | None = None
    fallback_reason: str | None = None
    contains_sorry: bool
    contains_admit: bool
    contains_axiom: bool
    contains_opaque: bool
    contains_unsafe: bool
    sorry_count: int
    admit_count: int
    axiom_count: int
    opaque_count: int
    unsafe_count: int
    occurrences: list[ToolchainPolicyOccurrenceView] = Field(default_factory=list)
    summary: str
    limitation: str
    warnings: list[ToolkitResponseWarning] = Field(default_factory=list)
    raw_excerpt: str | None = None
    issue_code: str | None = None


class LeanToolchainClient:
    """Semantic facade over Lake commands and Lean MCP Toolkit tools."""

    _FORBIDDEN_WORD_RE = re.compile(r"(?<![A-Za-z0-9_'])(sorry|admit|axiom|opaque|unsafe)(?![A-Za-z0-9_'])")

    def __init__(
        self,
        *,
        lake: LakeCommandClient,
        toolkit: LeanMcpToolkitClient,
        config: LeanToolchainClientConfig | None = None,
    ) -> None:
        self.lake = lake
        self.toolkit = toolkit
        self.config = config or LeanToolchainClientConfig()

    def run_lake_update(self, repo_root: Path, *, timeout_seconds: int | None = None) -> ToolchainCommandView:
        try:
            result = self.lake.run_lake_update(Path(repo_root), timeout_seconds=timeout_seconds)
        except TypeError:
            result = self.lake.run_lake_update(Path(repo_root))
        return self._command_view(result)

    def run_lake_build(
        self,
        repo_root: Path,
        *,
        target: str | None = None,
        targets: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> ToolchainCommandView:
        try:
            result = self.lake.run_lake_build(Path(repo_root), target=target, targets=targets, timeout_seconds=timeout_seconds)
        except TypeError:
            result = self.lake.run_lake_build(Path(repo_root), target=target)
        return self._command_view(result)

    def run_minimal_import_check(self, repo_root: Path, module: str, *, timeout_seconds: int | None = None) -> ToolchainLeanCheckView:
        try:
            result = self.lake.run_minimal_import_check(Path(repo_root), module, timeout_seconds=timeout_seconds)
        except TypeError:
            result = self.lake.run_minimal_import_check(Path(repo_root), module)
        return self._lean_check_view(result, provider="lake_command")

    def run_snippet_check(
        self,
        repo_root: Path,
        *,
        imports: list[str],
        code: str,
        timeout_seconds: int | None = None,
    ) -> ToolchainLeanCheckView:
        try:
            result = self.lake.run_snippet_check(repo_root=Path(repo_root), imports=imports, code=code, timeout_seconds=timeout_seconds)
        except TypeError:
            result = self.lake.run_snippet_check(repo_root=Path(repo_root), imports=imports, code=code)
        return self._lean_check_view(result, provider="lake_command")

    def run_file_diagnostics(
        self,
        repo_root: Path,
        file_path: Path | str,
        *,
        rel_file: str | None = None,
        timeout_seconds: int | None = None,
    ) -> ToolchainDiagnosticsView:
        repo = Path(repo_root)
        target = Path(file_path)
        toolkit_result: LeanDiagnosticsResult | None = None
        policy = self.config.provider_policy
        if policy.diagnostics_prefer_toolkit:
            toolkit_result = self._run_toolkit_diagnostics(repo, target)
            if toolkit_result.ok:
                return ToolchainDiagnosticsView(
                    ok=True,
                    provider="lean_mcp_toolkit",
                    repo_root=toolkit_result.repo_root,
                    file_path=toolkit_result.file_path,
                    diagnostics=list(toolkit_result.diagnostics),
                    warnings=list(toolkit_result.warnings),
                    summary=toolkit_result.summary,
                    raw_excerpt=toolkit_result.raw_excerpt,
                    issue_code=toolkit_result.issue_code,
                )
            if not policy.diagnostics_fallback_to_lake:
                return ToolchainDiagnosticsView(
                    ok=False,
                    provider="lean_mcp_toolkit",
                    repo_root=str(repo),
                    file_path=str(target),
                    summary=toolkit_result.summary,
                    warnings=list(toolkit_result.warnings),
                    raw_excerpt=toolkit_result.raw_excerpt,
                    issue_code=toolkit_result.issue_code,
                )

        rel = rel_file or self._relative_file(repo, target)
        fallback = self.lake.run_lake_env_lean(repo_root=repo, rel_file=rel, json=True, timeout_seconds=timeout_seconds)
        diagnostics = self._diagnostics_from_command_output(fallback.stdout_excerpt, fallback.stderr_excerpt)
        if not diagnostics and not fallback.ok:
            diagnostics = [{"severity": "error", "message": fallback.summary or "Lean diagnostics command failed."}]
        fallback_reason = None
        warnings: list[ToolkitResponseWarning] = []
        if toolkit_result is not None:
            fallback_reason = toolkit_result.issue_code or toolkit_result.summary
            warnings.extend(toolkit_result.warnings)
        return ToolchainDiagnosticsView(
            ok=fallback.ok or bool(diagnostics),
            provider="lake_command",
            fallback_provider="lean_mcp_toolkit" if toolkit_result is not None else None,
            fallback_reason=fallback_reason,
            repo_root=str(repo),
            file_path=str(target),
            diagnostics=diagnostics,
            warnings=warnings,
            summary=fallback.summary or ("Lean diagnostics passed." if fallback.ok else "Lean diagnostics failed."),
            raw_excerpt=fallback.stderr_excerpt or fallback.stdout_excerpt,
            issue_code=None if fallback.ok or diagnostics else fallback.issue_code,
        )

    def extract_declaration(self, repo_root: Path, target: str, decl_name: str) -> ToolchainDeclarationView:
        result = self.toolkit.extract_declaration(Path(repo_root), target, decl_name)
        if result.ok:
            return self._declaration_view(result, provider="lean_mcp_toolkit")
        fallback = self._extract_declaration_local(Path(repo_root), target, decl_name)
        if fallback.ok:
            return fallback.model_copy(
                update={
                    "fallback_provider": "lean_mcp_toolkit",
                    "fallback_reason": result.issue_code or result.summary,
                }
            )
        return self._declaration_view(result, provider="lean_mcp_toolkit")

    def list_repo_tree(
        self,
        repo_root: Path,
        *,
        module_prefix: str | None = None,
        name_filter: str | None = None,
        depth: int = 8,
        limit: int = 100,
    ) -> ToolchainToolCallView:
        payload: dict[str, Any] = {"repo_root": str(repo_root), "depth": depth, "limit": limit}
        if module_prefix:
            payload["module_prefix"] = module_prefix
        if name_filter:
            payload["name_filter"] = name_filter
        result = self._tool_call_view(self.toolkit.call_tool("repo_nav.tree", payload))
        if result.ok:
            return result
        fallback = self._list_repo_tree_local(
            Path(repo_root),
            module_prefix=module_prefix,
            name_filter=name_filter,
            depth=depth,
            limit=limit,
        )
        if fallback.ok:
            return fallback.model_copy(
                update={
                    "fallback_provider": "lean_mcp_toolkit",
                    "fallback_reason": result.issue_code or result.summary,
                }
            )
        return result

    def outline_repo_file(
        self,
        repo_root: Path,
        target: str,
        *,
        include_imports: bool = True,
        include_module_doc: bool = True,
        include_section_doc: bool = True,
        include_decl_headers: bool = True,
        include_scope_cmds: bool = True,
        limit_decls: int = 300,
    ) -> ToolchainToolCallView:
        result = self._tool_call_view(
            self.toolkit.call_tool(
                "repo_nav.file_outline",
                {
                    "repo_root": str(repo_root),
                    "target": target,
                    "include_imports": include_imports,
                    "include_module_doc": include_module_doc,
                    "include_section_doc": include_section_doc,
                    "include_decl_headers": include_decl_headers,
                    "include_scope_cmds": include_scope_cmds,
                    "limit_decls": limit_decls,
                },
            )
        )
        if result.ok:
            return result
        fallback = self._outline_repo_file_local(
            Path(repo_root),
            target,
            include_imports=include_imports,
            include_decl_headers=include_decl_headers,
            include_scope_cmds=include_scope_cmds,
            limit_decls=limit_decls,
        )
        if fallback.ok:
            return fallback.model_copy(
                update={
                    "fallback_provider": "lean_mcp_toolkit",
                    "fallback_reason": result.issue_code or result.summary,
                }
            )
        return result

    def grep_repo(
        self,
        repo_root: Path,
        pattern: str,
        *,
        module_prefix: str | None = None,
        limit: int = 50,
    ) -> ToolchainToolCallView:
        payload: dict[str, Any] = {"repo_root": str(repo_root), "pattern": pattern, "limit": limit}
        if module_prefix:
            payload["module_prefix"] = module_prefix
        return self._tool_call_view(self.toolkit.call_tool("repo_nav.grep", payload))

    def read_repo_source_window(
        self,
        repo_root: Path,
        target: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        max_lines: int | None = None,
        with_line_numbers: bool = True,
    ) -> ToolchainToolCallView:
        result = self._tool_call_view(
            self.toolkit.call_tool(
                "repo_nav.read",
                {
                    "repo_root": str(repo_root),
                    "target": target,
                    "start_line": start_line,
                    "end_line": end_line,
                    "max_lines": max_lines,
                    "with_line_numbers": with_line_numbers,
                },
            )
        )
        if result.ok:
            return result
        fallback = self._read_repo_source_window_local(
            Path(repo_root),
            target,
            start_line=start_line,
            end_line=end_line,
            max_lines=max_lines,
            with_line_numbers=with_line_numbers,
        )
        if fallback.ok:
            return fallback.model_copy(
                update={
                    "fallback_provider": "lean_mcp_toolkit",
                    "fallback_reason": result.issue_code or result.summary,
                }
            )
        return result

    def find_repo_declarations(
        self,
        repo_root: Path,
        *,
        query: str,
        match_mode: str = "contains",
        decl_kinds: list[str] | None = None,
        module_filter: str | None = None,
        include_deps: bool = False,
        limit: int = 20,
    ) -> ToolchainToolCallView:
        result = self._tool_call_view(
            self.toolkit.call_tool(
                "repo_nav.local_decl.find",
                {
                    "repo_root": str(repo_root),
                    "query": query,
                    "match_mode": match_mode,
                    "decl_kinds": decl_kinds,
                    "module_filter": module_filter,
                    "include_deps": include_deps,
                    "limit": limit,
                },
            )
        )
        if result.ok:
            return result
        fallback = self._find_repo_declarations_local(
            Path(repo_root),
            query=query,
            match_mode=match_mode,
            decl_kinds=decl_kinds,
            module_filter=module_filter,
            limit=limit,
        )
        if fallback.ok:
            return fallback.model_copy(
                update={
                    "fallback_provider": "lean_mcp_toolkit",
                    "fallback_reason": result.issue_code or result.summary,
                }
            )
        return result

    def find_repo_declaration(self, repo_root: Path, decl_name: str, *, module: str | None = None) -> ToolchainToolCallView:
        return self.find_repo_declarations(repo_root, query=decl_name, match_mode="exact", module_filter=module, limit=5)

    def search_mathlib_declarations(self, query: str, *, kinds: list[str] | None = None, limit: int = 20) -> ToolchainMathlibSearchView:
        result = self.toolkit.search_mathlib(query, kinds, limit)
        return self._mathlib_search_view(result, provider="lean_mcp_toolkit")

    def inspect_mathlib_declaration(self, decl_name: str) -> ToolchainDeclarationView:
        result = self.toolkit.inspect_mathlib_decl(decl_name)
        return self._declaration_view(result, provider="lean_mcp_toolkit")

    def inspect_mathlib_module(self, module: str) -> ToolchainModuleView:
        result = self.toolkit.inspect_mathlib_module(module)
        return self._module_view(result, provider="lean_mcp_toolkit")

    def check_mathlib_name(
        self,
        repo_root: Path,
        *,
        module: str | None,
        decl_name: str,
        timeout_seconds: int | None = None,
    ) -> ToolchainLeanCheckView:
        code = self._check_code(module, decl_name)
        if self.config.provider_policy.mathlib_check_prefer_lake_project and self._looks_like_lake_project(Path(repo_root)):
            checked = self.run_snippet_check(repo_root, imports=[module] if module else [], code=f"#check {decl_name}", timeout_seconds=timeout_seconds)
            if checked.ok or not self._should_fallback_mathlib_lake_check(checked):
                return checked.model_copy(update={"ok": True, "passed": checked.ok})
            fallback = self._check_mathlib_name_with_toolkit(Path(repo_root), module=module, decl_name=decl_name, code=code)
            return fallback.model_copy(
                update={
                    "fallback_provider": "lake_command",
                    "fallback_reason": checked.issue_code or checked.summary,
                }
            )
        return self._check_mathlib_name_with_toolkit(Path(repo_root), module=module, decl_name=decl_name, code=code)

    def check_mathlib_module(self, repo_root: Path, *, module: str, timeout_seconds: int | None = None) -> ToolchainLeanCheckView:
        code = "#check True"
        if self.config.provider_policy.mathlib_check_prefer_lake_project and self._looks_like_lake_project(Path(repo_root)):
            checked = self.run_snippet_check(repo_root, imports=[module], code=code, timeout_seconds=timeout_seconds)
            if checked.ok or not self._should_fallback_mathlib_lake_check(checked):
                return checked.model_copy(update={"ok": True, "passed": checked.ok})
            fallback = self._check_snippet_with_toolkit(Path(repo_root), code=f"import {module}\n{code}\n", attempted_tools=("lsp.run_snippet", "run_snippet"))
            return fallback.model_copy(update={"module": module, "fallback_provider": "lake_command", "fallback_reason": checked.issue_code or checked.summary})
        return self._check_snippet_with_toolkit(Path(repo_root), code=f"import {module}\n{code}\n", attempted_tools=("lsp.run_snippet", "run_snippet")).model_copy(
            update={"module": module}
        )

    def scan_sorry_axiom(self, file_text: str) -> ToolchainPolicyScanView:
        sanitized = self._strip_comments_and_strings(file_text)
        occurrences: list[ToolchainPolicyOccurrenceView] = []
        lines = file_text.splitlines() or [""]
        for match in self._FORBIDDEN_WORD_RE.finditer(sanitized):
            kind = match.group(1)
            line, column = self._line_col(sanitized, match.start())
            source_line = lines[line - 1] if line - 1 < len(lines) else ""
            occurrences.append(
                ToolchainPolicyOccurrenceView(
                    kind=kind,  # type: ignore[arg-type]
                    line=line,
                    column=column,
                    excerpt=source_line.strip()[:240],
                )
            )
        counts = {kind: sum(1 for occurrence in occurrences if occurrence.kind == kind) for kind in ["sorry", "admit", "axiom", "opaque", "unsafe"]}
        summary = ", ".join(f"{kind}={counts[kind]}" for kind in ["sorry", "admit", "axiom", "opaque", "unsafe"])
        return ToolchainPolicyScanView(
            ok=not any(counts.values()),
            contains_sorry=counts["sorry"] > 0,
            contains_admit=counts["admit"] > 0,
            contains_axiom=counts["axiom"] > 0,
            contains_opaque=counts["opaque"] > 0,
            contains_unsafe=counts["unsafe"] > 0,
            sorry_count=counts["sorry"],
            admit_count=counts["admit"],
            axiom_count=counts["axiom"],
            opaque_count=counts["opaque"],
            unsafe_count=counts["unsafe"],
            occurrences=occurrences,
            summary=summary,
            limitation="Text scan ignores Lean comments and string literals with a conservative first-round lexer; it is not a full Lean parser.",
        )

    def _command_view(self, result: ExternalCommandResult) -> ToolchainCommandView:
        return ToolchainCommandView(
            ok=result.ok,
            command=list(getattr(result, "command", [])),
            summary=result.summary or ("Command succeeded" if result.ok else "Command failed"),
            exit_code=getattr(result, "exit_code", None),
            timed_out=bool(getattr(result, "timed_out", False)),
            stderr_excerpt=getattr(result, "stderr_excerpt", None),
            raw_excerpt=getattr(result, "stderr_excerpt", None) or getattr(result, "stdout_excerpt", None),
            issue_code=getattr(result, "issue_code", None),
        )

    def _lean_check_view(self, result: LeanCheckSummaryView, *, provider: str) -> ToolchainLeanCheckView:
        return ToolchainLeanCheckView(
            ok=result.ok,
            passed=result.ok,
            provider=provider,
            module=result.module,
            command=list(result.command),
            summary=result.summary,
            diagnostics_excerpt=result.diagnostics_excerpt,
            raw_excerpt=result.diagnostics_excerpt,
            issue_code=result.issue_code,
        )

    def _run_toolkit_diagnostics(self, repo_root: Path, file_path: Path) -> LeanDiagnosticsResult:
        try:
            return self.toolkit.run_file_diagnostics(repo_root, file_path)
        except Exception as exc:  # noqa: BLE001 - external provider boundary.
            return LeanDiagnosticsResult(
                ok=False,
                repo_root=str(repo_root),
                file_path=str(file_path),
                summary=f"Toolkit diagnostics failed: {exc}",
                issue_code="toolkit_call_failed",
            )

    def _declaration_view(self, result: ToolkitDeclarationView, *, provider: str) -> ToolchainDeclarationView:
        return ToolchainDeclarationView(
            ok=result.ok,
            provider=provider,
            name=result.name,
            code=result.code,
            module=result.module,
            decl_start_pos=result.decl_start_pos,
            decl_end_pos=result.decl_end_pos,
            warnings=list(result.warnings),
            summary=result.summary,
            raw_excerpt=result.raw_excerpt,
            issue_code=result.issue_code,
        )

    def _module_view(self, result: ToolkitModuleView, *, provider: str) -> ToolchainModuleView:
        return ToolchainModuleView(
            ok=result.ok,
            provider=provider,
            module=result.module,
            imports=list(result.imports),
            declarations=list(result.declarations),
            warnings=list(result.warnings),
            summary=result.summary,
            raw_excerpt=result.raw_excerpt,
            issue_code=result.issue_code,
        )

    def _mathlib_search_view(self, result: MathlibSearchResult, *, provider: str) -> ToolchainMathlibSearchView:
        return ToolchainMathlibSearchView(
            ok=result.ok,
            provider=provider,
            query=result.query,
            items=list(result.items),
            warnings=list(result.warnings),
            summary=result.summary,
            raw_excerpt=result.raw_excerpt,
            issue_code=result.issue_code,
        )

    def _tool_call_view(self, result: ToolkitCallResult) -> ToolchainToolCallView:
        return ToolchainToolCallView(
            ok=result.ok,
            toolkit_tool=result.toolkit_tool,
            payload=dict(result.payload),
            value=result.value,
            warnings=list(result.warnings),
            summary=result.summary,
            raw_excerpt=result.raw_excerpt,
            issue_code=result.issue_code,
        )

    def _check_mathlib_name_with_toolkit(self, repo_root: Path, *, module: str | None, decl_name: str, code: str) -> ToolchainLeanCheckView:
        failures: list[str] = []
        for tool_name in ("check_mathlib_name", "lsp.run_snippet", "run_snippet"):
            payload = (
                {"repo_root": str(repo_root), "module": module, "decl_name": decl_name, "code": code}
                if tool_name == "check_mathlib_name"
                else {"repo_root": str(repo_root), "code": code, "include_diagnostics": True}
            )
            result = self.toolkit.call_tool(tool_name, payload)
            if not result.ok:
                failures.append(result.summary or result.issue_code or f"{tool_name} failed")
                continue
            passed, diagnostics = self._check_passed_from_value(result.value)
            return ToolchainLeanCheckView(
                ok=True,
                passed=passed,
                provider="lean_mcp_toolkit",
                toolkit_tool=tool_name,
                module=module,
                command=[],
                summary=f"Mathlib name check {'passed' if passed else 'failed'} for {decl_name}.",
                diagnostics_excerpt="\n".join(diagnostics) if diagnostics else None,
                raw_excerpt=result.raw_excerpt,
                warnings=list(result.warnings),
                issue_code=None if passed else "mathlib_check_failed",
            )
        return ToolchainLeanCheckView(
            ok=False,
            provider="lean_mcp_toolkit",
            module=module,
            command=[],
            summary=f"No usable toolkit check tool was available for {decl_name}.",
            diagnostics_excerpt="; ".join(failures) or None,
            issue_code="mathlib_check_unavailable",
        )

    def _check_snippet_with_toolkit(self, repo_root: Path, *, code: str, attempted_tools: tuple[str, ...]) -> ToolchainLeanCheckView:
        failures: list[str] = []
        for tool_name in attempted_tools:
            result = self.toolkit.call_tool(tool_name, {"repo_root": str(repo_root), "code": code, "include_diagnostics": True})
            if not result.ok:
                failures.append(result.summary or result.issue_code or f"{tool_name} failed")
                continue
            passed, diagnostics = self._check_passed_from_value(result.value)
            return ToolchainLeanCheckView(
                ok=True,
                passed=passed,
                provider="lean_mcp_toolkit",
                toolkit_tool=tool_name,
                command=[],
                summary="Snippet check passed." if passed else "Snippet check failed.",
                diagnostics_excerpt="\n".join(diagnostics) if diagnostics else None,
                raw_excerpt=result.raw_excerpt,
                warnings=list(result.warnings),
                issue_code=None if passed else "snippet_check_failed",
            )
        return ToolchainLeanCheckView(
            ok=False,
            provider="lean_mcp_toolkit",
            command=[],
            summary="No usable toolkit snippet check tool was available.",
            diagnostics_excerpt="; ".join(failures) or None,
            issue_code="snippet_check_unavailable",
        )

    def _check_passed_from_value(self, value: dict[str, Any] | list[Any] | str | None) -> tuple[bool, list[str]]:
        if not isinstance(value, dict):
            return True, []
        diagnostics = self._diagnostics_strings_from_value(value)
        if "passed" in value:
            return bool(value["passed"]), diagnostics
        if "success" in value:
            return bool(value["success"]), diagnostics
        if "ok" in value:
            return bool(value["ok"]) and not self._has_error_diagnostic(value), diagnostics
        return not self._has_error_diagnostic(value), diagnostics

    def _should_fallback_mathlib_lake_check(self, checked: ToolchainLeanCheckView) -> bool:
        if not self.config.provider_policy.mathlib_check_fallback_to_toolkit:
            return False
        if checked.diagnostics_excerpt:
            return False
        return checked.issue_code in {"command_start_failed", "command_timeout", "missing_repo_root", None}

    def _diagnostics_strings_from_value(self, value: dict[str, Any]) -> list[str]:
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
                if severity in {"error", "errors", "fatal"}:
                    return True
            elif item:
                return True
        return False

    def _diagnostics_from_command_output(self, stdout: str | None, stderr: str | None) -> list[dict[str, Any]]:
        diagnostics: list[dict[str, Any]] = []
        for text in [stdout, stderr]:
            if not text:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    diagnostics.append(payload)
        if diagnostics:
            return diagnostics
        fallback_text = (stderr or stdout or "").strip()
        if fallback_text:
            return [{"severity": "error", "message": fallback_text[:1000]}]
        return []

    def _relative_file(self, repo_root: Path, file_path: Path) -> str:
        target = file_path if file_path.is_absolute() else repo_root / file_path
        try:
            return target.resolve(strict=False).relative_to(repo_root.resolve(strict=False)).as_posix()
        except ValueError:
            return file_path.as_posix()

    def _list_repo_tree_local(
        self,
        repo_root: Path,
        *,
        module_prefix: str | None = None,
        name_filter: str | None = None,
        depth: int,
        limit: int,
    ) -> ToolchainToolCallView:
        modules = []
        normalized_filter = (name_filter or "").strip().lower()
        for path in self._iter_lean_files(repo_root):
            module = self._module_from_file(repo_root, path)
            if module_prefix and not module.startswith(module_prefix):
                continue
            rel_depth = len(path.relative_to(repo_root).parts)
            if rel_depth > depth:
                continue
            if (
                normalized_filter
                and normalized_filter not in module.lower()
                and normalized_filter not in path.name.lower()
            ):
                continue
            modules.append(
                {
                    "module": module,
                    "module_name": module,
                    "relative_path": path.relative_to(repo_root).as_posix(),
                    "kind": "lean_file",
                }
            )
            if len(modules) >= limit:
                break
        return ToolchainToolCallView(
            ok=True,
            provider="local_repo_fallback",
            toolkit_tool="repo_nav.tree",
            payload={
                "repo_root": str(repo_root),
                "module_prefix": module_prefix,
                "name_filter": name_filter,
                "depth": depth,
                "limit": limit,
            },
            value={"items": modules},
            summary=f"Local repo fallback listed {len(modules)} Lean modules.",
        )

    def _outline_repo_file_local(
        self,
        repo_root: Path,
        target: str,
        *,
        include_imports: bool,
        include_decl_headers: bool,
        include_scope_cmds: bool,
        limit_decls: int,
    ) -> ToolchainToolCallView:
        path = self._resolve_repo_module_or_file(repo_root, target)
        if path is None:
            return ToolchainToolCallView(
                ok=False,
                provider="local_repo_fallback",
                toolkit_tool="repo_nav.file_outline",
                payload={"repo_root": str(repo_root), "target": target},
                summary=f"Cannot resolve Lean module or file: {target}.",
                issue_code="repo_nav_target_missing",
            )
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        declarations = self._scan_local_declarations(lines, limit=limit_decls) if include_decl_headers else []
        imports = self._scan_local_imports(lines) if include_imports else []
        scope_cmds = self._scan_local_scope_cmds(lines) if include_scope_cmds else []
        return ToolchainToolCallView(
            ok=True,
            provider="local_repo_fallback",
            toolkit_tool="repo_nav.file_outline",
            payload={"repo_root": str(repo_root), "target": target},
            value={
                "module": self._module_from_file(repo_root, path),
                "relative_path": path.relative_to(repo_root).as_posix(),
                "imports": imports,
                "declarations": declarations,
                "scope_cmds": scope_cmds,
            },
            summary=f"Local repo fallback outlined {len(declarations)} declarations from {target}.",
        )

    def _read_repo_source_window_local(
        self,
        repo_root: Path,
        target: str,
        *,
        start_line: int | None,
        end_line: int | None,
        max_lines: int | None,
        with_line_numbers: bool,
    ) -> ToolchainToolCallView:
        path = self._resolve_repo_module_or_file(repo_root, target)
        if path is None:
            return ToolchainToolCallView(
                ok=False,
                provider="local_repo_fallback",
                toolkit_tool="repo_nav.read",
                payload={"repo_root": str(repo_root), "target": target},
                summary=f"Cannot resolve Lean module or file: {target}.",
                issue_code="repo_nav_target_missing",
            )
        lines = path.read_text(encoding="utf-8").splitlines()
        first = max(1, start_line or 1)
        last = min(len(lines), end_line or (first + (max_lines or len(lines)) - 1))
        selected = lines[first - 1 : last]
        if with_line_numbers:
            text = "\n".join(f"{line_no}: {line}" for line_no, line in enumerate(selected, start=first))
        else:
            text = "\n".join(selected)
        return ToolchainToolCallView(
            ok=True,
            provider="local_repo_fallback",
            toolkit_tool="repo_nav.read",
            payload={
                "repo_root": str(repo_root),
                "target": target,
                "start_line": start_line,
                "end_line": end_line,
                "max_lines": max_lines,
            },
            value={"text": text, "start_line": first, "end_line": last},
            summary=f"Local repo fallback read {len(selected)} lines from {target}.",
        )

    def _find_repo_declarations_local(
        self,
        repo_root: Path,
        *,
        query: str,
        match_mode: str,
        decl_kinds: list[str] | None,
        module_filter: str | None,
        limit: int,
    ) -> ToolchainToolCallView:
        normalized_query = query.strip().lower()
        normalized_kinds = {kind.strip() for kind in decl_kinds or [] if kind and kind.strip()}
        matches = []
        for path in self._iter_lean_files(repo_root):
            module = self._module_from_file(repo_root, path)
            if module_filter and module_filter != module:
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            for item in self._scan_local_declarations(lines, limit=1000):
                if normalized_kinds and item.get("kind") not in normalized_kinds:
                    continue
                haystack = " ".join(
                    str(item.get(key) or "")
                    for key in ("name", "full_name", "signature", "header_preview")
                ).lower()
                name = str(item.get("name") or "").lower()
                if match_mode == "exact":
                    matched = normalized_query == name or normalized_query == f"{module}.{name}".lower()
                else:
                    matched = normalized_query in haystack
                if not matched:
                    continue
                matches.append(
                    {
                        **item,
                        "module": module,
                        "module_name": module,
                        "match_reason": "local_repo_fallback",
                    }
                )
                if len(matches) >= limit:
                    return self._local_tool_call(
                        "repo_nav.local_decl.find",
                        repo_root,
                        {"results": matches},
                        f"Local repo fallback found {len(matches)} declarations.",
                    )
        return self._local_tool_call(
            "repo_nav.local_decl.find",
            repo_root,
            {"results": matches},
            f"Local repo fallback found {len(matches)} declarations.",
        )

    def _extract_declaration_local(
        self,
        repo_root: Path,
        target: str,
        decl_name: str,
    ) -> ToolchainDeclarationView:
        path = self._resolve_repo_module_or_file(repo_root, target)
        if path is None:
            return ToolchainDeclarationView(
                ok=False,
                provider="local_repo_fallback",
                name=decl_name,
                summary=f"Cannot resolve Lean module or file: {target}.",
                issue_code="repo_nav_target_missing",
            )
        lines = path.read_text(encoding="utf-8").splitlines()
        declarations = self._scan_local_declarations(lines, limit=1000)
        short_name = decl_name.rsplit(".", 1)[-1]
        item = next((decl for decl in declarations if decl.get("name") in {decl_name, short_name}), None)
        if item is None:
            return ToolchainDeclarationView(
                ok=False,
                provider="local_repo_fallback",
                name=decl_name,
                module=self._module_from_file(repo_root, path),
                summary=f"Declaration {decl_name} was not found in {target}.",
                issue_code="declaration_not_found",
            )
        start = int(item["line_start"])
        end = int(item["line_end"])
        code = "\n".join(lines[start - 1 : end])
        return ToolchainDeclarationView(
            ok=True,
            provider="local_repo_fallback",
            name=decl_name,
            code=code,
            module=self._module_from_file(repo_root, path),
            kind=str(item.get("kind") or ""),
            signature=str(item.get("signature") or ""),
            summary=f"Local repo fallback extracted {decl_name}.",
        )

    def _local_tool_call(
        self,
        tool_name: str,
        repo_root: Path,
        value: dict[str, Any],
        summary: str,
    ) -> ToolchainToolCallView:
        return ToolchainToolCallView(
            ok=True,
            provider="local_repo_fallback",
            toolkit_tool=tool_name,
            payload={"repo_root": str(repo_root)},
            value=value,
            summary=summary,
        )

    def _iter_lean_files(self, repo_root: Path) -> list[Path]:
        return sorted(
            path
            for path in repo_root.rglob("*.lean")
            if ".lake" not in path.relative_to(repo_root).parts
        )

    def _resolve_repo_module_or_file(self, repo_root: Path, target: str) -> Path | None:
        raw = target.strip()
        direct = Path(raw)
        candidates = []
        if direct.suffix == ".lean":
            candidates.append(direct if direct.is_absolute() else repo_root / direct)
        candidates.append(repo_root / f"{raw.replace('.', '/')}.lean")
        candidates.append(repo_root / f"{raw}.lean")
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def _module_from_file(self, repo_root: Path, path: Path) -> str:
        rel = path.relative_to(repo_root).with_suffix("")
        return ".".join(rel.parts)

    def _scan_local_imports(self, lines: list[str]) -> list[str]:
        imports = []
        for line in lines:
            match = re.match(r"^\s*import\s+(.+?)\s*$", line)
            if match:
                imports.extend(part for part in match.group(1).split() if part)
        return imports

    def _scan_local_scope_cmds(self, lines: list[str]) -> list[dict[str, Any]]:
        scopes = []
        for index, line in enumerate(lines, start=1):
            match = re.match(r"^\s*(namespace|section|open)\s+(.+?)\s*$", line)
            if match:
                scopes.append({"kind": match.group(1), "target": match.group(2).strip(), "line": index})
        return scopes

    def _scan_local_declarations(self, lines: list[str], *, limit: int) -> list[dict[str, Any]]:
        decl_starts: list[tuple[int, re.Match[str]]] = []
        pattern = re.compile(
            r"^\s*(theorem|lemma|def|instance|axiom|inductive|structure|class)\s+([A-Za-z0-9_'.]+)"
        )
        for index, line in enumerate(lines, start=1):
            match = pattern.match(line)
            if match:
                decl_starts.append((index, match))
        declarations = []
        for position, (start, match) in enumerate(decl_starts[:limit]):
            next_start = (
                decl_starts[position + 1][0]
                if position + 1 < len(decl_starts)
                else len(lines) + 1
            )
            end = max(start, next_start - 1)
            header = lines[start - 1].strip()
            signature = header.split(":=", 1)[0].strip()
            declarations.append(
                {
                    "name": match.group(2),
                    "full_name": match.group(2),
                    "kind": match.group(1),
                    "signature": signature,
                    "header_preview": header,
                    "line_start": start,
                    "line_end": end,
                }
            )
        return declarations

    def _looks_like_lake_project(self, repo_root: Path) -> bool:
        return (repo_root / "lakefile.toml").is_file() or (repo_root / "lakefile.lean").is_file()

    def _check_code(self, module: str | None, decl_name: str) -> str:
        imports = f"import {module}\n" if module else ""
        return f"{imports}#check {decl_name}\n"

    def _strip_comments_and_strings(self, text: str) -> str:
        chars = list(text)
        i = 0
        block_depth = 0
        in_string = False
        while i < len(chars):
            current = chars[i]
            nxt = chars[i + 1] if i + 1 < len(chars) else ""
            if in_string:
                if current != "\n":
                    chars[i] = " "
                if current == "\\" and i + 1 < len(chars):
                    if chars[i + 1] != "\n":
                        chars[i + 1] = " "
                    i += 2
                    continue
                if current == '"':
                    in_string = False
                i += 1
                continue
            if block_depth:
                if current == "/" and nxt == "-":
                    chars[i] = chars[i + 1] = " "
                    block_depth += 1
                    i += 2
                    continue
                if current == "-" and nxt == "/":
                    chars[i] = chars[i + 1] = " "
                    block_depth -= 1
                    i += 2
                    continue
                if current != "\n":
                    chars[i] = " "
                i += 1
                continue
            if current == "-" and nxt == "-":
                chars[i] = chars[i + 1] = " "
                i += 2
                while i < len(chars) and chars[i] != "\n":
                    chars[i] = " "
                    i += 1
                continue
            if current == "/" and nxt == "-":
                chars[i] = chars[i + 1] = " "
                block_depth = 1
                i += 2
                continue
            if current == '"':
                chars[i] = " "
                in_string = True
                i += 1
                continue
            i += 1
        return "".join(chars)

    def _line_col(self, text: str, offset: int) -> tuple[int, int]:
        line = text.count("\n", 0, offset) + 1
        last_newline = text.rfind("\n", 0, offset)
        column = offset + 1 if last_newline < 0 else offset - last_newline
        return line, column
