from pathlib import Path

from tests.unit_services_helpers import initialize_native_test_repo

from lean_constellation.services.foundation.module_layout import (
    local_module_name,
    local_projection_path,
    native_decl_module,
)


def test_native_module_and_projection_mapping_is_project_qualified_and_idempotent(tmp_path: Path) -> None:
    initialize_native_test_repo(tmp_path, project_name="ExampleRepo")
    logical_path = tmp_path / "Main" / "Topic" / "Prelude.lean"
    physical_path = tmp_path / "ExampleRepo" / "Main" / "Topic" / "Prelude.lean"

    assert local_module_name(tmp_path, "Main.Topic.Prelude") == "ExampleRepo.Main.Topic.Prelude"
    assert local_module_name(tmp_path, "ExampleRepo.Main.Topic.Prelude") == "ExampleRepo.Main.Topic.Prelude"
    assert local_projection_path(tmp_path, logical_path) == physical_path
    assert local_projection_path(tmp_path, physical_path) == physical_path
    assert native_decl_module(
        tmp_path,
        node_path="Main.Topic",
        kind="theorem",
        decl_name="main_result",
    ) == "ExampleRepo.Main.Topic.Theorems.main_result"
