"""Adapter projection coordination."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.adapter.adapter_decl_catalog import AdapterDeclCatalogComponent
from lean_constellation.services.foundation import FoundationContext, FoundationService, GateReport, ServiceResult
from lean_constellation.services.lean_projection import LeanProjectionService
from lean_constellation.services.lean_projection.node_projection import ProjectionView


class AdapterImportModuleItem(StrictModel):
    module: str
    decl_names: list[str] = Field(default_factory=list)


class AdapterImportPreviewView(StrictModel):
    modules: list[AdapterImportModuleItem] = Field(default_factory=list)
    module_count: int
    summary: str


class AdapterProjectionIssueView(StrictModel):
    issue_code: str
    module: str | None = None
    message: str


class ProjectionComponent:
    """Preview, refresh, and check generated adapter Main.Interfaces."""

    def __init__(
        self,
        foundation: FoundationService | None = None,
        adapter_decl_catalog: AdapterDeclCatalogComponent | None = None,
        lean_projection: LeanProjectionService | None = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.adapter_decl_catalog = adapter_decl_catalog or AdapterDeclCatalogComponent(self.foundation)
        self.lean_projection = lean_projection or LeanProjectionService(foundation=self.foundation)

    def preview_adapter_import_modules(self, repo_root: Path) -> ServiceResult[AdapterImportPreviewView]:
        modules = self.adapter_decl_catalog.list_registered_adapter_modules(repo_root)
        if not modules.ok or modules.value is None:
            return self.foundation.fail(modules.issues)
        items = [
            AdapterImportModuleItem(module=item.module, decl_names=item.decl_names)
            for item in modules.value.modules
        ]
        return self.foundation.ok(
            AdapterImportPreviewView(
                modules=items,
                module_count=len(items),
                summary=f"Adapter projection would import {len(items)} upstream modules.",
            )
        )

    def refresh_adapter_projection(self, repo_root: Path) -> ServiceResult[ProjectionView]:
        return self.lean_projection.adapter_facade.refresh_adapter_interfaces(repo_root)

    def check_adapter_projection(self, repo_root: Path) -> ServiceResult[GateReport]:
        preview = self.preview_adapter_import_modules(repo_root)
        if not preview.ok or preview.value is None:
            return self.foundation.fail(preview.issues)
        sync = self.lean_projection.adapter_facade.check_adapter_interfaces_sync(repo_root)
        if not sync.ok or sync.value is None:
            return self.foundation.fail(sync.issues)
        path = self.foundation.layout.adapter_interfaces_path(FoundationContext(repo_root=Path(repo_root)))
        actual = self._read_public_imports(path)
        expected = sorted(item.module for item in preview.value.modules)
        if actual != expected:
            issues = []
            missing = sorted(set(expected) - set(actual))
            extra = sorted(set(actual) - set(expected))
            for module in missing:
                issues.append(
                    self.foundation.issue(
                        "adapter_projection_missing_import",
                        "Generated adapter projection is missing an expected public import.",
                        object_ref=module,
                    )
                )
            for module in extra:
                issues.append(
                    self.foundation.issue(
                        "adapter_projection_extra_import",
                        "Generated adapter projection contains an unexpected public import.",
                        object_ref=module,
                    )
                )
            return self.foundation.ok(
                self.foundation.gate_failed(
                    "adapter_projection",
                    issues,
                    summary="Adapter projection import set does not match active catalog modules.",
                )
            )
        if not sync.value.passed:
            return self.foundation.ok(sync.value)
        return self.foundation.ok(
            self.foundation.gate_passed(
                "adapter_projection",
                summary=f"Adapter projection is synchronized with {len(expected)} modules.",
            )
        )

    def _read_public_imports(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        modules = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("public import "):
                module = stripped.removeprefix("public import ").strip()
                if module:
                    modules.append(module)
        return sorted(set(modules))
