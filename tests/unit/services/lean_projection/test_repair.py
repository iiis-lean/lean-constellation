from tests.unit_services_helpers import initialize_native_test_repo, make_runtime

from pathlib import Path
from typing import Any

from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.external_clients import ExternalCommandResult, LeanDiagnosticsResult
from lean_constellation.services.decl_graph import DeclFileRevisionView
from lean_constellation.services.foundation import FoundationService, ServiceResult
from lean_constellation.services.lean_projection import (
    AdapterFacadeComponent,
    AdapterModuleListView,
    DeclFileComponent,
    LeanCheckComponent,
    LeanCheckView,
    NodeProjectionComponent,
    RepairComponent,
)
from lean_constellation.services.node import DeclPublicView, ExportComponent, NodeTreeComponent


class FakePublicDeclProvider:
    def __init__(self, foundation: FoundationService, decls: dict[str, list[DeclPublicView]]) -> None:
        self.foundation = foundation
        self.decls = decls

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        del repo_root
        return self.foundation.ok(self.decls.get(node_path, []))


class FakeAdapterProvider:
    def __init__(self, foundation: FoundationService, active: list[str], visible: list[str]) -> None:
        self.foundation = foundation
        self.active = active
        self.visible = visible

    def list_active_adapter_modules(self, repo_root: Path) -> ServiceResult[AdapterModuleListView]:
        del repo_root
        return self.foundation.ok(AdapterModuleListView(modules=self.active, summary="active"))

    def list_visible_upstream_modules(self, repo_root: Path) -> ServiceResult[AdapterModuleListView]:
        del repo_root
        return self.foundation.ok(AdapterModuleListView(modules=self.visible, summary="visible"))


class FailingAdapterProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def list_active_adapter_modules(self, repo_root: Path) -> ServiceResult[AdapterModuleListView]:
        del repo_root
        return self.foundation.fail(
            self.foundation.issue(
                "adapter_provider_failed",
                "Adapter provider failed while listing active modules.",
            )
        )

    def list_visible_upstream_modules(self, repo_root: Path) -> ServiceResult[AdapterModuleListView]:
        del repo_root
        return self.foundation.fail(
            self.foundation.issue(
                "adapter_provider_failed",
                "Adapter provider failed while listing visible modules.",
            )
        )


class FakeToolkit:
    def run_file_diagnostics(self, repo_root: Path, file_path: Path) -> LeanDiagnosticsResult:
        return LeanDiagnosticsResult(ok=True, repo_root=str(repo_root), file_path=str(file_path), diagnostics=[], summary="ok")


class FakeLake:
    def run_lake_env_lean(self, *, repo_root: Path, rel_file: str, json: bool = True, timeout_seconds: int | None = None) -> ExternalCommandResult:
        del json, timeout_seconds
        return ExternalCommandResult(ok=True, command=["lake", "env", "lean", rel_file], cwd=str(repo_root), exit_code=0, summary="ok")


class FakeExternal:
    def __init__(self) -> None:
        self.lean_mcp_toolkit = FakeToolkit()
        self.lean_toolkit = self.lean_mcp_toolkit
        self.lake = FakeLake()


class FakeRevisionProvider:
    def __init__(self, foundation: FoundationService, revisions: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.foundation = foundation
        self.revisions = revisions

    def get_current_decl_revision(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[DeclFileRevisionView]:
        del repo_root
        revision = self.revisions.get((node_path, decl_name))
        if revision is None:
            return self.foundation.fail(self.foundation.issue("decl_revision_missing", "Decl revision missing.", object_ref=f"{node_path}:{decl_name}"))
        return self.foundation.ok(DeclFileRevisionView.model_validate(revision))

    def save_statement_formal_capture(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
    ) -> ServiceResult[DeclFileRevisionView]:
        del repo_root
        revision = self.revisions[(node_path, decl_name)]
        revision.setdefault("statement", {})["formal"] = {"code": code, "check": _current_check(check)}
        revision["state"] = "declared"
        return self.foundation.ok(DeclFileRevisionView.model_validate(revision))

    def save_proof_formal_capture(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
    ) -> ServiceResult[DeclFileRevisionView]:
        del repo_root
        revision = self.revisions[(node_path, decl_name)]
        revision.setdefault("proof", {})["formal"] = {"code": code, "check": _current_check(check)}
        revision["state"] = "proved"
        return self.foundation.ok(DeclFileRevisionView.model_validate(revision))


class FakeRepairDeclProvider:
    def __init__(self, foundation: FoundationService, decls: dict[str, list[str]]) -> None:
        self.foundation = foundation
        self.decls = decls

    def list_active_decl_names(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[str]]:
        del repo_root
        return self.foundation.ok(self.decls.get(node_path, []))


class FailingRepairDeclProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def list_active_decl_names(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[str]]:
        del repo_root
        return self.foundation.fail(
            self.foundation.issue(
                "active_decl_provider_failed",
                "Active DeclGraph provider failed.",
                object_ref=node_path,
            )
        )


def _create_nodes(tmp_path: Path) -> None:
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


def _revision() -> dict[str, Any]:
    statement_code = "import Main.Topic.Core.Prelude\n\n/-!\nlean-constellation target: main_result\nstage: statement\n-/\ntheorem main_result : True := by\n  sorry\n"
    proof_code = statement_code.replace("stage: statement", "stage: proof").replace("sorry", "trivial")
    return {
        "decl_name": "main_result",
        "revision": 1,
        "kind": "theorem",
        "state": "proved",
        "version_status": "open",
        "statement": {"nl": {"text": "The statement is true."}, "formal": {"code": statement_code}},
        "proof": {"nl": {"text": "Use triviality."}, "formal": {"code": proof_code}},
    }


def _current_check(check: LeanCheckView) -> dict[str, object]:
    return check.model_dump(mode="json")


def _repair_component(
    tmp_path: Path,
    *,
    revisions: dict[tuple[str, str], dict[str, Any]] | None = None,
    public_decls: dict[str, list[DeclPublicView]] | None = None,
    active_adapter_modules: list[str] | None = None,
    visible_adapter_modules: list[str] | None = None,
) -> RepairComponent:
    del tmp_path
    runtime = make_runtime(external_overrides={"lean_mcp_toolkit": FakeToolkit(), "lake": FakeLake()})
    foundation = runtime.foundation
    export = ExportComponent(runtime, public_decl_provider=FakePublicDeclProvider(foundation, public_decls or {}))
    node_projection = NodeProjectionComponent(runtime, export=export)
    lean_check = runtime.lean_projection.lean_check
    revision_provider = FakeRevisionProvider(foundation, revisions or {})
    decl_file = DeclFileComponent(runtime, lean_check=lean_check, revision_provider=revision_provider)
    adapter_facade = (
        AdapterFacadeComponent(
            runtime,
            provider=FakeAdapterProvider(foundation, active_adapter_modules or [], visible_adapter_modules or []),
        )
        if active_adapter_modules is not None or visible_adapter_modules is not None
        else None
    )
    decl_provider = FakeRepairDeclProvider(
        foundation,
        {
            node_path: sorted({decl_name for provider_node, decl_name in (revisions or {}) if provider_node == node_path})
            for node_path in {provider_node for provider_node, _ in (revisions or {})}
        },
    )
    return RepairComponent(
        runtime,
        node_projection=node_projection,
        adapter_facade=adapter_facade,
        decl_file=decl_file,
        node_tree=runtime.node.node_tree,
        decl_provider=decl_provider,
    )


def test_repair_node_projection_refreshes_missing_and_stale_files(tmp_path: Path) -> None:
    _create_nodes(tmp_path)
    component = _repair_component(tmp_path)

    first = component.repair_node_projection(tmp_path, node_path="Main.Topic.Core")

    assert first.ok
    assert first.value is not None
    assert first.value.changed is True
    assert len(first.value.changed_files) == 2
    assert (tmp_path / "Main" / "Topic" / "Core" / "Prelude.lean").exists()
    assert (tmp_path / "Main" / "Topic" / "Core" / "Interfaces.lean").exists()

    (tmp_path / "Main" / "Topic" / "Core" / "Prelude.lean").write_text("import Bad.Module\n", encoding="utf-8")
    second = component.repair_node_projection(tmp_path, node_path="Main.Topic.Core")

    assert second.ok
    assert second.value is not None
    assert second.value.changed is True
    assert any(action.target == "Main.Topic.Core:prelude" and action.status == "repaired" for action in second.value.actions)


def test_repair_node_projection_clean_reports_no_change(tmp_path: Path) -> None:
    _create_nodes(tmp_path)
    component = _repair_component(tmp_path)
    assert component.repair_node_projection(tmp_path, node_path="Main.Topic.Core").ok

    clean = component.repair_node_projection(tmp_path, node_path="Main.Topic.Core")

    assert clean.ok
    assert clean.value is not None
    assert clean.value.changed is False
    assert all(action.status == "passed" for action in clean.value.actions)


def test_repair_decl_files_from_active_graph_restores_snapshots(tmp_path: Path) -> None:
    _create_nodes(tmp_path)
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _repair_component(tmp_path, revisions=revisions)
    decl_file = component.decl_file.derive_decl_file_path(tmp_path, node_path="Main.Topic.Core", decl_name="main_result", kind="theorem")
    assert decl_file.ok and decl_file.value is not None
    path = Path(decl_file.value.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("broken\n", encoding="utf-8")

    repaired = component.repair_decl_files_from_active_graph(tmp_path, node_path="Main.Topic.Core")

    assert repaired.ok
    assert repaired.value is not None
    assert repaired.value.changed is True
    assert "trivial" in path.read_text(encoding="utf-8")
    assert repaired.value.changed_files == [str(path)]


def test_repair_decl_files_handles_empty_duplicate_and_provider_failure(tmp_path: Path) -> None:
    _create_nodes(tmp_path)

    empty = _repair_component(tmp_path)
    skipped = empty.repair_decl_files_from_active_graph(tmp_path, node_path="Main.Topic.Core")
    assert skipped.ok
    assert skipped.value is not None
    assert skipped.value.changed is False
    assert skipped.value.actions[0].status == "skipped"

    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    duplicate = _repair_component(tmp_path, revisions=revisions)
    duplicate.decl_provider = FakeRepairDeclProvider(duplicate.runtime.foundation, {"Main.Topic.Core": ["main_result", "main_result"]})
    restored = duplicate.repair_decl_files_from_active_graph(tmp_path, node_path="Main.Topic.Core")
    assert restored.ok
    assert restored.value is not None
    assert [action.target for action in restored.value.actions] == ["Main.Topic.Core:main_result"]

    failing = _repair_component(tmp_path, revisions=revisions)
    failing.decl_provider = FailingRepairDeclProvider(failing.runtime.foundation)
    failed = failing.repair_decl_files_from_active_graph(tmp_path, node_path="Main.Topic.Core")
    assert not failed.ok
    assert failed.issues[0].kind == "active_decl_provider_failed"


def test_restore_working_projection_to_active_graph_orders_generated_then_decl_files(tmp_path: Path) -> None:
    _create_nodes(tmp_path)
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _repair_component(tmp_path, revisions=revisions)

    restored = component.restore_working_projection_to_active_graph(tmp_path, node_path="Main.Topic.Core")

    assert restored.ok
    assert restored.value is not None
    assert restored.value.actions[0].action in {"refresh_prelude", "check_prelude"}
    assert any(action.action == "sync_decl_file" for action in restored.value.actions)
    assert restored.value.changed is True


def test_full_projection_audit_reports_failed_then_passed_after_repair(tmp_path: Path) -> None:
    _create_nodes(tmp_path)
    component = _repair_component(tmp_path)

    missing = component.full_projection_audit(tmp_path)

    assert missing.ok
    assert missing.value is not None
    assert missing.value.passed is False

    assert component.repair_node_projection(tmp_path, node_path="Main").ok
    assert component.repair_node_projection(tmp_path, node_path="Main.Topic").ok
    assert component.repair_node_projection(tmp_path, node_path="Main.Topic.Core").ok
    synced = component.full_projection_audit(tmp_path)

    assert synced.ok
    assert synced.value is not None
    assert synced.value.passed is True
    assert any(issue.kind == "adapter_facade_audit_skipped" for issue in synced.value.issues)


def test_full_projection_audit_skips_adapter_facade_for_native_repo_format(tmp_path: Path) -> None:
    initialize_native_test_repo(tmp_path)
    _create_nodes(tmp_path)
    component = _repair_component(tmp_path)
    formatted = component.runtime.repo_workspace.metadata.set_repo_format(
        tmp_path,
        repo_format="native",
        reason="Unit test native projection audit.",
    )
    assert formatted.ok
    assert component.repair_node_projection(tmp_path, node_path="Main").ok
    assert component.repair_node_projection(tmp_path, node_path="Main.Topic").ok
    assert component.repair_node_projection(tmp_path, node_path="Main.Topic.Core").ok

    audit = component.full_projection_audit(tmp_path)

    assert audit.ok
    assert audit.value is not None
    assert audit.value.passed is True
    assert any(issue.kind == "adapter_facade_audit_skipped" for issue in audit.value.issues)
    assert "checks passed" in audit.value.summary


def test_full_projection_audit_includes_adapter_facade_when_provider_configured(tmp_path: Path) -> None:
    component = _repair_component(tmp_path, active_adapter_modules=["Upstream.A"], visible_adapter_modules=["Upstream.A"])
    adapter_refresh = component.adapter_facade.refresh_adapter_interfaces(tmp_path)
    assert adapter_refresh.ok

    audit = component.full_projection_audit(tmp_path)

    assert audit.ok
    assert audit.value is not None
    assert audit.value.passed is True


def test_full_projection_audit_propagates_configured_adapter_provider_failure(tmp_path: Path) -> None:
    runtime = make_runtime()
    foundation = runtime.foundation
    component = RepairComponent(
        runtime,
        adapter_facade=AdapterFacadeComponent(runtime, provider=FailingAdapterProvider(foundation)),
    )

    audit = component.full_projection_audit(tmp_path)

    assert not audit.ok
    assert audit.issues[0].kind == "adapter_provider_failed"


def test_missing_decl_provider_is_explicit_failure(tmp_path: Path) -> None:
    _create_nodes(tmp_path)
    component = RepairComponent(make_runtime())

    result = component.repair_decl_files_from_active_graph(tmp_path, node_path="Main.Topic.Core")

    assert not result.ok
    assert result.issues[0].kind == "repair_decl_provider_missing"
