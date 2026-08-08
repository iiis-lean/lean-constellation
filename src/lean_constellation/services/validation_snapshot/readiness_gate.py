"""Admission, handoff, ready, and commit gate façade."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.preparation import RepoDependencyRequirementStatus
from lean_constellation.domain.repo import (
    ProofAvailability,
    RepoCompletionMode,
    RepoFormat,
    RepoPublicationStatus,
    completion_mode_satisfies,
    proof_availability_for_completion_mode,
)
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.decl_graph.availability_policy import is_theorem_like
from lean_constellation.services.foundation.module_layout import local_module_name, native_project_name
from lean_constellation.services.node import ContractVersionStatus, NodeKind, NodeService
from lean_constellation.services.foundation import GateReport, ServiceResult
from lean_constellation.services.validation_snapshot.consistency_check import ConsistencyCheckComponent

if TYPE_CHECKING:
    from lean_constellation.services.adapter import AdapterService
    from lean_constellation.services.lean_projection import LeanProjectionService
    from lean_constellation.services.material import MaterialService
    from lean_constellation.services.repo_workspace import RepoWorkspaceService
    from lean_constellation.services.runtime import LeanRuntimeServices


class ContentReadinessProvider(Protocol):
    """Provider for DeclGraph-owned Content node readiness."""

    def check_content_node_ready(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        ...


class _MissingContentReadinessProvider:
    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def check_content_node_ready(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        del repo_root
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_failed(
                "content_decl_graph_readiness",
                self.runtime.foundation.issue(
                    "content_readiness_provider_missing",
                    "No DeclGraph readiness provider is configured for Content node readiness.",
                    object_ref=node_path,
                    suggested_action="Inject a DeclGraph readiness provider before accepting Content node ready submit.",
                ),
                summary="Content DeclGraph readiness provider is missing.",
            )
        )


class ContentReadyGateView(StrictModel):
    """Submit-oriented view for Content node ready checks."""

    node_path: str
    contract_version: int | None = None
    contract_version_status: ContractVersionStatus | None = None
    gate: GateReport
    ready_to_submit: bool
    summary: str


class ContentNodeCompletionGateView(StrictModel):
    """Submit-oriented view for Content node completion checks."""

    node_path: str
    task_completion_mode: RepoCompletionMode
    repo_completion_mode: RepoCompletionMode
    remaining_repo_gap: bool
    target_proof_availability: ProofAvailability
    contract_version: int | None = None
    contract_version_status: ContractVersionStatus | None = None
    gate: GateReport
    ready_to_submit: bool
    checked_decl_count: int = 0
    blocking_issue_kinds: list[str] = Field(default_factory=list)
    summary: str


class ScopeReadyGateView(StrictModel):
    """Submit-oriented view for Scope close checks."""

    scope_path: str
    direct_child_count: int = 0
    blocking_child_count: int = 0
    interface_count: int = 0
    export_count: int = 0
    child_readiness_gate: GateReport
    scope_commit_gate: GateReport
    gate: GateReport
    ready_to_commit: bool
    summary: str


class RepoReadyGateView(StrictModel):
    """Submit-oriented view for repository ready checks."""

    root_scope_path: str = "Main"
    target_proof_availability: ProofAvailability = ProofAvailability.PROVED
    publication_status: RepoPublicationStatus = RepoPublicationStatus.DEVELOPING
    main_contract_version: int | None = None
    main_contract_version_status: ContractVersionStatus | None = None
    gate: GateReport
    ready_to_submit: bool
    summary: str
    blocking_issue_kinds: list[str] = Field(default_factory=list)


class ReadinessGateComponent:
    """Central gate façade used by Flow steps and submit tools."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        repo_workspace: RepoWorkspaceService | None = None,
        material: MaterialService | None = None,
        node: NodeService | None = None,
        adapter: AdapterService | None = None,
        lean_projection: LeanProjectionService | None = None,
        consistency: ConsistencyCheckComponent | None = None,
        content_readiness_provider: ContentReadinessProvider | None = None,
    ) -> None:
        self.runtime = runtime
        self._repo_workspace_override = repo_workspace
        self._material_override = material
        self._node_override = node
        self._lean_projection_override = lean_projection
        self._adapter_override = adapter
        self.consistency = consistency or ConsistencyCheckComponent(
            runtime,
            material=self.material,
            node=self.node,
            adapter=self.adapter,
            lean_projection=self.lean_projection,
        )
        self.content_readiness_provider = content_readiness_provider or _MissingContentReadinessProvider(runtime)

    @property
    def repo_workspace(self) -> RepoWorkspaceService:
        return self._repo_workspace_override or self.runtime.repo_workspace

    @property
    def material(self) -> MaterialService:
        return self._material_override or self.runtime.material

    @property
    def node(self) -> NodeService:
        return self._node_override or self.runtime.node

    @property
    def lean_projection(self) -> LeanProjectionService:
        return self._lean_projection_override or self.runtime.lean_projection

    @property
    def adapter(self) -> AdapterService:
        return self._adapter_override or self.runtime.adapter

    def check_native_handoff_gate(self, repo_root: Path) -> ServiceResult[GateReport]:
        reports: list[GateReport] = []

        native = self.repo_workspace.validate_native_handoff(Path(repo_root))
        if not native.ok or native.value is None:
            return self.runtime.foundation.fail(native.issues)
        reports.append(native.value)  # type: ignore[arg-type]

        source = self.consistency.check_source_corpus_consistency(Path(repo_root))
        if not source.ok or source.value is None:
            return self.runtime.foundation.fail(source.issues)
        reports.append(source.value)

        index = self.consistency.check_source_index_consistency(Path(repo_root))
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        reports.append(index.value)

        root_interfaces = self.node.check_root_main_handoff_interfaces(Path(repo_root))
        if not root_interfaces.ok or root_interfaces.value is None:
            return self.runtime.foundation.fail(root_interfaces.issues)
        reports.append(root_interfaces.value)

        main_contract = self.node.contract.get_current_contract(Path(repo_root), node_path="Main")
        if not main_contract.ok or main_contract.value is None:
            return self.runtime.foundation.fail(main_contract.issues)
        contract_issues = []
        if main_contract.value.contract.goal.strip() == "":
            contract_issues.append(self.runtime.foundation.issue("main_contract_goal_missing", "Main contract goal is missing.", object_ref="Main"))
        if main_contract.value.contract.boundary.strip() == "":
            contract_issues.append(
                self.runtime.foundation.issue("main_contract_boundary_missing", "Main contract boundary is missing.", object_ref="Main")
            )
        if main_contract.value.contract.objective is None or not main_contract.value.contract.objective.strip():
            contract_issues.append(
                self.runtime.foundation.issue("main_contract_objective_missing", "Main contract objective is missing.", object_ref="Main")
            )
        reports.append(
            self.runtime.foundation.gate_failed("main_contract_handoff", contract_issues, summary="Main contract is not ready.")
            if contract_issues
            else self.runtime.foundation.gate_passed("main_contract_handoff", summary="Main contract handoff fields are present.")
        )

        return self.runtime.foundation.ok(self.runtime.foundation.merge_gate_reports("native_handoff_gate", reports))

    def check_content_task_admission(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        reports: list[GateReport] = []

        admission = self.node.prepare_content_task_admission(Path(repo_root), node_path=node_path)
        if not admission.ok or admission.value is None:
            return self.runtime.foundation.fail(admission.issues)
        reports.append(admission.value)

        deps = self.node.dependency.validate_node_deps(Path(repo_root), node_path=node_path)
        if not deps.ok or deps.value is None:
            return self.runtime.foundation.fail(deps.issues)
        reports.append(deps.value)

        prelude = self.lean_projection.node_projection.check_prelude_sync(Path(repo_root), node_path=node_path)
        if not prelude.ok or prelude.value is None:
            return self.runtime.foundation.fail(prelude.issues)
        reports.append(prelude.value)

        return self.runtime.foundation.ok(self.runtime.foundation.merge_gate_reports("content_task_admission", reports))

    def check_content_node_ready(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        reports: list[GateReport] = []
        graph = self.content_readiness_provider.check_content_node_ready(Path(repo_root), node_path=node_path)
        if not graph.ok or graph.value is None:
            return self.runtime.foundation.fail(graph.issues)
        reports.append(graph.value)

        contract = self.node.contract.get_current_contract(Path(repo_root), node_path=node_path)
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        issues = []
        for interface in contract.value.contract.interfaces:
            if interface.bound_decl is None:
                issues.append(
                    self.runtime.foundation.issue(
                        "content_interface_unbound",
                        f"Content interface is not bound: {interface.name}",
                        object_ref=node_path,
                        field=f"interfaces.{interface.name}.bound_decl",
                    )
                )
        reports.append(
            self.runtime.foundation.gate_failed("content_interfaces_bound", issues, summary=f"{len(issues)} content interfaces are unbound.")
            if issues
            else self.runtime.foundation.gate_passed("content_interfaces_bound", summary="Content interfaces are bound.")
        )

        projection = self.consistency.check_projection_sync(Path(repo_root), scope=node_path)
        if not projection.ok or projection.value is None:
            return self.runtime.foundation.fail(projection.issues)
        reports.append(projection.value)
        return self.runtime.foundation.ok(self.runtime.foundation.merge_gate_reports("content_node_ready", reports))

    def check_content_node_completion(
        self,
        repo_root: Path,
        *,
        node_path: str,
        contract_version: int | None = None,
    ) -> ServiceResult[ContentNodeCompletionGateView]:
        repo_root = Path(repo_root)
        contract = self.node.contract.get_current_contract(repo_root, node_path=node_path)
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        if contract_version is not None and contract.value.version != contract_version:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "content_task_contract_version_mismatch",
                    "Content completion audit contract version does not match current contract truth.",
                    object_ref=node_path,
                    field="contract_version",
                    current=str(contract_version),
                    expected=str(contract.value.version),
                )
            )

        config = self.repo_workspace.metadata.get_repo_config(repo_root)
        if not config.ok or config.value is None:
            return self.runtime.foundation.fail(config.issues)
        task_mode = contract.value.contract.task_completion_mode
        repo_mode = config.value.config.completion_mode
        if not completion_mode_satisfies(repo_mode, task_mode):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "contract_task_completion_mode_exceeds_repo",
                    "Content contract task completion mode exceeds the repository completion mode.",
                    object_ref=node_path,
                    field="task_completion_mode",
                    current=task_mode.value,
                    expected=f"at most {repo_mode.value}",
                )
            )
        target = proof_availability_for_completion_mode(
            task_mode
        )
        provider_target = proof_availability_for_completion_mode(repo_mode)
        remaining_repo_gap = not completion_mode_satisfies(task_mode, repo_mode)

        reports: list[GateReport] = []
        refreshed_boundary, interfaces_ready = self._refresh_node_boundary(
            repo_root,
            node_path=node_path,
            include_interfaces=not remaining_repo_gap,
        )
        reports.extend(refreshed_boundary)
        node_deps = self.node.dependency.validate_node_deps(repo_root, node_path=node_path)
        if not node_deps.ok or node_deps.value is None:
            return self.runtime.foundation.fail(node_deps.issues)
        reports.append(node_deps.value)
        interface_issues = []
        decl_refs: dict[tuple[str | None, str, str], DeclRef] = {}
        for interface in contract.value.contract.interfaces:
            if interface.bound_decl is None:
                interface_issues.append(
                    self.runtime.foundation.issue(
                        "content_interface_unbound",
                        f"Content interface is not bound: {interface.name}",
                        object_ref=node_path,
                        field=f"interfaces.{interface.name}.bound_decl",
                    )
                )
                continue
            decl_refs[self._decl_ref_key(interface.bound_decl)] = interface.bound_decl
        reports.append(
            self.runtime.foundation.gate_failed("content_interfaces_bound", interface_issues, summary=f"{len(interface_issues)} content interfaces are unbound.")
            if interface_issues
            else self.runtime.foundation.gate_passed("content_interfaces_bound", summary="Content interfaces are bound.")
        )

        public_decls = self.runtime.decl_graph.list_content_public_decls(repo_root, node_path=node_path)
        if not public_decls.ok or public_decls.value is None:
            return self.runtime.foundation.fail(public_decls.issues)
        for public in public_decls.value:
            if public.public:
                decl_refs[self._decl_ref_key(public.ref)] = public.ref

        decl_issues = []
        for ref in decl_refs.values():
            if ref.repo is None and ref.node == node_path:
                continue
            checked = self._check_decl_ref_proof_policy(repo_root, ref=ref, fallback_node_path=node_path)
            if not checked.ok or checked.value is None:
                return self.runtime.foundation.fail(checked.issues)
            if not checked.value.ready:
                decl_issues.append(
                    self.runtime.foundation.issue(
                        "content_decl_proof_policy_unsatisfied",
                        f"Declaration does not satisfy current proof availability policy: {ref.name}",
                        object_ref=f"{ref.repo + ':' if ref.repo else ''}{ref.node}:{ref.name}",
                        details={
                            "target_proof_availability": provider_target.value,
                            "summary": checked.value.summary,
                        },
                    )
                )
        reports.append(
            self.runtime.foundation.gate_failed(
                "content_decl_proof_policy_satisfied",
                decl_issues,
                summary=f"{len(decl_issues)} public/interface declarations do not satisfy proof policy.",
            )
            if decl_issues
            else self.runtime.foundation.gate_passed(
                "content_decl_proof_policy_satisfied",
                summary="Cross-node and external interface declarations satisfy proof policy.",
                warnings=public_decls.issues,
            )
        )

        active_names = self.runtime.decl_graph.list_active_decl_names(repo_root, node_path=node_path)
        if not active_names.ok or active_names.value is None:
            return self.runtime.foundation.fail(active_names.issues)
        local_readiness = self.runtime.decl_graph.check_decl_proof_policy_batch(
            repo_root,
            roots=[(node_path, decl_name, target) for decl_name in active_names.value],
        )
        if not local_readiness.ok or local_readiness.value is None:
            return self.runtime.foundation.fail(local_readiness.issues)
        local_policy_issues = []
        for decl_name, policy_report in zip(active_names.value, local_readiness.value, strict=True):
            revision = self.runtime.decl_graph.get_current_decl_revision(
                repo_root,
                node_path=node_path,
                decl_name=decl_name,
            )
            if not revision.ok or revision.value is None:
                local_policy_issues.extend(revision.issues)
                continue
            stage = "proof" if target == ProofAvailability.PROVED and is_theorem_like(revision.value.kind) else "statement"
            if not policy_report.ready:
                local_policy_issues.append(
                    self.runtime.foundation.issue(
                        "content_decl_proof_policy_unsatisfied",
                        f"Declaration does not satisfy current proof availability policy: {decl_name}",
                        object_ref=f"{node_path}:{decl_name}",
                        details={"target_proof_availability": target.value, "summary": policy_report.summary},
                    )
                )
            formal = self.consistency.check_formal_stage_consistency(
                repo_root,
                node_path=node_path,
                decl_name=decl_name,
                stage=stage,
            )
            if not formal.ok or formal.value is None:
                local_policy_issues.extend(formal.issues)
            elif not formal.value.passed:
                local_policy_issues.extend(formal.value.issues)
            identity = self.lean_projection.check_decl_dependency_identity(
                repo_root,
                node_path=node_path,
                decl_name=decl_name,
                stage=stage,
            )
            if not identity.ok or identity.value is None:
                local_policy_issues.extend(identity.issues)
            elif not identity.value.passed:
                local_policy_issues.extend(identity.value.issues)
        reports.append(
            self.runtime.foundation.gate_failed(
                "content_local_decl_completion",
                local_policy_issues,
                summary=f"{len(local_policy_issues)} local declaration completion checks failed.",
            )
            if local_policy_issues
            else self.runtime.foundation.gate_passed(
                "content_local_decl_completion",
                summary=f"All {len(active_names.value)} active local declarations have synchronized captures and complete identities.",
            )
        )

        public_closure = self.node.public_statement_closure.check_content(
            repo_root,
            node_path=node_path,
        )
        if not public_closure.ok or public_closure.value is None:
            return self.runtime.foundation.fail(public_closure.issues)
        reports.append(public_closure.value)

        if interfaces_ready:
            reports.append(self._build_node_interfaces_gate(repo_root, node_path=node_path))

        projection = (
            self.lean_projection.node_projection.check_prelude_sync(
                repo_root,
                node_path=node_path,
            )
            if remaining_repo_gap
            else self.consistency.check_projection_sync(repo_root, scope=node_path)
        )
        if not projection.ok or projection.value is None:
            return self.runtime.foundation.fail(projection.issues)
        reports.append(projection.value)

        gate = self.runtime.foundation.merge_gate_reports("content_node_completion", reports)
        blocking_issue_kinds = sorted({issue.kind for issue in gate.issues if self.runtime.foundation.result_error.is_error_issue(issue)})
        return self.runtime.foundation.ok(
            ContentNodeCompletionGateView(
                node_path=node_path,
                task_completion_mode=task_mode,
                repo_completion_mode=repo_mode,
                remaining_repo_gap=remaining_repo_gap,
                target_proof_availability=target,
                contract_version=contract.value.version,
                contract_version_status=contract.value.version_status,
                gate=gate,
                ready_to_submit=gate.passed,
                checked_decl_count=len(set(active_names.value)) + len(
                    [ref for ref in decl_refs.values() if ref.repo is not None or ref.node != node_path]
                ),
                blocking_issue_kinds=blocking_issue_kinds,
                summary=(
                    (
                        "Content task target is complete; repository-level provider readiness remains pending."
                        if remaining_repo_gap
                        else "Content node is complete at the repository completion mode."
                    )
                    if gate.passed
                    else "Content node completion gate has blocking issues."
                ),
            ),
            warnings=[*contract.issues, *public_decls.issues],
        )

    def get_content_ready_view(self, repo_root: Path, *, node_path: str) -> ServiceResult[ContentReadyGateView]:
        contract = self.node.contract.get_current_contract(Path(repo_root), node_path=node_path)
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        gate = self.check_content_node_ready(Path(repo_root), node_path=node_path)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        return self.runtime.foundation.ok(
            ContentReadyGateView(
                node_path=node_path,
                contract_version=contract.value.version,
                contract_version_status=contract.value.version_status,
                gate=gate.value,
                ready_to_submit=gate.value.passed,
                summary=("Content node is ready to submit." if gate.value.passed else "Content node ready gate has blocking issues."),
            ),
            warnings=contract.issues,
        )

    def _check_decl_ref_proof_policy(self, repo_root: Path, *, ref: DeclRef, fallback_node_path: str):
        if ref.repo:
            provider_key = self.runtime.foundation.layout.ensure_safe_key(ref.repo)
            provider_root = repo_root.parent / provider_key
            node_path = ref.node
        else:
            provider_root = repo_root
            node_path = ref.node or fallback_node_path
        return self.runtime.decl_graph.check_decl_proof_policy_satisfied(provider_root, node_path=node_path, decl_name=ref.name)

    def _decl_ref_key(self, ref: DeclRef) -> tuple[str | None, str, str]:
        return (ref.repo, ref.node, ref.name)

    def check_content_node_blocked_submit(self, repo_root: Path, *, node_path: str, reason: str) -> ServiceResult[GateReport]:
        issues = []
        if not reason or not reason.strip():
            issues.append(
                self.runtime.foundation.issue(
                    "content_blocked_reason_required",
                    "Content blocked submit requires a reason.",
                    object_ref=node_path,
                    field="reason",
                )
            )
        node = self.node.node_tree.get_node(Path(repo_root), path=node_path)
        if not node.ok:
            return self.runtime.foundation.fail(node.issues)
        if node.value is not None and node.value.kind != NodeKind.CONTENT:
            issues.append(
                self.runtime.foundation.issue(
                    "node_not_content",
                    "Only Content nodes can submit blocked task result.",
                    object_ref=node_path,
                    current=node.value.kind.value,
                    expected=NodeKind.CONTENT.value,
                )
            )
        if issues:
            return self.runtime.foundation.ok(self.runtime.foundation.gate_failed("content_node_blocked_submit", issues, summary="Blocked submit is invalid."))
        return self.runtime.foundation.ok(self.runtime.foundation.gate_passed("content_node_blocked_submit", summary="Blocked submit is acceptable."))

    def check_scope_commit(self, repo_root: Path, *, scope_path: str, summary: str) -> ServiceResult[GateReport]:
        reports: list[GateReport] = []
        issues = []
        if not summary or not summary.strip():
            issues.append(self.runtime.foundation.issue("scope_summary_required", "Scope commit summary is required.", object_ref=scope_path, field="summary"))
        node = self.node.node_tree.get_node(Path(repo_root), path=scope_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        if node.value.kind != NodeKind.SCOPE:
            issues.append(
                self.runtime.foundation.issue(
                    "node_not_scope",
                    "Scope commit requires a Scope node.",
                    object_ref=scope_path,
                    current=node.value.kind.value,
                    expected=NodeKind.SCOPE.value,
                )
            )
        contract = self.node.contract.get_current_contract(Path(repo_root), node_path=scope_path)
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        if contract.value.version_status != ContractVersionStatus.OPEN:
            issues.append(
                self.runtime.foundation.issue(
                    "contract_not_open",
                    "Scope commit requires an open contract.",
                    object_ref=scope_path,
                    current=contract.value.version_status.value,
                    expected=ContractVersionStatus.OPEN.value,
                )
            )
        reports.append(
            self.runtime.foundation.gate_failed("scope_commit_base", issues, summary="Scope commit base checks failed.")
            if issues
            else self.runtime.foundation.gate_passed("scope_commit_base", summary="Scope commit base checks passed.")
        )

        exports = self.node.export.validate_scope_exports(Path(repo_root), scope_path=scope_path)
        if not exports.ok or exports.value is None:
            return self.runtime.foundation.fail(exports.issues)
        reports.append(exports.value)

        deps = self.node.dependency.validate_node_deps(Path(repo_root), node_path=scope_path)
        if not deps.ok or deps.value is None:
            return self.runtime.foundation.fail(deps.issues)
        reports.append(deps.value)

        public_closure = self.node.public_statement_closure.check_scope(
            Path(repo_root),
            scope_path=scope_path,
        )
        if not public_closure.ok or public_closure.value is None:
            return self.runtime.foundation.fail(public_closure.issues)
        reports.append(public_closure.value)

        refreshed_boundary, interfaces_ready = self._refresh_node_boundary(Path(repo_root), node_path=scope_path)
        reports.extend(refreshed_boundary)
        if interfaces_ready:
            reports.append(self._build_node_interfaces_gate(Path(repo_root), node_path=scope_path))

        projection = self.lean_projection.node_projection.check_interfaces_sync(Path(repo_root), node_path=scope_path)
        if not projection.ok or projection.value is None:
            return self.runtime.foundation.fail(projection.issues)
        reports.append(projection.value)
        return self.runtime.foundation.ok(self.runtime.foundation.merge_gate_reports("scope_commit", reports))

    def get_scope_ready_view(self, repo_root: Path, *, scope_path: str) -> ServiceResult[ScopeReadyGateView]:
        close = self.node.get_scope_close_view(Path(repo_root), scope_path=scope_path)
        if not close.ok or close.value is None:
            return self.runtime.foundation.fail(close.issues)
        gate = self.runtime.foundation.merge_gate_reports(
            "scope_ready",
            [
                close.value.child_readiness_gate,
                close.value.scope_commit_gate,
            ],
        )
        blocking_children = [child for child in close.value.children if not child.ready_for_scope_close]
        return self.runtime.foundation.ok(
            ScopeReadyGateView(
                scope_path=scope_path,
                direct_child_count=len(close.value.children),
                blocking_child_count=len(blocking_children),
                interface_count=len(close.value.interfaces.interfaces),
                export_count=len(close.value.exports),
                child_readiness_gate=close.value.child_readiness_gate,
                scope_commit_gate=close.value.scope_commit_gate,
                gate=gate,
                ready_to_commit=gate.passed,
                summary=("Scope is ready to commit." if gate.passed else "Scope ready gate has blocking issues."),
            ),
            warnings=close.issues,
        )

    def check_repo_ready(
        self,
        repo_root: Path,
        *,
        summary: str,
        include_targeted_builds: bool = True,
    ) -> ServiceResult[GateReport]:
        reports: list[GateReport] = []
        issues = []
        repo_root = Path(repo_root)
        if not summary or not summary.strip():
            issues.append(self.runtime.foundation.issue("repo_ready_summary_required", "Repo ready summary is required.", field="summary"))
        requirements = self.repo_workspace.requirement.list_requirements(repo_root, status=RepoDependencyRequirementStatus.OPEN)
        if not requirements.ok or requirements.value is None:
            return self.runtime.foundation.fail(requirements.issues)
        if requirements.value:
            issues.append(
                self.runtime.foundation.issue(
                    "open_requirements_block_repo_ready",
                    f"{len(requirements.value)} repo dependency requirements are still open.",
                    object_ref=str(repo_root),
                    expected="0 open requirements",
                )
            )
        main = self.node.contract.get_visible_contract(repo_root, node_path="Main")
        if not main.ok or main.value is None:
            issues.append(
                self.runtime.foundation.issue(
                    "main_scope_not_committed",
                    "Repo ready requires Main scope contract to be committed.",
                    object_ref="Main",
                    current="missing_or_uncommitted",
                    expected=ContractVersionStatus.COMMITTED.value,
                )
            )
        reports.append(
            self.runtime.foundation.gate_failed("repo_ready_base", issues, summary="Repo ready base checks failed.")
            if issues
            else self.runtime.foundation.gate_passed("repo_ready_base", summary="Repo ready base checks passed.")
        )

        protected_interfaces = self.node.check_root_main_handoff_interfaces(repo_root)
        if not protected_interfaces.ok or protected_interfaces.value is None:
            return self.runtime.foundation.fail(protected_interfaces.issues)
        reports.append(protected_interfaces.value)

        public_boundary = self._check_repo_public_boundary_proof_policy(repo_root)
        if not public_boundary.ok or public_boundary.value is None:
            return self.runtime.foundation.fail(public_boundary.issues)
        reports.append(public_boundary.value)

        statement_contracts = self.node.check_root_interface_statement_contracts(repo_root)
        if not statement_contracts.ok or statement_contracts.value is None:
            return self.runtime.foundation.fail(statement_contracts.issues)
        reports.append(statement_contracts.value)

        public_closure = self.node.public_statement_closure.check_scope(
            repo_root,
            scope_path="Main",
            visible=True,
        )
        if not public_closure.ok or public_closure.value is None:
            return self.runtime.foundation.fail(public_closure.issues)
        reports.append(public_closure.value)

        root_deps = self.node.dependency.validate_node_deps(repo_root, node_path="Main")
        if not root_deps.ok or root_deps.value is None:
            return self.runtime.foundation.fail(root_deps.issues)
        reports.append(root_deps.value)

        refreshed_boundary, interfaces_ready = self._refresh_node_boundary(repo_root, node_path="Main")
        reports.extend(refreshed_boundary)
        if include_targeted_builds and interfaces_ready:
            reports.append(self._build_node_interfaces_gate(repo_root, node_path="Main"))
        if include_targeted_builds:
            reports.append(
                self._build_module_gate(
                    repo_root,
                    module=native_project_name(repo_root),
                    gate_name="repo_public_module_build",
                )
            )

        source = self.consistency.check_source_corpus_consistency(repo_root)
        if source.ok and source.value is not None:
            reports.append(source.value)
        else:
            reports.append(
                self.runtime.foundation.gate_failed(
                    "source_corpus_consistency",
                    source.issues,
                    summary="Source corpus consistency could not be verified.",
                )
            )
        index = self.consistency.check_source_index_consistency(repo_root)
        if index.ok and index.value is not None:
            reports.append(index.value)
        else:
            reports.append(
                self.runtime.foundation.gate_failed(
                    "source_index_consistency",
                    index.issues,
                    summary="Source index consistency could not be verified.",
                )
            )
        projection = self.consistency.check_projection_sync(repo_root, scope="repo")
        if not projection.ok or projection.value is None:
            return self.runtime.foundation.fail(projection.issues)
        reports.append(projection.value)
        return self.runtime.foundation.ok(self.runtime.foundation.merge_gate_reports("repo_ready", reports))

    def _refresh_node_boundary(
        self,
        repo_root: Path,
        *,
        node_path: str,
        include_interfaces: bool = True,
    ) -> tuple[list[GateReport], bool]:
        reports: list[GateReport] = []
        interfaces_ready = include_interfaces
        refreshes = [("prelude", self.lean_projection.node_projection.refresh_prelude)]
        if include_interfaces:
            refreshes.append(
                ("interfaces", self.lean_projection.node_projection.refresh_interfaces)
            )
        for projection_kind, refresh in refreshes:
            refreshed = refresh(Path(repo_root), node_path=node_path)
            if not refreshed.ok or refreshed.value is None:
                reports.append(
                    self.runtime.foundation.gate_failed(
                        f"{projection_kind}_refresh",
                        refreshed.issues,
                        summary=f"Failed to refresh {node_path} {projection_kind} projection.",
                    )
                )
                if projection_kind == "interfaces":
                    interfaces_ready = False
                continue
            reports.append(
                self.runtime.foundation.gate_passed(
                    f"{projection_kind}_refresh",
                    summary=refreshed.value.summary,
                    warnings=refreshed.issues,
                )
            )
        return reports, interfaces_ready

    def _build_node_interfaces_gate(self, repo_root: Path, *, node_path: str) -> GateReport:
        return self._build_module_gate(
            Path(repo_root),
            module=local_module_name(Path(repo_root), f"{node_path}.Interfaces"),
            gate_name="interfaces_module_build",
        )

    def _build_module_gate(self, repo_root: Path, *, module: str | None, gate_name: str) -> GateReport:
        if module is None:
            return self.runtime.foundation.gate_failed(
                gate_name,
                [
                    self.runtime.foundation.issue(
                        "native_project_module_missing",
                        "Lean module build requires an initialized native project module.",
                        object_ref=str(repo_root),
                    )
                ],
                summary="Lean module build could not resolve the native project module.",
            )
        built = self.lean_projection.module_identity.build_module(Path(repo_root), module=module)
        if not built.ok or built.value is None:
            return self.runtime.foundation.gate_failed(
                gate_name,
                built.issues,
                summary=f"Lean module build failed for +{module}.",
            )
        return self.runtime.foundation.gate_passed(
            gate_name,
            summary=built.value.summary,
            warnings=built.issues,
        )

    def get_repo_ready_view(self, repo_root: Path) -> ServiceResult[RepoReadyGateView]:
        repo_root = Path(repo_root)
        main = self.node.contract.get_current_contract(repo_root, node_path="Main")
        if not main.ok or main.value is None:
            return self.runtime.foundation.fail(main.issues)
        config = self.repo_workspace.metadata.get_repo_config(repo_root)
        if not config.ok or config.value is None:
            return self.runtime.foundation.fail(config.issues)
        publication = self.repo_workspace.metadata.get_repo_publication(repo_root)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        repo_format = self.repo_workspace.metadata.get_repo_format(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        if repo_format.value.repo_format is RepoFormat.ADAPTER:
            contract_gate = (
                self.runtime.foundation.gate_passed(
                    "adapter_main_committed",
                    summary="Adapter Main contract is committed.",
                )
                if main.value.version_status is ContractVersionStatus.COMMITTED
                else self.runtime.foundation.gate_failed(
                    "adapter_main_committed",
                    self.runtime.foundation.issue(
                        "adapter_main_contract_not_committed",
                        "Adapter release readiness requires a committed Main contract.",
                        object_ref="Main",
                        current=main.value.version_status.value,
                        expected=ContractVersionStatus.COMMITTED.value,
                    ),
                    summary="Adapter Main contract is not committed.",
                )
            )
            adapter_gate = self.adapter.check_adapter_ready(repo_root)
            if not adapter_gate.ok or adapter_gate.value is None:
                return self.runtime.foundation.fail(adapter_gate.issues)
            gate = self.runtime.foundation.ok(
                self.runtime.foundation.merge_gate_reports(
                    "adapter_repo_ready",
                    [contract_gate, adapter_gate.value],
                )
            )
            success_summary = "Adapter repo is ready for Release preview."
            blocked_summary = "Adapter release-ready gate has blocking issues."
        else:
            gate = self.check_repo_ready(repo_root, summary="Repo ready preflight.")
            success_summary = "Repo is ready to submit."
            blocked_summary = "Repo ready gate has blocking issues."
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        return self.runtime.foundation.ok(
            RepoReadyGateView(
                target_proof_availability=proof_availability_for_completion_mode(
                    config.value.config.completion_mode
                ),
                publication_status=publication.value.publication.status,
                main_contract_version=main.value.version,
                main_contract_version_status=main.value.version_status,
                gate=gate.value,
                ready_to_submit=gate.value.passed,
                blocking_issue_kinds=sorted({issue.kind for issue in gate.value.issues}),
                summary=(success_summary if gate.value.passed else blocked_summary),
            ),
            warnings=main.issues,
        )

    def _check_repo_public_boundary_proof_policy(self, repo_root: Path) -> ServiceResult[GateReport]:
        config = self.repo_workspace.metadata.get_repo_config(repo_root)
        if not config.ok or config.value is None:
            return self.runtime.foundation.fail(config.issues)
        target = proof_availability_for_completion_mode(
            config.value.config.completion_mode
        )
        exports = self.node.export.list_scope_exports(repo_root, scope_path="Main")
        if not exports.ok or exports.value is None:
            return self.runtime.foundation.fail(exports.issues)
        issues = []
        for export in exports.value:
            checked = self._check_decl_ref_proof_policy(repo_root, ref=export.ref, fallback_node_path="Main")
            if not checked.ok or checked.value is None:
                return self.runtime.foundation.fail(checked.issues)
            if checked.value.ready:
                continue
            issues.append(
                self.runtime.foundation.issue(
                    "repo_public_decl_proof_policy_unsatisfied",
                    f"Repo public declaration does not satisfy current proof availability policy: {export.ref.name}",
                    object_ref=f"{export.ref.node}:{export.ref.name}",
                    details={
                        "target_proof_availability": target.value,
                        "summary": checked.value.summary,
                    },
                )
            )
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "repo_public_boundary_proof_policy_satisfied",
                    issues,
                    summary=f"{len(issues)} repo public declarations do not satisfy proof policy.",
                )
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "repo_public_boundary_proof_policy_satisfied",
                summary=f"{len(exports.value)} repo public declarations satisfy {target.value} proof availability.",
                warnings=exports.issues,
            )
        )

    def check_adapter_ready(self, repo_root: Path) -> ServiceResult[GateReport]:
        return self.adapter.check_adapter_ready(Path(repo_root))
