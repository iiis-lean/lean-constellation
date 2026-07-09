from __future__ import annotations

from pathlib import Path

from lean_constellation.domain.preparation import RepoPreparationInput, RepoRequirementRef, SourceCorpusMode
from lean_constellation.services import create_test_runtime_services
from lean_constellation.services.tool_facade import RawToolCallContext, RuntimeToolContext


def _raw(repo_root: Path) -> RawToolCallContext:
    return RawToolCallContext(
        endpoint_view_key="repo_format_discovery",
        runtime_context=RuntimeToolContext(
            flow_id="flow_repo_format",
            step_id="step_repo_format",
            agent_id="agent_repo_format",
            agent_type="RepoFormatDiscoveryAgent",
            agent_role="coordinator",
            expected_view_key="repo_format_discovery",
            repo_root=repo_root,
        ),
    )


def _prepare_workspace(tmp_path: Path):
    runtime = create_test_runtime_services(register_application_tools=True)
    workspace = tmp_path / "workspace"
    provider = workspace / "Provider"
    consumer = workspace / "Consumer"
    provider.mkdir(parents=True)
    consumer.mkdir(parents=True)
    assert runtime.repo_workspace.metadata.ensure_repo_model(provider).ok
    assert runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    assert runtime.repo_workspace.requirement.create_requirement(
        consumer,
        name="need_provider",
        target_repo="Provider",
        source_description="Need a provider theorem.",
        reason="The consumer proof depends on this result.",
    ).ok
    assert runtime.repo_workspace.requirement.create_requirement(
        consumer,
        name="other_need",
        target_repo="OtherProvider",
        source_description="Outside the current provider preparation refs.",
        reason=None,
    ).ok
    assert runtime.repo_workspace.preparation.write_preparation_input(
        provider,
        input=RepoPreparationInput(
            goal="Prepare provider.",
            source_corpus_mode=SourceCorpusMode.PREPARE,
            requirement_refs=[RepoRequirementRef(consumer_repo="Consumer", requirement_name="need_provider")],
        ),
    ).ok
    return runtime, provider


def test_list_preparation_requirements_returns_only_current_refs(tmp_path: Path) -> None:
    runtime, provider = _prepare_workspace(tmp_path)

    result = runtime.tool_facade.invoke_agent_tool(
        _raw(provider),
        tool_name="list_preparation_requirements",
        flat_args={},
    )

    assert result.ok and result.value is not None
    assert result.value.ok is True
    value = result.value.value
    assert value is not None
    assert value["target_repo"] == "Provider"
    assert [item["requirement"]["name"] for item in value["requirements"]] == ["need_provider"]
    assert value["missing_refs"] == []


def test_get_preparation_requirement_rejects_refs_outside_current_input(tmp_path: Path) -> None:
    runtime, provider = _prepare_workspace(tmp_path)

    allowed = runtime.tool_facade.invoke_agent_tool(
        _raw(provider),
        tool_name="get_preparation_requirement",
        flat_args={"consumer_repo": "Consumer", "requirement_name": "need_provider"},
    )
    denied = runtime.tool_facade.invoke_agent_tool(
        _raw(provider),
        tool_name="get_preparation_requirement",
        flat_args={"consumer_repo": "Consumer", "requirement_name": "other_need"},
    )

    assert allowed.ok and allowed.value is not None
    assert allowed.value.ok is True
    assert allowed.value.value is not None
    assert allowed.value.value["requirement"]["name"] == "need_provider"
    assert denied.ok and denied.value is not None
    assert denied.value.ok is False
    assert denied.value.issues[0].kind == "preparation_requirement_ref_not_allowed"


def test_remote_github_file_tool_reports_invalid_path_without_generic_failure(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = runtime.tool_facade.invoke_agent_tool(
        _raw(repo_root),
        tool_name="read_github_repository_file",
        flat_args={"git_url": "owner/repo", "path": "../lakefile.lean"},
    )

    assert result.ok and result.value is not None
    assert result.value.ok is True
    assert result.value.value is not None
    assert result.value.value["issue_code"] == "invalid_github_path"
