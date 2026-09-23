"""Small supervisor for the LC Restructure workflow.

The supervisor owns only durable Restructure intent and frontier decisions. ARK
FlowService remains the source of truth for execution state and recovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime_kit.flow.models import FlowRequest

from lean_constellation.domain.restructure import ContentWork, RestructureStage, TaskBinding
from lean_constellation.services.restructure import RestructureService


@dataclass
class RestructureSupervisor:
    service: RestructureService
    runtime_resolver: Any = None

    @classmethod
    def for_workspace(cls, workspace_root: str) -> "RestructureSupervisor":
        return cls(RestructureService(workspace_root))

    def status(self) -> dict[str, Any]:
        plan, plan_version = self.service.store.load_workspace_plan()
        run, run_version = self.service.store.load_run()
        if plan is None:
            return {"prepared": False, "plan_version": 0, "run_version": run_version}
        repos: dict[str, Any] = {}
        for repo_key, repo in plan.repos.items():
            repo_plan = repo.plan
            if repo_plan is None:
                repos[repo_key] = {"directory": repo.directory, "planned": False, "declared": [], "proved": []}
                continue
            declared: list[str] = []
            proved: list[str] = []
            for node in repo_plan.content_nodes():
                try:
                    work, _ = self.service.content.load(repo.directory, node.path)
                except KeyError:
                    continue
                if work.stage in {RestructureStage.DECLARED, RestructureStage.PROVED, RestructureStage.FINAL}:
                    declared.append(node.path)
                if work.stage in {RestructureStage.PROVED, RestructureStage.FINAL}:
                    proved.append(node.path)
            repos[repo_key] = {
                "directory": repo.directory,
                "planned": True,
                "declared": sorted(declared),
                "proved": sorted(proved),
            }
        return {
            "prepared": True,
            "plan_version": plan_version,
            "run": run.model_dump(mode="json") if run else None,
            "run_version": run_version,
            "repos": repos,
        }

    def frontier(self, repo_key: str, stage: RestructureStage) -> list[str]:
        workspace, _ = self.service.store.load_workspace_plan()
        if workspace is None:
            raise ValueError("restructure workspace is not prepared")
        return [
            item["node_path"]
            for item in self.workspace_frontier(stage)
            if item["repo_key"] == repo_key
        ]

    def workspace_frontier(self, stage: RestructureStage) -> list[dict[str, str]]:
        """Return runnable Content tasks after applying repo dependencies."""

        workspace, _ = self.service.store.load_workspace_plan()
        if workspace is None:
            raise ValueError("restructure workspace is not prepared")
        run, _ = self.service.store.load_run()
        completed: dict[str, set[str]] = {}
        declared_completed: dict[str, set[str]] = {}
        for repo_key, repo in workspace.repos.items():
            if repo.plan is None:
                completed[repo_key] = set()
                declared_completed[repo_key] = set()
                continue
            paths: set[str] = set()
            declared_paths: set[str] = set()
            for node in repo.plan.content_nodes():
                try:
                    work, _ = self.service.content.load(repo.directory, node.path)
                except KeyError:
                    continue
                declared_binding = run.bindings.get(self._content_binding_key(repo_key, node.path, "declared")) if run else None
                if declared_binding and (declared_binding.stale or declared_binding.terminal_outcome != "declared"):
                    continue
                if stage is RestructureStage.DECLARED and work.stage in {
                    RestructureStage.DECLARED,
                    RestructureStage.PROVED,
                    RestructureStage.FINAL,
                }:
                    paths.add(node.path)
                    declared_paths.add(node.path)
                if stage is RestructureStage.PROVED and work.stage in {
                    RestructureStage.PROVED,
                    RestructureStage.FINAL,
                }:
                    proved_binding = run.bindings.get(self._content_binding_key(repo_key, node.path, "proved")) if run else None
                    if not proved_binding or (not proved_binding.stale and proved_binding.terminal_outcome == "proved"):
                        paths.add(node.path)
                if work.stage in {
                    RestructureStage.DECLARED,
                    RestructureStage.PROVED,
                    RestructureStage.FINAL,
                }:
                    declared_paths.add(node.path)
            completed[repo_key] = paths
            declared_completed[repo_key] = declared_paths
        return [
            {"repo_key": item.repo_key, "directory": item.directory, "node_path": item.node_path, "stage": item.stage.value}
            for item in self.service.planning.workspace_frontier(
                workspace,
                completed=completed,
                declared_completed=declared_completed,
                stage=stage.value,
            )
        ]

    def _runtime_for(self, runtime: Any, repo_key: str):
        return self.runtime_resolver(repo_key) if self.runtime_resolver else runtime

    @staticmethod
    def _content_binding_key(repo_key: str, node_path: str, stage: str) -> str:
        return f"{repo_key}:{node_path}:{stage}"

    @staticmethod
    def _build_binding_key(repo_key: str, stage: RestructureStage) -> str:
        return f"build:{repo_key}:{stage.value}"

    @classmethod
    def _latest_build_binding(cls, run, repo_key, stage):
        base = cls._build_binding_key(repo_key, stage)
        items = [(k, v) for k, v in run.bindings.items()
                 if (k == base or k.startswith(base + ":retry:")) and v.repo_key == repo_key and v.stage is stage]
        return max(items, key=lambda item: item[1].attempt_epoch) if items else None

    def _dispatch(self, runtime, key, repo_key, stage, flow_type, params, *, node_path=None,
                  epoch=0, enqueue=True, retry=False):
        """Persist intent, reserve, adopt/create an unqueued Flow, bind, then enqueue."""
        from lean_constellation.services.restructure.budget import RestructureBudget
        runtime = self._runtime_for(runtime, repo_key)
        fs = runtime.ark.flow_service
        if fs is None:
            raise ValueError("Restructure execution requires a repo-local FlowService")
        with self.service.store._lock:
            workspace, _ = self.service.store.load_workspace_plan()
            run, version = self.service.store.load_run()
            if workspace is None or run is None:
                raise ValueError("restructure workspace is not prepared")
            binding = run.bindings.get(key)
            budget = RestructureBudget(self.service.store, workspace)
            if binding and retry and binding.terminal_consumed and binding.terminal_outcome != "succeeded":
                binding.stale = True
                epoch = binding.attempt_epoch + 1
                if binding.reservation_id:
                    budget.release(binding.reservation_id)
                run.blocked_issues = [issue for issue in run.blocked_issues if not issue.startswith(key + ": ")]
            if binding is None or binding.stale:
                if binding is not None:
                    epoch = max(epoch, binding.attempt_epoch + 1)
                binding = TaskBinding(request_id=f"{run.run_id}:{key}:{epoch}", run_id=run.run_id,
                                      repo_key=repo_key, node_path=node_path, stage=stage,
                                      attempt_epoch=epoch, flow_type=flow_type, flow_params=params)
                previous = run.bindings.get(key)
                if previous is not None:
                    run.bindings[f"{key}:retry:{previous.attempt_epoch}"] = previous
                run.bindings[key] = binding
                run.status = "blocked" if run.blocked_issues else "running"
                version = self.service.store.save_run(run, expected_version=version)
            elif binding.terminal_consumed:
                return binding.flow_id, False
            if not binding.reservation_id:
                reservation = budget.acquire(run_id=run.run_id, kind="build" if flow_type == "restructure_build" else "agent",
                                             owner_ref=binding.request_id)
                if reservation is None:
                    return None, False
                binding.reservation_id = reservation.reservation_id
                version = self.service.store.save_run(run, expected_version=version)
            existing = None
            if binding.flow_id:
                try:
                    existing = fs.get_flow(binding.flow_id)
                except KeyError:
                    pass
            if existing is None and hasattr(fs, "list_flows"):
                existing = next((f for f in fs.list_flows(flow_type=flow_type)
                                 if getattr(f.input, "request_id", None) == binding.request_id), None)
            created = existing is None
            if created:
                request_params = {**binding.flow_params, "request_id": binding.request_id,
                                  "attempt_epoch": binding.attempt_epoch, "reservation_id": binding.reservation_id}
                binding.flow_id = fs.start_flow(FlowRequest(flow_type=flow_type,
                    scope_id=f"restructure:{binding.request_id}", params=request_params), enqueue=False)
            else:
                binding.flow_id = existing.flow_id
            self.service.store.save_run(run, expected_version=version)
        scheduler = getattr(runtime.ark, "schedule_service", None)
        if enqueue and scheduler is not None:
            scheduler.enqueue_flow(binding.flow_id)
        return binding.flow_id, created

    def start_workspace_frontier(self, runtime, *, stage, agent_id=None, enqueue=True):
        return self._start_content_items(runtime, self.workspace_frontier(stage), agent_id=agent_id, enqueue=enqueue)

    def _start_content_items(self, runtime, items, *, agent_id=None, enqueue=True):
        workspace, _ = self.service.store.load_workspace_plan()
        run, _ = self.service.store.load_run()
        if workspace is None or run is None:
            raise ValueError("restructure workspace is not prepared")
        if any(item["stage"] == "proved" for item in items) and not self._all_builds_succeeded(workspace, run, RestructureStage.DECLARED):
            return []
        started = []
        for item in items:
            repo_key, node_path, stage = item["repo_key"], item["node_path"], item["stage"]
            repo = workspace.repos[repo_key]
            current_run, _ = self.service.store.load_run()
            plan_binding = current_run.bindings.get(f"repo:{repo_key}:plan")
            if plan_binding is not None and (plan_binding.stale or plan_binding.terminal_outcome != "planned"):
                continue
            try:
                work, _ = self.service.content.load(repo.directory, node_path)
            except KeyError:
                work = None
            if work is not None and work.issues:
                continue
            # A failed ancestor blocks only its dependent closure during declared.
            params = dict(repo_key=repo_key, directory=repo.directory, node_path=node_path,
                          stage=stage, workspace_root=str(self.service.store.workspace_root),
                          agent_type="RestructureContentPlanAgent" if stage == "declared" else "RestructureContentImplementationAgent")
            flow_id, created = self._dispatch(runtime, self._content_binding_key(repo_key, node_path, stage),
                repo_key, RestructureStage(stage), "restructure_content", params, node_path=node_path,
                epoch=work.attempt_epoch if work else 0, enqueue=enqueue)
            if created:
                started.append(flow_id)
        return started

    def start_content_frontier(self, runtime, *, repo_key, stage, agent_id=None, enqueue=True):
        return self._start_content_items(runtime, [item for item in self.workspace_frontier(stage)
            if item["repo_key"] == repo_key], agent_id=agent_id, enqueue=enqueue)

    def start_repo_plan(self, runtime, *, repo_key, agent_id=None, enqueue=True):
        workspace, _ = self.service.store.load_workspace_plan()
        repo = workspace.repos[repo_key]
        return self._dispatch(runtime, f"repo:{repo_key}:plan", repo_key, RestructureStage.PLAN,
            "restructure_repo_plan", dict(repo_key=repo_key, directory=repo.directory,
                workspace_root=str(self.service.store.workspace_root)), enqueue=enqueue)[0]

    def start_build(self, runtime, *, repo_key, stage, agent_id=None, enqueue=True,
                    operation_id=None, provider_refs=None, targets=None, retry=True):
        if stage not in {RestructureStage.DECLARED, RestructureStage.PROVED, RestructureStage.FINAL}:
            raise ValueError("build supports declared, proved, or final stages")
        workspace, _ = self.service.store.load_workspace_plan()
        repo = workspace.repos[repo_key]
        return self._dispatch(runtime, self._build_binding_key(repo_key, stage), repo_key, stage,
            "restructure_build", dict(repo_key=repo_key, directory=repo.directory, stage=stage.value,
                workspace_root=str(self.service.store.workspace_root), operation_id=operation_id,
                provider_refs=provider_refs or {}, targets=targets or []), enqueue=enqueue, retry=retry)[0]

    def advance_run(self, runtime, *, agent_id=None, enqueue=True):
        workspace, _ = self.service.store.load_workspace_plan()
        run, _ = self.service.store.load_run()
        if workspace is None or run is None:
            raise ValueError("restructure workspace is not prepared")
        if run.status in {"paused", "completed"}:
            return {"started": [], "status": run.status}
        started = []
        for key, repo in workspace.repos.items():
            if repo.plan is None:
                fid = self.start_repo_plan(runtime, repo_key=key, enqueue=enqueue)
                if fid:
                    started.append(fid)
        started += self.start_workspace_frontier(runtime, stage=RestructureStage.DECLARED, enqueue=enqueue)
        # Build each stable repo independently. A failed sibling does not stop it.
        for key, repo in workspace.repos.items():
            if repo.plan is not None and self._repo_at_least(repo, RestructureStage.DECLARED):
                if any(not self._build_succeeded(run, provider, RestructureStage.DECLARED)
                       for provider in workspace.repo_dependencies.get(key, [])):
                    continue
                latest = self._latest_build_binding(run, key, RestructureStage.DECLARED)
                if latest is None or latest[1].stale or not latest[1].terminal_consumed:
                    fid = self.start_build(runtime, repo_key=key, stage=RestructureStage.DECLARED, enqueue=enqueue, retry=False)
                    if fid:
                        started.append(fid)
        run, version = self.service.store.load_run()
        if not self._all_builds_succeeded(workspace, run, RestructureStage.DECLARED):
            return {"started": started, "status": run.status}
        if run.completion_target.value == "declared":
            run.status = "completed"
            self.service.store.save_run(run, expected_version=version)
            return {"started": started, "status": run.status}
        started += self.start_workspace_frontier(runtime, stage=RestructureStage.PROVED, enqueue=enqueue)
        for key, repo in workspace.repos.items():
            if repo.plan is None or not self._repo_at_least(repo, RestructureStage.PROVED):
                continue
            providers = workspace.repo_dependencies.get(key, [])
            if any(not self._build_succeeded(run, p, RestructureStage.FINAL) for p in providers):
                continue
            latest = self._latest_build_binding(run, key, RestructureStage.FINAL)
            if latest is not None and not latest[1].stale and latest[1].terminal_consumed:
                continue
            refs = {p: self._latest_build_binding(run, p, RestructureStage.FINAL)[1].result_artifact_id for p in providers}
            fid = self.start_build(runtime, repo_key=key, stage=RestructureStage.FINAL,
                                   provider_refs=refs, enqueue=enqueue, retry=False)
            if fid:
                started.append(fid)
        run, version = self.service.store.load_run()
        if self._all_builds_succeeded(workspace, run, RestructureStage.FINAL) and not run.blocked_issues:
            run.status = "completed"
            self.service.store.save_run(run, expected_version=version)
        return {"started": started, "status": run.status}

    def _repo_at_least(self, repo, stage):
        if repo.plan is None:
            return False
        order = {"plan": 0, "declared": 1, "proved": 2, "final": 3}
        for node in repo.plan.content_nodes():
            try:
                work, _ = self.service.content.load(repo.directory, node.path)
            except KeyError:
                return False
            run, _ = self.service.store.load_run()
            binding = run.bindings.get(self._content_binding_key(repo.key, node.path, stage.value)) if run else None
            if binding and (binding.stale or binding.terminal_outcome != stage.value):
                return False
            if work.issues or order[work.stage.value] < order[stage.value]:
                return False
        return True

    def _all_content_at_least(self, workspace, stage):
        return all(self._repo_at_least(repo, stage) for repo in workspace.repos.values())

    @staticmethod
    def _build_succeeded(run, repo_key, stage):
        latest = RestructureSupervisor._latest_build_binding(run, repo_key, stage)
        return bool(latest and not latest[1].stale and latest[1].terminal_outcome == "succeeded")

    def _all_builds_succeeded(self, workspace, run, stage):
        return all(repo.plan is not None and self._build_succeeded(run, key, stage) for key, repo in workspace.repos.items())

    def reconcile(self, runtime):
        from lean_constellation.services.restructure.budget import RestructureBudget
        run, _ = self.service.store.load_run()
        workspace, _ = self.service.store.load_workspace_plan()
        if run is None or workspace is None:
            raise ValueError("restructure workspace is not prepared")
        # Resolve runtimes before locking metadata (registry may materialize homes).
        runtimes = {key: self._runtime_for(runtime, key) for key in workspace.repos}
        changed, failures = 0, []
        budget = RestructureBudget(self.service.store, workspace)
        with self.service.store._lock:
            run, version = self.service.store.load_run()
            for key, binding in run.bindings.items():
                if binding.stale or binding.terminal_consumed:
                    if binding.reservation_id:
                        budget.release(binding.reservation_id)
                    continue
                if not binding.flow_id:
                    continue
                fs = runtimes[binding.repo_key].ark.flow_service
                try:
                    flow = fs.get_flow(binding.flow_id)
                except KeyError:
                    continue
                status = getattr(flow.status, "value", flow.status)
                if status not in {"completed", "failed"}:
                    continue
                changed += 1
                result = flow.result
                outcome = getattr(result, "outcome", None)
                binding.terminal_consumed = True
                binding.terminal_outcome = outcome or ("failed" if status == "failed" else "completed")
                binding.result_artifact_id = getattr(result, "operation_id", None) or getattr(result, "artifact_id", None)
                if binding.reservation_id:
                    budget.release(binding.reservation_id)
                if status == "failed" or outcome in {"failed", "blocked", "incomplete"}:
                    message = f"{key}: {getattr(flow.error, 'message', None) or f'flow ended with {outcome or status}'}"
                    failures.append(message)
                    if message not in run.blocked_issues:
                        run.blocked_issues.append(message)
                    if binding.node_path:
                        directory = workspace.repos[binding.repo_key].directory
                        try:
                            work, ver = self.service.content.load(directory, binding.node_path)
                        except KeyError:
                            work, ver = ContentWork(repo_key=binding.repo_key, node_path=binding.node_path), 0
                        if message not in work.issues:
                            work.issues.append(message)
                            self.service.store.save_content(directory, binding.node_path, work, expected_version=ver)
            if changed:
                run.status = "blocked" if run.blocked_issues else "running"
                self.service.store.save_run(run, expected_version=version)
        advance = self.advance_run(runtime) if run.status not in {"prepared", "paused", "completed"} else None
        run, _ = self.service.store.load_run()
        return {"changed": changed, "blocked_issues": failures, "advance": advance, "run": run.model_dump(mode="json")}

    def reopen_declared(self, repo_key, node_path):
        """Explicitly invalidate the interface's dependent closure, keeping source."""
        from lean_constellation.domain.restructure import DeclStatus
        from lean_constellation.services.restructure.budget import RestructureBudget
        with self.service.store._lock:
            workspace, _ = self.service.store.load_workspace_plan()
            if workspace is None or repo_key not in workspace.repos:
                raise ValueError("unknown workspace repo")
            repo = workspace.repos[repo_key]
            if repo.plan is None or node_path not in {n.path for n in repo.plan.content_nodes()}:
                raise ValueError("unknown Content node")
            affected = {(repo_key, node_path)}
            while True:
                previous = set(affected)
                for key, spec in workspace.repos.items():
                    if spec.plan is None:
                        continue
                    for node in spec.plan.content_nodes():
                        cross_repo = any(provider in {r for r, _ in affected} for provider in workspace.repo_dependencies.get(key, []))
                        local = any((key, dep) in affected for dep in node.dependencies)
                        work, _ = self.service.store.load_content(spec.directory, node.path, ContentWork)
                        registered = work and any((dep.ref.repo, dep.ref.node) in affected or
                            (dep.ref.node is None and dep.ref.repo in {r for r, _ in affected})
                            for decl in work.decls.values() for dep in decl.dependencies if not dep.external)
                        if cross_repo or local or registered:
                            affected.add((key, node.path))
                if affected == previous:
                    break
            repos = {r for r, _ in affected}
            run, version = self.service.store.load_run()
            impacted = [(k, b) for k, b in run.bindings.items() if not b.stale and
                        ((b.repo_key, b.node_path) in affected or (b.repo_key in repos and b.node_path is None and b.stage is not RestructureStage.PLAN))]
            if any(b.flow_id and not b.terminal_consumed for _, b in impacted):
                raise ValueError("interface replan requires affected tasks to be terminal and reconciled")
            for key, node in affected:
                directory = workspace.repos[key].directory
                work, ver = self.service.store.load_content(directory, node, ContentWork)
                if work is None:
                    continue
                work.stage = RestructureStage.PLAN
                work.attempt_epoch += 1
                work.declared_baseline = {}
                work.accepted_artifact_ids = []
                work.issues = []
                for decl in work.decls.values():
                    decl.file_digest = None
                    decl.status = DeclStatus.DRAFT
                self.service.store.save_content(directory, node, work, expected_version=ver)
            budget = RestructureBudget(self.service.store, workspace)
            for key, binding in impacted:
                binding.stale = True
                if binding.reservation_id:
                    budget.release(binding.reservation_id)
                run.blocked_issues = [issue for issue in run.blocked_issues if not issue.startswith(key + ": ")]
            run.status = "blocked" if run.blocked_issues else "running"
            self.service.store.save_run(run, expected_version=version)
            return self.service.content.load(repo.directory, node_path)[1]

    def retry_content(self, repo_key, node_path, *, reopen_declared=False):
        if reopen_declared:
            return self.reopen_declared(repo_key, node_path)
        from lean_constellation.services.restructure.budget import RestructureBudget
        with self.service.store._lock:
            workspace, _ = self.service.store.load_workspace_plan()
            if workspace is None or repo_key not in workspace.repos:
                raise ValueError(f"unknown workspace repo: {repo_key}")
            repo = workspace.repos[repo_key]
            if repo.plan is None or node_path not in {n.path for n in repo.plan.content_nodes()}:
                raise ValueError(f"unknown Content node for repo: {repo_key}/{node_path}")
            run, version = self.service.store.load_run()
            current_work, _ = self.service.store.load_content(repo.directory, node_path, ContentWork)
            retry_stage = RestructureStage.PROVED if current_work and current_work.declared_baseline else RestructureStage.DECLARED
            affected = [(k, b) for k, b in run.bindings.items() if b.repo_key == repo_key and
                        b.node_path == node_path and b.stage is retry_stage and not b.stale]
            affected_repos = {repo_key}
            while True:
                downstream = {key for key, providers in workspace.repo_dependencies.items()
                              if affected_repos.intersection(providers)}
                if downstream <= affected_repos:
                    break
                affected_repos.update(downstream)
            affected += [(k, b) for k, b in run.bindings.items()
                         if b.repo_key in affected_repos and b.flow_type == "restructure_build"
                         and not b.stale and (retry_stage is RestructureStage.DECLARED
                                              or b.stage is not RestructureStage.DECLARED)]
            if any(b.flow_id and not b.terminal_consumed for _, b in affected):
                raise ValueError("reconcile a terminal Content flow before retry; an active writer cannot be replaced")
            try:
                work, ver = self.service.content.load(repo.directory, node_path)
            except KeyError:
                work, ver = ContentWork(repo_key=repo_key, node_path=node_path), 0
            work.attempt_epoch += 1
            work.stage = RestructureStage.DECLARED if retry_stage is RestructureStage.PROVED else RestructureStage.PLAN
            work.issues = []
            saved = self.service.store.save_content(repo.directory, node_path, work, expected_version=ver)
            budget = RestructureBudget(self.service.store, workspace)
            for key, binding in affected:
                binding.stale = True
                if binding.reservation_id:
                    budget.release(binding.reservation_id)
                run.blocked_issues = [i for i in run.blocked_issues if not i.startswith(key + ": ")]
            run.status = "blocked" if run.blocked_issues else "running"
            self.service.store.save_run(run, expected_version=version)
            return saved


__all__ = ["RestructureSupervisor"]
