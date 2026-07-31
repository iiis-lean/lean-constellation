from __future__ import annotations

from pathlib import Path

from lean_constellation.domain.refs import MathlibRef
from lean_constellation.services.decl_graph.models import MathlibDeclDep
from lean_constellation.services.external_clients import ExternalCommandResult, LeanCheckSummaryView, LeanDiagnosticsResult
from lean_constellation.services.lean_projection.module_identity import module_artifact_relpaths
from tests.unit.services.lean_projection.test_formal_stage_sync import (
    DECL_NAME,
    NODE_PATH,
    _current_revision,
    _setup_theorem_round,
    _write_statement_target,
)
from tests.unit_services_helpers import make_runtime


class _Toolkit:
    def run_file_diagnostics(self, repo_root: Path, file_path: Path) -> LeanDiagnosticsResult:
        return LeanDiagnosticsResult(ok=True, repo_root=str(repo_root), file_path=str(file_path), diagnostics=[], summary="ok")


class _Lake:
    def run_lake_env_lean(self, *, repo_root: Path, rel_file: str, json: bool = True, timeout_seconds: int | None = None) -> ExternalCommandResult:
        del json, timeout_seconds
        return ExternalCommandResult(ok=True, command=["lake", "env", "lean", rel_file], cwd=str(repo_root), exit_code=0, summary="ok")

    def run_lake_build(self, repo_root: Path, target: str | None = None, targets=None, timeout_seconds=None):  # noqa: ANN001, ANN201
        del targets, timeout_seconds
        if target and target.startswith("+"):
            for relpath in module_artifact_relpaths(target[1:]):
                artifact = Path(repo_root) / relpath
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_text("candidate artifact", encoding="utf-8")
        return ExternalCommandResult(ok=True, command=["lake", "build", target or ""], cwd=str(repo_root), exit_code=0, summary="built")

    def run_snippet_check(self, *, repo_root: Path, imports: list[str], code: str, timeout_seconds: int | None = None) -> LeanCheckSummaryView:
        del timeout_seconds
        return LeanCheckSummaryView(ok=True, command=["lake", "env", "lean"], summary=f"confirmed {code} from {imports[0]}")


def _runtime():
    return make_runtime(external_overrides={"lean_mcp_toolkit": _Toolkit(), "lake": _Lake()})


def test_safe_statement_apply_generates_check_and_refreshes_projection(tmp_path: Path) -> None:
    runtime = _runtime()
    _setup_theorem_round(tmp_path, runtime)
    prepared = runtime.lean_projection.prepare_statement_formal_stage_file(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME)
    assert prepared.ok and prepared.value is not None
    _write_statement_target(Path(prepared.value.path))
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
    assert revision.statement.formal is not None
    assert revision.statement.formal.check is not None
    assert revision.statement.formal.check.status == "passed"
    assert Path(applied.value.file_path).read_text(encoding="utf-8") == revision.statement.formal.code


def test_safe_statement_apply_rolls_back_file_truth_and_projection_on_refresh_failure(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime()
    _setup_theorem_round(tmp_path, runtime)
    prepared = runtime.lean_projection.prepare_statement_formal_stage_file(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME)
    assert prepared.ok and prepared.value is not None
    path = Path(prepared.value.path)
    _write_statement_target(path)
    before_file = path.read_bytes()
    before_revision = _current_revision(runtime, tmp_path).model_dump(mode="json")
    module = prepared.value.module
    artifact = tmp_path / module_artifact_relpaths(module)[0]
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("previous artifact", encoding="utf-8")
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
    assert artifact.read_text(encoding="utf-8") == "previous artifact"


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


def test_reviewer_dependency_recapture_rejects_and_restores_unmanaged_edit(
    tmp_path: Path,
) -> None:
    runtime = _runtime()
    round_id = _setup_theorem_round(tmp_path, runtime)
    prepared = runtime.lean_projection.prepare_statement_formal_stage_file(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
    )
    assert prepared.ok and prepared.value is not None
    path = Path(prepared.value.path)
    _write_statement_target(path)
    assert runtime.lean_projection.capture_statement_formal(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
    ).ok
    assert runtime.mathlib.upsert_mathlib_decl_entry(
        tmp_path,
        name="Nat.succ",
        module="Mathlib.Data.Nat.Basic",
        kind="def",
        signature="Nat → Nat",
    ).ok
    before_file = path.read_bytes()
    before_revision = _current_revision(runtime, tmp_path).model_dump(mode="json")

    def mutate():
        result = runtime.decl_graph.add_statement_dep(
            tmp_path,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name=DECL_NAME,
            dep=MathlibDeclDep(
                ref=MathlibRef(
                    name="Nat.succ",
                    module="Mathlib.Data.Nat.Basic",
                ),
                reason="The statement uses successor.",
            ),
        )
        assert result.ok, result.issues
        path.write_text(
            path.read_text(encoding="utf-8") + "\n-- unauthorized reviewer edit\n",
            encoding="utf-8",
        )
        return result

    result = runtime.lean_projection.apply_dependency_mutation_with_capture(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
        stage="statement",
        capture_mode="required",
        mutate=mutate,
    )

    assert not result.ok
    assert result.issues[0].kind == "dependency_recapture_unmanaged_source_changed"
    assert path.read_bytes() == before_file
    assert _current_revision(runtime, tmp_path).model_dump(mode="json") == before_revision
