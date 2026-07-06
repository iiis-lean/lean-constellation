from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import BaseSubmission

from lean_constellation.services import LeanProviderOverrides, create_test_runtime_services
from lean_constellation.services.tool_facade import RawToolCallContext, RuntimeToolContext
from lean_constellation.tools import register_submit_tooling


class FakeSubmissionGateway:
    def __init__(self) -> None:
        self.accepted: list[BaseSubmission] = []

    def accept_step_submission(self, ctx, submission: BaseSubmission):
        del ctx
        self.accepted.append(submission)
        return {"accepted": True}


def _runtime_ctx(repo_root: Path, *, view: str, role: str = "coordinator", agent_type: str = "RepoFormatDiscoveryAgent", successful: bool = False) -> RuntimeToolContext:
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
        successful_submission_kind="repo_format_native_choice" if successful else None,
    )


def _runtime(gateway: FakeSubmissionGateway):
    return create_test_runtime_services(providers=LeanProviderOverrides(submission_gateway=gateway))


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
        flat_args={"summary": "Use native repo.", "source_corpus_mode": "prepare"},
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is True
    assert len(gateway.accepted) == 1
    assert gateway.accepted[0].submission_type == "repo_format_native_choice"


def test_native_repo_choice_rejects_empty_or_none_source_corpus_mode(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    raw = RawToolCallContext(
        endpoint_view_key="repo_format_discovery_submit",
        runtime_context=_runtime_ctx(tmp_path, view="repo_format_discovery_submit"),
    )

    missing = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_native_repo_choice",
        flat_args={"summary": "Use native repo."},
    )
    explicit_none = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_native_repo_choice",
        flat_args={"summary": "Use native repo.", "source_corpus_mode": "none"},
    )

    assert missing.ok
    assert missing.value is not None
    assert missing.value.ok is False
    assert missing.value.issues[0].kind == "tool_arguments_invalid"
    assert explicit_none.ok
    assert explicit_none.value is not None
    assert explicit_none.value.ok is False
    assert explicit_none.value.issues[0].kind == "tool_arguments_invalid"
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
        flat_args={"summary": "Use adapter repo.", "upstream_github_url": "https://example.com/project"},
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind == "upstream_github_url_invalid"
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
        flat_args={"summary": "Use native repo.", "source_corpus_mode": "prepare"},
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind in {"submission_already_accepted", "submission_already_recorded", "conflicting_submission"}
    assert gateway.accepted == []


def test_submit_repo_requirement_records_waiting_state(tmp_path: Path) -> None:
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
            "target_repo": "Provider",
            "summary": "Need provider repo.",
            "reason": "Need provider theorem.",
            "source_description": "A source dependency mentions the provider theorem.",
        },
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is True
    requirement = runtime.repo_workspace.requirement.get_requirement(tmp_path, name="need_provider")
    assert requirement.ok and requirement.value is not None
    assert requirement.value.requirement.provider_repo == "Provider"
    assert requirement.value.requirement.provider_request_submitted_at is not None
    assert requirement.value.requirement.provider_result_observed_at is None
    assert requirement.value.requirement.note == "Need provider theorem."
    assert len(gateway.accepted) == 1
    assert gateway.accepted[0].submission_type == "coordinator_repo_requirement"
