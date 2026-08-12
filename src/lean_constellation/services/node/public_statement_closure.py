"""Public visibility closure for formal statement dependencies."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.repo import proof_availability_for_completion_mode
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.decl_graph.models import (
    Decl,
    DeclLifecycle,
    DeclRevisionStatus,
    RepoDeclDep,
)
from lean_constellation.services.foundation import (
    FoundationContext,
    GateReport,
    ServiceIssue,
    ServiceResult,
    WriteMode,
)
from lean_constellation.services.node.node_tree import NodeKind

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class PublicStatementClosureBoundary(StrEnum):
    CONTENT = "content"
    SCOPE = "scope"


class PublicStatementClosureDecl(StrictModel):
    ref: DeclRef
    required_by: list[DeclRef] = Field(default_factory=list)
    public: bool
    required_export_scopes: list[str] = Field(default_factory=list)
    missing_export_scopes: list[str] = Field(default_factory=list)


class PublicStatementExternalCheck(StrictModel):
    ref: DeclRef
    provider_public: bool
    summary: str


class PublicStatementClosureReport(StrictModel):
    boundary: PublicStatementClosureBoundary
    node_path: str | None = None
    roots: list[DeclRef] = Field(default_factory=list)
    declarations: list[PublicStatementClosureDecl] = Field(default_factory=list)
    required_public_promotions: list[DeclRef] = Field(default_factory=list)
    required_export_additions: dict[str, list[DeclRef]] = Field(default_factory=dict)
    external_checks: list[PublicStatementExternalCheck] = Field(default_factory=list)
    issues: list[ServiceIssue] = Field(default_factory=list)
    closure_complete: bool
    summary: str


class PublicStatementPromotionReceipt(StrictModel):
    boundary: PublicStatementClosureBoundary
    node_path: str | None = None
    changed: bool
    promoted_declarations: list[DeclRef] = Field(default_factory=list)
    added_exports: dict[str, list[DeclRef]] = Field(default_factory=dict)
    report: PublicStatementClosureReport
    summary: str


class DeclVisibilityRevisionReceipt(StrictModel):
    node_path: str
    decl_name: str
    old_visibility: Literal["public", "private"]
    new_visibility: Literal["public", "private"]
    reason: str
    changed: bool
    refreshed_objects: list[str] = Field(default_factory=list)
    gate_reports: list[GateReport] = Field(default_factory=list)
    summary: str


@dataclass(frozen=True)
class _InspectionOptions:
    boundary: PublicStatementClosureBoundary
    node_path: str | None
    visible_contracts: bool


@dataclass(frozen=True)
class _ScopePromotionPlan:
    scope_path: str
    refs: tuple[DeclRef, ...]
    is_target: bool
    expected_active_version: int | None
    expected_open_version: int | None


class _PathSnapshot:
    """Small in-memory rollback snapshot for one scoped business mutation."""

    def __init__(self, roots: list[Path]) -> None:
        self.roots = sorted({path.resolve(strict=False) for path in roots}, key=str)
        self.files: dict[Path, bytes] = {}
        self.existed: set[Path] = set()
        for root in self.roots:
            if root.exists():
                self.existed.add(root)
            if root.is_file():
                self.files[root] = root.read_bytes()
            elif root.is_dir():
                for path in sorted(item for item in root.rglob("*") if item.is_file()):
                    self.files[path] = path.read_bytes()

    def restore(self) -> list[str]:
        errors: list[str] = []
        for root in reversed(self.roots):
            try:
                if root.is_dir():
                    for path in sorted(
                        (item for item in root.rglob("*") if item.is_file()),
                        key=lambda item: len(item.parts),
                        reverse=True,
                    ):
                        if path not in self.files:
                            path.unlink(missing_ok=True)
                elif root.is_file() and root not in self.files:
                    root.unlink(missing_ok=True)
                for path, contents in self.files.items():
                    if path == root or root in path.parents:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(contents)
                if root not in self.existed and root.exists() and root.is_dir():
                    for directory in sorted(
                        (item for item in root.rglob("*") if item.is_dir()),
                        key=lambda item: len(item.parts),
                        reverse=True,
                    ):
                        directory.rmdir()
                    root.rmdir()
            except OSError as exc:
                errors.append(f"{root}: {exc}")
        return errors


class PublicStatementClosureComponent:
    """Derive and repair public closure without persisting a second truth."""

    _REPAIRABLE_ISSUES = {
        "public_statement_decl_not_public",
        "public_statement_export_missing",
    }

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def inspect_content(
        self,
        repo_root: Path,
        *,
        node_path: str,
        root_decl_names: list[str] | None = None,
    ) -> ServiceResult[PublicStatementClosureReport]:
        roots = self._node_roots(Path(repo_root), node_path=node_path, names=root_decl_names)
        if not roots.ok or roots.value is None:
            return self.runtime.foundation.fail(roots.issues)
        return self._inspect(
            Path(repo_root),
            roots=roots.value,
            options=_InspectionOptions(
                boundary=PublicStatementClosureBoundary.CONTENT,
                node_path=node_path,
                visible_contracts=False,
            ),
        )

    def inspect_scope(
        self,
        repo_root: Path,
        *,
        scope_path: str,
        roots: list[DeclRef] | None = None,
        visible: bool = False,
    ) -> ServiceResult[PublicStatementClosureReport]:
        selected = self._scope_roots(
            Path(repo_root),
            scope_path=scope_path,
            roots=roots,
            visible=visible,
        )
        if not selected.ok or selected.value is None:
            return self.runtime.foundation.fail(selected.issues)
        return self._inspect(
            Path(repo_root),
            roots=selected.value,
            options=_InspectionOptions(
                boundary=PublicStatementClosureBoundary.SCOPE,
                node_path=scope_path,
                visible_contracts=visible,
            ),
        )

    def check_content(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        report = self.inspect_content(repo_root, node_path=node_path)
        if not report.ok or report.value is None:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "content_public_statement_closure",
                    report.issues,
                    summary="Public statement closure could not be inspected.",
                )
            )
        return self._gate("content_public_statement_closure", report)

    def check_scope(
        self,
        repo_root: Path,
        *,
        scope_path: str,
        visible: bool = False,
    ) -> ServiceResult[GateReport]:
        report = self.inspect_scope(repo_root, scope_path=scope_path, visible=visible)
        if not report.ok or report.value is None:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "scope_public_statement_closure",
                    report.issues,
                    summary="Public statement closure could not be inspected.",
                )
            )
        return self._gate("scope_public_statement_closure", report)

    def promote_content_decl_public(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
    ) -> ServiceResult[PublicStatementPromotionReceipt]:
        inspected = self.inspect_content(
            Path(repo_root),
            node_path=node_path,
            root_decl_names=[decl_name],
        )
        if not inspected.ok or inspected.value is None:
            return self.runtime.foundation.fail(inspected.issues)
        selected = [
            ref
            for ref in inspected.value.required_public_promotions
            if ref.node == node_path and ref.name == decl_name
        ]
        if not selected:
            return self.runtime.foundation.ok(
                PublicStatementPromotionReceipt(
                    boundary=PublicStatementClosureBoundary.CONTENT,
                    node_path=node_path,
                    changed=False,
                    report=inspected.value,
                    summary=f"{node_path}:{decl_name} is already public.",
                )
            )
        return self._apply(
            Path(repo_root),
            boundary=PublicStatementClosureBoundary.CONTENT,
            node_path=node_path,
            promoted=selected,
            export_additions={},
            reinspect=lambda: self.inspect_content(
                Path(repo_root),
                node_path=node_path,
            ),
        )

    def revise_content_decl_visibility(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        expected_current_visibility: Literal["public", "private"],
        new_visibility: Literal["public", "private"],
        reason: str,
    ) -> ServiceResult[DeclVisibilityRevisionReceipt]:
        repo_root = Path(repo_root)
        if expected_current_visibility not in {"public", "private"} or new_visibility not in {"public", "private"}:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_visibility_invalid",
                    "Declaration visibility must be public or private.",
                    object_ref=f"{node_path}:{decl_name}",
                )
            )
        if not reason.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_visibility_reason_required",
                    "Visibility revision requires an audit reason.",
                    field="reason",
                )
            )
        loaded = self._load_current_decl(repo_root, DeclRef(node=node_path, name=decl_name))
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        decl, _ = loaded.value
        current = "public" if decl.public else "private"
        if current != expected_current_visibility:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_visibility_cas_mismatch",
                    "Declaration visibility changed since the caller inspected it.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=current,
                    expected=expected_current_visibility,
                )
            )
        if current == new_visibility:
            closure = self.check_content(repo_root, node_path=node_path)
            if not closure.ok or closure.value is None:
                return self.runtime.foundation.fail(closure.issues)
            return self.runtime.foundation.ok(
                DeclVisibilityRevisionReceipt(
                    node_path=node_path,
                    decl_name=decl_name,
                    old_visibility=current,
                    new_visibility=new_visibility,
                    reason=reason.strip(),
                    changed=False,
                    gate_reports=[closure.value],
                    summary=f"{node_path}:{decl_name} visibility is already {current}.",
                )
            )
        if new_visibility == "public":
            promoted = self.promote_content_decl_public(
                repo_root,
                node_path=node_path,
                decl_name=decl_name,
            )
            if not promoted.ok or promoted.value is None:
                return self.runtime.foundation.fail(promoted.issues)
            closure = self.check_content(repo_root, node_path=node_path)
            if not closure.ok or closure.value is None:
                return self.runtime.foundation.fail(closure.issues)
            return self.runtime.foundation.ok(
                DeclVisibilityRevisionReceipt(
                    node_path=node_path,
                    decl_name=decl_name,
                    old_visibility=current,
                    new_visibility=new_visibility,
                    reason=reason.strip(),
                    changed=promoted.value.changed,
                    refreshed_objects=[f"interfaces:{node_path}", "node_index"],
                    gate_reports=[closure.value],
                    summary=f"Revised {node_path}:{decl_name} visibility from private to public.",
                )
            )

        blockers = self._visibility_demotion_issues(repo_root, node_path=node_path, decl_name=decl_name)
        if blockers:
            return self.runtime.foundation.fail(blockers)
        ctx = FoundationContext(repo_root=repo_root)
        snapshot = _PathSnapshot(
            [
                self.runtime.foundation.layout.nodes_root(ctx),
                self.runtime.foundation.layout.node_index_path(ctx),
                self.runtime.foundation.layout.interfaces_path(ctx, node_path),
            ]
        )
        mutation_issues: list[ServiceIssue] = []
        with self.runtime.foundation.store.mutation("revise_decl_visibility") as mutation:
            decl.public = False
            decl.updated_at = utc_now_iso()
            mutation.stage_json(
                self.runtime.decl_graph.graph_store.decl_record_path(
                    repo_root,
                    node_path=node_path,
                    decl_name=decl_name,
                ),
                decl,
                mode=WriteMode.UPDATE_EXISTING,
            )
            committed = mutation.commit()
        if not committed.ok:
            mutation_issues.extend(committed.issues)
        if not mutation_issues:
            refreshed = self.runtime.lean_projection.node_projection.refresh_interfaces(
                repo_root,
                node_path=node_path,
            )
            if not refreshed.ok:
                mutation_issues.extend(refreshed.issues)
        if not mutation_issues:
            rebuilt = self.runtime.node.node_tree.node_store.rebuild_index(repo_root)
            if not rebuilt.ok:
                mutation_issues.extend(rebuilt.issues)
        gate_reports: list[GateReport] = []
        if not mutation_issues:
            closure = self.check_content(repo_root, node_path=node_path)
            if not closure.ok or closure.value is None:
                mutation_issues.extend(closure.issues)
            else:
                gate_reports.append(closure.value)
                if not closure.value.passed:
                    mutation_issues.extend(closure.value.issues)
        if mutation_issues:
            rollback_errors = snapshot.restore()
            if rollback_errors:
                mutation_issues.append(
                    self.runtime.foundation.issue(
                        "decl_visibility_rollback_failed",
                        "Visibility revision failed and rollback was incomplete.",
                        details={"errors": rollback_errors},
                    )
                )
            return self.runtime.foundation.fail(self._unique_issues(mutation_issues))
        return self.runtime.foundation.ok(
            DeclVisibilityRevisionReceipt(
                node_path=node_path,
                decl_name=decl_name,
                old_visibility=current,
                new_visibility=new_visibility,
                reason=reason.strip(),
                changed=True,
                refreshed_objects=[f"interfaces:{node_path}", "node_index"],
                gate_reports=gate_reports,
                summary=f"Revised {node_path}:{decl_name} visibility from public to private.",
            )
        )

    def _visibility_demotion_issues(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
    ) -> list[ServiceIssue]:
        issues: list[ServiceIssue] = []
        release = self.runtime.repo_workspace.release.get_decl_release_status(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
        )
        if not release.ok or release.value is None:
            issues.extend(release.issues)
        elif release.value.release_protected:
            issues.append(
                self.runtime.foundation.issue(
                    "decl_visibility_release_protected",
                    "A declaration protected by the current Release cannot be made private.",
                    object_ref=f"{node_path}:{decl_name}",
                )
            )
        inbound = self.runtime.decl_graph.release_guard.current_inbound_refs(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
        )
        if not inbound.ok or inbound.value is None:
            issues.extend(inbound.issues)
            return self._unique_issues(issues)
        for ref in inbound.value:
            if ref.startswith("admitted:round:"):
                issues.append(
                    self.runtime.foundation.issue(
                        "decl_visibility_running_consumer_required",
                        "A running or awaiting-closeout declaration round consumes this public declaration.",
                        object_ref=ref,
                    )
                )
                continue
            if ref.endswith(":proof"):
                continue
            if ":contract:" in ref:
                if ref.endswith(":interfaces"):
                    kind = "decl_visibility_interface_required"
                    message = "A current interface binding requires this declaration to remain public."
                elif ref.endswith(":exports"):
                    kind = "decl_visibility_main_api_required" if ":Main:exports" in ref else "decl_visibility_scope_export_required"
                    message = "A current Scope or Main export requires this declaration to remain public."
                else:
                    kind = "decl_visibility_consumer_required"
                    message = "A current node contract dependency requires this declaration's public boundary."
                issues.append(self.runtime.foundation.issue(kind, message, object_ref=ref))
                continue
            if ref.endswith(":statement"):
                parts = ref.split(":")
                if len(parts) != 5:
                    continue
                consumer = self.runtime.decl_graph.get_decl(
                    repo_root,
                    node_path=parts[2],
                    name=parts[3],
                )
                if not consumer.ok or consumer.value is None:
                    issues.extend(consumer.issues)
                elif consumer.value.public:
                    issues.append(
                        self.runtime.foundation.issue(
                            "decl_visibility_public_statement_required",
                            "Another public declaration requires this declaration in its formal Statement closure.",
                            object_ref=ref,
                        )
                    )
        return self._unique_issues(issues)

    def promote_content_closure(
        self,
        repo_root: Path,
        *,
        node_path: str,
        root_decl_names: list[str] | None = None,
    ) -> ServiceResult[PublicStatementPromotionReceipt]:
        inspected = self.inspect_content(
            Path(repo_root),
            node_path=node_path,
            root_decl_names=root_decl_names,
        )
        if not inspected.ok or inspected.value is None:
            return self.runtime.foundation.fail(inspected.issues)
        unsafe = self._unsafe_issues(inspected.value)
        if unsafe:
            return self.runtime.foundation.fail(unsafe)
        promoted = [
            ref for ref in inspected.value.required_public_promotions if ref.node == node_path
        ]
        return self._apply(
            Path(repo_root),
            boundary=PublicStatementClosureBoundary.CONTENT,
            node_path=node_path,
            promoted=promoted,
            export_additions={},
            reinspect=lambda: self.inspect_content(
                Path(repo_root),
                node_path=node_path,
                root_decl_names=root_decl_names,
            ),
        )

    def promote_scope_closure(
        self,
        repo_root: Path,
        *,
        scope_path: str,
        roots: list[DeclRef] | None = None,
    ) -> ServiceResult[PublicStatementPromotionReceipt]:
        inspected = self.inspect_scope(
            Path(repo_root),
            scope_path=scope_path,
            roots=roots,
        )
        if not inspected.ok or inspected.value is None:
            return self.runtime.foundation.fail(inspected.issues)
        unsafe = self._unsafe_issues(inspected.value)
        if unsafe:
            return self.runtime.foundation.fail(unsafe)
        return self._apply(
            Path(repo_root),
            boundary=PublicStatementClosureBoundary.SCOPE,
            node_path=scope_path,
            promoted=inspected.value.required_public_promotions,
            export_additions=inspected.value.required_export_additions,
            reinspect=lambda: self.inspect_scope(
                Path(repo_root),
                scope_path=scope_path,
                roots=roots,
            ),
        )

    def _inspect(
        self,
        repo_root: Path,
        *,
        roots: list[DeclRef],
        options: _InspectionOptions,
    ) -> ServiceResult[PublicStatementClosureReport]:
        queue: deque[tuple[DeclRef, DeclRef | None]] = deque(
            (root, None) for root in roots
        )
        visited: set[tuple[str, str, str]] = set()
        requirements: dict[tuple[str, str, str], PublicStatementClosureDecl] = {}
        external: dict[tuple[str, str, str], PublicStatementExternalCheck] = {}
        issues: list[ServiceIssue] = []

        while queue:
            ref, consumer = queue.popleft()
            if ref.repo is not None:
                key = (ref.repo, ref.node, ref.name)
                if key in external:
                    continue
                provider_public = self._external_public(repo_root, ref)
                external[key] = PublicStatementExternalCheck(
                    ref=ref,
                    provider_public=provider_public,
                    summary=(
                        "External provider declaration is public."
                        if provider_public
                        else "External provider declaration is not available through its repository Main boundary."
                    ),
                )
                if not provider_public:
                    issues.append(
                        self.runtime.foundation.issue(
                            "public_statement_external_provider_not_public",
                            "External Statement dependency is not public through the provider repository Main boundary.",
                            object_ref=f"{ref.repo}:{ref.node}:{ref.name}",
                        )
                    )
                continue

            key = ("", ref.node, ref.name)
            loaded = self._load_exact_decl(repo_root, ref)
            if not loaded.ok or loaded.value is None:
                issues.extend(loaded.issues)
                continue
            decl, revision_ref = loaded.value
            ref = revision_ref
            required_scopes = self._required_export_scopes(ref.node, options)
            missing_scopes: list[str] = []
            for scope_path in required_scopes:
                stable_intermediate = scope_path != options.node_path
                exported = self._scope_exports_ref(
                    repo_root,
                    scope_path=scope_path,
                    ref=ref,
                    visible=(
                        options.visible_contracts
                        or stable_intermediate
                    ),
                )
                if not exported.ok or exported.value is None:
                    if not (
                        stable_intermediate
                        and {issue.kind for issue in exported.issues}
                        == {"node_committed_contract_missing"}
                    ):
                        issues.extend(exported.issues)
                        continue
                    is_exported = False
                else:
                    is_exported = exported.value
                if not is_exported:
                    missing_scopes.append(scope_path)
                    issues.append(
                        self.runtime.foundation.issue(
                            "public_statement_export_missing",
                            "A formal Statement dependency is missing from the required Scope public boundary.",
                            object_ref=f"{ref.node}:{ref.name}",
                            details={"scope_path": scope_path},
                        )
                    )

            item = requirements.get(key)
            required_by = list(item.required_by) if item is not None else []
            if consumer is not None and not any(
                self._identity(existing) == self._identity(consumer)
                for existing in required_by
            ):
                required_by.append(consumer)
            requirements[key] = PublicStatementClosureDecl(
                ref=ref,
                required_by=sorted(required_by, key=self._ref_sort_key),
                public=decl.public,
                required_export_scopes=required_scopes,
                missing_export_scopes=missing_scopes,
            )
            if not decl.public:
                issues.append(
                    self.runtime.foundation.issue(
                        "public_statement_decl_not_public",
                        "A declaration required by a public formal Statement is private.",
                        object_ref=f"{ref.node}:{ref.name}",
                    )
                )
            if key in visited:
                continue
            visited.add(key)
            revision = self.runtime.decl_graph.get_decl_revision(
                repo_root,
                node_path=decl.node_path,
                name=decl.name,
                revision=ref.revision,
            )
            if not revision.ok or revision.value is None:
                issues.extend(revision.issues)
                continue
            for dep in revision.value.statement.deps:
                if isinstance(dep, RepoDeclDep):
                    dep_ref = dep.ref
                    if (
                        dep_ref.repo is None
                        and dep_ref.node == "Main"
                        and decl.node_path != "Main"
                    ):
                        dep_ref = dep_ref.model_copy(
                            update={"node": decl.node_path}
                        )
                    queue.append((dep_ref, ref))

        declarations = sorted(requirements.values(), key=lambda item: self._ref_sort_key(item.ref))
        promotions = sorted(
            [item.ref for item in declarations if not item.public],
            key=self._ref_sort_key,
        )
        additions: dict[str, list[DeclRef]] = {}
        for item in declarations:
            for scope_path in item.missing_export_scopes:
                additions.setdefault(scope_path, []).append(item.ref)
        additions = {
            scope: sorted(self._unique_refs(refs), key=self._ref_sort_key)
            for scope, refs in sorted(additions.items())
        }
        issues = self._unique_issues(issues)
        return self.runtime.foundation.ok(
            PublicStatementClosureReport(
                boundary=options.boundary,
                node_path=options.node_path,
                roots=sorted(self._unique_refs(roots), key=self._ref_sort_key),
                declarations=declarations,
                required_public_promotions=promotions,
                required_export_additions=additions,
                external_checks=sorted(external.values(), key=lambda item: self._ref_sort_key(item.ref)),
                issues=issues,
                closure_complete=not issues,
                summary=(
                    f"Public Statement closure is complete for {len(roots)} root declaration(s)."
                    if not issues
                    else (
                        f"Public Statement closure needs {len(promotions)} declaration promotion(s) "
                        f"and {sum(len(refs) for refs in additions.values())} Scope export addition(s)."
                    )
                ),
            )
        )

    def _apply(
        self,
        repo_root: Path,
        *,
        boundary: PublicStatementClosureBoundary,
        node_path: str,
        promoted: list[DeclRef],
        export_additions: dict[str, list[DeclRef]],
        reinspect,
    ) -> ServiceResult[PublicStatementPromotionReceipt]:
        promoted = sorted(self._unique_refs(promoted), key=self._ref_sort_key)
        export_additions = {
            scope: sorted(self._unique_refs(refs), key=self._ref_sort_key)
            for scope, refs in export_additions.items()
        }
        preflight_issues: list[ServiceIssue] = []
        for ref in promoted:
            loaded = self._load_current_decl(repo_root, ref)
            if not loaded.ok or loaded.value is None:
                preflight_issues.extend(loaded.issues)
                continue
            decl, current_ref = loaded.value
            if current_ref.revision != ref.revision or decl.public:
                preflight_issues.append(
                    self.runtime.foundation.issue(
                        "public_statement_promotion_cas_mismatch",
                        "Declaration revision or visibility changed after closure inspection.",
                        object_ref=f"{ref.node}:{ref.name}",
                        current=(
                            f"revision={current_ref.revision}, "
                            f"visibility={'public' if decl.public else 'private'}"
                        ),
                        expected=f"revision={ref.revision}, visibility=private",
                    )
                )
        anchors = (
            self._validate_content_promotion_owners(
                repo_root,
                node_path=node_path,
                promoted=promoted,
            )
            if boundary == PublicStatementClosureBoundary.CONTENT
            else self._validate_committed_promotion_anchors(repo_root, promoted)
        )
        if not anchors.ok:
            preflight_issues.extend(anchors.issues)
        config = self.runtime.repo_workspace.metadata.get_repo_config(repo_root)
        if not config.ok or config.value is None:
            preflight_issues.extend(config.issues)
        if preflight_issues:
            return self.runtime.foundation.fail(self._unique_issues(preflight_issues))
        target = proof_availability_for_completion_mode(config.value.config.completion_mode)
        readiness = self.runtime.decl_graph.check_decl_proof_policy_batch(
            repo_root,
            roots=[(ref.node, ref.name, target) for ref in promoted],
        )
        if not readiness.ok or readiness.value is None:
            return self.runtime.foundation.fail(readiness.issues)
        for ref, report in zip(promoted, readiness.value, strict=True):
            if not report.ready:
                preflight_issues.append(
                    self.runtime.foundation.issue(
                        "public_statement_promotion_not_ready",
                        "Only an active declaration with a committed, policy-satisfied current revision can be promoted.",
                        object_ref=f"{ref.node}:{ref.name}",
                    )
                )
        if preflight_issues:
            return self.runtime.foundation.fail(self._unique_issues(preflight_issues))

        scope_plans = self._build_scope_promotion_plans(
            repo_root,
            target_scope=node_path,
            export_additions=export_additions,
        )
        if not scope_plans.ok or scope_plans.value is None:
            return self.runtime.foundation.fail(scope_plans.issues)

        touched_nodes = {ref.node for ref in promoted} | set(export_additions)
        snapshot = _PathSnapshot(
            [
                self.runtime.foundation.layout.nodes_root(FoundationContext(repo_root=repo_root)),
                self.runtime.foundation.layout.node_index_path(FoundationContext(repo_root=repo_root)),
                *[
                    self.runtime.foundation.layout.interfaces_path(
                        FoundationContext(repo_root=repo_root),
                        touched,
                    )
                    for touched in touched_nodes
                ],
            ]
        )
        mutation_issues: list[ServiceIssue] = []
        if promoted:
            with self.runtime.foundation.store.mutation("promote_public_statement_declarations") as mutation:
                for ref in promoted:
                    loaded = self.runtime.decl_graph.get_decl(
                        repo_root,
                        node_path=ref.node,
                        name=ref.name,
                    )
                    if not loaded.ok or loaded.value is None:
                        mutation_issues.extend(loaded.issues)
                        continue
                    if loaded.value.current_revision != ref.revision or loaded.value.public:
                        mutation_issues.append(
                            self.runtime.foundation.issue(
                                "public_statement_promotion_cas_mismatch",
                                "Declaration revision or visibility changed before promotion mutation.",
                                object_ref=f"{ref.node}:{ref.name}",
                                current=(
                                    f"revision={loaded.value.current_revision}, "
                                    f"visibility={'public' if loaded.value.public else 'private'}"
                                ),
                                expected=f"revision={ref.revision}, visibility=private",
                            )
                        )
                        continue
                    loaded.value.public = True
                    loaded.value.updated_at = utc_now_iso()
                    mutation.stage_json(
                        self.runtime.decl_graph.graph_store.decl_record_path(
                            repo_root,
                            node_path=ref.node,
                            decl_name=ref.name,
                        ),
                        loaded.value,
                        mode=WriteMode.UPDATE_EXISTING,
                    )
                committed = mutation.commit() if not mutation_issues else None
            if committed is not None and not committed.ok:
                mutation_issues.extend(committed.issues)
        if not mutation_issues:
            for content_path in sorted({ref.node for ref in promoted}):
                refreshed = self.runtime.lean_projection.node_projection.refresh_interfaces(
                    repo_root,
                    node_path=content_path,
                )
                if not refreshed.ok:
                    mutation_issues.extend(refreshed.issues)
                    break
        if not mutation_issues:
            for plan in scope_plans.value:
                current = self.runtime.node.node_tree.get_node(
                    repo_root,
                    path=plan.scope_path,
                )
                if plan.is_target:
                    if (
                        not current.ok
                        or current.value is None
                        or current.value.active_contract_version
                        != plan.expected_active_version
                        or current.value.open_contract_version
                        != plan.expected_open_version
                    ):
                        mutation_issues.append(
                            self.runtime.foundation.issue(
                                "scope_promotion_target_cas_mismatch",
                                "Target Scope ownership changed after promotion preflight.",
                                object_ref=plan.scope_path,
                                expected=(
                                    f"active={plan.expected_active_version}, "
                                    f"open={plan.expected_open_version}"
                                ),
                            )
                        )
                        break
                else:
                    if (
                        not current.ok
                        or current.value is None
                        or current.value.active_contract_version
                        != plan.expected_active_version
                        or current.value.open_contract_version is not None
                    ):
                        mutation_issues.append(
                            self.runtime.foundation.issue(
                                "scope_promotion_intermediate_cas_mismatch",
                                "Intermediate Scope ownership changed after promotion preflight.",
                                object_ref=plan.scope_path,
                                expected=(
                                    f"active={plan.expected_active_version}, open=None"
                                ),
                            )
                        )
                        break
                    opened = self.runtime.node.contract.ensure_open_contract(
                        repo_root,
                        node_path=plan.scope_path,
                    )
                    if (
                        not opened.ok
                        or opened.value is None
                        or not opened.value.created_new_open
                    ):
                        mutation_issues.extend(opened.issues)
                        if opened.ok:
                            mutation_issues.append(
                                self.runtime.foundation.issue(
                                    "scope_promotion_intermediate_fresh_open_required",
                                    "Intermediate Scope promotion requires a fresh revision copied from the expected active boundary.",
                                    object_ref=plan.scope_path,
                                )
                            )
                        break
                for ref in plan.refs:
                    added = self.runtime.node.export.add_scope_export(
                        repo_root,
                        scope_path=plan.scope_path,
                        decl_node=ref.node,
                        decl_name=ref.name,
                        revision=ref.revision,
                    )
                    if not added.ok:
                        mutation_issues.extend(added.issues)
                        break
                if mutation_issues:
                    break
                if not plan.is_target:
                    committed = self.runtime.node.commit_scope_contract(
                        repo_root,
                        scope_path=plan.scope_path,
                        summary="Commit repaired child Scope boundary before parent closure propagation.",
                    )
                    if not committed.ok:
                        mutation_issues.extend(committed.issues)
                        break
        if not mutation_issues:
            rebuilt = self.runtime.node.node_tree.node_store.rebuild_index(repo_root)
            if not rebuilt.ok:
                mutation_issues.extend(rebuilt.issues)
        if mutation_issues:
            rollback_errors = snapshot.restore()
            if rollback_errors:
                mutation_issues.append(
                    self.runtime.foundation.issue(
                        "public_statement_promotion_rollback_failed",
                        "Public visibility promotion failed and rollback was incomplete.",
                        details={"errors": rollback_errors},
                    )
                )
            return self.runtime.foundation.fail(self._unique_issues(mutation_issues))

        report = reinspect()
        if not report.ok or report.value is None:
            rollback_errors = snapshot.restore()
            issues = list(report.issues)
            if rollback_errors:
                issues.append(
                    self.runtime.foundation.issue(
                        "public_statement_promotion_rollback_failed",
                        "Post-mutation closure verification failed and rollback was incomplete.",
                        details={"errors": rollback_errors},
                    )
                )
            return self.runtime.foundation.fail(issues)
        if not report.value.closure_complete:
            rollback_errors = snapshot.restore()
            issues = list(report.value.issues)
            if rollback_errors:
                issues.append(
                    self.runtime.foundation.issue(
                        "public_statement_promotion_rollback_failed",
                        "Closure repair remained incomplete and rollback was incomplete.",
                        details={"errors": rollback_errors},
                    )
                )
            return self.runtime.foundation.fail(issues)
        changed = bool(promoted or any(export_additions.values()))
        return self.runtime.foundation.ok(
            PublicStatementPromotionReceipt(
                boundary=boundary,
                node_path=node_path,
                changed=changed,
                promoted_declarations=promoted,
                added_exports=export_additions,
                report=report.value,
                summary=(
                    f"Promoted {len(promoted)} declaration(s) and added "
                    f"{sum(len(refs) for refs in export_additions.values())} Scope export(s)."
                    if changed
                    else "Public Statement closure was already complete."
                ),
            )
        )

    def _validate_committed_promotion_anchors(
        self,
        repo_root: Path,
        promoted: list[DeclRef],
    ) -> ServiceResult[None]:
        heads: dict[str, dict[str, int]] = {}
        issues: list[ServiceIssue] = []
        for node_path in sorted({ref.node for ref in promoted}):
            visible = self.runtime.node.contract.get_visible_contract(
                repo_root,
                node_path=node_path,
            )
            if not visible.ok or visible.value is None:
                issues.extend(visible.issues)
                continue
            if visible.value.node_kind != NodeKind.CONTENT:
                issues.append(
                    self.runtime.foundation.issue(
                        "public_statement_promotion_owner_not_content",
                        "Declaration visibility promotion requires a Content owner.",
                        object_ref=node_path,
                    )
                )
                continue
            target = self.runtime.node.contract.check_provider_completion_target(
                repo_root,
                node_path=node_path,
                contract=visible.value,
                require_committed=True,
            )
            if not target.ok or target.value is None:
                issues.extend(target.issues)
                continue
            if not target.value.passed:
                issues.extend(target.value.issues)
                continue
            heads[node_path] = visible.value.contract.decl_graph_head
        for ref in promoted:
            if heads.get(ref.node, {}).get(ref.name) != ref.revision:
                issues.append(
                    self.runtime.foundation.issue(
                        "public_statement_promotion_not_committed_head",
                        "Visibility promotion requires the exact declaration revision in the active committed Content head.",
                        object_ref=f"{ref.node}:{ref.name}@{ref.revision}",
                    )
                )
        if issues:
            return self.runtime.foundation.fail(self._unique_issues(issues))
        return self.runtime.foundation.ok(None)

    def _validate_content_promotion_owners(
        self,
        repo_root: Path,
        *,
        node_path: str,
        promoted: list[DeclRef],
    ) -> ServiceResult[None]:
        issues: list[ServiceIssue] = []
        owner = self.runtime.node.node_tree.get_node(repo_root, path=node_path)
        if not owner.ok or owner.value is None:
            return self.runtime.foundation.fail(owner.issues)
        if owner.value.kind != NodeKind.CONTENT:
            issues.append(
                self.runtime.foundation.issue(
                    "public_statement_promotion_owner_not_content",
                    "Content-local visibility promotion requires a Content owner.",
                    object_ref=node_path,
                )
            )
        for ref in promoted:
            if ref.node != node_path:
                issues.append(
                    self.runtime.foundation.issue(
                        "public_statement_promotion_owner_mismatch",
                        "Content-local visibility promotion cannot mutate a declaration owned by another node.",
                        object_ref=f"{ref.node}:{ref.name}@{ref.revision}",
                        current=ref.node,
                        expected=node_path,
                    )
                )
        if issues:
            return self.runtime.foundation.fail(self._unique_issues(issues))
        return self.runtime.foundation.ok(None)

    def _build_scope_promotion_plans(
        self,
        repo_root: Path,
        *,
        target_scope: str,
        export_additions: dict[str, list[DeclRef]],
    ) -> ServiceResult[list[_ScopePromotionPlan]]:
        plans: list[_ScopePromotionPlan] = []
        issues: list[ServiceIssue] = []
        for scope_path in sorted(
            (path for path, refs in export_additions.items() if refs),
            key=lambda path: (-path.count("."), path),
        ):
            refs = tuple(export_additions[scope_path])
            if scope_path == target_scope:
                node = self.runtime.node.node_tree.get_node(
                    repo_root,
                    path=scope_path,
                )
                if not node.ok or node.value is None:
                    issues.extend(node.issues)
                    continue
                if node.value.kind != NodeKind.SCOPE:
                    issues.append(
                        self.runtime.foundation.issue(
                            "scope_promotion_target_not_scope",
                            "Public closure propagation can target only a Scope node.",
                            object_ref=scope_path,
                        )
                    )
                    continue
                if node.value.open_contract_version is None:
                    issues.append(
                        self.runtime.foundation.issue(
                            "scope_promotion_target_open_required",
                            "Target Scope promotion requires an existing caller-owned open revision.",
                            object_ref=scope_path,
                            current=(
                                f"active={node.value.active_contract_version}, open=None"
                            ),
                            expected="existing open Scope revision",
                        )
                    )
                    continue
                opened = self.runtime.node.contract.get_open_contract(
                    repo_root,
                    node_path=scope_path,
                )
                if not opened.ok or opened.value is None:
                    issues.extend(opened.issues)
                    continue
                plans.append(
                    _ScopePromotionPlan(
                        scope_path=scope_path,
                        refs=refs,
                        is_target=True,
                        expected_active_version=node.value.active_contract_version,
                        expected_open_version=node.value.open_contract_version,
                    )
                )
                continue
            node = self.runtime.node.node_tree.get_node(repo_root, path=scope_path)
            if not node.ok or node.value is None:
                issues.extend(node.issues)
                continue
            if node.value.kind != NodeKind.SCOPE:
                issues.append(
                    self.runtime.foundation.issue(
                        "scope_promotion_intermediate_not_scope",
                        "Public closure propagation can mutate only intermediate Scope nodes.",
                        object_ref=scope_path,
                    )
                )
                continue
            if node.value.active_contract_version is None:
                issues.append(
                    self.runtime.foundation.issue(
                        "scope_promotion_intermediate_not_committed",
                        "Intermediate Scope requires an active committed boundary before automatic promotion.",
                        object_ref=scope_path,
                        current=f"open={node.value.open_contract_version}",
                        expected="active committed contract",
                    )
                )
                continue
            if node.value.open_contract_version is not None:
                issues.append(
                    self.runtime.foundation.issue(
                        "scope_promotion_intermediate_open",
                        "Intermediate Scope has caller-owned open edits and cannot be mutated automatically.",
                        object_ref=scope_path,
                        current=(
                            f"active={node.value.active_contract_version}, "
                            f"open={node.value.open_contract_version}"
                        ),
                        expected="active committed contract with no open revision",
                    )
                )
                continue
            visible = self.runtime.node.contract.get_visible_contract(
                repo_root,
                node_path=scope_path,
            )
            if not visible.ok or visible.value is None:
                issues.extend(visible.issues)
                continue
            plans.append(
                _ScopePromotionPlan(
                    scope_path=scope_path,
                    refs=refs,
                    is_target=False,
                    expected_active_version=node.value.active_contract_version,
                    expected_open_version=None,
                )
            )
        if issues:
            return self.runtime.foundation.fail(self._unique_issues(issues))
        return self.runtime.foundation.ok(plans)

    def _node_roots(
        self,
        repo_root: Path,
        *,
        node_path: str,
        names: list[str] | None,
    ) -> ServiceResult[list[DeclRef]]:
        node = self.runtime.node.node_tree.get_node(repo_root, path=node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        if node.value.kind != NodeKind.CONTENT:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "public_statement_node_not_content",
                    "Current-node public Statement closure requires a Content node.",
                    object_ref=node_path,
                )
            )
        decls = self.runtime.decl_graph.list_decls(repo_root, node_path=node_path)
        if not decls.ok or decls.value is None:
            return self.runtime.foundation.fail(decls.issues)
        by_name = {
            decl.name: decl
            for decl in decls.value
            if decl.lifecycle == DeclLifecycle.ACTIVE
        }
        selected = {decl.name for decl in by_name.values() if decl.public}
        selected.update(name.strip() for name in (names or []) if name and name.strip())
        missing = sorted(selected - set(by_name))
        if missing:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "public_statement_root_missing",
                    "Selected public closure root does not exist in the current Content node.",
                    object_ref=node_path,
                    current=", ".join(missing),
                )
            )
        return self.runtime.foundation.ok(
            [
                DeclRef(node=node_path, name=name, revision=by_name[name].current_revision)
                for name in sorted(selected)
            ]
        )

    def _scope_roots(
        self,
        repo_root: Path,
        *,
        scope_path: str,
        roots: list[DeclRef] | None,
        visible: bool,
    ) -> ServiceResult[list[DeclRef]]:
        contract = (
            self.runtime.node.contract.get_visible_contract(repo_root, node_path=scope_path)
            if visible
            else self.runtime.node.contract.get_current_contract(repo_root, node_path=scope_path)
        )
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        if contract.value.node_kind != NodeKind.SCOPE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "public_statement_boundary_not_scope",
                    "Scope/repo public Statement closure requires a Scope node.",
                    object_ref=scope_path,
                )
            )
        selected = list(contract.value.contract.exports)
        root_issues: list[ServiceIssue] = []
        for root in roots or []:
            if root.repo is not None:
                root_issues.append(
                    self.runtime.foundation.issue(
                        "public_statement_root_cross_repo",
                        "Scope closure roots must belong to the current repository.",
                        object_ref=f"{root.repo}:{root.node}:{root.name}",
                    )
                )
                continue
            selected_root = root if visible else self._current_ref(repo_root, root)
            visible_from_child = self._scope_root_visible_from_direct_child(
                repo_root,
                scope_path=scope_path,
                ref=selected_root,
            )
            if not visible_from_child.ok:
                root_issues.extend(visible_from_child.issues)
                continue
            if not visible_from_child.value:
                root_issues.append(
                    self.runtime.foundation.issue(
                        "public_statement_scope_root_not_child_public",
                        "A Scope closure root must already be public through one direct child boundary.",
                        object_ref=f"{root.node}:{root.name}",
                        details={"scope_path": scope_path},
                    )
                )
                continue
            selected.append(selected_root)
        if root_issues:
            return self.runtime.foundation.fail(self._unique_issues(root_issues))
        return self.runtime.foundation.ok(self._unique_refs(selected))

    def _scope_root_visible_from_direct_child(
        self,
        repo_root: Path,
        *,
        scope_path: str,
        ref: DeclRef,
    ) -> ServiceResult[bool]:
        children = self.runtime.node.node_tree.list_children(repo_root, scope_path=scope_path)
        if not children.ok or children.value is None:
            return self.runtime.foundation.fail(children.issues)
        child = next(
            (
                candidate
                for candidate in children.value
                if ref.node == candidate.path or ref.node.startswith(f"{candidate.path}.")
            ),
            None,
        )
        if child is None:
            return self.runtime.foundation.ok(False)
        if child.kind == NodeKind.SCOPE:
            return self._scope_exports_ref(
                repo_root,
                scope_path=child.path,
                ref=ref,
                visible=True,
            )
        if ref.node != child.path:
            return self.runtime.foundation.ok(False)
        public = self.runtime.node.export.contains_committed_content_public_decl(
            repo_root,
            node_path=child.path,
            ref=ref,
        )
        if not public.ok or public.value is None:
            return self.runtime.foundation.fail(public.issues)
        return public

    def _load_exact_decl(
        self,
        repo_root: Path,
        ref: DeclRef,
    ) -> ServiceResult[tuple[Decl, DeclRef]]:
        decl = self.runtime.decl_graph.get_decl(
            repo_root,
            node_path=ref.node,
            name=ref.name,
        )
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        if decl.value.lifecycle != DeclLifecycle.ACTIVE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "public_statement_decl_not_active",
                    "Public Statement closure can only use active declarations.",
                    object_ref=f"{ref.node}:{ref.name}",
                )
            )
        revision = self.runtime.decl_graph.get_decl_revision(
            repo_root,
            node_path=ref.node,
            name=ref.name,
            revision=ref.revision,
        )
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        if revision.value.status != DeclRevisionStatus.COMMITTED:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "public_statement_revision_not_committed",
                    "Public Statement closure requires the referenced declaration revision to be committed.",
                    object_ref=f"{ref.node}:{ref.name}@{ref.revision}",
                    current=revision.value.status.value,
                    expected=DeclRevisionStatus.COMMITTED.value,
                )
            )
        return self.runtime.foundation.ok(
            (
                decl.value,
                DeclRef(
                    node=decl.value.node_path,
                    name=decl.value.name,
                    revision=ref.revision,
                ),
            )
        )

    def _load_current_decl(
        self,
        repo_root: Path,
        ref: DeclRef,
    ) -> ServiceResult[tuple[Decl, DeclRef]]:
        decl = self.runtime.decl_graph.get_decl(
            repo_root,
            node_path=ref.node,
            name=ref.name,
        )
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        if decl.value.lifecycle != DeclLifecycle.ACTIVE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "public_statement_decl_not_active",
                    "Public Statement closure can only use active declarations.",
                    object_ref=f"{ref.node}:{ref.name}",
                )
            )
        revision = self.runtime.decl_graph.get_decl_revision(
            repo_root,
            node_path=ref.node,
            name=ref.name,
            revision=decl.value.current_revision,
        )
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        if revision.value.status != DeclRevisionStatus.COMMITTED:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "public_statement_revision_not_committed",
                    "Public Statement closure requires the current declaration revision to be committed.",
                    object_ref=f"{ref.node}:{ref.name}",
                    current=revision.value.status.value,
                    expected=DeclRevisionStatus.COMMITTED.value,
                )
            )
        return self.runtime.foundation.ok(
            (
                decl.value,
                DeclRef(
                    node=decl.value.node_path,
                    name=decl.value.name,
                    revision=decl.value.current_revision,
                ),
            )
        )

    def _external_public(self, repo_root: Path, ref: DeclRef) -> bool:
        if ref.repo is None:
            return False
        config = self.runtime.repo_workspace.metadata.get_repo_config(repo_root)
        if not config.ok or config.value is None:
            return False
        resolved = self.runtime.decl_graph.ref_compatibility.resolve_public_decl_ref(
            repo_root,
            ref=ref,
            required_availability=proof_availability_for_completion_mode(
                config.value.config.completion_mode
            ),
        )
        return bool(
            resolved.ok
            and resolved.value is not None
            and resolved.value.compatible
        )

    def _required_export_scopes(
        self,
        provider_node: str,
        options: _InspectionOptions,
    ) -> list[str]:
        if options.boundary == PublicStatementClosureBoundary.CONTENT:
            return []
        boundary = options.node_path or "Main"
        if provider_node == boundary or not provider_node.startswith(f"{boundary}."):
            return []
        parts = provider_node.split(".")
        boundary_parts = boundary.split(".")
        return [
            ".".join(parts[:depth])
            for depth in range(len(parts) - 1, len(boundary_parts) - 1, -1)
        ]

    def _scope_exports_ref(
        self,
        repo_root: Path,
        *,
        scope_path: str,
        ref: DeclRef,
        visible: bool,
    ) -> ServiceResult[bool]:
        contract = (
            self.runtime.node.contract.get_visible_contract(repo_root, node_path=scope_path)
            if visible
            else self.runtime.node.contract.get_current_contract(repo_root, node_path=scope_path)
        )
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        for exported in contract.value.contract.exports:
            if self._identity(exported) != self._identity(ref):
                continue
            if exported.revision == ref.revision:
                return self.runtime.foundation.ok(True)
            expected = self._resolve_semantic_ref(repo_root, ref)
            candidate = self._resolve_semantic_ref(repo_root, exported)
            if not expected.ok or expected.value is None:
                return self.runtime.foundation.fail(expected.issues)
            if not candidate.ok or candidate.value is None:
                return self.runtime.foundation.fail(candidate.issues)
            if (
                expected.value.compatible
                and candidate.value.compatible
                and expected.value.resolved_revision
                == candidate.value.resolved_revision
            ):
                return self.runtime.foundation.ok(True)
        return self.runtime.foundation.ok(False)

    def _resolve_semantic_ref(
        self,
        repo_root: Path,
        ref: DeclRef,
    ):
        config = self.runtime.repo_workspace.metadata.get_repo_config(repo_root)
        if not config.ok or config.value is None:
            return self.runtime.foundation.fail(config.issues)
        return self.runtime.decl_graph.ref_compatibility.resolve_decl_ref(
            repo_root,
            ref=ref,
            required_availability=proof_availability_for_completion_mode(
                config.value.config.completion_mode
            ),
        )

    def _current_ref(self, repo_root: Path, ref: DeclRef) -> DeclRef:
        if ref.repo is not None:
            return ref
        decl = self.runtime.decl_graph.get_decl(
            repo_root,
            node_path=ref.node,
            name=ref.name,
        )
        if decl.ok and decl.value is not None:
            return ref.model_copy(update={"revision": decl.value.current_revision})
        return ref

    def _gate(
        self,
        name: str,
        report: ServiceResult[PublicStatementClosureReport],
    ) -> ServiceResult[GateReport]:
        if not report.ok or report.value is None:
            return self.runtime.foundation.fail(report.issues)
        if report.value.closure_complete:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_passed(name, summary=report.value.summary)
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_failed(
                name,
                report.value.issues,
                summary=report.value.summary,
            )
        )

    def _unsafe_issues(
        self,
        report: PublicStatementClosureReport,
    ) -> list[ServiceIssue]:
        return [
            issue
            for issue in report.issues
            if issue.kind not in self._REPAIRABLE_ISSUES
        ]

    @staticmethod
    def _identity(ref: DeclRef) -> tuple[str | None, str, str]:
        return (ref.repo, ref.node, ref.name)

    @staticmethod
    def _ref_sort_key(ref: DeclRef) -> tuple[str, str, str, int]:
        return (ref.repo or "", ref.node, ref.name, ref.revision)

    def _unique_refs(self, refs: list[DeclRef]) -> list[DeclRef]:
        unique: dict[tuple[str | None, str, str], DeclRef] = {}
        for ref in refs:
            unique[self._identity(ref)] = ref
        return list(unique.values())

    @staticmethod
    def _ref_key(ref: DeclRef) -> tuple[str | None, str, str, int]:
        return (ref.repo, ref.node, ref.name, ref.revision)

    @staticmethod
    def _unique_issues(issues: list[ServiceIssue]) -> list[ServiceIssue]:
        unique: dict[tuple[str, str | None, str | None], ServiceIssue] = {}
        for issue in issues:
            unique[(issue.kind, issue.object_ref, issue.field)] = issue
        return list(unique.values())


__all__ = [
    "DeclVisibilityRevisionReceipt",
    "PublicStatementClosureBoundary",
    "PublicStatementClosureComponent",
    "PublicStatementClosureDecl",
    "PublicStatementClosureReport",
    "PublicStatementPromotionReceipt",
]
