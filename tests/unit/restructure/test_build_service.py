from pathlib import Path
from lean_constellation.domain.restructure import RepoSpec, RestructureStage, WorkspacePlan, BuildReceipt
from lean_constellation.services.restructure import RestructureBuildService, RestructureLayout, RestructureStore


def test_build_view_is_frozen_and_placeholder_audit(tmp_path: Path):
    store = RestructureStore(tmp_path)
    layout = RestructureLayout(store)
    layout.prepare_repo("repo", "Pkg")
    source = tmp_path / "repo" / "Pkg" / "Main" / "Defs.lean"
    source.write_text("def bad : Nat := by sorry\ntheorem staged : True := by sorry\n", encoding="utf-8")
    service = RestructureBuildService(store)
    view = service.freeze_view("repo", operation_id="op1")
    source.write_text("def bad : Nat := by sorry\ntheorem staged : True := by sorry\n", encoding="utf-8")
    assert (view.root / "Pkg" / "Main" / "Defs.lean").read_text(encoding="utf-8").startswith("def bad : Nat := by sorry")
    assert any("non-theorem" in finding for finding in service.audit_placeholders("repo"))
    assert service.audit_placeholders("repo", final=True)


def test_final_build_requires_successful_provider_pin(tmp_path: Path):
    store = RestructureStore(tmp_path)
    store.save_workspace_plan(
        WorkspacePlan(
            run_id="run",
            workspace_root=str(tmp_path),
            main_repo="consumer",
            repos={
                "provider": RepoSpec(key="provider", directory="provider", module_root="Provider", goal="provider"),
                "consumer": RepoSpec(key="consumer", directory="consumer", module_root="Consumer", goal="consumer"),
            },
        )
    )
    layout = RestructureLayout(store)
    layout.prepare_repo("consumer", "Consumer")
    result = RestructureBuildService(store).build(
        "consumer",
        operation_id="consumer-final",
        stage=RestructureStage.FINAL,
        repo_key="consumer",
        provider_refs={"provider": "provider-final"},
    )
    assert not result.receipt.success
    assert "provider pin is missing" in result.receipt.diagnostics[0]
