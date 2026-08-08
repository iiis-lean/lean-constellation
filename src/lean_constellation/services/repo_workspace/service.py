"""RepoWorkspaceService composition and higher-level wrappers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.domain.lake_project import NativeLakeProjectConfig
from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import (
    AdapterProviderRoute,
    AutoProviderRoute,
    ProviderRoute,
    BootstrapInputValidationView,
    ProviderReadyView,
    RepoDependencyRequirementStatus,
    ProviderRepoPreparationView,
    RepoPreparationInputView,
    RepoRequirementRef,
    RepoPreparationInput,
    RequirementResumeCandidateView,
    RequirementWaitingView,
    SourceCorpusMode,
    UpstreamDependencyInput,
    VerifiedAdapterRouteReceipt,
)
from lean_constellation.domain.repo import (
    ProofAvailability,
    RepoCompletionMode,
    RepoFormat,
    RepoConfigView,
    WorkspaceConfig,
    WorkspaceCoordinatorView,
)
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.repo_workspace.git_release import GitReleaseComponent
from lean_constellation.services.repo_workspace.adapter_compatibility import (
    AdapterCompatibilityComponent,
)
from lean_constellation.services.repo_workspace.github_topics import (
    RepoGitHubTopicsComponent,
)
from lean_constellation.services.repo_workspace.dependency_release import (
    RepoDependencyReleaseComponent,
)
from lean_constellation.services.repo_workspace.lake_dependency import (
    AdapterSetupView,
    LakeDependencyAttachView,
    LakeDependencyComponent,
    LakeGitDependencyAttachView,
    RepoSkeletonView,
)
from lean_constellation.services.repo_workspace.native_source_index_recovery import (
    NativeSourceIndexRecoveryComponent,
)
from lean_constellation.services.repo_workspace.repo_metadata import RepoMetadataComponent
from lean_constellation.services.repo_workspace.repo_preparation import PreparationStartPreflightView, RepoPreparationComponent
from lean_constellation.services.repo_workspace.repo_requirement import RepoRequirementComponent
from lean_constellation.services.repo_workspace.repo_release import RepoReleaseComponent
from lean_constellation.services.repo_workspace.repo_run import RepoRunComponent
from lean_constellation.services.repo_workspace.repo_lifecycle_lock import (
    RepoLifecycleLockBusyError,
    RepoLifecycleLockComponent,
    WorkspaceRepoCreationLockComponent,
)
from lean_constellation.services.repo_workspace.provider_availability import ProviderAvailabilityComponent
from lean_constellation.services.repo_workspace.publication import RepoPublicationComponent
from lean_constellation.services.repo_workspace.remote_publication import (
    RepoRemotePublicationComponent,
)
from lean_constellation.services.repo_workspace.workspace_catalog import WorkspaceCatalogComponent
from lean_constellation.services.repo_workspace.workspace_publication import (
    WorkspacePublicationComponent,
)

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class RequirementConsumeView(StrictModel):
    requirement_name: str
    provider_repo: str
    attached: bool
    handled: bool
    summary: str
    issues: list[str] = Field(default_factory=list)


class NativeRepoCreationView(StrictModel):
    repo_root: str
    repo_key: str
    project_name: str
    config: RepoConfigView
    preparation_input: RepoPreparationInputView
    skeleton: RepoSkeletonView
    summary: str


class RepoWorkspaceService:
    """Composition root for repo/workspace deterministic operations."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        metadata: RepoMetadataComponent | None = None,
        requirement: RepoRequirementComponent | None = None,
        lake_dependency: LakeDependencyComponent | None = None,
        preparation: RepoPreparationComponent | None = None,
        workspace_catalog: WorkspaceCatalogComponent | None = None,
        release: RepoReleaseComponent | None = None,
        git_release: GitReleaseComponent | None = None,
        github_topics: RepoGitHubTopicsComponent | None = None,
        publication: RepoPublicationComponent | None = None,
        dependency_release: RepoDependencyReleaseComponent | None = None,
        remote_publication: RepoRemotePublicationComponent | None = None,
        workspace_publication: WorkspacePublicationComponent | None = None,
        provider_availability: ProviderAvailabilityComponent | None = None,
        adapter_compatibility: AdapterCompatibilityComponent | None = None,
        native_lake_project_config: NativeLakeProjectConfig | None = None,
        workspace_config: WorkspaceConfig | None = None,
    ) -> None:
        self.runtime = runtime
        self.workspace_config = workspace_config or WorkspaceConfig()
        self.metadata = metadata or RepoMetadataComponent(runtime)
        self.release = release or RepoReleaseComponent(runtime)
        self.git_release = git_release or GitReleaseComponent(runtime)
        self.github_topics = github_topics or RepoGitHubTopicsComponent(runtime)
        self.publication = publication or RepoPublicationComponent(
            runtime,
            workspace_policy=self.workspace_config.publication,
        )
        self.dependency_release = dependency_release or RepoDependencyReleaseComponent(
            runtime
        )
        self.remote_publication = (
            remote_publication or RepoRemotePublicationComponent(runtime)
        )
        self.workspace_publication = (
            workspace_publication or WorkspacePublicationComponent(runtime)
        )
        self.provider_availability = provider_availability or ProviderAvailabilityComponent(runtime, self.metadata, self.release)
        self.requirement = requirement or RepoRequirementComponent(runtime)
        self.lake_dependency = lake_dependency or LakeDependencyComponent(
            runtime,
            self.metadata,
            config=native_lake_project_config,
        )
        self.adapter_compatibility = adapter_compatibility or AdapterCompatibilityComponent(
            runtime,
            config=self.lake_dependency.config,
        )
        self.preparation = preparation or RepoPreparationComponent(
            runtime,
            self.metadata,
            self.requirement,
            workspace_config=self.workspace_config,
        )
        self.run = RepoRunComponent(runtime, self.metadata, self.preparation, self.release)
        self.native_source_index_recovery = NativeSourceIndexRecoveryComponent(runtime)
        self.lifecycle_lock = RepoLifecycleLockComponent(runtime)
        self.workspace_creation_lock = WorkspaceRepoCreationLockComponent(runtime)
        self.workspace_catalog = workspace_catalog or WorkspaceCatalogComponent(
            runtime,
            self.metadata,
            self.requirement,
            self.lake_dependency,
        )

    def inspect_workspace_for_coordinator(self, current_repo_root: Path) -> ServiceResult[WorkspaceCoordinatorView]:
        return self.workspace_catalog.inspect_workspace_for_coordinator(current_repo_root)

    def create_requirement_with_interfaces(
        self,
        repo_root: Path,
        *,
        name: str,
        target_repo: str,
        source_description: str | None = None,
        reason: str | None = None,
        interfaces: list[dict[str, str]] | None = None,
        required_proof_availability: ProofAvailability | str | None = None,
        provider_route: ProviderRoute | None = None,
    ) -> ServiceResult[object]:
        required = (
            ProofAvailability(required_proof_availability)
            if required_proof_availability is not None
            else self._requirement_proof_availability_for_repo(repo_root)
        )
        created = self.requirement.create_requirement(
            repo_root,
            name=name,
            target_repo=target_repo,
            required_proof_availability=required,
            source_description=source_description,
            reason=reason,
            provider_route=provider_route or AutoProviderRoute(),
        )
        if not created.ok or created.value is None:
            return self.runtime.foundation.fail(created.issues)
        current = created
        for interface in interfaces or []:
            current = self.requirement.add_requirement_interface(
                repo_root,
                requirement_name=name,
                interface_name=interface["name"],
                kind=DeclKind(interface["kind"]),
                summary=interface["summary"],
                statement_hint=interface.get("statement_hint"),
                expected_statement_lean_code=interface.get("expected_statement_lean_code"),
            )
            if not current.ok:
                return self.runtime.foundation.fail(current.issues)
        return current

    def requirement_proof_availability_for_repo(self, repo_root: Path):
        config = self.metadata.get_repo_config(repo_root)
        if config.ok and config.value is not None:
            return config.value.config.default_requirement_proof_availability
        return self.workspace_config.default_requirement_proof_availability

    def _requirement_proof_availability_for_repo(self, repo_root: Path):
        return self.requirement_proof_availability_for_repo(repo_root)

    def create_provider_repo_shell_from_group(
        self,
        workspace_root: Path,
        *,
        target_repo: str,
        source_corpus_mode: SourceCorpusMode | str = SourceCorpusMode.PREPARE,
    ) -> ServiceResult[object]:
        return self.preparation.create_provider_repo_shell_from_group(
            workspace_root,
            target_repo=target_repo,
            source_corpus_mode=source_corpus_mode,
        )

    def prepare_provider_repo_shell(
        self,
        workspace_root: Path,
        *,
        target_repo: str,
        preparation_input: RepoPreparationInput,
        project_name: str | None = None,
    ) -> ServiceResult[ProviderRepoPreparationView]:
        return self.preparation.prepare_provider_repo_shell(
            workspace_root,
            target_repo=target_repo,
            preparation_input=preparation_input,
            project_name=project_name,
        )

    def build_main_repo_preparation_input(
        self,
        *,
        goal: str,
        source_corpus_mode: SourceCorpusMode | str,
        source_description: str | None = None,
        interface_inputs: list[object] | None = None,
        allow_interface_supplement: bool = True,
        notes: str | None = None,
    ):
        return self.preparation.build_main_repo_preparation_input(
            goal=goal,
            source_corpus_mode=source_corpus_mode,
            source_description=source_description,
            interface_inputs=interface_inputs,  # type: ignore[arg-type]
            allow_interface_supplement=allow_interface_supplement,
            notes=notes,
        )

    def create_main_repo_shell(
        self,
        workspace_root: Path,
        *,
        repo_name: str,
        project_name: str,
        input: RepoPreparationInput | None = None,
    ):
        return self.preparation.create_main_repo_shell(
            workspace_root,
            repo_name=repo_name,
            project_name=project_name,
            input=input,
        )

    def create_native_repo(
        self,
        workspace_root: Path,
        *,
        repo_key: str,
        project_name: str,
        preparation_input: RepoPreparationInput,
        completion_mode: RepoCompletionMode | str,
        default_requirement_proof_availability: ProofAvailability | str,
        native_config: NativeLakeProjectConfig,
    ) -> ServiceResult[NativeRepoCreationView]:
        """Create one provider-neutral native repo as a rollback-safe Service transaction."""

        workspace_root = Path(workspace_root).resolve(strict=False)
        try:
            repo_key = self.runtime.foundation.layout.ensure_safe_key(repo_key)
            mode = RepoCompletionMode(completion_mode)
            default_requirement = ProofAvailability(default_requirement_proof_availability)
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("native_repo_creation_input_invalid", str(exc))
            )
        if not workspace_root.exists() or not workspace_root.is_dir():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "workspace_not_found",
                    f"Workspace root not found: {workspace_root}",
                    object_ref=str(workspace_root),
                )
            )
        validation = self.preparation.validate_preparation_input(preparation_input)
        if not validation.ok:
            return self.runtime.foundation.fail(validation.issues)
        if preparation_input.source_corpus_mode == SourceCorpusMode.NONE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "invalid_source_corpus_mode",
                    "Native repo creation requires source_corpus_mode != none.",
                )
            )
        repo_root = workspace_root / repo_key
        try:
            with self.workspace_creation_lock.locked(workspace_root):
                if repo_root.exists():
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "target_repo_already_exists",
                            f"Repo already exists: {repo_key}",
                            object_ref=str(repo_root),
                        )
                    )
                shell = self.preparation.create_provider_repo_shell(
                    workspace_root,
                    target_repo=repo_key,
                    project_name=project_name,
                )
                if not shell.ok:
                    return self.runtime.foundation.fail(shell.issues)
                configured = self.metadata.update_repo_config(
                    repo_root,
                    completion_mode=mode,
                    default_requirement_proof_availability=default_requirement,
                )
                if not configured.ok or configured.value is None:
                    self.preparation.rollback_created_repo(repo_root)
                    return self.runtime.foundation.fail(configured.issues)
                prepared = self.preparation.write_preparation_input(
                    repo_root,
                    input=preparation_input,
                )
                if not prepared.ok or prepared.value is None:
                    self.preparation.rollback_created_repo(repo_root)
                    return self.runtime.foundation.fail(prepared.issues)
                skeleton = self.lake_dependency.initialize_native_repo_skeleton(
                    repo_root,
                    project_name=project_name,
                    config=native_config,
                )
                if not skeleton.ok or skeleton.value is None:
                    self.preparation.rollback_created_repo(repo_root)
                    return self.runtime.foundation.fail(skeleton.issues)
                return self.runtime.foundation.ok(
                    NativeRepoCreationView(
                        repo_root=str(repo_root),
                        repo_key=repo_key,
                        project_name=skeleton.value.project_name,
                        config=configured.value,
                        preparation_input=prepared.value,
                        skeleton=skeleton.value,
                        summary=f"Created native repo {repo_key}.",
                    )
                )
        except RepoLifecycleLockBusyError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "workspace_repo_creation_lock_busy",
                    str(exc),
                    object_ref=str(workspace_root),
                )
            )
        except Exception as exc:  # noqa: BLE001 - rollback and normalize transaction failures.
            if repo_root.exists():
                self.preparation.rollback_created_repo(repo_root)
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "native_repo_creation_failed",
                    f"Native repo creation failed and was rolled back: {exc}",
                    object_ref=str(repo_root),
                )
            )

    def write_preparation_input(self, repo_root: Path, *, input: RepoPreparationInput):
        return self.preparation.write_preparation_input(repo_root, input=input)

    def preview_preparation_interface_append(self, repo_root: Path, *, interfaces: list[DeclInterface]):
        return self.preparation.preview_preparation_interface_append(
            repo_root,
            interfaces=interfaces,
        )

    def append_preparation_interfaces(self, repo_root: Path, *, interfaces: list[DeclInterface]):
        return self.preparation.append_preparation_interfaces(
            repo_root,
            interfaces=interfaces,
        )

    def attach_provider_for_requirement(
        self,
        consumer_repo_root: Path,
        *,
        requirement_name: str,
    ) -> ServiceResult[RequirementConsumeView]:
        requirement = self.requirement.get_requirement(consumer_repo_root, name=requirement_name)
        if not requirement.ok or requirement.value is None:
            return self.runtime.foundation.fail(requirement.issues)
        requirement_value = requirement.value.requirement
        if requirement_value.status not in {
            RepoDependencyRequirementStatus.SATISFIED,
            RepoDependencyRequirementStatus.HANDLED,
        }:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_not_satisfied",
                    "Provider dependency attachment requires a satisfied or already handled requirement.",
                    object_ref=requirement_name,
                    current=requirement_value.status.value,
                    expected=(
                        f"{RepoDependencyRequirementStatus.SATISFIED.value}|"
                        f"{RepoDependencyRequirementStatus.HANDLED.value}"
                    ),
                )
            )
        provider_repo = requirement_value.provider_repo
        if not provider_repo:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_not_satisfied",
                    "Requirement has no provider repo to attach.",
                    object_ref=requirement_name,
                )
            )
        valid = self.requirement.validate_requirement_provider_truth(
            consumer_repo_root,
            requirement_name=requirement_name,
            provider_repo=provider_repo,
            require_stable=True,
        )
        if not valid.ok:
            return self.runtime.foundation.fail(valid.issues)
        dependencies = self.lake_dependency.parse_lake_dependencies(consumer_repo_root)
        if not dependencies.ok or dependencies.value is None:
            return self.runtime.foundation.fail(dependencies.issues)
        already_attached = any(item.name == provider_repo for item in dependencies.value.dependencies)
        if not already_attached:
            if (
                requirement_value.provider_release_id is not None
                and requirement_value.provider_git_url is not None
            ):
                attached = self.lake_dependency.attach_released_repo_git_dependency(
                    consumer_repo_root,
                    provider_repo_key=provider_repo,
                    provider_release_id=requirement_value.provider_release_id,
                    canonical_git_url=requirement_value.provider_git_url,
                )
            else:
                attached = self.lake_dependency.attach_workspace_repo_dependency(
                    consumer_repo_root,
                    provider_repo_key=provider_repo,
                )
            if not attached.ok:
                return self.runtime.foundation.fail(attached.issues)
        if requirement_value.status == RepoDependencyRequirementStatus.SATISFIED:
            handled = self.requirement.mark_requirement_handled(
                consumer_repo_root,
                requirement_name=requirement_name,
                note=f"Attached provider repo {provider_repo}.",
            )
            if not handled.ok:
                return self.runtime.foundation.fail(handled.issues)
        return self.runtime.foundation.ok(
            RequirementConsumeView(
                requirement_name=requirement_name,
                provider_repo=provider_repo,
                attached=True,
                handled=True,
                summary=(
                    f"Provider repo {provider_repo} was already attached and requirement {requirement_name} was already handled."
                    if already_attached and requirement_value.status == RepoDependencyRequirementStatus.HANDLED
                    else f"Attached and handled requirement {requirement_name}."
                ),
            )
        )

    def attach_ready_workspace_repo_dependency(
        self,
        consumer_repo_root: Path,
        *,
        provider_repo: str,
    ) -> ServiceResult[LakeDependencyAttachView | LakeGitDependencyAttachView]:
        provider_repo = self.runtime.foundation.layout.ensure_safe_key(provider_repo)
        consumer_repo_root = Path(consumer_repo_root)
        if provider_repo == consumer_repo_root.name:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_repo_self_dependency",
                    "A repo cannot attach itself as a workspace dependency.",
                    object_ref=provider_repo,
                )
            )
        provider_root = consumer_repo_root.parent / provider_repo
        if not provider_root.exists() or not provider_root.is_dir():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_repo_not_found",
                    f"Provider repo does not exist in workspace: {provider_repo}",
                    object_ref=str(provider_root),
                )
            )
        availability = self.provider_availability.check_provider_available(provider_root)
        if not availability.ok or availability.value is None:
            return self.runtime.foundation.fail(availability.issues)
        if not availability.value.passed:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_repo_not_ready",
                    "Direct workspace dependency attachment requires an available provider repo.",
                    object_ref=provider_repo,
                    details={"issues": "; ".join(issue.kind for issue in availability.value.issues)},
                )
            )
        provider_format = self.metadata.get_repo_format(provider_root)
        if not provider_format.ok or provider_format.value is None:
            return self.runtime.foundation.fail(provider_format.issues)
        publication = self.metadata.get_repo_publication(provider_root)
        if (
            not publication.ok
            or publication.value is None
            or publication.value.publication.latest_release_id is None
        ):
            format_name = provider_format.value.repo_format.value
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    f"provider_{format_name}_stable_release_missing",
                    f"{format_name.title()} provider has no current Git Release.",
                    object_ref=provider_repo,
                )
            )
        policy = self.publication.resolve_policy(provider_root)
        if not policy.ok or policy.value is None:
            return self.runtime.foundation.fail(policy.issues)
        return self.lake_dependency.attach_released_repo_git_dependency(
            consumer_repo_root,
            provider_repo_key=provider_repo,
            provider_release_id=publication.value.publication.latest_release_id,
            canonical_git_url=policy.value.policy.canonical_fetch_url,
        )

    def mark_requirement_waiting_for_provider(
        self,
        repo_root: Path,
        *,
        requirement_name: str,
        provider_repo: str | None = None,
        reason: str | None = None,
    ) -> ServiceResult[RequirementWaitingView]:
        return self.requirement.mark_requirement_waiting_for_provider(
            repo_root,
            requirement_name=requirement_name,
            provider_repo=provider_repo,
            reason=reason,
        )

    def list_resume_candidates_for_requirement(
        self,
        workspace_root: Path,
        *,
        provider_repo: str,
    ) -> ServiceResult[list[RequirementResumeCandidateView]]:
        return self.requirement.list_resume_candidates_for_requirement(
            workspace_root,
            provider_repo=provider_repo,
        )

    def mark_requirement_result_observed(
        self,
        repo_root: Path,
        *,
        requirement_name: str,
        note: str | None = None,
    ) -> ServiceResult[RequirementWaitingView]:
        return self.requirement.mark_requirement_result_observed(
            repo_root,
            requirement_name=requirement_name,
            note=note,
        )

    def get_preparation_start_preflight(
        self,
        repo_root: Path,
        *,
        expected_format: str | None = None,
    ) -> ServiceResult[PreparationStartPreflightView]:
        return self.preparation.get_preparation_start_preflight(
            repo_root,
            expected_format=expected_format,
        )

    def validate_requirement_bootstrap_input(
        self,
        repo_root: Path,
        *,
        requirement_refs: list[RepoRequirementRef] | None = None,
    ) -> ServiceResult[BootstrapInputValidationView]:
        return self.preparation.validate_requirement_bootstrap_input(repo_root, requirement_refs=requirement_refs)

    def initialize_repo_as_adapter(
        self,
        repo_root: Path,
        *,
        upstream: UpstreamDependencyInput,
        project_name: str | None = None,
    ) -> ServiceResult[AdapterSetupView]:
        project_name = project_name or Path(repo_root).name
        initialized = self.lake_dependency.initialize_adapter_repo_skeleton(
            repo_root,
            project_name=project_name,
            upstream=upstream,
        )
        if not initialized.ok or initialized.value is None:
            return self.runtime.foundation.fail(initialized.issues)
        configured = self.metadata.update_repo_config(
            repo_root,
            completion_mode=RepoCompletionMode.GRAPH_PROVED,
        )
        if not configured.ok:
            return self.runtime.foundation.fail(configured.issues)
        return initialized

    def verify_adapter_provider_route(
        self,
        route: AdapterProviderRoute,
    ) -> ServiceResult[VerifiedAdapterRouteReceipt]:
        return self.adapter_compatibility.verify_adapter_provider_route(route)

    def initialize_repo_as_native(
        self,
        repo_root: Path,
        *,
        project_name: str,
    ) -> ServiceResult[RepoSkeletonView]:
        prep = self.preparation.get_preparation_input(repo_root)
        if prep.ok and prep.value and prep.value.input.source_corpus_mode == SourceCorpusMode.NONE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "invalid_source_corpus_mode",
                    "Native repo initialization requires source_corpus_mode != none.",
                )
            )
        return self.lake_dependency.initialize_native_repo_skeleton(repo_root, project_name=project_name)

    def validate_native_handoff(self, repo_root: Path) -> ServiceResult[object]:
        return self.preparation.validate_native_handoff(repo_root)

    def mark_provider_repo_ready(self, repo_root: Path, *, summary: str) -> ServiceResult[ProviderReadyView]:
        del summary
        repo_root = Path(repo_root)
        repo_format = self.metadata.get_repo_format(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        if repo_format.value.repo_format == RepoFormat.NATIVE:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "native_release_finalizer_required",
                "Native provider readiness is published by the RepoRelease finalizer transaction.",
            ))
        if repo_format.value.repo_format == RepoFormat.ADAPTER:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "adapter_release_finalizer_required",
                "Adapter provider readiness is published by the RepoRelease finalizer transaction.",
            ))
        return self.runtime.foundation.fail(self.runtime.foundation.issue(
            "provider_format_unknown",
            "Only Native and Adapter repos can become released providers.",
        ))
