"""Projection repair and audit helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import (
    FoundationService,
    GateReport,
    IssueSeverity,
    MutationSummaryView,
    ServiceIssue,
    ServiceResult,
)
from lean_constellation.services.lean_projection.adapter_facade import AdapterFacadeComponent
from lean_constellation.services.lean_projection.decl_file import DeclFileComponent
from lean_constellation.services.lean_projection.node_projection import NodeProjectionComponent, ProjectionView
from lean_constellation.services.node.node_tree import NodeTreeComponent


ProjectionRepairStatus = Literal["passed", "repaired", "skipped", "failed"]


class ProjectionRepairAction(StrictModel):
    """One repair or audit action performed against a projection target."""

    action: str
    target: str
    status: ProjectionRepairStatus
    changed: bool = False
    changed_files: list[str] = Field(default_factory=list)
    summary: str
    issues: list[ServiceIssue] = Field(default_factory=list)


class ProjectionRepairView(StrictModel):
    """Repair result for one projection scope."""

    scope: str
    changed: bool
    changed_files: list[str] = Field(default_factory=list)
    actions: list[ProjectionRepairAction] = Field(default_factory=list)
    summary: str


class ProjectionAuditView(StrictModel):
    """Structured audit details used by higher-level admin views."""

    reports: list[GateReport] = Field(default_factory=list)
    passed: bool
    summary: str


class RepairDeclProvider(Protocol):
    """Provider for active Decl names in the current graph view."""

    def list_active_decl_names(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[str]]:
        ...


class _MissingRepairDeclProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def list_active_decl_names(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[str]]:
        del repo_root
        return self.foundation.fail(
            self.foundation.issue(
                "repair_decl_provider_missing",
                "No active DeclGraph provider is configured for projection repair.",
                object_ref=node_path,
            )
        )


class RepairComponent:
    """Repair generated projection files without mutating contract or Decl truth."""

    def __init__(
        self,
        foundation: FoundationService | None = None,
        *,
        node_projection: NodeProjectionComponent | None = None,
        adapter_facade: AdapterFacadeComponent | None = None,
        decl_file: DeclFileComponent | None = None,
        node_tree: NodeTreeComponent | None = None,
        decl_provider: RepairDeclProvider | None = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.node_projection = node_projection or NodeProjectionComponent(self.foundation)
        self.adapter_facade = adapter_facade or AdapterFacadeComponent(self.foundation)
        self.decl_file = decl_file or DeclFileComponent(self.foundation)
        self.node_tree = node_tree or NodeTreeComponent(self.foundation)
        self.decl_provider = decl_provider or _MissingRepairDeclProvider(self.foundation)

    def repair_node_projection(self, repo_root: Path, *, node_path: str) -> ServiceResult[ProjectionRepairView]:
        actions: list[ProjectionRepairAction] = []
        for projection_kind in ("prelude", "interfaces"):
            check = self._check_node_projection(repo_root, node_path=node_path, projection_kind=projection_kind)
            if not check.ok or check.value is None:
                return self.foundation.fail(check.issues)
            if check.value.passed:
                actions.append(
                    ProjectionRepairAction(
                        action=f"check_{projection_kind}",
                        target=f"{node_path}:{projection_kind}",
                        status="passed",
                        summary=check.value.summary or f"{projection_kind} projection is synchronized.",
                        issues=check.value.issues,
                    )
                )
                continue
            refresh = self._refresh_node_projection(repo_root, node_path=node_path, projection_kind=projection_kind)
            if not refresh.ok or refresh.value is None:
                return self.foundation.fail([*check.value.issues, *refresh.issues])
            actions.append(self._projection_action(projection_kind, node_path, refresh.value, original_issues=check.value.issues))
        return self.foundation.ok(self._repair_view(scope=node_path, actions=actions))

    def repair_decl_files_from_active_graph(self, repo_root: Path, *, node_path: str) -> ServiceResult[ProjectionRepairView]:
        decls = self.decl_provider.list_active_decl_names(Path(repo_root), node_path=node_path)
        if not decls.ok or decls.value is None:
            return self.foundation.fail(decls.issues)
        actions: list[ProjectionRepairAction] = []
        for decl_name in sorted(set(decls.value)):
            sync = self.decl_file.sync_decl_file_after_revision_reset(Path(repo_root), node_path=node_path, decl_name=decl_name)
            if not sync.ok or sync.value is None:
                return self.foundation.fail(sync.issues)
            actions.append(self._mutation_action("sync_decl_file", f"{node_path}:{decl_name}", sync.value))
        if not actions:
            actions.append(
                ProjectionRepairAction(
                    action="sync_decl_file",
                    target=node_path,
                    status="skipped",
                    summary="No active Decl-owned files were reported for this node.",
                )
            )
        return self.foundation.ok(self._repair_view(scope=node_path, actions=actions))

    def restore_working_projection_to_active_graph(self, repo_root: Path, *, node_path: str) -> ServiceResult[ProjectionRepairView]:
        generated = self.repair_node_projection(Path(repo_root), node_path=node_path)
        if not generated.ok or generated.value is None:
            return self.foundation.fail(generated.issues)
        decls = self.repair_decl_files_from_active_graph(Path(repo_root), node_path=node_path)
        if not decls.ok or decls.value is None:
            return self.foundation.fail(decls.issues)
        return self.foundation.ok(
            self._repair_view(
                scope=node_path,
                actions=[*generated.value.actions, *decls.value.actions],
                summary_prefix="Restored working projection to active graph view",
            )
        )

    def full_projection_audit(self, repo_root: Path) -> ServiceResult[GateReport]:
        reports: list[GateReport] = []
        tree = self.node_tree.get_node_tree(Path(repo_root))
        if not tree.ok or tree.value is None:
            return self.foundation.fail(tree.issues)
        for node in tree.value.nodes:
            for projection_kind in ("prelude", "interfaces"):
                check = self._check_node_projection(Path(repo_root), node_path=node.path, projection_kind=projection_kind)
                if not check.ok or check.value is None:
                    return self.foundation.fail(check.issues)
                reports.append(check.value)
        adapter = self.adapter_facade.check_adapter_interfaces_sync(Path(repo_root))
        if adapter.ok and adapter.value is not None:
            reports.append(adapter.value)
        elif self._only_adapter_provider_missing(adapter.issues):
            reports.append(
                self.foundation.gate_passed(
                    "adapter_interfaces_sync",
                    summary="Adapter facade audit skipped because no adapter provider is configured.",
                    warnings=[
                        self.foundation.issue(
                            "adapter_facade_audit_skipped",
                            "Adapter facade audit skipped because no adapter provider is configured.",
                            severity=IssueSeverity.WARNING,
                        )
                    ],
                )
            )
        else:
            return self.foundation.fail(adapter.issues)
        if not reports:
            reports.append(
                self.foundation.gate_passed(
                    "projection_audit_empty",
                    summary="No projection targets were found.",
                )
            )
        return self.foundation.ok(self.foundation.merge_gate_reports("full_projection_audit", reports))

    def _check_node_projection(
        self,
        repo_root: Path,
        *,
        node_path: str,
        projection_kind: Literal["prelude", "interfaces"] | str,
    ) -> ServiceResult[GateReport]:
        if projection_kind == "prelude":
            return self.node_projection.check_prelude_sync(Path(repo_root), node_path=node_path)
        if projection_kind == "interfaces":
            return self.node_projection.check_interfaces_sync(Path(repo_root), node_path=node_path)
        return self.foundation.fail(
            self.foundation.issue(
                "projection_kind_invalid",
                "Projection repair kind must be prelude or interfaces.",
                object_ref=node_path,
                current=str(projection_kind),
            )
        )

    def _refresh_node_projection(
        self,
        repo_root: Path,
        *,
        node_path: str,
        projection_kind: Literal["prelude", "interfaces"] | str,
    ) -> ServiceResult[ProjectionView]:
        if projection_kind == "prelude":
            return self.node_projection.refresh_prelude(Path(repo_root), node_path=node_path)
        if projection_kind == "interfaces":
            return self.node_projection.refresh_interfaces(Path(repo_root), node_path=node_path)
        return self.foundation.fail(
            self.foundation.issue(
                "projection_kind_invalid",
                "Projection repair kind must be prelude or interfaces.",
                object_ref=node_path,
                current=str(projection_kind),
            )
        )

    def _projection_action(
        self,
        action: str,
        node_path: str,
        view: ProjectionView,
        *,
        original_issues: list[ServiceIssue],
    ) -> ProjectionRepairAction:
        changed_files = [view.path] if view.changed else []
        return ProjectionRepairAction(
            action=f"refresh_{action}",
            target=f"{node_path}:{action}",
            status="repaired" if view.changed else "passed",
            changed=view.changed,
            changed_files=changed_files,
            summary=view.summary,
            issues=original_issues,
        )

    def _mutation_action(self, action: str, target: str, view: MutationSummaryView) -> ProjectionRepairAction:
        return ProjectionRepairAction(
            action=action,
            target=target,
            status="repaired" if view.changed else "passed",
            changed=view.changed,
            changed_files=list(view.changed_items),
            summary=view.summary,
            issues=view.warnings,
        )

    def _repair_view(
        self,
        *,
        scope: str,
        actions: list[ProjectionRepairAction],
        summary_prefix: str = "Projection repair completed",
    ) -> ProjectionRepairView:
        changed_files = sorted({item for action in actions for item in action.changed_files})
        changed = any(action.changed for action in actions)
        failed_count = sum(1 for action in actions if action.status == "failed")
        repaired_count = sum(1 for action in actions if action.status == "repaired")
        summary = f"{summary_prefix} for {scope}: {repaired_count} repaired, {failed_count} failed, {len(actions)} actions."
        return ProjectionRepairView(
            scope=scope,
            changed=changed,
            changed_files=changed_files,
            actions=actions,
            summary=summary,
        )

    def _only_adapter_provider_missing(self, issues: list[ServiceIssue]) -> bool:
        return bool(issues) and all(issue.kind == "adapter_facade_provider_missing" for issue in issues)

