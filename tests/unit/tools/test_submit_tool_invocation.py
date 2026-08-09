from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_runtime_kit.flow.models import BaseSubmission, FlowStatus

from lean_constellation.domain.preparation import (
    AdapterProviderRoute,
    AutoProviderRoute,
    NativeProviderRoute,
)
from lean_constellation.domain.repo import ProofAvailability, RepoCompletionMode
from lean_constellation.domain.repo_run import SourceScope
from lean_constellation.services import LeanProviderOverrides, create_test_runtime_services
from lean_constellation.services.external_clients import (
    ExternalResourceCandidate,
    ExternalResourceInspectResult,
    GitHubCommitHistoryView,
    GitHubLeanRepoProbeView,
)
from lean_constellation.services.tool_facade import RawToolCallContext, RuntimeToolContext
from lean_constellation.tools import register_submit_tooling
from lean_constellation.tools.submit_args import RequirementInterfaceArg, SubmitRepoRequirementArgs
from lean_constellation.services.validation_snapshot.release_finalizer import CandidateReleaseGateView


class FakeSubmissionGateway:
    def __init__(self) -> None:
        self.accepted: list[BaseSubmission] = []

    def accept_step_submission(self, ctx, submission: BaseSubmission):
        del ctx
        self.accepted.append(submission)
        return {"accepted": True}


def _runtime_ctx(
    repo_root: Path,
    *,
    view: str,
    role: str = "coordinator",
    agent_type: str = "RepoFormatDiscoveryAgent",
    successful: bool = False,
    successful_kind: str | None = None,
) -> RuntimeToolContext:
    return RuntimeToolContext(
        flow_id="flow_1",
        step_id="step_1",
        agent_id="agent_1",
        scope_id="scope_1",
        agent_type=agent_type,
        agent_role=role,
        expected_view_key=view,
        repo_root=repo_root,
        successful_submission_count=1 if successful else 0,
        successful_submission_kind=successful_kind or ("repo_format_native_choice" if successful else None),
    )


def _runtime(gateway: FakeSubmissionGateway):
    runtime = create_test_runtime_services(
        providers=LeanProviderOverrides(submission_gateway=gateway),
        external_overrides={"github_repo": FakeGitHubRepo()},
    )
    runtime.external.resource_discovery = FakeResourceDiscovery()
    return runtime


class FakeResourceDiscovery:
    def __init__(self) -> None:
        self.inspect_calls: list[str] = []
        self.source_urls = ["https://arxiv.org/abs/2501.12345"]

    def inspect(self, target: str) -> ExternalResourceInspectResult:
        self.inspect_calls.append(target)
        if target == "missing":
            return ExternalResourceInspectResult(
                ok=False,
                target=target,
                summary="No matching resource.",
                issue_code="external_resource_not_found",
            )
        return ExternalResourceInspectResult(
            ok=True,
            target=target,
            candidate=ExternalResourceCandidate(
                title="Canonical theorem source",
                resource_kind="paper",
                canonical_locator="https://doi.org/10.1000/canonical",
                authors=["Ada Author"],
                version="v2",
                source_urls=list(self.source_urls),
            ),
            summary="Inspected canonical theorem source.",
        )


class FakeGitHubRepo:
    revision = "a" * 40

    def __init__(self) -> None:
        self.probe_calls: list[tuple[str, str | None, str | None]] = []
        self.lean_toolchain = "leanprover/lean4:v4.28.0"
        self.mathlib_revision = "v4.28.0"
        self.has_lakefile = True
        self.has_lean_files = True
        self.package_name: str | None = "Provider"
        self.likely_import_modules = ["Provider"]

    def normalize_github_url(self, value: str) -> str:
        normalized = value.strip().removesuffix(".git").rstrip("/")
        if normalized.startswith("git@github.com:"):
            return "https://github.com/" + normalized.removeprefix("git@github.com:")
        if normalized.startswith("https://github.com/"):
            return normalized
        if "/" in normalized and "://" not in normalized:
            return f"https://github.com/{normalized}"
        raise ValueError("Git URL must identify a GitHub repository.")

    def probe_github_lean_repo_candidate(
        self,
        git_url: str,
        revision: str | None = None,
        subdir: str | None = None,
    ) -> GitHubLeanRepoProbeView:
        self.probe_calls.append((git_url, revision, subdir))
        resolved = revision or self.revision
        normalized = self.normalize_github_url(git_url)
        is_mathlib = normalized.casefold() in {
            "https://github.com/leanprover-community/mathlib",
            "https://github.com/leanprover-community/mathlib4",
        }
        return GitHubLeanRepoProbeView(
            git_url=git_url,
            normalized_git_url=normalized,
            requested_revision=revision,
            resolved_revision=resolved,
            requested_subdir=subdir,
            selected_subdir=subdir,
            is_lean_project=True,
            has_lakefile=self.has_lakefile,
            has_lean_toolchain=True,
            has_lean_manifest=self.has_lakefile,
            has_lean_files=self.has_lean_files,
            is_mathlib_repository=is_mathlib,
            package_name=self.package_name,
            likely_import_modules=list(self.likely_import_modules),
            lakefile_paths=["lakefile.toml"] if self.has_lakefile else [],
            lean_toolchain_paths=["lean-toolchain"],
            lean_file_paths=["Provider/Main.lean"] if self.has_lean_files else [],
            lean_signals=["path:lakefile.toml", "tree:lean_files=1"],
            lakefile_excerpt=(
                'name = "Provider"\n\n'
                '[[require]]\nname = "mathlib"\n'
                f'scope = "leanprover-community"\nrev = "{self.mathlib_revision}"\n'
            ),
            lean_toolchain=self.lean_toolchain,
            evidence_summary="Exact compatible remote Lean project.",
        )

    def list_repository_commits(self, git_url: str, *, limit: int) -> GitHubCommitHistoryView:
        return GitHubCommitHistoryView(
            git_url=self.normalize_github_url(git_url),
            commits=[self.revision][:limit],
            summary="Fixture commit history.",
        )


def test_successful_submit_records_typed_submission(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="repo_format_discovery_submit",
        runtime_context=_runtime_ctx(tmp_path, view="repo_format_discovery_submit"),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_native_repo_choice",
        flat_args={"summary": "Use native repo.", "searched_targets": ["topology repo"]},
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is True
    assert len(gateway.accepted) == 1
    assert gateway.accepted[0].submission_type == "repo_format_native_choice"
    assert gateway.accepted[0].searched_targets == ["topology repo"]


@pytest.mark.parametrize(
    "coordinator_phase",
    ["coordinator_agent", "coordinator_callback", "coordinator_requirement_resume"],
)
def test_repo_ready_submit_only_records_candidate_intent_without_heavy_preview(
    tmp_path: Path,
    coordinator_phase: str,
) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    owner_flow = SimpleNamespace(
        flow_id="flow_1",
        flow_type="native_repo_coordinator",
        scope_id=f"repo:{tmp_path.name}",
        status="running",
        state=SimpleNamespace(position=SimpleNamespace(phase=coordinator_phase)),
        input=SimpleNamespace(repo_root=str(tmp_path), run_context=SimpleNamespace(base_release_id="release-base")),
    )
    runtime.ark.flow_service = SimpleNamespace(
        get_flow=lambda _flow_id: owner_flow,
        list_flows=lambda **_kwargs: [owner_flow],
    )
    runtime.ark.step_service = SimpleNamespace(list_steps=lambda **_kwargs: [])
    runtime.ark.agent_service = SimpleNamespace(
        list_agents=lambda **_kwargs: [SimpleNamespace(agent_id="agent_1", scope_id=f"repo:{tmp_path.name}", status="running")]
    )
    calls: list[dict[str, object]] = []

    def preview(repo_root, **kwargs):
        calls.append({"repo_root": repo_root, **kwargs})
        gate = runtime.foundation.gate_passed("candidate_release", summary="Candidate preview passed.")
        return runtime.foundation.ok(
            CandidateReleaseGateView(
                base_release_id="release-base",
                completion_mode=RepoCompletionMode.GRAPH_DECLARED,
                gate=gate,
                summary="Candidate preview passed.",
            )
        )

    runtime.validation_snapshot.preview_candidate_release = preview
    raw = RawToolCallContext(
        endpoint_view_key="native_repo_coordinator_submit",
        runtime_context=_runtime_ctx(
            tmp_path,
            view="native_repo_coordinator_submit",
            agent_type="CoordinatorAgent",
        ),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_repo_ready",
        flat_args={"summary": "Publish the current candidate."},
    )

    assert result.ok and result.value is not None and result.value.ok
    assert len(gateway.accepted) == 1
    assert gateway.accepted[0].submission_type == "coordinator_repo_ready"
    assert calls == []


def test_repo_ready_submit_rejects_nonterminal_repo_runtime(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    owner_flow = SimpleNamespace(
        flow_id="flow_1",
        flow_type="native_repo_coordinator",
        scope_id=f"repo:{tmp_path.name}",
        status=FlowStatus.RUNNING,
        state=SimpleNamespace(position="coordinator_agent"),
        input=SimpleNamespace(repo_root=str(tmp_path), run_context=None),
    )
    running_child = SimpleNamespace(
        flow_id="content_running",
        flow_type="content_node_task",
        scope_id=f"repo:{tmp_path.name}:node:n_topic",
        status=FlowStatus.RUNNING,
    )
    runtime.ark.flow_service = SimpleNamespace(
        get_flow=lambda _flow_id: owner_flow,
        list_flows=lambda **_kwargs: [owner_flow, running_child],
    )
    runtime.ark.step_service = SimpleNamespace(list_steps=lambda **_kwargs: [])
    runtime.ark.agent_service = SimpleNamespace(
        list_agents=lambda **_kwargs: [SimpleNamespace(
            agent_id="agent_1", scope_id=f"repo:{tmp_path.name}", status="running"
        )]
    )

    result = runtime.tool_facade.invoke_agent_tool(
        RawToolCallContext(
            endpoint_view_key="native_repo_coordinator_submit",
            runtime_context=_runtime_ctx(
                tmp_path,
                view="native_repo_coordinator_submit",
                agent_type="CoordinatorAgent",
            ),
        ),
        tool_name="submit_repo_ready",
        flat_args={"summary": "Publish the current candidate."},
    )

    assert result.ok and result.value is not None and not result.value.ok
    assert result.value.issues[0].kind == "release_workflow_not_closed"
    assert gateway.accepted == []


def test_terminal_submit_summary_is_stripped_and_cannot_be_blank(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="repo_format_discovery_submit",
        runtime_context=_runtime_ctx(tmp_path, view="repo_format_discovery_submit"),
    )

    blank = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_native_repo_choice",
        flat_args={"summary": "   "},
    )
    stripped = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_native_repo_choice",
        flat_args={"summary": "  Use native repo.  ", "searched_targets": ["topology repo"]},
    )

    assert blank.ok
    assert blank.value is not None
    assert blank.value.ok is False
    assert blank.value.issues[0].kind == "tool_arguments_invalid"
    assert stripped.ok
    assert stripped.value is not None
    assert stripped.value.ok is True
    assert gateway.accepted[0].summary == "Use native repo."


def test_native_repo_choice_rejects_empty_or_legacy_search_evidence(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="repo_format_discovery_submit",
        runtime_context=_runtime_ctx(tmp_path, view="repo_format_discovery_submit"),
    )

    empty = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_native_repo_choice",
        flat_args={"summary": "Use native.", "searched_targets": ["  "]},
    )
    legacy = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_native_repo_choice",
        flat_args={
            "summary": "Use native.",
            "searched_targets": ["provider theorem Lean"],
            "rejected_candidates": [],
        },
    )

    assert empty.ok and empty.value is not None and not empty.value.ok
    assert empty.value.issues[0].field == "searched_targets"
    assert legacy.ok and legacy.value is not None and not legacy.value.ok
    assert legacy.value.issues[0].field == "rejected_candidates"
    assert gateway.accepted == []


def test_resource_discovery_submit_reinspects_and_canonicalizes_candidates(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok

    result = runtime.tool_facade.invoke_agent_tool(
        RawToolCallContext(
            endpoint_view_key="repo_resource_discovery_submit",
            runtime_context=_runtime_ctx(
                tmp_path,
                view="repo_resource_discovery_submit",
                role="worker",
                agent_type="RepoResourceDiscoveryAgent",
            ),
        ),
        tool_name="submit_repo_resource_discovery_result",
        flat_args={
            "outcome": "completed",
            "summary": "Found one primary source.",
            "candidates": [
                {
                    "target": "arxiv:2501.12345",
                    "support_summary": "Supplies the exact finite theorem used by the repo.",
                    "recommended_handling": "local_resource",
                    "risks_or_gaps": ["Appendix notation needs reconciliation."],
                }
            ],
        },
    )

    assert result.ok and result.value is not None and result.value.ok
    assert runtime.external.resource_discovery.inspect_calls == ["arxiv:2501.12345"]
    candidate = gateway.accepted[0].candidates[0]
    assert candidate.title == "Canonical theorem source"
    assert candidate.canonical_locator == "https://doi.org/10.1000/canonical"
    assert candidate.authors == ["Ada Author"]
    assert candidate.support_summary.startswith("Supplies the exact finite theorem")
    assert candidate.recommended_handling == "local_resource"


def test_resource_discovery_submit_rejects_uninspectable_target_without_terminal_submit(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok

    result = runtime.tool_facade.invoke_agent_tool(
        RawToolCallContext(
            endpoint_view_key="repo_resource_discovery_submit",
            runtime_context=_runtime_ctx(
                tmp_path,
                view="repo_resource_discovery_submit",
                role="worker",
                agent_type="RepoResourceDiscoveryAgent",
            ),
        ),
        tool_name="submit_repo_resource_discovery_result",
        flat_args={
            "outcome": "completed",
            "summary": "Candidate requires inspection.",
            "candidates": [
                {
                    "target": "missing",
                    "support_summary": "Potential source.",
                    "recommended_handling": "inspect_later",
                }
            ],
        },
    )

    assert result.ok and result.value is not None and not result.value.ok
    assert result.value.issues[0].kind == "external_resource_not_found"
    assert result.value.issues[0].field == "candidates[0].target"
    assert gateway.accepted == []


def test_lean_provider_submit_probes_and_canonicalizes_backend_facts(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok

    result = runtime.tool_facade.invoke_agent_tool(
        RawToolCallContext(
            endpoint_view_key="repo_lean_provider_discovery_submit",
            runtime_context=_runtime_ctx(
                tmp_path,
                view="repo_lean_provider_discovery_submit",
                role="worker",
                agent_type="RepoLeanProviderDiscoveryAgent",
            ),
        ),
        tool_name="submit_repo_lean_provider_discovery_result",
        flat_args={
            "outcome": "completed",
            "summary": "Found one exact Lean provider.",
            "candidates": [
                {
                    "git_url": "owner/provider.git",
                    "capability_summary": "Provides the additive Kneser theorem.",
                    "relevant_declarations": ["Finset.add_kneser"],
                    "gaps": [],
                    "risks": ["License must be retained."],
                    "recommendation": "direct_adapter_requirement",
                }
            ],
        },
    )

    assert result.ok and result.value is not None and result.value.ok
    assert runtime.external.github_repo.probe_calls == [("owner/provider.git", None, None)]
    candidate = gateway.accepted[0].candidates[0]
    assert candidate.git_url == "https://github.com/owner/provider"
    assert candidate.resolved_revision == "a" * 40
    assert candidate.package_name == "Provider"
    assert candidate.likely_import_modules == ["Provider"]
    assert candidate.relevant_declarations == ["Finset.add_kneser"]
    assert "path:Provider/Main.lean" in candidate.lean_evidence


def test_lean_provider_submit_rejects_mathlib_after_probe(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok

    result = runtime.tool_facade.invoke_agent_tool(
        RawToolCallContext(
            endpoint_view_key="repo_lean_provider_discovery_submit",
            runtime_context=_runtime_ctx(
                tmp_path,
                view="repo_lean_provider_discovery_submit",
                role="worker",
                agent_type="RepoLeanProviderDiscoveryAgent",
            ),
        ),
        tool_name="submit_repo_lean_provider_discovery_result",
        flat_args={
            "outcome": "completed",
            "summary": "Found only platform Mathlib.",
            "candidates": [
                {
                    "git_url": "git@github.com:leanprover-community/mathlib4.git",
                    "capability_summary": "Platform graph support.",
                    "relevant_declarations": ["SimpleGraph"],
                    "gaps": [],
                    "risks": [],
                    "recommendation": "direct_adapter_requirement",
                }
            ],
        },
    )

    assert result.ok and result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind == "mathlib_provider_candidate_forbidden"
    assert gateway.accepted == []


def test_lean_provider_direct_submit_rejects_missing_probe_facts(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    runtime.external.github_repo.package_name = None
    runtime.external.github_repo.likely_import_modules = []
    assert register_submit_tooling(runtime).ok

    result = runtime.tool_facade.invoke_agent_tool(
        RawToolCallContext(
            endpoint_view_key="repo_lean_provider_discovery_submit",
            runtime_context=_runtime_ctx(
                tmp_path,
                view="repo_lean_provider_discovery_submit",
                role="worker",
                agent_type="RepoLeanProviderDiscoveryAgent",
            ),
        ),
        tool_name="submit_repo_lean_provider_discovery_result",
        flat_args={
            "outcome": "completed",
            "summary": "Candidate looked relevant.",
            "candidates": [
                {
                    "git_url": "owner/provider",
                    "capability_summary": "Potential provider.",
                    "relevant_declarations": ["Provider.target"],
                    "recommendation": "direct_adapter_requirement",
                }
            ],
        },
    )

    assert result.ok and result.value is not None and not result.value.ok
    assert result.value.issues[0].kind == "direct_adapter_candidate_incomplete"
    assert gateway.accepted == []


def test_node_dir_dependency_recon_completed_rejects_blank_summary(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="node_dir_dependency_recon_submit",
        runtime_context=_runtime_ctx(
            tmp_path,
            view="node_dir_dependency_recon_submit",
            role="worker",
            agent_type="NodeDirDependencyReconAgent",
        ),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_node_dir_dependency_recon_completed",
        flat_args={"summary": "   "},
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind == "tool_arguments_invalid"
    assert gateway.accepted == []


def test_native_repo_choice_rejects_legacy_source_corpus_mode(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="repo_format_discovery_submit",
        runtime_context=_runtime_ctx(tmp_path, view="repo_format_discovery_submit"),
    )

    legacy = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_native_repo_choice",
        flat_args={"summary": "Use native repo.", "source_corpus_mode": "none"},
    )

    assert legacy.ok
    assert legacy.value is not None
    assert legacy.value.ok is False
    assert legacy.value.issues[0].kind == "tool_arguments_invalid"
    assert gateway.accepted == []


def test_adapter_repo_choice_rejects_non_github_url(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="repo_format_discovery_submit",
        runtime_context=_runtime_ctx(tmp_path, view="repo_format_discovery_submit"),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_adapter_repo_choice",
        flat_args={"git_url": "https://example.com/project", "evidence_summary": "Remote evidence."},
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind == "git_url_invalid"
    assert gateway.accepted == []


def test_adapter_repo_choice_rejects_legacy_upstream_url_payload(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="repo_format_discovery_submit",
        runtime_context=_runtime_ctx(tmp_path, view="repo_format_discovery_submit"),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_adapter_repo_choice",
        flat_args={"summary": "Use adapter repo.", "upstream_github_url": "https://github.com/owner/repo"},
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind == "tool_arguments_invalid"
    assert gateway.accepted == []


def test_adapter_repo_choice_records_typed_remote_evidence(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="repo_format_discovery_submit",
        runtime_context=_runtime_ctx(tmp_path, view="repo_format_discovery_submit"),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_adapter_repo_choice",
        flat_args={
            "git_url": "owner/repo",
            "revision": "a" * 40,
            "subdir": "lean",
            "evidence_summary": "Remote probe found lakefile.lean.",
            "known_risks": ["Coverage not verified."],
        },
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is True
    assert gateway.accepted[0].git_url == "https://github.com/owner/repo"
    assert gateway.accepted[0].revision == "a" * 40
    assert gateway.accepted[0].verified_route.package_name == "Provider"
    assert gateway.accepted[0].verified_route.likely_import_module == "Provider"
    assert gateway.accepted[0].known_risks == ["Coverage not verified."]
    assert runtime.external.github_repo.probe_calls == [
        ("https://github.com/owner/repo", "a" * 40, "lean")
    ]


def test_adapter_repo_choice_rejects_agent_supplied_probe_facts(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="repo_format_discovery_submit",
        runtime_context=_runtime_ctx(tmp_path, view="repo_format_discovery_submit"),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_adapter_repo_choice",
        flat_args={
            "git_url": "owner/repo",
            "revision": "a" * 40,
            "package_name": "AgentGuess",
            "likely_import_module": "AgentGuess",
            "evidence_summary": "Remote project candidate.",
        },
    )

    assert result.ok and result.value is not None and not result.value.ok
    assert {issue.field for issue in result.value.issues} == {
        "package_name",
        "likely_import_module",
    }
    assert gateway.accepted == []


def test_adapter_repo_choice_rejects_incompatible_probe_before_submission(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    runtime.external.github_repo.lean_toolchain = "leanprover/lean4:v4.32.0"
    runtime.external.github_repo.mathlib_revision = "v4.32.0"
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="repo_format_discovery_submit",
        runtime_context=_runtime_ctx(tmp_path, view="repo_format_discovery_submit"),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_adapter_repo_choice",
        flat_args={
            "git_url": "owner/repo",
            "revision": "a" * 40,
            "evidence_summary": "Remote project candidate.",
        },
    )

    assert result.ok and result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind == "adapter_upstream_toolchain_mismatch"
    assert gateway.accepted == []


def test_gate_failure_does_not_record_submission(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="source_corpus_prepare_submit",
        runtime_context=_runtime_ctx(tmp_path, view="source_corpus_prepare_submit", role="worker", agent_type="SourceCorpusPrepareAgent"),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_source_corpus_prepared",
        flat_args={
            "summary": "Prepared.",
            "entry_path": "README.md",
            "overview": "overview",
            "preparation_summary": "done",
        },
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is False
    assert gateway.accepted == []


def test_source_corpus_prepared_gateway_missing_does_not_write_manifest(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    assert register_submit_tooling(runtime).ok
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(
        "Source overview.\n"
        "Source provenance: local source fixture.\n"
        "Reading order: this README is the entry and main material.\n"
        "Known gaps and extraction limits: no missing source sections are known.\n",
        encoding="utf-8",
    )
    raw = RawToolCallContext(
        endpoint_view_key="source_corpus_prepare_submit",
        runtime_context=_runtime_ctx(tmp_path, view="source_corpus_prepare_submit", role="worker", agent_type="SourceCorpusPrepareAgent"),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_source_corpus_prepared",
        flat_args={
            "summary": "Prepared.",
            "entry_path": "README.md",
            "overview": "Source corpus overview.",
            "preparation_summary": "Prepared source corpus.",
        },
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind == "submission_gateway_missing"
    assert not (tmp_path / ".lean_constellation" / "source_corpus" / "manifest.json").exists()


def test_source_corpus_prepared_weak_canonical_readme_rejected_before_gateway(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(
        "# Source\n\nMain material: notes. Known gaps and extraction limits: none.\n",
        encoding="utf-8",
    )
    raw = RawToolCallContext(
        endpoint_view_key="source_corpus_prepare_submit",
        runtime_context=_runtime_ctx(tmp_path, view="source_corpus_prepare_submit", role="worker", agent_type="SourceCorpusPrepareAgent"),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_source_corpus_prepared",
        flat_args={
            "summary": "Prepared.",
            "entry_path": "README.md",
            "overview": "Source corpus overview.",
            "preparation_summary": "Prepared source corpus.",
        },
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is False
    assert gateway.accepted == []
    assert {
        "source_corpus_provenance_missing",
        "source_corpus_reading_order_missing",
    } <= {issue.kind for issue in result.value.issues}
    assert not (tmp_path / ".lean_constellation" / "source_corpus" / "manifest.json").exists()


def test_second_successful_submit_is_rejected_before_gateway(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="repo_format_discovery_submit",
        runtime_context=_runtime_ctx(tmp_path, view="repo_format_discovery_submit", successful=True),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_native_repo_choice",
        flat_args={"summary": "Use native repo."},
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind in {"submission_already_accepted", "submission_already_recorded", "conflicting_submission"}
    assert gateway.accepted == []


def _prepare_valid_source_index(runtime, repo_root: Path) -> None:
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(
        "Source overview\nThe main statement.\nThe proof outline.\n",
        encoding="utf-8",
    )
    resolved = runtime.material.resolve_source_scope(repo_root, source_scope=SourceScope(mode="all"))
    assert resolved.ok and resolved.value is not None
    opened = runtime.material.open_source_index_update(
        repo_root,
        resolved_scope=resolved.value,
        index_policy="auto",
    )
    assert opened.ok
    block = runtime.material.create_source_block(
        repo_root,
        parent_id="root",
        kind="section",
        title="Main source block",
        summary="Covers the main statement and proof outline.",
    )
    assert block.ok and block.value is not None
    assert runtime.material.add_source_block_ref(
        repo_root,
        block_id=block.value.block_id,
        path="README.md",
        start_line=1,
        end_line=3,
        role="main",
    ).ok
    assert runtime.material.mark_block_refs_done(
        repo_root, block_id=block.value.block_id
    ).value.passed
    assert runtime.material.mark_block_links_done(
        repo_root, block_id=block.value.block_id
    ).value.passed
    assert runtime.material.mark_block_completed(
        repo_root, block_id=block.value.block_id
    ).value.passed
    assert runtime.material.set_file_survey_status(
        repo_root,
        path="README.md",
        status="surveyed",
        summary="Read.",
    ).ok
    assert runtime.material.set_file_indexing_status(
        repo_root, path="README.md", status="indexed"
    ).ok


def _install_source_index_submit_owner(runtime, repo_root: Path, *, step_type: str) -> None:
    runtime.ark.flow_service = SimpleNamespace(
        get_flow=lambda _flow_id: SimpleNamespace(
            flow_type="source_index_build",
            input=SimpleNamespace(repo_root=str(repo_root)),
        )
    )
    runtime.ark.step_service = SimpleNamespace(
        get_step=lambda _step_id: SimpleNamespace(flow_id="flow_1", step_type=step_type)
    )


def test_second_source_index_builder_submit_is_rejected_before_gateway(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    _prepare_valid_source_index(runtime, tmp_path)
    _install_source_index_submit_owner(
        runtime, tmp_path, step_type="source_index_builder_agent_step"
    )

    first = runtime.tool_facade.invoke_agent_tool(
        RawToolCallContext(
            endpoint_view_key="source_index_builder_submit",
            runtime_context=_runtime_ctx(
                tmp_path,
                view="source_index_builder_submit",
                role="worker",
                agent_type="SourceIndexBuilderAgent",
            ),
        ),
        tool_name="submit_source_index_builder_round",
        flat_args={"summary": "Builder round ready."},
    )
    second = runtime.tool_facade.invoke_agent_tool(
        RawToolCallContext(
            endpoint_view_key="source_index_builder_submit",
            runtime_context=_runtime_ctx(
                tmp_path,
                view="source_index_builder_submit",
                role="worker",
                agent_type="SourceIndexBuilderAgent",
                successful=True,
                successful_kind="source_index_builder_round",
            ),
        ),
        tool_name="submit_source_index_builder_round",
        flat_args={"summary": "Duplicate builder round."},
    )

    assert first.ok and first.value is not None and first.value.ok is True
    assert second.ok and second.value is not None and second.value.ok is False
    assert second.value.issues[0].kind in {"submission_already_accepted", "submission_already_recorded", "conflicting_submission"}
    assert len(gateway.accepted) == 1


def test_second_source_index_reviewer_submit_is_rejected_before_gateway(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    _prepare_valid_source_index(runtime, tmp_path)
    _install_source_index_submit_owner(
        runtime, tmp_path, step_type="source_index_reviewer_agent_step"
    )

    first = runtime.tool_facade.invoke_agent_tool(
        RawToolCallContext(
            endpoint_view_key="source_index_reviewer_submit",
            runtime_context=_runtime_ctx(
                tmp_path,
                view="source_index_reviewer_submit",
                role="reviewer",
                agent_type="SourceIndexReviewerAgent",
            ),
        ),
        tool_name="submit_source_index_review_round",
        flat_args={"approved": False, "summary": "Rejected.", "feedback": "Add missing source refs."},
    )
    second = runtime.tool_facade.invoke_agent_tool(
        RawToolCallContext(
            endpoint_view_key="source_index_reviewer_submit",
            runtime_context=_runtime_ctx(
                tmp_path,
                view="source_index_reviewer_submit",
                role="reviewer",
                agent_type="SourceIndexReviewerAgent",
                successful=True,
                successful_kind="source_index_reviewer_round",
            ),
        ),
        tool_name="submit_source_index_review_round",
        flat_args={"approved": True, "summary": "Duplicate review."},
    )

    assert first.ok and first.value is not None and first.value.ok is True
    assert second.ok and second.value is not None and second.value.ok is False
    assert second.value.issues[0].kind in {"submission_already_accepted", "submission_already_recorded", "conflicting_submission"}
    assert len(gateway.accepted) == 1


def test_source_index_submit_rejects_wrong_flow_and_step(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    _prepare_valid_source_index(runtime, tmp_path)
    raw = RawToolCallContext(
        endpoint_view_key="source_index_builder_submit",
        runtime_context=_runtime_ctx(
            tmp_path,
            view="source_index_builder_submit",
            role="worker",
            agent_type="SourceIndexBuilderAgent",
        ),
    )

    def invoke(*, flow_type: str, step_type: str):
        runtime.ark.flow_service = SimpleNamespace(
            get_flow=lambda _flow_id: SimpleNamespace(
                flow_type=flow_type,
                input=SimpleNamespace(repo_root=str(tmp_path)),
            )
        )
        runtime.ark.step_service = SimpleNamespace(
            get_step=lambda _step_id: SimpleNamespace(flow_id="flow_1", step_type=step_type)
        )
        return runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="submit_source_index_builder_round",
            flat_args={"summary": "Candidate builder round."},
        )

    wrong_flow = invoke(
        flow_type="native_repo_coordinator",
        step_type="source_index_builder_agent_step",
    )
    wrong_step = invoke(
        flow_type="source_index_build",
        step_type="source_index_reviewer_agent_step",
    )

    assert wrong_flow.ok and wrong_flow.value is not None and not wrong_flow.value.ok
    assert wrong_flow.value.issues[0].kind == "source_index_flow_context_mismatch"
    assert wrong_step.ok and wrong_step.value is not None and not wrong_step.value.ok
    assert wrong_step.value.issues[0].kind == "source_index_step_context_mismatch"

    reviewer_raw = RawToolCallContext(
        endpoint_view_key="source_index_reviewer_submit",
        runtime_context=_runtime_ctx(
            tmp_path,
            view="source_index_reviewer_submit",
            role="reviewer",
            agent_type="SourceIndexReviewerAgent",
        ),
    )
    runtime.ark.flow_service = SimpleNamespace(
        get_flow=lambda _flow_id: SimpleNamespace(
            flow_type="source_index_build",
            input=SimpleNamespace(repo_root=str(tmp_path)),
        )
    )
    runtime.ark.step_service = SimpleNamespace(
        get_step=lambda _step_id: SimpleNamespace(
            flow_id="flow_1", step_type="source_index_builder_agent_step"
        )
    )
    wrong_reviewer_step = runtime.tool_facade.invoke_agent_tool(
        reviewer_raw,
        tool_name="submit_source_index_review_round",
        flat_args={"approved": True, "summary": "Review candidate."},
    )
    assert wrong_reviewer_step.ok and wrong_reviewer_step.value is not None
    assert not wrong_reviewer_step.value.ok
    assert wrong_reviewer_step.value.issues[0].kind == "source_index_step_context_mismatch"

    runtime.ark.flow_service = SimpleNamespace(
        get_flow=lambda _flow_id: SimpleNamespace(
            flow_type="source_index_build",
            input=SimpleNamespace(repo_root=str(tmp_path)),
        )
    )
    runtime.ark.step_service = SimpleNamespace(
        get_step=lambda _step_id: SimpleNamespace(
            flow_id="flow_1", step_type="source_index_reviewer_agent_step"
        )
    )
    valid_reviewer = runtime.tool_facade.invoke_agent_tool(
        reviewer_raw,
        tool_name="submit_source_index_review_round",
        flat_args={"approved": True, "summary": "Review candidate."},
    )
    assert valid_reviewer.ok and valid_reviewer.value is not None and valid_reviewer.value.ok
    assert len(gateway.accepted) == 1


def test_submit_repo_requirement_builds_submission_without_waiting_state(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="native_repo_coordinator_submit",
        runtime_context=_runtime_ctx(
            tmp_path,
            view="native_repo_coordinator_submit",
            role="coordinator",
            agent_type="CoordinatorAgent",
        ),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_repo_requirement",
        flat_args={
            "name": "need_provider",
            "target_repo": "ReusableMath",
            "summary": "Need provider repo.",
            "reason": "Need provider theorem.",
            "source_description": "A source dependency mentions the provider theorem.",
            "interfaces": [
                {
                    "name": "ReusableMath.main_result",
                    "kind": "theorem",
                    "summary": "Expose the exact provider theorem.",
                    "expected_statement_lean_code": "theorem ReusableMath.main_result : True := by sorry",
                }
            ],
        },
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is True
    requirement = runtime.repo_workspace.requirement.get_requirement(tmp_path, name="need_provider")
    assert not requirement.ok
    assert requirement.issues[0].kind == "requirement_not_found"
    assert len(gateway.accepted) == 1
    assert gateway.accepted[0].submission_type == "coordinator_repo_requirement"
    assert gateway.accepted[0].required_proof_availability == ProofAvailability.DECLARED
    assert gateway.accepted[0].requirement_name == "need_provider"
    assert isinstance(gateway.accepted[0].provider_route, AutoProviderRoute)
    assert gateway.accepted[0].interfaces[0]["expected_statement_lean_code"] == (
        "theorem ReusableMath.main_result : True := by sorry"
    )


@pytest.mark.parametrize(
    ("tool_name", "route_args", "route_type"),
    [
        (
            "submit_adapter_repo_requirement",
            {
                "git_url": "example/provider",
                "revision": "a" * 40,
                "evidence_summary": "The exact Lean repository and commit were inspected.",
            },
            AdapterProviderRoute,
        ),
        (
            "submit_native_repo_requirement",
            {
                "evidence_summary": "No suitable Lean provider exists.",
                "searched_targets": ["provider theorem Lean"],
            },
            NativeProviderRoute,
        ),
    ],
)
def test_submit_typed_repo_requirement_builds_one_authoritative_submission(
    tmp_path: Path,
    tool_name: str,
    route_args: dict[str, object],
    route_type: type,
) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="native_repo_coordinator_submit",
        runtime_context=_runtime_ctx(
            tmp_path,
            view="native_repo_coordinator_submit",
            role="coordinator",
            agent_type="CoordinatorAgent",
        ),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name=tool_name,
        flat_args={
            "name": f"need_{route_type.__name__.lower()}",
            "target_repo": "ReusableMath",
            "summary": "Need the provider.",
            "reason": "Need an independent provider theorem.",
            **route_args,
        },
    )

    assert result.ok and result.value is not None and result.value.ok
    assert len(gateway.accepted) == 1
    submission = gateway.accepted[0]
    assert submission.submission_type == "coordinator_repo_requirement"
    assert isinstance(submission.provider_route, route_type)
    if isinstance(submission.provider_route, AdapterProviderRoute):
        assert submission.provider_route.package_name == "Provider"
        assert submission.provider_route.likely_import_module == "Provider"
    if isinstance(submission.provider_route, NativeProviderRoute):
        assert submission.provider_route.rejected_candidates == []


def test_submit_repo_requirement_schema_documents_business_field_contracts() -> None:
    fields = SubmitRepoRequirementArgs.model_fields
    interface_fields = RequirementInterfaceArg.model_fields

    assert "consumer-local" in (fields["name"].description or "")
    assert "lower_snake_case" in (fields["name"].description or "")
    assert "UpperCamelCase" in (fields["target_repo"].description or "")
    assert "mathematical source" in (fields["source_description"].description or "")
    assert "independent repository dependency" in (fields["reason"].description or "")
    assert "Minimal stable public API" in (fields["interfaces"].description or "")
    assert "public interface identity" in (interface_fields["name"].description or "")
    assert "supported DeclKind" in (interface_fields["kind"].description or "")
    assert "informal" in (interface_fields["statement_hint"].description or "")
    assert "preserve and satisfy verbatim" in (interface_fields["expected_statement_lean_code"].description or "")
    assert "affected_node_paths" not in fields
    assert "flow_id" not in fields
    assert "proof_availability" not in fields


def test_submit_repo_requirement_uses_consumer_repo_default_proof_availability(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    configured = runtime.repo_workspace.metadata.update_repo_config(
        tmp_path,
        completion_mode=RepoCompletionMode.GRAPH_PROVED,
        default_requirement_proof_availability=ProofAvailability.PROVED,
    )
    assert configured.ok
    raw = RawToolCallContext(
        endpoint_view_key="native_repo_coordinator_submit",
        runtime_context=_runtime_ctx(
            tmp_path,
            view="native_repo_coordinator_submit",
            role="coordinator",
            agent_type="CoordinatorAgent",
        ),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_repo_requirement",
        flat_args={
            "name": "need_proved_provider",
            "target_repo": "ReusableMath",
            "summary": "Need proved provider repo.",
            "reason": "Need provider theorem with proof availability.",
        },
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is True
    requirement = runtime.repo_workspace.requirement.get_requirement(tmp_path, name="need_proved_provider")
    assert not requirement.ok
    assert gateway.accepted[0].required_proof_availability == ProofAvailability.PROVED


def test_submit_repo_requirement_gateway_missing_does_not_write_requirement(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="native_repo_coordinator_submit",
        runtime_context=_runtime_ctx(
            tmp_path,
            view="native_repo_coordinator_submit",
            role="coordinator",
            agent_type="CoordinatorAgent",
        ),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_repo_requirement",
        flat_args={
            "name": "need_provider",
            "target_repo": "ReusableMath",
            "summary": "Need provider repo.",
            "reason": "Need provider theorem.",
        },
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind == "submission_gateway_missing"
    requirement = runtime.repo_workspace.requirement.get_requirement(tmp_path, name="need_provider")
    assert not requirement.ok
    assert requirement.issues[0].kind == "requirement_not_found"


def test_submit_repo_requirement_rejects_role_suffix_repo_name(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="native_repo_coordinator_submit",
        runtime_context=_runtime_ctx(
            tmp_path,
            view="native_repo_coordinator_submit",
            role="coordinator",
            agent_type="CoordinatorAgent",
        ),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_repo_requirement",
        flat_args={
            "name": "weighted_sieve_seven_forms",
            "target_repo": "WeightedSieveProvider",
            "summary": "Need weighted sieve mathematics.",
            "reason": "Need a reusable seven-form sieve result.",
        },
    )

    assert result.ok and result.value is not None and result.value.ok is False
    assert result.value.issues[0].kind == "requirement_target_repo_name_invalid"
    assert gateway.accepted == []


def test_submit_repo_requirement_rejects_non_lower_snake_case_name(tmp_path: Path) -> None:
    invalid_names = ["NeedProvider", "need-provider", "need__provider", "_need_provider", "need_provider_"]

    for invalid_name in invalid_names:
        gateway = FakeSubmissionGateway()
        runtime = _runtime(gateway)
        assert register_submit_tooling(runtime).ok
        raw = RawToolCallContext(
            endpoint_view_key="native_repo_coordinator_submit",
            runtime_context=_runtime_ctx(
                tmp_path,
                view="native_repo_coordinator_submit",
                role="coordinator",
                agent_type="CoordinatorAgent",
            ),
        )

        result = runtime.tool_facade.invoke_agent_tool(
            raw,
            tool_name="submit_repo_requirement",
            flat_args={
                "name": invalid_name,
                "target_repo": "ReusableMath",
                "summary": "Need provider repo.",
                "reason": "Need provider theorem.",
            },
        )

        assert result.ok and result.value is not None and result.value.ok is False
        assert result.value.issues[0].kind == "requirement_name_invalid"
        assert gateway.accepted == []


def test_submit_repo_requirement_rejects_invalid_or_duplicate_interfaces(tmp_path: Path) -> None:
    cases = [
        (
            [{"name": "main_result", "kind": "not_a_decl_kind", "summary": "Main result."}],
            "requirement_interface_kind_invalid",
        ),
        (
            [
                {"name": "main_result", "kind": "theorem", "summary": "Main result."},
                {"name": "main_result", "kind": "theorem", "summary": "Duplicate result."},
            ],
            "requirement_interface_name_duplicate",
        ),
    ]

    for interfaces, expected_issue in cases:
        gateway = FakeSubmissionGateway()
        runtime = _runtime(gateway)
        assert register_submit_tooling(runtime).ok
        result = runtime.tool_facade.invoke_agent_tool(
            RawToolCallContext(
                endpoint_view_key="native_repo_coordinator_submit",
                runtime_context=_runtime_ctx(
                    tmp_path,
                    view="native_repo_coordinator_submit",
                    role="coordinator",
                    agent_type="CoordinatorAgent",
                ),
            ),
            tool_name="submit_repo_requirement",
            flat_args={
                "name": "need_provider",
                "target_repo": "ReusableMath",
                "summary": "Need provider repo.",
                "reason": "Need provider theorem.",
                "interfaces": interfaces,
            },
        )

        assert result.ok and result.value is not None and result.value.ok is False
        assert result.value.issues[0].kind == expected_issue
        assert gateway.accepted == []


def test_submit_repo_requirement_rejects_duplicate_consumer_name_and_extra_control_fields(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    assert runtime.repo_workspace.create_requirement_with_interfaces(
        tmp_path,
        name="need_provider",
        target_repo="ReusableMath",
        reason="Existing requirement.",
    ).ok
    raw = RawToolCallContext(
        endpoint_view_key="native_repo_coordinator_submit",
        runtime_context=_runtime_ctx(
            tmp_path,
            view="native_repo_coordinator_submit",
            role="coordinator",
            agent_type="CoordinatorAgent",
        ),
    )

    duplicate = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_repo_requirement",
        flat_args={
            "name": "need_provider",
            "target_repo": "ReusableMath",
            "summary": "Duplicate provider repo.",
            "reason": "Duplicate requirement.",
        },
    )
    extra = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_repo_requirement",
        flat_args={
            "name": "another_provider",
            "target_repo": "AnotherMath",
            "summary": "Need another provider repo.",
            "reason": "Need another theorem.",
            "affected_node_paths": ["Main.Core"],
        },
    )

    assert duplicate.ok and duplicate.value is not None and duplicate.value.ok is False
    assert duplicate.value.issues[0].kind == "requirement_name_duplicate"
    assert extra.ok and extra.value is not None and extra.value.ok is False
    assert extra.value.issues[0].kind == "tool_arguments_invalid"
    assert gateway.accepted == []


def test_submit_content_node_tasks_passes_open_contract_version(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Core",
        goal="Core goal.",
        boundary="Core boundary.",
        objective="Run core task.",
        success_criteria="Core task completes.",
    ).ok
    raw = RawToolCallContext(
        endpoint_view_key="native_repo_coordinator_submit",
        runtime_context=_runtime_ctx(
            tmp_path,
            view="native_repo_coordinator_submit",
            role="coordinator",
            agent_type="CoordinatorAgent",
        ),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_content_node_tasks",
        flat_args={"node_paths": ["Main.Core"], "summary": "Run core."},
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is True, result.value.issues
    assert len(gateway.accepted) == 1
    submission = gateway.accepted[0]
    assert submission.submission_type == "coordinator_content_tasks"
    assert submission.requests[0].params["contract_version"] == 1


def test_submit_content_node_tasks_rejects_a_material_ref_that_no_longer_previews(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Core",
        goal="Core goal.",
        boundary="Core boundary.",
        objective="Run core task.",
        success_criteria="Core task completes.",
    ).ok
    source_path = tmp_path / ".lean_constellation" / "source" / "article" / "core.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("core statement\ncore proof\n", encoding="utf-8")
    assert runtime.node.material_ref.add_owned_source_ref(
        tmp_path,
        node_path="Main.Core",
        path="article/core.md",
        start_line=1,
        end_line=2,
        reason="Primary source contract.",
        actor="coordinator",
    ).ok
    source_path.unlink()
    raw = RawToolCallContext(
        endpoint_view_key="native_repo_coordinator_submit",
        runtime_context=_runtime_ctx(
            tmp_path,
            view="native_repo_coordinator_submit",
            role="coordinator",
            agent_type="CoordinatorAgent",
        ),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_content_node_tasks",
        flat_args={"node_paths": ["Main.Core"], "summary": "Run core."},
    )

    assert result.ok and result.value is not None and result.value.ok is False
    assert result.value.issues[0].kind == "content_task_material_ref_invalid"
    assert gateway.accepted == []


def test_submit_content_node_tasks_enforces_run_parallelism_before_dispatch(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    for path in ("Main.Left", "Main.Right"):
        assert runtime.node.create_content_node(
            tmp_path,
            path=path,
            goal=f"{path} goal.",
            boundary=f"{path} boundary.",
            objective=f"Run {path} task.",
            success_criteria=f"{path} completes.",
        ).ok
    owner_flow = SimpleNamespace(
        input=SimpleNamespace(
            run_context=SimpleNamespace(
                run_spec=SimpleNamespace(max_parallel_content_node_tasks=1),
            )
        )
    )
    runtime.ark.flow_service = SimpleNamespace(get_flow=lambda _flow_id: owner_flow)
    raw = RawToolCallContext(
        endpoint_view_key="native_repo_coordinator_submit",
        runtime_context=_runtime_ctx(
            tmp_path,
            view="native_repo_coordinator_submit",
            role="coordinator",
            agent_type="CoordinatorAgent",
        ),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_content_node_tasks",
        flat_args={"node_paths": ["Main.Left", "Main.Right"], "summary": "Run both."},
    )

    assert result.ok and result.value is not None and result.value.ok is False
    assert result.value.issues[0].kind == "content_task_batch_parallelism_exceeded"
    assert gateway.accepted == []
