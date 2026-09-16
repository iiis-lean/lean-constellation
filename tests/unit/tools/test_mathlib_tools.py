from __future__ import annotations

from lean_constellation.tools import build_application_tool_specs
from tests.unit.tools._family_helpers import assert_group_contains, assert_tools_registered


def test_mathlib_tools_are_registered() -> None:
    expected = {
        "search_mathlib_index",
        "get_mathlib_module_entry",
        "get_mathlib_decl_entry",
        "record_mathlib_module",
        "record_mathlib_decl",
        "add_mathlib_module_important_decl",
        "search_external_mathlib",
        "search_mathlib_declarations",
        "inspect_mathlib_search_candidate",
        "inspect_mathlib_declaration",
        "inspect_mathlib_module",
        "check_mathlib_name",
        "ingest_mathlib_candidate",
        "search_arxiv_theorems",
        "get_current_node_mathlib_hints",
        "add_current_mathlib_hints",
        "remove_current_mathlib_module_hint",
        "remove_current_mathlib_decl_hint",
        "validate_current_node_mathlib_hints",
        "add_node_mathlib_module_hint",
        "remove_node_mathlib_module_hint",
        "add_node_mathlib_decl_hint",
        "remove_node_mathlib_decl_hint",
    }

    assert_tools_registered(expected)


def test_mathlib_groups_expose_expected_tools() -> None:
    assert_group_contains("mathlib_index_read", {"search_mathlib_index", "get_mathlib_module_entry", "get_mathlib_decl_entry"})
    assert_group_contains("mathlib_index_write", {"record_mathlib_module", "record_mathlib_decl", "ingest_mathlib_candidate"})
    assert_group_contains("mathlib_semantic_search", {"search_external_mathlib", "search_mathlib_declarations"})
    assert_group_contains("mathlib_navigation", {"inspect_mathlib_declaration", "inspect_mathlib_module", "check_mathlib_name"})
    assert_group_contains("external_theorem_search_read", {"search_arxiv_theorems"})
    assert_group_contains("node_mathlib_hint_read", {"get_current_node_mathlib_hints", "validate_current_node_mathlib_hints"})
    assert_group_contains("node_mathlib_hint_write", {"add_current_mathlib_hints"})
    assert_group_contains(
        "node_contract_mathlib_write_by_node",
        {"add_node_mathlib_module_hint", "add_node_mathlib_decl_hint"},
    )


def test_mathlib_mutation_result_views_match_compact_receipts() -> None:
    specs = {spec.name: spec for spec in build_application_tool_specs()}

    for name in {
        "record_mathlib_module",
        "record_mathlib_decl",
        "add_mathlib_module_important_decl",
    }:
        assert specs[name].result_view == "mathlib_entry_mutation_receipt"
    for name in {
        "remove_current_mathlib_module_hint",
        "remove_current_mathlib_decl_hint",
        "add_node_mathlib_module_hint",
        "remove_node_mathlib_module_hint",
        "add_node_mathlib_decl_hint",
        "remove_node_mathlib_decl_hint",
    }:
        assert specs[name].result_view == "node_mathlib_hint_mutation"


def test_agent_curation_tools_forward_module_hint_through_real_service(tmp_path):
    from lean_constellation.services import create_test_runtime_services
    from lean_constellation.services.external_clients import LeanMcpToolkitClient
    from tests.unit.tools.test_application_tool_invocation_smoke import _raw, _unwrap_tool_result

    module = "Mathlib.Analysis.SpecialFunctions.BinaryEntropy"
    name = "Real.binEntropy_strictMonoOn"
    calls = []

    def dispatch(tool_name, payload):
        calls.append(tool_name)
        if tool_name == "lean_explore.find":
            return {"results": []}
        if tool_name == "mathlib_nav.file_outline":
            assert payload["target"] == module
            return {"declarations": [{"full_name": name, "header_preview": "lemma binEntropy_strictMonoOn : StrictMonoOn binEntropy (Icc 0 2⁻¹) := by"}]}
        if tool_name == "lsp.run_snippet":
            assert f"import {module}" in payload["code"]
            assert f"#check {name}" in payload["code"]
            return {"diagnostics": []}
        raise KeyError(tool_name)

    for tool_name in ("record_mathlib_decl", "record_mathlib_batch"):
        root = tmp_path / tool_name
        root.mkdir()
        runtime = create_test_runtime_services(
            register_application_tools=True,
            external_overrides={"lean_mcp_toolkit": LeanMcpToolkitClient(dispatcher=dispatch)},
        )
        args = {"decl_name": name, "module_name": module}
        if tool_name == "record_mathlib_batch":
            args = {"declarations": [args]}
        _unwrap_tool_result(runtime.tool_facade.invoke_agent_tool(
            _raw(root, view="mathlib_recon", agent_type="mathlib_recon", node_path="Main.Topic"),
            tool_name=tool_name, flat_args=args,
        ))
        entry = runtime.mathlib.get_mathlib_decl_entry(root, name=name)
        assert entry.ok and entry.value.module == module
    assert calls.count("lsp.run_snippet") == 2


def test_module_hint_is_optional_in_single_and_batch_agent_schemas():
    from lean_constellation.tools.args import MathlibBatchRecordArgs, MathlibDeclRecordArgs
    schema = MathlibDeclRecordArgs.model_json_schema()
    assert "module_name" in schema["properties"]
    assert "module_name" not in schema.get("required", [])
    batch = MathlibBatchRecordArgs.model_json_schema()
    assert "module_name" in batch["$defs"]["MathlibDeclRecordArgs"]["properties"]
    assert MathlibDeclRecordArgs(decl_name="Nat.add_comm").module_name is None
