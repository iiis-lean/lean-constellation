from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.foundation import FoundationContext
from lean_constellation.services.mathlib import MathlibService


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
    assert created.ok, created.issues


def _prelude_path(tmp_path: Path, service: MathlibService) -> Path:
    return service.runtime.foundation.prelude_path(FoundationContext(repo_root=tmp_path), "Main.Topic.Core")


def test_node_mathlib_hint_view_and_module_mutation(tmp_path: Path) -> None:
    service = make_runtime().mathlib
    _create_content_node(tmp_path, service)
    assert service.upsert_mathlib_module_entry(tmp_path, module="Mathlib.Data.Finset.Basic").ok

    empty = service.get_node_mathlib_hint_view(tmp_path, node_path="Main.Topic.Core")
    assert empty.ok and empty.value is not None
    assert empty.value.modules == []
    assert empty.value.declarations == []
    assert empty.value.validation_gate.passed is True

    added = service.add_node_mathlib_module_hint(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Data.Finset.Basic",
        reason="Finite sums.",
        actor="coordinator",
    )
    assert added.ok, added.issues
    assert added.value is not None
    assert added.value.changed is True
    assert added.value.changed_items == ["Mathlib.Data.Finset.Basic"]
    assert added.value.hints.modules[0].module == "Mathlib.Data.Finset.Basic"
    assert "import Mathlib.Data.Finset.Basic" in _prelude_path(tmp_path, service).read_text(encoding="utf-8")

    duplicate = service.add_node_mathlib_module_hint(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Data.Finset.Basic",
        reason="Duplicate.",
        actor="coordinator",
    )
    assert duplicate.ok
    assert duplicate.value is not None
    assert duplicate.value.changed is False
    assert duplicate.value.changed_items == []
    assert [issue.kind for issue in duplicate.issues] == ["mathlib_module_use_duplicate"]


def test_node_mathlib_decl_hint_mutation_and_missing_remove_noop(tmp_path: Path) -> None:
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

    added = service.add_node_mathlib_decl_hint(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="Finset.sum_congr",
        reason="Candidate theorem.",
        actor="worker",
    )
    assert added.ok, added.issues
    assert added.value is not None
    assert added.value.changed is True
    assert added.value.hints.declarations[0].module == "Mathlib.Data.Finset.Basic"
    assert not _prelude_path(tmp_path, service).exists()

    removed = service.remove_node_mathlib_decl_hint(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="Finset.sum_congr",
        actor="worker",
    )
    assert removed.ok, removed.issues
    assert removed.value is not None
    assert removed.value.changed is True
    assert removed.value.hints.declarations == []

    missing = service.remove_node_mathlib_decl_hint(
        tmp_path,
        node_path="Main.Topic.Core",
        decl_name="Finset.sum_congr",
        actor="worker",
    )
    assert missing.ok
    assert missing.value is not None
    assert missing.value.changed is False
    assert [issue.kind for issue in missing.issues] == ["mathlib_decl_use_missing"]


def test_node_mathlib_hint_view_surfaces_missing_index_warning(tmp_path: Path) -> None:
    service = make_runtime().mathlib
    _create_content_node(tmp_path, service)

    added = service.add_node_mathlib_module_hint(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Missing.Module",
        reason=None,
        actor="worker",
    )
    assert added.ok
    assert added.value is not None
    assert added.value.hints.validation_gate.passed is True
    assert [issue.kind for issue in added.issues] == ["mathlib_module_not_indexed"]
    assert [issue.kind for issue in added.value.hints.validation_gate.issues] == ["mathlib_module_not_indexed"]


def test_node_mathlib_hint_wrapper_preserves_remove_permission_gate(tmp_path: Path) -> None:
    service = make_runtime().mathlib
    _create_content_node(tmp_path, service)
    assert service.add_node_mathlib_module_hint(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Topology.Basic",
        reason=None,
        actor="coordinator",
    ).ok

    denied = service.remove_node_mathlib_module_hint(
        tmp_path,
        node_path="Main.Topic.Core",
        module="Mathlib.Topology.Basic",
        actor="worker",
    )

    assert not denied.ok
    assert denied.issues[0].kind == "mathlib_use_permission_denied"
