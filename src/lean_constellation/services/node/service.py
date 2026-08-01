"""NodeService composition and public wrappers."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.refs import DeclRef, SourceRef
from lean_constellation.services.foundation import FoundationContext, GateReport, MutationSummaryView, ServiceResult
from lean_constellation.services.foundation.module_layout import local_projection_path
from lean_constellation.services.node.contract import ContractComponent, ContractVersionStatus, NodeContractView
from lean_constellation.services.node.contract_fields import ContractMaterialRef
from lean_constellation.services.node.dependency import (
    DependencyComponent,
    NodeDependencyMutationReceipt,
)
from lean_constellation.services.node.export import (
    ContentPublicDeclProvider,
    DeclPublicView,
    DeclRefView,
    ExportComponent,
    ScopeExportCandidateView,
)
from lean_constellation.services.node.interface import InterfaceComponent, InterfaceListView
from lean_constellation.services.node.material_ref import MaterialRefComponent
from lean_constellation.services.node.node_tree import DeleteImpactView, NodeKind, NodeTreeComponent, NodeView
from lean_constellation.services.node.public_decl_access import PublicDeclAccessResolver
from lean_constellation.services.node.public_statement_closure import PublicStatementClosureComponent
from lean_constellation.services.node.release_guard import NodeReleaseGuard

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
    contract_version: int | None = None
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


class CurrentNodeInterfaceOverview(StrictModel):
    name: str
    kind: str
    summary: str
    bound_decl: DeclRef | None = None


class CurrentNodeDependencyOverview(StrictModel):
    repository: str | None = None
    node_path: str
    expected_declarations: list[str] = Field(default_factory=list)


class CurrentNodeMaterialOverview(StrictModel):
    scope: Literal["owned", "context"]
    kind: str
    locator: str
    start_line: int | None = None
    end_line: int | None = None


class CurrentNodeContractView(StrictModel):
    """Compact Agent-facing overview derived directly from NodeContract truth."""

    node_path: str
    node_kind: NodeKind
    contract_status: ContractVersionStatus
    goal: str
    boundary: str
    objective: str | None = None
    success_criteria: str | None = None
    constraints: str | None = None
    result_summary: str | None = None
    interfaces: list[CurrentNodeInterfaceOverview] = Field(default_factory=list)
    dependencies: list[CurrentNodeDependencyOverview] = Field(default_factory=list)
    materials: list[CurrentNodeMaterialOverview] = Field(default_factory=list)
    mathlib_modules: list[str] = Field(default_factory=list)
    mathlib_declarations: list[str] = Field(default_factory=list)
    exports: list[DeclRef] = Field(default_factory=list)
    summary: str


class CurrentNodeMaterialMutationReceipt(StrictModel):
    """Compact result of one current-node material mutation."""

    node_path: str
    ref_scope: Literal["owned", "context"]
    operation: Literal["add", "remove"]
    changed: bool
    added: list[ContractMaterialRef] = Field(default_factory=list)
    removed: list[ContractMaterialRef] = Field(default_factory=list)
    summary: str


def _current_material_overview(item, *, scope: Literal["owned", "context"]) -> CurrentNodeMaterialOverview:  # noqa: ANN001
    ref = item.ref.ref
    if isinstance(ref, SourceRef):
        return CurrentNodeMaterialOverview(
            scope=scope,
            kind=item.ref.kind,
            locator=ref.path,
            start_line=ref.start_line,
            end_line=ref.end_line,
        )
    return CurrentNodeMaterialOverview(
        scope=scope,
        kind=item.ref.kind,
        locator=ref.locator or ref.resource_key,
        start_line=ref.start_line,
        end_line=ref.end_line,
    )


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
    main_contract_version: int | None = None
    main_contract_version_status: ContractVersionStatus | None = None
    publication_status: str
    release_policy: str
    open_requirement_count: int = 0
    active_node_count: int = 0
    open_contract_node_paths: list[str] = Field(default_factory=list)
    structural_gate: GateReport
    ready_to_submit_intent: bool
    authoritative_audit_status: Literal["runs_after_submit"] = "runs_after_submit"
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
        self.release_guard = NodeReleaseGuard(runtime)
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
            public_decl_provider=public_decl_provider,
            node_projection=node_projection,  # type: ignore[arg-type]
            interface_identity=self.interface,
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
        self.public_statement_closure = PublicStatementClosureComponent(runtime)

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

    def check_root_interface_statement_contracts(self, repo_root: Path) -> ServiceResult[GateReport]:
        return self.interface.check_root_interface_statement_contracts(repo_root, node_path="Main")

    def prepare_content_task_admission(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        admission = self.contract.check_content_task_admission(repo_root, node_path=node_path)
        if not admission.ok or admission.value is None or not admission.value.passed:
            return admission
        logical_path = self.runtime.foundation.layout.node_projection_dir(
            FoundationContext(repo_root=Path(repo_root)), node_path
        )
        projection_dir = local_projection_path(repo_root, logical_path)
        ensured = self.runtime.foundation.store.ensure_dir(projection_dir)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        return admission

    def submit_content_node_batch_preflight(self, repo_root: Path, *, node_paths: list[str]) -> ServiceResult[GateReport]:
        return self.dependency.check_content_batch_independent(repo_root, node_paths=node_paths)

    def commit_scope_contract(self, repo_root: Path, *, scope_path: str, summary: str) -> ServiceResult[NodeContractView]:
        preflight = self.contract.check_scope_contract_commit(
            repo_root,
            scope_path=scope_path,
            summary=summary,
        )
        if not preflight.ok or preflight.value is None:
            return self.runtime.foundation.fail(preflight.issues)
        if not preflight.value.passed:
            return self.runtime.foundation.fail(preflight.value.issues)
        current = self.contract.get_edit_contract(repo_root, node_path=scope_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        identities = self.interface.check_bound_interface_lean_identities(
            repo_root,
            node_path=scope_path,
            contract=current.value.contract,
        )
        if not identities.ok or identities.value is None:
            return self.runtime.foundation.fail(identities.issues)
        if not identities.value.passed:
            return self.runtime.foundation.fail(identities.value.issues)
        closure = self.public_statement_closure.check_scope(
            repo_root,
            scope_path=scope_path,
        )
        if not closure.ok or closure.value is None:
            return self.runtime.foundation.fail(closure.issues)
        if not closure.value.passed:
            return self.runtime.foundation.fail(closure.value.issues)
        guarded = self.release_guard.check_scope_contract_candidate(
            repo_root, scope_path=scope_path, candidate=current.value.contract
        )
        if not guarded.ok:
            return self.runtime.foundation.fail(guarded.issues)
        return self.contract._commit_scope_contract_after_guard(repo_root, scope_path=scope_path, summary=summary)

    def commit_content_contract(self, repo_root: Path, *, node_path: str, summary: str) -> ServiceResult[NodeContractView]:
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("contract_summary_required", "Contract summary is required.", field="summary")
            )
        node = self.node_tree.get_node(repo_root, path=node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        if node.value.kind != NodeKind.CONTENT:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("node_not_content", "Content contract commit requires a Content node.", object_ref=node_path)
            )
        current = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        identities = self.interface.check_bound_interface_lean_identities(
            repo_root,
            node_path=node_path,
            contract=current.value.contract,
        )
        if not identities.ok or identities.value is None:
            return self.runtime.foundation.fail(identities.issues)
        if not identities.value.passed:
            return self.runtime.foundation.fail(identities.value.issues)
        closure = self.public_statement_closure.check_content(
            repo_root,
            node_path=node_path,
        )
        if not closure.ok or closure.value is None:
            return self.runtime.foundation.fail(closure.issues)
        if not closure.value.passed:
            return self.runtime.foundation.fail(closure.value.issues)
        head = self.release_guard.capture_content_contract_head(repo_root, node_path=node_path)
        if not head.ok or head.value is None:
            return self.runtime.foundation.fail(head.issues)
        return self.contract._commit_content_contract_with_head(
            repo_root,
            node_path=node_path,
            summary=summary,
            decl_graph_head=head.value,
        )

    def preview_delete_node(self, repo_root: Path, *, path: str) -> ServiceResult[DeleteImpactView]:
        return self.release_guard.preview_delete_node(repo_root, path=path)

    def mark_node_deleted(self, repo_root: Path, *, path: str, reason: str) -> ServiceResult[MutationSummaryView]:
        if not reason or not reason.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("delete_reason_required", "Node deletion requires a reason.", field="reason")
            )
        preview = self.preview_delete_node(repo_root, path=path)
        if not preview.ok or preview.value is None:
            return self.runtime.foundation.fail(preview.issues)
        if not preview.value.deletable:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "node_delete_blocked",
                    "Node deletion is blocked by current or released impacts.",
                    object_ref=path,
                    details={"blocking_reasons": ",".join(preview.value.blocking_reasons)},
                )
            )
        return self.node_tree._mark_node_deleted_after_guard(repo_root, path=path, reason=reason)

    def get_current_contract_view(self, repo_root: Path, *, node_path: str) -> ServiceResult[CurrentNodeContractView]:
        contract = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        truth = contract.value.contract
        return self.runtime.foundation.ok(
            CurrentNodeContractView(
                node_path=node_path,
                node_kind=contract.value.node_kind,
                contract_status=contract.value.status,
                goal=truth.goal,
                boundary=truth.boundary,
                objective=truth.objective,
                success_criteria=truth.success_criteria,
                constraints=truth.constraints,
                result_summary=truth.summary,
                interfaces=[
                    CurrentNodeInterfaceOverview(
                        name=item.name,
                        kind=item.kind.value,
                        summary=item.summary,
                        bound_decl=item.bound_decl,
                    )
                    for item in truth.interfaces
                ],
                dependencies=[
                    CurrentNodeDependencyOverview(
                        repository=item.target.repo,
                        node_path=item.target.node,
                        expected_declarations=sorted({ref.name for ref in item.expected_decl_refs}),
                    )
                    for item in truth.deps
                ],
                materials=[
                    _current_material_overview(item, scope="owned")
                    for item in truth.owned_refs
                ]
                + [
                    _current_material_overview(item, scope="context")
                    for item in truth.context_refs
                ],
                mathlib_modules=sorted({item.module for item in truth.mathlib_modules}),
                mathlib_declarations=sorted({item.name for item in truth.mathlib_decls}),
                exports=list(truth.exports),
                summary=(
                    f"{node_path} contract is {contract.value.status.value}: "
                    f"{len(truth.deps)} dependencies, {len(truth.interfaces)} interfaces, "
                    f"{len(truth.owned_refs) + len(truth.context_refs)} material references."
                ),
            ),
            warnings=contract.issues,
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
    ) -> ServiceResult[NodeDependencyMutationReceipt]:
        return self.dependency.add_node_dep(
            repo_root,
            node_path=node_path,
            target_node=target_node,
            reason=reason,
            actor=actor,
            expected_decl_names=expected_public_decl_names,
            target_repo=target_repo,
        )

    def remove_current_node_dep(
        self,
        repo_root: Path,
        *,
        node_path: str,
        index: int,
        actor: str,
    ) -> ServiceResult[NodeDependencyMutationReceipt]:
        return self.dependency.remove_node_dep(
            repo_root,
            node_path=node_path,
            index=index,
            actor=actor,
        )

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
    ) -> ServiceResult[CurrentNodeMaterialMutationReceipt]:
        before = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not before.ok or before.value is None:
            return self.runtime.foundation.fail(before.issues)
        previous = list(
            before.value.contract.owned_refs
            if ref_scope == "owned"
            else before.value.contract.context_refs
        )
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
        return self._current_node_material_receipt(
            node_path=node_path,
            ref_scope=ref_scope,
            operation="add",
            previous=previous,
            mutation=mutation,
        )

    def remove_current_material_ref(
        self,
        repo_root: Path,
        *,
        node_path: str,
        ref_scope: Literal["owned", "context"],
        index: int,
        actor: str,
    ) -> ServiceResult[CurrentNodeMaterialMutationReceipt]:
        before = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not before.ok or before.value is None:
            return self.runtime.foundation.fail(before.issues)
        previous = list(
            before.value.contract.owned_refs
            if ref_scope == "owned"
            else before.value.contract.context_refs
        )
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
        return self._current_node_material_receipt(
            node_path=node_path,
            ref_scope=ref_scope,
            operation="remove",
            previous=previous,
            mutation=mutation,
        )

    def finalize_content_task_result(
        self,
        repo_root: Path,
        *,
        node_path: str,
        task_result: ContentTaskResultView | dict[str, object] | object,
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
        current = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        if (
            parsed_result.value.contract_version is not None
            and current.value.version is not None
            and parsed_result.value.contract_version != current.value.version
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "content_task_contract_version_mismatch",
                    "Content task result contract version does not match the current node contract version.",
                    object_ref=node_path,
                    field="task_result.contract_version",
                    current=str(parsed_result.value.contract_version),
                    expected=str(current.value.version),
                )
            )

        if current.value.version_status == ContractVersionStatus.COMMITTED:
            existing_summary = (current.value.contract.summary or "").strip()
            requested_summary = coordinator_summary.strip()
            if existing_summary != requested_summary:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "content_task_already_finalized",
                        "Content task result is already finalized for this contract version; its committed Coordinator summary cannot be replaced.",
                        object_ref=node_path,
                        current=existing_summary,
                        expected=requested_summary,
                    )
                )
            gate = self.runtime.foundation.gate_passed(
                "content_task_finalize_already_committed",
                summary="Content task result was already finalized for this committed contract version.",
            )
            return self.runtime.foundation.ok(
                self._content_task_finalize_view(
                    node_path=node_path,
                    task_result=parsed_result.value,
                    coordinator_summary=existing_summary,
                    contract=current.value,
                    gate=gate,
                    finalized=True,
                    contract_summary_written=True,
                    contract_committed=True,
                    summary="Content task result was already finalized; returned the existing committed contract state.",
                )
            )

        if parsed_result.value.outcome == ContentTaskOutcome.READY:
            gate = self._check_content_task_ready(repo_root, node_path=node_path)
            if not gate.ok or gate.value is None:
                return self.runtime.foundation.fail(gate.issues)
            if not gate.value.passed:
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
            committed = self.commit_content_contract(repo_root, node_path=node_path, summary=coordinator_summary)
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
            summary=f"Content task {parsed_result.value.outcome.value} result is accepted for contract commit.",
        )
        committed = self.commit_content_contract(repo_root, node_path=node_path, summary=coordinator_summary)
        if not committed.ok or committed.value is None:
            return self.runtime.foundation.fail(committed.issues)
        return self.runtime.foundation.ok(
            self._content_task_finalize_view(
                node_path=node_path,
                task_result=parsed_result.value,
                coordinator_summary=coordinator_summary.strip(),
                contract=committed.value,
                gate=gate,
                finalized=True,
                contract_summary_written=True,
                contract_committed=True,
                summary=f"Content task result finalized as {parsed_result.value.outcome.value}; contract summary was committed.",
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
        repo_root = Path(repo_root)
        issues = []
        main = self.contract.get_visible_contract(repo_root, node_path="Main")
        if not main.ok or main.value is None:
            issues.append(
                self.runtime.foundation.issue(
                    "main_scope_not_committed",
                    "Repo-ready intent requires a committed Main Scope contract.",
                    object_ref="Main",
                )
            )
        requirements = self.runtime.repo_workspace.requirement.list_requirements(
            repo_root,
            status="open",
        )
        if not requirements.ok or requirements.value is None:
            return self.runtime.foundation.fail(requirements.issues)
        if requirements.value:
            issues.append(
                self.runtime.foundation.issue(
                    "open_requirements_block_repo_ready",
                    "Open repository requirements remain before repo-ready audit.",
                    expected="0 open requirements",
                    current=str(len(requirements.value)),
                )
            )
        nodes = self.node_tree.node_store.list_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        active_nodes = [node for node in nodes.value if node.lifecycle.value == "active"]
        open_contracts = sorted(
            node.path
            for node in active_nodes
            if node.open_contract_version is not None or node.active_contract_version is None
        )
        if open_contracts:
            issues.append(
                self.runtime.foundation.issue(
                    "repo_ready_node_contracts_open",
                    "Active nodes still have open or uncommitted contracts.",
                    current=", ".join(open_contracts),
                )
            )
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        policy = self.runtime.repo_workspace.publication.resolve_policy(repo_root)
        if not policy.ok or policy.value is None:
            return self.runtime.foundation.fail(policy.issues)
        gate = (
            self.runtime.foundation.gate_failed(
                "repo_ready_intent_structure",
                issues,
                summary="Repository structure has blockers before deterministic repo-ready audit.",
            )
            if issues
            else self.runtime.foundation.gate_passed(
                "repo_ready_intent_structure",
                summary="Repository structure is ready to request the deterministic repo-ready audit.",
            )
        )
        return self.runtime.foundation.ok(
            RepoReadyNodeView(
                main_contract_version=(main.value.version if main.value is not None else None),
                main_contract_version_status=(
                    main.value.version_status if main.value is not None else None
                ),
                publication_status=publication.value.publication.status.value,
                release_policy=policy.value.policy.release_policy.value,
                open_requirement_count=len(requirements.value),
                active_node_count=len(active_nodes),
                open_contract_node_paths=open_contracts,
                structural_gate=gate,
                ready_to_submit_intent=gate.passed,
                summary=(
                    "Repo-ready intent may be submitted; the deterministic Step will run the authoritative audit."
                    if gate.passed
                    else "Resolve structural blockers before submitting repo-ready intent."
                ),
            )
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

    def _parse_content_task_result(self, task_result: ContentTaskResultView | dict[str, object] | object) -> ServiceResult[ContentTaskResultView]:
        if isinstance(task_result, ContentTaskResultView):
            return self.runtime.foundation.ok(task_result)
        try:
            if hasattr(task_result, "model_dump"):
                return self.runtime.foundation.ok(ContentTaskResultView.model_validate(task_result.model_dump()))
            return self.runtime.foundation.ok(ContentTaskResultView.model_validate(task_result))
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("content_task_result_invalid", f"Content task result is invalid: {exc}", field="task_result")
            )

    def _check_content_task_ready(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        provider = self._content_ready_gate or self.runtime.validation_snapshot
        completion = getattr(provider, "check_content_node_completion", None)
        if callable(completion):
            view = completion(repo_root, node_path=node_path)
            if not view.ok or view.value is None:
                return self.runtime.foundation.fail(view.issues)
            return self.runtime.foundation.ok(view.value.gate)
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

        identity_gate = self.interface.check_bound_interface_lean_identities(
            repo_root,
            node_path=scope_path,
        )
        if not identity_gate.ok or identity_gate.value is None:
            return self.runtime.foundation.fail(identity_gate.issues)
        reports.append(identity_gate.value)

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

    def _current_node_material_receipt(
        self,
        *,
        node_path: str,
        ref_scope: Literal["owned", "context"],
        operation: Literal["add", "remove"],
        previous: list[ContractMaterialRef],
        mutation: ServiceResult[NodeContractView],
    ) -> ServiceResult[CurrentNodeMaterialMutationReceipt]:
        if not mutation.ok or mutation.value is None:
            return self.runtime.foundation.fail(mutation.issues)
        current = list(
            mutation.value.contract.owned_refs
            if ref_scope == "owned"
            else mutation.value.contract.context_refs
        )
        added = [item for item in current if item not in previous]
        removed = [item for item in previous if item not in current]
        return self.runtime.foundation.ok(
            CurrentNodeMaterialMutationReceipt(
                node_path=node_path,
                ref_scope=ref_scope,
                operation=operation,
                changed=previous != current,
                added=added,
                removed=removed,
                summary=f"{operation.capitalize()} current-node {ref_scope} material reference.",
            ),
            warnings=mutation.issues,
        )

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
