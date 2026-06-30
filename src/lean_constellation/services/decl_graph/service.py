"""DeclGraphService composition and public wrappers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.services.decl_graph.graph_store import GraphStoreComponent
from lean_constellation.services.decl_graph.decl_catalog import DeclCatalogComponent
from lean_constellation.services.decl_graph.dependency import DeclDependencyComponent
from lean_constellation.services.decl_graph.models import (
    DeclChangeRecord,
    DeclDeleteClosureView,
    DeclDependencyClosureView,
    DeclFileRevisionView,
    DeclGraphIndex,
    DeclGraphStoreView,
    DeclReviewMarkRecord,
    DeclReadinessReport,
    DeclRecord,
    DeclRevisionRecord,
    DeclRoundRecord,
    DeclRoundResultKind,
    DeclStage,
    DeclState,
    DeclStrategyRecord,
    StageReviewResultView,
)
from lean_constellation.services.decl_graph.review_gate import ReviewGateComponent
from lean_constellation.services.decl_graph.readiness import DeclReadinessComponent
from lean_constellation.services.decl_graph.strategy_round import StrategyRoundComponent
from lean_constellation.services.decl_graph.stage_mutation import StageMutationComponent
from lean_constellation.services.foundation import GateReport, ServiceResult
from lean_constellation.services.lean_projection.lean_check import LeanCheckView
from lean_constellation.services.node.export import DeclPublicView
from lean_constellation.services.validation_snapshot.audit import AuditReport

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class DeclGraphService:
    """Composition root for Content node declaration graph services."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        graph_store: GraphStoreComponent | None = None,
        strategy_round: StrategyRoundComponent | None = None,
        decl_catalog: DeclCatalogComponent | None = None,
        stage_mutation: StageMutationComponent | None = None,
        review_gate: ReviewGateComponent | None = None,
        dependency: DeclDependencyComponent | None = None,
        readiness: DeclReadinessComponent | None = None,
    ) -> None:
        self.runtime = runtime
        self.graph_store = graph_store or GraphStoreComponent(runtime)
        self.strategy_round = strategy_round or StrategyRoundComponent(runtime, self.graph_store)
        self.decl_catalog = decl_catalog or DeclCatalogComponent(runtime, self.graph_store, self.strategy_round)
        self.stage_mutation = stage_mutation or StageMutationComponent(
            runtime,
            self.graph_store,
            self.strategy_round,
            self.decl_catalog,
        )
        self.review_gate = review_gate or ReviewGateComponent(
            runtime,
            self.graph_store,
            self.strategy_round,
            self.decl_catalog,
        )
        self.dependency = dependency or DeclDependencyComponent(runtime, self.decl_catalog)
        self.readiness = readiness or DeclReadinessComponent(runtime, self.decl_catalog, self.dependency)

    def ensure_decl_graph(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphStoreView]:
        return self.graph_store.ensure_graph(repo_root, node_path=node_path)

    def get_decl_graph_index(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphIndex]:
        return self.graph_store.get_index(repo_root, node_path=node_path)

    def get_decl_graph_store_view(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphStoreView]:
        return self.graph_store.get_store_view(repo_root, node_path=node_path)

    def rebuild_decl_graph_index(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphIndex]:
        return self.graph_store.rebuild_index(repo_root, node_path=node_path)

    def ensure_open_strategy(
        self,
        repo_root: Path,
        *,
        node_path: str,
        objective: str,
        rationale: str | None = None,
    ) -> ServiceResult[DeclStrategyRecord]:
        return self.strategy_round.ensure_open_strategy(
            repo_root,
            node_path=node_path,
            objective=objective,
            rationale=rationale,
        )

    def close_strategy(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy_id: str,
        summary: str,
        reason: str | None = None,
        failed: bool = False,
    ) -> ServiceResult[DeclStrategyRecord]:
        return self.strategy_round.close_strategy(
            repo_root,
            node_path=node_path,
            strategy_id=strategy_id,
            summary=summary,
            reason=reason,
            failed=failed,
        )

    def create_round_draft(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy_id: str,
        objective: str,
        change_ids: list[str] | None = None,
    ) -> ServiceResult[DeclRoundRecord]:
        return self.strategy_round.create_round_draft(
            repo_root,
            node_path=node_path,
            strategy_id=strategy_id,
            objective=objective,
            change_ids=change_ids,
        )

    def start_round(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[DeclRoundRecord]:
        return self.strategy_round.start_round(repo_root, node_path=node_path, round_id=round_id)

    def write_decl_change_summary(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        change_id: str,
        summary: str,
    ) -> ServiceResult[DeclRoundRecord]:
        return self.strategy_round.write_decl_change_summary(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            change_id=change_id,
            summary=summary,
        )

    def write_round_summary(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        summary: str,
    ) -> ServiceResult[DeclRoundRecord]:
        return self.strategy_round.write_round_summary(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            summary=summary,
        )

    def mark_round_terminal(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        result_kind: DeclRoundResultKind | str,
        reason: str | None = None,
    ) -> ServiceResult[DeclRoundRecord]:
        return self.strategy_round.mark_round_terminal(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            result_kind=result_kind,
            reason=reason,
        )

    def get_strategy(self, repo_root: Path, *, node_path: str, strategy_id: str) -> ServiceResult[DeclStrategyRecord]:
        return self.strategy_round.get_strategy(repo_root, node_path=node_path, strategy_id=strategy_id)

    def list_strategies(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclStrategyRecord]]:
        return self.strategy_round.list_strategies(repo_root, node_path=node_path)

    def get_round(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[DeclRoundRecord]:
        return self.strategy_round.get_round(repo_root, node_path=node_path, round_id=round_id)

    def list_rounds(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclRoundRecord]]:
        return self.strategy_round.list_rounds(repo_root, node_path=node_path)

    def create_decl(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        name: str,
        kind: str,
        objective: str,
        summary: str,
        public: bool = False,
        end_after_state: DeclState | str = DeclState.DECLARED,
        module: str | None = None,
    ) -> ServiceResult[DeclChangeRecord]:
        return self.decl_catalog.create_decl(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            name=name,
            kind=kind,
            objective=objective,
            summary=summary,
            public=public,
            end_after_state=end_after_state,
            module=module,
        )

    def open_decl_update(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        name: str,
        objective: str,
        end_after_state: DeclState | str,
        start_before_state: DeclState | str | None = None,
    ) -> ServiceResult[DeclChangeRecord]:
        return self.decl_catalog.open_decl_update(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            name=name,
            objective=objective,
            end_after_state=end_after_state,
            start_before_state=start_before_state,
        )

    def mark_decl_delete(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        name: str,
        objective: str,
    ) -> ServiceResult[DeclChangeRecord]:
        return self.decl_catalog.mark_decl_delete(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            name=name,
            objective=objective,
        )

    def commit_decl_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        name: str,
        revision: int | None = None,
        state: DeclState | str | None = None,
    ) -> ServiceResult[DeclRevisionRecord]:
        return self.decl_catalog.commit_decl_revision(
            repo_root,
            node_path=node_path,
            name=name,
            revision=revision,
            state=state,
        )

    def get_decl(self, repo_root: Path, *, node_path: str, name: str) -> ServiceResult[DeclRecord]:
        return self.decl_catalog.get_decl(repo_root, node_path=node_path, name=name)

    def list_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclRecord]]:
        return self.decl_catalog.list_decls(repo_root, node_path=node_path)

    def get_decl_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        name: str,
        revision: int,
    ) -> ServiceResult[DeclRevisionRecord]:
        return self.decl_catalog.get_decl_revision(repo_root, node_path=node_path, name=name, revision=revision)

    def get_decl_change(self, repo_root: Path, *, node_path: str, change_id: str) -> ServiceResult[DeclChangeRecord]:
        return self.decl_catalog.get_decl_change(repo_root, node_path=node_path, change_id=change_id)

    def compute_delete_closure(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_names: list[str],
    ) -> ServiceResult[DeclDeleteClosureView]:
        return self.decl_catalog.compute_delete_closure(repo_root, node_path=node_path, decl_names=decl_names)

    def validate_round_draft(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[object]:
        return self.decl_catalog.validate_round_draft(repo_root, node_path=node_path, round_id=round_id)

    def write_statement_nl(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        nl: str,
        origin: list[dict[str, object]] | None = None,
        deps: list[str] | None = None,
    ) -> ServiceResult[DeclRevisionRecord]:
        return self.stage_mutation.write_statement_nl(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            decl_name=decl_name,
            nl=nl,
            origin=origin,
            deps=deps,
        )

    def write_statement_formal(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        lean_code: str,
        lean_check: dict[str, object],
        deps: list[str] | None = None,
    ) -> ServiceResult[DeclRevisionRecord]:
        return self.stage_mutation.write_statement_formal(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            decl_name=decl_name,
            lean_code=lean_code,
            lean_check=lean_check,
            deps=deps,
        )

    def write_proof_nl(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        nl: str,
        origin: list[dict[str, object]] | None = None,
        deps: list[str] | None = None,
    ) -> ServiceResult[DeclRevisionRecord]:
        return self.stage_mutation.write_proof_nl(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            decl_name=decl_name,
            nl=nl,
            origin=origin,
            deps=deps,
        )

    def write_proof_formal(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        lean_code: str,
        lean_check: dict[str, object],
        deps: list[str] | None = None,
    ) -> ServiceResult[DeclRevisionRecord]:
        return self.stage_mutation.write_proof_formal(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            decl_name=decl_name,
            lean_code=lean_code,
            lean_check=lean_check,
            deps=deps,
        )

    def record_decl_review(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        stage: DeclStage | str,
        decl_name: str,
        passed: bool,
        summary: str,
        issue_kind: str | None = None,
        suggested_fix: str | None = None,
    ) -> ServiceResult[DeclReviewMarkRecord]:
        return self.review_gate.record_decl_review(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            decl_name=decl_name,
            passed=passed,
            summary=summary,
            issue_kind=issue_kind,
            suggested_fix=suggested_fix,
        )

    def submit_stage_review(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        stage: DeclStage | str,
        summary: str,
    ) -> ServiceResult[StageReviewResultView]:
        return self.review_gate.submit_stage_review(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            summary=summary,
        )

    def compute_dependency_closure(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_names: list[str],
    ) -> ServiceResult[DeclDependencyClosureView]:
        return self.dependency.compute_dependency_closure(repo_root, node_path=node_path, decl_names=decl_names)

    def check_delete_preflight(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_names: list[str],
    ) -> ServiceResult[object]:
        return self.dependency.check_delete_preflight(repo_root, node_path=node_path, decl_names=decl_names)

    def audit_round_dependencies(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[object]:
        return self.dependency.audit_round_dependencies(repo_root, node_path=node_path, round_id=round_id)

    def check_decl_ready(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        policy: str | None = None,
    ) -> ServiceResult[DeclReadinessReport]:
        return self.readiness.check_decl_ready(repo_root, node_path=node_path, decl_name=decl_name, policy=policy)

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        return self.readiness.list_content_public_decls(repo_root, node_path=node_path)

    def get_current_decl_revision(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[DeclFileRevisionView]:
        return self.readiness.get_current_decl_revision(repo_root, node_path=node_path, decl_name=decl_name)

    def save_statement_formal_snapshot(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
    ) -> ServiceResult[DeclRevisionRecord]:
        return self.readiness.save_statement_formal_snapshot(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            code=code,
            check=check,
        )

    def save_proof_formal_snapshot(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
    ) -> ServiceResult[DeclRevisionRecord]:
        return self.readiness.save_proof_formal_snapshot(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            code=code,
            check=check,
        )

    def list_active_decl_names(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[str]]:
        return self.readiness.list_active_decl_names(repo_root, node_path=node_path)

    def check_content_node_ready(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        return self.readiness.check_content_node_ready(repo_root, node_path=node_path)

    def check_formal_stage_consistency(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: str,
    ) -> ServiceResult[GateReport]:
        return self.readiness.check_formal_stage_consistency(repo_root, node_path=node_path, decl_name=decl_name, stage=stage)

    def run_round_local_audit(self, repo_root: Path, *, node_path: str, round_id: str, stage: str) -> ServiceResult[AuditReport]:
        return self.readiness.run_round_local_audit(repo_root, node_path=node_path, round_id=round_id, stage=stage)

    def run_delete_sanity_audit(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[AuditReport]:
        return self.readiness.run_delete_sanity_audit(repo_root, node_path=node_path, round_id=round_id)
