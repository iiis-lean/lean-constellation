from __future__ import annotations

import pytest
from pydantic import ValidationError

from lean_constellation.services.decl_graph import (
    Decl,
    DeclGraphIndex,
    DeclGraphRound,
    DeclGraphStrategy,
    DeclRoundStatus,
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


def test_strategy_round_agent_views_use_sequences_without_random_ids() -> None:
    mapper = DeclGraphViewMapper()
    strategy = DeclGraphStrategy(
        strategy_id="strategy-random",
        node_path="Main.Topic",
        objective="Build theorem graph.",
        created_round_ids=["round-random"],
    )
    round_record = DeclGraphRound(
        round_id="round-random",
        node_path="Main.Topic",
        strategy_id="strategy-random",
        round_index=3,
        objective="Create main_result.",
    )

    strategy_dump = mapper.strategy_agent_view(
        strategy,
        strategy_sequence=2,
        round_sequences=[3],
    ).model_dump(mode="json")
    round_dump = mapper.round_agent_view(
        round_record,
        strategy_sequence=2,
    ).model_dump(mode="json")

    assert strategy_dump["strategy_sequence"] == 2
    assert strategy_dump["round_sequences"] == [3]
    assert "strategy_id" not in strategy_dump
    assert "created_round_ids" not in strategy_dump
    assert round_dump["round_sequence"] == 3
    assert round_dump["strategy_sequence"] == 2
    assert "round_id" not in round_dump
    assert "strategy_id" not in round_dump

    assert mapper.strategy_view(strategy).strategy_id == "strategy-random"
    assert mapper.round_view(round_record).round_id == "round-random"


def test_decl_graph_index_agent_view_uses_counts_without_durable_ids() -> None:
    mapper = DeclGraphViewMapper()
    index = DeclGraphIndex(
        node_id="node-random",
        node_path="Main.Topic",
        strategy_ids=["strategy-random"],
        round_ids=["round-random"],
        decl_names=["main_result"],
    )

    agent_dump = mapper.graph_index_agent_view(index).model_dump(mode="json")

    assert agent_dump == {
        "node_path": "Main.Topic",
        "strategy_count": 1,
        "round_count": 1,
        "decl_count": 1,
        "decl_names": ["main_result"],
    }
    assert index.strategy_ids == ["strategy-random"]
    assert index.round_ids == ["round-random"]


def test_committed_round_requires_complete_plan_closeout_truth() -> None:
    with pytest.raises(ValidationError):
        DeclGraphRound(
            round_id="round-legacy",
            node_path="Main.Topic",
            strategy_id="strategy-1",
            round_index=1,
            status=DeclRoundStatus.COMMITTED,
            objective="Legacy committed round without Plan closeout.",
        )
