from tests.unit_services_helpers import make_runtime

from pathlib import Path

from lean_constellation.domain.interface import DeclKind
from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import RepoCompletionMode
from lean_constellation.services import LeanProviderOverrides
from lean_constellation.services.foundation import FoundationContext, FoundationService, ServiceResult, WriteMode
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.decl_graph.models import (
    Decl,
    DeclFormalSection,
    DeclRevision,
    DeclRevisionStatus,
    DeclStatement,
)
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
    assert tree.create_content_node(
        tmp_path,
        path="Main.Topic.Sub.Inner",
        goal="Inner goal",
        boundary="Inner boundary",
        objective="Build inner declarations.",
        success_criteria="Inner declarations are ready.",
    ).ok


def _seed_declared_public_decl(
    repo_root: Path,
    *,
    node_path: str,
    name: str,
    kind: str,
) -> None:
    runtime = make_runtime()
    decl = Decl(
        name=name,
        node_path=node_path,
        kind=kind,
        public=True,
        module=f"Fixture.{name}",
        summary=f"{name} declaration.",
    )
    revision = DeclRevision(
        revision=1,
        state=DeclState.DECLARED,
        status=DeclRevisionStatus.COMMITTED,
        lean_decl_name=name,
        statement=DeclStatement(
            formal=DeclFormalSection(
                code=(
                    (
                        "/--\n"
                        f"# lean-constellation target: `{name}`\n"
                        "-/\n"
                        f"theorem {name} : True := by trivial"
                    )
                    if kind in {"theorem", "lemma"}
                    else f"def {name} : Nat := 0"
                )
            )
        ),
    )
    ensured = runtime.foundation.store.ensure_dir(
        runtime.decl_graph.graph_store.decl_revisions_dir(
            repo_root,
            node_path=node_path,
            decl_name=name,
        )
    )
    assert ensured.ok
    written_decl = runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.decl_record_path(
            repo_root,
            node_path=node_path,
            decl_name=name,
        ),
        decl,
    )
    assert written_decl.ok
    written_revision = runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.revision_path(
            repo_root,
            node_path=node_path,
            decl_name=name,
            revision=1,
        ),
        revision,
    )
    assert written_revision.ok
    rebuilt = runtime.decl_graph.graph_store.rebuild_index(repo_root, node_path=node_path)
    assert rebuilt.ok
    contract = runtime.node.contract._commit_content_contract_with_head(
        repo_root,
        node_path=node_path,
        summary=f"{name} boundary ready.",
        decl_graph_head={name: 1},
    )
    assert contract.ok, contract.issues


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
    _seed_declared_public_decl(
        tmp_path,
        node_path="Main.Topic.Sub.Inner",
        name="sub_result",
        kind="theorem",
    )
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
    assert child_scope_candidate.kind == "theorem"
    assert child_scope_candidate.ready is True
    assert child_scope_candidate.stale is False


def test_scope_export_draft_cannot_propagate_an_open_child_scope_boundary(
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
    assert ref not in [candidate.ref for candidate in candidates.value.candidates]
    assert not added.ok
    assert added.issues[0].kind == "scope_export_child_scope_unavailable"


def test_child_scope_export_binding_uses_exact_kind_and_is_atomic(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    child_ref = _write_child_scope_export(tmp_path)
    component = _component_with_provider(tmp_path)
    assert make_runtime().node.interface.add_interface(
        tmp_path,
        node_path="Main.Topic",
        name="topic_definition",
        kind=DeclKind.DEFINITION,
        summary="Expose a definition.",
        actor="coordinator",
    ).ok

    rejected = component.add_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        decl_node=child_ref.node,
        decl_name=child_ref.name,
        revision=child_ref.revision,
        bind_interface_name="topic_definition",
    )

    assert not rejected.ok
    assert rejected.issues[0].kind == "interface_binding_kind_mismatch"
    current = make_runtime().node.contract.get_current_contract(
        tmp_path,
        node_path="Main.Topic",
    )
    assert current.ok and current.value is not None
    assert current.value.contract.exports == []
    assert current.value.contract.interfaces[0].bound_decl is None


def test_child_scope_candidate_fails_closed_without_exact_kind_evidence(
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
    loaded.value.exports = [
        DeclRef(
            node="Main.Topic.Sub.Inner",
            name="missing_kind_evidence",
            revision=1,
        )
    ]
    assert foundation.write_json_atomic(
        path,
        loaded.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    committed = make_runtime().node.contract._commit_scope_contract_after_guard(
        tmp_path,
        scope_path="Main.Topic.Sub",
        summary="Persist malformed historical boundary for fail-closed coverage.",
    )
    assert committed.ok

    candidates = _component_with_provider(tmp_path).list_scope_export_candidates(
        tmp_path,
        scope_path="Main.Topic",
    )

    assert not candidates.ok
    assert "scope_export_kind_evidence_unavailable" in {
        issue.kind for issue in candidates.issues
    }


def test_scope_commit_revalidates_bound_interface_kind_after_candidate_drift(
    tmp_path: Path,
) -> None:
    _create_tree(tmp_path)
    child_ref = _write_child_scope_export(tmp_path)
    component = _component_with_provider(tmp_path)
    assert make_runtime().node.interface.add_interface(
        tmp_path,
        node_path="Main.Topic",
        name="topic_result",
        kind=DeclKind.THEOREM,
        summary="Expose a theorem.",
        actor="coordinator",
    ).ok
    assert component.add_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        decl_node=child_ref.node,
        decl_name=child_ref.name,
        revision=child_ref.revision,
        bind_interface_name="topic_result",
    ).ok
    runtime = make_runtime()
    decl = runtime.decl_graph.get_decl(
        tmp_path,
        node_path=child_ref.node,
        name=child_ref.name,
    )
    assert decl.ok and decl.value is not None
    decl.value.kind = "definition"
    saved = runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.decl_record_path(
            tmp_path,
            node_path=child_ref.node,
            decl_name=child_ref.name,
        ),
        decl.value,
        mode=WriteMode.UPDATE_EXISTING,
    )
    assert saved.ok

    committed = runtime.node.commit_scope_contract(
        tmp_path,
        scope_path="Main.Topic",
        summary="Must reject stale binding semantics.",
    )

    assert not committed.ok
    assert "interface_binding_kind_mismatch" in {
        issue.kind for issue in committed.issues
    }


def test_scope_commit_revalidates_bound_interface_statement_after_candidate_drift(
    tmp_path: Path,
) -> None:
    _create_tree(tmp_path)
    child_ref = _write_child_scope_export(tmp_path)
    component = _component_with_provider(tmp_path)
    runtime = make_runtime()
    current = runtime.node.contract.get_current_contract(
        tmp_path,
        node_path="Main.Topic",
    )
    assert current.ok and current.value is not None
    current.value.contract.interfaces.append(
        runtime.node.interface._build_interface(
            name="topic_result",
            kind=DeclKind.THEOREM,
            summary="Expose the exact theorem.",
            statement_hint=None,
        ).value.model_copy(
            update={
                "expected_statement_lean_code": (
                    "theorem sub_result : True := by trivial"
                )
            }
        )
    )
    saved_contract = runtime.node.contract._persist_open_candidate(
        tmp_path,
        node_path="Main.Topic",
        candidate=current.value.contract,
    )
    assert saved_contract.ok
    assert component.add_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        decl_node=child_ref.node,
        decl_name=child_ref.name,
        revision=child_ref.revision,
        bind_interface_name="topic_result",
    ).ok
    revision = runtime.decl_graph.get_decl_revision(
        tmp_path,
        node_path=child_ref.node,
        name=child_ref.name,
        revision=child_ref.revision,
    )
    assert revision.ok and revision.value is not None
    revision.value.statement.formal = DeclFormalSection(
        code=(
            "/--\n"
            "# lean-constellation target: `sub_result`\n"
            "-/\n"
            "theorem sub_result : False := by contradiction"
        )
    )
    saved_revision = runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.revision_path(
            tmp_path,
            node_path=child_ref.node,
            decl_name=child_ref.name,
            revision=child_ref.revision,
        ),
        revision.value,
        mode=WriteMode.UPDATE_EXISTING,
    )
    assert saved_revision.ok

    committed = runtime.node.commit_scope_contract(
        tmp_path,
        scope_path="Main.Topic",
        summary="Must reject stale statement semantics.",
    )

    assert not committed.ok
    assert "interface_statement_contract_mismatch" in {
        issue.kind for issue in committed.issues
    }


def test_scope_commit_rechecks_child_scope_after_parent_export_draft(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    child_ref = _write_child_scope_export(tmp_path)
    component = _component_with_provider(tmp_path)
    assert component.add_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        decl_node=child_ref.node,
        decl_name=child_ref.name,
        revision=child_ref.revision,
    ).ok

    runtime = make_runtime()
    opened = runtime.node.contract.ensure_open_contract(tmp_path, node_path="Main.Topic.Sub")
    assert opened.ok and opened.value is not None
    child_path = runtime.node.node_tree.node_store.contract_path(
        tmp_path,
        node_id=opened.value.node_id,
        version=opened.value.version,
    )
    loaded = runtime.foundation.read_json(child_path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.exports = []
    assert runtime.foundation.write_json_atomic(
        child_path,
        loaded.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    committed_child = runtime.node.commit_scope_contract(
        tmp_path,
        scope_path="Main.Topic.Sub",
        summary="Child boundary changed.",
    )
    assert committed_child.ok

    committed_parent = runtime.node.commit_scope_contract(
        tmp_path,
        scope_path="Main.Topic",
        summary="Parent must recheck child boundary.",
    )
    assert not committed_parent.ok
    assert committed_parent.issues[0].kind == "scope_export_not_child_scope_export"


def test_list_scope_export_candidates_marks_already_exported(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    component = _component_with_provider(tmp_path)
    assert component.add_scope_export(tmp_path, scope_path="Main.Topic", decl_node="Main.Topic.Core", decl_name="main_result").ok

    candidates = component.list_scope_export_candidates(tmp_path, scope_path="Main.Topic")

    assert candidates.ok
    assert candidates.value is not None
    assert candidates.value.candidates[0].already_exported is True


def test_scope_export_rejects_content_with_partial_task_target(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    runtime = make_runtime()
    lowered = runtime.node.contract.set_task_completion_mode_receipt(
        tmp_path,
        node_path="Main.Topic.Core",
        task_completion_mode=RepoCompletionMode.GRAPH_DECLARED,
    )
    assert lowered.ok, lowered.issues
    component = _component_with_provider(tmp_path)

    candidates = component.list_scope_export_candidates(
        tmp_path,
        scope_path="Main.Topic",
    )
    added = component.add_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        decl_node="Main.Topic.Core",
        decl_name="main_result",
    )

    assert candidates.ok and candidates.value is not None
    assert all(
        item.source_child != "Main.Topic.Core"
        for item in candidates.value.candidates
    )
    assert not added.ok
    assert added.issues[0].kind == "node_provider_completion_target_unsatisfied"


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


def test_add_scope_export_with_interface_bind_reuses_kind_validation(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    component = _component_with_provider(tmp_path)
    interface = make_runtime().node.interface
    assert interface.add_interface(
        tmp_path,
        node_path="Main.Topic",
        name="topic_definition",
        kind=DeclKind.DEFINITION,
        summary="Expose a definition.",
        actor="coordinator",
    ).ok

    rejected = component.add_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        decl_node="Main.Topic.Core",
        decl_name="main_result",
        bind_interface_name="topic_definition",
    )

    assert not rejected.ok
    assert rejected.issues[0].kind == "interface_binding_kind_mismatch"
    current = make_runtime().node.contract.get_current_contract(tmp_path, node_path="Main.Topic")
    assert current.ok and current.value is not None
    assert current.value.contract.exports == []
    assert current.value.contract.interfaces[0].bound_decl is None


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

    target = DeclRef(repo=None, node="Main.Topic.Core", name="main_result", revision=1)
    blocked = component.remove_scope_export(tmp_path, scope_path="Main.Topic", ref=target)
    assert not blocked.ok
    assert blocked.issues[0].kind == "scope_export_bound_interface"

    foundation = make_runtime().foundation
    path = foundation.node_contract_path(FoundationContext(repo_root=tmp_path), "Main.Topic", 1)
    loaded = foundation.read_json(path, NodeContractSnapshot)
    assert loaded.ok and loaded.value is not None
    loaded.value.interfaces[0].bound_decl = None
    assert foundation.write_json_atomic(path, loaded.value, mode=WriteMode.UPDATE_EXISTING).ok

    removed = component.remove_scope_export(tmp_path, scope_path="Main.Topic", ref=target)
    assert removed.ok
    assert removed.value is not None
    assert removed.value.changed is True
    assert removed.value.exports == []
    assert "import Main.Topic.Core.Theorems.main_result" not in (tmp_path / "Main" / "Topic" / "Interfaces.lean").read_text(encoding="utf-8")


def test_remove_scope_export_parse_missing_and_projection_failure(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    component = _component_with_provider(tmp_path)

    missing = component.remove_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        ref=DeclRef(repo=None, node="Main.Topic.Core", name="missing", revision=1),
    )
    assert not missing.ok
    assert missing.issues[0].kind == "scope_export_missing"

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
    projection_failed = failing.remove_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        ref=DeclRef(repo=None, node="Main.Topic.Core", name="main_result", revision=1),
    )
    assert not projection_failed.ok
    assert projection_failed.issues[0].kind == "projection_refresh_failed"
    restored = failing.list_scope_exports(tmp_path, scope_path="Main.Topic")
    assert restored.ok and restored.value is not None
    assert [item.name for item in restored.value] == ["main_result"]


def test_remove_scope_export_requires_exact_decl_ref_revision(tmp_path: Path) -> None:
    _create_tree(tmp_path)
    component = _component_with_provider(tmp_path)
    target = DeclRef(repo=None, node="Main.Topic.Core", name="main_result", revision=1)
    assert component.add_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        decl_node=target.node,
        decl_name=target.name,
        revision=target.revision,
    ).ok

    wrong_revision = component.remove_scope_export(
        tmp_path,
        scope_path="Main.Topic",
        ref=target.model_copy(update={"revision": 2}),
    )
    assert not wrong_revision.ok
    assert wrong_revision.issues[0].kind == "scope_export_missing"

    removed = component.remove_scope_export(tmp_path, scope_path="Main.Topic", ref=target)
    assert removed.ok
    assert removed.value is not None
    assert removed.value.exports == []


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
