from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.external_clients import (
    LeanMcpToolkitClient,
    LeanMcpToolkitClientConfig,
)
from lean_constellation.services.foundation import FoundationContext
from lean_constellation.services.mathlib import MathlibCandidateCache, MathlibService


def _create_content_node(repo_root: Path, service: MathlibService) -> None:
    tree = service.runtime.node.node_tree
    assert tree.ensure_root_scope_node(repo_root).ok
    assert tree.create_scope_node(
        repo_root,
        path="Main.Analysis",
        goal="Organize analysis declarations.",
        boundary="Analysis scope used by Mathlib real-service tests.",
    ).ok
    created = tree.create_content_node(
        repo_root,
        path="Main.Analysis.Core",
        goal="Formalize core analysis declarations.",
        boundary="Core analysis declarations only.",
        objective="Prepare Mathlib dependencies for this content node.",
        success_criteria="The content node records usable Mathlib imports and declaration hints.",
    )
    assert created.ok


def _fake_toolkit_service() -> MathlibService:
    def dispatch(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "lean_explore.find":
            query = str(payload.get("query") or "")
            include_source = bool(payload.get("include_source_text"))
            if query == "Nat.add_assoc" and include_source:
                return {
                    "results": [
                        {
                            "name": "Nat.add_assoc",
                            "module": "Mathlib.Data.Nat.Basic",
                            "kind": "theorem",
                            "type": "Nat.add_assoc : (n m k : Nat) -> n + m + k = n + (m + k)",
                            "docstring": "Associativity of natural number addition.",
                            "source_text": "theorem Nat.add_assoc (n m k : Nat) : n + m + k = n + (m + k) := by\n  omega",
                        }
                    ]
                }
            if "Nat.add" in query:
                return {
                    "results": [
                        {
                            "name": "Nat.add_assoc",
                            "module": "Mathlib.Data.Nat.Basic",
                            "kind": "theorem",
                            "type": "Nat.add_assoc : (n m k : Nat) -> n + m + k = n + (m + k)",
                            "docstring": "Associativity of natural number addition.",
                            "snippet": "theorem Nat.add_assoc ...",
                        },
                        {
                            "name": "Nat.add_comm",
                            "module": "Mathlib.Data.Nat.Basic",
                            "kind": "theorem",
                            "type": "Nat.add_comm : (n m : Nat) -> n + m = m + n",
                            "docstring": "Commutativity of natural number addition.",
                        },
                    ]
                }
            return {"results": []}
        if tool_name == "mathlib_nav.file_outline":
            assert payload["target"] == "Mathlib.Data.Nat.Basic"
            return {
                "imports": ["Mathlib.Init"],
                "declarations": [
                    {
                        "name": "Nat.add_assoc",
                        "kind": "theorem",
                        "signature": "Nat.add_assoc : (n m k : Nat) -> n + m + k = n + (m + k)",
                    },
                    {
                        "name": "Nat.add_comm",
                        "kind": "theorem",
                        "signature": "Nat.add_comm : (n m : Nat) -> n + m = m + n",
                    },
                ],
            }
        if tool_name == "check_mathlib_name":
            assert payload["module"] == "Mathlib.Data.Nat.Basic"
            assert payload["decl_name"] in {"Nat.add_assoc", "Nat.add_comm"}
            assert "import Mathlib.Data.Nat.Basic" in payload["code"]
            return {"passed": True, "diagnostics": []}
        raise KeyError(tool_name)

    toolkit = LeanMcpToolkitClient(dispatcher=dispatch)
    return make_runtime(external_overrides={"lean_mcp_toolkit": toolkit}).mathlib


@pytest.mark.real
def test_mathlib_index_toolkit_use_real_service_lifecycle(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    service = _fake_toolkit_service()
    _create_content_node(repo_root, service)

    search = service.search_external_mathlib(
        repo_root,
        query="Nat.add",
        search_kinds=["theorem"],
        limit=5,
    )
    assert search.ok
    assert search.value is not None
    assert [candidate.name for candidate in search.value.candidates] == ["Nat.add_assoc", "Nat.add_comm"]
    assert all(candidate.source_kind == "lean_explore.find" for candidate in search.value.candidates)

    cache_path = repo_root / ".lean_constellation" / "indexes" / "mathlib_candidates.json"
    cached = service.runtime.foundation.read_json(cache_path, MathlibCandidateCache)
    assert cached.ok
    assert cached.value is not None
    assert set(cached.value.candidates) == {candidate.candidate_id for candidate in search.value.candidates}

    module_nav = service.inspect_mathlib_module(repo_root, module="Mathlib.Data.Nat.Basic")
    assert module_nav.ok
    assert module_nav.value is not None
    assert module_nav.value.imports == ["Mathlib.Init"]
    assert {candidate.name for candidate in module_nav.value.declarations} == {"Nat.add_assoc", "Nat.add_comm"}

    decl_nav = service.inspect_mathlib_declaration(repo_root, decl_name="Nat.add_assoc")
    assert decl_nav.ok
    assert decl_nav.value is not None
    assert decl_nav.value.module == "Mathlib.Data.Nat.Basic"
    assert decl_nav.value.kind == "theorem"
    assert decl_nav.value.signature is not None
    assert "Nat.add_assoc" in decl_nav.value.signature

    ingested = service.ingest_mathlib_candidate(
        repo_root,
        candidate_id=search.value.candidates[0].candidate_id,
        summary="Associativity theorem for natural number addition.",
        note="Selected by the Mathlib real-service test.",
    )
    assert ingested.ok
    assert ingested.value is not None
    assert ingested.value.name == "Nat.add_assoc"
    assert ingested.value.module == "Mathlib.Data.Nat.Basic"

    module_entry = service.get_mathlib_module_entry(repo_root, module="Mathlib.Data.Nat.Basic")
    decl_entry = service.get_mathlib_decl_entry(repo_root, name="Nat.add_assoc")
    assert module_entry.ok
    assert module_entry.value is not None
    assert module_entry.value.important_decl_names == ["Nat.add_assoc"]
    assert decl_entry.ok
    assert decl_entry.value is not None
    assert decl_entry.value.summary == "Associativity theorem for natural number addition."

    local_search = service.search_mathlib_index(repo_root, query="Associativity", entry_kind="declaration")
    assert local_search.ok
    assert local_search.value is not None
    assert [hit.key for hit in local_search.value.hits] == ["Nat.add_assoc"]

    module_use = service.add_mathlib_module_use(
        repo_root,
        node_path="Main.Analysis.Core",
        module="Mathlib.Data.Nat.Basic",
        reason="Required for natural number addition lemmas.",
        actor="coordinator",
    )
    assert module_use.ok
    assert module_use.value is not None
    assert [item.model_dump(mode="json") for item in module_use.value.added_modules] == [
        {
            "module": "Mathlib.Data.Nat.Basic",
            "reason": "Required for natural number addition lemmas.",
            "added_by": "coordinator",
        }
    ]
    prelude_path = service.runtime.foundation.prelude_path(
        FoundationContext(repo_root=repo_root),
        "Main.Analysis.Core",
    )
    assert "import Mathlib.Data.Nat.Basic" in prelude_path.read_text(encoding="utf-8")

    decl_use = service.add_mathlib_decl_use(
        repo_root,
        node_path="Main.Analysis.Core",
        decl_name="Nat.add_assoc",
        reason="Candidate theorem for planned proof.",
        actor="worker",
    )
    assert decl_use.ok
    assert decl_use.value is not None
    assert [item.model_dump(mode="json") for item in decl_use.value.added_declarations] == [
        {
            "name": "Nat.add_assoc",
            "module": "Mathlib.Data.Nat.Basic",
            "kind": "theorem",
            "reason": "Candidate theorem for planned proof.",
            "added_by": "worker",
        }
    ]

    duplicate_decl_use = service.add_mathlib_decl_use(
        repo_root,
        node_path="Main.Analysis.Core",
        decl_name="Nat.add_assoc",
        reason="Duplicate candidate.",
        actor="worker",
    )
    assert duplicate_decl_use.ok
    assert [issue.kind for issue in duplicate_decl_use.issues] == ["mathlib_decl_use_duplicate"]

    validation = service.validate_node_mathlib_uses(repo_root, node_path="Main.Analysis.Core")
    assert validation.ok
    assert validation.value is not None
    assert validation.value.passed is True
    assert validation.value.issues == []


@pytest.mark.real
@pytest.mark.real_toolkit
def test_mathlib_index_toolkit_live_search_can_feed_index(tmp_path: Path) -> None:
    base_url = os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL")
    if not base_url:
        pytest.skip("Set LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL to run live Mathlib toolkit tests.")
    service = make_runtime(
        external_overrides={
            "lean_mcp_toolkit": LeanMcpToolkitClient.from_config(
                LeanMcpToolkitClientConfig(
                    base_url=base_url,
                    api_prefix=os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_API_PREFIX", "/api/v1"),
                    auth_token=os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_AUTH_TOKEN"),
                    timeout_seconds=int(os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_TIMEOUT", "120")),
                )
            )
        }
    )
    service = service.mathlib
    query = os.environ.get("LEAN_CONSTELLATION_REAL_MATHLIB_QUERY", "Nat.add")

    search = service.search_external_mathlib(tmp_path, query=query, search_kinds=["theorem"], limit=3)
    if not search.ok and search.issues:
        first_issue = search.issues[0]
        if first_issue.kind == "toolkit_call_failed" and "status=500" in first_issue.message:
            pytest.skip(f"Live toolkit lean_explore backend is unavailable: {first_issue.message}")
    assert search.ok, search.issues
    assert search.value is not None
    if not search.value.candidates:
        pytest.skip(f"Live toolkit returned no Mathlib candidates for query {query!r}.")

    candidate = next((item for item in search.value.candidates if item.name and item.module), search.value.candidates[0])
    assert candidate.name
    if candidate.module:
        manual_module = service.upsert_mathlib_module_entry(
            tmp_path,
            module=candidate.module,
            summary=f"Live toolkit module for {candidate.name}.",
        )
        assert manual_module.ok, manual_module.issues
    manual_decl = service.upsert_mathlib_decl_entry(
        tmp_path,
        name=candidate.name,
        module=candidate.module,
        kind=candidate.kind,
        signature=candidate.signature,
        summary=candidate.summary or f"Live toolkit candidate for {candidate.name}.",
        note="Written by live Mathlib toolkit real test.",
        snippet=candidate.snippet,
    )
    assert manual_decl.ok, manual_decl.issues

    local = service.search_mathlib_index(tmp_path, query=candidate.name, entry_kind="declaration")
    assert local.ok
    assert local.value is not None
    assert any(hit.key == candidate.name for hit in local.value.hits)
