"""Workspace-level derived catalog views."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.preparation import (
    RepoDependencyRequirementStatus,
    RequirementGroupItem,
    RequirementGroupView,
)
from lean_constellation.domain.repo import WorkspaceCatalogView, WorkspaceCoordinatorView, WorkspaceRepoSummary
from lean_constellation.services.foundation import FoundationContext, FoundationService, ServiceResult
from lean_constellation.services.repo_workspace.lake_dependency import LakeDependencyComponent, LakeDependencyEntry
from lean_constellation.services.repo_workspace.repo_metadata import RepoMetadataComponent
from lean_constellation.services.repo_workspace.repo_requirement import RepoRequirementComponent


class RequirementGroupSummaryView(StrictModel):
    target_repo: str
    requirement_count: int
    consumer_repos: list[str] = Field(default_factory=list)
    interface_names: list[str] = Field(default_factory=list)
    source_description_summary: str | None = None


class WorkspaceCatalogComponent:
    """Build read-only workspace views from repo truth files."""

    def __init__(
        self,
        foundation: FoundationService,
        metadata: RepoMetadataComponent,
        requirement: RepoRequirementComponent,
        lake_dependency: LakeDependencyComponent,
    ) -> None:
        self.foundation = foundation
        self.metadata = metadata
        self.requirement = requirement
        self.lake_dependency = lake_dependency

    def list_workspace_repos(self, workspace_root: Path) -> ServiceResult[list[WorkspaceRepoSummary]]:
        workspace_root = Path(workspace_root)
        if not workspace_root.exists() or not workspace_root.is_dir():
            return self.foundation.fail(
                self.foundation.issue("workspace_not_found", f"Workspace root not found: {workspace_root}")
            )
        repos: list[WorkspaceRepoSummary] = []
        for repo_dir in sorted(path for path in workspace_root.iterdir() if path.is_dir()):
            ctx = FoundationContext(repo_root=repo_dir)
            if not self.foundation.layout.constellation_root(ctx).exists():
                continue
            state = self.metadata.get_repo_state_view(repo_dir)
            if not state.ok or state.value is None:
                return self.foundation.fail(state.issues)
            repos.append(
                WorkspaceRepoSummary(
                    repo_key=repo_dir.name,
                    repo_root=str(repo_dir),
                    repo_format=state.value.repo_format,
                    provider_ready=state.value.provider_ready,
                    open_requirement_count=state.value.open_requirement_count,
                )
            )
        return self.foundation.ok(repos)

    def get_workspace_catalog(
        self,
        workspace_root: Path,
        *,
        current_repo: str | None = None,
    ) -> ServiceResult[WorkspaceCatalogView]:
        repos = self.list_workspace_repos(workspace_root)
        if not repos.ok or repos.value is None:
            return self.foundation.fail(repos.issues)
        values = repos.value
        if current_repo is not None:
            current_key = Path(current_repo).name
            values = sorted(values, key=lambda item: (item.repo_key != current_key, item.repo_key))
        return self.foundation.ok(WorkspaceCatalogView(workspace_root=str(Path(workspace_root)), repos=values))

    def list_ready_provider_repos(
        self,
        workspace_root: Path,
        *,
        current_repo: str | None = None,
    ) -> ServiceResult[list[WorkspaceRepoSummary]]:
        catalog = self.get_workspace_catalog(workspace_root, current_repo=current_repo)
        if not catalog.ok or catalog.value is None:
            return self.foundation.fail(catalog.issues)
        current_key = Path(current_repo).name if current_repo else None
        return self.foundation.ok(
            [
                repo
                for repo in catalog.value.repos
                if repo.provider_ready and (current_key is None or repo.repo_key != current_key)
            ]
        )

    def list_open_requirement_groups(self, workspace_root: Path) -> ServiceResult[list[RequirementGroupSummaryView]]:
        groups: dict[str, list[RequirementGroupItem]] = defaultdict(list)
        repos = self.list_workspace_repos(workspace_root)
        if not repos.ok or repos.value is None:
            return self.foundation.fail(repos.issues)
        for repo in repos.value:
            listed = self.requirement.list_requirements(
                Path(repo.repo_root),
                status=RepoDependencyRequirementStatus.OPEN,
            )
            if not listed.ok or listed.value is None:
                return self.foundation.fail(listed.issues)
            for view in listed.value:
                groups[view.requirement.target_repo].append(
                    RequirementGroupItem(
                        consumer_repo=repo.repo_key,
                        consumer_repo_root=repo.repo_root,
                        requirement=view.requirement,
                    )
                )
        summaries = []
        for target, items in sorted(groups.items()):
            consumer_repos = sorted({item.consumer_repo for item in items})
            interfaces = sorted({interface.name for item in items for interface in item.requirement.interfaces})
            descriptions = [item.requirement.source_description for item in items if item.requirement.source_description]
            summaries.append(
                RequirementGroupSummaryView(
                    target_repo=target,
                    requirement_count=len(items),
                    consumer_repos=consumer_repos,
                    interface_names=interfaces,
                    source_description_summary="\n".join(descriptions) if descriptions else None,
                )
            )
        return self.foundation.ok(summaries)

    def get_requirement_group(self, workspace_root: Path, *, target_repo: str) -> ServiceResult[RequirementGroupView]:
        target_repo = self.foundation.layout.ensure_safe_key(target_repo)
        items: list[RequirementGroupItem] = []
        repos = self.list_workspace_repos(workspace_root)
        if not repos.ok or repos.value is None:
            return self.foundation.fail(repos.issues)
        for repo in repos.value:
            listed = self.requirement.list_requirements(
                Path(repo.repo_root),
                status=RepoDependencyRequirementStatus.OPEN,
            )
            if not listed.ok or listed.value is None:
                return self.foundation.fail(listed.issues)
            for view in listed.value:
                if view.requirement.target_repo == target_repo:
                    items.append(
                        RequirementGroupItem(
                            consumer_repo=repo.repo_key,
                            consumer_repo_root=repo.repo_root,
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

    def list_current_lake_dependency_repos(self, repo_root: Path) -> ServiceResult[list[LakeDependencyEntry]]:
        deps = self.lake_dependency.parse_lake_dependencies(repo_root)
        if not deps.ok or deps.value is None:
            return self.foundation.fail(deps.issues)
        return self.foundation.ok(deps.value.dependencies)

    def inspect_workspace_for_coordinator(self, current_repo_root: Path) -> ServiceResult[WorkspaceCoordinatorView]:
        current_repo_root = Path(current_repo_root)
        catalog = self.get_workspace_catalog(current_repo_root.parent, current_repo=current_repo_root.name)
        if not catalog.ok or catalog.value is None:
            return self.foundation.fail(catalog.issues)
        ready = self.list_ready_provider_repos(current_repo_root.parent, current_repo=current_repo_root.name)
        if not ready.ok or ready.value is None:
            return self.foundation.fail(ready.issues)
        return self.foundation.ok(
            WorkspaceCoordinatorView(
                current_repo_root=str(current_repo_root),
                catalog=catalog.value,
                ready_provider_repos=ready.value,
            )
        )
