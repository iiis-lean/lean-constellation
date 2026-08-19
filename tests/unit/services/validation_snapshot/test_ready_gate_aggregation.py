from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tests.unit_services_helpers import (
    initialize_native_test_repo,
    lean_check_payload,
    make_runtime,
    set_current_decl_lean_name_for_test,
    write_statement_formal_for_test,
)

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import ProofAvailability, RepoCompletionMode
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.decl_graph import DeclReadinessReport, DeclState
from lean_constellation.services.external_clients import ExternalCommandResult
from lean_constellation.services.foundation import FoundationContext, GateReport, ServiceResult, WriteMode
from lean_constellation.services.runtime import LeanRuntimeServices
from lean_constellation.services.validation_snapshot import ReadinessGateComponent, ValidationSnapshotService


NODE_PATH = "Main.Topic.Core"
MAIN_CONTENT_NODE_PATH = "Main.Core"


class FakeLake:
    def run_lake_build(self, repo_root: Path, target: str | None = None, targets=None, timeout_seconds=None):  # noqa: ANN001, ANN201
        del targets, timeout_seconds
        return ExternalCommandResult(
            ok=True,
            command=["lake", "build", target or ""],
            cwd=str(repo_root),
            exit_code=0,
            summary="fake module build passed",
        )


def _runtime() -> LeanRuntimeServices:
    return make_runtime(external_overrides={"lake": FakeLake()})


class PassingConsistency:
    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def _passed(self, gate_name: str, summary: str) -> ServiceResult[GateReport]:
        return self.runtime.foundation.ok(self.runtime.foundation.gate_passed(gate_name, summary=summary))

    def check_projection_sync(self, repo_root: Path, *, scope: str = "repo") -> ServiceResult[GateReport]:
        del repo_root
        return self._passed("projection_sync", f"Projection sync passed for {scope}.")

    def check_source_corpus_consistency(self, repo_root: Path) -> ServiceResult[GateReport]:
        del repo_root
        return self._passed("source_corpus_consistency", "Source corpus consistency passed.")

    def check_source_index_consistency(self, repo_root: Path) -> ServiceResult[GateReport]:
        del repo_root
        return self._passed("source_index_consistency", "Source index consistency passed.")


def _write_preparation_input(
    runtime: LeanRuntimeServices,
    repo_root: Path,
    *,
    include_interface: bool = False,
    expected_statement_lean_code: str | None = None,
) -> None:
    initialize_native_test_repo(repo_root, project_name="TestProject")
    interfaces = [
        DeclInterface(
            name="main_result",
            kind=DeclKind.THEOREM,
            summary="Expose the main theorem.",
            expected_statement_lean_code=expected_statement_lean_code,
        )
    ] if include_interface else []
    prep = RepoPreparationInput(
        goal="Formalize the requested source material.",
        source_corpus_mode=SourceCorpusMode.PREPARE,
        source_corpus_relpath=".lean_constellation/source",
        source_description="A source description.",
        interface_inputs=interfaces,
    )
    path = runtime.foundation.layout.preparation_input_path(FoundationContext(repo_root=repo_root))
    assert runtime.foundation.store.write_json_atomic(path, prep).ok


def _create_scope_and_content(runtime: LeanRuntimeServices, repo_root: Path, *, content_path: str = NODE_PATH) -> None:
    initialize_native_test_repo(repo_root, project_name="TestProject")
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal.", boundary="Topic boundary.").ok
    assert runtime.node.create_content_node(
        repo_root,
        path=content_path,
        goal=f"{content_path} goal.",
        boundary=f"{content_path} boundary.",
        objective=f"Build {content_path}.",
        success_criteria=f"{content_path} is ready.",
    ).ok


def _create_public_decl(runtime: LeanRuntimeServices, repo_root: Path, *, decl_name: str = "main_result") -> None:
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=NODE_PATH, objective="Readiness strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Readiness round.",
    )
    assert round_record.ok and round_record.value is not None
    created = runtime.decl_graph.create_decl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        name=decl_name,
        kind=DeclKind.THEOREM.value,
        objective="Create the public result.",
        summary="Public theorem that is intentionally not ready.",
        public=True,
        target_state=DeclState.PROVED,
    )
    assert created.ok, created.issues


def _create_declared_main_public_theorem(runtime: LeanRuntimeServices, repo_root: Path, *, decl_name: str = "main_result") -> None:
    assert runtime.node.create_content_node(
        repo_root,
        path=MAIN_CONTENT_NODE_PATH,
        goal="Main core goal.",
        boundary="Main core boundary.",
        objective="Expose the main result.",
        success_criteria="Main core public declarations are complete.",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=MAIN_CONTENT_NODE_PATH, objective="Main export strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=MAIN_CONTENT_NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Declare the main public theorem.",
    )
    assert round_record.ok and round_record.value is not None
    created = runtime.decl_graph.create_decl(
        repo_root,
        node_path=MAIN_CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        name=decl_name,
        kind=DeclKind.THEOREM.value,
        objective="Create the public result.",
        summary="Public theorem with a declared statement only.",
        public=True,
        target_state=DeclState.DECLARED,
    )
    assert created.ok, created.issues
    assert runtime.decl_graph.start_round(repo_root, node_path=MAIN_CONTENT_NODE_PATH, round_id=round_record.value.round_id).ok
    assert runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=MAIN_CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        decl_name=decl_name,
        nl=f"{decl_name} states True.",
        deps=[],
    ).ok
    assert write_statement_formal_for_test(runtime,
        repo_root,
        node_path=MAIN_CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        decl_name=decl_name,
        lean_code=(
            "/--\n"
            f"# lean-constellation target: `{decl_name}`\n\n"
            f"{decl_name} states True.\n"
            "-/\n"
            f"theorem {decl_name} : True := by\n  sorry"
        ),
        lean_check=lean_check_payload(contains_sorry=True),
        deps=[],
    ).ok
    set_current_decl_lean_name_for_test(
        runtime,
        repo_root,
        node_path=MAIN_CONTENT_NODE_PATH,
        decl_name=decl_name,
        lean_decl_name=decl_name,
    )
    assert runtime.decl_graph.commit_decl_revision(
        repo_root,
        node_path=MAIN_CONTENT_NODE_PATH,
        name=decl_name,
        state=DeclState.DECLARED,
    ).ok
    round_after_commit = runtime.decl_graph.get_round(
        repo_root,
        node_path=MAIN_CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
    )
    assert round_after_commit.ok and round_after_commit.value is not None
    for change_id in round_after_commit.value.change_ids:
        assert runtime.decl_graph.write_decl_change_summary(
            repo_root,
            node_path=MAIN_CONTENT_NODE_PATH,
            round_id=round_record.value.round_id,
            change_id=change_id,
            summary=f"Declared {decl_name}.",
        ).ok
    assert runtime.decl_graph.write_round_summary(
        repo_root,
        node_path=MAIN_CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        summary=f"Declared {decl_name} for the Main export.",
    ).ok
    assert runtime.decl_graph.strategy_round.record_round_execution_result(
        repo_root,
        node_path=MAIN_CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        result_kind="blocked",
        reason="Test fixture committed revisions before round closeout.",
    ).ok
    assert runtime.decl_graph.strategy_round.persist_round_closeout(
        repo_root,
        node_path=MAIN_CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        result_kind="blocked",
        reason="Test fixture committed revisions before round closeout.",
        acknowledged_by="test-fixture",
    ).ok
    assert runtime.lean_projection.sync_decl_file_after_revision_reset(
        repo_root,
        node_path=MAIN_CONTENT_NODE_PATH,
        decl_name=decl_name,
    ).ok
    contract = runtime.node.contract.get_edit_contract(repo_root, node_path=MAIN_CONTENT_NODE_PATH)
    assert contract.ok and contract.value is not None
    contract.value.contract.decl_graph_head[decl_name] = 1
    contract_path = runtime.node.node_tree.node_store.contract_path(
        repo_root,
        node_id=contract.value.node_id,
        version=contract.value.contract.version,
    )
    assert runtime.foundation.store.write_json_atomic(
        contract_path,
        contract.value.contract,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    assert runtime.node.commit_content_contract(
        repo_root,
        node_path=MAIN_CONTENT_NODE_PATH,
        summary="Publish the declared Main content head.",
    ).ok


def _validation_with_passing_consistency(runtime: LeanRuntimeServices) -> ValidationSnapshotService:
    return ValidationSnapshotService(
        runtime,
        readiness_gate=ReadinessGateComponent(
            runtime,
            consistency=PassingConsistency(runtime),
            content_readiness_provider=runtime.decl_graph,
        ),
    )


def test_content_ready_view_aggregates_decl_graph_not_ready(tmp_path: Path) -> None:
    runtime = _runtime()
    _create_scope_and_content(runtime, tmp_path)
    _create_public_decl(runtime, tmp_path)
    service = _validation_with_passing_consistency(runtime)

    view = service.get_content_ready_view(tmp_path, node_path=NODE_PATH)

    assert view.ok and view.value is not None
    assert view.value.ready_to_submit is False
    assert view.value.contract_version_status is not None
    assert any(issue.kind == "content_public_decl_not_ready" for issue in view.value.gate.issues)


def test_scope_ready_view_reports_uncommitted_content_child(tmp_path: Path) -> None:
    runtime = _runtime()
    _create_scope_and_content(runtime, tmp_path)

    view = runtime.validation_snapshot.get_scope_ready_view(tmp_path, scope_path="Main.Topic")

    assert view.ok and view.value is not None
    assert view.value.ready_to_commit is False
    assert view.value.direct_child_count == 1
    assert view.value.blocking_child_count == 1
    assert view.value.child_readiness_gate.issues[0].kind == "content_child_not_ready"


def test_scope_ready_view_reports_unbound_interface(tmp_path: Path) -> None:
    runtime = _runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal.", boundary="Topic boundary.").ok
    assert runtime.node.interface.add_interface(
        tmp_path,
        node_path="Main.Topic",
        name="missing_binding",
        kind=DeclKind.THEOREM,
        summary="Unbound interface.",
        actor="coordinator",
    ).ok

    view = runtime.validation_snapshot.get_scope_ready_view(tmp_path, scope_path="Main.Topic")

    assert view.ok and view.value is not None
    assert view.value.ready_to_commit is False
    assert view.value.interface_count == 1
    assert any(issue.kind == "interface_unbound" for issue in view.value.gate.issues)


def test_scope_ready_view_orders_child_issues_deterministically(tmp_path: Path) -> None:
    runtime = _runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal.", boundary="Topic boundary.").ok
    for suffix in ["B", "A"]:
        assert runtime.node.create_content_node(
            tmp_path,
            path=f"Main.Topic.{suffix}",
            goal=f"{suffix} goal.",
            boundary=f"{suffix} boundary.",
            objective=f"Build {suffix}.",
            success_criteria=f"{suffix} ready.",
        ).ok

    view = runtime.validation_snapshot.get_scope_ready_view(tmp_path, scope_path="Main.Topic")

    assert view.ok and view.value is not None
    issue_paths = [issue.message.rsplit(": ", 1)[-1] for issue in view.value.child_readiness_gate.issues]
    assert issue_paths == ["Main.Topic.A", "Main.Topic.B"]


def test_repo_ready_view_passes_with_committed_main_and_passing_providers(tmp_path: Path) -> None:
    runtime = _runtime()
    _write_preparation_input(runtime, tmp_path)
    assert runtime.node.ensure_native_root_main_contract(tmp_path).ok
    committed = runtime.node.commit_scope_contract(tmp_path, scope_path="Main", summary="Main scope is committed.")
    assert committed.ok, committed.issues
    service = _validation_with_passing_consistency(runtime)

    view = service.get_repo_ready_view(tmp_path)

    assert view.ok and view.value is not None
    assert view.value.ready_to_submit is True
    assert view.value.main_contract_version_status is not None
    assert view.value.gate.passed is True
    assert view.value.blocking_issue_kinds == []


def test_repo_ready_gate_uses_target_proof_availability_for_main_public_exports(tmp_path: Path) -> None:
    runtime = _runtime()
    _write_preparation_input(runtime, tmp_path)
    assert runtime.node.ensure_native_root_main_contract(tmp_path).ok
    configured_declared = runtime.repo_workspace.metadata.update_repo_config(
        tmp_path,
        completion_mode=RepoCompletionMode.INTERFACE_DECLARED,
    )
    assert configured_declared.ok, configured_declared.issues
    _create_declared_main_public_theorem(runtime, tmp_path)
    exported = runtime.node.export.add_scope_export(
        tmp_path,
        scope_path="Main",
        decl_node=MAIN_CONTENT_NODE_PATH,
        decl_name="main_result",
    )
    assert exported.ok, exported.issues
    committed = runtime.node.commit_scope_contract(tmp_path, scope_path="Main", summary="Main scope is committed.")
    assert committed.ok, committed.issues
    service = _validation_with_passing_consistency(runtime)

    declared_view = service.get_repo_ready_view(tmp_path)
    assert declared_view.ok and declared_view.value is not None
    assert declared_view.value.target_proof_availability == ProofAvailability.DECLARED
    assert declared_view.value.ready_to_submit is True

    assert runtime.repo_workspace.metadata.mark_repo_developing(tmp_path).ok
    configured_proved = runtime.repo_workspace.metadata.update_repo_config(
        tmp_path,
        completion_mode=RepoCompletionMode.GRAPH_PROVED,
    )
    assert configured_proved.ok, configured_proved.issues
    proved_view = service.get_repo_ready_view(tmp_path)

    assert proved_view.ok and proved_view.value is not None
    assert proved_view.value.target_proof_availability == ProofAvailability.PROVED
    assert proved_view.value.ready_to_submit is False
    assert "repo_public_decl_proof_policy_unsatisfied" in proved_view.value.blocking_issue_kinds


def test_repo_public_boundary_batches_all_main_exports(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = _runtime()
    _write_preparation_input(runtime, tmp_path)
    gate = ReadinessGateComponent(runtime)
    refs = [
        DeclRef(repo=None, node=MAIN_CONTENT_NODE_PATH, name="first", revision=1),
        DeclRef(repo=None, node=MAIN_CONTENT_NODE_PATH, name="second", revision=1),
    ]
    monkeypatch.setattr(
        gate.node.export,
        "list_scope_exports",
        lambda *_args, **_kwargs: runtime.foundation.ok(
            [SimpleNamespace(ref=ref) for ref in refs]
        ),
    )
    batches: list[list[tuple[str, str, ProofAvailability]]] = []

    def check_batch(_repo_root: Path, *, roots, **_kwargs):
        selected = list(roots)
        batches.append(selected)
        return runtime.foundation.ok(
            [
                DeclReadinessReport(
                    node_path=node_path,
                    decl_name=decl_name,
                    revision=1,
                    required_availability=target,
                    ready=True,
                    summary=f"{node_path}:{decl_name} is ready.",
                )
                for node_path, decl_name, target in selected
            ]
        )

    monkeypatch.setattr(
        runtime.decl_graph,
        "check_decl_proof_policy_batch",
        check_batch,
    )
    monkeypatch.setattr(
        runtime.decl_graph,
        "check_decl_proof_policy_satisfied",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Main export collection must not use the single wrapper")
        ),
    )

    checked = gate._check_repo_public_boundary_proof_policy(tmp_path)

    assert checked.ok and checked.value is not None
    assert checked.value.passed is True
    assert batches == [
        [
            (MAIN_CONTENT_NODE_PATH, "first", ProofAvailability.PROVED),
            (MAIN_CONTENT_NODE_PATH, "second", ProofAvailability.PROVED),
        ]
    ]


def test_decl_ref_readiness_groups_by_repo_and_target_and_preserves_input_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    consumer = tmp_path / "Consumer"
    provider_a = tmp_path / "ProviderA"
    provider_b = tmp_path / "ProviderB"
    runtime = _runtime()
    for repo in (consumer, provider_a, provider_b):
        assert runtime.repo_workspace.metadata.ensure_repo_model(repo).ok
    assert runtime.repo_workspace.metadata.update_repo_config(
        provider_a,
        completion_mode=RepoCompletionMode.INTERFACE_DECLARED,
    ).ok
    assert runtime.repo_workspace.metadata.update_repo_config(
        provider_b,
        completion_mode=RepoCompletionMode.GRAPH_PROVED,
    ).ok
    gate = ReadinessGateComponent(runtime)
    refs = [
        DeclRef(repo=None, node="Main.Local", name="local", revision=1),
        DeclRef(repo="ProviderA", node="Main.A", name="a_first", revision=1),
        DeclRef(repo="ProviderB", node="Main.B", name="b", revision=1),
        DeclRef(repo="ProviderA", node="Main.A", name="a_second", revision=1),
    ]
    batches: list[tuple[Path, list[tuple[str, str, ProofAvailability]]]] = []

    def check_batch(repo_root: Path, *, roots, **_kwargs):
        selected = list(roots)
        batches.append((Path(repo_root), selected))
        return runtime.foundation.ok(
            [
                DeclReadinessReport(
                    node_path=node_path,
                    decl_name=decl_name,
                    revision=1,
                    required_availability=target,
                    ready=True,
                    summary=f"{node_path}:{decl_name} is ready.",
                )
                for node_path, decl_name, target in selected
            ]
        )

    monkeypatch.setattr(
        runtime.decl_graph,
        "check_decl_proof_policy_batch",
        check_batch,
    )

    checked = gate._check_decl_refs_proof_policy_batch(
        consumer,
        refs=refs,
        fallback_node_path="Main.Consumer",
        local_target=ProofAvailability.PROVED,
    )

    assert checked.ok and checked.value is not None
    assert [report.decl_name for report in checked.value] == [
        "local",
        "a_first",
        "b",
        "a_second",
    ]
    assert batches == [
        (consumer, [("Main.Local", "local", ProofAvailability.PROVED)]),
        (
            provider_a,
            [
                ("Main.A", "a_first", ProofAvailability.DECLARED),
                ("Main.A", "a_second", ProofAvailability.DECLARED),
            ],
        ),
        (provider_b, [("Main.B", "b", ProofAvailability.PROVED)]),
    ]
    assert runtime.repo_workspace.metadata.update_repo_config(
        provider_a,
        completion_mode=RepoCompletionMode.GRAPH_PROVED,
    ).ok
    batches.clear()

    refreshed = gate._check_decl_refs_proof_policy_batch(
        consumer,
        refs=refs,
        fallback_node_path="Main.Consumer",
        local_target=ProofAvailability.PROVED,
    )

    assert refreshed.ok and refreshed.value is not None
    assert batches[1] == (
        provider_a,
        [
            ("Main.A", "a_first", ProofAvailability.PROVED),
            ("Main.A", "a_second", ProofAvailability.PROVED),
        ],
    )


def test_decl_ref_readiness_replay_preserves_local_failure_before_later_config_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    consumer = tmp_path / "Consumer"
    provider = tmp_path / "Provider"
    runtime = _runtime()
    assert runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    gate = ReadinessGateComponent(runtime)
    refs = [
        DeclRef(repo=None, node="Main.Local", name="local", revision=1),
        DeclRef(repo="Provider", node="Main", name="external", revision=1),
    ]
    original_config = runtime.repo_workspace.metadata.get_repo_config
    config_reads = 0

    def get_config(repo_root: Path):
        nonlocal config_reads
        if Path(repo_root) == provider:
            config_reads += 1
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "synthetic_external_config_failure",
                    "External config is unavailable.",
                    object_ref=str(provider),
                    field="completion_mode",
                    details={"source": "external"},
                )
            )
        return original_config(repo_root)

    batches: list[list[tuple[str, str, ProofAvailability]]] = []

    def check_batch(_repo_root: Path, *, roots, **_kwargs):
        selected = list(roots)
        batches.append(selected)
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "synthetic_local_readiness_failure",
                "The first local declaration failed.",
                object_ref="Main.Local:local",
                field="statement",
                details={"source": "local"},
            )
        )

    monkeypatch.setattr(
        runtime.repo_workspace.metadata,
        "get_repo_config",
        get_config,
    )
    monkeypatch.setattr(
        runtime.decl_graph,
        "check_decl_proof_policy_batch",
        check_batch,
    )
    monkeypatch.setattr(
        runtime.decl_graph,
        "check_decl_proof_policy_satisfied",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("failure replay must use batch-of-one")
        ),
    )

    checked = gate._check_decl_refs_proof_policy_batch(
        consumer,
        refs=refs,
        fallback_node_path="Main.Consumer",
        local_target=ProofAvailability.PROVED,
    )

    assert not checked.ok
    assert checked.issues[0].kind == "synthetic_local_readiness_failure"
    assert checked.issues[0].object_ref == "Main.Local:local"
    assert checked.issues[0].field == "statement"
    assert checked.issues[0].details == {"source": "local"}
    assert batches == [[("Main.Local", "local", ProofAvailability.PROVED)]]
    assert config_reads == 1


def test_decl_ref_readiness_replay_preserves_local_failure_before_later_unsafe_repo(
    tmp_path: Path,
    monkeypatch,
) -> None:
    consumer = tmp_path / "Consumer"
    runtime = _runtime()
    assert runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    gate = ReadinessGateComponent(runtime)
    local_ref = DeclRef(
        repo=None,
        node="Main.Local",
        name="local",
        revision=1,
    )
    unsafe_ref = DeclRef(
        repo="../Unsafe",
        node="Main",
        name="external",
        revision=1,
    )
    batches: list[list[tuple[str, str, ProofAvailability]]] = []

    def fail_local(_repo_root: Path, *, roots, **_kwargs):
        selected = list(roots)
        batches.append(selected)
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "synthetic_local_readiness_failure",
                "The first local declaration failed.",
                object_ref="Main.Local:local",
                field="statement",
                details={"source": "local"},
            )
        )

    monkeypatch.setattr(
        runtime.decl_graph,
        "check_decl_proof_policy_batch",
        fail_local,
    )
    monkeypatch.setattr(
        runtime.decl_graph,
        "check_decl_proof_policy_satisfied",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("failure replay must use batch-of-one")
        ),
    )

    checked = gate._check_decl_refs_proof_policy_batch(
        consumer,
        refs=[local_ref, unsafe_ref],
        fallback_node_path="Main.Consumer",
        local_target=ProofAvailability.PROVED,
    )

    assert not checked.ok
    assert checked.issues[0].kind == "synthetic_local_readiness_failure"
    assert checked.issues[0].object_ref == "Main.Local:local"
    assert checked.issues[0].field == "statement"
    assert checked.issues[0].details == {"source": "local"}
    assert batches == [[("Main.Local", "local", ProofAvailability.PROVED)]]

    unsafe_first = gate._check_decl_refs_proof_policy_batch(
        consumer,
        refs=[unsafe_ref],
        fallback_node_path="Main.Consumer",
        local_target=ProofAvailability.PROVED,
    )

    assert not unsafe_first.ok
    assert unsafe_first.issues[0].kind == "dependency_provider_invalid"
    assert unsafe_first.issues[0].message == "unsafe key: ../Unsafe"
    assert unsafe_first.issues[0].object_ref == "../Unsafe"
    assert batches == [[("Main.Local", "local", ProofAvailability.PROVED)]]


def test_decl_ref_readiness_replay_preserves_interleaved_provider_failure_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    consumer = tmp_path / "Consumer"
    provider_a = tmp_path / "ProviderA"
    provider_b = tmp_path / "ProviderB"
    runtime = _runtime()
    for repo in (consumer, provider_a, provider_b):
        assert runtime.repo_workspace.metadata.ensure_repo_model(repo).ok
    gate = ReadinessGateComponent(runtime)
    refs = [
        DeclRef(repo="ProviderA", node="Main", name="a_first", revision=1),
        DeclRef(repo="ProviderB", node="Main", name="b", revision=1),
        DeclRef(repo="ProviderA", node="Main", name="a_second", revision=1),
    ]
    batches: list[tuple[Path, list[tuple[str, str, ProofAvailability]]]] = []

    def check_batch(repo_root: Path, *, roots, **_kwargs):
        selected = list(roots)
        batches.append((Path(repo_root), selected))
        if len(selected) > 1:
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "synthetic_provider_a_group_failure",
                    "Provider A grouped evaluation failed.",
                )
            )
        node_path, decl_name, target = selected[0]
        if Path(repo_root) == provider_b:
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "synthetic_provider_b_failure",
                    "Provider B is the first positional failure.",
                    object_ref="ProviderB:Main:b",
                    field="proof",
                    details={"source": "provider_b"},
                )
            )
        return runtime.foundation.ok(
            [
                DeclReadinessReport(
                    node_path=node_path,
                    decl_name=decl_name,
                    revision=1,
                    required_availability=target,
                    ready=True,
                    summary=f"{node_path}:{decl_name} is ready.",
                )
            ]
        )

    monkeypatch.setattr(
        runtime.decl_graph,
        "check_decl_proof_policy_batch",
        check_batch,
    )
    monkeypatch.setattr(
        runtime.decl_graph,
        "check_decl_proof_policy_satisfied",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("failure replay must use batch-of-one")
        ),
    )

    checked = gate._check_decl_refs_proof_policy_batch(
        consumer,
        refs=refs,
        fallback_node_path="Main.Consumer",
        local_target=ProofAvailability.PROVED,
    )

    assert not checked.ok
    assert checked.issues[0].kind == "synthetic_provider_b_failure"
    assert checked.issues[0].object_ref == "ProviderB:Main:b"
    assert checked.issues[0].field == "proof"
    assert checked.issues[0].details == {"source": "provider_b"}
    assert [(repo.name, len(roots)) for repo, roots in batches] == [
        ("ProviderA", 2),
        ("ProviderA", 1),
        ("ProviderB", 1),
    ]


def test_repo_ready_gate_rechecks_exact_root_interface_statement_contract(tmp_path: Path) -> None:
    runtime = _runtime()
    _write_preparation_input(
        runtime,
        tmp_path,
        include_interface=True,
        expected_statement_lean_code="theorem main_result : /- exact target -/ True := by sorry",
    )
    assert runtime.node.ensure_native_root_main_contract(tmp_path).ok
    configured = runtime.repo_workspace.metadata.update_repo_config(
        tmp_path,
        completion_mode=RepoCompletionMode.INTERFACE_DECLARED,
    )
    assert configured.ok, configured.issues
    _create_declared_main_public_theorem(runtime, tmp_path)
    exported = runtime.node.export.add_scope_export(
        tmp_path,
        scope_path="Main",
        decl_node=MAIN_CONTENT_NODE_PATH,
        decl_name="main_result",
    )
    assert exported.ok, exported.issues
    bound = runtime.node.interface.bind_interface_to_decl(
        tmp_path,
        node_path="Main",
        interface_name="main_result",
        decl_name="main_result",
        decl_node=MAIN_CONTENT_NODE_PATH,
    )
    assert bound.ok, bound.issues
    committed = runtime.node.commit_scope_contract(tmp_path, scope_path="Main", summary="Main scope is committed.")
    assert committed.ok, committed.issues
    service = _validation_with_passing_consistency(runtime)

    matching = service.get_repo_ready_view(tmp_path)

    assert matching.ok and matching.value is not None
    assert matching.value.ready_to_submit is True

    revision = runtime.decl_graph.get_decl_revision(
        tmp_path,
        node_path=MAIN_CONTENT_NODE_PATH,
        name="main_result",
        revision=1,
    )
    assert revision.ok and revision.value is not None
    assert revision.value.statement.formal is not None
    revision.value.statement.formal.code = "theorem main_result : False := by sorry"
    revision_path = runtime.decl_graph.graph_store.revision_path(
        tmp_path,
        node_path=MAIN_CONTENT_NODE_PATH,
        decl_name="main_result",
        revision=1,
    )
    assert runtime.foundation.store.write_json_atomic(
        revision_path,
        revision.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok

    drifted = service.get_repo_ready_view(tmp_path)

    assert drifted.ok and drifted.value is not None
    assert drifted.value.ready_to_submit is False
    assert "interface_statement_contract_mismatch" in drifted.value.blocking_issue_kinds
