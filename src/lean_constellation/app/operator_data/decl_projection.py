"""Typed Operator facade for DeclGraph and Lean projection business operations."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from lean_constellation.app.operator_data.common import (
    OperatorAccess,
    OperatorInputModel,
    OperatorLockPolicy,
    OperatorOperationSpec,
    operator_gate_view,
    project_operator_result,
)
from lean_constellation.app.operator_data.execution import OperatorExecutionContext, OperatorExecutionService
from lean_constellation.domain.common import StrictModel
from lean_constellation.services.decl_graph import DeclDraftSpec, DeclState, MathlibDeclDep, RepoDeclDep, RoundStageReview
from lean_constellation.services.decl_graph.models import DeclOriginRef
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.lean_projection import (
    DeclOwnedLeanFileView,
    ModuleBuildView,
    ProjectionRepairView,
    SafeFormalApplyView,
)


READ_DECL = OperatorOperationSpec("decl.read", OperatorAccess.READ, OperatorLockPolicy.NONE)
MUTATE_DECL = OperatorOperationSpec("decl.mutate", OperatorAccess.MUTATION, OperatorLockPolicy.OPERATOR, True)
APPLY_FORMAL = OperatorOperationSpec("projection.apply_formal", OperatorAccess.MUTATION, OperatorLockPolicy.OPERATOR, True)


class NodeInput(OperatorInputModel):
    node_path: str


class StrategyInput(NodeInput):
    objective: str
    rationale: str | None = None


class StrategyCloseInput(NodeInput):
    strategy_id: str
    summary: str
    reason: str | None = None
    failed: bool = False


class RoundInput(NodeInput):
    strategy_id: str
    objective: str


class RoundBatchInput(RoundInput):
    declarations: list[DeclDraftSpec]


class RoundIdentityInput(NodeInput):
    round_id: str


class DeclCreateInput(RoundIdentityInput):
    name: str
    kind: str
    objective: str
    summary: str
    public: bool = False
    target_state: DeclState = DeclState.DECLARED
    require_target_state_satisfied: bool = True


class DeclIdentityInput(NodeInput):
    decl_name: str


class DeclFileReadInput(DeclIdentityInput):
    revision: int | None = Field(default=None, ge=1)


class DeclRevisionInput(RoundIdentityInput):
    decl_name: str
    expected_revision: int = Field(ge=1)


class DeclUpdateInput(DeclRevisionInput):
    objective: str
    target_state: DeclState
    base_revision: int | None = Field(default=None, ge=1)
    reset_to_state: DeclState | None = None
    require_target_state_satisfied: bool = True


class DeclDeleteInput(DeclRevisionInput):
    objective: str


class NaturalLanguageInput(DeclRevisionInput):
    text: str
    origins: list[DeclOriginRef] = Field(default_factory=list)
    deps: list[RepoDeclDep | MathlibDeclDep] = Field(default_factory=list)


class FormalApplyInput(DeclRevisionInput):
    expected_state: DeclState
    expected_revision_digest: str
    lean_code: str


class StageGateInput(RoundIdentityInput):
    stage: Literal["statement_nl", "statement_formal", "proof_nl", "proof_formal"]
    target_decl_names: list[str]
    review: RoundStageReview
    retry_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=2, ge=0)


class RoundCloseoutInput(RoundIdentityInput):
    result_kind: Literal["success", "blocked", "failed"]
    reason: str | None = None
    acknowledged_by: str = "operator"


class RoundExecutionInput(RoundIdentityInput):
    outcome: Literal["completed", "blocked", "failed"]
    reason: str | None = None


class ProjectionSyncInput(DeclIdentityInput):
    stage: Literal["statement", "proof"]


class CanonicalDeclFileView(StrictModel):
    node_path: str
    decl_name: str
    stage: Literal["statement", "proof"]
    module: str
    changed: bool
    line_count: int
    content: str
    content_sha256: str
    summary: str


class OperatorDeclOwnedLeanFileView(StrictModel):
    node_path: str
    decl_name: str
    revision: int
    stage: Literal["statement", "proof"]
    module: str
    lean_decl_name: str | None = None
    visibility: Literal["public", "private"]
    source: Literal["physical_current", "captured_revision"]
    content: str
    line_count: int
    summary: str


class OperatorSafeFormalApplyView(StrictModel):
    node_path: str
    decl_name: str
    revision: int
    state: DeclState
    stage: Literal["statement", "proof"]
    module: str
    lean_decl_name: str
    build: ModuleBuildView
    revision_digest: str
    capture_summary: str
    projection_summary: str
    summary: str


class OperatorProjectionRepairActionView(StrictModel):
    action: str
    status: str
    changed: bool
    summary: str


class OperatorProjectionRepairView(StrictModel):
    scope: str
    changed: bool
    actions: list[OperatorProjectionRepairActionView] = Field(default_factory=list)
    summary: str


def _safe_formal_apply_view(value: SafeFormalApplyView) -> OperatorSafeFormalApplyView:
    return OperatorSafeFormalApplyView(
        node_path=value.node_path,
        decl_name=value.decl_name,
        revision=value.revision,
        state=value.state,
        stage=value.stage,
        module=value.module,
        lean_decl_name=value.lean_decl_name,
        build=value.build,
        revision_digest=value.revision_digest,
        capture_summary=value.capture_summary,
        projection_summary=value.projection_summary,
        summary=value.summary,
    )


def _projection_repair_view(value: ProjectionRepairView) -> OperatorProjectionRepairView:
    return OperatorProjectionRepairView(
        scope=value.scope,
        changed=value.changed,
        actions=[
            OperatorProjectionRepairActionView(
                action=action.action,
                status=action.status,
                changed=action.changed,
                summary=action.summary,
            )
            for action in value.actions
        ],
        summary=value.summary,
    )


def _decl_owned_file_view(value: DeclOwnedLeanFileView) -> OperatorDeclOwnedLeanFileView:
    return OperatorDeclOwnedLeanFileView(
        node_path=value.node_path,
        decl_name=value.decl_name,
        revision=value.revision,
        stage=value.stage,
        module=value.module,
        lean_decl_name=value.lean_decl_name,
        visibility=value.visibility,
        source=value.source,
        content=value.content,
        line_count=value.line_count,
        summary=value.summary,
    )


class _PathFreeExecutor:
    """Common envelope projection for every Decl/Projection operation."""

    def __init__(self, delegate: OperatorExecutionService) -> None:
        self.delegate = delegate

    def execute(self, *args: Any, **kwargs: Any) -> ServiceResult:
        return project_operator_result(self.delegate.execute(*args, **kwargs))


class DeclProjectionOperator:
    """Fixed operation set; callers cannot choose Service methods or internal policy."""

    def __init__(self, executor: OperatorExecutionService) -> None:
        self.executor = _PathFreeExecutor(executor)

    def ensure_strategy(self, repo_key: str, request: StrategyInput) -> ServiceResult:
        return self.executor.execute(
            repo_key,
            MUTATE_DECL,
            lambda ctx: ctx.runtime.decl_graph.ensure_open_strategy_view(
                ctx.repo_root,
                node_path=request.node_path,
                objective=request.objective,
                rationale=request.rationale,
            ),
        )

    def list_strategies(self, repo_key: str, request: NodeInput) -> ServiceResult:
        return self.executor.execute(repo_key, READ_DECL, lambda ctx: ctx.runtime.decl_graph.list_strategy_views(ctx.repo_root, node_path=request.node_path))

    def close_strategy(self, repo_key: str, request: StrategyCloseInput) -> ServiceResult:
        return self.executor.execute(
            repo_key,
            MUTATE_DECL,
            lambda ctx: ctx.runtime.decl_graph.close_strategy_view(ctx.repo_root, **request.model_dump()),
        )

    def create_round(self, repo_key: str, request: RoundInput) -> ServiceResult:
        return self.executor.execute(
            repo_key,
            MUTATE_DECL,
            lambda ctx: ctx.runtime.decl_graph.create_round_draft_view(ctx.repo_root, **request.model_dump()),
        )

    def create_round_with_decl_drafts(self, repo_key: str, request: RoundBatchInput) -> ServiceResult:
        return self.executor.execute(
            repo_key,
            MUTATE_DECL,
            lambda ctx: ctx.runtime.decl_graph.create_round_with_decl_drafts(
                ctx.repo_root,
                node_path=request.node_path,
                strategy_id=request.strategy_id,
                objective=request.objective,
                declarations=request.declarations,
            ),
        )

    def start_round(self, repo_key: str, request: RoundIdentityInput) -> ServiceResult:
        return self.executor.execute(
            repo_key,
            MUTATE_DECL,
            lambda ctx: ctx.runtime.decl_graph.start_round(ctx.repo_root, **request.model_dump()),
        )

    def list_rounds(self, repo_key: str, request: NodeInput) -> ServiceResult:
        return self.executor.execute(repo_key, READ_DECL, lambda ctx: ctx.runtime.decl_graph.list_round_views(ctx.repo_root, node_path=request.node_path))

    def create_decl(self, repo_key: str, request: DeclCreateInput) -> ServiceResult:
        return self.executor.execute(
            repo_key,
            MUTATE_DECL,
            lambda ctx: ctx.runtime.decl_graph.create_decl(ctx.repo_root, **request.model_dump()),
        )

    def get_decl(self, repo_key: str, request: DeclIdentityInput) -> ServiceResult:
        return self.executor.execute(
            repo_key,
            READ_DECL,
            lambda ctx: ctx.runtime.decl_graph.get_decl_view(ctx.repo_root, node_path=request.node_path, name=request.decl_name),
        )

    def list_decls(self, repo_key: str, request: NodeInput) -> ServiceResult:
        return self.executor.execute(repo_key, READ_DECL, lambda ctx: ctx.runtime.decl_graph.list_decl_views(ctx.repo_root, node_path=request.node_path))

    def read_decl_lean_file(self, repo_key: str, request: DeclFileReadInput) -> ServiceResult:
        return project_operator_result(
            self.executor.execute(
                repo_key,
                READ_DECL,
                lambda ctx: ctx.runtime.lean_projection.read_decl_owned_lean_file(
                    ctx.repo_root,
                    node_path=request.node_path,
                    decl_name=request.decl_name,
                    revision=request.revision,
                ),
            ),
            _decl_owned_file_view,
        )

    def open_decl_update(self, repo_key: str, request: DeclUpdateInput) -> ServiceResult:
        return self.executor.execute(repo_key, MUTATE_DECL, lambda ctx: self._open_update(ctx, request))

    def mark_decl_delete(self, repo_key: str, request: DeclDeleteInput) -> ServiceResult:
        return self.executor.execute(repo_key, MUTATE_DECL, lambda ctx: self._mark_delete(ctx, request))

    def write_statement_nl(self, repo_key: str, request: NaturalLanguageInput) -> ServiceResult:
        return self.executor.execute(repo_key, MUTATE_DECL, lambda ctx: self._write_nl(ctx, request, stage="statement"))

    def write_proof_nl(self, repo_key: str, request: NaturalLanguageInput) -> ServiceResult:
        return self.executor.execute(repo_key, MUTATE_DECL, lambda ctx: self._write_nl(ctx, request, stage="proof"))

    def revision_digest(self, repo_key: str, request: DeclIdentityInput) -> ServiceResult:
        return self.executor.execute(
            repo_key,
            READ_DECL,
            lambda ctx: ctx.runtime.lean_projection.current_revision_digest(ctx.repo_root, node_path=request.node_path, decl_name=request.decl_name),
        )

    def prepare_statement_formal_file(
        self, repo_key: str, request: DeclIdentityInput
    ) -> ServiceResult:
        return self.executor.execute(
            repo_key,
            APPLY_FORMAL,
            lambda ctx: self._prepare_formal_file(ctx, request, stage="statement"),
        )

    def prepare_proof_formal_file(
        self, repo_key: str, request: DeclIdentityInput
    ) -> ServiceResult:
        return self.executor.execute(
            repo_key,
            APPLY_FORMAL,
            lambda ctx: self._prepare_formal_file(ctx, request, stage="proof"),
        )

    def apply_statement_formal_code(self, repo_key: str, request: FormalApplyInput) -> ServiceResult:
        return project_operator_result(
            self.executor.execute(repo_key, APPLY_FORMAL, lambda ctx: self._apply_formal(ctx, request, stage="statement")),
            _safe_formal_apply_view,
        )

    def apply_proof_formal_code(self, repo_key: str, request: FormalApplyInput) -> ServiceResult:
        return project_operator_result(
            self.executor.execute(repo_key, APPLY_FORMAL, lambda ctx: self._apply_formal(ctx, request, stage="proof")),
            _safe_formal_apply_view,
        )

    def gate_and_advance_stage(self, repo_key: str, request: StageGateInput) -> ServiceResult:
        return self.executor.execute(
            repo_key,
            MUTATE_DECL,
            lambda ctx: ctx.runtime.decl_graph.gate_and_advance_round_stage(
                ctx.repo_root,
                node_path=request.node_path,
                round_id=request.round_id,
                stage=request.stage,
                target_decl_names=request.target_decl_names,
                review=request.review,
                retry_count=request.retry_count,
                max_retries=request.max_retries,
            ),
        )

    def audit_round_final(self, repo_key: str, request: RoundIdentityInput) -> ServiceResult:
        return self.executor.execute(
            repo_key,
            READ_DECL,
            lambda ctx: ctx.runtime.decl_graph.audit_round_final(ctx.repo_root, **request.model_dump()),
        )

    def closeout_round(self, repo_key: str, request: RoundCloseoutInput) -> ServiceResult:
        return self.executor.execute(
            repo_key,
            MUTATE_DECL,
            lambda ctx: ctx.runtime.decl_graph.closeout_round_by_plan(
                ctx.repo_root,
                **request.model_dump(),
            ),
        )

    def record_round_execution(self, repo_key: str, request: RoundExecutionInput) -> ServiceResult:
        return self.executor.execute(
            repo_key,
            MUTATE_DECL,
            lambda ctx: ctx.runtime.decl_graph.record_round_execution_result(
                ctx.repo_root,
                **request.model_dump(),
            ),
        )

    def refresh_node_projection(self, repo_key: str, request: NodeInput) -> ServiceResult:
        return project_operator_result(self.executor.execute(
            repo_key,
            MUTATE_DECL,
            lambda ctx: ctx.runtime.lean_projection.refresh_node_projection(ctx.repo_root, node_path=request.node_path),
        ), _projection_repair_view)

    def check_projection_sync(self, repo_key: str, request: ProjectionSyncInput) -> ServiceResult:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_DECL,
            lambda ctx: ctx.runtime.lean_projection.check_decl_file_snapshot_sync(
                ctx.repo_root,
                node_path=request.node_path,
                decl_name=request.decl_name,
                stage=request.stage,
            ),
        ), operator_gate_view)

    @staticmethod
    def _require_revision(ctx: OperatorExecutionContext, request: DeclRevisionInput) -> ServiceResult:
        current = ctx.runtime.decl_graph.get_decl(ctx.repo_root, node_path=request.node_path, name=request.decl_name)
        if not current.ok or current.value is None:
            return ctx.runtime.foundation.fail(current.issues)
        if current.value.current_revision != request.expected_revision:
            return ctx.runtime.foundation.fail(
                ctx.runtime.foundation.issue(
                    "operator_decl_revision_stale",
                    "Declaration revision changed before the operator mutation.",
                    object_ref=f"{request.node_path}:{request.decl_name}",
                    current=str(current.value.current_revision),
                    expected=str(request.expected_revision),
                )
            )
        return ctx.runtime.foundation.ok(current.value)

    def _open_update(self, ctx: OperatorExecutionContext, request: DeclUpdateInput) -> ServiceResult:
        checked = self._require_revision(ctx, request)
        if not checked.ok:
            return checked
        return ctx.runtime.decl_graph.open_decl_update(
            ctx.repo_root,
            node_path=request.node_path,
            round_id=request.round_id,
            name=request.decl_name,
            objective=request.objective,
            target_state=request.target_state,
            base_revision=request.base_revision,
            reset_to_state=request.reset_to_state,
            require_target_state_satisfied=request.require_target_state_satisfied,
        )

    def _mark_delete(self, ctx: OperatorExecutionContext, request: DeclDeleteInput) -> ServiceResult:
        checked = self._require_revision(ctx, request)
        if not checked.ok:
            return checked
        return ctx.runtime.decl_graph.mark_decl_delete(
            ctx.repo_root,
            node_path=request.node_path,
            round_id=request.round_id,
            name=request.decl_name,
            objective=request.objective,
        )

    def _write_nl(self, ctx: OperatorExecutionContext, request: NaturalLanguageInput, *, stage: Literal["statement", "proof"]) -> ServiceResult:
        checked = self._require_revision(ctx, request)
        if not checked.ok:
            return checked
        kwargs = dict(
            node_path=request.node_path,
            round_id=request.round_id,
            decl_name=request.decl_name,
            nl=request.text,
            origin=list(request.origins),
            deps=list(request.deps),
        )
        if stage == "statement":
            return ctx.runtime.decl_graph.write_statement_nl_typed(ctx.repo_root, **kwargs)
        return ctx.runtime.decl_graph.write_proof_nl_typed(ctx.repo_root, **kwargs)

    def _apply_formal(self, ctx: OperatorExecutionContext, request: FormalApplyInput, *, stage: Literal["statement", "proof"]) -> ServiceResult:
        checked = self._require_revision(ctx, request)
        if not checked.ok:
            return checked
        return ctx.runtime.lean_projection.apply_formal_code(
            ctx.repo_root,
            node_path=request.node_path,
            decl_name=request.decl_name,
            stage=stage,
            lean_code=request.lean_code,
            expected_revision=request.expected_revision,
            expected_state=request.expected_state,
            expected_revision_digest=request.expected_revision_digest,
        )

    @staticmethod
    def _prepare_formal_file(
        ctx: OperatorExecutionContext,
        request: DeclIdentityInput,
        *,
        stage: Literal["statement", "proof"],
    ) -> ServiceResult:
        if stage == "statement":
            prepared = ctx.runtime.lean_projection.prepare_statement_formal_stage_file(
                ctx.repo_root,
                node_path=request.node_path,
                decl_name=request.decl_name,
            )
        else:
            prepared = ctx.runtime.lean_projection.prepare_proof_formal_stage_file(
                ctx.repo_root,
                node_path=request.node_path,
                decl_name=request.decl_name,
            )
        if not prepared.ok or prepared.value is None:
            return ctx.runtime.foundation.fail(prepared.issues)
        try:
            content = Path(prepared.value.path).read_text(encoding="utf-8")
        except OSError as exc:
            return ctx.runtime.foundation.fail(
                ctx.runtime.foundation.issue(
                    "operator_canonical_decl_file_read_failed",
                    f"Failed to read the prepared canonical declaration file: {exc}",
                    object_ref=f"{request.node_path}:{request.decl_name}",
                )
            )
        return ctx.runtime.foundation.ok(
            CanonicalDeclFileView(
                node_path=request.node_path,
                decl_name=request.decl_name,
                stage=stage,
                module=prepared.value.module,
                changed=prepared.value.changed,
                line_count=prepared.value.line_count,
                content=content,
                content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                summary=prepared.value.summary,
            ),
            warnings=prepared.issues,
        )


__all__ = [name for name in globals() if name.endswith(("Input", "View")) or name in {"DeclProjectionOperator", "READ_DECL", "MUTATE_DECL", "APPLY_FORMAL"}]
