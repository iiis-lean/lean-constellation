from pathlib import Path

from lean_constellation.domain.mathlib import MathlibIndex
from lean_constellation.services.mathlib import MathlibService


def test_upsert_module_create_update_and_persist(tmp_path: Path) -> None:
    service = MathlibService()

    created = service.upsert_mathlib_module_entry(
        tmp_path,
        module="Mathlib.Data.Finset.Basic",
        summary="Basic Finset API.",
        note="Useful for finite sums.",
    )
    assert created.ok
    assert created.value is not None
    assert created.value.module == "Mathlib.Data.Finset.Basic"
    assert created.value.summary == "Basic Finset API."

    updated = service.upsert_mathlib_module_entry(
        tmp_path,
        module="Mathlib.Data.Finset.Basic",
        note="Updated note.",
    )
    assert updated.ok
    assert updated.value is not None
    assert updated.value.summary == "Basic Finset API."
    assert updated.value.note == "Updated note."

    path = tmp_path / ".lean_constellation" / "indexes" / "mathlib.json"
    stored = service.foundation.read_json(path, MathlibIndex)
    assert stored.ok
    assert stored.value is not None
    assert set(stored.value.modules) == {"Mathlib.Data.Finset.Basic"}


def test_upsert_module_empty_strings_clear_optional_fields(tmp_path: Path) -> None:
    service = MathlibService()
    assert service.upsert_mathlib_module_entry(
        tmp_path,
        module="Mathlib.Data.Finset.Basic",
        summary="Basic Finset API.",
        note="Useful for finite sums.",
    ).ok

    cleared = service.upsert_mathlib_module_entry(
        tmp_path,
        module="Mathlib.Data.Finset.Basic",
        summary=" ",
        note="",
    )

    assert cleared.ok
    assert cleared.value is not None
    assert cleared.value.summary is None
    assert cleared.value.note is None


def test_add_important_decl_autocreates_module_with_warning_and_dedupes(tmp_path: Path) -> None:
    service = MathlibService()

    added = service.add_module_important_decl(
        tmp_path,
        module="Mathlib.Topology.Basic",
        decl_name="TopologicalSpace",
    )
    assert added.ok
    assert added.value is not None
    assert added.issues[0].kind == "mathlib_module_entry_auto_created"
    assert added.value.important_decl_names == ["TopologicalSpace"]

    duplicate = service.add_module_important_decl(
        tmp_path,
        module="Mathlib.Topology.Basic",
        decl_name="TopologicalSpace",
    )
    assert duplicate.ok
    assert duplicate.value is not None
    assert [issue.kind for issue in duplicate.issues] == ["mathlib_module_important_decl_duplicate"]
    assert duplicate.value.important_decl_names == ["TopologicalSpace"]


def test_add_important_decl_rejects_invalid_module_and_decl_names(tmp_path: Path) -> None:
    service = MathlibService()

    bad_module = service.add_module_important_decl(
        tmp_path,
        module="Mathlib/Topology/Basic",
        decl_name="TopologicalSpace",
    )
    assert not bad_module.ok
    assert bad_module.issues[0].kind == "mathlib_module_name_invalid"

    bad_decl = service.add_module_important_decl(
        tmp_path,
        module="Mathlib.Topology.Basic",
        decl_name="Topological Space",
    )
    assert not bad_decl.ok
    assert bad_decl.issues[0].kind == "mathlib_decl_name_invalid"


def test_upsert_decl_missing_module_warning_update_and_snippet_truncation(tmp_path: Path) -> None:
    service = MathlibService()

    long_snippet = "x" * 2100
    created = service.upsert_mathlib_decl_entry(
        tmp_path,
        name="Finset.sum_congr",
        module="Mathlib.Data.Finset.Basic",
        kind="theorem",
        signature="Finset.sum_congr : ...",
        summary="Congruence for Finset sums.",
        note="Useful for rewriting sums.",
        snippet=long_snippet,
    )
    assert created.ok
    assert created.value is not None
    assert [issue.kind for issue in created.issues] == [
        "mathlib_decl_module_not_indexed",
        "mathlib_decl_snippet_truncated",
    ]
    assert created.value.module == "Mathlib.Data.Finset.Basic"
    assert created.value.kind == "theorem"
    assert created.value.snippet is not None
    assert len(created.value.snippet) == 2000

    updated = service.upsert_mathlib_decl_entry(
        tmp_path,
        name="Finset.sum_congr",
        module=None,
        kind=None,
        signature=None,
        summary="Updated summary.",
        note=None,
    )
    assert updated.ok
    assert updated.value is not None
    assert updated.value.module == "Mathlib.Data.Finset.Basic"
    assert updated.value.kind == "theorem"
    assert updated.value.signature == "Finset.sum_congr : ..."
    assert updated.value.summary == "Updated summary."


def test_upsert_decl_accepts_free_kind_and_empty_strings_clear_optional_fields(tmp_path: Path) -> None:
    service = MathlibService()
    assert service.upsert_mathlib_module_entry(tmp_path, module="Mathlib.Data.Finset.Basic").ok
    created = service.upsert_mathlib_decl_entry(
        tmp_path,
        name="Finset.sum_congr",
        module="Mathlib.Data.Finset.Basic",
        kind="custom theorem-like cache label",
        signature="Finset.sum_congr : ...",
        summary="Congruence for Finset sums.",
        note="Useful for rewriting sums.",
        snippet="theorem Finset.sum_congr ...",
    )
    assert created.ok
    assert created.value is not None
    assert created.value.kind == "custom theorem-like cache label"

    cleared = service.upsert_mathlib_decl_entry(
        tmp_path,
        name="Finset.sum_congr",
        module=None,
        kind=" ",
        signature="",
        summary=" ",
        note="",
        snippet=" ",
    )

    assert cleared.ok
    assert cleared.value is not None
    assert cleared.value.module == "Mathlib.Data.Finset.Basic"
    assert cleared.value.kind is None
    assert cleared.value.signature is None
    assert cleared.value.summary is None
    assert cleared.value.note is None
    assert cleared.value.snippet is None


def test_search_literal_regex_kind_filter_and_limit(tmp_path: Path) -> None:
    service = MathlibService()
    service.upsert_mathlib_module_entry(
        tmp_path,
        module="Mathlib.Data.Finset.Basic",
        summary="Finite sets and finite sums.",
        note="Contains Finset.sum tools.",
    )
    service.add_module_important_decl(
        tmp_path,
        module="Mathlib.Data.Finset.Basic",
        decl_name="Finset.sum_congr",
    )
    service.upsert_mathlib_module_entry(
        tmp_path,
        module="Mathlib.Topology.Basic",
        summary="Topology basics.",
    )
    service.upsert_mathlib_decl_entry(
        tmp_path,
        name="Finset.sum_congr",
        module="Mathlib.Data.Finset.Basic",
        kind="theorem",
        signature="Finset.sum_congr : ...",
        summary="Congruence for Finset sums.",
        note=None,
        snippet="theorem Finset.sum_congr ...",
    )
    service.upsert_mathlib_decl_entry(
        tmp_path,
        name="Finset.sum_empty",
        module="Mathlib.Data.Finset.Basic",
        kind="theorem",
        signature="Finset.sum_empty : ...",
        summary="Empty Finset sum.",
        note=None,
    )

    literal = service.search_mathlib_index(tmp_path, query="Finset", limit=10)
    assert literal.ok
    assert literal.value is not None
    assert {hit.key for hit in literal.value.hits} >= {"Mathlib.Data.Finset.Basic", "Finset.sum_congr"}

    regex = service.search_mathlib_index(tmp_path, query=r"sum_.*", regex=True, entry_kind="declaration", limit=10)
    assert regex.ok
    assert regex.value is not None
    assert {hit.key for hit in regex.value.hits} == {"Finset.sum_congr", "Finset.sum_empty"}
    assert all(hit.entry_kind == "declaration" for hit in regex.value.hits)

    modules = service.search_mathlib_index(tmp_path, query="topology", entry_kind="module", limit=10)
    assert modules.ok
    assert modules.value is not None
    assert [hit.key for hit in modules.value.hits] == ["Mathlib.Topology.Basic"]

    limited = service.search_mathlib_index(tmp_path, query="Finset", limit=1)
    assert limited.ok
    assert limited.value is not None
    assert len(limited.value.hits) == 1
    assert limited.value.truncated


def test_search_rejects_empty_query_and_invalid_limit_and_accepts_kind_aliases(tmp_path: Path) -> None:
    service = MathlibService()
    assert service.upsert_mathlib_module_entry(tmp_path, module="Mathlib.Data.Finset.Basic").ok
    assert service.upsert_mathlib_decl_entry(
        tmp_path,
        name="Finset.sum_congr",
        module="Mathlib.Data.Finset.Basic",
        kind="theorem",
        signature="Finset.sum_congr : ...",
        summary="Congruence for Finset sums.",
        note=None,
    ).ok

    empty_query = service.search_mathlib_index(tmp_path, query=" ")
    assert not empty_query.ok
    assert empty_query.issues[0].kind == "mathlib_index_query_empty"

    invalid_limit = service.search_mathlib_index(tmp_path, query="Finset", limit=0)
    assert not invalid_limit.ok
    assert invalid_limit.issues[0].kind == "mathlib_index_limit_invalid"

    decl_alias = service.search_mathlib_index(tmp_path, query="sum_congr", entry_kind="decls")
    assert decl_alias.ok
    assert decl_alias.value is not None
    assert decl_alias.value.entry_kind == "declaration"
    assert [hit.key for hit in decl_alias.value.hits] == ["Finset.sum_congr"]

    module_alias = service.search_mathlib_index(tmp_path, query="Finset", entry_kind="modules")
    assert module_alias.ok
    assert module_alias.value is not None
    assert module_alias.value.entry_kind == "module"


def test_get_missing_invalid_regex_and_invalid_inputs(tmp_path: Path) -> None:
    service = MathlibService()

    missing_module = service.get_mathlib_module_entry(tmp_path, module="Mathlib.Missing")
    assert not missing_module.ok
    assert missing_module.issues[0].kind == "mathlib_module_entry_missing"

    missing_decl = service.get_mathlib_decl_entry(tmp_path, name="Missing.decl")
    assert not missing_decl.ok
    assert missing_decl.issues[0].kind == "mathlib_decl_entry_missing"

    bad_regex = service.search_mathlib_index(tmp_path, query="[", regex=True)
    assert not bad_regex.ok
    assert bad_regex.issues[0].kind == "mathlib_index_regex_invalid"

    bad_kind = service.search_mathlib_index(tmp_path, query="Finset", entry_kind="bad")
    assert not bad_kind.ok
    assert bad_kind.issues[0].kind == "mathlib_index_entry_kind_invalid"

    bad_module = service.upsert_mathlib_module_entry(tmp_path, module="Mathlib/Bad")
    assert not bad_module.ok
    assert bad_module.issues[0].kind == "mathlib_module_name_invalid"


def test_get_rejects_invalid_module_and_decl_names(tmp_path: Path) -> None:
    service = MathlibService()

    empty_module = service.get_mathlib_module_entry(tmp_path, module=" ")
    assert not empty_module.ok
    assert empty_module.issues[0].kind == "mathlib_module_name_empty"

    bad_module = service.get_mathlib_module_entry(tmp_path, module="Mathlib Bad")
    assert not bad_module.ok
    assert bad_module.issues[0].kind == "mathlib_module_name_invalid"

    empty_decl = service.get_mathlib_decl_entry(tmp_path, name="")
    assert not empty_decl.ok
    assert empty_decl.issues[0].kind == "mathlib_decl_name_empty"

    bad_decl = service.get_mathlib_decl_entry(tmp_path, name="Bad/Decl")
    assert not bad_decl.ok
    assert bad_decl.issues[0].kind == "mathlib_decl_name_invalid"

    bad_upsert = service.upsert_mathlib_decl_entry(
        tmp_path,
        name="Bad Decl",
        module=None,
        kind=None,
        signature=None,
        summary=None,
        note=None,
    )
    assert not bad_upsert.ok
    assert bad_upsert.issues[0].kind == "mathlib_decl_name_invalid"

    bad_module_upsert = service.upsert_mathlib_decl_entry(
        tmp_path,
        name="Good.Decl",
        module="Mathlib/Bad",
        kind=None,
        signature=None,
        summary=None,
        note=None,
    )
    assert not bad_module_upsert.ok
    assert bad_module_upsert.issues[0].kind == "mathlib_module_name_invalid"
