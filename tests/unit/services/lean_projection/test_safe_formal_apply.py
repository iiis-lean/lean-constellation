from __future__ import annotations

from pathlib import Path

from lean_constellation.services.external_clients import ExternalCommandResult, LeanDiagnosticsResult
from tests.unit.services.lean_projection.test_formal_stage_sync import (
    DECL_NAME,
    NODE_PATH,
    _current_revision,
    _setup_theorem_round,
)
from tests.unit_services_helpers import make_runtime


class _Toolkit:
    def run_file_diagnostics(self, repo_root: Path, file_path: Path) -> LeanDiagnosticsResult:
        return LeanDiagnosticsResult(ok=True, repo_root=str(repo_root), file_path=str(file_path), diagnostics=[], summary="ok")


class _Lake:
    def run_lake_env_lean(self, *, repo_root: Path, rel_file: str, json: bool = True, timeout_seconds: int | None = None) -> ExternalCommandResult:
        del json, timeout_seconds
        return ExternalCommandResult(ok=True, command=["lake", "env", "lean", rel_file], cwd=str(repo_root), exit_code=0, summary="ok")


def _runtime():
    return make_runtime(external_overrides={"lean_mcp_toolkit": _Toolkit(), "lake": _Lake()})


def test_safe_statement_apply_generates_check_and_refreshes_projection(tmp_path: Path) -> None:
    runtime = _runtime()
    _setup_theorem_round(tmp_path, runtime)
    prepared = runtime.lean_projection.prepare_statement_formal_stage_file(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME)
    assert prepared.ok and prepared.value is not None
    code = Path(prepared.value.path).read_text(encoding="utf-8")
    digest = runtime.lean_projection.current_revision_digest(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME)
    assert digest.ok and digest.value is not None

    applied = runtime.lean_projection.apply_statement_formal_code(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
        lean_code=code,
        expected_revision=1,
        expected_state="planned",
        expected_revision_digest=digest.value,
    )

    assert applied.ok, applied.issues
    revision = _current_revision(runtime, tmp_path)
    assert revision.statement_lean_check is not None
    assert revision.statement_lean_check["status"] == "passed"
    assert Path(applied.value.file_path).read_text(encoding="utf-8") == revision.statement_lean_code


def test_safe_statement_apply_rolls_back_file_truth_and_projection_on_refresh_failure(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime()
    _setup_theorem_round(tmp_path, runtime)
    prepared = runtime.lean_projection.prepare_statement_formal_stage_file(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME)
    assert prepared.ok and prepared.value is not None
    path = Path(prepared.value.path)
    before_file = path.read_bytes()
    before_revision = _current_revision(runtime, tmp_path).model_dump(mode="json")
    digest = runtime.lean_projection.current_revision_digest(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME)
    assert digest.ok and digest.value is not None

    def fail_refresh(repo_root: Path, *, node_path: str):
        del repo_root, node_path
        return runtime.foundation.fail(runtime.foundation.issue("injected_projection_failure", "injected"))

    monkeypatch.setattr(runtime.lean_projection.repair.node_projection, "refresh_prelude", fail_refresh)
    applied = runtime.lean_projection.apply_statement_formal_code(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
        lean_code=path.read_text(encoding="utf-8") + "\n-- candidate\n",
        expected_revision=1,
        expected_state="planned",
        expected_revision_digest=digest.value,
    )

    assert not applied.ok
    assert path.read_bytes() == before_file
    assert _current_revision(runtime, tmp_path).model_dump(mode="json") == before_revision


def test_safe_statement_apply_rejects_stale_revision_without_writing(tmp_path: Path) -> None:
    runtime = _runtime()
    _setup_theorem_round(tmp_path, runtime)
    prepared = runtime.lean_projection.prepare_statement_formal_stage_file(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME)
    assert prepared.ok and prepared.value is not None
    path = Path(prepared.value.path)
    before = path.read_bytes()

    result = runtime.lean_projection.apply_statement_formal_code(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
        lean_code=path.read_text(encoding="utf-8"),
        expected_revision=2,
        expected_state="planned",
        expected_revision_digest="stale",
    )

    assert not result.ok
    assert result.issues[0].kind == "formal_apply_revision_stale"
    assert path.read_bytes() == before
