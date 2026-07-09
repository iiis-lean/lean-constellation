from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import BaseSubmission

from lean_constellation.domain.repo import ProofAvailability, RepoWorkMode
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
        flat_args={"summary": "Use native repo.", "searched_targets": ["topology repo"], "rejected_candidates": []},
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is True
    assert len(gateway.accepted) == 1
    assert gateway.accepted[0].submission_type == "repo_format_native_choice"
    assert gateway.accepted[0].searched_targets == ["topology repo"]


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
            "revision": "main",
            "subdir": "lean",
            "package_name": "Foo",
            "likely_import_module": "Foo",
            "evidence_summary": "Remote probe found lakefile.lean.",
            "known_risks": ["Coverage not verified."],
        },
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is True
    assert gateway.accepted[0].git_url == "https://github.com/owner/repo"
    assert gateway.accepted[0].package_name == "Foo"
    assert gateway.accepted[0].likely_import_module == "Foo"
    assert gateway.accepted[0].known_risks == ["Coverage not verified."]


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
        flat_args={"summary": "Use native repo."},
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
    assert requirement.value.requirement.required_proof_availability == ProofAvailability.DECLARED
    assert requirement.value.requirement.provider_request_submitted_at is not None
    assert requirement.value.requirement.provider_result_observed_at is None
    assert requirement.value.requirement.note == "Need provider theorem."
    assert len(gateway.accepted) == 1
    assert gateway.accepted[0].submission_type == "coordinator_repo_requirement"
    assert gateway.accepted[0].required_proof_availability == ProofAvailability.DECLARED


def test_submit_repo_requirement_uses_consumer_repo_default_proof_availability(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    configured = runtime.repo_workspace.metadata.update_repo_config(
        tmp_path,
        target_proof_availability=ProofAvailability.PROVED,
        work_mode=RepoWorkMode.PROVED_FULL_GRAPH,
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
            "target_repo": "Provider",
            "summary": "Need proved provider repo.",
            "reason": "Need provider theorem with proof availability.",
        },
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is True
    requirement = runtime.repo_workspace.requirement.get_requirement(tmp_path, name="need_proved_provider")
    assert requirement.ok and requirement.value is not None
    assert requirement.value.requirement.required_proof_availability == ProofAvailability.PROVED
    assert gateway.accepted[0].required_proof_availability == ProofAvailability.PROVED


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
