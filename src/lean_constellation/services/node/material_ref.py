"""Node contract material reference management."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, TypeAdapter

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.refs import MaterialRef, ResourceRef, SourceRef
from lean_constellation.services.foundation import (
    FoundationContext,
    FoundationService,
    IssueSeverity,
    ServiceIssue,
    ServiceResult,
    WriteMode,
)
from lean_constellation.services.material import MaterialService
from lean_constellation.services.node.contract import ContractComponent, NodeContractView


class MaterialRefActor(StrEnum):
    COORDINATOR = "coordinator"
    WORKER = "worker"


class ContractMaterialRef(StrictModel):
    ref_id: str
    ref: MaterialRef
    reason: str | None = None
    added_by: MaterialRefActor = MaterialRefActor.COORDINATOR


class NodeMaterialRefView(StrictModel):
    ref_id: str
    ref_kind: Literal["source", "resource"]
    locator: str
    start_line: int | None = None
    end_line: int | None = None
    reason: str | None = None
    added_by: MaterialRefActor
    valid: bool
    preview_summary: str | None = None
    summary: str


class NodeMaterialRefsView(StrictModel):
    node_path: str
    owned_refs: list[NodeMaterialRefView] = Field(default_factory=list)
    context_refs: list[NodeMaterialRefView] = Field(default_factory=list)
    summary: str


class MaterialRefComponent:
    """Maintain owned_refs and context_refs embedded in NodeContract."""

    def __init__(
        self,
        foundation: FoundationService | None = None,
        contract: ContractComponent | None = None,
        material: MaterialService | None = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.contract = contract or ContractComponent(self.foundation)
        self.material = material or MaterialService(foundation=self.foundation)

    def add_owned_ref(
        self,
        repo_root: Path,
        *,
        node_path: str,
        ref_kind: str,
        locator: str,
        start_line: int | None = None,
        end_line: int | None = None,
        reason: str | None = None,
        actor: str | MaterialRefActor,
    ) -> ServiceResult[NodeContractView]:
        return self._add_ref(
            repo_root,
            node_path=node_path,
            field_name="owned_refs",
            ref_kind=ref_kind,
            locator=locator,
            start_line=start_line,
            end_line=end_line,
            reason=reason,
            actor=actor,
        )

    def add_context_ref(
        self,
        repo_root: Path,
        *,
        node_path: str,
        ref_kind: str,
        locator: str,
        start_line: int | None = None,
        end_line: int | None = None,
        reason: str | None = None,
        actor: str | MaterialRefActor,
    ) -> ServiceResult[NodeContractView]:
        return self._add_ref(
            repo_root,
            node_path=node_path,
            field_name="context_refs",
            ref_kind=ref_kind,
            locator=locator,
            start_line=start_line,
            end_line=end_line,
            reason=reason,
            actor=actor,
        )

    def remove_owned_ref(
        self,
        repo_root: Path,
        *,
        node_path: str,
        ref_id: str,
        actor: str | MaterialRefActor,
    ) -> ServiceResult[NodeContractView]:
        return self._remove_ref(repo_root, node_path=node_path, field_name="owned_refs", ref_id=ref_id, actor=actor)

    def remove_context_ref(
        self,
        repo_root: Path,
        *,
        node_path: str,
        ref_id: str,
        actor: str | MaterialRefActor,
    ) -> ServiceResult[NodeContractView]:
        return self._remove_ref(repo_root, node_path=node_path, field_name="context_refs", ref_id=ref_id, actor=actor)

    def list_node_material_refs(self, repo_root: Path, *, node_path: str) -> ServiceResult[NodeMaterialRefsView]:
        current = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not current.ok or current.value is None:
            return self.foundation.fail(current.issues)
        owned = self._normalize_ref_list(current.value.contract.owned_refs)
        if not owned.ok or owned.value is None:
            return self.foundation.fail(owned.issues)
        context = self._normalize_ref_list(current.value.contract.context_refs)
        if not context.ok or context.value is None:
            return self.foundation.fail(context.issues)
        owned_views = [self._material_ref_view(repo_root, item) for item in owned.value]
        context_views = [self._material_ref_view(repo_root, item) for item in context.value]
        return self.foundation.ok(
            NodeMaterialRefsView(
                node_path=node_path,
                owned_refs=owned_views,
                context_refs=context_views,
                summary=f"Loaded {len(owned_views)} owned refs and {len(context_views)} context refs for {node_path}.",
            )
        )

    def _add_ref(
        self,
        repo_root: Path,
        *,
        node_path: str,
        field_name: Literal["owned_refs", "context_refs"],
        ref_kind: str,
        locator: str,
        start_line: int | None,
        end_line: int | None,
        reason: str | None,
        actor: str | MaterialRefActor,
    ) -> ServiceResult[NodeContractView]:
        normalized_actor = self._normalize_actor(actor)
        if not normalized_actor.ok or normalized_actor.value is None:
            return self.foundation.fail(normalized_actor.issues)
        material_ref = self._build_material_ref(
            ref_kind=ref_kind,
            locator=locator,
            start_line=start_line,
            end_line=end_line,
        )
        if not material_ref.ok or material_ref.value is None:
            return self.foundation.fail(material_ref.issues)
        valid = self._validate_material_ref(repo_root, material_ref.value)
        if not valid.ok:
            return self.foundation.fail(valid.issues)
        opened = self.contract.ensure_open_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.foundation.fail(opened.issues)
        current = self._normalize_ref_list(getattr(opened.value.contract, field_name))
        if not current.ok or current.value is None:
            return self.foundation.fail(current.issues)
        existing = self._find_duplicate(current.value, material_ref.value)
        if existing is not None:
            view = self.contract.get_current_contract(repo_root, node_path=node_path)
            if not view.ok:
                return self.foundation.fail(view.issues)
            return self.foundation.ok(
                view.value,
                warnings=[
                    self.foundation.issue(
                        "material_ref_duplicate",
                        f"Material ref already exists: {existing.ref_id}",
                        severity=IssueSeverity.WARNING,
                        object_ref=node_path,
                        field=field_name,
                    )
                ],
            )
        item = ContractMaterialRef(
            ref_id=self._stable_ref_id(material_ref.value),
            ref=material_ref.value,
            reason=reason.strip() if reason and reason.strip() else None,
            added_by=normalized_actor.value,
        )
        item = self._deduplicate_ref_id(item, current.value)
        current.value.append(item)
        setattr(opened.value.contract, field_name, [entry.model_dump(mode="json") for entry in current.value])
        saved = self._save_contract(repo_root, node_path, opened.value.contract)
        if not saved.ok:
            return self.foundation.fail(saved.issues)
        return self.contract.get_current_contract(repo_root, node_path=node_path)

    def _remove_ref(
        self,
        repo_root: Path,
        *,
        node_path: str,
        field_name: Literal["owned_refs", "context_refs"],
        ref_id: str,
        actor: str | MaterialRefActor,
    ) -> ServiceResult[NodeContractView]:
        normalized_actor = self._normalize_actor(actor)
        if not normalized_actor.ok or normalized_actor.value is None:
            return self.foundation.fail(normalized_actor.issues)
        if not ref_id or not ref_id.strip():
            return self.foundation.fail(self.foundation.issue("material_ref_id_required", "ref_id is required.", field="ref_id"))
        opened = self.contract.ensure_open_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.foundation.fail(opened.issues)
        current = self._normalize_ref_list(getattr(opened.value.contract, field_name))
        if not current.ok or current.value is None:
            return self.foundation.fail(current.issues)
        target = next((item for item in current.value if item.ref_id == ref_id.strip()), None)
        if target is None:
            return self.foundation.fail(
                self.foundation.issue("material_ref_missing", f"Material ref not found: {ref_id}", object_ref=node_path, field=field_name)
            )
        if normalized_actor.value == MaterialRefActor.WORKER and target.added_by != MaterialRefActor.WORKER:
            return self.foundation.fail(
                self.foundation.issue(
                    "material_ref_permission_denied",
                    "Worker can only remove material refs that were added by a worker.",
                    object_ref=node_path,
                    field=field_name,
                    current=target.added_by.value,
                    expected=MaterialRefActor.WORKER.value,
                )
            )
        setattr(
            opened.value.contract,
            field_name,
            [item.model_dump(mode="json") for item in current.value if item.ref_id != ref_id.strip()],
        )
        saved = self._save_contract(repo_root, node_path, opened.value.contract)
        if not saved.ok:
            return self.foundation.fail(saved.issues)
        return self.contract.get_current_contract(repo_root, node_path=node_path)

    def _build_material_ref(
        self,
        *,
        ref_kind: str,
        locator: str,
        start_line: int | None,
        end_line: int | None,
    ) -> ServiceResult[MaterialRef]:
        kind = ref_kind.strip().lower() if ref_kind else ""
        if kind not in {"source", "resource"}:
            return self.foundation.fail(self.foundation.issue("material_ref_kind_invalid", "ref_kind must be source or resource.", field="ref_kind"))
        if not locator or not locator.strip():
            return self.foundation.fail(self.foundation.issue("material_ref_locator_required", "locator is required.", field="locator"))
        if (start_line is None) != (end_line is None):
            return self.foundation.fail(
                self.foundation.issue("material_ref_range_incomplete", "start_line and end_line must be provided together.", field="start_line")
            )
        if start_line is not None and end_line is not None and not (1 <= start_line <= end_line):
            return self.foundation.fail(
                self.foundation.issue(
                    "material_ref_range_invalid",
                    "Material ref line range is invalid.",
                    current=f"{start_line}-{end_line}",
                    expected="1 <= start_line <= end_line",
                )
            )
        if kind == "source":
            return self.foundation.ok(
                MaterialRef(kind="source", ref=SourceRef(path=locator.strip(), start_line=start_line, end_line=end_line))
            )
        return self.foundation.ok(
            MaterialRef(
                kind="resource",
                ref=ResourceRef(resource_key=locator.strip(), start_line=start_line, end_line=end_line),
            )
        )

    def _validate_material_ref(self, repo_root: Path, ref: MaterialRef) -> ServiceResult[None]:
        locator = self._locator(ref)
        start_line, end_line = self._validation_range(ref)
        validation = self.material.material_read.validate_material_ref(
            repo_root,
            ref_kind=ref.kind,
            locator=locator,
            start_line=start_line,
            end_line=end_line,
        )
        if not validation.ok or validation.value is None:
            return self.foundation.fail(validation.issues)
        valid = self._validation_field(validation.value, "valid")
        if valid is True:
            return self.foundation.ok(None)
        issue_code = self._validation_field(validation.value, "issue_code") or "material_ref_invalid"
        summary = self._validation_field(validation.value, "summary") or "Material ref validation failed."
        return self.foundation.fail(
            self.foundation.issue(
                str(issue_code),
                str(summary),
                field="material_ref",
                details={"ref_kind": ref.kind, "locator": locator, "start_line": str(start_line), "end_line": str(end_line)},
            )
        )

    def _material_ref_view(self, repo_root: Path, item: ContractMaterialRef) -> NodeMaterialRefView:
        locator = self._locator(item.ref)
        start_line, end_line = self._stored_range(item.ref)
        preview = self.material.material_read.preview_material_ref(repo_root, ref=item.ref)
        valid = preview.ok
        preview_summary = preview.value.summary if preview.ok and preview.value is not None else "; ".join(issue.message for issue in preview.issues)
        return NodeMaterialRefView(
            ref_id=item.ref_id,
            ref_kind=item.ref.kind,  # type: ignore[arg-type]
            locator=locator,
            start_line=start_line,
            end_line=end_line,
            reason=item.reason,
            added_by=item.added_by,
            valid=valid,
            preview_summary=preview_summary or None,
            summary=f"{item.ref.kind} material ref {item.ref_id} ({item.added_by.value}).",
        )

    def _normalize_ref_list(self, refs: list[dict[str, Any]]) -> ServiceResult[list[ContractMaterialRef]]:
        values: list[ContractMaterialRef] = []
        issues: list[ServiceIssue] = []
        adapter = TypeAdapter(ContractMaterialRef)
        for index, item in enumerate(refs):
            try:
                normalized = adapter.validate_python(self._upgrade_legacy_ref_dict(item))
            except Exception as exc:  # noqa: BLE001 - pydantic validation details are returned to caller.
                issues.append(
                    self.foundation.issue(
                        "contract_material_ref_invalid",
                        f"Contract material ref is invalid: {exc}",
                        field=f"material_refs.{index}",
                    )
                )
                continue
            values.append(normalized)
        if issues:
            return self.foundation.fail(issues)
        return self.foundation.ok(values)

    def _upgrade_legacy_ref_dict(self, item: dict[str, Any]) -> dict[str, Any]:
        if "ref_id" in item and "ref" in item:
            return item
        kind = item.get("kind") or item.get("ref_kind")
        if kind == "source":
            ref = {
                "kind": "source",
                "ref": {
                    "path": item.get("path") or item.get("locator"),
                    "start_line": item.get("start_line"),
                    "end_line": item.get("end_line"),
                },
            }
        elif kind == "resource":
            ref = {
                "kind": "resource",
                "ref": {
                    "resource_key": item.get("resource_key") or item.get("locator"),
                    "start_line": item.get("start_line"),
                    "end_line": item.get("end_line"),
                },
            }
        else:
            ref = item.get("ref")
        return {
            "ref_id": item.get("ref_id") or self._stable_ref_id(TypeAdapter(MaterialRef).validate_python(ref)),
            "ref": ref,
            "reason": item.get("reason"),
            "added_by": item.get("added_by", MaterialRefActor.COORDINATOR.value),
        }

    def _normalize_actor(self, actor: str | MaterialRefActor) -> ServiceResult[MaterialRefActor]:
        try:
            return self.foundation.ok(MaterialRefActor(actor))
        except ValueError:
            return self.foundation.fail(
                self.foundation.issue(
                    "material_ref_actor_invalid",
                    "actor must be coordinator or worker.",
                    field="actor",
                    current=str(actor),
                )
            )

    def _save_contract(self, repo_root: Path, node_path: str, contract: object) -> ServiceResult[object]:
        path = self.foundation.layout.node_contract_path(
            FoundationContext(repo_root=Path(repo_root)),
            node_path,
            getattr(contract, "version"),
        )
        return self.foundation.store.write_json_atomic(path, contract, mode=WriteMode.UPDATE_EXISTING)

    def _stable_ref_id(self, ref: MaterialRef) -> str:
        payload = json.dumps(ref.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        return f"mat_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"

    def _deduplicate_ref_id(
        self,
        item: ContractMaterialRef,
        current: list[ContractMaterialRef],
    ) -> ContractMaterialRef:
        ids = {existing.ref_id for existing in current}
        if item.ref_id not in ids:
            return item
        suffix = 2
        while f"{item.ref_id}_{suffix}" in ids:
            suffix += 1
        return item.model_copy(update={"ref_id": f"{item.ref_id}_{suffix}"})

    def _find_duplicate(self, current: list[ContractMaterialRef], ref: MaterialRef) -> ContractMaterialRef | None:
        ref_dump = ref.model_dump(mode="json")
        for item in current:
            if item.ref.model_dump(mode="json") == ref_dump:
                return item
        return None

    def _locator(self, ref: MaterialRef) -> str:
        if ref.kind == "source" and isinstance(ref.ref, SourceRef):
            return ref.ref.path
        if ref.kind == "resource" and isinstance(ref.ref, ResourceRef):
            return ref.ref.resource_key
        raise ValueError(f"invalid material ref shape: {ref}")

    def _stored_range(self, ref: MaterialRef) -> tuple[int | None, int | None]:
        if ref.kind == "source" and isinstance(ref.ref, SourceRef):
            return ref.ref.start_line, ref.ref.end_line
        if ref.kind == "resource" and isinstance(ref.ref, ResourceRef):
            return ref.ref.start_line, ref.ref.end_line
        return None, None

    def _validation_range(self, ref: MaterialRef) -> tuple[int, int]:
        start_line, end_line = self._stored_range(ref)
        return start_line or 1, end_line or start_line or 1

    @staticmethod
    def _validation_field(value: Any, field_name: str) -> Any:
        if isinstance(value, dict):
            return value.get(field_name)
        return getattr(value, field_name, None)
