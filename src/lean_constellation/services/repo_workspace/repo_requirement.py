"""Repo dependency requirement truth operations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.common import utc_now_iso
from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import (
    RepoDependencyRequirement,
    RepoDependencyRequirementStatus,
    RequirementResumeCandidateView,
    RequirementWaitingView,
    RequirementView,
)
from lean_constellation.domain.repo import ProofAvailability, proof_availability_satisfies
from lean_constellation.services.foundation import FoundationContext, ServiceResult, WriteMode

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class RepoRequirementComponent:
    """Maintain `.lean_constellation/repo_dependency_requirements/*.json`."""

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def create_requirement(
        self,
        repo_root: Path,
        *,
        name: str,
        target_repo: str,
        required_proof_availability: ProofAvailability | str = ProofAvailability.DECLARED,
        source_description: str | None = None,
        reason: str | None = None,
    ) -> ServiceResult[RequirementView]:
        safe_name = self.runtime.foundation.layout.ensure_safe_key(name)
        safe_target = self.runtime.foundation.layout.ensure_safe_key(target_repo)
        if not (source_description and source_description.strip()) and not (reason and reason.strip()):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_missing_context",
                    "Requirement needs at least source_description or reason.",
                )
            )
        requirement = RepoDependencyRequirement(
            name=safe_name,
            target_repo=safe_target,
            required_proof_availability=ProofAvailability(required_proof_availability),
            source_description=self._strip_or_none(source_description),
            reason=self._strip_or_none(reason),
        )
        path = self._path(repo_root, safe_name)
        self.runtime.foundation.store.ensure_dir(path.parent)
        written = self.runtime.foundation.store.write_json_atomic(path, requirement, mode=WriteMode.CREATE_ONLY)
        if not written.ok:
            if any(issue.kind == "duplicate_file" for issue in written.issues):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "requirement_name_duplicate",
                        f"Requirement already exists: {safe_name}",
                        object_ref=str(path),
                    )
                )
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(self._view(repo_root, requirement))

    def get_requirement(self, repo_root: Path, *, name: str) -> ServiceResult[RequirementView]:
        path = self._path(repo_root, name)
        loaded = self.runtime.foundation.store.read_json(path, RepoDependencyRequirement)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_not_found",
                    f"Requirement not found: {name}",
                    object_ref=str(path),
                )
            )
        return self.runtime.foundation.ok(self._view(repo_root, loaded.value))

    def list_requirements(
        self,
        repo_root: Path,
        *,
        status: RepoDependencyRequirementStatus | str | None = None,
    ) -> ServiceResult[list[RequirementView]]:
        ctx = FoundationContext(repo_root=Path(repo_root))
        root = self.runtime.foundation.layout.requirements_root(ctx)
        if not root.exists():
            return self.runtime.foundation.ok([])
        status_value = RepoDependencyRequirementStatus(status) if status is not None else None
        loaded = self.runtime.foundation.store.list_json(root, RepoDependencyRequirement)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        requirements = sorted(loaded.value, key=lambda item: item.name)
        if status_value is not None:
            requirements = [item for item in requirements if item.status == status_value]
        return self.runtime.foundation.ok([self._view(repo_root, item) for item in requirements])

    def add_requirement_interface(
        self,
        repo_root: Path,
        *,
        requirement_name: str,
        interface_name: str,
        kind: DeclKind | str,
        summary: str,
        statement_hint: str | None = None,
        expected_statement_lean_code: str | None = None,
    ) -> ServiceResult[RequirementView]:
        requirement = self._load_open(repo_root, requirement_name)
        if not requirement.ok or requirement.value is None:
            return self.runtime.foundation.fail(requirement.issues)
        value = requirement.value
        interface = DeclInterface(
            name=interface_name.strip(),
            kind=DeclKind(kind),
            summary=summary.strip(),
            expected_statement_lean_code=self._strip_or_none(expected_statement_lean_code),
            note=self._strip_or_none(statement_hint),
        )
        for existing in value.interfaces:
            if existing.name == interface.name:
                if existing.model_dump() == interface.model_dump():
                    return self.runtime.foundation.ok(self._view(repo_root, value))
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
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
            return self.runtime.foundation.fail(requirement.issues)
        value = requirement.value
        before = len(value.interfaces)
        value.interfaces = [item for item in value.interfaces if item.name != interface_name]
        if len(value.interfaces) == before:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
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
            return self.runtime.foundation.fail(loaded.issues)
        requirement = loaded.value.requirement
        if requirement.status not in {
            RepoDependencyRequirementStatus.OPEN,
            RepoDependencyRequirementStatus.SATISFIED,
        }:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_not_open",
                    "Only open or already satisfied requirements can be marked satisfied.",
                    current=requirement.status.value,
                    expected=RepoDependencyRequirementStatus.OPEN.value,
                )
            )
        requirement.status = RepoDependencyRequirementStatus.SATISFIED
        requirement.provider_repo = self.runtime.foundation.layout.ensure_safe_key(provider_repo)
        requirement.note = self._strip_or_none(note) or requirement.note
        return self._save(repo_root, requirement)

    def mark_requirement_waiting_for_provider(
        self,
        repo_root: Path,
        *,
        requirement_name: str,
        provider_repo: str | None = None,
        reason: str | None = None,
    ) -> ServiceResult[RequirementWaitingView]:
        loaded = self.get_requirement(repo_root, name=requirement_name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        requirement = loaded.value.requirement
        if requirement.status == RepoDependencyRequirementStatus.OBSOLETE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_obsolete_cannot_resume",
                    "Obsolete requirements cannot enter waiting state.",
                    current=requirement.status.value,
                    expected=RepoDependencyRequirementStatus.OPEN.value,
                    object_ref=requirement.name,
                )
            )
        if requirement.status == RepoDependencyRequirementStatus.HANDLED:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_already_handled",
                    "Handled requirements cannot enter waiting state.",
                    current=requirement.status.value,
                    expected=RepoDependencyRequirementStatus.OPEN.value,
                    object_ref=requirement.name,
                )
            )
        provider = self.runtime.foundation.layout.ensure_safe_key(provider_repo or requirement.target_repo)
        waiting = self.is_requirement_waiting(requirement)
        current_provider = self.effective_provider_repo(requirement)
        if waiting and current_provider == provider and not self.is_requirement_result_observed(requirement):
            return self.runtime.foundation.ok(
                self._waiting_view(
                    repo_root,
                    requirement,
                    summary=f"Requirement {requirement.name} is already waiting for provider {provider}.",
                )
            )
        if waiting and current_provider != provider and not self.is_requirement_result_observed(requirement):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_waiting_conflict",
                    "Requirement is already waiting for a different provider repo.",
                    current=current_provider,
                    expected=provider,
                    object_ref=requirement.name,
                )
            )
        requirement.provider_repo = provider
        requirement.provider_request_submitted_at = requirement.provider_request_submitted_at or utc_now_iso()
        requirement.provider_result_observed_at = None
        requirement.note = self._strip_or_none(reason) or requirement.note
        saved = self._save(repo_root, requirement)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(
            self._waiting_view(
                repo_root,
                saved.value.requirement,
                summary=f"Requirement {requirement.name} is waiting for provider {provider}.",
            )
        )

    def list_resume_candidates_for_requirement(
        self,
        workspace_root: Path,
        *,
        provider_repo: str,
    ) -> ServiceResult[list[RequirementResumeCandidateView]]:
        provider_repo = self.runtime.foundation.layout.ensure_safe_key(provider_repo)
        workspace_root = Path(workspace_root)
        provider_root = workspace_root / provider_repo
        if not provider_root.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_repo_not_found",
                    f"Provider repo not found: {provider_repo}",
                    object_ref=str(provider_root),
                )
            )
        ready = self.runtime.repo_workspace.provider_availability.check_provider_available(provider_root)
        if not ready.ok or ready.value is None:
            return self.runtime.foundation.fail(ready.issues)
        if not ready.value.passed:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_repo_not_ready",
                    f"Provider repo is not ready: {provider_repo}",
                    object_ref=str(provider_root),
                )
            )
        candidates: list[RequirementResumeCandidateView] = []
        for repo_dir in sorted(path for path in workspace_root.iterdir() if path.is_dir()):
            ctx = FoundationContext(repo_root=repo_dir)
            if not self.runtime.foundation.layout.constellation_root(ctx).exists():
                continue
            listed = self.list_requirements(repo_dir, status=RepoDependencyRequirementStatus.SATISFIED)
            if not listed.ok or listed.value is None:
                return self.runtime.foundation.fail(listed.issues)
            for view in listed.value:
                requirement = view.requirement
                if not self.is_requirement_waiting(requirement):
                    continue
                if self.effective_provider_repo(requirement) != provider_repo:
                    continue
                valid = self.validate_requirement_provider_truth(
                    repo_dir,
                    requirement_name=requirement.name,
                    provider_repo=provider_repo,
                    require_stable=True,
                )
                if not valid.ok:
                    continue
                candidates.append(
                    RequirementResumeCandidateView(
                        consumer_repo=repo_dir.name,
                        consumer_repo_root=str(repo_dir),
                        requirement_name=requirement.name,
                        target_repo=requirement.target_repo,
                        provider_repo=provider_repo,
                        status=requirement.status,
                        result_observed=self.is_requirement_result_observed(requirement),
                        summary=f"{repo_dir.name}/{requirement.name} can resume from provider {provider_repo}.",
                    )
                )
        candidates.sort(key=lambda item: (item.consumer_repo, item.requirement_name))
        return self.runtime.foundation.ok(candidates)

    def validate_requirement_provider_truth(
        self,
        repo_root: Path,
        *,
        requirement_name: str,
        provider_repo: str | None = None,
        require_stable: bool = True,
    ) -> ServiceResult[None]:
        loaded = self.get_requirement(repo_root, name=requirement_name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        requirement = loaded.value.requirement
        provider_key = self.runtime.foundation.layout.ensure_safe_key(
            provider_repo or self.effective_provider_repo(requirement)
        )
        if self.effective_provider_repo(requirement) != provider_key:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_provider_mismatch",
                    "Requirement provider does not match the requested provider truth check.",
                    object_ref=requirement.name,
                    current=provider_key,
                    expected=self.effective_provider_repo(requirement),
                )
            )
        provider_root = Path(repo_root).parent / provider_key
        if not provider_root.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_repo_not_found",
                    f"Provider repo not found: {provider_key}",
                    object_ref=str(provider_root),
                )
            )
        if require_stable:
            availability = self.runtime.repo_workspace.provider_availability.check_provider_available(provider_root)
            if not availability.ok or availability.value is None:
                return self.runtime.foundation.fail(availability.issues)
        else:
            availability = None
        if availability is not None and not availability.value.passed:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_repo_not_ready",
                    f"Provider repo is not ready: {provider_key}",
                    object_ref=str(provider_root),
                    details={"issues": "; ".join(issue.kind for issue in availability.value.issues)},
                )
            )
        provider_config = self.runtime.repo_workspace.metadata.get_repo_config(provider_root)
        if not provider_config.ok or provider_config.value is None:
            return self.runtime.foundation.fail(provider_config.issues)
        target = provider_config.value.config.target_proof_availability
        if not proof_availability_satisfies(target, requirement.required_proof_availability):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_proof_availability_insufficient",
                    "Provider repo proof availability does not satisfy the consumer requirement.",
                    object_ref=requirement.name,
                    current=target.value,
                    expected=requirement.required_proof_availability.value,
                )
            )
        if not requirement.interfaces:
            return self.runtime.foundation.ok(None)
        if require_stable:
            public_refs = self.runtime.decl_graph.ref_compatibility.list_public_decl_refs(
                provider_root,
                required_availability=target,
            )
            if not public_refs.ok or public_refs.value is None:
                return self.runtime.foundation.fail(
                    [
                        self.runtime.foundation.issue(
                            "provider_interface_missing",
                            "Provider repo public interface is missing requested requirement interfaces.",
                            object_ref=f"{provider_key}:Main",
                            details={"issues": "; ".join(issue.kind for issue in public_refs.issues)},
                        )
                    ]
                )
            public_entries = [
                (item.anchor, item.resolved_revision, item.compatible, item.reason)
                for item in public_refs.value
            ]
        else:
            exports = self.runtime.node.export.list_scope_exports(provider_root, scope_path="Main")
            if not exports.ok or exports.value is None:
                return self.runtime.foundation.fail(
                    [
                        self.runtime.foundation.issue(
                            "provider_interface_missing",
                            "Provider repo public interface is missing requested requirement interfaces.",
                            object_ref=f"{provider_key}:Main",
                            details={"issues": "; ".join(issue.kind for issue in exports.issues)},
                        )
                    ]
                )
            public_entries = [
                (
                    item.ref,
                    item.resolved_revision or item.ref.revision,
                    item.valid,
                    None if item.valid else "; ".join(issue.kind for issue in item.issues),
                )
                for item in exports.value
            ]
        issues = []
        for interface in requirement.interfaces:
            matches = [item for item in public_entries if item[0].name == interface.name]
            if not matches:
                issues.append(
                    self.runtime.foundation.issue(
                        "provider_interface_missing",
                        "Provider repo public interface is missing a requested requirement interface.",
                        object_ref=f"{provider_key}:Main:{interface.name}",
                        field=interface.name,
                    )
                )
                continue
            valid_match = next(
                (item for item in matches if item[2] and item[1] is not None),
                None,
            )
            if valid_match is None:
                issues.append(
                    self.runtime.foundation.issue(
                        "provider_interface_invalid",
                        "Provider repo public interface exists but is not currently valid.",
                        object_ref=f"{provider_key}:Main:{interface.name}",
                        field=interface.name,
                        details={"issues": "; ".join(match[3] or "incompatible" for match in matches)},
                    )
                )
                continue
            decl = self.runtime.decl_graph.get_decl(
                provider_root,
                node_path=valid_match[0].node,
                name=valid_match[0].name,
            )
            if not decl.ok or decl.value is None:
                return self.runtime.foundation.fail(decl.issues)
            if decl.value.kind != interface.kind.value:
                issues.append(
                    self.runtime.foundation.issue(
                        "provider_interface_kind_mismatch",
                        "Provider repo public interface has a different declaration kind than requested.",
                        object_ref=f"{provider_key}:{valid_match[0].node}:{valid_match[0].name}",
                        field=interface.name,
                        current=decl.value.kind,
                        expected=interface.kind.value,
                    )
                )
                continue
            statement_contract = self._validate_provider_interface_statement_contract(
                provider_root,
                provider_key=provider_key,
                interface=interface,
                node_path=valid_match[0].node,
                decl_name=valid_match[0].name,
                revision=valid_match[1],
            )
            if not statement_contract.ok:
                issues.extend(statement_contract.issues)
                continue
            satisfied = self.runtime.decl_graph.check_decl_proof_policy_satisfied(
                provider_root,
                node_path=valid_match[0].node,
                decl_name=valid_match[0].name,
                target_proof_availability=target,
            )
            if not satisfied.ok or satisfied.value is None:
                return self.runtime.foundation.fail(satisfied.issues)
            if not satisfied.value.proof_policy_satisfied:
                issues.append(
                    self.runtime.foundation.issue(
                        "provider_interface_proof_policy_unsatisfied",
                        "Provider public interface does not satisfy the provider proof availability policy.",
                        object_ref=f"{provider_key}:{valid_match[0].node}:{valid_match[0].name}",
                        field=interface.name,
                        details={
                            "reason": satisfied.value.reason.value if satisfied.value.reason is not None else "unknown",
                            **satisfied.value.details,
                        },
                    )
                )
        if issues:
            return self.runtime.foundation.fail(issues)
        return self.runtime.foundation.ok(None)

    def _validate_provider_interface_statement_contract(
        self,
        provider_root: Path,
        *,
        provider_key: str,
        interface: DeclInterface,
        node_path: str,
        decl_name: str,
        revision: int,
    ) -> ServiceResult[None]:
        expected = interface.expected_statement_lean_code
        if expected is None:
            return self.runtime.foundation.ok(None)
        if interface.kind not in {DeclKind.THEOREM, DeclKind.LEMMA}:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_interface_statement_contract_kind_unsupported",
                    "Exact provider statement contracts currently support theorem-like interfaces.",
                    object_ref=f"{provider_key}:{node_path}:{decl_name}@{revision}",
                    current=interface.kind.value,
                    expected="theorem | lemma",
                )
            )
        loaded = self.runtime.decl_graph.get_decl_revision(
            provider_root,
            node_path=node_path,
            name=decl_name,
            revision=revision,
        )
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_interface_statement_contract_revision_missing",
                    "The provider declaration revision required by the exact statement contract is unavailable.",
                    object_ref=f"{provider_key}:{node_path}:{decl_name}@{revision}",
                )
            )
        statement_code = loaded.value.statement_lean_code
        if statement_code is None or not statement_code.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_interface_statement_contract_actual_missing",
                    "The provider declaration has no captured formal statement to compare.",
                    object_ref=f"{provider_key}:{node_path}:{decl_name}@{revision}",
                )
            )
        actual_codes = [("statement", statement_code)]
        if loaded.value.proof_lean_code is not None and loaded.value.proof_lean_code.strip():
            actual_codes.append(("proof", loaded.value.proof_lean_code))
        for stage, actual in actual_codes:
            compared = self.runtime.lean_projection.annotation.compare_theorem_header(
                expected,
                actual,
                decl_name=decl_name,
            )
            comparison_issues = compared.issues if not compared.ok or compared.value is None else compared.value.issues
            if comparison_issues:
                return self.runtime.foundation.fail(
                    [
                        issue.model_copy(
                            update={
                                "kind": "provider_interface_statement_contract_mismatch",
                                "object_ref": f"{provider_key}:{node_path}:{decl_name}@{revision}:{stage}",
                            }
                        )
                        for issue in comparison_issues
                    ]
                )
        return self.runtime.foundation.ok(None)

    def mark_requirement_result_observed(
        self,
        repo_root: Path,
        *,
        requirement_name: str,
        note: str | None = None,
    ) -> ServiceResult[RequirementWaitingView]:
        loaded = self.get_requirement(repo_root, name=requirement_name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        requirement = loaded.value.requirement
        if requirement.status == RepoDependencyRequirementStatus.OBSOLETE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_obsolete_cannot_resume",
                    "Obsolete requirements cannot be resumed.",
                    object_ref=requirement.name,
                )
            )
        if self.is_requirement_result_observed(requirement):
            valid = self.validate_requirement_provider_truth(
                repo_root,
                requirement_name=requirement.name,
                provider_repo=self.effective_provider_repo(requirement),
                require_stable=True,
            )
            if not valid.ok:
                return self.runtime.foundation.fail(valid.issues)
            return self.runtime.foundation.ok(
                self._waiting_view(
                    repo_root,
                    requirement,
                    summary=f"Requirement {requirement.name} result was already observed.",
                )
            )
        if not self.is_requirement_waiting(requirement):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_not_waiting",
                    "Requirement is not waiting for a provider result.",
                    object_ref=requirement.name,
                )
            )
        if requirement.status not in {
            RepoDependencyRequirementStatus.SATISFIED,
            RepoDependencyRequirementStatus.HANDLED,
        }:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_not_resumable",
                    "Requirement result can only be observed after it is satisfied or handled.",
                    current=requirement.status.value,
                    expected=f"{RepoDependencyRequirementStatus.SATISFIED.value}|{RepoDependencyRequirementStatus.HANDLED.value}",
                    object_ref=requirement.name,
                )
            )
        valid = self.validate_requirement_provider_truth(
            repo_root,
            requirement_name=requirement.name,
            provider_repo=self.effective_provider_repo(requirement),
            require_stable=True,
        )
        if not valid.ok:
            return self.runtime.foundation.fail(valid.issues)
        requirement.provider_repo = self.effective_provider_repo(requirement)
        requirement.provider_result_observed_at = utc_now_iso()
        requirement.note = self._strip_or_none(note) or requirement.note
        saved = self._save(repo_root, requirement)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(
            self._waiting_view(
                repo_root,
                saved.value.requirement,
                summary=f"Requirement {requirement.name} provider result observed.",
            )
        )

    def mark_requirement_handled(
        self,
        repo_root: Path,
        *,
        requirement_name: str,
        note: str | None = None,
    ) -> ServiceResult[RequirementView]:
        loaded = self.get_requirement(repo_root, name=requirement_name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        requirement = loaded.value.requirement
        if requirement.status != RepoDependencyRequirementStatus.SATISFIED:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
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
            return self.runtime.foundation.fail(self.runtime.foundation.issue("missing_note", "Obsolete requirements require a note."))
        loaded = self.get_requirement(repo_root, name=requirement_name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        requirement = loaded.value.requirement
        requirement.status = RepoDependencyRequirementStatus.OBSOLETE
        requirement.note = note.strip()
        return self._save(repo_root, requirement)

    def _load_open(self, repo_root: Path, name: str) -> ServiceResult[RepoDependencyRequirement]:
        loaded = self.get_requirement(repo_root, name=name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        requirement = loaded.value.requirement
        if requirement.status != RepoDependencyRequirementStatus.OPEN:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_not_open",
                    "Requirement must be open for this operation.",
                    current=requirement.status.value,
                    expected=RepoDependencyRequirementStatus.OPEN.value,
                    object_ref=requirement.name,
                )
            )
        return self.runtime.foundation.ok(requirement)

    def _save(self, repo_root: Path, requirement: RepoDependencyRequirement) -> ServiceResult[RequirementView]:
        written = self.runtime.foundation.store.write_json_atomic(self._path(repo_root, requirement.name), requirement)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(self._view(repo_root, requirement))

    def _view(self, repo_root: Path, requirement: RepoDependencyRequirement) -> RequirementView:
        return RequirementView(repo_root=str(Path(repo_root)), requirement=requirement)

    def effective_provider_repo(self, requirement: RepoDependencyRequirement) -> str:
        return requirement.provider_repo or requirement.target_repo

    def is_requirement_waiting(self, requirement: RepoDependencyRequirement) -> bool:
        return (
            requirement.provider_request_submitted_at is not None
            and requirement.provider_result_observed_at is None
            and requirement.status
            not in {
                RepoDependencyRequirementStatus.HANDLED,
                RepoDependencyRequirementStatus.OBSOLETE,
            }
        )

    def is_requirement_result_observed(self, requirement: RepoDependencyRequirement) -> bool:
        return requirement.provider_result_observed_at is not None

    def _waiting_view(
        self,
        repo_root: Path,
        requirement: RepoDependencyRequirement,
        *,
        summary: str,
    ) -> RequirementWaitingView:
        provider = self.effective_provider_repo(requirement)
        return RequirementWaitingView(
            repo_root=str(Path(repo_root)),
            requirement_name=requirement.name,
            target_repo=requirement.target_repo,
            provider_repo=provider,
            status=requirement.status,
            waiting=self.is_requirement_waiting(requirement),
            result_observed=self.is_requirement_result_observed(requirement),
            summary=summary,
        )

    def _path(self, repo_root: Path, name: str) -> Path:
        return self.runtime.foundation.layout.requirement_path(FoundationContext(repo_root=Path(repo_root)), name)

    @staticmethod
    def _strip_or_none(value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None
