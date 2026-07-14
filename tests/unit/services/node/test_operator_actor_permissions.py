from __future__ import annotations

from lean_constellation.services.node.contract_fields import (
    MaterialRefActor,
    MathlibUseActor,
    NodeDepActor,
)

from tests.unit_services_helpers import make_runtime


def _tree(repo_root):  # noqa: ANN001, ANN202
    runtime = make_runtime()
    tree = runtime.node.node_tree
    assert tree.ensure_root_scope_node(repo_root).ok
    assert tree.create_scope_node(
        repo_root, path="Main.Provider", goal="Provider goal.", boundary="Provider boundary."
    ).ok
    assert runtime.node.commit_scope_contract(
        repo_root, scope_path="Main.Provider", summary="Provider ready."
    ).ok
    assert tree.create_content_node(
        repo_root,
        path="Main.Consumer",
        goal="Consumer goal.",
        boundary="Consumer boundary.",
        objective="Consume provider.",
        success_criteria="Dependency recorded.",
    ).ok
    return runtime


def test_node_dep_operator_ownership_is_isolated_from_agents(tmp_path) -> None:
    runtime = _tree(tmp_path)
    deps = runtime.node.dependency
    added = deps.add_node_dep(
        tmp_path,
        node_path="Main.Consumer",
        target_node="Main.Provider",
        reason="Operator-managed dependency.",
        actor=NodeDepActor.OPERATOR,
    )
    assert added.ok
    assert added.value is not None
    assert added.value.contract.deps[0].added_by == NodeDepActor.OPERATOR

    for actor in (NodeDepActor.WORKER, NodeDepActor.COORDINATOR):
        denied = deps.remove_node_dep(tmp_path, node_path="Main.Consumer", index=0, actor=actor)
        assert not denied.ok
        assert denied.issues[0].kind == "node_dep_permission_denied"
    removed = deps.remove_node_dep(
        tmp_path, node_path="Main.Consumer", index=0, actor=NodeDepActor.OPERATOR
    )
    assert removed.ok


def test_operator_can_only_remove_operator_owned_mathlib_use(tmp_path) -> None:
    runtime = _tree(tmp_path)
    service = runtime.mathlib
    assert service.add_mathlib_module_use(
        tmp_path,
        node_path="Main.Consumer",
        module="Mathlib.Data.Nat.Basic",
        reason=None,
        actor=MathlibUseActor.COORDINATOR,
    ).ok
    denied = service.remove_mathlib_module_use(
        tmp_path,
        node_path="Main.Consumer",
        module="Mathlib.Data.Nat.Basic",
        actor=MathlibUseActor.OPERATOR,
    )
    assert not denied.ok
    assert denied.issues[0].kind == "mathlib_use_permission_denied"


def test_operator_owned_material_ref_is_protected_from_agents(tmp_path) -> None:
    runtime = _tree(tmp_path)
    source = tmp_path / ".lean_constellation" / "source" / "notes.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("operator material\n", encoding="utf-8")
    refs = runtime.node.material_ref
    added = refs.add_owned_source_ref(
        tmp_path,
        node_path="Main.Consumer",
        path="notes.md",
        reason="Operator source.",
        actor=MaterialRefActor.OPERATOR,
    )
    assert added.ok and added.value is not None
    assert added.value.contract.owned_refs[0].added_by == MaterialRefActor.OPERATOR
    for actor in (MaterialRefActor.WORKER, MaterialRefActor.COORDINATOR):
        denied = refs.remove_owned_ref(
            tmp_path, node_path="Main.Consumer", index=0, actor=actor
        )
        assert not denied.ok
        assert denied.issues[0].kind == "material_ref_permission_denied"
    assert refs.remove_owned_ref(
        tmp_path, node_path="Main.Consumer", index=0, actor=MaterialRefActor.OPERATOR
    ).ok


def test_actor_enums_include_latest_only_operator_value() -> None:
    assert NodeDepActor("operator") is NodeDepActor.OPERATOR
    assert MaterialRefActor("operator") is MaterialRefActor.OPERATOR
    assert MathlibUseActor("operator") is MathlibUseActor.OPERATOR
