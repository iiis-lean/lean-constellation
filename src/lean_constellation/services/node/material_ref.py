"""Node contract material reference management."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, TypeAdapter

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.refs import MaterialRef, ResourceRef, SourceRef
from lean_constellation.services.foundation import (
    IssueSeverity,
    ServiceIssue,
    ServiceResult,
)
from lean_constellation.services.material.ref_codec import format_material_ref, parse_material_ref
from lean_constellation.services.node.contract import ContractComponent, NodeContractView
from lean_constellation.services.node.contract_fields import ContractMaterialRef, MaterialRefActor

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class NodeMaterialRefView(StrictModel):
    index: int
    ref: str | None = None
    material_kind: Literal["source", "resource"]
    path: str | None = None
    resource_key: str | None = None
    resource_locator: str | None = None
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
        runtime: LeanRuntimeServices,
        *,
        contract: ContractComponent | None = None,
    ) -> None:
        self.runtime = runtime
        self.contract = contract or ContractComponent(runtime)

    def add_owned_source_ref(
        self,
        repo_root: Path,
        *,
        node_path: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        reason: str | None = None,
        actor: str | MaterialRefActor,
    ) -> ServiceResult[NodeContractView]:
        return self._add_ref(
            repo_root,
            node_path=node_path,
            field_name="owned_refs",
            ref_kind="source",
            locator=path,
            start_line=start_line,
            end_line=end_line,
            reason=reason,
            actor=actor,
        )

    def add_owned_resource_ref(
        self,
        repo_root: Path,
        *,
        node_path: str,
        resource_key: str,
        start_line: int | None = None,
        end_line: int | None = None,
        reason: str | None = None,
        actor: str | MaterialRefActor,
    ) -> ServiceResult[NodeContractView]:
        return self._add_ref(
            repo_root,
            node_path=node_path,
            field_name="owned_refs",
            ref_kind="resource",
            locator=resource_key,
            start_line=start_line,
            end_line=end_line,
            reason=reason,
            actor=actor,
        )

    def add_context_source_ref(
        self,
        repo_root: Path,
        *,
        node_path: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        reason: str | None = None,
        actor: str | MaterialRefActor,
    ) -> ServiceResult[NodeContractView]:
        return self._add_ref(
            repo_root,
            node_path=node_path,
            field_name="context_refs",
            ref_kind="source",
            locator=path,
            start_line=start_line,
            end_line=end_line,
            reason=reason,
            actor=actor,
        )

    def add_context_resource_ref(
        self,
        repo_root: Path,
        *,
        node_path: str,
        resource_key: str,
        start_line: int | None = None,
        end_line: int | None = None,
        reason: str | None = None,
        actor: str | MaterialRefActor,
    ) -> ServiceResult[NodeContractView]:
        return self._add_ref(
            repo_root,
            node_path=node_path,
            field_name="context_refs",
            ref_kind="resource",
            locator=resource_key,
            start_line=start_line,
            end_line=end_line,
            reason=reason,
            actor=actor,
        )

    def remove_ref(
        self,
        repo_root: Path,
        *,
        node_path: str,
        ref: str,
        actor: str | MaterialRefActor,
    ) -> ServiceResult[NodeContractView]:
        return self._remove_ref(repo_root, node_path=node_path, selector=ref, actor=actor)

    def list_node_material_refs(self, repo_root: Path, *, node_path: str) -> ServiceResult[NodeMaterialRefsView]:
        current = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        owned = self._normalize_ref_list(current.value.contract.owned_refs)
        if not owned.ok or owned.value is None:
            return self.runtime.foundation.fail(owned.issues)
        context = self._normalize_ref_list(current.value.contract.context_refs)
        if not context.ok or context.value is None:
            return self.runtime.foundation.fail(context.issues)
        owned_views = [self._material_ref_view(repo_root, index, item) for index, item in enumerate(owned.value)]
        context_views = [self._material_ref_view(repo_root, index, item) for index, item in enumerate(context.value)]
        return self.runtime.foundation.ok(
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
            return self.runtime.foundation.fail(normalized_actor.issues)
        material_ref = self._build_material_ref(
            ref_kind=ref_kind,
            locator=locator,
            start_line=start_line,
            end_line=end_line,
        )
        if not material_ref.ok or material_ref.value is None:
            return self.runtime.foundation.fail(material_ref.issues)
        valid = self._validate_material_ref(repo_root, material_ref.value)
        if not valid.ok:
            return self.runtime.foundation.fail(valid.issues)
        candidate = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not candidate.ok or candidate.value is None:
            return self.runtime.foundation.fail(candidate.issues)
        owned = self._normalize_ref_list(candidate.value.contract.owned_refs)
        context = self._normalize_ref_list(candidate.value.contract.context_refs)
        if not owned.ok or owned.value is None:
            return self.runtime.foundation.fail(owned.issues)
        if not context.ok or context.value is None:
            return self.runtime.foundation.fail(context.issues)
        target_refs = owned.value if field_name == "owned_refs" else context.value
        other_refs = context.value if field_name == "owned_refs" else owned.value
        conflicting = self._find_duplicate(other_refs, material_ref.value)
        if conflicting is not None:
            existing_role = "context" if field_name == "owned_refs" else "owned"
            requested_role = "owned" if field_name == "owned_refs" else "context"
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "material_role_conflict",
                    "The exact material ref already exists under a different role.",
                    object_ref=node_path,
                    field=field_name,
                    current=existing_role,
                    expected=requested_role,
                    details={
                        "ref": format_material_ref(material_ref.value),
                        "existing_role": existing_role,
                        "requested_role": requested_role,
                    },
                )
            )
        existing = self._find_duplicate(target_refs, material_ref.value)
        if existing is not None:
            return self.runtime.foundation.ok(
                candidate.value,
                warnings=[
                    self.runtime.foundation.issue(
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
        item = self._deduplicate_ref_id(item, [*owned.value, *context.value])
        target_refs.append(item)
        setattr(candidate.value.contract, field_name, list(target_refs))
        saved = self._save_contract(repo_root, node_path, candidate.value.contract)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        return saved

    def _remove_ref(
        self,
        repo_root: Path,
        *,
        node_path: str,
        selector: str,
        actor: str | MaterialRefActor,
    ) -> ServiceResult[NodeContractView]:
        normalized_actor = self._normalize_actor(actor)
        if not normalized_actor.ok or normalized_actor.value is None:
            return self.runtime.foundation.fail(normalized_actor.issues)
        try:
            parsed = parse_material_ref(selector)
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "material_ref_selector_invalid",
                    str(exc),
                    object_ref=node_path,
                    field="ref",
                    current=str(selector),
                )
            )
        candidate = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not candidate.ok or candidate.value is None:
            return self.runtime.foundation.fail(candidate.issues)
        owned = self._normalize_ref_list(candidate.value.contract.owned_refs)
        context = self._normalize_ref_list(candidate.value.contract.context_refs)
        if not owned.ok or owned.value is None:
            return self.runtime.foundation.fail(owned.issues)
        if not context.ok or context.value is None:
            return self.runtime.foundation.fail(context.issues)
        matches: list[tuple[Literal["owned_refs", "context_refs"], int, ContractMaterialRef]] = []
        for field_name, items in (("owned_refs", owned.value), ("context_refs", context.value)):
            matches.extend(
                (field_name, index, item)
                for index, item in enumerate(items)
                if self._same_ref(item.ref, parsed)
            )
        if not matches:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "material_ref_not_found",
                    "No current material ref matches the exact selector.",
                    object_ref=node_path,
                    field="ref",
                    current=selector,
                )
            )
        if len(matches) > 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "material_ref_selector_ambiguous",
                    "The exact selector matches more than one current material ref.",
                    object_ref=node_path,
                    field="ref",
                    current=selector,
                    details={"match_count": str(len(matches))},
                )
            )
        field_name, index, target = matches[0]
        if not self._actor_can_manage(normalized_actor.value, target.added_by):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "material_ref_permission_denied",
                    "The caller cannot remove a material ref owned by another authority.",
                    object_ref=node_path,
                    field=field_name,
                    current=target.added_by.value,
                    expected=normalized_actor.value.value,
                )
            )
        setattr(
            candidate.value.contract,
            field_name,
            [
                item
                for item_index, item in enumerate(
                    owned.value if field_name == "owned_refs" else context.value
                )
                if item_index != index
            ],
        )
        saved = self._save_contract(repo_root, node_path, candidate.value.contract)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        return saved

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
            return self.runtime.foundation.fail(self.runtime.foundation.issue("material_ref_kind_invalid", "ref_kind must be source or resource.", field="ref_kind"))
        if not locator or not locator.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("material_ref_locator_required", "locator is required.", field="locator"))
        if (start_line is None) != (end_line is None):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("material_ref_range_incomplete", "start_line and end_line must be provided together.", field="start_line")
            )
        if start_line is not None and end_line is not None and not (1 <= start_line <= end_line):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "material_ref_range_invalid",
                    "Material ref line range is invalid.",
                    current=f"{start_line}-{end_line}",
                    expected="1 <= start_line <= end_line",
                )
            )
        if kind == "source":
            return self.runtime.foundation.ok(
                MaterialRef(kind="source", ref=SourceRef(path=locator.strip(), start_line=start_line, end_line=end_line))
            )
        return self.runtime.foundation.ok(
            MaterialRef(
                kind="resource",
                ref=ResourceRef(resource_key=locator.strip(), start_line=start_line, end_line=end_line),
            )
        )

    def _validate_material_ref(self, repo_root: Path, ref: MaterialRef) -> ServiceResult[None]:
        start_line, end_line = self._validation_range(ref)
        if ref.kind == "source" and isinstance(ref.ref, SourceRef):
            validation = self._material_service().material_read.validate_source_range(
                repo_root,
                path=ref.ref.path,
                start_line=start_line,
                end_line=end_line,
            )
            details = {"material_kind": ref.kind, "path": ref.ref.path, "start_line": str(start_line), "end_line": str(end_line)}
        elif ref.kind == "resource" and isinstance(ref.ref, ResourceRef):
            validation = self._material_service().material_read.validate_resource_range(
                repo_root,
                resource_key=ref.ref.resource_key,
                start_line=start_line,
                end_line=end_line,
            )
            details = {"material_kind": ref.kind, "resource_key": ref.ref.resource_key, "start_line": str(start_line), "end_line": str(end_line)}
        else:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("material_ref_invalid", "Material ref shape is invalid.", field="material_ref"))
        if not validation.ok or validation.value is None:
            return self.runtime.foundation.fail(validation.issues)
        valid = self._validation_field(validation.value, "valid")
        if valid is True:
            return self.runtime.foundation.ok(None)
        issue_code = self._validation_field(validation.value, "issue_code") or "material_ref_invalid"
        summary = self._validation_field(validation.value, "summary") or "Material ref validation failed."
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                str(issue_code),
                str(summary),
                field="material_ref",
                details=details,
            )
        )

    def _material_ref_view(self, repo_root: Path, index: int, item: ContractMaterialRef) -> NodeMaterialRefView:
        start_line, end_line = self._stored_range(item.ref)
        try:
            selector = format_material_ref(item.ref)
        except ValueError:
            selector = None
        if item.ref.kind == "source" and isinstance(item.ref.ref, SourceRef):
            path = item.ref.ref.path
            resource_key = None
            resource_locator = None
            preview = self._material_service().material_read.preview_source_ref(
                repo_root,
                path=path,
                start_line=start_line or 1,
                end_line=end_line or start_line or 1,
            )
        elif item.ref.kind == "resource" and isinstance(item.ref.ref, ResourceRef):
            path = None
            resource_key = item.ref.ref.resource_key
            resource_locator = item.ref.ref.locator
            preview = self._material_service().material_read.preview_resource_ref(
                repo_root,
                resource_key=resource_key,
                start_line=start_line or 1,
                end_line=end_line or start_line or 1,
            )
        else:
            path = None
            resource_key = None
            resource_locator = None
            preview = self.runtime.foundation.fail(self.runtime.foundation.issue("material_ref_invalid", "Material ref shape is invalid."))
        valid = preview.ok
        preview_summary = preview.value.summary if preview.ok and preview.value is not None else "; ".join(issue.message for issue in preview.issues)
        return NodeMaterialRefView(
            index=index,
            ref=selector,
            material_kind=item.ref.kind,  # type: ignore[arg-type]
            path=path,
            resource_key=resource_key,
            resource_locator=resource_locator,
            start_line=start_line,
            end_line=end_line,
            reason=item.reason,
            added_by=item.added_by,
            valid=valid,
            preview_summary=preview_summary or None,
            summary=f"{item.ref.kind} material ref {item.ref_id} ({item.added_by.value}).",
        )

    def _material_service(self) -> object:
        material = self.runtime.app.material
        if material is None:
            raise RuntimeError("MaterialService is not initialized.")
        return material

    def _normalize_ref_list(self, refs: list[ContractMaterialRef]) -> ServiceResult[list[ContractMaterialRef]]:
        values: list[ContractMaterialRef] = []
        issues: list[ServiceIssue] = []
        adapter = TypeAdapter(ContractMaterialRef)
        for index, item in enumerate(refs):
            try:
                normalized = adapter.validate_python(item)
            except Exception as exc:  # noqa: BLE001 - pydantic validation details are returned to caller.
                issues.append(
                    self.runtime.foundation.issue(
                        "contract_material_ref_invalid",
                        f"Contract material ref is invalid: {exc}",
                        field=f"material_refs.{index}",
                    )
                )
                continue
            values.append(normalized)
        if issues:
            return self.runtime.foundation.fail(issues)
        return self.runtime.foundation.ok(values)

    def _normalize_actor(self, actor: str | MaterialRefActor) -> ServiceResult[MaterialRefActor]:
        try:
            return self.runtime.foundation.ok(MaterialRefActor(actor))
        except ValueError:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "material_ref_actor_invalid",
                    "actor must be coordinator, worker, or operator.",
                    field="actor",
                    current=str(actor),
                )
            )

    @staticmethod
    def _actor_can_manage(actor: MaterialRefActor, target: MaterialRefActor) -> bool:
        if actor == MaterialRefActor.WORKER:
            return target == MaterialRefActor.WORKER
        if actor == MaterialRefActor.COORDINATOR:
            return target != MaterialRefActor.OPERATOR
        return target == MaterialRefActor.OPERATOR

    def _save_contract(self, repo_root: Path, node_path: str, contract: object) -> ServiceResult[NodeContractView]:
        return self.contract._persist_open_candidate(repo_root, node_path=node_path, candidate=contract)

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

    @staticmethod
    def _same_ref(left: MaterialRef, right: MaterialRef) -> bool:
        return left.model_dump(mode="json") == right.model_dump(mode="json")

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
