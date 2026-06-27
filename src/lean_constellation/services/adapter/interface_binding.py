"""Adapter root interface binding to finalized flat catalog declarations."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.interface import DeclKind
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.adapter.adapter_decl_catalog import AdapterDeclCatalogComponent, AdapterDeclSummaryView
from lean_constellation.services.foundation import FoundationService, GateReport, ServiceResult, WriteMode
from lean_constellation.services.node.contract import ContractComponent


class InterfaceBindingView(StrictModel):
    interface_name: str
    bound_decl: DeclRef | None = None
    decl_kind: DeclKind | None = None
    binding_summary: str | None = None
    changed: bool = False
    summary: str


class AdapterInterfaceBindingIssue(StrictModel):
    interface_name: str
    issue_code: str
    message: str
    decl_name: str | None = None


class AdapterUnboundInterfaceView(StrictModel):
    interfaces: list[str] = Field(default_factory=list)
    summary: str


class InterfaceBindingComponent:
    """Bind required root Main interfaces to finalized adapter decls."""

    def __init__(
        self,
        foundation: FoundationService | None = None,
        contract: ContractComponent | None = None,
        adapter_decl_catalog: AdapterDeclCatalogComponent | None = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.contract = contract or ContractComponent(self.foundation)
        self.adapter_decl_catalog = adapter_decl_catalog or AdapterDeclCatalogComponent(self.foundation)

    def bind_adapter_interface(
        self,
        repo_root: Path,
        *,
        interface_name: str,
        decl_name: str,
        binding_summary: str,
    ) -> ServiceResult[InterfaceBindingView]:
        if not binding_summary or not binding_summary.strip():
            return self.foundation.fail(
                self.foundation.issue("adapter_binding_summary_required", "Adapter interface binding requires a summary.", field="binding_summary")
            )
        opened = self.contract.ensure_open_contract(repo_root, node_path="Main")
        if not opened.ok or opened.value is None:
            return self.foundation.fail(opened.issues)
        interface = next((item for item in opened.value.contract.interfaces if item.name == interface_name), None)
        if interface is None:
            return self.foundation.fail(
                self.foundation.issue("adapter_interface_missing", f"Adapter interface not found: {interface_name}", object_ref="Main")
            )
        decl = self.adapter_decl_catalog.inspect_adapter_decl(repo_root, name=decl_name)
        if not decl.ok or decl.value is None:
            return self.foundation.fail(decl.issues)
        record = decl.value.record
        if not record.active or not record.finalized:
            return self.foundation.fail(
                self.foundation.issue("adapter_decl_not_finalized", "Adapter interface can only bind to finalized active decls.", object_ref=decl_name)
            )
        if not self._kind_compatible(interface.kind, record.kind):
            return self.foundation.fail(
                self.foundation.issue(
                    "adapter_interface_kind_mismatch",
                    "Adapter decl kind does not satisfy interface kind.",
                    object_ref=interface_name,
                    current=record.kind.value,
                    expected=interface.kind.value,
                )
            )
        new_ref = DeclRef(repo=None, node="Main", name=record.name, revision=1)
        changed = interface.bound_decl != new_ref or interface.note != binding_summary.strip()
        interface.bound_decl = new_ref
        interface.note = binding_summary.strip()
        if changed:
            saved = self.foundation.store.write_json_atomic(
                self._contract_path(repo_root, opened.value.version),
                opened.value.contract,
                mode=WriteMode.UPDATE_EXISTING,
            )
            if not saved.ok:
                return self.foundation.fail(saved.issues)
        return self.foundation.ok(
            InterfaceBindingView(
                interface_name=interface.name,
                bound_decl=interface.bound_decl,
                decl_kind=record.kind,
                binding_summary=interface.note,
                changed=changed,
                summary=("Bound adapter interface." if changed else "Adapter interface binding was already current."),
            )
        )

    def unbind_adapter_interface(self, repo_root: Path, *, interface_name: str, reason: str) -> ServiceResult[InterfaceBindingView]:
        if not reason or not reason.strip():
            return self.foundation.fail(self.foundation.issue("adapter_unbind_reason_required", "Unbind reason is required.", field="reason"))
        opened = self.contract.ensure_open_contract(repo_root, node_path="Main")
        if not opened.ok or opened.value is None:
            return self.foundation.fail(opened.issues)
        interface = next((item for item in opened.value.contract.interfaces if item.name == interface_name), None)
        if interface is None:
            return self.foundation.fail(self.foundation.issue("adapter_interface_missing", f"Adapter interface not found: {interface_name}", object_ref="Main"))
        changed = interface.bound_decl is not None or interface.note != f"Unbound: {reason.strip()}"
        interface.bound_decl = None
        interface.note = f"Unbound: {reason.strip()}"
        if changed:
            saved = self.foundation.store.write_json_atomic(
                self._contract_path(repo_root, opened.value.version),
                opened.value.contract,
                mode=WriteMode.UPDATE_EXISTING,
            )
            if not saved.ok:
                return self.foundation.fail(saved.issues)
        return self.foundation.ok(
            InterfaceBindingView(
                interface_name=interface.name,
                bound_decl=None,
                binding_summary=interface.note,
                changed=changed,
                summary=("Unbound adapter interface." if changed else "Adapter interface was already unbound."),
            )
        )

    def list_unbound_adapter_interfaces(self, repo_root: Path) -> ServiceResult[AdapterUnboundInterfaceView]:
        current = self.contract.get_current_contract(repo_root, node_path="Main")
        if not current.ok or current.value is None:
            return self.foundation.fail(current.issues)
        names = sorted(item.name for item in current.value.contract.interfaces if item.bound_decl is None)
        return self.foundation.ok(
            AdapterUnboundInterfaceView(
                interfaces=names,
                summary=f"Found {len(names)} unbound adapter interfaces.",
            )
        )

    def validate_adapter_interface_bindings(self, repo_root: Path) -> ServiceResult[GateReport]:
        current = self.contract.get_current_contract(repo_root, node_path="Main")
        if not current.ok or current.value is None:
            return self.foundation.fail(current.issues)
        issues = []
        for interface in current.value.contract.interfaces:
            if interface.bound_decl is None:
                issues.append(
                    self.foundation.issue(
                        "adapter_interface_unbound",
                        "Adapter required interface is not bound.",
                        object_ref=interface.name,
                    )
                )
                continue
            if interface.bound_decl.node != "Main" or interface.bound_decl.revision != 1:
                issues.append(
                    self.foundation.issue(
                        "adapter_interface_decl_ref_invalid",
                        "Adapter interface binding must point to Main revision 1.",
                        object_ref=interface.name,
                    )
                )
                continue
            decl = self.adapter_decl_catalog.inspect_adapter_decl(repo_root, name=interface.bound_decl.name)
            if not decl.ok or decl.value is None:
                issues.append(
                    self.foundation.issue(
                        "adapter_interface_target_missing",
                        "Adapter interface binding target is missing.",
                        object_ref=interface.name,
                        current=interface.bound_decl.name,
                    )
                )
                continue
            record = decl.value.record
            if not record.active or not record.finalized:
                issues.append(
                    self.foundation.issue(
                        "adapter_interface_target_not_finalized",
                        "Adapter interface binding target is not finalized.",
                        object_ref=interface.name,
                        current=record.name,
                    )
                )
            if not self._kind_compatible(interface.kind, record.kind):
                issues.append(
                    self.foundation.issue(
                        "adapter_interface_kind_mismatch",
                        "Adapter interface binding target kind is incompatible.",
                        object_ref=interface.name,
                        current=record.kind.value,
                        expected=interface.kind.value,
                    )
                )
        if issues:
            return self.foundation.ok(
                self.foundation.gate_failed(
                    "adapter_interface_bindings",
                    issues,
                    summary=f"{len(issues)} adapter interface binding checks failed.",
                )
            )
        return self.foundation.ok(
            self.foundation.gate_passed(
                "adapter_interface_bindings",
                summary=f"{len(current.value.contract.interfaces)} adapter interfaces are bound.",
            )
        )

    def _contract_path(self, repo_root: Path, version: int) -> Path:
        from lean_constellation.services.foundation import FoundationContext

        return self.foundation.layout.node_contract_path(FoundationContext(repo_root=Path(repo_root)), "Main", version)

    def _kind_compatible(self, required: DeclKind, actual: DeclKind) -> bool:
        if required == actual:
            return True
        theorem_like = {DeclKind.THEOREM, DeclKind.LEMMA}
        return required in theorem_like and actual in theorem_like
