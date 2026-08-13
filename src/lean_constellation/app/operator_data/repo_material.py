"""Typed Repo and Material domain facade for the Operator Data API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from lean_constellation.app.operator_data.common import (
    OperatorAccess,
    OperatorInputModel,
    OperatorLockPolicy,
    OperatorOperationSpec,
    OperatorGateView,
    operator_gate_view,
    project_operator_result,
)
from lean_constellation.app.operator_data.execution import OperatorExecutionService
from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.lake_project import NativeLakeProjectConfig
from lean_constellation.domain.preparation import RepoPreparationInput, RepoPreparationInputView
from lean_constellation.domain.repo import (
    ProofAvailability,
    RepoCompletionMode,
    RepoConfig,
    RepoConfigView,
    RepoFormat,
    RepoPublicationState,
    RepoPublicationView,
    RepoPublicationStatus,
    RepoStateView,
    WorkspaceCatalogView,
    WorkspaceRepoSummary,
)
from lean_constellation.domain.repo_run import SourceScope
from lean_constellation.services.foundation import GateReport, IssueSeverity, ServiceResult
from lean_constellation.services.material import (
    MaterialContextCitationView,
    MaterialContextView,
    MaterialFileEntry,
    MaterialSearchHit,
    ResourceSummaryView,
    ResourceView,
    SourceCorpusImportView,
    SourceCorpusFileView,
    SourceCorpusManifestView,
    SourceIndexCommitView,
    SourceIndexOpenUpdateView,
    SourceIndexOverviewMutationReceipt,
    SourceIndexOverviewView,
    SourceIndexView,
    SourceBlockListItemView,
    SourceBlockView,
    SourceFileIndexView,
    SourceLinkView,
)
from lean_constellation.services.external_clients import ToolchainCommandView
from lean_constellation.services.repo_workspace.lake_dependency import (
    LakeDependencyAttachView,
    LakeDependencyEntry,
    RepoSkeletonView,
)
from lean_constellation.services.repo_workspace.service import NativeRepoCreationView
from lean_constellation.services.runtime import LeanRuntimeServices
from lean_constellation.services.validation_snapshot.source_index_checkpoint import (
    OperatorSourceIndexBaselineArtifact,
    SourceIndexCheckpointAdapter,
)


READ_REPO_MATERIAL = OperatorOperationSpec(
    name="repo_material.read",
    access=OperatorAccess.READ,
    lock_policy=OperatorLockPolicy.NONE,
)
MUTATE_REPO_MATERIAL = OperatorOperationSpec(
    name="repo_material.mutate",
    access=OperatorAccess.MUTATION,
    lock_policy=OperatorLockPolicy.OPERATOR,
    requires_stable_runtime=True,
)


class NativeRepoCreateInput(OperatorInputModel):
    project_name: str
    preparation_input: RepoPreparationInput
    completion_mode: RepoCompletionMode
    default_requirement_proof_availability: ProofAvailability = ProofAvailability.DECLARED
    native_config: NativeLakeProjectConfig


class RepoConfigUpdateInput(OperatorInputModel):
    completion_mode: RepoCompletionMode | None = None
    default_requirement_proof_availability: ProofAvailability | None = None


class DependencyAttachInput(OperatorInputModel):
    provider_repo: str


class LakeBuildInput(OperatorInputModel):
    target: str | None = None


class SourceCorpusLocalDirInput(OperatorInputModel):
    source_dir: Path
    entry_path: str
    overview: str
    preparation_summary: str
    replace_existing: bool = False
    expected_manifest_digest: str | None = None


class ResourceListInput(OperatorInputModel):
    query: str | None = None


class ResourceGetInput(OperatorInputModel):
    resource_key: str


class MaterialContextInput(OperatorInputModel):
    query: str | None = None
    scope: Literal["current_node", "source", "resource", "all"] = "current_node"
    node_path: str | None = None
    require_committed_source_index: bool = False
    regex: bool = False
    limit: int | None = Field(default=None, ge=1, le=200)


class SourceIndexOpenInput(OperatorInputModel):
    source_scope: SourceScope
    index_policy: Literal["auto", "update", "reuse"] = "auto"
    expected_baseline_digest: str


class SourceIndexExpectedDigestInput(OperatorInputModel):
    expected_current_digest: str


class SourceIndexOverviewInput(SourceIndexExpectedDigestInput):
    overview: str


class SourceIndexFileSurveyInput(SourceIndexExpectedDigestInput):
    path: str
    status: Literal["pending", "surveyed", "skipped"]
    summary: str | None = None


class SourceIndexFileIndexingInput(SourceIndexExpectedDigestInput):
    path: str
    status: Literal["pending", "indexed", "skipped"]


class SourceIndexBlockCreateInput(SourceIndexExpectedDigestInput):
    parent_id: str
    kind: str
    subtype: str | None = None
    title: str
    summary: str


class SourceIndexBlockUpdateInput(SourceIndexExpectedDigestInput):
    block_id: str
    title: str | None = None
    summary: str | None = None
    kind: str | None = None
    subtype: str | None = None


class SourceIndexBlockRefAddInput(SourceIndexExpectedDigestInput):
    block_id: str
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    role: str


class SourceIndexBlockRefRemoveInput(SourceIndexExpectedDigestInput):
    block_id: str
    ref_id: str


class SourceIndexBlockRefUpdateInput(SourceIndexBlockRefAddInput):
    ref_id: str


class SourceIndexBlockLifecycleInput(SourceIndexExpectedDigestInput):
    block_id: str


class SourceIndexLinkCreateInput(SourceIndexExpectedDigestInput):
    source_block_id: str
    target_block_id: str | None = None
    target_hint: str | None = None
    link_kind: str
    evidence_ref_ids: list[str]


class SourceIndexLinkUpdateInput(SourceIndexExpectedDigestInput):
    link_id: str
    target_block_id: str | None = None
    target_hint: str | None = None
    link_kind: str
    evidence_ref_ids: list[str]


class SourceIndexCommitInput(SourceIndexExpectedDigestInput):
    require_completed: bool = True


class SourceIndexOpenedView(StrictModel):
    opened: SourceIndexOpenUpdateView
    baseline_digest: str | None = None
    current_index_digest: str
    summary: str


class OperatorRepoStateView(StrictModel):
    main_node: str | None = None
    repo_summary: str | None = None
    repo_format: RepoFormat
    publication_status: RepoPublicationStatus
    latest_release_id: str | None = None
    completion_mode: RepoCompletionMode
    default_requirement_proof_availability: ProofAvailability
    provider_ready: bool
    readiness_policy: str
    preparation_input_exists: bool
    open_requirement_count: int
    summary: str | None = None


class OperatorRepoConfigView(StrictModel):
    config: RepoConfig


class OperatorPreparationInputView(StrictModel):
    input: RepoPreparationInput
    summary: str


class OperatorPublicationView(StrictModel):
    publication: RepoPublicationState


class OperatorRepoSkeletonView(StrictModel):
    repo_format: RepoFormat
    project_name: str
    lean_toolchain: str | None = None
    linked_packages: list[str] = Field(default_factory=list)
    lake_check_summary: str | None = None
    summary: str


class OperatorNativeRepoCreationView(StrictModel):
    repo_key: str
    project_name: str
    config: OperatorRepoConfigView
    preparation_input: OperatorPreparationInputView
    skeleton: OperatorRepoSkeletonView
    summary: str


class OperatorWorkspaceRepoView(StrictModel):
    repo_key: str
    repo_summary: str | None = None
    repo_format: RepoFormat
    publication_status: RepoPublicationStatus
    latest_release_id: str | None = None
    completion_mode: RepoCompletionMode
    provider_ready: bool
    open_requirement_count: int


class OperatorWorkspaceCatalogView(StrictModel):
    repos: list[OperatorWorkspaceRepoView] = Field(default_factory=list)


class OperatorDependencyAttachView(StrictModel):
    provider_repo_key: str
    dependency: LakeDependencyEntry
    changed: bool
    lake_update_summary: str | None = None
    summary: str


class OperatorLakeBuildView(StrictModel):
    ok: bool
    provider: str
    fallback_provider: str | None = None
    fallback_reason: str | None = None
    summary: str
    exit_code: int | None = None
    timed_out: bool
    issue_code: str | None = None


class OperatorSourceCorpusManifestView(StrictModel):
    schema_version: int
    relpath: str
    overview: str | None = None
    entry_path: str | None = None
    created_from_mode: str
    generated_at: str
    files: list[SourceCorpusFileView] = Field(default_factory=list)
    summary: str


class OperatorSourceCorpusImportView(StrictModel):
    manifest: OperatorSourceCorpusManifestView
    preparation_summary: str
    manifest_digest: str
    replaced_existing: bool
    summary: str


class OperatorResourceSummaryView(StrictModel):
    resource_key: str
    title: str | None = None
    kind: str
    summary: str


class OperatorResourceView(StrictModel):
    resource_key: str
    kind: str
    version: str | None = None
    title: str | None = None
    source_url: str | None = None
    notes: str | None = None
    canonical_entry: str
    content_hash: str | None = None
    summary: str


class OperatorMaterialContextView(StrictModel):
    node_path: str | None = None
    query: str | None = None
    scope: Literal["current_node", "source", "resource", "all"]
    source_index: SourceIndexOverviewView | None = None
    source_files: list[MaterialFileEntry] = Field(default_factory=list)
    source_blocks: list[SourceBlockListItemView] = Field(default_factory=list)
    resources: list[OperatorResourceSummaryView] = Field(default_factory=list)
    owned_refs: list[MaterialContextCitationView] = Field(default_factory=list)
    context_refs: list[MaterialContextCitationView] = Field(default_factory=list)
    matches: list[MaterialSearchHit] = Field(default_factory=list)
    returned_count: int
    total_matching_count: int
    truncated: bool
    summary: str


class OperatorSourceIndexView(StrictModel):
    schema_version: int
    status: str
    active_file_scope: list[str] = Field(default_factory=list)
    overview: str | None = None
    root_block_id: str
    blocks: dict[str, SourceBlockView] = Field(default_factory=dict)
    links: dict[str, SourceLinkView] = Field(default_factory=dict)
    files: dict[str, SourceFileIndexView] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    committed_at: str | None = None
    summary: str


class SourceIndexMutationView(StrictModel):
    value: (
        OperatorSourceIndexView
        | SourceBlockView
        | SourceLinkView
        | SourceFileIndexView
        | SourceIndexOverviewMutationReceipt
        | OperatorGateView
    )
    current_index_digest: str
    summary: str


def _repo_state_view(value: RepoStateView) -> OperatorRepoStateView:
    return OperatorRepoStateView(**value.model_dump(exclude={"repo_root"}))


def _repo_config_view(value: RepoConfigView) -> OperatorRepoConfigView:
    return OperatorRepoConfigView(config=value.config)


def _preparation_view(value: RepoPreparationInputView) -> OperatorPreparationInputView:
    return OperatorPreparationInputView(input=value.input, summary=value.summary)


def _publication_view(value: RepoPublicationView) -> OperatorPublicationView:
    return OperatorPublicationView(publication=value.publication)


def _skeleton_view(value: RepoSkeletonView) -> OperatorRepoSkeletonView:
    return OperatorRepoSkeletonView(
        repo_format=value.repo_format,
        project_name=value.project_name,
        lean_toolchain=value.lean_toolchain,
        linked_packages=value.linked_packages,
        lake_check_summary=value.lake_check_summary,
        summary=value.summary,
    )


def _native_creation_view(value: NativeRepoCreationView) -> OperatorNativeRepoCreationView:
    return OperatorNativeRepoCreationView(
        repo_key=value.repo_key,
        project_name=value.project_name,
        config=_repo_config_view(value.config),
        preparation_input=_preparation_view(value.preparation_input),
        skeleton=_skeleton_view(value.skeleton),
        summary=value.summary,
    )


def _workspace_repo_view(value: WorkspaceRepoSummary) -> OperatorWorkspaceRepoView:
    return OperatorWorkspaceRepoView(**value.model_dump(exclude={"repo_root"}))


def _workspace_view(value: WorkspaceCatalogView) -> OperatorWorkspaceCatalogView:
    return OperatorWorkspaceCatalogView(repos=[_workspace_repo_view(repo) for repo in value.repos])


def _dependency_attach_view(value: LakeDependencyAttachView) -> OperatorDependencyAttachView:
    return OperatorDependencyAttachView(
        provider_repo_key=value.provider_repo_key,
        dependency=value.dependency,
        changed=value.changed,
        lake_update_summary=value.lake_update_summary,
        summary=value.summary,
    )


def _lake_build_view(value: ToolchainCommandView) -> OperatorLakeBuildView:
    return OperatorLakeBuildView(
        ok=value.ok,
        provider=value.provider,
        fallback_provider=value.fallback_provider,
        fallback_reason=value.fallback_reason,
        summary=value.summary,
        exit_code=value.exit_code,
        timed_out=value.timed_out,
        issue_code=value.issue_code,
    )


def _source_manifest_view(value: SourceCorpusManifestView) -> OperatorSourceCorpusManifestView:
    return OperatorSourceCorpusManifestView(**value.model_dump())


def _source_import_view(value: SourceCorpusImportView) -> OperatorSourceCorpusImportView:
    return OperatorSourceCorpusImportView(
        manifest=_source_manifest_view(value.prepared.manifest),
        preparation_summary=value.prepared.preparation_summary,
        manifest_digest=value.manifest_digest,
        replaced_existing=value.replaced_existing,
        summary=value.summary,
    )


def _resource_summary_view(value: ResourceSummaryView) -> OperatorResourceSummaryView:
    return OperatorResourceSummaryView(
        resource_key=value.resource_key,
        title=value.title,
        kind=value.kind,
        summary=value.summary,
    )


def _resource_view(value: ResourceView) -> OperatorResourceView:
    resource = value.resource
    return OperatorResourceView(
        resource_key=resource.resource_key,
        kind=resource.target.kind,
        version=resource.target.version,
        title=resource.title,
        source_url=resource.source_url,
        notes=resource.notes,
        canonical_entry=resource.canonical_entry,
        content_hash=resource.content_hash,
        summary=value.summary,
    )


def _material_context_view(value: MaterialContextView) -> OperatorMaterialContextView:
    return OperatorMaterialContextView(
        node_path=value.node_path,
        query=value.query,
        scope=value.scope,
        source_index=value.source_index,
        source_files=value.source_files,
        source_blocks=value.source_blocks,
        resources=[_resource_summary_view(resource) for resource in value.resources],
        owned_refs=value.owned_refs,
        context_refs=value.context_refs,
        matches=value.matches,
        returned_count=value.returned_count,
        total_matching_count=value.total_matching_count,
        truncated=value.truncated,
        summary=value.summary,
    )


def _source_index_view(value: SourceIndexView) -> OperatorSourceIndexView:
    return OperatorSourceIndexView(**value.model_dump())


class _PathFreeExecutor:
    """Common envelope projection for every Repo/Material operation."""

    def __init__(self, delegate: OperatorExecutionService[Any]) -> None:
        self.delegate = delegate

    def execute(self, *args: Any, **kwargs: Any) -> ServiceResult[Any]:
        return project_operator_result(self.delegate.execute(*args, **kwargs))


class RepoMaterialOperatorApi:
    """Fixed typed operations; all existing-repo mutations use the shared executor."""

    def __init__(
        self,
        executor: OperatorExecutionService[Any],
        *,
        workspace_root: Path,
        workspace_runtime: LeanRuntimeServices,
    ) -> None:
        self.executor = _PathFreeExecutor(executor)
        self.workspace_root = Path(workspace_root).resolve(strict=False)
        self.workspace_runtime = workspace_runtime

    def create_native_repo(
        self,
        repo_key: str,
        input_model: NativeRepoCreateInput,
    ) -> ServiceResult[OperatorNativeRepoCreationView]:
        # Repo-key admission cannot run until the repo exists. The fixed Service
        # composite owns the workspace lock, validation, and complete rollback.
        return project_operator_result(
            self.workspace_runtime.repo_workspace.create_native_repo(
                self.workspace_root,
                repo_key=repo_key,
                project_name=input_model.project_name,
                preparation_input=input_model.preparation_input,
                completion_mode=input_model.completion_mode,
                default_requirement_proof_availability=input_model.default_requirement_proof_availability,
                native_config=input_model.native_config,
            ),
            _native_creation_view,
        )

    def inspect_repo(self, repo_key: str) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_REPO_MATERIAL,
            lambda ctx: ctx.runtime.repo_workspace.metadata.get_repo_state_view(ctx.repo_root),
        ), _repo_state_view)

    def get_repo_config(self, repo_key: str) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_REPO_MATERIAL,
            lambda ctx: ctx.runtime.repo_workspace.metadata.get_repo_config(ctx.repo_root),
        ), _repo_config_view)

    def get_preparation_input(self, repo_key: str) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_REPO_MATERIAL,
            lambda ctx: ctx.runtime.repo_workspace.preparation.get_preparation_input(ctx.repo_root),
        ), _preparation_view)

    def get_repo_publication(self, repo_key: str) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_REPO_MATERIAL,
            lambda ctx: ctx.runtime.repo_workspace.metadata.get_repo_publication(ctx.repo_root),
        ), _publication_view)

    def check_native_skeleton(self, repo_key: str) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_REPO_MATERIAL,
            lambda ctx: ctx.runtime.repo_workspace.lake_dependency.check_native_repo_skeleton(
                ctx.repo_root
            ),
        ), operator_gate_view)

    def inspect_workspace(self, repo_key: str) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_REPO_MATERIAL,
            lambda ctx: ctx.runtime.repo_workspace.workspace_catalog.get_workspace_catalog(
                ctx.repo_root.parent,
                current_repo=ctx.repo_key,
            ),
        ), _workspace_view)

    def check_provider_availability(self, repo_key: str) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_REPO_MATERIAL,
            lambda ctx: ctx.runtime.repo_workspace.provider_availability.check_provider_available(
                ctx.repo_root
            ),
        ), operator_gate_view)

    def update_repo_config(
        self,
        repo_key: str,
        input_model: RepoConfigUpdateInput,
    ) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            MUTATE_REPO_MATERIAL,
            lambda ctx: ctx.runtime.repo_workspace.metadata.update_repo_config(
                ctx.repo_root,
                completion_mode=input_model.completion_mode,
                default_requirement_proof_availability=input_model.default_requirement_proof_availability,
            ),
        ), _repo_config_view)

    def attach_ready_dependency(
        self,
        repo_key: str,
        input_model: DependencyAttachInput,
    ) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            MUTATE_REPO_MATERIAL,
            lambda ctx: ctx.runtime.repo_workspace.attach_ready_workspace_repo_dependency(
                ctx.repo_root,
                provider_repo=input_model.provider_repo,
            ),
        ), _dependency_attach_view)

    def run_lake_build(
        self,
        repo_key: str,
        input_model: LakeBuildInput,
    ) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            MUTATE_REPO_MATERIAL,
            lambda ctx: ctx.runtime.repo_workspace.lake_dependency.run_lake_build(
                ctx.repo_root,
                target=input_model.target,
            ),
        ), _lake_build_view)

    def import_local_source_corpus(
        self,
        repo_key: str,
        input_model: SourceCorpusLocalDirInput,
    ) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            MUTATE_REPO_MATERIAL,
            lambda ctx: ctx.runtime.material.import_local_source_corpus(
                ctx.repo_root,
                source_dir=input_model.source_dir,
                entry_path=input_model.entry_path,
                overview=input_model.overview,
                preparation_summary=input_model.preparation_summary,
                replace_existing=input_model.replace_existing,
                expected_manifest_digest=input_model.expected_manifest_digest,
            ),
        ), _source_import_view)

    def get_source_corpus_manifest(self, repo_key: str) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_REPO_MATERIAL,
            lambda ctx: ctx.runtime.material.source_corpus.get_source_corpus_manifest(ctx.repo_root),
        ), _source_manifest_view)

    def list_resources(
        self, repo_key: str, input_model: ResourceListInput
    ) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_REPO_MATERIAL,
            lambda ctx: ctx.runtime.material.resource_library.list_resources(
                ctx.repo_root,
                query=input_model.query,
            ),
        ), lambda values: [_resource_summary_view(value) for value in values])

    def get_resource(
        self, repo_key: str, input_model: ResourceGetInput
    ) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_REPO_MATERIAL,
            lambda ctx: ctx.runtime.material.resource_library.get_resource(
                ctx.repo_root,
                resource_key=input_model.resource_key,
            ),
        ), _resource_view)

    def get_material_context(
        self, repo_key: str, input_model: MaterialContextInput
    ) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_REPO_MATERIAL,
            lambda ctx: ctx.runtime.material.get_material_context_view(
                ctx.repo_root,
                node_path=input_model.node_path,
                query=input_model.query,
                scope=input_model.scope,
                require_committed_source_index=input_model.require_committed_source_index,
                regex=input_model.regex,
                limit=input_model.limit,
            ),
        ), _material_context_view)

    def get_source_index(self, repo_key: str) -> ServiceResult[SourceIndexView]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_REPO_MATERIAL,
            lambda ctx: ctx.runtime.material.get_source_index(ctx.repo_root),
        ), _source_index_view)

    def get_source_index_coverage(self, repo_key: str) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_REPO_MATERIAL,
            lambda ctx: ctx.runtime.material.get_source_index_coverage(ctx.repo_root),
        ))

    def get_committed_source_index(self, repo_key: str) -> ServiceResult[SourceIndexView]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_REPO_MATERIAL,
            lambda ctx: ctx.runtime.material.get_committed_source_index(ctx.repo_root),
        ), _source_index_view)

    def get_committed_source_index_coverage(self, repo_key: str) -> ServiceResult[Any]:
        return self.executor.execute(
            repo_key,
            READ_REPO_MATERIAL,
            lambda ctx: ctx.runtime.material.get_committed_source_index_coverage(ctx.repo_root),
        )

    def open_source_index_update(
        self,
        repo_key: str,
        input_model: SourceIndexOpenInput,
    ) -> ServiceResult[SourceIndexOpenedView]:
        def action(ctx):  # noqa: ANN001, ANN202
            resolved = ctx.runtime.material.resolve_source_scope(
                ctx.repo_root,
                source_scope=input_model.source_scope,
            )
            if not resolved.ok or resolved.value is None:
                return ctx.runtime.foundation.fail(resolved.issues)
            checkpoint = self._checkpoint(ctx.runtime)
            existing_baseline = checkpoint.load_operator_source_index_baseline(ctx.repo_root)
            had_baseline = existing_baseline.ok
            artifact = checkpoint.persist_operator_source_index_baseline(
                ctx.repo_root,
                resolved_file_scope=resolved.value.resolved_file_paths,
                source_manifest_digest=resolved.value.manifest_digest,
                expected_baseline_digest=input_model.expected_baseline_digest,
            )
            if not artifact.ok or artifact.value is None:
                return ctx.runtime.foundation.fail(artifact.issues)
            opened = ctx.runtime.material.open_source_index_update(
                ctx.repo_root,
                resolved_scope=resolved.value,
                index_policy=input_model.index_policy,
                expected_baseline_digest=artifact.value.baseline_digest,
                retry_baseline_index=artifact.value.baseline_index,
            )
            if not opened.ok or opened.value is None:
                if not had_baseline:
                    checkpoint.clear_operator_source_index_baseline(
                        ctx.repo_root,
                        expected_locator=artifact.value.locator,
                        expected_baseline_digest=artifact.value.baseline_digest,
                    )
                return ctx.runtime.foundation.fail(opened.issues)
            current_digest = self._current_index_digest(ctx.runtime, ctx.repo_root)
            if not current_digest.ok or current_digest.value is None:
                return ctx.runtime.foundation.fail(current_digest.issues)
            if opened.value.outcome == "no_op":
                cleared = checkpoint.clear_operator_source_index_baseline(
                    ctx.repo_root,
                    expected_locator=artifact.value.locator,
                    expected_baseline_digest=artifact.value.baseline_digest,
                )
                if not cleared.ok:
                    return ctx.runtime.foundation.fail(cleared.issues)
                return ctx.runtime.foundation.ok(
                    SourceIndexOpenedView(
                        opened=opened.value,
                        current_index_digest=current_digest.value,
                        summary="SourceIndex scope required no update.",
                    )
                )
            return ctx.runtime.foundation.ok(
                SourceIndexOpenedView(
                    opened=opened.value,
                    baseline_digest=artifact.value.baseline_digest,
                    current_index_digest=current_digest.value,
                    summary="Opened restart-safe SourceIndex update.",
                )
            )

        return self.executor.execute(repo_key, MUTATE_REPO_MATERIAL, action)

    def set_source_index_overview(
        self, repo_key: str, input_model: SourceIndexOverviewInput
    ) -> ServiceResult[SourceIndexMutationView]:
        return self._index_mutation(
            repo_key,
            input_model.expected_current_digest,
            lambda runtime, root: runtime.material.set_source_index_overview(
                root, overview=input_model.overview
            ),
        )

    def set_source_file_survey(
        self, repo_key: str, input_model: SourceIndexFileSurveyInput
    ) -> ServiceResult[SourceIndexMutationView]:
        return self._index_mutation(
            repo_key,
            input_model.expected_current_digest,
            lambda runtime, root: runtime.material.set_file_survey_status(
                root,
                path=input_model.path,
                status=input_model.status,
                summary=input_model.summary,
            ),
        )

    def set_source_file_indexing(
        self, repo_key: str, input_model: SourceIndexFileIndexingInput
    ) -> ServiceResult[SourceIndexMutationView]:
        return self._index_mutation(
            repo_key,
            input_model.expected_current_digest,
            lambda runtime, root: runtime.material.set_file_indexing_status(
                root,
                path=input_model.path,
                status=input_model.status,
            ),
        )

    def create_source_block(
        self, repo_key: str, input_model: SourceIndexBlockCreateInput
    ) -> ServiceResult[SourceIndexMutationView]:
        return self._index_mutation(
            repo_key,
            input_model.expected_current_digest,
            lambda runtime, root: runtime.material.create_source_block(
                root,
                parent_id=input_model.parent_id,
                kind=input_model.kind,
                subtype=input_model.subtype,
                title=input_model.title,
                summary=input_model.summary,
            ),
        )

    def update_source_block(
        self, repo_key: str, input_model: SourceIndexBlockUpdateInput
    ) -> ServiceResult[SourceIndexMutationView]:
        return self._index_mutation(
            repo_key,
            input_model.expected_current_digest,
            lambda runtime, root: runtime.material.update_source_block(
                root,
                block_id=input_model.block_id,
                title=input_model.title,
                summary=input_model.summary,
                kind=input_model.kind,
                subtype=input_model.subtype,
            ),
        )

    def add_source_block_ref(
        self, repo_key: str, input_model: SourceIndexBlockRefAddInput
    ) -> ServiceResult[SourceIndexMutationView]:
        return self._index_mutation(
            repo_key,
            input_model.expected_current_digest,
            lambda runtime, root: runtime.material.add_source_block_ref(
                root,
                block_id=input_model.block_id,
                path=input_model.path,
                start_line=input_model.start_line,
                end_line=input_model.end_line,
                role=input_model.role,
            ),
        )

    def remove_source_block_ref(
        self, repo_key: str, input_model: SourceIndexBlockRefRemoveInput
    ) -> ServiceResult[SourceIndexMutationView]:
        return self._index_mutation(
            repo_key,
            input_model.expected_current_digest,
            lambda runtime, root: runtime.material.remove_source_block_ref(
                root,
                block_id=input_model.block_id,
                ref_id=input_model.ref_id,
            ),
        )

    def update_source_block_ref(
        self, repo_key: str, input_model: SourceIndexBlockRefUpdateInput
    ) -> ServiceResult[SourceIndexMutationView]:
        return self._index_mutation(
            repo_key,
            input_model.expected_current_digest,
            lambda runtime, root: runtime.material.update_source_block_ref(
                root,
                block_id=input_model.block_id,
                ref_id=input_model.ref_id,
                path=input_model.path,
                start_line=input_model.start_line,
                end_line=input_model.end_line,
                role=input_model.role,
            ),
        )

    def mark_source_block_refs_done(
        self, repo_key: str, input_model: SourceIndexBlockLifecycleInput
    ) -> ServiceResult[SourceIndexMutationView]:
        return self._index_mutation(
            repo_key,
            input_model.expected_current_digest,
            lambda runtime, root: runtime.material.mark_block_refs_done(
                root, block_id=input_model.block_id
            ),
        )

    def create_source_link(
        self, repo_key: str, input_model: SourceIndexLinkCreateInput
    ) -> ServiceResult[SourceIndexMutationView]:
        return self._index_mutation(
            repo_key,
            input_model.expected_current_digest,
            lambda runtime, root: runtime.material.create_source_link(
                root,
                source_block_id=input_model.source_block_id,
                target_block_id=input_model.target_block_id,
                target_hint=input_model.target_hint,
                link_kind=input_model.link_kind,
                evidence_ref_ids=input_model.evidence_ref_ids,
            ),
        )

    def mark_source_block_links_done(
        self, repo_key: str, input_model: SourceIndexBlockLifecycleInput
    ) -> ServiceResult[SourceIndexMutationView]:
        return self._index_mutation(
            repo_key,
            input_model.expected_current_digest,
            lambda runtime, root: runtime.material.mark_block_links_done(
                root, block_id=input_model.block_id
            ),
        )

    def update_source_link(
        self, repo_key: str, input_model: SourceIndexLinkUpdateInput
    ) -> ServiceResult[SourceIndexMutationView]:
        return self._index_mutation(
            repo_key,
            input_model.expected_current_digest,
            lambda runtime, root: runtime.material.update_source_link(
                root,
                link_id=input_model.link_id,
                target_block_id=input_model.target_block_id,
                target_hint=input_model.target_hint,
                link_kind=input_model.link_kind,
                evidence_ref_ids=input_model.evidence_ref_ids,
            ),
        )

    def mark_source_block_completed(
        self, repo_key: str, input_model: SourceIndexBlockLifecycleInput
    ) -> ServiceResult[SourceIndexMutationView]:
        return self._index_mutation(
            repo_key,
            input_model.expected_current_digest,
            lambda runtime, root: runtime.material.mark_block_completed(
                root, block_id=input_model.block_id
            ),
        )

    def validate_and_commit_source_index(
        self,
        repo_key: str,
        input_model: SourceIndexCommitInput,
    ) -> ServiceResult[SourceIndexCommitView]:
        def action(ctx):  # noqa: ANN001, ANN202
            baseline = self._load_and_check_baseline(
                ctx.runtime,
                ctx.repo_root,
                input_model.expected_current_digest,
            )
            if not baseline.ok or baseline.value is None:
                return ctx.runtime.foundation.fail(baseline.issues)
            component = ctx.runtime.material.source_index
            validated = component.validate_source_index_update(
                ctx.repo_root,
                baseline_index=baseline.value.baseline_index,
                expected_baseline_digest=baseline.value.baseline_digest,
                resolved_scope=baseline.value.resolved_file_scope,
                require_completed=input_model.require_completed,
            )
            if not validated.ok or validated.value is None:
                return ctx.runtime.foundation.fail(validated.issues)
            if not validated.value.gate.passed:
                return ctx.runtime.foundation.fail(validated.value.gate.issues)
            committed = component.commit_source_index_update(
                ctx.repo_root,
                validated=validated.value,
            )
            if not committed.ok or committed.value is None:
                return ctx.runtime.foundation.fail(committed.issues)
            checkpoint = self._checkpoint(ctx.runtime)
            cleared = checkpoint.clear_operator_source_index_baseline(
                ctx.repo_root,
                expected_locator=baseline.value.locator,
                expected_baseline_digest=baseline.value.baseline_digest,
            )
            if not cleared.ok:
                return ctx.runtime.foundation.ok(
                    committed.value,
                    warnings=[
                        issue.model_copy(update={"severity": IssueSeverity.WARNING})
                        for issue in cleared.issues
                    ],
                )
            return committed

        return self.executor.execute(repo_key, MUTATE_REPO_MATERIAL, action)

    def _index_mutation(
        self,
        repo_key: str,
        expected_current_digest: str,
        mutation,  # noqa: ANN001
    ) -> ServiceResult[SourceIndexMutationView]:
        def action(ctx):  # noqa: ANN001, ANN202
            baseline = self._load_and_check_baseline(
                ctx.runtime,
                ctx.repo_root,
                expected_current_digest,
            )
            if not baseline.ok:
                return ctx.runtime.foundation.fail(baseline.issues)
            changed = mutation(ctx.runtime, ctx.repo_root)
            if not changed.ok or changed.value is None:
                return ctx.runtime.foundation.fail(changed.issues)
            public_value = changed.value
            if isinstance(public_value, SourceIndexView):
                public_value = _source_index_view(public_value)
            elif isinstance(public_value, GateReport):
                public_value = operator_gate_view(public_value)
            digest = self._current_index_digest(ctx.runtime, ctx.repo_root)
            if not digest.ok or digest.value is None:
                return ctx.runtime.foundation.fail(digest.issues)
            return ctx.runtime.foundation.ok(
                SourceIndexMutationView(
                    value=public_value,
                    current_index_digest=digest.value,
                    summary="Applied typed SourceIndex mutation.",
                ),
                warnings=changed.issues,
            )

        return self.executor.execute(repo_key, MUTATE_REPO_MATERIAL, action)

    @staticmethod
    def _current_index_digest(
        runtime: LeanRuntimeServices,
        repo_root: Path,
    ) -> ServiceResult[str]:
        current = runtime.material.source_index.get_source_index_model(repo_root)
        if not current.ok or current.value is None:
            if any(issue.kind == "source_index_missing" for issue in current.issues):
                return runtime.foundation.ok(
                    runtime.material.source_index.missing_source_index_digest()
                )
            return runtime.foundation.fail(current.issues)
        return runtime.foundation.ok(
            runtime.material.source_index.canonical_source_index_digest(current.value)
        )

    @classmethod
    def _load_and_check_baseline(
        cls,
        runtime: LeanRuntimeServices,
        repo_root: Path,
        expected_current_digest: str,
    ) -> ServiceResult[OperatorSourceIndexBaselineArtifact]:
        checkpoint = cls._checkpoint(runtime)
        baseline = checkpoint.load_operator_source_index_baseline(repo_root)
        if not baseline.ok or baseline.value is None:
            return runtime.foundation.fail(baseline.issues)
        current = cls._current_index_digest(runtime, repo_root)
        if not current.ok or current.value is None:
            return runtime.foundation.fail(current.issues)
        if current.value != expected_current_digest:
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "source_index_current_digest_mismatch",
                    "SourceIndex changed since the caller last observed it.",
                    current=current.value,
                    expected=expected_current_digest,
                )
            )
        current_model = runtime.material.source_index.get_source_index_model(repo_root)
        if not current_model.ok or current_model.value is None:
            return runtime.foundation.fail(current_model.issues)
        if (
            current_model.value.status not in {"draft", "updating"}
            or current_model.value.active_file_scope != baseline.value.resolved_file_scope
        ):
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "operator_source_index_update_context_mismatch",
                    "Persisted operator baseline does not match the active SourceIndex scope.",
                    current=", ".join(current_model.value.active_file_scope),
                    expected=", ".join(baseline.value.resolved_file_scope),
                )
            )
        manifest = runtime.material.source_corpus.refresh_source_corpus_manifest(repo_root)
        if not manifest.ok or manifest.value is None:
            return runtime.foundation.fail(manifest.issues)
        manifest_digest = runtime.material.source_corpus.canonical_manifest_digest(manifest.value)
        if manifest_digest != baseline.value.source_manifest_digest:
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "source_index_scope_manifest_drift",
                    "SourceCorpus changed while the operator SourceIndex update was active.",
                    current=manifest_digest,
                    expected=baseline.value.source_manifest_digest,
                )
            )
        return baseline

    @staticmethod
    def _checkpoint(runtime: LeanRuntimeServices) -> SourceIndexCheckpointAdapter:
        return runtime.app.source_index_checkpoint or SourceIndexCheckpointAdapter(runtime)


__all__ = [name for name in globals() if name.endswith("View") or name.startswith("SourceIndex") or name in {
    "DependencyAttachInput",
    "LakeBuildInput",
    "MaterialContextInput",
    "NativeRepoCreateInput",
    "READ_REPO_MATERIAL",
    "RepoConfigUpdateInput",
    "RepoMaterialOperatorApi",
    "ResourceGetInput",
    "ResourceListInput",
    "SourceCorpusLocalDirInput",
    "MUTATE_REPO_MATERIAL",
}]
