"""Resolve and apply immutable per-run native repository specifications."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from lean_constellation.domain.interface import DeclInterface
from lean_constellation.domain.repo import ProofAvailability, RepoFormat, RepoPublicationStatus, RepoWorkMode
from lean_constellation.domain.repo_run import RepoRunSpec, SourceScope
from lean_constellation.services.foundation import GateReport, ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.repo_workspace.repo_metadata import RepoMetadataComponent
    from lean_constellation.services.repo_workspace.repo_preparation import RepoPreparationComponent
    from lean_constellation.services.repo_workspace.repo_release import RepoReleaseComponent
    from lean_constellation.services.runtime import LeanRuntimeServices


class RepoRunComponent:
    """Keep request defaults and lifecycle transition checks out of Flow code."""

    def __init__(self, runtime: LeanRuntimeServices, metadata: RepoMetadataComponent,
                 preparation: RepoPreparationComponent, release: RepoReleaseComponent) -> None:
        self.runtime = runtime
        self.metadata = metadata
        self.preparation = preparation
        self.release = release

    def resolve_initial_repo_run_spec(
        self, repo_root: Path, *, origin: Literal["main", "requirement_provider"] = "main",
        run_objective: str | None = None,
        target_proof_availability: ProofAvailability | None = None,
        work_mode: RepoWorkMode | None = None, source_scope: SourceScope | None = None,
        index_policy: Literal["auto", "update", "reuse"] | None = None,
        root_interface_policy: Literal["auto", "prepare", "reuse"] | None = None,
        additional_required_interfaces: Sequence[DeclInterface] = (),
    ) -> ServiceResult[RepoRunSpec]:
        prepared = self.preparation.get_preparation_input(repo_root)
        if not prepared.ok or prepared.value is None:
            return self.runtime.foundation.fail(prepared.issues)
        config = self.metadata.get_repo_config(repo_root)
        if not config.ok or config.value is None:
            return self.runtime.foundation.fail(config.issues)
        try:
            spec = RepoRunSpec(
                run_objective=run_objective or prepared.value.input.goal,
                target_proof_availability=target_proof_availability or config.value.config.target_proof_availability,
                work_mode=work_mode or config.value.config.work_mode,
                source_scope=source_scope or SourceScope(mode="all"),
                index_policy=index_policy or "auto",
                root_interface_policy=root_interface_policy or "auto",
                additional_required_interfaces=list(additional_required_interfaces),
            )
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("repo_run_config_invalid", str(exc)))
        if origin == "requirement_provider" and (
            spec.target_proof_availability != config.value.config.target_proof_availability
            or spec.work_mode != config.value.config.work_mode
        ):
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "provider_run_config_mismatch", "Provider initial run must use its aggregated requirement config."
            ))
        return self.runtime.foundation.ok(spec)

    def resolve_continuation_repo_run_spec(
        self, repo_root: Path, *, run_objective: str | None = None,
        target_proof_availability: ProofAvailability | None = None,
        work_mode: RepoWorkMode | None = None, source_scope: SourceScope | None = None,
        index_policy: Literal["auto", "update", "reuse"] | None = None,
        root_interface_policy: Literal["auto", "prepare", "reuse"] | None = None,
        additional_required_interfaces: Sequence[DeclInterface] = (),
    ) -> ServiceResult[RepoRunSpec]:
        if run_objective is None or not run_objective.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "continuation_objective_required", "A continuation requires an explicit run objective."
            ))
        config = self.metadata.get_repo_config(repo_root)
        if not config.ok or config.value is None:
            return self.runtime.foundation.fail(config.issues)
        try:
            spec = RepoRunSpec(
                run_objective=run_objective,
                target_proof_availability=target_proof_availability or config.value.config.target_proof_availability,
                work_mode=work_mode or config.value.config.work_mode,
                source_scope=source_scope or SourceScope(mode="none"),
                index_policy=index_policy or "auto",
                root_interface_policy=root_interface_policy or "auto",
                additional_required_interfaces=list(additional_required_interfaces),
            )
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("repo_run_config_invalid", str(exc)))
        return self.runtime.foundation.ok(spec)

    def validate_repo_run_transition(
        self, repo_root: Path, *, run_spec: RepoRunSpec,
        start_kind: Literal["initial", "continuation", "standalone_preprocess"], base_release_id: str | None,
    ) -> ServiceResult[GateReport]:
        findings = []
        repo_format = self.metadata.get_repo_format(repo_root)
        publication = self.metadata.get_repo_publication(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        if repo_format.value.repo_format != RepoFormat.NATIVE:
            findings.append(self.runtime.foundation.issue("repo_run_requires_native", "Native repo run requires native format."))
        interface_preview = self.preparation.preview_preparation_interface_append(
            repo_root, interfaces=run_spec.additional_required_interfaces
        )
        if not interface_preview.ok:
            findings.extend(interface_preview.issues)
        current = publication.value.publication
        if start_kind == "initial":
            if current.status != RepoPublicationStatus.DEVELOPING or current.latest_release_id is not None:
                findings.append(self.runtime.foundation.issue("initial_repo_state_invalid", "Initial run requires developing publication without a release."))
        elif current.latest_release_id is None:
            code = "stable_release_missing" if current.status == RepoPublicationStatus.STABLE else "initial_release_not_created"
            findings.append(self.runtime.foundation.issue(code, "Continuation requires a latest release baseline."))
        elif base_release_id != current.latest_release_id:
            findings.append(self.runtime.foundation.issue("base_release_changed", "Latest release differs from the bound continuation baseline."))
        else:
            base = self.release.get_release(repo_root, release_id=base_release_id)
            if not base.ok or base.value is None:
                findings.append(self.runtime.foundation.issue("release_baseline_corrupt", "The bound release cannot be read."))
            else:
                checkpoint = self.runtime.validation_snapshot.validate_repo_checkpoint_snapshot(
                    repo_root, snapshot_id=base.value.release.repo_checkpoint_id
                )
                if not checkpoint.ok or checkpoint.value is None:
                    findings.append(self.runtime.foundation.issue("release_baseline_corrupt", "The bound release checkpoint evidence cannot be read."))
                if _availability_rank(run_spec.target_proof_availability) < _availability_rank(base.value.release.target_proof_availability):
                    findings.append(self.runtime.foundation.issue("repo_run_target_downgrade", "Continuation cannot lower the released proof target."))
        source_index = self.runtime.material.source_index.get_source_index_model(Path(repo_root))
        if source_index.ok and source_index.value is not None and source_index.value.active_update_id is not None:
            findings.append(self.runtime.foundation.issue(
                "source_index_update_owned", "SourceIndex has an open update owned by another lifecycle Flow."
            ))
        report = (
            self.runtime.foundation.gate_failed("repo_run_transition", findings)
            if findings
            else self.runtime.foundation.gate_passed("repo_run_transition", "Repo run transition is valid.")
        )
        return self.runtime.foundation.ok(report)

    def apply_repo_run_config(self, repo_root: Path, *, run_spec: RepoRunSpec,
                              expected_base_release_id: str | None) -> ServiceResult[object]:
        publication = self.metadata.get_repo_publication(repo_root)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        current = publication.value.publication
        if current.status != RepoPublicationStatus.DEVELOPING:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("repo_not_developing", "Repo run config can only be applied while developing."))
        if current.latest_release_id != expected_base_release_id:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("base_release_changed", "Latest release changed before config application."))
        return self.metadata.update_repo_config(
            repo_root, target_proof_availability=run_spec.target_proof_availability, work_mode=run_spec.work_mode
        )


def _availability_rank(value: ProofAvailability) -> int:
    return {ProofAvailability.DECLARED: 0, ProofAvailability.PROVED: 1}[value]


__all__ = ["RepoRunComponent"]
