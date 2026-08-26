import json
from pathlib import Path
from types import SimpleNamespace

from tests.unit_services_helpers import (
    initialize_native_test_repo,
    lean_check_payload,
    make_runtime,
    write_proof_formal_for_test,
    write_statement_formal_for_test,
)
from tests.unit.flows.decl_round._helpers import seed_committed_theorem

from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.decl_graph import DeclState, RepoDeclDep
from lean_constellation.services.decl_graph.models import (
    DeclNaturalLanguageSection,
    DeclOriginRef,
    DeclProof,
    DeclRevisionStatus,
)
from lean_constellation.services.foundation import WriteMode


_NODE_PATH = "Main.Topic.Core"


def _create_content_node(tmp_path: Path) -> None:
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
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build the core declarations.",
        success_criteria="The core declarations are ready.",
    ).ok


def _create_running_round_with_decl(tmp_path: Path, *, name: str = "main_result", kind: str = "theorem") -> str:
    runtime = make_runtime()
    service = runtime.decl_graph
    strategy = service.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = service.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round objective.",
    )
    assert round_record.ok and round_record.value is not None
    assert service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        name=name,
        kind=kind,
        objective=f"Create {name}.",
        summary=f"{name} summary.",
        target_state=DeclState.PROVED if kind == "theorem" else DeclState.DECLARED,
    ).ok
    assert service.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    return round_record.value.round_id


def _write_revision_for_test(runtime, repo_root: Path, *, decl_name: str, revision) -> Path:  # noqa: ANN001
    path = runtime.decl_graph.graph_store.revision_path(
        repo_root,
        node_path=_NODE_PATH,
        decl_name=decl_name,
        revision=revision.revision,
    )
    written = runtime.foundation.store.write_json_atomic(
        path,
        revision,
        mode=WriteMode.UPDATE_EXISTING,
    )
    assert written.ok, written.issues
    return path


def _history_prepare_fixture(
    tmp_path: Path,
    *,
    start_stage: str,
):
    _create_content_node(tmp_path)
    runtime = make_runtime()
    source_input = tmp_path / "source-input"
    source_input.mkdir(parents=True)
    (source_input / "README.md").write_text(
        "Source identity: Decl history prepare fixture.\n"
        "Source provenance: local unit-test fixture.\n"
        "License/access: local test fixture.\n"
        "Included scope: complete fixture. Excluded scope: none; omitted: none.\n"
        "File inventory: README.md and article.md.\n"
        "Reading order: README.md, then article.md.\n"
        "Main material: article.md contains the statement and proof context.\n"
        "Input-to-final mapping: fixture input maps to article.md.\n"
        "Known gaps and extraction limits: none.\n",
        encoding="utf-8",
    )
    (source_input / "article.md").write_text("context\nstatement\nproof\n", encoding="utf-8")
    imported = runtime.material.import_local_source_corpus(
        tmp_path,
        source_dir=source_input,
        entry_path="README.md",
        overview="Decl history prepare fixture.",
        preparation_summary="Prepared current SourceCorpus truth for history validation.",
    )
    assert imported.ok and imported.value is not None, imported.issues
    seed_committed_theorem(runtime, tmp_path, decl_name="supporting_result")
    seed_committed_theorem(runtime, tmp_path, decl_name="main_result")

    source = runtime.decl_graph.get_decl_revision(
        tmp_path,
        node_path=_NODE_PATH,
        name="main_result",
        revision=1,
    )
    assert source.ok and source.value is not None, source.issues
    source.value.statement.nl = DeclNaturalLanguageSection(
        text="Historical statement candidate.",
        origin=[
            DeclOriginRef(
                kind="source",
                source_path="article.md",
                start_line=1,
                end_line=2,
            )
        ],
    )
    source.value.statement.deps = [
        RepoDeclDep(
            ref=DeclRef(
                node=_NODE_PATH,
                name="supporting_result",
                revision=1,
            ),
            reason="Historical statement support.",
        )
    ]
    assert source.value.proof is not None
    source.value.proof.nl = DeclNaturalLanguageSection(
        text="Historical proof candidate.",
        origin=[
            DeclOriginRef(
                kind="source",
                source_path="article.md",
                start_line=2,
                end_line=3,
            )
        ],
    )
    source.value.proof.deps = [
        RepoDeclDep(
            ref=DeclRef(
                node=_NODE_PATH,
                name="supporting_result",
                revision=1,
            ),
            reason="Historical proof support.",
        )
    ]
    source_path = _write_revision_for_test(
        runtime,
        tmp_path,
        decl_name="main_result",
        revision=source.value,
    )
    source_bytes = source_path.read_bytes()

    provider = runtime.decl_graph.get_decl(
        tmp_path,
        node_path=_NODE_PATH,
        name="supporting_result",
    )
    provider_source = runtime.decl_graph.get_decl_revision(
        tmp_path,
        node_path=_NODE_PATH,
        name="supporting_result",
        revision=1,
    )
    assert provider.ok and provider.value is not None, provider.issues
    assert provider_source.ok and provider_source.value is not None, provider_source.issues
    provider_revision = provider_source.value.model_copy(deep=True)
    provider_revision.revision = 2
    provider_revision.status = DeclRevisionStatus.COMMITTED
    provider_revision.change = None
    provider_revision_path = runtime.decl_graph.graph_store.revision_path(
        tmp_path,
        node_path=_NODE_PATH,
        decl_name="supporting_result",
        revision=2,
    )
    assert runtime.foundation.store.write_json_atomic(
        provider_revision_path,
        provider_revision,
        mode=WriteMode.CREATE_ONLY,
    ).ok
    provider.value.current_revision = 2
    provider.value.revision_ids.append(2)
    provider_path = runtime.decl_graph.graph_store.decl_record_path(
        tmp_path,
        node_path=_NODE_PATH,
        decl_name="supporting_result",
    )
    assert runtime.foundation.store.write_json_atomic(
        provider_path,
        provider.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok

    strategy = runtime.decl_graph.ensure_open_strategy(
        tmp_path,
        node_path=_NODE_PATH,
        objective="Prepare a historical candidate.",
    )
    assert strategy.ok and strategy.value is not None, strategy.issues
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path=_NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Reuse a local historical candidate.",
    )
    assert round_record.ok and round_record.value is not None, round_record.issues
    opened = runtime.decl_graph.open_decl_update(
        tmp_path,
        node_path=_NODE_PATH,
        round_id=round_record.value.round_id,
        name="main_result",
        objective="Repair the current candidate from history.",
        start_stage=start_stage,
        target_state=DeclState.PROVED,
    )
    assert opened.ok and opened.value is not None, opened.issues
    started = runtime.decl_graph.start_round(
        tmp_path,
        node_path=_NODE_PATH,
        round_id=round_record.value.round_id,
    )
    assert started.ok, started.issues
    current_path = runtime.decl_graph.graph_store.revision_path(
        tmp_path,
        node_path=_NODE_PATH,
        decl_name="main_result",
        revision=opened.value.target_revision,
    )
    return runtime, round_record.value.round_id, source_path, source_bytes, current_path


def test_prepare_statement_nl_from_revision_copies_validated_bundle_and_rebinds_same_node_dep(
    tmp_path: Path,
) -> None:
    runtime, round_id, source_path, source_bytes, current_path = _history_prepare_fixture(
        tmp_path,
        start_stage="statement_nl",
    )

    prepared = runtime.decl_graph.prepare_statement_nl_from_revision(
        tmp_path,
        node_path=_NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        source_revision=1,
    )

    assert prepared.ok and prepared.value is not None, prepared.issues
    assert prepared.value.model_dump(mode="json", exclude_none=True) == {
        "decl_name": "main_result",
        "revision": 2,
        "source_revision": 1,
        "stage": "statement_nl",
        "copied_origin_count": 1,
        "copied_dependency_count": 1,
        "changed": True,
    }
    current = runtime.decl_graph.get_decl_revision(
        tmp_path,
        node_path=_NODE_PATH,
        name="main_result",
        revision=2,
    )
    assert current.ok and current.value is not None, current.issues
    assert current.value.state == DeclState.PLANNED
    assert current.value.statement.nl is not None
    assert current.value.statement.nl.text == "Historical statement candidate."
    assert current.value.statement.nl.origin[0].source_path == "article.md"
    assert current.value.statement.deps[0].ref.revision == 2
    assert current.value.statement.formal is None
    assert current.value.proof is None
    assert source_path.read_bytes() == source_bytes
    assert current_path.exists()


def test_prepare_proof_nl_from_revision_preserves_current_statement_truth(
    tmp_path: Path,
) -> None:
    runtime, round_id, source_path, source_bytes, _current_path = _history_prepare_fixture(
        tmp_path,
        start_stage="proof_nl",
    )
    before = runtime.decl_graph.get_decl_revision(
        tmp_path,
        node_path=_NODE_PATH,
        name="main_result",
        revision=2,
    )
    assert before.ok and before.value is not None, before.issues
    before.value.proof = DeclProof(nl=DeclNaturalLanguageSection())
    _write_revision_for_test(
        runtime,
        tmp_path,
        decl_name="main_result",
        revision=before.value,
    )
    statement_before = before.value.statement.model_dump(mode="json")

    prepared = runtime.decl_graph.prepare_proof_nl_from_revision(
        tmp_path,
        node_path=_NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        source_revision=1,
    )

    assert prepared.ok and prepared.value is not None, prepared.issues
    assert prepared.value.stage == "proof_nl"
    assert prepared.value.source_revision == 1
    assert prepared.value.copied_origin_count == 1
    assert prepared.value.copied_dependency_count == 1
    current = runtime.decl_graph.get_decl_revision(
        tmp_path,
        node_path=_NODE_PATH,
        name="main_result",
        revision=2,
    )
    assert current.ok and current.value is not None, current.issues
    assert current.value.state == DeclState.DECLARED
    assert current.value.statement.model_dump(mode="json") == statement_before
    assert current.value.proof is not None and current.value.proof.nl is not None
    assert current.value.proof.nl.text == "Historical proof candidate."
    assert current.value.proof.deps[0].ref.revision == 2
    assert current.value.proof.formal is None
    assert source_path.read_bytes() == source_bytes


def test_prepare_statement_nl_rejects_nonempty_current_candidate_without_writing(
    tmp_path: Path,
) -> None:
    runtime, round_id, _source_path, _source_bytes, current_path = _history_prepare_fixture(
        tmp_path,
        start_stage="statement_nl",
    )
    written = runtime.decl_graph.set_statement_nl(
        tmp_path,
        node_path=_NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="Worker has already started a replacement.",
    )
    assert written.ok, written.issues
    before = current_path.read_bytes()

    prepared = runtime.decl_graph.prepare_statement_nl_from_revision(
        tmp_path,
        node_path=_NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        source_revision=1,
    )

    assert not prepared.ok
    assert prepared.issues[0].kind == "statement_nl_prepare_target_not_empty"
    assert current_path.read_bytes() == before


def test_prepare_proof_nl_rejects_nonempty_current_candidate_without_writing(
    tmp_path: Path,
) -> None:
    runtime, round_id, _source_path, _source_bytes, current_path = _history_prepare_fixture(
        tmp_path,
        start_stage="proof_nl",
    )
    written = runtime.decl_graph.set_proof_nl(
        tmp_path,
        node_path=_NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="Worker has already started a replacement proof.",
    )
    assert written.ok, written.issues
    before = current_path.read_bytes()

    prepared = runtime.decl_graph.prepare_proof_nl_from_revision(
        tmp_path,
        node_path=_NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        source_revision=1,
    )

    assert not prepared.ok
    assert prepared.issues[0].kind == "proof_nl_prepare_target_not_empty"
    assert current_path.read_bytes() == before


def test_prepare_statement_nl_validation_failures_leave_current_revision_byte_identical(
    tmp_path: Path,
) -> None:
    cases = (
        ("open_source", "decl_prepare_source_not_committed"),
        ("missing_artifact", "statement_nl_prepare_source_missing"),
        ("stale_origin", "statement_origin_source_missing"),
        ("missing_dependency", "statement_dep_not_visible"),
    )
    for index, (case, expected_issue) in enumerate(cases):
        case_root = tmp_path / str(index)
        runtime, round_id, source_path, _source_bytes, current_path = _history_prepare_fixture(
            case_root,
            start_stage="statement_nl",
        )
        source = runtime.decl_graph.get_decl_revision(
            case_root,
            node_path=_NODE_PATH,
            name="main_result",
            revision=1,
        )
        assert source.ok and source.value is not None, source.issues
        if case == "open_source":
            source.value.status = DeclRevisionStatus.OPEN
        elif case == "missing_artifact":
            source.value.statement.nl = None
        elif case == "stale_origin":
            assert source.value.statement.nl is not None
            source.value.statement.nl.origin = [
                DeclOriginRef(
                    kind="source",
                    source_path="article.md",
                    start_line=99,
                    end_line=99,
                )
            ]
        else:
            source.value.statement.deps = [
                RepoDeclDep(
                    ref=DeclRef(
                        node=_NODE_PATH,
                        name="missing_result",
                        revision=1,
                    )
                )
            ]
        _write_revision_for_test(
            runtime,
            case_root,
            decl_name="main_result",
            revision=source.value,
        )
        source_before = source_path.read_bytes()
        current_before = current_path.read_bytes()

        prepared = runtime.decl_graph.prepare_statement_nl_from_revision(
            case_root,
            node_path=_NODE_PATH,
            round_id=round_id,
            decl_name="main_result",
            source_revision=1,
        )

        assert not prepared.ok
        assert expected_issue in {issue.kind for issue in prepared.issues}
        assert source_path.read_bytes() == source_before
        assert current_path.read_bytes() == current_before


def test_prepare_proof_nl_validation_failures_leave_current_revision_byte_identical(
    tmp_path: Path,
) -> None:
    cases = (
        ("stale_origin", "proof_origin_source_missing"),
        ("missing_dependency", "proof_dep_not_visible"),
    )
    for index, (case, expected_issue) in enumerate(cases):
        case_root = tmp_path / str(index)
        runtime, round_id, source_path, _source_bytes, current_path = _history_prepare_fixture(
            case_root,
            start_stage="proof_nl",
        )
        source = runtime.decl_graph.get_decl_revision(
            case_root,
            node_path=_NODE_PATH,
            name="main_result",
            revision=1,
        )
        assert source.ok and source.value is not None, source.issues
        assert source.value.proof is not None and source.value.proof.nl is not None
        if case == "stale_origin":
            source.value.proof.nl.origin = [
                DeclOriginRef(
                    kind="source",
                    source_path="article.md",
                    start_line=99,
                    end_line=99,
                )
            ]
        else:
            source.value.proof.deps = [
                RepoDeclDep(
                    ref=DeclRef(
                        node=_NODE_PATH,
                        name="missing_result",
                        revision=1,
                    )
                )
            ]
        _write_revision_for_test(
            runtime,
            case_root,
            decl_name="main_result",
            revision=source.value,
        )
        source_before = source_path.read_bytes()
        current_before = current_path.read_bytes()

        prepared = runtime.decl_graph.prepare_proof_nl_from_revision(
            case_root,
            node_path=_NODE_PATH,
            round_id=round_id,
            decl_name="main_result",
            source_revision=1,
        )

        assert not prepared.ok
        assert expected_issue in {issue.kind for issue in prepared.issues}
        assert source_path.read_bytes() == source_before
        assert current_path.read_bytes() == current_before


def test_prepare_public_dependency_failures_leave_current_revision_byte_identical(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cases = (
        (
            "statement_nl",
            None,
            "Main.Provider",
            False,
            True,
            False,
            "statement_dep_prepare_incompatible",
        ),
        (
            "proof_nl",
            "ProviderRepo",
            "Main",
            True,
            False,
            True,
            "proof_dep_prepare_not_ready",
        ),
        (
            "proof_nl",
            "ProviderRepo",
            "Main.Exact",
            True,
            True,
            False,
            "proof_dep_prepare_not_ready",
        ),
    )
    for index, (
        start_stage,
        repo,
        dep_node,
        compatible,
        ready,
        stale,
        expected_issue,
    ) in enumerate(cases):
        case_root = tmp_path / str(index)
        runtime, round_id, source_path, _source_bytes, current_path = _history_prepare_fixture(
            case_root,
            start_stage=start_stage,
        )
        resolver = runtime.decl_graph.ref_compatibility
        ref = DeclRef(repo=repo, node=dep_node, name="provider_result", revision=1)
        source = runtime.decl_graph.get_decl_revision(
            case_root,
            node_path=_NODE_PATH,
            name="main_result",
            revision=1,
        )
        assert source.ok and source.value is not None, source.issues
        if start_stage == "statement_nl":
            source.value.statement.deps = [RepoDeclDep(ref=ref)]
        else:
            assert source.value.proof is not None
            source.value.proof.deps = [RepoDeclDep(ref=ref)]
        _write_revision_for_test(
            runtime,
            case_root,
            decl_name="main_result",
            revision=source.value,
        )
        resolution = SimpleNamespace(
            compatible=compatible,
            resolved_revision=2 if compatible else None,
            reason="compatible_revision" if compatible else "declared_api_changed",
        )
        monkeypatch.setattr(
            resolver,
            "resolve_decl_refs_batch",
            lambda *args, **kwargs: runtime.foundation.ok([resolution]),
        )
        monkeypatch.setattr(
            resolver,
            "resolve_public_decl_refs_batch",
            lambda *args, **kwargs: runtime.foundation.ok([resolution]),
        )
        public_item = SimpleNamespace(
            ref=(
                ref.model_copy(update={"node": "Main.Decoy"})
                if dep_node == "Main.Exact"
                else ref
            ),
            resolved_revision=2,
            ready=ready,
            stale=stale,
        )
        if repo is None:
            monkeypatch.setattr(
                runtime.node.public_decl_access,
                "list_node_public_decls",
                lambda *args, **kwargs: runtime.foundation.ok([public_item]),
            )
        else:
            monkeypatch.setattr(
                runtime.node.public_decl_access,
                "list_repo_public_decls",
                lambda *args, **kwargs: runtime.foundation.ok([public_item]),
            )
        source_before = source_path.read_bytes()
        current_before = current_path.read_bytes()

        prepared = (
            runtime.decl_graph.prepare_statement_nl_from_revision(
                case_root,
                node_path=_NODE_PATH,
                round_id=round_id,
                decl_name="main_result",
                source_revision=1,
            )
            if start_stage == "statement_nl"
            else runtime.decl_graph.prepare_proof_nl_from_revision(
                case_root,
                node_path=_NODE_PATH,
                round_id=round_id,
                decl_name="main_result",
                source_revision=1,
            )
        )

        assert not prepared.ok
        assert [issue.kind for issue in prepared.issues] == [expected_issue]
        assert source_path.read_bytes() == source_before
        assert current_path.read_bytes() == current_before


def test_prepare_compatible_cross_node_dependency_preserves_historical_anchor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime, round_id, _source_path, _source_bytes, _current_path = _history_prepare_fixture(
        tmp_path,
        start_stage="statement_nl",
    )
    ref = DeclRef(node="Main.Provider", name="provider_result", revision=1)
    source = runtime.decl_graph.get_decl_revision(
        tmp_path,
        node_path=_NODE_PATH,
        name="main_result",
        revision=1,
    )
    assert source.ok and source.value is not None, source.issues
    source.value.statement.deps = [RepoDeclDep(ref=ref)]
    _write_revision_for_test(
        runtime,
        tmp_path,
        decl_name="main_result",
        revision=source.value,
    )
    resolution = SimpleNamespace(
        compatible=True,
        resolved_revision=2,
        reason="compatible_revision",
    )
    public_item = SimpleNamespace(
        ref=ref.model_copy(update={"revision": 2}),
        resolved_revision=2,
        ready=True,
        stale=False,
    )
    monkeypatch.setattr(
        runtime.decl_graph.ref_compatibility,
        "resolve_decl_refs_batch",
        lambda *args, **kwargs: runtime.foundation.ok([resolution]),
    )
    monkeypatch.setattr(
        runtime.node.public_decl_access,
        "list_node_public_decls",
        lambda *args, **kwargs: runtime.foundation.ok([public_item]),
    )

    prepared = runtime.decl_graph.prepare_statement_nl_from_revision(
        tmp_path,
        node_path=_NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        source_revision=1,
    )

    assert prepared.ok and prepared.value is not None, prepared.issues
    current = runtime.decl_graph.get_decl_revision(
        tmp_path,
        node_path=_NODE_PATH,
        name="main_result",
        revision=2,
    )
    assert current.ok and current.value is not None, current.issues
    assert current.value.statement.deps == [RepoDeclDep(ref=ref)]


def test_statement_and_proof_stage_mutations_write_candidates_without_advancing_revision_state(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round_with_decl(tmp_path)
    runtime = make_runtime()
    service = runtime.decl_graph

    statement_nl = service.write_statement_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
        origin=[{"kind": "source", "source_path": "main", "start_line": 1, "end_line": 1}],
        deps=["supporting_lemma"],
    )
    assert statement_nl.ok and statement_nl.value is not None
    statement_revision = service.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=statement_nl.value.revision,
    )
    assert statement_revision.ok and statement_revision.value is not None
    assert statement_revision.value.state == DeclState.PLANNED
    assert statement_revision.value.statement.nl is not None
    assert statement_revision.value.statement.nl.origin[0].model_dump(exclude_none=True) == {
        "kind": "source",
        "source_path": "main",
        "start_line": 1,
        "end_line": 1,
    }
    assert [item.ref.name for item in statement_revision.value.statement.deps] == ["supporting_lemma"]
    assert [item.ref.node for item in statement_revision.value.statement.deps] == ["Main.Topic.Core"]

    statement_deps = service.write_statement_deps(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        deps=["supporting_lemma", "statement_helper"],
    )
    assert statement_deps.ok and statement_deps.value is not None
    statement_deps_revision = service.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=statement_deps.value.revision,
    )
    assert statement_deps_revision.ok and statement_deps_revision.value is not None
    assert [item.ref.name for item in statement_deps_revision.value.statement.deps] == [
        "statement_helper",
        "supporting_lemma",
    ]
    assert [item.ref.node for item in statement_deps_revision.value.statement.deps] == [
        "Main.Topic.Core",
        "Main.Topic.Core",
    ]

    statement_formal = write_statement_formal_for_test(runtime,
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        lean_code="theorem main_result : True := by sorry",
        lean_check=lean_check_payload(contains_sorry=True),
        deps=["supporting_lemma"],
    )
    assert statement_formal.ok and statement_formal.value is not None
    assert statement_formal.value.state == DeclState.PLANNED
    assert statement_formal.value.statement.formal is not None
    assert statement_formal.value.statement.formal.check is not None
    assert statement_formal.value.statement.formal.check.contains_sorry is True
    assert statement_formal.value.statement.formal.check.status == "passed"
    assert statement_formal.value.statement.formal.check.contains_sorry is True

    proof_nl = service.write_proof_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        nl="The proof is by triviality.",
        deps=["supporting_lemma", "proof_helper"],
    )
    assert proof_nl.ok and proof_nl.value is not None
    proof_revision = service.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=proof_nl.value.revision,
    )
    assert proof_revision.ok and proof_revision.value is not None
    assert proof_revision.value.state == DeclState.PLANNED
    assert [item.ref.name for item in proof_revision.value.statement.deps] == ["supporting_lemma"]
    assert proof_revision.value.proof is not None
    assert [item.ref.name for item in proof_revision.value.proof.deps] == ["proof_helper", "supporting_lemma"]
    assert [item.ref.node for item in proof_revision.value.proof.deps] == ["Main.Topic.Core", "Main.Topic.Core"]

    proof_formal = write_proof_formal_for_test(runtime,
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        lean_code="by trivial",
        lean_check=lean_check_payload(),
        deps=["proof_helper"],
    )
    assert proof_formal.ok and proof_formal.value is not None
    assert proof_formal.value.state == DeclState.PLANNED
    assert proof_formal.value.proof is not None
    assert proof_formal.value.proof.formal is not None
    assert proof_formal.value.proof.formal.code == "by trivial"
    assert [item.ref.name for item in proof_formal.value.statement.deps] == ["supporting_lemma"]
    assert [item.ref.name for item in proof_formal.value.proof.deps] == ["proof_helper"]

    revision_path = service.graph_store.revision_path(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="main_result",
        revision=proof_formal.value.revision,
    )
    payload = json.loads(revision_path.read_text(encoding="utf-8"))
    assert payload["status"] == "open"
    assert payload["change"]["kind"] == "create"
    assert payload["statement"]["nl"]["text"] == "The main result states True."
    assert payload["statement"]["formal"]["code"] == "theorem main_result : True := by sorry"
    assert payload["statement"]["deps"] == [
        {
            "kind": "repo_decl",
            "reason": None,
            "ref": {"name": "supporting_lemma", "node": "Main.Topic.Core", "repo": None, "revision": 1},
        }
    ]
    assert payload["proof"]["nl"]["text"] == "The proof is by triviality."
    assert payload["proof"]["formal"]["code"] == "by trivial"
    assert payload["proof"]["deps"] == [
        {
            "kind": "repo_decl",
            "reason": None,
            "ref": {"name": "proof_helper", "node": "Main.Topic.Core", "repo": None, "revision": 1},
        }
    ]
    for legacy_field in [
        "version_status",
        "change_kind",
        "statement_nl",
        "statement_origin",
        "statement_deps",
        "statement_lean_code",
        "statement_lean_check",
        "proof_nl",
        "proof_origin",
        "proof_deps",
        "proof_lean_code",
        "proof_lean_check",
        "decl_deps",
    ]:
        assert legacy_field not in payload


def test_statement_formal_requires_statement_nl(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round_with_decl(tmp_path)
    runtime = make_runtime()

    result = write_statement_formal_for_test(runtime,
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        lean_code="theorem main_result : True := by sorry",
        lean_check=lean_check_payload(),
    )

    assert not result.ok
    assert result.issues[0].kind == "statement_nl_missing"


def test_add_statement_dependency_is_idempotent_and_reports_metadata_conflict(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round_with_decl(tmp_path)
    runtime = make_runtime()
    service = runtime.decl_graph
    dep = RepoDeclDep(ref=DeclRef(name="supporting_lemma"), reason="Needed by the statement.")

    added = service.add_statement_dep(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        dep=dep,
    )
    duplicate = service.add_statement_dep(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        dep=dep,
    )
    conflict = service.add_statement_dep(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        dep=RepoDeclDep(ref=DeclRef(name="supporting_lemma"), reason="Different semantic origin."),
    )

    assert added.ok and added.value is not None
    assert duplicate.ok and duplicate.value is not None
    assert [issue.kind for issue in duplicate.issues] == ["statement_dep_already_present"]
    assert duplicate.value.changed is False
    assert duplicate.value.already_present == [dep]
    assert not conflict.ok
    assert conflict.issues[0].kind == "statement_dep_conflict"


def test_add_proof_dependency_is_idempotent_and_reports_metadata_conflict(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round_with_decl(tmp_path)
    runtime = make_runtime()
    service = runtime.decl_graph
    dep = RepoDeclDep(ref=DeclRef(name="supporting_lemma"), reason="Needed by the proof.")

    added = service.add_proof_dep(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        dep=dep,
    )
    duplicate = service.add_proof_dep(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        dep=dep,
    )
    conflict = service.add_proof_dep(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        dep=RepoDeclDep(ref=DeclRef(name="supporting_lemma"), reason="Different semantic origin."),
    )

    assert added.ok and added.value is not None
    assert duplicate.ok and duplicate.value is not None
    assert [issue.kind for issue in duplicate.issues] == ["proof_dep_already_present"]
    assert duplicate.value.changed is False
    assert duplicate.value.already_present == [dep]
    assert not conflict.ok
    assert conflict.issues[0].kind == "proof_dep_conflict"


def test_advance_stage_state_is_explicit_after_candidate_write(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round_with_decl(tmp_path)
    runtime = make_runtime()
    service = runtime.decl_graph

    candidate = service.write_statement_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
    )
    assert candidate.ok and candidate.value is not None
    assert candidate.value.changed is True

    advanced = service.advance_stage_state(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        stage="statement_nl",
        decl_names=["main_result"],
    )

    assert advanced.ok and advanced.value is not None
    assert advanced.value == ["main_result"]
    revision = service.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=1,
    )
    assert revision.ok and revision.value is not None
    assert revision.value.state == DeclState.SPECIFIED


def test_advance_stage_state_validates_whole_batch_before_writing(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round_with_decl(tmp_path)
    runtime = make_runtime()
    service = runtime.decl_graph
    candidate = service.write_statement_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
    )
    assert candidate.ok and candidate.value is not None

    advanced = service.advance_stage_state(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        stage="statement_nl",
        decl_names=["main_result", "not_in_round"],
    )

    assert not advanced.ok
    revision = service.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="main_result",
        revision=1,
    )
    assert revision.ok and revision.value is not None
    assert revision.value.state == DeclState.PLANNED


def test_proof_stages_reject_non_theorem_like_decl(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round_with_decl(tmp_path, name="main_def", kind="definition")
    runtime = make_runtime()
    service = runtime.decl_graph
    assert service.write_statement_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_def",
        nl="The definition has type Nat.",
    ).ok
    assert write_statement_formal_for_test(runtime,
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_def",
        lean_code="def main_def : Nat := 0",
        lean_check=lean_check_payload(),
    ).ok

    result = service.write_proof_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_def",
        nl="No proof is needed.",
    )

    assert not result.ok
    assert result.issues[0].kind == "decl_not_theorem_like"


def test_stage_mutation_requires_running_round(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    service = make_runtime().decl_graph
    strategy = service.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = service.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round objective.",
    )
    assert round_record.ok and round_record.value is not None
    assert service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        name="main_result",
        kind="theorem",
        objective="Create main_result.",
        summary="Main result.",
    ).ok

    result = service.write_statement_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        decl_name="main_result",
        nl="The main result states True.",
    )

    assert not result.ok
    assert result.issues[0].kind == "round_not_running"


def test_stage_mutation_rejects_decl_not_in_round(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round_with_decl(tmp_path)
    service = make_runtime().decl_graph

    result = service.write_statement_nl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="missing_decl",
        nl="Missing declaration.",
    )

    assert not result.ok
    assert result.issues[0].kind == "decl_not_in_round"
