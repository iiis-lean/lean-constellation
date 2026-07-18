import json
from pathlib import Path

from tests.unit_services_helpers import initialize_native_test_repo, lean_check_payload, make_runtime

from lean_constellation.services.decl_graph import DeclChangeKind, DeclState
from lean_constellation.services.decl_graph.models import DeclRevision
from lean_constellation.services.foundation import WriteMode


def _create_content_node(tmp_path: Path, *, node_path: str = "Main.Topic.Core") -> None:
    initialize_native_test_repo(tmp_path)
    runtime = make_runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(
        tmp_path,
        path="Main.Topic",
        goal="Topic goal",
        boundary="Topic boundary",
    ).ok
    assert runtime.node.create_content_node(
        tmp_path,
        path=node_path,
        goal="Core goal",
        boundary="Core boundary",
        objective="Build the core declarations.",
        success_criteria="The core declarations are ready.",
    ).ok


def _create_round(tmp_path: Path, *, objective: str = "Plan a round.") -> tuple[str, str]:
    service = make_runtime().decl_graph
    strategy = service.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = service.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective=objective,
    )
    assert round_record.ok and round_record.value is not None
    return strategy.value.strategy_id, round_record.value.round_id


def _write_revision(tmp_path: Path, *, decl_name: str, revision: DeclRevision) -> None:
    runtime = make_runtime()
    path = runtime.decl_graph.graph_store.revision_path(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name=decl_name,
        revision=revision.revision,
    )
    assert runtime.foundation.store.write_json_atomic(path, revision, mode=WriteMode.UPDATE_EXISTING).ok


def _seed_committed_decl(
    tmp_path: Path,
    *,
    round_id: str,
    name: str,
    deps: list[str] | None = None,
    state: DeclState = DeclState.PROVED,
) -> None:
    service = make_runtime().decl_graph
    created = service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        name=name,
        kind="theorem",
        objective=f"Create {name}.",
        summary=f"{name} summary.",
        target_state=DeclState.PROVED if state == DeclState.PROVED else DeclState.DECLARED,
    )
    assert created.ok
    revision = service.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name=name, revision=1)
    assert revision.ok and revision.value is not None
    revision.value.state = state
    revision.value.statement_deps = []
    revision.value.proof_deps = deps or []
    _write_revision(tmp_path, decl_name=name, revision=revision.value)
    assert service.commit_decl_revision(tmp_path, node_path="Main.Topic.Core", name=name, state=state).ok


def test_create_decl_records_decl_revision_change_and_index(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    service = make_runtime().decl_graph

    change = service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        name="main_result",
        kind="theorem",
        objective="Create the main theorem declaration.",
        summary="The main theorem.",
        public=True,
        target_state=DeclState.PROVED,
    )

    assert change.ok and change.value is not None
    assert change.value.kind == DeclChangeKind.CREATE
    assert change.value.target_state == DeclState.PROVED
    assert change.value.require_target_state_satisfied is True
    assert change.value.target_revision == 1

    decl = service.get_decl(tmp_path, node_path="Main.Topic.Core", name="main_result")
    assert decl.ok and decl.value is not None
    assert decl.value.public is True
    assert decl.value.revision_ids == [1]

    revision = service.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name="main_result", revision=1)
    assert revision.ok and revision.value is not None
    assert revision.value.state == DeclState.PLANNED
    assert revision.value.status == "open"
    assert revision.value.change is not None
    assert revision.value.change.require_target_state_satisfied is True

    round_record = service.get_round(tmp_path, node_path="Main.Topic.Core", round_id=round_id)
    assert round_record.ok and round_record.value is not None
    assert round_record.value.change_ids == [change.value.change_id]
    raw_round = json.loads(
        service.graph_store.round_path(tmp_path, node_path="Main.Topic.Core", round_id=round_id).read_text(encoding="utf-8")
    )
    assert raw_round["revision_refs"] == [{"change_id": change.value.change_id, "decl_name": "main_result", "revision": 1}]
    assert "change_ids" not in raw_round
    raw_revision = json.loads(
        service.graph_store.revision_path(
            tmp_path,
            node_path="Main.Topic.Core",
            decl_name="main_result",
            revision=1,
        ).read_text(encoding="utf-8")
    )
    assert raw_revision["change"]["kind"] == "create"
    assert not (service.graph_store.graph_root(tmp_path, node_path="Main.Topic.Core") / "changes").exists()
    assert not (service.graph_store.graph_root(tmp_path, node_path="Main.Topic.Core") / "reviews").exists()

    index = service.get_decl_graph_index(tmp_path, node_path="Main.Topic.Core")
    assert index.ok and index.value is not None
    assert index.value.decl_names == ["main_result"]


def test_create_decl_revision_view_records_relaxed_satisfaction_target(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    service = make_runtime().decl_graph

    view = service.create_decl_revision_view(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        name="main_result",
        kind="theorem",
        objective="Create a top-down theorem shell.",
        summary="The proof may depend on helper lemmas planned later.",
        target_state=DeclState.PROVED,
        require_target_state_satisfied=False,
    )

    assert view.ok and view.value is not None
    assert view.value.require_target_state_satisfied is False
    revision = service.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name="main_result", revision=1)
    assert revision.ok and revision.value is not None
    assert revision.value.change is not None
    assert revision.value.change.require_target_state_satisfied is False


def test_duplicate_create_decl_fails(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    service = make_runtime().decl_graph
    assert service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        name="main_result",
        kind="theorem",
        objective="Create it.",
        summary="Summary.",
    ).ok

    duplicate = service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        name="main_result",
        kind="theorem",
        objective="Create it again.",
        summary="Summary.",
    )

    assert not duplicate.ok
    assert duplicate.issues[0].kind == "duplicate_decl"


def test_open_decl_update_copies_committed_revision_and_resets_stage_fields(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    _seed_committed_decl(tmp_path, round_id=round_id, name="main_result", deps=["supporting_lemma"])
    service = make_runtime().decl_graph
    revision = service.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name="main_result", revision=1)
    assert revision.ok and revision.value is not None
    revision.value.statement_nl = "A formal statement."
    revision.value.statement_deps = ["supporting_lemma"]
    revision.value.statement_lean_code = "theorem main_result : True := by trivial"
    revision.value.statement_lean_check = lean_check_payload()
    revision.value.proof_nl = "By triviality."
    revision.value.proof_deps = ["supporting_lemma"]
    revision.value.proof_lean_code = "by trivial"
    revision.value.proof_lean_check = lean_check_payload()
    _write_revision(tmp_path, decl_name="main_result", revision=revision.value)

    _, update_round_id = _create_round(tmp_path, objective="Update only the proof.")
    update = service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=update_round_id,
        name="main_result",
        objective="Redo the proof.",
        reset_to_state=DeclState.DECLARED,
        target_state=DeclState.PROVED,
    )

    assert update.ok and update.value is not None
    assert update.value.kind == DeclChangeKind.UPDATE
    assert update.value.base_revision == 1
    assert update.value.reset_to_state == DeclState.DECLARED
    assert update.value.target_revision == 2

    opened = service.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name="main_result", revision=2)
    assert opened.ok and opened.value is not None
    assert opened.value.state == DeclState.DECLARED
    assert opened.value.statement_nl == "A formal statement."
    assert opened.value.statement_lean_code == "theorem main_result : True := by trivial"
    assert opened.value.proof_nl is None
    assert opened.value.proof_lean_code is None
    assert opened.value.statement_deps == ["supporting_lemma"]
    assert opened.value.proof_deps == []
    assert opened.value.change is not None
    assert opened.value.change.base_revision == 1


def test_open_decl_update_defaults_to_committed_declared_head_and_starts_proof_pipeline(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    _seed_committed_decl(tmp_path, round_id=round_id, name="main_result", state=DeclState.DECLARED)
    service = make_runtime().decl_graph
    _, update_round_id = _create_round(tmp_path, objective="Prove the accepted statement.")

    update = service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=update_round_id,
        name="main_result",
        objective="Prove it.",
        target_state=DeclState.PROVED,
    )

    assert update.ok, update.issues
    assert update.value is not None
    assert update.value.base_revision == 1
    assert update.value.reset_to_state == DeclState.DECLARED
    opened = service.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name="main_result", revision=2)
    assert opened.ok and opened.value is not None
    assert opened.value.state == DeclState.DECLARED


def test_open_decl_update_requires_explicit_redo_when_base_already_reaches_target(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    _seed_committed_decl(tmp_path, round_id=round_id, name="main_result")
    service = make_runtime().decl_graph
    _, update_round_id = _create_round(tmp_path, objective="Redo proved work.")

    update = service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=update_round_id,
        name="main_result",
        objective="Redo it.",
        target_state=DeclState.PROVED,
    )

    assert not update.ok
    assert update.issues[0].kind == "decl_update_reset_required"


def test_open_decl_update_can_branch_from_older_committed_base_with_monotonic_revision(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    _seed_committed_decl(tmp_path, round_id=round_id, name="main_result")
    service = make_runtime().decl_graph
    _, second_round_id = _create_round(tmp_path, objective="Create revision two.")
    second = service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=second_round_id,
        name="main_result",
        objective="Redo proof.",
        reset_to_state=DeclState.DECLARED,
        target_state=DeclState.PROVED,
    )
    assert second.ok and second.value is not None
    assert service.commit_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=2,
        state=DeclState.PROVED,
    ).ok
    _, third_round_id = _create_round(tmp_path, objective="Branch from revision one.")

    third = service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=third_round_id,
        name="main_result",
        objective="Try an alternate proof from the original revision.",
        base_revision=1,
        reset_to_state=DeclState.DECLARED,
        target_state=DeclState.PROVED,
    )

    assert third.ok, third.issues
    assert third.value is not None
    assert third.value.target_revision == 3
    assert third.value.base_revision == 1
    decl = service.get_decl(tmp_path, node_path="Main.Topic.Core", name="main_result")
    assert decl.ok and decl.value is not None
    assert decl.value.current_revision == 3
    assert decl.value.revision_ids == [1, 2, 3]
    revision_two = service.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name="main_result", revision=2)
    assert revision_two.ok and revision_two.value is not None
    assert revision_two.value.status == "committed"


def test_open_decl_update_rejects_reset_later_than_base_state(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    _seed_committed_decl(tmp_path, round_id=round_id, name="main_result", state=DeclState.DECLARED)
    service = make_runtime().decl_graph
    _, update_round_id = _create_round(tmp_path, objective="Invalid reset.")

    update = service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=update_round_id,
        name="main_result",
        objective="Skip proof planning.",
        reset_to_state=DeclState.PROOF_PLANNED,
        target_state=DeclState.PROVED,
    )

    assert not update.ok
    assert update.issues[0].kind == "decl_update_reset_above_base"


def test_open_decl_update_rejects_open_current_revision(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    service = make_runtime().decl_graph
    assert service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        name="main_result",
        kind="theorem",
        objective="Create it.",
        summary="Summary.",
    ).ok

    update = service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        name="main_result",
        objective="Update it.",
        target_state=DeclState.PROVED,
    )

    assert not update.ok
    assert update.issues[0].kind == "decl_revision_already_open"


def test_delete_closure_and_round_draft_validation(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    _seed_committed_decl(tmp_path, round_id=round_id, name="A")
    _seed_committed_decl(tmp_path, round_id=round_id, name="B", deps=["A"])
    _seed_committed_decl(tmp_path, round_id=round_id, name="C", deps=["B"])
    service = make_runtime().decl_graph

    closure = service.compute_delete_closure(tmp_path, node_path="Main.Topic.Core", decl_names=["A"])
    assert closure.ok and closure.value is not None
    assert closure.value.closure_decl_names == ["A", "B", "C"]

    _, delete_round_id = _create_round(tmp_path, objective="Delete part of the chain.")
    blocked = service.mark_decl_delete(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=delete_round_id,
        name="A",
        objective="Delete A.",
    )
    assert not blocked.ok
    assert blocked.issues[0].kind == "decl_delete_current_inbound_refs"


def test_round_draft_validation_rejects_internal_update_dependency(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    _seed_committed_decl(tmp_path, round_id=round_id, name="A")
    _seed_committed_decl(tmp_path, round_id=round_id, name="B", deps=["A"])
    service = make_runtime().decl_graph

    _, update_round_id = _create_round(tmp_path, objective="Update dependent declarations together.")
    assert service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=update_round_id,
        name="A",
        objective="Update A.",
        reset_to_state=DeclState.PROOF_PLANNED,
        target_state=DeclState.PROVED,
    ).ok
    assert service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=update_round_id,
        name="B",
        objective="Update B.",
        reset_to_state=DeclState.PROOF_PLANNED,
        target_state=DeclState.PROVED,
    ).ok

    gate = service.validate_round_draft(tmp_path, node_path="Main.Topic.Core", round_id=update_round_id)

    assert gate.ok and gate.value is not None
    assert gate.value.passed is False
    assert any(issue.kind == "round_internal_dependency" for issue in gate.value.issues)


def test_decl_planning_rejects_non_draft_round(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    service = make_runtime().decl_graph
    assert service.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_id).ok

    result = service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        name="late_decl",
        kind="theorem",
        objective="Create too late.",
        summary="Too late.",
    )

    assert not result.ok
    assert result.issues[0].kind == "round_not_draft"
