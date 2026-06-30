from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.foundation import GateReport, ServiceResult
from lean_constellation.services.node import ContentTaskOutcome, ContentTaskResultView, NodeService


class FakeContentReadyGate:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, str]] = []

    def check_content_node_ready(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        self.calls.append((Path(repo_root), node_path))
        return ServiceResult(
            ok=True,
            value=GateReport(
                gate_name="content_node_ready",
                passed=True,
                summary="Fake content readiness passed.",
            ),
        )


def _service_with_content_node(repo_root: Path) -> tuple[NodeService, FakeContentReadyGate]:
    runtime = make_runtime()
    ready_gate = FakeContentReadyGate()
    service = NodeService(runtime, content_ready_gate=ready_gate)
    assert service.node_tree.ensure_root_scope_node(repo_root).ok
    assert service.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal.", boundary="Topic boundary.").ok
    content = service.create_content_node(
        repo_root,
        path="Main.Topic.Core",
        goal="Core goal.",
        boundary="Core boundary.",
        objective="Build core content.",
        success_criteria="Core content is ready.",
    )
    assert content.ok, content.issues
    return service, ready_gate


@pytest.mark.real
def test_content_task_ready_finalize_persists_and_reloads_real(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    service, ready_gate = _service_with_content_node(repo_root)

    finalized = service.finalize_content_task_result(
        repo_root,
        node_path="Main.Topic.Core",
        task_result=ContentTaskResultView(outcome=ContentTaskOutcome.READY, summary="Content task finished ready."),
        coordinator_summary="Coordinator accepted the ready content task.",
    )

    assert finalized.ok, finalized.issues
    assert finalized.value is not None
    assert finalized.value.contract_summary_written is True
    assert finalized.value.contract_committed is True
    assert ready_gate.calls == [(repo_root, "Main.Topic.Core")]

    reloaded = make_runtime().node.contract.get_current_contract(repo_root, node_path="Main.Topic.Core")
    assert reloaded.ok, reloaded.issues
    assert reloaded.value is not None
    assert reloaded.value.version_status.value == "committed"
    assert reloaded.value.contract.summary == "Coordinator accepted the ready content task."
    assert reloaded.value.contract.committed_at is not None


@pytest.mark.real
def test_content_task_blocked_finalize_persists_summary_without_commit_real(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    service, ready_gate = _service_with_content_node(repo_root)

    finalized = service.finalize_content_task_result(
        repo_root,
        node_path="Main.Topic.Core",
        task_result=ContentTaskResultView(
            outcome=ContentTaskOutcome.BLOCKED,
            summary="Content task needs more material.",
            reason="A cited source is missing from the repo resource library.",
        ),
        coordinator_summary="Coordinator recorded the blocked content task.",
    )

    assert finalized.ok, finalized.issues
    assert finalized.value is not None
    assert finalized.value.contract_summary_written is True
    assert finalized.value.contract_committed is False
    assert ready_gate.calls == []

    reloaded = make_runtime().node.contract.get_current_contract(repo_root, node_path="Main.Topic.Core")
    assert reloaded.ok, reloaded.issues
    assert reloaded.value is not None
    assert reloaded.value.version_status.value == "open"
    assert reloaded.value.contract.summary == "Coordinator recorded the blocked content task."
    assert reloaded.value.contract.committed_at is None
