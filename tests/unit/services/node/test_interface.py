from pathlib import Path

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.foundation import FoundationContext, FoundationService, ServiceResult, WriteMode
from lean_constellation.services.node import ContractComponent, DeclPublicView, ExportComponent, InterfaceComponent, NodeContractSnapshot, NodeTreeComponent


class FakePublicDeclProvider:
    def __init__(self, foundation: FoundationService, decls: dict[str, list[DeclPublicView]]) -> None:
        self.foundation = foundation
        self.decls = decls

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        return self.foundation.ok(self.decls.get(node_path, []))


def _write_preparation_input(tmp_path: Path, *, interfaces: list[DeclInterface]) -> None:
    foundation = FoundationService()
    input_value = RepoPreparationInput(
        goal="Formalize the repo goal.",
        source_corpus_mode=SourceCorpusMode.PREPARE,
        source_corpus_relpath=".lean_constellation/source",
        interface_inputs=interfaces,
    )
    path = foundation.layout.preparation_input_path(FoundationContext(repo_root=tmp_path))
    result = foundation.store.write_json_atomic(path, input_value)
    assert result.ok


def _init_main(tmp_path: Path, *, interfaces: list[DeclInterface]) -> None:
    _write_preparation_input(tmp_path, interfaces=interfaces)
    contract = ContractComponent()
    result = contract.initialize_main_contract_from_preparation_input(
        tmp_path,
        boundary="Main boundary.",
        objective="Main objective.",
    )
    assert result.ok


def _create_content_node(tmp_path: Path, path: str) -> None:
    tree = NodeTreeComponent()
    if "." in path:
        parent = path.rsplit(".", 1)[0]
        if parent != "Main":
            assert tree.create_scope_node(tmp_path, path=parent, goal=f"{parent} goal", boundary=f"{parent} boundary").ok
    assert tree.create_content_node(
        tmp_path,
        path=path,
        goal=f"{path} goal",
        boundary=f"{path} boundary",
        objective=f"Build {path}.",
        success_criteria=f"{path} ready.",
    ).ok


def _contract_path(tmp_path: Path, node_path: str, version: int = 1) -> Path:
    foundation = FoundationService()
    return foundation.layout.node_contract_path(FoundationContext(repo_root=tmp_path), node_path, version)


def _load_contract(tmp_path: Path, node_path: str, version: int = 1) -> NodeContractSnapshot:
    foundation = FoundationService()
    loaded = foundation.store.read_json(_contract_path(tmp_path, node_path, version), NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    return loaded.value


def _write_contract(tmp_path: Path, node_path: str, contract: NodeContractSnapshot) -> None:
    foundation = FoundationService()
    saved = foundation.store.write_json_atomic(
        _contract_path(tmp_path, node_path, contract.version),
        contract,
        mode=WriteMode.UPDATE_EXISTING,
    )
    assert saved.ok


def _component_with_public_decls(tmp_path: Path, decls: dict[str, list[DeclPublicView]]) -> tuple[InterfaceComponent, ExportComponent]:
    foundation = FoundationService()
    provider = FakePublicDeclProvider(foundation, decls)
    export = ExportComponent(foundation=foundation, public_decl_provider=provider)
    return InterfaceComponent(foundation=foundation, export=export), export


def test_add_update_remove_supplement_interface(tmp_path: Path) -> None:
    protected = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Main theorem.")
    _init_main(tmp_path, interfaces=[protected])
    component = InterfaceComponent()

    added = component.add_interface(
        tmp_path,
        node_path="Main",
        name="core_definition",
        kind=DeclKind.DEFINITION,
        summary="Expose the core definition.",
        statement_hint="Definition-level interface.",
        actor="coordinator",
    )
    assert added.ok
    assert added.value is not None
    assert [item.name for item in added.value.contract.interfaces] == ["main_result", "core_definition"]

    duplicate = component.add_interface(
        tmp_path,
        node_path="Main",
        name="core_definition",
        kind=DeclKind.DEFINITION,
        summary="Duplicate.",
        actor="coordinator",
    )
    assert not duplicate.ok
    assert duplicate.issues[0].kind == "interface_duplicate"

    updated = component.update_interface(
        tmp_path,
        node_path="Main",
        name="core_definition",
        summary="Updated definition interface.",
        actor="coordinator",
    )
    assert updated.ok
    assert updated.value is not None
    assert updated.value.contract.interfaces[1].summary == "Updated definition interface."

    removed = component.remove_interface(tmp_path, node_path="Main", name="core_definition", actor="coordinator")
    assert removed.ok
    assert removed.value is not None
    assert [item.name for item in removed.value.contract.interfaces] == ["main_result"]


def test_add_interface_rejects_invalid_inputs(tmp_path: Path) -> None:
    _init_main(tmp_path, interfaces=[])
    component = InterfaceComponent()

    missing_name = component.add_interface(
        tmp_path,
        node_path="Main",
        name=" ",
        kind=DeclKind.THEOREM,
        summary="A theorem.",
        actor="coordinator",
    )
    assert not missing_name.ok
    assert missing_name.issues[0].kind == "interface_name_required"

    invalid_kind = component.add_interface(
        tmp_path,
        node_path="Main",
        name="bad_kind",
        kind="not-a-kind",
        summary="A theorem.",
        actor="coordinator",
    )
    assert not invalid_kind.ok
    assert invalid_kind.issues[0].kind == "interface_kind_invalid"

    missing_summary = component.add_interface(
        tmp_path,
        node_path="Main",
        name="missing_summary",
        kind=DeclKind.THEOREM,
        summary="",
        actor="coordinator",
    )
    assert not missing_summary.ok
    assert missing_summary.issues[0].kind == "interface_summary_required"


def test_protected_interface_cannot_be_added_updated_or_removed_manually(tmp_path: Path) -> None:
    protected = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Main theorem.")
    _init_main(tmp_path, interfaces=[protected])
    component = InterfaceComponent()

    manual_add = component.add_interface(
        tmp_path,
        node_path="Main",
        name="main_result",
        kind=DeclKind.THEOREM,
        summary="Main theorem.",
        actor="coordinator",
    )
    assert not manual_add.ok
    assert manual_add.issues[0].kind == "protected_interface_requires_sync"

    update = component.update_interface(
        tmp_path,
        node_path="Main",
        name="main_result",
        summary="Changed.",
        actor="coordinator",
    )
    assert not update.ok
    assert update.issues[0].kind == "protected_interface_update_forbidden"

    remove = component.remove_interface(tmp_path, node_path="Main", name="main_result", actor="coordinator")
    assert not remove.ok
    assert remove.issues[0].kind == "protected_interface_remove_forbidden"


def test_update_interface_reports_missing_empty_summary_and_statement_hint_changes(tmp_path: Path) -> None:
    _init_main(tmp_path, interfaces=[])
    component = InterfaceComponent()
    assert component.add_interface(
        tmp_path,
        node_path="Main",
        name="supplement",
        kind=DeclKind.LEMMA,
        summary="Supplement lemma.",
        statement_hint="Initial hint.",
        actor="coordinator",
    ).ok

    missing = component.update_interface(
        tmp_path,
        node_path="Main",
        name="missing",
        summary="Updated.",
        actor="coordinator",
    )
    assert not missing.ok
    assert missing.issues[0].kind == "interface_missing"

    empty_summary = component.update_interface(
        tmp_path,
        node_path="Main",
        name="supplement",
        summary=" ",
        actor="coordinator",
    )
    assert not empty_summary.ok
    assert empty_summary.issues[0].kind == "interface_summary_required"

    updated = component.update_interface(
        tmp_path,
        node_path="Main",
        name="supplement",
        statement_hint="Refined statement hint.",
        actor="coordinator",
    )
    assert updated.ok
    assert updated.value is not None
    assert updated.value.contract.interfaces[0].note == "Refined statement hint."

    cleared = component.update_interface(
        tmp_path,
        node_path="Main",
        name="supplement",
        statement_hint=" ",
        actor="coordinator",
    )
    assert cleared.ok
    assert cleared.value is not None
    assert cleared.value.contract.interfaces[0].note is None


def test_sync_and_check_protected_root_interfaces(tmp_path: Path) -> None:
    protected = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Main theorem.")
    _write_preparation_input(tmp_path, interfaces=[protected])
    tree = NodeTreeComponent()
    assert tree.ensure_root_scope_node(tmp_path).ok
    component = InterfaceComponent()

    missing_gate = component.check_protected_root_interfaces(tmp_path)
    assert missing_gate.ok
    assert missing_gate.value is not None
    assert missing_gate.value.passed is False
    assert missing_gate.value.issues[0].kind == "protected_interface_missing"

    synced = component.sync_protected_root_interfaces_from_preparation_input(tmp_path)
    assert synced.ok
    assert synced.value is not None
    assert [item.name for item in synced.value.contract.interfaces] == ["main_result"]

    gate = component.check_root_interfaces_include_preparation_inputs(tmp_path)
    assert gate.ok
    assert gate.value is not None
    assert gate.value.passed is True


def test_sync_protected_root_interfaces_rejects_conflict(tmp_path: Path) -> None:
    protected = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Main theorem.")
    _write_preparation_input(tmp_path, interfaces=[protected])
    tree = NodeTreeComponent()
    assert tree.ensure_root_scope_node(tmp_path).ok
    component = InterfaceComponent()
    contract = _load_contract(tmp_path, "Main")
    contract.interfaces.append(DeclInterface(name="main_result", kind=DeclKind.DEFINITION, summary="Conflicting supplement."))
    _write_contract(tmp_path, "Main", contract)

    synced = component.sync_protected_root_interfaces_from_preparation_input(tmp_path)

    assert not synced.ok
    assert synced.issues[0].kind == "protected_interface_conflict"


def test_protected_root_interface_modified_gate_fails(tmp_path: Path) -> None:
    protected = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Main theorem.")
    _init_main(tmp_path, interfaces=[protected])
    foundation = FoundationService()
    path = foundation.layout.node_contract_path(FoundationContext(repo_root=tmp_path), "Main", 1)
    loaded = foundation.store.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.interfaces[0].summary = "Modified summary."
    assert foundation.store.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok
    component = InterfaceComponent()

    gate = component.check_protected_root_interfaces(tmp_path)

    assert gate.ok
    assert gate.value is not None
    assert gate.value.passed is False
    assert gate.value.issues[0].kind == "protected_interface_modified"


def test_protected_root_interface_gate_reports_duplicate_names(tmp_path: Path) -> None:
    protected = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Main theorem.")
    _init_main(tmp_path, interfaces=[protected])
    contract = _load_contract(tmp_path, "Main")
    contract.interfaces.append(DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Main theorem."))
    _write_contract(tmp_path, "Main", contract)
    component = InterfaceComponent()

    gate = component.check_protected_root_interfaces(tmp_path)

    assert gate.ok
    assert gate.value is not None
    assert gate.value.passed is False
    assert "interface_duplicate" in {issue.kind for issue in gate.value.issues}


def test_remove_bound_interface_requires_unbind_first(tmp_path: Path) -> None:
    _init_main(tmp_path, interfaces=[])
    component = InterfaceComponent()
    added = component.add_interface(
        tmp_path,
        node_path="Main",
        name="supplement",
        kind=DeclKind.THEOREM,
        summary="Supplement theorem.",
        actor="coordinator",
    )
    assert added.ok
    foundation = FoundationService()
    path = foundation.layout.node_contract_path(FoundationContext(repo_root=tmp_path), "Main", 1)
    loaded = foundation.store.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.interfaces[0].bound_decl = DeclRef(node="Main.Topic.Core", name="supplement")
    assert foundation.store.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok

    removed = component.remove_interface(tmp_path, node_path="Main", name="supplement", actor="coordinator")

    assert not removed.ok
    assert removed.issues[0].kind == "interface_bound"


def test_remove_and_unbind_report_missing_and_unbind_is_idempotent(tmp_path: Path) -> None:
    _init_main(tmp_path, interfaces=[])
    component = InterfaceComponent()
    assert component.add_interface(
        tmp_path,
        node_path="Main",
        name="supplement",
        kind=DeclKind.THEOREM,
        summary="Supplement theorem.",
        actor="coordinator",
    ).ok

    missing_remove = component.remove_interface(tmp_path, node_path="Main", name="missing", actor="coordinator")
    assert not missing_remove.ok
    assert missing_remove.issues[0].kind == "interface_missing"

    missing_unbind = component.unbind_interface(tmp_path, node_path="Main", interface_name="missing")
    assert not missing_unbind.ok
    assert missing_unbind.issues[0].kind == "interface_missing"

    first_unbind = component.unbind_interface(tmp_path, node_path="Main", interface_name="supplement")
    assert first_unbind.ok
    assert first_unbind.value is not None
    assert first_unbind.value.changed is False


def test_list_interfaces_returns_sorted_bound_views(tmp_path: Path) -> None:
    protected = DeclInterface(name="z_protected", kind=DeclKind.THEOREM, summary="Protected theorem.")
    _init_main(tmp_path, interfaces=[protected])
    component = InterfaceComponent()
    assert component.add_interface(
        tmp_path,
        node_path="Main",
        name="a_supplement",
        kind=DeclKind.DEFINITION,
        summary="Supplement definition.",
        actor="coordinator",
    ).ok
    contract = _load_contract(tmp_path, "Main")
    contract.interfaces[0].bound_decl = DeclRef(node="Main.Core", name="z_protected", revision=1)
    _write_contract(tmp_path, "Main", contract)

    listed = component.list_interfaces(tmp_path, node_path="Main")

    assert listed.ok
    assert listed.value is not None
    assert [item.name for item in listed.value.interfaces] == ["a_supplement", "z_protected"]
    assert listed.value.interfaces[1].protected is True
    assert listed.value.interfaces[1].bound_decl == DeclRef(node="Main.Core", name="z_protected", revision=1)


def test_bind_and_unbind_content_interface_to_public_decl(tmp_path: Path) -> None:
    _init_main(tmp_path, interfaces=[])
    _create_content_node(tmp_path, "Main.Core")
    component, _export = _component_with_public_decls(
        tmp_path,
        {
            "Main.Core": [
                DeclPublicView(
                    ref=DeclRef(node="Main.Core", name="main_result", revision=1),
                    kind=DeclKind.THEOREM.value,
                    summary="Main theorem.",
                )
            ]
        },
    )
    assert component.add_interface(
        tmp_path,
        node_path="Main.Core",
        name="core_result",
        kind=DeclKind.THEOREM,
        summary="Core result interface.",
        actor="coordinator",
    ).ok

    bound = component.bind_interface_to_decl(
        tmp_path,
        node_path="Main.Core",
        interface_name="core_result",
        decl_name="main_result",
    )

    assert bound.ok
    assert bound.value is not None
    assert bound.value.changed is True
    assert bound.value.bound_decl == DeclRef(node="Main.Core", name="main_result", revision=1)
    assert "import Main.Core.Theorems.main_result" in (tmp_path / "Main" / "Core" / "Interfaces.lean").read_text(encoding="utf-8")

    unbound = component.unbind_interface(tmp_path, node_path="Main.Core", interface_name="core_result")
    assert unbound.ok
    assert unbound.value is not None
    assert unbound.value.changed is True
    assert unbound.value.bound_decl is None


def test_bind_content_interface_rejects_kind_mismatch(tmp_path: Path) -> None:
    _init_main(tmp_path, interfaces=[])
    _create_content_node(tmp_path, "Main.Core")
    component, _export = _component_with_public_decls(
        tmp_path,
        {
            "Main.Core": [
                DeclPublicView(
                    ref=DeclRef(node="Main.Core", name="main_result", revision=1),
                    kind=DeclKind.THEOREM.value,
                    summary="Main theorem.",
                )
            ]
        },
    )
    assert component.add_interface(
        tmp_path,
        node_path="Main.Core",
        name="core_definition",
        kind=DeclKind.DEFINITION,
        summary="Core definition interface.",
        actor="coordinator",
    ).ok

    bound = component.bind_interface_to_decl(
        tmp_path,
        node_path="Main.Core",
        interface_name="core_definition",
        decl_name="main_result",
    )

    assert not bound.ok
    assert bound.issues[0].kind == "interface_binding_kind_mismatch"


def test_bind_content_interface_reports_missing_decl_name_and_outside_target(tmp_path: Path) -> None:
    _init_main(tmp_path, interfaces=[])
    _create_content_node(tmp_path, "Main.Core")
    component, _export = _component_with_public_decls(tmp_path, {"Main.Core": []})
    assert component.add_interface(
        tmp_path,
        node_path="Main.Core",
        name="core_result",
        kind=DeclKind.THEOREM,
        summary="Core result interface.",
        actor="coordinator",
    ).ok

    missing_interface = component.bind_interface_to_decl(
        tmp_path,
        node_path="Main.Core",
        interface_name="missing",
        decl_name="main_result",
    )
    assert not missing_interface.ok
    assert missing_interface.issues[0].kind == "interface_missing"

    missing_decl = component.bind_interface_to_decl(
        tmp_path,
        node_path="Main.Core",
        interface_name="core_result",
        decl_name=" ",
    )
    assert not missing_decl.ok
    assert missing_decl.issues[0].kind == "decl_name_required"

    outside = component.bind_interface_to_decl(
        tmp_path,
        node_path="Main.Core",
        interface_name="core_result",
        decl_name="main_result",
        decl_node="Main.Other",
    )
    assert not outside.ok
    assert outside.issues[0].kind == "interface_binding_decl_outside_content"


def test_bind_content_interface_reports_public_readiness_ambiguity_and_kind_edges(tmp_path: Path) -> None:
    _init_main(tmp_path, interfaces=[])
    _create_content_node(tmp_path, "Main.Core")
    component, _export = _component_with_public_decls(
        tmp_path,
        {
            "Main.Core": [
                DeclPublicView(ref=DeclRef(node="Main.Core", name="hidden", revision=1), kind=DeclKind.THEOREM.value, public=False),
                DeclPublicView(ref=DeclRef(node="Main.Core", name="duplicate", revision=1), kind=DeclKind.THEOREM.value),
                DeclPublicView(ref=DeclRef(node="Main.Core", name="duplicate", revision=2), kind=DeclKind.THEOREM.value),
                DeclPublicView(ref=DeclRef(node="Main.Core", name="not_ready", revision=1), kind=DeclKind.THEOREM.value, ready=False),
                DeclPublicView(ref=DeclRef(node="Main.Core", name="stale", revision=1), kind=DeclKind.THEOREM.value, stale=True),
                DeclPublicView(ref=DeclRef(node="Main.Core", name="unknown_kind", revision=1), kind=None),
                DeclPublicView(ref=DeclRef(node="Main.Core", name="invalid_kind", revision=1), kind="not-a-kind"),
            ]
        },
    )
    assert component.add_interface(
        tmp_path,
        node_path="Main.Core",
        name="core_result",
        kind=DeclKind.THEOREM,
        summary="Core result interface.",
        actor="coordinator",
    ).ok

    not_public = component.bind_interface_to_decl(tmp_path, node_path="Main.Core", interface_name="core_result", decl_name="hidden")
    assert not not_public.ok
    assert not_public.issues[0].kind == "interface_binding_decl_not_public"

    ambiguous = component.bind_interface_to_decl(tmp_path, node_path="Main.Core", interface_name="core_result", decl_name="duplicate")
    assert not ambiguous.ok
    assert ambiguous.issues[0].kind == "interface_binding_decl_ambiguous"

    not_ready = component.bind_interface_to_decl(tmp_path, node_path="Main.Core", interface_name="core_result", decl_name="not_ready")
    assert not not_ready.ok
    assert not_ready.issues[0].kind == "interface_binding_decl_not_ready"

    stale = component.bind_interface_to_decl(tmp_path, node_path="Main.Core", interface_name="core_result", decl_name="stale")
    assert not stale.ok
    assert stale.issues[0].kind == "interface_binding_decl_not_ready"

    component, _export = _component_with_public_decls(
        tmp_path,
        {
            "Main.Core": [
                DeclPublicView(ref=DeclRef(node="Main.Core", name="unknown_kind", revision=1), kind=None),
                DeclPublicView(ref=DeclRef(node="Main.Core", name="invalid_kind", revision=1), kind="not-a-kind"),
            ]
        },
    )
    unknown_kind = component.bind_interface_to_decl(tmp_path, node_path="Main.Core", interface_name="core_result", decl_name="unknown_kind")
    assert unknown_kind.ok
    assert [issue.kind for issue in unknown_kind.issues] == ["interface_binding_kind_check_deferred"]
    assert component.unbind_interface(tmp_path, node_path="Main.Core", interface_name="core_result").ok

    invalid_kind = component.bind_interface_to_decl(tmp_path, node_path="Main.Core", interface_name="core_result", decl_name="invalid_kind")
    assert not invalid_kind.ok
    assert invalid_kind.issues[0].kind == "interface_binding_kind_invalid"


def test_bind_scope_interface_requires_current_scope_export(tmp_path: Path) -> None:
    _init_main(tmp_path, interfaces=[])
    _create_content_node(tmp_path, "Main.Topic.Core")
    component, export = _component_with_public_decls(
        tmp_path,
        {
            "Main.Topic.Core": [
                DeclPublicView(
                    ref=DeclRef(node="Main.Topic.Core", name="main_result", revision=1),
                    kind=DeclKind.THEOREM.value,
                    summary="Main theorem.",
                )
            ]
        },
    )
    assert component.add_interface(
        tmp_path,
        node_path="Main.Topic",
        name="topic_result",
        kind=DeclKind.THEOREM,
        summary="Topic result interface.",
        actor="coordinator",
    ).ok

    missing_export = component.bind_interface_to_decl(
        tmp_path,
        node_path="Main.Topic",
        interface_name="topic_result",
        decl_name="main_result",
        decl_node="Main.Topic.Core",
    )
    assert not missing_export.ok
    assert missing_export.issues[0].kind == "interface_binding_decl_not_exported"

    assert export.add_scope_export(tmp_path, scope_path="Main.Topic", decl_ref="Main.Topic.Core:main_result").ok
    bound = component.bind_interface_to_decl(
        tmp_path,
        node_path="Main.Topic",
        interface_name="topic_result",
        decl_name="main_result",
        decl_node="Main.Topic.Core",
    )

    assert bound.ok
    assert bound.value is not None
    assert bound.value.bound_decl == DeclRef(node="Main.Topic.Core", name="main_result", revision=1)


def test_bind_scope_interface_reports_ambiguous_export_and_missing_candidate(tmp_path: Path) -> None:
    _init_main(tmp_path, interfaces=[])
    tree = NodeTreeComponent()
    assert tree.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    component, _export = _component_with_public_decls(tmp_path, {})
    assert component.add_interface(
        tmp_path,
        node_path="Main.Topic",
        name="topic_result",
        kind=DeclKind.THEOREM,
        summary="Topic result interface.",
        actor="coordinator",
    ).ok
    contract = _load_contract(tmp_path, "Main.Topic")
    contract.exports = [
        DeclRef(node="Main.Topic.Core", name="main_result", revision=1),
        DeclRef(node="Main.Topic.Aux", name="main_result", revision=1),
    ]
    _write_contract(tmp_path, "Main.Topic", contract)

    ambiguous = component.bind_interface_to_decl(
        tmp_path,
        node_path="Main.Topic",
        interface_name="topic_result",
        decl_name="main_result",
    )
    assert not ambiguous.ok
    assert ambiguous.issues[0].kind == "interface_binding_decl_ambiguous"

    missing_candidate = component.bind_interface_to_decl(
        tmp_path,
        node_path="Main.Topic",
        interface_name="topic_result",
        decl_name="main_result",
        decl_node="Main.Topic.Core",
    )
    assert not missing_candidate.ok
    assert missing_candidate.issues[0].kind == "interface_binding_export_candidate_missing"


def test_protected_root_gate_ignores_bound_decl_result(tmp_path: Path) -> None:
    protected = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Main theorem.")
    _init_main(tmp_path, interfaces=[protected])
    _create_content_node(tmp_path, "Main.Core")
    component, export = _component_with_public_decls(
        tmp_path,
        {
            "Main.Core": [
                DeclPublicView(
                    ref=DeclRef(node="Main.Core", name="main_result", revision=1),
                    kind=DeclKind.THEOREM.value,
                    summary="Main theorem.",
                )
            ]
        },
    )
    assert export.add_scope_export(tmp_path, scope_path="Main", decl_ref="Main.Core:main_result").ok
    bound = component.bind_interface_to_decl(
        tmp_path,
        node_path="Main",
        interface_name="main_result",
        decl_name="main_result",
        decl_node="Main.Core",
    )
    assert bound.ok

    gate = component.check_protected_root_interfaces(tmp_path)

    assert gate.ok
    assert gate.value is not None
    assert gate.value.passed is True


def test_submit_root_interface_prepare_ready_returns_counts_after_gate(tmp_path: Path) -> None:
    protected = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Main theorem.")
    _init_main(tmp_path, interfaces=[protected])
    component = InterfaceComponent()
    assert component.add_interface(
        tmp_path,
        node_path="Main",
        name="core_definition",
        kind=DeclKind.DEFINITION,
        summary="Expose core definition.",
        actor="coordinator",
    ).ok

    submitted = component.submit_root_interface_prepare_ready(tmp_path, summary="Root interfaces are ready.")

    assert submitted.ok
    assert submitted.value is not None
    assert submitted.value.protected_interface_count == 1
    assert submitted.value.total_interface_count == 2
    assert submitted.value.supplement_interface_count == 1
    assert submitted.value.gate.passed is True


def test_submit_root_interface_prepare_ready_reports_summary_gate_and_supplement_failures(tmp_path: Path) -> None:
    protected = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Main theorem.")
    _init_main(tmp_path, interfaces=[protected])
    component = InterfaceComponent()

    missing_summary = component.submit_root_interface_prepare_ready(tmp_path, summary=" ")
    assert not missing_summary.ok
    assert missing_summary.issues[0].kind == "root_interface_ready_summary_required"

    contract = _load_contract(tmp_path, "Main")
    contract.interfaces[0].summary = "Changed protected requirement."
    _write_contract(tmp_path, "Main", contract)
    gate_fail = component.submit_root_interface_prepare_ready(tmp_path, summary="Ready.")
    assert not gate_fail.ok
    assert gate_fail.issues[0].kind == "protected_interface_modified"

    contract.interfaces[0].summary = "Main theorem."
    contract.interfaces.append(DeclInterface(name="supplement", kind=DeclKind.THEOREM, summary="Temporary."))
    contract.interfaces[-1].summary = ""
    _write_contract(tmp_path, "Main", contract)
    supplement_fail = component.submit_root_interface_prepare_ready(tmp_path, summary="Ready.")
    assert not supplement_fail.ok
    assert supplement_fail.issues[0].kind == "supplement_interface_summary_missing"
