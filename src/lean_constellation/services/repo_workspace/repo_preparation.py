"""Preparation input, requirement-group aggregation, and handoff gates."""

from __future__ import annotations

from pathlib import Path

from lean_constellation.domain.interface import DeclInterface
from lean_constellation.domain.preparation import (
    RepoDependencyRequirementStatus,
    RepoPreparationInput,
    RepoPreparationInputDraftView,
    RepoPreparationInputView,
    RepoRequirementRef,
    RequirementGroupItem,
    RequirementGroupView,
    SourceCorpusMode,
    BootstrapInputValidationView,
    ProviderRepoShellView,
    RepoShellView,
)
from lean_constellation.domain.repo import RepoFormat
from lean_constellation.services.foundation import FoundationContext, FoundationService, ServiceResult, WriteMode
from lean_constellation.services.repo_workspace.repo_metadata import RepoMetadataComponent
from lean_constellation.services.repo_workspace.repo_requirement import RepoRequirementComponent


class RepoPreparationComponent:
    """Read/write repo preparation input and build provider repo shells."""

    def __init__(
        self,
        foundation: FoundationService,
        metadata: RepoMetadataComponent,
        requirement: RepoRequirementComponent,
    ) -> None:
        self.foundation = foundation
        self.metadata = metadata
        self.requirement = requirement

    def write_preparation_input(
        self,
        repo_root: Path,
        *,
        input: RepoPreparationInput,
    ) -> ServiceResult[RepoPreparationInputView]:
        validation = self._validate_input(input)
        if not validation.ok:
            return self.foundation.fail(validation.issues)
        path = self.foundation.layout.preparation_input_path(FoundationContext(repo_root=Path(repo_root)))
        self.foundation.store.ensure_dir(path.parent)
        written = self.foundation.store.write_json_atomic(path, input)
        if not written.ok:
            return self.foundation.fail(written.issues)
        return self.foundation.ok(
            RepoPreparationInputView(repo_root=str(Path(repo_root)), input=input, summary="Wrote preparation input.")
        )

    def get_preparation_input(self, repo_root: Path) -> ServiceResult[RepoPreparationInputView]:
        path = self.foundation.layout.preparation_input_path(FoundationContext(repo_root=Path(repo_root)))
        loaded = self.foundation.store.read_json(path, RepoPreparationInput)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(
                self.foundation.issue(
                    "preparation_input_missing",
                    "Preparation input is missing or invalid.",
                    object_ref=str(path),
                )
            )
        return self.foundation.ok(
            RepoPreparationInputView(
                repo_root=str(Path(repo_root)),
                input=loaded.value,
                summary="Loaded preparation input.",
            )
        )

    def aggregate_requirement_group(self, workspace_root: Path, *, target_repo: str) -> ServiceResult[RequirementGroupView]:
        target_repo = self.foundation.layout.ensure_safe_key(target_repo)
        workspace_root = Path(workspace_root)
        if not workspace_root.exists() or not workspace_root.is_dir():
            return self.foundation.fail(
                self.foundation.issue("workspace_not_found", f"Workspace root not found: {workspace_root}")
            )
        items: list[RequirementGroupItem] = []
        for repo_dir in sorted(path for path in workspace_root.iterdir() if path.is_dir()):
            if not self.foundation.layout.constellation_root(FoundationContext(repo_root=repo_dir)).exists():
                continue
            listed = self.requirement.list_requirements(repo_dir, status=RepoDependencyRequirementStatus.OPEN)
            if not listed.ok or listed.value is None:
                return self.foundation.fail(listed.issues)
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
        return self.foundation.ok(
            RequirementGroupView(
                target_repo=target_repo,
                requirements=items,
                summary=f"Found {len(items)} open requirements for {target_repo}.",
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
            return self.foundation.fail(group.issues)
        if not group.value.requirements:
            return self.foundation.fail(
                self.foundation.issue(
                    "requirement_group_empty",
                    f"No open requirements found for target repo: {target_repo}",
                )
            )
        try:
            source_corpus_mode = SourceCorpusMode(source_corpus_mode)
        except ValueError:
            return self.foundation.fail(
                self.foundation.issue(
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
        return self.foundation.ok(
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
        target_repo = self.foundation.layout.ensure_safe_key(target_repo)
        repo_root = Path(workspace_root) / target_repo
        if repo_root.exists():
            return self.foundation.fail(
                self.foundation.issue(
                    "target_repo_already_exists",
                    f"Target repo already exists: {target_repo}",
                    object_ref=str(repo_root),
                )
            )
        repo_root.mkdir(parents=True)
        ensure = self.metadata.ensure_repo_model(repo_root)
        if not ensure.ok:
            return self.foundation.fail(ensure.issues)
        return self.foundation.ok(
            RepoShellView(
                repo_root=str(repo_root),
                repo_name=target_repo,
                project_name=project_name,
                created=True,
                summary=f"Created provider repo shell: {target_repo}.",
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
            return self.foundation.fail(draft.issues)
        shell = self.create_provider_repo_shell(workspace_root, target_repo=target_repo)
        if not shell.ok or shell.value is None:
            return self.foundation.fail(shell.issues)
        input_view = self.write_preparation_input(Path(shell.value.repo_root), input=draft.value.input)
        if not input_view.ok or input_view.value is None:
            return self.foundation.fail(input_view.issues)
        return self.foundation.ok(
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
            return self.foundation.fail(validation.issues)
        return self.foundation.ok(
            RepoPreparationInputView(input=input_value, summary="Built main repo preparation input.")
        )

    def create_main_repo_shell(
        self,
        workspace_root: Path,
        *,
        repo_name: str,
        project_name: str,
        input: RepoPreparationInput,
    ) -> ServiceResult[RepoShellView]:
        repo_name = self.foundation.layout.ensure_safe_key(repo_name)
        repo_root = Path(workspace_root) / repo_name
        if repo_root.exists():
            return self.foundation.fail(
                self.foundation.issue("target_repo_already_exists", f"Repo already exists: {repo_name}")
            )
        repo_root.mkdir(parents=True)
        ensure = self.metadata.ensure_repo_model(repo_root)
        if not ensure.ok:
            return self.foundation.fail(ensure.issues)
        written = self.write_preparation_input(repo_root, input=input)
        if not written.ok:
            return self.foundation.fail(written.issues)
        return self.foundation.ok(
            RepoShellView(
                repo_root=str(repo_root),
                repo_name=repo_name,
                project_name=project_name,
                created=True,
                summary=f"Created main repo shell: {repo_name}.",
            )
        )

    def validate_requirement_bootstrap_input(
        self,
        repo_root: Path,
        *,
        requirement_refs: list[RepoRequirementRef] | None = None,
    ) -> ServiceResult[BootstrapInputValidationView]:
        loaded = self.get_preparation_input(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        expected = {(ref.consumer_repo, ref.requirement_name) for ref in loaded.value.input.requirement_refs}
        provided = {(ref.consumer_repo, ref.requirement_name) for ref in (requirement_refs or loaded.value.input.requirement_refs)}
        if provided != expected:
            return self.foundation.ok(
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
            return self.foundation.ok(
                BootstrapInputValidationView(
                    passed=False,
                    requirement_count=len(expected),
                    source_corpus_mode=loaded.value.input.source_corpus_mode,
                    issue_code="repo_format_already_set",
                    summary="Repo format has already been set.",
                    suggested_fix="Continue the selected preparation flow or repair the repo manually.",
                )
            )
        return self.foundation.ok(
            BootstrapInputValidationView(
                passed=True,
                requirement_count=len(expected),
                source_corpus_mode=loaded.value.input.source_corpus_mode,
                summary="Requirement bootstrap input is valid.",
            )
        )

    def validate_native_handoff(self, repo_root: Path) -> ServiceResult[object]:
        issues = []
        fmt = self.metadata.get_repo_format(repo_root)
        if not fmt.ok or fmt.value is None or fmt.value.repo_format != RepoFormat.NATIVE:
            issues.append(
                self.foundation.issue(
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
                self.foundation.issue(
                    "native_handoff_source_corpus_missing",
                    "Native handoff requires a source corpus.",
                    field="source_corpus_mode",
                    current=SourceCorpusMode.NONE.value,
                    expected="existing or prepare",
                )
            )
        elif input_view.value.input.source_corpus_mode == SourceCorpusMode.EXISTING:
            root = self.foundation.layout.source_corpus_root(
                FoundationContext(repo_root=Path(repo_root)),
                input_view.value.input.source_corpus_relpath or ".lean_constellation/source",
            )
            if not root.exists():
                issues.append(
                    self.foundation.issue(
                        "native_handoff_source_corpus_not_found",
                        "Existing source corpus path does not exist.",
                        object_ref=str(root),
                    )
                )
        if issues:
            return self.foundation.ok(
                self.foundation.gate_failed("native_handoff", issues, summary=f"{len(issues)} handoff checks failed.")
            )
        warnings = [
            self.foundation.issue(
                "native_handoff_deferred_checks",
                "SourceIndex and root Main node gates are deferred until MaterialService and NodeService are implemented.",
                severity="warning",
            )
        ]
        return self.foundation.ok(
            self.foundation.gate_passed(
                "native_handoff",
                summary="Base native handoff checks passed.",
                warnings=warnings,
            )
        )

    def _validate_input(self, input: RepoPreparationInput) -> ServiceResult[None]:
        if input.source_corpus_mode == SourceCorpusMode.NONE and input.source_corpus_relpath is not None:
            return self.foundation.fail(
                self.foundation.issue(
                    "invalid_source_corpus_mode",
                    "source_corpus_relpath must be None when source_corpus_mode is none.",
                    field="source_corpus_relpath",
                )
            )
        if input.source_corpus_mode != SourceCorpusMode.NONE and not input.source_corpus_relpath:
            return self.foundation.fail(
                self.foundation.issue(
                    "invalid_source_corpus_mode",
                    "source_corpus_relpath is required unless source_corpus_mode is none.",
                    field="source_corpus_relpath",
                )
            )
        names: set[str] = set()
        for interface in input.interface_inputs:
            if interface.name in names:
                return self.foundation.fail(
                    self.foundation.issue("interface_duplicate", f"Duplicate interface input: {interface.name}")
                )
            names.add(interface.name)
        return self.foundation.ok(None)

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
