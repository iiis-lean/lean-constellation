from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.foundation import FoundationService
from lean_constellation.services.validation_snapshot import (
    RepoCheckpointKind,
    RepoCheckpointSnapshotManifest,
    SnapshotFilesManifest,
    ValidationSnapshotService,
)


class RecordingRuntimeStabilityProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation
        self.calls: list[tuple[RepoCheckpointKind, list[str]]] = []

    def check_repo_stable_point(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind,
        node_paths: list[str] | None = None,
    ):
        del repo_root
        self.calls.append((checkpoint_kind, list(node_paths or [])))
        return self.foundation.ok(
            self.foundation.gate_passed(
                "runtime_stability",
                summary=f"Real-test runtime is stable for {checkpoint_kind.value}.",
            )
        )


class RecordingArkSnapshotProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation
        self.created: list[tuple[list[str], str | None]] = []

    def create_runtime_snapshot(self, repo_root: Path, *, scope_ids: list[str], label: str | None = None):
        del repo_root
        self.created.append((list(scope_ids), label))
        return self.foundation.ok(f"real_scope_ark_{len(self.created)}")

    def restore_runtime_snapshot(self, repo_root: Path, *, snapshot_id: str, leave_runtime_paused: bool = True):
        del repo_root, snapshot_id, leave_runtime_paused
        return self.foundation.ok(
            self.foundation.mutation_view(
                object_ref="ark:snapshot",
                changed=True,
                summary="Restored runtime snapshot through a checkpoint scope policy test double.",
            )
        )


@pytest.mark.real
def test_s1_s3_checkpoint_scope_policy_and_manifest(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "Main.lean").write_text("import Std\n", encoding="utf-8")
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(
        "# Source\n\n"
        "Source provenance: local checkpoint scope policy fixture.\n"
        "Reading order: read this README.md entry as the main material.\n"
        "Main material: checkpoint scope policy corpus.\n"
        "Known gaps and extraction limits: no missing source sections are known.\n",
        encoding="utf-8",
    )
    (repo_root / ".lake").mkdir()
    (repo_root / ".lake" / "cache.txt").write_text("do not snapshot", encoding="utf-8")
    (repo_root / ".git").mkdir()
    (repo_root / ".git" / "HEAD").write_text("do not snapshot", encoding="utf-8")
    (repo_root / ".agent_runtime").mkdir()
    (repo_root / ".agent_runtime" / "state.json").write_text("do not snapshot", encoding="utf-8")

    foundation = make_runtime().foundation
    node_service = foundation.runtime.node
    assert node_service.node_tree.ensure_root_scope_node(repo_root).ok
    assert node_service.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    assert node_service.create_content_node(
        repo_root,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Core ready.",
    ).ok
    assert node_service.create_content_node(
        repo_root,
        path="Main.Topic.Consumer",
        goal="Consumer goal",
        boundary="Consumer boundary",
        objective="Use core.",
        success_criteria="Consumer ready.",
    ).ok
    core = node_service.node_tree.node_store.resolve_active_node(repo_root, path="Main.Topic.Core").value
    consumer = node_service.node_tree.node_store.resolve_active_node(repo_root, path="Main.Topic.Consumer").value
    assert core is not None and consumer is not None
    runtime_stability = RecordingRuntimeStabilityProvider(foundation)
    ark = RecordingArkSnapshotProvider(foundation)
    service = ValidationSnapshotService(
        foundation.runtime,
        runtime_stability_provider=runtime_stability,
        ark_snapshot_provider=ark,
    )

    before_dispatch = service.create_repo_stable_point_snapshot(
        repo_root,
        checkpoint_kind=RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH,
        label="S1 before dispatch",
        node_paths=[" Main.Topic.Core ", "Main.Topic.Core"],
    )
    after_batch = service.create_repo_stable_point_snapshot(
        repo_root,
        checkpoint_kind=RepoCheckpointKind.AFTER_CONTENT_TASK_BATCH_TERMINAL,
        label="S3 after batch",
        node_paths=["Main.Topic.Core", "Main.Topic.Consumer"],
    )

    assert before_dispatch.ok
    assert before_dispatch.value is not None
    assert after_batch.ok
    assert after_batch.value is not None
    assert runtime_stability.calls == [
        (RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH, ["Main.Topic.Core"]),
        (RepoCheckpointKind.AFTER_CONTENT_TASK_BATCH_TERMINAL, ["Main.Topic.Core", "Main.Topic.Consumer"]),
    ]
    assert ark.created == [
        (["repo:repo", f"repo:repo:node:{core.node_id}"], "S1 before dispatch"),
        (["repo:repo", f"repo:repo:node:{core.node_id}", f"repo:repo:node:{consumer.node_id}"], "S3 after batch"),
    ]

    before_manifest = foundation.store.read_json(
        Path(before_dispatch.value.root) / "snapshot.json",
        RepoCheckpointSnapshotManifest,
    )
    after_manifest = foundation.store.read_json(
        Path(after_batch.value.root) / "snapshot.json",
        RepoCheckpointSnapshotManifest,
    )
    assert before_manifest.ok and before_manifest.value is not None
    assert after_manifest.ok and after_manifest.value is not None
    assert before_manifest.value.refreshed_scope_ids == ["repo:repo", f"repo:repo:node:{core.node_id}"]
    assert [ref.path for ref in before_manifest.value.node_refs] == ["Main.Topic.Core"]
    assert [ref.node_id for ref in before_manifest.value.node_refs] == [core.node_id]
    assert after_manifest.value.refreshed_scope_ids == [
        "repo:repo",
        f"repo:repo:node:{core.node_id}",
        f"repo:repo:node:{consumer.node_id}",
    ]
    assert [ref.path for ref in after_manifest.value.node_refs] == ["Main.Topic.Core", "Main.Topic.Consumer"]
    assert [ref.node_id for ref in after_manifest.value.node_refs] == [core.node_id, consumer.node_id]

    files_manifest = foundation.store.read_json(
        Path(after_batch.value.root) / "files_manifest.json",
        SnapshotFilesManifest,
    )
    assert files_manifest.ok and files_manifest.value is not None
    captured = {entry.source_relpath for entry in files_manifest.value.entries}
    assert "Main.lean" in captured
    assert ".lean_constellation/source/README.md" in captured
    assert not any(path.startswith(".lake/") for path in captured)
    assert not any(path.startswith(".git/") for path in captured)
    assert not any(path.startswith(".agent_runtime/") for path in captured)
