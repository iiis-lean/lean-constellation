"""Adapter root interface binding to finalized flat catalog declarations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.interface import DeclInterface, DeclKind, exact_interface_lean_decl_name
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.adapter.adapter_decl_catalog import AdapterDeclCatalogComponent, AdapterDeclView
from lean_constellation.services.adapter.upstream_navigation import is_compiled_reference_witness
from lean_constellation.services.foundation import GateReport, ServiceResult, WriteMode
from lean_constellation.services.node.contract import ContractComponent

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


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
        runtime: LeanRuntimeServices,
        *,
        contract: ContractComponent | None = None,
        adapter_decl_catalog: AdapterDeclCatalogComponent | None = None,
    ) -> None:
        self.runtime = runtime
        self.contract = contract or self.runtime.require_app_service("node").contract
        self.adapter_decl_catalog = adapter_decl_catalog or AdapterDeclCatalogComponent(runtime)

    def bind_adapter_interface(
        self,
        repo_root: Path,
        *,
        interface_name: str,
        decl_name: str,
        binding_summary: str,
    ) -> ServiceResult[InterfaceBindingView]:
        if not binding_summary or not binding_summary.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("adapter_binding_summary_required", "Adapter interface binding requires a summary.", field="binding_summary")
            )
        opened = self.contract.ensure_open_contract(repo_root, node_path="Main")
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        interface = next((item for item in opened.value.contract.interfaces if item.name == interface_name), None)
        if interface is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("adapter_interface_missing", f"Adapter interface not found: {interface_name}", object_ref="Main")
            )
        decl = self.adapter_decl_catalog.inspect_adapter_decl(repo_root, name=decl_name)
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        view = decl.value
        if not view.finalized:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("adapter_decl_not_finalized", "Adapter interface can only bind to finalized active decls.", object_ref=decl_name)
            )
        if not self._kind_compatible(interface.kind, view.kind):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "adapter_interface_kind_mismatch",
                    "Adapter decl kind does not satisfy interface kind.",
                    object_ref=interface_name,
                    current=view.kind.value,
                    expected=interface.kind.value,
                )
            )
        identity = self._validate_lean_identity(interface, view)
        if not identity.ok:
            return self.runtime.foundation.fail(identity.issues)
        statement = self._validate_statement_contract(repo_root, interface, view)
        if not statement.ok:
            return self.runtime.foundation.fail(statement.issues)
        new_ref = DeclRef(repo=None, node="Main", name=view.name, revision=view.revision.revision)
        changed = interface.bound_decl != new_ref
        interface.bound_decl = new_ref
        if changed:
            saved = self.runtime.foundation.store.write_json_atomic(
                self._contract_path(repo_root, opened.value.version),
                opened.value.contract,
                mode=WriteMode.UPDATE_EXISTING,
            )
            if not saved.ok:
                return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(
            InterfaceBindingView(
                interface_name=interface.name,
                bound_decl=interface.bound_decl,
                decl_kind=view.kind,
                binding_summary=binding_summary.strip(),
                changed=changed,
                summary=("Bound adapter interface." if changed else "Adapter interface binding was already current."),
            )
        )

    def unbind_adapter_interface(self, repo_root: Path, *, interface_name: str, reason: str) -> ServiceResult[InterfaceBindingView]:
        if not reason or not reason.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_unbind_reason_required", "Unbind reason is required.", field="reason"))
        opened = self.contract.ensure_open_contract(repo_root, node_path="Main")
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        interface = next((item for item in opened.value.contract.interfaces if item.name == interface_name), None)
        if interface is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_interface_missing", f"Adapter interface not found: {interface_name}", object_ref="Main"))
        changed = interface.bound_decl is not None
        interface.bound_decl = None
        if changed:
            saved = self.runtime.foundation.store.write_json_atomic(
                self._contract_path(repo_root, opened.value.version),
                opened.value.contract,
                mode=WriteMode.UPDATE_EXISTING,
            )
            if not saved.ok:
                return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(
            InterfaceBindingView(
                interface_name=interface.name,
                bound_decl=None,
                binding_summary=f"Unbound: {reason.strip()}",
                changed=changed,
                summary=("Unbound adapter interface." if changed else "Adapter interface was already unbound."),
            )
        )

    def list_unbound_adapter_interfaces(self, repo_root: Path) -> ServiceResult[AdapterUnboundInterfaceView]:
        current = self.contract.get_current_contract(repo_root, node_path="Main")
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        names = sorted(item.name for item in current.value.contract.interfaces if item.bound_decl is None)
        return self.runtime.foundation.ok(
            AdapterUnboundInterfaceView(
                interfaces=names,
                summary=f"Found {len(names)} unbound adapter interfaces.",
            )
        )

    def validate_adapter_interface_bindings(self, repo_root: Path) -> ServiceResult[GateReport]:
        current = self.contract.get_current_contract(repo_root, node_path="Main")
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        issues = []
        for interface in current.value.contract.interfaces:
            if interface.bound_decl is None:
                issues.append(
                    self.runtime.foundation.issue(
                        "adapter_interface_unbound",
                        "Adapter required interface is not bound.",
                        object_ref=interface.name,
                    )
                )
                continue
            if interface.bound_decl.node != "Main" or interface.bound_decl.revision != 1:
                issues.append(
                    self.runtime.foundation.issue(
                        "adapter_interface_decl_ref_invalid",
                        "Adapter interface binding must point to Main revision 1.",
                        object_ref=interface.name,
                    )
                )
                continue
            decl = self.adapter_decl_catalog.inspect_adapter_decl(repo_root, name=interface.bound_decl.name)
            if not decl.ok or decl.value is None:
                issues.append(
                    self.runtime.foundation.issue(
                        "adapter_interface_target_missing",
                        "Adapter interface binding target is missing.",
                        object_ref=interface.name,
                        current=interface.bound_decl.name,
                    )
                )
                continue
            view = decl.value
            if not view.finalized:
                issues.append(
                    self.runtime.foundation.issue(
                        "adapter_interface_target_not_finalized",
                        "Adapter interface binding target is not finalized.",
                        object_ref=interface.name,
                        current=view.name,
                    )
                )
            if not self._kind_compatible(interface.kind, view.kind):
                issues.append(
                    self.runtime.foundation.issue(
                        "adapter_interface_kind_mismatch",
                        "Adapter interface binding target kind is incompatible.",
                        object_ref=interface.name,
                        current=view.kind.value,
                        expected=interface.kind.value,
                    )
                )
                continue
            identity = self._validate_lean_identity(interface, view)
            if not identity.ok:
                issues.extend(identity.issues)
                continue
            statement = self._validate_statement_contract(repo_root, interface, view)
            if not statement.ok:
                issues.extend(statement.issues)
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "adapter_interface_bindings",
                    issues,
                    summary=f"{len(issues)} adapter interface binding checks failed.",
                )
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "adapter_interface_bindings",
                summary=f"{len(current.value.contract.interfaces)} adapter interfaces are bound.",
            )
        )

    def _contract_path(self, repo_root: Path, version: int) -> Path:
        node = self.runtime.node.node_tree.node_store.resolve_active_node(repo_root, path="Main")
        if node.ok and node.value is not None:
            return self.runtime.node.node_tree.node_store.contract_path(repo_root, node_id=node.value.node_id, version=version)
        raise ValueError("Cannot resolve active adapter root node: Main")

    def _kind_compatible(self, required: DeclKind, actual: DeclKind) -> bool:
        if required == actual:
            return True
        theorem_like = {DeclKind.THEOREM, DeclKind.LEMMA}
        return required in theorem_like and actual in theorem_like

    def _validate_lean_identity(
        self,
        interface: DeclInterface,
        decl: AdapterDeclView,
    ) -> ServiceResult[None]:
        expected = exact_interface_lean_decl_name(interface.name)
        if expected is None:
            return self.runtime.foundation.ok(None)
        actual = decl.lean_decl_name.removeprefix("_root_.")
        if actual != expected:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "adapter_interface_lean_decl_name_mismatch",
                    "Qualified adapter interface name does not match the bound Lean declaration identity.",
                    object_ref=interface.name,
                    current=actual,
                    expected=expected,
                )
            )
        return self.runtime.foundation.ok(None)

    def _validate_statement_contract(
        self,
        repo_root: Path,
        interface: DeclInterface,
        decl: AdapterDeclView,
    ) -> ServiceResult[None]:
        expected = interface.expected_statement_lean_code
        if expected is None:
            return self.runtime.foundation.ok(None)
        if interface.kind not in {DeclKind.THEOREM, DeclKind.LEMMA}:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "adapter_interface_statement_contract_kind_unsupported",
                    "Exact adapter statement contracts currently support theorem-like interfaces.",
                    object_ref=interface.name,
                    current=interface.kind.value,
                    expected="theorem | lemma",
                )
            )
        statement_code = (
            decl.revision.statement.formal.code
            if decl.revision.statement.formal is not None
            else None
        )
        if statement_code is None or not statement_code.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "adapter_interface_statement_contract_actual_missing",
                    "The adapter declaration has no formal statement to compare.",
                    object_ref=interface.name,
                )
            )
        lean_decl_name = decl.revision.lean_decl_name
        if lean_decl_name is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "adapter_interface_lean_decl_name_missing",
                    "The adapter declaration has no registered Lean declaration name.",
                    object_ref=interface.name,
                )
            )
        actual_codes = [("statement", statement_code)]
        proof_code = (
            decl.revision.proof.formal.code
            if decl.revision.proof is not None and decl.revision.proof.formal is not None
            else None
        )
        if proof_code is not None and proof_code.strip():
            actual_codes.append(("proof", proof_code))
        if all(
            is_compiled_reference_witness(actual, lean_decl_name=lean_decl_name)
            for _, actual in actual_codes
        ):
            expected_probe = self.runtime.lean_projection.annotation.build_external_declaration_probe(
                expected,
                lean_decl_name=lean_decl_name,
            )
            if not expected_probe.ok or expected_probe.value is None:
                return self.runtime.foundation.fail(
                    [
                        issue.model_copy(
                            update={
                                "kind": "adapter_interface_statement_contract_mismatch",
                                "object_ref": f"{interface.name}:expected",
                            }
                        )
                        for issue in expected_probe.issues
                    ]
                )
            compared = self.runtime.lean_projection.module_identity.verify_captured_declaration(
                repo_root,
                module=decl.module,
                lean_decl_name=lean_decl_name,
                probe_code=expected_probe.value.code,
                probe_lean_decl_name=expected_probe.value.probe_lean_decl_name,
            )
            if not compared.ok:
                return self.runtime.foundation.fail(
                    [
                        issue.model_copy(
                            update={
                                "kind": "adapter_interface_statement_contract_mismatch",
                                "object_ref": f"{interface.name}:expected",
                            }
                        )
                        for issue in compared.issues
                    ]
                )
            return self.runtime.foundation.ok(None)
        for stage, actual in actual_codes:
            compared = self.runtime.lean_projection.annotation.compare_external_theorem_header(
                expected,
                actual,
                lean_decl_name=lean_decl_name,
            )
            comparison_issues = compared.issues if not compared.ok or compared.value is None else compared.value.issues
            if comparison_issues:
                return self.runtime.foundation.fail(
                    [
                        issue.model_copy(
                            update={
                                "kind": "adapter_interface_statement_contract_mismatch",
                                "object_ref": f"{interface.name}:{stage}",
                            }
                        )
                        for issue in comparison_issues
                    ]
                )
        return self.runtime.foundation.ok(None)
