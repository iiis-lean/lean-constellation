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
