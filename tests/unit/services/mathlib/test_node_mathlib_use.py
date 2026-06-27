from pathlib import Path

from lean_constellation.services.foundation import FoundationContext, FoundationService, ServiceResult, WriteMode
from lean_constellation.services.mathlib import MathlibService, NodeMathlibUseComponent
from lean_constellation.services.node import NodeContractSnapshot, NodeTreeComponent


def _create_content_node(tmp_path: Path, service: MathlibService) -> None:
    tree = NodeTreeComponent(service.foundation)
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
    return service.foundation.prelude_path(FoundationContext(repo_root=tmp_path), "Main.Topic.Core")


class FailingProjection:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def refresh_prelude(self, repo_root: Path, *, node_path: str) -> ServiceResult[object]:
        return self.foundation.fail(self.foundation.issue("prelude_refresh_failed", "Prelude refresh failed.", object_ref=node_path))


def test_add_mathlib_module_use_refreshes_prelude_and_dedupes(tmp_path: Path) -> None:
    service = MathlibService()
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
    assert added.value.contract.mathlib_modules == [
        {
            "module": "Mathlib.Data.Finset.Basic",
            "reason": "Finite sums.",
            "added_by": "coordinator",
        }
    ]
    prelude_path = _prelude_path(tmp_path, service)
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
    assert len(duplicate.value.contract.mathlib_modules) == 1
    assert "import Mathlib.Data.Finset.Basic" in prelude_path.read_text(encoding="utf-8")


def test_worker_can_only_remove_worker_added_mathlib_module_use(tmp_path: Path) -> None:
    service = MathlibService()
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
    assert all(item["module"] != "Mathlib.Algebra.Group.Basic" for item in removed.value.contract.mathlib_modules)


def test_module_use_missing_invalid_and_coordinator_remove_branches(tmp_path: Path) -> None:
    service = MathlibService()
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
    assert not missing.ok
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
    assert removed.value.contract.mathlib_modules == []


def test_module_use_reports_prelude_refresh_failure(tmp_path: Path) -> None:
    foundation = FoundationService()
    component = NodeMathlibUseComponent(
        foundation,
        mathlib_index=MathlibService(foundation=foundation).mathlib_index,
        node_projection=FailingProjection(foundation),  # type: ignore[arg-type]
    )
    service = MathlibService(foundation=foundation, node_mathlib_use=component)
    _create_content_node(tmp_path, service)

    failed = service.add_mathlib_module_use(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Data.Finset.Basic",
        reason=None,
        actor="coordinator",
    )

    assert not failed.ok
    assert failed.issues[0].kind == "prelude_refresh_failed"


def test_add_mathlib_decl_use_records_hint_without_prelude_import(tmp_path: Path) -> None:
    service = MathlibService()
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
    assert added.value.contract.mathlib_decls == [
        {
            "name": "Finset.sum_congr",
            "module": "Mathlib.Data.Finset.Basic",
            "kind": "theorem",
            "reason": "Candidate theorem for formal proof.",
            "added_by": "worker",
        }
    ]
    assert not _prelude_path(tmp_path, service).exists()

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
    assert len(duplicate.value.contract.mathlib_decls) == 1

    removed = service.remove_mathlib_decl_use(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="Finset.sum_congr",
        actor="worker",
    )
    assert removed.ok
    assert removed.value is not None
    assert removed.value.contract.mathlib_decls == []


def test_add_mathlib_decl_use_missing_index_warning_and_remove_permission(tmp_path: Path) -> None:
    service = MathlibService()
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
    assert removed.value.contract.mathlib_decls == []


def test_decl_use_invalid_input_and_missing_remove(tmp_path: Path) -> None:
    service = MathlibService()
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
    assert not missing.ok
    assert missing.issues[0].kind == "mathlib_decl_use_missing"


def test_validate_node_mathlib_uses_reports_import_hint_and_invalid_entries(tmp_path: Path) -> None:
    service = MathlibService()
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

    foundation = FoundationService()
    contract_path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic.Core", 1)
    loaded = foundation.read_json(contract_path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.mathlib_modules.append({"module": "Bad Module", "added_by": "coordinator"})
    saved = foundation.write_json_atomic(contract_path, loaded.value, mode=WriteMode.UPDATE_EXISTING)
    assert saved.ok

    invalid = service.validate_node_mathlib_uses(tmp_path, node_path="Main.Topic.Core")
    assert invalid.ok
    assert invalid.value is not None
    assert invalid.value.passed is False
    assert invalid.value.issues[0].kind == "mathlib_module_name_invalid"


def test_validate_node_mathlib_uses_warning_policy_for_missing_index_entries(tmp_path: Path) -> None:
    service = MathlibService()
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


def test_validate_node_mathlib_uses_detects_duplicates_and_legacy_shapes(tmp_path: Path) -> None:
    service = MathlibService()
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
    foundation = FoundationService()
    contract_path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic.Core", 1)
    loaded = foundation.read_json(contract_path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.mathlib_modules = [
        "Mathlib.Data.Finset.Basic",
        {"module_name": "Mathlib.Data.Finset.Basic", "added_by": "worker"},
    ]
    loaded.value.mathlib_decls = [
        {"ref": {"name": "Finset.sum_congr", "module": "Mathlib.Data.Finset.Basic"}, "added_by": "worker"},
        "Finset.sum_congr",
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
