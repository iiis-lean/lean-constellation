from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from lean_constellation.app.semantic_watch import SemanticWatchOptions, SemanticWatcher


def _lease_payload(
    *,
    status: str,
    version: int,
    steps: list[dict],
    content_flow_id: str | None = "flow-1",
    current_agent_id: str | None = None,
    running_step_ids: list[str] | None = None,
) -> dict:
    return {
        "ok": True,
        "value": {
            "lease": {
                "lease_id": "lease-1",
                "version": version,
                "status": status,
                "terminal_reason": "content_child_closed:flow-1" if status == "terminal" else None,
            },
            "started_steps": steps,
            "current_content_task_flow_id": content_flow_id,
            "current_agent_id": current_agent_id,
            "runtime": {"running_step_ids": running_step_ids or []},
        },
        "issues": [],
    }


def test_semantic_watcher_runs_step_lease_and_business_progress_to_completion() -> None:
    events: list[dict] = []
    calls: list[tuple[str, float]] = []
    step = {"step_id": "step-1", "step_type": "logic", "bound_agent_id": None}

    def request_json(_method: str, url: str, _payload: dict | None, timeout_s: float) -> dict:
        calls.append((url, timeout_s))
        if url.endswith("/runtime/leases/lease-1"):
            return _lease_payload(status="active", version=1, steps=[step])
        if "/steps/step-1/wait?" in url:
            return {
                "ok": True,
                "value": {
                    "terminal": True,
                    "timed_out": False,
                    "runner_state": "settled",
                    "step": {**step, "status": "completed", "error_type": None},
                },
                "issues": [],
            }
        if "/runtime/leases/lease-1/wait?" in url:
            return _lease_payload(status="terminal", version=2, steps=[step])
        if url.endswith("/content-tasks/flow-1/progress"):
            return {
                "ok": True,
                "value": {
                    "task_status": "running",
                    "phase": "callback_plan_agent",
                    "round_status": "completed",
                    "latest_content_progress_checkpoint_id": "checkpoint-1",
                    "summary": "Stable business truth.",
                },
                "issues": [],
            }
        raise AssertionError(url)

    watcher = SemanticWatcher(
        SemanticWatchOptions(
            admin_base_url="http://admin.test",
            repo_key="Repo",
            lease_id="lease-1",
            wait_s=5,
        ),
        request_json=request_json,
        emit=events.append,
    )

    exit_code = watcher.run()

    assert exit_code == 0
    assert [event["event"] for event in events] == [
        "watch_started",
        "lease_changed",
        "step_started",
        "step_terminal",
        "lease_changed",
        "lease_terminal",
        "business_progress",
        "watch_completed",
    ]
    assert any("/steps/step-1/wait?" in url and timeout == 15 for url, timeout in calls)
    assert events[-1]["content_task_progress"]["latest_content_progress_checkpoint_id"] == "checkpoint-1"


def test_semantic_watcher_verbose_mode_preserves_opaque_agent_cursor() -> None:
    events: list[dict] = []
    step_wait_count = 0
    step = {"step_id": "step-agent", "step_type": "agent", "bound_agent_id": "agent-1"}

    def request_json(_method: str, url: str, _payload: dict | None, _timeout_s: float) -> dict:
        nonlocal step_wait_count
        if url.endswith("/runtime/leases/lease-1"):
            return _lease_payload(status="active", version=1, steps=[step], content_flow_id=None)
        if "/steps/step-agent/wait?" in url:
            step_wait_count += 1
            if step_wait_count == 1:
                return {
                    "ok": True,
                    "value": {"terminal": False, "timed_out": True, "runner_state": "active", "step": step},
                    "issues": [],
                }
            return {
                "ok": True,
                "value": {
                    "terminal": True,
                    "timed_out": False,
                    "runner_state": "settled",
                    "step": {**step, "status": "completed", "error_type": None},
                },
                "issues": [],
            }
        if "/agents/agent-1/live?" in url:
            return {
                "ok": True,
                "value": {
                    "next_cursor": "opaque-cursor",
                    "agent": {"status": "running"},
                    "delta_events": [{"event": "progress"}],
                    "delta_tool_calls": [],
                    "latest_response_available": False,
                },
                "issues": [],
            }
        if "/runtime/leases/lease-1/wait?" in url:
            return _lease_payload(status="terminal", version=2, steps=[step], content_flow_id=None)
        raise AssertionError(url)

    watcher = SemanticWatcher(
        SemanticWatchOptions(
            admin_base_url="http://admin.test",
            repo_key="Repo",
            lease_id="lease-1",
            activity="verbose",
            wait_s=1,
        ),
        request_json=request_json,
        emit=events.append,
    )

    assert watcher.run() == 0
    assert watcher.state.agent_cursors == {"agent-1": "opaque-cursor"}
    assert "agent_changed" in [event["event"] for event in events]


def test_semantic_watcher_refreshes_running_agent_step_binding_before_step_started() -> None:
    events: list[dict] = []
    initial_step = {
        "step_id": "statement-nl-worker",
        "step_type": "decl_stage_worker_agent_step",
        "state_type": "agent_step",
        "status": "running",
        "agent_type": "StatementNLWorkerAgent",
        "bound_agent_id": None,
    }
    bound_step = {**initial_step, "bound_agent_id": "agent-from-step"}

    def request_json(_method: str, url: str, _payload: dict | None, _timeout_s: float) -> dict:
        if url.endswith("/runtime/leases/lease-1"):
            return _lease_payload(
                status="active",
                version=1,
                steps=[initial_step],
                content_flow_id=None,
                current_agent_id="agent-from-lease",
                running_step_ids=["statement-nl-worker"],
            )
        if url.endswith("/steps/statement-nl-worker"):
            return {"ok": True, "value": bound_step, "issues": []}
        if "/steps/statement-nl-worker/wait?" in url:
            return {
                "ok": True,
                "value": {
                    "terminal": True,
                    "timed_out": False,
                    "runner_state": "settled",
                    "step": {**bound_step, "status": "completed", "error_type": None},
                },
                "issues": [],
            }
        if "/runtime/leases/lease-1/wait?" in url:
            return _lease_payload(
                status="terminal",
                version=2,
                steps=[bound_step],
                content_flow_id=None,
            )
        raise AssertionError(url)

    watcher = SemanticWatcher(
        SemanticWatchOptions(
            admin_base_url="http://admin.test",
            repo_key="Repo",
            lease_id="lease-1",
            wait_s=1,
        ),
        request_json=request_json,
        emit=events.append,
    )

    assert watcher.run() == 0
    started = next(event for event in events if event["event"] == "step_started")
    assert started["agent_id"] == "agent-from-step"


def test_semantic_watcher_uses_lease_agent_only_for_current_running_agent_step() -> None:
    events: list[dict] = []
    historical = {
        "step_id": "historical-agent-step",
        "step_type": "agent_step",
        "state_type": "agent_step",
        "status": "completed",
        "agent_type": "WorkerAgent",
        "bound_agent_id": None,
    }
    current = {
        "step_id": "current-agent-step",
        "step_type": "agent_step",
        "state_type": "agent_step",
        "status": "running",
        "agent_type": "ReviewerAgent",
        "bound_agent_id": None,
    }

    def request_json(_method: str, url: str, _payload: dict | None, _timeout_s: float) -> dict:
        if url.endswith("/runtime/leases/lease-1"):
            return _lease_payload(
                status="active",
                version=1,
                steps=[historical, current],
                content_flow_id=None,
                current_agent_id="agent-from-lease",
                running_step_ids=["current-agent-step"],
            )
        if url.endswith("/steps/current-agent-step"):
            return {"ok": True, "value": current, "issues": []}
        if "/steps/historical-agent-step/wait?" in url:
            return {
                "ok": True,
                "value": {
                    "terminal": True,
                    "timed_out": False,
                    "runner_state": "settled",
                    "step": {**historical, "error_type": None},
                },
                "issues": [],
            }
        if "/steps/current-agent-step/wait?" in url:
            return {
                "ok": True,
                "value": {
                    "terminal": True,
                    "timed_out": False,
                    "runner_state": "settled",
                    "step": {**current, "status": "completed", "error_type": None},
                },
                "issues": [],
            }
        if "/runtime/leases/lease-1/wait?" in url:
            return _lease_payload(
                status="terminal",
                version=2,
                steps=[historical, current],
                content_flow_id=None,
            )
        raise AssertionError(url)

    watcher = SemanticWatcher(
        SemanticWatchOptions(
            admin_base_url="http://admin.test",
            repo_key="Repo",
            lease_id="lease-1",
            wait_s=1,
        ),
        request_json=request_json,
        emit=events.append,
    )

    assert watcher.run() == 0
    started = [event for event in events if event["event"] == "step_started"]
    assert [event["agent_id"] for event in started] == [None, "agent-from-lease"]


def test_semantic_watcher_adopts_agent_binding_from_step_wait_after_start_race() -> None:
    events: list[dict] = []
    step_wait_count = 0
    initial_step = {
        "step_id": "late-bound-agent-step",
        "step_type": "agent_step",
        "state_type": "agent_step",
        "status": "running",
        "agent_type": "WorkerAgent",
        "bound_agent_id": None,
    }
    bound_step = {**initial_step, "bound_agent_id": "late-agent"}

    def request_json(_method: str, url: str, _payload: dict | None, _timeout_s: float) -> dict:
        nonlocal step_wait_count
        if url.endswith("/runtime/leases/lease-1"):
            return _lease_payload(
                status="active",
                version=1,
                steps=[initial_step],
                content_flow_id=None,
                running_step_ids=["late-bound-agent-step"],
            )
        if url.endswith("/steps/late-bound-agent-step"):
            return {"ok": True, "value": initial_step, "issues": []}
        if "/steps/late-bound-agent-step/wait?" in url:
            step_wait_count += 1
            if step_wait_count == 1:
                return {
                    "ok": True,
                    "value": {
                        "terminal": False,
                        "timed_out": True,
                        "runner_state": "active",
                        "step": bound_step,
                    },
                    "issues": [],
                }
            return {
                "ok": True,
                "value": {
                    "terminal": True,
                    "timed_out": False,
                    "runner_state": "settled",
                    "step": {**bound_step, "status": "completed", "error_type": None},
                },
                "issues": [],
            }
        if "/agents/late-agent/live?" in url:
            return {
                "ok": True,
                "value": {
                    "next_cursor": "late-agent-cursor",
                    "agent": {"status": "running"},
                    "delta_events": [],
                    "delta_tool_calls": [],
                    "latest_response_available": False,
                    "timed_out": True,
                },
                "issues": [],
            }
        if "/runtime/leases/lease-1/wait?" in url:
            return _lease_payload(
                status="terminal",
                version=2,
                steps=[bound_step],
                content_flow_id=None,
            )
        raise AssertionError(url)

    watcher = SemanticWatcher(
        SemanticWatchOptions(
            admin_base_url="http://admin.test",
            repo_key="Repo",
            lease_id="lease-1",
            activity="heartbeat",
            wait_s=1,
        ),
        request_json=request_json,
        emit=events.append,
    )

    assert watcher.run() == 0
    assert watcher.state.agent_cursors == {"late-agent": "late-agent-cursor"}
    assert any(event["event"] == "agent_changed" and event["agent_id"] == "late-agent" for event in events)


def test_semantic_watcher_reports_lease_lost_and_target_mismatch() -> None:
    lost_events: list[dict] = []
    lost = SemanticWatcher(
        SemanticWatchOptions(admin_base_url="http://admin.test", repo_key="Repo", lease_id="lease-1"),
        request_json=lambda *_args: {
            "ok": False,
            "value": None,
            "issues": [{"kind": "lease_lost", "message": "old process"}],
        },
        emit=lost_events.append,
    )
    mismatch_events: list[dict] = []
    mismatch = SemanticWatcher(
        SemanticWatchOptions(
            admin_base_url="http://admin.test",
            repo_key="Repo",
            lease_id="lease-1",
            content_task_flow_id="flow-explicit",
        ),
        request_json=lambda *_args: _lease_payload(status="terminal", version=1, steps=[]),
        emit=mismatch_events.append,
    )

    assert lost.run() == 3
    assert lost_events[-1]["event"] == "recovery_required"
    assert lost_events[-1]["resume_token"]["lease_id"] == "lease-1"
    assert mismatch.run() == 2
    assert mismatch_events[-1]["event"] == "watch_failed"


def test_semantic_watcher_summary_mode_only_emits_final_record() -> None:
    events: list[dict] = []
    watcher = SemanticWatcher(
        SemanticWatchOptions(
            admin_base_url="http://admin.test",
            repo_key="Repo",
            lease_id="lease-1",
            output="summary",
        ),
        request_json=lambda *_args: _lease_payload(
            status="terminal",
            version=1,
            steps=[],
            content_flow_id=None,
        ),
        emit=events.append,
    )

    assert watcher.run() == 0
    assert [event["event"] for event in events] == ["watch_completed"]


def test_semantic_watcher_retries_bounded_http_failures() -> None:
    events: list[dict] = []
    calls = 0

    def request_json(*_args) -> dict:
        nonlocal calls
        calls += 1
        return {
            "ok": False,
            "value": None,
            "issues": [{"kind": "admin_http_request_failed", "message": "server unavailable"}],
        }

    watcher = SemanticWatcher(
        SemanticWatchOptions(admin_base_url="http://admin.test", repo_key="Repo", lease_id="lease-1"),
        request_json=request_json,
        emit=events.append,
    )

    assert watcher.run() == 1
    assert calls == 3
    assert events[-1]["event"] == "watch_failed"
    assert events[-1]["resume_token"]["lease_id"] == "lease-1"


@pytest.mark.parametrize(
    ("issue_kind", "expected_exit"),
    [("step_not_found", 1), ("agent_live_cursor_invalid", 2)],
)
def test_semantic_watcher_classifies_unknown_step_and_invalid_agent_cursor(
    issue_kind: str,
    expected_exit: int,
) -> None:
    events: list[dict] = []
    step = {"step_id": "step-agent", "step_type": "agent", "bound_agent_id": "agent-1"}

    def request_json(_method: str, url: str, _payload: dict | None, _timeout_s: float) -> dict:
        if url.endswith("/runtime/leases/lease-1"):
            return _lease_payload(status="active", version=1, steps=[step], content_flow_id=None)
        if "/steps/step-agent/wait?" in url:
            if issue_kind == "step_not_found":
                return {
                    "ok": False,
                    "value": None,
                    "issues": [{"kind": issue_kind, "message": "unknown Step"}],
                }
            return {
                "ok": True,
                "value": {"terminal": False, "timed_out": True, "runner_state": "active", "step": step},
                "issues": [],
            }
        if "/agents/agent-1/live?" in url:
            return {
                "ok": False,
                "value": None,
                "issues": [{"kind": issue_kind, "message": "invalid opaque cursor"}],
            }
        raise AssertionError(url)

    watcher = SemanticWatcher(
        SemanticWatchOptions(
            admin_base_url="http://admin.test",
            repo_key="Repo",
            lease_id="lease-1",
            activity="verbose",
        ),
        request_json=request_json,
        emit=events.append,
    )

    assert watcher.run() == expected_exit
    assert events[-1]["event"] == "watch_failed"
    assert issue_kind in str(events[-1])


def test_semantic_watcher_emits_one_soft_stall_and_then_recovers() -> None:
    events: list[dict] = []
    step_waits = 0
    step = {"step_id": "slow-step", "step_type": "logic", "bound_agent_id": None}

    def request_json(_method: str, url: str, _payload: dict | None, _timeout_s: float) -> dict:
        nonlocal step_waits
        if url.endswith("/runtime/leases/lease-1"):
            return _lease_payload(status="active", version=1, steps=[step], content_flow_id=None)
        if "/steps/slow-step/wait?" in url:
            step_waits += 1
            if step_waits < 3:
                return {
                    "ok": True,
                    "value": {"terminal": False, "timed_out": True, "runner_state": "active", "step": step},
                    "issues": [],
                }
            return {
                "ok": True,
                "value": {
                    "terminal": True,
                    "timed_out": False,
                    "runner_state": "settled",
                    "step": {**step, "status": "completed", "error_type": None},
                },
                "issues": [],
            }
        if "/runtime/leases/lease-1/wait?" in url:
            return _lease_payload(status="terminal", version=2, steps=[step], content_flow_id=None)
        raise AssertionError(url)

    watcher = SemanticWatcher(
        SemanticWatchOptions(
            admin_base_url="http://admin.test",
            repo_key="Repo",
            lease_id="lease-1",
            wait_s=1,
            soft_stall_s=1e-9,
        ),
        request_json=request_json,
        emit=events.append,
    )

    assert watcher.run() == 0
    assert [event["event"] for event in events].count("soft_stall") == 1
    assert events[-1]["event"] == "watch_completed"


def test_semantic_watcher_ctrl_c_returns_resume_token_without_mutation() -> None:
    events: list[dict] = []

    def interrupt(*_args) -> dict:
        raise KeyboardInterrupt

    watcher = SemanticWatcher(
        SemanticWatchOptions(admin_base_url="http://admin.test", repo_key="Repo", lease_id="lease-1"),
        request_json=interrupt,
        emit=events.append,
    )

    assert watcher.run() == 130
    assert events[-1]["event"] == "watch_interrupted"
    assert events[-1]["resume_token"]["lease_id"] == "lease-1"


def test_semantic_watcher_re_attaches_to_started_steps_and_deduplicates_activity_bursts() -> None:
    events: list[dict] = []
    agent_live_urls: list[str] = []
    current_waits = 0
    historical = {"step_id": "historical-step", "step_type": "logic", "bound_agent_id": None}
    current = {"step_id": "current-step", "step_type": "agent", "bound_agent_id": "agent-1"}

    def terminal_step(step: dict) -> dict:
        return {
            "ok": True,
            "value": {
                "terminal": True,
                "timed_out": False,
                "runner_state": "settled",
                "step": {**step, "status": "completed", "error_type": None},
            },
            "issues": [],
        }

    def request_json(_method: str, url: str, _payload: dict | None, _timeout_s: float) -> dict:
        nonlocal current_waits
        if url.endswith("/runtime/leases/lease-1"):
            return _lease_payload(
                status="active",
                version=7,
                steps=[historical, current],
                content_flow_id=None,
            )
        if "/steps/historical-step/wait?" in url:
            return terminal_step(historical)
        if "/steps/current-step/wait?" in url:
            current_waits += 1
            if current_waits <= 3:
                return {
                    "ok": True,
                    "value": {"terminal": False, "timed_out": True, "runner_state": "active", "step": current},
                    "issues": [],
                }
            return terminal_step(current)
        if "/agents/agent-1/live?" in url:
            agent_live_urls.append(url)
            cursor = f"opaque-{len(agent_live_urls)}"
            return {
                "ok": True,
                "value": {
                    "next_cursor": cursor,
                    "agent": {"status": "running"},
                    "delta_events": [{"event": "progress"}],
                    "delta_tool_calls": [],
                    "latest_response_available": False,
                },
                "issues": [],
            }
        if "/runtime/leases/lease-1/wait?" in url:
            return _lease_payload(
                status="terminal",
                version=8,
                steps=[historical, current],
                content_flow_id=None,
            )
        raise AssertionError(url)

    watcher = SemanticWatcher(
        SemanticWatchOptions(
            admin_base_url="http://admin.test",
            repo_key="Repo",
            lease_id="lease-1",
            activity="verbose",
            wait_s=1,
        ),
        request_json=request_json,
        emit=events.append,
    )

    assert watcher.run() == 0
    assert [event["step_id"] for event in events if event["event"] == "step_started"] == [
        "historical-step",
        "current-step",
    ]
    assert [event["event"] for event in events].count("agent_changed") == 1
    assert [event["event"] for event in events].count("agent_heartbeat") == 1
    assert len(agent_live_urls) == 3
    assert "after_cursor" not in parse_qs(urlparse(agent_live_urls[0]).query)
    assert parse_qs(urlparse(agent_live_urls[1]).query)["after_cursor"] == ["opaque-1"]
    assert parse_qs(urlparse(agent_live_urls[2]).query)["after_cursor"] == ["opaque-2"]


def test_semantic_watcher_overall_timeout_is_classified() -> None:
    events: list[dict] = []
    watcher = SemanticWatcher(
        SemanticWatchOptions(
            admin_base_url="http://admin.test",
            repo_key="Repo",
            lease_id="lease-1",
            timeout_s=1e-9,
        ),
        request_json=lambda *_args: _lease_payload(
            status="active",
            version=1,
            steps=[],
            content_flow_id=None,
        ),
        emit=events.append,
    )

    assert watcher.run() == 1
    assert events[-1]["event"] == "watch_failed"
    assert "overall timeout" in events[-1]["summary"]


def test_semantic_watcher_agent_progress_resets_soft_stall_clock(monkeypatch) -> None:
    events: list[dict] = []
    timed_out = False

    def request_json(*_args) -> dict:
        return {
            "ok": True,
            "value": {
                "next_cursor": "opaque-next",
                "agent": {"status": "running"},
                "delta_events": [] if timed_out else [{"event": "progress"}],
                "delta_tool_calls": [],
                "latest_response_available": False,
                "timed_out": timed_out,
            },
            "issues": [],
        }

    watcher = SemanticWatcher(
        SemanticWatchOptions(
            admin_base_url="http://admin.test",
            repo_key="Repo",
            lease_id="lease-1",
            activity="verbose",
        ),
        request_json=request_json,
        emit=events.append,
    )
    watcher.state.current_step_id = "step-1"
    watcher.state.last_progress_at = 1
    watcher.state.emitted_stalls.add("step-1")
    monkeypatch.setattr("lean_constellation.app.semantic_watch.monotonic", lambda: 10)

    assert watcher._observe_agent("agent-1") is None  # noqa: SLF001 - focused state-machine contract.
    assert watcher.state.last_progress_at == 10
    assert "step-1" not in watcher.state.emitted_stalls

    timed_out = True
    monkeypatch.setattr("lean_constellation.app.semantic_watch.monotonic", lambda: 20)
    assert watcher._observe_agent("agent-1") is None  # noqa: SLF001 - focused state-machine contract.
    assert watcher.state.last_progress_at == 10
