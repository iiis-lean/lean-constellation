from __future__ import annotations

from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.foundation import FoundationContext, GateReport, ServiceResult
from lean_constellation.services.runtime import LeanRuntimeServices
from lean_constellation.services.validation_snapshot import ReadinessGateComponent, ValidationSnapshotService


NODE_PATH = "Main.Topic.Core"


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


def _write_preparation_input(runtime: LeanRuntimeServices, repo_root: Path, *, include_interface: bool = False) -> None:
    interfaces = [
        DeclInterface(
            name="main_result",
            kind=DeclKind.THEOREM,
            summary="Expose the main theorem.",
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
        end_after_state=DeclState.PROVED,
    )
    assert created.ok, created.issues


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
    runtime = make_runtime()
    _create_scope_and_content(runtime, tmp_path)
    _create_public_decl(runtime, tmp_path)
    service = _validation_with_passing_consistency(runtime)

    view = service.get_content_ready_view(tmp_path, node_path=NODE_PATH)

    assert view.ok and view.value is not None
    assert view.value.ready_to_submit is False
    assert view.value.contract_version_status is not None
    assert any(issue.kind == "content_public_decl_not_ready" for issue in view.value.gate.issues)


def test_scope_ready_view_reports_uncommitted_content_child(tmp_path: Path) -> None:
    runtime = make_runtime()
    _create_scope_and_content(runtime, tmp_path)

    view = runtime.validation_snapshot.get_scope_ready_view(tmp_path, scope_path="Main.Topic")

    assert view.ok and view.value is not None
    assert view.value.ready_to_commit is False
    assert view.value.direct_child_count == 1
    assert view.value.blocking_child_count == 1
    assert view.value.child_readiness_gate.issues[0].kind == "content_child_not_ready"


def test_scope_ready_view_reports_unbound_interface(tmp_path: Path) -> None:
    runtime = make_runtime()
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
    runtime = make_runtime()
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
    runtime = make_runtime()
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
