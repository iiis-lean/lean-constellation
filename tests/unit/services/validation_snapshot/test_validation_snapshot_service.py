from tests.unit_services_helpers import initialize_native_test_repo, make_runtime

import json
from types import SimpleNamespace
from pathlib import Path
from typing import Any

from lean_constellation.app.runtime import ApplicationSnapshotRuntime
from lean_constellation.domain.common import utc_now_iso
from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoDependencyRequirementStatus, RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo_run import SourceScope
from lean_constellation.domain.refs import NodeRef
from lean_constellation.services.adapter import AdapterDeclCompletenessView
from lean_constellation.services.adapter.adapter_decl_catalog import AdapterModuleSummaryItem, AdapterModuleSummaryView
from lean_constellation.services.foundation import (
    FoundationContext,
    FoundationService,
    IndexBuildContext,
    IndexBundle,
    IndexMetadata,
)
from lean_constellation.services.material import MaterialService
from lean_constellation.services.node.contract_fields import NodeDep, NodeDepActor
from lean_constellation.services.validation_snapshot import (
    AuditComponent,
    AuditFinding,
    AuditReport,
    ConsistencyCheckComponent,
    RepoCheckpointKind,
    RepoCheckpointSnapshotManifest,
    ReadinessGateComponent,
    SnapshotFilesManifest,
    ValidationSnapshotService,
)
from lean_constellation.services.validation_snapshot.source_index_checkpoint import SourceIndexCheckpointAdapter


class FakeRuntimeStabilityProvider:
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
                summary=f"Runtime is stable for {checkpoint_kind.value}.",
            )
        )


class BlockingRuntimeStabilityProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def check_repo_stable_point(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind,
        node_paths: list[str] | None = None,
    ):
        del repo_root, node_paths
        return self.foundation.ok(
            self.foundation.gate_failed(
                "runtime_stability",
                self.foundation.issue(
                    "runtime_not_stable",
                    "Runtime still has active work for this checkpoint.",
                    object_ref=checkpoint_kind.value,
                ),
                summary="Runtime is not stable.",
            )
        )


class FakeArkSnapshotProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation
        self.created: list[tuple[list[str], str | None]] = []
        self.restored: list[tuple[str, bool]] = []

    def create_runtime_snapshot(self, repo_root: Path, *, scope_ids: list[str], label: str | None = None):
        del repo_root
        self.created.append((scope_ids, label))
        return self.foundation.ok(f"ark_{len(self.created)}")

    def restore_runtime_snapshot(self, repo_root: Path, *, snapshot_id: str, leave_runtime_paused: bool = True):
        del repo_root
        self.restored.append((snapshot_id, leave_runtime_paused))
        return self.foundation.ok(
            self.foundation.mutation_view(
                object_ref=f"ark:{snapshot_id}",
                changed=True,
                summary="Restored runtime snapshot through a snapshot provider test double.",
            )
        )


class FailingArkSnapshotProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation
        self.created: list[tuple[list[str], str | None]] = []
        self.restored: list[tuple[str, bool]] = []

    def create_runtime_snapshot(self, repo_root: Path, *, scope_ids: list[str], label: str | None = None):
        del repo_root
        self.created.append((scope_ids, label))
        return self.foundation.fail(
            self.foundation.issue("ark_snapshot_create_failed", "Fake ARK snapshot create failed.")
        )

    def restore_runtime_snapshot(self, repo_root: Path, *, snapshot_id: str, leave_runtime_paused: bool = True):
        del repo_root
        self.restored.append((snapshot_id, leave_runtime_paused))
        return self.foundation.fail(
            self.foundation.issue("ark_snapshot_restore_failed", "Fake ARK snapshot restore failed.", object_ref=snapshot_id)
        )


class SnapshotTestHarness:
    def __init__(
        self,
        runtime,
        *,
        runtime_stability=None,
        ark_snapshot=None,
        **service_kwargs,
    ) -> None:
        self.service = ValidationSnapshotService(runtime, **service_kwargs)
        runtime.app.validation_snapshot = self.service
        self.application = ApplicationSnapshotRuntime(
            runtime,
            ark_snapshot or FakeArkSnapshotProvider(runtime.foundation),
            runtime_stability=runtime_stability or FakeRuntimeStabilityProvider(runtime.foundation),
        )
        runtime.app.snapshot_runtime = self.application

    def __getattr__(self, name):
        if hasattr(self.application, name):
            return getattr(self.application, name)
        return getattr(self.service, name)


def _snapshot_harness(runtime, **kwargs) -> SnapshotTestHarness:
    return SnapshotTestHarness(runtime, **kwargs)


class FakeFormalStageConsistencyProvider:
    def __init__(self, foundation: FoundationService, *, passed: bool) -> None:
        self.foundation = foundation
        self.passed = passed

    def check_formal_stage_consistency(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: str,
    ):
        del repo_root
        if self.passed:
            return self.foundation.ok(
                self.foundation.gate_passed(
                    "formal_stage_consistency",
                    summary=f"{stage} formal stage is synchronized for {node_path}:{decl_name}.",
                )
            )
        return self.foundation.ok(
            self.foundation.gate_failed(
                "formal_stage_consistency",
                self.foundation.issue(
                    "formal_stage_fake_failed",
                    "Fake formal-stage consistency failed.",
                    object_ref=f"{node_path}:{decl_name}",
                    field=stage,
                ),
                summary="Fake formal-stage consistency failed.",
            )
        )


class FakeAdapterForConsistency:
    def __init__(
        self,
        foundation: FoundationService,
        *,
        complete: bool = True,
        module_count: int = 1,
        projection_passed: bool = True,
    ) -> None:
        self.foundation = foundation
        self.complete = complete
        self.module_count = module_count
        self.projection_passed = projection_passed

    def check_adapter_decl_completeness(self, repo_root: Path, *, name: str | None = None):
        del repo_root
        if self.complete:
            return self.foundation.ok(
                AdapterDeclCompletenessView(
                    checked_names=[name or "main_result"],
                    complete=True,
                    summary="Adapter declaration is complete.",
                )
            )
        return self.foundation.ok(
            AdapterDeclCompletenessView(
                checked_names=[name or "main_result"],
                complete=False,
                issues=[
                    self.foundation.issue(
                        "adapter_decl_statement_missing",
                        "Adapter declaration statement is missing.",
                        object_ref=name or "main_result",
                    )
                ],
                summary="Adapter declaration is incomplete.",
            )
        )

    def list_registered_adapter_modules(self, repo_root: Path):
        del repo_root
        modules = [
            AdapterModuleSummaryItem(
                module=f"Upstream.Module{index}",
                decl_names=[f"decl_{index}"],
                finalized_decl_count=1,
                kinds=["theorem"],
            )
            for index in range(self.module_count)
        ]
        return self.foundation.ok(
            AdapterModuleSummaryView(
                modules=modules,
                module_count=len(modules),
                summary=f"{len(modules)} adapter modules registered.",
            )
        )

    def check_adapter_projection(self, repo_root: Path):
        del repo_root
        if self.projection_passed:
            return self.foundation.ok(
                self.foundation.gate_passed("adapter_projection", summary="Adapter projection is synchronized.")
            )
        return self.foundation.ok(
            self.foundation.gate_failed(
                "adapter_projection",
                self.foundation.issue("adapter_projection_stale", "Adapter projection is stale."),
                summary="Adapter projection is stale.",
            )
        )


class FakeRepairForConsistency:
    def __init__(self, foundation: FoundationService, *, passed: bool = True) -> None:
        self.foundation = foundation
        self.passed = passed

    def full_projection_audit(self, repo_root: Path):
        del repo_root
        if self.passed:
            return self.foundation.ok(
                self.foundation.gate_passed("full_projection_audit", summary="Repo projection audit passed.")
            )
        return self.foundation.ok(
            self.foundation.gate_failed(
                "full_projection_audit",
                self.foundation.issue("projection_audit_failed", "Repo projection audit failed."),
                summary="Repo projection audit failed.",
            )
        )


class FakeNodeProjectionForConsistency:
    def __init__(self, foundation: FoundationService, *, prelude_passed: bool = True, interfaces_passed: bool = True) -> None:
        self.foundation = foundation
        self.prelude_passed = prelude_passed
        self.interfaces_passed = interfaces_passed

    def check_prelude_sync(self, repo_root: Path, *, node_path: str):
        del repo_root
        if self.prelude_passed:
            return self.foundation.ok(
                self.foundation.gate_passed("prelude_projection_sync", summary=f"Prelude is synchronized for {node_path}.")
            )
        return self.foundation.ok(
            self.foundation.gate_failed(
                "prelude_projection_sync",
                self.foundation.issue("prelude_projection_stale", "Prelude projection is stale.", object_ref=node_path),
                summary="Prelude projection is stale.",
            )
        )

    def refresh_prelude(self, repo_root: Path, *, node_path: str):
        del repo_root
        return self.foundation.ok(SimpleNamespace(summary=f"Refreshed Prelude for {node_path}."))

    def check_interfaces_sync(self, repo_root: Path, *, node_path: str):
        del repo_root
        if self.interfaces_passed:
            return self.foundation.ok(
                self.foundation.gate_passed("interfaces_projection_sync", summary=f"Interfaces are synchronized for {node_path}.")
            )
        return self.foundation.ok(
            self.foundation.gate_failed(
                "interfaces_projection_sync",
                self.foundation.issue("interfaces_projection_stale", "Interfaces projection is stale.", object_ref=node_path),
                summary="Interfaces projection is stale.",
            )
        )

    def refresh_interfaces(self, repo_root: Path, *, node_path: str):
        del repo_root
        return self.foundation.ok(SimpleNamespace(summary=f"Refreshed Interfaces for {node_path}."))


class FakeModuleIdentityForConsistency:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation
        self.build_targets: list[str] = []
        self.build_artifacts: dict[str, list[str]] = {}

    def build_module(self, repo_root: Path, *, module: str):
        del repo_root
        artifacts = [
            f".lake/build/lib/lean/{module.replace('.', '/')}.olean",
            f".lake/build/lib/lean/{module.replace('.', '/')}.ilean",
        ]
        self.build_targets.append(f"+{module}")
        self.build_artifacts[module] = artifacts
        return self.foundation.ok(
            SimpleNamespace(
                module=module,
                target=f"+{module}",
                artifacts=artifacts,
                summary=f"Built +{module}.",
            )
        )


class FakeLeanProjectionForConsistency:
    def __init__(
        self,
        foundation: FoundationService,
        *,
        repo_projection_passed: bool = True,
        prelude_passed: bool = True,
        interfaces_passed: bool = True,
        formal_capture_passed: bool = True,
    ) -> None:
        self.foundation = foundation
        self.formal_capture_passed = formal_capture_passed
        self.repair = FakeRepairForConsistency(foundation, passed=repo_projection_passed)
        self.node_projection = FakeNodeProjectionForConsistency(
            foundation,
            prelude_passed=prelude_passed,
            interfaces_passed=interfaces_passed,
        )
        self.module_identity = FakeModuleIdentityForConsistency(foundation)

    def check_decl_file_snapshot_sync(self, repo_root: Path, *, node_path: str, decl_name: str, stage: str):
        del repo_root
        if self.formal_capture_passed:
            return self.foundation.ok(
                self.foundation.gate_passed(
                    "decl_file_capture_sync",
                    summary=f"{stage} formal capture is synchronized for {node_path}:{decl_name}.",
                )
            )
        return self.foundation.ok(
            self.foundation.gate_failed(
                "decl_file_capture_sync",
                self.foundation.issue(
                    "decl_file_capture_stale",
                    "Fake formal capture is stale.",
                    object_ref=f"{node_path}:{decl_name}",
                    field=stage,
                ),
                summary="Fake formal capture is stale.",
            )
        )

    def check_decl_dependency_identity(self, repo_root: Path, *, node_path: str, decl_name: str, stage: str):
        del repo_root, stage
        return self.foundation.ok(
            self.foundation.gate_passed(
                "decl_dependency_identity",
                summary=f"Dependency identity is complete for {node_path}:{decl_name}.",
            )
        )


class FakeConsistencyForReadiness:
    def __init__(
        self,
        foundation: FoundationService,
        *,
        source_passed: bool = True,
        index_passed: bool = True,
        projection_passed: bool = True,
    ) -> None:
        self.foundation = foundation
        self.source_passed = source_passed
        self.index_passed = index_passed
        self.projection_passed = projection_passed

    def check_source_corpus_consistency(self, repo_root: Path):
        del repo_root
        return self.foundation.ok(self._gate("source_corpus_consistency", "source_corpus_not_ready", self.source_passed))

    def check_source_index_consistency(self, repo_root: Path):
        del repo_root
        return self.foundation.ok(self._gate("source_index_consistency", "source_index_not_ready", self.index_passed))

    def check_projection_sync(self, repo_root: Path, *, scope: str = "repo"):
        del repo_root
        return self.foundation.ok(self._gate("projection_sync", "projection_not_ready", self.projection_passed, object_ref=scope))

    def check_formal_stage_consistency(self, repo_root: Path, *, node_path: str, decl_name: str, stage: str):
        del repo_root
        return self.foundation.ok(
            self.foundation.gate_passed(
                "formal_stage_consistency",
                summary=f"{stage} formal stage is synchronized for {node_path}:{decl_name}.",
            )
        )

    def _gate(self, gate_name: str, issue_kind: str, passed: bool, *, object_ref: str | None = None):
        if passed:
            return self.foundation.gate_passed(gate_name, summary=f"{gate_name} passed.")
        return self.foundation.gate_failed(
            gate_name,
            self.foundation.issue(issue_kind, f"{gate_name} failed.", object_ref=object_ref),
            summary=f"{gate_name} failed.",
        )


class FakeContentReadinessProvider:
    def __init__(self, foundation: FoundationService, *, passed: bool = True) -> None:
        self.foundation = foundation
        self.passed = passed

    def check_content_node_ready(self, repo_root: Path, *, node_path: str):
        del repo_root
        if self.passed:
            return self.foundation.ok(
                self.foundation.gate_passed("content_decl_graph_readiness", summary=f"{node_path} DeclGraph is ready.")
            )
        return self.foundation.ok(
            self.foundation.gate_failed(
                "content_decl_graph_readiness",
                self.foundation.issue("content_decl_graph_not_ready", "Content DeclGraph is not ready.", object_ref=node_path),
                summary="Content DeclGraph is not ready.",
            )
        )


class FakeAdapterReadyForReadiness:
    def __init__(self, foundation: FoundationService, *, passed: bool) -> None:
        self.foundation = foundation
        self.passed = passed

    def check_adapter_ready(self, repo_root: Path):
        del repo_root
        if self.passed:
            return self.foundation.ok(self.foundation.gate_passed("adapter_ready", summary="Adapter is ready."))
        return self.foundation.ok(
            self.foundation.gate_failed(
                "adapter_ready",
                self.foundation.issue("adapter_not_ready", "Adapter is not ready."),
                summary="Adapter is not ready.",
            )
        )


class SnapshotRestoreIndexBuilder:
    index_name = "snapshot_restore_index"

    def __init__(self, foundation: FoundationService, *, source_relpath: str = "Main.lean") -> None:
        self.foundation = foundation
        self.source_relpath = source_relpath
        self.calls = 0

    def build(self, ctx: IndexBuildContext):
        self.calls += 1
        source_path = Path(ctx.repo_root) / self.source_relpath
        try:
            text = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            return self.foundation.fail(
                self.foundation.issue("snapshot_restore_index_source_missing", f"Cannot read index source: {exc}")
            )
        return self.foundation.ok(
            IndexBundle[dict[str, Any]](
                metadata=IndexMetadata(
                    index_name=self.index_name,
                    rebuilt_at=utc_now_iso(),
                    builder_name=self.__class__.__name__,
                    source_truth_refs=[str(source_path)],
                ),
                data={"source_relpath": self.source_relpath, "text": text, "calls": self.calls},
            )
        )


class FakeDeclGraphAuditProvider:
    def __init__(self, foundation: FoundationService, *, round_passed: bool = True) -> None:
        self.foundation = foundation
        self.round_passed = round_passed

    def run_round_local_audit(self, repo_root: Path, *, node_path: str, round_id: str, stage: str):
        del repo_root
        if self.round_passed:
            return self.foundation.ok(
                AuditReport(
                    audit_name="round_local_audit",
                    passed=True,
                    checked_items=[f"{node_path}:{round_id}:{stage}"],
                    summary="Fake round audit passed.",
                )
            )
        return self.foundation.ok(
            AuditReport(
                audit_name="round_local_audit",
                passed=False,
                checked_items=[f"{node_path}:{round_id}:{stage}"],
                findings=[
                    AuditFinding(
                        kind="same_round_dependency_found",
                        object_ref=f"{node_path}:{round_id}",
                        message="A declaration depends on another declaration from the same round.",
                    )
                ],
                summary="Fake round audit failed.",
            )
        )


class FakeRepoReadyGateForAudit:
    def __init__(self, foundation: FoundationService, *, passed: bool = True) -> None:
        self.foundation = foundation
        self.passed = passed

    def check_repo_ready(self, repo_root: Path, *, summary: str):
        del repo_root, summary
        if self.passed:
            return self.foundation.ok(self.foundation.gate_passed("repo_ready", summary="Repo ready gate passed."))
        return self.foundation.ok(
            self.foundation.gate_failed(
                "repo_ready",
                self.foundation.issue("repo_open_requirements", "Repo still has open requirements."),
                summary="Repo ready gate failed.",
            )
        )


def _write_preparation_input(tmp_path: Path, *, mode: SourceCorpusMode = SourceCorpusMode.PREPARE) -> None:
    foundation = make_runtime().foundation
    prep = RepoPreparationInput(
        goal="Formalize the requested source material.",
        source_corpus_mode=mode,
        source_corpus_relpath=".lean_constellation/source" if mode != SourceCorpusMode.NONE else None,
        source_description="A source description.",
        interface_inputs=[DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Expose the main theorem.")],
    )
    path = foundation.layout.preparation_input_path(FoundationContext(repo_root=tmp_path))
    result = foundation.store.write_json_atomic(path, prep)
    assert result.ok


def _prepare_source_and_index(service: MaterialService, repo_root: Path) -> None:
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(
        "# Entry\n\n"
        "Source provenance: local markdown fixture.\n"
        "Reading order: start here, then read `chapter.md` as the main material.\n"
        "Main material: `chapter.md` contains the indexed definitions and lemmas.\n"
        "Known gaps and extraction limits: no missing source sections are known.\n",
        encoding="utf-8",
    )
    (source_root / "chapter.md").write_text("Definition A.\nLemma B.\nTheorem C.\n", encoding="utf-8")
    prepared = service.submit_source_corpus_prepared(
        repo_root,
        entry_path="README.md",
        overview="Indexed source corpus.",
        preparation_summary="Prepared source files.",
    )
    assert prepared.ok
    scope = service.resolve_source_scope(repo_root, source_scope=SourceScope(mode="all"))
    assert scope.ok and scope.value is not None
    opened = service.open_source_index_update(
        repo_root,
        resolved_scope=scope.value,
        index_policy="auto",
    )
    assert opened.ok and opened.value is not None
    block = service.create_source_block(
        repo_root,
        parent_id="root",
        kind="section",
        title="Chapter",
        summary="Main source chapter.",
    )
    assert block.ok and block.value is not None
    assert service.add_source_block_ref(
        repo_root,
        block_id=block.value.block_id,
        path="chapter.md",
        start_line=1,
        end_line=3,
        role="primary",
    ).ok
    assert service.mark_block_refs_done(
        repo_root, block_id=block.value.block_id
    ).ok
    assert service.mark_block_links_done(
        repo_root, block_id=block.value.block_id
    ).ok
    assert service.mark_block_completed(
        repo_root, block_id=block.value.block_id
    ).ok
    for path in ["README.md", "chapter.md"]:
        assert service.set_file_survey_status(
            repo_root,
            path=path,
            status="surveyed",
            summary=f"Surveyed {path}.",
        ).ok
        assert service.set_file_indexing_status(
            repo_root, path=path, status="indexed"
        ).ok
    gate = service.validate_source_index_update(
        repo_root,
        baseline_index=None,
        expected_baseline_digest=opened.value.baseline_digest,
        resolved_scope=scope.value.resolved_file_paths,
        require_completed=True,
    )
    assert gate.ok and gate.value is not None and gate.value.gate.passed
    assert service.commit_source_index_update(
        repo_root, validated=gate.value
    ).ok


def _read_gate_gap_records(repo_root: Path) -> list[dict[str, Any]]:
    path = repo_root / ".lean_constellation" / "audit" / "gate_gaps.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_pure_service_business_gate_does_not_require_runtime_provider(tmp_path: Path) -> None:
    _write_preparation_input(tmp_path)
    service = make_runtime().validation_snapshot

    gate = service.check_repo_checkpoint_business_gate(
        tmp_path, checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL
    )

    assert gate.ok
    assert gate.value is not None
    assert gate.value.passed is True


def test_runtime_gate_failure_blocks_snapshot_before_ark_provider(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    ark = FakeArkSnapshotProvider(foundation)
    snapshot = _snapshot_harness(
        foundation.runtime,
        runtime_stability=BlockingRuntimeStabilityProvider(foundation),
        ark_snapshot=ark,
    )

    created = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
    )

    assert not created.ok
    assert created.issues[0].kind == "runtime_not_stable"
    assert ark.created == []
    assert snapshot.list_repo_checkpoint_snapshots(tmp_path).value == []


def test_pure_service_archive_can_be_lc_only(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    service = ValidationSnapshotService(foundation.runtime)

    created = service.create_repo_checkpoint_archive(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
    )

    assert created.ok and created.value is not None
    assert created.value.ark_runtime_snapshot_id is None
    assert len(service.list_repo_checkpoint_snapshots(tmp_path).value or []) == 1


def test_repo_stable_point_snapshot_create_list_and_restore(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    assert foundation.store.write_json_atomic(tmp_path / ".lean_constellation" / "repo.json", {"main_node": "Main"}).ok
    (tmp_path / "Main.lean").write_text("import Main\n", encoding="utf-8")
    (tmp_path / ".lake").mkdir()
    (tmp_path / ".lake" / "build.txt").write_text("do not snapshot", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("do not snapshot", encoding="utf-8")
    (tmp_path / ".agent_runtime").mkdir()
    (tmp_path / ".agent_runtime" / "state.json").write_text("do not snapshot", encoding="utf-8")
    ark = FakeArkSnapshotProvider(foundation)
    snapshot = _snapshot_harness(
        foundation.runtime,
        runtime_stability=FakeRuntimeStabilityProvider(foundation),
        ark_snapshot=ark,
    )

    created = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
        label="bootstrap done",
    )

    assert created.ok
    assert created.value is not None
    assert created.value.ark_runtime_snapshot_id == "ark_1"
    assert ark.created == [([f"repo:{tmp_path.name}"], "bootstrap done")]
    files_manifest = foundation.store.read_json(
        Path(created.value.root) / "files_manifest.json",
        SnapshotFilesManifest,
    )
    assert files_manifest.ok and files_manifest.value is not None
    captured = {entry.source_relpath for entry in files_manifest.value.entries}
    assert ".lean_constellation/preparation_input.json" in captured
    assert "Main.lean" in captured
    assert not any(path.startswith(".lake/") for path in captured)
    assert not any(path.startswith(".git/") for path in captured)
    assert not any(path.startswith(".agent_runtime/") for path in captured)

    listed = snapshot.list_repo_checkpoint_snapshots(tmp_path)
    assert listed.ok
    assert [item.snapshot_id for item in listed.value or []] == [created.value.snapshot_id]

    (tmp_path / "Main.lean").write_text("-- modified\n", encoding="utf-8")
    dry_run = snapshot.restore_repo_checkpoint_snapshot(tmp_path, snapshot_id=created.value.snapshot_id, dry_run=True)
    assert dry_run.ok
    assert dry_run.value is not None
    assert "Main.lean" in dry_run.value.would_restore_files
    restored = snapshot.restore_repo_checkpoint_snapshot(tmp_path, snapshot_id=created.value.snapshot_id)
    assert restored.ok
    assert (tmp_path / "Main.lean").read_text(encoding="utf-8") == "import Main\n"
    assert ark.restored == [("ark_1", True)]


def test_lc_only_checkpoint_restore_skips_ark_runtime(tmp_path: Path) -> None:
    runtime = make_runtime()
    ark = FakeArkSnapshotProvider(runtime.foundation)
    service = ValidationSnapshotService(runtime)
    (tmp_path / "Main.lean").write_text("theorem original : True := by trivial\n", encoding="utf-8")

    created = service.snapshot_restore.create_repo_checkpoint_archive(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.MANUAL_TEST_STABLE_POINT,
        snapshot_id="lc_only",
        ark_runtime_snapshot_id=None,
    )
    assert created.ok and created.value is not None
    assert created.value.ark_runtime_snapshot_id is None
    assert ark.created == []
    (tmp_path / "Main.lean").write_text("-- changed\n", encoding="utf-8")

    restored = service.snapshot_restore.restore_repo_checkpoint_snapshot(
        tmp_path, snapshot_id="lc_only"
    )

    assert restored.ok and restored.value is not None
    assert restored.value.ark_runtime_snapshot_id is None
    assert ark.restored == []
    assert (tmp_path / "Main.lean").read_text(encoding="utf-8") == "theorem original : True := by trivial\n"


def test_source_index_checkpoint_materialization_is_idempotent_and_loads_retry_baseline(tmp_path: Path) -> None:
    runtime = make_runtime()
    _write_preparation_input(tmp_path)
    ark = FakeArkSnapshotProvider(runtime.foundation)
    service = ValidationSnapshotService(runtime)
    runtime.app.validation_snapshot = service
    runtime.app.snapshot_runtime = ApplicationSnapshotRuntime(
        runtime,
        ark,
        runtime_stability=FakeRuntimeStabilityProvider(runtime.foundation),
    )
    adapter = SourceIndexCheckpointAdapter(runtime)

    first = adapter.materialize_source_index_baseline_checkpoint(
        tmp_path,
        checkpoint_id="source_index_cp_fixed",
        scope_ids=[f"repo:{tmp_path.name}"],
        label="before scoped SourceIndex update",
    )
    retried = adapter.materialize_source_index_baseline_checkpoint(
        tmp_path,
        checkpoint_id="source_index_cp_fixed",
        scope_ids=[f"repo:{tmp_path.name}"],
        label="before scoped SourceIndex update",
    )

    assert first.ok and retried.ok
    assert first.value is not None and retried.value is not None
    assert first.value.checkpoint_id == retried.value.checkpoint_id == "source_index_cp_fixed"
    assert ark.created == [([f"repo:{tmp_path.name}"], "before scoped SourceIndex update")]
    baseline = adapter.load_source_index_baseline(tmp_path, checkpoint_id="source_index_cp_fixed")
    assert baseline.ok and baseline.value is None

    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "README.md").write_text(
        "# Fixture\n\n"
        "Source provenance: local fixture.\n"
        "Reading order: read this file.\n"
        "Main material: a theorem.\n"
        "Known gaps and extraction limits: none.\n",
        encoding="utf-8",
    )
    assert runtime.material.submit_source_corpus_prepared(
        tmp_path,
        entry_path="README.md",
        overview="Fixture.",
        preparation_summary="Prepared.",
    ).ok
    scope = runtime.material.resolve_source_scope(tmp_path, source_scope=SourceScope(mode="all"))
    assert scope.ok and scope.value is not None
    opened = runtime.material.open_source_index_update(
        tmp_path,
        resolved_scope=scope.value,
        index_policy="auto",
        expected_baseline_digest=first.value.baseline_digest,
        retry_baseline_index=baseline.value,
    )
    assert opened.ok and opened.value is not None
    assert opened.value.outcome == "opened"
    validated_retry = adapter.validate_source_index_baseline_checkpoint(
        tmp_path,
        checkpoint_id="source_index_cp_fixed",
    )
    assert validated_retry.ok


def test_repo_stable_point_snapshot_accepts_explicit_scope_ids(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    ark = FakeArkSnapshotProvider(foundation)
    snapshot = _snapshot_harness(
        foundation.runtime,
        runtime_stability=FakeRuntimeStabilityProvider(foundation),
        ark_snapshot=ark,
    )

    created = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
        label="explicit repo scope",
        scope_ids=[" repo:Repo ", "repo:Repo", "node:Main"],
    )

    assert created.ok
    assert created.value is not None
    assert ark.created == [(["repo:Repo", "node:Main"], "explicit repo scope")]
    assert "refreshed_scope_ids" not in created.value.model_dump()
    assert "ark_runtime_scope_ids" not in created.value.model_dump()
    assert "node_refs" not in created.value.model_dump()
    manifest = foundation.store.read_json(
        Path(created.value.root) / "snapshot.json",
        RepoCheckpointSnapshotManifest,
    )
    assert manifest.ok
    assert manifest.value is not None
    assert "refreshed_scope_ids" not in manifest.value.model_dump()
    assert "ark_runtime_scope_ids" not in manifest.value.model_dump()
    assert "node_refs" not in manifest.value.model_dump()


def test_repo_stable_point_snapshot_rejects_empty_explicit_scope_id(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    snapshot = _snapshot_harness(
        foundation.runtime,
        runtime_stability=FakeRuntimeStabilityProvider(foundation),
        ark_snapshot=FakeArkSnapshotProvider(foundation),
    )

    created = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
        scope_ids=["repo:Repo", " "],
    )

    assert not created.ok
    assert created.issues[0].kind == "checkpoint_scope_id_required"


def test_content_task_checkpoint_refreshes_repo_and_node_scopes(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    (tmp_path / "Main.lean").write_text("import Main\n", encoding="utf-8")
    node_service = foundation.runtime.node
    assert node_service.node_tree.ensure_root_scope_node(tmp_path).ok
    assert node_service.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    assert node_service.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Core ready.",
    ).ok
    assert node_service.create_content_node(
        tmp_path,
        path="Main.Topic.Consumer",
        goal="Consumer goal",
        boundary="Consumer boundary",
        objective="Use core.",
        success_criteria="Consumer ready.",
    ).ok
    ark = FakeArkSnapshotProvider(foundation)
    snapshot = _snapshot_harness(
        foundation.runtime,
        runtime_stability=FakeRuntimeStabilityProvider(foundation),
        ark_snapshot=ark,
    )

    created = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.AFTER_CONTENT_TASK_BATCH_TERMINAL,
        label="content batch finished",
        node_paths=["Main.Topic.Core", "Main.Topic.Consumer"],
    )

    assert created.ok
    assert created.value is not None
    core = node_service.node_tree.node_store.resolve_active_node(tmp_path, path="Main.Topic.Core")
    consumer = node_service.node_tree.node_store.resolve_active_node(tmp_path, path="Main.Topic.Consumer")
    assert core.ok and core.value is not None
    assert consumer.ok and consumer.value is not None
    expected_scopes = [
        f"repo:{tmp_path.name}",
        f"repo:{tmp_path.name}:node:{core.value.node_id}",
        f"repo:{tmp_path.name}:node:{consumer.value.node_id}",
    ]
    assert ark.created == [(expected_scopes, "content batch finished")]
    assert "refreshed_scope_ids" not in created.value.model_dump()
    assert "node_refs" not in created.value.model_dump()
    manifest = foundation.store.read_json(
        Path(created.value.root) / "snapshot.json",
        RepoCheckpointSnapshotManifest,
    )
    assert manifest.ok
    assert manifest.value is not None
    assert "refreshed_scope_ids" not in manifest.value.model_dump()
    assert "node_refs" not in manifest.value.model_dump()


def test_content_task_checkpoint_normalizes_node_paths_and_accepts_node_ids(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    (tmp_path / "Main.lean").write_text("import Main\n", encoding="utf-8")
    node_service = foundation.runtime.node
    assert node_service.node_tree.ensure_root_scope_node(tmp_path).ok
    assert node_service.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    assert node_service.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Core ready.",
    ).ok
    assert node_service.create_content_node(
        tmp_path,
        path="Main.Topic.Consumer",
        goal="Consumer goal",
        boundary="Consumer boundary",
        objective="Use core.",
        success_criteria="Consumer ready.",
    ).ok
    core = node_service.node_tree.node_store.resolve_active_node(tmp_path, path="Main.Topic.Core").value
    consumer = node_service.node_tree.node_store.resolve_active_node(tmp_path, path="Main.Topic.Consumer").value
    assert core is not None and consumer is not None
    ark = FakeArkSnapshotProvider(foundation)
    runtime = FakeRuntimeStabilityProvider(foundation)
    snapshot = _snapshot_harness(
        foundation.runtime,
        runtime_stability=runtime,
        ark_snapshot=ark,
    )

    created = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH,
        label="before dispatch",
        node_paths=[" Main.Topic.Core ", "Main.Topic.Core"],
        node_ids=[consumer.node_id, consumer.node_id],
    )

    assert created.ok
    assert created.value is not None
    expected_scopes = [
        f"repo:{tmp_path.name}",
        f"repo:{tmp_path.name}:node:{core.node_id}",
        f"repo:{tmp_path.name}:node:{consumer.node_id}",
    ]
    assert "refreshed_scope_ids" not in created.value.model_dump()
    assert "node_refs" not in created.value.model_dump()
    assert runtime.calls == [
        (
            RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH,
            ["Main.Topic.Core", "Main.Topic.Consumer"],
        )
    ]
    assert ark.created == [(expected_scopes, "before dispatch")]

    invalid = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH,
        node_paths=["Main.Topic.Core", " "],
    )

    assert not invalid.ok
    assert invalid.issues[0].kind == "checkpoint_node_path_required"

    missing = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH,
        node_paths=["Main.Topic.Missing"],
    )

    assert not missing.ok
    assert missing.issues[0].kind == "node_missing"


def test_content_task_checkpoint_rejects_conflicting_node_path_and_node_id(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    (tmp_path / "Main.lean").write_text("import Main\n", encoding="utf-8")
    node_service = foundation.runtime.node
    assert node_service.node_tree.ensure_root_scope_node(tmp_path).ok
    assert node_service.create_content_node(
        tmp_path,
        path="Main.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Core ready.",
    ).ok
    original = node_service.node_tree.node_store.resolve_active_node(tmp_path, path="Main.Core").value
    assert original is not None
    assert node_service.commit_content_contract(
        tmp_path, node_path="Main.Core", summary="Original core complete."
    ).ok
    assert node_service.mark_node_deleted(tmp_path, path="Main.Core", reason="replace").ok
    assert node_service.create_content_node(
        tmp_path,
        path="Main.Core",
        goal="Core replacement goal",
        boundary="Core replacement boundary",
        objective="Build replacement core.",
        success_criteria="Replacement core ready.",
    ).ok
    snapshot = _snapshot_harness(
        foundation.runtime,
        runtime_stability=FakeRuntimeStabilityProvider(foundation),
        ark_snapshot=FakeArkSnapshotProvider(foundation),
    )

    created = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH,
        node_paths=["Main.Core"],
        node_ids=[original.node_id],
    )

    assert not created.ok
    assert created.issues[0].kind == "checkpoint_node_ref_conflict"


def test_pure_service_restore_does_not_require_ark_provider(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    (tmp_path / "Main.lean").write_text("import Main\n", encoding="utf-8")
    creator = _snapshot_harness(
        foundation.runtime,
        runtime_stability=FakeRuntimeStabilityProvider(foundation),
        ark_snapshot=FakeArkSnapshotProvider(foundation),
    )
    created = creator.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
    )
    assert created.ok
    assert created.value is not None
    (tmp_path / "Main.lean").write_text("-- modified after snapshot\n", encoding="utf-8")
    restorer = ValidationSnapshotService(foundation.runtime)

    restored = restorer.restore_repo_checkpoint_snapshot(tmp_path, snapshot_id=created.value.snapshot_id)

    assert restored.ok
    assert (tmp_path / "Main.lean").read_text(encoding="utf-8") == "import Main\n"


def test_snapshot_stable_point_kind_branches(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    node_service = ValidationSnapshotService(foundation.runtime).node
    assert node_service.ensure_native_root_main_contract(tmp_path).ok
    assert node_service.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    assert node_service.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Core ready.",
    ).ok
    requirement_component = ValidationSnapshotService(foundation.runtime)
    adapter_pass_component = ValidationSnapshotService(foundation.runtime,
        readiness_gate=ReadinessGateComponent(foundation.runtime,
            adapter=FakeAdapterReadyForReadiness(foundation, passed=True),
        ),
    )
    adapter_fail_component = ValidationSnapshotService(foundation.runtime,
        readiness_gate=ReadinessGateComponent(foundation.runtime,
            adapter=FakeAdapterReadyForReadiness(foundation, passed=False),
        ),
    )

    requirement = requirement_component.check_repo_checkpoint_business_gate(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
    )
    adapter_pass = adapter_pass_component.check_repo_checkpoint_business_gate(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.ADAPTER_PREPARATION_TERMINAL,
    )
    adapter_fail = adapter_fail_component.check_repo_checkpoint_business_gate(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.ADAPTER_PREPARATION_TERMINAL,
    )
    before_source = requirement_component.check_repo_checkpoint_business_gate(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.BEFORE_NATIVE_SOURCE_PROCESSING,
    )
    native_missing_index = requirement_component.check_repo_checkpoint_business_gate(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.BEFORE_NATIVE_COORDINATOR_DISPATCH,
    )
    before_tasks = requirement_component.check_repo_checkpoint_business_gate(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH,
    )
    after_tasks = requirement_component.check_repo_checkpoint_business_gate(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.AFTER_CONTENT_TASK_BATCH_TERMINAL,
    )
    before_resource = requirement_component.check_repo_checkpoint_business_gate(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.BEFORE_RESOURCE_REQUEST_DISPATCH,
    )
    after_resource = requirement_component.check_repo_checkpoint_business_gate(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.AFTER_RESOURCE_REQUEST_TERMINAL,
    )

    assert requirement.ok and requirement.value is not None and requirement.value.passed is True
    assert adapter_pass.ok and adapter_pass.value is not None and adapter_pass.value.passed is True
    assert adapter_fail.ok and adapter_fail.value is not None and adapter_fail.value.passed is False
    assert adapter_fail.value.issues[0].kind == "adapter_not_ready"
    assert before_source.ok and before_source.value is not None and before_source.value.passed is True
    assert not native_missing_index.ok
    assert native_missing_index.issues[0].kind == "source_index_missing"
    assert before_tasks.ok and before_tasks.value is not None and before_tasks.value.passed is True
    assert after_tasks.ok and after_tasks.value is not None and after_tasks.value.passed is True
    assert before_resource.ok and before_resource.value is not None and before_resource.value.passed is True
    assert after_resource.ok and after_resource.value is not None and after_resource.value.passed is True


def test_snapshot_create_copy_failure_cleans_manifest_without_ark_rollback(tmp_path: Path, monkeypatch) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    (tmp_path / "Main.lean").write_text("import Main\n", encoding="utf-8")
    ark = FakeArkSnapshotProvider(foundation)
    snapshot = _snapshot_harness(
        foundation.runtime,
        runtime_stability=FakeRuntimeStabilityProvider(foundation),
        ark_snapshot=ark,
    )

    def fail_project_copy(*args, **kwargs):
        del args, kwargs
        raise OSError("copy blocked")

    monkeypatch.setattr(snapshot.service.snapshot_restore, "_copy_project_files", fail_project_copy)

    created = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
    )

    assert not created.ok
    assert created.issues[0].kind == "repo_checkpoint_snapshot_write_failed"
    assert ark.created == [([f"repo:{tmp_path.name}"], None)]
    assert snapshot.list_repo_checkpoint_snapshots(tmp_path).value == []


def test_restore_preflights_missing_archive_before_ark_restore(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    (tmp_path / "Main.lean").write_text("import Main\n", encoding="utf-8")
    ark = FakeArkSnapshotProvider(foundation)
    snapshot = _snapshot_harness(
        foundation.runtime,
        runtime_stability=FakeRuntimeStabilityProvider(foundation),
        ark_snapshot=ark,
    )
    created = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
    )
    assert created.ok and created.value is not None
    files_manifest = foundation.store.read_json(Path(created.value.root) / "files_manifest.json", SnapshotFilesManifest)
    assert files_manifest.ok and files_manifest.value is not None
    main_entry = next(entry for entry in files_manifest.value.entries if entry.source_relpath == "Main.lean")
    (Path(created.value.root) / "files" / main_entry.archive_relpath).unlink()
    (tmp_path / "Main.lean").write_text("-- modified after snapshot\n", encoding="utf-8")

    restored = snapshot.restore_repo_checkpoint_snapshot(tmp_path, snapshot_id=created.value.snapshot_id)

    assert not restored.ok
    assert restored.issues[0].kind == "repo_checkpoint_archive_file_missing"
    assert ark.restored == []
    assert (tmp_path / "Main.lean").read_text(encoding="utf-8") == "-- modified after snapshot\n"


def test_restore_preflights_archive_checksum_before_ark_restore(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    (tmp_path / "Main.lean").write_text("import Main\n", encoding="utf-8")
    ark = FakeArkSnapshotProvider(foundation)
    snapshot = _snapshot_harness(
        foundation.runtime,
        runtime_stability=FakeRuntimeStabilityProvider(foundation),
        ark_snapshot=ark,
    )
    created = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
    )
    assert created.ok and created.value is not None
    files_manifest = foundation.store.read_json(Path(created.value.root) / "files_manifest.json", SnapshotFilesManifest)
    assert files_manifest.ok and files_manifest.value is not None
    main_entry = next(entry for entry in files_manifest.value.entries if entry.source_relpath == "Main.lean")
    assert main_entry.sha256 is not None
    archive = Path(created.value.root) / "files" / main_entry.archive_relpath
    archive.write_text("import Math\n", encoding="utf-8")

    restored = snapshot.restore_repo_checkpoint_snapshot(tmp_path, snapshot_id=created.value.snapshot_id)

    assert not restored.ok
    assert restored.issues[0].kind == "repo_checkpoint_archive_file_mismatch"
    assert ark.restored == []


def test_restore_rejects_manifest_path_escape_before_ark_restore(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    (tmp_path / "Main.lean").write_text("import Main\n", encoding="utf-8")
    outside_archive = tmp_path.parent / "outside_snapshot_file"
    outside_archive.write_text("import Main\n", encoding="utf-8")
    outside_target = tmp_path.parent / "escaped.lean"
    outside_target.write_text("keep me\n", encoding="utf-8")
    ark = FakeArkSnapshotProvider(foundation)
    snapshot = _snapshot_harness(
        foundation.runtime,
        runtime_stability=FakeRuntimeStabilityProvider(foundation),
        ark_snapshot=ark,
    )
    created = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
    )
    assert created.ok and created.value is not None
    files_manifest_path = Path(created.value.root) / "files_manifest.json"
    files_manifest = foundation.store.read_json(files_manifest_path, SnapshotFilesManifest)
    assert files_manifest.ok and files_manifest.value is not None
    main_entry = next(entry for entry in files_manifest.value.entries if entry.source_relpath == "Main.lean")
    main_entry.source_relpath = "../escaped.lean"
    main_entry.archive_relpath = str(outside_archive)
    assert foundation.store.write_json_atomic(files_manifest_path, files_manifest.value).ok

    restored = snapshot.restore_repo_checkpoint_snapshot(tmp_path, snapshot_id=created.value.snapshot_id)

    assert not restored.ok
    assert restored.issues[0].kind == "repo_checkpoint_archive_path_unsafe"
    assert ark.restored == []
    assert outside_target.read_text(encoding="utf-8") == "keep me\n"


def test_restore_keeps_extra_files_and_rebuilds_indexes(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    (tmp_path / "Main.lean").write_text("import Main\n", encoding="utf-8")
    builder = SnapshotRestoreIndexBuilder(foundation)
    assert foundation.register_index_builder(builder).ok
    ctx = FoundationContext(repo_root=tmp_path, caller="unit-test")
    first_index = foundation.ensure_index(ctx, builder.index_name)
    assert first_index.ok and first_index.value is not None
    snapshot = _snapshot_harness(
        foundation.runtime,
        runtime_stability=FakeRuntimeStabilityProvider(foundation),
        ark_snapshot=FakeArkSnapshotProvider(foundation),
    )
    created = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
    )
    assert created.ok and created.value is not None
    (tmp_path / "Main.lean").write_text("-- modified after snapshot\n", encoding="utf-8")
    (tmp_path / "Extra.lean").write_text("-- extra file should not be pruned\n", encoding="utf-8")
    lake_build = tmp_path / ".lake" / "build"
    lake_build.mkdir(parents=True)
    (lake_build / "stale.olean").write_text("stale\n", encoding="utf-8")

    restored = snapshot.restore_repo_checkpoint_snapshot(tmp_path, snapshot_id=created.value.snapshot_id)

    assert restored.ok
    assert restored.value is not None
    assert (tmp_path / "Main.lean").read_text(encoding="utf-8") == "import Main\n"
    assert (tmp_path / "Extra.lean").exists()
    assert not lake_build.exists()
    assert restored.value.invalidated_paths == [".lake/build"]
    assert builder.calls == 2
    rebuilt_index = foundation.index.read_index(ctx, builder.index_name)
    assert rebuilt_index.ok and rebuilt_index.value is not None
    assert rebuilt_index.value.data == {
        "source_relpath": "Main.lean",
        "text": "import Main\n",
        "calls": 2,
    }


def test_restore_rebuilds_node_and_decl_graph_indexes_from_truth(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    (tmp_path / "Main.lean").write_text("import Main\n", encoding="utf-8")
    node_service = foundation.runtime.node
    assert node_service.node_tree.ensure_root_scope_node(tmp_path).ok
    created_node = node_service.create_content_node(
        tmp_path,
        path="Main.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Core ready.",
    )
    assert created_node.ok and created_node.value is not None
    node_id = created_node.value.node_id
    graph = foundation.runtime.decl_graph.ensure_decl_graph(tmp_path, node_path="Main.Core")
    assert graph.ok and graph.value is not None
    snapshot_runtime = _snapshot_harness(
        foundation.runtime,
        runtime_stability=FakeRuntimeStabilityProvider(foundation),
        ark_snapshot=FakeArkSnapshotProvider(foundation),
    )
    snapshot = snapshot_runtime.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
    )
    assert snapshot.ok and snapshot.value is not None
    (tmp_path / ".lean_constellation" / "index" / "nodes.json").write_text("{bad-index", encoding="utf-8")
    graph_index_path = node_service.node_tree.node_store.decl_graph_dir(tmp_path, node_id=node_id) / "index.json"
    graph_index_path.write_text("{bad-decl-graph-index", encoding="utf-8")

    restored = snapshot_runtime.restore_repo_checkpoint_snapshot(tmp_path, snapshot_id=snapshot.value.snapshot_id)

    assert restored.ok
    assert restored.value is not None
    node_index = node_service.node_tree.node_store.read_index(tmp_path)
    assert node_index.ok and node_index.value is not None
    assert node_index.value.active_path_to_node_id["Main.Core"] == node_id
    decl_index = foundation.runtime.decl_graph.get_decl_graph_index(tmp_path, node_path="Main.Core")
    assert decl_index.ok and decl_index.value is not None
    assert decl_index.value.node_id == node_id
    assert decl_index.value.node_path == "Main.Core"


def test_restore_can_prune_extra_snapshot_managed_files(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    (tmp_path / "Main.lean").write_text("import Main\n", encoding="utf-8")
    snapshot = _snapshot_harness(
        foundation.runtime,
        runtime_stability=FakeRuntimeStabilityProvider(foundation),
        ark_snapshot=FakeArkSnapshotProvider(foundation),
    )
    created = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
    )
    assert created.ok and created.value is not None
    (tmp_path / "Main.lean").write_text("-- modified after snapshot\n", encoding="utf-8")
    (tmp_path / "Extra.lean").write_text("-- extra project file\n", encoding="utf-8")
    (tmp_path / ".lean_constellation" / "repo_format.json").write_text('{"repo_format": "native"}\n', encoding="utf-8")
    (tmp_path / ".lake").mkdir()
    (tmp_path / ".lake" / "kept.txt").write_text("build artifact\n", encoding="utf-8")

    dry_run = snapshot.restore_repo_checkpoint_snapshot(
        tmp_path,
        snapshot_id=created.value.snapshot_id,
        dry_run=True,
        prune_extra_files=True,
    )

    assert dry_run.ok
    assert dry_run.value is not None
    assert set(dry_run.value.would_prune_files) == {"Extra.lean", ".lean_constellation/repo_format.json"}
    assert (tmp_path / "Extra.lean").exists()
    assert (tmp_path / ".lean_constellation" / "repo_format.json").exists()

    restored = snapshot.restore_repo_checkpoint_snapshot(
        tmp_path,
        snapshot_id=created.value.snapshot_id,
        prune_extra_files=True,
    )

    assert restored.ok
    assert restored.value is not None
    assert (tmp_path / "Main.lean").read_text(encoding="utf-8") == "import Main\n"
    assert not (tmp_path / "Extra.lean").exists()
    assert not (tmp_path / ".lean_constellation" / "repo_format.json").exists()
    assert (tmp_path / ".lake" / "kept.txt").exists()
    assert set(restored.value.pruned_files) == {"Extra.lean", ".lean_constellation/repo_format.json"}


def test_snapshot_list_filters_sorts_and_skips_malformed_manifests(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    (tmp_path / "Main.lean").write_text("import Main\n", encoding="utf-8")
    node_service = foundation.runtime.node
    assert node_service.node_tree.ensure_root_scope_node(tmp_path).ok
    assert node_service.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    assert node_service.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Core ready.",
    ).ok
    snapshot = _snapshot_harness(
        foundation.runtime,
        runtime_stability=FakeRuntimeStabilityProvider(foundation),
        ark_snapshot=FakeArkSnapshotProvider(foundation),
    )
    older = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
        label="older",
    )
    newer = snapshot.create_repo_stable_point_snapshot(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.AFTER_CONTENT_TASK_BATCH_TERMINAL,
        label="newer",
        node_paths=["Main.Topic.Core"],
    )
    assert older.ok and older.value is not None
    assert newer.ok and newer.value is not None
    old_manifest_path = Path(older.value.root) / "snapshot.json"
    new_manifest_path = Path(newer.value.root) / "snapshot.json"
    old_manifest = foundation.store.read_json(old_manifest_path, RepoCheckpointSnapshotManifest).value
    new_manifest = foundation.store.read_json(new_manifest_path, RepoCheckpointSnapshotManifest).value
    assert old_manifest is not None and new_manifest is not None
    assert foundation.store.write_json_atomic(old_manifest_path, old_manifest.model_copy(update={"created_at": "2026-01-01T00:00:00Z"})).ok
    assert foundation.store.write_json_atomic(new_manifest_path, new_manifest.model_copy(update={"created_at": "2026-01-02T00:00:00Z"})).ok
    bad_snapshot = foundation.layout.snapshot_root(FoundationContext(repo_root=tmp_path)) / "repo_checkpoints" / "bad"
    bad_snapshot.mkdir(parents=True)
    (bad_snapshot / "snapshot.json").write_text("{not-json", encoding="utf-8")

    listed = snapshot.list_repo_checkpoint_snapshots(tmp_path)
    filtered = snapshot.list_repo_checkpoint_snapshots(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
    )

    assert listed.ok
    assert [view.snapshot_id for view in listed.value or []] == [newer.value.snapshot_id, older.value.snapshot_id]
    assert filtered.ok
    assert [view.snapshot_id for view in filtered.value or []] == [older.value.snapshot_id]


def test_native_handoff_gate_aggregates_source_index_and_main_contract(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    material = foundation.runtime.material
    _write_preparation_input(tmp_path)
    initialize_native_test_repo(tmp_path)
    _prepare_source_and_index(material, tmp_path)
    service = ValidationSnapshotService(foundation.runtime)
    assert service.node.ensure_native_root_main_contract(tmp_path).ok

    gate = service.readiness_gate.check_native_handoff_gate(tmp_path)

    assert gate.ok
    assert gate.value is not None
    assert gate.value.passed is True


def test_consistency_source_corpus_and_index_direct_failures(tmp_path: Path) -> None:
    service = make_runtime().validation_snapshot

    corpus = service.consistency.check_source_corpus_consistency(tmp_path)
    index = service.consistency.check_source_index_consistency(tmp_path)

    assert corpus.ok
    assert corpus.value is not None
    assert corpus.value.passed is False
    assert corpus.value.issues[0].kind == "source_corpus_missing"
    assert not index.ok
    assert index.issues[0].kind == "source_index_missing"


def test_consistency_contract_directly_reports_bad_dep_and_warning(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    service = ValidationSnapshotService(foundation.runtime)
    assert service.node.ensure_native_root_main_contract(tmp_path).ok
    assert service.node.create_scope_node(
        tmp_path,
        path="Main.Topic",
        goal="Topic goal",
        boundary="Topic boundary.",
    ).ok
    created = service.node.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary.",
        objective="Build the core result.",
        success_criteria="Core result is represented in the contract.",
    )
    assert created.ok, created.issues
    opened = service.node.contract.ensure_open_contract(tmp_path, node_path="Main.Topic.Core")
    assert opened.ok and opened.value is not None
    opened.value.contract.deps = [
        NodeDep(
            dep_id="dep_missing",
            target=NodeRef(repo=None, node="Main.Topic.Missing"),
            expected_decl_refs=[],
            reason="Bad dep inserted to verify consistency detection.",
            added_by=NodeDepActor.COORDINATOR,
        )
    ]
    saved = foundation.store.write_json_atomic(
        service.node.contract._contract_file(tmp_path, "Main.Topic.Core", opened.value.version),  # noqa: SLF001
        opened.value.contract,
    )
    assert saved.ok

    gate = service.consistency.check_contract_consistency(tmp_path, node_path="Main.Topic.Core")

    assert gate.ok
    assert gate.value is not None
    assert gate.value.passed is False
    issue_kinds = {issue.kind for issue in gate.value.issues}
    assert "node_dep_target_missing" in issue_kinds
    assert "contract_mathlib_uses_empty" in issue_kinds


def test_consistency_formal_stage_default_and_injected_provider(tmp_path: Path) -> None:
    default_service = make_runtime().validation_snapshot
    missing = ConsistencyCheckComponent(default_service.runtime).check_formal_stage_consistency(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="core_result",
        stage="statement",
    )
    foundation = make_runtime().foundation
    passing = ConsistencyCheckComponent(
        foundation.runtime,
        lean_projection=FakeLeanProjectionForConsistency(foundation),
        formal_stage_provider=FakeFormalStageConsistencyProvider(foundation, passed=True),
    ).check_formal_stage_consistency(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="core_result",
        stage="proof",
    )
    failing = ConsistencyCheckComponent(
        foundation.runtime,
        lean_projection=FakeLeanProjectionForConsistency(foundation),
        formal_stage_provider=FakeFormalStageConsistencyProvider(foundation, passed=False),
    ).check_formal_stage_consistency(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="core_result",
        stage="proof",
    )

    assert missing.ok
    assert missing.value is not None
    assert missing.value.passed is False
    assert missing.value.issues[0].kind == "formal_stage_provider_missing"
    assert passing.ok
    assert passing.value is not None
    assert passing.value.passed is True
    assert failing.ok
    assert failing.value is not None
    assert failing.value.passed is False
    assert failing.value.issues[0].kind == "formal_stage_fake_failed"


def test_consistency_adapter_decl_direct_branches(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    missing_module = ConsistencyCheckComponent(foundation.runtime,
        adapter=FakeAdapterForConsistency(foundation, complete=True, module_count=0, projection_passed=True),
    ).check_adapter_decl_consistency(tmp_path, decl_name="main_result")
    incomplete = ConsistencyCheckComponent(foundation.runtime,
        adapter=FakeAdapterForConsistency(foundation, complete=False, module_count=1, projection_passed=True),
    ).check_adapter_decl_consistency(tmp_path, decl_name="main_result")
    stale_projection = ConsistencyCheckComponent(foundation.runtime,
        adapter=FakeAdapterForConsistency(foundation, complete=True, module_count=1, projection_passed=False),
    ).check_adapter_decl_consistency(tmp_path, decl_name="main_result")

    assert missing_module.ok
    assert missing_module.value is not None
    assert missing_module.value.passed is False
    assert missing_module.value.issues[0].kind == "adapter_decl_module_missing"
    assert incomplete.ok
    assert incomplete.value is not None
    assert incomplete.value.passed is False
    assert incomplete.value.issues[0].kind == "adapter_decl_statement_missing"
    assert stale_projection.ok
    assert stale_projection.value is not None
    assert stale_projection.value.passed is False
    assert stale_projection.value.issues[0].kind == "adapter_projection_stale"


def test_consistency_projection_sync_dispatches_by_scope(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    node_service = ValidationSnapshotService(foundation.runtime).node
    assert node_service.ensure_native_root_main_contract(tmp_path).ok
    component = ConsistencyCheckComponent(foundation.runtime,
        node=node_service,
        adapter=FakeAdapterForConsistency(foundation, complete=True, module_count=1, projection_passed=False),
        lean_projection=FakeLeanProjectionForConsistency(
            foundation,
            repo_projection_passed=False,
            prelude_passed=True,
            interfaces_passed=False,
        ),
    )

    repo = component.check_projection_sync(tmp_path, scope="repo")
    adapter = component.check_projection_sync(tmp_path, scope="adapter")
    node = component.check_projection_sync(tmp_path, scope="Main")
    missing_node = component.check_projection_sync(tmp_path, scope="Main.Missing")

    assert repo.ok
    assert repo.value is not None
    assert repo.value.issues[0].kind == "projection_audit_failed"
    assert adapter.ok
    assert adapter.value is not None
    assert adapter.value.issues[0].kind == "adapter_projection_stale"
    assert node.ok
    assert node.value is not None
    assert node.value.passed is False
    assert node.value.issues[0].kind == "interfaces_projection_stale"
    assert not missing_node.ok
    assert missing_node.issues[0].kind == "node_missing"


def test_readiness_native_handoff_reports_missing_source_index(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    initialize_native_test_repo(tmp_path)
    service = ValidationSnapshotService(foundation.runtime)
    assert service.node.ensure_native_root_main_contract(tmp_path).ok

    gate = service.readiness_gate.check_native_handoff_gate(tmp_path)

    assert not gate.ok
    assert gate.issues[0].kind == "source_index_missing"


def test_readiness_content_task_admission_reports_stale_prelude(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    node_service = ValidationSnapshotService(foundation.runtime).node
    gate_component = ReadinessGateComponent(foundation.runtime,
        node=node_service,
        adapter=FakeAdapterReadyForReadiness(foundation, passed=True),
        lean_projection=FakeLeanProjectionForConsistency(foundation, prelude_passed=False),
    )
    _write_preparation_input(tmp_path)
    assert node_service.ensure_native_root_main_contract(tmp_path).ok
    assert node_service.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary.").ok
    assert node_service.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary.",
        objective="Build the core result.",
        success_criteria="Core result is ready.",
    ).ok

    gate = gate_component.check_content_task_admission(tmp_path, node_path="Main.Topic.Core")

    assert gate.ok
    assert gate.value is not None
    assert gate.value.passed is False
    assert "prelude_projection_stale" in {issue.kind for issue in gate.value.issues}


def test_readiness_content_node_ready_provider_and_interface_branches(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    node_service = ValidationSnapshotService(foundation.runtime).node
    assert node_service.ensure_native_root_main_contract(tmp_path).ok
    assert node_service.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary.").ok
    assert node_service.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary.",
        objective="Build the core result.",
        success_criteria="Core result is ready.",
    ).ok
    assert node_service.interface.add_interface(
        tmp_path,
        node_path="Main.Topic.Core",
        name="core_result",
        kind=DeclKind.THEOREM,
        summary="Expose the core result.",
        actor="coordinator",
    ).ok
    ready_gate = ReadinessGateComponent(foundation.runtime,
        node=node_service,
        adapter=FakeAdapterReadyForReadiness(foundation, passed=True),
        lean_projection=FakeLeanProjectionForConsistency(foundation),
        consistency=FakeConsistencyForReadiness(foundation, projection_passed=True),
        content_readiness_provider=FakeContentReadinessProvider(foundation, passed=True),
    )
    graph_fail_gate = ReadinessGateComponent(foundation.runtime,
        node=node_service,
        adapter=FakeAdapterReadyForReadiness(foundation, passed=True),
        lean_projection=FakeLeanProjectionForConsistency(foundation),
        consistency=FakeConsistencyForReadiness(foundation, projection_passed=True),
        content_readiness_provider=FakeContentReadinessProvider(foundation, passed=False),
    )

    unbound = ready_gate.check_content_node_ready(tmp_path, node_path="Main.Topic.Core")
    graph_failed = graph_fail_gate.check_content_node_ready(tmp_path, node_path="Main.Topic.Core")

    assert unbound.ok
    assert unbound.value is not None
    assert unbound.value.passed is False
    assert unbound.value.issues[0].kind == "content_interface_unbound"
    assert graph_failed.ok
    assert graph_failed.value is not None
    assert graph_failed.value.passed is False
    assert graph_failed.value.issues[0].kind == "content_decl_graph_not_ready"


def test_readiness_content_blocked_submit_reason_and_kind(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    service = ValidationSnapshotService(foundation.runtime)
    assert service.node.ensure_native_root_main_contract(tmp_path).ok

    missing_reason = service.readiness_gate.check_content_node_blocked_submit(tmp_path, node_path="Main", reason=" ")
    wrong_kind = service.readiness_gate.check_content_node_blocked_submit(tmp_path, node_path="Main", reason="Blocked.")

    assert missing_reason.ok
    assert missing_reason.value is not None
    issue_kinds = {issue.kind for issue in missing_reason.value.issues}
    assert "content_blocked_reason_required" in issue_kinds
    assert "node_not_content" in issue_kinds
    assert wrong_kind.ok
    assert wrong_kind.value is not None
    assert wrong_kind.value.issues[0].kind == "node_not_content"


def test_readiness_scope_commit_base_and_projection_branches(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    initialize_native_test_repo(tmp_path)
    _write_preparation_input(tmp_path)
    node_service = ValidationSnapshotService(foundation.runtime).node
    assert node_service.ensure_native_root_main_contract(tmp_path).ok
    lean_projection = FakeLeanProjectionForConsistency(foundation, interfaces_passed=False)
    stale_projection = ReadinessGateComponent(foundation.runtime,
        node=node_service,
        adapter=FakeAdapterReadyForReadiness(foundation, passed=True),
        lean_projection=lean_projection,
    )

    missing_summary = stale_projection.check_scope_commit(tmp_path, scope_path="Main", summary=" ")
    wrong_kind = stale_projection.check_scope_commit(tmp_path, scope_path="Main.Missing", summary="Commit.")
    stale = stale_projection.check_scope_commit(tmp_path, scope_path="Main", summary="Commit Main.")

    assert missing_summary.ok
    assert missing_summary.value is not None
    assert missing_summary.value.passed is False
    assert missing_summary.value.issues[0].kind == "scope_summary_required"
    assert not wrong_kind.ok
    assert wrong_kind.issues[0].kind == "node_missing"
    assert stale.ok
    assert stale.value is not None
    assert stale.value.passed is False
    assert stale.value.issues[0].kind == "interfaces_projection_stale"
    assert lean_projection.module_identity.build_targets == ["+TestProject.Main.Interfaces"] * 2
    assert lean_projection.module_identity.build_artifacts["TestProject.Main.Interfaces"] == [
        ".lake/build/lib/lean/TestProject/Main/Interfaces.olean",
        ".lake/build/lib/lean/TestProject/Main/Interfaces.ilean",
    ]


def test_readiness_repo_ready_base_and_consistency_branches(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    initialize_native_test_repo(tmp_path)
    _write_preparation_input(tmp_path)
    node_service = ValidationSnapshotService(foundation.runtime).node
    assert node_service.ensure_native_root_main_contract(tmp_path).ok
    lean_projection = FakeLeanProjectionForConsistency(foundation)
    gate = ReadinessGateComponent(foundation.runtime,
        node=node_service,
        lean_projection=lean_projection,
        consistency=FakeConsistencyForReadiness(foundation, source_passed=True, index_passed=True, projection_passed=True),
    )
    projection_fail_gate = ReadinessGateComponent(foundation.runtime,
        node=node_service,
        lean_projection=FakeLeanProjectionForConsistency(foundation),
        consistency=FakeConsistencyForReadiness(foundation, source_passed=True, index_passed=True, projection_passed=False),
    )

    base = gate.check_repo_ready(tmp_path, summary=" ")
    projection_failed = projection_fail_gate.check_repo_ready(tmp_path, summary="Repo is ready.")

    assert base.ok
    assert base.value is not None
    assert base.value.passed is False
    base_issue_kinds = {issue.kind for issue in base.value.issues}
    assert "repo_ready_summary_required" in base_issue_kinds
    assert "main_scope_not_committed" in base_issue_kinds
    assert projection_failed.ok
    assert projection_failed.value is not None
    assert projection_failed.value.passed is False
    assert projection_failed.value.issues[0].kind == "main_scope_not_committed"
    assert "projection_not_ready" in {issue.kind for issue in projection_failed.value.issues}
    assert lean_projection.module_identity.build_targets == [
        "+TestProject.Main.Interfaces",
        "+TestProject",
    ]
    assert lean_projection.module_identity.build_artifacts["TestProject"] == [
        ".lake/build/lib/lean/TestProject.olean",
        ".lake/build/lib/lean/TestProject.ilean",
    ]


def test_readiness_adapter_ready_delegates_adapter_gate(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    passed = ReadinessGateComponent(foundation.runtime,
        adapter=FakeAdapterReadyForReadiness(foundation, passed=True),
    ).check_adapter_ready(tmp_path)
    failed = ReadinessGateComponent(foundation.runtime,
        adapter=FakeAdapterReadyForReadiness(foundation, passed=False),
    ).check_adapter_ready(tmp_path)

    assert passed.ok
    assert passed.value is not None
    assert passed.value.passed is True
    assert failed.ok
    assert failed.value is not None
    assert failed.value.passed is False
    assert failed.value.issues[0].kind == "adapter_not_ready"


def test_audit_missing_decl_graph_provider_is_explicit(tmp_path: Path) -> None:
    service = AuditComponent(make_runtime().validation_snapshot.runtime)

    audit = service.run_round_local_audit(tmp_path, node_path="Main.Core", round_id="round_1", stage="proof")

    assert audit.ok
    assert audit.value is not None
    assert audit.value.passed is False
    assert audit.value.findings[0].kind == "decl_graph_audit_provider_missing"


def test_audit_round_provider_pass_and_blocking(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    passing = AuditComponent(foundation.runtime,
        decl_graph_provider=FakeDeclGraphAuditProvider(foundation, round_passed=True),
    )
    blocking = AuditComponent(foundation.runtime,
        decl_graph_provider=FakeDeclGraphAuditProvider(foundation, round_passed=False),
    )

    passed = passing.run_round_local_audit(tmp_path, node_path="Main.Core", round_id="round_1", stage="proof")
    failed = blocking.run_round_local_audit(tmp_path, node_path="Main.Core", round_id="round_1", stage="proof")

    assert passed.ok
    assert passed.value is not None
    assert passed.value.passed is True
    assert passed.value.checked_items == ["Main.Core:round_1:proof"]
    assert failed.ok
    assert failed.value is not None
    assert failed.value.passed is False
    assert failed.value.findings[0].kind == "same_round_dependency_found"


def test_audit_record_gate_gap_validates_and_appends_jsonl(tmp_path: Path) -> None:
    service = make_runtime().validation_snapshot

    missing_source = service.audit.record_gate_gap(
        tmp_path,
        source=" ",
        description="A gate gap.",
        suggested_gate="new_gate",
    )
    missing_description = service.audit.record_gate_gap(
        tmp_path,
        source="reviewer",
        description=" ",
        suggested_gate="new_gate",
    )
    first = service.audit.record_gate_gap(
        tmp_path,
        source=" reviewer ",
        description="First gap.",
        suggested_gate=" proof_gate ",
    )
    second = service.audit.record_gate_gap(
        tmp_path,
        source="admin",
        description="Second gap.",
    )

    assert not missing_source.ok
    assert missing_source.issues[0].kind == "gate_gap_source_required"
    assert not missing_description.ok
    assert missing_description.issues[0].kind == "gate_gap_description_required"
    assert first.ok
    assert first.value is not None
    assert first.value.changed_items == ["proof_gate"]
    assert second.ok
    gap_path = tmp_path / ".lean_constellation" / "audit" / "gate_gaps.jsonl"
    lines = [json.loads(line) for line in gap_path.read_text(encoding="utf-8").splitlines()]
    assert [line["source"] for line in lines] == ["reviewer", "admin"]
    assert lines[0]["suggested_gate"] == "proof_gate"
    assert lines[1]["suggested_gate"] is None


def test_audit_repo_ready_aggregates_gate_issues_into_findings(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    passing = AuditComponent(foundation.runtime,
        consistency=FakeConsistencyForReadiness(foundation, source_passed=True, index_passed=True, projection_passed=True),
        readiness_gate=FakeRepoReadyGateForAudit(foundation, passed=True),
    )
    failing = AuditComponent(foundation.runtime,
        consistency=FakeConsistencyForReadiness(foundation, source_passed=False, index_passed=True, projection_passed=False),
        readiness_gate=FakeRepoReadyGateForAudit(foundation, passed=False),
    )

    passed = passing.run_repo_ready_audit(tmp_path)
    failed = failing.run_repo_ready_audit(tmp_path)

    assert passed.ok
    assert passed.value is not None
    assert passed.value.passed is True
    assert passed.value.checked_items == ["source_corpus_consistency", "source_index_consistency", "projection_sync", "repo_ready"]
    assert failed.ok
    assert failed.value is not None
    assert failed.value.passed is False
    finding_kinds = {finding.kind for finding in failed.value.findings}
    assert {"source_corpus_not_ready", "projection_not_ready", "repo_open_requirements"} <= finding_kinds


def test_admin_repair_requirement_obsolete_marks_requirement_and_records_audit_note(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    service = ValidationSnapshotService(foundation.runtime)
    created = service.repo_workspace.requirement.create_requirement(
        tmp_path,
        name="need_topology",
        target_repo="TopologyProvider",
        reason="Need topology lemmas.",
    )
    assert created.ok

    missing_note = service.admin_repair.mark_requirement_obsolete_and_recreate_hint(
        tmp_path,
        requirement_name="need_topology",
        note=" ",
    )
    repaired = service.admin_repair.mark_requirement_obsolete_and_recreate_hint(
        tmp_path,
        requirement_name="need_topology",
        note="Superseded by local proof.",
        target_repo="ReplacementProvider",
    )
    loaded = service.repo_workspace.requirement.get_requirement(tmp_path, name="need_topology")

    assert not missing_note.ok
    assert missing_note.issues[0].kind == "repair_note_required"
    assert repaired.ok
    assert repaired.value is not None
    assert repaired.value.target_repo == "ReplacementProvider"
    assert repaired.value.obsolete_marked is True
    assert loaded.ok
    assert loaded.value is not None
    assert loaded.value.requirement.status == RepoDependencyRequirementStatus.OBSOLETE
    records = _read_gate_gap_records(tmp_path)
    assert len(records) == 1
    assert records[0]["source"] == "admin_repair"
    assert "need_topology" in records[0]["description"]


def test_admin_repair_projection_requires_note_and_records_scope_repair(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    _write_preparation_input(tmp_path)
    service = ValidationSnapshotService(foundation.runtime)
    assert service.node.ensure_native_root_main_contract(tmp_path).ok

    missing_note = service.admin_repair.repair_projection(tmp_path, scope="Main", note=" ")
    repaired = service.admin_repair.repair_projection(tmp_path, scope="Main", note="Regenerate Main projection.")

    assert not missing_note.ok
    assert missing_note.issues[0].kind == "repair_note_required"
    assert repaired.ok
    assert repaired.value is not None
    assert repaired.value.scope == "Main"
    assert any(action.action.startswith("refresh_") or action.action.startswith("check_") for action in repaired.value.actions)
    records = _read_gate_gap_records(tmp_path)
    assert len(records) == 1
    assert records[0]["description"] == "Projection repair for Main: Regenerate Main projection."


def test_admin_repair_rebuild_all_indexes_requires_note_and_records_audit_note(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    (tmp_path / "Main.lean").write_text("import Main\n", encoding="utf-8")
    builder = SnapshotRestoreIndexBuilder(foundation)
    assert foundation.register_index_builder(builder).ok
    ctx = FoundationContext(repo_root=tmp_path, caller="unit-test")
    assert foundation.ensure_index(ctx, builder.index_name).ok
    service = ValidationSnapshotService(foundation.runtime)

    missing_note = service.admin_repair.rebuild_all_indexes(tmp_path, note=" ")
    rebuilt = service.admin_repair.rebuild_all_indexes(tmp_path, note="Rebuild after manual repair.")

    assert not missing_note.ok
    assert missing_note.issues[0].kind == "repair_note_required"
    assert rebuilt.ok
    assert rebuilt.value is not None
    assert rebuilt.value.rebuilt_indexes == [builder.index_name]
    assert builder.calls == 2
    records = _read_gate_gap_records(tmp_path)
    assert len(records) == 1
    assert records[0]["description"] == "Rebuilt indexes: Rebuild after manual repair."


def test_admin_repair_run_full_audit_delegates_audit_component(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    audit = AuditComponent(foundation.runtime,
        consistency=FakeConsistencyForReadiness(foundation, source_passed=False, index_passed=True, projection_passed=True),
        readiness_gate=FakeRepoReadyGateForAudit(foundation, passed=False),
    )
    admin = ValidationSnapshotService(foundation.runtime, audit=audit).admin_repair

    report = admin.run_full_audit(tmp_path)

    assert report.ok
    assert report.value is not None
    assert report.value.passed is False
    assert {finding.kind for finding in report.value.findings} == {"source_corpus_not_ready", "repo_open_requirements"}


def test_admin_repair_preparation_input_patch_is_whitelisted_and_noted(tmp_path: Path) -> None:
    _write_preparation_input(tmp_path)
    service = make_runtime().validation_snapshot

    missing_note = service.admin_repair.repair_preparation_input(
        tmp_path,
        patch={"source_description": "Updated source description."},
        note=" ",
    )
    forbidden = service.admin_repair.repair_preparation_input(tmp_path, patch={"interface_inputs": []}, note="bad")
    assert not forbidden.ok
    assert forbidden.issues[0].kind == "preparation_input_patch_field_forbidden"
    assert not missing_note.ok
    assert missing_note.issues[0].kind == "repair_note_required"

    repaired = service.admin_repair.repair_preparation_input(
        tmp_path,
        patch={"source_description": "Updated source description."},
        note="Clarified source description.",
    )

    assert repaired.ok
    assert repaired.value is not None
    assert "source_description" in repaired.value.updated_fields
    loaded = service.repo_workspace.preparation.get_preparation_input(tmp_path)
    assert loaded.ok
    assert loaded.value is not None
    assert loaded.value.input.source_description == "Updated source description."
    assert "[admin repair] Clarified source description." in (loaded.value.input.notes or "")
    records = _read_gate_gap_records(tmp_path)
    assert len(records) == 1
    assert records[0]["description"] == "Preparation input repair: Clarified source description."
