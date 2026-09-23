"""Deterministic plan validation and Content frontier computation."""

from __future__ import annotations

from dataclasses import dataclass

from lean_constellation.domain.restructure import (
    CompletionTarget,
    FrontierItem,
    NodeKind,
    RepoPlan,
    WorkspacePlan,
)


@dataclass(frozen=True)
class PlanValidation:
    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class RestructurePlanningService:
    """Keep scheduling decisions deterministic and independent of Agent text."""

    def validate_workspace(self, plan: WorkspacePlan) -> PlanValidation:
        errors: list[str] = []
        warnings: list[str] = []
        directories = [repo.directory for repo in plan.repos.values()]
        if any(directory.startswith(".") for directory in directories):
            errors.append("repo directory may not be a metadata directory")
        for key, repo in plan.repos.items():
            if repo.plan is None:
                warnings.append(f"{key}: repo plan has not been submitted")
            else:
                result = self.validate_repo(repo.plan, workspace_plan=plan)
                errors.extend(f"{key}: {issue}" for issue in result.errors)
                warnings.extend(f"{key}: {warning}" for warning in result.warnings)
        return PlanValidation(not errors, tuple(errors), tuple(warnings))

    def validate_repo(self, repo_plan: RepoPlan, *, plan_workspace: WorkspacePlan | None = None, workspace_plan: WorkspacePlan | None = None) -> PlanValidation:
        # ``plan_workspace`` is retained as a keyword-compatible alias for callers
        # that use the more explicit name; the shorter ``plan`` is convenient in
        # the service facade.
        plan = repo_plan
        workspace = plan_workspace or workspace_plan
        errors: list[str] = []
        warnings: list[str] = []
        main = plan.nodes.get("Main")
        if main is None or main.kind is not NodeKind.MAIN:
            errors.append("Main node must be the unique root")
        leaves = plan.content_nodes()
        if not leaves:
            errors.append("repo plan must contain at least one Content leaf")
        for node in plan.nodes.values():
            children = [candidate for candidate in plan.nodes.values() if candidate.parent == node.path]
            if node.kind is NodeKind.CONTENT and children:
                errors.append(f"Content node has children: {node.path}")
            if node.kind is NodeKind.MAIN and node.parent is not None:
                errors.append("Main cannot have a parent")
            if len(children) > 1 and sum(1 for child in children if child.kind is NodeKind.CONTENT) > 32:
                warnings.append(f"{node.path}: unusually large Content fanout")
        for node_path, refs in plan.material_assignments.items():
            if node_path not in plan.nodes:
                errors.append(f"material assignment references unknown node {node_path}")
            if not refs:
                warnings.append(f"{node_path}: empty material assignment")
        if workspace is not None and plan.repo_key not in workspace.repos:
            errors.append("repo plan key is absent from workspace plan")
        if workspace is not None and workspace.repos.get(plan.repo_key) is not None:
            spec = workspace.repos[plan.repo_key]
            if spec.directory != plan.directory or spec.module_root != plan.module_root:
                errors.append("repo plan physical layout differs from workspace plan")
        return PlanValidation(not errors, tuple(errors), tuple(warnings))

    def declared_frontier(
        self,
        workspace: WorkspacePlan,
        repo_key: str,
        *,
        completed_nodes: set[str] | None = None,
        stage: str = "declared",
    ) -> list[FrontierItem]:
        repo = workspace.repos[repo_key]
        if repo.plan is None:
            return []
        completed = completed_nodes or set()
        candidates = repo.plan.declared_frontier(completed)
        return [
            FrontierItem(
                repo_key=repo_key,
                directory=repo.directory,
                node_path=node_path,
                stage=stage,  # pydantic coerces the literal string to RestructureStage
                plan_version=repo.plan.version,
            )
            for node_path in candidates
        ]

    def proved_frontier(
        self,
        workspace: WorkspacePlan,
        repo_key: str,
        *,
        declared_nodes: set[str],
        proved_nodes: set[str] | None = None,
    ) -> list[FrontierItem]:
        if not declared_nodes.issuperset(self._repo_content_paths(workspace, repo_key)):
            return []
        repo = workspace.repos[repo_key]
        return [FrontierItem(repo_key=repo_key, directory=repo.directory,
                             node_path=node.path, stage="proved", plan_version=repo.plan.version)
                for node in repo.plan.content_nodes() if node.path not in (proved_nodes or set())]

    def workspace_frontier(
        self,
        workspace: WorkspacePlan,
        *,
        completed: dict[str, set[str]] | None = None,
        declared_completed: dict[str, set[str]] | None = None,
        stage: str = "declared",
    ) -> list[FrontierItem]:
        """Return runnable Content leaves across the workspace DAG.

        ``repo_dependencies`` maps a consumer repo to its provider repos.  A
        provider is considered ready for a stage only after every Content leaf
        in that provider has reached the same stage.  Independent repos remain
        eligible in the same call.
        """

        completed = completed or {}
        declared_completed = declared_completed if declared_completed is not None else completed
        if stage == "proved" and not all(
            self._repo_declared(workspace, repo_key, declared_completed)
            for repo_key in workspace.repos
        ):
            return []
        ready: list[FrontierItem] = []
        for repo_key in sorted(workspace.repos):
            repo = workspace.repos[repo_key]
            if repo.plan is None:
                continue
            # Declared interfaces are published bottom-up through the repo DAG.
            # Once the workspace declared barrier is reached, proof work is
            # intentionally released for every repo at once; provider proofs
            # are pinned only by the final build phase.
            if stage != "proved":
                provider_ready = True
                for provider_key in workspace.repo_dependencies.get(repo_key, []):
                    provider_plan = workspace.repos[provider_key].plan
                    if provider_plan is None:
                        provider_ready = False
                        break
                    provider_paths = {node.path for node in provider_plan.content_nodes()}
                    if not provider_paths.issubset(completed.get(provider_key, set())):
                        provider_ready = False
                        break
                if not provider_ready:
                    continue
            items = self.declared_frontier(
                workspace,
                repo_key,
                completed_nodes=completed.get(repo_key, set()),
                stage=stage,
            )
            if stage == "proved":
                if not self._repo_declared(workspace, repo_key, declared_completed):
                    continue
                items = self.proved_frontier(
                    workspace,
                    repo_key,
                    declared_nodes=declared_completed.get(repo_key, set()),
                    proved_nodes=completed.get(repo_key, set()),
                )
            ready.extend(items)
        return ready

    @staticmethod
    def _repo_declared(
        workspace: WorkspacePlan,
        repo_key: str,
        completed: dict[str, set[str]],
    ) -> bool:
        plan = workspace.repos[repo_key].plan
        if plan is None:
            return False
        return {node.path for node in plan.content_nodes()}.issubset(completed.get(repo_key, set()))

    @staticmethod
    def _repo_content_paths(workspace: WorkspacePlan, repo_key: str) -> set[str]:
        plan = workspace.repos[repo_key].plan
        return {node.path for node in plan.content_nodes()} if plan is not None else set()


__all__ = ["PlanValidation", "RestructurePlanningService"]
