from __future__ import annotations

from lean_constellation.domain.refs import DeclRef, MathlibRef
from lean_constellation.services.decl_graph import (
    Decl,
    DeclChangeKind,
    DeclGraphRound,
    DeclGraphStrategy,
    DeclReviewMarkRecord,
    DeclRevision,
    DeclRevisionChange,
    DeclStage,
    DeclState,
    DeclGraphViewMapper,
    MathlibDeclDep,
    RepoDeclDep,
)


def test_revision_tool_view_flattens_nested_truth_without_legacy_decl_deps() -> None:
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
        change=DeclRevisionChange(
            kind=DeclChangeKind.UPDATE,
            objective="Prove the main theorem.",
            summary="Statement and proof route updated.",
        ),
    )
    revision.statement_nl = "The main theorem states True."
    revision.statement_deps = ["statement_helper"]
    revision.proof_nl = "Use the helper."
    revision.proof.deps.append(RepoDeclDep(ref=DeclRef(repo=None, node="Main.Topic", name="proof_helper", revision=1)))
    revision.proof.deps.append(MathlibDeclDep(ref=MathlibRef(name="Nat.succ", module="Mathlib.Data.Nat.Basic"), reason="Uses successor."))

    view = mapper.revision_tool_view(decl=decl, revision=revision)
    dumped = view.model_dump(mode="json")

    assert dumped["decl_name"] == "main_result"
    assert dumped["module"] == "TestProject.Main.Topic.Theorems.main_result"
    assert dumped["lean_decl_name"] == "TestProject.main_result"
    assert dumped["visibility"] == "public"
    assert dumped["change_id"] == "main_result@rev:2"
    assert dumped["statement_nl"] == "The main theorem states True."
    assert dumped["statement_deps"] == ["statement_helper"]
    assert dumped["statement_dep_refs"][0]["kind"] == "repo_decl"
    assert dumped["proof_nl"] == "Use the helper."
    assert dumped["proof_deps"] == ["Nat.succ", "proof_helper"]
    proof_dep_refs = {item["kind"]: item for item in dumped["proof_dep_refs"]}
    assert proof_dep_refs["mathlib_decl"]["ref"] == {"name": "Nat.succ", "module": "Mathlib.Data.Nat.Basic"}
    assert proof_dep_refs["mathlib_decl"]["reason"] == "Uses successor."
    assert proof_dep_refs["repo_decl"]["ref"]["node"] == "Main.Topic"
    assert proof_dep_refs["repo_decl"]["ref"]["name"] == "proof_helper"
    assert proof_dep_refs["repo_decl"]["ref"]["revision"] == 1
    assert dumped["effective_deps"] == ["Nat.succ", "proof_helper", "statement_helper"]
    assert len(dumped["effective_dep_refs"]) == 3
    assert "statement" not in dumped
    assert "proof" not in dumped
    assert "decl_deps" not in dumped


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
