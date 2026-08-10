from pathlib import Path

from tests.unit_services_helpers import (
    initialize_native_test_repo,
    lean_check_payload,
    make_runtime,
)

from lean_constellation.domain.lean_check import LeanCheck
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.decl_graph.models import (
    DeclFormalSection,
    DeclProof,
    DeclStatement,
    RepoDeclDep,
)
from lean_constellation.services.foundation import ServiceResult, WriteMode


NODE_PATH = "Main.Topic.Core"


def _prepare_repo(repo_root: Path) -> str:
    initialize_native_test_repo(repo_root)
    runtime = make_runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.create_scope_node(
        repo_root,
        path="Main.Topic",
        goal="Topic goal.",
        boundary="Topic boundary.",
    ).ok
    assert runtime.node.create_content_node(
        repo_root,
        path=NODE_PATH,
        goal="Core goal.",
        boundary="Core boundary.",
        objective="Build core declarations.",
        success_criteria="Core declarations are ready.",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(
        repo_root,
        node_path=NODE_PATH,
        objective="Build the public API.",
    )
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Declare foundations and result.",
    )
    assert round_record.ok and round_record.value is not None
    return round_record.value.round_id


def _seed_definition(
    repo_root: Path,
    *,
    round_id: str,
    name: str,
    public: bool,
    statement_deps: list[str] | None = None,
    proof_deps: list[str] | None = None,
) -> None:
    runtime = make_runtime()
    created = runtime.decl_graph.create_decl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        name=name,
        kind="definition",
        objective=f"Define {name}.",
        summary=f"{name} definition.",
        public=public,
        target_state=DeclState.DECLARED,
    )
    assert created.ok, created.issues
    decl = runtime.decl_graph.get_decl(
        repo_root,
        node_path=NODE_PATH,
        name=name,
    )
    revision = runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=NODE_PATH,
        name=name,
        revision=1,
    )
    assert decl.ok and decl.value is not None
    assert revision.ok and revision.value is not None
    revision.value.state = DeclState.DECLARED
    revision.value.lean_decl_name = name
    revision.value.statement = DeclStatement(
        formal=DeclFormalSection(
            code=f"def {name} : Nat := 0",
            check=LeanCheck.model_validate(lean_check_payload()),
        ),
        deps=[
            RepoDeclDep(
                ref=DeclRef(node=NODE_PATH, name=dependency, revision=1),
                reason="Required by the formal statement.",
            )
            for dependency in statement_deps or []
        ],
    )
    revision.value.proof = DeclProof(
        deps=[
            RepoDeclDep(
                ref=DeclRef(node=NODE_PATH, name=dependency, revision=1),
                reason="Used only by the proof implementation.",
            )
            for dependency in proof_deps or []
        ],
    )
    written = runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.revision_path(
            repo_root,
            node_path=NODE_PATH,
            decl_name=name,
            revision=1,
        ),
        revision.value,
        mode=WriteMode.UPDATE_EXISTING,
    )
    assert written.ok
    committed = runtime.decl_graph.commit_decl_revision(
        repo_root,
        node_path=NODE_PATH,
        name=name,
        state=DeclState.DECLARED,
    )
    assert committed.ok, committed.issues


def _commit_content_head(repo_root: Path, *names: str) -> None:
    committed = make_runtime().node.contract._commit_content_contract_with_head(
        repo_root,
        node_path=NODE_PATH,
        summary="Commit the fixture Content boundary.",
        decl_graph_head={name: 1 for name in names},
    )
    assert committed.ok, committed.issues


def test_node_closure_reports_and_promotes_private_statement_dependency(
    tmp_path: Path,
) -> None:
    round_id = _prepare_repo(tmp_path)
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="Family",
        public=False,
    )
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="MainResult",
        public=True,
        statement_deps=["Family"],
    )
    runtime = make_runtime()

    before = runtime.node.public_statement_closure.inspect_content(
        tmp_path,
        node_path=NODE_PATH,
    )
    assert before.ok and before.value is not None
    assert before.value.closure_complete is False
    assert [ref.name for ref in before.value.required_public_promotions] == [
        "Family"
    ]
    assert {
        issue.kind for issue in before.value.issues
    } == {"public_statement_decl_not_public"}

    promoted = runtime.node.public_statement_closure.promote_content_closure(
        tmp_path,
        node_path=NODE_PATH,
    )
    assert promoted.ok and promoted.value is not None
    assert promoted.value.changed is True
    assert [ref.name for ref in promoted.value.promoted_declarations] == [
        "Family"
    ]
    assert promoted.value.report.closure_complete is True

    replay = runtime.node.public_statement_closure.promote_content_closure(
        tmp_path,
        node_path=NODE_PATH,
    )
    assert replay.ok and replay.value is not None
    assert replay.value.changed is False


def test_content_commit_rejects_private_public_statement_dependency(
    tmp_path: Path,
) -> None:
    round_id = _prepare_repo(tmp_path)
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="Family",
        public=False,
    )
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="MainResult",
        public=True,
        statement_deps=["Family"],
    )

    committed = make_runtime().node.commit_content_contract(
        tmp_path,
        node_path=NODE_PATH,
        summary="Publish an incomplete boundary.",
    )

    assert not committed.ok
    assert {
        issue.kind for issue in committed.issues
    } == {"public_statement_decl_not_public"}


def test_main_scope_closure_adds_statement_dependency_to_scope_chain(
    tmp_path: Path,
) -> None:
    round_id = _prepare_repo(tmp_path)
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="Family",
        public=False,
    )
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="MainResult",
        public=True,
        statement_deps=["Family"],
    )
    _commit_content_head(tmp_path, "Family", "MainResult")
    runtime = make_runtime()
    assert runtime.node.export.add_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        decl_node=NODE_PATH,
        decl_name="MainResult",
    ).ok
    committed_topic = runtime.node.contract._commit_scope_contract_after_guard(
        tmp_path,
        scope_path="Main.Topic",
        summary="Seed a legacy incomplete public boundary.",
    )
    assert committed_topic.ok, committed_topic.issues
    assert runtime.node.export.add_scope_export(
        tmp_path,
        scope_path="Main",
        decl_node=NODE_PATH,
        decl_name="MainResult",
    ).ok
    rejected_main = runtime.node.commit_scope_contract(
        tmp_path,
        scope_path="Main",
        summary="Attempt to commit an incomplete Main boundary.",
    )
    assert not rejected_main.ok
    assert {
        issue.kind for issue in rejected_main.issues
    } == {
        "public_statement_decl_not_public",
        "public_statement_export_missing",
    }

    before = runtime.node.public_statement_closure.inspect_scope(
        tmp_path,
        scope_path="Main",
    )
    assert before.ok and before.value is not None
    assert before.value.closure_complete is False
    assert [ref.name for ref in before.value.required_public_promotions] == [
        "Family"
    ]
    assert set(before.value.required_export_additions) == {
        "Main",
        "Main.Topic",
    }

    promoted = runtime.node.public_statement_closure.promote_scope_closure(
        tmp_path,
        scope_path="Main",
    )
    assert promoted.ok and promoted.value is not None
    assert promoted.value.report.closure_complete is True
    assert set(promoted.value.added_exports) == {"Main", "Main.Topic"}
    for scope_path in ("Main.Topic", "Main"):
        exports = runtime.node.export.list_scope_exports(
            tmp_path,
            scope_path=scope_path,
        )
        assert exports.ok and exports.value is not None
        assert {item.ref.name for item in exports.value} == {
            "Family",
            "MainResult",
        }


def test_scope_explicit_root_adds_existing_boundary_and_stops_at_target_scope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    round_id = _prepare_repo(tmp_path)
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="Family",
        public=False,
    )
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="MainResult",
        public=True,
        statement_deps=["Family"],
    )
    _commit_content_head(tmp_path, "Family", "MainResult")
    runtime = make_runtime()
    batch_calls = 0
    original_batch = runtime.decl_graph.check_decl_proof_policy_batch

    def count_batch(*args, **kwargs):
        nonlocal batch_calls
        batch_calls += 1
        return original_batch(*args, **kwargs)

    monkeypatch.setattr(
        runtime.decl_graph,
        "check_decl_proof_policy_batch",
        count_batch,
    )
    promoted = runtime.node.public_statement_closure.promote_scope_closure(
        tmp_path,
        scope_path="Main.Topic",
        roots=[DeclRef(node=NODE_PATH, name="MainResult")],
    )

    assert promoted.ok and promoted.value is not None
    assert batch_calls == 1
    assert [ref.name for ref in promoted.value.promoted_declarations] == ["Family"]
    assert set(promoted.value.added_exports) == {"Main.Topic"}
    topic_exports = runtime.node.export.list_scope_exports(
        tmp_path,
        scope_path="Main.Topic",
    )
    main_exports = runtime.node.export.list_scope_exports(
        tmp_path,
        scope_path="Main",
    )
    assert topic_exports.ok and topic_exports.value is not None
    assert {item.ref.name for item in topic_exports.value} == {
        "Family",
        "MainResult",
    }
    assert main_exports.ok and main_exports.value == []


def test_scope_explicit_root_must_be_visible_from_a_direct_child_boundary(
    tmp_path: Path,
) -> None:
    round_id = _prepare_repo(tmp_path)
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="MainResult",
        public=True,
    )
    runtime = make_runtime()

    inspected = runtime.node.public_statement_closure.inspect_scope(
        tmp_path,
        scope_path="Main",
        roots=[DeclRef(node=NODE_PATH, name="MainResult")],
    )

    assert not inspected.ok
    assert {issue.kind for issue in inspected.issues} == {
        "public_statement_scope_root_not_child_public"
    }


def test_node_closure_ignores_proof_only_dependencies_and_handles_cycles(
    tmp_path: Path,
) -> None:
    round_id = _prepare_repo(tmp_path)
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="ProofHelper",
        public=False,
    )
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="CycleA",
        public=True,
        statement_deps=["CycleB"],
        proof_deps=["ProofHelper"],
    )
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="CycleB",
        public=False,
        statement_deps=["CycleA"],
    )

    inspected = make_runtime().node.public_statement_closure.inspect_content(
        tmp_path,
        node_path=NODE_PATH,
    )

    assert inspected.ok and inspected.value is not None
    assert [ref.name for ref in inspected.value.required_public_promotions] == [
        "CycleB"
    ]
    assert {item.ref.name for item in inspected.value.declarations} == {
        "CycleA",
        "CycleB",
    }


def test_node_closure_promotion_rolls_back_on_projection_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    round_id = _prepare_repo(tmp_path)
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="Family",
        public=False,
    )
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="MainResult",
        public=True,
        statement_deps=["Family"],
    )
    runtime = make_runtime()

    def fail_refresh(*_args, **_kwargs):
        return ServiceResult(
            ok=False,
            issues=[
                runtime.foundation.issue(
                    "test_projection_failure",
                    "Injected projection failure.",
                )
            ],
        )

    monkeypatch.setattr(
        runtime.lean_projection.node_projection,
        "refresh_interfaces",
        fail_refresh,
    )
    promoted = runtime.node.public_statement_closure.promote_content_closure(
        tmp_path,
        node_path=NODE_PATH,
    )

    assert not promoted.ok
    restored = runtime.decl_graph.get_decl(
        tmp_path,
        node_path=NODE_PATH,
        name="Family",
    )
    assert restored.ok and restored.value is not None
    assert restored.value.public is False


def test_visibility_revision_promotes_and_replays_without_new_revision(
    tmp_path: Path,
) -> None:
    round_id = _prepare_repo(tmp_path)
    _seed_definition(tmp_path, round_id=round_id, name="Helper", public=False)
    runtime = make_runtime()

    revised = runtime.node.public_statement_closure.revise_content_decl_visibility(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="Helper",
        expected_current_visibility="private",
        new_visibility="public",
        reason="Expose the reusable construction.",
    )
    replay = runtime.node.public_statement_closure.revise_content_decl_visibility(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="Helper",
        expected_current_visibility="public",
        new_visibility="public",
        reason="Confirm the reviewed public boundary.",
    )

    assert revised.ok and revised.value is not None
    assert revised.value.changed is True
    assert revised.value.old_visibility == "private"
    assert revised.value.new_visibility == "public"
    assert revised.value.gate_reports[0].passed is True
    assert replay.ok and replay.value is not None
    assert replay.value.changed is False
    decl = runtime.decl_graph.get_decl(tmp_path, node_path=NODE_PATH, name="Helper")
    assert decl.ok and decl.value is not None
    assert decl.value.public is True
    assert decl.value.current_revision == 1


def test_visibility_revision_demotes_proof_only_helper(
    tmp_path: Path,
) -> None:
    round_id = _prepare_repo(tmp_path)
    _seed_definition(tmp_path, round_id=round_id, name="ProofHelper", public=True)
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="PublicResult",
        public=True,
        proof_deps=["ProofHelper"],
    )
    runtime = make_runtime()

    revised = runtime.node.public_statement_closure.revise_content_decl_visibility(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="ProofHelper",
        expected_current_visibility="public",
        new_visibility="private",
        reason="The declaration is used only by a proof implementation.",
    )

    assert revised.ok and revised.value is not None
    assert revised.value.changed is True
    assert revised.value.gate_reports[0].passed is True
    decl = runtime.decl_graph.get_decl(tmp_path, node_path=NODE_PATH, name="ProofHelper")
    assert decl.ok and decl.value is not None and decl.value.public is False
    assert decl.value.current_revision == 1


def test_visibility_revision_rejects_stale_cas_without_mutation(
    tmp_path: Path,
) -> None:
    round_id = _prepare_repo(tmp_path)
    _seed_definition(tmp_path, round_id=round_id, name="Helper", public=True)
    runtime = make_runtime()
    path = runtime.decl_graph.graph_store.decl_record_path(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="Helper",
    )
    before = path.read_bytes()

    revised = runtime.node.public_statement_closure.revise_content_decl_visibility(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="Helper",
        expected_current_visibility="private",
        new_visibility="public",
        reason="Based on a stale inspection.",
    )

    assert not revised.ok
    assert [issue.kind for issue in revised.issues] == ["decl_visibility_cas_mismatch"]
    assert path.read_bytes() == before


def test_visibility_revision_rejects_interface_binding(
    tmp_path: Path,
) -> None:
    round_id = _prepare_repo(tmp_path)
    _seed_definition(tmp_path, round_id=round_id, name="PublicResult", public=True)
    runtime = make_runtime()
    assert runtime.node.interface.add_interface(
        tmp_path,
        node_path=NODE_PATH,
        name="public_result",
        kind="definition",
        summary="Expose the public result.",
        actor="coordinator",
    ).ok
    assert runtime.node.interface.bind_interface_to_decl(
        tmp_path,
        node_path=NODE_PATH,
        interface_name="public_result",
        decl_name="PublicResult",
    ).ok

    revised = runtime.node.public_statement_closure.revise_content_decl_visibility(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="PublicResult",
        expected_current_visibility="public",
        new_visibility="private",
        reason="Incorrectly classify an interface output as private.",
    )

    assert not revised.ok
    assert "decl_visibility_interface_required" in {issue.kind for issue in revised.issues}


def test_visibility_revision_rejects_scope_and_main_exports(
    tmp_path: Path,
) -> None:
    round_id = _prepare_repo(tmp_path)
    _seed_definition(tmp_path, round_id=round_id, name="PublicResult", public=True)
    _commit_content_head(tmp_path, "PublicResult")
    runtime = make_runtime()
    assert runtime.node.export.add_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        decl_node=NODE_PATH,
        decl_name="PublicResult",
    ).ok
    committed_topic = runtime.node.commit_scope_contract(
        tmp_path,
        scope_path="Main.Topic",
        summary="Commit the child boundary before testing Main visibility.",
    )
    assert committed_topic.ok, committed_topic.issues

    scope_revised = runtime.node.public_statement_closure.revise_content_decl_visibility(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="PublicResult",
        expected_current_visibility="public",
        new_visibility="private",
        reason="Attempt to bypass the Scope export boundary.",
    )
    assert not scope_revised.ok
    assert "decl_visibility_scope_export_required" in {
        issue.kind for issue in scope_revised.issues
    }

    assert runtime.node.export.add_scope_export(
        tmp_path,
        scope_path="Main",
        decl_node=NODE_PATH,
        decl_name="PublicResult",
    ).ok
    main_revised = runtime.node.public_statement_closure.revise_content_decl_visibility(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="PublicResult",
        expected_current_visibility="public",
        new_visibility="private",
        reason="Attempt to bypass the Main API.",
    )
    assert not main_revised.ok
    assert "decl_visibility_main_api_required" in {
        issue.kind for issue in main_revised.issues
    }


def test_visibility_revision_rejects_public_statement_consumer(
    tmp_path: Path,
) -> None:
    round_id = _prepare_repo(tmp_path)
    _seed_definition(tmp_path, round_id=round_id, name="Support", public=True)
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="PublicResult",
        public=True,
        statement_deps=["Support"],
    )
    runtime = make_runtime()

    revised = runtime.node.public_statement_closure.revise_content_decl_visibility(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="Support",
        expected_current_visibility="public",
        new_visibility="private",
        reason="Attempt to hide a public Statement prerequisite.",
    )

    assert not revised.ok
    assert "decl_visibility_public_statement_required" in {
        issue.kind for issue in revised.issues
    }


def test_visibility_revision_rejects_release_protected_decl(
    tmp_path: Path,
    monkeypatch,
) -> None:
    round_id = _prepare_repo(tmp_path)
    _seed_definition(tmp_path, round_id=round_id, name="ReleasedResult", public=True)
    runtime = make_runtime()
    monkeypatch.setattr(
        runtime.repo_workspace.release,
        "get_decl_release_status",
        lambda *_args, **_kwargs: runtime.foundation.ok(
            type("ReleaseStatus", (), {"release_protected": True})()
        ),
    )

    revised = runtime.node.public_statement_closure.revise_content_decl_visibility(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="ReleasedResult",
        expected_current_visibility="public",
        new_visibility="private",
        reason="Attempt to rewrite a stable Release boundary.",
    )

    assert not revised.ok
    assert "decl_visibility_release_protected" in {
        issue.kind for issue in revised.issues
    }


def test_visibility_revision_rolls_back_incomplete_promotion(
    tmp_path: Path,
) -> None:
    round_id = _prepare_repo(tmp_path)
    _seed_definition(tmp_path, round_id=round_id, name="PrivateSupport", public=False)
    _seed_definition(
        tmp_path,
        round_id=round_id,
        name="CandidateRoot",
        public=False,
        statement_deps=["PrivateSupport"],
    )
    runtime = make_runtime()

    revised = runtime.node.public_statement_closure.revise_content_decl_visibility(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="CandidateRoot",
        expected_current_visibility="private",
        new_visibility="public",
        reason="Try the single-declaration path instead of closure repair.",
    )

    assert not revised.ok
    assert "public_statement_decl_not_public" in {issue.kind for issue in revised.issues}
    restored = runtime.decl_graph.get_decl(
        tmp_path,
        node_path=NODE_PATH,
        name="CandidateRoot",
    )
    assert restored.ok and restored.value is not None
    assert restored.value.public is False


def test_visibility_demotion_rolls_back_on_projection_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    round_id = _prepare_repo(tmp_path)
    _seed_definition(tmp_path, round_id=round_id, name="Helper", public=True)
    runtime = make_runtime()

    def fail_refresh(*_args, **_kwargs):
        return ServiceResult(
            ok=False,
            issues=[
                runtime.foundation.issue(
                    "test_projection_failure",
                    "Injected projection failure.",
                )
            ],
        )

    monkeypatch.setattr(
        runtime.lean_projection.node_projection,
        "refresh_interfaces",
        fail_refresh,
    )
    revised = runtime.node.public_statement_closure.revise_content_decl_visibility(
        tmp_path,
        node_path=NODE_PATH,
        decl_name="Helper",
        expected_current_visibility="public",
        new_visibility="private",
        reason="Demote an internal helper.",
    )

    assert not revised.ok
    restored = runtime.decl_graph.get_decl(
        tmp_path,
        node_path=NODE_PATH,
        name="Helper",
    )
    assert restored.ok and restored.value is not None
    assert restored.value.public is True
