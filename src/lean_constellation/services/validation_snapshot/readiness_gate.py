"""Admission, handoff, ready, and commit gate façade."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from lean_constellation.domain.preparation import RepoDependencyRequirementStatus
from lean_constellation.services.adapter import AdapterService
from lean_constellation.services.foundation import FoundationService, GateReport, ServiceResult
from lean_constellation.services.lean_projection import LeanProjectionService
from lean_constellation.services.material import MaterialService
from lean_constellation.services.node import ContractVersionStatus, NodeKind, NodeService
from lean_constellation.services.repo_workspace import RepoWorkspaceService
from lean_constellation.services.validation_snapshot.consistency_check import ConsistencyCheckComponent


class ContentReadinessProvider(Protocol):
    """Provider for DeclGraph-owned Content node readiness."""

    def check_content_node_ready(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        ...


class _MissingContentReadinessProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def check_content_node_ready(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        del repo_root
        return self.foundation.ok(
            self.foundation.gate_failed(
                "content_decl_graph_readiness",
                self.foundation.issue(
                    "content_readiness_provider_missing",
                    "No DeclGraph readiness provider is configured for Content node readiness.",
                    object_ref=node_path,
                    suggested_action="Inject a DeclGraph readiness provider before accepting Content node ready submit.",
                ),
                summary="Content DeclGraph readiness provider is missing.",
            )
        )


class ReadinessGateComponent:
    """Central gate façade used by Flow steps and submit tools."""

    def __init__(
        self,
        *,
        foundation: FoundationService | None = None,
        repo_workspace: RepoWorkspaceService | None = None,
        material: MaterialService | None = None,
        node: NodeService | None = None,
        adapter: AdapterService | None = None,
        lean_projection: LeanProjectionService | None = None,
        consistency: ConsistencyCheckComponent | None = None,
        content_readiness_provider: ContentReadinessProvider | None = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.repo_workspace = repo_workspace or RepoWorkspaceService(foundation=self.foundation)
        self.material = material or MaterialService(foundation=self.foundation)
        self.node = node or NodeService(
            foundation=self.foundation,
            repo_workspace=self.repo_workspace,
            material=self.material,
        )
        self.lean_projection = lean_projection or LeanProjectionService(foundation=self.foundation)
        self.adapter = adapter or AdapterService(
            foundation=self.foundation,
            repo_workspace=self.repo_workspace,
            node=self.node,
            lean_projection=self.lean_projection,
        )
        self.consistency = consistency or ConsistencyCheckComponent(
            foundation=self.foundation,
            material=self.material,
            node=self.node,
            adapter=self.adapter,
            lean_projection=self.lean_projection,
        )
        self.content_readiness_provider = content_readiness_provider or _MissingContentReadinessProvider(self.foundation)

    def check_native_handoff_gate(self, repo_root: Path) -> ServiceResult[GateReport]:
        reports: list[GateReport] = []

        native = self.repo_workspace.validate_native_handoff(Path(repo_root))
        if not native.ok or native.value is None:
            return self.foundation.fail(native.issues)
        reports.append(native.value)  # type: ignore[arg-type]

        source = self.consistency.check_source_corpus_consistency(Path(repo_root))
        if not source.ok or source.value is None:
            return self.foundation.fail(source.issues)
        reports.append(source.value)

        index = self.consistency.check_source_index_consistency(Path(repo_root))
        if not index.ok or index.value is None:
            return self.foundation.fail(index.issues)
        reports.append(index.value)

        root_interfaces = self.node.check_root_main_handoff_interfaces(Path(repo_root))
        if not root_interfaces.ok or root_interfaces.value is None:
            return self.foundation.fail(root_interfaces.issues)
        reports.append(root_interfaces.value)

        main_contract = self.node.contract.get_current_contract(Path(repo_root), node_path="Main")
        if not main_contract.ok or main_contract.value is None:
            return self.foundation.fail(main_contract.issues)
        contract_issues = []
        if main_contract.value.contract.goal.strip() == "":
            contract_issues.append(self.foundation.issue("main_contract_goal_missing", "Main contract goal is missing.", object_ref="Main"))
        if main_contract.value.contract.boundary.strip() == "":
            contract_issues.append(
                self.foundation.issue("main_contract_boundary_missing", "Main contract boundary is missing.", object_ref="Main")
            )
        if main_contract.value.contract.objective is None or not main_contract.value.contract.objective.strip():
            contract_issues.append(
                self.foundation.issue("main_contract_objective_missing", "Main contract objective is missing.", object_ref="Main")
            )
        reports.append(
            self.foundation.gate_failed("main_contract_handoff", contract_issues, summary="Main contract is not ready.")
            if contract_issues
            else self.foundation.gate_passed("main_contract_handoff", summary="Main contract handoff fields are present.")
        )

        return self.foundation.ok(self.foundation.merge_gate_reports("native_handoff_gate", reports))

    def check_content_task_admission(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        reports: list[GateReport] = []

        admission = self.node.prepare_content_task_admission(Path(repo_root), node_path=node_path)
        if not admission.ok or admission.value is None:
            return self.foundation.fail(admission.issues)
        reports.append(admission.value)

        deps = self.node.dependency.validate_node_deps(Path(repo_root), node_path=node_path)
        if not deps.ok or deps.value is None:
            return self.foundation.fail(deps.issues)
        reports.append(deps.value)

        prelude = self.lean_projection.node_projection.check_prelude_sync(Path(repo_root), node_path=node_path)
        if not prelude.ok or prelude.value is None:
            return self.foundation.fail(prelude.issues)
        reports.append(prelude.value)

        return self.foundation.ok(self.foundation.merge_gate_reports("content_task_admission", reports))

    def check_content_node_ready(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        reports: list[GateReport] = []
        graph = self.content_readiness_provider.check_content_node_ready(Path(repo_root), node_path=node_path)
        if not graph.ok or graph.value is None:
            return self.foundation.fail(graph.issues)
        reports.append(graph.value)

        contract = self.node.contract.get_current_contract(Path(repo_root), node_path=node_path)
        if not contract.ok or contract.value is None:
            return self.foundation.fail(contract.issues)
        issues = []
        for interface in contract.value.contract.interfaces:
            if interface.bound_decl is None:
                issues.append(
                    self.foundation.issue(
                        "content_interface_unbound",
                        f"Content interface is not bound: {interface.name}",
                        object_ref=node_path,
                        field=f"interfaces.{interface.name}.bound_decl",
                    )
                )
        reports.append(
            self.foundation.gate_failed("content_interfaces_bound", issues, summary=f"{len(issues)} content interfaces are unbound.")
            if issues
            else self.foundation.gate_passed("content_interfaces_bound", summary="Content interfaces are bound.")
        )

        projection = self.consistency.check_projection_sync(Path(repo_root), scope=node_path)
        if not projection.ok or projection.value is None:
            return self.foundation.fail(projection.issues)
        reports.append(projection.value)
        return self.foundation.ok(self.foundation.merge_gate_reports("content_node_ready", reports))

    def check_content_node_blocked_submit(self, repo_root: Path, *, node_path: str, reason: str) -> ServiceResult[GateReport]:
        issues = []
        if not reason or not reason.strip():
            issues.append(
                self.foundation.issue(
                    "content_blocked_reason_required",
                    "Content blocked submit requires a reason.",
                    object_ref=node_path,
                    field="reason",
                )
            )
        node = self.node.node_tree.get_node(Path(repo_root), path=node_path)
        if not node.ok:
            return self.foundation.fail(node.issues)
        if node.value is not None and node.value.kind != NodeKind.CONTENT:
            issues.append(
                self.foundation.issue(
                    "node_not_content",
                    "Only Content nodes can submit blocked task result.",
                    object_ref=node_path,
                    current=node.value.kind.value,
                    expected=NodeKind.CONTENT.value,
                )
            )
        if issues:
            return self.foundation.ok(self.foundation.gate_failed("content_node_blocked_submit", issues, summary="Blocked submit is invalid."))
        return self.foundation.ok(self.foundation.gate_passed("content_node_blocked_submit", summary="Blocked submit is acceptable."))

    def check_scope_commit(self, repo_root: Path, *, scope_path: str, summary: str) -> ServiceResult[GateReport]:
        reports: list[GateReport] = []
        issues = []
        if not summary or not summary.strip():
            issues.append(self.foundation.issue("scope_summary_required", "Scope commit summary is required.", object_ref=scope_path, field="summary"))
        node = self.node.node_tree.get_node(Path(repo_root), path=scope_path)
        if not node.ok or node.value is None:
            return self.foundation.fail(node.issues)
        if node.value.kind != NodeKind.SCOPE:
            issues.append(
                self.foundation.issue(
                    "node_not_scope",
                    "Scope commit requires a Scope node.",
                    object_ref=scope_path,
                    current=node.value.kind.value,
                    expected=NodeKind.SCOPE.value,
                )
            )
        contract = self.node.contract.get_current_contract(Path(repo_root), node_path=scope_path)
        if not contract.ok or contract.value is None:
            return self.foundation.fail(contract.issues)
        if contract.value.version_status != ContractVersionStatus.OPEN:
            issues.append(
                self.foundation.issue(
                    "contract_not_open",
                    "Scope commit requires an open contract.",
                    object_ref=scope_path,
                    current=contract.value.version_status.value,
                    expected=ContractVersionStatus.OPEN.value,
                )
            )
        reports.append(
            self.foundation.gate_failed("scope_commit_base", issues, summary="Scope commit base checks failed.")
            if issues
            else self.foundation.gate_passed("scope_commit_base", summary="Scope commit base checks passed.")
        )

        exports = self.node.export.validate_scope_exports(Path(repo_root), scope_path=scope_path)
        if not exports.ok or exports.value is None:
            return self.foundation.fail(exports.issues)
        reports.append(exports.value)

        deps = self.node.dependency.validate_node_deps(Path(repo_root), node_path=scope_path)
        if not deps.ok or deps.value is None:
            return self.foundation.fail(deps.issues)
        reports.append(deps.value)

        projection = self.lean_projection.node_projection.check_interfaces_sync(Path(repo_root), node_path=scope_path)
        if not projection.ok or projection.value is None:
            return self.foundation.fail(projection.issues)
        reports.append(projection.value)
        return self.foundation.ok(self.foundation.merge_gate_reports("scope_commit", reports))

    def check_repo_ready(self, repo_root: Path, *, summary: str) -> ServiceResult[GateReport]:
        reports: list[GateReport] = []
        issues = []
        if not summary or not summary.strip():
            issues.append(self.foundation.issue("repo_ready_summary_required", "Repo ready summary is required.", field="summary"))
        requirements = self.repo_workspace.requirement.list_requirements(Path(repo_root), status=RepoDependencyRequirementStatus.OPEN)
        if not requirements.ok or requirements.value is None:
            return self.foundation.fail(requirements.issues)
        if requirements.value:
            issues.append(
                self.foundation.issue(
                    "open_requirements_block_repo_ready",
                    f"{len(requirements.value)} repo dependency requirements are still open.",
                    object_ref=str(repo_root),
                    expected="0 open requirements",
                )
            )
        main = self.node.contract.get_current_contract(Path(repo_root), node_path="Main")
        if not main.ok or main.value is None:
            return self.foundation.fail(main.issues)
        if main.value.version_status != ContractVersionStatus.COMMITTED:
            issues.append(
                self.foundation.issue(
                    "main_scope_not_committed",
                    "Repo ready requires Main scope contract to be committed.",
                    object_ref="Main",
                    current=main.value.version_status.value,
                    expected=ContractVersionStatus.COMMITTED.value,
                )
            )
        reports.append(
            self.foundation.gate_failed("repo_ready_base", issues, summary="Repo ready base checks failed.")
            if issues
            else self.foundation.gate_passed("repo_ready_base", summary="Repo ready base checks passed.")
        )

        source = self.consistency.check_source_corpus_consistency(Path(repo_root))
        if source.ok and source.value is not None:
            reports.append(source.value)
        else:
            reports.append(
                self.foundation.gate_failed(
                    "source_corpus_consistency",
                    source.issues,
                    summary="Source corpus consistency could not be verified.",
                )
            )
        index = self.consistency.check_source_index_consistency(Path(repo_root))
        if index.ok and index.value is not None:
            reports.append(index.value)
        else:
            reports.append(
                self.foundation.gate_failed(
                    "source_index_consistency",
                    index.issues,
                    summary="Source index consistency could not be verified.",
                )
            )
        projection = self.consistency.check_projection_sync(Path(repo_root), scope="repo")
        if not projection.ok or projection.value is None:
            return self.foundation.fail(projection.issues)
        reports.append(projection.value)
        return self.foundation.ok(self.foundation.merge_gate_reports("repo_ready", reports))

    def check_adapter_ready(self, repo_root: Path) -> ServiceResult[GateReport]:
        return self.adapter.check_adapter_ready(Path(repo_root))
