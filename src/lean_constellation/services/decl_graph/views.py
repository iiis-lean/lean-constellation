"""View mapping for DeclGraph domain truth."""

from __future__ import annotations

from lean_constellation.services.decl_graph.models import (
    Decl,
    DeclGraphRound,
    DeclGraphRoundView,
    DeclGraphStrategy,
    DeclGraphStrategyView,
    DeclRoundStatus,
    DeclReviewMarkRecord,
    DeclReviewMarkView,
    DeclRevision,
    DeclView,
)


class DeclGraphViewMapper:
    """Build Agent/API-facing views from DeclGraph truth models."""

    def decl_view(self, decl: Decl, revision: DeclRevision | None = None) -> DeclView:
        return DeclView(
            name=decl.name,
            node_path=decl.node_path,
            kind=decl.kind,
            lifecycle=decl.lifecycle,
            public=decl.public,
            visibility="public" if decl.public else "private",
            current_revision=decl.current_revision,
            revision_ids=list(decl.revision_ids),
            module=decl.module,
            lean_decl_name=revision.lean_decl_name if revision is not None else None,
            state=revision.state if revision is not None else None,
            status=revision.status if revision is not None else None,
            summary=decl.summary,
            created_at=decl.created_at,
            updated_at=decl.updated_at,
        )

    def strategy_view(self, strategy: DeclGraphStrategy) -> DeclGraphStrategyView:
        return DeclGraphStrategyView(
            strategy_id=strategy.strategy_id,
            node_path=strategy.node_path,
            status=strategy.status,
            objective=strategy.objective,
            rationale=strategy.rationale,
            created_round_ids=list(strategy.created_round_ids),
            summary=strategy.summary,
            closed_reason=strategy.closed_reason,
            created_at=strategy.created_at,
            closed_at=strategy.closed_at,
        )

    def round_view(self, round_record: DeclGraphRound) -> DeclGraphRoundView:
        return DeclGraphRoundView(
            round_id=round_record.round_id,
            node_path=round_record.node_path,
            strategy_id=round_record.strategy_id,
            round_index=round_record.round_index,
            status=round_record.status,
            objective=round_record.objective,
            revision_refs=list(round_record.revision_refs),
            discarded_by=round_record.discarded_by,
            discarded_at=round_record.discarded_at,
            change_ids=round_record.change_ids,
            summary=round_record.summary,
            execution_result_kind=round_record.execution_result_kind,
            execution_reason=round_record.execution_reason,
            result_kind=round_record.result_kind,
            result_reason=round_record.result_reason,
            closeout_required=round_record.status == DeclRoundStatus.AWAITING_CLOSEOUT,
            required_next_action=(
                "Write every declaration change summary, write the round summary, then close the round."
                if round_record.status == DeclRoundStatus.AWAITING_CLOSEOUT
                else None
            ),
            created_at=round_record.created_at,
            started_at=round_record.started_at,
            committed_at=round_record.committed_at,
        )

    def review_mark_view(self, mark: DeclReviewMarkRecord) -> DeclReviewMarkView:
        return DeclReviewMarkView(
            round_id=mark.round_id,
            node_path=mark.node_path,
            stage=mark.stage,
            decl_name=mark.decl_name,
            passed=mark.passed,
            summary=mark.summary,
            issue_kind=mark.issue_kind,
            suggested_fix=mark.suggested_fix,
            issue_categories=list(mark.issue_categories),
            required_changes=list(mark.required_changes),
            recommended_next_action=mark.recommended_next_action,
            created_at=mark.created_at,
        )
