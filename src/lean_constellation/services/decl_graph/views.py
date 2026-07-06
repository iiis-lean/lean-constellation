"""View mapping for DeclGraph domain truth."""

from __future__ import annotations

from lean_constellation.services.decl_graph.models import (
    Decl,
    DeclGraphRound,
    DeclGraphRoundView,
    DeclGraphStrategy,
    DeclGraphStrategyView,
    DeclReviewMarkRecord,
    DeclReviewMarkView,
    DeclRevision,
    DeclRevisionToolView,
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
            change_ids=round_record.change_ids,
            change_summaries=dict(round_record.change_summaries),
            summary=round_record.summary,
            result_kind=round_record.result_kind,
            result_reason=round_record.result_reason,
            created_at=round_record.created_at,
            started_at=round_record.started_at,
            committed_at=round_record.committed_at,
        )

    def revision_tool_view(
        self,
        *,
        decl: Decl,
        revision: DeclRevision,
    ) -> DeclRevisionToolView:
        change = revision.change
        statement_origin = list(revision.statement.nl.origin) if revision.statement.nl is not None else []
        proof_origin = list(revision.proof.nl.origin) if revision.proof is not None and revision.proof.nl is not None else []
        return DeclRevisionToolView(
            decl_name=revision.decl_name,
            node_path=decl.node_path,
            revision=revision.revision,
            kind=decl.kind,
            lifecycle=decl.lifecycle,
            public=decl.public,
            visibility="public" if decl.public else "private",
            state=revision.state,
            status=revision.status,
            module=revision.module or decl.module,
            change_id=self.change_id_for_revision(revision),
            change_kind=change.kind if change is not None else None,
            change_objective=change.objective if change is not None else None,
            change_summary=change.summary if change is not None else None,
            start_before_state=change.start_before_state if change is not None else None,
            end_after_state=change.end_after_state if change is not None else None,
            require_target_state_satisfied=change.require_target_state_satisfied if change is not None else True,
            statement_nl=revision.statement_nl,
            statement_origin=statement_origin,
            statement_deps=revision.statement_deps,
            statement_lean_code=revision.statement_lean_code,
            statement_lean_check=revision.statement_lean_check,
            proof_nl=revision.proof_nl,
            proof_origin=proof_origin,
            proof_deps=revision.proof_deps,
            proof_lean_code=revision.proof_lean_code,
            proof_lean_check=revision.proof_lean_check,
            effective_deps=sorted(set(revision.statement_deps) | set(revision.proof_deps)),
            summary=decl.summary,
            updated_at=revision.updated_at,
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
            created_at=mark.created_at,
        )

    def change_id_for_revision(self, revision: DeclRevision) -> str | None:
        if revision.change is None:
            return None
        return f"{revision.decl_name}@rev:{revision.revision}"
