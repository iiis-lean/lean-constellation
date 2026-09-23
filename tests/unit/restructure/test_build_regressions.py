from pathlib import Path
import shutil

import pytest

from lean_constellation.domain.restructure import RepoSpec, WorkspacePlan, RestructureStage
from lean_constellation.services.restructure import RestructureService


def _fixture(tmp_path):
    if not shutil.which("lake"):
        pytest.skip("Lake is not installed")
    service = RestructureService(tmp_path)
    service.prepare(WorkspacePlan(run_id="test", workspace_root=str(tmp_path), main_repo="repo",
        repos={"repo": RepoSpec(key="repo", directory="repo", module_root="Pkg", goal="test")}))
    repo = tmp_path / "repo"
    (repo / "lean-toolchain").write_text("leanprover/lean4:v4.32.0\n")
    (repo / "lakefile.toml").write_text('name = "fixture"\nversion = "0.1.0"\ndefaultTargets = ["Pkg"]\n[[lean_lib]]\nname = "Pkg"\n')
    (repo / "Pkg.lean").write_text("-- root intentionally does not import private modules\n")
    return service, repo


def test_complete_inventory_and_incremental_cache(tmp_path):
    service, repo = _fixture(tmp_path)
    private = repo / "Pkg/Main/Private.lean"
    private.write_text('def broken : Nat := "not a Nat"\n')
    failed = service.builds.build("repo", stage=RestructureStage.FINAL)
    assert not failed.receipt.success
    assert "Type mismatch" in failed.output
    private.write_text("def fixed : Nat := 1\n")
    good = service.builds.build("repo", stage=RestructureStage.FINAL)
    assert good.receipt.success, good.output
    work = service.store.repo_metadata_root("repo") / "build_work"
    cached = work / ".lake/build/lib/lean/Pkg/Main/Private.olean"
    mtime = cached.stat().st_mtime_ns
    source_mtime = (work / "Pkg/Main/Private.lean").stat().st_mtime_ns
    again = service.builds.build("repo", stage=RestructureStage.FINAL)
    assert again.receipt.success, again.output
    assert cached.stat().st_mtime_ns == mtime
    assert (work / "Pkg/Main/Private.lean").stat().st_mtime_ns == source_mtime
    assert good.view.root != again.view.root


def test_compiled_audit_checks_private_and_generated_constants(tmp_path):
    service, repo = _fixture(tmp_path)
    private = repo / "Pkg/Main/Private.lean"
    private.write_text("private theorem unfinished : True := by\n  sorry\ntheorem downstream : True := unfinished\n")
    declared = service.builds.build("repo", stage=RestructureStage.DECLARED)
    assert declared.receipt.success, declared.output
    final = service.builds.build("repo", stage=RestructureStage.FINAL)
    assert not final.receipt.success
    assert "forbidden axiom sorryAx" in final.output
    private.write_text("def incomplete : Nat := by\n  sorry\n")
    declared_bad = service.builds.build("repo", stage=RestructureStage.DECLARED)
    assert not declared_bad.receipt.success
    assert "non-theorem placeholder" in declared_bad.output
    private.write_text("axiom invented : False\n")
    new_axiom = service.builds.build("repo", stage=RestructureStage.FINAL)
    assert not new_axiom.receipt.success
    assert "forbidden axiom invented" in new_axiom.output
