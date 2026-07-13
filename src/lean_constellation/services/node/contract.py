"""Node contract lifecycle and admission gates."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.interface import DeclInterface
from lean_constellation.domain.preparation import RepoPreparationInput
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.foundation import (
    FoundationContext,
    GateReport,
    OpenVersionResult,
    ServiceResult,
    WriteMode,
)
from lean_constellation.services.node.node_tree import (
    NodeContract,
    NodeContractStatus,
    NodeKind,
    NodeLifecycle,
    NodeMetadata,
    NodeTreeComponent,
)

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


ContractVersionStatus = NodeContractStatus


class ScopeNodeContract(NodeContract):
    contract_kind: Literal[NodeKind.SCOPE] = NodeKind.SCOPE
    exports: list[DeclRef] = Field(default_factory=list)


class ContentNodeContract(NodeContract):
    contract_kind: Literal[NodeKind.CONTENT] = NodeKind.CONTENT


class NodeContractSummaryView(StrictModel):
    node_path: str
    node_id: str
    version: int
    status: NodeContractStatus
    is_open: bool
    is_active: bool
    created_at: str
    committed_at: str | None = None
    summary: str


class NodeContractView(StrictModel):
    node_path: str
    node_id: str
    node_kind: NodeKind
    version: int
    status: NodeContractStatus
    version_status: ContractVersionStatus
    is_open: bool
    active_contract_version: int | None = None
    open_contract_version: int | None = None
    contract: NodeContract
    summary: str


class OpenContractView(StrictModel):
    node_path: str
    node_id: str
    node_kind: NodeKind
    version: int
    created_new_open: bool
    path: str
    contract: NodeContract
    summary: str


class ContractComponent:
    """Maintain versioned Scope / Content node contracts."""

    _ROOT_BOOTSTRAP_GOAL = "Organize and complete the full repository formalization goal."

    def __init__(self, runtime: LeanRuntimeServices, node_tree: NodeTreeComponent | None = None) -> None:
        self.runtime = runtime
        self.node_tree = node_tree or NodeTreeComponent(runtime)

    def get_current_contract(self, repo_root: Path, *, node_path: str) -> ServiceResult[NodeContractView]:
        return self.get_edit_contract(repo_root, node_path=node_path)

    def get_edit_contract(self, repo_root: Path, *, node_path: str) -> ServiceResult[NodeContractView]:
        node = self._load_active_node(repo_root, node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        contract = self._select_edit_contract(repo_root, node.value)
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        return self.runtime.foundation.ok(self._contract_view(node.value, contract.value))

    def get_open_contract(self, repo_root: Path, *, node_path: str) -> ServiceResult[NodeContractView]:
        node = self._load_active_node(repo_root, node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        contract = self._load_open_contract(repo_root, node.value)
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        return self.runtime.foundation.ok(self._contract_view(node.value, contract.value))

    def get_committed_contract(self, repo_root: Path, *, node_path: str) -> ServiceResult[NodeContractView]:
        return self.get_visible_contract(repo_root, node_path=node_path)

    def get_visible_contract(self, repo_root: Path, *, node_path: str) -> ServiceResult[NodeContractView]:
        node = self._load_active_node(repo_root, node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        contract = self._load_active_contract(repo_root, node.value)
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        return self.runtime.foundation.ok(self._contract_view(node.value, contract.value))

    def list_contract_versions(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[NodeContractSummaryView]]:
        node = self._load_active_node(repo_root, node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        contracts = self._list_contracts_for_node(repo_root, node.value)
        if not contracts.ok or contracts.value is None:
            return self.runtime.foundation.fail(contracts.issues)
        return self.runtime.foundation.ok(
            [
                NodeContractSummaryView(
                    node_path=node.value.path,
                    node_id=node.value.node_id,
                    version=contract.version,
                    status=contract.status,
                    is_open=contract.status == NodeContractStatus.OPEN,
                    is_active=node.value.active_contract_version == contract.version,
                    created_at=contract.created_at,
                    committed_at=contract.committed_at,
                    summary=f"{node.value.path} contract v{contract.version} is {contract.status.value}.",
                )
                for contract in contracts.value
            ]
        )

    def ensure_open_contract(self, repo_root: Path, *, node_path: str) -> ServiceResult[OpenContractView]:
        node = self._load_active_node(repo_root, node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        latest = self._select_edit_contract(repo_root, node.value)
        if not latest.ok or latest.value is None:
            return self.runtime.foundation.fail(latest.issues)
        ensured = self.runtime.foundation.store.ensure_open_version(
            load_latest=lambda: latest.value,
            copy_committed=self._copy_committed_contract,
            path_for_version=lambda version: self._contract_file(repo_root, node.value.path, version),
        )
        if not ensured.ok or ensured.value is None:
            return self.runtime.foundation.fail(ensured.issues)
        if ensured.value.created_new_open:
            node.value.current_contract_version = ensured.value.version
            node.value.open_contract_version = ensured.value.version
            saved = self.node_tree.node_store.save_node(repo_root, node.value, mode=WriteMode.UPDATE_EXISTING)
            if not saved.ok:
                return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self._open_view(node.value, ensured.value))

    def ensure_scope_contract(self, repo_root: Path, *, scope_path: str) -> ServiceResult[OpenContractView]:
        node = self._load_active_node(repo_root, scope_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        if node.value.kind != NodeKind.SCOPE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("node_not_scope", "ensure_scope_contract requires a Scope node.", object_ref=scope_path)
            )
        opened = self.ensure_open_contract(repo_root, node_path=scope_path)
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        if opened.value.contract.exports is None:
            opened.value.contract.exports = []
            saved = self.runtime.foundation.store.write_json_atomic(
                self._contract_file(repo_root, scope_path, opened.value.version),
                opened.value.contract,
                mode=WriteMode.UPDATE_EXISTING,
            )
            if not saved.ok:
                return self.runtime.foundation.fail(saved.issues)
        return opened

    def _persist_open_candidate(
        self,
        repo_root: Path,
        *,
        node_path: str,
        candidate: NodeContract,
    ) -> ServiceResult[NodeContractView]:
        """Persist a preflighted in-memory candidate, opening a version atomically if needed."""
        node = self._load_active_node(repo_root, node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        current = self._select_edit_contract(repo_root, node.value)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        if candidate.version != current.value.version or candidate.contract_kind != node.value.kind:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "contract_candidate_stale",
                    "Contract mutation candidate no longer matches current truth.",
                    object_ref=node_path,
                )
            )
        persisted = deepcopy(candidate)
        created_open = current.value.status == NodeContractStatus.COMMITTED
        if created_open:
            persisted.version = current.value.version + 1
            persisted.status = NodeContractStatus.OPEN
            persisted.summary = None
            persisted.committed_at = None
            persisted.created_at = utc_now_iso()
        elif current.value.status != NodeContractStatus.OPEN:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("contract_not_open", "Contract candidate is not based on an editable contract.", object_ref=node_path)
            )
        with self.runtime.foundation.store.mutation("persist_open_contract_candidate") as mutation:
            mutation.stage_json(
                self._contract_file_for_node(repo_root, node.value, persisted.version),
                persisted,
                mode=WriteMode.CREATE_ONLY if created_open else WriteMode.UPDATE_EXISTING,
            )
            if created_open:
                node.value.current_contract_version = persisted.version
                node.value.open_contract_version = persisted.version
                mutation.stage_json(
                    self.node_tree.node_store.node_file(repo_root, node_id=node.value.node_id),
                    node.value,
                    mode=WriteMode.UPDATE_EXISTING,
                )
            committed = mutation.commit()
        if not committed.ok:
            return self.runtime.foundation.fail(committed.issues)
        return self.runtime.foundation.ok(self._contract_view(node.value, persisted))

    def initialize_main_contract_from_preparation_input(
        self,
        repo_root: Path,
        *,
        boundary: str,
        objective: str,
    ) -> ServiceResult[NodeContractView]:
        if not boundary or not boundary.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("contract_boundary_required", "Main boundary is required.", field="boundary"))
        if not objective or not objective.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("contract_objective_required", "Main objective is required.", field="objective"))
        input_result = self._load_preparation_input(repo_root)
        if not input_result.ok or input_result.value is None:
            return self.runtime.foundation.fail(input_result.issues)
        ensured_root = self.node_tree.ensure_root_scope_node(repo_root)
        if not ensured_root.ok:
            return self.runtime.foundation.fail(ensured_root.issues)
        opened = self.ensure_scope_contract(repo_root, scope_path="Main")
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        contract = opened.value.contract
        prep = input_result.value
        if contract.goal not in {self._ROOT_BOOTSTRAP_GOAL, prep.goal}:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "main_goal_conflict",
                    "Existing Main contract goal conflicts with preparation input.",
                    object_ref="Main",
                    current=contract.goal,
                    expected=prep.goal,
                )
            )
        if contract.interfaces and self._interface_dump(contract.interfaces) != self._interface_dump(prep.interface_inputs):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "main_interfaces_conflict",
                    "Existing Main interfaces conflict with preparation input interfaces.",
                    object_ref="Main",
                )
            )
        contract.goal = prep.goal
        contract.boundary = boundary.strip()
        contract.objective = objective.strip()
        contract.interfaces = list(prep.interface_inputs)
        if contract.success_criteria is None:
            contract.success_criteria = "The Main scope exposes the required repository interfaces and can be handed to the repository Coordinator."
        saved = self.runtime.foundation.store.write_json_atomic(
            self._contract_file(repo_root, "Main", contract.version),
            contract,
            mode=WriteMode.UPDATE_EXISTING,
        )
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        node = self._load_active_node(repo_root, "Main")
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        return self.runtime.foundation.ok(self._contract_view(node.value, contract))

    def update_contract_text_fields(
        self,
        repo_root: Path,
        *,
        node_path: str,
        goal: str | None = None,
        boundary: str | None = None,
        objective: str | None = None,
        success_criteria: str | None = None,
        constraints: str | None = None,
    ) -> ServiceResult[NodeContractView]:
        opened = self.ensure_open_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        contract = opened.value.contract
        if node_path == "Main" and goal is not None and goal.strip() != contract.goal:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "main_goal_protected",
                    "Main contract goal is protected after preparation input initialization.",
                    object_ref="Main",
                    field="goal",
                )
            )
        changed = False
        for field_name, value in {
            "goal": goal,
            "boundary": boundary,
            "objective": objective,
            "success_criteria": success_criteria,
            "constraints": constraints,
        }.items():
            if value is None:
                continue
            stripped = value.strip()
            if field_name in {"goal", "boundary", "objective", "success_criteria"} and not stripped:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(f"contract_{field_name}_required", f"Contract {field_name} cannot be empty.", field=field_name)
                )
            if getattr(contract, field_name) != stripped:
                setattr(contract, field_name, stripped)
                changed = True
        if changed:
            saved = self.runtime.foundation.store.write_json_atomic(
                self._contract_file(repo_root, node_path, contract.version),
                contract,
                mode=WriteMode.UPDATE_EXISTING,
            )
            if not saved.ok:
                return self.runtime.foundation.fail(saved.issues)
        node = self._load_active_node(repo_root, node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        return self.runtime.foundation.ok(self._contract_view(node.value, contract))

    def _commit_content_contract_with_head(
        self,
        repo_root: Path,
        *,
        node_path: str,
        summary: str,
        decl_graph_head: dict[str, int],
    ) -> ServiceResult[NodeContractView]:
        """System-only primitive; callers must capture and validate the exact head."""
        summary_issue = self._validate_summary(summary)
        if not summary_issue.ok:
            return self.runtime.foundation.fail(summary_issue.issues)
        node = self._load_active_node(repo_root, node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        if node.value.kind != NodeKind.CONTENT:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_not_content", "Content contract commit requires a Content node.", object_ref=node_path))
        contract = self._load_current_contract(repo_root, node.value)
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        if contract.value.status != ContractVersionStatus.OPEN:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("contract_not_open", "Only an open contract can be committed.", object_ref=node_path)
            )
        contract.value.decl_graph_head = dict(sorted(decl_graph_head.items()))
        return self._commit_contract(repo_root, node.value, contract.value, summary=summary)

    def adopt_committed_content_contract_head(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_graph_head: dict[str, int],
        summary: str,
    ) -> ServiceResult[NodeContractView]:
        """Create a new committed, head-bound version for legacy adoption.

        This system-only migration primitive never rewrites the historical
        committed contract.  Lifecycle locking, checkpointing, and rollback
        are owned by the release-adoption orchestrator.
        """
        summary_issue = self._validate_summary(summary)
        if not summary_issue.ok:
            return self.runtime.foundation.fail(summary_issue.issues)
        node = self._load_active_node(repo_root, node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        if node.value.kind != NodeKind.CONTENT:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "node_not_content",
                "Legacy contract-head adoption requires a Content node.",
                object_ref=node_path,
            ))
        if node.value.open_contract_version is not None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "legacy_adoption_open_truth",
                "Legacy contract-head adoption cannot run with an open contract.",
                object_ref=node_path,
            ))
        current = self._load_active_contract(repo_root, node.value)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        if current.value.status != ContractVersionStatus.COMMITTED:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "legacy_adoption_open_truth",
                "Legacy contract-head adoption requires a committed active contract.",
                object_ref=node_path,
            ))
        expected_head = dict(sorted(decl_graph_head.items()))
        if current.value.decl_graph_head == expected_head:
            return self.runtime.foundation.ok(self._contract_view(node.value, current.value))
        if current.value.decl_graph_head:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "legacy_adoption_contract_head_conflict",
                "A non-empty legacy contract head differs from current DeclGraph truth.",
                object_ref=node_path,
            ))
        adopted = self._copy_committed_contract(current.value)
        adopted.status = ContractVersionStatus.COMMITTED
        adopted.summary = summary.strip()
        adopted.committed_at = utc_now_iso()
        adopted.decl_graph_head = expected_head
        migrated_node = node.value.model_copy(deep=True)
        migrated_node.current_contract_version = adopted.version
        migrated_node.active_contract_version = adopted.version
        migrated_node.open_contract_version = None
        with self.runtime.foundation.store.mutation("adopt_committed_content_contract_head") as mutation:
            mutation.stage_json(
                self._contract_file_for_node(repo_root, migrated_node, adopted.version),
                adopted,
                mode=WriteMode.CREATE_ONLY,
            )
            mutation.stage_json(
                self.node_tree.node_store.node_file(repo_root, node_id=migrated_node.node_id),
                migrated_node,
                mode=WriteMode.UPDATE_EXISTING,
            )
            committed = mutation.commit()
        if not committed.ok:
            return self.runtime.foundation.fail(committed.issues)
        return self.runtime.foundation.ok(self._contract_view(migrated_node, adopted))

    def record_content_contract_summary(self, repo_root: Path, *, node_path: str, summary: str) -> ServiceResult[NodeContractView]:
        """Write a task summary to the current open Content contract without committing it."""

        summary_issue = self._validate_summary(summary)
        if not summary_issue.ok:
            return self.runtime.foundation.fail(summary_issue.issues)
        node = self._load_active_node(repo_root, node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        if node.value.kind != NodeKind.CONTENT:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("node_not_content", "Content contract summary recording requires a Content node.", object_ref=node_path)
            )
        contract = self._load_current_contract(repo_root, node.value)
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        if contract.value.status != ContractVersionStatus.OPEN:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("contract_not_open", "Only an open contract can record a task summary.", object_ref=node_path)
            )
        contract.value.summary = summary.strip()
        saved = self.runtime.foundation.store.write_json_atomic(
            self._contract_file_for_node(repo_root, node.value, contract.value.version),
            contract.value,
            mode=WriteMode.UPDATE_EXISTING,
        )
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self._contract_view(node.value, contract.value))

    def _commit_scope_contract_after_guard(self, repo_root: Path, *, scope_path: str, summary: str) -> ServiceResult[NodeContractView]:
        """System-only primitive after NodeService release guard validation."""
        gate = self._check_scope_commit(repo_root, scope_path=scope_path, summary=summary)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        if not gate.value.passed:
            return self.runtime.foundation.fail(gate.value.issues)
        node = self._load_active_node(repo_root, scope_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        contract = self._load_current_contract(repo_root, node.value)
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        return self._commit_contract(repo_root, node.value, contract.value, summary=summary)

    def check_scope_contract_commit(self, repo_root: Path, *, scope_path: str, summary: str) -> ServiceResult[GateReport]:
        return self._check_scope_commit(repo_root, scope_path=scope_path, summary=summary)

    def check_content_task_admission(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        node = self._load_active_node(repo_root, node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        issues = []
        warnings = []
        if node.value.kind != NodeKind.CONTENT:
            issues.append(self.runtime.foundation.issue("node_not_content", "Content task admission requires a Content node.", object_ref=node_path))
        contract = self._load_current_contract(repo_root, node.value)
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        self._collect_contract_required_field_issues(contract.value, issues)
        if contract.value.status != ContractVersionStatus.OPEN:
            issues.append(
                self.runtime.foundation.issue(
                    "contract_not_open",
                    "Content node task admission requires an open contract.",
                    object_ref=node_path,
                    current=contract.value.status.value,
                    expected=ContractVersionStatus.OPEN.value,
                )
            )
        self._collect_dep_issues(repo_root, contract.value, issues)
        warnings.append(
            self.runtime.foundation.issue(
                "content_admission_deferred_checks",
                "Material ref validation, dependency readiness, and prelude projection sync are deferred until their components are implemented.",
                severity="warning",
                object_ref=node_path,
            )
        )
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "content_task_admission",
                    issues,
                    summary=f"{len(issues)} content task admission checks failed.",
                )
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "content_task_admission",
                summary="Content task admission base checks passed.",
                warnings=warnings,
            )
        )

    def _check_scope_commit(self, repo_root: Path, *, scope_path: str, summary: str) -> ServiceResult[GateReport]:
        summary_issue = self._validate_summary(summary)
        if not summary_issue.ok:
            return self.runtime.foundation.ok(self.runtime.foundation.gate_failed("scope_commit", summary_issue.issues, summary="Scope commit summary is invalid."))
        node = self._load_active_node(repo_root, scope_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        issues = []
        warnings = []
        if node.value.kind != NodeKind.SCOPE:
            issues.append(self.runtime.foundation.issue("node_not_scope", "Scope commit requires a Scope node.", object_ref=scope_path))
        contract = self._load_current_contract(repo_root, node.value)
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        if contract.value.status != ContractVersionStatus.OPEN:
            issues.append(
                self.runtime.foundation.issue(
                    "contract_not_open",
                    "Only an open Scope contract can be committed.",
                    object_ref=scope_path,
                    current=contract.value.status.value,
                    expected=ContractVersionStatus.OPEN.value,
                )
            )
        export_keys = {self._decl_ref_key(ref) for ref in contract.value.exports}
        for interface in contract.value.interfaces:
            if interface.bound_decl is None:
                issues.append(
                    self.runtime.foundation.issue(
                        "interface_unbound",
                        f"Scope interface is not bound: {interface.name}",
                        object_ref=scope_path,
                        field=f"interfaces.{interface.name}.bound_decl",
                    )
                )
                continue
            if self._decl_ref_key(interface.bound_decl) not in export_keys:
                issues.append(
                    self.runtime.foundation.issue(
                        "interface_binding_not_exported",
                        f"Scope interface binding is not present in exports: {interface.name}",
                        object_ref=scope_path,
                        field=f"interfaces.{interface.name}.bound_decl",
                    )
                )
        warnings.append(
            self.runtime.foundation.issue(
                "scope_commit_deferred_checks",
                "Export descendant validation, Decl revision readiness, generated Interfaces.lean sync, and dependency closure checks are deferred until their components are implemented.",
                severity="warning",
                object_ref=scope_path,
            )
        )
        if issues:
            return self.runtime.foundation.ok(self.runtime.foundation.gate_failed("scope_commit", issues, summary=f"{len(issues)} scope commit checks failed."))
        return self.runtime.foundation.ok(self.runtime.foundation.gate_passed("scope_commit", summary="Scope commit base checks passed.", warnings=warnings))

    def _commit_contract(
        self,
        repo_root: Path,
        node: NodeMetadata,
        contract: NodeContract,
        *,
        summary: str,
    ) -> ServiceResult[NodeContractView]:
        contract.summary = summary.strip()
        contract.status = ContractVersionStatus.COMMITTED
        contract.committed_at = utc_now_iso()
        node.current_contract_version = contract.version
        node.active_contract_version = contract.version
        if node.open_contract_version == contract.version:
            node.open_contract_version = None
        with self.runtime.foundation.store.mutation("commit_node_contract") as mutation:
            mutation.stage_json(
                self._contract_file_for_node(repo_root, node, contract.version),
                contract,
                mode=WriteMode.UPDATE_EXISTING,
            )
            mutation.stage_json(
                self.node_tree.node_store.node_file(repo_root, node_id=node.node_id),
                node,
                mode=WriteMode.UPDATE_EXISTING,
            )
            committed = mutation.commit()
        if not committed.ok:
            return self.runtime.foundation.fail(committed.issues)
        return self.runtime.foundation.ok(self._contract_view(node, contract))

    def _validate_summary(self, summary: str) -> ServiceResult[None]:
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("contract_summary_required", "Contract summary is required.", field="summary"))
        return self.runtime.foundation.ok(None)

    def _copy_committed_contract(self, latest: NodeContract) -> NodeContract:
        new_contract = deepcopy(latest)
        new_contract.version = latest.version + 1
        new_contract.status = ContractVersionStatus.OPEN
        new_contract.summary = None
        new_contract.committed_at = None
        new_contract.created_at = utc_now_iso()
        return new_contract

    def _collect_contract_required_field_issues(self, contract: NodeContract, issues: list[object]) -> None:
        for field_name in ["goal", "boundary", "objective", "success_criteria"]:
            value = getattr(contract, field_name)
            if not isinstance(value, str) or not value.strip():
                issues.append(
                    self.runtime.foundation.issue(
                        f"contract_{field_name}_missing",
                        f"Contract field is required for content admission: {field_name}",
                        field=field_name,
                    )
                )

    def _collect_dep_issues(self, repo_root: Path, contract: NodeContract, issues: list[object]) -> None:
        for index, dep in enumerate(contract.deps):
            repo = dep.target.repo
            node_path = dep.target.node
            if repo is None and (not isinstance(node_path, str) or not node_path.strip()):
                issues.append(
                    self.runtime.foundation.issue(
                        "contract_dep_target_invalid",
                        "Local contract dependency target must include a node path.",
                        field=f"deps.{index}.target.node",
                    )
                )
                continue
            if repo is None and isinstance(node_path, str):
                target_node = self._load_active_node(repo_root, node_path)
                if not target_node.ok or target_node.value is None:
                    issues.append(
                        self.runtime.foundation.issue(
                            "contract_dep_node_missing",
                            f"Contract dependency target node is missing or inactive: {node_path}",
                            field=f"deps.{index}.target.node",
                            object_ref=contract.contract_kind.value,
                        )
                    )

    def _load_preparation_input(self, repo_root: Path) -> ServiceResult[RepoPreparationInput]:
        path = self.runtime.foundation.layout.preparation_input_path(FoundationContext(repo_root=Path(repo_root)))
        loaded = self.runtime.foundation.store.read_json(path, RepoPreparationInput)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("preparation_input_missing", "Preparation input is missing or invalid.", object_ref=str(path))
            )
        return loaded

    def _select_edit_contract(self, repo_root: Path, node: NodeMetadata) -> ServiceResult[NodeContract]:
        contracts = self._list_contracts_for_node(repo_root, node)
        if not contracts.ok or contracts.value is None:
            return self.runtime.foundation.fail(contracts.issues)
        if not contracts.value:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_contract_missing", "Node has no contract versions.", object_ref=node.path))
        if node.open_contract_version is not None:
            for contract in contracts.value:
                if contract.version == node.open_contract_version:
                    return self.runtime.foundation.ok(contract)
        open_contracts = [contract for contract in contracts.value if contract.status == ContractVersionStatus.OPEN]
        if open_contracts:
            return self.runtime.foundation.ok(max(open_contracts, key=lambda item: item.version))
        if node.active_contract_version is not None:
            for contract in contracts.value:
                if contract.version == node.active_contract_version:
                    return self.runtime.foundation.ok(contract)
        if node.current_contract_version is not None:
            for contract in contracts.value:
                if contract.version == node.current_contract_version:
                    return self.runtime.foundation.ok(contract)
        return self.runtime.foundation.ok(max(contracts.value, key=lambda item: item.version))

    def _load_open_contract(self, repo_root: Path, node: NodeMetadata) -> ServiceResult[NodeContract]:
        if node.open_contract_version is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("node_open_contract_missing", "Node has no open contract version.", object_ref=node.path)
            )
        loaded = self.runtime.foundation.store.read_json(
            self._contract_file_for_node(repo_root, node, node.open_contract_version),
            NodeContract,
        )
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        if loaded.value.status != ContractVersionStatus.OPEN:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "node_open_contract_invalid",
                    "Node open_contract_version does not point to an open contract.",
                    object_ref=node.path,
                    current=loaded.value.status.value,
                    expected=ContractVersionStatus.OPEN.value,
                )
            )
        return loaded

    def _load_active_contract(self, repo_root: Path, node: NodeMetadata) -> ServiceResult[NodeContract]:
        if node.active_contract_version is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("node_committed_contract_missing", "Node has no active committed contract version.", object_ref=node.path)
            )
        loaded = self.runtime.foundation.store.read_json(
            self._contract_file_for_node(repo_root, node, node.active_contract_version),
            NodeContract,
        )
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        if loaded.value.status != ContractVersionStatus.COMMITTED:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "node_committed_contract_invalid",
                    "Node active_contract_version does not point to a committed contract.",
                    object_ref=node.path,
                    current=loaded.value.status.value,
                    expected=ContractVersionStatus.COMMITTED.value,
                )
            )
        return loaded

    def _list_contracts_for_node(self, repo_root: Path, node: NodeMetadata) -> ServiceResult[list[NodeContract]]:
        contracts_dir = self._contract_file_for_node(repo_root, node, 1).parent
        if not contracts_dir.exists():
            return self.runtime.foundation.ok([])
        contracts: list[NodeContract] = []
        issues = []
        for path in sorted(contracts_dir.glob("*.json")):
            loaded = self.runtime.foundation.store.read_json(path, NodeContract)
            if loaded.ok and loaded.value is not None:
                contracts.append(loaded.value)
            else:
                issues.extend(loaded.issues)
        if issues:
            return self.runtime.foundation.fail(issues)
        contracts.sort(key=lambda item: item.version)
        return self.runtime.foundation.ok(contracts)

    def _load_current_contract(self, repo_root: Path, node: NodeMetadata) -> ServiceResult[NodeContract]:
        return self._select_edit_contract(repo_root, node)

    def _load_active_node(self, repo_root: Path, node_path: str) -> ServiceResult[NodeMetadata]:
        loaded = self.node_tree.node_store.resolve_active_node(repo_root, path=node_path)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        if loaded.value.lifecycle != NodeLifecycle.ACTIVE:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_not_active", "Node is not active.", object_ref=node_path))
        return loaded

    def _node_file(self, repo_root: Path, node_path: str) -> Path:
        node = self.node_tree.node_store.resolve_active_node(repo_root, path=node_path)
        if not node.ok or node.value is None:
            raise ValueError(f"Cannot resolve active node path: {node_path}")
        return self.node_tree.node_store.node_file(repo_root, node_id=node.value.node_id)

    def _contract_file(self, repo_root: Path, node_path: str, version: int) -> Path:
        node = self.node_tree.node_store.resolve_active_node(repo_root, path=node_path)
        if not node.ok or node.value is None:
            raise ValueError(f"Cannot resolve active node path: {node_path}")
        return self._contract_file_for_node(repo_root, node.value, version)

    def _contract_file_for_node(self, repo_root: Path, node: NodeMetadata, version: int) -> Path:
        return self.node_tree.node_store.contract_path(repo_root, node_id=node.node_id, version=version)

    def _contract_view(self, node: NodeMetadata, contract: NodeContract) -> NodeContractView:
        return NodeContractView(
            node_path=node.path,
            node_id=node.node_id,
            node_kind=node.kind,
            version=contract.version,
            status=contract.status,
            version_status=ContractVersionStatus(contract.status),
            is_open=contract.status == ContractVersionStatus.OPEN,
            active_contract_version=node.active_contract_version,
            open_contract_version=node.open_contract_version,
            contract=contract,
            summary=f"{node.path} contract v{contract.version} is {contract.status.value}.",
        )

    def _open_view(self, node: NodeMetadata, result: OpenVersionResult[object]) -> OpenContractView:
        contract = result.value
        if not isinstance(contract, NodeContract):
            raise TypeError("open contract value must be NodeContract")
        return OpenContractView(
            node_path=node.path,
            node_id=node.node_id,
            node_kind=node.kind,
            version=result.version,
            created_new_open=result.created_new_open,
            path=result.path,
            contract=contract,
            summary=("Created a new open contract version." if result.created_new_open else "Reused existing open contract version."),
        )

    def _interface_dump(self, interfaces: list[DeclInterface]) -> list[dict[str, object]]:
        return [interface.model_dump(mode="json") for interface in interfaces]

    def _decl_ref_key(self, ref: DeclRef) -> tuple[str | None, str, str, int]:
        return (ref.repo, ref.node, ref.name, ref.revision)
