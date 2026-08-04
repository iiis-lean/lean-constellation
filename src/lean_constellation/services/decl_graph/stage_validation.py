"""Unified deterministic validation for one declaration round stage."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.decl_graph.models import DeclRevisionRef, DeclStage
from lean_constellation.services.decl_graph.proof_nl_validation import (
    validate_proof_deps,
    validate_proof_nl_candidate,
)
from lean_constellation.services.decl_graph.statement_nl_validation import (
    validate_statement_deps,
    validate_statement_nl_candidate,
)
from lean_constellation.services.foundation import ServiceIssue, ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class DeclStageValidationResult(StrictModel):
    """Narrow deterministic gate result for an exact round target batch."""

    round_id: str
    stage: DeclStage
    checked_revision_refs: list[DeclRevisionRef] = Field(default_factory=list)
    passed: bool
    issues: list[ServiceIssue] = Field(default_factory=list)
    timings_ms: dict[str, float] = Field(default_factory=dict)
    summary: str


def validate_round_stage_candidates(
    runtime: LeanRuntimeServices,
    repo_root: Path,
    *,
    node_path: str,
    round_id: str,
    stage: DeclStage | str,
    target_decl_names: list[str],
) -> ServiceResult[DeclStageValidationResult]:
    """Validate exact round revisions through the one shared stage gate."""

    stage_value = DeclStage(stage)
    round_record = runtime.decl_graph.get_round(
        repo_root,
        node_path=node_path,
        round_id=round_id,
    )
    if not round_record.ok or round_record.value is None:
        return runtime.foundation.fail(round_record.issues)
    refs_by_name = {ref.decl_name: ref for ref in round_record.value.revision_refs}
    checked_refs: list[DeclRevisionRef] = []
    issues: list[ServiceIssue] = []
    timings_ms: dict[str, float] = {
        "nl_origin": 0.0,
        "formal_sync_consistency": 0.0,
        "dependency_visibility_readiness": 0.0,
    }
    visibility_cache: dict[tuple[str, str], object] = {}
    for decl_name in target_decl_names:
        ref = refs_by_name.get(decl_name)
        if ref is None:
            issues.append(
                runtime.foundation.issue(
                    "decl_not_in_round",
                    "Stage target declaration is not part of the current round.",
                    object_ref=decl_name,
                    details={"round_id": round_id, "stage": stage_value.value},
                )
            )
            continue
        checked_refs.append(ref)
        revision_result = runtime.decl_graph.get_decl_revision(
            repo_root,
            node_path=node_path,
            name=decl_name,
            revision=ref.revision,
        )
        if not revision_result.ok or revision_result.value is None:
            issues.extend(revision_result.issues)
            continue
        revision = revision_result.value
        if stage_value == DeclStage.STATEMENT_NL:
            checked = validate_statement_nl_candidate(
                runtime,
                repo_root,
                node_path=node_path,
                round_id=round_id,
                decl_name=decl_name,
                timing_sink=timings_ms,
                visibility_cache=visibility_cache,
            )
        elif stage_value == DeclStage.PROOF_NL:
            checked = validate_proof_nl_candidate(
                runtime,
                repo_root,
                node_path=node_path,
                round_id=round_id,
                decl_name=decl_name,
                timing_sink=timings_ms,
                visibility_cache=visibility_cache,
            )
        else:
            formal_stage = "statement" if stage_value == DeclStage.STATEMENT_FORMAL else "proof"
            checked = _validate_formal_candidate(
                runtime,
                repo_root,
                node_path=node_path,
                round_id=round_id,
                decl_name=decl_name,
                formal_stage=formal_stage,
                revision=revision,
                timing_sink=timings_ms,
                visibility_cache=visibility_cache,
            )
        if not checked.ok:
            issues.extend(checked.issues)
    passed = not issues and len(checked_refs) == len(target_decl_names)
    return runtime.foundation.ok(
        DeclStageValidationResult(
            round_id=round_id,
            stage=stage_value,
            checked_revision_refs=checked_refs,
            passed=passed,
            issues=issues,
            timings_ms={key: round(value, 3) for key, value in timings_ms.items()},
            summary=(
                f"{stage_value.value} validation passed for {len(checked_refs)} declarations."
                if passed
                else f"{stage_value.value} validation failed for the current target batch."
            ),
        )
    )


def _validate_formal_candidate(
    runtime: LeanRuntimeServices,
    repo_root: Path,
    *,
    node_path: str,
    round_id: str,
    decl_name: str,
    formal_stage: str,
    revision,
    timing_sink: dict[str, float] | None = None,
    visibility_cache: dict[tuple[str, str], object] | None = None,
) -> ServiceResult[None]:
    section = revision.statement if formal_stage == "statement" else revision.proof
    formal = section.formal if section is not None else None
    issues: list[ServiceIssue] = []
    if formal is None or not (formal.code or "").strip():
        issues.append(
            runtime.foundation.issue(
                f"{formal_stage}_formal_candidate_missing",
                f"{formal_stage.title()} formal candidate is missing.",
                object_ref=decl_name,
            )
        )
    if formal is None or formal.check is None:
        issues.append(
            runtime.foundation.issue(
                f"{formal_stage}_formal_check_missing",
                f"{formal_stage.title()} formal Lean check is missing.",
                object_ref=decl_name,
            )
        )
    if formal_stage == "proof" and (revision.proof is None or revision.proof.nl is None or not (revision.proof.nl.text or "").strip()):
        issues.append(
            runtime.foundation.issue(
                "proof_nl_candidate_missing",
                "Proof Formal validation requires an accepted Proof NL route.",
                object_ref=decl_name,
            )
        )
    sync_started = perf_counter()
    sync = runtime.lean_projection.check_decl_file_snapshot_sync(
        repo_root,
        node_path=node_path,
        decl_name=decl_name,
        stage=formal_stage,
    )
    if not sync.ok or sync.value is None:
        issues.extend(sync.issues)
    elif not sync.value.passed:
        issues.extend(sync.value.issues)
    _record_timing(timing_sink, "formal_sync_consistency", sync_started)
    consistency_started = perf_counter()
    consistency = runtime.decl_graph.check_formal_stage_consistency(
        repo_root,
        node_path=node_path,
        decl_name=decl_name,
        stage=formal_stage,
    )
    if not consistency.ok or consistency.value is None:
        issues.extend(consistency.issues)
    elif not consistency.value.passed:
        issues.extend(consistency.value.issues)
    _record_timing(timing_sink, "formal_sync_consistency", consistency_started)
    dependencies_started = perf_counter()
    deps = section.deps if section is not None else []
    dependency_check = (
        validate_statement_deps(
            runtime,
            repo_root,
            node_path=node_path,
            round_id=round_id,
            decl_name=decl_name,
            deps=deps,
            visibility_cache=visibility_cache,
        )
        if formal_stage == "statement"
        else validate_proof_deps(
            runtime,
            repo_root,
            node_path=node_path,
            round_id=round_id,
            decl_name=decl_name,
            deps=deps,
            visibility_cache=visibility_cache,
        )
    )
    if not dependency_check.ok:
        issues.extend(dependency_check.issues)
    _record_timing(timing_sink, "dependency_visibility_readiness", dependencies_started)
    if issues:
        return runtime.foundation.fail(issues)
    return runtime.foundation.ok(None)


def _record_timing(sink: dict[str, float] | None, key: str, started: float) -> None:
    if sink is not None:
        sink[key] = sink.get(key, 0.0) + round((perf_counter() - started) * 1000, 3)


__all__ = ["DeclStageValidationResult", "validate_round_stage_candidates"]
