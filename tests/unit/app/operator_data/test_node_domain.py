from __future__ import annotations

from pathlib import Path

from lean_constellation.app.operator_data.node import (
    CommitContractInput,
    CreateContentNodeInput,
    CreateScopeNodeInput,
    DeleteNodeInput,
    MathlibModuleMutationInput,
    NodeOperatorApi,
    NodePathInput,
    UpdateContractTextInput,
)
from lean_constellation.app.operator_data.node_http import NodeHttpHandlers
from lean_constellation.services.node.contract_fields import MathlibUseActor

from tests.unit.app.operator_data._helpers import make_registry, make_repo


def _make_api(tmp_path: Path) -> tuple[NodeOperatorApi, Path]:
    workspace = tmp_path / "workspace"
    repo_root = make_repo(workspace)
    api = NodeOperatorApi(make_registry(workspace))
    root = api.create_scope_node(
        "MainRepo",
        CreateScopeNodeInput(
            path="Main",
            goal="Root goal.",
            boundary="Root boundary.",
            objective="Organize the repo.",
            success_criteria="Public boundary is complete.",
        ),
    )
    assert root.ok
    return api, repo_root


def test_node_operator_create_read_stale_version_and_fixed_mathlib_actor(tmp_path: Path) -> None:
    api, repo_root = _make_api(tmp_path)
    created = api.create_content_node(
        "MainRepo",
        CreateContentNodeInput(
            path="Main.Core",
            goal="Core goal.",
            boundary="Core boundary.",
            objective="Build core.",
            success_criteria="Core is ready.",
            expected_parent_contract_version=1,
        ),
    )
    assert created.ok
    loaded = api.execution.registry.get_or_load_paused("MainRepo", refresh_homes=False)
    assert loaded.ok and loaded.value is not None
    assert loaded.value.mathlib.upsert_mathlib_module_entry(
        repo_root,
        module="Mathlib.Data.Finset.Basic",
    ).ok

    stale = api.update_contract_text(
        "MainRepo",
        UpdateContractTextInput(
            node_path="Main.Core",
            expected_contract_version=2,
            objective="This must not be written.",
        ),
    )
    assert not stale.ok
    assert stale.issues[0].kind == "operator_contract_version_stale"

    added = api.add_mathlib_module(
        "MainRepo",
        MathlibModuleMutationInput(
            node_path="Main.Core",
            expected_contract_version=1,
            module="Mathlib.Data.Finset.Basic",
            reason="Finite sums.",
        ),
    )
    assert added.ok and added.value is not None
    module = added.value.added_modules[0]
    assert module.added_by == MathlibUseActor.OPERATOR

    loaded = api.get_node("MainRepo", NodePathInput(node_path="Main.Core"))
    assert loaded.ok and loaded.value is not None
    assert loaded.value.path == "Main.Core"


def test_node_http_strict_identity_and_direct_parity(tmp_path: Path) -> None:
    api, _ = _make_api(tmp_path)
    http = NodeHttpHandlers(api)

    direct = api.get_node("MainRepo", NodePathInput(node_path="Main"))
    mapped = http.get_node("MainRepo", {"node_path": "Main"})

    assert direct.ok and direct.value is not None
    assert mapped["ok"] is True
    assert mapped["value"]["path"] == direct.value.path
    forbidden = http.get_node("MainRepo", {"node_path": "Main", "repo_key": "Other"})
    assert forbidden["ok"] is False
    assert forbidden["issues"][0]["kind"] == "operator_request_validation_failed"
    forged = http.add_mathlib_module(
        "MainRepo",
        {
            "node_path": "Main",
            "expected_contract_version": 1,
            "module": "Mathlib.Data.Nat.Basic",
            "reason": None,
            "actor": "coordinator",
        },
    )
    assert forged["ok"] is False


def test_node_delete_requires_stable_preview_identity_and_rechecks(tmp_path: Path, monkeypatch) -> None:
    api, _ = _make_api(tmp_path)
    created = api.create_content_node(
        "MainRepo",
        CreateContentNodeInput(
            path="Main.Private",
            goal="Private goal.",
            boundary="Private boundary.",
            objective="Create a disposable node.",
            success_criteria="The node can be removed.",
            expected_parent_contract_version=1,
        ),
    )
    assert created.ok
    committed = api.commit_content_contract(
        "MainRepo",
        CommitContractInput(
            node_path="Main.Private",
            expected_contract_version=1,
            summary="Private node completed.",
        ),
    )
    assert committed.ok
    preview = api.preview_delete_node("MainRepo", NodePathInput(node_path="Main.Private"))
    assert preview.ok and preview.value is not None and preview.value.deletable
    assert preview.value.impact_identity.startswith("node_delete_")

    stale = api.delete_node(
        "MainRepo",
        DeleteNodeInput(
            path="Main.Private",
            reason="Remove private node.",
            expected_impact_identity="node_delete_stale",
        ),
    )
    assert not stale.ok
    assert stale.issues[0].kind == "operator_node_delete_preview_stale"

    called = False
    original = api._recheck_runtime

    def observing_recheck(ctx):  # noqa: ANN001, ANN202
        nonlocal called
        called = True
        return original(ctx)

    monkeypatch.setattr(api, "_recheck_runtime", observing_recheck)
    deleted = api.delete_node(
        "MainRepo",
        DeleteNodeInput(
            path="Main.Private",
            reason="Remove private node.",
            expected_impact_identity=preview.value.impact_identity,
        ),
    )
    assert deleted.ok
    assert called is True
    missing = api.get_node("MainRepo", NodePathInput(node_path="Main.Private"))
    assert not missing.ok


def test_node_delete_fails_closed_if_runtime_history_appears_during_recheck(
    tmp_path: Path, monkeypatch
) -> None:
    api, _ = _make_api(tmp_path)
    assert api.create_content_node(
        "MainRepo",
        CreateContentNodeInput(
            path="Main.Private",
            goal="Private goal.",
            boundary="Private boundary.",
            objective="Create a disposable node.",
            success_criteria="The node can be removed.",
            expected_parent_contract_version=1,
        ),
    ).ok
    assert api.commit_content_contract(
        "MainRepo",
        CommitContractInput(
            node_path="Main.Private",
            expected_contract_version=1,
            summary="Private node completed.",
        ),
    ).ok
    preview = api.preview_delete_node("MainRepo", NodePathInput(node_path="Main.Private"))
    assert preview.ok and preview.value is not None
    calls = 0

    def changing_history(record):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return calls >= 3

    monkeypatch.setattr(api.execution.registry, "runtime_history_exists", changing_history)
    deleted = api.delete_node(
        "MainRepo",
        DeleteNodeInput(
            path="Main.Private",
            reason="Remove private node.",
            expected_impact_identity=preview.value.impact_identity,
        ),
    )
    assert not deleted.ok
    assert deleted.issues[0].kind == "operator_repo_runtime_history_changed"
    assert api.get_node("MainRepo", NodePathInput(node_path="Main.Private")).ok
