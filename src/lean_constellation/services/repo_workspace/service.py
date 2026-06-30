"""RepoWorkspaceService composition and higher-level wrappers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.interface import DeclKind
from lean_constellation.domain.preparation import (
    BootstrapInputValidationView,
    ProviderReadyView,
    ProviderRepoRuntimeShellView,
    RepoRequirementRef,
    RepoPreparationInput,
    RequirementResumeCandidateView,
    RequirementWaitingView,
    SourceCorpusMode,
    UpstreamDependencyInput,
)
from lean_constellation.domain.repo import WorkspaceCoordinatorView
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.repo_workspace.lake_dependency import (
    AdapterSetupView,
    LakeDependencyComponent,
    RepoSkeletonView,
)
from lean_constellation.services.repo_workspace.repo_metadata import RepoMetadataComponent
from lean_constellation.services.repo_workspace.repo_preparation import (
    PreparationStartPreflightView,
    ProviderRepoRuntimeBootstrapProvider,
    RepoPreparationComponent,
)
from lean_constellation.services.repo_workspace.repo_requirement import RepoRequirementComponent
from lean_constellation.services.repo_workspace.workspace_catalog import WorkspaceCatalogComponent

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class RequirementConsumeView(StrictModel):
    requirement_name: str
    provider_repo: str
    attached: bool
    handled: bool
    summary: str
    issues: list[str] = Field(default_factory=list)


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
    ) -> None:
        self.runtime = runtime
        self.metadata = metadata or RepoMetadataComponent(runtime)
        self.requirement = requirement or RepoRequirementComponent(runtime)
        self.lake_dependency = lake_dependency or LakeDependencyComponent(
            runtime,
            self.metadata,
        )
        self.preparation = preparation or RepoPreparationComponent(
            runtime,
            self.metadata,
            self.requirement,
        )
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
    ) -> ServiceResult[object]:
        created = self.requirement.create_requirement(
            repo_root,
            name=name,
            target_repo=target_repo,
            source_description=source_description,
            reason=reason,
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
            )
            if not current.ok:
                return self.runtime.foundation.fail(current.issues)
        return current

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

    def prepare_provider_repo_runtime_shell(
        self,
        workspace_root: Path,
        *,
        target_repo: str,
        preparation_input: RepoPreparationInput,
        project_name: str | None = None,
        runtime_bootstrap: ProviderRepoRuntimeBootstrapProvider | None = None,
    ) -> ServiceResult[ProviderRepoRuntimeShellView]:
        return self.preparation.prepare_provider_repo_runtime_shell(
            workspace_root,
            target_repo=target_repo,
            preparation_input=preparation_input,
            project_name=project_name,
            runtime_bootstrap=runtime_bootstrap,
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
        provider_repo = requirement.value.requirement.provider_repo
        if not provider_repo:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_not_satisfied",
                    "Requirement has no provider repo to attach.",
                    object_ref=requirement_name,
                )
            )
        attached = self.lake_dependency.attach_workspace_repo_dependency(
            consumer_repo_root,
            provider_repo_key=provider_repo,
        )
        if not attached.ok:
            return self.runtime.foundation.fail(attached.issues)
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
                summary=f"Attached and handled requirement {requirement_name}.",
            )
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
        return self.lake_dependency.initialize_adapter_repo_skeleton(
            repo_root,
            project_name=project_name,
            upstream=upstream,
        )

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
        ready = self.metadata.set_provider_ready(repo_root, summary=summary)
        if not ready.ok:
            return self.runtime.foundation.fail(ready.issues)
        prep = self.preparation.get_preparation_input(repo_root)
        satisfied = 0
        if prep.ok and prep.value is not None:
            for ref in prep.value.input.requirement_refs:
                consumer = Path(repo_root).parent / ref.consumer_repo
                result = self.requirement.mark_requirement_satisfied(
                    consumer,
                    requirement_name=ref.requirement_name,
                    provider_repo=Path(repo_root).name,
                    note=f"Provider ready: {summary}",
                )
                if result.ok:
                    satisfied += 1
        return self.runtime.foundation.ok(
            ProviderReadyView(
                provider_ready_marked=True,
                satisfied_requirement_count=satisfied,
                repo_summary=summary.strip(),
                summary=f"Marked provider repo ready; satisfied {satisfied} requirements.",
            )
        )
