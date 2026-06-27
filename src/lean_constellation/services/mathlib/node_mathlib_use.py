"""NodeContract Mathlib module/declaration use management."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import (
    FoundationContext,
    FoundationService,
    GateReport,
    IssueSeverity,
    ServiceIssue,
    ServiceResult,
    WriteMode,
)
from lean_constellation.services.lean_projection import NodeProjectionComponent
from lean_constellation.services.mathlib.mathlib_index import MathlibDeclEntryView, MathlibIndexComponent
from lean_constellation.services.node import ContractComponent, NodeContractView


class MathlibUseActor(StrEnum):
    COORDINATOR = "coordinator"
    WORKER = "worker"


class NodeMathlibModuleUse(StrictModel):
    module: str
    reason: str | None = None
    added_by: MathlibUseActor = MathlibUseActor.COORDINATOR


class NodeMathlibDeclUse(StrictModel):
    name: str
    module: str | None = None
    kind: str | None = None
    reason: str | None = None
    added_by: MathlibUseActor = MathlibUseActor.COORDINATOR


class NodeMathlibUseComponent:
    """Maintain Mathlib module imports and declaration hints in NodeContract."""

    def __init__(
        self,
        foundation: FoundationService | None = None,
        contract: ContractComponent | None = None,
        mathlib_index: MathlibIndexComponent | None = None,
        node_projection: NodeProjectionComponent | None = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.contract = contract or ContractComponent(self.foundation)
        self.mathlib_index = mathlib_index or MathlibIndexComponent(self.foundation)
        self.node_projection = node_projection or NodeProjectionComponent(self.foundation, self.contract)

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
            return self.foundation.fail(normalized_actor.issues)
        normalized_module = self._normalize_dotted_name(module, field="module", issue_prefix="mathlib_module")
        if not normalized_module.ok or normalized_module.value is None:
            return self.foundation.fail(normalized_module.issues)
        index_warnings = self._module_index_warnings(repo_root, normalized_module.value)
        if not index_warnings.ok or index_warnings.value is None:
            return self.foundation.fail(index_warnings.issues)

        opened = self.contract.ensure_open_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.foundation.fail(opened.issues)
        current = self._normalize_module_uses(opened.value.contract.mathlib_modules)
        if not current.ok or current.value is None:
            return self.foundation.fail(current.issues)

        warnings = list(index_warnings.value)
        if any(item.module == normalized_module.value for item in current.value):
            refreshed = self.node_projection.refresh_prelude(repo_root, node_path=node_path)
            if not refreshed.ok:
                return self.foundation.fail(refreshed.issues)
            view = self.contract.get_current_contract(repo_root, node_path=node_path)
            if not view.ok:
                return self.foundation.fail(view.issues)
            warnings.append(
                self.foundation.issue(
                    "mathlib_module_use_duplicate",
                    f"Mathlib module use already exists: {normalized_module.value}",
                    severity=IssueSeverity.WARNING,
                    object_ref=node_path,
                    field="mathlib_modules",
                )
            )
            return self.foundation.ok(view.value, warnings=warnings)

        current.value.append(
            NodeMathlibModuleUse(
                module=normalized_module.value,
                reason=self._optional_text(reason),
                added_by=normalized_actor.value,
            )
        )
        opened.value.contract.mathlib_modules = [item.model_dump(mode="json") for item in current.value]
        saved = self._save_contract(repo_root, node_path, opened.value.contract)
        if not saved.ok:
            return self.foundation.fail(saved.issues)
        refreshed = self.node_projection.refresh_prelude(repo_root, node_path=node_path)
        if not refreshed.ok:
            return self.foundation.fail(refreshed.issues)
        view = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not view.ok:
            return self.foundation.fail(view.issues)
        return self.foundation.ok(view.value, warnings=warnings)

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
            return self.foundation.fail(normalized_actor.issues)
        normalized_module = self._normalize_dotted_name(module, field="module", issue_prefix="mathlib_module")
        if not normalized_module.ok or normalized_module.value is None:
            return self.foundation.fail(normalized_module.issues)
        opened = self.contract.ensure_open_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.foundation.fail(opened.issues)
        current = self._normalize_module_uses(opened.value.contract.mathlib_modules)
        if not current.ok or current.value is None:
            return self.foundation.fail(current.issues)
        target = next((item for item in current.value if item.module == normalized_module.value), None)
        if target is None:
            return self.foundation.fail(
                self.foundation.issue(
                    "mathlib_module_use_missing",
                    f"Mathlib module use not found: {normalized_module.value}",
                    object_ref=node_path,
                    field="mathlib_modules",
                )
            )
        permission = self._check_remove_permission(node_path, "mathlib_modules", target.added_by, normalized_actor.value)
        if not permission.ok:
            return self.foundation.fail(permission.issues)

        opened.value.contract.mathlib_modules = [
            item.model_dump(mode="json") for item in current.value if item.module != normalized_module.value
        ]
        saved = self._save_contract(repo_root, node_path, opened.value.contract)
        if not saved.ok:
            return self.foundation.fail(saved.issues)
        refreshed = self.node_projection.refresh_prelude(repo_root, node_path=node_path)
        if not refreshed.ok:
            return self.foundation.fail(refreshed.issues)
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
            return self.foundation.fail(normalized_actor.issues)
        normalized_decl = self._normalize_dotted_name(decl_name, field="decl_name", issue_prefix="mathlib_decl")
        if not normalized_decl.ok or normalized_decl.value is None:
            return self.foundation.fail(normalized_decl.issues)
        decl_entry = self._decl_entry_or_warning(repo_root, normalized_decl.value)
        if not decl_entry.ok:
            return self.foundation.fail(decl_entry.issues)

        opened = self.contract.ensure_open_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.foundation.fail(opened.issues)
        current = self._normalize_decl_uses(opened.value.contract.mathlib_decls)
        if not current.ok or current.value is None:
            return self.foundation.fail(current.issues)

        warnings = list(decl_entry.issues)
        if any(item.name == normalized_decl.value for item in current.value):
            view = self.contract.get_current_contract(repo_root, node_path=node_path)
            if not view.ok:
                return self.foundation.fail(view.issues)
            warnings.append(
                self.foundation.issue(
                    "mathlib_decl_use_duplicate",
                    f"Mathlib declaration use already exists: {normalized_decl.value}",
                    severity=IssueSeverity.WARNING,
                    object_ref=node_path,
                    field="mathlib_decls",
                )
            )
            return self.foundation.ok(view.value, warnings=warnings)

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
        opened.value.contract.mathlib_decls = [item.model_dump(mode="json") for item in current.value]
        saved = self._save_contract(repo_root, node_path, opened.value.contract)
        if not saved.ok:
            return self.foundation.fail(saved.issues)
        view = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not view.ok:
            return self.foundation.fail(view.issues)
        return self.foundation.ok(view.value, warnings=warnings)

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
            return self.foundation.fail(normalized_actor.issues)
        normalized_decl = self._normalize_dotted_name(decl_name, field="decl_name", issue_prefix="mathlib_decl")
        if not normalized_decl.ok or normalized_decl.value is None:
            return self.foundation.fail(normalized_decl.issues)
        opened = self.contract.ensure_open_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.foundation.fail(opened.issues)
        current = self._normalize_decl_uses(opened.value.contract.mathlib_decls)
        if not current.ok or current.value is None:
            return self.foundation.fail(current.issues)
        target = next((item for item in current.value if item.name == normalized_decl.value), None)
        if target is None:
            return self.foundation.fail(
                self.foundation.issue(
                    "mathlib_decl_use_missing",
                    f"Mathlib declaration use not found: {normalized_decl.value}",
                    object_ref=node_path,
                    field="mathlib_decls",
                )
            )
        permission = self._check_remove_permission(node_path, "mathlib_decls", target.added_by, normalized_actor.value)
        if not permission.ok:
            return self.foundation.fail(permission.issues)

        opened.value.contract.mathlib_decls = [item.model_dump(mode="json") for item in current.value if item.name != normalized_decl.value]
        saved = self._save_contract(repo_root, node_path, opened.value.contract)
        if not saved.ok:
            return self.foundation.fail(saved.issues)
        return self.contract.get_current_contract(repo_root, node_path=node_path)

    def validate_node_mathlib_uses(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        current = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not current.ok or current.value is None:
            return self.foundation.fail(current.issues)
        modules = self._normalize_module_uses(current.value.contract.mathlib_modules)
        decls = self._normalize_decl_uses(current.value.contract.mathlib_decls)
        issues: list[ServiceIssue] = []
        warnings: list[ServiceIssue] = []
        if not modules.ok:
            issues.extend(modules.issues)
        if not decls.ok:
            issues.extend(decls.issues)
        if issues:
            return self.foundation.ok(
                self.foundation.gate_failed("node_mathlib_uses", issues, summary="Node Mathlib use entries are invalid.")
            )

        assert modules.value is not None
        assert decls.value is not None
        module_names = [item.module for item in modules.value]
        duplicate_modules = sorted({name for name in module_names if module_names.count(name) > 1})
        for module in duplicate_modules:
            issues.append(
                self.foundation.issue(
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
                self.foundation.issue(
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
                        self.foundation.issue(
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
                        self.foundation.issue(
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
                    self.foundation.issue(
                        "mathlib_decl_module_not_imported",
                        f"Mathlib declaration {item.name} is indexed in module {module}, but the node does not import that module.",
                        severity=IssueSeverity.WARNING,
                        object_ref=node_path,
                        field="mathlib_decls",
                        suggested_action=f"Add Mathlib module use {module}.",
                    )
                )

        if issues:
            return self.foundation.ok(
                self.foundation.gate_failed("node_mathlib_uses", issues, summary=f"{len(issues)} Mathlib use checks failed.")
            )
        return self.foundation.ok(
            self.foundation.gate_passed(
                "node_mathlib_uses",
                summary=f"Checked {len(modules.value)} Mathlib modules and {len(decls.value)} Mathlib declaration hints.",
                warnings=warnings,
            )
        )

    def _normalize_module_uses(self, values: list[dict[str, Any]]) -> ServiceResult[list[NodeMathlibModuleUse]]:
        adapter = TypeAdapter(NodeMathlibModuleUse)
        normalized: list[NodeMathlibModuleUse] = []
        issues: list[ServiceIssue] = []
        for index, value in enumerate(values):
            try:
                item = adapter.validate_python(self._upgrade_module_use(value))
            except Exception as exc:  # noqa: BLE001 - validation details are returned to caller.
                issues.append(
                    self.foundation.issue(
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
            return self.foundation.fail(issues)
        return self.foundation.ok(normalized)

    def _normalize_decl_uses(self, values: list[dict[str, Any]]) -> ServiceResult[list[NodeMathlibDeclUse]]:
        adapter = TypeAdapter(NodeMathlibDeclUse)
        normalized: list[NodeMathlibDeclUse] = []
        issues: list[ServiceIssue] = []
        for index, value in enumerate(values):
            try:
                item = adapter.validate_python(self._upgrade_decl_use(value))
            except Exception as exc:  # noqa: BLE001 - validation details are returned to caller.
                issues.append(
                    self.foundation.issue(
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
            return self.foundation.fail(issues)
        return self.foundation.ok(normalized)

    def _upgrade_module_use(self, value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            return {"module": value, "added_by": MathlibUseActor.COORDINATOR.value}
        if not isinstance(value, dict):
            return {"module": value}
        ref = value.get("ref")
        module = value.get("module") or value.get("module_name") or value.get("name")
        if isinstance(ref, dict):
            module = module or ref.get("module") or ref.get("name")
        return {
            "module": module,
            "reason": value.get("reason"),
            "added_by": value.get("added_by", MathlibUseActor.COORDINATOR.value),
        }

    def _upgrade_decl_use(self, value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            return {"name": value, "added_by": MathlibUseActor.COORDINATOR.value}
        if not isinstance(value, dict):
            return {"name": value}
        ref = value.get("ref")
        name = value.get("name") or value.get("decl_name") or value.get("declaration")
        module = value.get("module")
        if isinstance(ref, dict):
            name = name or ref.get("name") or ref.get("decl_name") or ref.get("declaration")
            module = module or ref.get("module")
        return {
            "name": name,
            "module": module,
            "kind": value.get("kind"),
            "reason": value.get("reason"),
            "added_by": value.get("added_by", MathlibUseActor.COORDINATOR.value),
        }

    def _module_index_warnings(self, repo_root: Path, module: str) -> ServiceResult[list[ServiceIssue]]:
        entry = self.mathlib_index.get_mathlib_module_entry(repo_root, module=module)
        if entry.ok:
            return self.foundation.ok([])
        if self._is_missing_kind(entry.issues, "mathlib_module_entry_missing"):
            return self.foundation.ok(
                [
                    self.foundation.issue(
                        "mathlib_module_not_indexed",
                        f"Mathlib module use is not recorded in MathlibIndex: {module}",
                        severity=IssueSeverity.WARNING,
                        object_ref=module,
                        field="module",
                    )
                ]
            )
        return self.foundation.fail(entry.issues)

    def _decl_entry_or_warning(self, repo_root: Path, name: str) -> ServiceResult[MathlibDeclEntryView | None]:
        entry = self.mathlib_index.get_mathlib_decl_entry(repo_root, name=name)
        if entry.ok:
            return self.foundation.ok(entry.value)
        if self._is_missing_kind(entry.issues, "mathlib_decl_entry_missing"):
            return self.foundation.ok(
                None,
                warnings=[
                    self.foundation.issue(
                        "mathlib_decl_not_indexed",
                        f"Mathlib declaration use is not recorded in MathlibIndex: {name}",
                        severity=IssueSeverity.WARNING,
                        object_ref=name,
                        field="decl_name",
                    )
                ],
            )
        return self.foundation.fail(entry.issues)

    def _check_remove_permission(
        self,
        node_path: str,
        field_name: str,
        target_actor: MathlibUseActor,
        actor: MathlibUseActor,
    ) -> ServiceResult[None]:
        if actor == MathlibUseActor.WORKER and target_actor != MathlibUseActor.WORKER:
            return self.foundation.fail(
                self.foundation.issue(
                    "mathlib_use_permission_denied",
                    "Worker can only remove Mathlib uses that were added by a worker.",
                    object_ref=node_path,
                    field=field_name,
                    current=target_actor.value,
                    expected=MathlibUseActor.WORKER.value,
                )
            )
        return self.foundation.ok(None)

    def _normalize_actor(self, actor: str | MathlibUseActor) -> ServiceResult[MathlibUseActor]:
        try:
            return self.foundation.ok(MathlibUseActor(actor))
        except ValueError:
            return self.foundation.fail(
                self.foundation.issue(
                    "mathlib_use_actor_invalid",
                    "actor must be coordinator or worker.",
                    field="actor",
                    current=str(actor),
                )
            )

    def _normalize_dotted_name(self, value: str, *, field: str, issue_prefix: str) -> ServiceResult[str]:
        normalized = value.strip() if isinstance(value, str) else ""
        if not normalized:
            return self.foundation.fail(self.foundation.issue(f"{issue_prefix}_name_empty", "Lean dotted name is required.", field=field))
        if not self._is_safe_dotted_name(normalized):
            return self.foundation.fail(
                self.foundation.issue(
                    f"{issue_prefix}_name_invalid",
                    f"Invalid Lean dotted name: {value}",
                    field=field,
                    expected="a non-empty dotted Lean name without whitespace or path separators",
                )
            )
        return self.foundation.ok(normalized)

    def _save_contract(self, repo_root: Path, node_path: str, contract: object) -> ServiceResult[object]:
        path = self.foundation.layout.node_contract_path(
            FoundationContext(repo_root=Path(repo_root)),
            node_path,
            getattr(contract, "version"),
        )
        return self.foundation.store.write_json_atomic(path, contract, mode=WriteMode.UPDATE_EXISTING)

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
