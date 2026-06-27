"""Repo dependency requirement truth operations."""

from __future__ import annotations

from pathlib import Path

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import (
    RepoDependencyRequirement,
    RepoDependencyRequirementStatus,
    RequirementView,
)
from lean_constellation.services.foundation import FoundationContext, FoundationService, ServiceResult, WriteMode


class RepoRequirementComponent:
    """Maintain `.lean_constellation/repo_dependency_requirements/*.json`."""

    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def create_requirement(
        self,
        repo_root: Path,
        *,
        name: str,
        target_repo: str,
        source_description: str | None = None,
        reason: str | None = None,
    ) -> ServiceResult[RequirementView]:
        safe_name = self.foundation.layout.ensure_safe_key(name)
        safe_target = self.foundation.layout.ensure_safe_key(target_repo)
        if not (source_description and source_description.strip()) and not (reason and reason.strip()):
            return self.foundation.fail(
                self.foundation.issue(
                    "requirement_missing_context",
                    "Requirement needs at least source_description or reason.",
                )
            )
        requirement = RepoDependencyRequirement(
            name=safe_name,
            target_repo=safe_target,
            source_description=self._strip_or_none(source_description),
            reason=self._strip_or_none(reason),
        )
        path = self._path(repo_root, safe_name)
        self.foundation.store.ensure_dir(path.parent)
        written = self.foundation.store.write_json_atomic(path, requirement, mode=WriteMode.CREATE_ONLY)
        if not written.ok:
            if any(issue.kind == "duplicate_file" for issue in written.issues):
                return self.foundation.fail(
                    self.foundation.issue(
                        "requirement_name_duplicate",
                        f"Requirement already exists: {safe_name}",
                        object_ref=str(path),
                    )
                )
            return self.foundation.fail(written.issues)
        return self.foundation.ok(self._view(repo_root, requirement))

    def get_requirement(self, repo_root: Path, *, name: str) -> ServiceResult[RequirementView]:
        path = self._path(repo_root, name)
        loaded = self.foundation.store.read_json(path, RepoDependencyRequirement)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(
                self.foundation.issue(
                    "requirement_not_found",
                    f"Requirement not found: {name}",
                    object_ref=str(path),
                )
            )
        return self.foundation.ok(self._view(repo_root, loaded.value))

    def list_requirements(
        self,
        repo_root: Path,
        *,
        status: RepoDependencyRequirementStatus | str | None = None,
    ) -> ServiceResult[list[RequirementView]]:
        ctx = FoundationContext(repo_root=Path(repo_root))
        root = self.foundation.layout.requirements_root(ctx)
        if not root.exists():
            return self.foundation.ok([])
        status_value = RepoDependencyRequirementStatus(status) if status is not None else None
        loaded = self.foundation.store.list_json(root, RepoDependencyRequirement)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        requirements = sorted(loaded.value, key=lambda item: item.name)
        if status_value is not None:
            requirements = [item for item in requirements if item.status == status_value]
        return self.foundation.ok([self._view(repo_root, item) for item in requirements])

    def add_requirement_interface(
        self,
        repo_root: Path,
        *,
        requirement_name: str,
        interface_name: str,
        kind: DeclKind | str,
        summary: str,
        statement_hint: str | None = None,
    ) -> ServiceResult[RequirementView]:
        requirement = self._load_open(repo_root, requirement_name)
        if not requirement.ok or requirement.value is None:
            return self.foundation.fail(requirement.issues)
        value = requirement.value
        interface = DeclInterface(
            name=interface_name.strip(),
            kind=DeclKind(kind),
            summary=summary.strip(),
            note=self._strip_or_none(statement_hint),
        )
        for existing in value.interfaces:
            if existing.name == interface.name:
                if existing.model_dump() == interface.model_dump():
                    return self.foundation.ok(self._view(repo_root, value))
                return self.foundation.fail(
                    self.foundation.issue(
                        "interface_duplicate",
                        f"Interface already exists with different content: {interface.name}",
                        object_ref=requirement_name,
                        field="interfaces",
                    )
                )
        value.interfaces.append(interface)
        return self._save(repo_root, value)

    def remove_requirement_interface(
        self,
        repo_root: Path,
        *,
        requirement_name: str,
        interface_name: str,
    ) -> ServiceResult[RequirementView]:
        requirement = self._load_open(repo_root, requirement_name)
        if not requirement.ok or requirement.value is None:
            return self.foundation.fail(requirement.issues)
        value = requirement.value
        before = len(value.interfaces)
        value.interfaces = [item for item in value.interfaces if item.name != interface_name]
        if len(value.interfaces) == before:
            return self.foundation.fail(
                self.foundation.issue(
                    "interface_not_found",
                    f"Interface not found in requirement: {interface_name}",
                    object_ref=requirement_name,
                )
            )
        return self._save(repo_root, value)

    def mark_requirement_satisfied(
        self,
        repo_root: Path,
        *,
        requirement_name: str,
        provider_repo: str,
        note: str | None = None,
    ) -> ServiceResult[RequirementView]:
        loaded = self.get_requirement(repo_root, name=requirement_name)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        requirement = loaded.value.requirement
        if requirement.status not in {
            RepoDependencyRequirementStatus.OPEN,
            RepoDependencyRequirementStatus.SATISFIED,
        }:
            return self.foundation.fail(
                self.foundation.issue(
                    "requirement_not_open",
                    "Only open or already satisfied requirements can be marked satisfied.",
                    current=requirement.status.value,
                    expected=RepoDependencyRequirementStatus.OPEN.value,
                )
            )
        requirement.status = RepoDependencyRequirementStatus.SATISFIED
        requirement.provider_repo = self.foundation.layout.ensure_safe_key(provider_repo)
        requirement.note = self._strip_or_none(note) or requirement.note
        return self._save(repo_root, requirement)

    def mark_requirement_handled(
        self,
        repo_root: Path,
        *,
        requirement_name: str,
        note: str | None = None,
    ) -> ServiceResult[RequirementView]:
        loaded = self.get_requirement(repo_root, name=requirement_name)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        requirement = loaded.value.requirement
        if requirement.status != RepoDependencyRequirementStatus.SATISFIED:
            return self.foundation.fail(
                self.foundation.issue(
                    "requirement_not_satisfied",
                    "Only satisfied requirements can be marked handled.",
                    current=requirement.status.value,
                    expected=RepoDependencyRequirementStatus.SATISFIED.value,
                )
            )
        requirement.status = RepoDependencyRequirementStatus.HANDLED
        requirement.note = self._strip_or_none(note) or requirement.note
        return self._save(repo_root, requirement)

    def mark_requirement_obsolete(
        self,
        repo_root: Path,
        *,
        requirement_name: str,
        note: str,
    ) -> ServiceResult[RequirementView]:
        if not note.strip():
            return self.foundation.fail(self.foundation.issue("missing_note", "Obsolete requirements require a note."))
        loaded = self.get_requirement(repo_root, name=requirement_name)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        requirement = loaded.value.requirement
        requirement.status = RepoDependencyRequirementStatus.OBSOLETE
        requirement.note = note.strip()
        return self._save(repo_root, requirement)

    def _load_open(self, repo_root: Path, name: str) -> ServiceResult[RepoDependencyRequirement]:
        loaded = self.get_requirement(repo_root, name=name)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        requirement = loaded.value.requirement
        if requirement.status != RepoDependencyRequirementStatus.OPEN:
            return self.foundation.fail(
                self.foundation.issue(
                    "requirement_not_open",
                    "Requirement must be open for this operation.",
                    current=requirement.status.value,
                    expected=RepoDependencyRequirementStatus.OPEN.value,
                    object_ref=requirement.name,
                )
            )
        return self.foundation.ok(requirement)

    def _save(self, repo_root: Path, requirement: RepoDependencyRequirement) -> ServiceResult[RequirementView]:
        written = self.foundation.store.write_json_atomic(self._path(repo_root, requirement.name), requirement)
        if not written.ok:
            return self.foundation.fail(written.issues)
        return self.foundation.ok(self._view(repo_root, requirement))

    def _view(self, repo_root: Path, requirement: RepoDependencyRequirement) -> RequirementView:
        return RequirementView(repo_root=str(Path(repo_root)), requirement=requirement)

    def _path(self, repo_root: Path, name: str) -> Path:
        return self.foundation.layout.requirement_path(FoundationContext(repo_root=Path(repo_root)), name)

    @staticmethod
    def _strip_or_none(value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None
