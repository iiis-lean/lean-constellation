"""NodeContract Mathlib module/declaration use management."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import (
    GateReport,
    IssueSeverity,
    ServiceIssue,
    ServiceResult,
)
from lean_constellation.services.mathlib.mathlib_index import MathlibDeclEntryView, MathlibIndexComponent
from lean_constellation.services.node import ContractComponent, NodeContractView
from lean_constellation.services.node.contract_fields import MathlibUseActor, NodeMathlibDeclUse, NodeMathlibModuleUse
from lean_constellation.services.node.projection_transaction import persist_contract_with_projection

if TYPE_CHECKING:
    from lean_constellation.services.lean_projection import NodeProjectionComponent
    from lean_constellation.services.runtime import LeanRuntimeServices


class NodeMathlibHintView(StrictModel):
    node_path: str
    contract: NodeContractView
    modules: list[NodeMathlibModuleUse]
    declarations: list[NodeMathlibDeclUse]
    validation_gate: GateReport
    summary: str


class NodeMathlibHintMutationView(StrictModel):
    node_path: str
    changed: bool
    changed_items: list[str]
    hints: NodeMathlibHintView
    summary: str


class NodeMathlibUseComponent:
    """Maintain Mathlib module imports and declaration hints in NodeContract."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        contract: ContractComponent | None = None,
        mathlib_index: MathlibIndexComponent | None = None,
        node_projection: "NodeProjectionComponent | None" = None,
    ) -> None:
        self.runtime = runtime
        self.contract = contract or ContractComponent(runtime)
        self.mathlib_index = mathlib_index or MathlibIndexComponent(runtime)
        self.node_projection = node_projection

    def add_mathlib_module_use(
        self,
        repo_root: Path,
        *,
        node_path: str,
        module: str,
        reason: str | None,
        actor: str | MathlibUseActor,
    ) -> ServiceResult[NodeContractView]:
        normalized_actor = self._normalize_actor(actor)
        if not normalized_actor.ok or normalized_actor.value is None:
            return self.runtime.foundation.fail(normalized_actor.issues)
        normalized_module = self._normalize_dotted_name(module, field="module", issue_prefix="mathlib_module")
        if not normalized_module.ok or normalized_module.value is None:
            return self.runtime.foundation.fail(normalized_module.issues)
        index_warnings = self._module_index_warnings(repo_root, normalized_module.value)
        if not index_warnings.ok or index_warnings.value is None:
            return self.runtime.foundation.fail(index_warnings.issues)

        opened = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        current = self._normalize_module_uses(opened.value.contract.mathlib_modules)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)

        warnings = list(index_warnings.value)
        if any(item.module == normalized_module.value for item in current.value):
            refreshed = self._refresh_prelude(repo_root, node_path=node_path)
            if not refreshed.ok:
                return self.runtime.foundation.fail(refreshed.issues)
            view = self.contract.get_current_contract(repo_root, node_path=node_path)
            if not view.ok:
                return self.runtime.foundation.fail(view.issues)
            warnings.append(
                self.runtime.foundation.issue(
                    "mathlib_module_use_duplicate",
                    f"Mathlib module use already exists: {normalized_module.value}",
                    severity=IssueSeverity.WARNING,
                    object_ref=node_path,
                    field="mathlib_modules",
                )
            )
            return self.runtime.foundation.ok(view.value, warnings=warnings)

        current.value.append(
            NodeMathlibModuleUse(
                module=normalized_module.value,
                reason=self._optional_text(reason),
                added_by=normalized_actor.value,
            )
        )
        opened.value.contract.mathlib_modules = list(current.value)
        persisted = self._save_and_refresh_prelude(repo_root, node_path, opened.value.contract)
        if not persisted.ok:
            return self.runtime.foundation.fail(persisted.issues)
        view = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not view.ok:
            return self.runtime.foundation.fail(view.issues)
        return self.runtime.foundation.ok(view.value, warnings=warnings)

    def get_node_mathlib_hint_view(self, repo_root: Path, *, node_path: str) -> ServiceResult[NodeMathlibHintView]:
        current = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        modules = self._normalize_module_uses(current.value.contract.mathlib_modules)
        decls = self._normalize_decl_uses(current.value.contract.mathlib_decls)
        if not modules.ok:
            return self.runtime.foundation.fail(modules.issues)
        if not decls.ok:
            return self.runtime.foundation.fail(decls.issues)
        gate = self.validate_node_mathlib_uses(repo_root, node_path=node_path)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        assert modules.value is not None
        assert decls.value is not None
        return self.runtime.foundation.ok(
            NodeMathlibHintView(
                node_path=node_path,
                contract=current.value,
                modules=modules.value,
                declarations=decls.value,
                validation_gate=gate.value,
                summary=f"Loaded {len(modules.value)} Mathlib module hints and {len(decls.value)} declaration hints for {node_path}.",
            ),
            warnings=current.issues,
        )

    def add_node_mathlib_module_hint(
        self,
        repo_root: Path,
        *,
        node_path: str,
        module: str,
        reason: str | None,
        actor: str | MathlibUseActor,
    ) -> ServiceResult[NodeMathlibHintMutationView]:
        added = self.add_mathlib_module_use(repo_root, node_path=node_path, module=module, reason=reason, actor=actor)
        if not added.ok:
            return self.runtime.foundation.fail(added.issues)
        hints = self.get_node_mathlib_hint_view(repo_root, node_path=node_path)
        if not hints.ok or hints.value is None:
            return self.runtime.foundation.fail(hints.issues)
        changed = not self._has_issue_kind(added.issues, "mathlib_module_use_duplicate")
        changed_items = [module.strip()] if changed else []
        return self.runtime.foundation.ok(
            NodeMathlibHintMutationView(
                node_path=node_path,
                changed=changed,
                changed_items=changed_items,
                hints=hints.value,
                summary=("Added Mathlib module hint." if changed else "Mathlib module hint already existed."),
            ),
            warnings=[*added.issues, *hints.issues],
        )

    def remove_node_mathlib_module_hint(
        self,
        repo_root: Path,
        *,
        node_path: str,
        module: str,
        actor: str | MathlibUseActor,
    ) -> ServiceResult[NodeMathlibHintMutationView]:
        removed = self.remove_mathlib_module_use(repo_root, node_path=node_path, module=module, actor=actor)
        if not removed.ok and self._has_issue_kind(removed.issues, "mathlib_module_use_missing"):
            return self._missing_hint_noop(repo_root, node_path=node_path, target=module, target_kind="module")
        if not removed.ok:
            return self.runtime.foundation.fail(removed.issues)
        hints = self.get_node_mathlib_hint_view(repo_root, node_path=node_path)
        if not hints.ok or hints.value is None:
            return self.runtime.foundation.fail(hints.issues)
        return self.runtime.foundation.ok(
            NodeMathlibHintMutationView(
                node_path=node_path,
                changed=True,
                changed_items=[module.strip()],
                hints=hints.value,
                summary="Removed Mathlib module hint.",
            ),
            warnings=[*removed.issues, *hints.issues],
        )

    def add_node_mathlib_decl_hint(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        reason: str | None,
        actor: str | MathlibUseActor,
    ) -> ServiceResult[NodeMathlibHintMutationView]:
        added = self.add_mathlib_decl_use(repo_root, node_path=node_path, decl_name=decl_name, reason=reason, actor=actor)
        if not added.ok:
            return self.runtime.foundation.fail(added.issues)
        hints = self.get_node_mathlib_hint_view(repo_root, node_path=node_path)
        if not hints.ok or hints.value is None:
            return self.runtime.foundation.fail(hints.issues)
        changed = not self._has_issue_kind(added.issues, "mathlib_decl_use_duplicate")
        changed_items = [decl_name.strip()] if changed else []
        return self.runtime.foundation.ok(
            NodeMathlibHintMutationView(
                node_path=node_path,
                changed=changed,
                changed_items=changed_items,
                hints=hints.value,
                summary=("Added Mathlib declaration hint." if changed else "Mathlib declaration hint already existed."),
            ),
            warnings=[*added.issues, *hints.issues],
        )

    def remove_node_mathlib_decl_hint(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        actor: str | MathlibUseActor,
    ) -> ServiceResult[NodeMathlibHintMutationView]:
        removed = self.remove_mathlib_decl_use(repo_root, node_path=node_path, decl_name=decl_name, actor=actor)
        if not removed.ok and self._has_issue_kind(removed.issues, "mathlib_decl_use_missing"):
            return self._missing_hint_noop(repo_root, node_path=node_path, target=decl_name, target_kind="declaration")
        if not removed.ok:
            return self.runtime.foundation.fail(removed.issues)
        hints = self.get_node_mathlib_hint_view(repo_root, node_path=node_path)
        if not hints.ok or hints.value is None:
            return self.runtime.foundation.fail(hints.issues)
        return self.runtime.foundation.ok(
            NodeMathlibHintMutationView(
                node_path=node_path,
                changed=True,
                changed_items=[decl_name.strip()],
                hints=hints.value,
                summary="Removed Mathlib declaration hint.",
            ),
            warnings=[*removed.issues, *hints.issues],
        )

    def remove_mathlib_module_use(
        self,
        repo_root: Path,
        *,
        node_path: str,
        module: str,
        actor: str | MathlibUseActor,
    ) -> ServiceResult[NodeContractView]:
        normalized_actor = self._normalize_actor(actor)
        if not normalized_actor.ok or normalized_actor.value is None:
            return self.runtime.foundation.fail(normalized_actor.issues)
        normalized_module = self._normalize_dotted_name(module, field="module", issue_prefix="mathlib_module")
        if not normalized_module.ok or normalized_module.value is None:
            return self.runtime.foundation.fail(normalized_module.issues)
        opened = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        current = self._normalize_module_uses(opened.value.contract.mathlib_modules)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        target = next((item for item in current.value if item.module == normalized_module.value), None)
        if target is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_module_use_missing",
                    f"Mathlib module use not found: {normalized_module.value}",
                    object_ref=node_path,
                    field="mathlib_modules",
                )
            )
        permission = self._check_remove_permission(node_path, "mathlib_modules", target.added_by, normalized_actor.value)
        if not permission.ok:
            return self.runtime.foundation.fail(permission.issues)

        opened.value.contract.mathlib_modules = [item for item in current.value if item.module != normalized_module.value]
        persisted = self._save_and_refresh_prelude(repo_root, node_path, opened.value.contract)
        if not persisted.ok:
            return self.runtime.foundation.fail(persisted.issues)
        return self.contract.get_current_contract(repo_root, node_path=node_path)

    def add_mathlib_decl_use(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        reason: str | None,
        actor: str | MathlibUseActor,
    ) -> ServiceResult[NodeContractView]:
        normalized_actor = self._normalize_actor(actor)
        if not normalized_actor.ok or normalized_actor.value is None:
            return self.runtime.foundation.fail(normalized_actor.issues)
        normalized_decl = self._normalize_dotted_name(decl_name, field="decl_name", issue_prefix="mathlib_decl")
        if not normalized_decl.ok or normalized_decl.value is None:
            return self.runtime.foundation.fail(normalized_decl.issues)
        decl_entry = self._decl_entry_or_warning(repo_root, normalized_decl.value)
        if not decl_entry.ok:
            return self.runtime.foundation.fail(decl_entry.issues)

        opened = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        current = self._normalize_decl_uses(opened.value.contract.mathlib_decls)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)

        warnings = list(decl_entry.issues)
        if any(item.name == normalized_decl.value for item in current.value):
            view = self.contract.get_current_contract(repo_root, node_path=node_path)
            if not view.ok:
                return self.runtime.foundation.fail(view.issues)
            warnings.append(
                self.runtime.foundation.issue(
                    "mathlib_decl_use_duplicate",
                    f"Mathlib declaration use already exists: {normalized_decl.value}",
                    severity=IssueSeverity.WARNING,
                    object_ref=node_path,
                    field="mathlib_decls",
                )
            )
            return self.runtime.foundation.ok(view.value, warnings=warnings)

        entry = decl_entry.value
        current.value.append(
            NodeMathlibDeclUse(
                name=normalized_decl.value,
                module=entry.module if entry is not None else None,
                kind=entry.kind if entry is not None else None,
                reason=self._optional_text(reason),
                added_by=normalized_actor.value,
            )
        )
        opened.value.contract.mathlib_decls = list(current.value)
        saved = self._save_contract(repo_root, node_path, opened.value.contract)
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        view = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not view.ok:
            return self.runtime.foundation.fail(view.issues)
        return self.runtime.foundation.ok(view.value, warnings=warnings)

    def remove_mathlib_decl_use(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        actor: str | MathlibUseActor,
    ) -> ServiceResult[NodeContractView]:
        normalized_actor = self._normalize_actor(actor)
        if not normalized_actor.ok or normalized_actor.value is None:
            return self.runtime.foundation.fail(normalized_actor.issues)
        normalized_decl = self._normalize_dotted_name(decl_name, field="decl_name", issue_prefix="mathlib_decl")
        if not normalized_decl.ok or normalized_decl.value is None:
            return self.runtime.foundation.fail(normalized_decl.issues)
        opened = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        current = self._normalize_decl_uses(opened.value.contract.mathlib_decls)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        target = next((item for item in current.value if item.name == normalized_decl.value), None)
        if target is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_decl_use_missing",
                    f"Mathlib declaration use not found: {normalized_decl.value}",
                    object_ref=node_path,
                    field="mathlib_decls",
                )
            )
        permission = self._check_remove_permission(node_path, "mathlib_decls", target.added_by, normalized_actor.value)
        if not permission.ok:
            return self.runtime.foundation.fail(permission.issues)

        opened.value.contract.mathlib_decls = [item for item in current.value if item.name != normalized_decl.value]
        saved = self._save_contract(repo_root, node_path, opened.value.contract)
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        return self.contract.get_current_contract(repo_root, node_path=node_path)

    def validate_node_mathlib_uses(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        current = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        modules = self._normalize_module_uses(current.value.contract.mathlib_modules)
        decls = self._normalize_decl_uses(current.value.contract.mathlib_decls)
        issues: list[ServiceIssue] = []
        warnings: list[ServiceIssue] = []
        if not modules.ok:
            issues.extend(modules.issues)
        if not decls.ok:
            issues.extend(decls.issues)
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed("node_mathlib_uses", issues, summary="Node Mathlib use entries are invalid.")
            )

        assert modules.value is not None
        assert decls.value is not None
        module_names = [item.module for item in modules.value]
        duplicate_modules = sorted({name for name in module_names if module_names.count(name) > 1})
        for module in duplicate_modules:
            issues.append(
                self.runtime.foundation.issue(
                    "mathlib_module_use_duplicate",
                    f"Duplicate Mathlib module use: {module}",
                    object_ref=node_path,
                    field="mathlib_modules",
                )
            )
        decl_names = [item.name for item in decls.value]
        duplicate_decls = sorted({name for name in decl_names if decl_names.count(name) > 1})
        for decl in duplicate_decls:
            issues.append(
                self.runtime.foundation.issue(
                    "mathlib_decl_use_duplicate",
                    f"Duplicate Mathlib declaration use: {decl}",
                    object_ref=node_path,
                    field="mathlib_decls",
                )
            )

        module_set = set(module_names)
        for item in modules.value:
            module_entry = self.mathlib_index.get_mathlib_module_entry(repo_root, module=item.module)
            if not module_entry.ok:
                if self._is_missing_kind(module_entry.issues, "mathlib_module_entry_missing"):
                    warnings.append(
                        self.runtime.foundation.issue(
                            "mathlib_module_not_indexed",
                            f"Mathlib module use is not recorded in MathlibIndex: {item.module}",
                            severity=IssueSeverity.WARNING,
                            object_ref=node_path,
                            field="mathlib_modules",
                        )
                    )
                else:
                    issues.extend(module_entry.issues)

        for item in decls.value:
            decl_entry = self.mathlib_index.get_mathlib_decl_entry(repo_root, name=item.name)
            if not decl_entry.ok or decl_entry.value is None:
                if self._is_missing_kind(decl_entry.issues, "mathlib_decl_entry_missing"):
                    warnings.append(
                        self.runtime.foundation.issue(
                            "mathlib_decl_not_indexed",
                            f"Mathlib declaration use is not recorded in MathlibIndex: {item.name}",
                            severity=IssueSeverity.WARNING,
                            object_ref=node_path,
                            field="mathlib_decls",
                        )
                    )
                    continue
                issues.extend(decl_entry.issues)
                continue
            module = decl_entry.value.module or item.module
            if module and module not in module_set:
                warnings.append(
                    self.runtime.foundation.issue(
                        "mathlib_decl_module_not_imported",
                        f"Mathlib declaration {item.name} is indexed in module {module}, but the node does not import that module.",
                        severity=IssueSeverity.WARNING,
                        object_ref=node_path,
                        field="mathlib_decls",
                        suggested_action=f"Add Mathlib module use {module}.",
                    )
                )

        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed("node_mathlib_uses", issues, summary=f"{len(issues)} Mathlib use checks failed.")
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "node_mathlib_uses",
                summary=f"Checked {len(modules.value)} Mathlib modules and {len(decls.value)} Mathlib declaration hints.",
                warnings=warnings,
            )
        )

    def _normalize_module_uses(self, values: list[NodeMathlibModuleUse]) -> ServiceResult[list[NodeMathlibModuleUse]]:
        adapter = TypeAdapter(NodeMathlibModuleUse)
        normalized: list[NodeMathlibModuleUse] = []
        issues: list[ServiceIssue] = []
        for index, value in enumerate(values):
            try:
                item = adapter.validate_python(value)
            except Exception as exc:  # noqa: BLE001 - validation details are returned to caller.
                issues.append(
                    self.runtime.foundation.issue(
                        "mathlib_module_use_invalid",
                        f"Mathlib module use entry is invalid: {exc}",
                        field=f"mathlib_modules.{index}",
                    )
                )
                continue
            module = self._normalize_dotted_name(item.module, field=f"mathlib_modules.{index}.module", issue_prefix="mathlib_module")
            if not module.ok or module.value is None:
                issues.extend(module.issues)
                continue
            normalized.append(item.model_copy(update={"module": module.value, "reason": self._optional_text(item.reason)}))
        if issues:
            return self.runtime.foundation.fail(issues)
        return self.runtime.foundation.ok(normalized)

    def _normalize_decl_uses(self, values: list[NodeMathlibDeclUse]) -> ServiceResult[list[NodeMathlibDeclUse]]:
        adapter = TypeAdapter(NodeMathlibDeclUse)
        normalized: list[NodeMathlibDeclUse] = []
        issues: list[ServiceIssue] = []
        for index, value in enumerate(values):
            try:
                item = adapter.validate_python(value)
            except Exception as exc:  # noqa: BLE001 - validation details are returned to caller.
                issues.append(
                    self.runtime.foundation.issue(
                        "mathlib_decl_use_invalid",
                        f"Mathlib declaration use entry is invalid: {exc}",
                        field=f"mathlib_decls.{index}",
                    )
                )
                continue
            name = self._normalize_dotted_name(item.name, field=f"mathlib_decls.{index}.name", issue_prefix="mathlib_decl")
            if not name.ok or name.value is None:
                issues.extend(name.issues)
                continue
            module: str | None = None
            if item.module is not None:
                module_result = self._normalize_dotted_name(item.module, field=f"mathlib_decls.{index}.module", issue_prefix="mathlib_module")
                if not module_result.ok:
                    issues.extend(module_result.issues)
                    continue
                module = module_result.value
            normalized.append(
                item.model_copy(
                    update={
                        "name": name.value,
                        "module": module,
                        "kind": self._optional_text(item.kind),
                        "reason": self._optional_text(item.reason),
                    }
                )
            )
        if issues:
            return self.runtime.foundation.fail(issues)
        return self.runtime.foundation.ok(normalized)

    def _module_index_warnings(self, repo_root: Path, module: str) -> ServiceResult[list[ServiceIssue]]:
        entry = self.mathlib_index.get_mathlib_module_entry(repo_root, module=module)
        if entry.ok:
            return self.runtime.foundation.ok([])
        if self._is_missing_kind(entry.issues, "mathlib_module_entry_missing"):
            return self.runtime.foundation.ok(
                [
                    self.runtime.foundation.issue(
                        "mathlib_module_not_indexed",
                        f"Mathlib module use is not recorded in MathlibIndex: {module}",
                        severity=IssueSeverity.WARNING,
                        object_ref=module,
                        field="module",
                    )
                ]
            )
        return self.runtime.foundation.fail(entry.issues)

    def _decl_entry_or_warning(self, repo_root: Path, name: str) -> ServiceResult[MathlibDeclEntryView | None]:
        entry = self.mathlib_index.get_mathlib_decl_entry(repo_root, name=name)
        if entry.ok:
            return self.runtime.foundation.ok(entry.value)
        if self._is_missing_kind(entry.issues, "mathlib_decl_entry_missing"):
            return self.runtime.foundation.ok(
                None,
                warnings=[
                    self.runtime.foundation.issue(
                        "mathlib_decl_not_indexed",
                        f"Mathlib declaration use is not recorded in MathlibIndex: {name}",
                        severity=IssueSeverity.WARNING,
                        object_ref=name,
                        field="decl_name",
                    )
                ],
            )
        return self.runtime.foundation.fail(entry.issues)

    def _check_remove_permission(
        self,
        node_path: str,
        field_name: str,
        target_actor: MathlibUseActor,
        actor: MathlibUseActor,
    ) -> ServiceResult[None]:
        allowed = (
            target_actor == MathlibUseActor.WORKER
            if actor == MathlibUseActor.WORKER
            else target_actor != MathlibUseActor.OPERATOR
            if actor == MathlibUseActor.COORDINATOR
            else target_actor == MathlibUseActor.OPERATOR
        )
        if not allowed:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_use_permission_denied",
                    "The caller cannot remove a Mathlib use owned by another authority.",
                    object_ref=node_path,
                    field=field_name,
                    current=target_actor.value,
                    expected=actor.value,
                )
            )
        return self.runtime.foundation.ok(None)

    def _missing_hint_noop(
        self,
        repo_root: Path,
        *,
        node_path: str,
        target: str,
        target_kind: str,
    ) -> ServiceResult[NodeMathlibHintMutationView]:
        hints = self.get_node_mathlib_hint_view(repo_root, node_path=node_path)
        if not hints.ok or hints.value is None:
            return self.runtime.foundation.fail(hints.issues)
        issue_kind = "mathlib_module_use_missing" if target_kind == "module" else "mathlib_decl_use_missing"
        return self.runtime.foundation.ok(
            NodeMathlibHintMutationView(
                node_path=node_path,
                changed=False,
                changed_items=[],
                hints=hints.value,
                summary=f"Mathlib {target_kind} hint was already absent.",
            ),
            warnings=[
                self.runtime.foundation.issue(
                    issue_kind,
                    f"Mathlib {target_kind} hint was already absent: {target.strip()}",
                    severity=IssueSeverity.WARNING,
                    object_ref=node_path,
                ),
                *hints.issues,
            ],
        )

    def _has_issue_kind(self, issues: list[ServiceIssue], kind: str) -> bool:
        return any(issue.kind == kind for issue in issues)

    def _normalize_actor(self, actor: str | MathlibUseActor) -> ServiceResult[MathlibUseActor]:
        try:
            return self.runtime.foundation.ok(MathlibUseActor(actor))
        except ValueError:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_use_actor_invalid",
                    "actor must be coordinator, worker, or operator.",
                    field="actor",
                    current=str(actor),
                )
            )

    def _normalize_dotted_name(self, value: str, *, field: str, issue_prefix: str) -> ServiceResult[str]:
        normalized = value.strip() if isinstance(value, str) else ""
        if not normalized:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(f"{issue_prefix}_name_empty", "Lean dotted name is required.", field=field))
        if not self._is_safe_dotted_name(normalized):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    f"{issue_prefix}_name_invalid",
                    f"Invalid Lean dotted name: {value}",
                    field=field,
                    expected="a non-empty dotted Lean name without whitespace or path separators",
                )
            )
        return self.runtime.foundation.ok(normalized)

    def _save_contract(self, repo_root: Path, node_path: str, contract: object) -> ServiceResult[object]:
        return self.contract._persist_open_candidate(repo_root, node_path=node_path, candidate=contract)

    def _save_and_refresh_prelude(self, repo_root: Path, node_path: str, contract: object) -> ServiceResult[object]:
        return persist_contract_with_projection(
            self.runtime,
            repo_root=repo_root,
            node_path=node_path,
            candidate=contract,
            projection_kind="prelude",
            save=self._save_contract,
            refresh=lambda: self._refresh_prelude(repo_root, node_path=node_path),
        )

    def _refresh_prelude(self, repo_root: Path, *, node_path: str) -> ServiceResult[object]:
        node_projection = self.node_projection
        if node_projection is None:
            lean_projection = self.runtime.app.lean_projection
            if lean_projection is None:
                return self.runtime.foundation.ok(None)
            node_projection = lean_projection.node_projection
        return node_projection.refresh_prelude(repo_root, node_path=node_path)

    def _optional_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None

    def _is_safe_dotted_name(self, value: str) -> bool:
        if not value or any(ch.isspace() for ch in value):
            return False
        if "/" in value or "\\" in value or ".." in value:
            return False
        parts = value.split(".")
        return all(bool(part) and part not in {".", ".."} for part in parts)

    def _is_missing_kind(self, issues: list[ServiceIssue], kind: str) -> bool:
        return bool(issues) and all(issue.kind == kind for issue in issues)
