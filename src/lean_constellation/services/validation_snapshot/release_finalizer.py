"""Native repo candidate release gates and publication transaction."""

from __future__ import annotations

import hashlib
import json
import shutil
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from agent_runtime_kit.flow.models import FlowStatus
from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.preparation import RepoDependencyRequirementStatus
from lean_constellation.domain.repo import (
    ProofAvailability,
    RepoFormat,
    RepoModel,
    RepoPublicationState,
    RepoPublicationStatus,
    RepoPublicationView,
    proof_availability_satisfies,
)
from lean_constellation.domain.repo_release import RepoRelease, RepoReleaseView
from lean_constellation.services.decl_graph.models import DeclRoundStatus, DeclStrategyStatus
from lean_constellation.services.external_clients import ToolchainCommandView
from lean_constellation.services.foundation import GateReport, MutationSummaryView, ServiceResult, WriteMode
from lean_constellation.services.foundation import FoundationContext
from lean_constellation.services.node import ContractVersionStatus, NodeKind, NodeLifecycle
from lean_constellation.services.material.source_index import SourceIndexSchemaCompatibilityView
from lean_constellation.services.validation_snapshot.snapshot_restore import (
    RepoCheckpointSnapshotView,
    SnapshotRestoreView,
)

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class CandidateReleaseGateView(StrictModel):
    base_release_id: str | None = None
    candidate_node_contract_versions: dict[str, int] = Field(default_factory=dict)
    target_proof_availability: ProofAvailability
    gate: GateReport
    blocking_issue_kinds: list[str] = Field(default_factory=list)
    summary: str


class PreparedRepoReleaseView(StrictModel):
    release: RepoRelease
    publication: RepoPublicationState
    candidate_digest: str
    build: ToolchainCommandView
    gate: GateReport
    summary: str


class CandidateReleasePreparationView(StrictModel):
    outcome: Literal["prepared", "blocked"]
    gate: GateReport
    build: ToolchainCommandView | None = None
    prepared_release: PreparedRepoReleaseView | None = None
    blocking_issue_kinds: list[str] = Field(default_factory=list)
    summary: str


class ProviderRequirementReconciliationView(StrictModel):
    release_id: str
    satisfied: list[str] = Field(default_factory=list)
    already_satisfied: list[str] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    summary: str


class RepoReleaseFinalizeView(StrictModel):
    release: RepoReleaseView
    checkpoint: RepoCheckpointSnapshotView
    publication: RepoPublicationView
    reconciliation: ProviderRequirementReconciliationView
    notification_pending: bool = False
    summary: str


class RepoReleaseStorageAuditView(StrictModel):
    passed: bool
    latest_release_id: str | None = None
    reachable_release_ids: list[str] = Field(default_factory=list)
    orphan_release_ids: list[str] = Field(default_factory=list)
    orphan_checkpoint_ids: list[str] = Field(default_factory=list)
    staging_paths: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    audit_digest: str
    summary: str


class LegacyContractHeadAdoptionView(StrictModel):
    node_path: str
    node_id: str
    current_contract_version: int
    adopted_contract_version: int
    decl_graph_head: dict[str, int] = Field(default_factory=dict)
    migration_required: bool
    summary: str


class LegacyStableAdoptionPreviewView(StrictModel):
    outcome: Literal["eligible", "blocked"]
    publication_source: Literal["repo_publication", "provider_ready", "default"]
    source_index: SourceIndexSchemaCompatibilityView | None = None
    contract_heads: list[LegacyContractHeadAdoptionView] = Field(default_factory=list)
    gate: GateReport
    build: ToolchainCommandView | None = None
    blocking_issue_kinds: list[str] = Field(default_factory=list)
    current_digest: str
    summary: str


class LegacyStableAdoptionView(StrictModel):
    outcome: Literal["eligible", "adopted", "blocked"]
    preview: LegacyStableAdoptionPreviewView
    pre_adoption_checkpoint_id: str | None = None
    finalized: RepoReleaseFinalizeView | None = None
    summary: str


class RepoReleaseFinalizerComponent:
    """Prepare and publish native releases without exposing partial latest truth."""

    _EXCLUDED_TOP_LEVEL = {".git", ".lake", ".agent_runtime", "__pycache__", ".pytest_cache"}
    _EXCLUDED_CONSTELLATION_CHILDREN = {"snapshots", ".locks"}

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def preview_candidate_release(
        self,
        repo_root: Path,
        *,
        base_release_id: str | None,
        summary: str,
        owner_flow_id: str | None = None,
        submission_intent_preview: bool = False,
    ) -> ServiceResult[CandidateReleaseGateView]:
        return self._preview_release(
            repo_root,
            base_release_id=base_release_id,
            summary=summary,
            owner_flow_id=owner_flow_id,
            legacy_adoption=False,
            submission_intent_preview=submission_intent_preview,
        )

    def _preview_release(
        self,
        repo_root: Path,
        *,
        base_release_id: str | None,
        summary: str,
        owner_flow_id: str | None = None,
        legacy_adoption: bool,
        submission_intent_preview: bool = False,
    ) -> ServiceResult[CandidateReleaseGateView]:
        repo_root = Path(repo_root)
        reports: list[GateReport] = []
        node_versions: dict[str, int] = {}

        base = (
            self._check_legacy_adoption_base(repo_root, summary=summary)
            if legacy_adoption
            else self._check_base(repo_root, base_release_id=base_release_id, summary=summary)
        )
        if not base.ok or base.value is None:
            return self.runtime.foundation.fail(base.issues)
        reports.append(base.value)

        workflow = self._check_workflow_closeout(
            repo_root,
            owner_flow_id=owner_flow_id,
            stable_hook=False,
            submission_intent_preview=submission_intent_preview,
        )
        if not workflow.ok or workflow.value is None:
            return self.runtime.foundation.fail(workflow.issues)
        reports.append(workflow.value)

        nodes = self.runtime.node.node_tree.node_store.list_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        node_issues = []
        graph_reports: list[GateReport] = []
        lake_dependencies = self.runtime.repo_workspace.workspace_catalog.list_current_lake_dependency_repos(repo_root)
        lake_dependency_names = {
            item.name for item in (lake_dependencies.value or [])
        } if lake_dependencies.ok else set()
        if not lake_dependencies.ok:
            reports.append(self.runtime.foundation.gate_failed(
                "release_lake_dependencies", lake_dependencies.issues,
                summary="Lake dependencies could not be parsed.",
            ))
        active_nodes = [node for node in nodes.value if node.lifecycle == NodeLifecycle.ACTIVE]
        active_by_path = {node.path: node for node in active_nodes}
        tree_issues = []
        if len(active_by_path) != len(active_nodes):
            tree_issues.append(self.runtime.foundation.issue(
                "node_active_path_duplicate", "Active Node tree contains duplicate paths."
            ))
        if len({node.node_id for node in active_nodes}) != len(active_nodes):
            tree_issues.append(self.runtime.foundation.issue(
                "node_id_duplicate", "Active Node tree contains duplicate node ids."
            ))
        main = active_by_path.get("Main")
        if main is None or main.kind != NodeKind.SCOPE:
            tree_issues.append(self.runtime.foundation.issue(
                "release_main_scope_invalid", "The active Node tree must contain Main as a Scope node."
            ))
        for node in active_nodes:
            if node.path == "Main":
                continue
            parent_path = node.path.rsplit(".", maxsplit=1)[0] if "." in node.path else None
            parent = active_by_path.get(parent_path or "")
            if parent is None:
                tree_issues.append(self.runtime.foundation.issue(
                    "node_parent_missing", "Active node parent is missing.", object_ref=node.path
                ))
            elif parent.kind != NodeKind.SCOPE:
                tree_issues.append(self.runtime.foundation.issue(
                    "node_parent_not_scope", "Active node parent is not a Scope.", object_ref=node.path
                ))
            if node.kind == NodeKind.CONTENT and any(
                other.path.startswith(f"{node.path}.") for other in active_nodes
            ):
                tree_issues.append(self.runtime.foundation.issue(
                    "content_node_has_children", "Active Content node has active descendants.", object_ref=node.path
                ))
        reports.append(
            self.runtime.foundation.gate_failed("release_node_tree", tree_issues)
            if tree_issues
            else self.runtime.foundation.gate_passed("release_node_tree", summary="Active Node tree invariants passed.")
        )
        for node in nodes.value:
            if node.lifecycle != NodeLifecycle.ACTIVE:
                continue
            if node.open_contract_version is not None:
                node_issues.append(self.runtime.foundation.issue(
                    "release_node_contract_open",
                    "All active Node contracts must be closed before release.",
                    object_ref=node.path,
                    current=str(node.open_contract_version),
                ))
            if node.active_contract_version is None:
                node_issues.append(self.runtime.foundation.issue(
                    "release_node_contract_uncommitted",
                    "Every active node must have a committed active contract.",
                    object_ref=node.path,
                ))
                continue
            loaded = self.runtime.node.contract.get_visible_contract(repo_root, node_path=node.path)
            if not loaded.ok or loaded.value is None:
                node_issues.append(self.runtime.foundation.issue(
                    "release_node_contract_missing",
                    "The active Node contract is missing or unreadable.",
                    object_ref=node.path,
                ))
                continue
            contract = loaded.value.contract
            if contract.status != ContractVersionStatus.COMMITTED:
                node_issues.append(self.runtime.foundation.issue(
                    "release_node_contract_uncommitted",
                    "The active Node contract is not committed.",
                    object_ref=node.path,
                ))
                continue
            node_versions[node.node_id] = contract.version
            deps = self.runtime.node.dependency.validate_node_deps(repo_root, node_path=node.path)
            if deps.ok and deps.value is not None:
                reports.append(deps.value)
            else:
                reports.append(self.runtime.foundation.gate_failed(
                    "release_node_deps", deps.issues,
                    summary=f"Node dependencies could not be validated for {node.path}.",
                ))
            external_dep_issues = []
            for dep in contract.deps:
                if dep.target.repo is None:
                    continue
                if dep.target.repo not in lake_dependency_names:
                    external_dep_issues.append(self.runtime.foundation.issue(
                        "node_dep_external_lake_dependency_missing",
                        "External Node dependency is not attached as a Lake dependency.",
                        object_ref=f"{node.path}->{dep.target.repo}:{dep.target.node}",
                    ))
                    continue
                provider_root = repo_root.parent / dep.target.repo
                available = self.runtime.repo_workspace.provider_availability.check_provider_available(provider_root)
                if not available.ok or available.value is None or not available.value.passed:
                    external_dep_issues.extend(
                        available.issues if available.issues else [self.runtime.foundation.issue(
                            "node_dep_external_provider_unavailable",
                            "External Node dependency provider is not available.",
                            object_ref=dep.target.repo,
                        )]
                    )
            if external_dep_issues:
                reports.append(self.runtime.foundation.gate_failed(
                    "release_external_node_dependencies", external_dep_issues,
                    summary=f"External Node dependencies are invalid for {node.path}.",
                ))
            material_refs = self.runtime.node.material_ref.list_node_material_refs(repo_root, node_path=node.path)
            if material_refs.ok and material_refs.value is not None:
                invalid_refs = [
                    item
                    for item in [*material_refs.value.owned_refs, *material_refs.value.context_refs]
                    if not item.valid
                ]
                if invalid_refs:
                    reports.append(self.runtime.foundation.gate_failed(
                        "release_material_refs",
                        [self.runtime.foundation.issue(
                            "material_ref_invalid",
                            item.preview_summary or "Node material reference is invalid.",
                            object_ref=node.path,
                        ) for item in invalid_refs],
                        summary=f"Invalid material references found for {node.path}.",
                    ))
            else:
                reports.append(self.runtime.foundation.gate_failed(
                    "release_material_refs", material_refs.issues,
                    summary=f"Material references could not be validated for {node.path}.",
                ))
            if node.kind == NodeKind.SCOPE:
                exports = self.runtime.node.export.validate_scope_exports(repo_root, scope_path=node.path)
                if exports.ok and exports.value is not None:
                    reports.append(exports.value)
                else:
                    reports.append(self.runtime.foundation.gate_failed(
                        "release_scope_exports", exports.issues,
                        summary=f"Scope exports could not be validated for {node.path}.",
                    ))
                if contract.decl_graph_head:
                    node_issues.append(self.runtime.foundation.issue(
                        "release_scope_decl_graph_head_not_empty",
                        "Scope contracts must have an empty DeclGraph head.",
                        object_ref=node.path,
                    ))
                guarded = self.runtime.node.release_guard.check_scope_contract_candidate(
                    repo_root, scope_path=node.path, candidate=contract
                )
                if not guarded.ok:
                    node_issues.extend(guarded.issues)
                interfaces = self.runtime.lean_projection.node_projection.check_interfaces_sync(
                    repo_root, node_path=node.path
                )
                if interfaces.ok and interfaces.value is not None:
                    reports.append(interfaces.value)
                else:
                    reports.append(self.runtime.foundation.gate_failed(
                        "release_interfaces_sync", interfaces.issues,
                        summary=f"Interface projection could not be checked for {node.path}.",
                    ))
                continue
            captured = self.runtime.node.release_guard.capture_content_contract_head(repo_root, node_path=node.path)
            if not captured.ok or captured.value is None:
                node_issues.append(self.runtime.foundation.issue(
                    "release_decl_graph_open",
                    "Content DeclGraph head cannot be recaptured for release.",
                    object_ref=node.path,
                    details={"issues": "; ".join(issue.kind for issue in captured.issues)},
                ))
            elif legacy_adoption and not contract.decl_graph_head:
                node_versions[node.node_id] = contract.version + 1
            elif captured.value != contract.decl_graph_head:
                node_issues.append(self.runtime.foundation.issue(
                    "release_decl_graph_head_stale",
                    "Committed Content contract head differs from current committed DeclGraph truth.",
                    object_ref=node.path,
                    current=json.dumps(contract.decl_graph_head, sort_keys=True),
                    expected=json.dumps(captured.value, sort_keys=True),
                ))
            elif not captured.value:
                node_issues.append(self.runtime.foundation.issue(
                    "release_content_decl_graph_head_empty",
                    "An active Content node must publish a non-empty committed DeclGraph head.",
                    object_ref=node.path,
                ))
            strategies = self.runtime.decl_graph.list_strategies(repo_root, node_path=node.path)
            rounds = self.runtime.decl_graph.list_rounds(repo_root, node_path=node.path)
            if not strategies.ok or strategies.value is None:
                return self.runtime.foundation.fail(strategies.issues)
            if not rounds.ok or rounds.value is None:
                return self.runtime.foundation.fail(rounds.issues)
            open_strategies = [item.strategy_id for item in strategies.value if item.status == DeclStrategyStatus.OPEN]
            open_rounds = [
                item.round_id for item in rounds.value if item.status in {DeclRoundStatus.DRAFT, DeclRoundStatus.RUNNING}
            ]
            graph_issues = []
            if open_strategies:
                graph_issues.append(self.runtime.foundation.issue(
                    "release_decl_graph_open",
                    "Content node has open declaration strategies.",
                    object_ref=node.path,
                    current=", ".join(sorted(open_strategies)),
                ))
            if open_rounds:
                graph_issues.append(self.runtime.foundation.issue(
                    "release_decl_graph_open",
                    "Content node has draft or running declaration rounds.",
                    object_ref=node.path,
                    current=", ".join(sorted(open_rounds)),
                ))
            graph_reports.append(
                self.runtime.foundation.gate_failed("release_decl_graph_closeout", graph_issues)
                if graph_issues
                else self.runtime.foundation.gate_passed(
                    "release_decl_graph_closeout", summary=f"DeclGraph closeout passed for {node.path}."
                )
            )
        reports.append(
            self.runtime.foundation.gate_failed("release_node_contract_closeout", node_issues)
            if node_issues
            else self.runtime.foundation.gate_passed(
                "release_node_contract_closeout", summary=f"{len(node_versions)} active Node contracts are committed."
            )
        )
        reports.extend(graph_reports)

        skeleton = self.runtime.repo_workspace.lake_dependency.check_native_repo_skeleton(repo_root)
        if skeleton.ok and skeleton.value is not None:
            reports.append(skeleton.value)
        else:
            reports.append(self.runtime.foundation.gate_failed(
                "release_native_lake_dependency", skeleton.issues,
                summary="Native Lake dependency skeleton could not be validated.",
            ))

        ordinary = self.runtime.validation_snapshot.readiness_gate.check_repo_ready(repo_root, summary=summary)
        if not ordinary.ok or ordinary.value is None:
            return self.runtime.foundation.fail(ordinary.issues)
        reports.append(ordinary.value)

        gate = self.runtime.foundation.merge_gate_reports("candidate_repo_release", reports)
        config = self.runtime.repo_workspace.metadata.get_repo_config(repo_root)
        if not config.ok or config.value is None:
            return self.runtime.foundation.fail(config.issues)
        blocking = sorted({issue.kind for issue in gate.issues if self.runtime.foundation.result_error.is_error_issue(issue)})
        return self.runtime.foundation.ok(CandidateReleaseGateView(
            base_release_id=base_release_id,
            candidate_node_contract_versions=dict(sorted(node_versions.items())),
            target_proof_availability=config.value.config.target_proof_availability,
            gate=gate,
            blocking_issue_kinds=blocking,
            summary="Candidate release gate passed." if gate.passed else "Candidate release has blocking findings.",
        ))

    def prepare_candidate_release(
        self,
        repo_root: Path,
        *,
        base_release_id: str | None,
        summary: str,
        owner_flow_id: str,
    ) -> ServiceResult[CandidateReleasePreparationView]:
        preview = self.preview_candidate_release(
            repo_root, base_release_id=base_release_id, summary=summary, owner_flow_id=owner_flow_id
        )
        if not preview.ok or preview.value is None:
            return self.runtime.foundation.fail(preview.issues)
        if not preview.value.gate.passed:
            return self.runtime.foundation.ok(CandidateReleasePreparationView(
                outcome="blocked",
                gate=preview.value.gate,
                blocking_issue_kinds=preview.value.blocking_issue_kinds,
                summary=preview.value.summary,
            ))
        build = self.runtime.external.lean_toolchain.run_lake_build(Path(repo_root))
        if not build.ok:
            issue = self.runtime.foundation.issue(
                "release_lake_build_failed",
                "The candidate repository failed the required Lake build.",
                object_ref=str(repo_root),
                details={
                    "command": " ".join(build.command),
                    "exit_code": str(build.exit_code),
                    "timed_out": str(build.timed_out),
                    "stderr": build.stderr_excerpt or build.raw_excerpt or "",
                },
            )
            gate = self.runtime.foundation.gate_failed("candidate_repo_release", issue, summary="Lake build failed.")
            return self.runtime.foundation.ok(CandidateReleasePreparationView(
                outcome="blocked", gate=gate, build=build,
                blocking_issue_kinds=[issue.kind], summary="Candidate Lake build failed."
            ))
        release_id_result = self.runtime.repo_workspace.release.allocate_release_id(Path(repo_root))
        if not release_id_result.ok or release_id_result.value is None:
            return self.runtime.foundation.fail(release_id_result.issues)
        checkpoint_id_result = self.runtime.foundation.store.allocate_uuid(
            lambda candidate: self.runtime.validation_snapshot.snapshot_restore._snapshot_dir(Path(repo_root), candidate).exists(),
            prefix="repo_release_cp",
        )
        if not checkpoint_id_result.ok or checkpoint_id_result.value is None:
            return self.runtime.foundation.fail(checkpoint_id_result.issues)
        release = RepoRelease(
            release_id=release_id_result.value,
            parent_release_id=base_release_id,
            node_contract_versions=preview.value.candidate_node_contract_versions,
            target_proof_availability=preview.value.target_proof_availability,
            repo_checkpoint_id=checkpoint_id_result.value,
            summary=summary,
        )
        publication = RepoPublicationState(
            status=RepoPublicationStatus.STABLE,
            latest_release_id=release.release_id,
        )
        digest = self.compute_candidate_digest(Path(repo_root))
        prepared = PreparedRepoReleaseView(
            release=release,
            publication=publication,
            candidate_digest=digest,
            build=build,
            gate=preview.value.gate,
            summary=f"Prepared candidate release {release.release_id}.",
        )
        return self.runtime.foundation.ok(CandidateReleasePreparationView(
            outcome="prepared", gate=preview.value.gate, build=build,
            prepared_release=prepared, summary=prepared.summary,
        ))

    def preview_legacy_stable_adoption(
        self, repo_root: Path, *, summary: str
    ) -> ServiceResult[LegacyStableAdoptionPreviewView]:
        """Inspect a legacy native stable repo without mutating any truth."""
        repo_root = Path(repo_root)
        preflight = self._check_legacy_adoption_base(repo_root, summary=summary)
        if not preflight.ok or preflight.value is None:
            return self.runtime.foundation.fail(preflight.issues)
        reports = [preflight.value]
        workflow = self._check_workflow_closeout(repo_root, owner_flow_id=None, stable_hook=True)
        if not workflow.ok or workflow.value is None:
            return self.runtime.foundation.fail(workflow.issues)
        reports.append(workflow.value)

        source_index = self.runtime.material.source_index.inspect_source_index_schema(repo_root)
        source_view = source_index.value if source_index.ok else None
        source_issues = list(source_index.issues)
        if source_view is not None:
            source_issues.extend(source_view.file_findings)
        reports.append(
            self.runtime.foundation.gate_failed(
                "legacy_adoption_source_index", source_issues,
                summary="SourceIndex migration is blocked.",
            )
            if source_issues
            else self.runtime.foundation.gate_passed(
                "legacy_adoption_source_index",
                summary=(
                    f"SourceIndex schema v{source_view.stored_schema_version} is inspectable."
                    if source_view is not None else "SourceIndex is inspectable."
                ),
            )
        )

        contracts = self._inspect_legacy_contract_heads(repo_root)
        if not contracts.ok or contracts.value is None:
            return self.runtime.foundation.fail(contracts.issues)
        contract_views, contract_gate = contracts.value
        reports.append(contract_gate)

        candidate = self._preview_release(
            repo_root,
            base_release_id=None,
            summary=summary,
            owner_flow_id=None,
            legacy_adoption=True,
        )
        if not candidate.ok or candidate.value is None:
            return self.runtime.foundation.fail(candidate.issues)
        reports.append(candidate.value.gate)
        gate = self.runtime.foundation.merge_gate_reports("legacy_stable_adoption", reports)
        build = None
        if gate.passed:
            build = self.runtime.external.lean_toolchain.run_lake_build(repo_root)
            if not build.ok:
                build_issue = self.runtime.foundation.issue(
                    "legacy_adoption_build_failed",
                    "Legacy stable adoption preview failed the required Lake build.",
                    object_ref=str(repo_root),
                    details={"stderr": build.stderr_excerpt or build.raw_excerpt or ""},
                )
                gate = self.runtime.foundation.merge_gate_reports(
                    "legacy_stable_adoption",
                    [gate, self.runtime.foundation.gate_failed("legacy_adoption_build", build_issue)],
                )
        blockers = sorted({
            issue.kind for issue in gate.issues
            if self.runtime.foundation.result_error.is_error_issue(issue)
        })
        publication_path = self.runtime.repo_workspace.metadata._repo_publication_path(repo_root)
        legacy_path = self.runtime.repo_workspace.metadata._provider_ready_path(repo_root)
        publication_source = (
            "repo_publication" if publication_path.exists()
            else "provider_ready" if legacy_path.exists()
            else "default"
        )
        return self.runtime.foundation.ok(LegacyStableAdoptionPreviewView(
            outcome="eligible" if gate.passed else "blocked",
            publication_source=publication_source,
            source_index=source_view,
            contract_heads=contract_views,
            gate=gate,
            build=build,
            blocking_issue_kinds=blockers,
            current_digest=self.compute_candidate_digest(repo_root),
            summary=(
                "Legacy native stable repo is eligible for adoption."
                if gate.passed else "Legacy native stable repo adoption is blocked."
            ),
        ))

    def prepare_legacy_stable_adoption(
        self, repo_root: Path, *, summary: str
    ) -> ServiceResult[CandidateReleasePreparationView]:
        """Prepare R1 after legacy schema and contract migrations are complete."""
        preview = self._preview_release(
            Path(repo_root), base_release_id=None, summary=summary,
            owner_flow_id=None, legacy_adoption=True,
        )
        if not preview.ok or preview.value is None:
            return self.runtime.foundation.fail(preview.issues)
        if not preview.value.gate.passed:
            return self.runtime.foundation.ok(CandidateReleasePreparationView(
                outcome="blocked", gate=preview.value.gate,
                blocking_issue_kinds=preview.value.blocking_issue_kinds,
                summary="Legacy stable adoption gate is blocked.",
            ))
        build = self.runtime.external.lean_toolchain.run_lake_build(Path(repo_root))
        if not build.ok:
            issue = self.runtime.foundation.issue(
                "legacy_adoption_build_failed", "Legacy stable adoption failed Lake build.",
                object_ref=str(repo_root),
                details={"stderr": build.stderr_excerpt or build.raw_excerpt or ""},
            )
            gate = self.runtime.foundation.gate_failed("legacy_stable_adoption", issue)
            return self.runtime.foundation.ok(CandidateReleasePreparationView(
                outcome="blocked", gate=gate, build=build,
                blocking_issue_kinds=[issue.kind], summary="Legacy stable adoption build failed.",
            ))
        release_id = self.runtime.repo_workspace.release.allocate_release_id(Path(repo_root))
        checkpoint_id = self.runtime.foundation.store.allocate_uuid(
            lambda candidate: self.runtime.validation_snapshot.snapshot_restore._snapshot_dir(
                Path(repo_root), candidate
            ).exists(),
            prefix="repo_release_cp",
        )
        if not release_id.ok or release_id.value is None:
            return self.runtime.foundation.fail(release_id.issues)
        if not checkpoint_id.ok or checkpoint_id.value is None:
            return self.runtime.foundation.fail(checkpoint_id.issues)
        release = RepoRelease(
            release_id=release_id.value,
            parent_release_id=None,
            node_contract_versions=preview.value.candidate_node_contract_versions,
            target_proof_availability=preview.value.target_proof_availability,
            repo_checkpoint_id=checkpoint_id.value,
            summary=summary,
        )
        prepared = PreparedRepoReleaseView(
            release=release,
            publication=RepoPublicationState(
                status=RepoPublicationStatus.STABLE, latest_release_id=release.release_id
            ),
            candidate_digest=self.compute_candidate_digest(Path(repo_root)),
            build=build,
            gate=preview.value.gate,
            summary=f"Prepared legacy stable adoption release {release.release_id}.",
        )
        return self.runtime.foundation.ok(CandidateReleasePreparationView(
            outcome="prepared", gate=preview.value.gate, build=build,
            prepared_release=prepared, summary=prepared.summary,
        ))

    def commit_prepared_release(
        self,
        repo_root: Path,
        *,
        prepared: PreparedRepoReleaseView,
        owner_flow_id: str,
        scope_ids: list[str],
    ) -> ServiceResult[RepoReleaseFinalizeView]:
        return self._commit_prepared_release_transaction(
            repo_root,
            prepared=prepared,
            owner_flow_id=owner_flow_id,
            scope_ids=scope_ids,
            legacy_adoption=False,
            lock_held=False,
        )

    def commit_legacy_stable_adoption(
        self,
        repo_root: Path,
        *,
        prepared: PreparedRepoReleaseView,
        scope_ids: list[str],
        lock_held: bool = False,
    ) -> ServiceResult[RepoReleaseFinalizeView]:
        """Commit an already prepared legacy R1 through the shared writer."""
        return self._commit_prepared_release_transaction(
            repo_root,
            prepared=prepared,
            owner_flow_id=None,
            scope_ids=scope_ids,
            legacy_adoption=True,
            lock_held=lock_held,
        )

    def adopt_legacy_stable_repo(
        self,
        repo_root: Path,
        *,
        summary: str,
        dry_run: bool,
        scope_ids: list[str] | None = None,
    ) -> ServiceResult[LegacyStableAdoptionView]:
        """Inspect or atomically adopt a legacy stable native repository."""
        repo_root = Path(repo_root)
        checkpoint_for_restore: str | None = None
        try:
            with self.runtime.repo_workspace.lifecycle_lock.locked(repo_root):
                preview = self.preview_legacy_stable_adoption(repo_root, summary=summary)
                if not preview.ok or preview.value is None:
                    return self.runtime.foundation.fail(preview.issues)
                if dry_run or preview.value.outcome == "blocked":
                    return self.runtime.foundation.ok(LegacyStableAdoptionView(
                        outcome="blocked" if preview.value.outcome == "blocked" else "eligible",
                        preview=preview.value,
                        summary=(
                            "Legacy adoption dry-run is eligible; no files were written."
                            if preview.value.outcome == "eligible"
                            else "Legacy adoption dry-run is blocked; no files were written."
                        ),
                    ))
                checkpoint_id = self.runtime.foundation.store.allocate_uuid(
                    lambda candidate: self.runtime.validation_snapshot.snapshot_restore._snapshot_dir(
                        repo_root, candidate
                    ).exists(),
                    prefix="legacy_adoption_cp",
                )
                if not checkpoint_id.ok or checkpoint_id.value is None:
                    return self.runtime.foundation.fail(checkpoint_id.issues)
                checkpoint = self.runtime.validation_snapshot.snapshot_restore.create_repo_stable_point_snapshot(
                    repo_root,
                    snapshot_id=checkpoint_id.value,
                    checkpoint_kind="before_native_run_mutation",
                    label="before legacy stable release adoption",
                    scope_ids=scope_ids or [f"repo:{repo_root.name}"],
                )
                if not checkpoint.ok or checkpoint.value is None:
                    return self.runtime.foundation.fail(checkpoint.issues)
                checkpoint_for_restore = checkpoint.value.snapshot_id

                source = preview.value.source_index
                if source is not None and source.migration_required:
                    migrated = self.runtime.material.source_index.migrate_source_index_schema(
                        repo_root, expected_source_index_digest=source.current_digest
                    )
                    if not migrated.ok:
                        return self._legacy_adoption_failure_with_restore(
                            repo_root, checkpoint_id=checkpoint.value.snapshot_id,
                            issues=migrated.issues,
                        )
                for contract in preview.value.contract_heads:
                    if not contract.migration_required:
                        continue
                    adopted = self.runtime.node.adopt_committed_content_contract_head(
                        repo_root,
                        node_path=contract.node_path,
                        summary="Captured legacy DeclGraph head for initial repository release.",
                    )
                    if not adopted.ok:
                        return self._legacy_adoption_failure_with_restore(
                            repo_root, checkpoint_id=checkpoint.value.snapshot_id,
                            issues=adopted.issues,
                        )

                prepared = self.prepare_legacy_stable_adoption(repo_root, summary=summary)
                if (
                    not prepared.ok or prepared.value is None
                    or prepared.value.outcome != "prepared"
                    or prepared.value.prepared_release is None
                ):
                    issues = prepared.issues if not prepared.ok else prepared.value.gate.issues
                    return self._legacy_adoption_failure_with_restore(
                        repo_root, checkpoint_id=checkpoint.value.snapshot_id, issues=issues,
                    )
                finalized = self.commit_legacy_stable_adoption(
                    repo_root,
                    prepared=prepared.value.prepared_release,
                    scope_ids=scope_ids or [f"repo:{repo_root.name}"],
                    lock_held=True,
                )
                if not finalized.ok or finalized.value is None:
                    return self._legacy_adoption_failure_with_restore(
                        repo_root, checkpoint_id=checkpoint.value.snapshot_id,
                        issues=finalized.issues,
                    )
                final_preview = preview.value.model_copy(update={
                    "current_digest": prepared.value.prepared_release.candidate_digest,
                    "summary": "Legacy native stable repo was adopted as release R1.",
                })
                return self.runtime.foundation.ok(LegacyStableAdoptionView(
                    outcome="adopted",
                    preview=final_preview,
                    pre_adoption_checkpoint_id=checkpoint.value.snapshot_id,
                    finalized=finalized.value,
                    summary=f"Adopted legacy native repo as {finalized.value.release.release.release_id}.",
                ), warnings=finalized.issues)
        except Exception as exc:
            if checkpoint_for_restore is not None:
                return self._legacy_adoption_failure_with_restore(
                    repo_root,
                    checkpoint_id=checkpoint_for_restore,
                    issues=[self.runtime.foundation.issue(
                        "legacy_adoption_failed", f"Legacy stable adoption failed: {exc}",
                        object_ref=str(repo_root),
                    )],
                )
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "legacy_adoption_failed", f"Legacy stable adoption failed: {exc}",
                object_ref=str(repo_root),
            ))

    def _commit_prepared_release_transaction(
        self,
        repo_root: Path,
        *,
        prepared: PreparedRepoReleaseView,
        owner_flow_id: str | None,
        scope_ids: list[str],
        legacy_adoption: bool,
        lock_held: bool,
    ) -> ServiceResult[RepoReleaseFinalizeView]:
        repo_root = Path(repo_root)
        current_publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
        if not current_publication.ok or current_publication.value is None:
            return self.runtime.foundation.fail(current_publication.issues)
        if (
            current_publication.value.publication.status == RepoPublicationStatus.STABLE
            and current_publication.value.publication.latest_release_id == prepared.release.release_id
        ):
            return self._finalize_existing_release(repo_root, prepared=prepared)
        checkpoint: RepoCheckpointSnapshotView | None = None
        release_published = False
        publication_committed = False
        publication_durability_warning = False
        committed_release_view: RepoReleaseView | None = None
        try:
            lock_context = (
                nullcontext()
                if lock_held
                else self.runtime.repo_workspace.lifecycle_lock.locked(repo_root)
            )
            with lock_context:
                if legacy_adoption:
                    legacy_base = self._check_legacy_adoption_base(
                        repo_root, summary=prepared.release.summary
                    )
                    if not legacy_base.ok or legacy_base.value is None:
                        return self.runtime.foundation.fail(legacy_base.issues)
                    if not legacy_base.value.passed:
                        return self.runtime.foundation.fail(legacy_base.value.issues)
                publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
                if not publication.ok or publication.value is None:
                    return self.runtime.foundation.fail(publication.issues)
                current = publication.value.publication
                expected_status = (
                    RepoPublicationStatus.STABLE if legacy_adoption
                    else RepoPublicationStatus.DEVELOPING
                )
                if (
                    current.status != expected_status
                    or current.latest_release_id != prepared.release.parent_release_id
                    or (legacy_adoption and prepared.release.parent_release_id is not None)
                ):
                    return self.runtime.foundation.fail(self.runtime.foundation.issue(
                        "release_base_mismatch",
                        "Publication baseline changed after candidate preparation.",
                        current=current.latest_release_id,
                        expected=prepared.release.parent_release_id,
                    ))
                workflow = self._check_workflow_closeout(
                    repo_root,
                    owner_flow_id=None if legacy_adoption else owner_flow_id,
                    stable_hook=True,
                )
                if not workflow.ok or workflow.value is None:
                    return self.runtime.foundation.fail(workflow.issues)
                if not workflow.value.passed:
                    return self.runtime.foundation.fail(workflow.value.issues)
                digest = self.compute_candidate_digest(repo_root)
                if digest != prepared.candidate_digest:
                    return self.runtime.foundation.fail(self.runtime.foundation.issue(
                        "release_candidate_drift", "Candidate truth changed after preparation.",
                        current=digest, expected=prepared.candidate_digest,
                    ))
                model = self.runtime.repo_workspace.metadata.get_repo_model(repo_root)
                if not model.ok or model.value is None:
                    return self.runtime.foundation.fail(model.issues)
                checkpoint_result = self.runtime.validation_snapshot.snapshot_restore.create_repo_release_checkpoint(
                    repo_root,
                    snapshot_id=prepared.release.repo_checkpoint_id,
                    release=prepared.release,
                    publication=prepared.publication,
                    repo_model=RepoModel(main_node=model.value.main_node, summary=prepared.release.summary),
                    expected_candidate_digest=prepared.candidate_digest,
                    label=f"release {prepared.release.release_id}",
                    scope_ids=scope_ids,
                )
                if not checkpoint_result.ok or checkpoint_result.value is None:
                    return self.runtime.foundation.fail(checkpoint_result.issues)
                checkpoint = checkpoint_result.value
                created = self.runtime.repo_workspace.release.create_release(repo_root, release=prepared.release)
                if not created.ok or created.value is None:
                    release_path = self.runtime.foundation.layout.release_path(
                        FoundationContext(repo_root=repo_root, caller="release_finalizer.create_readback"),
                        prepared.release.release_id,
                    )
                    visible_release = self.runtime.foundation.store.read_json(release_path, RepoRelease)
                    exact_visible_release = (
                        visible_release.ok and visible_release.value == prepared.release
                    )
                    conflicting_visible_release = (
                        release_path.exists() and not exact_visible_release
                    )
                    cleanup_error = self._cleanup_precommit_artifacts(
                        repo_root,
                        release_id=prepared.release.release_id if exact_visible_release else None,
                        checkpoint_id=prepared.release.repo_checkpoint_id,
                    )
                    if cleanup_error is not None:
                        return self.runtime.foundation.fail(cleanup_error)
                    if conflicting_visible_release:
                        return self.runtime.foundation.fail(self.runtime.foundation.issue(
                            "release_identity_conflict",
                            "Release CREATE_ONLY failed because the target path contains a different payload; it was not removed.",
                            object_ref=prepared.release.release_id,
                        ))
                    return self.runtime.foundation.fail(created.issues)
                release_published = True
                committed_release_view = created.value
                publication_path = self.runtime.repo_workspace.metadata._repo_publication_path(repo_root)
                committed = self.runtime.foundation.store.write_json_atomic(
                    publication_path, prepared.publication, mode=WriteMode.OVERWRITE
                )
                if not committed.ok:
                    publication_readback = self.runtime.foundation.store.read_json(
                        publication_path, RepoPublicationState
                    )
                    if (
                        publication_readback.ok
                        and publication_readback.value == prepared.publication
                    ):
                        publication_committed = True
                        publication_durability_warning = True
                    else:
                        cleanup_error = self._cleanup_precommit_artifacts(
                            repo_root,
                            release_id=prepared.release.release_id,
                            checkpoint_id=prepared.release.repo_checkpoint_id,
                        )
                        if cleanup_error is not None:
                            return self.runtime.foundation.fail(cleanup_error)
                        return self.runtime.foundation.fail(self.runtime.foundation.issue(
                            "release_publication_commit_failed",
                            "Failed to commit the stable publication pointer.",
                            details={"issues": "; ".join(issue.kind for issue in committed.issues)},
                        ))
                else:
                    publication_committed = True
        except Exception as exc:  # lifecycle lock and filesystem failures
            if publication_committed and committed_release_view is not None and checkpoint is not None:
                warning = self.runtime.foundation.issue(
                    "release_postcommit_followup_pending",
                    f"Release publication committed; a later transaction follow-up failed: {exc}",
                    severity="warning",
                    object_ref=prepared.release.release_id,
                )
                return self.runtime.foundation.ok(RepoReleaseFinalizeView(
                    release=committed_release_view,
                    checkpoint=checkpoint,
                    publication=RepoPublicationView(repo_root=str(repo_root), publication=prepared.publication),
                    reconciliation=ProviderRequirementReconciliationView(
                        release_id=prepared.release.release_id,
                        pending=["provider_requirement_reconciliation"],
                        summary="Post-commit reconciliation is pending.",
                    ),
                    notification_pending=True,
                    summary=f"Committed native repo release {prepared.release.release_id}; follow-up pending.",
                ), warnings=[warning])
            cleanup_error = self._cleanup_precommit_artifacts(
                repo_root,
                release_id=prepared.release.release_id if release_published else None,
                checkpoint_id=prepared.release.repo_checkpoint_id if checkpoint is not None else None,
            )
            if cleanup_error is not None:
                return self.runtime.foundation.fail(cleanup_error)
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "release_truth_publish_failed", f"Release publication transaction failed: {exc}",
                object_ref=prepared.release.release_id,
            ))

        if committed_release_view is None or checkpoint is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "release_commit_state_missing", "Publication committed without in-memory release/checkpoint evidence."
            ))
        warnings = []
        if publication_durability_warning:
            warnings.append(self.runtime.foundation.issue(
                "release_publication_durability_warning",
                "Publication replace is visible, but the writer reported a post-replace durability failure.",
                severity="warning",
                object_ref=prepared.release.release_id,
            ))
        try:
            summary_sync = self.runtime.repo_workspace.metadata.set_repo_summary(
                repo_root, summary=prepared.release.summary
            )
        except Exception:
            summary_sync = None
        if summary_sync is None or not summary_sync.ok:
            warnings.append(self.runtime.foundation.issue(
                "release_summary_sync_pending",
                "Release committed, but display summary synchronization is pending.",
                severity="warning",
                object_ref=prepared.release.release_id,
            ))
        try:
            readback = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
        except Exception:
            readback = None
        if (
            readback is None
            or not readback.ok
            or readback.value is None
            or readback.value.publication != prepared.publication
        ):
            warnings.append(self.runtime.foundation.issue(
                "release_publication_readback_pending",
                "Publication commit succeeded, but post-commit readback could not be confirmed.",
                severity="warning",
                object_ref=prepared.release.release_id,
            ))
        try:
            reconciliation = self.reconcile_provider_requirements(repo_root, release_id=prepared.release.release_id)
        except Exception as exc:  # post-commit consumer failures never roll back provider truth
            reconciliation = self.runtime.foundation.fail(self.runtime.foundation.issue(
                "release_requirement_notification_failed",
                f"Provider requirement reconciliation raised after commit: {exc}",
                object_ref=prepared.release.release_id,
            ))
        if not reconciliation.ok or reconciliation.value is None:
            reconciliation_view = ProviderRequirementReconciliationView(
                release_id=prepared.release.release_id,
                pending=["provider_requirement_reconciliation"],
                summary="Requirement reconciliation is pending.",
            )
            warnings.append(self.runtime.foundation.issue(
                "release_requirement_notification_pending",
                "Release committed, but provider requirement reconciliation is pending.",
                severity="warning",
                object_ref=prepared.release.release_id,
            ))
        else:
            reconciliation_view = reconciliation.value
            if reconciliation_view.pending or reconciliation_view.conflicts:
                warnings.append(self.runtime.foundation.issue(
                    "release_requirement_notification_pending",
                    "Release committed with pending or conflicting requirement notifications.",
                    severity="warning",
                    object_ref=prepared.release.release_id,
                ))
        result = RepoReleaseFinalizeView(
            release=committed_release_view,
            checkpoint=checkpoint,
            publication=RepoPublicationView(repo_root=str(repo_root), publication=prepared.publication),
            reconciliation=reconciliation_view,
            notification_pending=bool(reconciliation_view.pending or reconciliation_view.conflicts),
            summary=f"Committed native repo release {prepared.release.release_id}.",
        )
        return self.runtime.foundation.ok(result, warnings=warnings)

    def _finalize_existing_release(
        self, repo_root: Path, *, prepared: PreparedRepoReleaseView
    ) -> ServiceResult[RepoReleaseFinalizeView]:
        checkpoint = self.runtime.validation_snapshot.snapshot_restore._load_existing_release_checkpoint_view(
            repo_root,
            prepared.release.repo_checkpoint_id,
            release=prepared.release,
            publication=prepared.publication,
            repo_model=RepoModel(main_node="Main", summary=prepared.release.summary),
        )
        if not checkpoint.ok or checkpoint.value is None:
            return self.runtime.foundation.fail(checkpoint.issues)
        warnings = []
        try:
            release = self.runtime.repo_workspace.release.get_release(
                repo_root, release_id=prepared.release.release_id
            )
        except Exception:
            release = None
        if release is not None and release.ok and release.value is not None:
            if release.value.release != prepared.release:
                return self.runtime.foundation.fail(self.runtime.foundation.issue(
                    "release_identity_conflict",
                    "The committed latest release differs from the prepared release payload.",
                    object_ref=prepared.release.release_id,
                ))
        else:
            warnings.append(self.runtime.foundation.issue(
                "release_readback_repair_pending",
                "Exact release checkpoint confirms the committed payload, but live release readback needs repair.",
                severity="warning",
                object_ref=prepared.release.release_id,
            ))
        try:
            model = self.runtime.repo_workspace.metadata.get_repo_model(repo_root)
        except Exception:
            model = None
        if (
            model is None
            or not model.ok
            or model.value is None
            or model.value.summary != prepared.release.summary
        ):
            warnings.append(self.runtime.foundation.issue(
                "release_model_repair_pending",
                "Committed release model summary readback is unavailable or requires repair.",
                severity="warning",
                object_ref=prepared.release.release_id,
            ))
        try:
            publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
        except Exception:
            publication = None
        if publication is not None and publication.ok and publication.value is not None:
            if publication.value.publication != prepared.publication:
                return self.runtime.foundation.fail(self.runtime.foundation.issue(
                    "release_identity_conflict",
                    "Live publication changed after the committed release was identified.",
                    object_ref=prepared.release.release_id,
                ))
        else:
            warnings.append(self.runtime.foundation.issue(
                "release_publication_readback_pending",
                "Committed publication readback requires repair.",
                severity="warning",
                object_ref=prepared.release.release_id,
            ))
        try:
            reconciliation = self.reconcile_provider_requirements(
                repo_root, release_id=prepared.release.release_id
            )
        except Exception:
            reconciliation = None
        if reconciliation is None or not reconciliation.ok or reconciliation.value is None:
            reconciliation_view = ProviderRequirementReconciliationView(
                release_id=prepared.release.release_id,
                pending=["provider_requirement_reconciliation"],
                summary="Requirement reconciliation is pending.",
            )
            warnings.append(self.runtime.foundation.issue(
                "release_requirement_notification_pending",
                "Committed release reconciliation requires retry.",
                severity="warning",
                object_ref=prepared.release.release_id,
            ))
        else:
            reconciliation_view = reconciliation.value
        pending = bool(reconciliation_view.pending or reconciliation_view.conflicts)
        return self.runtime.foundation.ok(RepoReleaseFinalizeView(
            release=RepoReleaseView(
                repo_root=str(repo_root),
                release=prepared.release,
                summary=prepared.release.summary,
            ),
            checkpoint=checkpoint.value,
            publication=RepoPublicationView(repo_root=str(repo_root), publication=prepared.publication),
            reconciliation=reconciliation_view,
            notification_pending=pending,
            summary=f"Release {prepared.release.release_id} was already committed; reconciliation retried idempotently.",
        ), warnings=warnings)

    def reconcile_provider_requirements(
        self, provider_root: Path, *, release_id: str
    ) -> ServiceResult[ProviderRequirementReconciliationView]:
        provider_root = Path(provider_root)
        repo_format = self.runtime.repo_workspace.metadata.get_repo_format(provider_root)
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(provider_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        if repo_format.value.repo_format != RepoFormat.NATIVE:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "native_release_reconciliation_required", "Release reconciliation only accepts native providers."
            ))
        if (
            publication.value.publication.status != RepoPublicationStatus.STABLE
            or publication.value.publication.latest_release_id != release_id
        ):
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "release_reconciliation_not_latest", "Requirement reconciliation requires the current stable latest release.",
                current=publication.value.publication.latest_release_id, expected=release_id,
            ))
        available = self.runtime.repo_workspace.provider_availability.check_provider_available(provider_root)
        if not available.ok or available.value is None:
            return self.runtime.foundation.fail(available.issues)
        if not available.value.passed:
            return self.runtime.foundation.fail(available.value.issues)
        prep = self.runtime.repo_workspace.preparation.get_preparation_input(provider_root)
        if not prep.ok or prep.value is None:
            return self.runtime.foundation.fail(prep.issues)
        config = self.runtime.repo_workspace.metadata.get_repo_config(provider_root)
        if not config.ok or config.value is None:
            return self.runtime.foundation.fail(config.issues)
        provider_key = provider_root.name
        satisfied: list[str] = []
        already: list[str] = []
        pending: list[str] = []
        conflicts: list[str] = []
        for ref in prep.value.input.requirement_refs:
            key = f"{ref.consumer_repo}/{ref.requirement_name}"
            consumer = provider_root.parent / ref.consumer_repo
            loaded = self.runtime.repo_workspace.requirement.get_requirement(consumer, name=ref.requirement_name)
            if not loaded.ok or loaded.value is None:
                pending.append(key)
                continue
            requirement = loaded.value.requirement
            effective = self.runtime.repo_workspace.requirement.effective_provider_repo(requirement)
            if effective != provider_key or (
                requirement.provider_repo is not None and requirement.provider_repo != provider_key
            ):
                conflicts.append(key)
                continue
            if not proof_availability_satisfies(
                config.value.config.target_proof_availability, requirement.required_proof_availability
            ):
                conflicts.append(key)
                continue
            if requirement.status in {
                RepoDependencyRequirementStatus.SATISFIED,
                RepoDependencyRequirementStatus.HANDLED,
            }:
                already.append(key)
                continue
            if requirement.status != RepoDependencyRequirementStatus.OPEN:
                conflicts.append(key)
                continue
            valid = self.runtime.repo_workspace.requirement.validate_requirement_provider_truth(
                consumer, requirement_name=requirement.name, provider_repo=provider_key, require_stable=True
            )
            if not valid.ok:
                pending.append(key)
                continue
            marked = self.runtime.repo_workspace.requirement.mark_requirement_satisfied(
                consumer, requirement_name=requirement.name, provider_repo=provider_key,
                note=f"Provider release {release_id} is ready.",
            )
            if marked.ok:
                satisfied.append(key)
            else:
                pending.append(key)
        return self.runtime.foundation.ok(ProviderRequirementReconciliationView(
            release_id=release_id,
            satisfied=sorted(satisfied), already_satisfied=sorted(already),
            pending=sorted(pending), conflicts=sorted(conflicts),
            summary=(
                f"Reconciled {len(satisfied)} requirements; {len(already)} already satisfied; "
                f"{len(pending)} pending; {len(conflicts)} conflicts."
            ),
        ))

    def _provider_requirement_notification_pending(self, provider_root: Path) -> bool:
        """Inspect consumer requirement truth without validating or mutating it."""
        prep = self.runtime.repo_workspace.preparation.get_preparation_input(provider_root)
        if not prep.ok or prep.value is None:
            return False
        provider_key = provider_root.name
        for ref in prep.value.input.requirement_refs:
            consumer = provider_root.parent / ref.consumer_repo
            loaded = self.runtime.repo_workspace.requirement.get_requirement(
                consumer, name=ref.requirement_name
            )
            if not loaded.ok or loaded.value is None:
                return True
            requirement = loaded.value.requirement
            effective = self.runtime.repo_workspace.requirement.effective_provider_repo(requirement)
            if effective != provider_key:
                return True
            if requirement.status not in {
                RepoDependencyRequirementStatus.SATISFIED,
                RepoDependencyRequirementStatus.HANDLED,
            }:
                return True
        return False

    def audit_repo_release_storage(self, repo_root: Path) -> ServiceResult[RepoReleaseStorageAuditView]:
        repo_root = Path(repo_root)
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
        releases = self.runtime.repo_workspace.release.list_releases(repo_root)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        if not releases.ok or releases.value is None:
            return self.runtime.foundation.fail(releases.issues)
        latest_id = publication.value.publication.latest_release_id
        reachable: set[str] = set()
        issues: list[str] = []
        if (
            publication.value.publication.status == RepoPublicationStatus.STABLE
            and latest_id is None
        ):
            repo_format = self.runtime.repo_workspace.metadata.get_repo_format(repo_root)
            if (
                repo_format.ok and repo_format.value is not None
                and repo_format.value.repo_format == RepoFormat.NATIVE
            ):
                issues.append("legacy_native_release_adoption_required")
        if latest_id is not None:
            lineage = self.runtime.repo_workspace.release.resolve_release_lineage(repo_root, release_id=latest_id)
            if not lineage.ok or lineage.value is None:
                issues.extend(issue.kind for issue in lineage.issues)
            else:
                reachable = {item.release_id for item in lineage.value}
                for lineage_release in lineage.value:
                    checkpoint = self.runtime.validation_snapshot.snapshot_restore.validate_repo_checkpoint_snapshot(
                        repo_root, snapshot_id=lineage_release.repo_checkpoint_id
                    )
                    if not checkpoint.ok:
                        issues.append("release_checkpoint_manifest_invalid")
                        continue
                    archive_root = self.runtime.validation_snapshot.snapshot_restore._snapshot_dir(
                        repo_root, lineage_release.repo_checkpoint_id
                    ) / "files" / "lean_constellation"
                    archived_release = self.runtime.foundation.store.read_json(
                        archive_root / "releases" / f"{lineage_release.release_id}.json", RepoRelease
                    )
                    archived_publication = self.runtime.foundation.store.read_json(
                        archive_root / "repo_publication.json", RepoPublicationState
                    )
                    archived_model = self.runtime.foundation.store.read_json(
                        archive_root / "repo.json", RepoModel
                    )
                    if not archived_release.ok or archived_release.value != lineage_release:
                        issues.append("release_checkpoint_truth_mismatch")
                    if (
                        not archived_publication.ok
                        or archived_publication.value is None
                        or archived_publication.value.status != RepoPublicationStatus.STABLE
                        or archived_publication.value.latest_release_id != lineage_release.release_id
                    ):
                        issues.append("release_checkpoint_truth_mismatch")
                    if (
                        not archived_model.ok
                        or archived_model.value is None
                        or archived_model.value.summary != lineage_release.summary
                    ):
                        issues.append("release_checkpoint_truth_mismatch")
        all_release_ids = {item.release.release_id for item in releases.value}
        checkpoint_root = self.runtime.validation_snapshot.snapshot_restore._snapshot_root(repo_root)
        checkpoint_ids = {
            path.name for path in checkpoint_root.iterdir()
            if path.is_dir() and path.name != ".staging"
        } if checkpoint_root.exists() else set()
        referenced_checkpoints = {item.release.repo_checkpoint_id for item in releases.value}
        staging_root = checkpoint_root / ".staging"
        staging = [str(path) for path in sorted(staging_root.iterdir())] if staging_root.exists() else []
        orphan_releases = sorted(all_release_ids - reachable)
        orphan_checkpoints = sorted(checkpoint_ids - referenced_checkpoints)
        if orphan_releases:
            issues.append("orphan_repo_release")
        if orphan_checkpoints:
            issues.append("orphan_repo_release_checkpoint")
        try:
            for flow in self.runtime.list_flows():
                result = getattr(flow, "result", None)
                prepared = getattr(result, "prepared_release", None)
                release = getattr(prepared, "release", None)
                if (
                    getattr(flow, "flow_type", None) == "native_repo_coordinator"
                    and release is not None
                    and release.release_id not in all_release_ids
                ):
                    issues.append("release_prepared_without_publication_commit")
        except RuntimeError:
            pass
        if latest_id is not None and self._provider_requirement_notification_pending(repo_root):
            issues.append("release_requirement_notification_pending")
        issues = sorted(set(issues))
        audit_payload = {
            "latest_release_id": latest_id,
            "reachable_release_ids": sorted(reachable),
            "orphan_release_ids": orphan_releases,
            "orphan_checkpoint_ids": orphan_checkpoints,
            "staging_paths": staging,
            "issues": issues,
        }
        audit_digest = hashlib.sha256(
            json.dumps(audit_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return self.runtime.foundation.ok(RepoReleaseStorageAuditView(
            passed=not issues and not staging,
            latest_release_id=latest_id,
            reachable_release_ids=sorted(reachable),
            orphan_release_ids=orphan_releases,
            orphan_checkpoint_ids=orphan_checkpoints,
            staging_paths=staging,
            issues=issues,
            audit_digest=audit_digest,
            summary="Release storage audit passed." if not issues and not staging else "Release storage audit found issues.",
        ))

    def cleanup_repo_release_orphans(
        self, repo_root: Path, *, expected_audit_digest: str
    ) -> ServiceResult[MutationSummaryView]:
        """Delete only unreachable checkpoints/staging identified by an exact audit."""
        repo_root = Path(repo_root)
        try:
            with self.runtime.repo_workspace.lifecycle_lock.locked(repo_root):
                audit = self.audit_repo_release_storage(repo_root)
                if not audit.ok or audit.value is None:
                    return self.runtime.foundation.fail(audit.issues)
                if audit.value.audit_digest != expected_audit_digest:
                    return self.runtime.foundation.fail(self.runtime.foundation.issue(
                        "release_audit_digest_mismatch",
                        "Release storage changed after the cleanup audit.",
                        current=audit.value.audit_digest,
                        expected=expected_audit_digest,
                    ))
                changed: list[str] = []
                for checkpoint_id in audit.value.orphan_checkpoint_ids:
                    self._remove_unpublished_checkpoint(repo_root, checkpoint_id)
                    changed.append(f"checkpoint:{checkpoint_id}")
                staging_root = self.runtime.validation_snapshot.snapshot_restore._snapshot_root(
                    repo_root
                ) / ".staging"
                for raw_path in audit.value.staging_paths:
                    path = Path(raw_path)
                    if path.parent != staging_root or not path.is_dir():
                        continue
                    shutil.rmtree(path)
                    changed.append(f"staging:{path.name}")
                return self.runtime.foundation.ok(self.runtime.foundation.mutation_view(
                    object_ref=str(repo_root),
                    changed=bool(changed),
                    changed_items=changed,
                    summary=f"Cleaned {len(changed)} unreachable release checkpoint/staging artifacts.",
                ))
        except Exception as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "release_cleanup_failed", f"Release orphan cleanup failed: {exc}",
                object_ref=str(repo_root),
            ))

    def cleanup_unpublished_release_artifacts(
        self,
        repo_root: Path,
        *,
        release_id: str | None = None,
        checkpoint_id: str | None = None,
        staging_id: str | None = None,
    ) -> ServiceResult[MutationSummaryView]:
        repo_root = Path(repo_root)
        targets = [release_id is not None, checkpoint_id is not None, staging_id is not None]
        if sum(targets) != 1:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "release_cleanup_target_required",
                "Specify exactly one of release_id, checkpoint_id, or staging_id.",
            ))
        object_ref = release_id or checkpoint_id or staging_id or "release_cleanup"
        try:
            with self.runtime.repo_workspace.lifecycle_lock.locked(repo_root):
                publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
                listed = self.runtime.repo_workspace.release.list_releases(repo_root)
                if not publication.ok or publication.value is None:
                    return self.runtime.foundation.fail(publication.issues)
                if not listed.ok or listed.value is None:
                    return self.runtime.foundation.fail(listed.issues)
                reachable: set[str] = set()
                latest = publication.value.publication.latest_release_id
                if latest is not None:
                    lineage = self.runtime.repo_workspace.release.resolve_release_lineage(repo_root, release_id=latest)
                    if not lineage.ok or lineage.value is None:
                        return self.runtime.foundation.fail(lineage.issues)
                    reachable = {item.release_id for item in lineage.value}
                changed: list[str] = []
                if release_id is not None:
                    if release_id in reachable or any(
                        item.release.parent_release_id == release_id for item in listed.value
                    ):
                        return self.runtime.foundation.fail(self.runtime.foundation.issue(
                            "release_artifact_reachable",
                            "Reachable or parent release artifacts cannot be cleaned up.",
                            object_ref=release_id,
                        ))
                    target = next(
                        (item.release for item in listed.value if item.release.release_id == release_id), None
                    )
                    if target is not None:
                        self._remove_unpublished_release(repo_root, release_id)
                        self._remove_unpublished_checkpoint(repo_root, target.repo_checkpoint_id)
                        changed.extend([f"release:{release_id}", f"checkpoint:{target.repo_checkpoint_id}"])
                elif checkpoint_id is not None:
                    if any(item.release.repo_checkpoint_id == checkpoint_id for item in listed.value):
                        return self.runtime.foundation.fail(self.runtime.foundation.issue(
                            "release_artifact_reachable",
                            "A checkpoint referenced by any immutable release cannot be cleaned up.",
                            object_ref=checkpoint_id,
                        ))
                    checkpoint_path = self.runtime.validation_snapshot.snapshot_restore._snapshot_dir(
                        repo_root, checkpoint_id
                    )
                    if checkpoint_path.exists():
                        self._remove_unpublished_checkpoint(repo_root, checkpoint_id)
                        changed.append(f"checkpoint:{checkpoint_id}")
                else:
                    safe_staging = self.runtime.foundation.layout.ensure_safe_key(staging_id or "")
                    staging = (
                        self.runtime.validation_snapshot.snapshot_restore._snapshot_root(repo_root)
                        / ".staging" / safe_staging
                    )
                    existed = staging.exists()
                    if existed:
                        shutil.rmtree(staging)
                    if staging.exists():
                        raise OSError(f"staging cleanup did not remove {staging}")
                    if existed:
                        changed.append(f"staging:{safe_staging}")
                return self.runtime.foundation.ok(self.runtime.foundation.mutation_view(
                    object_ref=object_ref,
                    changed=bool(changed),
                    changed_items=changed,
                    summary=f"Cleaned {len(changed)} unpublished release artifacts.",
                ))
        except (OSError, RuntimeError, ValueError) as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "release_cleanup_failed", f"Release artifact cleanup failed: {exc}", object_ref=object_ref
            ))

    def restore_repo_release(
        self,
        repo_root: Path,
        *,
        release_id: str,
        dry_run: bool = False,
        leave_runtime_paused: bool = True,
    ) -> ServiceResult[SnapshotRestoreView]:
        repo_root = Path(repo_root)
        try:
            with self.runtime.repo_workspace.lifecycle_lock.locked(repo_root):
                publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
                if not publication.ok or publication.value is None:
                    return self.runtime.foundation.fail(publication.issues)
                if publication.value.publication.latest_release_id != release_id:
                    return self.runtime.foundation.fail(self.runtime.foundation.issue(
                        "historical_release_restore_not_supported",
                        "Only the current latest release can be restored as the working repository.",
                        current=release_id, expected=publication.value.publication.latest_release_id,
                    ))
                release = self.runtime.repo_workspace.release.get_release(repo_root, release_id=release_id)
                if not release.ok or release.value is None:
                    return self.runtime.foundation.fail(release.issues)
                restored = self.runtime.validation_snapshot.snapshot_restore.restore_repo_checkpoint_snapshot(
                    repo_root, snapshot_id=release.value.release.repo_checkpoint_id,
                    dry_run=dry_run, leave_runtime_paused=leave_runtime_paused, prune_extra_files=True,
                    allow_release_internal=True,
                )
                if not restored.ok or restored.value is None or dry_run:
                    return restored
                current = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
                if (
                    not current.ok or current.value is None
                    or current.value.publication.status != RepoPublicationStatus.STABLE
                    or current.value.publication.latest_release_id != release_id
                ):
                    return self.runtime.foundation.fail(self.runtime.foundation.issue(
                        "release_storage_corrupt", "Restored release publication truth is inconsistent.", object_ref=release_id
                    ))
                build = self.runtime.external.lean_toolchain.run_lake_build(repo_root)
                if not build.ok:
                    return self.runtime.foundation.fail(self.runtime.foundation.issue(
                        "release_lake_build_failed", "Restored release failed Lake build.",
                        details={"stderr": build.stderr_excerpt or build.raw_excerpt or ""},
                    ))
                return restored
        except Exception as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "release_restore_failed", f"Release restore failed: {exc}", object_ref=release_id
            ))

    def compute_candidate_digest(self, repo_root: Path) -> str:
        repo_root = Path(repo_root)
        digest = hashlib.sha256()
        for path in sorted(self._candidate_files(repo_root), key=lambda item: item.relative_to(repo_root).as_posix()):
            relpath = path.relative_to(repo_root).as_posix()
            digest.update(relpath.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def _candidate_files(self, repo_root: Path) -> list[Path]:
        files: list[Path] = []
        for path in repo_root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(repo_root)
            if rel.parts[0] in self._EXCLUDED_TOP_LEVEL:
                continue
            if rel.parts[0] == ".lean_constellation" and len(rel.parts) > 1:
                if rel.parts[1] in self._EXCLUDED_CONSTELLATION_CHILDREN:
                    continue
            files.append(path)
        return files

    def _check_base(self, repo_root: Path, *, base_release_id: str | None, summary: str) -> ServiceResult[GateReport]:
        issues = []
        repo_format = self.runtime.repo_workspace.metadata.get_repo_format(repo_root)
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        if repo_format.value.repo_format != RepoFormat.NATIVE:
            issues.append(self.runtime.foundation.issue(
                "release_repo_format_not_native", "Only native repositories create RepoRelease truth."
            ))
        if publication.value.publication.status != RepoPublicationStatus.DEVELOPING:
            issues.append(self.runtime.foundation.issue(
                "release_publication_not_developing", "Candidate release requires developing publication.",
                current=publication.value.publication.status.value, expected=RepoPublicationStatus.DEVELOPING.value,
            ))
        if publication.value.publication.latest_release_id != base_release_id:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "release_base_mismatch", "Candidate base does not match current publication latest.",
                current=publication.value.publication.latest_release_id, expected=base_release_id,
            ))
        if not summary.strip():
            issues.append(self.runtime.foundation.issue("repo_ready_summary_required", "Release summary is required."))
        if base_release_id is not None:
            baseline = self.runtime.repo_workspace.release.resolve_release_baseline(repo_root, release_id=base_release_id)
            if not baseline.ok:
                return self.runtime.foundation.fail(baseline.issues)
            current_config = self.runtime.repo_workspace.metadata.get_repo_config(repo_root)
            base_release = self.runtime.repo_workspace.release.get_release(repo_root, release_id=base_release_id)
            if not current_config.ok or current_config.value is None:
                return self.runtime.foundation.fail(current_config.issues)
            if not base_release.ok or base_release.value is None:
                return self.runtime.foundation.fail(base_release.issues)
            checkpoint = self.runtime.validation_snapshot.snapshot_restore.validate_repo_checkpoint_snapshot(
                repo_root, snapshot_id=base_release.value.release.repo_checkpoint_id
            )
            if not checkpoint.ok:
                return self.runtime.foundation.fail(self.runtime.foundation.issue(
                    "release_baseline_corrupt",
                    "The base release checkpoint is missing or invalid.",
                    object_ref=base_release.value.release.repo_checkpoint_id,
                    details={"issues": "; ".join(issue.kind for issue in checkpoint.issues)},
                ))
            if not proof_availability_satisfies(
                current_config.value.config.target_proof_availability,
                base_release.value.release.target_proof_availability,
            ):
                issues.append(self.runtime.foundation.issue(
                    "release_target_regression", "Candidate target is below the base release target."
                ))
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_failed("release_base", issues)
            if issues else self.runtime.foundation.gate_passed("release_base", summary="Release baseline is valid.")
        )

    def _check_legacy_adoption_base(
        self, repo_root: Path, *, summary: str
    ) -> ServiceResult[GateReport]:
        issues = []
        repo_format = self.runtime.repo_workspace.metadata.get_repo_format(repo_root)
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        if repo_format.value.repo_format != RepoFormat.NATIVE:
            issues.append(self.runtime.foundation.issue(
                "legacy_adoption_repo_not_native",
                "Only a native repository can enter legacy stable adoption.",
                object_ref=str(repo_root),
            ))
        state = publication.value.publication
        if state.status != RepoPublicationStatus.STABLE or state.latest_release_id is not None:
            issues.append(self.runtime.foundation.issue(
                "legacy_adoption_not_required",
                "Legacy adoption requires stable publication without a latest release.",
                object_ref=str(repo_root),
                current=f"{state.status.value}:{state.latest_release_id or '-'}",
                expected="stable:-",
            ))
        controller = self.runtime.ark.pause_controller
        paused = bool(
            controller is not None
            and hasattr(controller, "is_paused")
            and controller.is_paused()
        )
        if not paused:
            issues.append(self.runtime.foundation.issue(
                "legacy_adoption_runtime_not_paused",
                "Legacy adoption requires the repository runtime to be globally paused.",
                object_ref=str(repo_root),
            ))
        if not summary.strip():
            issues.append(self.runtime.foundation.issue(
                "repo_ready_summary_required", "Legacy adoption release summary is required."
            ))
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_failed("legacy_adoption_base", issues)
            if issues else self.runtime.foundation.gate_passed(
                "legacy_adoption_base", summary="Legacy adoption base is valid."
            )
        )

    def _inspect_legacy_contract_heads(
        self, repo_root: Path
    ) -> ServiceResult[tuple[list[LegacyContractHeadAdoptionView], GateReport]]:
        nodes = self.runtime.node.node_tree.node_store.list_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        views: list[LegacyContractHeadAdoptionView] = []
        issues = []
        for node in nodes.value:
            if node.lifecycle != NodeLifecycle.ACTIVE:
                continue
            if node.open_contract_version is not None:
                issues.append(self.runtime.foundation.issue(
                    "legacy_adoption_open_truth",
                    "Legacy adoption cannot run with an open Node contract.",
                    object_ref=node.path,
                ))
                continue
            contract = self.runtime.node.contract.get_visible_contract(repo_root, node_path=node.path)
            if not contract.ok or contract.value is None:
                issues.extend(contract.issues)
                continue
            value = contract.value.contract
            if value.status != ContractVersionStatus.COMMITTED:
                issues.append(self.runtime.foundation.issue(
                    "legacy_adoption_open_truth",
                    "Legacy adoption requires every active Node contract to be committed.",
                    object_ref=node.path,
                ))
                continue
            if node.kind == NodeKind.SCOPE:
                if value.decl_graph_head:
                    issues.append(self.runtime.foundation.issue(
                        "legacy_adoption_contract_head_capture_failed",
                        "Scope contracts must not contain a DeclGraph head.",
                        object_ref=node.path,
                    ))
                continue
            head = self.runtime.node.release_guard.capture_content_contract_head(
                repo_root, node_path=node.path
            )
            if not head.ok or head.value is None:
                issues.append(self.runtime.foundation.issue(
                    "legacy_adoption_contract_head_capture_failed",
                    "Current committed DeclGraph head could not be captured.",
                    object_ref=node.path,
                    details={"issues": "; ".join(issue.kind for issue in head.issues)},
                ))
                continue
            if not head.value:
                issues.append(self.runtime.foundation.issue(
                    "legacy_adoption_contract_head_capture_failed",
                    "An active legacy Content node has an empty committed DeclGraph.",
                    object_ref=node.path,
                ))
                continue
            migration_required = not value.decl_graph_head
            if value.decl_graph_head and value.decl_graph_head != head.value:
                issues.append(self.runtime.foundation.issue(
                    "legacy_adoption_contract_head_capture_failed",
                    "Existing Content contract head differs from current committed DeclGraph truth.",
                    object_ref=node.path,
                ))
                continue
            views.append(LegacyContractHeadAdoptionView(
                node_path=node.path,
                node_id=node.node_id,
                current_contract_version=value.version,
                adopted_contract_version=value.version + 1 if migration_required else value.version,
                decl_graph_head=dict(sorted(head.value.items())),
                migration_required=migration_required,
                summary=(
                    "A new committed head-bound contract version will be created."
                    if migration_required else "The committed contract head is already current."
                ),
            ))
        gate = (
            self.runtime.foundation.gate_failed(
                "legacy_adoption_contract_heads", issues,
                summary="Legacy Content contract heads cannot be adopted.",
            )
            if issues else self.runtime.foundation.gate_passed(
                "legacy_adoption_contract_heads",
                summary=f"Inspected {len(views)} legacy Content contract heads.",
            )
        )
        return self.runtime.foundation.ok((views, gate))

    def _legacy_adoption_failure_with_restore(
        self, repo_root: Path, *, checkpoint_id: str, issues: list
    ) -> ServiceResult[LegacyStableAdoptionView]:
        restored = self.runtime.validation_snapshot.snapshot_restore.restore_repo_checkpoint_snapshot(
            repo_root,
            snapshot_id=checkpoint_id,
            dry_run=False,
            leave_runtime_paused=True,
            prune_extra_files=True,
        )
        controller = self.runtime.ark.pause_controller
        if controller is not None:
            controller.pause(None)
        if not restored.ok:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "legacy_adoption_restore_failed",
                "Legacy adoption failed and its pre-adoption checkpoint could not be restored.",
                object_ref=checkpoint_id,
                details={
                    "adoption_issues": "; ".join(issue.kind for issue in issues),
                    "restore_issues": "; ".join(issue.kind for issue in restored.issues),
                },
            ))
        return self.runtime.foundation.fail(issues or [self.runtime.foundation.issue(
            "legacy_adoption_failed", "Legacy adoption failed and was restored.",
            object_ref=checkpoint_id,
        )])

    def _check_workflow_closeout(
        self,
        repo_root: Path,
        *,
        owner_flow_id: str | None,
        stable_hook: bool,
        submission_intent_preview: bool = False,
    ) -> ServiceResult[GateReport]:
        issues = []
        try:
            flows = self.runtime.list_flows()
        except RuntimeError as exc:
            issue = self.runtime.foundation.issue(
                "release_workflow_inspection_failed",
                "Repository workflow closeout could not inspect Flow truth.",
                field="flows",
                details={"error": str(exc)},
            )
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "release_workflow_closeout",
                    issue,
                    summary="Repository workflow closeout inspection failed.",
                )
            )
        try:
            steps = self.runtime.list_steps()
        except RuntimeError as exc:
            steps = []
            issues.append(self.runtime.foundation.issue(
                "release_workflow_inspection_failed",
                "Repository workflow closeout could not inspect Step truth.",
                field="steps",
                details={"error": str(exc)},
            ))
        repo_scope = f"repo:{repo_root.name}"
        owner = None
        for flow in flows:
            if getattr(flow, "flow_id", None) == owner_flow_id:
                owner = flow
            scope_id = str(getattr(flow, "scope_id", ""))
            if scope_id != repo_scope and not scope_id.startswith(f"{repo_scope}:node:"):
                continue
            status = getattr(flow, "status", None)
            if status in {FlowStatus.COMPLETED, FlowStatus.FAILED}:
                continue
            if getattr(flow, "flow_id", None) != owner_flow_id:
                issues.append(self.runtime.foundation.issue(
                    "release_workflow_not_closed", "Another repo workflow is still nonterminal.",
                    object_ref=getattr(flow, "flow_id", None),
                ))
        if owner_flow_id is not None and owner is None and flows:
            issues.append(self.runtime.foundation.issue(
                "release_workflow_owner_invalid", "Candidate release owner Flow is missing.", object_ref=owner_flow_id
            ))
        elif owner is not None and getattr(owner, "flow_type", None) != "native_repo_coordinator":
            issues.append(self.runtime.foundation.issue(
                "release_workflow_owner_invalid", "Candidate release owner must be a native Coordinator Flow.",
                object_ref=owner_flow_id,
            ))
        elif owner is not None and getattr(owner, "scope_id", None) != repo_scope:
            issues.append(self.runtime.foundation.issue(
                "release_workflow_owner_invalid",
                "Candidate release owner must belong to the repository scope.",
                object_ref=owner_flow_id,
            ))
        elif owner is not None:
            phase = str(getattr(getattr(owner, "state", None), "position", ""))
            allowed = (
                "coordinator_agent" in phase
                if submission_intent_preview
                else ("mark_repo_ready" in phase if not stable_hook else "completed" in phase)
            )
            if not allowed:
                issues.append(self.runtime.foundation.issue(
                    "release_workflow_owner_invalid",
                    "Coordinator owner is not at the release preparation/finalization phase.",
                    object_ref=owner_flow_id,
                    current=phase,
                ))
        for step in steps:
            status = str(getattr(getattr(step, "status", None), "value", getattr(step, "status", "")))
            step_scope = str(getattr(step, "scope_id", ""))
            if status != "running" or (
                step_scope != repo_scope and not step_scope.startswith(f"{repo_scope}:node:")
            ):
                continue
            allowed_ready_step = (
                not stable_hook
                and getattr(step, "flow_id", None) == owner_flow_id
                and getattr(step, "step_type", None) == (
                    "coordinator_agent_step" if submission_intent_preview else "mark_coordinator_repo_ready_step"
                )
            )
            if not allowed_ready_step:
                issues.append(self.runtime.foundation.issue(
                    "release_workflow_not_closed", "A repo Step is still running.",
                    object_ref=getattr(step, "step_id", None),
                ))
        requirements = self.runtime.repo_workspace.requirement.list_requirements(repo_root)
        if not requirements.ok or requirements.value is None:
            return self.runtime.foundation.fail(requirements.issues)
        for view in requirements.value:
            requirement = view.requirement
            if requirement.status == RepoDependencyRequirementStatus.OPEN or self.runtime.repo_workspace.requirement.is_requirement_waiting(requirement):
                issues.append(self.runtime.foundation.issue(
                    "release_workflow_not_closed", "A repo requirement is open or waiting.", object_ref=requirement.name
                ))
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_failed("release_workflow_closeout", issues)
            if issues else self.runtime.foundation.gate_passed("release_workflow_closeout", summary="Repo workflow is closed for release.")
        )

    def _remove_unpublished_release(self, repo_root: Path, release_id: str) -> None:
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
        if publication.ok and publication.value is not None and publication.value.publication.latest_release_id == release_id:
            return
        path = self.runtime.foundation.layout.release_path(
            FoundationContext(repo_root=repo_root, caller="release_finalizer.cleanup"), release_id
        )
        path.unlink(missing_ok=True)
        if path.exists():
            raise OSError(f"release cleanup did not remove {path}")

    def _cleanup_precommit_artifacts(
        self,
        repo_root: Path,
        *,
        release_id: str | None,
        checkpoint_id: str | None,
    ):
        errors: list[str] = []
        if release_id is not None:
            try:
                self._remove_unpublished_release(repo_root, release_id)
            except OSError as exc:
                errors.append(str(exc))
        if checkpoint_id is not None:
            try:
                self._remove_unpublished_checkpoint(repo_root, checkpoint_id)
            except OSError as exc:
                errors.append(str(exc))
        if not errors:
            return None
        return self.runtime.foundation.issue(
            "release_precommit_cleanup_failed",
            "Release commit failed before publication and artifact cleanup was incomplete.",
            details={"errors": "; ".join(errors)},
        )

    def _remove_unpublished_checkpoint(self, repo_root: Path, checkpoint_id: str) -> None:
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
        if publication.ok and publication.value is not None and publication.value.publication.latest_release_id:
            latest = self.runtime.repo_workspace.release.get_release(
                repo_root, release_id=publication.value.publication.latest_release_id
            )
            if latest.ok and latest.value is not None and latest.value.release.repo_checkpoint_id == checkpoint_id:
                return
        path = self.runtime.validation_snapshot.snapshot_restore._snapshot_dir(repo_root, checkpoint_id)
        if path.exists():
            shutil.rmtree(path)
        if path.exists():
            raise OSError(f"checkpoint cleanup did not remove {path}")


__all__ = [
    "CandidateReleaseGateView", "CandidateReleasePreparationView", "PreparedRepoReleaseView",
    "LegacyContractHeadAdoptionView", "LegacyStableAdoptionPreviewView", "LegacyStableAdoptionView",
    "ProviderRequirementReconciliationView", "RepoReleaseFinalizeView", "RepoReleaseFinalizerComponent",
    "RepoReleaseStorageAuditView",
]
