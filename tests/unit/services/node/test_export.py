from tests.unit_services_helpers import make_runtime

from pathlib import Path

from lean_constellation.domain.interface import DeclKind
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services import LeanProviderOverrides
from lean_constellation.services.foundation import FoundationContext, FoundationService, ServiceResult, WriteMode
from lean_constellation.services.node import (
    DeclPublicView,
    ExportComponent,
    NodeContractSnapshot,
)


class FakePublicDeclProvider:
    def __init__(self, foundation: FoundationService, decls: dict[str, list[DeclPublicView]]) -> None:
        self.foundation = foundation
        self.decls = decls

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        return self.foundation.ok(self.decls.get(node_path, []))


class FailingPublicDeclProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        return self.foundation.fail(self.foundation.issue("provider_failed", "Provider failed.", object_ref=node_path))


class FailingProjection:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def refresh_interfaces(self, repo_root: Path, *, node_path: str) -> ServiceResult[object]:
        return self.foundation.fail(self.foundation.issue("projection_refresh_failed", "Projection refresh failed.", object_ref=node_path))


def _create_tree(tmp_path: Path) -> None:
    tree = make_runtime().node.node_tree
    assert tree.ensure_root_scope_node(tmp_path).ok
    assert tree.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    assert tree.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Core ready.",
    ).ok
    assert tree.create_scope_node(tmp_path, path="Main.Topic.Sub", goal="Sub goal", boundary="Sub boundary").ok


def _component_with_provider(tmp_path: Path) -> ExportComponent:
    base_runtime = make_runtime()
    foundation = base_runtime.foundation
    public = {
        "Main.Topic.Core": [
            DeclPublicView(
                ref=DeclRef(repo=None, node="Main.Topic.Core", name="main_result", revision=1),
                kind="theorem",
                module="Main.Topic.Core.Theorems.main_result",
                summary="Main result.",
                ready=True,
                stale=False,
            )
        ]
    }
    runtime = make_runtime(providers=LeanProviderOverrides(content_public_decl_provider=FakePublicDeclProvider(foundation, public)))
    return runtime.node.export


def _component_with_public_decls(decls: dict[str, list[DeclPublicView]]) -> ExportComponent:
    base_runtime = make_runtime()
    foundation = base_runtime.foundation
    runtime = make_runtime(providers=LeanProviderOverrides(content_public_decl_provider=FakePublicDeclProvider(foundation, decls)))
    return runtime.node.export


def _write_child_scope_export(tmp_path: Path) -> DeclRef:
    foundation = make_runtime().foundation
    path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic.Sub", 1)
    loaded = foundation.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    ref = DeclRef(repo=None, node="Main.Topic.Sub.Inner", name="sub_result", revision=1)
    loaded.value.exports = [ref]
    assert foundation.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok
    committed = make_runtime().node.contract._commit_scope_contract_after_guard(
        tmp_path,
        scope_path="Main.Topic.Sub",
        summary="Sub exports ready.",
    )
    assert committed.ok
    return ref


def test_list_content_public_decls_uses_provider_and_rejects_scope(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    component = _component_with_provider(tmp_path)

    public = component.list_content_public_decls(tmp_path, node_path="Main.Topic.Core")
    assert public.ok
    assert public.value is not None
    assert [decl.ref.name for decl in public.value] == ["main_result"]

    scope = component.list_content_public_decls(tmp_path, node_path="Main.Topic")
    assert not scope.ok
    assert scope.issues[0].kind == "node_not_content"


def test_list_content_public_decls_default_warning_adapter_source_and_provider_failure(tmp_path: Path) -> None:
    _create_tree(tmp_path)

    default_component = ExportComponent(make_runtime())
    default_result = default_component.list_content_public_decls(tmp_path, node_path="Main.Topic.Core")
    assert default_result.ok
    assert default_result.value == []
    assert default_result.issues[0].kind == "content_public_decl_provider_missing"

    adapter_like_component = _component_with_public_decls(
        {
            "Main.Topic.Core": [
                DeclPublicView(
                    ref=DeclRef(repo=None, node="Main.Topic.Core", name="adapter_result", revision=1),
                    kind="theorem",
                    source="adapter_catalog",
                )
            ]
        }
    )
    adapter_like = adapter_like_component.list_content_public_decls(tmp_path, node_path="Main.Topic.Core")
    assert adapter_like.ok
    assert adapter_like.value is not None
    assert adapter_like.value[0].source == "adapter_catalog"

    runtime = make_runtime()
    failing_component = ExportComponent(
        runtime,
        public_decl_provider=FailingPublicDeclProvider(runtime.foundation),
    )
    failed = failing_component.list_content_public_decls(tmp_path, node_path="Main.Topic.Core")
    assert not failed.ok
    assert failed.issues[0].kind == "provider_failed"


def test_list_scope_export_candidates_from_content_and_child_scope(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    child_ref = _write_child_scope_export(tmp_path)
    component = _component_with_provider(tmp_path)

    candidates = component.list_scope_export_candidates(tmp_path, scope_path="Main.Topic")

    assert candidates.ok
    assert candidates.value is not None
    assert [(item.ref.node, item.ref.name) for item in candidates.value.candidates] == [
        ("Main.Topic.Core", "main_result"),
        (child_ref.node, child_ref.name),
    ]
    child_scope_candidate = candidates.value.candidates[1]
    assert child_scope_candidate.source_kind == "scope"
    assert child_scope_candidate.ready is True
    assert child_scope_candidate.stale is False


def test_scope_export_draft_can_propagate_an_open_child_scope_boundary(
    tmp_path: Path,
) -> None:
    _create_tree(tmp_path)
    foundation = make_runtime().foundation
    path = foundation.node_contract_path(
        FoundationContext(repo_root=tmp_path),
        "Main.Topic.Sub",
        1,
    )
    loaded = foundation.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    ref = DeclRef(
        repo=None,
        node="Main.Topic.Sub.Inner",
        name="sub_result",
        revision=1,
    )
    loaded.value.exports = [ref]
    assert foundation.write_json_atomic(
        path,
        loaded.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    component = _component_with_provider(tmp_path)

    candidates = component.list_scope_export_candidates(
        tmp_path,
        scope_path="Main.Topic",
    )
    added = component.add_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        decl_node=ref.node,
        decl_name=ref.name,
    )

    assert candidates.ok and candidates.value is not None
    assert ref in [candidate.ref for candidate in candidates.value.candidates]
    assert added.ok and added.value is not None
    assert added.value.changed is True


def test_list_scope_export_candidates_marks_already_exported(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    component = _component_with_provider(tmp_path)
    assert component.add_scope_export(tmp_path, scope_path="Main.Topic", decl_node="Main.Topic.Core", decl_name="main_result").ok

    candidates = component.list_scope_export_candidates(tmp_path, scope_path="Main.Topic")

    assert candidates.ok
    assert candidates.value is not None
    assert candidates.value.candidates[0].already_exported is True


def test_add_scope_export_with_optional_interface_bind_and_duplicate_warning(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    component = _component_with_provider(tmp_path)
    assert make_runtime().node.interface.add_interface(
        tmp_path,
        node_path="Main.Topic",
        name="topic_result",
        kind=DeclKind.THEOREM,
        summary="Expose topic result.",
        actor="coordinator",
    ).ok

    added = component.add_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        decl_node="Main.Topic.Core",
        decl_name="main_result",
        bind_interface_name="topic_result",
    )

    assert added.ok
    assert added.value is not None
    assert added.value.changed is True
    assert added.value.exports[0].ref.name == "main_result"
    assert added.issues == []
    interfaces_path = tmp_path / "Main" / "Topic" / "Interfaces.lean"
    assert "import Main.Topic.Core.Theorems.main_result" in interfaces_path.read_text(encoding="utf-8")

    contract = make_runtime().node.contract.get_current_contract(tmp_path, node_path="Main.Topic")
    assert contract.ok and contract.value is not None
    assert contract.value.contract.interfaces[0].bound_decl == DeclRef(repo=None, node="Main.Topic.Core", name="main_result", revision=1)

    duplicate = component.add_scope_export(tmp_path, scope_path="Main.Topic", decl_node="Main.Topic.Core", decl_name="main_result")
    assert duplicate.ok
    assert duplicate.value is not None
    assert duplicate.value.changed is False
    assert [issue.kind for issue in duplicate.issues] == ["scope_export_duplicate"]


def test_add_scope_export_rejects_non_descendant_and_non_public_decl(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    component = _component_with_provider(tmp_path)

    non_descendant = component.add_scope_export(tmp_path, scope_path="Main.Topic", decl_node="Main.Other", decl_name="main_result")
    assert not non_descendant.ok
    assert non_descendant.issues[0].kind == "scope_export_not_descendant"

    non_public = component.add_scope_export(tmp_path, scope_path="Main.Topic", decl_node="Main.Topic.Core", decl_name="missing")
    assert not non_public.ok
    assert non_public.issues[0].kind == "scope_export_not_public"


def test_add_scope_export_rejects_parse_child_and_readiness_failures(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    component = _component_with_provider(tmp_path)

    missing_name = component.add_scope_export(tmp_path, scope_path="Main.Topic", decl_node="Main.Topic.Core", decl_name=" ")
    assert not missing_name.ok
    assert missing_name.issues[0].kind == "decl_ref_name_required"

    bad_revision = component.add_scope_export(tmp_path, scope_path="Main.Topic", decl_node="Main.Topic.Core", decl_name="main_result", revision=0)
    assert not bad_revision.ok
    assert bad_revision.issues[0].kind == "decl_ref_revision_invalid"

    scope_self = component.add_scope_export(tmp_path, scope_path="Main.Topic", decl_node="Main.Topic", decl_name="self_export")
    assert not scope_self.ok
    assert scope_self.issues[0].kind == "scope_export_not_child_visible"

    missing_child = component.add_scope_export(tmp_path, scope_path="Main.Topic", decl_node="Main.Topic.Missing", decl_name="ghost")
    assert not missing_child.ok
    assert missing_child.issues[0].kind == "scope_export_child_missing"

    unready_component = _component_with_public_decls(
        {
            "Main.Topic.Core": [
                DeclPublicView(
                    ref=DeclRef(repo=None, node="Main.Topic.Core", name="draft_result", revision=1),
                    kind="theorem",
                    ready=False,
                    stale=True,
                )
            ]
        }
    )
    unready = unready_component.add_scope_export(tmp_path, scope_path="Main.Topic", decl_node="Main.Topic.Core", decl_name="draft_result")
    assert not unready.ok
    assert unready.issues[0].kind == "scope_export_decl_not_ready"


def test_add_scope_export_interface_bind_failure_branches(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    component = _component_with_provider(tmp_path)
    interface = make_runtime().node.interface
    assert interface.add_interface(
        tmp_path,
        node_path="Main.Topic",
        name="topic_result",
        kind=DeclKind.THEOREM,
        summary="Expose topic result.",
        actor="coordinator",
    ).ok

    blank = component.add_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        decl_node="Main.Topic.Core",
        decl_name="main_result",
        bind_interface_name=" ",
    )
    assert not blank.ok
    assert blank.issues[0].kind == "interface_name_required"

    missing = component.add_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        decl_node="Main.Topic.Core",
        decl_name="main_result",
        bind_interface_name="missing_interface",
    )
    assert not missing.ok
    assert missing.issues[0].kind == "interface_missing"

    foundation = make_runtime().foundation
    path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic", 1)
    loaded = foundation.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.interfaces[0].bound_decl = DeclRef(repo=None, node="Main.Topic.Core", name="different_result", revision=1)
    assert foundation.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok

    already_bound = component.add_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        decl_node="Main.Topic.Core", decl_name="main_result",
        bind_interface_name="topic_result",
    )
    assert not already_bound.ok
    assert already_bound.issues[0].kind == "interface_already_bound"


def test_remove_scope_export_blocks_bound_interface_then_removes_unbound(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    component = _component_with_provider(tmp_path)
    assert make_runtime().node.interface.add_interface(
        tmp_path,
        node_path="Main.Topic",
        name="topic_result",
        kind=DeclKind.THEOREM,
        summary="Expose topic result.",
        actor="coordinator",
    ).ok
    assert component.add_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        decl_node="Main.Topic.Core", decl_name="main_result",
        bind_interface_name="topic_result",
    ).ok

    blocked = component.remove_scope_export(tmp_path, scope_path="Main.Topic", index=0)
    assert not blocked.ok
    assert blocked.issues[0].kind == "scope_export_bound_interface"

    foundation = make_runtime().foundation
    path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic", 1)
    loaded = foundation.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.interfaces[0].bound_decl = None
    assert foundation.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok

    removed = component.remove_scope_export(tmp_path, scope_path="Main.Topic", index=0)
    assert removed.ok
    assert removed.value is not None
    assert removed.value.changed is True
    assert removed.value.exports == []
    assert "import Main.Topic.Core.Theorems.main_result" not in (tmp_path / "Main" / "Topic" / "Interfaces.lean").read_text(encoding="utf-8")


def test_remove_scope_export_parse_missing_and_projection_failure(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    component = _component_with_provider(tmp_path)

    missing = component.remove_scope_export(tmp_path, scope_path="Main.Topic", index=0)
    assert not missing.ok
    assert missing.issues[0].kind == "scope_export_index_out_of_range"

    assert component.add_scope_export(tmp_path, scope_path="Main.Topic", decl_node="Main.Topic.Core", decl_name="main_result").ok
    runtime = make_runtime()
    foundation = runtime.foundation
    failing = ExportComponent(
        runtime,
        public_decl_provider=FakePublicDeclProvider(
            foundation,
            {
                "Main.Topic.Core": [
                    DeclPublicView(
                        ref=DeclRef(repo=None, node="Main.Topic.Core", name="main_result", revision=1),
                        ready=True,
                        stale=False,
                    )
                ]
            },
        ),
        node_projection=FailingProjection(foundation),  # type: ignore[arg-type]
    )
    projection_failed = failing.remove_scope_export(tmp_path, scope_path="Main.Topic", index=0)
    assert not projection_failed.ok
    assert projection_failed.issues[0].kind == "projection_refresh_failed"
    restored = failing.list_scope_exports(tmp_path, scope_path="Main.Topic")
    assert restored.ok and restored.value is not None
    assert [item.name for item in restored.value] == ["main_result"]


def test_add_scope_export_reports_projection_failure(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    runtime = make_runtime()
    foundation = runtime.foundation
    component = ExportComponent(
        runtime,
        public_decl_provider=FakePublicDeclProvider(
            foundation,
            {
                "Main.Topic.Core": [
                    DeclPublicView(
                        ref=DeclRef(repo=None, node="Main.Topic.Core", name="main_result", revision=1),
                        ready=True,
                        stale=False,
                    )
                ]
            },
        ),
        node_projection=FailingProjection(foundation),  # type: ignore[arg-type]
    )

    result = component.add_scope_export(tmp_path, scope_path="Main.Topic", decl_node="Main.Topic.Core", decl_name="main_result")

    assert not result.ok
    assert result.issues[0].kind == "projection_refresh_failed"
    restored = component.list_scope_exports(tmp_path, scope_path="Main.Topic")
    assert restored.ok and restored.value == []


def test_validate_scope_exports_reports_binding_missing_and_pass(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    component = _component_with_provider(tmp_path)
    assert component.add_scope_export(tmp_path, scope_path="Main.Topic", decl_node="Main.Topic.Core", decl_name="main_result").ok
    assert make_runtime().node.interface.add_interface(
        tmp_path,
        node_path="Main.Topic",
        name="bad_binding",
        kind=DeclKind.THEOREM,
        summary="Bad binding.",
        actor="coordinator",
    ).ok

    foundation = make_runtime().foundation
    path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic", 1)
    loaded = foundation.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.interfaces[0].bound_decl = DeclRef(repo=None, node="Main.Topic.Core", name="missing", revision=1)
    assert foundation.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok

    invalid = component.validate_scope_exports(tmp_path, scope_path="Main.Topic")
    assert invalid.ok
    assert invalid.value is not None
    assert invalid.value.passed is False
    assert invalid.value.issues[0].kind == "interface_binding_not_exported"

    loaded = foundation.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.interfaces[0].bound_decl = DeclRef(repo=None, node="Main.Topic.Core", name="main_result", revision=1)
    assert foundation.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok

    valid = component.validate_scope_exports(tmp_path, scope_path="Main.Topic")
    assert valid.ok
    assert valid.value is not None
    assert valid.value.passed is True
    assert valid.value.issues == []


def test_list_and_validate_scope_exports_report_invalid_export_view(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    component = _component_with_provider(tmp_path)
    foundation = make_runtime().foundation
    path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic", 1)
    loaded = foundation.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.exports = [DeclRef(repo=None, node="Main.Topic.Missing", name="ghost", revision=1)]
    assert foundation.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok

    listed = component.list_scope_exports(tmp_path, scope_path="Main.Topic")
    assert listed.ok
    assert listed.value is not None
    assert listed.value[0].valid is False
    assert listed.value[0].issues[0].kind == "scope_export_child_missing"

    gate = component.validate_scope_exports(tmp_path, scope_path="Main.Topic")
    assert gate.ok
    assert gate.value is not None
    assert gate.value.passed is False
    assert gate.value.issues[0].kind == "scope_export_child_missing"


def test_validate_scope_exports_reports_duplicate_and_unready_provider_result(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    foundation = make_runtime().foundation
    component = _component_with_public_decls(
        {
            "Main.Topic.Core": [
                DeclPublicView(
                    ref=DeclRef(repo=None, node="Main.Topic.Core", name="draft_result", revision=1),
                    ready=False,
                    stale=False,
                )
            ]
        }
    )
    path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic", 1)
    loaded = foundation.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    draft_ref = DeclRef(repo=None, node="Main.Topic.Core", name="draft_result", revision=1)
    loaded.value.exports = [draft_ref, draft_ref]
    assert foundation.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok

    gate = component.validate_scope_exports(tmp_path, scope_path="Main.Topic")

    assert gate.ok
    assert gate.value is not None
    assert gate.value.passed is False
    assert [issue.kind for issue in gate.value.issues] == [
        "scope_export_duplicate",
        "scope_export_decl_not_ready",
        "scope_export_decl_not_ready",
    ]
