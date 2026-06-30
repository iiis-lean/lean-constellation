"""Read-only navigation over the configured upstream Lean repo."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.adapter.upstream_metadata import UpstreamMetadataComponent
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.lean_projection.lean_check import LeanCheckComponent, SorryAxiomScanView

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class UpstreamDeclSearchItem(StrictModel):
    module: str | None = None
    decl_name: str
    kind: str | None = None
    statement: str | None = None
    match_reason: str | None = None


class UpstreamDeclSearchView(StrictModel):
    query: str
    items: list[UpstreamDeclSearchItem] = Field(default_factory=list)
    summary: str


class UpstreamModuleSearchItem(StrictModel):
    module: str
    summary: str | None = None
    match_reason: str | None = None


class UpstreamModuleSearchView(StrictModel):
    query: str
    items: list[UpstreamModuleSearchItem] = Field(default_factory=list)
    summary: str


class UpstreamModuleDeclsView(StrictModel):
    module: str
    declarations: list[UpstreamDeclSearchItem] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    summary: str


class UpstreamDeclDetailView(StrictModel):
    module: str
    decl_name: str
    kind: str | None = None
    signature: str | None = None
    code_excerpt: str | None = None
    imports: list[str] = Field(default_factory=list)
    summary: str


class UpstreamSourceContextView(StrictModel):
    module: str
    decl_name: str | None = None
    text: str
    truncated: bool = False
    summary: str


class UpstreamCaptureView(StrictModel):
    module: str
    decl_name: str
    capture_mode: Literal["statement_only", "full_declaration"]
    code: str
    scan: SorryAxiomScanView
    summary: str


class UpstreamModuleImportsView(StrictModel):
    module: str
    imports: list[str] = Field(default_factory=list)
    namespace_hints: list[str] = Field(default_factory=list)
    package_hints: list[str] = Field(default_factory=list)
    summary: str


class UpstreamNavigationComponent:
    """Adapter read-only upstream navigation through Lean MCP Toolkit."""

    _MAX_CONTEXT_CHARS = 12000

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        upstream_metadata: UpstreamMetadataComponent | None = None,
        lean_check: LeanCheckComponent | None = None,
    ) -> None:
        self.runtime = runtime
        self.upstream_metadata = upstream_metadata or UpstreamMetadataComponent(runtime)
        self.lean_check = lean_check or self.runtime.require_app_service("lean_projection").lean_check

    def search_upstream_declarations(
        self,
        repo_root: Path,
        *,
        query: str,
        kind_filter: str | None = None,
        module_filter: str | None = None,
        limit: int = 20,
    ) -> ServiceResult[UpstreamDeclSearchView]:
        if not query or not query.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("upstream_search_query_required", "Declaration search query is required.", field="query"))
        gate = self._metadata_available(repo_root)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        limit = self._normalize_limit(limit)
        result = self.runtime.external.lean_mcp_toolkit.call_tool(
            "repo_nav.local_decl.find",
            {
                "repo_root": str(gate.value["upstream_root"]),
                "query": query.strip(),
                "match_mode": "contains",
                "decl_kinds": [kind_filter] if kind_filter else None,
                "module_filter": module_filter,
                "include_deps": False,
                "limit": limit,
            },
        )
        if not result.ok:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(result.issue_code or "upstream_declaration_search_failed", result.summary or "Upstream declaration search failed."))
        items = [self._decl_item(item) for item in self._items(result.value)[:limit]]
        return self.runtime.foundation.ok(
            UpstreamDeclSearchView(query=query.strip(), items=items, summary=f"Found {len(items)} upstream declaration candidates.")
        )

    def search_upstream_modules(self, repo_root: Path, *, query: str, limit: int = 20) -> ServiceResult[UpstreamModuleSearchView]:
        if not query or not query.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("upstream_module_query_required", "Module search query is required.", field="query"))
        gate = self._metadata_available(repo_root)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        limit = self._normalize_limit(limit)
        result = self.runtime.external.lean_mcp_toolkit.call_tool(
            "repo_nav.tree",
            {"repo_root": str(gate.value["upstream_root"]), "name_filter": query.strip(), "depth": 8, "limit": limit},
        )
        if not result.ok:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(result.issue_code or "upstream_module_search_failed", result.summary or "Upstream module search failed."))
        modules = []
        for item in self._items(result.value)[:limit]:
            module = self._field(item, "module", "module_name", "module_path")
            if module:
                modules.append(
                    UpstreamModuleSearchItem(
                        module=module,
                        summary=self._field(item, "summary", "description", "relative_path"),
                        match_reason=self._field(item, "match_reason", "reason", "kind"),
                    )
                )
        if not modules:
            metadata = gate.value["metadata"]
            visible_modules = [
                module
                for module in metadata.visible_modules
                if query.strip().lower() in module.lower()
            ][:limit]
            modules = [
                UpstreamModuleSearchItem(module=module, summary="Visible upstream module.", match_reason="visible_module")
                for module in visible_modules
            ]
        return self.runtime.foundation.ok(
            UpstreamModuleSearchView(query=query.strip(), items=modules, summary=f"Found {len(modules)} upstream modules.")
        )

    def list_upstream_module_declarations(
        self,
        repo_root: Path,
        *,
        module: str,
        kind_filter: str | None = None,
    ) -> ServiceResult[UpstreamModuleDeclsView]:
        module = self._normalize_module_or_fail(module)
        if not module.ok or module.value is None:
            return self.runtime.foundation.fail(module.issues)
        gate = self._metadata_available(repo_root)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        inspected = self._repo_module_outline(gate.value["upstream_root"], module.value)
        if not inspected.ok:
            return self.runtime.foundation.fail(inspected.issues)
        outline = inspected.value or {}
        declarations = [self._decl_item(item, module=module.value) for item in self._items(outline, key="declarations")]
        if kind_filter:
            declarations = [item for item in declarations if item.kind == kind_filter]
        return self.runtime.foundation.ok(
            UpstreamModuleDeclsView(
                module=module.value,
                declarations=declarations,
                imports=self._string_list(outline.get("imports")),
                summary=f"Loaded {len(declarations)} declarations from upstream module {module.value}.",
            )
        )

    def inspect_upstream_declaration(self, repo_root: Path, *, module: str, decl_name: str) -> ServiceResult[UpstreamDeclDetailView]:
        module_result = self._normalize_module_or_fail(module)
        if not module_result.ok or module_result.value is None:
            return self.runtime.foundation.fail(module_result.issues)
        if not decl_name or not decl_name.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("upstream_decl_name_required", "Declaration name is required.", field="decl_name"))
        gate = self._metadata_available(repo_root)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        outline = self._repo_module_outline(gate.value["upstream_root"], module_result.value)
        if not outline.ok:
            return self.runtime.foundation.fail(outline.issues)
        outline_value = outline.value or {}
        outline_item = self._find_decl_item(outline_value, decl_name.strip()) or {}
        extracted = self.runtime.external.lean_mcp_toolkit.extract_declaration(gate.value["upstream_root"], module_result.value, decl_name.strip())
        if not extracted.ok:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(extracted.issue_code or "upstream_decl_inspect_failed", extracted.summary, object_ref=decl_name))
        code = extracted.code or extracted.raw_excerpt or ""
        return self.runtime.foundation.ok(
            UpstreamDeclDetailView(
                module=extracted.module or module_result.value,
                decl_name=decl_name.strip(),
                kind=getattr(extracted, "kind", None) or self._field(outline_item, "kind", "decl_kind"),
                signature=getattr(extracted, "signature", None) or self._field(outline_item, "signature", "type", "header_preview"),
                code_excerpt=self._excerpt(code),
                imports=self._string_list(outline_value.get("imports")),
                summary=extracted.summary,
            )
        )

    def read_upstream_source_context(
        self,
        repo_root: Path,
        *,
        module: str,
        decl_name: str | None = None,
        line_window: int = 20,
    ) -> ServiceResult[UpstreamSourceContextView]:
        module_result = self._normalize_module_or_fail(module)
        if not module_result.ok or module_result.value is None:
            return self.runtime.foundation.fail(module_result.issues)
        gate = self._metadata_available(repo_root)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        line_window = max(1, min(int(line_window), 200))
        start_line: int | None = None
        end_line: int | None = None
        if decl_name and decl_name.strip():
            outline = self._repo_module_outline(gate.value["upstream_root"], module_result.value)
            if not outline.ok:
                return self.runtime.foundation.fail(outline.issues)
            item = self._find_decl_item(outline.value or {}, decl_name.strip())
            raw_start = item.get("line_start") if item else None
            raw_end = item.get("line_end") if item else None
            if raw_start is not None:
                try:
                    center = int(raw_start)
                    start_line = max(1, center - line_window)
                    end_line = int(raw_end) + line_window if raw_end is not None else center + line_window
                except (TypeError, ValueError):
                    start_line = None
                    end_line = None
        result = self.runtime.external.lean_mcp_toolkit.call_tool(
            "repo_nav.read",
            {
                "repo_root": str(gate.value["upstream_root"]),
                "target": module_result.value,
                "start_line": start_line,
                "end_line": end_line,
                "max_lines": line_window if start_line is None else None,
                "with_line_numbers": True,
            },
        )
        if not result.ok:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(result.issue_code or "upstream_source_context_failed", result.summary or "Upstream source context read failed."))
        text = self._text_from_value(result.value) or result.raw_excerpt or ""
        excerpt = self._excerpt(text)
        return self.runtime.foundation.ok(
            UpstreamSourceContextView(
                module=module_result.value,
                decl_name=self._strip(decl_name),
                text=excerpt,
                truncated=len(text) > len(excerpt),
                summary=f"Read upstream source context for {module_result.value}.",
            )
        )

    def capture_upstream_declaration_code(
        self,
        repo_root: Path,
        *,
        module: str,
        decl_name: str,
        capture_mode: Literal["statement_only", "full_declaration"],
    ) -> ServiceResult[UpstreamCaptureView]:
        module_result = self._normalize_module_or_fail(module)
        if not module_result.ok or module_result.value is None:
            return self.runtime.foundation.fail(module_result.issues)
        if not decl_name or not decl_name.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("upstream_decl_name_required", "Declaration name is required.", field="decl_name"))
        gate = self._metadata_available(repo_root)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        extracted = self.runtime.external.lean_mcp_toolkit.extract_declaration(gate.value["upstream_root"], module_result.value, decl_name.strip())
        if not extracted.ok or not extracted.code:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(extracted.issue_code or "upstream_decl_capture_failed", extracted.summary, object_ref=decl_name))
        code = extracted.code if capture_mode == "full_declaration" else self._statement_only(extracted.code)
        scan = self.lean_check.detect_sorry_axiom(code)
        if not scan.ok or scan.value is None:
            return self.runtime.foundation.fail(scan.issues)
        return self.runtime.foundation.ok(
            UpstreamCaptureView(
                module=extracted.module or module_result.value,
                decl_name=decl_name.strip(),
                capture_mode=capture_mode,
                code=code,
                scan=scan.value,
                summary=f"Captured {capture_mode} code for upstream declaration {decl_name.strip()}.",
            )
        )

    def inspect_upstream_module_imports(self, repo_root: Path, *, module: str) -> ServiceResult[UpstreamModuleImportsView]:
        module_result = self._normalize_module_or_fail(module)
        if not module_result.ok or module_result.value is None:
            return self.runtime.foundation.fail(module_result.issues)
        gate = self._metadata_available(repo_root)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        inspected = self._repo_module_outline(gate.value["upstream_root"], module_result.value)
        if not inspected.ok:
            return self.runtime.foundation.fail(inspected.issues)
        outline = inspected.value or {}
        imports = sorted(set(self._string_list(outline.get("imports"))))
        namespace_hints = sorted(set(self._scope_targets(outline, kinds={"namespace", "section", "open"})))
        metadata = gate.value["metadata"]
        return self.runtime.foundation.ok(
            UpstreamModuleImportsView(
                module=module_result.value,
                imports=imports,
                namespace_hints=namespace_hints,
                package_hints=sorted({item for item in [metadata.package_name, metadata.dependency_name] if item}),
                summary=f"Loaded {len(imports)} imports for upstream module {module_result.value}.",
            )
        )

    def _metadata_available(self, repo_root: Path) -> ServiceResult[dict[str, Any]]:
        metadata = self.upstream_metadata._load_metadata(repo_root)  # Internal service-to-service truth access.
        if not metadata.ok or metadata.value is None:
            return self.runtime.foundation.fail(metadata.issues)
        upstream_root = self._resolve_upstream_root(repo_root, metadata.value)
        if not upstream_root.ok or upstream_root.value is None:
            return self.runtime.foundation.fail(upstream_root.issues)
        return self.runtime.foundation.ok({"metadata": metadata.value, "upstream_root": upstream_root.value})

    def _resolve_upstream_root(self, repo_root: Path, metadata: Any) -> ServiceResult[Path]:
        if metadata.source_kind == "local_path":
            local = Path(metadata.local_path or "")
            root = local if local.is_absolute() else Path(repo_root) / local
            if root.is_dir():
                return self.runtime.foundation.ok(root)
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "adapter_upstream_root_missing",
                    "Configured local upstream path does not exist or is not a directory.",
                    object_ref=metadata.dependency_name,
                )
            )
        candidates = []
        for name in [metadata.dependency_name, metadata.package_name]:
            if name:
                base = Path(repo_root) / ".lake" / "packages" / name
                candidates.append(base / metadata.subdir if metadata.subdir else base)
        for candidate in candidates:
            if candidate.is_dir():
                return self.runtime.foundation.ok(candidate)
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "adapter_upstream_root_missing",
                "Cannot locate upstream Lake dependency checkout under .lake/packages.",
                object_ref=metadata.dependency_name,
                details={"candidates": "\n".join(str(path) for path in candidates)},
                suggested_action="Run lake update or configure adapter upstream as local_path.",
            )
        )

    def _repo_module_outline(self, upstream_root: Path, module: str) -> ServiceResult[dict[str, Any]]:
        result = self.runtime.external.lean_mcp_toolkit.call_tool(
            "repo_nav.file_outline",
            {
                "repo_root": str(upstream_root),
                "target": module,
                "include_imports": True,
                "include_module_doc": True,
                "include_section_doc": True,
                "include_decl_headers": True,
                "include_scope_cmds": True,
                "limit_decls": 300,
            },
        )
        if not result.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    result.issue_code or "upstream_module_inspect_failed",
                    result.summary or "Upstream module inspection failed.",
                    object_ref=module,
                )
            )
        value = result.value if isinstance(result.value, dict) else {}
        if value.get("success") is False:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "upstream_module_inspect_failed",
                    str(value.get("error_message") or "Upstream module inspection failed."),
                    object_ref=module,
                )
            )
        return self.runtime.foundation.ok(value)

    def _normalize_module_or_fail(self, module: str) -> ServiceResult[str]:
        value = self._normalize_module(module)
        if value is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("upstream_module_invalid", "Upstream module name is invalid.", field="module", current=module))
        return self.runtime.foundation.ok(value)

    def _normalize_module(self, module: str) -> str | None:
        value = module.strip()
        if not value or any(ch.isspace() for ch in value):
            return None
        if "/" in value or "\\" in value or ".." in value:
            return None
        if any(not part for part in value.split(".")):
            return None
        return value

    def _normalize_limit(self, limit: int) -> int:
        return max(1, min(int(limit), 100))

    def _decl_item(self, item: dict[str, Any], module: str | None = None) -> UpstreamDeclSearchItem:
        name = self._field(item, "decl_name", "full_name", "name", "declaration") or self._field(item, "value") or "<unknown>"
        return UpstreamDeclSearchItem(
            module=module or self._field(item, "module", "module_name", "module_path"),
            decl_name=name,
            kind=self._field(item, "kind", "decl_kind"),
            statement=self._field(item, "statement", "type", "signature", "summary", "header_preview"),
            match_reason=self._field(item, "match_reason", "reason"),
        )

    def _items(self, value: dict[str, Any] | list[Any] | str | None, key: str | None = None) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [item if isinstance(item, dict) else {"value": item} for item in value]
        if isinstance(value, dict):
            raw = (value.get(key) if key else None) or value.get("items") or value.get("results") or value.get("matches") or value.get("declarations") or value.get("entries") or []
            if isinstance(raw, list):
                return [item if isinstance(item, dict) else {"value": item} for item in raw]
            return [raw if isinstance(raw, dict) else {"value": raw}]
        if value is None:
            return []
        return [{"value": value}]

    def _field(self, item: dict[str, Any], *names: str) -> str | None:
        for name in names:
            value = item.get(name)
            if value is not None and str(value).strip():
                return str(value).strip()
        return None

    def _find_decl_item(self, value: dict[str, Any], decl_name: str) -> dict[str, Any] | None:
        candidates = self._items(value, key="declarations")
        short = decl_name.rsplit(".", 1)[-1]
        return next(
            (
                item
                for item in candidates
                if any(self._field(item, field) in {decl_name, short} for field in ["full_name", "name", "short_name"])
            ),
            None,
        )

    def _string_list(self, value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, tuple):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _scope_targets(self, value: dict[str, Any], *, kinds: set[str]) -> list[str]:
        targets = []
        for item in self._items(value, key="scope_cmds"):
            kind = self._field(item, "kind")
            target = self._field(item, "target")
            if kind in kinds and target:
                targets.append(target)
        return targets

    def _text_from_value(self, value: dict[str, Any] | list[Any] | str | None) -> str | None:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ["text", "source", "context", "code", "summary"]:
                raw = value.get(key)
                if raw is not None:
                    return str(raw)
        return None

    def _statement_only(self, code: str) -> str:
        marker = ":="
        if marker not in code:
            return code
        head = code.split(marker, 1)[0].rstrip()
        if head.lstrip().startswith(("theorem ", "lemma ")):
            return f"{head} := by\n  sorry"
        return f"{head} := by\n  sorry"

    def _excerpt(self, text: str) -> str:
        if len(text) <= self._MAX_CONTEXT_CHARS:
            return text
        return text[: self._MAX_CONTEXT_CHARS] + "\n...[truncated]"

    @staticmethod
    def _strip(value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None
