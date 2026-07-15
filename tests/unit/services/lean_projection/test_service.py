from tests.unit_services_helpers import make_runtime

from pathlib import Path
from typing import Any

from lean_constellation.services.external_clients import ExternalCommandResult, LeanCheckSummaryView, LeanDiagnosticsResult
from lean_constellation.services.decl_graph import DeclFileRevisionView
from lean_constellation.services.foundation import FoundationService, ServiceResult
from lean_constellation.services.lean_projection import AdapterModuleListView, LeanCheckView, LeanProjectionService
from lean_constellation.services.lean_projection.repair import ProjectionRepairView
from lean_constellation.services.node import NodeTreeComponent


class FakeToolkit:
    def run_file_diagnostics(self, repo_root: Path, file_path: Path) -> LeanDiagnosticsResult:
        return LeanDiagnosticsResult(ok=True, repo_root=str(repo_root), file_path=str(file_path), diagnostics=[], summary="ok")


class FakeLake:
    def run_lake_env_lean(self, *, repo_root: Path, rel_file: str, json: bool = True, timeout_seconds: int | None = None) -> ExternalCommandResult:
        del json, timeout_seconds
        return ExternalCommandResult(ok=True, command=["lake", "env", "lean", rel_file], cwd=str(repo_root), exit_code=0, summary="ok")

    def run_lake_build(self, repo_root: Path, target: str | None = None, targets=None, timeout_seconds=None):  # noqa: ANN001, ANN201
        del targets, timeout_seconds
        return ExternalCommandResult(ok=True, command=["lake", "build", target or ""], cwd=str(repo_root), exit_code=0, summary="built")

    def run_snippet_check(self, *, repo_root: Path, imports: list[str], code: str, timeout_seconds: int | None = None) -> LeanCheckSummaryView:
        del timeout_seconds
        return LeanCheckSummaryView(ok=True, command=["lake", "env", "lean"], summary=f"confirmed {code} from {imports[0]}")


class FakeExternal:
    def __init__(self) -> None:
        self.lean_mcp_toolkit = FakeToolkit()
        self.lean_toolkit = self.lean_mcp_toolkit
        self.lake = FakeLake()


class FakeAdapterProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def list_active_adapter_modules(self, repo_root: Path) -> ServiceResult[AdapterModuleListView]:
        del repo_root
        return self.foundation.ok(AdapterModuleListView(modules=["Upstream.Core", "Upstream.Core"], summary="active"))

    def list_visible_upstream_modules(self, repo_root: Path) -> ServiceResult[AdapterModuleListView]:
        del repo_root
        return self.foundation.ok(AdapterModuleListView(modules=["Upstream.Core"], summary="visible"))


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
        lean_decl_name: str,
    ) -> ServiceResult[DeclFileRevisionView]:
        del repo_root
        revision = self.revisions[(node_path, decl_name)]
        revision.setdefault("statement", {})["formal"] = {"code": code, "check": _current_check(check)}
        revision["lean_decl_name"] = lean_decl_name
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
        lean_decl_name: str,
    ) -> ServiceResult[DeclFileRevisionView]:
        del repo_root
        revision = self.revisions[(node_path, decl_name)]
        revision.setdefault("proof", {})["formal"] = {"code": code, "check": _current_check(check)}
        revision["lean_decl_name"] = lean_decl_name
        revision["state"] = "proved"
        return self.foundation.ok(DeclFileRevisionView.model_validate(revision))


class FakeRepairDeclProvider:
    def __init__(self, foundation: FoundationService, decls: dict[str, list[str]]) -> None:
        self.foundation = foundation
        self.decls = decls

    def list_active_decl_names(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[str]]:
        del repo_root
        return self.foundation.ok(self.decls.get(node_path, []))


class FakeRepairComponent:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation
        self.repaired_node_paths: list[str] = []

    def repair_node_projection(self, repo_root: Path, *, node_path: str) -> ServiceResult[ProjectionRepairView]:
        del repo_root
        self.repaired_node_paths.append(node_path)
        return self.foundation.ok(
            ProjectionRepairView(
                scope=node_path,
                changed=False,
                changed_files=[],
                actions=[],
                summary=f"repair wrapper used for {node_path}",
            )
        )

    def restore_working_projection_to_active_graph(self, repo_root: Path, *, node_path: str) -> ServiceResult[ProjectionRepairView]:
        return self.repair_node_projection(repo_root, node_path=node_path)


def _revision() -> dict[str, Any]:
    return {
        "decl_name": "main_result",
        "revision": 1,
        "kind": "theorem",
        "state": "specified",
        "version_status": "open",
        "module": "Main.Topic.Core.Theorems.main_result",
        "statement": {"nl": {"text": "The statement is true."}, "deps": []},
        "proof": {"nl": {"text": "Use triviality."}, "deps": []},
    }


def _current_check(check: LeanCheckView) -> dict[str, object]:
    return check.model_dump(mode="json")


def _create_content_node(repo_root: Path) -> None:
    tree = make_runtime().node.node_tree
    assert tree.ensure_root_scope_node(repo_root).ok
    assert tree.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    assert tree.create_content_node(
        repo_root,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Core ready.",
    ).ok


def test_lean_projection_service_composes_components_and_stage_wrappers(tmp_path: Path) -> None:
    runtime = make_runtime(external_overrides={"lean_mcp_toolkit": FakeToolkit(), "lake": FakeLake()})
    foundation = runtime.foundation
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    service = LeanProjectionService(
        runtime,
        decl_revision_provider=FakeRevisionProvider(foundation, revisions),
        repair_decl_provider=FakeRepairDeclProvider(foundation, {"Main.Topic.Core": ["main_result"]}),
    )

    prepared_statement = service.prepare_statement_formal_stage_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert prepared_statement.ok
    assert prepared_statement.value is not None
    statement_path = Path(prepared_statement.value.path)
    statement_path.write_text(
        statement_path.read_text(encoding="utf-8") + "theorem actualResult : True := by\n  sorry\n",
        encoding="utf-8",
    )

    captured_statement = service.capture_statement_formal(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert captured_statement.ok
    assert captured_statement.value is not None
    assert revisions[("Main.Topic.Core", "main_result")]["statement"]["formal"]["check"]["policy"] == "statement_formal"

    prepared_proof = service.prepare_proof_formal_stage_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert prepared_proof.ok
    assert prepared_proof.value is not None
    proof_path = Path(prepared_proof.value.path)
    proof_path.write_text(proof_path.read_text(encoding="utf-8").replace("sorry", "trivial"), encoding="utf-8")

    captured_proof = service.capture_proof_formal(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert captured_proof.ok
    assert captured_proof.value is not None
    proof_check = revisions[("Main.Topic.Core", "main_result")]["proof"]["formal"]["check"]
    assert proof_check["policy"] == "proof_formal"
    assert proof_check["allow_sorry"] is False


def test_lean_projection_service_refresh_wrappers(tmp_path: Path) -> None:
    runtime = make_runtime(external_overrides={"lean_mcp_toolkit": FakeToolkit(), "lake": FakeLake()})
    foundation = runtime.foundation
    _create_content_node(tmp_path)
    service = LeanProjectionService(
        runtime,
        adapter_facade_provider=FakeAdapterProvider(foundation),
    )

    node = service.refresh_node_projection(tmp_path, node_path="Main.Topic.Core")
    assert node.ok
    assert node.value is not None
    assert node.value.changed is True
    assert (tmp_path / "Main" / "Topic" / "Core" / "Prelude.lean").exists()
    assert (tmp_path / "Main" / "Topic" / "Core" / "Interfaces.lean").exists()

    adapter = service.refresh_adapter_projection(tmp_path)
    assert adapter.ok
    assert adapter.value is not None
    assert "public import Upstream.Core" in Path(adapter.value.path).read_text(encoding="utf-8")


def test_lean_projection_service_refresh_node_projection_is_repair_wrapper(tmp_path: Path) -> None:
    runtime = make_runtime(external_overrides={"lean_mcp_toolkit": FakeToolkit(), "lake": FakeLake()})
    foundation = runtime.foundation
    repair = FakeRepairComponent(foundation)
    service = LeanProjectionService(runtime, repair=repair)  # type: ignore[arg-type]

    result = service.refresh_node_projection(tmp_path, node_path="Main.Topic.Core")

    assert result.ok
    assert result.value is not None
    assert result.value.summary == "repair wrapper used for Main.Topic.Core"
    assert repair.repaired_node_paths == ["Main.Topic.Core"]


def test_lean_projection_service_missing_adapter_provider_fails_without_fake_projection(tmp_path: Path) -> None:
    runtime = make_runtime(external_overrides={"lean_mcp_toolkit": FakeToolkit(), "lake": FakeLake()})
    service = LeanProjectionService(runtime)

    result = service.refresh_adapter_projection(tmp_path)

    assert not result.ok
    assert [issue.kind for issue in result.issues] == ["adapter_facade_provider_missing"]
    assert not (tmp_path / "Main" / "Interfaces.lean").exists()


def test_lean_projection_service_restore_projection_wrapper(tmp_path: Path) -> None:
    runtime = make_runtime(external_overrides={"lean_mcp_toolkit": FakeToolkit(), "lake": FakeLake()})
    foundation = runtime.foundation
    _create_content_node(tmp_path)
    revision = _revision()
    revision["statement"]["formal"] = {
        "code": "import Main.Topic.Core.Prelude\n\n/-!\nlean-constellation target: main_result\nstage: statement\n-/\ntheorem main_result : True := by\n  sorry\n"
    }
    revision["proof"]["formal"] = {"code": revision["statement"]["formal"]["code"].replace("stage: statement", "stage: proof").replace("sorry", "trivial")}
    service = LeanProjectionService(
        runtime,
        decl_revision_provider=FakeRevisionProvider(foundation, {("Main.Topic.Core", "main_result"): revision}),
        repair_decl_provider=FakeRepairDeclProvider(foundation, {"Main.Topic.Core": ["main_result"]}),
    )

    restored = service.restore_projection_to_active_graph(tmp_path, node_path="Main.Topic.Core")

    assert restored.ok
    assert restored.value is not None
    assert restored.value.changed is True
    assert any(action.action == "sync_decl_file" for action in restored.value.actions)
