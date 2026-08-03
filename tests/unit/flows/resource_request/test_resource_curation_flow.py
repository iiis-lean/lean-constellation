from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.resource_request.submissions import (
    ExternalRepoRequiredSubmission,
    LocalResourceCreatedSubmission,
    ResourceDuplicateSubmission,
)
from tests.unit_services_helpers import make_runtime, valid_resource_readme


def _runtime(tmp_path: Path) -> tuple[FakeLeanFlowRuntime, object]:
    lean_runtime = make_runtime()
    flow_runtime = create_fake_lean_flow_runtime(
        tmp_path / "ark",
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    return flow_runtime, lean_runtime


def _start_resource_flow(runtime: FakeLeanFlowRuntime, repo_root: Path, *, target_kind: str, target: str) -> str:
    return runtime.start_flow(
        "resource_curation",
        {
            "repo_key": repo_root.name,
            "repo_root": str(repo_root),
            "target_kind": target_kind,
            "target": target,
            "requested_use": "supporting_material",
            "consumer_need": "Need the source as supporting evidence.",
            "requested_by": "content_plan",
            "context_summary": "Need stable material.",
            "node_path": "Main.Core",
        },
        scope_id=f"repo:{repo_root.name}",
    )


def _advance_and_run(runtime: FakeLeanFlowRuntime, flow_id: str) -> str:
    step_id = runtime.flow_service.advance_flow(flow_id)
    assert step_id is not None
    runtime.run_step(step_id)
    return step_id


def _create_local_resource(lean_runtime, repo_root: Path, *, target_kind: str, target: str) -> str:
    normalized_target = lean_runtime.material.resource_curation.prepare_resource_target(
        target_kind=target_kind,
        target=target,
    )
    assert normalized_target.ok and normalized_target.value is not None
    draft = lean_runtime.material.allocate_resource_draft(repo_root, target=normalized_target.value)
    assert draft.ok and draft.value is not None
    Path(draft.value.readme_path).write_text(
        valid_resource_readme(
            original_ref="Original unavailable: this fixture is normalized-only.",
        ),
        encoding="utf-8",
    )
    Path(draft.value.normalized_dir, "main.md").write_text("Normalized theorem background.\n", encoding="utf-8")
    promoted = lean_runtime.material.resource_curation.submit_local_resource_created(
        repo_root,
        target=normalized_target.value,
        draft_id=draft.value.draft.draft_id,
        summary="Curated local resource.",
        classification_reason="The note is supporting material.",
        resource_role="Background theorem note.",
        consumer_formalization_scope="This repo proves its own theorem.",
    )
    assert promoted.ok and promoted.value is not None
    assert promoted.value.resource_key is not None
    return promoted.value.resource_key


def _write_draft_files(draft_root: Path) -> None:
    (draft_root / "README.md").write_text(valid_resource_readme(), encoding="utf-8")
    (draft_root / "original" / "raw.txt").write_text("raw resource text\n", encoding="utf-8")
    (draft_root / "normalized" / "main.md").write_text("normalized resource text\n", encoding="utf-8")


def test_resource_curation_preflight_duplicate_hint_continues_to_agent(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    target = "https://example.com/math/page"
    resource_key = _create_local_resource(lean_runtime, repo_root, target_kind="web", target=target)
    flow_id = _start_resource_flow(runtime, repo_root, target_kind="web", target=target)

    _advance_and_run(runtime, flow_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.RUNNING
    assert flow.state.position.phase == "curator_agent"
    assert flow.state.active_resource_draft_key is not None
    assert flow.state.resource_duplicate_hint.existing_resource_key == resource_key
    assert runtime.agent_service.start_records == []

    runtime.agent_service.queue_submission(
        ResourceDuplicateSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="resource_duplicate",
            tool_name="submit_resource_duplicate",
            repo_key=repo_root.name,
            target_kind="web",
            target=target,
            existing_kind="resource",
            duplicate_reason="Already curated.",
            existing_resource_key=resource_key,
            summary="Duplicate.",
        )
    )
    _advance_and_run(runtime, flow_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    assert flow.result.outcome == "duplicate"
    assert flow.result.existing_resource_key == resource_key
    assert flow.state.active_resource_draft_key is None


def test_resource_curation_local_resource_created_result(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    target_file = tmp_path / "paper-note.md"
    target_file.write_text("A useful lemma appears here.\n", encoding="utf-8")
    flow_id = _start_resource_flow(runtime, repo_root, target_kind="local_file", target=str(target_file))

    _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.position.phase == "curator_agent"
    draft_id = flow.state.active_resource_draft_key
    assert draft_id is not None
    draft = lean_runtime.material.get_resource_draft(repo_root, draft_id=draft_id)
    assert draft.ok and draft.value is not None
    _write_draft_files(Path(draft.value.draft_root))
    runtime.agent_service.queue_submission(
        LocalResourceCreatedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="local_resource_created",
            tool_name="submit_local_resource_created",
            repo_key=repo_root.name,
            target_kind="local_file",
            target=str(target_file),
            draft_id=draft_id,
            classification_reason="The note is supporting material.",
            resource_role="Background theorem note.",
            consumer_formalization_scope="This repo proves its own theorem.",
            summary="Curated local resource.",
        )
    )
    _advance_and_run(runtime, flow_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    assert flow.result.outcome == "local_resource_created"
    assert flow.result.resource_key is not None
    assert lean_runtime.material.resource_library.get_resource(repo_root, resource_key=flow.result.resource_key).ok


def test_resource_curation_external_repo_required_result_does_not_create_resource(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    target = "2501.12345"
    flow_id = _start_resource_flow(runtime, repo_root, target_kind="arxiv", target=target)

    _advance_and_run(runtime, flow_id)
    allocated_flow = runtime.flow_service.get_flow(flow_id)
    draft_id = allocated_flow.state.active_resource_draft_key
    assert draft_id is not None
    runtime.agent_service.queue_submission(
        ExternalRepoRequiredSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="external_repo_required",
            tool_name="submit_external_repo_required",
            repo_key=repo_root.name,
            target_kind="arxiv",
            target=target,
            reason="The paper should be a reusable provider repo.",
            source_description="A paper-scale source.",
            classification_reason="The paper defines an independent reusable theory.",
            relation_to_current_repo_or_node="The consumer needs one public theorem.",
            consumer_need="A stable statement of the main theorem.",
            provider_scope="Formalize the paper's reusable theory.",
            suggested_repo_name="provider_paper",
            summary="Needs provider repo.",
        )
    )
    _advance_and_run(runtime, flow_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    assert flow.result.outcome == "external_repo_required"
    assert flow.result.external_repo is not None
    assert flow.result.external_repo.suggested_repo_name == "provider_paper"
    assert lean_runtime.material.resource_library.list_resources(repo_root).value == []
    abandoned = lean_runtime.material.get_resource_draft(repo_root, draft_id=draft_id)
    assert abandoned.ok and abandoned.value is not None
    assert str(abandoned.value.draft.status) == "abandoned"
    assert flow.state.active_resource_draft_key is None
