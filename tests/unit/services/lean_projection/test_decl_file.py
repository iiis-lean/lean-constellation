from pathlib import Path
from typing import Any

from tests.unit_services_helpers import initialize_native_test_repo, make_runtime

from lean_constellation.services.decl_graph import DeclFileRevisionView
from lean_constellation.services.external_clients import (
    ExternalCommandResult,
    LeanCheckSummaryView,
    LeanDiagnosticsResult,
)
from lean_constellation.services.foundation import FoundationService, ServiceResult
from lean_constellation.services.lean_projection import DeclFileComponent, LeanCheckView


class FakeToolkit:
    def __init__(self, diagnostics: list[dict[str, Any]] | None = None) -> None:
        self.result = LeanDiagnosticsResult(
            ok=True,
            repo_root=".",
            file_path=None,
            diagnostics=diagnostics or [],
            summary="fake toolkit diagnostics",
        )

    def run_file_diagnostics(self, repo_root: Path, file_path: Path) -> LeanDiagnosticsResult:
        return self.result.model_copy(update={"repo_root": str(repo_root), "file_path": str(file_path)})


class FakeLake:
    def run_lake_env_lean(self, *, repo_root: Path, rel_file: str, json: bool = True, timeout_seconds: int | None = None) -> ExternalCommandResult:
        del json, timeout_seconds
        return ExternalCommandResult(ok=True, command=["lake", "env", "lean", rel_file], cwd=str(repo_root), exit_code=0, summary="diagnostics passed")

    def run_lake_build(self, repo_root: Path, target: str | None = None, targets=None, timeout_seconds=None):  # noqa: ANN001, ANN201
        del targets, timeout_seconds
        return ExternalCommandResult(ok=True, command=["lake", "build", target or ""], cwd=str(repo_root), exit_code=0, summary="module built")

    def run_snippet_check(self, *, repo_root: Path, imports: list[str], code: str, timeout_seconds: int | None = None) -> LeanCheckSummaryView:
        del timeout_seconds
        return LeanCheckSummaryView(ok=True, command=["lake", "env", "lean"], summary=f"confirmed {code} from {imports[0]}")


class FakeRevisionProvider:
    def __init__(self, foundation: FoundationService, revisions: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.foundation = foundation
        self.revisions = revisions

    def get_current_decl_revision(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[DeclFileRevisionView]:
        del repo_root
        revision = self.revisions.get((node_path, decl_name))
        if revision is None:
            return self.foundation.fail(self.foundation.issue("decl_revision_missing", "DeclRevision not found."))
        return self.foundation.ok(DeclFileRevisionView.model_validate(revision))

    def save_statement_formal_capture(self, repo_root: Path, *, node_path: str, decl_name: str, code: str, check: LeanCheckView, lean_decl_name: str) -> ServiceResult[DeclFileRevisionView]:
        del repo_root
        revision = self.revisions[(node_path, decl_name)]
        revision.setdefault("statement", {})["formal"] = {"code": code, "check": check.model_dump(mode="json")}
        revision["lean_decl_name"] = lean_decl_name
        revision["state"] = "declared"
        return self.foundation.ok(DeclFileRevisionView.model_validate(revision))

    def save_proof_formal_capture(self, repo_root: Path, *, node_path: str, decl_name: str, code: str, check: LeanCheckView, lean_decl_name: str) -> ServiceResult[DeclFileRevisionView]:
        del repo_root
        revision = self.revisions[(node_path, decl_name)]
        revision.setdefault("proof", {})["formal"] = {"code": code, "check": check.model_dump(mode="json")}
        revision["lean_decl_name"] = lean_decl_name
        revision["state"] = "proved"
        return self.foundation.ok(DeclFileRevisionView.model_validate(revision))


class FailingSaveRevisionProvider(FakeRevisionProvider):
    def save_statement_formal_capture(self, repo_root: Path, *, node_path: str, decl_name: str, code: str, check: LeanCheckView, lean_decl_name: str) -> ServiceResult[DeclFileRevisionView]:
        del repo_root, code, check, lean_decl_name
        return self.foundation.fail(self.foundation.issue("decl_revision_save_failed", "Rejected.", object_ref=f"{node_path}:{decl_name}"))


def _revision(kind: str = "theorem", *, name: str = "main_result", module: str | None = None) -> dict[str, Any]:
    module = module or f"Main.Topic.Core.{'Defs' if kind == 'definition' else 'Theorems'}.{name}"
    return {
        "decl_name": name,
        "revision": 1,
        "kind": kind,
        "state": "specified",
        "version_status": "open",
        "module": module,
        "statement": {"nl": {"text": "The main result is true.", "origin": []}, "deps": []},
        "proof": {"nl": {"text": "Use triviality.", "origin": []}, "deps": []},
    }


def _component(revisions: dict[tuple[str, str], dict[str, Any]], diagnostics: list[dict[str, Any]] | None = None, *, failing_save: bool = False) -> DeclFileComponent:
    runtime = make_runtime(external_overrides={"lean_mcp_toolkit": FakeToolkit(diagnostics), "lake": FakeLake()})
    provider = FailingSaveRevisionProvider(runtime.foundation, revisions) if failing_save else FakeRevisionProvider(runtime.foundation, revisions)
    return DeclFileComponent(runtime, lean_check=runtime.lean_projection.lean_check, revision_provider=provider)


def _append_statement_target(path: Path, *, source_name: str = "actualResult", body: str = "by\n  sorry") -> None:
    path.write_text(path.read_text(encoding="utf-8") + f"theorem {source_name} : True := {body}\n", encoding="utf-8")


def _prepare_with_target(component: DeclFileComponent, repo_root: Path) -> Path:
    prepared = component.prepare_statement_formal_file(repo_root, node_path="Main.Topic.Core", decl_name="main_result")
    assert prepared.ok and prepared.value is not None, prepared.issues
    path = Path(prepared.value.path)
    _append_statement_target(path)
    return path


def test_derive_decl_file_path_is_flat_and_native_project_qualified(tmp_path: Path) -> None:
    initialize_native_test_repo(tmp_path, project_name="ExampleRepo")
    component = make_runtime().lean_projection.decl_file
    result = component.derive_decl_file_path(tmp_path, node_path="Main.Topic.Core", decl_name="main_result", kind="theorem")
    assert result.ok and result.value is not None
    assert result.value.relative_path == "ExampleRepo/Main/Topic/Core/Theorems/main_result.lean"
    assert result.value.module == "ExampleRepo.Main.Topic.Core.Theorems.main_result"
    dotted = component.derive_decl_file_path(tmp_path, node_path="Main.Topic.Core", decl_name="Example.result", kind="theorem")
    assert not dotted.ok and dotted.issues[0].kind == "decl_file_path_invalid"


def test_prepare_writes_managed_regions_and_no_fabricated_skeleton(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _component(revisions)
    prepared = component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert prepared.ok and prepared.value is not None
    text = Path(prepared.value.path).read_text(encoding="utf-8")
    assert text.startswith("-- lean-constellation: managed-imports-begin")
    assert "import Main.Topic.Core.Prelude" in text
    assert "-- lean-constellation: declaration-source-begin" in text
    assert "# lean-constellation target: `main_result`" in text
    assert "theorem main_result" not in text
    assert "sorry" not in text


def test_prepare_refresh_preserves_agent_helpers_and_primary_source(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _component(revisions)
    path = _prepare_with_target(component, tmp_path)
    text = path.read_text(encoding="utf-8").replace(
        "/--\n# lean-constellation",
        "private lemma helper : True := by trivial\n\n/--\n# lean-constellation",
    )
    path.write_text(text, encoding="utf-8")
    revisions[("Main.Topic.Core", "main_result")]["statement"]["nl"]["text"] = "Updated mathematical statement."
    refreshed = component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert refreshed.ok
    current = path.read_text(encoding="utf-8")
    assert "private lemma helper : True := by trivial" in current
    assert "theorem actualResult : True := by" in current
    assert "Updated mathematical statement." in current


def test_prepare_refresh_rejects_content_between_managed_regions(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _component(revisions)
    prepared = component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert prepared.ok and prepared.value is not None
    path = Path(prepared.value.path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "-- lean-constellation: managed-imports-end\n\n",
            "-- lean-constellation: managed-imports-end\n\n#check True\n\n",
            1,
        ),
        encoding="utf-8",
    )

    refreshed = component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")

    assert not refreshed.ok
    assert refreshed.issues[0].kind == "decl_managed_region_gap_invalid"
    assert "#check True" in path.read_text(encoding="utf-8")


def test_same_node_dependency_import_and_docstring_use_module_and_full_name(tmp_path: Path) -> None:
    helper = _revision(kind="definition", name="helper")
    helper["state"] = "declared"
    helper["version_status"] = "committed"
    helper["lean_decl_name"] = "Example.helper"
    result = _revision()
    result["statement"]["deps"] = [
        {"kind": "repo_decl", "ref": {"node": "Main.Topic.Core", "name": "helper", "revision": 1}}
    ]
    component = _component({("Main.Topic.Core", "helper"): helper, ("Main.Topic.Core", "main_result"): result})
    prepared = component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert prepared.ok and prepared.value is not None
    text = Path(prepared.value.path).read_text(encoding="utf-8")
    assert "import Main.Topic.Core.Defs.helper" in text
    assert "`Main.Topic.Core::helper` → `Example.helper` from `Main.Topic.Core.Defs.helper`" in text


def test_capture_builds_module_confirms_full_name_and_saves_truth(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _component(revisions)
    _prepare_with_target(component, tmp_path)
    captured = component.capture_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert captured.ok and captured.value is not None, captured.issues
    assert captured.value.lean_decl_name == "actualResult"
    assert revisions[("Main.Topic.Core", "main_result")]["lean_decl_name"] == "actualResult"
    assert "theorem actualResult" in revisions[("Main.Topic.Core", "main_result")]["statement"]["formal"]["code"]


def test_capture_rejects_docstring_drift_failed_diagnostics_and_save_failure(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _component(revisions)
    path = _prepare_with_target(component, tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("The main result is true.", "Changed."), encoding="utf-8")
    drift = component.capture_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert not drift.ok and drift.issues[0].kind == "system_docstring_changed"

    revisions2 = {("Main.Topic.Core", "main_result"): _revision()}
    diagnostics = _component(revisions2, diagnostics=[{"severity": "error", "message": "type mismatch"}])
    _prepare_with_target(diagnostics, tmp_path / "diagnostics")
    failed_check = diagnostics.capture_statement_formal_file(tmp_path / "diagnostics", node_path="Main.Topic.Core", decl_name="main_result")
    assert not failed_check.ok and failed_check.issues[0].kind == "statement_lean_check_failed"

    revisions3 = {("Main.Topic.Core", "main_result"): _revision()}
    save_failure = _component(revisions3, failing_save=True)
    _prepare_with_target(save_failure, tmp_path / "save")
    failed_save = save_failure.capture_statement_formal_file(tmp_path / "save", node_path="Main.Topic.Core", decl_name="main_result")
    assert not failed_save.ok and failed_save.issues[0].kind == "decl_revision_save_failed"
    assert "formal" not in revisions3[("Main.Topic.Core", "main_result")]["statement"]


def test_proof_prepare_preserves_statement_header_and_capture_requires_identity(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _component(revisions)
    path = _prepare_with_target(component, tmp_path)
    statement = component.capture_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert statement.ok, statement.issues
    proof = component.prepare_proof_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert proof.ok and proof.value is not None
    proof_text = path.read_text(encoding="utf-8")
    assert "## Proof outline\n\nUse triviality." in proof_text
    assert "theorem actualResult : True := by" in proof_text
    path.write_text(proof_text.replace("sorry", "trivial"), encoding="utf-8")
    captured = component.capture_proof_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert captured.ok and captured.value is not None, captured.issues
    assert captured.value.lean_decl_name == "actualResult"

    path.write_text(path.read_text(encoding="utf-8").replace("theorem actualResult", "theorem changedResult"), encoding="utf-8")
    changed = component.capture_proof_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert not changed.ok
    assert changed.issues[0].kind in {"theorem_header_changed", "proof_lean_decl_name_changed"}


def test_snapshot_sync_reset_and_delete_use_canonical_file(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _component(revisions)
    path = _prepare_with_target(component, tmp_path)
    assert component.capture_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok
    synced = component.check_decl_file_snapshot_sync(tmp_path, node_path="Main.Topic.Core", decl_name="main_result", stage="statement")
    assert synced.ok and synced.value is not None and synced.value.passed
    path.write_text(path.read_text(encoding="utf-8") + "\n-- stale\n", encoding="utf-8")
    stale = component.check_decl_file_snapshot_sync(tmp_path, node_path="Main.Topic.Core", decl_name="main_result", stage="statement")
    assert stale.ok and stale.value is not None and not stale.value.passed

    reset = component.sync_decl_file_after_revision_reset(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert reset.ok
    assert path.read_text(encoding="utf-8") == revisions[("Main.Topic.Core", "main_result")]["statement"]["formal"]["code"]
    removed = component.remove_decl_file_for_delete(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert removed.ok and not path.exists()
