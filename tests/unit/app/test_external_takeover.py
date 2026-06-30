from __future__ import annotations

import json
from pathlib import Path
from time import monotonic, sleep

from agent_runtime_kit.agent.homes import HomeCreateSpec

from lean_constellation.app import (
    ExternalTakeoverCompleteInput,
    ExternalTakeoverToolListInput,
    LeanAdminApi,
    build_external_takeover_agent_providers,
    complete_external_takeover_handoff,
    create_app_runtime_services,
    list_external_takeover_handoffs,
    list_external_takeover_tools,
)


def test_external_takeover_helper_writes_completion_and_lists_handoffs(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".runtime"
    handoff_dir = runtime_root / "external_turns" / "h_test"
    handoff_dir.mkdir(parents=True)
    (handoff_dir / "handoff.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "handoff_id": "h_test",
                "status": "pending",
                "home_id": "ResourceCuratorAgent",
                "agent_id": "agent_1",
                "thread_id": None,
            }
        ),
        encoding="utf-8",
    )

    view = complete_external_takeover_handoff(
        runtime_root,
        ExternalTakeoverCompleteInput(
            handoff_id="h_test",
            final_response="completed by test",
            thread_id="thread_test",
            metadata={"case": "unit"},
        ),
    )
    listed = list_external_takeover_handoffs(runtime_root)
    completion = json.loads((handoff_dir / "completion.json").read_text(encoding="utf-8"))

    assert view.status == "completed"
    assert view.thread_id == "thread_test"
    assert completion["metadata"] == {"case": "unit"}
    assert [item.handoff_id for item in listed] == ["h_test"]
    assert listed[0].status == "completed"


def test_admin_can_complete_external_takeover_handoff(tmp_path: Path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    handoff_dir = tmp_path / ".runtime" / "external_turns" / "h_admin"
    handoff_dir.mkdir(parents=True)
    (handoff_dir / "handoff.json").write_text(
        json.dumps({"schema_version": 1, "handoff_id": "h_admin", "status": "pending"}),
        encoding="utf-8",
    )
    admin = LeanAdminApi(runtime)

    pending = admin.list_external_takeovers(status="pending")
    completed = admin.complete_external_takeover(
        ExternalTakeoverCompleteInput(handoff_id="h_admin", final_response="done")
    )

    assert pending.ok
    assert pending.value is not None
    assert [item.handoff_id for item in pending.value] == ["h_admin"]
    assert completed.ok
    assert completed.value is not None
    assert completed.value.status == "completed"


def test_external_takeover_tool_listing_uses_handoff_view_env(tmp_path: Path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    handoff_dir = tmp_path / ".runtime" / "external_turns" / "h_tools"
    handoff_dir.mkdir(parents=True)
    (handoff_dir / "handoff.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "handoff_id": "h_tools",
                "status": "pending",
                "env": {
                    "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": "repo_format_discovery",
                    "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "repo_format_discovery_submit",
                },
            }
        ),
        encoding="utf-8",
    )
    input_model = ExternalTakeoverToolListInput(handoff_id="h_tools", view_kind="submit")

    tools = list_external_takeover_tools(runtime, tmp_path / ".runtime", input_model)
    admin_tools = LeanAdminApi(runtime).list_external_takeover_tools(input_model)

    assert "submit_native_repo_choice" in {tool.name for tool in tools}
    assert admin_tools.ok
    assert admin_tools.value is not None
    assert "submit_adapter_repo_choice" in {tool.name for tool in admin_tools.value}


def test_external_takeover_provider_integrates_with_agent_service(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".runtime"
    runtime = create_app_runtime_services(
        runtime_root=runtime_root,
        agent_providers=build_external_takeover_agent_providers(
            runtime_root,
            poll_interval_s=0.01,
            default_timeout_s=2,
        ),
    )
    agent_service = runtime.ark.agent_service
    agent_service.home_service.create_home(
        HomeCreateSpec(
            cli_type="codex",
            home_id="ResourceCuratorAgent",
            fixed_env={"LEAN_CONSTELLATION_AGENT_TYPE": "ResourceCuratorAgent"},
        )
    )
    agent = agent_service.create_agent(
        "repo:Repo",
        "ResourceCuratorAgent",
        home_id="ResourceCuratorAgent",
    )

    agent_service.start_agent(
        agent.agent_id,
        variables={"goal": "controlled"},
        prompt="Use the controlled handoff.",
        env={"ARK_AGENT_ID": agent.agent_id},
        workdir=str(tmp_path),
    )
    handoff_path = _wait_for_handoff(runtime_root)
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    complete_external_takeover_handoff(
        runtime_root,
        ExternalTakeoverCompleteInput(
            handoff_id=handoff["handoff_id"],
            final_response="external controller completed the turn",
            thread_id="thread_external",
        ),
    )

    result = agent_service.wait_agent(agent.agent_id, timeout_s=2)

    assert handoff["agent_id"] == agent.agent_id
    assert handoff["prompt"] == "Use the controlled handoff."
    assert agent_service.get_agent(agent.agent_id).thread_id == "thread_external"
    assert result.final_response == "external controller completed the turn"


def _wait_for_handoff(runtime_root: Path) -> Path:
    deadline = monotonic() + 2
    while monotonic() < deadline:
        matches = list((runtime_root / "external_turns").glob("*/handoff.json"))
        if matches:
            return matches[0]
        sleep(0.01)
    raise TimeoutError("external handoff was not written")
