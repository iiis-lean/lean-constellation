from __future__ import annotations

from pathlib import Path

import pytest

from lean_constellation.services import create_test_runtime_services
from lean_constellation.services.external_clients import (
    GitHubCodeSearchResult,
    GitHubLeanRepoProbeView,
    GitHubRepoCandidate,
    GitHubRepoSearchResult,
    GitHubRepositoryFileView,
    GitHubRepositoryTreeView,
)
from lean_constellation.services.tool_facade import RawToolCallContext, RuntimeToolContext


def _raw(repo_root: Path, *, agent_type: str, role: str, view_key: str) -> RawToolCallContext:
    return RawToolCallContext(
        endpoint_view_key=view_key,
        runtime_context=RuntimeToolContext(
            flow_id="flow_discovery",
            step_id="step_discovery",
            agent_id="agent_discovery",
            agent_type=agent_type,
            agent_role=role,
            expected_view_key=view_key,
            repo_root=repo_root,
        ),
    )


def _install_fake_github(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = GitHubRepoCandidate(
        full_name="owner/repo",
        html_url="https://github.com/owner/repo",
        primary_language="Lean",
        languages=["Lean"],
    )
    monkeypatch.setattr(
        runtime.external.github_repo,
        "search_repositories",
        lambda query, limit=10: GitHubRepoSearchResult(
            ok=True,
            query=query,
            candidates=[candidate],
        ),
    )
    monkeypatch.setattr(
        runtime.external.github_repo,
        "inspect_repository",
        lambda url_or_slug: candidate,
    )
    monkeypatch.setattr(
        runtime.external.github_repo,
        "get_repository",
        lambda git_url: candidate,
    )
    monkeypatch.setattr(
        runtime.external.github_repo,
        "probe_github_lean_repo_candidate",
        lambda git_url, **kwargs: GitHubLeanRepoProbeView(
            git_url=git_url,
            normalized_git_url="https://github.com/owner/repo",
            resolved_revision="a" * 40,
            is_lean_project=True,
            has_lakefile=True,
            has_lean_toolchain=True,
            adapter_candidate=True,
            evidence_summary="Verified fixture.",
        ),
    )
    monkeypatch.setattr(
        runtime.external.github_repo,
        "list_repository_tree",
        lambda git_url, **kwargs: GitHubRepositoryTreeView(
            git_url="https://github.com/owner/repo",
            resolved_revision="a" * 40,
        ),
    )
    monkeypatch.setattr(
        runtime.external.github_repo,
        "read_repository_file",
        lambda git_url, path, **kwargs: GitHubRepositoryFileView(
            git_url="https://github.com/owner/repo",
            path=path,
            content_excerpt="theorem fixture : True := by trivial\n",
        ),
    )
    monkeypatch.setattr(
        runtime.external.github_repo,
        "search_code",
        lambda query, **kwargs: GitHubCodeSearchResult(ok=True, query=query),
    )


@pytest.mark.parametrize(
    ("agent_type", "role", "view_key"),
    [
        ("RepoLeanProviderDiscoveryAgent", "worker", "repo_lean_provider_discovery"),
        ("RepoFormatDiscoveryAgent", "coordinator", "repo_format_discovery"),
    ],
)
def test_discovery_agents_can_invoke_every_visible_github_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent_type: str,
    role: str,
    view_key: str,
) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    _install_fake_github(runtime, monkeypatch)
    repo_root = tmp_path / agent_type
    repo_root.mkdir()
    raw = _raw(repo_root, agent_type=agent_type, role=role, view_key=view_key)
    calls = {
        "search_github_lean_repositories": {"query": "Kneser", "limit": 3},
        "inspect_github_lean_repository": {"url_or_slug": "owner/repo"},
        "probe_github_lean_repo_candidate": {"git_url": "owner/repo"},
        "get_github_repository": {"git_url": "owner/repo"},
        "list_github_repository_tree": {"git_url": "owner/repo", "limit": 20},
        "read_github_repository_file": {"git_url": "owner/repo", "path": "Main.lean"},
        "search_github_code": {"query": "Kneser", "repo": "owner/repo", "limit": 3},
    }

    for tool_name, args in calls.items():
        result = runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name=tool_name,
            flat_args=args,
        )
        assert result.ok and result.value is not None, tool_name
        assert result.value.ok is True, tool_name


def test_ordinary_worker_cannot_borrow_discovery_github_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    _install_fake_github(runtime, monkeypatch)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = runtime.tool_facade.invoke_agent_tool(
        _raw(
            repo_root,
            agent_type="ResourceCuratorAgent",
            role="worker",
            view_key="repo_lean_provider_discovery",
        ),
        tool_name="get_github_repository",
        flat_args={"git_url": "owner/repo"},
    )

    assert result.ok and result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind == "agent_type_not_allowed_for_view"
