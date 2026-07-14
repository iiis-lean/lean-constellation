from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_runtime_kit.flow.models import BaseSubmission, FlowStatus

from lean_constellation.domain.repo import ProofAvailability, RepoWorkMode
from lean_constellation.domain.repo_run import SourceScope
from lean_constellation.services import LeanProviderOverrides, create_test_runtime_services
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


def test_repo_ready_submit_only_records_candidate_intent_after_preview(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = _runtime(gateway)
    assert register_submit_tooling(runtime).ok
    owner_flow = SimpleNamespace(
        flow_id="flow_1",
        flow_type="native_repo_coordinator",
        scope_id=f"repo:{tmp_path.name}",
        status="running",
        state=SimpleNamespace(position="coordinator_agent"),
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
                target_proof_availability=ProofAvailability.DECLARED,
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
    assert calls == [
        {
            "repo_root": tmp_path,
            "base_release_id": "release-base",
            "summary": "Publish the current candidate.",
        }
    ]


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
        flat_args={"summary": "  Use native repo.  "},
    )

    assert blank.ok
    assert blank.value is not None
    assert blank.value.ok is False
    assert blank.value.issues[0].kind == "tool_arguments_invalid"
    assert stripped.ok
    assert stripped.value is not None
    assert stripped.value.ok is True
    assert gateway.accepted[0].summary == "Use native repo."


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
    assert gateway.accepted[0].interfaces[0]["expected_statement_lean_code"] == (
        "theorem ReusableMath.main_result : True := by sorry"
    )


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
