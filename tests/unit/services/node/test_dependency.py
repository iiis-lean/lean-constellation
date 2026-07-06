import json

from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.refs import NodeRef
from lean_constellation.services.foundation import FoundationContext, FoundationService, WriteMode
from lean_constellation.services.node import ContractComponent, ContractVersionStatus, DependencyComponent, NodeContractSnapshot, NodeTreeComponent
from lean_constellation.services.node.contract_fields import NodeDep, NodeDepActor
from lean_constellation.services.repo_workspace import RepoWorkspaceService


def _create_base_tree(tmp_path: Path) -> None:
    tree = make_runtime().node.node_tree
    assert tree.ensure_root_scope_node(tmp_path).ok
    assert tree.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    assert tree.create_scope_node(tmp_path, path="Main.Topic.Provider", goal="Provider goal", boundary="Provider boundary").ok
    assert tree.create_content_node(
        tmp_path,
        path="Main.Topic.Consumer",
        goal="Consumer goal",
        boundary="Consumer boundary",
        objective="Use provider.",
        success_criteria="Consumer ready.",
    ).ok
    assert tree.create_content_node(
        tmp_path,
        path="Main.Topic.A",
        goal="A goal",
        boundary="A boundary",
        objective="Build A.",
        success_criteria="A ready.",
    ).ok
    assert tree.create_content_node(
        tmp_path,
        path="Main.Topic.B",
        goal="B goal",
        boundary="B boundary",
        objective="Build B.",
        success_criteria="B ready.",
    ).ok


def _write_provider_export(tmp_path: Path) -> DeclRef:
    foundation = make_runtime().foundation
    path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic.Provider", 1)
    loaded = foundation.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    ref = DeclRef(repo=None, node="Main.Topic.Provider.Core", name="helper", revision=1)
    loaded.value.exports = [ref]
    saved = foundation.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING)
    assert saved.ok
    return ref


def _commit_provider_scope(tmp_path: Path) -> DeclRef:
    ref = _write_provider_export(tmp_path)
    committed = make_runtime().node.contract.commit_scope_contract(
        tmp_path,
        scope_path="Main.Topic.Provider",
        summary="Provider exposes helper.",
    )
    assert committed.ok
    return ref


def _prelude_text(tmp_path: Path, node_path: str) -> str:
    foundation = make_runtime().foundation
    path = foundation.prelude_path(FoundationContext(repo_root=tmp_path), node_path)
    return path.read_text(encoding="utf-8")


def test_list_visible_node_boundaries_only_shows_committed_boundaries(tmp_path: Path) -> None:
    _create_base_tree(tmp_path)
    _commit_provider_scope(tmp_path)
    component = make_runtime().node.dependency

    visible = component.list_visible_node_boundaries(tmp_path, node_path="Main.Topic.Consumer")

    assert visible.ok
    assert visible.value is not None
    assert [item.node_path for item in visible.value.boundaries] == ["Main.Topic.Provider"]
    assert visible.value.boundaries[0].index == 0
    assert visible.value.boundaries[0].exported_decl_refs[0].name == "helper"


def test_visible_node_boundaries_use_active_committed_contract_not_open_draft(tmp_path: Path) -> None:
    _create_base_tree(tmp_path)
    _commit_provider_scope(tmp_path)
    contract = make_runtime().node.contract
    opened = contract.ensure_open_contract(tmp_path, node_path="Main.Topic.Provider")
    assert opened.ok and opened.value is not None
    opened.value.contract.exports = []
    saved = make_runtime().foundation.write_json_atomic(
        make_runtime().foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic.Provider", opened.value.version),
        opened.value.contract,
        mode=WriteMode.UPDATE_EXISTING,
    )
    assert saved.ok
    component = make_runtime().node.dependency

    visible = component.list_visible_node_boundaries(tmp_path, node_path="Main.Topic.Consumer")

    assert visible.ok
    assert visible.value is not None
    assert [item.node_path for item in visible.value.boundaries] == ["Main.Topic.Provider"]
    assert visible.value.boundaries[0].exported_decl_refs[0].name == "helper"


def test_list_visible_node_boundaries_includes_lake_dependency_boundaries(tmp_path: Path) -> None:
    _create_base_tree(tmp_path)
    (tmp_path / "lakefile.toml").write_text(
        'name = "consumer"\n\n'
        '[[require]]\nname = "ProviderRepo"\npath = "../ProviderRepo"\n',
        encoding="utf-8",
    )
    component = make_runtime().node.dependency

    visible = component.list_visible_node_boundaries(tmp_path, node_path="Main.Topic.Consumer")

    assert visible.ok
    assert visible.value is not None
    assert [(item.repo, item.node_path, item.import_module) for item in visible.value.boundaries] == [
        ("ProviderRepo", "Main", "ProviderRepo.Main.Interfaces")
    ]


def test_add_node_dep_resolves_expected_decl_and_refreshes_prelude(tmp_path: Path) -> None:
    _create_base_tree(tmp_path)
    ref = _commit_provider_scope(tmp_path)
    component = make_runtime().node.dependency

    added = component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Provider",
        expected_decl_names=["helper"],
        reason="Use provider helper.",
        actor="coordinator",
    )

    assert added.ok
    assert added.value is not None
    assert len(added.value.contract.deps) == 1
    dep = added.value.contract.deps[0]
    assert dep.target == NodeRef(repo=None, node="Main.Topic.Provider")
    assert dep.expected_decl_refs == [ref]
    assert dep.added_by == NodeDepActor.COORDINATOR
    listed = component.list_node_deps(tmp_path, node_path="Main.Topic.Consumer")
    assert listed.ok
    assert listed.value is not None
    assert listed.value.deps[0].index == 0
    assert listed.value.deps[0].target_node == "Main.Topic.Provider"
    assert listed.value.deps[0].expected_decl_names == ["helper"]
    assert "import Main.Topic.Provider.Interfaces" in _prelude_text(tmp_path, "Main.Topic.Consumer")


def test_add_node_dep_from_visible_candidate_uses_display_index(tmp_path: Path) -> None:
    _create_base_tree(tmp_path)
    _commit_provider_scope(tmp_path)
    component = make_runtime().node.dependency

    visible = component.list_visible_node_boundaries(tmp_path, node_path="Main.Topic.Consumer")
    assert visible.ok
    assert visible.value is not None
    assert visible.value.boundaries[0].index == 0

    added = component.add_node_dep_from_visible_candidate(
        tmp_path,
        node_path="Main.Topic.Consumer",
        candidate_index=0,
        expected_decl_names=["helper"],
        reason="Use provider from visible boundary list.",
        actor="coordinator",
    )
    assert added.ok, added.issues
    assert added.value is not None
    assert added.value.contract.deps[0].target == NodeRef(repo=None, node="Main.Topic.Provider")

    missing = component.add_node_dep_from_visible_candidate(
        tmp_path,
        node_path="Main.Topic.Consumer",
        candidate_index=99,
        reason="Use missing candidate.",
        actor="coordinator",
    )
    assert not missing.ok
    assert missing.issues[0].kind == "node_dep_candidate_index_out_of_range"


def test_add_node_dep_rejects_unready_target_and_missing_expected_decl(tmp_path: Path) -> None:
    _create_base_tree(tmp_path)
    component = make_runtime().node.dependency

    unready = component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Provider",
        expected_decl_names=None,
        reason="Provider is still open.",
        actor="coordinator",
    )
    assert not unready.ok
    assert unready.issues[0].kind == "node_dep_target_not_visible"

    _commit_provider_scope(tmp_path)
    missing_decl = component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Provider",
        expected_decl_names=["missing"],
        reason="Need missing decl.",
        actor="coordinator",
    )
    assert not missing_decl.ok
    assert missing_decl.issues[0].kind == "node_dep_expected_decl_missing"


def test_add_node_dep_rejects_invalid_inputs_and_self_dependency(tmp_path: Path) -> None:
    _create_base_tree(tmp_path)
    _commit_provider_scope(tmp_path)
    component = make_runtime().node.dependency

    invalid_actor = component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Provider",
        expected_decl_names=None,
        reason="Use provider.",
        actor="reviewer",
    )
    assert not invalid_actor.ok
    assert invalid_actor.issues[0].kind == "node_dep_actor_invalid"

    missing_reason = component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Provider",
        expected_decl_names=None,
        reason=" ",
        actor="coordinator",
    )
    assert not missing_reason.ok
    assert missing_reason.issues[0].kind == "node_dep_reason_required"

    invalid_target = component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Topic.Provider",
        expected_decl_names=None,
        reason="Use provider.",
        actor="coordinator",
    )
    assert not invalid_target.ok
    assert invalid_target.issues[0].kind == "node_dep_target_invalid"

    self_dep = component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Consumer",
        expected_decl_names=None,
        reason="Use self.",
        actor="coordinator",
    )
    assert not self_dep.ok
    assert self_dep.issues[0].kind == "node_dep_self_dependency"


def test_add_node_dep_merges_duplicates_and_rejects_worker_modifying_coordinator_dep(tmp_path: Path) -> None:
    _create_base_tree(tmp_path)
    _commit_provider_scope(tmp_path)
    component = make_runtime().node.dependency

    first = component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Provider",
        expected_decl_names=["helper"],
        reason="Initial reason.",
        actor="coordinator",
    )
    assert first.ok and first.value is not None
    dep_id = first.value.contract.deps[0].dep_id

    duplicate = component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Provider",
        expected_decl_names=["helper"],
        reason="Initial reason.",
        actor="coordinator",
    )
    assert duplicate.ok
    assert duplicate.issues[0].kind == "node_dep_duplicate"
    assert duplicate.value is not None
    assert duplicate.value.contract.deps[0].dep_id == dep_id

    updated = component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Provider",
        expected_decl_names=["helper"],
        reason="Updated reason.",
        actor="coordinator",
    )
    assert updated.ok and updated.value is not None
    assert updated.value.contract.deps[0].reason == "Updated reason."

    denied = component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Provider",
        expected_decl_names=["helper"],
        reason="Worker tries to change reason.",
        actor="worker",
    )
    assert not denied.ok
    assert denied.issues[0].kind == "node_dep_permission_denied"


def test_worker_can_only_remove_worker_added_node_dep(tmp_path: Path) -> None:
    _create_base_tree(tmp_path)
    _commit_provider_scope(tmp_path)
    assert make_runtime().node.contract.commit_content_contract(tmp_path, node_path="Main.Topic.B", summary="B ready.").ok
    component = make_runtime().node.dependency

    coordinator_added = component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Provider",
        expected_decl_names=None,
        reason="Coordinator dep.",
        actor="coordinator",
    )
    assert coordinator_added.ok and coordinator_added.value is not None
    listed = component.list_node_deps(tmp_path, node_path="Main.Topic.Consumer")
    assert listed.ok and listed.value is not None
    coordinator_index = listed.value.deps[0].index

    denied = component.remove_node_dep(tmp_path, node_path="Main.Topic.Consumer", index=coordinator_index, actor="worker")
    assert not denied.ok
    assert denied.issues[0].kind == "node_dep_permission_denied"

    worker_added = component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.B",
        expected_decl_names=None,
        reason="Worker dep.",
        actor="worker",
    )
    assert worker_added.ok and worker_added.value is not None
    listed = component.list_node_deps(tmp_path, node_path="Main.Topic.Consumer")
    assert listed.ok and listed.value is not None
    worker_index = next(dep.index for dep in listed.value.deps if dep.target_node == "Main.Topic.B")
    removed = component.remove_node_dep(tmp_path, node_path="Main.Topic.Consumer", index=worker_index, actor="worker")
    assert removed.ok
    assert removed.value is not None
    assert all(dep.target.node != "Main.Topic.B" for dep in removed.value.contract.deps)


def test_remove_node_dep_reports_missing_dep_and_allows_coordinator_remove(tmp_path: Path) -> None:
    _create_base_tree(tmp_path)
    _commit_provider_scope(tmp_path)
    component = make_runtime().node.dependency

    missing = component.remove_node_dep(tmp_path, node_path="Main.Topic.Consumer", index=0, actor="coordinator")
    assert not missing.ok
    assert missing.issues[0].kind == "node_dep_index_out_of_range"

    added = component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Provider",
        expected_decl_names=None,
        reason="Coordinator dep.",
        actor="coordinator",
    )
    assert added.ok and added.value is not None

    listed = component.list_node_deps(tmp_path, node_path="Main.Topic.Consumer")
    assert listed.ok and listed.value is not None
    removed = component.remove_node_dep(tmp_path, node_path="Main.Topic.Consumer", index=listed.value.deps[0].index, actor="coordinator")
    assert removed.ok
    assert removed.value is not None
    assert removed.value.contract.deps == []

    negative = component.remove_node_dep(tmp_path, node_path="Main.Topic.Consumer", index=-1, actor="coordinator")
    assert not negative.ok
    assert negative.issues[0].kind == "node_dep_index_out_of_range"


def test_validate_node_deps_reports_cycle_and_batch_dependency(tmp_path: Path) -> None:
    _create_base_tree(tmp_path)
    contract = make_runtime().node.contract
    assert contract.commit_content_contract(tmp_path, node_path="Main.Topic.B", summary="B ready.").ok
    component = make_runtime().node.dependency
    assert component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.A",
        target_node="Main.Topic.B",
        expected_decl_names=None,
        reason="A uses B.",
        actor="coordinator",
    ).ok

    foundation = make_runtime().foundation
    path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic.B", 1)
    loaded = foundation.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.deps.append(
        NodeDep(
            dep_id="dep_cycle_to_a",
            target=NodeRef(repo=None, node="Main.Topic.A"),
            reason="B uses A.",
            added_by=NodeDepActor.COORDINATOR,
        )
    )
    assert foundation.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok

    validation = component.validate_node_deps(tmp_path, node_path="Main.Topic.A")
    assert validation.ok
    assert validation.value is not None
    assert validation.value.passed is False
    assert any(issue.kind == "node_dep_cycle" for issue in validation.value.issues)

    batch = component.check_content_batch_independent(tmp_path, node_paths=["Main.Topic.A", "Main.Topic.B"])
    assert batch.ok
    assert batch.value is not None
    assert batch.value.passed is False
    assert batch.value.issues[0].kind == "content_batch_dependency_present"


def test_validate_node_deps_reports_invalid_expected_public_missing_unready_and_external_warning(tmp_path: Path) -> None:
    _create_base_tree(tmp_path)
    ref = _commit_provider_scope(tmp_path)
    component = make_runtime().node.dependency
    assert component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.Consumer",
        target_node="Main.Topic.Provider",
        expected_decl_names=["helper"],
        reason="Use provider helper.",
        actor="coordinator",
    ).ok

    foundation = make_runtime().foundation
    consumer_path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic.Consumer", 1)
    consumer = foundation.read_json(consumer_path, NodeContractSnapshot)
    assert consumer.ok and consumer.value is not None
    consumer_payload = consumer.value.model_dump(mode="json")
    consumer_payload["deps"].append("bad dependency entry")
    consumer_path.write_text(json.dumps(consumer_payload), encoding="utf-8")

    invalid = component.validate_node_deps(tmp_path, node_path="Main.Topic.Consumer")
    assert not invalid.ok
    assert invalid.issues[0].kind == "schema_validation_failed"

    consumer_payload["deps"] = [
        {
            "dep_id": "dep_provider_helper",
            "target": {"repo": None, "node": "Main.Topic.Provider"},
            "expected_decl_refs": [ref.model_dump(mode="json")],
            "reason": "Use provider helper.",
            "added_by": "coordinator",
        }
    ]
    consumer_path.write_text(json.dumps(consumer_payload), encoding="utf-8")
    provider_path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic.Provider", 1)
    provider = foundation.read_json(provider_path, NodeContractSnapshot)
    assert provider.ok and provider.value is not None
    provider.value.exports = []
    assert foundation.write_json_atomic(provider_path, provider.value, mode=WriteMode.UPDATE_EXISTING).ok

    expected_missing = component.validate_node_deps(tmp_path, node_path="Main.Topic.Consumer")
    assert expected_missing.ok
    assert expected_missing.value is not None
    assert expected_missing.value.passed is False
    assert expected_missing.value.issues[0].kind == "node_dep_expected_decl_not_public"

    consumer_payload["deps"] = [
        {
            "dep_id": "dep_provider_open",
            "target": {"repo": None, "node": "Main.Topic.Provider"},
            "reason": "Provider open again.",
            "added_by": "coordinator",
        }
    ]
    consumer_path.write_text(json.dumps(consumer_payload), encoding="utf-8")
    provider.value.status = ContractVersionStatus.OPEN
    assert foundation.write_json_atomic(provider_path, provider.value, mode=WriteMode.UPDATE_EXISTING).ok

    unready = component.validate_node_deps(tmp_path, node_path="Main.Topic.Consumer")
    assert unready.ok
    assert unready.value is not None
    assert unready.value.passed is False
    assert unready.value.issues[0].kind == "node_dep_target_not_ready"

    consumer_payload["deps"] = [
        {
            "dep_id": "dep_external_provider",
            "target": {"repo": "ProviderRepo", "node": "Main"},
            "reason": "External provider.",
            "added_by": "coordinator",
        }
    ]
    consumer_path.write_text(json.dumps(consumer_payload), encoding="utf-8")

    external = component.validate_node_deps(tmp_path, node_path="Main.Topic.Consumer")
    assert external.ok
    assert external.value is not None
    assert external.value.passed is True
    assert [issue.kind for issue in external.value.issues] == ["node_dep_external_validation_deferred"]


def test_check_content_batch_independent_reports_pass_duplicates_missing_noncontent_and_transitive_dependency(tmp_path: Path) -> None:
    _create_base_tree(tmp_path)
    tree = make_runtime().node.node_tree
    assert tree.create_content_node(
        tmp_path,
        path="Main.Topic.C",
        goal="C goal",
        boundary="C boundary",
        objective="Build C.",
        success_criteria="C ready.",
    ).ok
    contract = make_runtime().node.contract
    assert contract.commit_content_contract(tmp_path, node_path="Main.Topic.B", summary="B ready.").ok
    assert contract.commit_content_contract(tmp_path, node_path="Main.Topic.C", summary="C ready.").ok
    component = make_runtime().node.dependency

    independent = component.check_content_batch_independent(tmp_path, node_paths=["Main.Topic.A", "Main.Topic.B"])
    assert independent.ok
    assert independent.value is not None
    assert independent.value.passed is True

    malformed = component.check_content_batch_independent(
        tmp_path,
        node_paths=["Main.Topic.A", "Main.Topic.A", "Main.Topic.Missing", "Main.Topic"],
    )
    assert malformed.ok
    assert malformed.value is not None
    assert malformed.value.passed is False
    assert {issue.kind for issue in malformed.value.issues} == {
        "content_batch_duplicate",
        "content_batch_node_missing",
        "content_batch_node_not_content",
    }

    assert component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.A",
        target_node="Main.Topic.B",
        expected_decl_names=None,
        reason="A uses B.",
        actor="coordinator",
    ).ok
    assert component.add_node_dep(
        tmp_path,
        node_path="Main.Topic.B",
        target_node="Main.Topic.C",
        expected_decl_names=None,
        reason="B uses C.",
        actor="coordinator",
    ).ok

    transitive = component.check_content_batch_independent(tmp_path, node_paths=["Main.Topic.A", "Main.Topic.C"])
    assert transitive.ok
    assert transitive.value is not None
    assert transitive.value.passed is False
    assert transitive.value.issues[0].kind == "content_batch_dependency_present"
