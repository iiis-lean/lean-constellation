from __future__ import annotations

import pytest
from pydantic import ValidationError

from lean_constellation.app.operator_data.api import OperatorDataApi
from lean_constellation.app.operator_data.decl_projection import (
    FormalApplyInput,
    NodeInput,
    RoundBatchInput,
    RoundInput,
    StrategyInput,
)
from lean_constellation.app.operator_data.node import (
    CreateContentNodeInput,
    CreateScopeNodeInput,
)
from lean_constellation.services.decl_graph import DeclDraftSpec

from tests.unit.app.operator_data._helpers import make_registry, make_repo


def test_formal_apply_input_requires_business_stale_guards_and_rejects_forged_check() -> None:
    payload = {
        "node_path": "Main.Topic.Core",
        "round_id": "round-1",
        "decl_name": "main_result",
        "expected_revision": 1,
        "expected_state": "planned",
        "expected_revision_digest": "abc",
        "lean_code": "theorem main_result : True := by sorry",
    }
    parsed = FormalApplyInput.model_validate(payload)
    assert parsed.expected_revision == 1
    with pytest.raises(ValidationError):
        FormalApplyInput.model_validate({**payload, "lean_check": {"status": "passed"}})
    with pytest.raises(ValidationError):
        FormalApplyInput.model_validate({**payload, "repo_root": "/tmp/repo"})
    with pytest.raises(ValidationError):
        FormalApplyInput.model_validate({**payload, "skip_check": True})


def test_round_batch_preserves_typed_decl_drafts_at_service_boundary(tmp_path) -> None:  # noqa: ANN001
    workspace = tmp_path / "workspace"
    repo_root = make_repo(workspace)
    registry = make_registry(workspace)
    api = OperatorDataApi(registry)
    assert api.node.create_scope_node(
        "MainRepo",
        CreateScopeNodeInput(path="Main", goal="Root.", boundary="Root."),
    ).ok
    assert api.node.create_content_node(
        "MainRepo",
        CreateContentNodeInput(
            path="Main.Core",
            goal="Core.",
            boundary="Core.",
            objective="Declare one value.",
            success_criteria="The declaration draft exists.",
            expected_parent_contract_version=1,
        ),
    ).ok
    strategy = api.decl_projection.ensure_strategy(
        "MainRepo",
        StrategyInput(
            node_path="Main.Core",
            objective="Declare the core value.",
            execution_constraints="Use one small declaration round.",
        ),
    )
    assert strategy.ok and strategy.value is not None

    created = api.decl_projection.create_round_with_decl_drafts(
        "MainRepo",
        RoundBatchInput(
            node_path="Main.Core",
            strategy_id=strategy.value.strategy_id,
            objective="Create the value draft.",
            execution_constraints="Keep this batch declaration-only.",
            declarations=[
                DeclDraftSpec(
                    name="coreValue",
                    kind="definition",
                    objective="Define the core value.",
                    execution_constraints="Do not add helper declarations.",
                    summary="A core declaration.",
                    public=True,
                )
            ],
        ),
    )

    assert created.ok and created.value is not None, created.issues
    assert [item.decl_name for item in created.value.revision_refs] == ["coreValue"]
    runtime = registry.get_or_load_paused("MainRepo", refresh_homes=False)
    assert runtime.ok and runtime.value is not None
    round_record = runtime.value.decl_graph.get_round(
        repo_root,
        node_path="Main.Core",
        round_id=created.value.round_id,
    )
    revision = runtime.value.decl_graph.get_decl_revision(
        repo_root,
        node_path="Main.Core",
        name="coreValue",
        revision=1,
    )
    assert round_record.ok and round_record.value is not None
    assert round_record.value.execution_constraints == "Keep this batch declaration-only."
    assert revision.ok and revision.value is not None and revision.value.change is not None
    assert revision.value.change.execution_constraints == "Do not add helper declarations."


def test_operator_strategy_and_round_views_preserve_exact_identity(tmp_path) -> None:  # noqa: ANN001
    workspace = tmp_path / "workspace"
    make_repo(workspace)
    api = OperatorDataApi(make_registry(workspace))
    assert api.node.create_scope_node(
        "MainRepo",
        CreateScopeNodeInput(path="Main", goal="Root.", boundary="Root."),
    ).ok
    assert api.node.create_content_node(
        "MainRepo",
        CreateContentNodeInput(
            path="Main.Core",
            goal="Core.",
            boundary="Core.",
            objective="Declare one value.",
            success_criteria="The declaration draft exists.",
            expected_parent_contract_version=1,
        ),
    ).ok
    strategy = api.decl_projection.ensure_strategy(
        "MainRepo",
        StrategyInput(
            node_path="Main.Core",
            objective="Declare the core value.",
            execution_constraints="Keep the route bottom-up.",
        ),
    )
    assert strategy.ok and strategy.value is not None
    assert strategy.value.strategy_id
    assert strategy.value.execution_constraints == "Keep the route bottom-up."

    round_record = api.decl_projection.create_round(
        "MainRepo",
        RoundInput(
            node_path="Main.Core",
            strategy_id=strategy.value.strategy_id,
            objective="Create the value draft.",
            execution_constraints="Do not advance beyond this batch.",
        ),
    )
    assert round_record.ok and round_record.value is not None
    assert round_record.value.round_id
    assert round_record.value.strategy_id == strategy.value.strategy_id
    assert round_record.value.execution_constraints == "Do not advance beyond this batch."

    strategies = api.decl_projection.list_strategies(
        "MainRepo",
        NodeInput(node_path="Main.Core"),
    )
    rounds = api.decl_projection.list_rounds(
        "MainRepo",
        NodeInput(node_path="Main.Core"),
    )
    assert strategies.ok and strategies.value is not None
    assert rounds.ok and rounds.value is not None
    assert strategies.value[0].strategy_id == strategy.value.strategy_id
    assert rounds.value[0].round_id == round_record.value.round_id
