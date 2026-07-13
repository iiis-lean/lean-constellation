from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.domain.repo import (
    RepoFormat,
    RepoPublicationState,
    RepoPublicationStatus,
)
from lean_constellation.services.external_clients import ToolchainCommandView
from lean_constellation.services.foundation import WriteMode
from lean_constellation.services.material.source_index import SourceIndexSchemaCompatibilityView
from lean_constellation.services.validation_snapshot import (
    ArkRuntimeSnapshotRef,
    CandidateReleaseGateView,
    ValidationSnapshotService,
)
from tests.unit.services.repo_workspace.test_repo_release import _prepare_release_repo


class _PauseController:
    def __init__(self) -> None:
        self.paused = True

    def is_paused(self) -> bool:
        return self.paused

    def pause(self, scope_id=None) -> None:  # noqa: ANN001
        del scope_id
        self.paused = True


class _StableRuntime:
    def __init__(self, foundation) -> None:  # noqa: ANN001
        self.foundation = foundation

    def check_repo_stable_point(self, repo_root, *, checkpoint_kind, node_paths=None):  # noqa: ANN001
        del repo_root, checkpoint_kind, node_paths
        return self.foundation.ok(self.foundation.gate_passed("runtime_stability"))


class _ArkSnapshots:
    def __init__(self, foundation) -> None:  # noqa: ANN001
        self.foundation = foundation
        self.created = 0

    def create_runtime_snapshot(self, repo_root, *, scope_ids, label=None):  # noqa: ANN001
        del repo_root, label
        self.created += 1
        return self.foundation.ok(ArkRuntimeSnapshotRef(
            snapshot_id=f"ark_{self.created}", scope_ids=scope_ids
        ))

    def restore_runtime_snapshot(self, repo_root, *, snapshot_id, leave_runtime_paused=True):  # noqa: ANN001
        del repo_root, leave_runtime_paused
        return self.foundation.ok(self.foundation.mutation_view(
            object_ref=snapshot_id, changed=True, summary="restored"
        ))


def _legacy_repo(repo_root: Path, monkeypatch):  # noqa: ANN001
    runtime, _ = _prepare_release_repo(repo_root)
    runtime.ark.flow_service = SimpleNamespace(list_flows=lambda **_filters: [])
    runtime.ark.step_service = SimpleNamespace(list_steps=lambda **_filters: [])
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
    assert runtime.repo_workspace.metadata.set_repo_format(
        repo_root, repo_format=RepoFormat.NATIVE, reason="legacy fixture"
    ).ok
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(repo_root),
        RepoPublicationState(status=RepoPublicationStatus.STABLE),
        mode=WriteMode.OVERWRITE,
    ).ok
    for node in runtime.node.node_tree.get_node_tree(repo_root).value.nodes:
        if node.kind.value != "content":
            continue
        contract = runtime.node.contract.get_visible_contract(repo_root, node_path=node.path).value.contract
        contract.decl_graph_head = {}
        assert runtime.foundation.store.write_json_atomic(
            runtime.node.node_tree.node_store.contract_path(
                repo_root, node_id=node.node_id, version=contract.version
            ),
            contract,
            mode=WriteMode.UPDATE_EXISTING,
        ).ok
        for decl in runtime.decl_graph.list_decls(repo_root, node_path=node.path).value:
            revision = runtime.decl_graph.get_decl_revision(
                repo_root,
                node_path=node.path,
                name=decl.name,
                revision=decl.current_revision,
            ).value
            path_view = runtime.lean_projection.decl_file.derive_decl_file_path(
                repo_root, node_path=node.path, decl_name=decl.name, kind=decl.kind
            ).value
            path = Path(path_view.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(revision.proof.formal.code, encoding="utf-8")
    runtime.ark.pause_controller = _PauseController()
    snapshots = _ArkSnapshots(runtime.foundation)
    runtime.app.validation_snapshot = ValidationSnapshotService(
        runtime,
        runtime_stability_provider=_StableRuntime(runtime.foundation),
        ark_snapshot_provider=snapshots,
    )
    for node in runtime.node.node_tree.get_node_tree(repo_root).value.nodes:
        if node.kind.value == "content":
            captured = runtime.node.release_guard.capture_content_contract_head(
                repo_root, node_path=node.path
            )
            assert captured.ok, [(issue.kind, issue.message) for issue in captured.issues]
    source_view = SourceIndexSchemaCompatibilityView(
        stored_schema_version=3,
        migration_required=False,
        source_index_status="committed",
        current_digest="a" * 64,
        summary="current",
    )
    monkeypatch.setattr(
        runtime.material.source_index,
        "inspect_source_index_schema",
        lambda _root: runtime.foundation.ok(source_view),
    )

    def candidate(_root, *, base_release_id, summary, owner_flow_id, legacy_adoption):  # noqa: ANN001
        del base_release_id, summary, owner_flow_id
        assert legacy_adoption
        versions = {}
        for node in runtime.node.node_tree.get_node_tree(repo_root).value.nodes:
            current = runtime.node.contract.get_visible_contract(repo_root, node_path=node.path).value.contract
            versions[node.node_id] = (
                current.version + 1
                if node.kind.value == "content" and not current.decl_graph_head
                else current.version
            )
        config = runtime.repo_workspace.metadata.get_repo_config(repo_root).value.config
        gate = runtime.foundation.gate_passed("candidate_repo_release")
        return runtime.foundation.ok(CandidateReleaseGateView(
            base_release_id=None,
            candidate_node_contract_versions=versions,
            target_proof_availability=config.target_proof_availability,
            gate=gate,
            summary="candidate passed",
        ))

    monkeypatch.setattr(runtime.validation_snapshot.release_finalizer, "_preview_release", candidate)
    monkeypatch.setattr(
        runtime.external.lean_toolchain,
        "run_lake_build",
        lambda _root: ToolchainCommandView(
            ok=True, command=["lake", "build"], exit_code=0, summary="built"
        ),
    )
    return runtime, snapshots


def test_legacy_adoption_dry_run_is_read_only_and_adapter_is_blocked(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    runtime, snapshots = _legacy_repo(tmp_path, monkeypatch)
    before = runtime.validation_snapshot.release_finalizer.compute_candidate_digest(tmp_path)

    preview = runtime.validation_snapshot.adopt_legacy_stable_repo(
        tmp_path, summary="Adopt legacy release.", dry_run=True
    )

    assert preview.ok and preview.value is not None
    assert preview.value.outcome == "eligible", preview.value.preview.blocking_issue_kinds
    assert runtime.validation_snapshot.release_finalizer.compute_candidate_digest(tmp_path) == before
    assert snapshots.created == 0
    assert not runtime.validation_snapshot.snapshot_restore._snapshot_root(tmp_path).exists()

    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_format_path(tmp_path),
        {"repo_format": RepoFormat.ADAPTER.value, "reason": "adapter fixture"},
        mode=WriteMode.OVERWRITE,
    ).ok
    blocked = runtime.validation_snapshot.adopt_legacy_stable_repo(
        tmp_path, summary="Must reject adapter.", dry_run=True
    )
    assert blocked.ok and blocked.value is not None and blocked.value.outcome == "blocked"
    assert "legacy_adoption_repo_not_native" in blocked.value.preview.blocking_issue_kinds


def test_legacy_adoption_dry_run_blocks_when_flow_truth_cannot_be_inspected(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    runtime, snapshots = _legacy_repo(tmp_path, monkeypatch)
    before = runtime.validation_snapshot.release_finalizer.compute_candidate_digest(tmp_path)
    monkeypatch.setattr(
        runtime,
        "list_flows",
        lambda: (_ for _ in ()).throw(RuntimeError("flow store unavailable")),
    )
    monkeypatch.setattr(
        runtime,
        "list_steps",
        lambda: (_ for _ in ()).throw(AssertionError("Step truth must not be queried after Flow failure")),
    )

    result = runtime.validation_snapshot.adopt_legacy_stable_repo(
        tmp_path, summary="Inspect legacy release.", dry_run=True
    )

    assert result.ok and result.value is not None and result.value.outcome == "blocked"
    findings = [issue for issue in result.value.preview.gate.issues if issue.kind == "release_workflow_inspection_failed"]
    assert findings and {issue.field for issue in findings} == {"flows"}
    assert runtime.validation_snapshot.release_finalizer.compute_candidate_digest(tmp_path) == before
    assert snapshots.created == 0
    assert not runtime.validation_snapshot.snapshot_restore._snapshot_root(tmp_path).exists()


def test_legacy_adoption_dry_run_keeps_active_flow_finding_when_step_inspection_fails(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    runtime, snapshots = _legacy_repo(tmp_path, monkeypatch)
    before = runtime.validation_snapshot.release_finalizer.compute_candidate_digest(tmp_path)
    active = SimpleNamespace(
        flow_id="active_content_flow",
        flow_type="content_node_task",
        scope_id=f"repo:{tmp_path.name}:node:Main.Results",
        status=FlowStatus.RUNNING,
    )
    monkeypatch.setattr(runtime, "list_flows", lambda: [active])
    monkeypatch.setattr(
        runtime,
        "list_steps",
        lambda: (_ for _ in ()).throw(RuntimeError("step store unavailable")),
    )

    result = runtime.validation_snapshot.adopt_legacy_stable_repo(
        tmp_path, summary="Inspect legacy release.", dry_run=True
    )

    assert result.ok and result.value is not None and result.value.outcome == "blocked"
    issues = result.value.preview.gate.issues
    assert any(issue.kind == "release_workflow_not_closed" and issue.object_ref == active.flow_id for issue in issues)
    assert any(issue.kind == "release_workflow_inspection_failed" and issue.field == "steps" for issue in issues)
    assert runtime.validation_snapshot.release_finalizer.compute_candidate_digest(tmp_path) == before
    assert snapshots.created == 0
    assert not runtime.validation_snapshot.snapshot_restore._snapshot_root(tmp_path).exists()


def test_legacy_adoption_creates_new_contract_versions_and_release_r1(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    runtime, snapshots = _legacy_repo(tmp_path, monkeypatch)
    old_contract_bytes = {}
    for node in runtime.node.node_tree.get_node_tree(tmp_path).value.nodes:
        if node.kind.value == "content":
            path = runtime.node.node_tree.node_store.contract_path(
                tmp_path, node_id=node.node_id, version=1
            )
            old_contract_bytes[node.node_id] = path.read_bytes()

    adopted = runtime.validation_snapshot.adopt_legacy_stable_repo(
        tmp_path, summary="Adopt legacy release.", dry_run=False
    )

    assert adopted.ok, [(issue.kind, issue.message) for issue in adopted.issues]
    assert adopted.value is not None and adopted.value.outcome == "adopted"
    assert adopted.value.finalized is not None
    release = adopted.value.finalized.release.release
    assert release.parent_release_id is None
    assert snapshots.created == 2  # pre-adoption checkpoint + final release checkpoint
    publication = runtime.repo_workspace.metadata.get_repo_publication(tmp_path).value.publication
    assert publication.status == RepoPublicationStatus.STABLE
    assert publication.latest_release_id == release.release_id
    for node in runtime.node.node_tree.get_node_tree(tmp_path).value.nodes:
        if node.kind.value != "content":
            continue
        assert runtime.node.contract.get_visible_contract(tmp_path, node_path=node.path).value.version == 2
        old_path = runtime.node.node_tree.node_store.contract_path(
            tmp_path, node_id=node.node_id, version=1
        )
        assert old_path.read_bytes() == old_contract_bytes[node.node_id]
    assert runtime.ark.pause_controller.is_paused()


def test_legacy_adoption_failure_restores_contract_truth_and_stays_paused(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    runtime, _ = _legacy_repo(tmp_path, monkeypatch)
    original = runtime.node.adopt_committed_content_contract_head
    calls = 0

    def fail_second(*args, **kwargs):  # noqa: ANN001
        nonlocal calls
        calls += 1
        if calls == 2:
            return runtime.foundation.fail(runtime.foundation.issue("injected_contract_failure", "stop"))
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.node, "adopt_committed_content_contract_head", fail_second)
    result = runtime.validation_snapshot.adopt_legacy_stable_repo(
        tmp_path, summary="Adopt legacy release.", dry_run=False
    )

    assert not result.ok and result.issues[0].kind == "injected_contract_failure"
    publication = runtime.repo_workspace.metadata.get_repo_publication(tmp_path).value.publication
    assert publication.status == RepoPublicationStatus.STABLE and publication.latest_release_id is None
    for node in runtime.node.node_tree.get_node_tree(tmp_path).value.nodes:
        if node.kind.value == "content":
            contract = runtime.node.contract.get_visible_contract(tmp_path, node_path=node.path).value
            assert contract.version == 1 and contract.contract.decl_graph_head == {}
    assert runtime.ark.pause_controller.is_paused()


def test_legacy_adoption_restore_primitive_failure_is_reported_and_runtime_stays_paused(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    runtime, _ = _legacy_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runtime.node,
        "adopt_committed_content_contract_head",
        lambda *args, **kwargs: runtime.foundation.fail(
            runtime.foundation.issue("injected_adoption_failure", "stop")
        ),
    )
    monkeypatch.setattr(
        runtime.validation_snapshot.snapshot_restore,
        "restore_repo_checkpoint_snapshot",
        lambda *args, **kwargs: runtime.foundation.fail(
            runtime.foundation.issue("injected_restore_failure", "restore stopped")
        ),
    )

    result = runtime.validation_snapshot.adopt_legacy_stable_repo(
        tmp_path, summary="Adopt legacy release.", dry_run=False
    )

    assert not result.ok and result.issues[0].kind == "legacy_adoption_restore_failed"
    assert result.issues[0].details == {
        "adoption_issues": "injected_adoption_failure",
        "restore_issues": "injected_restore_failure",
    }
    assert runtime.ark.pause_controller.is_paused()


@pytest.mark.parametrize(
    "failure_boundary",
    [
        "source_index_migration",
        "post_migration_build",
        "release_checkpoint",
        "release_create",
        "publication_commit",
    ],
)
def test_legacy_adoption_late_failure_boundaries_restore_pre_adoption_truth(
    tmp_path: Path, monkeypatch, failure_boundary: str
) -> None:  # noqa: ANN001
    runtime, _ = _legacy_repo(tmp_path, monkeypatch)
    if failure_boundary == "source_index_migration":
        source_view = SourceIndexSchemaCompatibilityView(
            stored_schema_version=2,
            migration_required=True,
            source_index_status="committed",
            current_digest="b" * 64,
            summary="migration required",
        )
        monkeypatch.setattr(
            runtime.material.source_index,
            "inspect_source_index_schema",
            lambda _root: runtime.foundation.ok(source_view),
        )
        monkeypatch.setattr(
            runtime.material.source_index,
            "migrate_source_index_schema",
            lambda *args, **kwargs: runtime.foundation.fail(
                runtime.foundation.issue("injected_source_migration", "stop")
            ),
        )
    elif failure_boundary == "post_migration_build":
        calls = 0

        def fail_second_build(_root):  # noqa: ANN001
            nonlocal calls
            calls += 1
            if calls == 2:
                return ToolchainCommandView(
                    ok=False,
                    command=["lake", "build"],
                    exit_code=1,
                    stderr_excerpt="injected",
                    summary="failed",
                )
            return ToolchainCommandView(
                ok=True, command=["lake", "build"], exit_code=0, summary="built"
            )

        monkeypatch.setattr(runtime.external.lean_toolchain, "run_lake_build", fail_second_build)
    elif failure_boundary == "release_checkpoint":
        monkeypatch.setattr(
            runtime.validation_snapshot.snapshot_restore,
            "create_repo_release_checkpoint",
            lambda *args, **kwargs: runtime.foundation.fail(
                runtime.foundation.issue("injected_release_checkpoint", "stop")
            ),
        )
    elif failure_boundary == "release_create":
        monkeypatch.setattr(
            runtime.repo_workspace.release,
            "create_release",
            lambda *args, **kwargs: runtime.foundation.fail(
                runtime.foundation.issue("injected_release_create", "stop")
            ),
        )
    else:
        publication_path = runtime.repo_workspace.metadata._repo_publication_path(tmp_path)
        original_write = runtime.foundation.store.write_json_atomic

        def fail_publication(path, value, **kwargs):  # noqa: ANN001
            if Path(path) == publication_path and getattr(value, "latest_release_id", None) is not None:
                return runtime.foundation.fail(
                    runtime.foundation.issue("injected_publication_commit", "stop")
                )
            return original_write(path, value, **kwargs)

        monkeypatch.setattr(runtime.foundation.store, "write_json_atomic", fail_publication)

    result = runtime.validation_snapshot.adopt_legacy_stable_repo(
        tmp_path, summary="Adopt legacy release.", dry_run=False
    )

    assert not result.ok
    publication = runtime.repo_workspace.metadata.get_repo_publication(tmp_path).value.publication
    assert publication.status == RepoPublicationStatus.STABLE and publication.latest_release_id is None
    assert runtime.repo_workspace.release.list_releases(tmp_path).value == []
    checkpoints = runtime.validation_snapshot.list_repo_checkpoint_snapshots(tmp_path)
    assert checkpoints.ok and checkpoints.value is not None
    assert {item.checkpoint_kind.value for item in checkpoints.value} == {"before_native_run_mutation"}
    for node in runtime.node.node_tree.get_node_tree(tmp_path).value.nodes:
        if node.kind.value == "content":
            current = runtime.node.contract.get_visible_contract(tmp_path, node_path=node.path).value
            assert current.version == 1 and current.contract.decl_graph_head == {}
    assert runtime.ark.pause_controller.is_paused()
