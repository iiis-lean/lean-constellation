"""DeclGraph readiness checks and protocol-backed provider adapters."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import ProofAvailability
from lean_constellation.services.decl_graph.decl_catalog import DeclCatalogComponent
from lean_constellation.services.decl_graph.dependency import DeclDependencyComponent
from lean_constellation.services.decl_graph.availability_policy import (
    is_theorem_like,
    required_check_stage,
    required_state_for_availability,
)
from lean_constellation.services.decl_graph.models import (
    DeclLifecycle,
    DeclFileFormalView,
    DeclFileNaturalLanguageView,
    DeclFileRevisionView,
    DeclFileStageView,
    DeclReadinessReason,
    DeclReadinessReport,
    Decl,
    DeclRevision,
    DeclRevisionStatus,
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

    _DECLARED_OR_HIGHER = {DeclState.DECLARED, DeclState.PROOF_PLANNED, DeclState.PROVED}

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
        target = self._coerce_policy_target(policy, default=ProofAvailability.PROVED)
        if not target.ok or target.value is None:
            return self.runtime.foundation.fail(target.issues)
        return self._check_decl_proof_policy_satisfied(
            Path(repo_root),
            node_path=node_path,
            decl_name=decl_name,
            target_proof_availability=target.value,
            stack=[],
        )

    def check_decl_proof_policy_satisfied(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        target_proof_availability: ProofAvailability | str | None = None,
    ) -> ServiceResult[DeclReadinessReport]:
        target = self._resolve_target_proof_availability(Path(repo_root), target_proof_availability)
        if not target.ok or target.value is None:
            return self.runtime.foundation.fail(target.issues)
        return self._check_decl_proof_policy_satisfied(
            Path(repo_root),
            node_path=node_path,
            decl_name=decl_name,
            target_proof_availability=target.value,
            stack=[],
        )

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        decls = self.decl_catalog.list_decls(Path(repo_root), node_path=node_path)
        if not decls.ok or decls.value is None:
            return self.runtime.foundation.fail(decls.issues)
        public_decls: list[DeclPublicView] = []
        warnings: list[ServiceIssue] = []
        for decl in decls.value:
            if decl.lifecycle != DeclLifecycle.ACTIVE or not decl.public:
                continue
            satisfied = self.check_decl_proof_policy_satisfied(Path(repo_root), node_path=node_path, decl_name=decl.name)
            if not satisfied.ok or satisfied.value is None:
                return self.runtime.foundation.fail(satisfied.issues)
            if not satisfied.value.proof_policy_satisfied:
                warnings.append(
                    self.runtime.foundation.issue(
                        "public_decl_proof_policy_unsatisfied",
                        f"Public declaration does not satisfy current proof availability policy: {decl.name}",
                        severity=IssueSeverity.WARNING,
                        object_ref=f"{node_path}:{decl.name}",
                        details={
                            "reason": satisfied.value.reason.value if satisfied.value.reason is not None else "unknown",
                            **satisfied.value.details,
                        },
                    )
                )
            release_status = self.runtime.repo_workspace.release.get_decl_release_status(
                repo_root,
                node_path=node_path,
                decl_name=decl.name,
            )
            if not release_status.ok or release_status.value is None:
                return self.runtime.foundation.fail(release_status.issues)
            public_decls.append(
                DeclPublicView(
                    ref=DeclRef(repo=None, node=node_path, name=decl.name, revision=decl.current_revision),
                    resolved_revision=decl.current_revision,
                    resolution_reason="current_decl_revision",
                    kind=decl.kind,
                    module=decl.module,
                    summary=decl.summary,
                    public=True,
                    ready=satisfied.value.proof_policy_satisfied,
                    stale=self._is_stale_reason(satisfied.value.reason),
                    source="decl_graph",
                    released_state=release_status.value.released_state,
                    release_protected=release_status.value.release_protected,
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

    def save_statement_formal_capture(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
        lean_decl_name: str,
    ) -> ServiceResult[DeclFileRevisionView]:
        current = self._current_decl_and_revision(Path(repo_root), node_path=node_path, decl_name=decl_name)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        decl, revision = current.value
        if revision.status != "open":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_revision_not_open",
                    "Saving a statement formal capture requires the current revision to be open.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=revision.status.value,
                    expected="open",
                )
            )
        revision.statement_lean_code = code
        revision.statement_lean_check = check
        revision.lean_decl_name = lean_decl_name
        written = self._write_revision(Path(repo_root), node_path=node_path, decl_name=decl_name, revision=revision)
        if not written.ok or written.value is None:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(self._decl_file_revision_view(decl, written.value))

    def save_proof_formal_capture(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
        lean_decl_name: str,
    ) -> ServiceResult[DeclFileRevisionView]:
        current = self._current_decl_and_revision(Path(repo_root), node_path=node_path, decl_name=decl_name)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        decl, revision = current.value
        if not self._is_theorem_like(decl.kind):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_not_theorem_like",
                    "Saving a proof formal capture is only valid for theorem-like declarations.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=decl.kind,
                    expected="theorem-like kind",
                )
            )
        if revision.status != "open":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_revision_not_open",
                    "Saving a proof formal capture requires the current revision to be open.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=revision.status.value,
                    expected="open",
                )
            )
        revision.proof_lean_code = code
        revision.proof_lean_check = check
        revision.lean_decl_name = lean_decl_name
        written = self._write_revision(Path(repo_root), node_path=node_path, decl_name=decl_name, revision=revision)
        if not written.ok or written.value is None:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(self._decl_file_revision_view(decl, written.value))

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
        changes = self.runtime.decl_graph.list_round_changes(Path(repo_root), node_path=node_path, round_id=round_id)
        if not changes.ok or changes.value is None:
            return self.runtime.foundation.fail(changes.issues)
        for change in changes.value:
            if change.kind.value == "delete":
                delete_names.append(change.decl_name)
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

    def run_strict_proved_audit(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_names: list[str] | None = None,
    ) -> ServiceResult[AuditReport]:
        roots = self._strict_audit_roots(Path(repo_root), node_path=node_path, decl_names=decl_names)
        if not roots.ok or roots.value is None:
            return self.runtime.foundation.fail(roots.issues)
        findings: list[AuditFinding] = []
        checked: list[str] = []
        for decl_name in roots.value:
            checked.append(f"{node_path}:{decl_name}")
            report = self._check_decl_proof_policy_satisfied(
                Path(repo_root),
                node_path=node_path,
                decl_name=decl_name,
                target_proof_availability=ProofAvailability.PROVED,
                stack=[],
                provider_target_override=ProofAvailability.PROVED,
            )
            if not report.ok or report.value is None:
                return self.runtime.foundation.fail(report.issues)
            if not report.value.ready:
                findings.append(
                    AuditFinding(
                        kind="strict_proved_decl_not_satisfied",
                        object_ref=f"{node_path}:{decl_name}",
                        message=report.value.summary,
                        suggested_action="Prove this declaration and its theorem-like proof dependency closure.",
                    )
                )
        passed = not findings
        return self.runtime.foundation.ok(
            AuditReport(
                audit_name="strict_proved_audit",
                passed=passed,
                findings=findings,
                checked_items=checked,
                summary=("Strict proved audit passed." if passed else f"Strict proved audit found {len(findings)} findings."),
            )
        )

    def _strict_audit_roots(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_names: list[str] | None,
    ) -> ServiceResult[list[str]]:
        if decl_names is not None:
            roots = sorted({name.strip() for name in decl_names if name and name.strip()})
            if not roots:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("strict_proved_audit_empty", "At least one declaration name is required.")
                )
            return self.runtime.foundation.ok(roots)
        decls = self.decl_catalog.list_decls(repo_root, node_path=node_path)
        if not decls.ok or decls.value is None:
            return self.runtime.foundation.fail(decls.issues)
        return self.runtime.foundation.ok(
            sorted(decl.name for decl in decls.value if decl.lifecycle == DeclLifecycle.ACTIVE and decl.public)
        )

    def _check_decl_proof_policy_satisfied(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        target_proof_availability: ProofAvailability,
        stack: list[str],
        provider_target_override: ProofAvailability | None = None,
    ) -> ServiceResult[DeclReadinessReport]:
        stack_key = f"{Path(repo_root).name}:{node_path}:{decl_name}"
        if stack_key in stack:
            cycle = [*stack, stack_key]
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    reason=DeclReadinessReason.CYCLE_DETECTED,
                    target_proof_availability=target_proof_availability,
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
                    target_proof_availability=target_proof_availability,
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
                    target_proof_availability=target_proof_availability,
                    details={"lifecycle": decl.lifecycle.value},
                )
            )
        if revision.status != "committed":
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    revision=revision.revision,
                    reason=DeclReadinessReason.NO_ACTIVE_REVISION,
                    target_proof_availability=target_proof_availability,
                    details={"version_status": revision.status.value},
                )
            )
        required_state = self._required_state_for_target(decl.kind, target_proof_availability)
        if self._state_rank(revision.state) < self._state_rank(required_state):
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    revision=revision.revision,
                    reason=DeclReadinessReason.STATE_TOO_LOW,
                    target_proof_availability=target_proof_availability,
                    details={
                        "current_state": revision.state.value,
                        "required_state": required_state.value,
                        "target_proof_availability": target_proof_availability.value,
                    },
                )
            )
        stage = self._required_check_stage(decl.kind, target_proof_availability)
        check = revision.proof_lean_check if stage == "proof" else revision.statement_lean_check
        if check is None:
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    revision=revision.revision,
                    reason=DeclReadinessReason.LEAN_CHECK_FAILED,
                    target_proof_availability=target_proof_availability,
                    details={"stage": stage, "check": "missing", "target_proof_availability": target_proof_availability.value},
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
                    target_proof_availability=target_proof_availability,
                    details=check,
                )
            )

        checked: list[str] = []
        failed: list[str] = []
        dep_requirements = self.dependency.dependency_ref_requirements_for_proof_policy(
            decl,
            revision,
            target_proof_availability=target_proof_availability,
        )
        for dep_ref, dep_target in dep_requirements:
            dep_label = self._decl_ref_label(dep_ref, fallback_node_path=node_path)
            checked.append(dep_label)
            resolved_dep = self._resolve_dependency_ref(
                repo_root,
                ref=dep_ref,
                fallback_node_path=node_path,
                local_target=dep_target,
                provider_target_override=provider_target_override,
            )
            if not resolved_dep.ok or resolved_dep.value is None:
                failed.append(dep_label)
                return self.runtime.foundation.ok(
                    self._not_ready(
                        node_path=node_path,
                        decl_name=decl_name,
                        revision=revision.revision,
                        reason=DeclReadinessReason.DEPENDENCY_NOT_READY,
                        target_proof_availability=target_proof_availability,
                        details={
                            "dependency": dep_label,
                            "dependency_reason": "; ".join(issue.kind for issue in resolved_dep.issues) or "dependency resolution failed",
                            "dependency_required_proof_availability": dep_target.value,
                        },
                        dependencies_checked=checked,
                        failed_dependencies=failed,
                    )
                )
            dep_root, dep_node, effective_target = resolved_dep.value
            dep = self._check_decl_proof_policy_satisfied(
                dep_root,
                node_path=dep_node,
                decl_name=dep_ref.name,
                target_proof_availability=effective_target,
                provider_target_override=provider_target_override,
                stack=[*stack, stack_key],
            )
            if not dep.ok or dep.value is None:
                return self.runtime.foundation.fail(dep.issues)
            if not dep.value.ready:
                failed.append(dep_label)
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
                        target_proof_availability=target_proof_availability,
                        details={
                            "dependency": dep_label,
                            "dependency_reason": dep.value.reason.value if dep.value.reason else "unknown",
                            "dependency_required_proof_availability": effective_target.value,
                        },
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
                proof_policy_satisfied=True,
                target_proof_availability=target_proof_availability,
                dependencies_checked=checked,
                failed_dependencies=[],
                summary=(
                    f"Declaration {node_path}:{decl_name}@{revision.revision} satisfies "
                    f"{target_proof_availability.value} proof availability."
                ),
            )
        )

    def _resolve_dependency_ref(
        self,
        repo_root: Path,
        *,
        ref: DeclRef,
        fallback_node_path: str,
        local_target: ProofAvailability,
        provider_target_override: ProofAvailability | None = None,
    ) -> ServiceResult[tuple[Path, str, ProofAvailability]]:
        if ref.repo:
            try:
                provider_key = self.runtime.foundation.layout.ensure_safe_key(ref.repo)
            except ValueError as exc:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("dependency_provider_invalid", str(exc), object_ref=ref.repo)
                )
            provider_root = Path(repo_root).parent / provider_key
            config = self.runtime.repo_workspace.metadata.get_repo_config(provider_root)
            if not config.ok or config.value is None:
                return self.runtime.foundation.fail(config.issues)
            effective_target = provider_target_override or config.value.config.target_proof_availability
            compatible = self.runtime.decl_graph.ref_compatibility.resolve_public_decl_ref(
                repo_root,
                ref=ref,
                required_availability=effective_target,
            )
            if not compatible.ok or compatible.value is None:
                return self.runtime.foundation.fail(compatible.issues)
            if not compatible.value.compatible:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "dependency_decl_ref_incompatible",
                        "Cross-repo declaration dependency anchor is not compatible with the provider release head.",
                        object_ref=f"{provider_key}:{ref.node}:{ref.name}@{ref.revision}",
                        current=compatible.value.reason,
                    )
                )
            return self.runtime.foundation.ok((provider_root, ref.node, effective_target))
        dep_node = ref.node
        if dep_node == "Main" and fallback_node_path != "Main":
            dep_node = fallback_node_path
        if dep_node == fallback_node_path:
            local_decl = self.decl_catalog.get_decl(repo_root, node_path=dep_node, name=ref.name)
            local_revision = self.decl_catalog.get_decl_revision(
                repo_root,
                node_path=dep_node,
                name=ref.name,
                revision=ref.revision,
            )
            if (
                local_decl.ok
                and local_decl.value is not None
                and local_decl.value.lifecycle == DeclLifecycle.ACTIVE
                and local_decl.value.current_revision == ref.revision
                and local_revision.ok
                and local_revision.value is not None
                and local_revision.value.status == DeclRevisionStatus.COMMITTED
            ):
                return self.runtime.foundation.ok((Path(repo_root), dep_node, local_target))
        local_ref = ref.model_copy(update={"node": dep_node})
        compatible = self.runtime.decl_graph.ref_compatibility.resolve_decl_ref(
            repo_root,
            ref=local_ref,
            required_availability=local_target,
        )
        if not compatible.ok or compatible.value is None:
            return self.runtime.foundation.fail(compatible.issues)
        if not compatible.value.compatible:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "dependency_decl_ref_incompatible",
                    "Declaration dependency anchor is not compatible with the current contract head.",
                    object_ref=f"{dep_node}:{ref.name}@{ref.revision}",
                    current=compatible.value.reason,
                )
            )
        return self.runtime.foundation.ok((Path(repo_root), dep_node, local_target))

    def _decl_ref_label(self, ref: DeclRef, *, fallback_node_path: str) -> str:
        node = ref.node
        if ref.repo is None and node == "Main" and fallback_node_path != "Main":
            node = fallback_node_path
        if ref.repo:
            return f"{ref.repo}:{node}:{ref.name}"
        return ref.name if node == fallback_node_path else f"{node}:{ref.name}"

    def _resolve_target_proof_availability(
        self,
        repo_root: Path,
        target_proof_availability: ProofAvailability | str | None,
    ) -> ServiceResult[ProofAvailability]:
        if target_proof_availability is not None:
            return self._coerce_policy_target(str(target_proof_availability), default=ProofAvailability.PROVED)
        config = self.runtime.repo_workspace.metadata.get_repo_config(repo_root)
        if not config.ok or config.value is None:
            return self.runtime.foundation.fail(config.issues)
        return self.runtime.foundation.ok(config.value.config.target_proof_availability)

    def _coerce_policy_target(self, policy: str | None, *, default: ProofAvailability) -> ServiceResult[ProofAvailability]:
        if policy is None:
            return self.runtime.foundation.ok(default)
        normalized = str(policy).strip().lower()
        if normalized in {"declared", "declared_closure", ProofAvailability.DECLARED.value}:
            return self.runtime.foundation.ok(ProofAvailability.DECLARED)
        if normalized in {"proved", "proved_closure", "ready", ProofAvailability.PROVED.value}:
            return self.runtime.foundation.ok(ProofAvailability.PROVED)
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "proof_availability_policy_invalid",
                "Proof availability policy must be declared or proved.",
                current=str(policy),
                expected="declared | proved",
            )
        )

    def _required_state_for_target(self, kind: str, target_proof_availability: ProofAvailability) -> DeclState:
        return required_state_for_availability(kind, target_proof_availability)

    def _required_check_stage(self, kind: str, target_proof_availability: ProofAvailability) -> str:
        return required_check_stage(kind, target_proof_availability)

    def _current_decl_and_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
    ) -> ServiceResult[tuple[Decl, DeclRevision]]:
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

    def _write_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        revision: DeclRevision,
    ) -> ServiceResult[DeclRevision]:
        written = self.runtime.foundation.store.write_json_atomic(
            self.decl_catalog.graph_store.revision_path(
                repo_root,
                node_path=node_path,
                decl_name=decl_name,
                revision=revision.revision,
            ),
            revision,
            mode=WriteMode.UPDATE_EXISTING,
        )
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(revision)

    def _decl_file_revision_view(self, decl: Decl, revision: DeclRevision) -> DeclFileRevisionView:
        statement = DeclFileStageView(
            nl=DeclFileNaturalLanguageView(
                text=revision.statement_nl,
                origin=list(revision.statement.nl.origin) if revision.statement.nl is not None else [],
            ),
            formal=(
                DeclFileFormalView(code=revision.statement_lean_code, check=revision.statement.formal.check)
                if revision.statement.formal is not None
                else None
            ),
            deps=revision.statement_deps,
            dep_refs=list(revision.statement.deps),
        )
        proof = None
        if revision.proof is not None:
            proof = DeclFileStageView(
                nl=DeclFileNaturalLanguageView(
                    text=revision.proof_nl,
                    origin=list(revision.proof.nl.origin) if revision.proof.nl is not None else [],
                ),
                formal=(
                    DeclFileFormalView(code=revision.proof_lean_code, check=revision.proof.formal.check)
                    if revision.proof.formal is not None
                    else None
                ),
                deps=revision.proof_deps,
                dep_refs=list(revision.proof.deps),
            )
        return DeclFileRevisionView(
            decl_name=decl.name,
            revision=revision.revision,
            kind=decl.kind,
            state=revision.state,
            version_status=revision.status.value,
            module=decl.module,
            lean_decl_name=revision.lean_decl_name,
            statement=statement,
            proof=proof,
        )

    def _not_ready(
        self,
        *,
        node_path: str,
        decl_name: str,
        reason: DeclReadinessReason,
        revision: int | None = None,
        target_proof_availability: ProofAvailability | None = None,
        details: dict[str, str] | None = None,
        dependencies_checked: list[str] | None = None,
        failed_dependencies: list[str] | None = None,
    ) -> DeclReadinessReport:
        return DeclReadinessReport(
            node_path=node_path,
            decl_name=decl_name,
            revision=revision,
            ready=False,
            proof_policy_satisfied=False,
            target_proof_availability=target_proof_availability,
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

    def _truthy(self, value: str | None) -> bool:
        return value is not None and value.strip().lower() in {"1", "true", "yes", "passed"}

    def _is_theorem_like(self, kind: str) -> bool:
        return is_theorem_like(kind)

    def _state_rank(self, state: DeclState) -> int:
        return {
            DeclState.PLANNED: 0,
            DeclState.SPECIFIED: 1,
            DeclState.DECLARED: 2,
            DeclState.PROOF_PLANNED: 3,
            DeclState.PROVED: 4,
            DeclState.OBSOLETE: -1,
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
