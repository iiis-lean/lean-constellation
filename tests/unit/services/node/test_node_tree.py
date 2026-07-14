from tests.unit_services_helpers import make_runtime

from pathlib import Path

from lean_constellation.domain.refs import DeclRef, NodeRef
from lean_constellation.services.foundation import FoundationContext, WriteMode
from lean_constellation.services.node import ContractVersionStatus, NodeContractSnapshot, NodeKind, NodeLifecycle, NodeMetadata
from lean_constellation.services.node.contract_fields import NodeDep, NodeDepActor


def _contract_path(tmp_path: Path, node_path: str, version: int = 1) -> Path:
    runtime = make_runtime()
    node = runtime.node.node_tree.node_store.resolve_active_node(tmp_path, path=node_path)
    assert node.ok and node.value is not None
    return runtime.node.node_tree.node_store.contract_path(tmp_path, node_id=node.value.node_id, version=version)


def _load_contract(tmp_path: Path, node_path: str, version: int = 1) -> NodeContractSnapshot:
    foundation = make_runtime().foundation
    loaded = foundation.store.read_json(_contract_path(tmp_path, node_path, version), NodeContractSnapshot)
    assert loaded.ok
    assert loaded.value is not None
    return loaded.value


def _write_contract(tmp_path: Path, node_path: str, contract: NodeContractSnapshot, version: int = 1) -> None:
    foundation = make_runtime().foundation
    written = foundation.store.write_json_atomic(
        _contract_path(tmp_path, node_path, version),
        contract,
        mode=WriteMode.UPDATE_EXISTING,
    )
    assert written.ok


def test_ensure_root_scope_node_is_idempotent(tmp_path: Path) -> None:
    component = make_runtime().node.node_tree

    created = component.ensure_root_scope_node(tmp_path)
    again = component.ensure_root_scope_node(tmp_path)

    assert created.ok
    assert again.ok
    assert created.value is not None and again.value is not None
    assert created.value.path == "Main"
    assert created.value.node_id == again.value.node_id
    assert created.value.kind == NodeKind.SCOPE


def test_create_scope_and_content_requires_scope_parent(tmp_path: Path) -> None:
    component = make_runtime().node.node_tree

    missing_parent = component.create_scope_node(tmp_path, path="Main.Topic", goal="g", boundary="b")
    assert not missing_parent.ok
    assert missing_parent.issues[0].kind == "node_parent_missing"

    component.ensure_root_scope_node(tmp_path)
    topic = component.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary")
    assert topic.ok
    content = component.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core definitions.",
        success_criteria="Core declarations ready.",
    )
    assert content.ok

    child_under_content = component.create_scope_node(tmp_path, path="Main.Topic.Core.Child", goal="bad", boundary="bad")
    assert not child_under_content.ok
    assert child_under_content.issues[0].kind == "node_parent_not_scope"


def test_create_content_node_requires_objective_and_success_criteria(tmp_path: Path) -> None:
    component = make_runtime().node.node_tree
    assert component.ensure_root_scope_node(tmp_path).ok
    assert component.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok

    missing_objective = component.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="",
        success_criteria="Core ready.",
    )
    assert not missing_objective.ok
    assert missing_objective.issues[0].kind == "node_objective_required"

    missing_success = component.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria=" ",
    )
    assert not missing_success.ok
    assert missing_success.issues[0].kind == "node_success_criteria_required"


def test_node_tree_children_delete_preview_and_soft_delete(tmp_path: Path) -> None:
    component = make_runtime().node.node_tree
    component.ensure_root_scope_node(tmp_path)
    component.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary")
    component.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Ready.",
    )

    tree = component.get_node_tree(tmp_path)
    assert tree.ok
    assert tree.value is not None
    assert [node.path for node in tree.value.nodes] == ["Main", "Main.Topic", "Main.Topic.Core"]

    children = component.list_children(tmp_path, scope_path="Main.Topic")
    assert children.ok
    assert children.value is not None
    assert [node.path for node in children.value] == ["Main.Topic.Core"]

    blocked = make_runtime().node.preview_delete_node(tmp_path, path="Main.Topic")
    assert blocked.ok
    assert blocked.value is not None
    assert blocked.value.deletable is False
    assert blocked.value.affected_children == ["Main.Topic.Core"]

    no_reason = make_runtime().node.mark_node_deleted(tmp_path, path="Main.Topic.Core", reason="")
    assert not no_reason.ok
    assert no_reason.issues[0].kind == "delete_reason_required"

    assert make_runtime().node.commit_content_contract(
        tmp_path, node_path="Main.Topic.Core", summary="Core node complete."
    ).ok
    deleted = make_runtime().node.mark_node_deleted(tmp_path, path="Main.Topic.Core", reason="No longer needed.")
    assert deleted.ok
    core = component.get_node(tmp_path, path="Main.Topic.Core")
    assert not core.ok
    assert core.issues[0].kind == "node_missing"
    nodes = component.node_store.list_nodes(tmp_path)
    assert nodes.ok and nodes.value is not None
    assert any(node.path == "Main.Topic.Core" and node.lifecycle == NodeLifecycle.OBSOLETE for node in nodes.value)


def test_get_node_missing_and_tree_hides_obsolete_nodes(tmp_path: Path) -> None:
    component = make_runtime().node.node_tree
    assert component.ensure_root_scope_node(tmp_path).ok
    assert component.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    assert component.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Ready.",
    ).ok

    missing = component.get_node(tmp_path, path="Main.Topic.Missing")
    assert not missing.ok
    assert missing.issues[0].kind == "node_missing"

    assert make_runtime().node.commit_content_contract(
        tmp_path, node_path="Main.Topic.Core", summary="Core node complete."
    ).ok
    deleted = make_runtime().node.mark_node_deleted(tmp_path, path="Main.Topic.Core", reason="Hide from active tree.")
    assert deleted.ok

    tree = component.get_node_tree(tmp_path)
    assert tree.ok
    assert tree.value is not None
    assert [node.path for node in tree.value.nodes] == ["Main", "Main.Topic"]
    assert tree.value.active_count == 2


def test_list_children_rejects_missing_and_content_nodes(tmp_path: Path) -> None:
    component = make_runtime().node.node_tree
    assert component.ensure_root_scope_node(tmp_path).ok
    assert component.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    assert component.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Ready.",
    ).ok

    missing = component.list_children(tmp_path, scope_path="Main.Missing")
    assert not missing.ok
    assert missing.issues[0].kind == "node_missing"

    content = component.list_children(tmp_path, scope_path="Main.Topic.Core")
    assert not content.ok
    assert content.issues[0].kind == "node_not_scope"


def test_preview_delete_detects_contract_inbound_refs(tmp_path: Path) -> None:
    component = make_runtime().node.node_tree
    assert component.ensure_root_scope_node(tmp_path).ok
    assert component.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    assert component.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Ready.",
    ).ok
    assert component.create_content_node(
        tmp_path,
        path="Main.Topic.Consumer",
        goal="Consumer goal",
        boundary="Consumer boundary",
        objective="Use core.",
        success_criteria="Ready.",
    ).ok
    consumer = _load_contract(tmp_path, "Main.Topic.Consumer")
    consumer.deps.append(
        NodeDep(
            dep_id="dep_core",
            target=NodeRef(repo=None, node="Main.Topic.Core"),
            reason="Need core.",
            added_by=NodeDepActor.COORDINATOR,
        )
    )
    _write_contract(tmp_path, "Main.Topic.Consumer", consumer)
    topic = _load_contract(tmp_path, "Main.Topic")
    topic.exports = [DeclRef(repo=None, node="Main.Topic.Core", name="core_result", revision=1)]
    _write_contract(tmp_path, "Main.Topic", topic)

    preview = make_runtime().node.preview_delete_node(tmp_path, path="Main.Topic.Core")

    assert preview.ok
    assert preview.value is not None
    assert preview.value.deletable is False
    assert "current_inbound_refs" in preview.value.blocking_reasons
    assert "current:Main.Topic.Consumer:deps" in preview.value.inbound_refs
    assert "current:Main.Topic:public" in preview.value.inbound_refs

    blocked = make_runtime().node.mark_node_deleted(tmp_path, path="Main.Topic.Core", reason="Still referenced.")
    assert not blocked.ok
    assert blocked.issues[0].kind == "node_delete_blocked"


def test_runnable_content_candidates_filter_contract_and_max_count(tmp_path: Path) -> None:
    component = make_runtime().node.node_tree
    component.ensure_root_scope_node(tmp_path)
    component.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary")
    component.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Ready.",
    )
    component.create_content_node(
        tmp_path,
        path="Main.Topic.More",
        goal="More goal",
        boundary="More boundary",
        objective="Build more.",
        success_criteria="Ready.",
    )

    candidates = component.list_runnable_content_candidates(tmp_path, max_count=1)
    assert candidates.ok
    assert candidates.value is not None
    assert [node.path for node in candidates.value.candidates] == ["Main.Topic.Core"]
    assert candidates.value.truncated is True


def test_runnable_content_candidates_reports_invalid_and_skipped_reasons(tmp_path: Path) -> None:
    component = make_runtime().node.node_tree
    assert component.ensure_root_scope_node(tmp_path).ok
    assert component.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    assert component.create_content_node(
        tmp_path,
        path="Main.Topic.Ready",
        goal="Ready goal",
        boundary="Ready boundary",
        objective="Build ready.",
        success_criteria="Ready.",
    ).ok
    assert component.create_content_node(
        tmp_path,
        path="Main.Topic.Committed",
        goal="Committed goal",
        boundary="Committed boundary",
        objective="Build committed.",
        success_criteria="Ready.",
    ).ok
    committed = _load_contract(tmp_path, "Main.Topic.Committed")
    committed.status = ContractVersionStatus.COMMITTED
    _write_contract(tmp_path, "Main.Topic.Committed", committed)
    assert component.create_content_node(
        tmp_path,
        path="Main.Topic.MissingObjective",
        goal="Missing objective goal",
        boundary="Missing objective boundary",
        objective="Will be cleared.",
        success_criteria="Ready.",
    ).ok
    missing_objective = _load_contract(tmp_path, "Main.Topic.MissingObjective")
    missing_objective.objective = None
    _write_contract(tmp_path, "Main.Topic.MissingObjective", missing_objective)
    assert component.create_content_node(
        tmp_path,
        path="Main.Topic.MissingDep",
        goal="Missing dep goal",
        boundary="Missing dep boundary",
        objective="Use missing dep.",
        success_criteria="Ready.",
    ).ok
    missing_dep = _load_contract(tmp_path, "Main.Topic.MissingDep")
    missing_dep.deps.append(
        NodeDep(
            dep_id="dep_absent",
            target=NodeRef(repo=None, node="Main.Topic.Absent"),
            reason="Need absent node.",
            added_by=NodeDepActor.COORDINATOR,
        )
    )
    _write_contract(tmp_path, "Main.Topic.MissingDep", missing_dep)

    invalid = component.list_runnable_content_candidates(tmp_path, max_count=0)
    assert not invalid.ok
    assert invalid.issues[0].kind == "max_count_invalid"

    candidates = component.list_runnable_content_candidates(tmp_path, max_count=10)
    assert candidates.ok
    assert candidates.value is not None
    assert [node.path for node in candidates.value.candidates] == ["Main.Topic.Ready"]
    assert "Main.Topic.Committed: contract_not_open" in candidates.value.skipped
    assert "Main.Topic.MissingObjective: objective_missing" in candidates.value.skipped
    assert "Main.Topic.MissingDep: dep_missing:Main.Topic.Absent" in candidates.value.skipped


def test_ensure_root_rejects_existing_wrong_kind(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    ctx = FoundationContext(repo_root=tmp_path)
    node_dir = foundation.layout.node_dir_by_id(ctx, "node_bad")
    node_dir.mkdir(parents=True)
    foundation.store.write_json_atomic(
        node_dir / "node.json",
        NodeMetadata(node_id="node_bad", path="Main", kind=NodeKind.CONTENT, lifecycle=NodeLifecycle.ACTIVE, current_contract_version=None),
    )
    component = make_runtime().node.node_tree

    result = component.ensure_root_scope_node(tmp_path)

    assert not result.ok
    assert result.issues[0].kind == "root_node_wrong_kind"


def test_ensure_root_rejects_invalid_path_and_obsolete_root(tmp_path: Path) -> None:
    component = make_runtime().node.node_tree

    wrong_path = component.ensure_root_scope_node(tmp_path, path="Main.Other")
    assert not wrong_path.ok
    assert wrong_path.issues[0].kind == "root_scope_path_invalid"

    assert component.ensure_root_scope_node(tmp_path).ok
    deleted = make_runtime().node.mark_node_deleted(tmp_path, path="Main", reason="Exercise obsolete root branch.")
    assert not deleted.ok
    assert "root_main" in deleted.issues[0].details["blocking_reasons"]
