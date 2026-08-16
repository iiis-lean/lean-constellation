"""Audit reports and gate-gap records for admin/debug workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import Field

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.services.foundation import FoundationContext, GateReport, IssueSeverity, MutationSummaryView, ServiceResult
from lean_constellation.services.validation_snapshot.consistency_check import ConsistencyCheckComponent
from lean_constellation.services.validation_snapshot.readiness_gate import ReadinessGateComponent

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


AuditScope = Literal["repo", "round", "gate_gap"]


class AuditFinding(StrictModel):
    kind: str
    severity: IssueSeverity = IssueSeverity.ERROR
    object_ref: str | None = None
    message: str
    suggested_action: str | None = None


class AuditReport(StrictModel):
    audit_name: str
    passed: bool
    findings: list[AuditFinding] = Field(default_factory=list)
    checked_items: list[str] = Field(default_factory=list)
    summary: str


class GateGapRecord(StrictModel):
    source: str
    description: str
    suggested_gate: str | None = None
    recorded_at: str


class DeclGraphAuditProvider(Protocol):
    """Provider for DeclGraph audits that are not implemented in this service layer."""

    def run_round_local_audit(self, repo_root: Path, *, node_path: str, round_id: str, stage: str) -> ServiceResult[AuditReport]:
        ...


class _MissingDeclGraphAuditProvider:
    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def run_round_local_audit(self, repo_root: Path, *, node_path: str, round_id: str, stage: str) -> ServiceResult[AuditReport]:
        del repo_root
        return self.runtime.foundation.ok(
            AuditReport(
                audit_name="round_local_audit",
                passed=False,
                checked_items=[f"{node_path}:{round_id}:{stage}"],
                findings=[
                    AuditFinding(
                        kind="decl_graph_audit_provider_missing",
                        object_ref=f"{node_path}:{round_id}",
                        message="No DeclGraph audit provider is configured for round-local audits.",
                        suggested_action="Inject a DeclGraph audit provider before running round-local audit.",
                    )
                ],
                summary="DeclGraph round-local audit provider is missing.",
            )
        )


class AuditComponent:
    """Admin-oriented audit aggregation."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        consistency: ConsistencyCheckComponent | None = None,
        readiness_gate: ReadinessGateComponent | None = None,
        decl_graph_provider: DeclGraphAuditProvider | None = None,
    ) -> None:
        self.runtime = runtime
        self.consistency = consistency or ConsistencyCheckComponent(runtime)
        self.readiness_gate = readiness_gate or ReadinessGateComponent(runtime, consistency=self.consistency)
        self.decl_graph_provider = decl_graph_provider or _MissingDeclGraphAuditProvider(runtime)

    def run_round_local_audit(self, repo_root: Path, *, node_path: str, round_id: str, stage: str) -> ServiceResult[AuditReport]:
        return self.decl_graph_provider.run_round_local_audit(Path(repo_root), node_path=node_path, round_id=round_id, stage=stage)

    def record_gate_gap(
        self,
        repo_root: Path,
        *,
        source: str,
        description: str,
        suggested_gate: str | None = None,
    ) -> ServiceResult[MutationSummaryView]:
        if not source or not source.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("gate_gap_source_required", "Gate gap source is required.", field="source"))
        if not description or not description.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("gate_gap_description_required", "Gate gap description is required.", field="description")
            )
        record = GateGapRecord(
            source=source.strip(),
            description=description.strip(),
            suggested_gate=suggested_gate.strip() if suggested_gate else None,
            recorded_at=utc_now_iso(),
        )
        path = self._gate_gap_path(Path(repo_root))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.model_dump(mode="json"), sort_keys=True) + "\n")
        except OSError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("gate_gap_write_failed", f"Failed to write gate gap record: {exc}", object_ref=str(path))
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.mutation_view(
                object_ref=str(path),
                changed=True,
                summary=f"Recorded gate gap from {record.source}.",
                changed_items=[record.suggested_gate or record.source],
            )
        )

    def run_repo_ready_audit(self, repo_root: Path) -> ServiceResult[AuditReport]:
        checks: list[GateReport] = []
        for result in [
            self.consistency.check_source_corpus_consistency(Path(repo_root)),
            self.consistency.check_source_index_consistency(Path(repo_root)),
            self.consistency.check_projection_sync(Path(repo_root), scope="repo"),
            self.readiness_gate.check_repo_ready(Path(repo_root), summary="Audit repo ready check."),
        ]:
            if not result.ok or result.value is None:
                return self.runtime.foundation.fail(result.issues)
            checks.append(result.value)
        findings: list[AuditFinding] = []
        for report in checks:
            for issue in report.issues:
                if issue.severity == IssueSeverity.INFO:
                    continue
                findings.append(
                    AuditFinding(
                        kind=issue.kind,
                        severity=issue.severity,
                        object_ref=issue.object_ref,
                        message=issue.message,
                        suggested_action=issue.suggested_action,
                    )
                )
        passed = not any(self.runtime.foundation.result.is_error_issue(issue) for report in checks for issue in report.issues)
        return self.runtime.foundation.ok(
            AuditReport(
                audit_name="repo_ready_audit",
                passed=passed,
                findings=findings,
                checked_items=[report.gate_name for report in checks],
                summary=("Repo ready audit passed." if passed else f"Repo ready audit found {len(findings)} findings."),
            )
        )

    def _gate_gap_path(self, repo_root: Path) -> Path:
        return self.runtime.foundation.layout.constellation_root(FoundationContext(repo_root=repo_root)) / "audit" / "gate_gaps.jsonl"
