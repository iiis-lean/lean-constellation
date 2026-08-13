from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.standard_steps import AgentStepState

from lean_constellation.domain.preparation import AutoProviderRoute, VerifiedAdapterRouteReceipt
from lean_constellation.flows.common.agent_steps import (
    AdapterDeclCatalogAgentStep,
    RepoFormatDiscoveryAgentStep,
    RootInterfacePrepareAgentStep,
    SourceCorpusBuilderAgentStep,
    SourceCorpusReviewerAgentStep,
    SourceIndexBuilderAgentStep,
    SourceIndexReviewerAgentStep,
)
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.repo_lifecycle.steps import (
    AdapterDeclCatalogStepResult,
    RepoFormatDiscoveryStepResult,
    RootInterfacePrepareStepResult,
    SourceCorpusBuilderStepResult,
    SourceCorpusReviewerStepResult,
    SourceIndexBuilderStepResult,
    SourceIndexReviewerStepResult,
)
from lean_constellation.flows.repo_lifecycle.submissions import (
    AdapterCatalogBlockedSubmission,
    RepoFormatAdapterChoiceSubmission,
    RepoFormatNativeChoiceSubmission,
    RootInterfacePrepareReadySubmission,
    SourceCorpusBuilderBlockedSubmission,
    SourceCorpusBuilderReadySubmission,
    SourceCorpusReviewSubmission,
    SourceIndexBuilderRoundSubmission,
    SourceIndexReviewerRoundSubmission,
)


def _start_host_flow(runtime: FakeLeanFlowRuntime, tmp_path: Path) -> str:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    return runtime.start_flow(
        "requirement_group_repo_bootstrap",
        {
            "target_repo": "repo",
            "repo_root": str(repo_root),
            "workspace_root": str(tmp_path),
            "requirement_refs": [],
            "resolved_provider_route": AutoProviderRoute().model_dump(mode="json"),
        },
        scope_id="repo:repo",
    )


def _state(role: str, agent_type: str) -> AgentStepState:
    return AgentStepState(
        agent_role=role,
        agent_type=agent_type,
        create_agent_if_missing=True,
        bind_created_agent_to="step",
        max_auto_continue_turns=0,
    )


def _run_step(runtime: FakeLeanFlowRuntime, step, submission=None):
    step_id = runtime.attach_step(step)
    if submission is not None:
        runtime.agent_service.queue_submission(submission)
    else:
        runtime.agent_service.queue_incomplete_turn()
    runtime.run_step(step_id)
    return runtime.flow_service.get_step(step_id)


def _adapter_receipt() -> VerifiedAdapterRouteReceipt:
    revision = "a" * 40
    return VerifiedAdapterRouteReceipt(
        git_url="https://github.com/example/upstream",
        revision=revision,
        package_name="upstream",
        likely_import_module="upstream",
        lean_toolchain="leanprover/lean4:v4.28.0",
        expected_lean_toolchain="leanprover/lean4:v4.28.0",
        expected_mathlib_revision="v4.28.0",
        revision_resolution="explicit",
        candidates_checked=[revision],
        evidence_summary="Remote probe matched the exact route.",
    )


def test_repo_format_discovery_agent_step_business_results(tmp_path: Path) -> None:
    runtime = create_fake_lean_flow_runtime(tmp_path / "ark")
    flow_id = _start_host_flow(runtime, tmp_path)

    adapter_step = _run_step(
        runtime,
        RepoFormatDiscoveryAgentStep(
            step_id="repo_format_adapter_step",
            flow_id=flow_id,
            scope_id="repo:repo",
            state=_state("repo_format_discovery", "RepoFormatDiscoveryAgent"),
        ),
        RepoFormatAdapterChoiceSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="repo_format_adapter_choice",
            tool_name="submit_adapter_repo_choice",
            git_url="https://github.com/example/upstream",
            revision="a" * 40,
            evidence_summary="Remote probe found lakefile.lean.",
            verified_route=_adapter_receipt(),
            summary="Use adapter.",
        ),
    )
    assert isinstance(adapter_step.result, RepoFormatDiscoveryStepResult)
    assert adapter_step.result.outcome == "adapter"

    native_step = _run_step(
        runtime,
        RepoFormatDiscoveryAgentStep(
            step_id="repo_format_native_step",
            flow_id=flow_id,
            scope_id="repo:repo",
            state=_state("repo_format_discovery", "RepoFormatDiscoveryAgent"),
        ),
        RepoFormatNativeChoiceSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="repo_format_native_choice",
            tool_name="submit_native_repo_choice",
            summary="Use native.",
            searched_targets=["upstream theorem"],
        ),
    )
    assert isinstance(native_step.result, RepoFormatDiscoveryStepResult)
    assert native_step.result.outcome == "native"


def test_source_corpus_builder_and_reviewer_agent_step_business_results(tmp_path: Path) -> None:
    runtime = create_fake_lean_flow_runtime(tmp_path / "ark")
    flow_id = _start_host_flow(runtime, tmp_path)

    prepared = _run_step(
        runtime,
        SourceCorpusBuilderAgentStep(
            step_id="source_corpus_builder_ready_step",
            flow_id=flow_id,
            scope_id="repo:repo",
            state=_state("source_corpus_builder", "SourceCorpusBuilderAgent"),
        ),
        SourceCorpusBuilderReadySubmission(
            submission_id=new_submission_id("sub"),
            submission_type="source_corpus_builder_ready",
            tool_name="submit_source_corpus_builder_ready",
            entry_path="README.md",
            overview="Readable corpus.",
            preparation_summary="Prepared.",
            summary="Prepared.",
        ),
    )
    assert isinstance(prepared.result, SourceCorpusBuilderStepResult)
    assert prepared.result.outcome == "ready"
    assert prepared.result.entry_path == "README.md"

    blocked = _run_step(
        runtime,
        SourceCorpusBuilderAgentStep(
            step_id="source_corpus_blocked_step",
            flow_id=flow_id,
            scope_id="repo:repo",
            state=_state("source_corpus_builder", "SourceCorpusBuilderAgent"),
        ),
        SourceCorpusBuilderBlockedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="source_corpus_builder_blocked",
            tool_name="submit_source_corpus_builder_blocked",
            reason="Missing source material.",
            summary="Blocked.",
        ),
    )
    assert isinstance(blocked.result, SourceCorpusBuilderStepResult)
    assert blocked.result.outcome == "blocked"
    assert blocked.result.blocked_reason == "Missing source material."

    reviewed = _run_step(
        runtime,
        SourceCorpusReviewerAgentStep(
            step_id="source_corpus_review_step",
            flow_id=flow_id,
            scope_id="repo:repo",
            state=_state("source_corpus_reviewer", "SourceCorpusReviewerAgent"),
        ),
        SourceCorpusReviewSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="source_corpus_review",
            tool_name="submit_source_corpus_review",
            approved=True,
            checked_materials=["README.md", "original/paper.pdf#page=1"],
            summary="Reviewed current corpus.",
        ),
    )
    assert isinstance(reviewed.result, SourceCorpusReviewerStepResult)
    assert reviewed.result.outcome == "approved"


def test_source_index_agent_step_business_results_and_incomplete(tmp_path: Path) -> None:
    runtime = create_fake_lean_flow_runtime(tmp_path / "ark")
    flow_id = _start_host_flow(runtime, tmp_path)

    builder = _run_step(
        runtime,
        SourceIndexBuilderAgentStep(
            step_id="source_index_builder_step",
            flow_id=flow_id,
            scope_id="repo:repo",
            state=_state("source_index_builder", "SourceIndexBuilderAgent"),
        ),
        SourceIndexBuilderRoundSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="source_index_builder_round",
            tool_name="submit_source_index_builder_round",
            validation_summary="Valid.",
            summary="Builder submitted.",
        ),
    )
    assert isinstance(builder.result, SourceIndexBuilderStepResult)
    assert builder.result.outcome == "submitted"

    reviewer = _run_step(
        runtime,
        SourceIndexReviewerAgentStep(
            step_id="source_index_reviewer_step",
            flow_id=flow_id,
            scope_id="repo:repo",
            state=_state("source_index_reviewer", "SourceIndexReviewerAgent"),
        ),
        SourceIndexReviewerRoundSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="source_index_reviewer_round",
            tool_name="submit_source_index_review_round",
            approved=False,
            feedback="Need better coverage.",
            summary="Rejected.",
        ),
    )
    assert isinstance(reviewer.result, SourceIndexReviewerStepResult)
    assert reviewer.result.outcome == "rejected"
    assert reviewer.result.feedback == "Need better coverage."

    incomplete = _run_step(
        runtime,
        SourceIndexBuilderAgentStep(
            step_id="source_index_incomplete_step",
            flow_id=flow_id,
            scope_id="repo:repo",
            state=_state("source_index_builder", "SourceIndexBuilderAgent"),
        ),
    )
    assert isinstance(incomplete.result, SourceIndexBuilderStepResult)
    assert incomplete.result.outcome == "incomplete"


def test_root_interface_and_adapter_catalog_agent_step_business_results(tmp_path: Path) -> None:
    runtime = create_fake_lean_flow_runtime(tmp_path / "ark")
    flow_id = _start_host_flow(runtime, tmp_path)

    root_ready = _run_step(
        runtime,
        RootInterfacePrepareAgentStep(
            step_id="root_interface_ready_step",
            flow_id=flow_id,
            scope_id="repo:repo",
            state=_state("root_interface_preparer", "RootInterfacePrepareAgent"),
        ),
        RootInterfacePrepareReadySubmission(
            submission_id=new_submission_id("sub"),
            submission_type="root_interface_prepare_ready",
            tool_name="submit_root_interface_prepare_ready",
            summary="Root interfaces ready.",
        ),
    )
    assert isinstance(root_ready.result, RootInterfacePrepareStepResult)
    assert root_ready.result.outcome == "ready"

    adapter_blocked = _run_step(
        runtime,
        AdapterDeclCatalogAgentStep(
            step_id="adapter_catalog_blocked_step",
            flow_id=flow_id,
            scope_id="repo:repo",
            state=_state("adapter_decl_catalog", "AdapterDeclCatalogAgent"),
        ),
        AdapterCatalogBlockedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="adapter_catalog_blocked",
            tool_name="submit_adapter_catalog_blocked",
            reason="No matching upstream declaration.",
            missing_interfaces=["main_result"],
            suggested_next_action="Pick another upstream.",
            summary="Blocked.",
        ),
    )
    assert isinstance(adapter_blocked.result, AdapterDeclCatalogStepResult)
    assert adapter_blocked.result.outcome == "blocked"
    assert adapter_blocked.result.missing_interfaces == ["main_result"]
