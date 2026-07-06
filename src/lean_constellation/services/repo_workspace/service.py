"""RepoWorkspaceService composition and higher-level wrappers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.domain.lake_project import NativeLakeProjectConfig
from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.interface import DeclKind
from lean_constellation.domain.preparation import (
    BootstrapInputValidationView,
    ProviderReadyView,
    RepoDependencyRequirementStatus,
    ProviderRepoRuntimeShellView,
    RepoRequirementRef,
    RepoPreparationInput,
    RequirementResumeCandidateView,
    RequirementWaitingView,
    SourceCorpusMode,
    UpstreamDependencyInput,
)
from lean_constellation.domain.repo import WorkspaceConfig, WorkspaceCoordinatorView, proof_availability_satisfies
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
        native_lake_project_config: NativeLakeProjectConfig | None = None,
        workspace_config: WorkspaceConfig | None = None,
    ) -> None:
        self.runtime = runtime
        self.workspace_config = workspace_config or WorkspaceConfig()
        self.metadata = metadata or RepoMetadataComponent(runtime)
        self.requirement = requirement or RepoRequirementComponent(runtime)
        self.lake_dependency = lake_dependency or LakeDependencyComponent(
            runtime,
            self.metadata,
            config=native_lake_project_config,
        )
        self.preparation = preparation or RepoPreparationComponent(
            runtime,
            self.metadata,
            self.requirement,
            workspace_config=self.workspace_config,
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
            required_proof_availability=self._requirement_proof_availability_for_repo(repo_root),
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

    def _requirement_proof_availability_for_repo(self, repo_root: Path):
        config = self.metadata.get_repo_config(repo_root)
        if config.ok and config.value is not None:
            return config.value.config.default_requirement_proof_availability
        return self.workspace_config.default_requirement_proof_availability

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

    def write_preparation_input(self, repo_root: Path, *, input: RepoPreparationInput):
        return self.preparation.write_preparation_input(repo_root, input=input)

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
        repo_root = Path(repo_root)
        summary = summary.strip()
        if not summary:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("missing_summary", "Repo summary is required."))
        prep = self.preparation.get_preparation_input(repo_root)
        if not prep.ok or prep.value is None:
            return self.runtime.foundation.fail(prep.issues)
        provider_repo = self.runtime.foundation.layout.ensure_safe_key(repo_root.name)
        provider_config = self.metadata.get_repo_config(repo_root)
        if not provider_config.ok or provider_config.value is None:
            return self.runtime.foundation.fail(provider_config.issues)
        requirement_refs: list[tuple[Path, str]] = []
        for ref in prep.value.input.requirement_refs:
            try:
                consumer_repo = self.runtime.foundation.layout.ensure_safe_key(ref.consumer_repo)
                requirement_name = self.runtime.foundation.layout.ensure_safe_key(ref.requirement_name)
            except ValueError as exc:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "invalid_requirement_ref",
                        f"Invalid provider preparation requirement ref: {exc}",
                        object_ref=str(repo_root),
                    )
                )
            consumer = repo_root.parent / consumer_repo
            loaded = self.requirement.get_requirement(consumer, name=requirement_name)
            if not loaded.ok or loaded.value is None:
                return self.runtime.foundation.fail(loaded.issues)
            requirement = loaded.value.requirement
            if requirement.status not in {
                RepoDependencyRequirementStatus.OPEN,
                RepoDependencyRequirementStatus.SATISFIED,
            }:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "requirement_not_open",
                        "Provider ready can only satisfy open or already satisfied requirements.",
                        object_ref=requirement.name,
                        current=requirement.status.value,
                        expected=f"{RepoDependencyRequirementStatus.OPEN.value}|{RepoDependencyRequirementStatus.SATISFIED.value}",
                    )
                )
            expected_provider = self.requirement.effective_provider_repo(requirement)
            if expected_provider != provider_repo:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "requirement_provider_mismatch",
                        "Provider ready requirement ref points at a different provider repo.",
                        object_ref=requirement.name,
                        current=provider_repo,
                        expected=expected_provider,
                    )
                )
            if requirement.status == RepoDependencyRequirementStatus.SATISFIED and requirement.provider_repo:
                if requirement.provider_repo != provider_repo:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "requirement_provider_mismatch",
                            "Satisfied requirement is already attached to a different provider repo.",
                            object_ref=requirement.name,
                            current=provider_repo,
                            expected=requirement.provider_repo,
                        )
                    )
            if not proof_availability_satisfies(
                provider_config.value.config.target_proof_availability,
                requirement.required_proof_availability,
            ):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "provider_proof_availability_insufficient",
                        "Provider repo proof availability does not satisfy the consumer requirement.",
                        object_ref=requirement.name,
                        current=provider_config.value.config.target_proof_availability.value,
                        expected=requirement.required_proof_availability.value,
                    )
                )
            provider_truth = self.requirement.validate_requirement_provider_truth(
                consumer,
                requirement_name=requirement_name,
                provider_repo=provider_repo,
                require_stable=False,
            )
            if not provider_truth.ok:
                return self.runtime.foundation.fail(provider_truth.issues)
            requirement_refs.append((consumer, requirement_name))

        ready = self.metadata.set_provider_ready(repo_root, summary=summary)
        if not ready.ok:
            return self.runtime.foundation.fail(ready.issues)
        for consumer, requirement_name in requirement_refs:
            result = self.requirement.mark_requirement_satisfied(
                consumer,
                requirement_name=requirement_name,
                provider_repo=provider_repo,
                note=f"Provider ready: {summary}",
            )
            if not result.ok:
                return self.runtime.foundation.fail(result.issues)
        satisfied = len(requirement_refs)
        return self.runtime.foundation.ok(
            ProviderReadyView(
                provider_ready_marked=True,
                satisfied_requirement_count=satisfied,
                repo_summary=summary.strip(),
                summary=f"Marked provider repo ready; satisfied {satisfied} requirements.",
            )
        )
