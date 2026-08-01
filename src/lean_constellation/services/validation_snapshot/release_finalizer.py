"""Native repo candidate release gates and publication transaction."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.preparation import RepoDependencyRequirementStatus
from lean_constellation.domain.repo import (
    RepoCompletionMode,
    RepoFormat,
    RepoModel,
    RepoPublicationState,
    RepoPublicationStatus,
    RepoPublicationView,
    completion_mode_satisfies,
    proof_availability_for_completion_mode,
    proof_availability_satisfies,
)
from lean_constellation.domain.repo_release import (
    RepoRelease,
    RepoReleaseKind,
    RepoReleaseView,
    RepoReleaseValidationProfile,
)
from lean_constellation.domain.publication import PushPolicy
from lean_constellation.services.decl_graph.models import DeclRoundStatus, DeclStrategyStatus
from lean_constellation.services.external_clients import ToolchainCommandView
from lean_constellation.services.foundation import GateReport, MutationSummaryView, ServiceResult, WriteMode
from lean_constellation.services.foundation import FoundationContext
from lean_constellation.services.node import ContractVersionStatus, NodeKind, NodeLifecycle
from lean_constellation.services.validation_snapshot.snapshot_restore import (
    RepoCheckpointKind,
    RepoCheckpointSnapshotView,
)
from lean_constellation.services.repo_workspace.git_release import (
    GitReleaseCommitView,
    GitReleaseRestorePreview,
    GitReleaseRestoreView,
    GitReleaseValidationView,
)

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class CandidateReleaseGateView(StrictModel):
    base_release_id: str | None = None
    candidate_node_contract_versions: dict[str, int] = Field(default_factory=dict)
    completion_mode: RepoCompletionMode
    gate: GateReport
    blocking_issue_kinds: list[str] = Field(default_factory=list)
    summary: str


class PreparedRepoReleaseView(StrictModel):
    release: RepoRelease
    publication: RepoPublicationState
    candidate_digest: str
    expected_git_head: str | None = None
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
    git_release: GitReleaseCommitView | GitReleaseValidationView
    checkpoint: RepoCheckpointSnapshotView | None = None
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


class RepoReleaseFinalizerComponent:
    """Prepare and publish native releases without exposing partial latest truth."""

    _EXCLUDED_TOP_LEVEL = {
        ".agent_runtime",
        ".git",
        ".lake",
        ".pytest_cache",
        ".runtime",
        "__pycache__",
    }
    _EXCLUDED_CONSTELLATION_CHILDREN = {".locks", "locks", "snapshots", "staging"}
    _SEMANTIC_EXCLUDED_ROOT_FILES = {
        ".gitignore",
        "API.md",
        "PROVENANCE.md",
        "README.md",
        "lake-manifest.json",
        "lakefile.lean",
        "lakefile.toml",
    }

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def preview_candidate_release(
        self,
        repo_root: Path,
        *,
        base_release_id: str | None,
        summary: str,
    ) -> ServiceResult[CandidateReleaseGateView]:
        return self._preview_release(
            repo_root,
            base_release_id=base_release_id,
            summary=summary,
        )

    def _preview_release(
        self,
        repo_root: Path,
        *,
        base_release_id: str | None,
        summary: str,
    ) -> ServiceResult[CandidateReleaseGateView]:
        repo_root = Path(repo_root)
        reports: list[GateReport] = []
        node_versions: dict[str, int] = {}

        base = self._check_base(repo_root, base_release_id=base_release_id, summary=summary)
        if not base.ok or base.value is None:
            return self.runtime.foundation.fail(base.issues)
        reports.append(base.value)

        requirement_closeout = self._check_requirement_closeout(repo_root)
        if not requirement_closeout.ok or requirement_closeout.value is None:
            return self.runtime.foundation.fail(requirement_closeout.issues)
        reports.append(requirement_closeout.value)

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
                item.round_id
                for item in rounds.value
                if item.status
                in {
                    DeclRoundStatus.DRAFT,
                    DeclRoundStatus.RUNNING,
                    DeclRoundStatus.AWAITING_CLOSEOUT,
                }
                or (
                    item.status == DeclRoundStatus.COMMITTED
                    and item.plan_closeout_acknowledged_at is None
                )
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
                    "Content node has unfinished or unacknowledged declaration rounds.",
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
        public_closure = self.runtime.node.public_statement_closure.check_scope(
            repo_root,
            scope_path="Main",
            visible=True,
        )
        if not public_closure.ok or public_closure.value is None:
            return self.runtime.foundation.fail(public_closure.issues)
        reports.append(
            public_closure.value.model_copy(
                update={"gate_name": "candidate_release_public_statement_closure"}
            )
        )

        gate = self.runtime.foundation.merge_gate_reports("candidate_repo_release", reports)
        config = self.runtime.repo_workspace.metadata.get_repo_config(repo_root)
        if not config.ok or config.value is None:
            return self.runtime.foundation.fail(config.issues)
        blocking = sorted({issue.kind for issue in gate.issues if self.runtime.foundation.result_error.is_error_issue(issue)})
        return self.runtime.foundation.ok(CandidateReleaseGateView(
            base_release_id=base_release_id,
            candidate_node_contract_versions=dict(sorted(node_versions.items())),
            completion_mode=config.value.config.completion_mode,
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
    ) -> ServiceResult[CandidateReleasePreparationView]:
        preview = self.preview_candidate_release(
            repo_root, base_release_id=base_release_id, summary=summary
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
        release_id_result = self.runtime.repo_workspace.release.allocate_release_id(
            Path(repo_root)
        )
        if not release_id_result.ok or release_id_result.value is None:
            return self.runtime.foundation.fail(release_id_result.issues)
        semantic_digest = self.compute_semantic_manifest_digest(Path(repo_root))
        release = RepoRelease(
            release_id=release_id_result.value,
            parent_release_id=base_release_id,
            release_kind=RepoReleaseKind.SEMANTIC,
            validation_profile=RepoReleaseValidationProfile.SEMANTIC_FULL,
            node_contract_versions=preview.value.candidate_node_contract_versions,
            completion_mode=preview.value.completion_mode,
            semantic_manifest_digest=semantic_digest,
            dependency_lock_digest=self.compute_dependency_lock_digest(
                Path(repo_root)
            ),
            summary=summary,
        )
        publication_files = (
            self.runtime.repo_workspace.publication.prepare_publication(
                Path(repo_root),
                release_id=release.release_id,
                semantic_manifest_digest=semantic_digest,
                generated_at=release.created_at,
            )
        )
        if not publication_files.ok:
            self._refresh_publication_documents_for_current_release(Path(repo_root))
            return self.runtime.foundation.fail(publication_files.issues)
        build = self.runtime.external.lean_toolchain.run_lake_build(Path(repo_root))
        if not build.ok:
            self._refresh_publication_documents_for_current_release(Path(repo_root))
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
        git_state = self.runtime.repo_workspace.git_release.inspect_repo(Path(repo_root))
        if not git_state.ok or git_state.value is None:
            self._refresh_publication_documents_for_current_release(Path(repo_root))
            return self.runtime.foundation.fail(git_state.issues)
        publication = RepoPublicationState(
            status=RepoPublicationStatus.STABLE,
            latest_release_id=release.release_id,
        )
        digest = self.compute_candidate_digest(Path(repo_root))
        prepared = PreparedRepoReleaseView(
            release=release,
            publication=publication,
            candidate_digest=digest,
            expected_git_head=(
                git_state.value.head_commit
                if git_state.value.initialized and git_state.value.independent
                else None
            ),
            build=build,
            gate=preview.value.gate,
            summary=f"Prepared candidate release {release.release_id}.",
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
    ) -> ServiceResult[RepoReleaseFinalizeView]:
        return self._commit_prepared_release_transaction(
            repo_root,
            prepared=prepared,
        )

    def _commit_prepared_release_transaction(
        self,
        repo_root: Path,
        *,
        prepared: PreparedRepoReleaseView,
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
        original_publication = current_publication.value.publication
        original_model = self.runtime.repo_workspace.metadata.get_repo_model(repo_root)
        if not original_model.ok or original_model.value is None:
            return self.runtime.foundation.fail(original_model.issues)
        original_repo_model = original_model.value
        committed_release_view: RepoReleaseView | None = None
        git_commit: GitReleaseCommitView | None = None
        transaction_warnings = []
        try:
            with self.runtime.repo_workspace.lifecycle_lock.locked(repo_root):
                publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
                if not publication.ok or publication.value is None:
                    return self.runtime.foundation.fail(publication.issues)
                current = publication.value.publication
                dependency_maintenance = (
                    prepared.release.release_kind
                    == RepoReleaseKind.DEPENDENCY_MAINTENANCE
                )
                valid_status = (
                    current.status == RepoPublicationStatus.STABLE
                    if dependency_maintenance
                    else current.status == RepoPublicationStatus.DEVELOPING
                )
                if (
                    not valid_status
                    or current.latest_release_id
                    != prepared.release.parent_release_id
                ):
                    return self.runtime.foundation.fail(self.runtime.foundation.issue(
                        "release_base_mismatch",
                        "Publication baseline changed after candidate preparation.",
                        current=current.latest_release_id,
                        expected=prepared.release.parent_release_id,
                    ))
                if not dependency_maintenance:
                    requirement_closeout = self._check_requirement_closeout(repo_root)
                    if (
                        not requirement_closeout.ok
                        or requirement_closeout.value is None
                    ):
                        return self.runtime.foundation.fail(
                            requirement_closeout.issues
                        )
                    if not requirement_closeout.value.passed:
                        return self.runtime.foundation.fail(
                            requirement_closeout.value.issues
                        )
                digest = self.compute_candidate_digest(repo_root)
                if digest != prepared.candidate_digest:
                    return self.runtime.foundation.fail(self.runtime.foundation.issue(
                        "release_candidate_drift", "Candidate truth changed after preparation.",
                        current=digest, expected=prepared.candidate_digest,
                    ))
                semantic_digest = self.compute_semantic_manifest_digest(repo_root)
                if semantic_digest != prepared.release.semantic_manifest_digest:
                    return self.runtime.foundation.fail(self.runtime.foundation.issue(
                        "release_semantic_manifest_drift",
                        "Semantic Release truth changed after preparation.",
                        current=semantic_digest,
                        expected=prepared.release.semantic_manifest_digest,
                    ))
                dependency_digest = self.compute_dependency_lock_digest(repo_root)
                if dependency_digest != prepared.release.dependency_lock_digest:
                    return self.runtime.foundation.fail(self.runtime.foundation.issue(
                        "release_dependency_lock_drift",
                        "Dependency lock truth changed after preparation.",
                        current=dependency_digest,
                        expected=prepared.release.dependency_lock_digest,
                    ))
                initialized = self.runtime.repo_workspace.git_release.ensure_independent_repo(repo_root)
                if not initialized.ok or initialized.value is None:
                    return self.runtime.foundation.fail(initialized.issues)
                if initialized.value.head_commit != prepared.expected_git_head:
                    return self.runtime.foundation.fail(self.runtime.foundation.issue(
                        "git_release_head_drift",
                        "Repository Git HEAD changed after Release preparation.",
                        current=initialized.value.head_commit or "<unborn>",
                        expected=prepared.expected_git_head or "<unborn>",
                    ))
                if not dependency_maintenance:
                    try:
                        summary_sync = (
                            self.runtime.repo_workspace.metadata.set_repo_summary(
                                repo_root,
                                summary=prepared.release.summary,
                            )
                        )
                    except Exception:
                        summary_sync = None
                    if summary_sync is None or not summary_sync.ok:
                        transaction_warnings.append(
                            self.runtime.foundation.issue(
                                "release_summary_sync_pending",
                                "Release can proceed, but display summary synchronization is pending.",
                                severity="warning",
                                object_ref=prepared.release.release_id,
                            )
                        )
                created = self.runtime.repo_workspace.release.create_release(
                    repo_root,
                    release=prepared.release,
                )
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
                    if exact_visible_release:
                        self._remove_unpublished_release(repo_root, prepared.release.release_id)
                    if conflicting_visible_release:
                        return self.runtime.foundation.fail(self.runtime.foundation.issue(
                            "release_identity_conflict",
                            "Release CREATE_ONLY failed because the target path contains a different payload; it was not removed.",
                            object_ref=prepared.release.release_id,
                        ))
                    return self.runtime.foundation.fail(created.issues)
                committed_release_view = created.value
                publication_path = self.runtime.repo_workspace.metadata._repo_publication_path(repo_root)
                committed = self.runtime.foundation.store.write_json_atomic(
                    publication_path, prepared.publication, mode=WriteMode.OVERWRITE
                )
                if not committed.ok:
                    self._rollback_release_worktree(
                        repo_root,
                        release_id=prepared.release.release_id,
                        publication=original_publication,
                        repo_model=original_repo_model,
                    )
                    return self.runtime.foundation.fail(self.runtime.foundation.issue(
                        "release_publication_commit_failed",
                        "Failed to stage the stable publication pointer.",
                        details={"issues": "; ".join(issue.kind for issue in committed.issues)},
                    ))
                candidate_files = [
                    path.relative_to(repo_root).as_posix()
                    for path in self._candidate_files(repo_root)
                ]
                policy = self.runtime.repo_workspace.publication.resolve_policy(
                    repo_root
                )
                if not policy.ok or policy.value is None:
                    self._rollback_release_worktree(
                        repo_root,
                        release_id=prepared.release.release_id,
                        publication=original_publication,
                        repo_model=original_repo_model,
                    )
                    return self.runtime.foundation.fail(policy.issues)
                published = self.runtime.repo_workspace.git_release.commit_release(
                    repo_root,
                    release=prepared.release,
                    candidate_files=candidate_files,
                    expected_head=prepared.expected_git_head,
                    commit_message=(
                        f"chore(deps): {prepared.release.summary}"
                        if dependency_maintenance
                        else f"release(repo): {prepared.release.summary}"
                    ),
                    commit_identity=policy.value.policy.commit_identity,
                )
                if not published.ok or published.value is None:
                    self._rollback_release_worktree(
                        repo_root,
                        release_id=prepared.release.release_id,
                        publication=original_publication,
                        repo_model=original_repo_model,
                    )
                    return self.runtime.foundation.fail(published.issues)
                git_commit = published.value
        except Exception as exc:  # lifecycle lock and filesystem failures
            if git_commit is not None and committed_release_view is not None:
                warning = self.runtime.foundation.issue(
                    "release_postcommit_followup_pending",
                    f"Release publication committed; a later transaction follow-up failed: {exc}",
                    severity="warning",
                    object_ref=prepared.release.release_id,
                )
                return self.runtime.foundation.ok(RepoReleaseFinalizeView(
                    release=committed_release_view,
                    git_release=git_commit,
                    publication=RepoPublicationView(repo_root=str(repo_root), publication=prepared.publication),
                    reconciliation=ProviderRequirementReconciliationView(
                        release_id=prepared.release.release_id,
                        pending=["provider_requirement_reconciliation"],
                        summary="Post-commit reconciliation is pending.",
                    ),
                    notification_pending=True,
                    summary=f"Committed native repo release {prepared.release.release_id}; follow-up pending.",
                ), warnings=[warning])
            self._rollback_release_worktree(
                repo_root,
                release_id=(
                    prepared.release.release_id
                    if committed_release_view is not None
                    else None
                ),
                publication=original_publication,
                repo_model=original_repo_model,
            )
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "release_truth_publish_failed", f"Release publication transaction failed: {exc}",
                object_ref=prepared.release.release_id,
            ))

        if committed_release_view is None or git_commit is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "release_commit_state_missing",
                "Publication committed without in-memory Git Release evidence.",
            ))
        validation = self.runtime.repo_workspace.git_release.validate_release(
            repo_root,
            release=prepared.release,
        )
        warnings = list(transaction_warnings)
        if not validation.ok:
            warnings.append(self.runtime.foundation.issue(
                "release_git_readback_pending",
                "Git Release committed, but post-commit readback validation requires follow-up.",
                severity="warning",
                object_ref=prepared.release.release_id,
                details={"issues": "; ".join(issue.kind for issue in validation.issues)},
            ))
        checkpoint_view = None
        checkpoint_policy = (
            self.runtime.repo_workspace.publication.resolve_policy(repo_root)
        )
        if (
            checkpoint_policy.ok
            and checkpoint_policy.value is not None
            and checkpoint_policy.value.policy.post_release_checkpoint
        ):
            checkpoint = self._create_post_release_checkpoint(
                repo_root,
                release=prepared.release,
            )
            checkpoint_view = checkpoint.value if checkpoint.ok else None
            if not checkpoint.ok:
                warnings.append(self.runtime.foundation.issue(
                    "release_post_checkpoint_pending",
                    "Git Release committed, but the optional operational checkpoint could not be created.",
                    severity="warning",
                    object_ref=prepared.release.release_id,
                    details={"issues": "; ".join(issue.kind for issue in checkpoint.issues)},
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
        policy = self.runtime.repo_workspace.publication.resolve_policy(repo_root)
        if (
            policy.ok
            and policy.value is not None
            and policy.value.policy.push_policy == PushPolicy.ON_RELEASE
        ):
            push_preview = self.runtime.repo_workspace.remote_publication.preview(
                repo_root,
                release_id=prepared.release.release_id,
            )
            if push_preview.ok and push_preview.value is not None:
                pushed = self.runtime.repo_workspace.remote_publication.apply(
                    repo_root,
                    preview=push_preview.value,
                    expected_recovery_token=push_preview.value.recovery_token,
                    push=True,
                )
            else:
                pushed = push_preview
            if not pushed.ok:
                warnings.append(
                    self.runtime.foundation.issue(
                        "release_remote_publication_pending",
                        "Local Git Release committed, but configured remote publication is pending.",
                        severity="warning",
                        object_ref=prepared.release.release_id,
                        details={
                            "issues": "; ".join(
                                issue.kind for issue in pushed.issues
                            )
                        },
                    )
                )
        result = RepoReleaseFinalizeView(
            release=committed_release_view,
            git_release=git_commit,
            checkpoint=checkpoint_view,
            publication=RepoPublicationView(repo_root=str(repo_root), publication=prepared.publication),
            reconciliation=reconciliation_view,
            notification_pending=bool(reconciliation_view.pending or reconciliation_view.conflicts),
            summary=f"Committed native repo release {prepared.release.release_id}.",
        )
        return self.runtime.foundation.ok(result, warnings=warnings)

    def _finalize_existing_release(
        self, repo_root: Path, *, prepared: PreparedRepoReleaseView
    ) -> ServiceResult[RepoReleaseFinalizeView]:
        git_release = self.runtime.repo_workspace.git_release.validate_release(
            repo_root,
            release=prepared.release,
        )
        if not git_release.ok or git_release.value is None:
            return self.runtime.foundation.fail(git_release.issues)
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
            git_release=git_release.value,
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
        provider_commit = self.runtime.repo_workspace.git_release.resolve_release_commit(
            provider_root, release_id=release_id
        )
        if not provider_commit.ok or provider_commit.value is None:
            return self.runtime.foundation.fail(provider_commit.issues)
        provider_policy = self.runtime.repo_workspace.publication.resolve_policy(
            provider_root
        )
        if not provider_policy.ok or provider_policy.value is None:
            return self.runtime.foundation.fail(provider_policy.issues)
        provider_git_url = (
            provider_policy.value.policy.canonical_fetch_url
            or provider_root.resolve().as_uri()
        )
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
                proof_availability_for_completion_mode(
                    config.value.config.completion_mode
                ),
                requirement.required_proof_availability,
            ):
                conflicts.append(key)
                continue
            if requirement.status in {
                RepoDependencyRequirementStatus.SATISFIED,
                RepoDependencyRequirementStatus.HANDLED,
            }:
                if (
                    requirement.provider_release_id != release_id
                    or requirement.provider_commit != provider_commit.value
                    or requirement.provider_git_url != provider_git_url
                ):
                    refreshed = (
                        self.runtime.repo_workspace.requirement.record_requirement_provider_release(
                            consumer,
                            requirement_name=requirement.name,
                            provider_repo=provider_key,
                            provider_release_id=release_id,
                            provider_commit=provider_commit.value,
                            provider_git_url=provider_git_url,
                            note=f"Provider release {release_id} is ready.",
                        )
                    )
                    if not refreshed.ok:
                        pending.append(key)
                        continue
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
                provider_release_id=release_id,
                provider_commit=provider_commit.value,
                provider_git_url=provider_git_url,
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
        if latest_id is not None:
            lineage = self.runtime.repo_workspace.release.resolve_release_lineage(repo_root, release_id=latest_id)
            if not lineage.ok or lineage.value is None:
                issues.extend(issue.kind for issue in lineage.issues)
            else:
                reachable = {item.release_id for item in lineage.value}
                for lineage_release in lineage.value:
                    git_release = self.runtime.repo_workspace.git_release.validate_release(
                        repo_root,
                        release=lineage_release,
                    )
                    if not git_release.ok:
                        issues.extend(issue.kind for issue in git_release.issues)
        all_release_ids = {item.release.release_id for item in releases.value}
        if not all_release_ids and latest_id is None:
            ref_release_ids: set[str] = set()
        else:
            git_refs = self.runtime.repo_workspace.git_release.list_release_refs(repo_root)
            if not git_refs.ok or git_refs.value is None:
                issues.extend(issue.kind for issue in git_refs.issues)
                ref_release_ids = set()
            else:
                ref_release_ids = set(git_refs.value)
        for missing_ref in sorted(all_release_ids - ref_release_ids):
            issues.append(f"release_ref_missing:{missing_ref}")
        for orphan_ref in sorted(ref_release_ids - all_release_ids):
            issues.append(f"release_ref_without_manifest:{orphan_ref}")
        checkpoint_root = self.runtime.validation_snapshot.snapshot_restore._snapshot_root(repo_root)
        staging_root = checkpoint_root / ".staging"
        staging = [str(path) for path in sorted(staging_root.iterdir())] if staging_root.exists() else []
        orphan_releases = sorted(all_release_ids - reachable)
        orphan_checkpoints: list[str] = []
        if orphan_releases:
            issues.append("orphan_repo_release")
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
                        deleted_ref = self.runtime.repo_workspace.git_release.delete_release_ref(
                            repo_root,
                            release_id=release_id,
                        )
                        if not deleted_ref.ok:
                            return self.runtime.foundation.fail(deleted_ref.issues)
                        self._remove_unpublished_release(repo_root, release_id)
                        changed.append(f"release:{release_id}")
                elif checkpoint_id is not None:
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

    def preview_repo_release_restore(
        self,
        repo_root: Path,
        *,
        release_id: str,
    ) -> ServiceResult[GitReleaseRestorePreview]:
        repo_root = Path(repo_root)
        try:
            with self.runtime.repo_workspace.lifecycle_lock.locked(repo_root):
                release = (
                    self.runtime.repo_workspace.git_release.read_release_manifest(
                        repo_root,
                        release_id=release_id,
                    )
                )
                if not release.ok or release.value is None:
                    return self.runtime.foundation.fail(release.issues)
                validated = self.runtime.repo_workspace.git_release.validate_release(
                    repo_root,
                    release=release.value,
                )
                if not validated.ok:
                    return self.runtime.foundation.fail(validated.issues)
                return self.runtime.repo_workspace.git_release.preview_restore_release(
                    repo_root,
                    release_id=release_id,
                )
        except Exception as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "release_restore_preview_failed",
                f"Release restore preview failed: {exc}",
                object_ref=release_id,
            ))

    def apply_repo_release_restore(
        self,
        repo_root: Path,
        *,
        preview: GitReleaseRestorePreview,
        expected_recovery_token: str,
    ) -> ServiceResult[GitReleaseRestoreView]:
        repo_root = Path(repo_root)
        try:
            with self.runtime.repo_workspace.lifecycle_lock.locked(repo_root):
                release = (
                    self.runtime.repo_workspace.git_release.read_release_manifest(
                        repo_root,
                        release_id=preview.release_id,
                    )
                )
                if not release.ok or release.value is None:
                    return self.runtime.foundation.fail(release.issues)
                validated = self.runtime.repo_workspace.git_release.validate_release(
                    repo_root,
                    release=release.value,
                )
                if not validated.ok:
                    return self.runtime.foundation.fail(validated.issues)
                restored = (
                    self.runtime.repo_workspace.git_release.apply_restore_release(
                        repo_root,
                        preview=preview,
                        expected_recovery_token=expected_recovery_token,
                    )
                )
                if not restored.ok or restored.value is None:
                    return self.runtime.foundation.fail(restored.issues)
                current = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
                if (
                    not current.ok or current.value is None
                    or current.value.publication.status != RepoPublicationStatus.STABLE
                    or current.value.publication.latest_release_id != preview.release_id
                ):
                    return self.runtime.foundation.fail(self.runtime.foundation.issue(
                        "release_storage_corrupt",
                        "Restored release publication truth is inconsistent.",
                        object_ref=preview.release_id,
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
                "release_restore_failed",
                f"Release restore failed: {exc}",
                object_ref=preview.release_id,
            ))

    def compute_candidate_digest(self, repo_root: Path) -> str:
        repo_root = Path(repo_root)
        return self._digest_files(repo_root, self._candidate_files(repo_root))

    def compute_semantic_manifest_digest(self, repo_root: Path) -> str:
        repo_root = Path(repo_root)
        semantic_files = []
        for path in self._candidate_files(repo_root):
            relpath = path.relative_to(repo_root)
            if relpath.as_posix() in self._SEMANTIC_EXCLUDED_ROOT_FILES:
                continue
            if relpath.parts[:2] == (".lean_constellation", "releases"):
                continue
            if relpath.as_posix() in {
                ".lean_constellation/repo.json",
                ".lean_constellation/repo_publication.json",
            }:
                continue
            if (
                len(relpath.parts) >= 2
                and relpath.parts[0] == ".lean_constellation"
                and relpath.parts[1] in {"publication", "release_receipts"}
            ):
                continue
            if relpath.parts[:2] == ("docs", "lean-constellation"):
                continue
            semantic_files.append(path)
        return self._digest_files(repo_root, semantic_files)

    def compute_dependency_lock_digest(self, repo_root: Path) -> str:
        repo_root = Path(repo_root)
        parsed = self.runtime.repo_workspace.lake_dependency.parse_lake_dependencies(repo_root)
        if not parsed.ok or parsed.value is None:
            payload: list[dict[str, object]] = []
        else:
            payload = [
                {
                    "name": item.name,
                    "source": item.source,
                    "scope": item.scope,
                    "path": item.path if item.source == "path" else None,
                    "rev": item.rev,
                    "subdir": item.subdir,
                }
                for item in sorted(
                    parsed.value.dependencies,
                    key=lambda dependency: (
                        dependency.name,
                        dependency.git or "",
                        dependency.path or "",
                    ),
                )
            ]
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _digest_files(repo_root: Path, files: list[Path]) -> str:
        digest = hashlib.sha256()
        for path in sorted(files, key=lambda item: item.relative_to(repo_root).as_posix()):
            relpath = path.relative_to(repo_root).as_posix()
            digest.update(relpath.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def _create_post_release_checkpoint(
        self,
        repo_root: Path,
        *,
        release: RepoRelease,
    ) -> ServiceResult[RepoCheckpointSnapshotView]:
        return self.runtime.validation_snapshot.snapshot_restore.create_repo_checkpoint_archive(
            repo_root,
            checkpoint_kind=RepoCheckpointKind.REPO_RELEASE,
            label=f"post-release {release.release_id}",
        )

    def _rollback_release_worktree(
        self,
        repo_root: Path,
        *,
        release_id: str | None,
        publication: RepoPublicationState,
        repo_model: RepoModel,
    ) -> None:
        publication_path = self.runtime.repo_workspace.metadata._repo_publication_path(repo_root)
        current_publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
        if (
            not current_publication.ok
            or current_publication.value is None
            or current_publication.value.publication != publication
        ):
            restored = self.runtime.foundation.store.write_json_atomic(
                publication_path,
                publication,
                mode=WriteMode.OVERWRITE,
            )
            if not restored.ok:
                raise OSError(
                    "failed to restore publication truth: "
                    + "; ".join(issue.kind for issue in restored.issues)
                )
        if release_id is not None:
            self._remove_unpublished_release(repo_root, release_id)
        current_model = self.runtime.repo_workspace.metadata.get_repo_model(repo_root)
        if not current_model.ok or current_model.value != repo_model:
            restored_model = self.runtime.foundation.store.write_json_atomic(
                self.runtime.foundation.layout.repo_metadata_path(
                    FoundationContext(repo_root=repo_root)
                ),
                repo_model,
                mode=WriteMode.OVERWRITE,
            )
            if not restored_model.ok:
                raise OSError(
                    "failed to restore repo model: "
                    + "; ".join(issue.kind for issue in restored_model.issues)
                )
        self._refresh_publication_documents_for_current_release(repo_root)

    def _refresh_publication_documents_for_current_release(
        self,
        repo_root: Path,
    ) -> bool:
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(
            repo_root
        )
        if not publication.ok or publication.value is None:
            return False
        release_id = publication.value.publication.latest_release_id
        semantic_digest = None
        if release_id is not None:
            release = self.runtime.repo_workspace.release.get_release(
                repo_root,
                release_id=release_id,
            )
            if release.ok and release.value is not None:
                semantic_digest = release.value.release.semantic_manifest_digest
                generated_at = release.value.release.created_at
            else:
                generated_at = None
        else:
            generated_at = None
        refreshed = self.runtime.repo_workspace.publication.prepare_publication(
            repo_root,
            release_id=release_id,
            semantic_manifest_digest=semantic_digest,
            generated_at=generated_at,
        )
        return refreshed.ok

    def _candidate_files(self, repo_root: Path) -> list[Path]:
        files: list[Path] = []
        policy = self.runtime.repo_workspace.publication.resolve_policy(repo_root)
        include_lake_manifest = (
            policy.value.policy.include_lake_manifest
            if policy.ok and policy.value is not None
            else True
        )
        for path in repo_root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(repo_root)
            if rel.as_posix() == "lake-manifest.json" and not include_lake_manifest:
                continue
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
            git_release = self.runtime.repo_workspace.git_release.validate_release(
                repo_root,
                release=base_release.value.release,
            )
            if not git_release.ok:
                return self.runtime.foundation.fail(self.runtime.foundation.issue(
                    "release_baseline_corrupt",
                    "The base Git Release ref, commit, or manifest is missing or invalid.",
                    object_ref=base_release.value.release.release_id,
                    details={"issues": "; ".join(issue.kind for issue in git_release.issues)},
                ))
            if not completion_mode_satisfies(
                current_config.value.config.completion_mode,
                base_release.value.release.completion_mode,
            ):
                issues.append(self.runtime.foundation.issue(
                    "release_completion_regression",
                    "Candidate completion requirement is below the base release.",
                ))
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_failed("release_base", issues)
            if issues else self.runtime.foundation.gate_passed("release_base", summary="Release baseline is valid.")
        )

    def _check_requirement_closeout(self, repo_root: Path) -> ServiceResult[GateReport]:
        """Check release business truth without inspecting ARK Flow or Step state."""
        issues = []
        requirements = self.runtime.repo_workspace.requirement.list_requirements(repo_root)
        if not requirements.ok or requirements.value is None:
            return self.runtime.foundation.fail(requirements.issues)
        for view in requirements.value:
            requirement = view.requirement
            if requirement.status == RepoDependencyRequirementStatus.OPEN or self.runtime.repo_workspace.requirement.is_requirement_waiting(requirement):
                issues.append(self.runtime.foundation.issue(
                    "release_requirement_not_closed",
                    "A repo requirement is open or waiting.",
                    object_ref=requirement.name,
                ))
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_failed("release_requirement_closeout", issues)
            if issues
            else self.runtime.foundation.gate_passed(
                "release_requirement_closeout", summary="Repo requirements are closed for release."
            )
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
        path = self.runtime.validation_snapshot.snapshot_restore._snapshot_dir(repo_root, checkpoint_id)
        if path.exists():
            shutil.rmtree(path)
        if path.exists():
            raise OSError(f"checkpoint cleanup did not remove {path}")


__all__ = [
    "CandidateReleaseGateView", "CandidateReleasePreparationView", "PreparedRepoReleaseView",
    "ProviderRequirementReconciliationView", "RepoReleaseFinalizeView", "RepoReleaseFinalizerComponent",
    "RepoReleaseStorageAuditView",
]
