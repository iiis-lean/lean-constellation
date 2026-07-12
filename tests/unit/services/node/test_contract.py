import json

from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.refs import DeclRef, NodeRef
from lean_constellation.services.foundation import FoundationContext, FoundationService, WriteMode
from lean_constellation.services.node import (
    ContractComponent,
    ContractVersionStatus,
    NodeContractSnapshot,
    NodeKind,
    NodeLifecycle,
    NodeMetadata,
    NodeTreeComponent,
)
from lean_constellation.services.node.contract_fields import NodeDep, NodeDepActor


def _write_preparation_input(tmp_path: Path, *, interfaces: list[DeclInterface] | None = None) -> None:
    foundation = make_runtime().foundation
    input_value = RepoPreparationInput(
        goal="Formalize the requested source material.",
        source_corpus_mode=SourceCorpusMode.PREPARE,
        source_corpus_relpath=".lean_constellation/source",
        source_description="A source description.",
        interface_inputs=interfaces or [],
    )
    path = foundation.layout.preparation_input_path(FoundationContext(repo_root=tmp_path))
    result = foundation.store.write_json_atomic(path, input_value)
    assert result.ok


def _create_topic_content(tmp_path: Path) -> None:
    tree = make_runtime().node.node_tree
    assert tree.ensure_root_scope_node(tmp_path).ok
    assert tree.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    assert tree.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Ready.",
    ).ok


def _contract_path(tmp_path: Path, node_path: str, version: int = 1) -> Path:
    runtime = make_runtime()
    node = runtime.node.node_tree.node_store.resolve_active_node(tmp_path, path=node_path)
    assert node.ok and node.value is not None
    return runtime.node.node_tree.node_store.contract_path(tmp_path, node_id=node.value.node_id, version=version)


def _load_contract(tmp_path: Path, node_path: str, version: int = 1) -> NodeContractSnapshot:
    foundation = make_runtime().foundation
    loaded = foundation.store.read_json(_contract_path(tmp_path, node_path, version), NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    return loaded.value


def _write_contract(tmp_path: Path, node_path: str, contract: NodeContractSnapshot) -> None:
    foundation = make_runtime().foundation
    saved = foundation.store.write_json_atomic(
        _contract_path(tmp_path, node_path, contract.version),
        contract,
        mode=WriteMode.UPDATE_EXISTING,
    )
    assert saved.ok


def _write_node_without_contract(tmp_path: Path, node_path: str) -> None:
    foundation = make_runtime().foundation
    ctx = FoundationContext(repo_root=tmp_path)
    node_id = "manual_missing_contract"
    node_dir = foundation.layout.node_dir_by_id(ctx, node_id)
    metadata = NodeMetadata(
        node_id=node_id,
        path=node_path,
        kind=NodeKind.CONTENT,
        lifecycle=NodeLifecycle.ACTIVE,
        current_contract_version=1,
        open_contract_version=1,
    )
    saved = foundation.store.write_json_atomic(node_dir / "node.json", metadata)
    assert saved.ok
    rebuilt = make_runtime().node.node_tree.node_store.rebuild_index(tmp_path)
    assert rebuilt.ok


def test_get_current_and_ensure_open_contract_copies_committed_version(tmp_path: Path) -> None:
    _create_topic_content(tmp_path)
    component = make_runtime().node.contract

    current = component.get_current_contract(tmp_path, node_path="Main.Topic.Core")
    assert current.ok
    assert current.value is not None
    assert current.value.version == 1
    assert current.value.version_status == ContractVersionStatus.OPEN

    committed = component.commit_content_contract(tmp_path, node_path="Main.Topic.Core", summary="Initial core contract complete.")
    assert committed.ok
    assert committed.value is not None
    assert committed.value.status == ContractVersionStatus.COMMITTED
    assert committed.value.version_status == ContractVersionStatus.COMMITTED
    assert committed.value.active_contract_version == 1
    assert committed.value.open_contract_version is None

    visible = component.get_visible_contract(tmp_path, node_path="Main.Topic.Core")
    assert visible.ok and visible.value is not None
    assert visible.value.version == 1
    assert visible.value.status == ContractVersionStatus.COMMITTED

    missing_open = component.get_open_contract(tmp_path, node_path="Main.Topic.Core")
    assert not missing_open.ok
    assert missing_open.issues[0].kind == "node_open_contract_missing"

    opened = component.ensure_open_contract(tmp_path, node_path="Main.Topic.Core")
    assert opened.ok
    assert opened.value is not None
    assert opened.value.created_new_open is True
    assert opened.value.version == 2
    assert opened.value.node_id == committed.value.node_id
    assert opened.value.contract.status == ContractVersionStatus.OPEN
    assert opened.value.contract.version_status == ContractVersionStatus.OPEN
    assert opened.value.contract.summary is None

    latest = component.get_current_contract(tmp_path, node_path="Main.Topic.Core")
    assert latest.ok
    assert latest.value is not None
    assert latest.value.version == 2
    assert latest.value.status == ContractVersionStatus.OPEN
    assert latest.value.active_contract_version == 1
    assert latest.value.open_contract_version == 2

    open_view = component.get_open_contract(tmp_path, node_path="Main.Topic.Core")
    assert open_view.ok and open_view.value is not None
    assert open_view.value.version == 2

    visible_after_open = component.get_visible_contract(tmp_path, node_path="Main.Topic.Core")
    assert visible_after_open.ok and visible_after_open.value is not None
    assert visible_after_open.value.version == 1
    assert visible_after_open.value.status == ContractVersionStatus.COMMITTED

    versions = component.list_contract_versions(tmp_path, node_path="Main.Topic.Core")
    assert versions.ok and versions.value is not None
    assert [(item.version, item.status, item.is_active, item.is_open) for item in versions.value] == [
        (1, ContractVersionStatus.COMMITTED, True, False),
        (2, ContractVersionStatus.OPEN, False, True),
    ]
    persisted = _contract_path(tmp_path, "Main.Topic.Core", 2).read_text(encoding="utf-8")
    assert '"status"' in persisted
    assert '"version_status"' not in persisted


def test_get_current_contract_reports_missing_node_and_missing_contract_versions(tmp_path: Path) -> None:
    component = make_runtime().node.contract

    missing_node = component.get_current_contract(tmp_path, node_path="Main.Missing")
    assert not missing_node.ok

    _write_node_without_contract(tmp_path, "Main.Topic.Core")
    missing_contract = component.get_current_contract(tmp_path, node_path="Main.Topic.Core")
    assert not missing_contract.ok
    assert missing_contract.issues[0].kind == "node_contract_missing"


def test_ensure_open_contract_reuses_existing_open_and_reports_missing_contract(tmp_path: Path) -> None:
    _create_topic_content(tmp_path)
    component = make_runtime().node.contract

    opened = component.ensure_open_contract(tmp_path, node_path="Main.Topic.Core")
    assert opened.ok
    assert opened.value is not None
    assert opened.value.created_new_open is False
    assert opened.value.version == 1

    _write_node_without_contract(tmp_path, "Main.Topic.MissingContract")
    missing_contract = component.ensure_open_contract(tmp_path, node_path="Main.Topic.MissingContract")
    assert not missing_contract.ok
    assert missing_contract.issues[0].kind == "node_contract_missing"


def test_ensure_scope_contract_repairs_missing_exports_list(tmp_path: Path) -> None:
    tree = make_runtime().node.node_tree
    assert tree.ensure_root_scope_node(tmp_path).ok
    component = make_runtime().node.contract

    contract_path = _contract_path(tmp_path, "Main", 1)
    raw = contract_path.read_text(encoding="utf-8").replace('"exports": []', '"exports": null')
    contract_path.write_text(raw, encoding="utf-8")

    result = component.ensure_scope_contract(tmp_path, scope_path="Main")

    assert result.ok
    assert result.value is not None
    assert result.value.contract.exports == []


def test_old_contract_json_without_decl_graph_head_loads_without_rewrite(tmp_path: Path) -> None:
    _create_topic_content(tmp_path)
    contract_path = _contract_path(tmp_path, "Main.Topic.Core", 1)
    original = json.loads(contract_path.read_text(encoding="utf-8"))
    original.pop("decl_graph_head", None)
    serialized = json.dumps(original, indent=2) + "\n"
    contract_path.write_text(serialized, encoding="utf-8")

    loaded = _load_contract(tmp_path, "Main.Topic.Core", 1)

    assert loaded.decl_graph_head == {}
    assert contract_path.read_text(encoding="utf-8") == serialized


def test_update_contract_text_fields_creates_open_version_and_protects_main_goal(tmp_path: Path) -> None:
    _create_topic_content(tmp_path)
    component = make_runtime().node.contract
    assert component.commit_content_contract(tmp_path, node_path="Main.Topic.Core", summary="Committed.").ok

    updated = component.update_contract_text_fields(
        tmp_path,
        node_path="Main.Topic.Core",
        objective="Follow-up objective.",
        constraints="Stay in topic boundary.",
    )

    assert updated.ok
    assert updated.value is not None
    assert updated.value.version == 2
    assert updated.value.contract.objective == "Follow-up objective."
    assert updated.value.contract.constraints == "Stay in topic boundary."

    protected = component.update_contract_text_fields(tmp_path, node_path="Main", goal="Change protected goal.")
    assert not protected.ok
    assert protected.issues[0].kind == "main_goal_protected"


def test_update_contract_text_fields_rejects_empty_required_fields_and_preserves_contract(tmp_path: Path) -> None:
    _create_topic_content(tmp_path)
    component = make_runtime().node.contract

    empty_objective = component.update_contract_text_fields(tmp_path, node_path="Main.Topic.Core", objective=" ")
    assert not empty_objective.ok
    assert empty_objective.issues[0].kind == "contract_objective_required"

    empty_success = component.update_contract_text_fields(tmp_path, node_path="Main.Topic.Core", success_criteria="\t")
    assert not empty_success.ok
    assert empty_success.issues[0].kind == "contract_success_criteria_required"

    no_op = component.update_contract_text_fields(tmp_path, node_path="Main.Topic.Core", objective="Build core.")
    assert no_op.ok
    assert no_op.value is not None
    assert no_op.value.version == 1


def test_initialize_main_contract_from_preparation_input_syncs_goal_boundary_objective_and_interfaces(tmp_path: Path) -> None:
    interface = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Expose the main theorem.")
    _write_preparation_input(tmp_path, interfaces=[interface])
    component = make_runtime().node.contract

    initialized = component.initialize_main_contract_from_preparation_input(
        tmp_path,
        boundary="Main covers the whole prepared source corpus and repo public interface.",
        objective="Prepare Main for repository coordination.",
    )

    assert initialized.ok
    assert initialized.value is not None
    contract = initialized.value.contract
    assert contract.goal == "Formalize the requested source material."
    assert contract.boundary == "Main covers the whole prepared source corpus and repo public interface."
    assert contract.objective == "Prepare Main for repository coordination."
    assert [item.name for item in contract.interfaces] == ["main_result"]


def test_initialize_main_contract_from_preparation_input_rejects_missing_input_required_fields_and_goal_conflict(tmp_path: Path) -> None:
    component = make_runtime().node.contract

    missing_input = component.initialize_main_contract_from_preparation_input(
        tmp_path,
        boundary="Main boundary.",
        objective="Main objective.",
    )
    assert not missing_input.ok
    assert missing_input.issues[0].kind == "preparation_input_missing"

    _write_preparation_input(tmp_path)
    missing_boundary = component.initialize_main_contract_from_preparation_input(
        tmp_path,
        boundary=" ",
        objective="Main objective.",
    )
    assert not missing_boundary.ok
    assert missing_boundary.issues[0].kind == "contract_boundary_required"

    missing_objective = component.initialize_main_contract_from_preparation_input(
        tmp_path,
        boundary="Main boundary.",
        objective="",
    )
    assert not missing_objective.ok
    assert missing_objective.issues[0].kind == "contract_objective_required"

    assert component.initialize_main_contract_from_preparation_input(
        tmp_path,
        boundary="Main boundary.",
        objective="Main objective.",
    ).ok
    contract = _load_contract(tmp_path, "Main")
    contract.goal = "Different protected goal."
    _write_contract(tmp_path, "Main", contract)

    conflict = component.initialize_main_contract_from_preparation_input(
        tmp_path,
        boundary="Main boundary.",
        objective="Main objective.",
    )
    assert not conflict.ok
    assert conflict.issues[0].kind == "main_goal_conflict"


def test_initialize_main_contract_from_preparation_input_is_idempotent_and_rejects_interface_conflict(tmp_path: Path) -> None:
    interface = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Expose the main theorem.")
    _write_preparation_input(tmp_path, interfaces=[interface])
    component = make_runtime().node.contract

    first = component.initialize_main_contract_from_preparation_input(
        tmp_path,
        boundary="Main boundary.",
        objective="Main objective.",
    )
    assert first.ok
    second = component.initialize_main_contract_from_preparation_input(
        tmp_path,
        boundary="Main boundary.",
        objective="Main objective.",
    )
    assert second.ok

    contract = _load_contract(tmp_path, "Main")
    contract.interfaces[0].summary = "Changed interface meaning."
    _write_contract(tmp_path, "Main", contract)

    conflict = component.initialize_main_contract_from_preparation_input(
        tmp_path,
        boundary="Main boundary.",
        objective="Main objective.",
    )
    assert not conflict.ok
    assert conflict.issues[0].kind == "main_interfaces_conflict"


def test_commit_scope_contract_checks_interface_binding_against_exports(tmp_path: Path) -> None:
    interface = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Expose the main theorem.")
    _write_preparation_input(tmp_path, interfaces=[interface])
    component = make_runtime().node.contract
    initialized = component.initialize_main_contract_from_preparation_input(
        tmp_path,
        boundary="Main boundary.",
        objective="Main objective.",
    )
    assert initialized.ok

    unbound = component.commit_scope_contract(tmp_path, scope_path="Main", summary="Ready.")
    assert not unbound.ok
    assert unbound.issues[0].kind == "interface_unbound"

    foundation = make_runtime().foundation
    path = _contract_path(tmp_path, "Main", 1)
    loaded = foundation.store.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    ref = DeclRef(repo=None, node="Main.Topic.Core", name="main_result", revision=1)
    loaded.value.exports = [ref]
    loaded.value.interfaces[0].bound_decl = ref
    assert foundation.store.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok

    committed = component.commit_scope_contract(tmp_path, scope_path="Main", summary="Main scope exposes the required interface.")
    assert committed.ok
    assert committed.value is not None
    assert committed.value.version_status == ContractVersionStatus.COMMITTED


def test_commit_content_contract_rejects_summary_scope_node_and_committed_contract(tmp_path: Path) -> None:
    _create_topic_content(tmp_path)
    component = make_runtime().node.contract

    missing_summary = component.commit_content_contract(tmp_path, node_path="Main.Topic.Core", summary=" ")
    assert not missing_summary.ok
    assert missing_summary.issues[0].kind == "contract_summary_required"

    scope_node = component.commit_content_contract(tmp_path, node_path="Main.Topic", summary="Scope is not content.")
    assert not scope_node.ok
    assert scope_node.issues[0].kind == "node_not_content"

    committed = component.commit_content_contract(tmp_path, node_path="Main.Topic.Core", summary="Committed.")
    assert committed.ok
    already_committed = component.commit_content_contract(tmp_path, node_path="Main.Topic.Core", summary="Committed again.")
    assert not already_committed.ok
    assert already_committed.issues[0].kind == "contract_not_open"


def test_commit_scope_contract_rejects_summary_content_node_and_committed_contract(tmp_path: Path) -> None:
    _create_topic_content(tmp_path)
    component = make_runtime().node.contract

    missing_summary = component.commit_scope_contract(tmp_path, scope_path="Main.Topic", summary="")
    assert not missing_summary.ok
    assert missing_summary.issues[0].kind == "contract_summary_required"

    content_node = component.commit_scope_contract(tmp_path, scope_path="Main.Topic.Core", summary="Content is not scope.")
    assert not content_node.ok
    assert content_node.issues[0].kind == "node_not_scope"

    committed = component.commit_scope_contract(tmp_path, scope_path="Main.Topic", summary="Topic scope committed.")
    assert committed.ok
    already_committed = component.commit_scope_contract(tmp_path, scope_path="Main.Topic", summary="Topic scope committed again.")
    assert not already_committed.ok
    assert already_committed.issues[0].kind == "contract_not_open"


def test_content_task_admission_reports_missing_local_dependency(tmp_path: Path) -> None:
    _create_topic_content(tmp_path)
    component = make_runtime().node.contract
    foundation = make_runtime().foundation
    path = _contract_path(tmp_path, "Main.Topic.Core", 1)
    loaded = foundation.store.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.deps.append(
        NodeDep(
            dep_id="dep_missing",
            target=NodeRef(repo=None, node="Main.Topic.Missing"),
            reason="Need missing node.",
            added_by=NodeDepActor.COORDINATOR,
        )
    )
    assert foundation.store.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok

    admission = component.check_content_task_admission(tmp_path, node_path="Main.Topic.Core")

    assert admission.ok
    assert admission.value is not None
    assert admission.value.passed is False
    assert admission.value.issues[0].kind == "contract_dep_node_missing"


def test_content_task_admission_reports_invalid_dep_shapes(tmp_path: Path) -> None:
    _create_topic_content(tmp_path)
    component = make_runtime().node.contract
    contract = _load_contract(tmp_path, "Main.Topic.Core")
    payload = contract.model_dump(mode="json")
    payload["deps"] = [
        "not an object",
        {"target": "not an object", "reason": "Malformed target."},
        {"target": {"repo": None}, "reason": "Missing local node."},
    ]
    _contract_path(tmp_path, "Main.Topic.Core").write_text(json.dumps(payload), encoding="utf-8")

    admission = component.check_content_task_admission(tmp_path, node_path="Main.Topic.Core")

    assert not admission.ok
    assert admission.issues[0].kind == "schema_validation_failed"


def test_content_task_admission_reports_wrong_kind_missing_required_fields_and_committed_contract(tmp_path: Path) -> None:
    _create_topic_content(tmp_path)
    component = make_runtime().node.contract

    scope_admission = component.check_content_task_admission(tmp_path, node_path="Main.Topic")
    assert scope_admission.ok
    assert scope_admission.value is not None
    assert scope_admission.value.passed is False
    assert "node_not_content" in {issue.kind for issue in scope_admission.value.issues}

    contract = _load_contract(tmp_path, "Main.Topic.Core")
    contract.objective = None
    contract.success_criteria = ""
    _write_contract(tmp_path, "Main.Topic.Core", contract)

    missing_fields = component.check_content_task_admission(tmp_path, node_path="Main.Topic.Core")
    assert missing_fields.ok
    assert missing_fields.value is not None
    assert missing_fields.value.passed is False
    assert {issue.kind for issue in missing_fields.value.issues} == {
        "contract_objective_missing",
        "contract_success_criteria_missing",
    }

    contract.objective = "Build core."
    contract.success_criteria = "Ready."
    _write_contract(tmp_path, "Main.Topic.Core", contract)
    assert component.commit_content_contract(tmp_path, node_path="Main.Topic.Core", summary="Committed.").ok

    committed_admission = component.check_content_task_admission(tmp_path, node_path="Main.Topic.Core")
    assert committed_admission.ok
    assert committed_admission.value is not None
    assert committed_admission.value.passed is False
    assert committed_admission.value.issues[0].kind == "contract_not_open"


def test_ensure_scope_contract_rejects_content_node(tmp_path: Path) -> None:
    _create_topic_content(tmp_path)
    component = make_runtime().node.contract

    result = component.ensure_scope_contract(tmp_path, scope_path="Main.Topic.Core")

    assert not result.ok
    assert result.issues[0].kind == "node_not_scope"
