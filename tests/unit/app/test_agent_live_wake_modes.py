from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.agent.homes import HomeCreateSpec
import pytest

from lean_constellation.app import LeanAdminApi, create_app_runtime_services


def _make_admin(tmp_path: Path) -> tuple[LeanAdminApi, str]:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    runtime.ark.agent_service.home_service.create_home(
        HomeCreateSpec(cli_type="codex", home_id="RepoFormatDiscoveryAgent")
    )
    agent = runtime.ark.agent_service.create_agent(
        "repo:Repo:node:Main",
        "RepoFormatDiscoveryAgent",
        home_id="RepoFormatDiscoveryAgent",
    )
    return LeanAdminApi(runtime), agent.agent_id


def _snapshot(admin: LeanAdminApi, agent_id: str, *, events: int = 0, responses: int = 0) -> dict:
    agent = admin.runtime.ark.agent_service.get_agent(agent_id)
    return {
        "agent": agent,
        "status": str(agent.status),
        "turns": 0,
        "events": events,
        "tool_calls": 0,
        "responses": responses,
        "turn_items": [],
        "tool_call_items": [],
        "latest_response": "response" if responses else None,
    }


def test_agent_live_wake_modes_use_distinct_predicates(tmp_path: Path, monkeypatch) -> None:
    admin, agent_id = _make_admin(tmp_path)
    baseline = admin._encode_agent_live_cursor(  # noqa: SLF001 - opaque cursor fixture.
        {"turns": 0, "events": 0, "tool_calls": 0, "responses": 0, "status": "idle"}
    )

    monkeypatch.setattr(admin, "_agent_live_snapshot", lambda _agent_id: _snapshot(admin, agent_id, events=1))
    activity = admin.get_agent_live(
        agent_id,
        repo_key="Repo",
        after_cursor=baseline,
        wait_s=0,
        wake_on="activity",
    )
    status = admin.get_agent_live(
        agent_id,
        repo_key="Repo",
        after_cursor=baseline,
        wait_s=0,
        wake_on="status",
    )
    response_to_activity = admin.get_agent_live(
        agent_id,
        repo_key="Repo",
        after_cursor=baseline,
        wait_s=0,
        wake_on="response",
    )

    monkeypatch.setattr(admin, "_agent_live_snapshot", lambda _agent_id: _snapshot(admin, agent_id, responses=1))
    response = admin.get_agent_live(
        agent_id,
        repo_key="Repo",
        after_cursor=baseline,
        wait_s=0,
        wake_on="response",
    )

    assert activity.ok and activity.value is not None
    assert activity.value.wake_on == "activity"
    assert activity.value.timed_out is False
    assert status.ok and status.value is not None
    assert status.value.wake_on == "status"
    assert status.value.timed_out is True
    assert response_to_activity.ok and response_to_activity.value is not None
    assert response_to_activity.value.wake_on == "response"
    assert response_to_activity.value.timed_out is True
    assert response.ok and response.value is not None
    assert response.value.wake_on == "response"
    assert response.value.timed_out is False


def test_agent_live_rejects_unknown_wake_mode(tmp_path: Path) -> None:
    admin, agent_id = _make_admin(tmp_path)

    result = admin.get_agent_live(agent_id, repo_key="Repo", wake_on="tool_terminal")  # type: ignore[arg-type]

    assert result.ok is False
    assert result.issues[0].kind == "agent_live_wake_invalid"


@pytest.mark.parametrize("wake_on", ["activity", "status", "response"])
def test_agent_live_wake_modes_preserve_cursor_timeout_status_and_accumulated_delta(
    tmp_path: Path,
    monkeypatch,
    wake_on: str,
) -> None:
    admin, agent_id = _make_admin(tmp_path)
    baseline = admin._encode_agent_live_cursor(  # noqa: SLF001 - opaque cursor fixture.
        {"turns": 0, "events": 0, "tool_calls": 0, "responses": 0, "status": "idle"}
    )
    admin.runtime.ark.agent_service.close_agent(agent_id)
    agent = admin.runtime.ark.agent_service.get_agent(agent_id)
    snapshot = {
        "agent": agent,
        "status": "closed",
        "turns": 2,
        "events": 2,
        "tool_calls": 2,
        "responses": 1,
        "turn_items": [{"turn": 1}, {"turn": 2}],
        "tool_call_items": [{"tool": 1}, {"tool": 2}],
        "latest_response": "finished",
    }
    monkeypatch.setattr(admin, "_agent_live_snapshot", lambda _agent_id: snapshot)
    monkeypatch.setattr(
        admin.runtime.ark.agent_service,
        "tail_trace_events",
        lambda _agent_id, *, limit: [{"event": index} for index in range(limit)],
    )

    changed = admin.get_agent_live(
        agent_id,
        repo_key="Repo",
        after_cursor=baseline,
        wait_s=0,
        wake_on=wake_on,  # type: ignore[arg-type]
    )
    assert changed.ok and changed.value is not None
    assert changed.value.wake_on == wake_on
    assert changed.value.agent.status == "closed"
    assert changed.value.timed_out is False
    assert changed.value.delta_turns == [{"turn": 1}, {"turn": 2}]
    assert changed.value.delta_events == [{"event": 0}, {"event": 1}]
    assert changed.value.delta_tool_calls == [{"tool": 1}, {"tool": 2}]
    assert changed.value.latest_response_available is True

    unchanged = admin.get_agent_live(
        agent_id,
        repo_key="Repo",
        after_cursor=changed.value.next_cursor,
        wait_s=0,
        wake_on=wake_on,  # type: ignore[arg-type]
    )
    assert unchanged.ok and unchanged.value is not None
    assert unchanged.value.timed_out is True
    assert unchanged.value.delta_turns == []
    assert unchanged.value.delta_events == []
    assert unchanged.value.delta_tool_calls == []
