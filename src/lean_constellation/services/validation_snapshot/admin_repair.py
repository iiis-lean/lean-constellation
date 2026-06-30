"""Admin/debug-only repair helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.preparation import RepoPreparationInput
from lean_constellation.services.foundation import FoundationContext, MutationSummaryView, ServiceResult, WriteMode
from lean_constellation.services.lean_projection import LeanProjectionService
from lean_constellation.services.lean_projection.repair import ProjectionRepairView
from lean_constellation.services.node import NodeService
from lean_constellation.services.repo_workspace import RepoWorkspaceService
from lean_constellation.services.validation_snapshot.audit import AuditComponent, AuditReport

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class RequirementRepairHintView(StrictModel):
    requirement_name: str
    target_repo: str | None = None
    obsolete_marked: bool
    recreate_hint: str
    summary: str


class IndexRebuildSummaryView(StrictModel):
    rebuilt_indexes: list[str] = Field(default_factory=list)
    summary: str


class PreparationInputRepairView(StrictModel):
    changed: bool
    allowed_fields: list[str]
    updated_fields: list[str] = Field(default_factory=list)
    summary: str


class AdminRepairComponent:
    """Repair operations intended for admin tool views only."""

    _PREPARATION_INPUT_PATCH_FIELDS = {
        "goal",
        "source_description",
        "source_corpus_relpath",
        "allow_interface_supplement",
        "notes",
    }

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        repo_workspace: RepoWorkspaceService | None = None,
        node: NodeService | None = None,
        lean_projection: LeanProjectionService | None = None,
        audit: AuditComponent | None = None,
    ) -> None:
        self.runtime = runtime
        self._repo_workspace_override = repo_workspace
        self._node_override = node
        self._lean_projection_override = lean_projection
        self.audit = audit or AuditComponent(runtime)

    @property
    def repo_workspace(self) -> RepoWorkspaceService:
        return self._repo_workspace_override or self.runtime.repo_workspace

    @property
    def node(self) -> NodeService:
        return self._node_override or self.runtime.node

    @property
    def lean_projection(self) -> LeanProjectionService:
        return self._lean_projection_override or self.runtime.lean_projection

    def mark_requirement_obsolete_and_recreate_hint(
        self,
        repo_root: Path,
        *,
        requirement_name: str,
        note: str,
        target_repo: str | None = None,
    ) -> ServiceResult[RequirementRepairHintView]:
        if not note or not note.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("repair_note_required", "Repair note is required.", field="note"))
        obsolete = self.repo_workspace.requirement.mark_requirement_obsolete(
            Path(repo_root),
            requirement_name=requirement_name,
            note=note,
        )
        if not obsolete.ok or obsolete.value is None:
            return self.runtime.foundation.fail(obsolete.issues)
        audit_note = self.mutation_view_for_audit_note(
            repo_root,
            note=f"Marked requirement {requirement_name} obsolete: {note.strip()}",
        )
        if not audit_note.ok:
            return self.runtime.foundation.fail(audit_note.issues)
        target = target_repo or obsolete.value.requirement.target_repo
        return self.runtime.foundation.ok(
            RequirementRepairHintView(
                requirement_name=requirement_name,
                target_repo=target,
                obsolete_marked=True,
                recreate_hint=f"Create a new requirement for target repo {target!r} if the dependency is still needed.",
                summary=f"Marked requirement {requirement_name} obsolete.",
            )
        )

    def repair_projection(self, repo_root: Path, *, scope: str = "repo", note: str) -> ServiceResult[ProjectionRepairView]:
        if not note or not note.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("repair_note_required", "Projection repair requires a note.", field="note"))
        repo_root = Path(repo_root)
        if scope != "repo":
            repaired = self.lean_projection.refresh_node_projection(repo_root, node_path=scope)
            if not repaired.ok or repaired.value is None:
                return self.runtime.foundation.fail(repaired.issues)
            audit_note = self.mutation_view_for_audit_note(repo_root, note=f"Projection repair for {scope}: {note.strip()}")
            if not audit_note.ok:
                return self.runtime.foundation.fail(audit_note.issues)
            return repaired
        tree = self.node.node_tree.get_node_tree(repo_root)
        if not tree.ok or tree.value is None:
            return self.runtime.foundation.fail(tree.issues)
        actions = []
        changed_files: list[str] = []
        changed = False
        for node in tree.value.nodes:
            repaired = self.lean_projection.refresh_node_projection(repo_root, node_path=node.path)
            if not repaired.ok or repaired.value is None:
                return self.runtime.foundation.fail(repaired.issues)
            actions.extend(repaired.value.actions)
            changed_files.extend(repaired.value.changed_files)
            changed = changed or repaired.value.changed
        adapter = self.lean_projection.refresh_adapter_projection(repo_root)
        if adapter.ok and adapter.value is not None and adapter.value.changed:
            changed = True
            changed_files.append(adapter.value.path)
        elif not adapter.ok and not self._only_adapter_provider_missing(adapter.issues):
            return self.runtime.foundation.fail(adapter.issues)
        audit_note = self.mutation_view_for_audit_note(repo_root, note=f"Projection repair for repo: {note.strip()}")
        if not audit_note.ok:
            return self.runtime.foundation.fail(audit_note.issues)
        return self.runtime.foundation.ok(
            ProjectionRepairView(
                scope="repo",
                changed=changed,
                changed_files=sorted(set(changed_files)),
                actions=actions,
                summary=f"Repaired repo projection for {len(tree.value.nodes)} nodes.",
            )
        )

    def rebuild_all_indexes(self, repo_root: Path, *, note: str) -> ServiceResult[IndexRebuildSummaryView]:
        if not note or not note.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("repair_note_required", "Index rebuild repair requires a note.", field="note"))
        ctx = FoundationContext(repo_root=Path(repo_root), caller="validation_snapshot.admin_repair")
        metadata = self.runtime.foundation.index.list_index_metadata(ctx)
        if not metadata.ok or metadata.value is None:
            return self.runtime.foundation.fail(metadata.issues)
        rebuilt: list[str] = []
        for item in metadata.value:
            result = self.runtime.foundation.index.rebuild_index(ctx, item.index_name, reason="admin_repair")
            if not result.ok:
                return self.runtime.foundation.fail(result.issues)
            rebuilt.append(item.index_name)
        audit_note = self.mutation_view_for_audit_note(Path(repo_root), note=f"Rebuilt indexes: {note.strip()}")
        if not audit_note.ok:
            return self.runtime.foundation.fail(audit_note.issues)
        return self.runtime.foundation.ok(
            IndexRebuildSummaryView(
                rebuilt_indexes=rebuilt,
                summary=f"Rebuilt {len(rebuilt)} indexes.",
            )
        )

    def run_full_audit(self, repo_root: Path) -> ServiceResult[AuditReport]:
        return self.audit.run_repo_ready_audit(Path(repo_root))

    def repair_preparation_input(
        self,
        repo_root: Path,
        *,
        patch: dict[str, Any],
        note: str,
    ) -> ServiceResult[PreparationInputRepairView]:
        if not note or not note.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("repair_note_required", "Preparation input repair requires a note.", field="note"))
        unknown = sorted(set(patch) - self._PREPARATION_INPUT_PATCH_FIELDS)
        if unknown:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "preparation_input_patch_field_forbidden",
                    "Preparation input patch contains fields that are not allowed through generic admin repair.",
                    field=", ".join(unknown),
                    expected=", ".join(sorted(self._PREPARATION_INPUT_PATCH_FIELDS)),
                )
            )
        current = self.repo_workspace.preparation.get_preparation_input(Path(repo_root))
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        value = current.value.input
        old_dump = value.model_dump(mode="json")
        new_notes = patch.get("notes", value.notes)
        repair_note = f"[admin repair] {note.strip()}"
        patch = dict(patch)
        patch["notes"] = f"{new_notes}\n{repair_note}" if new_notes else repair_note
        try:
            updated = RepoPreparationInput.model_validate({**old_dump, **patch})
        except Exception as exc:  # noqa: BLE001 - normalized as ServiceResult.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("preparation_input_patch_invalid", f"Preparation input patch is invalid: {exc}")
            )
        changed_fields = [
            field
            for field in sorted(self._PREPARATION_INPUT_PATCH_FIELDS)
            if old_dump.get(field) != updated.model_dump(mode="json").get(field)
        ]
        path = self.runtime.foundation.layout.preparation_input_path(FoundationContext(repo_root=Path(repo_root)))
        saved = self.runtime.foundation.store.write_json_atomic(path, updated, mode=WriteMode.UPDATE_EXISTING)
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        audit_note = self.mutation_view_for_audit_note(Path(repo_root), note=f"Preparation input repair: {note.strip()}")
        if not audit_note.ok:
            return self.runtime.foundation.fail(audit_note.issues)
        return self.runtime.foundation.ok(
            PreparationInputRepairView(
                changed=bool(changed_fields),
                allowed_fields=sorted(self._PREPARATION_INPUT_PATCH_FIELDS),
                updated_fields=changed_fields,
                summary=f"Updated preparation input fields: {', '.join(changed_fields) if changed_fields else 'none'}.",
            )
        )

    def mutation_view_for_audit_note(self, repo_root: Path, *, note: str) -> ServiceResult[MutationSummaryView]:
        return self.audit.record_gate_gap(
            Path(repo_root),
            source="admin_repair",
            description=note,
            suggested_gate="admin_repair_note",
        )

    @staticmethod
    def _only_adapter_provider_missing(issues: list[object]) -> bool:
        return bool(issues) and all(getattr(issue, "kind", None) == "adapter_facade_provider_missing" for issue in issues)
