"""DeclGraph readiness checks and protocol-backed provider adapters."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.decl_graph.decl_catalog import DeclCatalogComponent
from lean_constellation.services.decl_graph.dependency import DeclDependencyComponent
from lean_constellation.services.decl_graph.models import (
    DeclLifecycle,
    DeclFileRevisionView,
    DeclReadinessReason,
    DeclReadinessReport,
    DeclRecord,
    DeclRevisionRecord,
    DeclState,
)
from lean_constellation.services.foundation import GateReport, IssueSeverity, ServiceIssue, ServiceResult, WriteMode
from lean_constellation.services.lean_projection.lean_check import LeanCheckView
from lean_constellation.services.node.export import DeclPublicView
from lean_constellation.services.validation_snapshot.audit import AuditFinding, AuditReport

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class DeclReadinessComponent:
    """Compute dynamic Decl readiness and satisfy cross-service provider protocols."""

    _THEOREM_LIKE_KINDS = {"theorem", "lemma", "proposition", "corollary"}
    _DECLARED_OR_HIGHER = {DeclState.DECLARED, DeclState.PROVED}

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        decl_catalog: DeclCatalogComponent,
        dependency: DeclDependencyComponent,
    ) -> None:
        self.runtime = runtime
        self.decl_catalog = decl_catalog
        self.dependency = dependency

    def check_decl_ready(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        policy: str | None = None,
    ) -> ServiceResult[DeclReadinessReport]:
        del policy
        return self._check_decl_ready(Path(repo_root), node_path=node_path, decl_name=decl_name, stack=[])

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        decls = self.decl_catalog.list_decls(Path(repo_root), node_path=node_path)
        if not decls.ok or decls.value is None:
            return self.runtime.foundation.fail(decls.issues)
        public_decls: list[DeclPublicView] = []
        warnings: list[ServiceIssue] = []
        for decl in decls.value:
            if decl.lifecycle != DeclLifecycle.ACTIVE or not decl.public:
                continue
            readiness = self.check_decl_ready(Path(repo_root), node_path=node_path, decl_name=decl.name)
            if not readiness.ok or readiness.value is None:
                return self.runtime.foundation.fail(readiness.issues)
            if not readiness.value.ready:
                warnings.append(
                    self.runtime.foundation.issue(
                        "public_decl_not_ready",
                        f"Public declaration is not ready: {decl.name}",
                        severity=IssueSeverity.WARNING,
                        object_ref=f"{node_path}:{decl.name}",
                        details={
                            "reason": readiness.value.reason.value if readiness.value.reason is not None else "unknown",
                            **readiness.value.details,
                        },
                    )
                )
            public_decls.append(
                DeclPublicView(
                    ref=DeclRef(repo=None, node=node_path, name=decl.name, revision=decl.current_revision),
                    kind=decl.kind,
                    summary=decl.summary,
                    public=True,
                    ready=readiness.value.ready,
                    stale=self._is_stale_reason(readiness.value.reason),
                    source="decl_graph",
                )
            )
        return self.runtime.foundation.ok(
            sorted(public_decls, key=lambda item: (item.ref.node, item.ref.name, item.ref.revision)),
            warnings=warnings,
        )

    def get_current_decl_revision(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[DeclFileRevisionView]:
        current = self._current_decl_and_revision(Path(repo_root), node_path=node_path, decl_name=decl_name)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        decl, revision = current.value
        return self.runtime.foundation.ok(self._decl_file_revision_view(decl, revision))

    def save_statement_formal_snapshot(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
    ) -> ServiceResult[DeclRevisionRecord]:
        current = self._current_decl_and_revision(Path(repo_root), node_path=node_path, decl_name=decl_name)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        _decl, revision = current.value
        if revision.version_status != "open":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_revision_not_open",
                    "Saving a formal statement snapshot requires the current revision to be open.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=revision.version_status,
                    expected="open",
                )
            )
        revision.statement_lean_code = code
        revision.statement_lean_check = self._lean_check_dict(check)
        revision.state = DeclState.DECLARED
        return self._write_revision(Path(repo_root), node_path=node_path, revision=revision)

    def save_proof_formal_snapshot(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
    ) -> ServiceResult[DeclRevisionRecord]:
        current = self._current_decl_and_revision(Path(repo_root), node_path=node_path, decl_name=decl_name)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        decl, revision = current.value
        if not self._is_theorem_like(decl.kind):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_not_theorem_like",
                    "Saving a proof formal snapshot is only valid for theorem-like declarations.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=decl.kind,
                    expected="theorem-like kind",
                )
            )
        if revision.version_status != "open":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_revision_not_open",
                    "Saving a proof snapshot requires the current revision to be open.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=revision.version_status,
                    expected="open",
                )
            )
        revision.proof_lean_code = code
        revision.proof_lean_check = self._lean_check_dict(check)
        revision.state = DeclState.PROVED
        return self._write_revision(Path(repo_root), node_path=node_path, revision=revision)

    def list_active_decl_names(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[str]]:
        decls = self.decl_catalog.list_decls(Path(repo_root), node_path=node_path)
        if not decls.ok or decls.value is None:
            return self.runtime.foundation.fail(decls.issues)
        return self.runtime.foundation.ok(sorted(decl.name for decl in decls.value if decl.lifecycle == DeclLifecycle.ACTIVE))

    def check_content_node_ready(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        decls = self.decl_catalog.list_decls(Path(repo_root), node_path=node_path)
        if not decls.ok or decls.value is None:
            return self.runtime.foundation.fail(decls.issues)
        issues: list[ServiceIssue] = []
        checked = 0
        for decl in decls.value:
            if decl.lifecycle != DeclLifecycle.ACTIVE or not decl.public:
                continue
            checked += 1
            readiness = self.check_decl_ready(Path(repo_root), node_path=node_path, decl_name=decl.name)
            if not readiness.ok or readiness.value is None:
                return self.runtime.foundation.fail(readiness.issues)
            if not readiness.value.ready:
                issues.append(self._readiness_issue(readiness.value, kind="content_public_decl_not_ready"))
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "content_decl_graph_readiness",
                    issues,
                    summary=f"{len(issues)} public declarations are not ready.",
                )
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "content_decl_graph_readiness",
                summary=f"Content DeclGraph readiness passed for {checked} public declarations.",
            )
        )

    def check_formal_stage_consistency(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: str,
    ) -> ServiceResult[GateReport]:
        current = self._current_decl_and_revision(Path(repo_root), node_path=node_path, decl_name=decl_name)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        decl, revision = current.value
        normalized_stage = stage.strip().lower()
        if normalized_stage not in {"statement", "proof"}:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "formal_stage_invalid",
                    "Formal stage must be statement or proof.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=stage,
                )
            )
        if normalized_stage == "proof" and not self._is_theorem_like(decl.kind):
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_passed(
                    "formal_stage_consistency",
                    summary=f"Proof formal consistency is skipped for non-theorem-like declaration {decl_name}.",
                )
            )
        code = revision.statement_lean_code if normalized_stage == "statement" else revision.proof_lean_code
        check = revision.statement_lean_check if normalized_stage == "statement" else revision.proof_lean_check
        issues = []
        if not code:
            issues.append(
                self.runtime.foundation.issue(
                    "formal_code_missing",
                    f"{normalized_stage} formal code is missing.",
                    object_ref=f"{node_path}:{decl_name}",
                    field=f"{normalized_stage}_lean_code",
                )
            )
        if check is None:
            issues.append(
                self.runtime.foundation.issue(
                    "formal_lean_check_missing",
                    f"{normalized_stage} Lean check is missing.",
                    object_ref=f"{node_path}:{decl_name}",
                    field=f"{normalized_stage}_lean_check",
                )
            )
        else:
            reason = self._lean_check_failure_reason(check)
            if reason is not None:
                issues.append(
                    self.runtime.foundation.issue(
                        reason.value,
                        f"{normalized_stage} Lean check is not acceptable.",
                        object_ref=f"{node_path}:{decl_name}",
                        field=f"{normalized_stage}_lean_check",
                        details=check,
                    )
                )
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "formal_stage_consistency",
                    issues,
                    summary=f"{normalized_stage} formal consistency failed for {decl_name}.",
                )
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "formal_stage_consistency",
                summary=f"{normalized_stage} formal consistency passed for {decl_name}.",
            )
        )

    def run_round_local_audit(self, repo_root: Path, *, node_path: str, round_id: str, stage: str) -> ServiceResult[AuditReport]:
        del stage
        gate = self.dependency.audit_round_dependencies(Path(repo_root), node_path=node_path, round_id=round_id)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        return self.runtime.foundation.ok(self._audit_report("round_local_audit", gate.value, [f"{node_path}:{round_id}"]))

    def run_delete_sanity_audit(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[AuditReport]:
        round_record = self.runtime.decl_graph.get_round(Path(repo_root), node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        delete_names: list[str] = []
        for change_id in round_record.value.change_ids:
            change = self.runtime.decl_graph.get_decl_change(Path(repo_root), node_path=node_path, change_id=change_id)
            if not change.ok or change.value is None:
                return self.runtime.foundation.fail(change.issues)
            if change.value.kind.value == "delete":
                delete_names.append(change.value.decl_name)
        if not delete_names:
            gate = self.runtime.foundation.gate_passed(
                "decl_delete_sanity",
                summary="No delete changes are present in this round.",
            )
        else:
            check = self.dependency.check_delete_preflight(Path(repo_root), node_path=node_path, decl_names=delete_names)
            if not check.ok or check.value is None:
                return self.runtime.foundation.fail(check.issues)
            gate = check.value
        return self.runtime.foundation.ok(self._audit_report("delete_sanity_audit", gate, [f"{node_path}:{round_id}"]))

    def _check_decl_ready(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stack: list[str],
    ) -> ServiceResult[DeclReadinessReport]:
        if decl_name in stack:
            cycle = [*stack, decl_name]
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    reason=DeclReadinessReason.CYCLE_DETECTED,
                    details={"cycle": " -> ".join(cycle)},
                )
            )

        current = self._current_decl_and_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not current.ok or current.value is None:
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    reason=DeclReadinessReason.MISSING_DECL,
                    details={"issues": "; ".join(issue.kind for issue in current.issues) or "missing"},
                )
            )
        decl, revision = current.value
        if decl.lifecycle != DeclLifecycle.ACTIVE:
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    revision=revision.revision,
                    reason=DeclReadinessReason.MISSING_DECL,
                    details={"lifecycle": decl.lifecycle.value},
                )
            )
        if revision.version_status != "committed":
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    revision=revision.revision,
                    reason=DeclReadinessReason.NO_ACTIVE_REVISION,
                    details={"version_status": revision.version_status},
                )
            )
        required_state = DeclState.PROVED if self._is_theorem_like(decl.kind) else DeclState.DECLARED
        if self._state_rank(revision.state) < self._state_rank(required_state):
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    revision=revision.revision,
                    reason=DeclReadinessReason.STATE_TOO_LOW,
                    details={"current_state": revision.state.value, "required_state": required_state.value},
                )
            )
        check = revision.proof_lean_check if self._is_theorem_like(decl.kind) else revision.statement_lean_check
        if check is None:
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    revision=revision.revision,
                    reason=DeclReadinessReason.LEAN_CHECK_FAILED,
                    details={"stage": "proof" if self._is_theorem_like(decl.kind) else "statement", "check": "missing"},
                )
            )
        check_reason = self._lean_check_failure_reason(check)
        if check_reason is not None:
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    revision=revision.revision,
                    reason=check_reason,
                    details=check,
                )
            )

        checked: list[str] = []
        failed: list[str] = []
        for dep_name in revision.decl_deps:
            checked.append(dep_name)
            dep = self._check_decl_ready(repo_root, node_path=node_path, decl_name=dep_name, stack=[*stack, decl_name])
            if not dep.ok or dep.value is None:
                return self.runtime.foundation.fail(dep.issues)
            if not dep.value.ready:
                failed.append(dep_name)
                reason = (
                    DeclReadinessReason.CYCLE_DETECTED
                    if dep.value.reason == DeclReadinessReason.CYCLE_DETECTED
                    else DeclReadinessReason.DEPENDENCY_MISSING
                    if dep.value.reason == DeclReadinessReason.MISSING_DECL
                    else DeclReadinessReason.DEPENDENCY_NOT_READY
                )
                return self.runtime.foundation.ok(
                    self._not_ready(
                        node_path=node_path,
                        decl_name=decl_name,
                        revision=revision.revision,
                        reason=reason,
                        details={"dependency": dep_name, "dependency_reason": dep.value.reason.value if dep.value.reason else "unknown"},
                        dependencies_checked=checked,
                        failed_dependencies=failed,
                    )
                )
        return self.runtime.foundation.ok(
            DeclReadinessReport(
                node_path=node_path,
                decl_name=decl_name,
                revision=revision.revision,
                ready=True,
                dependencies_checked=checked,
                failed_dependencies=[],
                summary=f"Declaration {node_path}:{decl_name}@{revision.revision} is ready.",
            )
        )

    def _current_decl_and_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
    ) -> ServiceResult[tuple[DeclRecord, DeclRevisionRecord]]:
        decl = self.decl_catalog.get_decl(repo_root, node_path=node_path, name=decl_name)
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        revision = self.decl_catalog.get_decl_revision(
            repo_root,
            node_path=node_path,
            name=decl_name,
            revision=decl.value.current_revision,
        )
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        return self.runtime.foundation.ok((decl.value, revision.value))

    def _write_revision(self, repo_root: Path, *, node_path: str, revision: DeclRevisionRecord) -> ServiceResult[DeclRevisionRecord]:
        written = self.runtime.foundation.store.write_json_atomic(
            self.decl_catalog.graph_store.revision_path(
                repo_root,
                node_path=node_path,
                decl_name=revision.decl_name,
                revision=revision.revision,
            ),
            revision,
            mode=WriteMode.UPDATE_EXISTING,
        )
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(revision)

    def _decl_file_revision_view(self, decl: DeclRecord, revision: DeclRevisionRecord) -> DeclFileRevisionView:
        statement: dict[str, object] = {
            "nl": {
                "text": revision.statement_nl,
                "origin": revision.statement_origin,
            },
            "deps": revision.statement_deps,
        }
        if revision.statement_lean_code is not None or revision.statement_lean_check is not None:
            statement["formal"] = {
                "code": revision.statement_lean_code,
                "check": revision.statement_lean_check,
            }
        proof: dict[str, object] = {
            "nl": {
                "text": revision.proof_nl,
                "origin": revision.proof_origin,
            },
            "deps": revision.proof_deps,
        }
        if revision.proof_lean_code is not None or revision.proof_lean_check is not None:
            proof["formal"] = {
                "code": revision.proof_lean_code,
                "check": revision.proof_lean_check,
            }
        return DeclFileRevisionView(
            decl_name=revision.decl_name,
            revision=revision.revision,
            kind=decl.kind,
            state=revision.state,
            version_status=revision.version_status,
            module=revision.module or decl.module,
            statement=statement,
            proof=proof,
            decl_deps=revision.decl_deps,
        )

    def _not_ready(
        self,
        *,
        node_path: str,
        decl_name: str,
        reason: DeclReadinessReason,
        revision: int | None = None,
        details: dict[str, str] | None = None,
        dependencies_checked: list[str] | None = None,
        failed_dependencies: list[str] | None = None,
    ) -> DeclReadinessReport:
        return DeclReadinessReport(
            node_path=node_path,
            decl_name=decl_name,
            revision=revision,
            ready=False,
            reason=reason,
            details={str(key): str(value) for key, value in (details or {}).items()},
            dependencies_checked=dependencies_checked or [],
            failed_dependencies=failed_dependencies or [],
            summary=f"Declaration {node_path}:{decl_name} is not ready: {reason.value}.",
        )

    def _readiness_issue(self, report: DeclReadinessReport, *, kind: str) -> ServiceIssue:
        return self.runtime.foundation.issue(
            kind,
            report.summary,
            object_ref=f"{report.node_path}:{report.decl_name}",
            current=report.reason.value if report.reason is not None else "not_ready",
            expected="ready",
            details=report.details,
        )

    def _lean_check_failure_reason(self, check: dict[str, str]) -> DeclReadinessReason | None:
        if self._truthy(check.get("contains_axiom")) or self._truthy(check.get("contains_admit")) or self._truthy(check.get("contains_opaque")) or self._truthy(check.get("contains_unsafe")):
            return DeclReadinessReason.CONTAINS_AXIOM_OR_UNSAFE
        if self._truthy(check.get("contains_sorry")) and not self._truthy(check.get("allow_sorry")):
            return DeclReadinessReason.CONTAINS_SORRY
        status = (check.get("status") or "").strip().lower()
        passed = check.get("passed")
        if status:
            return None if status == "passed" else DeclReadinessReason.LEAN_CHECK_FAILED
        if passed is not None:
            return None if self._truthy(passed) else DeclReadinessReason.LEAN_CHECK_FAILED
        return DeclReadinessReason.LEAN_CHECK_FAILED

    def _lean_check_dict(self, check: LeanCheckView) -> dict[str, str]:
        return {
            "status": check.status,
            "policy": check.policy,
            "allow_sorry": str(check.allow_sorry),
            "contains_sorry": str(check.contains_sorry),
            "contains_axiom": str(check.contains_axiom),
            "message": check.message,
        }

    def _truthy(self, value: str | None) -> bool:
        return value is not None and value.strip().lower() in {"1", "true", "yes", "passed"}

    def _is_theorem_like(self, kind: str) -> bool:
        return kind.strip().lower() in self._THEOREM_LIKE_KINDS

    def _state_rank(self, state: DeclState) -> int:
        return {
            DeclState.PLANNED: 0,
            DeclState.SPECIFIED: 1,
            DeclState.DECLARED: 2,
            DeclState.PROVED: 3,
        }[state]

    def _is_stale_reason(self, reason: DeclReadinessReason | None) -> bool:
        return reason in {DeclReadinessReason.NO_ACTIVE_REVISION, DeclReadinessReason.STALE_REVISION}

    def _audit_report(self, audit_name: str, gate: GateReport, checked_items: list[str]) -> AuditReport:
        findings = [
            AuditFinding(
                kind=issue.kind,
                severity=issue.severity,
                object_ref=issue.object_ref,
                message=issue.message,
                suggested_action=issue.suggested_action,
            )
            for issue in gate.issues
            if self.runtime.foundation.result.is_error_issue(issue)
        ]
        return AuditReport(
            audit_name=audit_name,
            passed=gate.passed,
            findings=findings,
            checked_items=checked_items,
            summary=gate.summary or audit_name,
        )
