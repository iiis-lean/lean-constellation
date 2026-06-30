from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.external_clients import LeanMcpToolkitClient
from lean_constellation.services.mathlib import MathlibCandidateCache, MathlibService


def _service(dispatcher) -> MathlibService:
    toolkit = LeanMcpToolkitClient(dispatcher=dispatcher)
    return make_runtime(external_overrides={"lean_mcp_toolkit": toolkit}).mathlib


def test_search_external_mathlib_caches_candidates_without_index_write(tmp_path: Path) -> None:
    def dispatch(tool_name: str, payload: dict):
        assert tool_name == "lean_explore.find"
        assert payload["query"] == "Finset sum"
        assert payload["limit"] == 1
        return {
            "results": [
                {
                    "name": "Finset.sum_congr",
                    "module": "Mathlib.Data.Finset.Basic",
                    "kind": "theorem",
                    "signature": "Finset.sum_congr : ...",
                    "docstring": "Congruence for Finset sums.",
                },
                {
                    "name": "Finset.sum_empty",
                    "module": "Mathlib.Data.Finset.Basic",
                    "kind": "theorem",
                },
            ]
        }

    service = _service(dispatch)

    search = service.search_external_mathlib(tmp_path, query="Finset sum", search_kinds=["theorem", "def"], limit=1)
    assert search.ok
    assert search.value is not None
    assert len(search.value.candidates) == 1
    assert search.value.candidates[0].name == "Finset.sum_congr"
    assert search.value.candidates[0].source_kind == "lean_explore.find"

    cached_path = tmp_path / ".lean_constellation" / "indexes" / "mathlib_candidates.json"
    assert cached_path.exists()
    assert not service.get_mathlib_decl_entry(tmp_path, name="Finset.sum_congr").ok


def test_search_external_mathlib_reuses_stable_candidate_id_for_same_query_and_item(tmp_path: Path) -> None:
    def dispatch(tool_name: str, payload: dict):
        if tool_name == "lean_explore.find":
            return {"results": [{"name": "Nat.add_assoc", "module": "Init", "kind": "theorem"}]}
        raise KeyError(tool_name)

    service = _service(dispatch)

    first = service.search_external_mathlib(tmp_path, query="Nat add", search_kinds=["theorem"], limit=5)
    second = service.search_external_mathlib(tmp_path, query="Nat add", search_kinds=["theorem"], limit=5)

    assert first.ok and first.value is not None
    assert second.ok and second.value is not None
    assert first.value.candidates[0].candidate_id == second.value.candidates[0].candidate_id
    assert second.value.candidates[0].source_kind == "lean_explore.find"
    cache_path = tmp_path / ".lean_constellation" / "indexes" / "mathlib_candidates.json"
    cached = service.runtime.foundation.read_json(cache_path, MathlibCandidateCache)
    assert cached.ok and cached.value is not None
    assert list(cached.value.candidates) == [first.value.candidates[0].candidate_id]


def test_inspect_mathlib_declaration_and_module(tmp_path: Path) -> None:
    def dispatch(tool_name: str, payload: dict):
        if tool_name == "inspect_mathlib_decl":
            return {
                "code": "import Mathlib.Data.Finset.Basic\nnamespace Finset\n@[simp]\ntheorem Finset.sum_congr : True := by trivial",
                "module": "Mathlib.Data.Finset.Basic",
            }
        if tool_name == "inspect_mathlib_module":
            return {
                "imports": "Mathlib.Data.Finset.Defs",
                "results": [
                    {"name": "Finset.sum_congr", "kind": "theorem", "signature": "Finset.sum_congr : ..."},
                    {"name": "Finset.sum_empty", "kind": "theorem"},
                ],
            }
        raise KeyError(tool_name)

    service = _service(dispatch)

    decl = service.inspect_mathlib_declaration(tmp_path, decl_name="Finset.sum_congr")
    assert decl.ok
    assert decl.value is not None
    assert decl.value.module == "Mathlib.Data.Finset.Basic"
    assert decl.value.kind == "theorem"
    assert decl.value.signature == "theorem Finset.sum_congr : True"

    module = service.inspect_mathlib_module(tmp_path, module="Mathlib.Data.Finset.Basic")
    assert module.ok
    assert module.value is not None
    assert module.value.imports == ["Mathlib.Data.Finset.Defs"]
    assert module.value.important_decl_hints == ["Finset.sum_congr", "Finset.sum_empty"]


def test_check_mathlib_name_direct_and_fallback(tmp_path: Path) -> None:
    direct = _service(lambda tool_name, payload: {"passed": False, "diagnostics": [{"severity": "error", "message": "unknown"}]})

    failed_check = direct.check_mathlib_name(tmp_path, module="Mathlib.Data.Nat.Basic", decl_name="Nat.nope")
    assert failed_check.ok
    assert failed_check.value is not None
    assert failed_check.value.passed is False
    assert failed_check.value.toolkit_tool == "check_mathlib_name"
    assert failed_check.value.diagnostics == ["error: unknown"]

    def fallback_dispatch(tool_name: str, payload: dict):
        if tool_name == "check_mathlib_name":
            raise KeyError(tool_name)
        if tool_name == "lsp.run_snippet":
            assert "#check Nat.add_assoc" in payload["code"]
            return {"diagnostics": []}
        raise KeyError(tool_name)

    fallback = _service(fallback_dispatch)
    passed_check = fallback.check_mathlib_name(tmp_path, module="Init", decl_name="Nat.add_assoc")
    assert passed_check.ok
    assert passed_check.value is not None
    assert passed_check.value.passed is True
    assert passed_check.value.toolkit_tool == "lsp.run_snippet"

    unavailable = _service(lambda tool_name, payload: (_ for _ in ()).throw(KeyError(tool_name)))
    unavailable_check = unavailable.check_mathlib_name(tmp_path, module="Init", decl_name="Nat.add_assoc")
    assert not unavailable_check.ok
    assert unavailable_check.issues[0].kind == "mathlib_check_unavailable"


def test_ingest_mathlib_candidate_success_and_check_failure(tmp_path: Path) -> None:
    check_should_pass = False

    def dispatch(tool_name: str, payload: dict):
        nonlocal check_should_pass
        if tool_name == "search_mathlib":
            return {
                "items": [
                    {
                        "name": "Finset.sum_congr",
                        "module": "Mathlib.Data.Finset.Basic",
                        "kind": "theorem",
                        "signature": "Finset.sum_congr : ...",
                        "snippet": "theorem Finset.sum_congr ...",
                    }
                ]
            }
        if tool_name == "check_mathlib_name":
            if check_should_pass:
                return {"passed": True, "diagnostics": []}
            return {"passed": False, "diagnostics": [{"severity": "error", "message": "bad import"}]}
        raise KeyError(tool_name)

    service = _service(dispatch)
    search = service.search_external_mathlib(tmp_path, query="Finset sum", search_kinds=["theorem"])
    assert search.ok
    assert search.value is not None
    candidate_id = search.value.candidates[0].candidate_id

    unknown = service.ingest_mathlib_candidate(tmp_path, candidate_id="mc_missing", summary="Missing.")
    assert not unknown.ok
    assert unknown.issues[0].kind == "mathlib_candidate_unknown"

    rejected = service.ingest_mathlib_candidate(tmp_path, candidate_id=candidate_id, summary="Congruence for sums.")
    assert not rejected.ok
    assert rejected.issues[0].kind == "mathlib_candidate_check_failed"
    assert not service.get_mathlib_decl_entry(tmp_path, name="Finset.sum_congr").ok

    check_should_pass = True
    ingested = service.ingest_mathlib_candidate(
        tmp_path,
        candidate_id=candidate_id,
        summary="Congruence for Finset sums.",
        note="Selected from external Mathlib search.",
    )
    assert ingested.ok
    assert ingested.value is not None
    assert ingested.value.name == "Finset.sum_congr"
    assert ingested.value.module == "Mathlib.Data.Finset.Basic"
    assert ingested.value.summary == "Congruence for Finset sums."

    module = service.get_mathlib_module_entry(tmp_path, module="Mathlib.Data.Finset.Basic")
    assert module.ok
    assert module.value is not None
    assert module.value.important_decl_names == ["Finset.sum_congr"]


def test_ingest_candidate_requires_module_or_navigation_recovery(tmp_path: Path) -> None:
    mode = "navigation_fail"

    def dispatch(tool_name: str, payload: dict):
        nonlocal mode
        if tool_name == "search_mathlib":
            return {"items": [{"name": "Nat.add_assoc", "kind": "theorem", "signature": "Nat.add_assoc : ..."}]}
        if tool_name == "inspect_mathlib_decl":
            if mode == "navigation_success":
                return {"code": "theorem Nat.add_assoc : True := by trivial", "module": "Init"}
            raise KeyError(tool_name)
        if tool_name == "check_mathlib_name":
            return {"passed": True, "diagnostics": []}
        raise KeyError(tool_name)

    service = _service(dispatch)
    search = service.search_external_mathlib(tmp_path, query="Nat add", search_kinds=["theorem"])
    assert search.ok and search.value is not None
    candidate_id = search.value.candidates[0].candidate_id

    missing_module = service.ingest_mathlib_candidate(tmp_path, candidate_id=candidate_id, summary="Associativity.")
    assert not missing_module.ok
    assert missing_module.issues[0].kind == "mathlib_candidate_module_missing"

    mode = "navigation_success"
    recovered = service.ingest_mathlib_candidate(tmp_path, candidate_id=candidate_id, summary="Associativity.")
    assert recovered.ok
    assert recovered.value is not None
    assert recovered.value.module == "Init"
