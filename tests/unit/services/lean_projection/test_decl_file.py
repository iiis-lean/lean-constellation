from tests.unit_services_helpers import make_runtime

from pathlib import Path
from typing import Any

from lean_constellation.services.external_clients import ExternalCommandResult, LeanDiagnosticsResult
from lean_constellation.services.decl_graph import DeclFileRevisionView
from lean_constellation.services.foundation import FoundationService, ServiceResult
from lean_constellation.services.lean_projection import DeclFileComponent, LeanCheckView


class FakeToolkit:
    def __init__(self, diagnostics: list[dict[str, Any]] | None = None, ok: bool = True) -> None:
        self.result = LeanDiagnosticsResult(
            ok=ok,
            repo_root=".",
            file_path=None,
            diagnostics=diagnostics or [],
            summary="fake toolkit diagnostics",
            issue_code=None if ok else "toolkit_unavailable",
        )

    def run_file_diagnostics(self, repo_root: Path, file_path: Path) -> LeanDiagnosticsResult:
        return self.result.model_copy(update={"repo_root": str(repo_root), "file_path": str(file_path)})


class FakeLake:
    def run_lake_env_lean(self, *, repo_root: Path, rel_file: str, json: bool = True, timeout_seconds: int | None = None) -> ExternalCommandResult:
        del json, timeout_seconds
        return ExternalCommandResult(
            ok=True,
            command=["lake", "env", "lean", "--json", rel_file],
            cwd=str(repo_root),
            exit_code=0,
            summary="fake lake diagnostics",
        )


class FakeExternal:
    def __init__(self, diagnostics: list[dict[str, Any]] | None = None, ok: bool = True) -> None:
        self.lean_mcp_toolkit = FakeToolkit(diagnostics=diagnostics, ok=ok)
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
            return self.foundation.fail(
                self.foundation.issue(
                    "decl_revision_missing",
                    "DeclRevision not found.",
                    object_ref=f"{node_path}:{decl_name}",
                )
            )
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
        revision.setdefault("statement", {})["formal"] = {
            "code": code,
            "check": _compact_check(check),
        }
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
        revision.setdefault("proof", {})["formal"] = {
            "code": code,
            "check": _compact_check(check),
        }
        revision["state"] = "proved"
        return self.foundation.ok(DeclFileRevisionView.model_validate(revision))


class FailingSaveRevisionProvider(FakeRevisionProvider):
    def __init__(
        self,
        foundation: FoundationService,
        revisions: dict[tuple[str, str], dict[str, Any]],
        *,
        fail_statement: bool = False,
        fail_proof: bool = False,
    ) -> None:
        super().__init__(foundation, revisions)
        self.fail_statement = fail_statement
        self.fail_proof = fail_proof

    def _save_failure(self, *, node_path: str, decl_name: str) -> ServiceResult[DeclFileRevisionView]:
        return self.foundation.fail(
            self.foundation.issue(
                "decl_revision_save_failed",
                "DeclRevision provider rejected the formal capture save.",
                object_ref=f"{node_path}:{decl_name}",
            )
        )

    def save_statement_formal_capture(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
    ) -> ServiceResult[DeclFileRevisionView]:
        if self.fail_statement:
            return self._save_failure(node_path=node_path, decl_name=decl_name)
        return super().save_statement_formal_capture(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            code=code,
            check=check,
        )

    def save_proof_formal_capture(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
    ) -> ServiceResult[DeclFileRevisionView]:
        if self.fail_proof:
            return self._save_failure(node_path=node_path, decl_name=decl_name)
        return super().save_proof_formal_capture(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            code=code,
            check=check,
        )


def _revision(kind: str = "theorem") -> dict[str, Any]:
    return {
        "decl_name": "main_result",
        "revision": 1,
        "kind": kind,
        "state": "specified",
        "version_status": "open",
        "statement": {
            "nl": {"text": "The main result is true.", "origin": [{"kind": "generated"}]},
            "deps": [],
        },
        "proof": {
            "nl": {"text": "Use triviality.", "origin": [{"kind": "generated"}]},
            "deps": [],
        },
    }


def _compact_check(check: LeanCheckView) -> dict[str, str]:
    return {
        "status": check.status,
        "policy": check.policy,
        "allow_sorry": str(check.allow_sorry),
        "contains_sorry": str(check.contains_sorry),
        "contains_axiom": str(check.contains_axiom),
        "message": check.message,
    }


def _component(tmp_path: Path, revisions: dict[tuple[str, str], dict[str, Any]], diagnostics: list[dict[str, Any]] | None = None) -> DeclFileComponent:
    del tmp_path
    runtime = make_runtime(external_overrides={"lean_mcp_toolkit": FakeToolkit(diagnostics=diagnostics), "lake": FakeLake()})
    provider = FakeRevisionProvider(runtime.foundation, revisions)
    return DeclFileComponent(runtime, lean_check=runtime.lean_projection.lean_check, revision_provider=provider)


def test_derive_decl_file_path_maps_kind_and_blocks_unsafe_name(tmp_path: Path) -> None:
    component = make_runtime().lean_projection.decl_file

    result = component.derive_decl_file_path(tmp_path, node_path="Main.Topic.Core", decl_name="main_result", kind="theorem")

    assert result.ok
    assert result.value is not None
    assert result.value.relative_path == "Main/Topic/Core/Theorems/main_result.lean"
    assert result.value.module == "Main.Topic.Core.Theorems.main_result"

    invalid = component.derive_decl_file_path(tmp_path, node_path="Main.Topic.Core", decl_name="../bad", kind="theorem")
    assert not invalid.ok
    assert invalid.issues[0].kind == "decl_file_path_invalid"


def test_prepare_statement_formal_file_writes_docstring_prelude_and_skeleton(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _component(tmp_path, revisions)

    result = component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")

    assert result.ok
    assert result.value is not None
    assert result.value.changed is True
    path = Path(result.value.path)
    text = path.read_text(encoding="utf-8")
    assert "import Main.Topic.Core.Prelude" in text
    assert "lean-constellation target: main_result" in text
    assert "stage: statement" in text
    assert "theorem main_result : True := by" in text
    assert "sorry" in text


def test_prepare_statement_formal_file_requires_statement_nl(tmp_path: Path) -> None:
    revision = _revision()
    revision["statement"]["nl"]["text"] = ""
    component = _component(tmp_path, {("Main.Topic.Core", "main_result"): revision})

    result = component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")

    assert not result.ok
    assert result.issues[0].kind == "statement_nl_missing"


def test_default_missing_revision_provider_fails_without_writing_decl_file(tmp_path: Path) -> None:
    runtime = make_runtime()
    component = DeclFileComponent(runtime, lean_check=runtime.lean_projection.lean_check)

    result = component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")

    assert not result.ok
    assert result.issues[0].kind == "decl_revision_provider_missing"
    assert not (tmp_path / "Main" / "Topic" / "Core" / "Theorems" / "main_result.lean").exists()


def test_capture_statement_validates_docstring_runs_check_and_saves_snapshot(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _component(tmp_path, revisions)
    assert component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok

    result = component.capture_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")

    assert result.ok
    assert result.value is not None
    assert result.value.check.status == "passed"
    formal = revisions[("Main.Topic.Core", "main_result")]["statement"]["formal"]
    assert "theorem main_result" in formal["code"]
    assert formal["check"]["policy"] == "statement_formal"


def test_capture_statement_propagates_revision_provider_save_failure(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    runtime = make_runtime(external_overrides={"lean_mcp_toolkit": FakeToolkit(), "lake": FakeLake()})
    provider = FailingSaveRevisionProvider(runtime.foundation, revisions, fail_statement=True)
    component = DeclFileComponent(runtime, lean_check=runtime.lean_projection.lean_check, revision_provider=provider)
    assert component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok

    result = component.capture_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")

    assert not result.ok
    assert result.issues[0].kind == "decl_revision_save_failed"
    assert "formal" not in revisions[("Main.Topic.Core", "main_result")]["statement"]


def test_capture_statement_rejects_changed_docstring_and_failed_check(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _component(tmp_path, revisions)
    prepared = component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert prepared.ok and prepared.value is not None
    path = Path(prepared.value.path)
    path.write_text(path.read_text(encoding="utf-8").replace("The main result is true.", "Changed."), encoding="utf-8")

    changed_doc = component.capture_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert not changed_doc.ok
    assert changed_doc.issues[0].kind == "system_docstring_changed"

    assert component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok
    failing = _component(tmp_path, revisions, diagnostics=[{"severity": "error", "message": "type mismatch"}])
    check_fail = failing.capture_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert not check_fail.ok
    assert check_fail.issues[0].kind == "statement_lean_check_failed"


def test_prepare_proof_formal_file_uses_statement_snapshot_and_drops_old_proof(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _component(tmp_path, revisions)
    assert component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok
    assert component.capture_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok
    revisions[("Main.Topic.Core", "main_result")]["proof"]["formal"] = {"code": "theorem main_result : False := by\n  exact False.elim (by contradiction)"}

    result = component.prepare_proof_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")

    assert result.ok
    assert result.value is not None
    text = Path(result.value.path).read_text(encoding="utf-8")
    assert "stage: proof" in text
    assert "Use triviality." in text
    assert "False.elim" not in text
    assert "theorem main_result : True := by" in text


def test_capture_proof_checks_header_strict_policy_and_saves_snapshot(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _component(tmp_path, revisions)
    assert component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok
    assert component.capture_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok
    prepared = component.prepare_proof_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert prepared.ok and prepared.value is not None
    path = Path(prepared.value.path)

    with_sorry = component.capture_proof_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert not with_sorry.ok
    assert with_sorry.issues[0].kind == "proof_lean_check_failed"

    path.write_text(path.read_text(encoding="utf-8").replace("sorry", "trivial"), encoding="utf-8")
    result = component.capture_proof_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")

    assert result.ok
    assert result.value is not None
    assert result.value.check.status == "passed"
    assert "trivial" in revisions[("Main.Topic.Core", "main_result")]["proof"]["formal"]["code"]

    path.write_text(path.read_text(encoding="utf-8").replace(": True :=", ": False :="), encoding="utf-8")
    changed_header = component.capture_proof_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert not changed_header.ok
    assert changed_header.issues[0].kind == "theorem_header_changed"


def test_capture_proof_accepts_multiline_equivalent_header_and_save_failure(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _component(tmp_path, revisions)
    assert component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok
    assert component.capture_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok
    prepared = component.prepare_proof_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert prepared.ok and prepared.value is not None
    path = Path(prepared.value.path)
    text = path.read_text(encoding="utf-8").replace(
        "theorem main_result : True := by\n  sorry",
        "theorem main_result\n    : True := by\n  trivial",
    )
    path.write_text(text, encoding="utf-8")

    result = component.capture_proof_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")

    assert result.ok
    assert result.value is not None
    assert "theorem main_result\n    : True := by" in revisions[("Main.Topic.Core", "main_result")]["proof"]["formal"]["code"]

    revisions_failed = {("Main.Topic.Core", "main_result"): _revision()}
    failing_runtime = make_runtime(external_overrides={"lean_mcp_toolkit": FakeToolkit(), "lake": FakeLake()})
    failing_provider = FailingSaveRevisionProvider(failing_runtime.foundation, revisions_failed, fail_proof=True)
    failing = DeclFileComponent(failing_runtime, lean_check=failing_runtime.lean_projection.lean_check, revision_provider=failing_provider)
    assert failing.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok
    assert failing.capture_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok
    failing_prepared = failing.prepare_proof_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert failing_prepared.ok and failing_prepared.value is not None
    failing_path = Path(failing_prepared.value.path)
    failing_path.write_text(failing_path.read_text(encoding="utf-8").replace("sorry", "trivial"), encoding="utf-8")

    failed = failing.capture_proof_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")

    assert not failed.ok
    assert failed.issues[0].kind == "decl_revision_save_failed"
    assert "formal" not in revisions_failed[("Main.Topic.Core", "main_result")]["proof"]


def test_check_snapshot_sync_detects_missing_and_changed_file(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _component(tmp_path, revisions)

    missing_snapshot = component.check_decl_file_snapshot_sync(tmp_path, node_path="Main.Topic.Core", decl_name="main_result", stage="statement")
    assert missing_snapshot.ok
    assert missing_snapshot.value is not None
    assert not missing_snapshot.value.passed
    assert missing_snapshot.value.issues[0].kind == "formal_capture_missing"

    assert component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok
    assert component.capture_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok

    synced = component.check_decl_file_snapshot_sync(tmp_path, node_path="Main.Topic.Core", decl_name="main_result", stage="statement")
    assert synced.ok
    assert synced.value is not None
    assert synced.value.passed

    path = tmp_path / "Main" / "Topic" / "Core" / "Theorems" / "main_result.lean"
    path.write_text(path.read_text(encoding="utf-8") + "\n-- edit after capture\n", encoding="utf-8")
    stale = component.check_decl_file_snapshot_sync(tmp_path, node_path="Main.Topic.Core", decl_name="main_result", stage="statement")
    assert stale.ok
    assert stale.value is not None
    assert not stale.value.passed
    assert stale.value.issues[0].kind == "decl_file_capture_stale"


def test_sync_after_revision_reset_and_remove_for_delete(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _component(tmp_path, revisions)
    assert component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok
    assert component.capture_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok
    prepared = component.prepare_proof_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert prepared.ok and prepared.value is not None
    path = Path(prepared.value.path)
    path.write_text(path.read_text(encoding="utf-8").replace("sorry", "trivial"), encoding="utf-8")
    assert component.capture_proof_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok

    path.write_text("bad working content\n", encoding="utf-8")
    sync_proof = component.sync_decl_file_after_revision_reset(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert sync_proof.ok
    assert path.read_text(encoding="utf-8") == revisions[("Main.Topic.Core", "main_result")]["proof"]["formal"]["code"]

    revisions[("Main.Topic.Core", "main_result")]["proof"].pop("formal")
    sync_statement = component.sync_decl_file_after_revision_reset(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert sync_statement.ok
    assert path.read_text(encoding="utf-8") == revisions[("Main.Topic.Core", "main_result")]["statement"]["formal"]["code"]

    revisions[("Main.Topic.Core", "main_result")]["statement"].pop("formal")
    sync_remove = component.sync_decl_file_after_revision_reset(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert sync_remove.ok
    assert not path.exists()

    assert component.prepare_statement_formal_file(tmp_path, node_path="Main.Topic.Core", decl_name="main_result").ok
    removed = component.remove_decl_file_for_delete(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert removed.ok
    assert removed.value is not None
    assert removed.value.changed is True
    assert not path.exists()


def test_sync_and_remove_decl_file_noop_and_delete_error_branches(tmp_path: Path) -> None:
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    component = _component(tmp_path, revisions)

    sync_noop = component.sync_decl_file_after_revision_reset(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert sync_noop.ok
    assert sync_noop.value is not None
    assert sync_noop.value.changed is False
    assert sync_noop.value.changed_items == []

    remove_noop = component.remove_decl_file_for_delete(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert remove_noop.ok
    assert remove_noop.value is not None
    assert remove_noop.value.changed is False
    assert remove_noop.value.changed_items == []

    path = tmp_path / "Main" / "Topic" / "Core" / "Theorems" / "main_result.lean"
    path.mkdir(parents=True)
    remove_error = component.remove_decl_file_for_delete(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert not remove_error.ok
    assert remove_error.issues[0].kind == "decl_file_delete_failed"

    sync_error = component.sync_decl_file_after_revision_reset(tmp_path, node_path="Main.Topic.Core", decl_name="main_result")
    assert not sync_error.ok
    assert sync_error.issues[0].kind == "decl_file_delete_failed"
