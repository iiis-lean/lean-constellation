"""RepoWorkspaceService composition and higher-level wrappers."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.interface import DeclKind
from lean_constellation.domain.preparation import (
    BootstrapInputValidationView,
    ProviderReadyView,
    RepoRequirementRef,
    SourceCorpusMode,
    UpstreamDependencyInput,
)
from lean_constellation.domain.repo import WorkspaceCoordinatorView
from lean_constellation.services.external_clients import ExternalClientService
from lean_constellation.services.foundation import FoundationService, ServiceResult
from lean_constellation.services.repo_workspace.lake_dependency import (
    AdapterSetupView,
    LakeDependencyComponent,
    RepoSkeletonView,
)
from lean_constellation.services.repo_workspace.repo_metadata import RepoMetadataComponent
from lean_constellation.services.repo_workspace.repo_preparation import RepoPreparationComponent
from lean_constellation.services.repo_workspace.repo_requirement import RepoRequirementComponent
from lean_constellation.services.repo_workspace.workspace_catalog import WorkspaceCatalogComponent


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
        *,
        foundation: FoundationService | None = None,
        external: ExternalClientService | None = None,
        metadata: RepoMetadataComponent | None = None,
        requirement: RepoRequirementComponent | None = None,
        lake_dependency: LakeDependencyComponent | None = None,
        preparation: RepoPreparationComponent | None = None,
        workspace_catalog: WorkspaceCatalogComponent | None = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.external = external or ExternalClientService()
        self.metadata = metadata or RepoMetadataComponent(self.foundation)
        self.requirement = requirement or RepoRequirementComponent(self.foundation)
        self.lake_dependency = lake_dependency or LakeDependencyComponent(
            self.foundation,
            self.external,
            self.metadata,
        )
        self.preparation = preparation or RepoPreparationComponent(
            self.foundation,
            self.metadata,
            self.requirement,
        )
        self.workspace_catalog = workspace_catalog or WorkspaceCatalogComponent(
            self.foundation,
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
            return self.foundation.fail(created.issues)
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
                return self.foundation.fail(current.issues)
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

    def attach_provider_for_requirement(
        self,
        consumer_repo_root: Path,
        *,
        requirement_name: str,
    ) -> ServiceResult[RequirementConsumeView]:
        requirement = self.requirement.get_requirement(consumer_repo_root, name=requirement_name)
        if not requirement.ok or requirement.value is None:
            return self.foundation.fail(requirement.issues)
        provider_repo = requirement.value.requirement.provider_repo
        if not provider_repo:
            return self.foundation.fail(
                self.foundation.issue(
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
            return self.foundation.fail(attached.issues)
        handled = self.requirement.mark_requirement_handled(
            consumer_repo_root,
            requirement_name=requirement_name,
            note=f"Attached provider repo {provider_repo}.",
        )
        if not handled.ok:
            return self.foundation.fail(handled.issues)
        return self.foundation.ok(
            RequirementConsumeView(
                requirement_name=requirement_name,
                provider_repo=provider_repo,
                attached=True,
                handled=True,
                summary=f"Attached and handled requirement {requirement_name}.",
            )
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
            return self.foundation.fail(
                self.foundation.issue(
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
            return self.foundation.fail(ready.issues)
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
        return self.foundation.ok(
            ProviderReadyView(
                provider_ready_marked=True,
                satisfied_requirement_count=satisfied,
                summary=f"Marked provider repo ready; satisfied {satisfied} requirements.",
            )
        )
