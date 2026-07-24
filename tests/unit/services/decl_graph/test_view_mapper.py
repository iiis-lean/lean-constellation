from __future__ import annotations

from lean_constellation.services.decl_graph import (
    Decl,
    DeclGraphRound,
    DeclGraphStrategy,
    DeclReviewMarkRecord,
    DeclRevision,
    DeclStage,
    DeclState,
    DeclGraphViewMapper,
)


def test_decl_view_exposes_identity_and_status_without_revision_payload() -> None:
    mapper = DeclGraphViewMapper()
    decl = Decl(
        name="main_result",
        node_path="Main.Topic",
        kind="theorem",
        module="TestProject.Main.Topic.Theorems.main_result",
        public=True,
        summary="Main theorem.",
    )
    revision = DeclRevision(
        revision=2,
        lean_decl_name="TestProject.main_result",
        state=DeclState.PROOF_PLANNED,
    )

    view = mapper.decl_view(decl, revision)
    dumped = view.model_dump(mode="json")

    assert dumped["name"] == "main_result"
    assert dumped["module"] == "TestProject.Main.Topic.Theorems.main_result"
    assert dumped["lean_decl_name"] == "TestProject.main_result"
    assert dumped["visibility"] == "public"
    assert dumped["state"] == "proof_planned"
    assert "statement" not in dumped
    assert "proof" not in dumped


def test_strategy_round_decl_and_review_views_are_read_only_shapes() -> None:
    mapper = DeclGraphViewMapper()
    strategy = DeclGraphStrategy(strategy_id="strategy-1", node_path="Main.Topic", objective="Build theorem graph.")
    round_record = DeclGraphRound(
        round_id="round-1",
        node_path="Main.Topic",
        strategy_id="strategy-1",
        round_index=1,
        objective="Create main_result.",
    )
    decl = Decl(name="main_result", node_path="Main.Topic", kind="theorem", public=False)
    review = DeclReviewMarkRecord(
        round_id="round-1",
        node_path="Main.Topic",
        stage=DeclStage.STATEMENT_NL,
        decl_name="main_result",
        passed=True,
        summary="Statement accepted.",
    )

    assert mapper.strategy_view(strategy).strategy_id == "strategy-1"
    assert mapper.round_view(round_record).change_ids == []
    assert mapper.decl_view(decl).visibility == "private"
    assert mapper.review_mark_view(review).passed is True
