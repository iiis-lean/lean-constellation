"""Workspace registry for repo-local Lean Constellation runtimes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Literal

from agent_runtime_kit.flow import SchedulerRunBudget, SchedulerRunControlView
from agent_runtime_kit.flow.models import FlowStatus
from pydantic import Field

from lean_constellation.app.bootstrap import (
    ProductionAgentHomesView,
    initialize_repo_business_truth,
    materialize_production_agent_homes,
)
from lean_constellation.app.config import LeanAppConfig
from lean_constellation.app.runtime import create_app_runtime_services, external_client_config_from_app_config
from lean_constellation.domain.common import StrictModel
from lean_constellation.services import create_lean_runtime_services
from lean_constellation.services.foundation import ServiceIssue, ServiceResult
from lean_constellation.services.foundation.result_error import ResultErrorComponent
from lean_constellation.services.runtime import LeanRuntimeServices


RepoRuntimeState = Literal["unloaded", "loading", "active", "paused", "dormant", "failed"]


class RepoRuntimeStatusView(StrictModel):
    repo_key: str
    repo_root: str
    runtime_root: str
    state: RepoRuntimeState
    loaded: bool
    paused: bool | None = None
    run_control: SchedulerRunControlView | None = None
    flow_count: int | None = None
    step_count: int | None = None
    agent_count: int | None = None
    agent_homes: ProductionAgentHomesView | None = None
    last_error: str | None = None
    startup_warnings: list[str] = Field(default_factory=list)
    summary: str


class RepoRuntimeListView(StrictModel):
    workspace_root: str
    scheduler_enabled: bool
    server_start_paused: bool
    test_control_enabled: bool
    repos: list[RepoRuntimeStatusView] = Field(default_factory=list)
    summary: str


@dataclass
class RepoRuntimeRecord:
    repo_key: str
    repo_root: Path
    runtime_root: Path
    runtime: LeanRuntimeServices | None = None
    state: RepoRuntimeState = "unloaded"
    agent_homes: ProductionAgentHomesView | None = None
    last_error: str | None = None
    startup_warnings: list[str] = field(default_factory=list)
    lock: RLock = field(default_factory=RLock, repr=False)

    @property
    def loaded(self) -> bool:
        return self.runtime is not None


class RepoRuntimeRegistry:
    """Manage repo-local runtimes for one workspace-level server."""

    def __init__(
        self,
        config: LeanAppConfig,
        *,
        external_config: object | None = None,
        external_overrides: dict[str, object] | None = None,
        agent_providers: dict[str, object] | None = None,
        result: ResultErrorComponent | None = None,
    ) -> None:
        self.config = config
        self.workspace_root = Path(config.workspace_root).expanduser()
        self.external_config = external_config or external_client_config_from_app_config(config)
        self.external_overrides = external_overrides
        self.agent_providers = agent_providers
        self.result = result or ResultErrorComponent()
        self._records: dict[str, RepoRuntimeRecord] = {}
        self._lock = RLock()
        self._workspace_runtime: LeanRuntimeServices | None = None

    def normalize_repo_key(self, repo_key: str) -> ServiceResult[str]:
        value = str(repo_key).strip()
        if not value:
            return self.result.fail(self.result.issue("repo_key_empty", "Repo key must be non-empty."))
        if value in {".", ".."} or "/" in value or "\\" in value:
            return self.result.fail(
                self.result.issue(
                    "repo_key_invalid",
                    "Repo key must be a simple workspace child directory name.",
                    field="repo_key",
                    current=value,
                )
            )
        return self.result.ok(value)

    def discover_repo(self, repo_key: str) -> ServiceResult[RepoRuntimeRecord]:
        normalized = self.normalize_repo_key(repo_key)
        if not normalized.ok or normalized.value is None:
            return self.result.fail(normalized.issues)
        key = normalized.value
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                return self.result.ok(existing)
            repo_root = (self.workspace_root / key).resolve()
            workspace_root = self.workspace_root.resolve()
            if workspace_root not in [repo_root, *repo_root.parents]:
                return self.result.fail(
                    self.result.issue(
                        "repo_outside_workspace",
                        "Resolved repo root is outside the configured workspace.",
                        object_ref=str(repo_root),
                    )
                )
            constellation_root = repo_root / ".lean_constellation"
            if not repo_root.exists() or not repo_root.is_dir():
                return self.result.fail(
                    self.result.issue("repo_not_found", f"Repo root not found: {repo_root}", object_ref=str(repo_root))
                )
            if not constellation_root.exists() or not constellation_root.is_dir():
                return self.result.fail(
                    self.result.issue(
                        "repo_not_initialized",
                        "Repo does not contain a .lean_constellation directory.",
                        object_ref=str(repo_root),
                    )
                )
            record = RepoRuntimeRecord(
                repo_key=key,
                repo_root=repo_root,
                runtime_root=repo_root / ".agent_runtime",
            )
            self._records[key] = record
            return self.result.ok(record)

    def list_discovered_repo_keys(self) -> ServiceResult[list[str]]:
        if not self.workspace_root.exists() or not self.workspace_root.is_dir():
            return self.result.fail(
                self.result.issue(
                    "workspace_not_found",
                    f"Workspace root not found: {self.workspace_root}",
                    object_ref=str(self.workspace_root),
                )
            )
        keys = [
            path.name
            for path in sorted(self.workspace_root.iterdir())
            if path.is_dir() and (path / ".lean_constellation").is_dir()
        ]
        return self.result.ok(keys)

    def list_status(self, *, discover: bool = True) -> ServiceResult[RepoRuntimeListView]:
        if discover:
            discovered = self.list_discovered_repo_keys()
            if not discovered.ok or discovered.value is None:
                return self.result.fail(discovered.issues)
            for key in discovered.value:
                result = self.discover_repo(key)
                if not result.ok:
                    return self.result.fail(result.issues)
        with self._lock:
            statuses = [self._status_for_record(record) for record in sorted(self._records.values(), key=lambda item: item.repo_key)]
        return self.result.ok(
            RepoRuntimeListView(
                workspace_root=str(self.workspace_root),
                scheduler_enabled=self.config.scheduler_enabled,
                server_start_paused=self.config.server_start_paused,
                test_control_enabled=self.config.test_control_enabled,
                repos=statuses,
                summary=f"Found {len(statuses)} repo runtime records.",
            )
        )

    def get_status(self, repo_key: str) -> ServiceResult[RepoRuntimeStatusView]:
        discovered = self.discover_repo(repo_key)
        if not discovered.ok or discovered.value is None:
            return self.result.fail(discovered.issues)
        return self.result.ok(self._status_for_record(discovered.value))

    def loaded_records(self) -> list[RepoRuntimeRecord]:
        with self._lock:
            return [record for record in self._records.values() if record.runtime is not None]

    def workspace_runtime(self) -> LeanRuntimeServices:
        """Return a lightweight workspace control-plane service runtime."""

        with self._lock:
            if self._workspace_runtime is None:
                self._workspace_runtime = create_lean_runtime_services(
                    external_config=self.external_config,
                    external_overrides=self.external_overrides,
                    native_lake_project_config=self.config.native_lake_project,
                    workspace_config=self.config.workspace_config,
                    register_application_tools=False,
                )
            return self._workspace_runtime

    def initialize_and_load(
        self,
        repo_key: str,
        *,
        main_node: str = "Main",
        refresh_homes: bool = True,
    ) -> ServiceResult[LeanRuntimeServices]:
        """Initialize business truth, register the repo, then create its ARK runtime."""
        normalized = self.normalize_repo_key(repo_key)
        if not normalized.ok or normalized.value is None:
            return self.result.fail(normalized.issues)
        root = self.workspace_root / normalized.value
        initialized = initialize_repo_business_truth(
            self.workspace_runtime(), root, main_node=main_node
        )
        if not initialized.ok:
            return self.result.fail(initialized.issues)
        discovered = self.discover_repo(normalized.value)
        if not discovered.ok:
            return self.result.fail(discovered.issues)
        return self.get_or_load(normalized.value, refresh_homes=refresh_homes)

    def get_or_load(self, repo_key: str, *, refresh_homes: bool = True) -> ServiceResult[LeanRuntimeServices]:
        discovered = self.discover_repo(repo_key)
        if not discovered.ok or discovered.value is None:
            return self.result.fail(discovered.issues)
        record = discovered.value
        with record.lock:
            return self._get_or_load_record(
                record,
                refresh_homes=refresh_homes,
                start_paused=self.config.server_start_paused,
            )

    def get_or_load_paused(
        self,
        repo_key: str,
        *,
        refresh_homes: bool = False,
    ) -> ServiceResult[LeanRuntimeServices]:
        """Atomically load a repo runtime in paused management mode.

        The runtime is constructed with its pause gate already active while the
        repo record lock is held.  This deliberately does not implement
        get-or-load followed by pause, which would leave a scheduler window.
        """

        discovered = self.discover_repo(repo_key)
        if not discovered.ok or discovered.value is None:
            return self.result.fail(discovered.issues)
        record = discovered.value
        with record.lock:
            if record.runtime is not None and not _runtime_paused(record.runtime):
                return self.result.fail(
                    self.result.issue(
                        "operator_repo_runtime_not_paused",
                        "Loaded repo runtime must be paused before operator management.",
                        object_ref=record.repo_key,
                    )
                )
            return self._get_or_load_record(
                record,
                refresh_homes=refresh_homes,
                start_paused=True,
            )

    @staticmethod
    def runtime_history_exists(record: RepoRuntimeRecord) -> bool:
        """Return whether the repo has persisted ARK runtime history."""

        root = record.runtime_root
        if not root.exists() or not root.is_dir():
            return False
        return any(path.is_file() for path in root.rglob("*"))

    def check_operator_runtime_stable(self, record: RepoRuntimeRecord) -> ServiceResult[None]:
        """Fail closed unless a loaded runtime is paused and fully quiescent.

        Callers must hold ``record.lock`` across this inspection and the
        protected mutation.
        """

        runtime = record.runtime
        if runtime is None:
            return self.result.fail(
                self.result.issue(
                    "operator_repo_runtime_history_unloaded",
                    "Repo runtime history exists but is not loaded for operator management.",
                    object_ref=record.repo_key,
                    suggested_action="Call prepare_repo_management before mutating this repo.",
                )
            )
        controller = runtime.ark.pause_controller
        try:
            if controller is None or not hasattr(controller, "is_paused") or not controller.is_paused():
                return self.result.fail(
                    self.result.issue(
                        "operator_repo_runtime_not_paused",
                        "Repo runtime must be globally paused for operator mutation.",
                        object_ref=record.repo_key,
                    )
                )
            agent_service = runtime.ark.agent_service
            step_service = runtime.ark.step_service
            flow_service = runtime.ark.flow_service
            schedule_service = runtime.ark.schedule_service
            if agent_service is None or not hasattr(agent_service, "list_running_agents"):
                raise RuntimeError("agent runtime inspection is unavailable")
            if step_service is None or not hasattr(step_service, "list_running_steps"):
                raise RuntimeError("step runtime inspection is unavailable")
            if flow_service is None or not hasattr(flow_service, "list_flows"):
                raise RuntimeError("flow runtime inspection is unavailable")
            if schedule_service is None or not hasattr(schedule_service, "active_flow_advances"):
                raise RuntimeError("scheduler runtime inspection is unavailable")
            running_agents = list(agent_service.list_running_agents())
            running_steps = list(step_service.list_running_steps())
            nonterminal_flows = [
                flow
                for flow in flow_service.list_flows()
                if getattr(flow, "status", None) not in {FlowStatus.COMPLETED, FlowStatus.FAILED}
            ]
            active_advances = list(schedule_service.active_flow_advances)
        except Exception as exc:  # noqa: BLE001 - admission must fail closed.
            return self.result.fail(
                self.result.issue(
                    "operator_runtime_inspection_failed",
                    f"Failed to inspect repo runtime stability: {exc}",
                    object_ref=record.repo_key,
                )
            )
        if running_agents or running_steps or nonterminal_flows or active_advances:
            return self.result.fail(
                self.result.issue(
                    "operator_repo_runtime_busy",
                    "Repo runtime still has active work and cannot accept operator mutation.",
                    object_ref=record.repo_key,
                    details={
                        "running_agents": len(running_agents),
                        "running_steps": len(running_steps),
                        "nonterminal_flows": len(nonterminal_flows),
                        "active_flow_advances": len(active_advances),
                    },
                )
            )
        return self.result.ok(None)

    def try_get_loaded(self, repo_key: str) -> LeanRuntimeServices | None:
        normalized = self.normalize_repo_key(repo_key)
        if not normalized.ok or normalized.value is None:
            return None
        with self._lock:
            record = self._records.get(normalized.value)
        return record.runtime if record is not None else None

    def _check_bounded_resume_admission(self, record: RepoRuntimeRecord) -> ServiceResult[None]:
        """Validate a repo-global bounded lease without rejecting runnable candidates."""

        runtime = record.runtime
        if runtime is None:
            return self.result.fail(
                self.result.issue(
                    "bounded_resume_runtime_unloaded",
                    "Repo runtime must be loaded before bounded resume.",
                    object_ref=record.repo_key,
                )
            )
        controller = runtime.ark.pause_controller
        if controller is None or not hasattr(controller, "is_paused") or not controller.is_paused(None):
            return self.result.fail(
                self.result.issue(
                    "bounded_resume_requires_global_pause",
                    "Bounded scheduler resume requires a globally paused runtime.",
                    object_ref=record.repo_key,
                )
            )
        try:
            agent_service = runtime.ark.agent_service
            step_service = runtime.ark.step_service
            schedule_service = runtime.ark.schedule_service
            if agent_service is None or not hasattr(agent_service, "list_running_agents"):
                raise RuntimeError("agent runtime inspection is unavailable")
            if step_service is None or not hasattr(step_service, "list_running_steps"):
                raise RuntimeError("step runtime inspection is unavailable")
            if schedule_service is None or not hasattr(schedule_service, "active_flow_advances"):
                raise RuntimeError("scheduler runtime inspection is unavailable")
            running_agents = list(agent_service.list_running_agents())
            running_steps = list(step_service.list_running_steps())
            active_advances = list(schedule_service.active_flow_advances)
        except Exception as exc:  # noqa: BLE001 - admission must fail closed.
            return self.result.fail(
                self.result.issue(
                    "bounded_resume_inspection_failed",
                    f"Failed to inspect bounded resume admission: {exc}",
                    object_ref=record.repo_key,
                )
            )
        if running_agents or running_steps or active_advances:
            return self.result.fail(
                self.result.issue(
                    "bounded_resume_runtime_busy",
                    "Bounded scheduler resume requires no running Agent, Step, or Flow advance.",
                    object_ref=record.repo_key,
                    details={
                        "running_agents": len(running_agents),
                        "running_steps": len(running_steps),
                        "active_flow_advances": len(active_advances),
                    },
                )
            )
        return self.result.ok(None)

    def pause(self, repo_key: str) -> ServiceResult[RepoRuntimeStatusView]:
        runtime_result = self.get_or_load(repo_key, refresh_homes=False)
        if not runtime_result.ok or runtime_result.value is None:
            return self.result.fail(runtime_result.issues)
        normalized = self.normalize_repo_key(repo_key)
        if not normalized.ok or normalized.value is None:
            return self.result.fail(normalized.issues)
        record = self._records[normalized.value]
        with record.lock:
            controller = runtime_result.value.ark.pause_controller
            if controller is not None:
                controller.pause(None)
            schedule_service = runtime_result.value.ark.schedule_service
            if schedule_service is not None and hasattr(schedule_service, "clear_run_budget"):
                schedule_service.clear_run_budget(reason="manual_pause")
            record.state = "paused"
            return self.result.ok(self._status_for_record(record))

    def resume(
        self,
        repo_key: str,
        *,
        budget: SchedulerRunBudget | None = None,
        rebuild_queues: bool = True,
    ) -> ServiceResult[RepoRuntimeStatusView]:
        if budget is not None and not rebuild_queues:
            return self.result.fail(
                self.result.issue(
                    "bounded_resume_rebuild_required",
                    "Bounded scheduler resume requires candidate queue rebuild.",
                    object_ref=repo_key,
                )
            )
        runtime_result = self.get_or_load(repo_key, refresh_homes=False)
        if not runtime_result.ok or runtime_result.value is None:
            return self.result.fail(runtime_result.issues)
        normalized = self.normalize_repo_key(repo_key)
        if not normalized.ok or normalized.value is None:
            return self.result.fail(normalized.issues)
        record = self._records[normalized.value]
        with record.lock:
            runtime = runtime_result.value
            controller = runtime.ark.pause_controller
            schedule_service = runtime.ark.schedule_service
            if controller is None:
                return self.result.fail(
                    self.result.issue(
                        "pause_controller_missing",
                        "ARK pause controller is not configured.",
                        object_ref=record.repo_key,
                    )
                )
            if schedule_service is None:
                return self.result.fail(
                    self.result.issue(
                        "schedule_service_missing",
                        "ARK schedule service is not configured.",
                        object_ref=record.repo_key,
                    )
                )
            if budget is not None:
                admission = self._check_bounded_resume_admission(record)
                if not admission.ok:
                    return self.result.fail(admission.issues)
            was_paused = bool(controller.is_paused(None)) if hasattr(controller, "is_paused") else False
            try:
                if budget is None:
                    schedule_service.clear_run_budget()
                else:
                    schedule_service.configure_run_budget(budget)
                if rebuild_queues:
                    schedule_service.rebuild_candidate_queues(scope_id=None)
                controller.resume(None)
            except Exception as exc:  # noqa: BLE001 - registry mutation boundary.
                if was_paused:
                    controller.pause(None)
                else:
                    controller.resume(None)
                if hasattr(schedule_service, "clear_run_budget"):
                    schedule_service.clear_run_budget(reason="resume_failed")
                record.state = "paused" if _runtime_paused(runtime) else "active"
                return self.result.fail(
                    self.result.issue(
                        "repo_runtime_resume_failed",
                        f"Failed to resume repo runtime: {exc}",
                        object_ref=record.repo_key,
                    )
                )
            record.state = "paused" if _runtime_paused(runtime) else "active"
            return self.result.ok(self._status_for_record(record))

    def unload(self, repo_key: str, *, require_stable: bool = True) -> ServiceResult[RepoRuntimeStatusView]:
        discovered = self.discover_repo(repo_key)
        if not discovered.ok or discovered.value is None:
            return self.result.fail(discovered.issues)
        record = discovered.value
        with record.lock:
            if record.runtime is None:
                record.state = "unloaded"
                return self.result.ok(self._status_for_record(record))
            if require_stable:
                stable = self._check_stable(record)
                if not stable.ok:
                    return self.result.fail(stable.issues)
            record.runtime = None
            record.agent_homes = None
            record.state = "unloaded"
            return self.result.ok(self._status_for_record(record))

    def shutdown_all(self) -> None:
        with self._lock:
            records = list(self._records.values())
            self._workspace_runtime = None
        for record in records:
            with record.lock:
                record.runtime = None
                record.agent_homes = None
                if record.state != "failed":
                    record.state = "unloaded"

    def mark_failed(self, repo_key: str, exc: BaseException) -> None:
        discovered = self.discover_repo(repo_key)
        if not discovered.ok or discovered.value is None:
            return
        record = discovered.value
        with record.lock:
            record.state = "failed"
            record.last_error = str(exc)

    def _get_or_load_record(
        self,
        record: RepoRuntimeRecord,
        *,
        refresh_homes: bool,
        start_paused: bool,
    ) -> ServiceResult[LeanRuntimeServices]:
        """Load one record while its lock is held by the caller."""

        if record.runtime is not None:
            if refresh_homes and self.config.materialize_agent_homes:
                refreshed = self._materialize_homes(record)
                if not refreshed.ok:
                    record.state = "failed"
                    record.last_error = _issue_summary(refreshed.issues)
                    return self.result.fail(refreshed.issues)
            if record.state in {"unloaded", "loading", "failed"}:
                record.state = "paused" if _runtime_paused(record.runtime) else "active"
            return self.result.ok(record.runtime)
        record.state = "loading"
        record.last_error = None
        try:
            record.runtime_root.mkdir(parents=True, exist_ok=True)
            runtime = create_app_runtime_services(
                runtime_root=record.runtime_root,
                external_config=self.external_config,
                external_overrides=self.external_overrides,
                agent_providers=self.agent_providers,
                native_lake_project_config=self.config.native_lake_project,
                workspace_config=self.config.workspace_config,
                max_concurrent_flow_advances=self.config.max_concurrent_flow_advances,
                max_concurrent_steps=self.config.max_concurrent_steps,
                start_paused=start_paused,
                test_control_enabled=self.config.test_control_enabled,
                automatic_checkpoints=self.config.automatic_checkpoints,
                agent_trace_reports=self.config.agent_trace_reports,
            )
            record.runtime = runtime
            self._rebuild_queues(record)
            self._audit_release_state(record)
            self._audit_content_checkpoint_parallelism(record)
            if self.config.materialize_agent_homes:
                homes = self._materialize_homes(record)
                if not homes.ok:
                    record.runtime = None
                    record.state = "failed"
                    record.last_error = _issue_summary(homes.issues)
                    return self.result.fail(homes.issues)
            record.state = "paused" if _runtime_paused(runtime) else "active"
            return self.result.ok(runtime)
        except Exception as exc:  # noqa: BLE001 - registry load boundary.
            record.runtime = None
            record.state = "failed"
            record.last_error = str(exc)
            return self.result.fail(
                self.result.issue(
                    "repo_runtime_load_failed",
                    f"Failed to load repo runtime: {exc}",
                    object_ref=str(record.repo_root),
                )
            )

    def _materialize_homes(self, record: RepoRuntimeRecord) -> ServiceResult[ProductionAgentHomesView]:
        if record.runtime is None:
            return self.result.fail(
                self.result.issue(
                    "repo_runtime_not_loaded",
                    "Cannot materialize Agent homes before repo runtime is loaded.",
                    object_ref=record.repo_key,
                )
            )
        result = materialize_production_agent_homes(
            record.runtime,
            mcp_http_base_url=self.repo_mcp_http_base_url(record.repo_key),
            base_config_path=self.config.codex_base_config_path,
            auth_json_path=self.config.codex_auth_json_path,
            shared_elan_home=self.config.shared_elan_home,
        )
        if result.ok and result.value is not None:
            record.agent_homes = result.value
        return result

    def repo_mcp_http_base_url(self, repo_key: str) -> str:
        normalized = self.normalize_repo_key(repo_key)
        if not normalized.ok or normalized.value is None:
            raise ValueError(_issue_summary(normalized.issues))
        return f"{self.config.production_mcp_http_effective_base_url().rstrip('/')}/repos/{normalized.value}"

    def _rebuild_queues(self, record: RepoRuntimeRecord) -> None:
        runtime = record.runtime
        if runtime is None:
            return
        schedule_service = runtime.ark.schedule_service
        if schedule_service is not None and hasattr(schedule_service, "rebuild_candidate_queues"):
            schedule_service.rebuild_candidate_queues()

    def _audit_content_checkpoint_parallelism(self, record: RepoRuntimeRecord) -> None:
        if not self.config.automatic_checkpoints.content_task_progress_enabled:
            return
        runtime = record.runtime
        if runtime is None:
            return
        parallelism_values: set[int] = set()
        try:
            flows = runtime.list_flows()
        except Exception as exc:  # noqa: BLE001 - startup warning audit is advisory.
            record.startup_warnings.append(f"content_checkpoint_startup_audit_failed: {exc}")
            return
        for flow in flows:
            input_model = getattr(flow, "input", None)
            run_context = getattr(input_model, "run_context", None)
            run_spec = getattr(run_context, "run_spec", None)
            value = getattr(run_spec, "max_parallel_content_node_tasks", None)
            if value is None and getattr(flow, "flow_type", None) == "content_node_task":
                value = getattr(input_model, "max_parallel_content_node_tasks", None)
            if value is not None and int(value) != 1:
                parallelism_values.add(int(value))
        for value in sorted(parallelism_values):
            record.startup_warnings.append(
                "content_task_progress_checkpoint_skipped: "
                f"max_parallel_content_node_tasks={value}; internal checkpoints require 1"
            )

    def _audit_release_state(self, record: RepoRuntimeRecord) -> None:
        """Run the startup release audit without repairing or deleting truth."""
        runtime = record.runtime
        if runtime is None:
            return
        try:
            audit = runtime.validation_snapshot.audit_repo_release_storage(record.repo_root)
        except Exception as exc:  # noqa: BLE001 - startup audit is advisory.
            record.startup_warnings = [f"release_startup_audit_failed: {exc}"]
            return
        if not audit.ok or audit.value is None:
            record.startup_warnings = [
                f"{issue.kind}: {issue.message}" for issue in audit.issues
            ]
            return
        record.startup_warnings = list(audit.value.issues)
        if audit.value.staging_paths:
            record.startup_warnings.append(
                f"release_orphan_staging: {len(audit.value.staging_paths)} staging path(s) require explicit cleanup"
            )

    def _check_stable(self, record: RepoRuntimeRecord) -> ServiceResult[None]:
        runtime = record.runtime
        if runtime is None:
            return self.result.ok(None)
        running_agents = []
        if runtime.ark.agent_service is not None and hasattr(runtime.ark.agent_service, "list_running_agents"):
            running_agents = list(runtime.ark.agent_service.list_running_agents())
        running_steps = []
        if runtime.ark.step_service is not None and hasattr(runtime.ark.step_service, "list_running_steps"):
            running_steps = list(runtime.ark.step_service.list_running_steps())
        if running_agents or running_steps:
            return self.result.fail(
                self.result.issue(
                    "repo_runtime_not_stable",
                    "Repo runtime has running agents or steps and cannot be unloaded.",
                    object_ref=record.repo_key,
                    details={
                        "running_agents": ",".join(getattr(agent, "agent_id", str(agent)) for agent in running_agents),
                        "running_steps": ",".join(getattr(step, "step_id", str(step)) for step in running_steps),
                    },
                )
            )
        return self.result.ok(None)

    def _status_for_record(self, record: RepoRuntimeRecord) -> RepoRuntimeStatusView:
        runtime = record.runtime
        paused = _runtime_paused(runtime) if runtime is not None else None
        run_control = None
        flow_count = step_count = agent_count = None
        if runtime is not None:
            try:
                schedule_service = runtime.ark.schedule_service
                if schedule_service is not None and hasattr(schedule_service, "get_run_control_view"):
                    run_control = schedule_service.get_run_control_view()
            except Exception:  # noqa: BLE001 - status should be best-effort.
                run_control = None
            try:
                flow_count = len(runtime.list_flows())
            except Exception:  # noqa: BLE001 - status should be best-effort.
                flow_count = None
            try:
                step_count = len(runtime.list_steps())
            except Exception:  # noqa: BLE001 - status should be best-effort.
                step_count = None
            try:
                agent_service = runtime.ark.agent_service
                agent_count = len(list(agent_service.list_agents())) if hasattr(agent_service, "list_agents") else None
            except Exception:  # noqa: BLE001 - status should be best-effort.
                agent_count = None
        return RepoRuntimeStatusView(
            repo_key=record.repo_key,
            repo_root=str(record.repo_root),
            runtime_root=str(record.runtime_root),
            state=record.state,
            loaded=record.loaded,
            paused=paused,
            run_control=run_control,
            flow_count=flow_count,
            step_count=step_count,
            agent_count=agent_count,
            agent_homes=record.agent_homes,
            last_error=record.last_error,
            startup_warnings=list(record.startup_warnings),
            summary=f"Repo runtime {record.repo_key} is {record.state}.",
        )


def _runtime_paused(runtime: LeanRuntimeServices | None) -> bool:
    if runtime is None or runtime.ark.pause_controller is None:
        return False
    controller = runtime.ark.pause_controller
    return bool(controller.is_paused()) if hasattr(controller, "is_paused") else False


def _issue_summary(issues: list[ServiceIssue]) -> str:
    return "; ".join(issue.message for issue in issues) or "Unknown repo runtime registry error."


__all__ = [
    "RepoRuntimeListView",
    "RepoRuntimeRecord",
    "RepoRuntimeRegistry",
    "RepoRuntimeState",
    "RepoRuntimeStatusView",
]
