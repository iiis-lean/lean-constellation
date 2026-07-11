"""Adapter ready preflight and final gate."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.adapter.adapter_decl_catalog import AdapterDeclCatalogComponent
from lean_constellation.services.adapter.interface_binding import InterfaceBindingComponent
from lean_constellation.services.adapter.projection import ProjectionComponent
from lean_constellation.services.adapter.upstream_metadata import UpstreamMetadataComponent
from lean_constellation.services.foundation import GateReport, ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class AdapterReadyPreflightView(StrictModel):
    gate: GateReport
    summary: str


class AdapterReadyGateView(StrictModel):
    gate: GateReport
    summary: str


class AdapterReadyIssueCategory(StrictModel):
    category: str
    issue_count: int
    object_refs: list[str] = Field(default_factory=list)


class ReadyGateComponent:
    """Aggregate adapter upstream, catalog, binding, and projection gates."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        upstream_metadata: UpstreamMetadataComponent | None = None,
        adapter_decl_catalog: AdapterDeclCatalogComponent | None = None,
        interface_binding: InterfaceBindingComponent | None = None,
        projection: ProjectionComponent | None = None,
    ) -> None:
        self.runtime = runtime
        self.upstream_metadata = upstream_metadata or UpstreamMetadataComponent(runtime)
        self.adapter_decl_catalog = adapter_decl_catalog or AdapterDeclCatalogComponent(runtime)
        self.interface_binding = interface_binding or InterfaceBindingComponent(
            runtime,
            adapter_decl_catalog=self.adapter_decl_catalog,
        )
        self.projection = projection or ProjectionComponent(
            runtime,
            adapter_decl_catalog=self.adapter_decl_catalog,
        )

    def check_adapter_catalog_ready_preflight(self, repo_root: Path) -> ServiceResult[GateReport]:
        return self._check(repo_root, gate_name="adapter_catalog_ready_preflight", include_projection=False)

    def check_adapter_ready(self, repo_root: Path) -> ServiceResult[GateReport]:
        return self._check(repo_root, gate_name="adapter_ready", include_projection=True)

    def _check(self, repo_root: Path, *, gate_name: str, include_projection: bool) -> ServiceResult[GateReport]:
        reports: list[GateReport] = []

        upstream = self.upstream_metadata.validate_upstream_metadata(repo_root)
        if not upstream.ok or upstream.value is None:
            return self.runtime.foundation.fail(upstream.issues)
        reports.append(upstream.value)

        completeness = self.adapter_decl_catalog.check_adapter_decl_completeness(repo_root)
        if not completeness.ok or completeness.value is None:
            return self.runtime.foundation.fail(completeness.issues)
        if completeness.value.complete:
            reports.append(
                self.runtime.foundation.gate_passed(
                    "adapter_decl_completeness",
                    summary=f"{len(completeness.value.checked_names)} adapter declarations are complete.",
                )
            )
        else:
            reports.append(
                self.runtime.foundation.gate_failed(
                    "adapter_decl_completeness",
                    completeness.value.issues,
                    summary=completeness.value.summary,
                )
            )

        bindings = self.interface_binding.validate_adapter_interface_bindings(repo_root)
        if not bindings.ok or bindings.value is None:
            return self.runtime.foundation.fail(bindings.issues)
        reports.append(bindings.value)

        protected_interfaces = self.runtime.node.check_root_main_handoff_interfaces(repo_root)
        if not protected_interfaces.ok or protected_interfaces.value is None:
            return self.runtime.foundation.fail(protected_interfaces.issues)
        reports.append(protected_interfaces.value)

        if include_projection:
            projection = self.projection.check_adapter_projection(repo_root)
            if not projection.ok or projection.value is None:
                return self.runtime.foundation.fail(projection.issues)
            reports.append(projection.value)

        return self.runtime.foundation.ok(self.runtime.foundation.merge_gate_reports(gate_name, reports))
