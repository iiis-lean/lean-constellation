from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_runtime_kit.flow.models import BaseSubmission
from lean_constellation.services import LeanProviderOverrides, create_test_runtime_services
from lean_constellation.services.tool_facade import SubmitBehavior
from lean_constellation.services.tool_facade import RawToolCallContext, RuntimeToolContext
from lean_constellation.tools import register_submit_tooling
from tests.unit.tools._submit_family_helpers import assert_submit_tools, submit_specs


class FakeSubmissionGateway:
    def __init__(self) -> None:
        self.accepted: list[BaseSubmission] = []

    def accept_step_submission(self, ctx, submission: BaseSubmission):
        del ctx
        self.accepted.append(submission)
        return {"accepted": True}


class FakeResourceFlowService:
    def __init__(self, *, flow_id: str = "flow_resource", active_draft_id: str | None = "draft_active") -> None:
        self.flow_id = flow_id
        self.flow = SimpleNamespace(
            flow_type="resource_curation",
            input=SimpleNamespace(
                target=SimpleNamespace(kind="web", target="https://example.com/current", arxiv_version=None),
            ),
            state=SimpleNamespace(active_resource_draft_key=active_draft_id),
        )

    def get_flow(self, flow_id: str):
        assert flow_id == self.flow_id
        return self.flow


def test_resource_submit_tools_registered() -> None:
    assert_submit_tools(
        {
            "submit_resource_duplicate",
            "submit_local_resource_created",
            "submit_external_repo_required",
            "submit_resource_rejected",
        },
        behavior=SubmitBehavior.TERMINAL,
    )
    specs = submit_specs()
    assert specs["submit_resource_request"].submit_behavior == SubmitBehavior.DISPATCH_CHILD_FLOWS


def test_resource_request_submit_injects_runtime_repo_context(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = create_test_runtime_services(providers=LeanProviderOverrides(submission_gateway=gateway))
    assert register_submit_tooling(runtime).ok
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    raw = RawToolCallContext(
        endpoint_view_key="content_plan_submit",
        runtime_context=RuntimeToolContext(
            flow_id="flow_1",
            step_id="step_1",
            agent_id="agent_1",
            scope_id="repo:Repo:node:Main.Core",
            agent_type="ContentPlanAgent",
            agent_role="plan",
            expected_view_key="content_plan_submit",
            repo_root=repo_root,
            node_path="Main.Core",
        ),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_resource_request",
        flat_args={
            "target_kind": "web",
            "target": "https://example.com/source",
            "requested_use": "supporting_material",
            "consumer_need": "Need theorem background.",
            "context_summary": "Need background.",
            "summary": "Request resource curation.",
        },
    )

    assert result.ok and result.value is not None
    assert result.value.ok is True
    assert len(gateway.accepted) == 1
    submission = gateway.accepted[0]
    assert submission.submission_type == "content_resource_request"
    request = submission.requests[0]
    assert request.flow_type == "resource_curation"
    assert request.params["repo_key"] == "Repo"
    assert request.params["repo_root"] == str(repo_root)
    assert request.params["node_path"] == "Main.Core"


def _resource_curator_raw(repo_root: Path) -> RawToolCallContext:
    return RawToolCallContext(
        endpoint_view_key="resource_curator_submit",
        runtime_context=RuntimeToolContext(
            flow_id="flow_resource",
            step_id="step_resource",
            agent_id="agent_resource",
            agent_type="ResourceCuratorAgent",
            agent_role="worker",
            expected_view_key="resource_curator_submit",
            repo_root=repo_root,
        ),
    )


def test_resource_curator_submit_rejects_removed_target_fields(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = create_test_runtime_services(providers=LeanProviderOverrides(submission_gateway=gateway))
    runtime.ark.flow_service = FakeResourceFlowService()
    assert register_submit_tooling(runtime).ok

    result = runtime.tool_facade.invoke_agent_tool(
        _resource_curator_raw(tmp_path),
        tool_name="submit_resource_rejected",
        flat_args={
            "reason": "Wrong target.",
            "target_kind": "web",
            "target": "https://example.com/other",
        },
    )

    assert result.ok and result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind == "tool_arguments_invalid"
    assert gateway.accepted == []


def test_resource_curator_duplicate_submit_rejects_removed_target_fields(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = create_test_runtime_services(providers=LeanProviderOverrides(submission_gateway=gateway))
    runtime.ark.flow_service = FakeResourceFlowService()
    assert register_submit_tooling(runtime).ok

    result = runtime.tool_facade.invoke_agent_tool(
        _resource_curator_raw(tmp_path),
        tool_name="submit_resource_duplicate",
        flat_args={
            "target_kind": "web",
            "target": "https://example.com/other",
            "existing_kind": "resource",
            "duplicate_reason": "Already curated.",
            "existing_resource_key": "res_existing",
        },
    )

    assert result.ok and result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind == "tool_arguments_invalid"
    assert gateway.accepted == []


def test_resource_curator_external_submit_rejects_removed_target_fields(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = create_test_runtime_services(providers=LeanProviderOverrides(submission_gateway=gateway))
    runtime.ark.flow_service = FakeResourceFlowService()
    assert register_submit_tooling(runtime).ok

    result = runtime.tool_facade.invoke_agent_tool(
        _resource_curator_raw(tmp_path),
        tool_name="submit_external_repo_required",
        flat_args={
            "reason": "Wrong target.",
            "target_kind": "web",
            "target": "https://example.com/other",
            "source_description": "Reusable source.",
            "classification_reason": "Independent theory.",
            "relation_to_current_repo_or_node": "Consumer dependency.",
            "consumer_need": "Main theorem API.",
            "provider_scope": "Own the reusable theory.",
        },
    )

    assert result.ok and result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind == "tool_arguments_invalid"
    assert gateway.accepted == []


def test_resource_curator_local_submit_rejects_non_active_draft_before_gateway(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = create_test_runtime_services(providers=LeanProviderOverrides(submission_gateway=gateway))
    runtime.ark.flow_service = FakeResourceFlowService(active_draft_id="draft_active")
    assert register_submit_tooling(runtime).ok

    result = runtime.tool_facade.invoke_agent_tool(
        _resource_curator_raw(tmp_path),
        tool_name="submit_local_resource_created",
        flat_args={
            "summary": "Promote draft.",
            "draft_id": "draft_other",
            "classification_reason": "Supporting material.",
            "resource_role": "Background.",
            "consumer_formalization_scope": "Current repo owns the proof.",
        },
    )

    assert result.ok and result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind == "resource_active_draft_mismatch"
    assert gateway.accepted == []


def test_resource_curator_local_submit_validates_active_draft_without_finalizing(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = create_test_runtime_services(providers=LeanProviderOverrides(submission_gateway=gateway))
    target = runtime.material.normalize_resource_target("https://example.com/current")
    assert target.ok and target.value is not None
    draft = runtime.material.allocate_resource_draft(tmp_path, target=target.value, title_hint="Current")
    assert draft.ok and draft.value is not None, draft.issues
    draft_root = Path(draft.value.draft_root)
    (draft_root / "README.md").write_text("# Current\n\nCurated resource.\n", encoding="utf-8")
    (draft_root / "original" / "raw.txt").write_text("raw resource text\n", encoding="utf-8")
    (draft_root / "normalized" / "main.md").write_text("normalized resource text\n", encoding="utf-8")
    runtime.ark.flow_service = FakeResourceFlowService(active_draft_id=draft.value.draft.draft_id)
    assert register_submit_tooling(runtime).ok

    result = runtime.tool_facade.invoke_agent_tool(
        _resource_curator_raw(tmp_path),
        tool_name="submit_local_resource_created",
        flat_args={
            "summary": "Promote active draft.",
            "draft_id": draft.value.draft.draft_id,
            "classification_reason": "Supporting material.",
            "resource_role": "Background.",
            "consumer_formalization_scope": "Current repo owns the proof.",
        },
    )

    assert result.ok and result.value is not None
    assert result.value.ok is True
    assert len(gateway.accepted) == 1
    submission = gateway.accepted[0]
    assert submission.submission_type == "local_resource_created"
    assert submission.resource_key
    loaded = runtime.material.resource_library.get_resource(tmp_path, resource_key=submission.resource_key)
    assert not loaded.ok
    assert loaded.issues[0].kind == "resource_not_found"
    draft_after = runtime.material.get_resource_draft(tmp_path, draft_id=draft.value.draft.draft_id)
    assert draft_after.ok and draft_after.value is not None
    assert str(draft_after.value.draft.status) == "allocated"


def test_resource_curator_local_submit_gateway_missing_does_not_finalize_draft(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    target = runtime.material.normalize_resource_target("https://example.com/current")
    assert target.ok and target.value is not None
    draft = runtime.material.allocate_resource_draft(tmp_path, target=target.value, title_hint="Current")
    assert draft.ok and draft.value is not None, draft.issues
    draft_root = Path(draft.value.draft_root)
    (draft_root / "README.md").write_text("# Current\n\nCurated resource.\n", encoding="utf-8")
    (draft_root / "original" / "raw.txt").write_text("raw resource text\n", encoding="utf-8")
    (draft_root / "normalized" / "main.md").write_text("normalized resource text\n", encoding="utf-8")
    runtime.ark.flow_service = FakeResourceFlowService(active_draft_id=draft.value.draft.draft_id)
    assert register_submit_tooling(runtime).ok

    result = runtime.tool_facade.invoke_agent_tool(
        _resource_curator_raw(tmp_path),
        tool_name="submit_local_resource_created",
        flat_args={
            "summary": "Promote active draft.",
            "draft_id": draft.value.draft.draft_id,
            "classification_reason": "Supporting material.",
            "resource_role": "Background.",
            "consumer_formalization_scope": "Current repo owns the proof.",
        },
    )

    assert result.ok and result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind == "submission_gateway_missing"
    resource_key = runtime.material.resource_library.resource_key_for_target(target.value)
    assert resource_key.ok and resource_key.value is not None
    loaded = runtime.material.resource_library.get_resource(tmp_path, resource_key=resource_key.value)
    assert not loaded.ok
    assert loaded.issues[0].kind == "resource_not_found"
    draft_after = runtime.material.get_resource_draft(tmp_path, draft_id=draft.value.draft.draft_id)
    assert draft_after.ok and draft_after.value is not None
    assert str(draft_after.value.draft.status) == "allocated"
