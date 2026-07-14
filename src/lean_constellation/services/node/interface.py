"""Node contract interface management."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput
from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import ProofAvailability
from lean_constellation.services.foundation import FoundationContext, GateReport, ServiceResult
from lean_constellation.services.node.contract import ContractComponent, NodeContractView
from lean_constellation.services.node.export import DeclPublicView, ExportComponent
from lean_constellation.services.node.node_tree import NodeContract, NodeKind

if TYPE_CHECKING:
    from lean_constellation.services.lean_projection.node_projection import NodeProjectionComponent
    from lean_constellation.services.runtime import LeanRuntimeServices


class InterfaceActor(StrEnum):
    COORDINATOR = "coordinator"
    WORKER = "worker"
    REVIEWER = "reviewer"
    SYSTEM = "system"


class InterfaceView(StrictModel):
    name: str
    kind: DeclKind
    summary: str
    protected: bool = False
    expected_statement_lean_code: str | None = None
    bound_decl: DeclRef | None = None
    note: str | None = None


class InterfaceListView(StrictModel):
    node_path: str
    interfaces: list[InterfaceView] = Field(default_factory=list)
    protected_names: list[str] = Field(default_factory=list)
    summary: str


class InterfaceBindingView(StrictModel):
    node_path: str
    interface_name: str
    bound_decl: DeclRef | None = None
    changed: bool
    summary: str


class RootInterfaceReadyGateView(StrictModel):
    summary: str
    protected_interface_count: int
    total_interface_count: int
    supplement_interface_count: int
    gate: GateReport


class InterfaceComponent:
    """Maintain DeclInterface entries embedded in the current NodeContract."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        contract: ContractComponent | None = None,
        export: ExportComponent | None = None,
        node_projection: "NodeProjectionComponent | None" = None,
    ) -> None:
        self.runtime = runtime
        self.contract = contract or ContractComponent(runtime)
        self.export = export or ExportComponent(runtime, contract=self.contract)
        self.node_projection = node_projection

    def add_interface(
        self,
        repo_root: Path,
        *,
        node_path: str,
        name: str,
        kind: str | DeclKind,
        summary: str,
        statement_hint: str | None = None,
        actor: str | InterfaceActor,
    ) -> ServiceResult[NodeContractView]:
        normalized = self._build_interface(name=name, kind=kind, summary=summary, statement_hint=statement_hint)
        if not normalized.ok or normalized.value is None:
            return self.runtime.foundation.fail(normalized.issues)
        protected = self._protected_names(repo_root, node_path)
        if not protected.ok or protected.value is None:
            return self.runtime.foundation.fail(protected.issues)
        if normalized.value.name in protected.value:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "protected_interface_requires_sync",
                    "Protected root interfaces must be restored from preparation input sync, not added manually.",
                    object_ref=node_path,
                    field=normalized.value.name,
                )
            )
        opened = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        existing_names = [interface.name for interface in opened.value.contract.interfaces]
        if normalized.value.name in existing_names:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("interface_duplicate", f"Interface already exists: {normalized.value.name}", object_ref=node_path)
            )
        opened.value.contract.interfaces.append(normalized.value)
        saved = self._save_contract(repo_root, node_path, opened.value.contract)
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        return self.contract.get_current_contract(repo_root, node_path=node_path)

    def update_interface(
        self,
        repo_root: Path,
        *,
        node_path: str,
        name: str,
        summary: str | None = None,
        statement_hint: str | None = None,
        actor: str | InterfaceActor,
    ) -> ServiceResult[NodeContractView]:
        if summary is None and statement_hint is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "interface_update_field_required",
                    "Interface update requires at least one field to change.",
                    object_ref=node_path,
                    field=name,
                )
            )
        protected = self._protected_names(repo_root, node_path)
        if not protected.ok or protected.value is None:
            return self.runtime.foundation.fail(protected.issues)
        if name in protected.value:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("protected_interface_update_forbidden", "Protected root interface cannot be modified.", object_ref=node_path, field=name)
            )
        opened = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        target = self._find_interface(opened.value.contract.interfaces, name)
        if target is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("interface_missing", f"Interface not found: {name}", object_ref=node_path))
        if summary is not None:
            if not summary.strip():
                return self.runtime.foundation.fail(self.runtime.foundation.issue("interface_summary_required", "Interface summary cannot be empty.", field="summary"))
            target.summary = summary.strip()
        if statement_hint is not None:
            target.note = statement_hint.strip() or None
        saved = self._save_contract(repo_root, node_path, opened.value.contract)
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        return self.contract.get_current_contract(repo_root, node_path=node_path)

    def remove_interface(
        self,
        repo_root: Path,
        *,
        node_path: str,
        name: str,
        actor: str | InterfaceActor,
    ) -> ServiceResult[NodeContractView]:
        protected = self._protected_names(repo_root, node_path)
        if not protected.ok or protected.value is None:
            return self.runtime.foundation.fail(protected.issues)
        if name in protected.value:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("protected_interface_remove_forbidden", "Protected root interface cannot be removed.", object_ref=node_path, field=name)
            )
        opened = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        target = self._find_interface(opened.value.contract.interfaces, name)
        if target is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("interface_missing", f"Interface not found: {name}", object_ref=node_path))
        if target.bound_decl is not None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("interface_bound", "Bound interface must be unbound before removal.", object_ref=node_path, field=name)
            )
        opened.value.contract.interfaces = [interface for interface in opened.value.contract.interfaces if interface.name != name]
        saved = self._save_contract(repo_root, node_path, opened.value.contract)
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        return self.contract.get_current_contract(repo_root, node_path=node_path)

    def bind_interface_to_decl(
        self,
        repo_root: Path,
        *,
        node_path: str,
        interface_name: str,
        decl_name: str,
        decl_node: str | None = None,
    ) -> ServiceResult[InterfaceBindingView]:
        opened = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        interface = self._find_interface(opened.value.contract.interfaces, interface_name)
        if interface is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("interface_missing", f"Interface not found: {interface_name}", object_ref=node_path))
        resolved = self._resolve_binding_decl(
            repo_root,
            node_path=node_path,
            node_kind=opened.value.node_kind,
            interface=interface,
            decl_name=decl_name,
            decl_node=decl_node,
        )
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        changed = interface.bound_decl != resolved.value
        interface.bound_decl = resolved.value
        warnings = list(resolved.issues)
        if changed:
            saved = self._save_contract(repo_root, node_path, opened.value.contract)
            if not saved.ok:
                return self.runtime.foundation.fail(saved.issues)
            refreshed = self._refresh_interfaces(repo_root, node_path)
            if not refreshed.ok:
                return self.runtime.foundation.fail(refreshed.issues)
            warnings.extend(refreshed.issues)
        return self.runtime.foundation.ok(
            InterfaceBindingView(
                node_path=node_path,
                interface_name=interface.name,
                bound_decl=interface.bound_decl,
                changed=changed,
                summary=("Bound interface to declaration." if changed else "Interface was already bound to the requested declaration."),
            ),
            warnings=warnings,
        )

    def unbind_interface(
        self,
        repo_root: Path,
        *,
        node_path: str,
        interface_name: str,
    ) -> ServiceResult[InterfaceBindingView]:
        opened = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        interface = self._find_interface(opened.value.contract.interfaces, interface_name)
        if interface is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("interface_missing", f"Interface not found: {interface_name}", object_ref=node_path))
        changed = interface.bound_decl is not None
        interface.bound_decl = None
        warnings = []
        if changed:
            saved = self._save_contract(repo_root, node_path, opened.value.contract)
            if not saved.ok:
                return self.runtime.foundation.fail(saved.issues)
            refreshed = self._refresh_interfaces(repo_root, node_path)
            if not refreshed.ok:
                return self.runtime.foundation.fail(refreshed.issues)
            warnings.extend(refreshed.issues)
        return self.runtime.foundation.ok(
            InterfaceBindingView(
                node_path=node_path,
                interface_name=interface.name,
                bound_decl=None,
                changed=changed,
                summary=("Unbound interface." if changed else "Interface was already unbound."),
            ),
            warnings=warnings,
        )

    def list_interfaces(self, repo_root: Path, *, node_path: str) -> ServiceResult[InterfaceListView]:
        view = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not view.ok or view.value is None:
            return self.runtime.foundation.fail(view.issues)
        protected = self._protected_names(repo_root, node_path)
        if not protected.ok or protected.value is None:
            return self.runtime.foundation.fail(protected.issues)
        interfaces = [
            InterfaceView(
                name=interface.name,
                kind=interface.kind,
                summary=interface.summary,
                protected=interface.name in protected.value,
                expected_statement_lean_code=interface.expected_statement_lean_code,
                bound_decl=interface.bound_decl,
                note=interface.note,
            )
            for interface in sorted(view.value.contract.interfaces, key=lambda item: item.name)
        ]
        return self.runtime.foundation.ok(
            InterfaceListView(
                node_path=node_path,
                interfaces=interfaces,
                protected_names=sorted(protected.value),
                summary=f"Loaded {len(interfaces)} interfaces for {node_path}.",
            )
        )

    def sync_protected_root_interfaces_from_preparation_input(
        self,
        repo_root: Path,
        *,
        node_path: str = "Main",
    ) -> ServiceResult[NodeContractView]:
        prep = self._load_preparation_input(repo_root)
        if not prep.ok or prep.value is None:
            return self.runtime.foundation.fail(prep.issues)
        opened = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        by_name = {interface.name: interface for interface in opened.value.contract.interfaces}
        for protected in prep.value.interface_inputs:
            existing = by_name.get(protected.name)
            if existing is None:
                opened.value.contract.interfaces.append(protected)
                by_name[protected.name] = protected
            elif self._interface_requirement_dump(existing) != self._interface_requirement_dump(protected):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "protected_interface_conflict",
                        f"Protected interface conflicts with preparation input: {protected.name}",
                        object_ref=node_path,
                        field=protected.name,
                    )
                )
        saved = self._save_contract(repo_root, node_path, opened.value.contract)
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        return self.contract.get_current_contract(repo_root, node_path=node_path)

    def check_protected_root_interfaces(self, repo_root: Path, *, node_path: str = "Main") -> ServiceResult[GateReport]:
        prep = self._load_preparation_input(repo_root)
        if not prep.ok or prep.value is None:
            return self.runtime.foundation.fail(prep.issues)
        view = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not view.ok or view.value is None:
            return self.runtime.foundation.fail(view.issues)
        issues = []
        names = [interface.name for interface in view.value.contract.interfaces]
        duplicate_names = sorted({name for name in names if names.count(name) > 1})
        for duplicate in duplicate_names:
            issues.append(self.runtime.foundation.issue("interface_duplicate", f"Duplicate root interface name: {duplicate}", object_ref=node_path, field=duplicate))
        current = {interface.name: interface for interface in view.value.contract.interfaces}
        for protected in prep.value.interface_inputs:
            existing = current.get(protected.name)
            if existing is None:
                issues.append(
                    self.runtime.foundation.issue("protected_interface_missing", f"Protected interface is missing: {protected.name}", object_ref=node_path, field=protected.name)
                )
            elif self._interface_requirement_dump(existing) != self._interface_requirement_dump(protected):
                issues.append(
                    self.runtime.foundation.issue(
                        "protected_interface_modified",
                        f"Protected interface was modified: {protected.name}",
                        object_ref=node_path,
                        field=protected.name,
                    )
                )
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed("protected_root_interfaces", issues, summary=f"{len(issues)} protected root interface checks failed.")
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed("protected_root_interfaces", summary="Protected root interfaces are intact.")
        )

    def check_root_interfaces_include_preparation_inputs(
        self,
        repo_root: Path,
        *,
        node_path: str = "Main",
    ) -> ServiceResult[GateReport]:
        return self.check_protected_root_interfaces(repo_root, node_path=node_path)

    def check_root_interface_statement_contracts(
        self,
        repo_root: Path,
        *,
        node_path: str = "Main",
    ) -> ServiceResult[GateReport]:
        current = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        issues = []
        checked = 0
        for interface in current.value.contract.interfaces:
            if interface.expected_statement_lean_code is None:
                continue
            checked += 1
            if interface.bound_decl is None:
                issues.append(
                    self.runtime.foundation.issue(
                        "interface_statement_contract_unbound",
                        "An exact statement interface must be bound before its statement can be checked.",
                        object_ref=node_path,
                        field=interface.name,
                    )
                )
                continue
            statement = self._validate_binding_statement_contract(
                repo_root,
                node_path=node_path,
                interface=interface,
                ref=interface.bound_decl,
            )
            if not statement.ok:
                issues.extend(statement.issues)
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "root_interface_statement_contracts",
                    issues,
                    summary=f"{len(issues)} exact interface statement checks failed.",
                )
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "root_interface_statement_contracts",
                summary=f"{checked} exact interface statement contracts are satisfied.",
            )
        )

    def submit_root_interface_prepare_ready(
        self,
        repo_root: Path,
        *,
        summary: str,
    ) -> ServiceResult[RootInterfaceReadyGateView]:
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("root_interface_ready_summary_required", "Root interface ready summary is required.", field="summary"))
        gate = self.check_protected_root_interfaces(repo_root, node_path="Main")
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        if not gate.value.passed:
            return self.runtime.foundation.fail(gate.value.issues)
        listed = self.list_interfaces(repo_root, node_path="Main")
        if not listed.ok or listed.value is None:
            return self.runtime.foundation.fail(listed.issues)
        supplement_missing_summary = [
            interface.name
            for interface in listed.value.interfaces
            if not interface.protected and not interface.summary.strip()
        ]
        if supplement_missing_summary:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "supplement_interface_summary_missing",
                    "Supplement interfaces must have non-empty summaries.",
                    object_ref="Main",
                    current=", ".join(supplement_missing_summary),
                )
            )
        protected_count = len(listed.value.protected_names)
        total_count = len(listed.value.interfaces)
        return self.runtime.foundation.ok(
            RootInterfaceReadyGateView(
                summary=summary.strip(),
                protected_interface_count=protected_count,
                total_interface_count=total_count,
                supplement_interface_count=total_count - protected_count,
                gate=gate.value,
            )
        )

    def _build_interface(
        self,
        *,
        name: str,
        kind: str | DeclKind,
        summary: str,
        statement_hint: str | None,
    ) -> ServiceResult[DeclInterface]:
        if not name or not name.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("interface_name_required", "Interface name is required.", field="name"))
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("interface_summary_required", "Interface summary is required.", field="summary"))
        try:
            decl_kind = DeclKind(kind)
        except ValueError:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("interface_kind_invalid", f"Invalid interface kind: {kind}", field="kind")
            )
        return self.runtime.foundation.ok(
            DeclInterface(
                name=name.strip(),
                kind=decl_kind,
                summary=summary.strip(),
                note=statement_hint.strip() if statement_hint and statement_hint.strip() else None,
            )
        )

    def _resolve_binding_decl(
        self,
        repo_root: Path,
        *,
        node_path: str,
        node_kind: NodeKind,
        interface: DeclInterface,
        decl_name: str,
        decl_node: str | None,
    ) -> ServiceResult[DeclRef]:
        if not decl_name or not decl_name.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("decl_name_required", "Decl name is required.", field="decl_name"))
        if node_kind == NodeKind.CONTENT:
            resolved = self._resolve_content_binding(repo_root, node_path=node_path, interface=interface, decl_name=decl_name.strip(), decl_node=decl_node)
        elif node_kind == NodeKind.SCOPE:
            resolved = self._resolve_scope_binding(repo_root, scope_path=node_path, interface=interface, decl_name=decl_name.strip(), decl_node=decl_node)
        else:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "interface_binding_node_kind_unsupported",
                    "Interface binding only supports Scope and Content nodes.",
                    object_ref=node_path,
                    current=str(node_kind),
                )
            )
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        statement = self._validate_binding_statement_contract(
            repo_root,
            node_path=node_path,
            interface=interface,
            ref=resolved.value,
        )
        if not statement.ok:
            return self.runtime.foundation.fail(statement.issues)
        return self.runtime.foundation.ok(resolved.value, warnings=resolved.issues)

    def _resolve_content_binding(
        self,
        repo_root: Path,
        *,
        node_path: str,
        interface: DeclInterface,
        decl_name: str,
        decl_node: str | None,
    ) -> ServiceResult[DeclRef]:
        target_node = decl_node.strip() if decl_node and decl_node.strip() else node_path
        if target_node != node_path:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "interface_binding_decl_outside_content",
                    "Content interface binding target must belong to the current Content node.",
                    object_ref=node_path,
                    current=target_node,
                    expected=node_path,
                )
            )
        public = self.export.list_content_public_decls(repo_root, node_path=node_path)
        if not public.ok or public.value is None:
            return self.runtime.foundation.fail(public.issues)
        matches = [decl for decl in public.value if decl.ref.name == decl_name and decl.ref.node == node_path and decl.public]
        if not matches:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "interface_binding_decl_not_public",
                    f"Declaration is not public on current Content node: {decl_name}",
                    object_ref=node_path,
                )
            )
        if len(matches) > 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "interface_binding_decl_ambiguous",
                    f"Multiple public declarations match binding target: {decl_name}",
                    object_ref=node_path,
                )
            )
        return self._validate_binding_candidate(node_path=node_path, interface=interface, decl=matches[0])

    def _resolve_scope_binding(
        self,
        repo_root: Path,
        *,
        scope_path: str,
        interface: DeclInterface,
        decl_name: str,
        decl_node: str | None,
    ) -> ServiceResult[DeclRef]:
        current = self.contract.get_current_contract(repo_root, node_path=scope_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        target_node = decl_node.strip() if decl_node and decl_node.strip() else None
        export_matches = [
            ref
            for ref in current.value.contract.exports
            if ref.name == decl_name and (target_node is None or ref.node == target_node)
        ]
        if not export_matches:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "interface_binding_decl_not_exported",
                    f"Scope interface binding target is not in current Scope exports: {decl_name}",
                    object_ref=scope_path,
                    expected="A DeclRef already present in Scope exports.",
                )
            )
        if len(export_matches) > 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "interface_binding_decl_ambiguous",
                    "Multiple Scope exports match binding target; pass decl_node explicitly.",
                    object_ref=scope_path,
                    current=", ".join(sorted(ref.node for ref in export_matches)),
                )
            )
        candidate_result = self.export._find_visible_candidate(repo_root, scope_path=scope_path, ref=export_matches[0])
        if not candidate_result.ok or candidate_result.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "interface_binding_export_candidate_missing",
                    f"Scope export has no valid public candidate: {decl_name}",
                    object_ref=scope_path,
                    details={"issues": "; ".join(issue.kind for issue in candidate_result.issues)},
                )
            )
        candidate = candidate_result.value
        if not candidate.ready or candidate.stale:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "interface_binding_decl_not_ready",
                    f"Scope export is not ready for interface binding: {decl_name}",
                    object_ref=scope_path,
                    current=f"ready={candidate.ready}, stale={candidate.stale}",
                    expected="ready=True, stale=False",
                )
            )
        kind_check = self._validate_binding_kind(node_path=scope_path, interface=interface, decl_kind=candidate.kind)
        if not kind_check.ok:
            return self.runtime.foundation.fail(kind_check.issues)
        return self.runtime.foundation.ok(export_matches[0], warnings=kind_check.issues)

    def _validate_binding_candidate(self, *, node_path: str, interface: DeclInterface, decl: DeclPublicView) -> ServiceResult[DeclRef]:
        if not decl.ready or decl.stale:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "interface_binding_decl_not_ready",
                    f"Declaration is not ready for interface binding: {decl.ref.name}",
                    object_ref=node_path,
                    current=f"ready={decl.ready}, stale={decl.stale}",
                    expected="ready=True, stale=False",
                )
            )
        kind_check = self._validate_binding_kind(node_path=node_path, interface=interface, decl_kind=decl.kind)
        if not kind_check.ok:
            return self.runtime.foundation.fail(kind_check.issues)
        return self.runtime.foundation.ok(decl.ref, warnings=kind_check.issues)

    def _validate_binding_kind(
        self,
        *,
        node_path: str,
        interface: DeclInterface,
        decl_kind: str | None,
    ) -> ServiceResult[None]:
        if decl_kind is None:
            return self.runtime.foundation.ok(
                None,
                warnings=[
                    self.runtime.foundation.issue(
                        "interface_binding_kind_check_deferred",
                        "Declaration kind is not available from the current public boundary provider.",
                        object_ref=node_path,
                        severity="warning",
                    )
                ],
            )
        try:
            actual = DeclKind(decl_kind)
        except ValueError:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "interface_binding_kind_invalid",
                    f"Declaration kind is invalid: {decl_kind}",
                    object_ref=node_path,
                    current=decl_kind,
                    expected=", ".join(kind.value for kind in DeclKind),
                )
            )
        if actual != interface.kind:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "interface_binding_kind_mismatch",
                    f"Interface kind does not match declaration kind: {interface.name}",
                    object_ref=node_path,
                    current=actual.value,
                    expected=interface.kind.value,
                )
            )
        return self.runtime.foundation.ok(None)

    def _validate_binding_statement_contract(
        self,
        repo_root: Path,
        *,
        node_path: str,
        interface: DeclInterface,
        ref: DeclRef,
    ) -> ServiceResult[None]:
        expected = interface.expected_statement_lean_code
        if expected is None:
            return self.runtime.foundation.ok(None)
        if interface.kind not in {DeclKind.THEOREM, DeclKind.LEMMA}:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "interface_statement_contract_kind_unsupported",
                    "Exact statement contracts currently support theorem-like interfaces.",
                    object_ref=node_path,
                    current=interface.kind.value,
                    expected="theorem | lemma",
                )
            )
        target_root = Path(repo_root)
        if ref.repo is not None:
            target_root = target_root.parent / self.runtime.foundation.layout.ensure_safe_key(ref.repo)
        resolved = self.runtime.decl_graph.ref_compatibility.resolve_decl_ref(
            repo_root,
            ref=ref,
            required_availability=ProofAvailability.DECLARED,
        )
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        if not resolved.value.compatible or resolved.value.resolved_revision is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "interface_statement_contract_decl_ref_incompatible",
                    "The bound declaration anchor is not compatible with the current public target.",
                    object_ref=f"{ref.node}:{ref.name}@{ref.revision}",
                    current=resolved.value.reason,
                )
            )
        revision = self.runtime.decl_graph.get_decl_revision(
            target_root,
            node_path=ref.node,
            name=ref.name,
            revision=resolved.value.resolved_revision,
        )
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "interface_statement_contract_decl_revision_missing",
                    "The bound declaration revision required by the exact statement contract is unavailable.",
                    object_ref=f"{ref.node}:{ref.name}@{ref.revision}",
                )
            )
        statement_code = revision.value.statement_lean_code
        if statement_code is None or not statement_code.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "interface_statement_contract_actual_missing",
                    "The bound declaration has no captured formal statement to compare.",
                    object_ref=f"{ref.node}:{ref.name}@{ref.revision}",
                )
            )
        actual_codes = [("statement", statement_code)]
        if revision.value.proof_lean_code is not None and revision.value.proof_lean_code.strip():
            actual_codes.append(("proof", revision.value.proof_lean_code))
        for stage, actual in actual_codes:
            compared = self.runtime.lean_projection.annotation.compare_theorem_header(
                expected,
                actual,
                decl_name=ref.name,
            )
            comparison_issues = compared.issues if not compared.ok or compared.value is None else compared.value.issues
            if comparison_issues:
                return self.runtime.foundation.fail(
                    [
                        issue.model_copy(
                            update={
                                "kind": "interface_statement_contract_mismatch",
                                "object_ref": f"{ref.node}:{ref.name}@{ref.revision}:{stage}",
                            }
                        )
                        for issue in comparison_issues
                    ]
                )
        return self.runtime.foundation.ok(None)

    def _refresh_interfaces(self, repo_root: Path, node_path: str) -> ServiceResult[object]:
        projection = self.node_projection
        if projection is None:
            lean_projection = self.runtime.app.lean_projection
            if lean_projection is None:
                return self.runtime.foundation.ok(None)
            projection = lean_projection.node_projection
        return projection.refresh_interfaces(repo_root, node_path=node_path)

    def _protected_names(self, repo_root: Path, node_path: str) -> ServiceResult[set[str]]:
        if node_path != "Main":
            return self.runtime.foundation.ok(set())
        prep = self._load_preparation_input(repo_root)
        if not prep.ok or prep.value is None:
            return self.runtime.foundation.fail(prep.issues)
        return self.runtime.foundation.ok({interface.name for interface in prep.value.interface_inputs})

    def _load_preparation_input(self, repo_root: Path) -> ServiceResult[RepoPreparationInput]:
        path = self.runtime.foundation.layout.preparation_input_path(FoundationContext(repo_root=Path(repo_root)))
        loaded = self.runtime.foundation.store.read_json(path, RepoPreparationInput)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("preparation_input_missing", "Preparation input is missing or invalid.", object_ref=str(path))
            )
        return loaded

    def _save_contract(self, repo_root: Path, node_path: str, contract: object) -> ServiceResult[object]:
        node = self.runtime.node.node_tree.node_store.resolve_active_node(repo_root, path=node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        if node.value.kind == NodeKind.SCOPE and isinstance(contract, NodeContract):
            guarded = self.runtime.node.release_guard.check_scope_contract_candidate(
                repo_root, scope_path=node_path, candidate=contract
            )
            if not guarded.ok:
                return self.runtime.foundation.fail(guarded.issues)
        if not isinstance(contract, NodeContract):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("contract_candidate_invalid", "Node contract candidate has an invalid type.", object_ref=node_path)
            )
        return self.contract._persist_open_candidate(repo_root, node_path=node_path, candidate=contract)

    @staticmethod
    def _find_interface(interfaces: list[DeclInterface], name: str) -> DeclInterface | None:
        for interface in interfaces:
            if interface.name == name:
                return interface
        return None

    @staticmethod
    def _interface_requirement_dump(interface: DeclInterface) -> dict[str, object]:
        dumped = interface.model_dump(mode="json")
        dumped.pop("bound_decl", None)
        return dumped
