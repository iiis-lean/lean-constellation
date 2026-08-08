"""DeclGraph readiness checks and protocol-backed provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import (
    ProofAvailability,
    proof_availability_for_completion_mode,
    proof_availability_satisfies,
    RepoFormat,
)
from lean_constellation.domain.repo_release import (
    DeclAvailabilityEntry,
    DeclAvailabilityIndex,
)
from lean_constellation.domain.lean_check import compact_lean_check
from lean_constellation.services.decl_graph.decl_catalog import DeclCatalogComponent
from lean_constellation.services.decl_graph.dependency import DeclDependencyComponent
from lean_constellation.services.decl_graph.availability_policy import (
    is_theorem_like,
    required_check_stage,
    required_state_for_availability,
)
from lean_constellation.services.decl_graph.models import (
    DeclLifecycle,
    DeclFormalSection,
    DeclFileFormalView,
    DeclFileNaturalLanguageView,
    DeclFileRevisionView,
    DeclFileStageView,
    DeclReadinessBlocker,
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


@dataclass(frozen=True)
class _ReadinessKey:
    repo_root: str
    node_path: str
    decl_name: str
    revision: int | None
    required_availability: ProofAvailability
    provider_target_override: ProofAvailability | None
    round_overlay_id: int | None


@dataclass
class _ReadinessEvaluationContext:
    reports: dict[_ReadinessKey, DeclReadinessReport] = field(default_factory=dict)
    active: set[_ReadinessKey] = field(default_factory=set)
    current: dict[
        tuple[str, str, str, int | None],
        ServiceResult[tuple[Decl, DeclRevision]],
    ] = field(default_factory=dict)
    evaluated_state_count: int = 0


@dataclass(frozen=True)
class _ResolvedReadinessDependency:
    repo_root: Path
    node_path: str
    required_availability: ProofAvailability
    revision: int
    release_id: str | None = None


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
        batch = self.check_decl_proof_policy_batch(
            Path(repo_root),
            roots=[(node_path, decl_name, target.value)],
        )
        if not batch.ok or batch.value is None:
            return self.runtime.foundation.fail(batch.issues)
        return self.runtime.foundation.ok(batch.value[0])

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
        batch = self.check_decl_proof_policy_batch(
            Path(repo_root),
            roots=[(node_path, decl_name, target.value)],
        )
        if not batch.ok or batch.value is None:
            return self.runtime.foundation.fail(batch.issues)
        return self.runtime.foundation.ok(batch.value[0])

    def check_decl_proof_policy_batch(
        self,
        repo_root: Path,
        *,
        roots: Sequence[tuple[str, str, ProofAvailability]],
        provider_target_override: ProofAvailability | None = None,
        round_overlay: dict[str, tuple[Decl, DeclRevision]] | None = None,
    ) -> ServiceResult[list[DeclReadinessReport]]:
        """Evaluate multiple roots with one exact-state dependency traversal."""

        context = _ReadinessEvaluationContext()
        reports: list[DeclReadinessReport] = []
        for node_path, decl_name, target in roots:
            report = self._check_decl_proof_policy_satisfied(
                Path(repo_root),
                node_path=node_path,
                decl_name=decl_name,
                target_proof_availability=target,
                provider_target_override=provider_target_override,
                round_overlay=round_overlay,
                context=context,
            )
            if not report.ok or report.value is None:
                return self.runtime.foundation.fail(report.issues)
            reports.append(report.value)
        return self.runtime.foundation.ok(reports)

    def build_release_decl_availability_index(
        self,
        repo_root: Path,
    ) -> ServiceResult[DeclAvailabilityIndex]:
        """Derive a conservative Release lookup sidecar in one batch traversal."""

        repo_root = Path(repo_root)
        nodes = self.runtime.node.node_tree.node_store.list_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        repo_format = self.runtime.repo_workspace.metadata.get_repo_format(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        adapter = repo_format.value.repo_format == RepoFormat.ADAPTER
        declarations: list[Decl] = []
        revisions: dict[tuple[str, str], DeclRevision] = {}
        for node in sorted(nodes.value, key=lambda item: item.path):
            eligible_node = (
                node.path == "Main" and node.kind.value == "scope"
                if adapter
                else node.kind.value == "content"
            )
            if not eligible_node or node.lifecycle.value != "active":
                continue
            listed = self.decl_catalog.list_decls(repo_root, node_path=node.path)
            if not listed.ok or listed.value is None:
                return self.runtime.foundation.fail(listed.issues)
            for decl in listed.value:
                if (
                    decl.lifecycle != DeclLifecycle.ACTIVE
                    or (adapter and not decl.public)
                ):
                    continue
                revision = self.decl_catalog.get_decl_revision(
                    repo_root,
                    node_path=decl.node_path,
                    name=decl.name,
                    revision=decl.current_revision,
                )
                if not revision.ok or revision.value is None:
                    return self.runtime.foundation.fail(revision.issues)
                if revision.value.status != DeclRevisionStatus.COMMITTED:
                    continue
                declarations.append(decl)
                revisions[(decl.node_path, decl.name)] = revision.value
        roots = [
            (decl.node_path, decl.name, target)
            for decl in declarations
            for target in (ProofAvailability.DECLARED, ProofAvailability.PROVED)
        ]
        checked = self.check_decl_proof_policy_batch(repo_root, roots=roots)
        if not checked.ok or checked.value is None:
            return self.runtime.foundation.fail(checked.issues)
        reports = {
            (report.node_path, report.decl_name, report.required_availability): report
            for report in checked.value
        }
        main_contract = self.runtime.node.contract.get_visible_contract(
            repo_root,
            node_path="Main",
        )
        if not main_contract.ok or main_contract.value is None:
            return self.runtime.foundation.fail(main_contract.issues)
        exported = {
            (ref.node, ref.name, ref.revision)
            for ref in main_contract.value.contract.exports
        }
        entries: list[DeclAvailabilityEntry] = []
        for decl in sorted(declarations, key=lambda item: (item.node_path, item.name)):
            revision = revisions[(decl.node_path, decl.name)]
            proved = reports[(decl.node_path, decl.name, ProofAvailability.PROVED)]
            declared = reports[(decl.node_path, decl.name, ProofAvailability.DECLARED)]
            availability = (
                ProofAvailability.PROVED
                if proved.ready
                else ProofAvailability.DECLARED
                if declared.ready
                else None
            )
            if availability is None:
                continue
            entries.append(
                DeclAvailabilityEntry(
                    node=decl.node_path,
                    name=decl.name,
                    revision=revision.revision,
                    decl_state=revision.state.value,
                    availability=availability,
                    main_export=(
                        decl.node_path,
                        decl.name,
                        revision.revision,
                    )
                    in exported,
                )
            )
        return self.runtime.foundation.ok(DeclAvailabilityIndex(entries=entries))

    def check_round_decl_ready(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        required_availability: ProofAvailability | str,
    ) -> ServiceResult[DeclReadinessReport]:
        """Check an exact open round revision with same-round dependency overlay."""

        target = self._coerce_policy_target(str(required_availability), default=ProofAvailability.PROVED)
        if not target.ok or target.value is None:
            return self.runtime.foundation.fail(target.issues)
        round_record = self.runtime.decl_graph.get_round(
            repo_root,
            node_path=node_path,
            round_id=round_id,
        )
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        overlay: dict[str, tuple[Decl, DeclRevision]] = {}
        for ref in round_record.value.revision_refs:
            decl = self.decl_catalog.get_decl(repo_root, node_path=node_path, name=ref.decl_name)
            revision = self.decl_catalog.get_decl_revision(
                repo_root,
                node_path=node_path,
                name=ref.decl_name,
                revision=ref.revision,
            )
            if not decl.ok or decl.value is None:
                return self.runtime.foundation.fail(decl.issues)
            if not revision.ok or revision.value is None:
                return self.runtime.foundation.fail(revision.issues)
            overlay[ref.decl_name] = (decl.value, revision.value)
        batch = self.check_decl_proof_policy_batch(
            Path(repo_root),
            roots=[(node_path, decl_name, target.value)],
            round_overlay=overlay,
        )
        if not batch.ok or batch.value is None:
            return self.runtime.foundation.fail(batch.issues)
        return self.runtime.foundation.ok(batch.value[0])

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        decls = self.decl_catalog.list_decls(Path(repo_root), node_path=node_path)
        if not decls.ok or decls.value is None:
            return self.runtime.foundation.fail(decls.issues)
        active_public = [
            decl
            for decl in decls.value
            if decl.lifecycle == DeclLifecycle.ACTIVE and decl.public
        ]
        target = self._resolve_target_proof_availability(Path(repo_root), None)
        if not target.ok or target.value is None:
            return self.runtime.foundation.fail(target.issues)
        batch = self.check_decl_proof_policy_batch(
            Path(repo_root),
            roots=[(node_path, decl.name, target.value) for decl in active_public],
        )
        if not batch.ok or batch.value is None:
            return self.runtime.foundation.fail(batch.issues)
        public_decls: list[DeclPublicView] = []
        warnings: list[ServiceIssue] = []
        for decl, satisfied in zip(active_public, batch.value, strict=True):
            if not satisfied.ready:
                warnings.append(
                    self.runtime.foundation.issue(
                        "public_decl_proof_policy_unsatisfied",
                        f"Public declaration does not satisfy current proof availability policy: {decl.name}",
                        severity=IssueSeverity.WARNING,
                        object_ref=f"{node_path}:{decl.name}",
                        details={"reason": satisfied.blocker.reason.value if satisfied.blocker is not None else "unknown"},
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
                    ready=satisfied.ready,
                    stale=self._is_stale_reason(
                        satisfied.blocker.reason if satisfied.blocker is not None else None
                    ),
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
        revision.statement.formal = DeclFormalSection(code=code, check=check)
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
        proof = revision._ensure_proof()
        proof.formal = DeclFormalSection(code=code, check=check)
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
        public_decls = [
            decl
            for decl in decls.value
            if decl.lifecycle == DeclLifecycle.ACTIVE and decl.public
        ]
        readiness = self.check_decl_proof_policy_batch(
            Path(repo_root),
            roots=[(node_path, decl.name, ProofAvailability.PROVED) for decl in public_decls],
        )
        if not readiness.ok or readiness.value is None:
            return self.runtime.foundation.fail(readiness.issues)
        issues = [
            self._readiness_issue(report, kind="content_public_decl_not_ready")
            for report in readiness.value
            if not report.ready
        ]
        checked = len(public_decls)
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
        formal = (
            revision.statement.formal
            if normalized_stage == "statement"
            else revision.proof.formal
            if revision.proof is not None
            else None
        )
        code = formal.code if formal is not None else None
        check = compact_lean_check(formal.check) if formal is not None else None
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
        batch = self.check_decl_proof_policy_batch(
            Path(repo_root),
            roots=[
                (node_path, decl_name, ProofAvailability.PROVED)
                for decl_name in roots.value
            ],
            provider_target_override=ProofAvailability.PROVED,
        )
        if not batch.ok or batch.value is None:
            return self.runtime.foundation.fail(batch.issues)
        findings: list[AuditFinding] = []
        checked = [f"{node_path}:{decl_name}" for decl_name in roots.value]
        for decl_name, report in zip(roots.value, batch.value, strict=True):
            if not report.ready:
                findings.append(
                    AuditFinding(
                        kind="strict_proved_decl_not_satisfied",
                        object_ref=f"{node_path}:{decl_name}",
                        message=report.summary,
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
        provider_target_override: ProofAvailability | None = None,
        round_overlay: dict[str, tuple[Decl, DeclRevision]] | None = None,
        context: _ReadinessEvaluationContext | None = None,
    ) -> ServiceResult[DeclReadinessReport]:
        context = context or _ReadinessEvaluationContext()
        normalized_root = str(Path(repo_root).resolve())
        overlay_id = id(round_overlay) if round_overlay is not None else None
        overlay_pair = round_overlay.get(decl_name) if round_overlay is not None else None
        current_key = (normalized_root, node_path, decl_name, overlay_id)
        current = context.current.get(current_key)
        if current is None:
            current = (
                self.runtime.foundation.ok(overlay_pair)
                if overlay_pair is not None
                else self._current_decl_and_revision(repo_root, node_path=node_path, decl_name=decl_name)
            )
            context.current[current_key] = current
        revision_number = current.value[1].revision if current.ok and current.value is not None else None
        key = _ReadinessKey(
            repo_root=normalized_root,
            node_path=node_path,
            decl_name=decl_name,
            revision=revision_number,
            required_availability=target_proof_availability,
            provider_target_override=provider_target_override,
            round_overlay_id=overlay_id,
        )
        cached = context.reports.get(key)
        if cached is not None:
            return self.runtime.foundation.ok(cached)
        current_ref = DeclRef(node=node_path, name=decl_name, revision=revision_number or 1)
        if key in context.active:
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    reason=DeclReadinessReason.CYCLE_DETECTED,
                    required_availability=target_proof_availability,
                    revision=revision_number,
                    blocking_decl=current_ref,
                    dependency_chain=[current_ref],
                    message=f"Dependency cycle detected at {node_path}:{decl_name}.",
                )
            )
        context.active.add(key)
        context.evaluated_state_count += 1
        try:
            evaluated = self._evaluate_decl_proof_policy_state(
                Path(repo_root),
                node_path=node_path,
                decl_name=decl_name,
                target_proof_availability=target_proof_availability,
                provider_target_override=provider_target_override,
                round_overlay=round_overlay,
                context=context,
                current=current,
                overlay_pair=overlay_pair,
            )
        finally:
            context.active.discard(key)
        if not evaluated.ok or evaluated.value is None:
            return self.runtime.foundation.fail(evaluated.issues)
        context.reports[key] = evaluated.value
        return self.runtime.foundation.ok(evaluated.value)

    def _evaluate_decl_proof_policy_state(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        target_proof_availability: ProofAvailability,
        provider_target_override: ProofAvailability | None,
        round_overlay: dict[str, tuple[Decl, DeclRevision]] | None,
        context: _ReadinessEvaluationContext,
        current: ServiceResult[tuple[Decl, DeclRevision]],
        overlay_pair: tuple[Decl, DeclRevision] | None,
    ) -> ServiceResult[DeclReadinessReport]:
        if not current.ok or current.value is None:
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    reason=DeclReadinessReason.MISSING_DECL,
                    required_availability=target_proof_availability,
                    message=f"Declaration {node_path}:{decl_name} does not exist.",
                )
            )
        decl, revision = current.value
        current_ref = DeclRef(node=node_path, name=decl_name, revision=revision.revision)
        if decl.lifecycle != DeclLifecycle.ACTIVE:
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    revision=revision.revision,
                    reason=DeclReadinessReason.MISSING_DECL,
                    required_availability=target_proof_availability,
                    blocking_decl=current_ref,
                    message=f"Declaration {node_path}:{decl_name} is not active.",
                )
            )
        if overlay_pair is None and revision.status != "committed":
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    revision=revision.revision,
                    reason=DeclReadinessReason.NO_ACTIVE_REVISION,
                    required_availability=target_proof_availability,
                    blocking_decl=current_ref,
                    message=f"Declaration {node_path}:{decl_name}@{revision.revision} is not committed.",
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
                    required_availability=target_proof_availability,
                    blocking_decl=current_ref,
                    current_state=revision.state,
                    required_state=required_state,
                    message=f"{decl_name} is {revision.state.value}; {required_state.value} is required.",
                )
            )
        stage = self._required_check_stage(decl.kind, target_proof_availability)
        check = (
            revision.proof.formal.check
            if stage == "proof" and revision.proof is not None and revision.proof.formal is not None
            else revision.statement.formal.check
            if stage == "statement" and revision.statement.formal is not None
            else None
        )
        if check is None:
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    revision=revision.revision,
                    reason=DeclReadinessReason.LEAN_CHECK_FAILED,
                    required_availability=target_proof_availability,
                    blocking_decl=current_ref,
                    check_stage=stage,
                    message=f"{decl_name} has no accepted {stage} Lean check.",
                )
            )
        check_reason = self._lean_check_failure_reason(compact_lean_check(check) or {})
        if check_reason is not None:
            return self.runtime.foundation.ok(
                self._not_ready(
                    node_path=node_path,
                    decl_name=decl_name,
                    revision=revision.revision,
                    reason=check_reason,
                    required_availability=target_proof_availability,
                    blocking_decl=current_ref,
                    check_stage=stage,
                    message=f"{decl_name} has an unacceptable {stage} Lean check.",
                )
            )
        dep_requirements = self.dependency.dependency_ref_requirements_for_proof_policy(
            decl,
            revision,
            target_proof_availability=target_proof_availability,
        )
        for dep_ref, dep_target in dep_requirements:
            dep_node = dep_ref.node
            if dep_ref.repo is None and dep_node == "Main" and node_path != "Main":
                dep_node = node_path
            effective_ref = dep_ref.model_copy(update={"node": dep_node})
            if (
                dep_ref.repo is None
                and dep_node == node_path
                and round_overlay is not None
                and dep_ref.name in round_overlay
                and round_overlay[dep_ref.name][1].revision == dep_ref.revision
            ):
                dep = self._check_decl_proof_policy_satisfied(
                    repo_root,
                    node_path=node_path,
                    decl_name=dep_ref.name,
                    target_proof_availability=dep_target,
                    provider_target_override=provider_target_override,
                    round_overlay=round_overlay,
                    context=context,
                )
                if not dep.ok or dep.value is None:
                    return self.runtime.foundation.fail(dep.issues)
                if dep.value.ready:
                    continue
                return self.runtime.foundation.ok(
                    self._dependency_blocked(
                        node_path=node_path,
                        decl_name=decl_name,
                        revision=revision.revision,
                        required_availability=target_proof_availability,
                        dependency_ref=effective_ref,
                        dependency_required=dep_target,
                        dependency_report=dep.value,
                        chain=self._prefixed_dependency_chain(current_ref, dep.value),
                    )
                )
            resolved_dep = self._resolve_dependency_ref(
                repo_root,
                ref=dep_ref,
                fallback_node_path=node_path,
                local_target=dep_target,
                provider_target_override=provider_target_override,
            )
            if not resolved_dep.ok or resolved_dep.value is None:
                label = self._decl_ref_label(dep_ref, fallback_node_path=node_path)
                issue = resolved_dep.issues[0] if resolved_dep.issues else None
                message = (
                    issue.message
                    if issue is not None and issue.kind != "dependency_decl_ref_incompatible"
                    else f"Dependency {label} could not be resolved or its provider is not stable."
                )
                return self.runtime.foundation.ok(
                    self._not_ready(
                        node_path=node_path,
                        decl_name=decl_name,
                        revision=revision.revision,
                        reason=DeclReadinessReason.DEPENDENCY_NOT_READY,
                        required_availability=target_proof_availability,
                        blocking_decl=effective_ref,
                        dependency_required=dep_target,
                        dependency_chain=[current_ref],
                        message=message,
                    )
                )
            resolved = resolved_dep.value
            if resolved.release_id is not None:
                indexed = self.runtime.repo_workspace.release.lookup_decl_availability(
                    resolved.repo_root,
                    release_id=resolved.release_id,
                    node_path=resolved.node_path,
                    decl_name=dep_ref.name,
                    revision=resolved.revision,
                )
                if (
                    indexed.ok
                    and indexed.value is not None
                    and proof_availability_satisfies(
                        indexed.value.availability,
                        resolved.required_availability,
                    )
                ):
                    continue
            dep = self._check_decl_proof_policy_satisfied(
                resolved.repo_root,
                node_path=resolved.node_path,
                decl_name=dep_ref.name,
                target_proof_availability=resolved.required_availability,
                provider_target_override=provider_target_override,
                context=context,
            )
            if not dep.ok or dep.value is None:
                return self.runtime.foundation.fail(dep.issues)
            if not dep.value.ready:
                return self.runtime.foundation.ok(
                    self._dependency_blocked(
                        node_path=node_path,
                        decl_name=decl_name,
                        revision=revision.revision,
                        required_availability=target_proof_availability,
                        dependency_ref=effective_ref,
                        dependency_required=resolved.required_availability,
                        dependency_report=dep.value,
                        chain=self._prefixed_dependency_chain(current_ref, dep.value),
                    )
                )
        return self.runtime.foundation.ok(
            DeclReadinessReport(
                node_path=node_path,
                decl_name=decl_name,
                revision=revision.revision,
                required_availability=target_proof_availability,
                ready=True,
                summary=(
                    f"{node_path}:{decl_name}@{revision.revision} is available as "
                    f"{target_proof_availability.value}."
                ),
            )
        )

    @staticmethod
    def _prefixed_dependency_chain(
        current_ref: DeclRef,
        dependency_report: DeclReadinessReport,
    ) -> list[DeclRef]:
        nested = dependency_report.blocker
        nested_chain = list(nested.dependency_chain) if nested is not None else []
        return [current_ref, *nested_chain]

    def _resolve_dependency_ref(
        self,
        repo_root: Path,
        *,
        ref: DeclRef,
        fallback_node_path: str,
        local_target: ProofAvailability,
        provider_target_override: ProofAvailability | None = None,
    ) -> ServiceResult[_ResolvedReadinessDependency]:
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
            effective_target = provider_target_override or proof_availability_for_completion_mode(
                config.value.config.completion_mode
            )
            compatible = self.runtime.decl_graph.ref_compatibility.resolve_public_decl_ref(
                repo_root,
                ref=ref,
                required_availability=effective_target,
            )
            if not compatible.ok or compatible.value is None:
                return self.runtime.foundation.fail(compatible.issues)
            if not compatible.value.compatible:
                if compatible.value.reason == "state_too_low":
                    current_state = compatible.value.current_state or "missing"
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "dependency_provider_availability_insufficient",
                            (
                                f"{provider_key}:{ref.node}:{ref.name} is {current_state}; "
                                f"{effective_target.value} is required."
                            ),
                            object_ref=f"{provider_key}:{ref.node}:{ref.name}@{ref.revision}",
                            current=current_state,
                            expected=effective_target.value,
                        )
                    )
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "dependency_decl_ref_incompatible",
                        "Cross-repo declaration dependency anchor is not compatible with the provider release head.",
                        object_ref=f"{provider_key}:{ref.node}:{ref.name}@{ref.revision}",
                        current=compatible.value.reason,
                    )
                )
            repo_format = self.runtime.repo_workspace.metadata.get_repo_format(provider_root)
            if not repo_format.ok or repo_format.value is None:
                return self.runtime.foundation.fail(repo_format.issues)
            release_id = None
            if repo_format.value.repo_format == RepoFormat.NATIVE:
                publication = self.runtime.repo_workspace.metadata.get_repo_publication(provider_root)
                if not publication.ok or publication.value is None:
                    return self.runtime.foundation.fail(publication.issues)
                release_id = publication.value.publication.latest_release_id
            return self.runtime.foundation.ok(
                _ResolvedReadinessDependency(
                    repo_root=provider_root,
                    node_path=ref.node,
                    required_availability=effective_target,
                    revision=compatible.value.resolved_revision or ref.revision,
                    release_id=release_id,
                )
            )
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
                return self.runtime.foundation.ok(
                    _ResolvedReadinessDependency(
                        repo_root=Path(repo_root),
                        node_path=dep_node,
                        required_availability=local_target,
                        revision=ref.revision,
                    )
                )
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
        return self.runtime.foundation.ok(
            _ResolvedReadinessDependency(
                repo_root=Path(repo_root),
                node_path=dep_node,
                required_availability=local_target,
                revision=compatible.value.resolved_revision or ref.revision,
            )
        )

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
        return self.runtime.foundation.ok(
            proof_availability_for_completion_mode(
                config.value.config.completion_mode
            )
        )

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
                text=revision.statement.nl.text if revision.statement.nl is not None else None,
                origin=list(revision.statement.nl.origin) if revision.statement.nl is not None else [],
            ),
            formal=(
                DeclFileFormalView(code=revision.statement.formal.code, check=revision.statement.formal.check)
                if revision.statement.formal is not None
                else None
            ),
            deps=list(revision.statement.deps),
        )
        proof = None
        if revision.proof is not None:
            proof = DeclFileStageView(
                nl=DeclFileNaturalLanguageView(
                    text=revision.proof.nl.text if revision.proof.nl is not None else None,
                    origin=list(revision.proof.nl.origin) if revision.proof.nl is not None else [],
                ),
                formal=(
                    DeclFileFormalView(code=revision.proof.formal.code, check=revision.proof.formal.check)
                    if revision.proof.formal is not None
                    else None
                ),
                deps=list(revision.proof.deps),
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
        required_availability: ProofAvailability,
        revision: int | None = None,
        blocking_decl: DeclRef | None = None,
        current_state: DeclState | None = None,
        required_state: DeclState | None = None,
        check_stage: str | None = None,
        dependency_required: ProofAvailability | None = None,
        dependency_chain: list[DeclRef] | None = None,
        message: str,
    ) -> DeclReadinessReport:
        return DeclReadinessReport(
            node_path=node_path,
            decl_name=decl_name,
            revision=revision,
            required_availability=required_availability,
            ready=False,
            blocker=DeclReadinessBlocker(
                reason=reason,
                blocking_decl=blocking_decl,
                required_availability=dependency_required,
                current_state=current_state,
                required_state=required_state,
                check_stage=check_stage,  # type: ignore[arg-type]
                dependency_chain=dependency_chain or [],
                message=message,
            ),
            summary=f"{node_path}:{decl_name} is not ready: {message}",
        )

    def _dependency_blocked(
        self,
        *,
        node_path: str,
        decl_name: str,
        revision: int,
        required_availability: ProofAvailability,
        dependency_ref: DeclRef,
        dependency_required: ProofAvailability,
        dependency_report: DeclReadinessReport,
        chain: list[DeclRef],
    ) -> DeclReadinessReport:
        nested = dependency_report.blocker
        nested_reason = nested.reason if nested is not None else DeclReadinessReason.DEPENDENCY_NOT_READY
        reason = (
            DeclReadinessReason.CYCLE_DETECTED
            if nested_reason == DeclReadinessReason.CYCLE_DETECTED
            else DeclReadinessReason.DEPENDENCY_MISSING
            if nested_reason == DeclReadinessReason.MISSING_DECL
            else DeclReadinessReason.DEPENDENCY_NOT_READY
        )
        return self._not_ready(
            node_path=node_path,
            decl_name=decl_name,
            revision=revision,
            reason=reason,
            required_availability=required_availability,
            blocking_decl=nested.blocking_decl if nested is not None and nested.blocking_decl is not None else dependency_ref,
            current_state=nested.current_state if nested is not None else None,
            required_state=nested.required_state if nested is not None else None,
            check_stage=nested.check_stage if nested is not None else None,
            dependency_required=dependency_required,
            dependency_chain=chain,
            message=(
                nested.message
                if nested is not None
                else f"Dependency {dependency_ref.name} is not available as {dependency_required.value}."
            ),
        )

    def _readiness_issue(self, report: DeclReadinessReport, *, kind: str) -> ServiceIssue:
        blocker = report.blocker
        return self.runtime.foundation.issue(
            kind,
            report.summary,
            object_ref=f"{report.node_path}:{report.decl_name}",
            current=blocker.reason.value if blocker is not None else "not_ready",
            expected="ready",
            details={
                "blocking_decl": blocker.blocking_decl.model_dump_json(exclude_none=True)
                if blocker is not None and blocker.blocking_decl is not None
                else "",
                "message": blocker.message if blocker is not None else report.summary,
            },
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
