from __future__ import annotations

import pytest
from pydantic import ValidationError

from lean_constellation.app.operator_data.api import OperatorDataApi
from lean_constellation.app.operator_data.decl_projection import (
    FormalApplyInput,
    RoundBatchInput,
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
        StrategyInput(node_path="Main.Core", objective="Declare the core value."),
    )
    assert strategy.ok and strategy.value is not None

    created = api.decl_projection.create_round_with_decl_drafts(
        "MainRepo",
        RoundBatchInput(
            node_path="Main.Core",
            strategy_id=strategy.value.strategy_id,
            objective="Create the value draft.",
            declarations=[
                DeclDraftSpec(
                    name="coreValue",
                    kind="definition",
                    objective="Define the core value.",
                    summary="A core declaration.",
                    public=True,
                )
            ],
        ),
    )

    assert created.ok and created.value is not None, created.issues
    assert [item.decl_name for item in created.value.revision_refs] == ["coreValue"]
