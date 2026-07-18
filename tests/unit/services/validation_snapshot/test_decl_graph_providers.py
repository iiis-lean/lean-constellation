from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.unit_services_helpers import initialize_native_test_repo, make_runtime, publish_native_provider_release

from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import ProofAvailability, RepoWorkMode
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.decl_graph.models import RepoDeclDep
from lean_constellation.services.external_clients import ExternalCommandResult, LeanCheckSummaryView, LeanDiagnosticsResult
from lean_constellation.services.foundation import GateReport, ServiceResult, WriteMode
from lean_constellation.services.runtime import LeanRuntimeServices
from lean_constellation.services.validation_snapshot.readiness_gate import ReadinessGateComponent


NODE_PATH = "Main.Topic.Core"


class FakeToolkit:
    def run_file_diagnostics(self, repo_root: Path, file_path: Path) -> LeanDiagnosticsResult:
        return LeanDiagnosticsResult(
            ok=True,
            repo_root=str(repo_root),
            file_path=str(file_path),
            diagnostics=[],
            summary="fake toolkit diagnostics",
        )


class FakeLake:
    def __init__(self) -> None:
        self.build_targets: list[str] = []
        self.fail_build = False

    def run_lake_update(self, repo_root: Path, timeout_seconds: int | None = None) -> ExternalCommandResult:
        del timeout_seconds
        return ExternalCommandResult(
            ok=True,
            command=["lake", "update"],
            cwd=str(repo_root),
            exit_code=0,
            summary="fake lake update passed",
        )

    def run_lake_env_lean(
        self,
        *,
        repo_root: Path,
        rel_file: str,
        json: bool = True,
        timeout_seconds: int | None = None,
    ) -> ExternalCommandResult:
        del json, timeout_seconds
        return ExternalCommandResult(
            ok=True,
            command=["lake", "env", "lean", "--json", rel_file],
            cwd=str(repo_root),
            exit_code=0,
            summary="fake lake diagnostics",
        )

    def run_lake_build(self, repo_root: Path, target: str | None = None, targets=None, timeout_seconds=None):  # noqa: ANN001, ANN201
        del targets, timeout_seconds
        self.build_targets.append(target or "")
        return ExternalCommandResult(
            ok=not self.fail_build,
            command=["lake", "build", target or ""],
            cwd=str(repo_root),
            exit_code=1 if self.fail_build else 0,
            summary="fake module build failed" if self.fail_build else "fake module build passed",
        )

    def run_snippet_check(self, *, repo_root: Path, imports: list[str], code: str, timeout_seconds: int | None = None) -> LeanCheckSummaryView:
        del timeout_seconds
        return LeanCheckSummaryView(
            ok=True,
            command=["lake", "env", "lean"],
            summary=f"fake semantic check passed for {imports[0]}: {code[:20]}",
        )


class ProjectionPassConsistency:
    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def check_projection_sync(self, repo_root: Path, *, scope: str = "repo") -> ServiceResult[GateReport]:
        del repo_root
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed("projection_sync", summary=f"Projection sync stub passed for {scope}.")
        )

    def check_formal_stage_consistency(self, repo_root: Path, *, node_path: str, decl_name: str, stage: str) -> ServiceResult[GateReport]:
        return self.runtime.validation_snapshot.consistency.check_formal_stage_consistency(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            stage=stage,
        )


def _runtime() -> LeanRuntimeServices:
    return make_runtime(external_overrides={"lean_mcp_toolkit": FakeToolkit(), "lake": FakeLake()})


def _create_content_node(runtime: LeanRuntimeServices, repo_root: Path) -> None:
    if not (repo_root / "lakefile.toml").exists():
        initialize_native_test_repo(repo_root)
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    created = runtime.node.create_content_node(
        repo_root,
        path=NODE_PATH,
        goal="Core goal.",
        boundary="Core declarations only.",
        objective="Validate DeclGraph provider gates.",
        success_criteria="ValidationSnapshot reads real DeclGraph provider output.",
    )
    assert created.ok, created.issues


def _create_round(runtime: LeanRuntimeServices, repo_root: Path, *, objective: str = "Validation round.") -> str:
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=NODE_PATH, objective="Validation strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective=objective,
    )
    assert round_record.ok and round_record.value is not None
    return round_record.value.round_id


def _create_decl(
    runtime: LeanRuntimeServices,
    repo_root: Path,
    *,
    round_id: str,
    name: str,
    public: bool = True,
    target_state: DeclState = DeclState.PROVED,
) -> Any:
    created = runtime.decl_graph.create_decl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        name=name,
        kind="theorem",
        objective=f"Create {name}.",
        summary=f"{name} summary.",
        public=public,
        target_state=target_state,
    )
    assert created.ok and created.value is not None, created.issues
    return created.value


def _seed_ready_public_theorem(runtime: LeanRuntimeServices, repo_root: Path) -> None:
    _create_content_node(runtime, repo_root)
    round_id = _create_round(runtime, repo_root)
    _create_decl(runtime, repo_root, round_id=round_id, name="main_result", public=True)
    assert runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id).ok
    statement = runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
        origin=[{"kind": "unit_test"}],
        deps=[],
    )
    assert statement.ok, statement.issues
    prepared_statement = runtime.lean_projection.prepare_statement_formal_stage_file(repo_root, node_path=NODE_PATH, decl_name="main_result")
    assert prepared_statement.ok and prepared_statement.value is not None
    statement_path = Path(prepared_statement.value.path)
    statement_path.write_text(
        statement_path.read_text(encoding="utf-8") + "\ntheorem main_result : True := by\n  sorry\n",
        encoding="utf-8",
    )
    assert runtime.lean_projection.capture_statement_formal(repo_root, node_path=NODE_PATH, decl_name="main_result").ok
    proof_nl = runtime.decl_graph.write_proof_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="Use triviality.",
        origin=[{"kind": "unit_test"}],
        deps=[],
    )
    assert proof_nl.ok, proof_nl.issues
    prepared_proof = runtime.lean_projection.prepare_proof_formal_stage_file(repo_root, node_path=NODE_PATH, decl_name="main_result")
    assert prepared_proof.ok and prepared_proof.value is not None, prepared_proof.issues
    path = Path(prepared_proof.value.path)
    path.write_text(path.read_text(encoding="utf-8").replace("sorry", "trivial"), encoding="utf-8")
    assert runtime.lean_projection.capture_proof_formal(repo_root, node_path=NODE_PATH, decl_name="main_result").ok
    committed = runtime.decl_graph.commit_decl_revision(repo_root, node_path=NODE_PATH, name="main_result", state=DeclState.PROVED)
    assert committed.ok, committed.issues
    projection = runtime.lean_projection.refresh_node_projection(repo_root, node_path=NODE_PATH)
    assert projection.ok, projection.issues


def _seed_declared_public_theorem(
    runtime: LeanRuntimeServices,
    repo_root: Path,
    *,
    statement_lean_code: str = "theorem main_result : True := by\n  sorry",
) -> None:
    _create_content_node(runtime, repo_root)
    round_id = _create_round(runtime, repo_root)
    _create_decl(runtime, repo_root, round_id=round_id, name="main_result", public=True, target_state=DeclState.DECLARED)
    assert runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id).ok
    assert runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
        origin=[{"kind": "unit_test"}],
        deps=[],
    ).ok
    prepared = runtime.lean_projection.prepare_statement_formal_stage_file(repo_root, node_path=NODE_PATH, decl_name="main_result")
    assert prepared.ok and prepared.value is not None, prepared.issues
    path = Path(prepared.value.path)
    path.write_text(path.read_text(encoding="utf-8") + "\n" + statement_lean_code.rstrip() + "\n", encoding="utf-8")
    captured = runtime.lean_projection.capture_statement_formal(repo_root, node_path=NODE_PATH, decl_name="main_result")
    assert captured.ok, captured.issues
    committed = runtime.decl_graph.commit_decl_revision(repo_root, node_path=NODE_PATH, name="main_result", state=DeclState.DECLARED)
    assert committed.ok, committed.issues


def _export_main_result_from_provider_main(runtime: LeanRuntimeServices, repo_root: Path) -> None:
    topic_export = runtime.node.export.add_scope_export(
        repo_root,
        scope_path="Main.Topic",
        decl_node=NODE_PATH,
        decl_name="main_result",
        revision=1,
    )
    assert topic_export.ok, topic_export.issues
    topic_commit = runtime.node.commit_scope_contract(
        repo_root,
        scope_path="Main.Topic",
        summary="Publish topic public declarations.",
    )
    assert topic_commit.ok, topic_commit.issues
    main_export = runtime.node.export.add_scope_export(
        repo_root,
        scope_path="Main",
        decl_node=NODE_PATH,
        decl_name="main_result",
        revision=1,
    )
    assert main_export.ok, main_export.issues


def _seed_proved_public_theorem_with_provider_dep(runtime: LeanRuntimeServices, repo_root: Path, *, provider_repo: str):  # noqa: ANN201
    _create_content_node(runtime, repo_root)
    round_id = _create_round(runtime, repo_root)
    _create_decl(runtime, repo_root, round_id=round_id, name="consumer_result", public=True)
    assert runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id).ok
    assert runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="consumer_result",
        nl="The consumer result states True.",
        deps=[],
    ).ok
    prepared_statement = runtime.lean_projection.prepare_statement_formal_stage_file(
        repo_root, node_path=NODE_PATH, decl_name="consumer_result"
    )
    assert prepared_statement.ok and prepared_statement.value is not None, prepared_statement.issues
    statement_path = Path(prepared_statement.value.path)
    statement_path.write_text(
        statement_path.read_text(encoding="utf-8") + "\ntheorem consumer_result : True := by\n  sorry\n",
        encoding="utf-8",
    )
    assert runtime.lean_projection.capture_statement_formal(repo_root, node_path=NODE_PATH, decl_name="consumer_result").ok
    assert runtime.decl_graph.write_proof_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="consumer_result",
        nl="Use the declared provider theorem as an accepted external interface.",
        deps=[],
    ).ok
    node_dep = runtime.node.add_current_node_dep(
        repo_root,
        node_path=NODE_PATH,
        target_repo=provider_repo,
        target_node="Main",
        reason="The proof uses the provider's public node boundary.",
        actor="coordinator",
        expected_public_decl_names=["main_result"],
    )
    if not node_dep.ok:
        return node_dep
    added_dep = runtime.decl_graph.add_proof_dep(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="consumer_result",
        dep=RepoDeclDep(
            ref=DeclRef(repo=provider_repo, node=NODE_PATH, name="main_result", revision=1),
            reason="Provider publishes this theorem at declared proof availability.",
        ),
    )
    if not added_dep.ok:
        return added_dep
    prepared_proof = runtime.lean_projection.prepare_proof_formal_stage_file(
        repo_root, node_path=NODE_PATH, decl_name="consumer_result"
    )
    assert prepared_proof.ok and prepared_proof.value is not None, prepared_proof.issues
    proof_path = Path(prepared_proof.value.path)
    proof_path.write_text(proof_path.read_text(encoding="utf-8").replace("sorry", "trivial"), encoding="utf-8")
    captured_proof = runtime.lean_projection.capture_proof_formal(
        repo_root, node_path=NODE_PATH, decl_name="consumer_result"
    )
    assert captured_proof.ok, captured_proof.issues
    committed = runtime.decl_graph.commit_decl_revision(repo_root, node_path=NODE_PATH, name="consumer_result", state=DeclState.PROVED)
    assert committed.ok, committed.issues
    return committed


def test_content_ready_gate_uses_default_decl_graph_provider_pass(tmp_path: Path) -> None:
    runtime = _runtime()
    _seed_ready_public_theorem(runtime, tmp_path)

    ready = runtime.validation_snapshot.check_content_node_ready(tmp_path, node_path=NODE_PATH)

    assert ready.ok, ready.issues
    assert ready.value is not None
    assert ready.value.passed is True
    assert runtime.validation_snapshot.readiness_gate.content_readiness_provider is runtime.decl_graph


def test_content_completion_accepts_declared_theorem_under_declared_target(tmp_path: Path) -> None:
    runtime = _runtime()
    _seed_declared_public_theorem(runtime, tmp_path)
    configured = runtime.repo_workspace.metadata.update_repo_config(
        tmp_path,
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
    )
    assert configured.ok
    gate = ReadinessGateComponent(
        runtime,
        consistency=ProjectionPassConsistency(runtime),
        content_readiness_provider=runtime.decl_graph,
    )

    completion = gate.check_content_node_completion(tmp_path, node_path=NODE_PATH)

    assert completion.ok and completion.value is not None
    assert completion.value.ready_to_submit is True
    assert completion.value.target_proof_availability == ProofAvailability.DECLARED
    assert completion.value.checked_decl_count == 1
    assert completion.value.gate.gate_name == "content_node_completion"
    lake = runtime.external.lean_toolchain.lake
    assert "+TestProject.Main.Topic.Core.Interfaces" in lake.build_targets


def test_content_completion_rejects_stale_capture_even_when_interfaces_builds(tmp_path: Path) -> None:
    runtime = _runtime()
    _seed_declared_public_theorem(runtime, tmp_path)
    assert runtime.repo_workspace.metadata.update_repo_config(
        tmp_path,
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
    ).ok
    path_view = runtime.lean_projection.decl_file.derive_decl_file_path(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
        kind="theorem",
    )
    assert path_view.ok and path_view.value is not None
    path = Path(path_view.value.path)
    path.write_text(path.read_text(encoding="utf-8") + "\n-- stale after capture\n", encoding="utf-8")
    gate = ReadinessGateComponent(
        runtime,
        consistency=ProjectionPassConsistency(runtime),
        content_readiness_provider=runtime.decl_graph,
    )

    completion = gate.check_content_node_completion(tmp_path, node_path=NODE_PATH)

    assert completion.ok and completion.value is not None
    assert completion.value.ready_to_submit is False
    assert "decl_file_capture_stale" in completion.value.blocking_issue_kinds


def test_content_completion_rejects_missing_compiler_confirmed_decl_identity(tmp_path: Path) -> None:
    runtime = _runtime()
    _seed_declared_public_theorem(runtime, tmp_path)
    current = runtime.decl_graph.get_decl_revision(
        tmp_path,
        node_path=NODE_PATH,
        name="main_result",
        revision=1,
    )
    assert current.ok and current.value is not None
    current.value.lean_decl_name = None
    revision_path = runtime.decl_graph.graph_store.revision_path(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="main_result",
        revision=1,
    )
    assert runtime.foundation.store.write_json_atomic(
        revision_path,
        current.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    gate = ReadinessGateComponent(
        runtime,
        consistency=ProjectionPassConsistency(runtime),
        content_readiness_provider=runtime.decl_graph,
    )

    completion = gate.check_content_node_completion(tmp_path, node_path=NODE_PATH)

    assert completion.ok and completion.value is not None
    assert completion.value.ready_to_submit is False
    assert "lean_decl_name_missing" in completion.value.blocking_issue_kinds


def test_content_completion_reports_interfaces_build_failure_as_blocking_issue(tmp_path: Path) -> None:
    runtime = _runtime()
    _seed_declared_public_theorem(runtime, tmp_path)
    assert runtime.repo_workspace.metadata.update_repo_config(
        tmp_path,
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
    ).ok
    runtime.external.lean_toolchain.lake.fail_build = True
    gate = ReadinessGateComponent(
        runtime,
        consistency=ProjectionPassConsistency(runtime),
        content_readiness_provider=runtime.decl_graph,
    )

    completion = gate.check_content_node_completion(tmp_path, node_path=NODE_PATH)

    assert completion.ok and completion.value is not None
    assert completion.value.ready_to_submit is False
    assert "decl_module_build_failed" in completion.value.blocking_issue_kinds


def test_content_completion_rejects_declared_theorem_under_proved_target(tmp_path: Path) -> None:
    runtime = _runtime()
    _seed_declared_public_theorem(runtime, tmp_path)
    configured = runtime.repo_workspace.metadata.update_repo_config(
        tmp_path,
        target_proof_availability=ProofAvailability.PROVED,
        work_mode=RepoWorkMode.PROVED_FULL_GRAPH,
    )
    assert configured.ok
    gate = ReadinessGateComponent(
        runtime,
        consistency=ProjectionPassConsistency(runtime),
        content_readiness_provider=runtime.decl_graph,
    )

    completion = gate.check_content_node_completion(tmp_path, node_path=NODE_PATH)

    assert completion.ok and completion.value is not None
    assert completion.value.ready_to_submit is False
    assert completion.value.target_proof_availability == ProofAvailability.PROVED
    assert "content_decl_proof_policy_unsatisfied" in completion.value.blocking_issue_kinds


def _setup_stable_declared_provider_consumer(workspace: Path) -> tuple[LeanRuntimeServices, Path, Path]:
    consumer = workspace / "Consumer"
    provider = workspace / "Provider"
    consumer.mkdir()
    initialize_native_test_repo(consumer, project_name="Consumer")
    runtime = _runtime()
    assert runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    created = runtime.repo_workspace.create_requirement_with_interfaces(
        consumer,
        name="need_provider_result",
        target_repo="Provider",
        source_description="Consumer proof uses Provider.main_result as an external interface.",
        reason="Provider theorem is intentionally requested at declared availability.",
        interfaces=[{"name": "main_result", "kind": "theorem", "summary": "Provider theorem interface."}],
    )
    assert created.ok, created.issues
    waiting = runtime.repo_workspace.requirement.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider_result",
        provider_repo="Provider",
        reason="Coordinator submitted the declared provider request.",
    )
    assert waiting.ok, waiting.issues
    shell = runtime.repo_workspace.create_provider_repo_shell_from_group(workspace, target_repo="Provider")
    assert shell.ok, shell.issues
    _seed_declared_public_theorem(runtime, provider)
    _export_main_result_from_provider_main(runtime, provider)
    release = publish_native_provider_release(runtime, provider, summary="Provider publishes declared theorem interface.")
    marked = runtime.validation_snapshot.reconcile_provider_requirements(provider, release_id=release.release_id)
    assert marked.ok, marked.issues
    assert runtime.repo_workspace.requirement.mark_requirement_result_observed(
        consumer,
        requirement_name="need_provider_result",
        note="Consumer resumes from stable declared provider.",
    ).ok
    attached = runtime.repo_workspace.attach_provider_for_requirement(
        consumer,
        requirement_name="need_provider_result",
    )
    assert attached.ok, attached.issues
    assert runtime.repo_workspace.metadata.update_repo_config(
        consumer,
        target_proof_availability=ProofAvailability.PROVED,
        work_mode=RepoWorkMode.PROVED_FULL_GRAPH,
    ).ok
    seeded = _seed_proved_public_theorem_with_provider_dep(runtime, consumer, provider_repo="Provider")
    assert seeded.ok, seeded.issues
    return runtime, consumer, provider


def test_content_completion_accepts_stable_declared_provider_dependency(tmp_path: Path) -> None:
    runtime, consumer, _provider = _setup_stable_declared_provider_consumer(tmp_path)
    gate = ReadinessGateComponent(
        runtime,
        consistency=ProjectionPassConsistency(runtime),
        content_readiness_provider=runtime.decl_graph,
    )

    proof_policy = runtime.decl_graph.check_decl_proof_policy_satisfied(
        consumer,
        node_path=NODE_PATH,
        decl_name="consumer_result",
    )
    completion = gate.check_content_node_completion(consumer, node_path=NODE_PATH)

    assert proof_policy.ok and proof_policy.value is not None
    assert proof_policy.value.proof_policy_satisfied is True
    assert proof_policy.value.dependencies_checked == ["Provider:Main.Topic.Core:main_result"]
    assert completion.ok and completion.value is not None
    assert completion.value.ready_to_submit is True
    assert completion.value.target_proof_availability == ProofAvailability.PROVED


def test_content_completion_rejects_unexported_external_expected_decl(tmp_path: Path) -> None:
    runtime, consumer, _provider = _setup_stable_declared_provider_consumer(tmp_path)
    current = runtime.node.contract.get_current_contract(consumer, node_path=NODE_PATH)
    assert current.ok and current.value is not None
    candidate = current.value.contract.model_copy(deep=True)
    assert len(candidate.deps) == 1
    candidate.deps[0].expected_decl_refs = [
        DeclRef(repo="Provider", node=NODE_PATH, name="missing_result", revision=1)
    ]
    assert runtime.node.contract._persist_open_candidate(  # noqa: SLF001 - direct gate fixture setup
        consumer,
        node_path=NODE_PATH,
        candidate=candidate,
    ).ok
    gate = ReadinessGateComponent(
        runtime,
        consistency=ProjectionPassConsistency(runtime),
        content_readiness_provider=runtime.decl_graph,
    )

    completion = gate.check_content_node_completion(consumer, node_path=NODE_PATH)

    assert completion.ok and completion.value is not None
    assert completion.value.ready_to_submit is False
    assert "node_dep_external_expected_decl_incompatible" in completion.value.blocking_issue_kinds


def test_provider_requirement_rejects_exact_statement_drift(tmp_path: Path) -> None:
    workspace = tmp_path
    consumer = workspace / "Consumer"
    provider = workspace / "Provider"
    consumer.mkdir()
    runtime = _runtime()
    assert runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    created = runtime.repo_workspace.create_requirement_with_interfaces(
        consumer,
        name="need_provider_result",
        target_repo="Provider",
        source_description="Consumer proof needs one exact provider theorem.",
        reason="The provider statement is part of the dependency contract.",
        interfaces=[
            {
                "name": "main_result",
                "kind": "theorem",
                "summary": "Exact provider theorem interface.",
                "expected_statement_lean_code": "theorem main_result : False := by sorry",
            }
        ],
    )
    assert created.ok, created.issues
    waiting = runtime.repo_workspace.requirement.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider_result",
        provider_repo="Provider",
    )
    assert waiting.ok, waiting.issues
    shell = runtime.repo_workspace.create_provider_repo_shell_from_group(workspace, target_repo="Provider")
    assert shell.ok, shell.issues
    _seed_declared_public_theorem(runtime, provider)
    _export_main_result_from_provider_main(runtime, provider)

    validated = runtime.repo_workspace.requirement.validate_requirement_provider_truth(
        consumer,
        requirement_name="need_provider_result",
        provider_repo="Provider",
        require_stable=False,
    )

    assert not validated.ok
    assert validated.issues[0].kind == "provider_interface_statement_contract_mismatch"
    publication = runtime.repo_workspace.metadata.get_repo_publication(provider)
    assert publication.ok and publication.value is not None
    assert publication.value.publication.status == "developing"


def test_cross_repo_dependency_requires_provider_public_export(tmp_path: Path) -> None:
    workspace = tmp_path
    consumer = workspace / "Consumer"
    provider = workspace / "Provider"
    consumer.mkdir()
    provider.mkdir()
    initialize_native_test_repo(consumer, project_name="Consumer")
    runtime = _runtime()
    assert runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    assert runtime.repo_workspace.metadata.ensure_repo_model(provider).ok
    assert runtime.repo_workspace.metadata.update_repo_config(
        provider,
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
    ).ok
    _seed_declared_public_theorem(runtime, provider)
    publish_native_provider_release(runtime, provider, summary="Provider marked stable without Main export.")
    attached = runtime.repo_workspace.attach_ready_workspace_repo_dependency(consumer, provider_repo="Provider")
    assert attached.ok, attached.issues
    assert runtime.repo_workspace.metadata.update_repo_config(
        consumer,
        target_proof_availability=ProofAvailability.PROVED,
        work_mode=RepoWorkMode.PROVED_FULL_GRAPH,
    ).ok
    seeded = _seed_proved_public_theorem_with_provider_dep(runtime, consumer, provider_repo="Provider")

    assert not seeded.ok
    assert seeded.issues[0].kind == "node_dep_expected_decl_missing"


def test_strict_proved_audit_does_not_downgrade_declared_provider_policy(tmp_path: Path) -> None:
    workspace = tmp_path
    consumer = workspace / "Consumer"
    provider = workspace / "Provider"
    consumer.mkdir()
    provider.mkdir()
    initialize_native_test_repo(consumer, project_name="Consumer")
    runtime = _runtime()
    assert runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    assert runtime.repo_workspace.metadata.ensure_repo_model(provider).ok
    assert runtime.repo_workspace.metadata.update_repo_config(
        provider,
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
    ).ok
    _seed_declared_public_theorem(runtime, provider)
    _export_main_result_from_provider_main(runtime, provider)
    publish_native_provider_release(runtime, provider, summary="Provider publishes declared theorem interface.")
    attached = runtime.repo_workspace.attach_ready_workspace_repo_dependency(consumer, provider_repo="Provider")
    assert attached.ok, attached.issues
    assert runtime.repo_workspace.metadata.update_repo_config(
        consumer,
        target_proof_availability=ProofAvailability.PROVED,
        work_mode=RepoWorkMode.PROVED_FULL_GRAPH,
    ).ok
    seeded = _seed_proved_public_theorem_with_provider_dep(runtime, consumer, provider_repo="Provider")
    assert seeded.ok, seeded.issues

    normal = runtime.decl_graph.check_decl_proof_policy_satisfied(
        consumer,
        node_path=NODE_PATH,
        decl_name="consumer_result",
    )
    audit = runtime.decl_graph.run_strict_proved_audit(consumer, node_path=NODE_PATH)

    assert normal.ok and normal.value is not None
    assert normal.value.proof_policy_satisfied is True
    assert audit.ok and audit.value is not None
    assert audit.value.passed is False
    assert audit.value.findings[0].kind == "strict_proved_decl_not_satisfied"


def test_content_ready_gate_preserves_decl_graph_not_ready_issue(tmp_path: Path) -> None:
    runtime = _runtime()
    _create_content_node(runtime, tmp_path)
    round_id = _create_round(runtime, tmp_path)
    _create_decl(runtime, tmp_path, round_id=round_id, name="main_result", public=True)
    assert runtime.decl_graph.start_round(tmp_path, node_path=NODE_PATH, round_id=round_id).ok
    statement = runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
        origin=[{"kind": "unit_test"}],
        deps=[],
    )
    assert statement.ok, statement.issues

    ready_gate = ReadinessGateComponent(
        runtime,
        consistency=ProjectionPassConsistency(runtime),  # type: ignore[arg-type]
        content_readiness_provider=runtime.decl_graph,
    )
    ready = ready_gate.check_content_node_ready(tmp_path, node_path=NODE_PATH)

    assert ready.ok, ready.issues
    assert ready.value is not None
    assert ready.value.passed is False
    assert any(issue.kind == "content_public_decl_not_ready" for issue in ready.value.issues)


def test_round_local_audit_uses_default_decl_graph_provider(tmp_path: Path) -> None:
    runtime = _runtime()
    _create_content_node(runtime, tmp_path)
    round_id = _create_round(runtime, tmp_path)
    _create_decl(runtime, tmp_path, round_id=round_id, name="supporting_lemma", public=False)
    _create_decl(runtime, tmp_path, round_id=round_id, name="main_result", public=False)
    assert runtime.decl_graph.start_round(tmp_path, node_path=NODE_PATH, round_id=round_id).ok
    statement = runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The main result depends on a same-round declaration.",
        origin=[{"kind": "unit_test"}],
        deps=["supporting_lemma"],
    )
    assert statement.ok, statement.issues

    audit = runtime.validation_snapshot.run_round_local_audit(tmp_path, node_path=NODE_PATH, round_id=round_id, stage="statement")

    assert audit.ok, audit.issues
    assert audit.value is not None
    assert audit.value.passed is False
    assert audit.value.findings[0].kind == "round_internal_dependency"


def test_delete_sanity_guard_rejects_inbound_current_refs_before_audit(tmp_path: Path) -> None:
    runtime = _runtime()
    _create_content_node(runtime, tmp_path)
    round_id = _create_round(runtime, tmp_path)
    _create_decl(runtime, tmp_path, round_id=round_id, name="supporting_lemma", public=False)
    _create_decl(runtime, tmp_path, round_id=round_id, name="main_result", public=False)
    assert runtime.decl_graph.start_round(tmp_path, node_path=NODE_PATH, round_id=round_id).ok
    statement = runtime.decl_graph.write_statement_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The main result depends on the support lemma.",
        origin=[{"kind": "unit_test"}],
        deps=["supporting_lemma"],
    )
    assert statement.ok, statement.issues
    assert runtime.decl_graph.commit_decl_revision(tmp_path, node_path=NODE_PATH, name="supporting_lemma", state=DeclState.PLANNED).ok
    assert runtime.decl_graph.commit_decl_revision(tmp_path, node_path=NODE_PATH, name="main_result", state=DeclState.SPECIFIED).ok
    assert runtime.decl_graph.write_decl_change_summary(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        change_id=runtime.decl_graph.get_round(tmp_path, node_path=NODE_PATH, round_id=round_id).value.change_ids[0],  # type: ignore[union-attr]
        summary="Created support.",
    ).ok
    assert runtime.decl_graph.write_decl_change_summary(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        change_id=runtime.decl_graph.get_round(tmp_path, node_path=NODE_PATH, round_id=round_id).value.change_ids[1],  # type: ignore[union-attr]
        summary="Created main.",
    ).ok
    assert runtime.decl_graph.write_round_summary(tmp_path, node_path=NODE_PATH, round_id=round_id, summary="Seeded dependency.").ok
    assert runtime.decl_graph.mark_round_terminal(tmp_path, node_path=NODE_PATH, round_id=round_id, result_kind="success").ok
    delete_round_id = _create_round(runtime, tmp_path, objective="Delete only support.")
    delete = runtime.decl_graph.mark_decl_delete(
        tmp_path,
        node_path=NODE_PATH,
        round_id=delete_round_id,
        name="supporting_lemma",
        objective="Delete support only.",
    )
    assert not delete.ok
    assert delete.issues[0].kind == "decl_delete_current_inbound_refs"
