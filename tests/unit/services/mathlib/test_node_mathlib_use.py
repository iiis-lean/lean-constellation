import json

from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.foundation import FoundationContext, FoundationService, ServiceResult, WriteMode
from lean_constellation.services.mathlib import MathlibService, NodeMathlibUseComponent
from lean_constellation.services.node import NodeContractSnapshot
from lean_constellation.services.node.contract_fields import MathlibUseActor, NodeMathlibDeclUse, NodeMathlibModuleUse


def _create_content_node(tmp_path: Path, service: MathlibService) -> None:
    tree = service.runtime.node.node_tree
    assert tree.ensure_root_scope_node(tmp_path).ok
    assert tree.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    created = tree.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Formalize core topic.",
        boundary="Core topic only.",
        objective="Prepare Mathlib dependencies.",
        success_criteria="Core declarations can use configured Mathlib imports.",
    )
    assert created.ok


def _prelude_path(tmp_path: Path, service: MathlibService) -> Path:
    return service.runtime.foundation.prelude_path(FoundationContext(repo_root=tmp_path), "Main.Topic.Core")


class FailingProjection:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def refresh_prelude(self, repo_root: Path, *, node_path: str) -> ServiceResult[object]:
        return self.foundation.fail(self.foundation.issue("prelude_refresh_failed", "Prelude refresh failed.", object_ref=node_path))


def test_add_mathlib_module_use_refreshes_prelude_and_dedupes(tmp_path: Path) -> None:
    service = make_runtime().mathlib
    _create_content_node(tmp_path, service)
    assert service.upsert_mathlib_module_entry(tmp_path, module="Mathlib.Data.Finset.Basic").ok

    added = service.add_mathlib_module_use(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Data.Finset.Basic",
        reason="Finite sums.",
        actor="coordinator",
    )

    assert added.ok
    assert added.value is not None
    assert added.value.added_modules == [
        NodeMathlibModuleUse(
            module="Mathlib.Data.Finset.Basic",
            reason="Finite sums.",
            added_by=MathlibUseActor.COORDINATOR,
        )
    ]
    prelude_path = _prelude_path(tmp_path, service)
    assert added.value.managed_projection_changed is True
    assert added.value.changed_files == [str(prelude_path)]
    assert added.value.reread_required is True
    assert "import Mathlib.Data.Finset.Basic" in prelude_path.read_text(encoding="utf-8")
    prelude_path.unlink()

    duplicate = service.add_mathlib_module_use(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Data.Finset.Basic",
        reason="Duplicate.",
        actor="coordinator",
    )
    assert duplicate.ok
    assert duplicate.value is not None
    assert [issue.kind for issue in duplicate.issues] == ["mathlib_module_use_duplicate"]
    assert [item.module for item in duplicate.value.already_present_modules] == [
        "Mathlib.Data.Finset.Basic"
    ]
    assert "import Mathlib.Data.Finset.Basic" in prelude_path.read_text(encoding="utf-8")


def test_worker_can_only_remove_worker_added_mathlib_module_use(tmp_path: Path) -> None:
    service = make_runtime().mathlib
    _create_content_node(tmp_path, service)

    coordinator_added = service.add_mathlib_module_use(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Topology.Basic",
        reason=None,
        actor="coordinator",
    )
    assert coordinator_added.ok
    denied = service.remove_mathlib_module_use(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Topology.Basic",
        actor="worker",
    )
    assert not denied.ok
    assert denied.issues[0].kind == "mathlib_use_permission_denied"

    worker_added = service.add_mathlib_module_use(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Algebra.Group.Basic",
        reason="Worker discovered group API.",
        actor="worker",
    )
    assert worker_added.ok
    removed = service.remove_mathlib_module_use(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Algebra.Group.Basic",
        actor="worker",
    )
    assert removed.ok
    assert removed.value is not None
    assert [item.module for item in removed.value.removed_modules] == [
        "Mathlib.Algebra.Group.Basic"
    ]


def test_module_use_missing_invalid_and_coordinator_remove_branches(tmp_path: Path) -> None:
    service = make_runtime().mathlib
    _create_content_node(tmp_path, service)

    bad_actor = service.add_mathlib_module_use(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Data.Finset.Basic",
        reason=None,
        actor="reviewer",
    )
    assert not bad_actor.ok
    assert bad_actor.issues[0].kind == "mathlib_use_actor_invalid"

    bad_module = service.add_mathlib_module_use(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib Bad",
        reason=None,
        actor="coordinator",
    )
    assert not bad_module.ok
    assert bad_module.issues[0].kind == "mathlib_module_name_invalid"

    missing = service.remove_mathlib_module_use(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Data.Finset.Basic",
        actor="coordinator",
    )
    assert missing.ok
    assert missing.value is not None
    assert missing.value.changed is False
    assert missing.issues[0].kind == "mathlib_module_use_missing"

    added = service.add_mathlib_module_use(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Data.Finset.Basic",
        reason=None,
        actor="coordinator",
    )
    assert added.ok
    removed = service.remove_mathlib_module_use(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Data.Finset.Basic",
        actor="coordinator",
    )
    assert removed.ok
    assert removed.value is not None
    assert [item.module for item in removed.value.removed_modules] == [
        "Mathlib.Data.Finset.Basic"
    ]


def test_module_use_reports_prelude_refresh_failure(tmp_path: Path) -> None:
    runtime = make_runtime()
    foundation = runtime.foundation
    component = NodeMathlibUseComponent(
        runtime,
        mathlib_index=runtime.mathlib.mathlib_index,
        node_projection=FailingProjection(foundation),  # type: ignore[arg-type]
    )
    service = MathlibService(runtime, mathlib_index=runtime.mathlib.mathlib_index, node_mathlib_use=component)
    _create_content_node(tmp_path, service)
    assert runtime.node.commit_content_contract(
        tmp_path,
        node_path="Main.Topic.Core",
        summary="Commit before exercising transaction rollback.",
    ).ok

    failed = service.add_mathlib_module_use(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Data.Finset.Basic",
        reason=None,
        actor="coordinator",
    )

    assert not failed.ok
    assert failed.issues[0].kind == "prelude_refresh_failed"
    current = runtime.node.contract.get_current_contract(tmp_path, node_path="Main.Topic.Core")
    assert current.ok and current.value is not None
    assert current.value.contract.mathlib_modules == []
    assert current.value.contract.version == 1
    assert current.value.status.value == "committed"
    node = runtime.node.node_tree.get_node(tmp_path, path="Main.Topic.Core")
    assert node.ok and node.value is not None
    assert node.value.open_contract_version is None


def test_add_mathlib_decl_use_records_hint_and_refreshes_prelude_import(tmp_path: Path) -> None:
    service = make_runtime().mathlib
    _create_content_node(tmp_path, service)
    assert service.upsert_mathlib_module_entry(tmp_path, module="Mathlib.Data.Finset.Basic").ok
    assert service.upsert_mathlib_decl_entry(
        tmp_path,
        name="Finset.sum_congr",
        module="Mathlib.Data.Finset.Basic",
        kind="theorem",
        signature="Finset.sum_congr : ...",
        summary="Congruence for finite sums.",
        note=None,
    ).ok

    added = service.add_mathlib_decl_use(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="Finset.sum_congr",
        reason="Candidate theorem for formal proof.",
        actor="worker",
    )

    assert added.ok
    assert added.value is not None
    assert added.value.added_declarations == [
        NodeMathlibDeclUse(
            name="Finset.sum_congr",
            module="Mathlib.Data.Finset.Basic",
            kind="theorem",
            reason="Candidate theorem for formal proof.",
            added_by=MathlibUseActor.WORKER,
        )
    ]
    prelude_path = _prelude_path(tmp_path, service)
    assert added.value.managed_projection_changed is True
    assert added.value.changed_files == [str(prelude_path)]
    assert added.value.reread_required is True
    assert "import Mathlib.Data.Finset.Basic" in prelude_path.read_text(encoding="utf-8")
    prelude_path.unlink()

    duplicate = service.add_mathlib_decl_use(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="Finset.sum_congr",
        reason="Duplicate hint.",
        actor="worker",
    )
    assert duplicate.ok
    assert duplicate.value is not None
    assert [issue.kind for issue in duplicate.issues] == ["mathlib_decl_use_duplicate"]
    assert [item.name for item in duplicate.value.already_present_declarations] == [
        "Finset.sum_congr"
    ]
    assert "import Mathlib.Data.Finset.Basic" in prelude_path.read_text(encoding="utf-8")

    removed = service.remove_mathlib_decl_use(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="Finset.sum_congr",
        actor="worker",
    )
    assert removed.ok
    assert removed.value is not None
    assert [item.name for item in removed.value.removed_declarations] == [
        "Finset.sum_congr"
    ]
    assert "import Mathlib.Data.Finset.Basic" not in prelude_path.read_text(encoding="utf-8")


def test_add_mathlib_decl_use_missing_index_warning_and_remove_permission(tmp_path: Path) -> None:
    service = make_runtime().mathlib
    _create_content_node(tmp_path, service)

    added = service.add_mathlib_decl_use(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="Missing.decl",
        reason=None,
        actor="coordinator",
    )
    assert added.ok
    assert [issue.kind for issue in added.issues] == ["mathlib_decl_not_indexed"]

    denied = service.remove_mathlib_decl_use(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="Missing.decl",
        actor="worker",
    )
    assert not denied.ok
    assert denied.issues[0].kind == "mathlib_use_permission_denied"

    removed = service.remove_mathlib_decl_use(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="Missing.decl",
        actor="coordinator",
    )
    assert removed.ok
    assert removed.value is not None
    assert [item.name for item in removed.value.removed_declarations] == [
        "Missing.decl"
    ]


def test_decl_use_invalid_input_and_missing_remove(tmp_path: Path) -> None:
    service = make_runtime().mathlib
    _create_content_node(tmp_path, service)

    bad_decl = service.add_mathlib_decl_use(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="Bad Decl",
        reason=None,
        actor="worker",
    )
    assert not bad_decl.ok
    assert bad_decl.issues[0].kind == "mathlib_decl_name_invalid"

    missing = service.remove_mathlib_decl_use(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="Missing.decl",
        actor="coordinator",
    )
    assert missing.ok
    assert missing.value is not None
    assert missing.value.changed is False
    assert missing.issues[0].kind == "mathlib_decl_use_missing"


def test_validate_node_mathlib_uses_reports_import_hint_and_invalid_entries(tmp_path: Path) -> None:
    service = make_runtime().mathlib
    _create_content_node(tmp_path, service)
    assert service.upsert_mathlib_module_entry(tmp_path, module="Mathlib.Data.Finset.Basic").ok
    assert service.upsert_mathlib_decl_entry(
        tmp_path,
        name="Finset.sum_congr",
        module="Mathlib.Data.Finset.Basic",
        kind="theorem",
        signature="Finset.sum_congr : ...",
        summary="Congruence for finite sums.",
        note=None,
    ).ok
    assert service.add_mathlib_decl_use(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="Finset.sum_congr",
        reason=None,
        actor="worker",
    ).ok

    checked = service.validate_node_mathlib_uses(tmp_path, node_path="Main.Topic.Core")
    assert checked.ok
    assert checked.value is not None
    assert checked.value.passed is True
    assert [issue.kind for issue in checked.value.issues] == ["mathlib_decl_module_not_imported"]

    foundation = make_runtime().foundation
    contract_path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic.Core", 1)
    loaded = foundation.read_json(contract_path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.mathlib_modules.append(NodeMathlibModuleUse(module="Bad Module", added_by=MathlibUseActor.COORDINATOR))
    saved = foundation.write_json_atomic(contract_path, loaded.value, mode=WriteMode.UPDATE_EXISTING)
    assert saved.ok

    invalid = service.validate_node_mathlib_uses(tmp_path, node_path="Main.Topic.Core")
    assert invalid.ok
    assert invalid.value is not None
    assert invalid.value.passed is False
    assert invalid.value.issues[0].kind == "mathlib_module_name_invalid"


def test_validate_node_mathlib_uses_warning_policy_for_missing_index_entries(tmp_path: Path) -> None:
    service = make_runtime().mathlib
    _create_content_node(tmp_path, service)
    assert service.add_mathlib_module_use(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Missing.Module",
        reason=None,
        actor="coordinator",
    ).ok
    assert service.add_mathlib_decl_use(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="Missing.decl",
        reason=None,
        actor="worker",
    ).ok

    checked = service.validate_node_mathlib_uses(tmp_path, node_path="Main.Topic.Core")

    assert checked.ok
    assert checked.value is not None
    assert checked.value.passed is True
    assert [issue.kind for issue in checked.value.issues] == [
        "mathlib_module_not_indexed",
        "mathlib_decl_not_indexed",
    ]


def test_validate_node_mathlib_uses_detects_duplicates_and_rejects_legacy_shapes(tmp_path: Path) -> None:
    service = make_runtime().mathlib
    _create_content_node(tmp_path, service)
    assert service.upsert_mathlib_module_entry(tmp_path, module="Mathlib.Data.Finset.Basic").ok
    assert service.upsert_mathlib_decl_entry(
        tmp_path,
        name="Finset.sum_congr",
        module="Mathlib.Data.Finset.Basic",
        kind="theorem",
        signature="Finset.sum_congr : ...",
        summary="Congruence for finite sums.",
        note=None,
    ).ok
    foundation = make_runtime().foundation
    contract_path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic.Core", 1)
    loaded = foundation.read_json(contract_path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.mathlib_modules = [
        NodeMathlibModuleUse(module="Mathlib.Data.Finset.Basic", added_by=MathlibUseActor.COORDINATOR),
        NodeMathlibModuleUse(module="Mathlib.Data.Finset.Basic", added_by=MathlibUseActor.WORKER),
    ]
    loaded.value.mathlib_decls = [
        NodeMathlibDeclUse(name="Finset.sum_congr", module="Mathlib.Data.Finset.Basic", added_by=MathlibUseActor.WORKER),
        NodeMathlibDeclUse(name="Finset.sum_congr", module="Mathlib.Data.Finset.Basic", added_by=MathlibUseActor.COORDINATOR),
    ]
    assert foundation.write_json_atomic(contract_path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok

    checked = service.validate_node_mathlib_uses(tmp_path, node_path="Main.Topic.Core")

    assert checked.ok
    assert checked.value is not None
    assert checked.value.passed is False
    assert [issue.kind for issue in checked.value.issues] == [
        "mathlib_module_use_duplicate",
        "mathlib_decl_use_duplicate",
    ]

    payload = loaded.value.model_dump(mode="json")
    payload["mathlib_modules"] = ["Mathlib.Data.Finset.Basic"]
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    legacy = service.validate_node_mathlib_uses(tmp_path, node_path="Main.Topic.Core")
    assert not legacy.ok
    assert legacy.issues[0].kind == "schema_validation_failed"
