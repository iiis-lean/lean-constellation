from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.external_clients import LeanMcpToolkitClient
from lean_constellation.services.mathlib import MathlibCandidateCache, MathlibService


def _service(dispatcher) -> MathlibService:
    toolkit = LeanMcpToolkitClient(dispatcher=dispatcher)
    return make_runtime(external_overrides={"lean_mcp_toolkit": toolkit}).mathlib


def test_search_mathlib_declarations_returns_semantic_view_and_cached_candidate(tmp_path: Path) -> None:
    def dispatch(tool_name: str, payload: dict):
        assert tool_name == "lean_explore.find"
        assert payload["query"] == "Nat add"
        assert payload["limit"] == 1
        return {
            "results": [
                {
                    "name": "Nat.add_assoc",
                    "module": "Init",
                    "kind": "theorem",
                    "signature": "Nat.add_assoc : ...",
                    "docstring": "Associativity of natural number addition.",
                },
                {"name": "Nat.add_comm", "module": "Init", "kind": "theorem"},
            ]
        }

    service = _service(dispatch)

    search = service.search_mathlib_declarations(tmp_path, query="Nat add", limit=1)

    assert search.ok, search.issues
    assert search.value is not None
    assert search.value.query == "Nat add"
    assert [candidate.name for candidate in search.value.candidates] == ["Nat.add_assoc"]
    assert search.value.returned_count == 1
    assert "source_kind" not in search.value.candidates[0].model_dump()
    assert "search_query" not in search.value.candidates[0].model_dump()
    assert "raw_excerpt" not in search.value.candidates[0].model_dump()
    cached = service.runtime.foundation.read_json(
        tmp_path / ".lean_constellation" / "indexes" / "mathlib_candidates.json",
        MathlibCandidateCache,
    )
    assert cached.ok and cached.value is not None
    assert list(cached.value.candidates) == [search.value.candidates[0].candidate_id]
    assert not service.get_mathlib_decl_entry(tmp_path, name="Nat.add_assoc").ok


def test_inspect_mathlib_search_candidate_enriches_from_navigation_without_index_write(tmp_path: Path) -> None:
    def dispatch(tool_name: str, payload: dict):
        if tool_name == "lean_explore.find" and payload["query"] == "Nat add":
            return {"results": [{"name": "Nat.add_assoc", "kind": "theorem"}]}
        if tool_name == "lean_explore.find" and payload["query"] == "Nat.add_assoc":
            return {
                "results": [
                    {
                        "name": "Nat.add_assoc",
                        "module": "Init",
                        "source_text": "theorem Nat.add_assoc : True := by trivial",
                    }
                ]
            }
        raise KeyError(tool_name)

    service = _service(dispatch)
    search = service.search_mathlib_declarations(tmp_path, query="Nat add", limit=5)
    assert search.ok and search.value is not None
    candidate_id = search.value.candidates[0].candidate_id

    inspected = service.inspect_mathlib_search_candidate(
        tmp_path,
        candidate_id=candidate_id,
        include_source_excerpt=True,
    )

    assert inspected.ok, inspected.issues
    assert inspected.value is not None
    assert inspected.value.name == "Nat.add_assoc"
    assert inspected.value.module == "Init"
    assert inspected.value.signature == "theorem Nat.add_assoc : True"
    assert inspected.value.navigation_verified
    assert inspected.value.source_excerpt == "theorem Nat.add_assoc : True := by trivial"
    assert not service.get_mathlib_decl_entry(tmp_path, name="Nat.add_assoc").ok


def test_candidate_detail_omits_source_excerpt_by_default(tmp_path: Path) -> None:
    def dispatch(tool_name: str, payload: dict):
        if tool_name == "lean_explore.find" and payload["query"] == "Nat add":
            return {"results": [{"name": "Nat.add_assoc", "kind": "theorem"}]}
        if tool_name == "lean_explore.find" and payload["query"] == "Nat.add_assoc":
            return {
                "results": [
                    {
                        "name": "Nat.add_assoc",
                        "module": "Init",
                        "source_text": "theorem Nat.add_assoc : True := by trivial",
                    }
                ]
            }
        raise KeyError(tool_name)

    service = _service(dispatch)
    search = service.search_mathlib_declarations(tmp_path, query="Nat add", limit=5)
    assert search.ok and search.value is not None

    inspected = service.inspect_mathlib_search_candidate(
        tmp_path,
        candidate_id=search.value.candidates[0].candidate_id,
    )

    assert inspected.ok and inspected.value is not None
    assert inspected.value.navigation_verified
    assert inspected.value.source_excerpt is None
    assert inspected.value.source_excerpt_truncated is None


def test_inspect_mathlib_module_supports_module_name_alias_and_pattern_filter(tmp_path: Path) -> None:
    def dispatch(tool_name: str, payload: dict):
        assert tool_name == "mathlib_nav.file_outline"
        assert payload["target"] == "Mathlib.Data.Finset.Basic"
        return {
            "imports": ["Mathlib.Data.Finset.Defs"],
            "declarations": [
                {"name": "Finset.sum_congr", "kind": "theorem", "signature": "Finset.sum_congr : ..."},
                {"name": "Finset.card_empty", "kind": "theorem", "signature": "Finset.card_empty : ..."},
            ],
        }

    service = _service(dispatch)

    module = service.inspect_mathlib_module(
        tmp_path,
        module_name="Mathlib.Data.Finset.Basic",
        pattern="sum",
        include_imports=True,
    )

    assert module.ok, module.issues
    assert module.value is not None
    assert module.value.module == "Mathlib.Data.Finset.Basic"
    assert module.value.pattern == "sum"
    assert module.value.imports == ["Mathlib.Data.Finset.Defs"]
    assert [candidate.name for candidate in module.value.declarations] == ["Finset.sum_congr"]
    assert module.value.returned_count == 1
    assert module.value.matched_count == 1


def test_inspect_mathlib_module_defaults_to_bounded_compact_navigation(
    tmp_path: Path,
) -> None:
    declarations = [
        {
            "name": f"Demo.decl_{index}",
            "kind": "theorem",
            "signature": f"Demo.decl_{index} : True",
        }
        for index in range(25)
    ]

    def dispatch(tool_name: str, payload: dict):
        assert tool_name == "mathlib_nav.file_outline"
        return {
            "imports": [f"Mathlib.Import{index}" for index in range(60)],
            "declarations": declarations,
            "source_text": "x" * 2500,
        }

    service = _service(dispatch)

    default = service.inspect_mathlib_module(
        tmp_path,
        module_name="Mathlib.Demo",
    )
    expanded = service.inspect_mathlib_module(
        tmp_path,
        module_name="Mathlib.Demo",
        limit=3,
        include_imports=True,
        include_source_excerpt=True,
    )

    assert default.ok and default.value is not None
    assert default.value.returned_count == 20
    assert default.value.matched_count == 25
    assert default.value.truncated
    assert default.value.import_count == 60
    assert default.value.imports is None
    assert default.value.source_excerpt is None
    assert expanded.ok and expanded.value is not None
    assert expanded.value.returned_count == 3
    assert len(expanded.value.imports or []) == 50
    assert expanded.value.imports_truncated
    assert 1500 <= len(expanded.value.source_excerpt or "") <= 1520
    assert expanded.value.source_excerpt_truncated


def test_check_mathlib_accessible_for_declaration_and_module(tmp_path: Path) -> None:
    def dispatch(tool_name: str, payload: dict):
        if tool_name == "check_mathlib_name":
            assert payload["decl_name"] == "Nat.add_assoc"
            assert "import Init" in payload["code"]
            return {"passed": True, "diagnostics": []}
        if tool_name == "lsp.run_snippet":
            assert payload["code"] == "import Mathlib.Data.Nat.Basic\n#check True\n"
            return {"diagnostics": []}
        raise KeyError(tool_name)

    service = _service(dispatch)

    decl = service.check_mathlib_accessible(tmp_path, name_or_module="Nat.add_assoc", module="Init")
    module = service.check_mathlib_accessible(tmp_path, name_or_module="Mathlib.Data.Nat.Basic", target_kind="module")

    assert decl.ok and decl.value is not None
    assert decl.value.target_kind == "declaration"
    assert decl.value.target == "Nat.add_assoc"
    assert decl.value.passed is True
    assert module.ok and module.value is not None
    assert module.value.target_kind == "module"
    assert module.value.module == "Mathlib.Data.Nat.Basic"
    assert module.value.passed is True


def test_toolkit_views_reject_invalid_inputs(tmp_path: Path) -> None:
    service = _service(lambda tool_name, payload: (_ for _ in ()).throw(KeyError(tool_name)))

    empty_search = service.search_mathlib_declarations(tmp_path, query=" ")
    empty_candidate = service.inspect_mathlib_search_candidate(tmp_path, candidate_id=" ")
    invalid_kind = service.check_mathlib_accessible(tmp_path, name_or_module="Nat.add_assoc", target_kind="bad")

    assert not empty_search.ok
    assert empty_search.issues[0].kind == "mathlib_external_query_empty"
    assert not empty_candidate.ok
    assert empty_candidate.issues[0].kind == "mathlib_candidate_id_empty"
    assert not invalid_kind.ok
    assert invalid_kind.issues[0].kind == "mathlib_access_target_kind_invalid"
