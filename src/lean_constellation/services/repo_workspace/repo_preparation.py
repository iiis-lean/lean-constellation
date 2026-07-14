"""Preparation input, requirement-group aggregation, and handoff gates."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.interface import DeclInterface
from lean_constellation.domain.preparation import (
    BootstrapInputValidationView,
    RepoDependencyRequirementStatus,
    RepoPreparationInput,
    RepoPreparationInputDraftView,
    RepoPreparationInputView,
    RepoRequirementRef,
    RequirementGroupItem,
    RequirementGroupView,
    SourceCorpusMode,
    ProviderRepoPreparationView,
    ProviderRepoShellView,
    RepoShellView,
)
from lean_constellation.domain.repo import ProofAvailability, RepoFormat, RepoWorkMode, WorkspaceConfig
from lean_constellation.services.foundation import (
    FoundationContext,
    GateReport,
    IssueSeverity,
    ServiceIssue,
    ServiceResult,
    WriteMode,
)
from lean_constellation.services.repo_workspace.repo_metadata import RepoMetadataComponent
from lean_constellation.services.repo_workspace.repo_requirement import RepoRequirementComponent

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class PreparationStartPreflightView(StrictModel):
    repo_root: str
    expected_format: RepoFormat | None = None
    repo_format: RepoFormat
    preparation_input_exists: bool
    source_corpus_mode: SourceCorpusMode | None = None
    source_corpus_relpath: str | None = None
    source_corpus_path: str | None = None
    source_corpus_exists: bool | None = None
    lake_skeleton_present: bool | None = None
    adapter_upstream_metadata_exists: bool | None = None
    skeleton_gate: GateReport | None = None
    passed: bool
    issues: list[ServiceIssue] = Field(default_factory=list)
    warnings: list[ServiceIssue] = Field(default_factory=list)
    summary: str
    suggested_next_action: str | None = None


class PreparationInterfaceAppendView(StrictModel):
    repo_root: str
    added_names: list[str] = Field(default_factory=list)
    existing_names: list[str] = Field(default_factory=list)
    total_count: int
    changed: bool
    summary: str


class RepoPreparationComponent:
    """Read/write repo preparation input and build provider repo shells."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        metadata: RepoMetadataComponent,
        requirement: RepoRequirementComponent,
        workspace_config: WorkspaceConfig | None = None,
    ) -> None:
        self.runtime = runtime
        self.metadata = metadata
        self.requirement = requirement
        self.workspace_config = workspace_config or WorkspaceConfig()

    def write_preparation_input(
        self,
        repo_root: Path,
        *,
        input: RepoPreparationInput,
    ) -> ServiceResult[RepoPreparationInputView]:
        validation = self.validate_preparation_input(input)
        if not validation.ok:
            return self.runtime.foundation.fail(validation.issues)
        path = self.runtime.foundation.layout.preparation_input_path(FoundationContext(repo_root=Path(repo_root)))
        self.runtime.foundation.store.ensure_dir(path.parent)
        written = self.runtime.foundation.store.write_json_atomic(path, input)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(
            RepoPreparationInputView(repo_root=str(Path(repo_root)), input=input, summary="Wrote preparation input.")
        )

    def validate_preparation_input(
        self,
        input: RepoPreparationInput,
    ) -> ServiceResult[None]:
        return self._validate_input(input)

    def get_preparation_input(self, repo_root: Path) -> ServiceResult[RepoPreparationInputView]:
        path = self.runtime.foundation.layout.preparation_input_path(FoundationContext(repo_root=Path(repo_root)))
        loaded = self.runtime.foundation.store.read_json(path, RepoPreparationInput)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "preparation_input_missing",
                    "Preparation input is missing or invalid.",
                    object_ref=str(path),
                )
            )
        return self.runtime.foundation.ok(
            RepoPreparationInputView(
                repo_root=str(Path(repo_root)),
                input=loaded.value,
                summary="Loaded preparation input.",
            )
        )

    def preview_preparation_interface_append(
        self,
        repo_root: Path,
        *,
        interfaces: list[DeclInterface],
    ) -> ServiceResult[PreparationInterfaceAppendView]:
        current = self.get_preparation_input(repo_root)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        requested_names: set[str] = set()
        for interface in interfaces:
            if interface.name in requested_names:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "interface_duplicate",
                        f"Duplicate interface in append request: {interface.name}",
                        field=interface.name,
                    )
                )
            requested_names.add(interface.name)
        existing_by_name = {
            interface.name: interface for interface in current.value.input.interface_inputs
        }
        added_names: list[str] = []
        existing_names: list[str] = []
        for interface in interfaces:
            existing = existing_by_name.get(interface.name)
            if existing is None:
                added_names.append(interface.name)
                continue
            if existing.model_dump(mode="json") != interface.model_dump(mode="json"):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "protected_interface_conflict",
                        f"Protected preparation interface conflicts with the append request: {interface.name}",
                        object_ref=str(Path(repo_root)),
                        field=interface.name,
                    )
                )
            existing_names.append(interface.name)
        return self.runtime.foundation.ok(
            PreparationInterfaceAppendView(
                repo_root=str(Path(repo_root)),
                added_names=added_names,
                existing_names=existing_names,
                total_count=len(current.value.input.interface_inputs) + len(added_names),
                changed=bool(added_names),
                summary=(
                    f"Would append {len(added_names)} preparation interfaces and reuse "
                    f"{len(existing_names)} existing interfaces."
                ),
            )
        )

    def append_preparation_interfaces(
        self,
        repo_root: Path,
        *,
        interfaces: list[DeclInterface],
    ) -> ServiceResult[PreparationInterfaceAppendView]:
        preview = self.preview_preparation_interface_append(repo_root, interfaces=interfaces)
        if not preview.ok or preview.value is None:
            return self.runtime.foundation.fail(preview.issues)
        if not preview.value.changed:
            return self.runtime.foundation.ok(
                preview.value.model_copy(
                    update={
                        "summary": (
                            f"Reused {len(preview.value.existing_names)} existing preparation "
                            "interfaces; no write was required."
                        )
                    }
                )
            )
        current = self.get_preparation_input(repo_root)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        existing_names = {interface.name for interface in current.value.input.interface_inputs}
        updated = current.value.input.model_copy(deep=True)
        updated.interface_inputs.extend(
            interface.model_copy(deep=True)
            for interface in interfaces
            if interface.name not in existing_names
        )
        validation = self._validate_input(updated)
        if not validation.ok:
            return self.runtime.foundation.fail(validation.issues)
        path = self.runtime.foundation.layout.preparation_input_path(
            FoundationContext(repo_root=Path(repo_root))
        )
        written = self.runtime.foundation.store.write_json_atomic(
            path,
            updated,
            mode=WriteMode.UPDATE_EXISTING,
        )
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(
            preview.value.model_copy(
                update={
                    "summary": (
                        f"Appended {len(preview.value.added_names)} preparation interfaces and "
                        f"reused {len(preview.value.existing_names)} existing interfaces."
                    )
                }
            )
        )

    def aggregate_requirement_group(self, workspace_root: Path, *, target_repo: str) -> ServiceResult[RequirementGroupView]:
        target_repo = self.runtime.foundation.layout.ensure_safe_key(target_repo)
        workspace_root = Path(workspace_root)
        if not workspace_root.exists() or not workspace_root.is_dir():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("workspace_not_found", f"Workspace root not found: {workspace_root}")
            )
        items: list[RequirementGroupItem] = []
        for repo_dir in sorted(path for path in workspace_root.iterdir() if path.is_dir()):
            if not self.runtime.foundation.layout.constellation_root(FoundationContext(repo_root=repo_dir)).exists():
                continue
            listed = self.requirement.list_requirements(repo_dir, status=RepoDependencyRequirementStatus.OPEN)
            if not listed.ok or listed.value is None:
                return self.runtime.foundation.fail(listed.issues)
            for view in listed.value:
                if view.requirement.target_repo == target_repo:
                    items.append(
                        RequirementGroupItem(
                            consumer_repo=repo_dir.name,
                            consumer_repo_root=str(repo_dir),
                            requirement=view.requirement,
                        )
                    )
        items.sort(key=lambda item: (item.consumer_repo, item.requirement.name))
        required = self._required_proof_availability(items)
        work_mode = self._provider_work_mode(required)
        return self.runtime.foundation.ok(
            RequirementGroupView(
                target_repo=target_repo,
                required_proof_availability=required,
                provider_work_mode=work_mode,
                requirements=items,
                summary=(
                    f"Found {len(items)} open requirements for {target_repo}; "
                    f"provider target is {required.value}/{work_mode.value}."
                ),
            )
        )

    def build_preparation_input_from_group(
        self,
        workspace_root: Path,
        *,
        target_repo: str,
        source_corpus_mode: SourceCorpusMode | str = SourceCorpusMode.PREPARE,
    ) -> ServiceResult[RepoPreparationInputDraftView]:
        group = self.aggregate_requirement_group(workspace_root, target_repo=target_repo)
        if not group.ok or group.value is None:
            return self.runtime.foundation.fail(group.issues)
        if not group.value.requirements:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_group_empty",
                    f"No open requirements found for target repo: {target_repo}",
                )
            )
        try:
            source_corpus_mode = SourceCorpusMode(source_corpus_mode)
        except ValueError:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "invalid_source_corpus_mode",
                    "source_corpus_mode must be existing, prepare, or none.",
                    current=str(source_corpus_mode),
                    expected="existing|prepare|none",
                )
            )
        warnings: list[str] = []
        interfaces: list[DeclInterface] = []
        seen: dict[str, DeclInterface] = {}
        for item in group.value.requirements:
            for interface in item.requirement.interfaces:
                if interface.name not in seen:
                    seen[interface.name] = interface
                    interfaces.append(interface)
                elif seen[interface.name].model_dump() != interface.model_dump():
                    warnings.append(
                        f"Interface conflict for {interface.name}; kept first from sorted requirement order."
                    )
        reasons = [
            f"- {item.consumer_repo}/{item.requirement.name}: {item.requirement.reason}"
            for item in group.value.requirements
            if item.requirement.reason
        ]
        goal = self._build_provider_goal(target_repo, group.value.requirements, interfaces)
        source_description = self._build_provider_source_description(target_repo, group.value.requirements)
        refs = [
            RepoRequirementRef(consumer_repo=item.consumer_repo, requirement_name=item.requirement.name)
            for item in group.value.requirements
        ]
        notes_lines = [
            f"Aggregated {len(group.value.requirements)} requirements for {target_repo}.",
            "Requirement ordering uses (consumer_repo, requirement_name).",
        ]
        if reasons:
            notes_lines.append("Requirement reasons:\n" + "\n".join(reasons))
        if warnings:
            notes_lines.append("Warnings:\n" + "\n".join(f"- {warning}" for warning in warnings))
        input_value = RepoPreparationInput(
            goal=goal,
            source_corpus_mode=source_corpus_mode,
            source_corpus_relpath=None if source_corpus_mode == SourceCorpusMode.NONE else ".lean_constellation/source",
            source_description=source_description,
            interface_inputs=interfaces,
            allow_interface_supplement=True,
            requirement_refs=refs,
            notes="\n\n".join(notes_lines),
        )
        return self.runtime.foundation.ok(
            RepoPreparationInputDraftView(
                input=input_value,
                requirement_group=group.value,
                warnings=warnings,
                summary=f"Built preparation input draft for {target_repo}.",
            )
        )

    def create_provider_repo_shell(
        self,
        workspace_root: Path,
        *,
        target_repo: str,
        project_name: str | None = None,
    ) -> ServiceResult[RepoShellView]:
        target_repo = self.runtime.foundation.layout.ensure_safe_key(target_repo)
        repo_root = Path(workspace_root) / target_repo
        if repo_root.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "target_repo_already_exists",
                    f"Target repo already exists: {target_repo}",
                    object_ref=str(repo_root),
                )
            )
        repo_root.mkdir(parents=True)
        ensure = self.metadata.ensure_repo_model(repo_root)
        if not ensure.ok:
            self.rollback_created_repo(repo_root)
            return self.runtime.foundation.fail(ensure.issues)
        return self.runtime.foundation.ok(
            RepoShellView(
                repo_root=str(repo_root),
                repo_name=target_repo,
                project_name=project_name,
                created=True,
                summary=f"Created provider repo shell: {target_repo}.",
            )
        )

    def prepare_provider_repo_shell(
        self,
        workspace_root: Path,
        *,
        target_repo: str,
        preparation_input: RepoPreparationInput,
        project_name: str | None = None,
    ) -> ServiceResult[ProviderRepoPreparationView]:
        try:
            target_repo = self.runtime.foundation.layout.ensure_safe_key(target_repo)
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "invalid_target_repo_name",
                    f"Invalid target repo name: {exc}",
                    field="target_repo",
                    current=str(target_repo),
                )
            )
        validation = self._validate_input(preparation_input)
        if not validation.ok:
            return self.runtime.foundation.fail(validation.issues)
        repo_root = Path(workspace_root) / target_repo
        if repo_root.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "target_repo_already_exists",
                    f"Target repo already exists: {target_repo}",
                    object_ref=str(repo_root),
                )
            )

        shell = self.create_provider_repo_shell(
            workspace_root,
            target_repo=target_repo,
            project_name=project_name,
        )
        if not shell.ok or shell.value is None:
            return self.runtime.foundation.fail(shell.issues)
        created_repo_root = Path(shell.value.repo_root)
        config = self._provider_config_for_group(workspace_root, target_repo=target_repo)
        if not config.ok or config.value is None:
            if any(issue.kind == "requirement_group_empty" for issue in config.issues) and not preparation_input.requirement_refs:
                required_proof_availability = self.workspace_config.default_requirement_proof_availability
                provider_work_mode = self._provider_work_mode(required_proof_availability)
            else:
                self.rollback_created_repo(created_repo_root)
                return self.runtime.foundation.fail(config.issues)
        else:
            required_proof_availability = config.value.required_proof_availability
            provider_work_mode = config.value.provider_work_mode
        configured = self.metadata.update_repo_config(
            created_repo_root,
            target_proof_availability=required_proof_availability,
            work_mode=provider_work_mode,
        )
        if not configured.ok or configured.value is None:
            self.rollback_created_repo(created_repo_root)
            return self.runtime.foundation.fail(configured.issues)

        input_view = self.write_preparation_input(created_repo_root, input=preparation_input)
        if not input_view.ok or input_view.value is None:
            self.rollback_created_repo(created_repo_root)
            return self.runtime.foundation.fail(input_view.issues)

        return self.runtime.foundation.ok(
            ProviderRepoPreparationView(
                shell=shell.value,
                preparation_input=input_view.value,
                summary=f"Prepared provider repo shell and preparation input for {target_repo}.",
            )
        )

    def create_provider_repo_shell_from_group(
        self,
        workspace_root: Path,
        *,
        target_repo: str,
        source_corpus_mode: SourceCorpusMode | str = SourceCorpusMode.PREPARE,
    ) -> ServiceResult[ProviderRepoShellView]:
        draft = self.build_preparation_input_from_group(
            workspace_root,
            target_repo=target_repo,
            source_corpus_mode=source_corpus_mode,
        )
        if not draft.ok or draft.value is None:
            return self.runtime.foundation.fail(draft.issues)
        shell = self.create_provider_repo_shell(workspace_root, target_repo=target_repo)
        if not shell.ok or shell.value is None:
            return self.runtime.foundation.fail(shell.issues)
        input_view = self.write_preparation_input(Path(shell.value.repo_root), input=draft.value.input)
        if not input_view.ok or input_view.value is None:
            return self.runtime.foundation.fail(input_view.issues)
        configured = self.metadata.update_repo_config(
            Path(shell.value.repo_root),
            target_proof_availability=draft.value.requirement_group.required_proof_availability,
            work_mode=draft.value.requirement_group.provider_work_mode,
        )
        if not configured.ok:
            return self.runtime.foundation.fail(configured.issues)
        return self.runtime.foundation.ok(
            ProviderRepoShellView(
                shell=shell.value,
                preparation_input=input_view.value,
                requirement_group=draft.value.requirement_group,
                summary=f"Created provider repo shell and preparation input for {target_repo}.",
            )
        )

    def build_main_repo_preparation_input(
        self,
        *,
        goal: str,
        source_corpus_mode: SourceCorpusMode | str,
        source_description: str | None = None,
        interface_inputs: list[DeclInterface] | None = None,
        allow_interface_supplement: bool = True,
        notes: str | None = None,
    ) -> ServiceResult[RepoPreparationInputView]:
        mode = SourceCorpusMode(source_corpus_mode)
        input_value = RepoPreparationInput(
            goal=goal,
            source_corpus_mode=mode,
            source_corpus_relpath=None if mode == SourceCorpusMode.NONE else ".lean_constellation/source",
            source_description=source_description,
            interface_inputs=interface_inputs or [],
            allow_interface_supplement=allow_interface_supplement,
            requirement_refs=[],
            notes=notes,
        )
        validation = self._validate_input(input_value)
        if not validation.ok:
            return self.runtime.foundation.fail(validation.issues)
        return self.runtime.foundation.ok(
            RepoPreparationInputView(input=input_value, summary="Built main repo preparation input.")
        )

    def create_main_repo_shell(
        self,
        workspace_root: Path,
        *,
        repo_name: str,
        project_name: str,
        input: RepoPreparationInput | None = None,
    ) -> ServiceResult[RepoShellView]:
        repo_name = self.runtime.foundation.layout.ensure_safe_key(repo_name)
        repo_root = Path(workspace_root) / repo_name
        if repo_root.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("target_repo_already_exists", f"Repo already exists: {repo_name}")
            )
        repo_root.mkdir(parents=True)
        ensure = self.metadata.ensure_repo_model(repo_root)
        if not ensure.ok:
            self.rollback_created_repo(repo_root)
            return self.runtime.foundation.fail(ensure.issues)
        configured = self.metadata.update_repo_config(
            repo_root,
            target_proof_availability=self.workspace_config.default_direct_repo_proof_availability,
            work_mode=self.workspace_config.default_direct_repo_work_mode,
            default_requirement_proof_availability=self.workspace_config.default_requirement_proof_availability,
        )
        if not configured.ok:
            self.rollback_created_repo(repo_root)
            return self.runtime.foundation.fail(configured.issues)
        if input is not None:
            written = self.write_preparation_input(repo_root, input=input)
            if not written.ok:
                self.rollback_created_repo(repo_root)
                return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(
            RepoShellView(
                repo_root=str(repo_root),
                repo_name=repo_name,
                project_name=project_name,
                created=True,
                summary=f"Created main repo shell: {repo_name}.",
            )
        )

    def _provider_config_for_group(
        self,
        workspace_root: Path,
        *,
        target_repo: str,
    ) -> ServiceResult[RequirementGroupView]:
        group = self.aggregate_requirement_group(workspace_root, target_repo=target_repo)
        if not group.ok or group.value is None:
            return self.runtime.foundation.fail(group.issues)
        if not group.value.requirements:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_group_empty",
                    f"No open requirements found for target repo: {target_repo}",
                )
            )
        return self.runtime.foundation.ok(group.value)

    def _required_proof_availability(self, items: list[RequirementGroupItem]) -> ProofAvailability:
        if any(item.requirement.required_proof_availability == ProofAvailability.PROVED for item in items):
            return ProofAvailability.PROVED
        return ProofAvailability.DECLARED

    def _provider_work_mode(self, required: ProofAvailability) -> RepoWorkMode:
        return self.workspace_config.requirement_provider_work_mode_by_proof_availability[required]

    def validate_requirement_bootstrap_input(
        self,
        repo_root: Path,
        *,
        requirement_refs: list[RepoRequirementRef] | None = None,
    ) -> ServiceResult[BootstrapInputValidationView]:
        loaded = self.get_preparation_input(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        expected = {(ref.consumer_repo, ref.requirement_name) for ref in loaded.value.input.requirement_refs}
        provided = {(ref.consumer_repo, ref.requirement_name) for ref in (requirement_refs or loaded.value.input.requirement_refs)}
        if provided != expected:
            return self.runtime.foundation.ok(
                BootstrapInputValidationView(
                    passed=False,
                    requirement_count=len(expected),
                    source_corpus_mode=loaded.value.input.source_corpus_mode,
                    issue_code="requirement_refs_mismatch",
                    summary="Flow input requirement refs do not match preparation input.",
                    suggested_fix="Use the requirement_refs recorded in preparation_input.json.",
                )
            )
        repo_format = self.metadata.get_repo_format(repo_root)
        if repo_format.ok and repo_format.value and repo_format.value.repo_format != RepoFormat.UNKNOWN:
            return self.runtime.foundation.ok(
                BootstrapInputValidationView(
                    passed=False,
                    requirement_count=len(expected),
                    source_corpus_mode=loaded.value.input.source_corpus_mode,
                    issue_code="repo_format_already_set",
                    summary="Repo format has already been set.",
                    suggested_fix="Continue the selected preparation flow or repair the repo manually.",
                )
            )
        return self.runtime.foundation.ok(
            BootstrapInputValidationView(
                passed=True,
                requirement_count=len(expected),
                source_corpus_mode=loaded.value.input.source_corpus_mode,
                summary="Requirement bootstrap input is valid.",
            )
        )

    def get_preparation_start_preflight(
        self,
        repo_root: Path,
        *,
        expected_format: RepoFormat | str | None = None,
    ) -> ServiceResult[PreparationStartPreflightView]:
        repo_root = Path(repo_root)
        expected = self._coerce_expected_format(expected_format)
        if not expected.ok:
            return self.runtime.foundation.fail(expected.issues)

        issues: list[ServiceIssue] = []
        warnings: list[ServiceIssue] = []

        repo_format = self.metadata.get_repo_format(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        actual_format = repo_format.value.repo_format
        expected_value = expected.value
        effective_format = expected_value if expected_value is not None else actual_format

        if actual_format == RepoFormat.UNKNOWN:
            issues.append(
                self.runtime.foundation.issue(
                    "preparation_start_repo_format_unset",
                    "Repo format must be selected before starting native or adapter preparation.",
                    field="repo_format",
                    current=RepoFormat.UNKNOWN.value,
                    expected="native|adapter",
                    suggested_action="Run the bootstrap format discovery flow or set the repo format before starting preparation.",
                )
            )
        elif expected_value is not None and actual_format != expected_value:
            issues.append(
                self.runtime.foundation.issue(
                    "preparation_start_repo_format_mismatch",
                    "Repo format does not match the requested preparation flow.",
                    field="repo_format",
                    current=actual_format.value,
                    expected=expected_value.value,
                    suggested_action="Start the matching preparation flow, or repair the repo format before continuing.",
                )
            )

        input_view = self.get_preparation_input(repo_root)
        preparation_input_exists = input_view.ok and input_view.value is not None
        source_mode: SourceCorpusMode | None = None
        source_relpath: str | None = None
        source_path: Path | None = None
        source_exists: bool | None = None
        if not preparation_input_exists:
            issues.extend(input_view.issues)
        else:
            input_value = input_view.value.input  # type: ignore[union-attr]
            source_mode = input_value.source_corpus_mode
            source_relpath = input_value.source_corpus_relpath
            if source_mode == SourceCorpusMode.NONE:
                source_exists = None
                if effective_format == RepoFormat.NATIVE:
                    issues.append(
                        self.runtime.foundation.issue(
                            "preparation_start_native_source_corpus_none",
                            "Native preparation requires source_corpus_mode to be existing or prepare.",
                            field="source_corpus_mode",
                            current=SourceCorpusMode.NONE.value,
                            expected="existing|prepare",
                            suggested_action="Update preparation_input.json before starting native preparation.",
                        )
                    )
            else:
                try:
                    source_path = self.runtime.foundation.layout.source_corpus_root(
                        FoundationContext(repo_root=repo_root),
                        source_relpath or ".lean_constellation/source",
                    )
                    source_exists = source_path.exists()
                    if source_mode == SourceCorpusMode.EXISTING and not source_exists:
                        warnings.append(
                            self.runtime.foundation.issue(
                                "preparation_start_source_corpus_missing",
                                "source_corpus_mode is existing, but the source corpus path does not exist yet.",
                                severity=IssueSeverity.WARNING,
                                object_ref=str(source_path),
                                field="source_corpus_relpath",
                                suggested_action="Create or import the source corpus before SourceCorpus preflight, or change the mode to prepare.",
                            )
                        )
                except ValueError as exc:
                    issues.append(
                        self.runtime.foundation.issue(
                            "preparation_start_source_corpus_path_invalid",
                            f"Source corpus path is invalid: {exc}",
                            field="source_corpus_relpath",
                            current=source_relpath,
                        )
                    )

        skeleton_gate: GateReport | None = None
        lake_skeleton_present: bool | None = None
        if effective_format in {RepoFormat.NATIVE, RepoFormat.ADAPTER} and actual_format != RepoFormat.UNKNOWN:
            skeleton = self.runtime.repo_workspace.lake_dependency.check_native_repo_skeleton(repo_root)
            if not skeleton.ok or skeleton.value is None:
                return self.runtime.foundation.fail(skeleton.issues)
            skeleton_gate = skeleton.value
            lake_skeleton_present = skeleton.value.passed
            if not skeleton.value.passed:
                issues.extend(skeleton.value.issues)

        adapter_upstream_metadata_exists: bool | None = None
        if effective_format == RepoFormat.ADAPTER:
            adapter_upstream_path = (
                self.runtime.foundation.layout.constellation_root(FoundationContext(repo_root=repo_root))
                / "adapter_upstream.json"
            )
            adapter_upstream_metadata_exists = adapter_upstream_path.exists()
            if not adapter_upstream_metadata_exists:
                issues.append(
                    self.runtime.foundation.issue(
                        "preparation_start_adapter_upstream_missing",
                        "Adapter preparation requires adapter upstream metadata.",
                        object_ref=str(adapter_upstream_path),
                        suggested_action="Write adapter_upstream metadata before starting AdapterRepoPreparationFlow.",
                    )
                )

        passed = not issues
        summary = (
            "Preparation start preflight passed."
            if passed
            else f"Preparation start preflight found {len(issues)} blocking issues."
        )
        if passed and warnings:
            summary = f"{summary} {len(warnings)} warnings require attention."
        return self.runtime.foundation.ok(
            PreparationStartPreflightView(
                repo_root=str(repo_root),
                expected_format=expected_value,
                repo_format=actual_format,
                preparation_input_exists=preparation_input_exists,
                source_corpus_mode=source_mode,
                source_corpus_relpath=source_relpath,
                source_corpus_path=str(source_path) if source_path is not None else None,
                source_corpus_exists=source_exists,
                lake_skeleton_present=lake_skeleton_present,
                adapter_upstream_metadata_exists=adapter_upstream_metadata_exists,
                skeleton_gate=skeleton_gate,
                passed=passed,
                issues=issues,
                warnings=warnings,
                summary=summary,
                suggested_next_action=None if passed else "Repair the listed issues before starting the preparation flow.",
            )
        )

    def validate_native_handoff(self, repo_root: Path) -> ServiceResult[object]:
        issues = []
        fmt = self.metadata.get_repo_format(repo_root)
        if not fmt.ok or fmt.value is None or fmt.value.repo_format != RepoFormat.NATIVE:
            issues.append(
                self.runtime.foundation.issue(
                    "native_handoff_repo_format_invalid",
                    "Native handoff requires repo_format=native.",
                    current=fmt.value.repo_format.value if fmt.ok and fmt.value else None,
                    expected=RepoFormat.NATIVE.value,
                )
            )
        input_view = self.get_preparation_input(repo_root)
        if not input_view.ok or input_view.value is None:
            issues.extend(input_view.issues)
        elif input_view.value.input.source_corpus_mode == SourceCorpusMode.NONE:
            issues.append(
                self.runtime.foundation.issue(
                    "native_handoff_source_corpus_missing",
                    "Native handoff requires a source corpus.",
                    field="source_corpus_mode",
                    current=SourceCorpusMode.NONE.value,
                    expected="existing or prepare",
                )
            )
        elif input_view.value.input.source_corpus_mode == SourceCorpusMode.EXISTING:
            root = self.runtime.foundation.layout.source_corpus_root(
                FoundationContext(repo_root=Path(repo_root)),
                input_view.value.input.source_corpus_relpath or ".lean_constellation/source",
            )
            if not root.exists():
                issues.append(
                    self.runtime.foundation.issue(
                        "native_handoff_source_corpus_not_found",
                        "Existing source corpus path does not exist.",
                        object_ref=str(root),
                    )
                )
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed("native_handoff", issues, summary=f"{len(issues)} handoff checks failed.")
            )
        warnings = [
            self.runtime.foundation.issue(
                "native_handoff_deferred_checks",
                "SourceIndex and root Main node gates are deferred until MaterialService and NodeService are implemented.",
                severity="warning",
            )
        ]
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "native_handoff",
                summary="Base native handoff checks passed.",
                warnings=warnings,
            )
        )

    def _validate_input(self, input: RepoPreparationInput) -> ServiceResult[None]:
        if input.source_corpus_mode == SourceCorpusMode.NONE and input.source_corpus_relpath is not None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "invalid_source_corpus_mode",
                    "source_corpus_relpath must be None when source_corpus_mode is none.",
                    field="source_corpus_relpath",
                )
            )
        if input.source_corpus_mode != SourceCorpusMode.NONE and not input.source_corpus_relpath:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "invalid_source_corpus_mode",
                    "source_corpus_relpath is required unless source_corpus_mode is none.",
                    field="source_corpus_relpath",
                )
            )
        names: set[str] = set()
        for interface in input.interface_inputs:
            if interface.name in names:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("interface_duplicate", f"Duplicate interface input: {interface.name}")
                )
            names.add(interface.name)
        return self.runtime.foundation.ok(None)

    def _coerce_expected_format(self, expected_format: RepoFormat | str | None) -> ServiceResult[RepoFormat | None]:
        if expected_format is None:
            return self.runtime.foundation.ok(None)
        try:
            expected = RepoFormat(expected_format)
        except ValueError:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "invalid_expected_repo_format",
                    "expected_format must be native or adapter.",
                    current=str(expected_format),
                    expected="native|adapter",
                    field="expected_format",
                )
            )
        if expected == RepoFormat.UNKNOWN:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "invalid_expected_repo_format",
                    "expected_format must be native or adapter.",
                    current=RepoFormat.UNKNOWN.value,
                    expected="native|adapter",
                    field="expected_format",
                )
            )
        return self.runtime.foundation.ok(expected)

    @staticmethod
    def rollback_created_repo(repo_root: Path) -> None:
        shutil.rmtree(repo_root, ignore_errors=True)

    @staticmethod
    def _build_provider_goal(
        target_repo: str,
        requirements: list[RequirementGroupItem],
        interfaces: list[DeclInterface],
    ) -> str:
        consumer_names = ", ".join(sorted({item.consumer_repo for item in requirements}))
        interface_names = ", ".join(interface.name for interface in interfaces) if interfaces else "the requested root interfaces"
        return (
            f"Prepare a Lean provider repo `{target_repo}` for consumer repos {consumer_names}. "
            f"The repo must satisfy and expose root interfaces for {interface_names}, and should also make "
            "important supporting public definitions and lemmas available when they are needed by those interfaces."
        )

    @staticmethod
    def _build_provider_source_description(
        target_repo: str,
        requirements: list[RequirementGroupItem],
    ) -> str:
        sections = [f"Collected dependency requirements for `{target_repo}`:"]
        for item in requirements:
            lines = [f"- {item.consumer_repo}/{item.requirement.name}"]
            if item.requirement.source_description:
                lines.append(f"  source: {item.requirement.source_description}")
            if item.requirement.reason:
                lines.append(f"  reason: {item.requirement.reason}")
            if item.requirement.interfaces:
                lines.append("  requested interfaces:")
                for interface in item.requirement.interfaces:
                    lines.append(
                        f"    - {interface.name} ({interface.kind.value}): {interface.summary}"
                    )
            if len(lines) == 1:
                lines.append("  source: No explicit source description was provided.")
            sections.append("\n".join(lines))
        return "\n".join(sections)
