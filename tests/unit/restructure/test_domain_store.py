from pathlib import Path

import pytest

from lean_constellation.domain.restructure import (
    CompletionTarget,
    DeclKind,
    DeclRecord,
    DeclStatus,
    NodeKind,
    NodeRecord,
    RepoPlan,
    RepoSpec,
    WorkspacePlan,
)
from lean_constellation.services.restructure import RestructureLayout, RestructureStore, StoreConflict


def _plan() -> WorkspacePlan:
    repo_plan = RepoPlan(
        repo_key="main-key",
        directory="physical-main",
        module_root="Result",
        goal="prove the result",
        nodes={
            "Main": NodeRecord(path="Main", kind=NodeKind.MAIN, summary="root", goal="root", boundary="all", module="Main"),
            "Main.Algebra": NodeRecord(path="Main.Algebra", kind=NodeKind.SCOPE, parent="Main", summary="algebra", goal="algebra", boundary="algebra", module="Main.Algebra"),
            "Main.Algebra.Bounds": NodeRecord(path="Main.Algebra.Bounds", kind=NodeKind.CONTENT, parent="Main.Algebra", summary="bounds", goal="bounds", boundary="bounds", module="Main.Algebra.Bounds"),
        },
    )
    return WorkspacePlan(
        run_id="run-1",
        workspace_root="/tmp/ws",
        main_repo="main-key",
        repos={"main-key": RepoSpec(key="main-key", directory="physical-main", module_root="Result", goal="result", plan=repo_plan)},
        completion_target=CompletionTarget.DECLARED,
    )


def test_plan_allows_logical_key_different_from_directory_and_rejects_cycles():
    plan = _plan()
    assert plan.repos["main-key"].directory == "physical-main"
    with pytest.raises(ValueError, match="cycle"):
        RepoPlan(
            repo_key="r",
            directory="r",
            module_root="R",
            goal="g",
            nodes={
                "Main": NodeRecord(path="Main", kind=NodeKind.MAIN, summary="", goal="", boundary="test responsibility", module="Main"),
                "Main.A": NodeRecord(path="Main.A", kind=NodeKind.SCOPE, parent="Main", summary="", goal="", boundary="test responsibility", module="Main.A", dependencies=["Main.B"]),
                "Main.B": NodeRecord(path="Main.B", kind=NodeKind.CONTENT, parent="Main", summary="", goal="", boundary="test responsibility", module="Main.B", dependencies=["Main.A"]),
            },
        )


def test_declared_record_requires_statement_nl():
    with pytest.raises(ValueError):
        DeclRecord(name="x", lean_name="x", kind=DeclKind.THEOREM, summary="x", file="Main/Theorems/x.lean", status=DeclStatus.DECLARED)


def test_store_cas_and_layout(tmp_path: Path):
    plan = _plan()
    store = RestructureStore(tmp_path)
    version = store.save_workspace_plan(plan)
    assert version == 1
    loaded, loaded_version = store.load_workspace_plan()
    assert loaded is not None and loaded_version == 1
    with pytest.raises(StoreConflict):
        store.save_workspace_plan(plan, expected_version=0)
    layout = RestructureLayout(store)
    layout.prepare_workspace(plan)
    assert (tmp_path / "physical-main" / "Result" / "Main" / "Prelude.lean").exists()
    assert (tmp_path / "physical-main" / ".lean_constellation" / "restructure").is_dir()
    assert not (tmp_path / "physical-main" / ".lean_constellation" / "repo_format.json").exists()


def test_v2_rejects_legacy_scope_and_blank_accepted_explanation():
    from lean_constellation.domain.restructure import DeclarationSection, SectionNL
    with pytest.raises(ValueError):
        NodeRecord(path='Main', kind='main', summary='root', goal='root', scope='parent', module='Main')
    with pytest.raises(ValueError):
        NodeRecord(path='Main', kind='main', summary='root', goal='root', boundary=' ', module='Main')
    with pytest.raises(ValueError):
        DeclRecord(name='x', lean_name='x', kind='def', summary='x', file='Main/x.lean',
                   status='declared', statement=DeclarationSection(nl=SectionNL(text='  ')))


def test_store_roundtrip_without_schema_marker_and_rejects_legacy_nodes(tmp_path):
    store = RestructureStore(tmp_path)
    store.save_workspace_plan(_plan())
    payload = store.read_json(store.workspace_plan_path)
    assert 'schema_version' not in str(payload)
    assert store.load_workspace_plan()[0] == _plan()
    node = payload['repos']['main-key']['plan']['nodes']['Main']
    node['scope'] = node.pop('boundary')
    store._atomic_write(store.workspace_plan_path, payload)
    with pytest.raises(ValueError):
        store.load_workspace_plan()


def test_declaration_schema_has_no_version_marker():
    decl = DeclRecord(name='x', lean_name='x', kind='def', summary='x', file='Main/x.lean')
    assert 'schema_version' not in decl.model_dump()
    assert 'schema_version' not in DeclRecord.model_json_schema()['properties']
