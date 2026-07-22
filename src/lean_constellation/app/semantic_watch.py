"""Read-only client state machine for one production semantic scheduler lease."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
from time import monotonic, sleep
from typing import Callable, Literal


RequestJson = Callable[[str, str, dict | None, float], dict]
EmitEvent = Callable[[dict], None]


@dataclass(frozen=True)
class SemanticWatchOptions:
    admin_base_url: str
    repo_key: str
    lease_id: str
    content_task_flow_id: str | None = None
    output: Literal["ndjson", "summary"] = "ndjson"
    activity: Literal["quiet", "heartbeat", "verbose"] = "quiet"
    wait_s: float = 30.0
    timeout_s: float | None = None
    soft_stall_s: float = 300.0


@dataclass
class SemanticWatchState:
    last_lease_version: int | None = None
    seen_step_ids: set[str] = field(default_factory=set)
    current_step_id: str | None = None
    current_agent_id: str | None = None
    agent_cursors: dict[str, str] = field(default_factory=dict)
    last_progress_at: float = field(default_factory=monotonic)
    emitted_stalls: set[str] = field(default_factory=set)


class SemanticWatcher:
    def __init__(
        self,
        options: SemanticWatchOptions,
        *,
        request_json: RequestJson,
        emit: EmitEvent,
    ) -> None:
        if options.wait_s <= 0 or options.wait_s > 300:
            raise ValueError("wait_s must be greater than 0 and at most 300 seconds")
        if options.timeout_s is not None and options.timeout_s <= 0:
            raise ValueError("timeout_s must be greater than 0")
        if options.soft_stall_s <= 0:
            raise ValueError("soft_stall_s must be greater than 0")
        self.options = options
        self.request_json = request_json
        self.emit = emit
        self.state = SemanticWatchState()
        self._started_at = monotonic()
        self._last_summary_digest: str | None = None

    def run(self) -> int:
        self._emit("watch_started", summary="Started read-only semantic lease watcher.")
        try:
            return self._run()
        except KeyboardInterrupt:
            self._emit(
                "watch_interrupted",
                summary="Watcher interrupted without changing runtime state.",
                resume_token=self._resume_token(),
                force=True,
            )
            return 130

    def _run(self) -> int:
        lease_payload = self._get_lease()
        if not lease_payload.get("ok"):
            return self._handle_issue_payload(lease_payload)

        while True:
            if self._overall_timed_out():
                self._emit(
                    "watch_failed",
                    summary="Watcher overall timeout expired before lease terminal.",
                    resume_token=self._resume_token(),
                    force=True,
                )
                return 1
            lease_view = lease_payload["value"]
            target_check = self._validate_content_target(lease_view)
            if target_check is not None:
                return target_check
            lease = lease_view["lease"]
            version = int(lease["version"])
            if version != self.state.last_lease_version:
                self.state.last_lease_version = version
                self.state.last_progress_at = monotonic()
                self._emit(
                    "lease_changed",
                    lease_version=version,
                    lease_status=lease["status"],
                    terminal_reason=lease.get("terminal_reason"),
                    summary=f"Lease advanced to version {version} ({lease['status']}).",
                )

            for step in lease_view.get("started_steps", []):
                step_id = str(step["step_id"])
                if step_id in self.state.seen_step_ids:
                    continue
                self.state.seen_step_ids.add(step_id)
                self.state.current_step_id = step_id
                self.state.current_agent_id = self._resolve_started_step_agent(step, lease_view)
                self.state.last_progress_at = monotonic()
                self._emit(
                    "step_started",
                    step_id=step_id,
                    step_type=step.get("step_type"),
                    agent_id=self.state.current_agent_id,
                    summary=f"Observing Step {step_id}.",
                )
                step_exit = self._wait_step(step_id)
                if step_exit is not None:
                    return step_exit

            if lease["status"] == "terminal":
                return self._finish_terminal(lease_view)

            lease_payload = self._request(
                "GET",
                self._url(
                    f"runtime/leases/{self.options.lease_id}/wait",
                    after_version=self.state.last_lease_version,
                    timeout_s=self.options.wait_s,
                ),
                transport_timeout=self.options.wait_s + 10,
            )
            if not lease_payload.get("ok"):
                return self._handle_issue_payload(lease_payload)

    def _wait_step(self, step_id: str) -> int | None:
        while True:
            payload = self._request(
                "GET",
                self._url(f"steps/{step_id}/wait", timeout_s=self.options.wait_s),
                transport_timeout=self.options.wait_s + 10,
            )
            if not payload.get("ok"):
                return self._handle_issue_payload(payload)
            waited = payload["value"]
            waited_step = waited.get("step") or {}
            waited_agent_id = waited_step.get("bound_agent_id")
            if waited_agent_id is not None:
                self.state.current_agent_id = waited_agent_id
            if waited.get("runner_state") == "lost":
                self._emit(
                    "recovery_required",
                    step_id=step_id,
                    runner_state="lost",
                    summary=f"Step {step_id} has persisted running truth without an active runner.",
                    resume_token=self._resume_token(),
                    force=True,
                )
                return 3
            if waited.get("terminal"):
                step = waited["step"]
                self.state.last_progress_at = monotonic()
                self._emit(
                    "step_terminal",
                    step_id=step_id,
                    step_status=step.get("status"),
                    error_type=step.get("error_type"),
                    summary=f"Step {step_id} settled as {step.get('status')}.",
                )
                self.state.current_step_id = None
                self.state.current_agent_id = None
                return None
            if self.options.activity != "quiet" and self.state.current_agent_id is not None:
                diagnostic_exit = self._observe_agent(self.state.current_agent_id)
                if diagnostic_exit is not None:
                    return diagnostic_exit
            if monotonic() - self.state.last_progress_at >= self.options.soft_stall_s:
                if step_id not in self.state.emitted_stalls:
                    self.state.emitted_stalls.add(step_id)
                    self._emit(
                        "soft_stall",
                        step_id=step_id,
                        agent_id=self.state.current_agent_id,
                        summary=f"Step {step_id} has no observed terminal progress within the soft-stall window.",
                    )
            if self._overall_timed_out():
                self._emit(
                    "watch_failed",
                    step_id=step_id,
                    summary="Watcher overall timeout expired while waiting for Step terminal.",
                    resume_token=self._resume_token(),
                    force=True,
                )
                return 1

    def _resolve_started_step_agent(self, step: dict, lease_view: dict) -> str | None:
        bound_agent_id = step.get("bound_agent_id")
        if bound_agent_id is not None:
            return str(bound_agent_id)

        step_id = str(step["step_id"])
        runtime = lease_view.get("runtime") or {}
        running_step_ids = {str(item) for item in runtime.get("running_step_ids", [])}
        is_running = step.get("status") == "running" or step_id in running_step_ids
        is_agent_step = bool(
            step.get("agent_type")
            or step.get("state_type") == "agent_step"
            or str(step.get("step_type", "")).endswith("agent_step")
        )
        if not is_running or not is_agent_step:
            return None

        refreshed_payload = self._request(
            "GET",
            self._url(f"steps/{step_id}"),
            transport_timeout=30,
        )
        if refreshed_payload.get("ok"):
            refreshed_step = refreshed_payload.get("value") or {}
            refreshed_agent_id = refreshed_step.get("bound_agent_id")
            if refreshed_agent_id is not None:
                return str(refreshed_agent_id)

        lease_agent_id = lease_view.get("current_agent_id")
        return str(lease_agent_id) if lease_agent_id is not None else None

    def _observe_agent(self, agent_id: str) -> int | None:
        cursor = self.state.agent_cursors.get(agent_id)
        wake_on = "activity" if self.options.activity == "verbose" else "response"
        payload = self._request(
            "GET",
            self._url(
                f"agents/{agent_id}/live",
                after_cursor=cursor,
                wait_s=0,
                wake_on=wake_on,
            ),
            transport_timeout=30,
        )
        if not payload.get("ok"):
            return self._handle_issue_payload(payload)
        live = payload["value"]
        self.state.agent_cursors[agent_id] = live["next_cursor"]
        if not live.get("timed_out", False):
            self.state.last_progress_at = monotonic()
            if self.state.current_step_id is not None:
                self.state.emitted_stalls.discard(self.state.current_step_id)
        event = "agent_changed" if cursor is None else "agent_heartbeat"
        self._emit(
            event,
            agent_id=agent_id,
            agent_status=live["agent"]["status"],
            wake_on=wake_on,
            event_count=len(live.get("delta_events", [])),
            tool_call_count=len(live.get("delta_tool_calls", [])),
            response_available=live.get("latest_response_available", False),
            summary=f"Observed Agent {agent_id} ({live['agent']['status']}).",
        )
        return None

    def _finish_terminal(self, lease_view: dict) -> int:
        lease = lease_view["lease"]
        reason = lease.get("terminal_reason")
        self._emit(
            "lease_terminal",
            lease_version=lease["version"],
            terminal_reason=reason,
            summary=f"Lease reached terminal state: {reason or 'unspecified'}.",
        )
        content_flow_id = self.options.content_task_flow_id or lease_view.get("current_content_task_flow_id")
        progress = None
        if content_flow_id is not None:
            progress_payload = self._request(
                "GET",
                self._url(f"content-tasks/{content_flow_id}/progress"),
                transport_timeout=30,
            )
            if not progress_payload.get("ok"):
                return self._handle_issue_payload(progress_payload)
            progress = progress_payload["value"]
            self._emit(
                "business_progress",
                content_task_flow_id=content_flow_id,
                task_status=progress.get("task_status"),
                phase=progress.get("phase"),
                round_status=progress.get("round_status"),
                checkpoint_id=progress.get("latest_content_progress_checkpoint_id"),
                summary=progress.get("summary", "Loaded ContentTask progress."),
            )

        failed_reason = reason in {
            "runtime_failure",
            "semantic_safety_cap_exhausted",
            "no_runnable_candidate",
            "run_control_cleared",
        }
        failed_progress = progress is not None and progress.get("task_status") in {"failed", "blocked"}
        exit_code = 1 if failed_reason or failed_progress else 0
        self._emit(
            "watch_completed",
            exit_code=exit_code,
            terminal_reason=reason,
            content_task_progress=progress,
            summary="Semantic lease observation completed." if exit_code == 0 else "Semantic lease requires review.",
            force=True,
        )
        return exit_code

    def _get_lease(self) -> dict:
        return self._request(
            "GET",
            self._url(f"runtime/leases/{self.options.lease_id}"),
            transport_timeout=30,
        )

    def _validate_content_target(self, lease_view: dict) -> int | None:
        explicit = self.options.content_task_flow_id
        observed = lease_view.get("current_content_task_flow_id")
        if explicit is not None and observed is not None and explicit != observed:
            self._emit(
                "watch_failed",
                summary=f"Explicit ContentTask {explicit} does not match lease target {observed}.",
                force=True,
            )
            return 2
        return None

    def _request(self, method: str, url: str, *, transport_timeout: float) -> dict:
        payload: dict = {}
        for attempt in range(3):
            payload = self.request_json(method, url, None, transport_timeout)
            kinds = {issue.get("kind") for issue in payload.get("issues", [])}
            if payload.get("ok") or not kinds.intersection(
                {"admin_http_request_failed", "admin_http_request_timeout"}
            ):
                return payload
            if attempt < 2:
                sleep(0.25 * (2**attempt))
        return payload

    def _handle_issue_payload(self, payload: dict) -> int:
        issues = payload.get("issues", [])
        kinds = {issue.get("kind") for issue in issues}
        if "lease_lost" in kinds:
            exit_code = 3
            event = "recovery_required"
        elif "agent_live_cursor_invalid" in kinds:
            exit_code = 2
            event = "watch_failed"
        else:
            exit_code = 1
            event = "watch_failed"
        self._emit(
            event,
            exit_code=exit_code,
            issues=issues,
            summary=issues[0].get("message", "Admin observation failed.") if issues else "Admin observation failed.",
            resume_token=self._resume_token(),
            force=True,
        )
        return exit_code

    def _overall_timed_out(self) -> bool:
        return self.options.timeout_s is not None and monotonic() - self._started_at >= self.options.timeout_s

    def _resume_token(self) -> dict:
        return {
            "v": 1,
            "repo_key": self.options.repo_key,
            "lease_id": self.options.lease_id,
            "lease_version": self.state.last_lease_version,
            "seen_step_ids": sorted(self.state.seen_step_ids),
            "current_step_id": self.state.current_step_id,
            "current_agent_id": self.state.current_agent_id,
            "agent_cursors": dict(self.state.agent_cursors),
        }

    def _url(self, suffix: str, **params: object) -> str:
        from urllib.parse import urlencode

        url = f"{self.options.admin_base_url.rstrip('/')}/admin/repos/{self.options.repo_key}/{suffix}"
        clean = {key: value for key, value in params.items() if value is not None}
        return url if not clean else f"{url}?{urlencode(clean)}"

    def _emit(self, event: str, *, force: bool = False, **payload: object) -> None:
        record = {
            "event": event,
            "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "repo_key": self.options.repo_key,
            "lease_id": self.options.lease_id,
            **payload,
        }
        digest_payload = {key: value for key, value in record.items() if key != "observed_at"}
        digest = hashlib.sha256(json.dumps(digest_payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        if not force and digest == self._last_summary_digest:
            return
        self._last_summary_digest = digest
        if self.options.output == "ndjson" or force:
            self.emit(record)
