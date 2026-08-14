"""Fixed HTTP route declarations for DeclGraph/Projection integration wiring."""

from __future__ import annotations

from dataclasses import dataclass

from lean_constellation.app.operator_data.decl_projection import (
    DeclCreateInput,
    DeclDeleteInput,
    DeclFileReadInput,
    DeclIdentityInput,
    DeclRestoreInput,
    DeclUpdateInput,
    FormalApplyInput,
    NaturalLanguageInput,
    NodeInput,
    ProjectionSyncInput,
    RoundCloseoutInput,
    RoundExecutionInput,
    RoundBatchInput,
    RoundIdentityInput,
    RoundInput,
    StageGateInput,
    StrategyCloseInput,
    StrategyInput,
)
from lean_constellation.app.operator_data.common import OperatorInputModel


@dataclass(frozen=True, slots=True)
class DeclProjectionRoute:
    method: str
    path: str
    input_model: type[OperatorInputModel]
    handler_name: str


DECL_PROJECTION_ROUTES = (
    DeclProjectionRoute("POST", "/repos/{repo_key}/decl/strategies", StrategyInput, "ensure_strategy"),
    DeclProjectionRoute("GET", "/repos/{repo_key}/decl/strategies", NodeInput, "list_strategies"),
    DeclProjectionRoute("POST", "/repos/{repo_key}/decl/strategies/close", StrategyCloseInput, "close_strategy"),
    DeclProjectionRoute("POST", "/repos/{repo_key}/decl/rounds", RoundInput, "create_round"),
    DeclProjectionRoute("POST", "/repos/{repo_key}/decl/rounds/with-declarations", RoundBatchInput, "create_round_with_decl_drafts"),
    DeclProjectionRoute("POST", "/repos/{repo_key}/decl/rounds/start", RoundIdentityInput, "start_round"),
    DeclProjectionRoute("GET", "/repos/{repo_key}/decl/rounds", NodeInput, "list_rounds"),
    DeclProjectionRoute("POST", "/repos/{repo_key}/decl/declarations", DeclCreateInput, "create_decl"),
    DeclProjectionRoute("GET", "/repos/{repo_key}/decl/declarations/item", DeclIdentityInput, "get_decl"),
    DeclProjectionRoute("GET", "/repos/{repo_key}/decl/declarations", NodeInput, "list_decls"),
    DeclProjectionRoute("GET", "/repos/{repo_key}/decl/declarations/file", DeclFileReadInput, "read_decl_lean_file"),
    DeclProjectionRoute("POST", "/repos/{repo_key}/decl/declarations/update", DeclUpdateInput, "open_decl_update"),
    DeclProjectionRoute("POST", "/repos/{repo_key}/decl/declarations/restore", DeclRestoreInput, "restore_decl_revision"),
    DeclProjectionRoute("POST", "/repos/{repo_key}/decl/declarations/delete", DeclDeleteInput, "delete_decls"),
    DeclProjectionRoute("POST", "/repos/{repo_key}/decl/stages/statement-nl", NaturalLanguageInput, "write_statement_nl"),
    DeclProjectionRoute("POST", "/repos/{repo_key}/decl/stages/proof-nl", NaturalLanguageInput, "write_proof_nl"),
    DeclProjectionRoute("GET", "/repos/{repo_key}/projection/revision-digest", DeclIdentityInput, "revision_digest"),
    DeclProjectionRoute("POST", "/repos/{repo_key}/projection/statement/prepare", DeclIdentityInput, "prepare_statement_formal_file"),
    DeclProjectionRoute("POST", "/repos/{repo_key}/projection/proof/prepare", DeclIdentityInput, "prepare_proof_formal_file"),
    DeclProjectionRoute("POST", "/repos/{repo_key}/projection/statement", FormalApplyInput, "apply_statement_formal_code"),
    DeclProjectionRoute("POST", "/repos/{repo_key}/projection/proof", FormalApplyInput, "apply_proof_formal_code"),
    DeclProjectionRoute("POST", "/repos/{repo_key}/decl/stages/gate", StageGateInput, "gate_and_advance_stage"),
    DeclProjectionRoute("GET", "/repos/{repo_key}/decl/rounds/audit", RoundIdentityInput, "audit_round_final"),
    DeclProjectionRoute("POST", "/repos/{repo_key}/decl/rounds/closeout", RoundCloseoutInput, "closeout_round"),
    DeclProjectionRoute(
        "POST",
        "/repos/{repo_key}/decl/rounds/execution-result",
        RoundExecutionInput,
        "record_round_execution",
    ),
    DeclProjectionRoute("POST", "/repos/{repo_key}/projection/refresh", NodeInput, "refresh_node_projection"),
    DeclProjectionRoute("GET", "/repos/{repo_key}/projection/sync", ProjectionSyncInput, "check_projection_sync"),
)


__all__ = ["DECL_PROJECTION_ROUTES", "DeclProjectionRoute"]
