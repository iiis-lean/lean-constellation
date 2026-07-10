"""AdapterService composition and public wrappers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.preparation import SourceCorpusMode
from lean_constellation.domain.repo import RepoFormat
from lean_constellation.services.adapter.adapter_decl_catalog import (
    AdapterCatalogInitView,
    AdapterDeclCatalogComponent,
    AdapterDeclCompletenessView,
    AdapterDeclMatchView,
    AdapterDeclSummaryView,
    AdapterDeclView,
    AdapterModuleSummaryView,
)
from lean_constellation.services.adapter.interface_binding import (
    AdapterUnboundInterfaceView,
    InterfaceBindingComponent,
    InterfaceBindingView,
)
from lean_constellation.services.adapter.projection import AdapterImportPreviewView, ProjectionComponent
from lean_constellation.services.adapter.ready_gate import ReadyGateComponent
from lean_constellation.services.adapter.upstream_metadata import (
    AdapterUpstreamStatusView,
    AdapterUpstreamView,
    UpstreamMetadataComponent,
)
from lean_constellation.services.adapter.upstream_navigation import (
    UpstreamCaptureView,
    UpstreamDeclDetailView,
    UpstreamDeclSearchView,
    UpstreamModuleDeclsView,
    UpstreamModuleImportsView,
    UpstreamModuleSearchView,
    UpstreamNavigationComponent,
    UpstreamSourceContextView,
)
from lean_constellation.services.foundation import GateReport, ServiceResult
from lean_constellation.services.lean_projection.adapter_facade import AdapterModuleListView

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class AdapterPreparationValidationView(StrictModel):
    outcome: Literal["passed", "invalid_input", "blocked"]
    upstream_summary: str | None = None
    issue_code: str | None = None
    summary: str
    suggested_fix: str | None = None


class AdapterInputView(StrictModel):
    repo_root: str
    goal: str | None = None
    interface_count: int = 0
    upstream_status: AdapterUpstreamStatusView | None = None
    summary: str


class AdapterCatalogSubmissionView(StrictModel):
    submission_type: Literal["adapter_catalog_ready", "adapter_catalog_blocked"]
    accepted: bool
    summary: str
    gate: GateReport | None = None
    reason: str | None = None
    missing_interfaces: list[str] = Field(default_factory=list)
    evidence_summary: str | None = None
    suggested_next_action: str | None = None


class AdapterService:
    """Composition root for adapter repo preparation services."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        upstream_metadata: UpstreamMetadataComponent | None = None,
        upstream_navigation: UpstreamNavigationComponent | None = None,
        adapter_decl_catalog: AdapterDeclCatalogComponent | None = None,
        interface_binding: InterfaceBindingComponent | None = None,
        projection: ProjectionComponent | None = None,
        ready_gate: ReadyGateComponent | None = None,
    ) -> None:
        self.runtime = runtime
        self.upstream_metadata = upstream_metadata or UpstreamMetadataComponent(runtime)
        self.adapter_decl_catalog = adapter_decl_catalog or AdapterDeclCatalogComponent(runtime)
        self.upstream_navigation = upstream_navigation or UpstreamNavigationComponent(
            runtime,
            upstream_metadata=self.upstream_metadata,
            lean_check=self._lean_projection().lean_check,
        )
        self.interface_binding = interface_binding or InterfaceBindingComponent(
            runtime,
            contract=self._node().contract,
            adapter_decl_catalog=self.adapter_decl_catalog,
        )
        self.projection = projection or ProjectionComponent(
            runtime,
            adapter_decl_catalog=self.adapter_decl_catalog,
        )
        self.ready_gate = ready_gate or ReadyGateComponent(
            runtime,
            upstream_metadata=self.upstream_metadata,
            adapter_decl_catalog=self.adapter_decl_catalog,
            interface_binding=self.interface_binding,
            projection=self.projection,
        )

    def _repo_workspace(self) -> object:
        return self.runtime.require_app_service("repo_workspace")

    def _node(self) -> object:
        return self.runtime.require_app_service("node")

    def _lean_projection(self) -> object:
        return self.runtime.require_app_service("lean_projection")

    def validate_adapter_preparation_input(self, repo_root: Path) -> ServiceResult[AdapterPreparationValidationView]:
        repo_format = self._repo_workspace().metadata.get_repo_format(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        if repo_format.value.repo_format != RepoFormat.ADAPTER:
            return self.runtime.foundation.ok(
                AdapterPreparationValidationView(
                    outcome="invalid_input",
                    issue_code="repo_format_not_adapter",
                    summary="Repo format is not adapter.",
                    suggested_fix="Initialize or classify this provider repo as adapter first.",
                )
            )
        prep = self._repo_workspace().preparation.get_preparation_input(repo_root)
        if not prep.ok or prep.value is None:
            return self.runtime.foundation.ok(
                AdapterPreparationValidationView(
                    outcome="invalid_input",
                    issue_code="preparation_input_missing",
                    summary="Adapter preparation input is missing.",
                    suggested_fix="Create preparation input before adapter preparation.",
                )
            )
        if prep.value.input.source_corpus_mode != SourceCorpusMode.NONE:
            return self.runtime.foundation.ok(
                AdapterPreparationValidationView(
                    outcome="invalid_input",
                    issue_code="adapter_source_corpus_mode_invalid",
                    summary="Adapter repo preparation requires source_corpus_mode=none.",
                    suggested_fix="Use native repo preparation for source corpus, or rebuild this repo as adapter input.",
                )
            )
        upstream_status = self.upstream_metadata.get_adapter_upstream_status(repo_root)
        if not upstream_status.ok or upstream_status.value is None:
            return self.runtime.foundation.ok(
                AdapterPreparationValidationView(
                    outcome="blocked",
                    issue_code="adapter_upstream_missing",
                    summary="Adapter upstream metadata is missing.",
                    suggested_fix="Run adapter repo setup and write adapter_upstream metadata.",
                )
            )
        deps = self._repo_workspace().lake_dependency.parse_lake_dependencies(repo_root)
        if not deps.ok or deps.value is None:
            return self.runtime.foundation.fail(deps.issues)
        dependency_name = upstream_status.value.dependency_name
        if dependency_name and dependency_name not in {dep.name for dep in deps.value.dependencies}:
            return self.runtime.foundation.ok(
                AdapterPreparationValidationView(
                    outcome="blocked",
                    upstream_summary=upstream_status.value.source_summary,
                    issue_code="adapter_upstream_dependency_missing",
                    summary=f"Adapter upstream Lake dependency is not attached: {dependency_name}.",
                    suggested_fix="Attach the upstream Lake dependency and run lake update.",
                )
            )
        upstream_gate = self.upstream_metadata.validate_upstream_metadata(repo_root)
        if not upstream_gate.ok or upstream_gate.value is None:
            return self.runtime.foundation.fail(upstream_gate.issues)
        if not upstream_gate.value.passed:
            return self.runtime.foundation.ok(
                AdapterPreparationValidationView(
                    outcome="blocked",
                    upstream_summary=upstream_status.value.source_summary,
                    issue_code=upstream_gate.value.issues[0].kind if upstream_gate.value.issues else "adapter_upstream_not_ready",
                    summary=upstream_gate.value.summary or "Adapter upstream metadata is not ready.",
                    suggested_fix="Complete upstream setup and mark the trusted build summary.",
                )
            )
        return self.runtime.foundation.ok(
            AdapterPreparationValidationView(
                outcome="passed",
                upstream_summary=upstream_status.value.source_summary,
                summary="Adapter preparation input is valid.",
            )
        )

    def inspect_adapter_input(self, repo_root: Path) -> ServiceResult[AdapterInputView]:
        prep = self._repo_workspace().preparation.get_preparation_input(repo_root)
        upstream_status = self.upstream_metadata.get_adapter_upstream_status(repo_root)
        if not prep.ok or prep.value is None:
            return self.runtime.foundation.fail(prep.issues)
        upstream_available = upstream_status.ok and upstream_status.value is not None
        summary = f"Adapter input has {len(prep.value.input.interface_inputs)} required interfaces."
        if not upstream_available:
            summary = f"{summary} Upstream metadata is not available yet."
        return self.runtime.foundation.ok(
            AdapterInputView(
                repo_root=str(Path(repo_root)),
                goal=prep.value.input.goal,
                interface_count=len(prep.value.input.interface_inputs),
                upstream_status=upstream_status.value if upstream_available else None,
                summary=summary,
            )
        )

    def get_adapter_upstream_metadata(self, repo_root: Path) -> ServiceResult[AdapterUpstreamView]:
        return self.upstream_metadata.get_adapter_upstream_metadata(repo_root)

    def get_adapter_upstream_status(self, repo_root: Path) -> ServiceResult[AdapterUpstreamStatusView]:
        return self.upstream_metadata.get_adapter_upstream_status(repo_root)

    def write_adapter_upstream_metadata(
        self,
        repo_root: Path,
        *,
        source_kind: Literal["git", "local_path"] = "git",
        git_url: str | None = None,
        revision: str | None = None,
        subdir: str | None = None,
        local_path: str | None = None,
        package_name: str,
        dependency_name: str,
        evidence_summary: str | None = None,
        setup_summary: str | None = None,
        visible_modules: list[str] | None = None,
    ) -> ServiceResult[AdapterUpstreamView]:
        return self.upstream_metadata.write_adapter_upstream_metadata(
            repo_root,
            source_kind=source_kind,
            git_url=git_url,
            revision=revision,
            subdir=subdir,
            local_path=local_path,
            package_name=package_name,
            dependency_name=dependency_name,
            evidence_summary=evidence_summary,
            setup_summary=setup_summary,
            visible_modules=visible_modules,
        )

    def mark_upstream_build_trusted(self, repo_root: Path, *, summary: str) -> ServiceResult[AdapterUpstreamView]:
        return self.upstream_metadata.mark_upstream_build_trusted(repo_root, summary=summary)

    def record_visible_upstream_modules(self, repo_root: Path, *, modules: list[str], summary: str | None = None) -> ServiceResult[AdapterUpstreamView]:
        return self.upstream_metadata.record_visible_upstream_modules(repo_root, modules=modules, summary=summary)

    def search_upstream_declarations(
        self,
        repo_root: Path,
        *,
        query: str,
        kind_filter: str | None = None,
        module_filter: str | None = None,
        limit: int = 20,
    ) -> ServiceResult[UpstreamDeclSearchView]:
        return self.upstream_navigation.search_upstream_declarations(
            repo_root,
            query=query,
            kind_filter=kind_filter,
            module_filter=module_filter,
            limit=limit,
        )

    def search_upstream_modules(self, repo_root: Path, *, query: str, limit: int = 20) -> ServiceResult[UpstreamModuleSearchView]:
        return self.upstream_navigation.search_upstream_modules(repo_root, query=query, limit=limit)

    def list_upstream_module_declarations(
        self,
        repo_root: Path,
        *,
        module: str,
        kind_filter: str | None = None,
    ) -> ServiceResult[UpstreamModuleDeclsView]:
        return self.upstream_navigation.list_upstream_module_declarations(repo_root, module=module, kind_filter=kind_filter)

    def inspect_upstream_declaration(self, repo_root: Path, *, module: str, decl_name: str) -> ServiceResult[UpstreamDeclDetailView]:
        return self.upstream_navigation.inspect_upstream_declaration(repo_root, module=module, decl_name=decl_name)

    def read_upstream_source_context(
        self,
        repo_root: Path,
        *,
        module: str,
        decl_name: str | None = None,
        line_window: int = 20,
    ) -> ServiceResult[UpstreamSourceContextView]:
        return self.upstream_navigation.read_upstream_source_context(
            repo_root,
            module=module,
            decl_name=decl_name,
            line_window=line_window,
        )

    def capture_upstream_declaration_code(
        self,
        repo_root: Path,
        *,
        module: str,
        decl_name: str,
        capture_mode: Literal["statement_only", "full_declaration"],
    ) -> ServiceResult[UpstreamCaptureView]:
        return self.upstream_navigation.capture_upstream_declaration_code(
            repo_root,
            module=module,
            decl_name=decl_name,
            capture_mode=capture_mode,
        )

    def inspect_upstream_module_imports(self, repo_root: Path, *, module: str) -> ServiceResult[UpstreamModuleImportsView]:
        return self.upstream_navigation.inspect_upstream_module_imports(repo_root, module=module)

    def ensure_flat_main_catalog(self, repo_root: Path) -> ServiceResult[AdapterCatalogInitView]:
        root = self._node().ensure_adapter_root_main_contract(repo_root)
        if not root.ok:
            return self.runtime.foundation.fail(root.issues)
        return self.adapter_decl_catalog.ensure_flat_main_catalog(repo_root)

    def create_adapter_decl(
        self,
        repo_root: Path,
        *,
        name: str,
        kind: str,
        module: str,
        plan_summary: str,
    ) -> ServiceResult[AdapterDeclView]:
        return self.adapter_decl_catalog.create_adapter_decl(
            repo_root,
            name=name,
            kind=kind,
            module=module,
            plan_summary=plan_summary,
        )

    def set_adapter_statement_formal(
        self,
        repo_root: Path,
        *,
        name: str,
        code: str,
        upstream_decl_name: str | None = None,
    ) -> ServiceResult[AdapterDeclView]:
        return self.adapter_decl_catalog.set_adapter_statement_formal(
            repo_root,
            name=name,
            code=code,
            upstream_decl_name=upstream_decl_name,
        )

    def set_adapter_statement_nl(
        self,
        repo_root: Path,
        *,
        name: str,
        summary: str,
        detail: str | None = None,
    ) -> ServiceResult[AdapterDeclView]:
        return self.adapter_decl_catalog.set_adapter_statement_nl(repo_root, name=name, summary=summary, detail=detail)

    def add_adapter_statement_origin(
        self,
        repo_root: Path,
        *,
        name: str,
        origin_text: str,
        source_hint: str | None = None,
    ) -> ServiceResult[AdapterDeclView]:
        return self.adapter_decl_catalog.add_adapter_statement_origin(
            repo_root,
            name=name,
            origin_text=origin_text,
            source_hint=source_hint,
        )

    def add_adapter_statement_dep(self, repo_root: Path, *, name: str, dep_name: str, reason: str) -> ServiceResult[AdapterDeclView]:
        return self.adapter_decl_catalog.add_adapter_statement_dep(repo_root, name=name, dep_name=dep_name, reason=reason)

    def remove_adapter_statement_dep(self, repo_root: Path, *, name: str, dep_name: str) -> ServiceResult[AdapterDeclView]:
        return self.adapter_decl_catalog.remove_adapter_statement_dep(repo_root, name=name, dep_name=dep_name)

    def set_adapter_proof_formal(
        self,
        repo_root: Path,
        *,
        name: str,
        code: str,
        upstream_decl_name: str | None = None,
    ) -> ServiceResult[AdapterDeclView]:
        return self.adapter_decl_catalog.set_adapter_proof_formal(
            repo_root,
            name=name,
            code=code,
            upstream_decl_name=upstream_decl_name,
        )

    def set_adapter_proof_nl(
        self,
        repo_root: Path,
        *,
        name: str,
        summary: str,
        detail: str | None = None,
    ) -> ServiceResult[AdapterDeclView]:
        return self.adapter_decl_catalog.set_adapter_proof_nl(repo_root, name=name, summary=summary, detail=detail)

    def add_adapter_proof_origin(
        self,
        repo_root: Path,
        *,
        name: str,
        origin_text: str,
        source_hint: str | None = None,
    ) -> ServiceResult[AdapterDeclView]:
        return self.adapter_decl_catalog.add_adapter_proof_origin(
            repo_root,
            name=name,
            origin_text=origin_text,
            source_hint=source_hint,
        )

    def add_adapter_proof_dep(self, repo_root: Path, *, name: str, dep_name: str, reason: str) -> ServiceResult[AdapterDeclView]:
        return self.adapter_decl_catalog.add_adapter_proof_dep(repo_root, name=name, dep_name=dep_name, reason=reason)

    def remove_adapter_proof_dep(self, repo_root: Path, *, name: str, dep_name: str) -> ServiceResult[AdapterDeclView]:
        return self.adapter_decl_catalog.remove_adapter_proof_dep(repo_root, name=name, dep_name=dep_name)

    def list_adapter_decls(
        self,
        repo_root: Path,
        *,
        module_filter: str | None = None,
        kind_filter: str | None = None,
        name_query: str | None = None,
    ) -> ServiceResult[list[AdapterDeclSummaryView]]:
        return self.adapter_decl_catalog.list_adapter_decls(
            repo_root,
            module_filter=module_filter,
            kind_filter=kind_filter,
            name_query=name_query,
        )

    def inspect_adapter_decl(self, repo_root: Path, *, name: str) -> ServiceResult[AdapterDeclView]:
        return self.adapter_decl_catalog.inspect_adapter_decl(repo_root, name=name)

    def list_registered_adapter_modules(self, repo_root: Path) -> ServiceResult[AdapterModuleSummaryView]:
        return self.adapter_decl_catalog.list_registered_adapter_modules(repo_root)

    def check_adapter_decl_completeness(self, repo_root: Path, *, name: str | None = None) -> ServiceResult[AdapterDeclCompletenessView]:
        return self.adapter_decl_catalog.check_adapter_decl_completeness(repo_root, name=name)

    def find_adapter_decl_by_upstream(
        self,
        repo_root: Path,
        *,
        module: str,
        upstream_decl_name: str | None = None,
        adapter_name_query: str | None = None,
    ) -> ServiceResult[AdapterDeclMatchView]:
        return self.adapter_decl_catalog.find_adapter_decl_by_upstream(
            repo_root,
            module=module,
            upstream_decl_name=upstream_decl_name,
            adapter_name_query=adapter_name_query,
        )

    def finalize_adapter_decl(self, repo_root: Path, *, name: str) -> ServiceResult[AdapterDeclView]:
        return self.adapter_decl_catalog.finalize_adapter_decl(repo_root, name=name)

    def bind_adapter_interface(
        self,
        repo_root: Path,
        *,
        interface_name: str,
        decl_name: str,
        binding_summary: str,
    ) -> ServiceResult[InterfaceBindingView]:
        return self.interface_binding.bind_adapter_interface(
            repo_root,
            interface_name=interface_name,
            decl_name=decl_name,
            binding_summary=binding_summary,
        )

    def unbind_adapter_interface(self, repo_root: Path, *, interface_name: str, reason: str) -> ServiceResult[InterfaceBindingView]:
        return self.interface_binding.unbind_adapter_interface(repo_root, interface_name=interface_name, reason=reason)

    def list_unbound_adapter_interfaces(self, repo_root: Path) -> ServiceResult[AdapterUnboundInterfaceView]:
        return self.interface_binding.list_unbound_adapter_interfaces(repo_root)

    def validate_adapter_interface_bindings(self, repo_root: Path) -> ServiceResult[GateReport]:
        return self.interface_binding.validate_adapter_interface_bindings(repo_root)

    def preview_adapter_import_modules(self, repo_root: Path) -> ServiceResult[AdapterImportPreviewView]:
        return self.projection.preview_adapter_import_modules(repo_root)

    def refresh_adapter_projection(self, repo_root: Path) -> ServiceResult[object]:
        return self.projection.refresh_adapter_projection(repo_root)

    def check_adapter_projection(self, repo_root: Path) -> ServiceResult[GateReport]:
        return self.projection.check_adapter_projection(repo_root)

    def check_adapter_catalog_ready_preflight(self, repo_root: Path) -> ServiceResult[GateReport]:
        return self.ready_gate.check_adapter_catalog_ready_preflight(repo_root)

    def check_adapter_ready(self, repo_root: Path) -> ServiceResult[GateReport]:
        return self.ready_gate.check_adapter_ready(repo_root)

    def submit_adapter_catalog_ready(
        self,
        repo_root: Path,
        *,
        summary: str,
        ctx: object | None = None,
    ) -> ServiceResult[AdapterCatalogSubmissionView]:
        del ctx
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_ready_summary_required", "Adapter ready submit summary is required.", field="summary"))
        gate = self.check_adapter_catalog_ready_preflight(repo_root)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        if not gate.value.passed:
            return self.runtime.foundation.fail(gate.value.issues)
        return self.runtime.foundation.ok(
            AdapterCatalogSubmissionView(
                submission_type="adapter_catalog_ready",
                accepted=True,
                summary=summary.strip(),
                gate=gate.value,
            )
        )

    def submit_adapter_catalog_blocked(
        self,
        repo_root: Path,
        *,
        reason: str,
        missing_interfaces: list[str] | None = None,
        evidence_summary: str | None = None,
        suggested_next_action: str | None = None,
        ctx: object | None = None,
    ) -> ServiceResult[AdapterCatalogSubmissionView]:
        del repo_root, ctx
        if not reason or not reason.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_blocked_reason_required", "Blocked submit reason is required.", field="reason"))
        if missing_interfaces and not (evidence_summary and evidence_summary.strip()):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "adapter_blocked_evidence_required",
                    "Blocked submit with missing interfaces requires an evidence_summary.",
                    field="evidence_summary",
                )
            )
        return self.runtime.foundation.ok(
            AdapterCatalogSubmissionView(
                submission_type="adapter_catalog_blocked",
                accepted=True,
                reason=reason.strip(),
                missing_interfaces=missing_interfaces or [],
                evidence_summary=evidence_summary,
                suggested_next_action=suggested_next_action,
                summary=f"Adapter catalog blocked: {reason.strip()}",
            )
        )

    def list_active_adapter_modules(self, repo_root: Path) -> ServiceResult[AdapterModuleListView]:
        modules = self.adapter_decl_catalog.list_registered_adapter_modules(repo_root)
        if not modules.ok or modules.value is None:
            return self.runtime.foundation.fail(modules.issues)
        module_names = [item.module for item in modules.value.modules]
        return self.runtime.foundation.ok(
            AdapterModuleListView(
                modules=module_names,
                summary=f"{len(module_names)} active adapter modules.",
            )
        )

    def list_visible_upstream_modules(self, repo_root: Path) -> ServiceResult[AdapterModuleListView]:
        modules = self.upstream_metadata.list_visible_upstream_modules(repo_root)
        if not modules.ok or modules.value is None:
            return self.runtime.foundation.fail(modules.issues)
        return self.runtime.foundation.ok(
            AdapterModuleListView(
                modules=modules.value,
                summary=f"{len(modules.value)} upstream modules are visible.",
            )
        )
