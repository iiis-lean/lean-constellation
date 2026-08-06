"""Lean MCP Toolkit HTTP wrapper."""

from __future__ import annotations

import gzip
import io
import json
import re
import socket
import tarfile
import zipfile
from collections.abc import Callable
from json import JSONDecodeError
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

from pydantic import Field

from lean_constellation.domain.common import StrictModel


ToolkitDispatcher = Callable[[str, dict[str, Any]], Any]


class ToolkitTimeoutError(TimeoutError):
    """Internal timeout marker for external toolkit boundaries."""


class ToolkitMalformedResponseError(ValueError):
    """Internal malformed-response marker with a bounded excerpt."""

    def __init__(self, message: str, raw_text: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text


class LeanMcpToolkitClientConfig(StrictModel):
    base_url: str | None = None
    api_prefix: str = "/api/v1"
    auth_token: str | None = None
    timeout_seconds: int = 120
    enabled_groups: list[str] = Field(default_factory=list)
    response_excerpt_chars: int = 12000


class ToolkitResponseWarning(StrictModel):
    code: str
    message: str
    field: str | None = None
    item_index: int | None = None


class ToolkitCallResult(StrictModel):
    ok: bool
    toolkit_tool: str
    payload: dict[str, Any] = Field(default_factory=dict)
    value: dict[str, Any] | list[Any] | str | None = None
    raw_excerpt: str | None = None
    warnings: list[ToolkitResponseWarning] = Field(default_factory=list)
    summary: str | None = None
    issue_code: str | None = None


class ToolkitToolView(StrictModel):
    name: str
    available: bool
    summary: str | None = None


class ToolkitCatalogResult(StrictModel):
    ok: bool
    tools: list[ToolkitToolView] = Field(default_factory=list)
    missing_tools: list[str] = Field(default_factory=list)
    raw_excerpt: str | None = None
    summary: str
    issue_code: str | None = None


class MathlibSearchResult(StrictModel):
    ok: bool
    query: str
    items: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[ToolkitResponseWarning] = Field(default_factory=list)
    raw_excerpt: str | None = None
    summary: str
    issue_code: str | None = None


class LeanDiagnosticsResult(StrictModel):
    ok: bool
    repo_root: str
    file_path: str | None = None
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[ToolkitResponseWarning] = Field(default_factory=list)
    summary: str
    issue_code: str | None = None
    raw_excerpt: str | None = None


class ToolkitDeclarationView(StrictModel):
    ok: bool
    name: str
    code: str | None = None
    module: str | None = None
    decl_start_pos: dict[str, Any] | None = None
    decl_end_pos: dict[str, Any] | None = None
    warnings: list[ToolkitResponseWarning] = Field(default_factory=list)
    summary: str
    issue_code: str | None = None
    raw_excerpt: str | None = None


class ToolkitModuleView(StrictModel):
    ok: bool
    module: str
    summary: str
    imports: list[str] = Field(default_factory=list)
    declarations: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[ToolkitResponseWarning] = Field(default_factory=list)
    raw_excerpt: str | None = None
    issue_code: str | None = None


class ToolkitDeclarationSoundnessTarget(StrictModel):
    module: str
    declaration_name: str
    source_file_path: str | None = None


class ToolkitDeclarationSoundnessWarning(StrictModel):
    line: int
    pattern: str


class ToolkitDeclarationSoundnessItem(StrictModel):
    module: str
    declaration_name: str
    success: bool
    source_file_path: str | None = None
    error_message: str | None = None
    axioms: list[str] = Field(default_factory=list)
    warnings: list[ToolkitDeclarationSoundnessWarning] = Field(default_factory=list)
    axiom_count: int = 0
    warning_count: int = 0


class ToolkitDeclarationSoundnessBatchResult(StrictModel):
    protocol_ok: bool
    batch_success: bool
    items: list[ToolkitDeclarationSoundnessItem] = Field(default_factory=list)
    count: int = 0
    success_count: int = 0
    failure_count: int = 0
    error_message: str | None = None
    warnings: list[ToolkitResponseWarning] = Field(default_factory=list)
    raw_excerpt: str | None = None
    summary: str
    issue_code: str | None = None


class SorryAxiomScanResult(StrictModel):
    ok: bool
    contains_sorry: bool
    contains_axiom: bool
    sorry_count: int
    admit_count: int = 0
    axiom_count: int
    summary: str


class LeanMcpToolkitClient:
    def __init__(
        self,
        config: LeanMcpToolkitClientConfig | None = None,
        *,
        dispatcher: ToolkitDispatcher | None = None,
    ) -> None:
        self.config = config or LeanMcpToolkitClientConfig()
        self._dispatcher = dispatcher

    @classmethod
    def from_config(cls, config: LeanMcpToolkitClientConfig) -> "LeanMcpToolkitClient":
        if not config.base_url:
            return cls(config)
        try:
            from lean_mcp_toolkit.app.service_factory import create_toolkit_http_client
            from lean_mcp_toolkit.transport.http import HttpConfig

            toolkit_client = create_toolkit_http_client(
                http_config=HttpConfig(
                    base_url=config.base_url,
                    api_prefix=config.api_prefix,
                    auth_token=config.auth_token,
                    timeout_seconds=config.timeout_seconds,
                )
            )
        except Exception:
            return cls(config)

        def dispatch(tool_name: str, payload: dict[str, Any]) -> Any:
            if hasattr(toolkit_client, "call_tool"):
                return toolkit_client.call_tool(tool_name, payload)
            if hasattr(toolkit_client, "dispatch_api"):
                return toolkit_client.dispatch_api(tool_name, payload)
            if hasattr(toolkit_client, "dispatch"):
                return toolkit_client.dispatch(tool_name, payload)
            raise AttributeError("Toolkit client has no known dispatch method")

        return cls(config, dispatcher=dispatch)

    def call_tool(self, tool_name: str, payload: dict[str, Any]) -> ToolkitCallResult:
        if self._dispatcher is None:
            if self.config.base_url:
                return self._call_tool_via_http(tool_name, payload)
            return ToolkitCallResult(
                ok=False,
                toolkit_tool=tool_name,
                payload=payload,
                issue_code="toolkit_unavailable",
                summary="Lean MCP Toolkit client is not configured.",
            )
        try:
            value = self._dispatcher(tool_name, payload)
        except KeyError as exc:
            if self.config.base_url:
                return self._call_tool_via_http(tool_name, payload)
            return ToolkitCallResult(
                ok=False,
                toolkit_tool=tool_name,
                payload=payload,
                issue_code="toolkit_tool_missing",
                summary=f"Toolkit tool is missing: {exc}",
            )
        except (TimeoutError, socket.timeout) as exc:
            return ToolkitCallResult(
                ok=False,
                toolkit_tool=tool_name,
                payload=payload,
                issue_code="toolkit_timeout",
                summary=f"Toolkit call timed out: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 - external boundary.
            return ToolkitCallResult(
                ok=False,
                toolkit_tool=tool_name,
                payload=payload,
                issue_code="toolkit_call_failed",
                summary=f"Toolkit call failed: {exc}",
            )
        try:
            value_dict, warnings = self._normalize_value(value)
        except ToolkitMalformedResponseError as exc:
            return ToolkitCallResult(
                ok=False,
                toolkit_tool=tool_name,
                payload=payload,
                issue_code="toolkit_malformed_response",
                summary=str(exc),
                raw_excerpt=self._excerpt(exc.raw_text),
            )
        success_failure = self._toolkit_success_failure(value_dict)
        if success_failure is not None:
            issue_code, summary = success_failure
            return ToolkitCallResult(
                ok=False,
                toolkit_tool=tool_name,
                payload=payload,
                value=value_dict,
                raw_excerpt=self._excerpt(value_dict),
                warnings=warnings,
                issue_code=issue_code,
                summary=summary,
            )
        return ToolkitCallResult(
            ok=True,
            toolkit_tool=tool_name,
            payload=payload,
            value=value_dict,
            raw_excerpt=self._excerpt(value_dict),
            warnings=warnings,
            summary="Toolkit call succeeded",
        )

    def probe_tool_catalog(self, required_tools: list[str] | None = None) -> ToolkitCatalogResult:
        required_tools = required_tools or []
        if not self.config.base_url:
            return ToolkitCatalogResult(
                ok=False,
                summary="Lean MCP Toolkit base_url is not configured.",
                issue_code="toolkit_unavailable",
                missing_tools=list(required_tools),
            )
        try:
            raw = self._get_json("/meta/tools")
        except ToolkitTimeoutError as exc:
            return ToolkitCatalogResult(
                ok=False,
                summary=f"Toolkit catalog probe timed out: {exc}",
                issue_code="toolkit_timeout",
                missing_tools=list(required_tools),
            )
        except ToolkitMalformedResponseError as exc:
            return ToolkitCatalogResult(
                ok=False,
                summary=str(exc),
                issue_code="toolkit_malformed_response",
                raw_excerpt=self._excerpt(exc.raw_text),
                missing_tools=list(required_tools),
            )
        except Exception as exc:  # noqa: BLE001 - external HTTP boundary.
            return ToolkitCatalogResult(
                ok=False,
                summary=f"Toolkit catalog probe failed: {exc}",
                issue_code="toolkit_catalog_probe_failed",
                missing_tools=list(required_tools),
            )
        tools_raw = raw.get("tools")
        if not isinstance(tools_raw, list):
            return ToolkitCatalogResult(
                ok=False,
                summary="Toolkit catalog response does not contain a tools list.",
                issue_code="toolkit_catalog_invalid_schema",
                raw_excerpt=self._excerpt(raw),
                missing_tools=list(required_tools),
            )
        tools = [self._tool_view_from_catalog_item(item) for item in tools_raw if isinstance(item, dict)]
        names = {tool.name for tool in tools}
        missing = [tool_name for tool_name in required_tools if tool_name not in names]
        return ToolkitCatalogResult(
            ok=not missing,
            tools=tools,
            missing_tools=missing,
            raw_excerpt=self._excerpt(raw),
            summary=f"Toolkit catalog contains {len(tools)} tools" + (f"; missing {len(missing)} required tools" if missing else ""),
            issue_code="toolkit_required_tools_missing" if missing else None,
        )

    def search_mathlib(self, query: str, kinds: list[str] | None = None, limit: int = 20) -> MathlibSearchResult:
        payload = {
            "query": query,
            "limit": limit,
            "include_module": True,
            "include_docstring": True,
            "include_source_text": False,
        }
        result = self._call_tool_with_fallback(
            "lean_explore.find",
            payload,
            fallback_tool="search_mathlib",
            fallback_payload={"query": query, "kinds": kinds or [], "limit": limit},
        )
        if not result.ok:
            return MathlibSearchResult(
                ok=False,
                query=query,
                summary=result.summary or "Mathlib search failed",
                issue_code=result.issue_code,
                raw_excerpt=result.raw_excerpt,
                warnings=list(result.warnings),
            )
        items = self._items_from_value(result.value, key="results")
        items = self._items_with_source_tool(items, result.toolkit_tool)
        normalized_items = items[:limit]
        warnings = [*result.warnings, *self._field_warnings(normalized_items, optional_fields=("module",), context="Mathlib search result")]
        return MathlibSearchResult(
            ok=True,
            query=query,
            items=normalized_items,
            warnings=warnings,
            raw_excerpt=result.raw_excerpt,
            summary=f"Found {len(normalized_items)} Mathlib results",
        )

    def inspect_mathlib_decl(self, decl_name: str) -> ToolkitDeclarationView:
        result = self._call_tool_with_fallback(
            "lean_explore.find",
            {
                "query": decl_name,
                "exact_name": decl_name,
                "limit": 5,
                "include_module": True,
                "include_docstring": True,
                "include_source_text": True,
                "include_source_link": True,
                "include_dependencies": True,
            },
            fallback_tool="inspect_mathlib_decl",
            fallback_payload={"decl_name": decl_name},
        )
        if result.toolkit_tool == "inspect_mathlib_decl":
            return self._declaration_view(decl_name, result)
        if not result.ok:
            return ToolkitDeclarationView(
                ok=False,
                name=decl_name,
                summary=result.summary or "Declaration inspect failed",
                issue_code=result.issue_code,
                raw_excerpt=result.raw_excerpt,
                warnings=list(result.warnings),
            )
        items = self._items_from_value(result.value, key="results")
        exact = next((item for item in items if item.get("name") == decl_name), None)
        if exact is None:
            return ToolkitDeclarationView(
                ok=False,
                name=decl_name,
                summary=f"Declaration not found: {decl_name}",
                issue_code="declaration_not_found",
                raw_excerpt=result.raw_excerpt,
                warnings=list(result.warnings),
            )
        item = exact
        warnings = [
            *result.warnings,
            *self._field_warnings([item], optional_fields=("module", "source_text"), context=f"Declaration candidate {decl_name}"),
        ]
        return ToolkitDeclarationView(
            ok=True,
            name=str(item.get("name") or decl_name),
            code=str(item.get("source_text")) if item.get("source_text") is not None else None,
            module=str(item.get("module")) if item.get("module") is not None else None,
            warnings=warnings,
            summary=f"Inspected declaration {decl_name}",
            raw_excerpt=result.raw_excerpt,
        )

    def inspect_mathlib_module(self, module: str) -> ToolkitModuleView:
        result = self._call_tool_with_fallback(
            "mathlib_nav.file_outline",
            {
                "target": module,
                "include_imports": True,
                "include_decl_headers": True,
                "limit_decls": 200,
            },
            fallback_tool="inspect_mathlib_module",
            fallback_payload={"module": module},
        )
        if not result.ok:
            return ToolkitModuleView(
                ok=False,
                module=module,
                summary=result.summary or "Module inspect failed",
                issue_code=result.issue_code,
                raw_excerpt=result.raw_excerpt,
                warnings=list(result.warnings),
            )
        value = result.value if isinstance(result.value, dict) else {}
        items = self._items_from_value(result.value, key="declarations")
        imports = value.get("imports") or []
        if not isinstance(imports, list):
            imports = [imports]
        return ToolkitModuleView(
            ok=True,
            module=module,
            imports=[str(item) for item in imports if str(item).strip()],
            declarations=items,
            warnings=list(result.warnings),
            summary=f"Inspected module {module}",
            raw_excerpt=result.raw_excerpt,
        )

    def search_arxiv_theorems(self, query: str, limit: int = 20) -> MathlibSearchResult:
        result = self.call_tool("search_arxiv_theorems", {"query": query, "limit": limit})
        if not result.ok:
            fallback = self._search_arxiv_theorems_from_eprint_source(query, limit=limit, failed_result=result)
            if fallback is not None:
                return fallback
            return MathlibSearchResult(
                ok=False,
                query=query,
                summary=result.summary or "arXiv theorem search unavailable",
                issue_code=result.issue_code,
                raw_excerpt=result.raw_excerpt,
                warnings=list(result.warnings),
            )
        items = self._items_from_value(result.value)
        return MathlibSearchResult(
            ok=True,
            query=query,
            items=items[:limit],
            raw_excerpt=result.raw_excerpt,
            warnings=list(result.warnings),
            summary=f"Found {len(items[:limit])} theorem candidates",
        )

    def _search_arxiv_theorems_from_eprint_source(
        self,
        query: str,
        *,
        limit: int,
        failed_result: ToolkitCallResult,
    ) -> MathlibSearchResult | None:
        if not self.config.base_url:
            return None
        arxiv_id = self._extract_arxiv_id(query)
        if arxiv_id is None:
            return None
        try:
            source_bytes = self._download_arxiv_eprint_source(arxiv_id)
            tex_sources = self._extract_tex_sources_from_arxiv_payload(source_bytes)
        except Exception as exc:  # noqa: BLE001 - external fallback must preserve the original toolkit failure.
            return MathlibSearchResult(
                ok=False,
                query=query,
                summary=failed_result.summary or f"arXiv theorem search unavailable; e-print fallback failed: {exc}",
                issue_code=failed_result.issue_code,
                raw_excerpt=failed_result.raw_excerpt,
                warnings=[
                    *failed_result.warnings,
                    ToolkitResponseWarning(
                        code="arxiv_eprint_fallback_failed",
                        message=f"Failed to download or parse arXiv e-print source for {arxiv_id}: {exc}",
                    ),
                ],
            )
        items = self._theorem_items_from_tex_sources(arxiv_id, tex_sources, limit=limit)
        if not items:
            title = self._first_latex_title(tex_sources) or f"arXiv {arxiv_id}"
            abstract = self._first_latex_abstract(tex_sources) or title
            items = [
                {
                    "title": title,
                    "theorem": abstract,
                    "arxiv_id": arxiv_id,
                    "theorem_id": f"{arxiv_id}:abstract",
                    "kind": "abstract",
                    "source_tool": "arxiv_eprint_fallback",
                }
            ]
        return MathlibSearchResult(
            ok=True,
            query=query,
            items=items[:limit],
            raw_excerpt=(failed_result.raw_excerpt or "")[: self.config.response_excerpt_chars],
            warnings=[
                *failed_result.warnings,
                ToolkitResponseWarning(
                    code=failed_result.issue_code or "search_arxiv_theorems_failed",
                    message=failed_result.summary or "Toolkit arXiv theorem provider failed before fallback.",
                ),
                ToolkitResponseWarning(
                    code="arxiv_eprint_fallback",
                    message=f"Toolkit theorem provider failed; parsed real arXiv e-print source for {arxiv_id}.",
                ),
            ],
            summary=f"Found {len(items[:limit])} theorem candidates via arXiv e-print fallback",
        )

    @staticmethod
    def _extract_arxiv_id(query: str) -> str | None:
        new_style = re.search(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b", query)
        if new_style:
            return new_style.group(0)
        old_style = re.search(r"\b[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?\b", query)
        if old_style:
            return old_style.group(0)
        return None

    def _download_arxiv_eprint_source(self, arxiv_id: str) -> bytes:
        url = f"https://arxiv.org/e-print/{quote(arxiv_id, safe='/')}"
        request = Request(url, headers={"User-Agent": "lean-constellation-runtime-matrix/1.0"})
        with urlopen(request, timeout=self.config.timeout_seconds) as response:  # noqa: S310 - arXiv e-print fallback.
            return response.read()

    @staticmethod
    def _extract_tex_sources_from_arxiv_payload(source_bytes: bytes) -> list[str]:
        tex_sources: list[str] = []
        try:
            with tarfile.open(fileobj=io.BytesIO(source_bytes), mode="r:*") as archive:
                for member in archive.getmembers():
                    name = Path(member.name)
                    if member.isfile() and name.suffix.lower() == ".tex" and not name.is_absolute() and ".." not in name.parts:
                        extracted = archive.extractfile(member)
                        if extracted is not None:
                            tex_sources.append(extracted.read().decode("utf-8", errors="ignore"))
        except tarfile.TarError:
            pass
        if tex_sources:
            return tex_sources
        if zipfile.is_zipfile(io.BytesIO(source_bytes)):
            with zipfile.ZipFile(io.BytesIO(source_bytes)) as archive:
                for name in archive.namelist():
                    path = Path(name)
                    if path.suffix.lower() == ".tex" and not path.is_absolute() and ".." not in path.parts:
                        tex_sources.append(archive.read(name).decode("utf-8", errors="ignore"))
        if tex_sources:
            return tex_sources
        try:
            return [gzip.decompress(source_bytes).decode("utf-8", errors="ignore")]
        except OSError:
            return [source_bytes.decode("utf-8", errors="ignore")]

    @staticmethod
    def _theorem_items_from_tex_sources(arxiv_id: str, tex_sources: list[str], *, limit: int) -> list[dict[str, Any]]:
        pattern = re.compile(
            r"\\begin\{(?P<kind>theorem|lemma|proposition|corollary|definition)\*?\}(?P<body>.*?)\\end\{(?P=kind)\*?\}",
            re.IGNORECASE | re.DOTALL,
        )
        title = LeanMcpToolkitClient._first_latex_title(tex_sources) or f"arXiv {arxiv_id}"
        items: list[dict[str, Any]] = []
        for source in tex_sources:
            for match in pattern.finditer(source):
                kind = match.group("kind").lower()
                body = LeanMcpToolkitClient._clean_latex_text(match.group("body"))
                if not body:
                    continue
                index = len(items) + 1
                items.append(
                    {
                        "title": title,
                        "theorem": body,
                        "arxiv_id": arxiv_id,
                        "theorem_id": f"{arxiv_id}:{kind}:{index}",
                        "kind": kind,
                        "source_tool": "arxiv_eprint_fallback",
                    }
                )
                if len(items) >= limit:
                    return items
        return items

    @staticmethod
    def _first_latex_title(tex_sources: list[str]) -> str | None:
        for source in tex_sources:
            match = re.search(r"\\title\{(?P<title>.*?)\}", source, flags=re.DOTALL)
            if match:
                return LeanMcpToolkitClient._clean_latex_text(match.group("title"))
        return None

    @staticmethod
    def _first_latex_abstract(tex_sources: list[str]) -> str | None:
        for source in tex_sources:
            match = re.search(r"\\begin\{abstract\}(?P<abstract>.*?)\\end\{abstract\}", source, flags=re.DOTALL)
            if match:
                return LeanMcpToolkitClient._clean_latex_text(match.group("abstract"))
        return None

    @staticmethod
    def _clean_latex_text(value: str) -> str:
        text = re.sub(r"%.*", " ", value)
        text = re.sub(r"\\label\{[^}]*\}", " ", text)
        text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{([^{}]*)\})?", lambda match: match.group(1) or " ", text)
        text = text.replace("{", " ").replace("}", " ")
        return re.sub(r"\s+", " ", text).strip()

    def run_file_diagnostics(self, repo_root: Path, file_path: Path) -> LeanDiagnosticsResult:
        result = self._call_tool_with_fallback(
            "diagnostics.file",
            {"project_root": str(repo_root), "file_path": str(file_path)},
            fallback_tool="run_file_diagnostics",
            fallback_payload={"repo_root": str(repo_root), "file_path": str(file_path)},
        )
        if not result.ok:
            return LeanDiagnosticsResult(
                ok=False,
                repo_root=str(repo_root),
                file_path=str(file_path),
                summary=result.summary or "Diagnostics failed",
                issue_code=result.issue_code,
                raw_excerpt=result.raw_excerpt,
                warnings=list(result.warnings),
            )
        diagnostics = self._items_from_value(result.value, key="diagnostics")
        return LeanDiagnosticsResult(
            ok=True,
            repo_root=str(repo_root),
            file_path=str(file_path),
            diagnostics=diagnostics,
            warnings=list(result.warnings),
            summary=f"Diagnostics returned {len(diagnostics)} items",
            raw_excerpt=result.raw_excerpt,
        )

    def check_declaration_soundness_batch(
        self,
        repo_root: Path,
        declarations: list[ToolkitDeclarationSoundnessTarget],
        *,
        scan_source: bool = False,
    ) -> ToolkitDeclarationSoundnessBatchResult:
        payload = {
            "project_root": str(repo_root),
            "declarations": [item.model_dump(mode="json") for item in declarations],
            "scan_source": scan_source,
        }
        result = self.call_tool("lsp.declaration_soundness_batch", payload)
        raw = result.value
        if not isinstance(raw, dict):
            return ToolkitDeclarationSoundnessBatchResult(
                protocol_ok=False,
                batch_success=False,
                warnings=list(result.warnings),
                raw_excerpt=result.raw_excerpt,
                summary=result.summary or "Declaration soundness batch did not return an object.",
                issue_code=result.issue_code or "declaration_soundness_invalid_response",
            )
        parsed = self._parse_declaration_soundness_batch(raw, declarations)
        if isinstance(parsed, str):
            return ToolkitDeclarationSoundnessBatchResult(
                protocol_ok=False,
                batch_success=False,
                warnings=list(result.warnings),
                raw_excerpt=result.raw_excerpt,
                summary=parsed,
                issue_code="declaration_soundness_invalid_response",
            )
        return parsed.model_copy(
            update={
                "warnings": list(result.warnings),
                "raw_excerpt": result.raw_excerpt,
            }
        )

    def check_declaration_soundness(
        self,
        repo_root: Path,
        declaration: ToolkitDeclarationSoundnessTarget,
        *,
        scan_source: bool = False,
    ) -> ToolkitDeclarationSoundnessBatchResult:
        return self.check_declaration_soundness_batch(
            repo_root,
            [declaration],
            scan_source=scan_source,
        )

    def extract_declaration(self, repo_root: Path, module_or_file: str, decl_name: str) -> ToolkitDeclarationView:
        result = self._call_tool_with_fallback(
            "declarations.extract",
            {"project_root": str(repo_root), "target": module_or_file},
            fallback_tool="extract_declaration",
            fallback_payload={"repo_root": str(repo_root), "module_or_file": module_or_file, "decl_name": decl_name},
        )
        if result.toolkit_tool == "extract_declaration":
            return self._declaration_view(decl_name, result)
        return self._declaration_view_from_extract(decl_name, result, module_or_file=module_or_file)

    def scan_sorry_axiom(self, file_text: str) -> SorryAxiomScanResult:
        scan_text = self._strip_comments_and_strings(file_text)
        sorry_count = len(re.findall(r"(?<![A-Za-z0-9_])sorry(?![A-Za-z0-9_])", scan_text))
        admit_count = len(re.findall(r"(?<![A-Za-z0-9_])admit(?![A-Za-z0-9_])", scan_text))
        axiom_count = len(re.findall(r"(?<![A-Za-z0-9_])axiom(?![A-Za-z0-9_])", scan_text))
        contains_sorry = (sorry_count + admit_count) > 0
        contains_axiom = axiom_count > 0
        return SorryAxiomScanResult(
            ok=not contains_sorry and not contains_axiom,
            contains_sorry=contains_sorry,
            contains_axiom=contains_axiom,
            sorry_count=sorry_count,
            admit_count=admit_count,
            axiom_count=axiom_count,
            summary=f"sorry={sorry_count}, admit={admit_count}, axiom={axiom_count}",
        )

    def _parse_declaration_soundness_batch(
        self,
        raw: dict[str, Any],
        requested: list[ToolkitDeclarationSoundnessTarget],
    ) -> ToolkitDeclarationSoundnessBatchResult | str:
        batch_success = raw.get("success")
        raw_items = raw.get("items")
        if not isinstance(batch_success, bool) or not isinstance(raw_items, list):
            return "Declaration soundness response is missing typed success/items fields."
        if len(raw_items) != len(requested):
            return (
                "Declaration soundness response item count does not match the request: "
                f"expected {len(requested)}, received {len(raw_items)}."
            )
        items: list[ToolkitDeclarationSoundnessItem] = []
        for index, (raw_item, target) in enumerate(zip(raw_items, requested, strict=True)):
            if not isinstance(raw_item, dict):
                return f"Declaration soundness item {index} is not an object."
            module = raw_item.get("module")
            declaration_name = raw_item.get("declaration_name")
            item_success = raw_item.get("success")
            axioms = raw_item.get("axioms")
            warnings = raw_item.get("warnings")
            if module != target.module or declaration_name != target.declaration_name:
                return (
                    f"Declaration soundness item {index} identity mismatch: expected "
                    f"{target.module}::{target.declaration_name}."
                )
            if raw_item.get("source_file_path") != target.source_file_path:
                return f"Declaration soundness item {index} source_file_path mismatch."
            if not isinstance(item_success, bool):
                return f"Declaration soundness item {index} is missing boolean success."
            if not isinstance(axioms, list) or any(not isinstance(item, str) for item in axioms):
                return f"Declaration soundness item {index} has invalid axioms."
            if not isinstance(warnings, list) or any(not isinstance(item, dict) for item in warnings):
                return f"Declaration soundness item {index} has invalid warnings."
            if (
                type(raw_item.get("axiom_count")) is not int
                or type(raw_item.get("warning_count")) is not int
            ):
                return f"Declaration soundness item {index} is missing integer count fields."
            try:
                item = ToolkitDeclarationSoundnessItem.model_validate(raw_item)
            except Exception as exc:  # noqa: BLE001 - external response validation boundary.
                return f"Declaration soundness item {index} is invalid: {exc}"
            if item.axiom_count != len(item.axioms) or item.warning_count != len(item.warnings):
                return f"Declaration soundness item {index} count fields are inconsistent."
            items.append(item)
        count = raw.get("count")
        success_count = raw.get("success_count")
        failure_count = raw.get("failure_count")
        if any(type(value) is not int for value in (count, success_count, failure_count)):
            return "Declaration soundness batch is missing integer count fields."
        expected_success = sum(item.success for item in items)
        expected_failure = len(items) - expected_success
        if (count, success_count, failure_count) != (
            len(items),
            expected_success,
            expected_failure,
        ):
            return "Declaration soundness batch count fields are inconsistent."
        if batch_success != (expected_failure == 0):
            return "Declaration soundness batch success is inconsistent with item results."
        error_message = raw.get("error_message")
        if error_message is not None and not isinstance(error_message, str):
            return "Declaration soundness batch error_message must be a string or null."
        return ToolkitDeclarationSoundnessBatchResult(
            protocol_ok=True,
            batch_success=batch_success,
            items=items,
            count=len(items),
            success_count=expected_success,
            failure_count=expected_failure,
            error_message=error_message,
            summary=(
                f"Resolved {expected_success} of {len(items)} declaration soundness reports."
            ),
        )

    def _call_tool_with_fallback(
        self,
        tool_name: str,
        payload: dict[str, Any],
        *,
        fallback_tool: str,
        fallback_payload: dict[str, Any],
    ) -> ToolkitCallResult:
        result = self.call_tool(tool_name, payload)
        if result.ok or result.issue_code != "toolkit_tool_missing":
            return result
        return self.call_tool(fallback_tool, fallback_payload)

    def _declaration_view(self, name: str, result: ToolkitCallResult) -> ToolkitDeclarationView:
        if not result.ok:
            return ToolkitDeclarationView(
                ok=False,
                name=name,
                summary=result.summary or "Declaration inspect failed",
                issue_code=result.issue_code,
                raw_excerpt=result.raw_excerpt,
                warnings=list(result.warnings),
            )
        value = result.value if isinstance(result.value, dict) else {}
        code = value.get("code") or value.get("source") or value.get("text")
        module = value.get("module")
        warnings = [*result.warnings, *self._field_warnings([value], optional_fields=("module", "code"), context=f"Declaration view {name}")]
        return ToolkitDeclarationView(
            ok=True,
            name=name,
            code=str(code) if code is not None else None,
            module=str(module) if module is not None else None,
            decl_start_pos=value.get("decl_start_pos") if isinstance(value.get("decl_start_pos"), dict) else None,
            decl_end_pos=value.get("decl_end_pos") if isinstance(value.get("decl_end_pos"), dict) else None,
            warnings=warnings,
            summary=f"Inspected declaration {name}",
            raw_excerpt=result.raw_excerpt,
        )

    def _declaration_view_from_extract(
        self,
        name: str,
        result: ToolkitCallResult,
        *,
        module_or_file: str,
    ) -> ToolkitDeclarationView:
        if not result.ok:
            return ToolkitDeclarationView(
                ok=False,
                name=name,
                summary=result.summary or "Declaration extract failed",
                issue_code=result.issue_code,
                raw_excerpt=result.raw_excerpt,
                warnings=list(result.warnings),
            )
        value = result.value if isinstance(result.value, dict) else {}
        if value.get("success") is False:
            return ToolkitDeclarationView(
                ok=False,
                name=name,
                summary=str(value.get("error_message") or "Declaration extract failed"),
                issue_code="declaration_extract_failed",
                raw_excerpt=result.raw_excerpt,
                warnings=list(result.warnings),
            )
        declarations = self._items_from_value(value, key="declarations")
        item = next((decl for decl in declarations if decl.get("name") == name), None)
        if item is None:
            return ToolkitDeclarationView(
                ok=False,
                name=name,
                summary=f"Declaration not found: {name}",
                issue_code="declaration_not_found",
                raw_excerpt=result.raw_excerpt,
                warnings=list(result.warnings),
            )
        code = item.get("full_declaration") or item.get("code") or item.get("source") or item.get("text")
        return ToolkitDeclarationView(
            ok=True,
            name=name,
            code=str(code) if code is not None else None,
            module=module_or_file,
            decl_start_pos=item.get("decl_start_pos") if isinstance(item.get("decl_start_pos"), dict) else None,
            decl_end_pos=item.get("decl_end_pos") if isinstance(item.get("decl_end_pos"), dict) else None,
            warnings=list(result.warnings),
            summary=f"Extracted declaration {name}",
            raw_excerpt=result.raw_excerpt,
        )

    def _normalize_value(self, value: Any) -> tuple[dict[str, Any] | list[Any] | str, list[ToolkitResponseWarning]]:
        warnings: list[ToolkitResponseWarning] = []
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json"), warnings
        if hasattr(value, "to_dict"):
            return value.to_dict(), warnings
        if isinstance(value, bytes):
            decoded = value.decode("utf-8", errors="replace")
            parsed = self._parse_jsonish_string(decoded)
            if parsed is not None:
                return parsed, warnings
            warnings.append(
                ToolkitResponseWarning(
                    code="toolkit_response_bytes_not_json",
                    message="Toolkit returned bytes that were decoded as plain text.",
                )
            )
            return decoded, warnings
        if isinstance(value, str):
            parsed = self._parse_jsonish_string(value)
            if parsed is not None:
                return parsed, warnings
            return value, warnings
        if isinstance(value, (dict, list)):
            return value, warnings
        warnings.append(
            ToolkitResponseWarning(
                code="toolkit_response_repr_fallback",
                message=f"Toolkit returned unsupported response type: {type(value).__name__}.",
            )
        )
        return {"value": repr(value)}, warnings

    def _toolkit_success_failure(self, value: dict[str, Any] | list[Any] | str) -> tuple[str, str] | None:
        if not isinstance(value, dict) or value.get("success") is not False:
            return None
        summary = str(value.get("error_message") or value.get("summary") or "Toolkit tool returned success=false.")
        provider = str(value.get("provider") or "toolkit")
        return f"{provider}_failed", summary

    def _parse_jsonish_string(self, value: str) -> dict[str, Any] | list[Any] | str | None:
        text = value.strip()
        if not text:
            return None
        if text[0] not in "{[":
            return None
        try:
            decoded = json.loads(text)
        except JSONDecodeError as exc:
            raise ToolkitMalformedResponseError(f"Toolkit response is not valid JSON: {exc.msg}", value) from exc
        if isinstance(decoded, (dict, list, str)):
            return decoded
        return {"value": decoded}

    def _field_warnings(
        self,
        items: list[dict[str, Any]],
        *,
        optional_fields: tuple[str, ...],
        context: str,
    ) -> list[ToolkitResponseWarning]:
        warnings: list[ToolkitResponseWarning] = []
        for index, item in enumerate(items):
            for field in optional_fields:
                if item.get(field) is None:
                    warnings.append(
                        ToolkitResponseWarning(
                            code="toolkit_candidate_missing_optional_field",
                            message=f"{context} is missing optional field: {field}",
                            field=field,
                            item_index=index,
                        )
                    )
        return warnings

    def _items_with_source_tool(self, items: list[dict[str, Any]], toolkit_tool: str) -> list[dict[str, Any]]:
        return [dict(item, source_tool=toolkit_tool) for item in items]

    def _items_from_value(self, value: dict[str, Any] | list[Any] | str | None, key: str = "items") -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [item if isinstance(item, dict) else {"value": item} for item in value]
        if isinstance(value, dict):
            raw_items = value.get(key) or value.get("items") or value.get("results") or value.get("matches") or []
            if isinstance(raw_items, list):
                return [item if isinstance(item, dict) else {"value": item} for item in raw_items]
            return [{"value": raw_items}]
        if value is None:
            return []
        return [{"value": value}]

    def _tool_view_from_catalog_item(self, item: dict[str, Any]) -> ToolkitToolView:
        name = item.get("canonical_name") or item.get("name") or item.get("tool_name") or item.get("raw_name") or ""
        summary = item.get("summary") or item.get("description")
        return ToolkitToolView(
            name=str(name),
            available=bool(str(name).strip()),
            summary=str(summary) if summary is not None else None,
        )

    def _get_json(self, path: str) -> dict[str, Any]:
        url = self._build_url(path)
        headers = {"Accept": "application/json"}
        if self.config.auth_token:
            headers["Authorization"] = f"Bearer {self.config.auth_token}"
        request = Request(url=url, method="GET", headers=headers)
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:  # noqa: S310 - configured external toolkit endpoint.
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = str(exc)
            raise RuntimeError(f"HTTP {exc.code}: {body[: self.config.response_excerpt_chars]}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ToolkitTimeoutError(str(exc)) from exc
        except URLError as exc:
            if self._is_timeout_exception(exc):
                raise ToolkitTimeoutError(str(exc)) from exc
            raise RuntimeError(str(exc)) from exc
        try:
            decoded = json.loads(body or "{}")
        except JSONDecodeError as exc:
            raise ToolkitMalformedResponseError(f"Toolkit HTTP response is not valid JSON: {exc.msg}", body) from exc
        if not isinstance(decoded, dict):
            raise ToolkitMalformedResponseError("Toolkit HTTP response must be a JSON object.", body)
        return decoded

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = self._build_url(path)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.config.auth_token:
            headers["Authorization"] = f"Bearer {self.config.auth_token}"
        request = Request(url=url, method="POST", data=json.dumps(payload).encode("utf-8"), headers=headers)
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:  # noqa: S310 - configured external toolkit endpoint.
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = str(exc)
            raise RuntimeError(f"HTTP {exc.code}: {body[: self.config.response_excerpt_chars]}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ToolkitTimeoutError(str(exc)) from exc
        except URLError as exc:
            if self._is_timeout_exception(exc):
                raise ToolkitTimeoutError(str(exc)) from exc
            raise RuntimeError(str(exc)) from exc
        try:
            decoded = json.loads(body or "{}")
        except JSONDecodeError as exc:
            raise ToolkitMalformedResponseError(f"Toolkit HTTP response is not valid JSON: {exc.msg}", body) from exc
        if not isinstance(decoded, dict):
            raise ToolkitMalformedResponseError("Toolkit HTTP response must be a JSON object.", body)
        return decoded

    def _build_url(self, path: str) -> str:
        if not self.config.base_url:
            raise RuntimeError("base_url is not configured")
        base = self.config.base_url.rstrip("/") + "/"
        prefix = self.config.api_prefix.strip("/")
        normalized_path = path.strip("/")
        return urljoin(base, "/".join(part for part in (prefix, normalized_path) if part))

    def _call_tool_via_http(self, tool_name: str, payload: dict[str, Any]) -> ToolkitCallResult:
        try:
            catalog = self._get_json("/meta/tools")
            api_path = self._api_path_for_tool(catalog, tool_name)
            if api_path is None:
                return ToolkitCallResult(
                    ok=False,
                    toolkit_tool=tool_name,
                    payload=payload,
                    issue_code="toolkit_tool_missing",
                    summary=f"Toolkit tool is missing: {tool_name}",
                )
            value = self._post_json(api_path, payload)
        except ToolkitTimeoutError as exc:
            return ToolkitCallResult(
                ok=False,
                toolkit_tool=tool_name,
                payload=payload,
                issue_code="toolkit_timeout",
                summary=f"Toolkit HTTP call timed out: {exc}",
            )
        except ToolkitMalformedResponseError as exc:
            return ToolkitCallResult(
                ok=False,
                toolkit_tool=tool_name,
                payload=payload,
                issue_code="toolkit_malformed_response",
                raw_excerpt=self._excerpt(exc.raw_text),
                summary=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - external HTTP boundary.
            return ToolkitCallResult(
                ok=False,
                toolkit_tool=tool_name,
                payload=payload,
                issue_code="toolkit_call_failed",
                summary=f"Toolkit HTTP call failed: {exc}",
            )
        try:
            value_dict, warnings = self._normalize_value(value)
        except ToolkitMalformedResponseError as exc:
            return ToolkitCallResult(
                ok=False,
                toolkit_tool=tool_name,
                payload=payload,
                issue_code="toolkit_malformed_response",
                raw_excerpt=self._excerpt(exc.raw_text),
                summary=str(exc),
            )
        success_failure = self._toolkit_success_failure(value_dict)
        if success_failure is not None:
            issue_code, summary = success_failure
            return ToolkitCallResult(
                ok=False,
                toolkit_tool=tool_name,
                payload=payload,
                value=value_dict,
                raw_excerpt=self._excerpt(value_dict),
                warnings=warnings,
                issue_code=issue_code,
                summary=summary,
            )
        return ToolkitCallResult(
            ok=True,
            toolkit_tool=tool_name,
            payload=payload,
            value=value_dict,
            raw_excerpt=self._excerpt(value_dict),
            warnings=warnings,
            summary="Toolkit call succeeded",
        )

    def _is_timeout_exception(self, exc: URLError) -> bool:
        reason = getattr(exc, "reason", None)
        return isinstance(reason, (TimeoutError, socket.timeout))

    def _api_path_for_tool(self, catalog: dict[str, Any], tool_name: str) -> str | None:
        tools_raw = catalog.get("tools")
        if not isinstance(tools_raw, list):
            raise RuntimeError("Toolkit catalog response does not contain a tools list.")
        for item in tools_raw:
            if not isinstance(item, dict):
                continue
            names = {
                str(item.get("canonical_name") or ""),
                str(item.get("name") or ""),
                str(item.get("tool_name") or ""),
                str(item.get("raw_name") or ""),
            }
            for alias_key in ("aliases", "visible_aliases"):
                aliases = item.get(alias_key)
                if isinstance(aliases, list):
                    names.update(str(alias) for alias in aliases)
            if tool_name in names:
                api_path = item.get("api_path")
                if not api_path:
                    raise RuntimeError(f"Toolkit catalog item has no api_path for tool: {tool_name}")
                return str(api_path)
        return None

    def _excerpt(self, value: Any) -> str:
        text = repr(value)
        limit = self.config.response_excerpt_chars
        if len(text) <= limit:
            return text
        return text[:limit] + "\n...[truncated]"

    def _strip_comments_and_strings(self, file_text: str) -> str:
        result: list[str] = []
        i = 0
        block_depth = 0
        in_string = False
        in_line_comment = False
        while i < len(file_text):
            ch = file_text[i]
            nxt = file_text[i + 1] if i + 1 < len(file_text) else ""
            if in_line_comment:
                if ch == "\n":
                    in_line_comment = False
                    result.append(ch)
                else:
                    result.append(" ")
                i += 1
                continue
            if block_depth:
                if ch == "/" and nxt == "-":
                    block_depth += 1
                    result.extend("  ")
                    i += 2
                    continue
                if ch == "-" and nxt == "/":
                    block_depth -= 1
                    result.extend("  ")
                    i += 2
                    continue
                result.append("\n" if ch == "\n" else " ")
                i += 1
                continue
            if in_string:
                if ch == "\\" and nxt:
                    result.extend("  ")
                    i += 2
                    continue
                if ch == "\"":
                    in_string = False
                result.append("\n" if ch == "\n" else " ")
                i += 1
                continue
            if ch == "-" and nxt == "-":
                in_line_comment = True
                result.extend("  ")
                i += 2
                continue
            if ch == "/" and nxt == "-":
                block_depth = 1
                result.extend("  ")
                i += 2
                continue
            if ch == "\"":
                in_string = True
                result.append(" ")
                i += 1
                continue
            result.append(ch)
            i += 1
        return "".join(result)
