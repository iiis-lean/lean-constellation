from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from lean_constellation.services import create_test_runtime_services
from lean_constellation.services.tool_facade import RawToolCallContext, RuntimeToolContext
from tests.unit.tools._family_helpers import assert_group_contains, assert_tools_registered


def test_resource_tools_are_registered() -> None:
    expected = {
        "get_material_context",
        "normalize_resource_target",
        "find_duplicate_resource",
        "acquire_resource_material",
        "extract_resource_artifact",
        "import_resource_material",
        "normalize_resource_text_material",
        "read_resource_range",
        "list_resources",
        "get_resource",
        "allocate_resource_draft",
        "get_resource_draft",
        "check_resource_draft",
        "abandon_resource_draft",
    }

    assert_tools_registered(expected)


def test_resource_groups_expose_expected_tools() -> None:
    assert_group_contains(
        "resource_curation_context_read",
        {"get_material_context", "normalize_resource_target", "find_duplicate_resource"},
    )
    assert_group_contains("material_context_read", {"get_material_context"})
    assert_group_contains(
        "resource_target_preflight_read",
        {"normalize_resource_target", "find_duplicate_resource"},
    )
    assert_group_contains(
        "resource_acquisition",
        {"acquire_resource_material", "extract_resource_artifact", "import_resource_material", "normalize_resource_text_material"},
    )
    assert_group_contains("resource_library_read", {"read_resource_range", "list_resources", "get_resource"})
    assert_group_contains("resource_draft_write", {"allocate_resource_draft", "get_resource_draft", "check_resource_draft", "abandon_resource_draft"})


def _resource_raw(repo_root: Path, *, flow_id: str = "flow_resource") -> RawToolCallContext:
    return RawToolCallContext(
        endpoint_view_key="resource_curator",
        runtime_context=RuntimeToolContext(
            flow_id=flow_id,
            step_id="step_resource",
            agent_id="agent_resource",
            agent_type="ResourceCuratorAgent",
            agent_role="worker",
            expected_view_key="resource_curator",
            repo_root=repo_root,
        ),
    )


class _FakeResourceFlowService:
    def __init__(self, *, flow_id: str, draft_id: str | None) -> None:
        self.flow_id = flow_id
        self.flow = SimpleNamespace(
            flow_type="resource_curation",
            state=SimpleNamespace(active_resource_draft_key=draft_id),
        )

    def get_flow(self, flow_id: str):
        assert flow_id == self.flow_id
        return self.flow


def test_resource_acquisition_requires_active_resource_draft(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    runtime.ark.flow_service = _FakeResourceFlowService(flow_id="flow_resource", draft_id=None)
    source = tmp_path / "source.txt"
    source.write_text("resource text\n", encoding="utf-8")

    result = runtime.tool_facade.invoke_agent_tool(
        _resource_raw(tmp_path),
        tool_name="import_resource_material",
        flat_args={"source_path": str(source)},
    )

    assert result.ok and result.value is not None
    assert result.value.ok is False
    assert result.value.issues[0].kind == "resource_active_draft_missing"


def test_resource_acquisition_writes_active_draft_not_source_corpus(tmp_path: Path) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    target = runtime.material.normalize_resource_target("https://example.com/resource")
    assert target.ok and target.value is not None
    draft = runtime.material.allocate_resource_draft(tmp_path, target=target.value, title_hint="Resource")
    assert draft.ok and draft.value is not None
    runtime.ark.flow_service = _FakeResourceFlowService(flow_id="flow_resource", draft_id=draft.value.draft.draft_id)
    source = tmp_path / "source.txt"
    source.write_text("resource text\n", encoding="utf-8")

    imported = runtime.tool_facade.invoke_agent_tool(
        _resource_raw(tmp_path),
        tool_name="import_resource_material",
        flat_args={"source_path": str(source), "as_name": "raw.txt"},
    )
    normalized = runtime.tool_facade.invoke_agent_tool(
        _resource_raw(tmp_path),
        tool_name="normalize_resource_text_material",
        flat_args={"material_ref": "original/raw.txt"},
    )

    assert imported.ok and imported.value is not None and imported.value.ok is True
    assert normalized.ok and normalized.value is not None and normalized.value.ok is True
    draft_root = Path(draft.value.draft_root)
    assert (draft_root / "original" / "raw.txt").is_file()
    assert any((draft_root / "normalized").iterdir())
    assert not (tmp_path / ".lean_constellation" / "source" / "original" / "raw.txt").exists()
