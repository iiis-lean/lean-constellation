from __future__ import annotations

from pathlib import Path

from lean_constellation.services.decl_graph.models import (
    DeclDependencyMutationReceipt,
)
from lean_constellation.services.mathlib.mathlib_index import MathlibDeclEntryView
from lean_constellation.services.mathlib.service import MathlibDependencyRequest
from tests.unit_services_helpers import initialize_native_test_repo, make_runtime


def _receipt(*, changed: bool = True) -> DeclDependencyMutationReceipt:
    return DeclDependencyMutationReceipt(
        target="current node / example / Statement dependencies",
        operation="add",
        changed=changed,
    )


def _resolved_entry() -> MathlibDeclEntryView:
    return MathlibDeclEntryView(
        name="Finset.card_union_of_disjoint",
        module="Mathlib.Data.Finset.Card",
        kind="theorem",
        signature="Finset.card_union_of_disjoint",
        snippet="#check Finset.card_union_of_disjoint",
    )


def test_mathlib_dependency_transaction_verifies_and_creates_canonical_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = make_runtime()
    initialize_native_test_repo(tmp_path)
    calls: list[str] = []

    def resolve(_repo_root, *, decl_name: str, module_name: str | None = None):
        calls.append(f"{decl_name}:{module_name}")
        return runtime.foundation.ok(_resolved_entry())

    monkeypatch.setattr(
        runtime.mathlib.toolkit_ingestion,
        "resolve_mathlib_decl_entry",
        resolve,
    )
    captured = []
    result = runtime.mathlib.add_decl_dependencies_transaction(
        tmp_path,
        requests=[
            MathlibDependencyRequest(
                name="Finset.card_union_of_disjoint",
                module=None,
                reason="Compute the cardinality of a disjoint union.",
            )
        ],
        dependency_stage="proof",
        add_dependencies=lambda deps: (
            captured.extend(deps) or runtime.foundation.ok(_receipt())
        ),
    )

    assert result.ok, result.issues
    assert result.value is not None
    assert calls == ["Finset.card_union_of_disjoint:None"]
    assert captured[0].ref.module == "Mathlib.Data.Finset.Card"
    assert result.value.mathlib_index == {
        "changed": True,
        "created_declarations": ["Finset.card_union_of_disjoint"],
        "reused_declarations": [],
        "updated_declarations": [],
        "created_modules": ["Mathlib.Data.Finset.Card"],
    }
    stored = runtime.mathlib.get_mathlib_decl_entry(
        tmp_path,
        name="Finset.card_union_of_disjoint",
    )
    assert stored.ok and stored.value == _resolved_entry()


def test_mathlib_dependency_transaction_reuses_complete_index_without_toolkit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = make_runtime()
    initialize_native_test_repo(tmp_path)
    seeded = runtime.mathlib.upsert_mathlib_decl_entry(
        tmp_path,
        **_resolved_entry().model_dump(),
    )
    assert seeded.ok, seeded.issues
    module = runtime.mathlib.upsert_mathlib_module_entry(
        tmp_path,
        module="Mathlib.Data.Finset.Card",
    )
    assert module.ok, module.issues
    linked = runtime.mathlib.add_module_important_decl(
        tmp_path,
        module="Mathlib.Data.Finset.Card",
        decl_name="Finset.card_union_of_disjoint",
    )
    assert linked.ok, linked.issues

    def unexpected(*_args, **_kwargs):
        raise AssertionError("complete canonical index entry must avoid Toolkit")

    monkeypatch.setattr(
        runtime.mathlib.toolkit_ingestion,
        "resolve_mathlib_decl_entry",
        unexpected,
    )
    result = runtime.mathlib.add_decl_dependencies_transaction(
        tmp_path,
        requests=[
            MathlibDependencyRequest(
                name="Finset.card_union_of_disjoint",
                module="Mathlib.Data.Finset.Card",
                reason=None,
            )
        ],
        dependency_stage="statement",
        add_dependencies=lambda _deps: runtime.foundation.ok(_receipt(changed=False)),
    )

    assert result.ok, result.issues
    assert result.value is not None
    assert result.value.changed is False
    assert result.value.mathlib_index is not None
    assert result.value.mathlib_index["reused_declarations"] == [
        "Finset.card_union_of_disjoint"
    ]


def test_mathlib_dependency_transaction_restores_index_bytes_when_decl_write_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = make_runtime()
    initialize_native_test_repo(tmp_path)
    index_path = runtime.mathlib.mathlib_index.index_path(tmp_path)
    seeded = runtime.mathlib.upsert_mathlib_module_entry(
        tmp_path,
        module="Mathlib.Init",
        summary="Unrelated stable entry.",
    )
    assert seeded.ok, seeded.issues
    before = index_path.read_bytes()
    monkeypatch.setattr(
        runtime.mathlib.toolkit_ingestion,
        "resolve_mathlib_decl_entry",
        lambda *_args, **_kwargs: runtime.foundation.ok(_resolved_entry()),
    )

    result = runtime.mathlib.add_decl_dependencies_transaction(
        tmp_path,
        requests=[
            MathlibDependencyRequest(
                name="Finset.card_union_of_disjoint",
                module=None,
                reason=None,
            )
        ],
        dependency_stage="proof",
        add_dependencies=lambda _deps: runtime.foundation.fail(
            runtime.foundation.issue(
                "injected_decl_projection_failure",
                "injected failure",
            )
        ),
    )

    assert not result.ok
    assert result.issues[0].kind == "injected_decl_projection_failure"
    assert index_path.read_bytes() == before
    missing = runtime.mathlib.get_mathlib_decl_entry(
        tmp_path,
        name="Finset.card_union_of_disjoint",
    )
    assert not missing.ok


def test_mathlib_dependency_transaction_rejects_conflicting_duplicate_requests(
    tmp_path: Path,
) -> None:
    runtime = make_runtime()
    initialize_native_test_repo(tmp_path)

    result = runtime.mathlib.add_decl_dependencies_transaction(
        tmp_path,
        requests=[
            MathlibDependencyRequest(
                name="Finset.card_union_of_disjoint",
                module="Mathlib.Data.Finset.Card",
                reason="first",
            ),
            MathlibDependencyRequest(
                name="Finset.card_union_of_disjoint",
                module="Mathlib.Data.Finset.Basic",
                reason="second",
            ),
        ],
        dependency_stage="proof",
        add_dependencies=lambda _deps: runtime.foundation.ok(_receipt()),
    )

    assert not result.ok
    assert result.issues[0].kind == "mathlib_dependency_duplicate_conflict"
