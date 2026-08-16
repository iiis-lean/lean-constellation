import json
from pathlib import Path

from tests.unit_services_helpers import initialize_native_test_repo, lean_check_payload, make_runtime

from lean_constellation.services.decl_graph import DeclChangeKind, DeclState
from lean_constellation.services.decl_graph.models import (
    DeclFormalSection,
    DeclNaturalLanguageSection,
    DeclProof,
    DeclRevision,
    DeclRevisionStatus,
    MathlibDeclDep,
    RepoDeclDep,
)
from lean_constellation.domain.refs import DeclRef, MathlibRef
from lean_constellation.domain.lean_check import LeanCheck
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
    for prior in service.list_rounds(tmp_path, node_path="Main.Topic.Core").value or []:
        if prior.status.value == "committed":
            continue
        revisions = service.list_round_revisions(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=prior.round_id,
        )
        if revisions.ok and revisions.value and all(
            revision.status.value == "committed"
            for _, revision in revisions.value or []
        ):
            persisted = service.strategy_round.persist_round_closeout(
                tmp_path,
                node_path="Main.Topic.Core",
                round_id=prior.round_id,
                result_kind="success",
                reason=None,
                acknowledged_by="test-fixture",
            )
            assert persisted.ok, persisted.issues
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
    kind: str = "theorem",
    deps: list[str] | None = None,
    statement_deps: list[RepoDeclDep | MathlibDeclDep] | None = None,
    proof_deps: list[RepoDeclDep | MathlibDeclDep] | None = None,
    public: bool = False,
    state: DeclState = DeclState.PROVED,
) -> None:
    service = make_runtime().decl_graph
    created = service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        name=name,
        kind=kind,
        objective=f"Create {name}.",
        summary=f"{name} summary.",
        public=public,
        target_state=DeclState.PROVED if state == DeclState.PROVED else DeclState.DECLARED,
    )
    assert created.ok
    revision = service.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name=name, revision=1)
    assert revision.ok and revision.value is not None
    revision.value.state = state
    revision.value.statement.deps = statement_deps or []
    if revision.value.proof is None:
        revision.value.proof = DeclProof()
    revision.value.proof.deps = proof_deps or [
        RepoDeclDep(ref=DeclRef(node="Main.Topic.Core", name=dep, revision=1)) for dep in deps or []
    ]
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


def test_discard_round_draft_rolls_back_created_decls_and_allows_replanning(
    tmp_path: Path,
) -> None:
    _create_content_node(tmp_path)
    strategy_id, round_id = _create_round(tmp_path)
    service = make_runtime().decl_graph
    provider = service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        name="provider",
        kind="definition",
        objective="Create the provider first.",
        summary="Provider.",
        target_state=DeclState.DECLARED,
    )
    consumer = service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        name="consumer",
        kind="theorem",
        objective="Use provider only after its round commits.",
        summary="Consumer.",
        target_state=DeclState.PROVED,
    )
    assert provider.ok and consumer.ok
    assert provider.value is not None
    assert service.add_statement_dep(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="consumer",
        dep=RepoDeclDep(
            ref=DeclRef(
                node="Main.Topic.Core",
                name="provider",
                    revision=provider.value.target_revision,
            )
        ),
        refresh_projection=False,
        allow_draft=True,
    ).ok
    gate = service.validate_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
    )
    assert gate.ok and gate.value is not None and not gate.value.passed
    assert {issue.kind for issue in gate.value.issues} == {
        "round_internal_dependency"
    }

    discarded = service.discard_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        discarded_by="test-content-plan",
    )

    assert discarded.ok and discarded.value is not None
    assert discarded.value.changed is True
    assert discarded.value.deleted_created_decl_names == ["consumer", "provider"]
    round_record = service.get_round(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
    )
    assert round_record.ok and round_record.value is not None
    assert round_record.value.status == "discarded"
    assert round_record.value.revision_refs == []
    assert round_record.value.discarded_by == "test-content-plan"
    assert round_record.value.discarded_at is not None
    assert not service.get_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        name="provider",
    ).ok
    assert not service.get_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        name="consumer",
    ).ok
    index = service.get_decl_graph_index(
        tmp_path,
        node_path="Main.Topic.Core",
    )
    assert index.ok and index.value is not None
    assert index.value.decl_names == []
    assert round_id in index.value.round_ids

    replay = service.discard_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        discarded_by="test-content-plan",
    )
    assert replay.ok and replay.value is not None and replay.value.changed is False
    replacement = service.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy_id,
        objective="Create only the provider.",
    )
    assert replacement.ok and replacement.value is not None
    assert replacement.value.round_index == 2


def test_discard_round_draft_restores_update_head(
    tmp_path: Path,
) -> None:
    _create_content_node(tmp_path)
    _, seed_round_id = _create_round(tmp_path)
    _seed_committed_decl(
        tmp_path,
        round_id=seed_round_id,
        name="updated_decl",
    )
    _, round_id = _create_round(tmp_path)
    service = make_runtime().decl_graph
    updated = service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        name="updated_decl",
        objective="Redo the proof.",
        start_stage="proof_nl",
        target_state=DeclState.PROVED,
    )
    assert updated.ok

    discarded = service.discard_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        discarded_by="test-content-plan",
    )

    assert discarded.ok and discarded.value is not None
    assert discarded.value.restored_decl_revisions == {
        "updated_decl": 1,
    }
    for name in ("updated_decl",):
        decl = service.get_decl(
            tmp_path,
            node_path="Main.Topic.Core",
            name=name,
        )
        assert decl.ok and decl.value is not None
        assert decl.value.current_revision == 1
        assert decl.value.revision_ids == [1]
        revision = service.get_decl_revision(
            tmp_path,
            node_path="Main.Topic.Core",
            name=name,
            revision=1,
        )
        assert revision.ok and revision.value is not None
        assert revision.value.status == "committed"
        assert not service.graph_store.revision_path(
            tmp_path,
            node_path="Main.Topic.Core",
            decl_name=name,
            revision=2,
        ).exists()


def test_create_decl_records_relaxed_satisfaction_target(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    service = make_runtime().decl_graph

    view = service.create_decl(
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
    dep = RepoDeclDep(ref=DeclRef(node="Main.Topic.Core", name="supporting_lemma", revision=1))
    revision.value.statement.nl = DeclNaturalLanguageSection(text="A formal statement.")
    revision.value.statement.deps = [dep]
    revision.value.statement.formal = DeclFormalSection(
        code="theorem main_result : True := by trivial",
        check=LeanCheck.model_validate(lean_check_payload()),
    )
    revision.value.proof = DeclProof(
        nl=DeclNaturalLanguageSection(text="By triviality."),
        deps=[dep],
        formal=DeclFormalSection(
            code="by trivial",
            check=LeanCheck.model_validate(lean_check_payload()),
        ),
    )
    _write_revision(tmp_path, decl_name="main_result", revision=revision.value)

    _, update_round_id = _create_round(tmp_path, objective="Update only the proof.")
    update = service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=update_round_id,
        name="main_result",
        objective="Redo the proof.",
        start_stage="proof_nl",
        target_state=DeclState.PROVED,
    )

    assert update.ok and update.value is not None
    assert update.value.kind == DeclChangeKind.UPDATE
    assert update.value.base_revision == 1
    assert update.value.start_stage.value == "proof_nl"
    assert update.value.target_revision == 2

    opened = service.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name="main_result", revision=2)
    assert opened.ok and opened.value is not None
    assert opened.value.state == DeclState.DECLARED
    assert opened.value.statement.nl.text == "A formal statement."
    assert opened.value.statement.formal.code == "theorem main_result : True := by trivial"
    assert opened.value.proof is None or opened.value.proof.nl is None
    assert opened.value.proof is None or opened.value.proof.formal is None
    assert [item.ref.name for item in opened.value.statement.deps] == ["supporting_lemma"]
    assert opened.value.proof is None or opened.value.proof.deps == []
    assert opened.value.change is not None
    assert opened.value.change.base_revision == 1


def test_restore_decl_revision_creates_monotonic_committed_copy_with_lineage(
    tmp_path: Path,
) -> None:
    _create_content_node(tmp_path)
    _, create_round_id = _create_round(tmp_path)
    service = make_runtime().decl_graph
    created = service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=create_round_id,
        name="main_result",
        kind="theorem",
        objective="Create the theorem.",
        summary="Main result.",
        target_state=DeclState.DECLARED,
    )
    assert created.ok
    first = service.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=1,
    )
    assert first.ok and first.value is not None
    first.value.state = DeclState.SPECIFIED
    first.value.statement.nl = DeclNaturalLanguageSection(text="Historical statement.")
    _write_revision(tmp_path, decl_name="main_result", revision=first.value)
    assert service.commit_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=1,
        state=DeclState.SPECIFIED,
    ).ok

    _, update_round_id = _create_round(tmp_path, objective="Replace the statement.")
    update = service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=update_round_id,
        name="main_result",
        objective="Rewrite the statement.",
        start_stage="statement_nl",
        target_state=DeclState.DECLARED,
    )
    assert update.ok
    second = service.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=2,
    )
    assert second.ok and second.value is not None
    second.value.state = DeclState.SPECIFIED
    second.value.statement.nl = DeclNaturalLanguageSection(text="Replacement statement.")
    _write_revision(tmp_path, decl_name="main_result", revision=second.value)
    assert service.commit_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=2,
        state=DeclState.SPECIFIED,
    ).ok
    _create_round(tmp_path, objective="Close the previous round.")

    restored = service.restore_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
        source_revision=1,
    )

    assert restored.ok, restored.issues
    assert restored.value is not None
    assert restored.value.source_revision == 1
    assert restored.value.restored_revision == 3
    decl = service.get_decl(tmp_path, node_path="Main.Topic.Core", name="main_result")
    assert decl.ok and decl.value is not None
    assert decl.value.current_revision == 3
    assert decl.value.revision_ids == [1, 2, 3]
    revision = service.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=3,
    )
    assert revision.ok and revision.value is not None
    assert revision.value.status.value == "committed"
    assert revision.value.restored_from_revision == 1
    assert revision.value.change is None
    assert revision.value.statement.nl is not None
    assert revision.value.statement.nl.text == "Historical statement."


def test_restore_decl_revision_rejects_open_current_head(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    _seed_committed_decl(
        tmp_path,
        round_id=round_id,
        name="main_result",
        state=DeclState.DECLARED,
    )
    service = make_runtime().decl_graph
    _, update_round_id = _create_round(tmp_path, objective="Open an update.")
    assert service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=update_round_id,
        name="main_result",
        objective="Update it.",
        start_stage="proof_nl",
        target_state=DeclState.PROVED,
    ).ok

    restored = service.restore_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
        source_revision=1,
    )

    assert not restored.ok
    assert restored.issues[0].kind == "decl_restore_open_revision"


def test_restore_public_decl_rejects_historical_state_below_declared_without_mutation(
    tmp_path: Path,
) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    _seed_committed_decl(
        tmp_path,
        round_id=round_id,
        name="main_result",
        public=True,
        state=DeclState.PROVED,
    )
    _create_round(tmp_path, objective="Close the seed round.")
    runtime = make_runtime()
    service = runtime.decl_graph
    decl = service.get_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
    )
    first = service.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=1,
    )
    assert decl.ok and decl.value is not None
    assert first.ok and first.value is not None
    historical = first.value.model_copy(deep=True)
    historical.revision = 2
    historical.state = DeclState.SPECIFIED
    historical.status = DeclRevisionStatus.COMMITTED
    historical.change = None
    current = first.value.model_copy(deep=True)
    current.revision = 3
    current.status = DeclRevisionStatus.COMMITTED
    current.change = None
    for revision in (historical, current):
        assert runtime.foundation.store.write_json_atomic(
            service.graph_store.revision_path(
                tmp_path,
                node_path="Main.Topic.Core",
                decl_name="main_result",
                revision=revision.revision,
            ),
            revision,
            mode=WriteMode.CREATE_ONLY,
        ).ok
    decl.value.current_revision = 3
    decl.value.revision_ids = [1, 2, 3]
    decl_path = service.graph_store.decl_record_path(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    )
    assert runtime.foundation.store.write_json_atomic(
        decl_path,
        decl.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    before_decl = decl_path.read_bytes()

    restored = service.restore_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
        source_revision=2,
    )

    assert not restored.ok
    assert restored.issues[0].kind == "decl_restore_public_state_too_low"
    assert decl_path.read_bytes() == before_decl
    assert not service.graph_store.revision_path(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
        revision=4,
    ).exists()


def test_open_decl_update_rebinds_same_node_dependencies_to_provider_head(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    _seed_committed_decl(tmp_path, round_id=round_id, name="supporting_lemma")
    _seed_committed_decl(tmp_path, round_id=round_id, name="main_result", deps=["supporting_lemma"])
    service = make_runtime().decl_graph
    consumer = service.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=1,
    )
    assert consumer.ok and consumer.value is not None
    consumer.value.statement.deps = [
        RepoDeclDep(ref=DeclRef(node="Main.Topic.Core", name="supporting_lemma", revision=1))
    ]
    _write_revision(tmp_path, decl_name="main_result", revision=consumer.value)

    _, provider_round_id = _create_round(tmp_path, objective="Advance the provider.")
    provider_update = service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=provider_round_id,
        name="supporting_lemma",
        objective="Create the provider's next revision.",
        start_stage="proof_nl",
        target_state=DeclState.PROVED,
    )
    assert provider_update.ok, provider_update.issues
    assert service.commit_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="supporting_lemma",
        revision=2,
        state=DeclState.PROVED,
    ).ok

    _, consumer_round_id = _create_round(tmp_path, objective="Reopen the consumer.")
    consumer_update = service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=consumer_round_id,
        name="main_result",
        objective="Reuse the statement against the current provider.",
        start_stage="proof_nl",
        target_state=DeclState.PROVED,
    )

    assert consumer_update.ok, consumer_update.issues
    opened = service.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=2,
    )
    assert opened.ok and opened.value is not None
    assert [dep.ref.revision for dep in opened.value.statement.deps if isinstance(dep, RepoDeclDep)] == [2]


def test_open_decl_update_uses_latest_committed_declared_head_and_starts_proof_pipeline(tmp_path: Path) -> None:
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
        start_stage="proof_nl",
        target_state=DeclState.PROVED,
    )

    assert update.ok, update.issues
    assert update.value is not None
    assert update.value.base_revision == 1
    assert update.value.start_stage.value == "proof_nl"
    opened = service.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name="main_result", revision=2)
    assert opened.ok and opened.value is not None
    assert opened.value.state == DeclState.DECLARED


def test_open_decl_update_can_redo_proved_head_from_explicit_start_stage(tmp_path: Path) -> None:
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
        start_stage="proof_nl",
        target_state=DeclState.PROVED,
    )

    assert update.ok, update.issues
    assert update.value is not None
    assert update.value.base_revision == 1
    assert update.value.start_stage.value == "proof_nl"


def test_open_decl_update_always_copies_latest_committed_head_with_monotonic_revision(tmp_path: Path) -> None:
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
        start_stage="proof_nl",
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
        start_stage="proof_nl",
        target_state=DeclState.PROVED,
    )

    assert third.ok, third.issues
    assert third.value is not None
    assert third.value.target_revision == 3
    assert third.value.base_revision == 2
    decl = service.get_decl(tmp_path, node_path="Main.Topic.Core", name="main_result")
    assert decl.ok and decl.value is not None
    assert decl.value.current_revision == 3
    assert decl.value.revision_ids == [1, 2, 3]
    revision_two = service.get_decl_revision(tmp_path, node_path="Main.Topic.Core", name="main_result", revision=2)
    assert revision_two.ok and revision_two.value is not None
    assert revision_two.value.status == "committed"


def test_open_decl_update_rejects_start_stage_later_than_source_state(tmp_path: Path) -> None:
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
        start_stage="proof_formal",
        target_state=DeclState.PROVED,
    )

    assert not update.ok
    assert update.issues[0].kind == "decl_update_start_stage_above_source"


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
        start_stage="statement_nl",
        target_state=DeclState.PROVED,
    )

    assert not update.ok
    assert update.issues[0].kind == "decl_revision_already_open"


def test_delete_decls_requires_and_deletes_exact_current_closure(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    _seed_committed_decl(tmp_path, round_id=round_id, name="A")
    _seed_committed_decl(tmp_path, round_id=round_id, name="B", deps=["A"])
    _seed_committed_decl(tmp_path, round_id=round_id, name="C", deps=["B"])
    service = make_runtime().decl_graph

    closure = service.compute_delete_closure(tmp_path, node_path="Main.Topic.Core", decl_names=["A"])
    assert closure.ok and closure.value is not None
    assert closure.value.closure_decl_names == ["A", "B", "C"]

    _create_round(tmp_path, objective="Close the seed round.")
    blocked = service.delete_decls(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_names=["A"],
    )
    assert not blocked.ok
    assert blocked.issues[0].kind == "decl_delete_closure_mismatch"
    assert blocked.issues[0].expected == "A, B, C"

    deleted = service.delete_decls(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_names=["C", "A", "B"],
    )

    assert deleted.ok, deleted.issues
    assert deleted.value is not None
    assert deleted.value.deleted_decl_names == ["A", "B", "C"]
    for decl_name in deleted.value.deleted_decl_names:
        decl = service.get_decl(
            tmp_path,
            node_path="Main.Topic.Core",
            name=decl_name,
        )
        assert decl.ok and decl.value is not None
        assert decl.value.lifecycle.value == "deleted"
    index = service.get_decl_graph_index(tmp_path, node_path="Main.Topic.Core")
    assert index.ok and index.value is not None
    assert index.value.decl_names == ["A", "B", "C"]


def test_delete_closure_uses_only_exact_same_node_repo_dependencies(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    _seed_committed_decl(tmp_path, round_id=round_id, name="A")
    _seed_committed_decl(
        tmp_path,
        round_id=round_id,
        name="LocalProofConsumer",
        proof_deps=[RepoDeclDep(ref=DeclRef(node="Main.Topic.Core", name="A", revision=1))],
    )
    _seed_committed_decl(
        tmp_path,
        round_id=round_id,
        name="LocalStatementConsumer",
        statement_deps=[RepoDeclDep(ref=DeclRef(node="Main.Topic.Core", name="A", revision=1))],
    )
    _seed_committed_decl(
        tmp_path,
        round_id=round_id,
        name="ExternalConsumer",
        proof_deps=[
            RepoDeclDep(ref=DeclRef(repo="ExternalRepo", node="Main.Topic.Core", name="A", revision=1))
        ],
    )
    _seed_committed_decl(
        tmp_path,
        round_id=round_id,
        name="OtherNodeConsumer",
        proof_deps=[RepoDeclDep(ref=DeclRef(node="Main.Other", name="A", revision=1))],
    )
    _seed_committed_decl(
        tmp_path,
        round_id=round_id,
        name="MathlibConsumer",
        proof_deps=[MathlibDeclDep(ref=MathlibRef(name="A", module="Mathlib.Test"))],
    )
    service = make_runtime().decl_graph

    closure = service.compute_delete_closure(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_names=["A", "A"],
    )

    assert closure.ok and closure.value is not None
    assert closure.value.requested_decl_names == ["A"]
    assert closure.value.closure_decl_names == ["A", "LocalProofConsumer", "LocalStatementConsumer"]


def test_delete_decls_rejects_public_declaration_before_mutation(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    _seed_committed_decl(tmp_path, round_id=round_id, name="PublicA", public=True)
    _create_round(tmp_path, objective="Close the seed round.")
    service = make_runtime().decl_graph
    decl_path = service.graph_store.decl_record_path(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="PublicA",
    )
    revision_path = service.graph_store.revision_path(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="PublicA",
        revision=1,
    )
    before_decl = decl_path.read_bytes()
    before_revision = revision_path.read_bytes()

    preview = service.compute_delete_closure(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_names=["PublicA"],
    )
    deleted = service.delete_decls(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_names=["PublicA"],
    )

    assert preview.ok and preview.value is not None
    assert preview.value.public_decl_names == ["PublicA"]
    assert not deleted.ok
    assert deleted.issues[0].kind == "decl_delete_public_requires_demotion"
    assert decl_path.read_bytes() == before_decl
    assert revision_path.read_bytes() == before_revision


def test_delete_decls_rejects_public_member_of_downstream_closure(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    _seed_committed_decl(tmp_path, round_id=round_id, name="PrivateRoot")
    _seed_committed_decl(
        tmp_path,
        round_id=round_id,
        name="PublicConsumer",
        deps=["PrivateRoot"],
        public=True,
    )
    _create_round(tmp_path, objective="Close the seed round.")
    service = make_runtime().decl_graph

    preview = service.compute_delete_closure(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_names=["PrivateRoot"],
    )
    deleted = service.delete_decls(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_names=["PrivateRoot", "PublicConsumer"],
    )

    assert preview.ok and preview.value is not None
    assert preview.value.closure_decl_names == ["PrivateRoot", "PublicConsumer"]
    assert preview.value.public_decl_names == ["PublicConsumer"]
    assert not deleted.ok
    assert deleted.issues[0].kind == "decl_delete_public_requires_demotion"
    for decl_name in preview.value.closure_decl_names:
        decl = service.get_decl(
            tmp_path,
            node_path="Main.Topic.Core",
            name=decl_name,
        )
        assert decl.ok and decl.value is not None
        assert decl.value.lifecycle.value == "active"


def test_delete_decls_rejects_cross_repo_current_consumer(tmp_path: Path) -> None:
    provider = tmp_path / "Provider"
    consumer = tmp_path / "Consumer"
    _create_content_node(provider)
    _create_content_node(consumer)
    _, provider_round = _create_round(provider)
    _, consumer_round = _create_round(consumer)
    _seed_committed_decl(provider, round_id=provider_round, name="ProviderResult")
    _seed_committed_decl(consumer, round_id=consumer_round, name="ConsumerResult")
    _create_round(provider, objective="Close the provider seed round.")
    _create_round(consumer, objective="Close the consumer seed round.")
    runtime = make_runtime()
    revision = runtime.decl_graph.get_decl_revision(
        consumer,
        node_path="Main.Topic.Core",
        name="ConsumerResult",
        revision=1,
    )
    assert revision.ok and revision.value is not None
    assert revision.value.proof is not None
    revision.value.proof.deps = [
        RepoDeclDep(
            ref=DeclRef(
                repo="Provider",
                node="Main.Topic.Core",
                name="ProviderResult",
                revision=1,
            )
        )
    ]
    revision_path = runtime.decl_graph.graph_store.revision_path(
        consumer,
        node_path="Main.Topic.Core",
        decl_name="ConsumerResult",
        revision=1,
    )
    assert runtime.foundation.store.write_json_atomic(
        revision_path,
        revision.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok

    deleted = runtime.decl_graph.delete_decls(
        provider,
        node_path="Main.Topic.Core",
        decl_names=["ProviderResult"],
    )

    assert not deleted.ok
    assert deleted.issues[0].kind == "decl_delete_current_inbound_refs"
    assert "workspace:decl:Consumer" in (deleted.issues[0].current or "")


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
        start_stage="proof_formal",
        target_state=DeclState.PROVED,
    ).ok
    assert service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=update_round_id,
        name="B",
        objective="Update B.",
        start_stage="proof_formal",
        target_state=DeclState.PROVED,
    ).ok

    gate = service.validate_round_draft(tmp_path, node_path="Main.Topic.Core", round_id=update_round_id)

    assert gate.ok and gate.value is not None
    assert gate.value.passed is False
    assert any(issue.kind == "round_internal_dependency" for issue in gate.value.issues)


def test_round_draft_validation_accepts_declared_definition_proof_dependency(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    _seed_committed_decl(
        tmp_path,
        round_id=round_id,
        name="DefinitionProvider",
        kind="definition",
        state=DeclState.DECLARED,
    )
    _seed_committed_decl(
        tmp_path,
        round_id=round_id,
        name="ConsumerTheorem",
        deps=["DefinitionProvider"],
    )
    service = make_runtime().decl_graph

    _, update_round_id = _create_round(tmp_path, objective="Retry the consumer proof.")
    assert service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=update_round_id,
        name="ConsumerTheorem",
        objective="Retry the proof without rebuilding its definition dependency.",
        start_stage="proof_formal",
        target_state=DeclState.PROVED,
    ).ok

    gate = service.validate_round_draft(tmp_path, node_path="Main.Topic.Core", round_id=update_round_id)

    assert gate.ok and gate.value is not None
    assert gate.value.passed is True
    assert gate.value.issues == []


def test_round_draft_validation_still_requires_theorem_proof_dependency_proved(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    _, round_id = _create_round(tmp_path)
    _seed_committed_decl(
        tmp_path,
        round_id=round_id,
        name="TheoremProvider",
        state=DeclState.DECLARED,
    )
    _seed_committed_decl(
        tmp_path,
        round_id=round_id,
        name="ConsumerTheorem",
        deps=["TheoremProvider"],
    )
    service = make_runtime().decl_graph

    _, update_round_id = _create_round(tmp_path, objective="Retry the consumer proof.")
    assert service.open_decl_update(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=update_round_id,
        name="ConsumerTheorem",
        objective="Retry the proof while retaining its theorem dependency.",
        start_stage="proof_formal",
        target_state=DeclState.PROVED,
    ).ok

    gate = service.validate_round_draft(tmp_path, node_path="Main.Topic.Core", round_id=update_round_id)

    assert gate.ok and gate.value is not None
    assert gate.value.passed is False
    issue = next(issue for issue in gate.value.issues if issue.kind == "round_dependency_provider_not_ready")
    assert issue.object_ref == "ConsumerTheorem"
    assert issue.current == DeclState.DECLARED.value
    assert issue.expected == DeclState.PROVED.value


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
