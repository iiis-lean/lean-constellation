"""NodeService composition and public wrappers."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import GateReport, ServiceResult
from lean_constellation.services.node.contract import ContractComponent, ContractVersionStatus, NodeContractView
from lean_constellation.services.node.contract_fields import NodeMathlibDeclUse, NodeMathlibModuleUse
from lean_constellation.services.node.dependency import DependencyComponent, NodeDepsView
from lean_constellation.services.node.export import (
    ContentPublicDeclProvider,
    DeclPublicView,
    DeclRefView,
    ExportComponent,
    ScopeExportCandidateView,
)
from lean_constellation.services.node.interface import InterfaceComponent, InterfaceListView
from lean_constellation.services.node.material_ref import MaterialRefComponent, NodeMaterialRefsView
from lean_constellation.services.node.node_tree import NodeKind, NodeTreeComponent, NodeView
from lean_constellation.services.node.public_decl_access import PublicDeclAccessResolver

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class ContentTaskReadyGateProvider(Protocol):
    """Provider used by NodeService to validate a ready Content task result."""

    def check_content_node_ready(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        ...


class ContentTaskOutcome(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"
    FAILED = "failed"


class ContentTaskResultView(StrictModel):
    """Minimal ContentNodeTaskFlow result shape consumed by Coordinator finalize."""

    outcome: ContentTaskOutcome
    summary: str | None = None
    reason: str | None = None


class ContentTaskFinalizeInput(StrictModel):
    node_path: str
    task_result: ContentTaskResultView
    coordinator_summary: str


class ContentTaskFinalizeView(StrictModel):
    node_path: str
    task_outcome: ContentTaskOutcome
    task_summary: str | None = None
    task_reason: str | None = None
    coordinator_summary: str
    contract_version: int | None = None
    contract_version_status: ContractVersionStatus | None = None
    contract_summary_written: bool = False
    contract_committed: bool = False
    finalized: bool = False
    gate: GateReport | None = None
    follow_up_hints: list[str] = Field(default_factory=list)
    summary: str


class CurrentNodeContractView(StrictModel):
    """Coordinator / worker oriented current NodeContract view."""

    node_path: str
    contract: NodeContractView
    deps: NodeDepsView
    material_refs: NodeMaterialRefsView
    mathlib_modules: list[NodeMathlibModuleUse] = Field(default_factory=list)
    mathlib_decls: list[NodeMathlibDeclUse] = Field(default_factory=list)
    summary: str


class ScopeChildCloseView(StrictModel):
    path: str
    node_kind: NodeKind
    contract_version: int | None = None
    contract_version_status: ContractVersionStatus | None = None
    ready_for_scope_close: bool
    summary: str


class ScopeCloseView(StrictModel):
    scope_path: str
    scope_contract: NodeContractView
    children: list[ScopeChildCloseView] = Field(default_factory=list)
    interfaces: InterfaceListView
    exports: list[DeclRefView] = Field(default_factory=list)
    export_candidates: ScopeExportCandidateView
    child_readiness_gate: GateReport
    scope_commit_gate: GateReport
    ready_to_commit: bool
    summary: str


class RepoReadyNodeView(StrictModel):
    main_scope: ScopeCloseView
    repo_ready_gate: GateReport
    ready_to_submit: bool
    summary: str


class NodeBoundaryView(StrictModel):
    """Current public boundary view for a Scope or Content node."""

    node_path: str
    node_kind: NodeKind
    interfaces: InterfaceListView
    public_decls: list[DeclPublicView] = Field(default_factory=list)
    exports: list[DeclRefView] = Field(default_factory=list)
    summary: str


class NodeService:
    """Composition root for node tree, contracts, interfaces, refs, deps, and exports."""

    _NATIVE_MAIN_BOUNDARY = (
        "Main covers the full native repository formalization scope, including the prepared source corpus, "
        "formalized content, internal node tree, and repository public interface."
    )
    _NATIVE_MAIN_OBJECTIVE = (
        "Maintain the root scope for the repository Coordinator: organize child scopes/content nodes, "
        "track the public boundary, and eventually expose all required repository interfaces."
    )
    _ADAPTER_MAIN_BOUNDARY = (
        "Main covers the adapter repository facade for the upstream Lean dependency and exposes selected upstream declarations "
        "through the repository public interface."
    )
    _ADAPTER_MAIN_OBJECTIVE = (
        "Register and expose upstream Lean declarations needed by the preparation input without creating a native scope/content subtree."
    )

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        node_tree: NodeTreeComponent | None = None,
        contract: ContractComponent | None = None,
        interface: InterfaceComponent | None = None,
        dependency: DependencyComponent | None = None,
        material_ref: MaterialRefComponent | None = None,
        export: ExportComponent | None = None,
        public_decl_provider: ContentPublicDeclProvider | None = None,
        node_projection: object | None = None,
        content_ready_gate: ContentTaskReadyGateProvider | None = None,
    ) -> None:
        self.runtime = runtime
        self._content_ready_gate = content_ready_gate
        self.node_tree = node_tree or NodeTreeComponent(runtime)
        self.contract = contract or ContractComponent(runtime, self.node_tree)
        self.export = export or ExportComponent(
            runtime,
            node_tree=self.node_tree,
            contract=self.contract,
            public_decl_provider=public_decl_provider,
            node_projection=node_projection,  # type: ignore[arg-type]
        )
        self.interface = interface or InterfaceComponent(
            runtime,
            contract=self.contract,
            export=self.export,
            node_projection=node_projection,  # type: ignore[arg-type]
        )
        self.dependency = dependency or DependencyComponent(
            runtime,
            node_tree=self.node_tree,
            contract=self.contract,
            node_projection=node_projection,  # type: ignore[arg-type]
        )
        self.material_ref = material_ref or MaterialRefComponent(
            runtime,
            contract=self.contract,
        )
        self.public_decl_access = PublicDeclAccessResolver(
            runtime,
            node_tree=self.node_tree,
            dependency=self.dependency,
            export=self.export,
        )

    def create_scope_node(
        self,
        repo_root: Path,
        *,
        path: str,
        goal: str,
        boundary: str,
        objective: str | None = None,
        constraints: str | None = None,
        success_criteria: str | None = None,
    ) -> ServiceResult[NodeView]:
        return self.node_tree.create_scope_node(
            repo_root,
            path=path,
            goal=goal,
            boundary=boundary,
            objective=objective,
            constraints=constraints,
            success_criteria=success_criteria,
        )

    def create_content_node(
        self,
        repo_root: Path,
        *,
        path: str,
        goal: str,
        boundary: str,
        objective: str,
        success_criteria: str,
        constraints: str | None = None,
    ) -> ServiceResult[NodeView]:
        return self.node_tree.create_content_node(
            repo_root,
            path=path,
            goal=goal,
            boundary=boundary,
            objective=objective,
            success_criteria=success_criteria,
            constraints=constraints,
        )

    def ensure_native_root_main_contract(
        self,
        repo_root: Path,
        *,
        boundary: str | None = None,
        objective: str | None = None,
    ) -> ServiceResult[NodeContractView]:
        return self._ensure_root_main_contract(
            repo_root,
            boundary=boundary or self._NATIVE_MAIN_BOUNDARY,
            objective=objective or self._NATIVE_MAIN_OBJECTIVE,
        )

    def ensure_adapter_root_main_contract(self, repo_root: Path) -> ServiceResult[NodeContractView]:
        return self._ensure_root_main_contract(
            repo_root,
            boundary=self._ADAPTER_MAIN_BOUNDARY,
            objective=self._ADAPTER_MAIN_OBJECTIVE,
        )

    def check_root_main_handoff_interfaces(self, repo_root: Path) -> ServiceResult[GateReport]:
        return self.interface.check_root_interfaces_include_preparation_inputs(repo_root, node_path="Main")

    def prepare_content_task_admission(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        return self.contract.check_content_task_admission(repo_root, node_path=node_path)

    def submit_content_node_batch_preflight(self, repo_root: Path, *, node_paths: list[str]) -> ServiceResult[GateReport]:
        return self.dependency.check_content_batch_independent(repo_root, node_paths=node_paths)

    def commit_scope_contract(self, repo_root: Path, *, scope_path: str, summary: str) -> ServiceResult[NodeContractView]:
        return self.contract.commit_scope_contract(repo_root, scope_path=scope_path, summary=summary)

    def commit_content_contract(self, repo_root: Path, *, node_path: str, summary: str) -> ServiceResult[NodeContractView]:
        return self.contract.commit_content_contract(repo_root, node_path=node_path, summary=summary)

    def get_current_contract_view(self, repo_root: Path, *, node_path: str) -> ServiceResult[CurrentNodeContractView]:
        contract = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        deps = self.dependency.list_node_deps(repo_root, node_path=node_path)
        if not deps.ok or deps.value is None:
            return self.runtime.foundation.fail(deps.issues)
        material_refs = self.material_ref.list_node_material_refs(repo_root, node_path=node_path)
        if not material_refs.ok or material_refs.value is None:
            return self.runtime.foundation.fail(material_refs.issues)
        return self.runtime.foundation.ok(
            CurrentNodeContractView(
                node_path=node_path,
                contract=contract.value,
                deps=deps.value,
                material_refs=material_refs.value,
                mathlib_modules=list(contract.value.contract.mathlib_modules),
                mathlib_decls=list(contract.value.contract.mathlib_decls),
                summary=(
                    f"Loaded current contract view for {node_path}: "
                    f"{len(deps.value.deps)} deps, "
                    f"{len(material_refs.value.owned_refs)} owned refs, "
                    f"{len(material_refs.value.context_refs)} context refs."
                ),
            ),
            warnings=[*contract.issues, *deps.issues, *material_refs.issues],
        )

    def add_current_node_dep(
        self,
        repo_root: Path,
        *,
        node_path: str,
        target_node: str,
        reason: str,
        actor: str,
        expected_public_decl_names: list[str] | None = None,
        target_repo: str | None = None,
    ) -> ServiceResult[CurrentNodeContractView]:
        mutation = self.dependency.add_node_dep(
            repo_root,
            node_path=node_path,
            target_node=target_node,
            reason=reason,
            actor=actor,
            expected_decl_names=expected_public_decl_names,
            target_repo=target_repo,
        )
        return self._current_contract_view_after_mutation(repo_root, node_path=node_path, mutation=mutation)

    def remove_current_node_dep(
        self,
        repo_root: Path,
        *,
        node_path: str,
        index: int,
        actor: str,
    ) -> ServiceResult[CurrentNodeContractView]:
        mutation = self.dependency.remove_node_dep(repo_root, node_path=node_path, index=index, actor=actor)
        return self._current_contract_view_after_mutation(repo_root, node_path=node_path, mutation=mutation)

    def add_current_material_ref(
        self,
        repo_root: Path,
        *,
        node_path: str,
        ref_scope: Literal["owned", "context"],
        material_kind: Literal["source", "resource"],
        locator: str,
        start_line: int | None = None,
        end_line: int | None = None,
        reason: str | None = None,
        actor: str,
    ) -> ServiceResult[CurrentNodeContractView]:
        if ref_scope == "owned" and material_kind == "source":
            mutation = self.material_ref.add_owned_source_ref(
                repo_root,
                node_path=node_path,
                path=locator,
                start_line=start_line,
                end_line=end_line,
                reason=reason,
                actor=actor,
            )
        elif ref_scope == "owned" and material_kind == "resource":
            mutation = self.material_ref.add_owned_resource_ref(
                repo_root,
                node_path=node_path,
                resource_key=locator,
                start_line=start_line,
                end_line=end_line,
                reason=reason,
                actor=actor,
            )
        elif ref_scope == "context" and material_kind == "source":
            mutation = self.material_ref.add_context_source_ref(
                repo_root,
                node_path=node_path,
                path=locator,
                start_line=start_line,
                end_line=end_line,
                reason=reason,
                actor=actor,
            )
        elif ref_scope == "context" and material_kind == "resource":
            mutation = self.material_ref.add_context_resource_ref(
                repo_root,
                node_path=node_path,
                resource_key=locator,
                start_line=start_line,
                end_line=end_line,
                reason=reason,
                actor=actor,
            )
        else:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "current_material_ref_kind_invalid",
                    "ref_scope must be owned/context and material_kind must be source/resource.",
                    object_ref=node_path,
                )
            )
        return self._current_contract_view_after_mutation(repo_root, node_path=node_path, mutation=mutation)

    def remove_current_material_ref(
        self,
        repo_root: Path,
        *,
        node_path: str,
        ref_scope: Literal["owned", "context"],
        index: int,
        actor: str,
    ) -> ServiceResult[CurrentNodeContractView]:
        if ref_scope == "owned":
            mutation = self.material_ref.remove_owned_ref(repo_root, node_path=node_path, index=index, actor=actor)
        elif ref_scope == "context":
            mutation = self.material_ref.remove_context_ref(repo_root, node_path=node_path, index=index, actor=actor)
        else:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "current_material_ref_scope_invalid",
                    "ref_scope must be owned or context.",
                    object_ref=node_path,
                    field="ref_scope",
                    current=str(ref_scope),
                )
            )
        return self._current_contract_view_after_mutation(repo_root, node_path=node_path, mutation=mutation)

    def finalize_content_task_result(
        self,
        repo_root: Path,
        *,
        node_path: str,
        task_result: ContentTaskResultView | dict[str, object],
        coordinator_summary: str,
    ) -> ServiceResult[ContentTaskFinalizeView]:
        """Record the Coordinator callback summary for a terminal Content node task result."""

        if not coordinator_summary or not coordinator_summary.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("contract_summary_required", "Content task finalize summary is required.", field="coordinator_summary")
            )
        parsed_result = self._parse_content_task_result(task_result)
        if not parsed_result.ok or parsed_result.value is None:
            return self.runtime.foundation.fail(parsed_result.issues)
        if parsed_result.value.outcome in {ContentTaskOutcome.BLOCKED, ContentTaskOutcome.FAILED} and not (
            parsed_result.value.reason and parsed_result.value.reason.strip()
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "content_task_reason_required",
                    "Blocked or failed Content task result requires a reason.",
                    object_ref=node_path,
                    field="task_result.reason",
                )
            )

        if parsed_result.value.outcome == ContentTaskOutcome.READY:
            gate = self._check_content_task_ready(repo_root, node_path=node_path)
            if not gate.ok or gate.value is None:
                return self.runtime.foundation.fail(gate.issues)
            if not gate.value.passed:
                current = self.contract.get_current_contract(repo_root, node_path=node_path)
                if not current.ok or current.value is None:
                    return self.runtime.foundation.fail(current.issues)
                view = self._content_task_finalize_view(
                    node_path=node_path,
                    task_result=parsed_result.value,
                    coordinator_summary=coordinator_summary.strip(),
                    contract=current.value,
                    gate=gate.value,
                    finalized=False,
                    contract_summary_written=False,
                    contract_committed=False,
                    summary="Content task claimed ready, but the ready gate did not pass.",
                )
                return ServiceResult[ContentTaskFinalizeView](ok=False, value=view, issues=gate.value.issues)
            committed = self.contract.commit_content_contract(repo_root, node_path=node_path, summary=coordinator_summary)
            if not committed.ok or committed.value is None:
                return self.runtime.foundation.fail(committed.issues)
            return self.runtime.foundation.ok(
                self._content_task_finalize_view(
                    node_path=node_path,
                    task_result=parsed_result.value,
                    coordinator_summary=coordinator_summary.strip(),
                    contract=committed.value,
                    gate=gate.value,
                    finalized=True,
                    contract_summary_written=True,
                    contract_committed=True,
                    summary="Content task result finalized as ready; contract summary was committed.",
                )
            )

        gate_name = f"content_task_finalize_{parsed_result.value.outcome.value}"
        gate = self.runtime.foundation.gate_passed(
            gate_name,
            summary=f"Content task {parsed_result.value.outcome.value} result is accepted for summary recording.",
        )
        recorded = self.contract.record_content_contract_summary(repo_root, node_path=node_path, summary=coordinator_summary)
        if not recorded.ok or recorded.value is None:
            return self.runtime.foundation.fail(recorded.issues)
        return self.runtime.foundation.ok(
            self._content_task_finalize_view(
                node_path=node_path,
                task_result=parsed_result.value,
                coordinator_summary=coordinator_summary.strip(),
                contract=recorded.value,
                gate=gate,
                finalized=True,
                contract_summary_written=True,
                contract_committed=False,
                summary=f"Content task result finalized as {parsed_result.value.outcome.value}; contract remains open.",
            )
        )

    def get_scope_close_view(self, repo_root: Path, *, scope_path: str) -> ServiceResult[ScopeCloseView]:
        scope = self.node_tree.get_node(repo_root, path=scope_path)
        if not scope.ok or scope.value is None:
            return self.runtime.foundation.fail(scope.issues)
        if scope.value.kind != NodeKind.SCOPE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "node_not_scope",
                    "Scope close view requires a Scope node.",
                    object_ref=scope_path,
                    current=scope.value.kind.value,
                    expected=NodeKind.SCOPE.value,
                )
            )
        contract = self.contract.get_current_contract(repo_root, node_path=scope_path)
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        children = self._scope_child_close_views(repo_root, scope_path=scope_path)
        if not children.ok or children.value is None:
            return self.runtime.foundation.fail(children.issues)
        child_gate = self._scope_child_readiness_gate(scope_path, children.value)
        interfaces = self.interface.list_interfaces(repo_root, node_path=scope_path)
        if not interfaces.ok or interfaces.value is None:
            return self.runtime.foundation.fail(interfaces.issues)
        exports = self._scope_export_views_from_contract(repo_root, scope_path=scope_path, contract=contract.value)
        candidates = self.export.list_scope_export_candidates(repo_root, scope_path=scope_path)
        if not candidates.ok or candidates.value is None:
            return self.runtime.foundation.fail(candidates.issues)
        commit_gate = self._scope_close_commit_gate(repo_root, scope_path=scope_path)
        if not commit_gate.ok or commit_gate.value is None:
            return self.runtime.foundation.fail(commit_gate.issues)
        ready = child_gate.passed and commit_gate.value.passed
        return self.runtime.foundation.ok(
            ScopeCloseView(
                scope_path=scope_path,
                scope_contract=contract.value,
                children=children.value,
                interfaces=interfaces.value,
                exports=exports,
                export_candidates=candidates.value,
                child_readiness_gate=child_gate,
                scope_commit_gate=commit_gate.value,
                ready_to_commit=ready,
                summary=("Scope is ready to commit." if ready else "Scope close preflight has blocking issues."),
            ),
            warnings=[*contract.issues, *children.issues, *interfaces.issues, *candidates.issues],
        )

    def get_repo_ready_node_view(self, repo_root: Path) -> ServiceResult[RepoReadyNodeView]:
        main = self.get_scope_close_view(repo_root, scope_path="Main")
        if not main.ok or main.value is None:
            return self.runtime.foundation.fail(main.issues)
        gate = self.runtime.validation_snapshot.check_repo_ready(repo_root, summary="Repo ready preflight.")
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        ready = main.value.ready_to_commit and gate.value.passed
        return self.runtime.foundation.ok(
            RepoReadyNodeView(
                main_scope=main.value,
                repo_ready_gate=gate.value,
                ready_to_submit=ready,
                summary=("Repo is ready to submit." if ready else "Repo ready preflight has blocking issues."),
            ),
            warnings=main.issues,
        )

    def get_node_public_boundary(self, repo_root: Path, *, node_path: str) -> ServiceResult[NodeBoundaryView]:
        node = self.node_tree.get_node(repo_root, path=node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        interfaces = self.interface.list_interfaces(repo_root, node_path=node_path)
        if not interfaces.ok or interfaces.value is None:
            return self.runtime.foundation.fail(interfaces.issues)
        if node.value.kind == NodeKind.CONTENT:
            public = self.export.list_content_public_decls(repo_root, node_path=node_path)
            if not public.ok or public.value is None:
                return self.runtime.foundation.fail(public.issues)
            return self.runtime.foundation.ok(
                NodeBoundaryView(
                    node_path=node_path,
                    node_kind=node.value.kind,
                    interfaces=interfaces.value,
                    public_decls=public.value,
                    summary=f"Content node {node_path} exposes {len(public.value)} public declarations.",
                ),
                warnings=public.issues,
            )
        exports = self.export.list_scope_exports(repo_root, scope_path=node_path)
        if not exports.ok or exports.value is None:
            return self.runtime.foundation.fail(exports.issues)
        return self.runtime.foundation.ok(
            NodeBoundaryView(
                node_path=node_path,
                node_kind=node.value.kind,
                interfaces=interfaces.value,
                exports=exports.value,
                summary=f"Scope node {node_path} exposes {len(exports.value)} exports.",
            )
        )

    def _ensure_root_main_contract(
        self,
        repo_root: Path,
        *,
        boundary: str,
        objective: str,
    ) -> ServiceResult[NodeContractView]:
        root = self.node_tree.ensure_root_scope_node(repo_root, path="Main")
        if not root.ok:
            return self.runtime.foundation.fail(root.issues)
        initialized = self.contract.initialize_main_contract_from_preparation_input(
            repo_root,
            boundary=boundary,
            objective=objective,
        )
        if not initialized.ok or initialized.value is None:
            return self.runtime.foundation.fail(initialized.issues)
        synced = self.interface.sync_protected_root_interfaces_from_preparation_input(repo_root, node_path="Main")
        if not synced.ok or synced.value is None:
            return self.runtime.foundation.fail(synced.issues)
        return synced

    def _parse_content_task_result(self, task_result: ContentTaskResultView | dict[str, object]) -> ServiceResult[ContentTaskResultView]:
        if isinstance(task_result, ContentTaskResultView):
            return self.runtime.foundation.ok(task_result)
        try:
            return self.runtime.foundation.ok(ContentTaskResultView.model_validate(task_result))
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("content_task_result_invalid", f"Content task result is invalid: {exc}", field="task_result")
            )

    def _check_content_task_ready(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        provider = self._content_ready_gate or self.runtime.validation_snapshot
        return provider.check_content_node_ready(repo_root, node_path=node_path)

    def _scope_child_close_views(self, repo_root: Path, *, scope_path: str) -> ServiceResult[list[ScopeChildCloseView]]:
        children = self.node_tree.list_children(repo_root, scope_path=scope_path)
        if not children.ok or children.value is None:
            return self.runtime.foundation.fail(children.issues)
        views: list[ScopeChildCloseView] = []
        for child in children.value:
            contract = self.contract.get_visible_contract(repo_root, node_path=child.path)
            if contract.ok and contract.value is not None:
                ready = True
                contract_version = contract.value.version
                contract_status = contract.value.version_status
            else:
                edit_contract = self.contract.get_current_contract(repo_root, node_path=child.path)
                if not edit_contract.ok or edit_contract.value is None:
                    return self.runtime.foundation.fail(edit_contract.issues)
                ready = False
                contract_version = edit_contract.value.version
                contract_status = edit_contract.value.version_status
            views.append(
                ScopeChildCloseView(
                    path=child.path,
                    node_kind=child.kind,
                    contract_version=contract_version,
                    contract_version_status=contract_status,
                    ready_for_scope_close=ready,
                    summary=(
                        f"{child.kind.value} child {child.path} is committed."
                        if ready
                        else f"{child.kind.value} child {child.path} is not committed."
                    ),
                )
            )
        return self.runtime.foundation.ok(views)

    def _scope_child_readiness_gate(self, scope_path: str, children: list[ScopeChildCloseView]) -> GateReport:
        issues = []
        for child in children:
            if child.ready_for_scope_close:
                continue
            issue_kind = "content_child_not_ready" if child.node_kind == NodeKind.CONTENT else "scope_child_not_committed"
            issues.append(
                self.runtime.foundation.issue(
                    issue_kind,
                    f"Direct child is not ready for Scope close: {child.path}",
                    object_ref=scope_path,
                    current=child.contract_version_status.value if child.contract_version_status else "missing",
                    expected=ContractVersionStatus.COMMITTED.value,
                )
            )
        if issues:
            return self.runtime.foundation.gate_failed("scope_child_readiness", issues, summary=f"{len(issues)} direct child checks failed.")
        return self.runtime.foundation.gate_passed("scope_child_readiness", summary=f"{len(children)} direct children are ready.")

    def _scope_export_views_from_contract(self, repo_root: Path, *, scope_path: str, contract: NodeContractView) -> list[DeclRefView]:
        views = [self.export._decl_ref_view(repo_root, scope_path, ref, index=-1) for ref in contract.contract.exports]
        views.sort(key=lambda item: (item.ref.node, item.ref.name, item.ref.revision))
        return [view.model_copy(update={"index": index}) for index, view in enumerate(views)]

    def _scope_close_commit_gate(self, repo_root: Path, *, scope_path: str) -> ServiceResult[GateReport]:
        reports: list[GateReport] = []
        contract_gate = self.contract.check_scope_contract_commit(
            repo_root,
            scope_path=scope_path,
            summary="Scope close preflight.",
        )
        if not contract_gate.ok or contract_gate.value is None:
            return self.runtime.foundation.fail(contract_gate.issues)
        reports.append(contract_gate.value)

        validation_gate = self.runtime.validation_snapshot.check_scope_commit(
            repo_root,
            scope_path=scope_path,
            summary="Scope close preflight.",
        )
        if validation_gate.ok and validation_gate.value is not None:
            reports.append(validation_gate.value)
        elif validation_gate.issues:
            reports.append(
                self.runtime.foundation.gate_failed(
                    "scope_commit_validation",
                    validation_gate.issues,
                    summary="Scope validation checks failed.",
                )
            )
        return self.runtime.foundation.ok(self.runtime.foundation.merge_gate_reports("scope_commit", reports))

    def _current_contract_view_after_mutation(
        self,
        repo_root: Path,
        *,
        node_path: str,
        mutation: ServiceResult[NodeContractView],
    ) -> ServiceResult[CurrentNodeContractView]:
        if not mutation.ok or mutation.value is None:
            return self.runtime.foundation.fail(mutation.issues)
        view = self.get_current_contract_view(repo_root, node_path=node_path)
        if not view.ok or view.value is None:
            return self.runtime.foundation.fail(view.issues)
        return self.runtime.foundation.ok(view.value, warnings=[*mutation.issues, *view.issues])

    def _content_task_finalize_view(
        self,
        *,
        node_path: str,
        task_result: ContentTaskResultView,
        coordinator_summary: str,
        contract: NodeContractView,
        gate: GateReport | None,
        finalized: bool,
        contract_summary_written: bool,
        contract_committed: bool,
        summary: str,
    ) -> ContentTaskFinalizeView:
        follow_up_hints = []
        if gate is not None:
            follow_up_hints = [issue.suggested_action for issue in gate.issues if issue.suggested_action]
        return ContentTaskFinalizeView(
            node_path=node_path,
            task_outcome=task_result.outcome,
            task_summary=task_result.summary,
            task_reason=task_result.reason,
            coordinator_summary=coordinator_summary,
            contract_version=contract.version,
            contract_version_status=contract.version_status,
            contract_summary_written=contract_summary_written,
            contract_committed=contract_committed,
            finalized=finalized,
            gate=gate,
            follow_up_hints=follow_up_hints,
            summary=summary,
        )
