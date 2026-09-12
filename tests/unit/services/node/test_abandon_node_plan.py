"""Withdraw unexecuted plans without claiming mathematical completion."""
import json

import pytest

from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.foundation import WriteMode
from lean_constellation.services.node import NodeContract, NodeLifecycle
from tests.unit_services_helpers import make_runtime


def _plan(root, *, kind="content"):
    runtime = make_runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(root).ok
    args = dict(path="Main.Topic", goal="Topic", boundary="Topic boundary")
    if kind == "content":
        result = runtime.node.create_content_node(root, **args, objective="Develop topic", success_criteria="Proved")
    else:
        result = runtime.node.create_scope_node(root, **args)
    assert result.ok
    return runtime, result.value


@pytest.mark.parametrize("kind", ["content", "scope"])
def test_abandon_initial_plan_preserves_draft_and_recreates_scope(tmp_path, kind):
    runtime, old = _plan(tmp_path, kind=kind)
    store = runtime.node.node_tree.node_store
    draft = store.contract_path(tmp_path, node_id=old.node_id, version=1)
    before = draft.read_bytes()
    assert "open_contract" in runtime.node.preview_delete_node(tmp_path, path=old.path).value.blocking_reasons
    assert not runtime.node.mark_node_deleted(tmp_path, path=old.path, reason="Ordinary delete").ok
    assert runtime.node.preview_abandon_node_plan(tmp_path, path=old.path).value.deletable
    assert runtime.node.abandon_node_plan(tmp_path, path=old.path, reason="Split unexecuted plan").ok
    assert draft.read_bytes() == before
    assert store.load_node_by_id(tmp_path, node_id=old.node_id).value.lifecycle == NodeLifecycle.OBSOLETE
    new = runtime.node.create_scope_node(tmp_path, path=old.path, goal="Grouped topic", boundary="Grouped topic")
    assert new.ok and new.value.node_id != old.node_id
    assert runtime.node.create_content_node(tmp_path, path="Main.Topic.Part", goal="Part", boundary="Part", objective="Prove", success_criteria="Proved").ok
    assert store.resolve_active_node(tmp_path, path=old.path).value.node_id == new.value.node_id


def test_abandon_requires_reason_and_rechecks_children(tmp_path):
    runtime, node = _plan(tmp_path, kind="scope")
    assert not runtime.node.abandon_node_plan(tmp_path, path=node.path, reason=" ").ok
    assert runtime.node.preview_abandon_node_plan(tmp_path, path=node.path).value.deletable
    assert runtime.node.create_scope_node(tmp_path, path="Main.Topic.Child", goal="Child", boundary="Child").ok
    assert "active_children" in runtime.node.preview_abandon_node_plan(tmp_path, path=node.path).value.blocking_reasons
    assert not runtime.node.abandon_node_plan(tmp_path, path=node.path, reason="Stale preview").ok
    assert "root_main" in runtime.node.preview_abandon_node_plan(tmp_path, path="Main").value.blocking_reasons


@pytest.mark.parametrize("successor", [False, True])
def test_abandon_rejects_committed_or_successor_contract(tmp_path, successor):
    runtime, node = _plan(tmp_path, kind="scope")
    assert runtime.node.commit_scope_contract(tmp_path, scope_path=node.path, summary="Empty scope fixture").ok
    if successor:
        assert runtime.node.contract.ensure_open_contract(tmp_path, node_path=node.path).ok
    assert "node_plan_not_initial_open" in runtime.node.preview_abandon_node_plan(tmp_path, path=node.path).value.blocking_reasons
    assert not runtime.node.abandon_node_plan(tmp_path, path=node.path, reason="Cannot withdraw").ok


def test_abandon_enumerates_contract_history_not_just_metadata(tmp_path):
    runtime, node = _plan(tmp_path, kind="scope")
    path = runtime.node.node_tree.node_store.contract_path(tmp_path, node_id=node.node_id, version=1)
    contract = json.loads(path.read_text())
    contract['version'] = 2
    path.with_name('2.json').write_text(json.dumps(contract))
    assert "node_plan_not_initial_open" in runtime.node.preview_abandon_node_plan(tmp_path, path=node.path).value.blocking_reasons


@pytest.mark.parametrize("relative", ["decls/Removed/decl.json", "strategies/closed.json", "rounds/finished.json"])
def test_abandon_rejects_any_execution_record_even_on_scope(tmp_path, relative):
    runtime, node = _plan(tmp_path, kind="scope")
    # Scope has no DeclGraph loader; any persisted record still disqualifies an empty plan.
    path = runtime.node.node_tree.node_store.decl_graph_dir(tmp_path, node_id=node.node_id) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{}')
    assert "node_plan_has_execution_history" in runtime.node.preview_abandon_node_plan(tmp_path, path=node.path).value.blocking_reasons
    assert not runtime.node.abandon_node_plan(tmp_path, path=node.path, reason="Not empty").ok


def test_abandon_empty_graph_index_is_not_execution_history(tmp_path):
    runtime, node = _plan(tmp_path)
    assert runtime.decl_graph.ensure_decl_graph(tmp_path, node_path=node.path).ok
    assert runtime.node.preview_abandon_node_plan(tmp_path, path=node.path).value.deletable


def test_abandon_preserves_inbound_public_reference_guard(tmp_path):
    runtime, node = _plan(tmp_path)
    store = runtime.node.node_tree.node_store
    main = store.resolve_active_node(tmp_path, path="Main").value
    path = store.contract_path(tmp_path, node_id=main.node_id, version=1)
    contract = runtime.foundation.store.read_json(path, NodeContract).value
    contract.exports = [DeclRef(node=node.path, name="futureResult", revision=1)]
    assert runtime.foundation.store.write_json_atomic(path, contract, mode=WriteMode.UPDATE_EXISTING).ok
    assert "current_inbound_refs" in runtime.node.preview_abandon_node_plan(tmp_path, path=node.path).value.blocking_reasons
    assert not runtime.node.abandon_node_plan(tmp_path, path=node.path, reason="Referenced").ok


def test_abandon_preserves_release_protection(tmp_path):
    from tests.unit.services.test_release_mutation_guards import _prepare_release_repo, _publish_latest
    runtime, versions = _prepare_release_repo(tmp_path)
    _publish_latest(runtime, tmp_path, versions)
    assert "release_protected" in runtime.node.preview_abandon_node_plan(tmp_path, path="Main.Results").value.blocking_reasons
    assert not runtime.node.abandon_node_plan(tmp_path, path="Main.Results", reason="Published").ok


def test_abandon_rejects_closed_strategy_on_content(tmp_path):
    runtime, node = _plan(tmp_path)
    strategy = runtime.decl_graph.ensure_open_strategy(tmp_path, node_path=node.path, objective="Past work").value
    graph = runtime.node.node_tree.node_store.decl_graph_dir(tmp_path, node_id=node.node_id)
    path = graph / 'strategies' / (strategy.strategy_id + '.json')
    value = json.loads(path.read_text())
    value['status'] = 'closed'
    path.write_text(json.dumps(value))
    result = runtime.node.preview_abandon_node_plan(tmp_path, path=node.path)
    assert result.ok
    assert 'node_plan_has_execution_history' in result.value.blocking_reasons
    assert 'open_decl_graph_work' not in result.value.blocking_reasons
