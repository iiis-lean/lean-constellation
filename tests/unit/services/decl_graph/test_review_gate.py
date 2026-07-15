from pathlib import Path

from tests.unit_services_helpers import initialize_native_test_repo, make_runtime

from lean_constellation.services.decl_graph import DeclStage, DeclState


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


def _create_running_round(tmp_path: Path, decl_kinds: dict[str, str]) -> str:
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
    for name, kind in decl_kinds.items():
        assert service.create_decl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            name=name,
            kind=kind,
            objective=f"Create {name}.",
            summary=f"{name} summary.",
            end_after_state=DeclState.PROVED if kind == "theorem" else DeclState.DECLARED,
        ).ok
    assert service.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    return round_record.value.round_id


def test_stage_review_passes_after_all_required_decl_marks(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round(tmp_path, {"main_result": "theorem", "helper_def": "definition"})
    service = make_runtime().decl_graph

    main_mark = service.record_decl_review(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        stage=DeclStage.STATEMENT_NL,
        decl_name="main_result",
        passed=True,
        summary="Statement is clear.",
    )
    assert main_mark.ok and main_mark.value is not None
    helper_mark = service.record_decl_review(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        stage=DeclStage.STATEMENT_NL,
        decl_name="helper_def",
        passed=True,
        summary="Definition statement is clear.",
    )
    assert helper_mark.ok and helper_mark.value is not None

    result = service.aggregate_stage_review_marks(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        stage=DeclStage.STATEMENT_NL,
        summary="All statements accepted.",
        marks=[main_mark.value, helper_mark.value],
    )

    assert result.ok and result.value is not None
    assert result.value.passed is True
    assert result.value.reviewed_decl_names == ["helper_def", "main_result"]
    assert result.value.failed_decl_names == []
    assert result.value.missing_decl_names == []


def test_stage_review_rejects_submit_when_marks_are_missing(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round(tmp_path, {"main_result": "theorem", "helper_def": "definition"})
    service = make_runtime().decl_graph

    mark = service.record_decl_review(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        stage=DeclStage.STATEMENT_FORMAL,
        decl_name="main_result",
        passed=False,
        summary="Formal statement is too weak.",
        issue_kind="semantic_mismatch",
        suggested_fix="Strengthen the conclusion.",
    )
    assert mark.ok and mark.value is not None

    result = service.aggregate_stage_review_marks(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        stage=DeclStage.STATEMENT_FORMAL,
        summary="One formal statement failed.",
        marks=[mark.value],
    )

    assert not result.ok
    assert any(issue.kind == "review_marks_missing" for issue in result.issues)


def test_failed_review_mark_requires_issue_kind(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round(tmp_path, {"main_result": "theorem"})
    service = make_runtime().decl_graph

    result = service.record_decl_review(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        stage=DeclStage.STATEMENT_NL,
        decl_name="main_result",
        passed=False,
        summary="Something is wrong.",
    )

    assert not result.ok
    assert result.issues[0].kind == "review_issue_kind_required"


def test_proof_stage_requires_only_theorem_like_decl_marks(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    round_id = _create_running_round(tmp_path, {"main_result": "theorem", "helper_def": "definition"})
    service = make_runtime().decl_graph

    skipped = service.record_decl_review(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        stage=DeclStage.PROOF_NL,
        decl_name="helper_def",
        passed=True,
        summary="Definitions do not need proof review.",
    )
    assert not skipped.ok
    assert skipped.issues[0].kind == "review_decl_not_required"

    mark = service.record_decl_review(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        stage=DeclStage.PROOF_NL,
        decl_name="main_result",
        passed=True,
        summary="Proof route is valid.",
    )
    assert mark.ok and mark.value is not None
    result = service.aggregate_stage_review_marks(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        stage=DeclStage.PROOF_NL,
        summary="Proof routes accepted.",
        marks=[mark.value],
    )
    assert result.ok and result.value is not None
    assert result.value.passed is True
    assert result.value.reviewed_decl_names == ["main_result"]


def test_stage_review_requires_running_round(tmp_path: Path) -> None:
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

    result = service.submit_stage_review(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        stage=DeclStage.STATEMENT_NL,
        summary="Review before start.",
    )

    assert not result.ok
    assert result.issues[0].kind == "round_not_running"
