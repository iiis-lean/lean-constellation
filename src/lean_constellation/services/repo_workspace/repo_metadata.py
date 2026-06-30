"""Repo metadata, format, and policy operations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.preparation import RepoDependencyRequirement, RepoDependencyRequirementStatus
from lean_constellation.domain.repo import (
    ProviderReadyState,
    RepoFormat,
    RepoFormatState,
    RepoFormatView,
    RepoModel,
    RepoModelView,
    RepoPolicy,
    RepoPolicyView,
    RepoStateView,
)
from lean_constellation.services.foundation import (
    FoundationContext,
    IssueSeverity,
    MutationSummaryView,
    ServiceResult,
    WriteMode,
)

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class RepoMetadataComponent:
    """Maintain repo-local manifest, format, policy, and provider ready marker."""

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def ensure_repo_model(self, repo_root: Path, *, main_node: str = "Main") -> ServiceResult[RepoModelView]:
        ctx = self._ctx(repo_root)
        path = self.runtime.foundation.layout.repo_metadata_path(ctx)
        self.runtime.foundation.store.ensure_dir(path.parent)
        if path.exists():
            existing = self.runtime.foundation.store.read_json(path, RepoModel)
            if not existing.ok or existing.value is None:
                return self.runtime.foundation.fail(existing.issues)
            if existing.value.main_node != main_node:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "repo_model_conflict",
                        "Existing repo model uses a different main node.",
                        field="main_node",
                        current=existing.value.main_node,
                        expected=main_node,
                        object_ref=str(path),
                    )
                )
            return self.runtime.foundation.ok(
                RepoModelView(
                    repo_root=str(Path(repo_root)),
                    main_node=main_node,
                    summary=existing.value.summary,
                    created=False,
                )
            )

        model = RepoModel(main_node=main_node)
        written = self.runtime.foundation.store.write_json_atomic(path, model, mode=WriteMode.CREATE_ONLY)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(RepoModelView(repo_root=str(Path(repo_root)), main_node=main_node, created=True))

    def get_repo_model(self, repo_root: Path) -> ServiceResult[RepoModelView]:
        path = self.runtime.foundation.layout.repo_metadata_path(self._ctx(repo_root))
        loaded = self.runtime.foundation.store.read_json(path, RepoModel)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "repo_model_missing",
                    "Repo model is missing or invalid.",
                    object_ref=str(path),
                    suggested_action="Call ensure_repo_model before using this repo.",
                )
            )
        return self.runtime.foundation.ok(
            RepoModelView(
                repo_root=str(Path(repo_root)),
                main_node=loaded.value.main_node,
                summary=loaded.value.summary,
            )
        )

    def set_repo_summary(self, repo_root: Path, *, summary: str) -> ServiceResult[RepoModelView]:
        summary = summary.strip()
        if not summary:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("missing_summary", "Repo summary is required."))
        current = self.get_repo_model(repo_root)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        model = RepoModel(main_node=current.value.main_node, summary=summary)
        written = self.runtime.foundation.store.write_json_atomic(
            self.runtime.foundation.layout.repo_metadata_path(self._ctx(repo_root)),
            model,
        )
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(
            RepoModelView(repo_root=str(Path(repo_root)), main_node=model.main_node, summary=model.summary)
        )

    def get_repo_format(self, repo_root: Path) -> ServiceResult[RepoFormatView]:
        path = self._repo_format_path(repo_root)
        if not path.exists():
            return self.runtime.foundation.ok(
                RepoFormatView(repo_root=str(Path(repo_root)), repo_format=RepoFormat.UNKNOWN, reason=None)
            )
        loaded = self.runtime.foundation.store.read_json(path, RepoFormatState)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        return self.runtime.foundation.ok(
            RepoFormatView(
                repo_root=str(Path(repo_root)),
                repo_format=loaded.value.repo_format,
                reason=loaded.value.reason,
            )
        )

    def set_repo_format(
        self,
        repo_root: Path,
        *,
        repo_format: RepoFormat | str,
        reason: str,
        allow_overwrite: bool = False,
    ) -> ServiceResult[MutationSummaryView]:
        try:
            repo_format = RepoFormat(repo_format)
        except ValueError:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "invalid_repo_format",
                    "repo_format must be native or adapter.",
                    current=str(repo_format),
                    expected="native|adapter",
                )
            )
        if repo_format == RepoFormat.UNKNOWN:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("invalid_repo_format", "repo_format must be native or adapter.")
            )
        reason = reason.strip()
        if not reason:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("missing_reason", "set_repo_format requires a reason."))

        path = self._repo_format_path(repo_root)
        self.runtime.foundation.store.ensure_dir(path.parent)
        current = self.get_repo_format(repo_root)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        if current.value.repo_format == repo_format:
            return self.runtime.foundation.ok(
                self.runtime.foundation.mutation_view(
                    object_ref=str(path),
                    changed=False,
                    summary=f"Repo format already set to {repo_format.value}.",
                )
            )
        provider_ready = self.get_provider_ready(repo_root)
        if not provider_ready.ok:
            return self.runtime.foundation.fail(provider_ready.issues)
        if provider_ready.value is not None and provider_ready.value.ready:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "repo_format_locked",
                    "Repo format cannot be changed after the provider repo is marked ready.",
                    current=current.value.repo_format.value,
                    expected=repo_format.value,
                    object_ref=str(path),
                )
            )
        if current.value.repo_format != RepoFormat.UNKNOWN and not allow_overwrite:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "repo_format_conflict",
                    "Repo format is already set and cannot be changed without explicit overwrite.",
                    current=current.value.repo_format.value,
                    expected=repo_format.value,
                    object_ref=str(path),
                )
            )
        written = self.runtime.foundation.store.write_json_atomic(
            path,
            RepoFormatState(repo_format=repo_format, reason=reason),
            mode=WriteMode.OVERWRITE,
        )
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(
            self.runtime.foundation.mutation_view(
                object_ref=str(path),
                changed=True,
                summary=f"Set repo format to {repo_format.value}.",
                changed_items=["repo_format"],
            )
        )

    def get_repo_policy(self, repo_root: Path) -> ServiceResult[RepoPolicyView]:
        path = self._repo_policy_path(repo_root)
        if not path.exists():
            return self.runtime.foundation.ok(RepoPolicyView(repo_root=str(Path(repo_root)), policy=RepoPolicy()))
        loaded = self.runtime.foundation.store.read_json(path, RepoPolicy)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        return self.runtime.foundation.ok(RepoPolicyView(repo_root=str(Path(repo_root)), policy=loaded.value))

    def update_repo_policy(
        self,
        repo_root: Path,
        *,
        max_parallel_content_node_tasks: int | None = None,
        readiness_policy: str | None = None,
    ) -> ServiceResult[RepoPolicyView]:
        current = self.get_repo_policy(repo_root)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        policy = current.value.policy.model_copy(deep=True)
        if max_parallel_content_node_tasks is not None:
            policy.max_parallel_content_node_tasks = max_parallel_content_node_tasks
        if readiness_policy is not None:
            if not readiness_policy.strip():
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("policy_invalid", "readiness_policy must be non-empty.")
                )
            policy.readiness_policy = readiness_policy.strip()
        try:
            policy = RepoPolicy.model_validate(policy.model_dump())
        except Exception as exc:  # noqa: BLE001 - normalized as ServiceResult.
            return self.runtime.foundation.fail(self.runtime.foundation.issue("policy_invalid", f"Invalid repo policy: {exc}"))
        written = self.runtime.foundation.store.write_json_atomic(self._repo_policy_path(repo_root), policy)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(RepoPolicyView(repo_root=str(Path(repo_root)), policy=policy))

    def get_repo_state_view(self, repo_root: Path) -> ServiceResult[RepoStateView]:
        model = self.get_repo_model(repo_root)
        repo_format = self.get_repo_format(repo_root)
        policy = self.get_repo_policy(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        if not policy.ok or policy.value is None:
            return self.runtime.foundation.fail(policy.issues)
        ctx = self._ctx(repo_root)
        open_requirement_count, requirement_warnings = self._count_open_requirements(ctx)
        main_node = model.value.main_node if model.ok and model.value is not None else None
        repo_summary = model.value.summary if model.ok and model.value is not None else None
        provider_ready = self.get_provider_ready(repo_root)
        ready_flag = bool(provider_ready.ok and provider_ready.value and provider_ready.value.ready)
        issues = [] if model.ok else model.issues
        issues.extend(requirement_warnings)
        summary = "Repo state view built."
        if issues:
            summary = "Repo state view built with warnings."
        return self.runtime.foundation.ok(
            RepoStateView(
                repo_root=str(Path(repo_root)),
                main_node=main_node,
                repo_summary=repo_summary,
                repo_format=repo_format.value.repo_format,
                provider_ready=ready_flag,
                readiness_policy=policy.value.policy.readiness_policy,
                max_parallel_content_node_tasks=policy.value.policy.max_parallel_content_node_tasks,
                preparation_input_exists=self.runtime.foundation.layout.preparation_input_path(ctx).exists(),
                open_requirement_count=open_requirement_count,
                summary=summary,
            ),
            warnings=[
                issue.model_copy(update={"severity": IssueSeverity.WARNING})
                for issue in issues
                if issue.kind == "repo_model_missing"
            ],
        )

    def get_provider_ready(self, repo_root: Path) -> ServiceResult[ProviderReadyState]:
        path = self._provider_ready_path(repo_root)
        if not path.exists():
            return self.runtime.foundation.ok(ProviderReadyState())
        return self.runtime.foundation.store.read_json(path, ProviderReadyState)

    def set_provider_ready(self, repo_root: Path, *, summary: str) -> ServiceResult[ProviderReadyState]:
        summary = summary.strip()
        if not summary:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("missing_summary", "Repo summary is required."))
        updated_summary = self.set_repo_summary(repo_root, summary=summary)
        if not updated_summary.ok:
            return self.runtime.foundation.fail(updated_summary.issues)
        state = ProviderReadyState(ready=True)
        written = self.runtime.foundation.store.write_json_atomic(self._provider_ready_path(repo_root), state)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(state)

    def _ctx(self, repo_root: Path) -> FoundationContext:
        return FoundationContext(repo_root=Path(repo_root))

    def _repo_format_path(self, repo_root: Path) -> Path:
        return self.runtime.foundation.layout.constellation_root(self._ctx(repo_root)) / "repo_format.json"

    def _repo_policy_path(self, repo_root: Path) -> Path:
        return self.runtime.foundation.layout.constellation_root(self._ctx(repo_root)) / "repo_policy.json"

    def _provider_ready_path(self, repo_root: Path) -> Path:
        return self.runtime.foundation.layout.constellation_root(self._ctx(repo_root)) / "provider_ready.json"

    def _count_open_requirements(self, ctx: FoundationContext) -> tuple[int, list]:
        req_root = self.runtime.foundation.layout.requirements_root(ctx)
        if not req_root.exists():
            return 0, []
        loaded = self.runtime.foundation.store.list_json(req_root, RepoDependencyRequirement)
        if not loaded.ok or loaded.value is None:
            return 0, [issue.model_copy(update={"severity": IssueSeverity.WARNING}) for issue in loaded.issues]
        count = sum(
            1
            for requirement in loaded.value
            if requirement.status == RepoDependencyRequirementStatus.OPEN
        )
        return count, []
