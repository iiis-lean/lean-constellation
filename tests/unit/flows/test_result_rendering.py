from __future__ import annotations

from agent_runtime_kit.flow.rendering import RenderContext
from agent_runtime_kit.runtime import ARKServices, AppServices

from lean_constellation.flows.content_node_task.flows import ContentNodeTaskInput, ContentNodeTaskResult
from lean_constellation.flows.resource_request.flows import ResourceCallerContextInput, ResourceCurationInput, ResourceCurationResult, ResourceTargetInput


def _ctx() -> RenderContext:
    return RenderContext(ark=ARKServices(), app=AppServices(), scope_id="scope", viewer="agent")


def test_flow_input_rendering_hides_runtime_ids_and_paths() -> None:
    text = ContentNodeTaskInput(
        repo_key="Repo",
        repo_path="/tmp/Repo",
        node_path="Main.Core",
        contract_version=2,
        task_mode="first",
        summary="Run the current contract.",
    ).render_for_agent(_ctx())

    assert "Run content node task Main.Core" in text
    assert "Flow" not in text
    assert "/tmp/Repo" not in text
    assert "Contract Version: 2" in text


def test_flow_result_rendering_is_agent_facing_summary() -> None:
    text = ResourceCurationResult(
        outcome="local_resource_created",
        repo_key="Repo",
        resource_key="res_1",
        summary="Local resource registered.",
    ).render_for_agent(_ctx())

    assert "Resource Curation" in text
    assert "Outcome: local_resource_created" in text
    assert "Resource Key: res_1" in text


def test_nested_resource_input_rendering_shows_business_context() -> None:
    text = ResourceCurationInput(
        repo_key="Repo",
        target=ResourceTargetInput(kind="web", target="https://example.com/paper"),
        caller_context=ResourceCallerContextInput(caller_kind="content_plan", node_path="Main.Core", purpose_hint="Need lemma source."),
    ).render_for_agent(_ctx())

    assert "Curate resource https://example.com/paper" in text
    assert "Caller Kind: content_plan" in text
    assert "Node Path: Main.Core" in text


def test_content_node_task_result_rendering_shows_terminal_outcome() -> None:
    text = ContentNodeTaskResult(
        outcome="blocked",
        repo_key="Repo",
        node_path="Main.Core",
        reason="Missing source material.",
        summary="Task blocked.",
    ).render_for_agent(_ctx())

    assert "Outcome: blocked" in text
    assert "Reason: Missing source material." in text
