"""Synchronous Admin facade used by the Restructure Flow and tests."""

from __future__ import annotations

from pathlib import Path

from lean_constellation.domain.restructure import (
    ContentWork,
    RepoPlan,
    RepoSpec,
    ReviewReceipt,
    WorkspacePlan,
    WorkspaceRun,
    utc_now_iso,
    RestructureStage,
)
from lean_constellation.services.restructure.content import RestructureContentService, content_transaction
from lean_constellation.services.restructure.artifacts import RestructureArtifactService
from lean_constellation.services.restructure.build import BuildResult, RestructureBuildService
from lean_constellation.services.restructure.layout import RestructureLayout
from lean_constellation.services.restructure.store import RestructureStore
from lean_constellation.services.restructure.planning import RestructurePlanningService


class RestructureService:
    def __init__(self, workspace_root: Path | str) -> None:
        self.store = RestructureStore(workspace_root)
        self.layout = RestructureLayout(self.store)
        self.content = RestructureContentService(self.store, self.layout)
        self.artifacts = RestructureArtifactService(self.store)
        self.builds = RestructureBuildService(self.store)
        self.planning = RestructurePlanningService()

    @content_transaction
    def prepare(self, plan: WorkspacePlan, *, overwrite: bool = False) -> WorkspaceRun:
        existing, _ = self.store.load_workspace_plan()
        if existing is not None and not overwrite:
            raise ValueError("restructure workspace is already prepared; use overwrite explicitly")
        run, _ = self.store.load_run()
        if run and any(not b.stale and not b.terminal_consumed for b in run.bindings.values()):
            raise ValueError("cannot overwrite a workspace with pending tasks")
        validation = self.planning.validate_workspace(plan)
        if not validation.ok:
            raise ValueError("; ".join(validation.errors))
        self.store.save_workspace_plan(plan)
        self.layout.prepare_workspace(plan)
        for repo in plan.repos.values():
            if repo.plan is not None:
                validation = self.planning.validate_repo(repo.plan, workspace_plan=plan)
                if not validation.ok:
                    raise ValueError("; ".join(validation.errors))
                self.store.save_repo_plan(repo.directory, repo.plan)
        run = WorkspaceRun(
            run_id=plan.run_id,
            plan_version=plan.version,
            completion_target=plan.completion_target,
            status="prepared",
        )
        self.store.save_run(run)
        return run

    @content_transaction
    def submit_repo_plan(self, plan: RepoPlan, *, expected_version: int | None = None) -> int:
        workspace, workspace_version = self.store.load_workspace_plan()
        if workspace is None or plan.repo_key not in workspace.repos:
            raise ValueError(f"unknown workspace repo: {plan.repo_key}")
        repo = workspace.repos[plan.repo_key]
        if repo.plan is not None and repo.plan != plan:
            run, _ = self.store.load_run()
            if run and any(b.repo_key == plan.repo_key and b.node_path and not b.stale and not b.terminal_consumed for b in run.bindings.values()):
                raise ValueError("cannot change a repo plan while Content tasks are active")
            for node in repo.plan.content_nodes():
                work, _ = self.store.load_content(repo.directory, node.path, ContentWork)
                if work and work.declared_baseline:
                    raise ValueError("accepted interfaces require explicit replan invalidation before changing the plan")
        if repo.directory != plan.directory or repo.module_root != plan.module_root:
            raise ValueError("repo plan directory/module_root does not match workspace plan")
        validation = self.planning.validate_repo(plan, workspace_plan=workspace)
        if not validation.ok:
            raise ValueError("invalid restructure repo plan: " + "; ".join(validation.errors))
        self.layout.prepare_repo(repo.directory, repo.module_root, plan)
        plan_version = self.store.save_repo_plan(repo.directory, plan, expected_version=expected_version)
        repo.plan = plan
        workspace.repos[plan.repo_key] = repo
        self.store.save_workspace_plan(workspace, expected_version=workspace_version)
        return plan_version

    def repo_spec(self, repo_key: str) -> RepoSpec:
        workspace, _ = self.store.load_workspace_plan()
        if workspace is None or repo_key not in workspace.repos:
            raise ValueError(f"unknown workspace repo: {repo_key}")
        return workspace.repos[repo_key]

    def repo_key_for_directory(self, directory: str) -> str:
        workspace, _ = self.store.load_workspace_plan()
        if workspace is None:
            raise ValueError("restructure workspace is not prepared")
        return workspace.repo_key_for_directory(directory)

    def directory_for_repo(self, repo_key: str) -> str:
        return self.repo_spec(repo_key).directory

    def load_repo_plan(self, repo_key: str) -> tuple[RepoPlan, int]:
        workspace, _ = self.store.load_workspace_plan()
        if workspace is None or repo_key not in workspace.repos:
            raise ValueError(f"unknown workspace repo: {repo_key}")
        directory = workspace.repos[repo_key].directory
        plan, version = self.store.load_repo_plan(directory, RepoPlan)
        if plan is None:
            raise KeyError(f"repo plan not found: {repo_key}")
        return plan, version

    def content_status(self, repo_key: str, node_path: str) -> ContentWork:
        workspace, _ = self.store.load_workspace_plan()
        if workspace is None or repo_key not in workspace.repos:
            raise ValueError(f"unknown workspace repo: {repo_key}")
        return self.content.load(workspace.repos[repo_key].directory, node_path)[0]

    def record_review(self, directory: str, receipt: ReviewReceipt) -> Path:
        root = self.store.repo_metadata_root(directory) / "reviews"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{receipt.artifact_id}.json"
        self.store._atomic_write(path, receipt.model_dump(mode="json"))
        return path

    def build_repo(
        self,
        repo_key: str,
        *,
        operation_id: str | None = None,
        targets: list[str] | None = None,
        stage: RestructureStage = RestructureStage.PROVED,
        provider_refs: dict[str, str] | None = None,
        declared_repair: bool = False,
        request_id: str | None = None,
    ) -> BuildResult:
        directory = self.directory_for_repo(repo_key)
        if declared_repair:
            if RestructureStage(stage) is not RestructureStage.DECLARED:
                raise ValueError("interface repair is only allowed at the declared build gate")
            from .repair import prepare_declared_repair
            prepare_declared_repair(self, repo_key, request_id)
        result = self.builds.build(
            directory,
            operation_id=operation_id,
            targets=targets,
            stage=stage,
            repo_key=repo_key,
            provider_refs=provider_refs,
            allow_declared_repair=declared_repair,
        )
        if result.receipt.success:
            from .repair import accept_build_content
            accept_build_content(self, directory, result, declared_repair=declared_repair)
        receipt_path = self.store.save_build_receipt(directory, result.receipt)
        if result.receipt.success and result.receipt.stage is RestructureStage.FINAL:
            from lean_constellation.lhf.restructure_export import seal_restructure_acceptance
            try:
                with self.store._lock:
                    seal_restructure_acceptance(
                        repo_root=self.store.repo_root(directory), receipt_path=receipt_path,
                        output=self.store.repo_metadata_root(directory) / "accepted" / result.receipt.operation_id,
                    )
            except (ValueError, OSError) as exc:
                result.receipt.success = False
                result.receipt.diagnostics.append(f"Final acceptance could not be sealed: {exc}")
                self.store.save_build_receipt(directory, result.receipt)
        return result


__all__ = ["RestructureService"]
