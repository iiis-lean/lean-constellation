from pathlib import Path
from unittest.mock import Mock

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.external_clients import LeanMcpToolkitClient
from lean_constellation.services.external_clients.lean_toolchain import ToolchainDeclarationView
from lean_constellation.services.mathlib import MathlibService


def _service(dispatcher) -> MathlibService:
    toolkit = LeanMcpToolkitClient(dispatcher=dispatcher)
    return make_runtime(external_overrides={"lean_mcp_toolkit": toolkit}).mathlib


def test_record_mathlib_module_checked_writes_only_after_access_check(tmp_path: Path) -> None:
    calls: list[tuple[str, dict]] = []

    def dispatch(tool_name: str, payload: dict):
        calls.append((tool_name, payload))
        assert tool_name == "lsp.run_snippet"
        assert payload["code"] == "import Mathlib.Data.Nat.Basic\n#check True\n"
        return {"diagnostics": []}

    service = _service(dispatch)

    recorded = service.record_mathlib_module_checked(
        tmp_path,
        module_name="Mathlib.Data.Nat.Basic",
        summary="Natural number basics.",
        source="confirmed by toolkit module check",
    )

    assert recorded.ok, recorded.issues
    assert recorded.value is not None
    assert recorded.value.module == "Mathlib.Data.Nat.Basic"
    assert recorded.value.summary == "Natural number basics."
    assert recorded.value.note == "confirmed by toolkit module check"
    assert calls


def test_record_mathlib_module_checked_failure_does_not_mutate_index(tmp_path: Path) -> None:
    def dispatch(tool_name: str, payload: dict):
        assert tool_name == "lsp.run_snippet"
        return {"diagnostics": [{"severity": "error", "message": "unknown module"}]}

    service = _service(dispatch)

    rejected = service.record_mathlib_module_checked(tmp_path, module_name="Mathlib.Unknown.Module", summary="Bad.")

    assert not rejected.ok
    assert rejected.issues[0].kind == "mathlib_module_access_check_failed"
    missing = service.get_mathlib_module_entry(tmp_path, module="Mathlib.Unknown.Module")
    assert not missing.ok
    assert missing.issues[0].kind == "mathlib_module_entry_missing"


def test_record_mathlib_decl_checked_uses_navigation_and_records_module_decl(tmp_path: Path) -> None:
    def dispatch(tool_name: str, payload: dict):
        if tool_name == "lean_explore.find":
            assert payload["query"] == "Nat.add_assoc"
            return {
                "results": [
                    {
                        "name": "Nat.add_assoc",
                        "module": "Init",
                        "source_text": "theorem Nat.add_assoc : True := by trivial",
                    }
                ]
            }
        if tool_name == "check_mathlib_name":
            assert payload["decl_name"] == "Nat.add_assoc"
            assert "import Init" in payload["code"]
            return {"passed": True, "diagnostics": []}
        raise KeyError(tool_name)

    service = _service(dispatch)

    recorded = service.record_mathlib_decl_checked(
        tmp_path,
        decl_name="Nat.add_assoc",
        summary="Associativity of natural number addition.",
        source="confirmed by checked record test",
    )

    assert recorded.ok, recorded.issues
    assert recorded.value is not None
    assert recorded.value.module == "Init"
    assert recorded.value.kind == "theorem"
    assert recorded.value.signature == "theorem Nat.add_assoc : True"
    assert recorded.value.summary == "Associativity of natural number addition."
    assert recorded.value.note == "confirmed by checked record test"
    module = service.get_mathlib_module_entry(tmp_path, module="Init")
    assert module.ok and module.value is not None
    assert module.value.important_decl_names == ["Nat.add_assoc"]


def test_record_mathlib_decl_checked_uses_exact_navigation_with_fallback_check_and_updates(tmp_path: Path) -> None:
    def dispatch(tool_name: str, payload: dict):
        if tool_name == "lean_explore.find":
            assert payload["query"] == "Nat.add_comm"
            return {
                "results": [
                    {
                        "name": "Nat.add_comm",
                        "module": "Init",
                        "source_text": "theorem Nat.add_comm : True := by trivial",
                    }
                ]
            }
        if tool_name == "check_mathlib_name":
            raise KeyError(tool_name)
        if tool_name == "lsp.run_snippet":
            assert "#check Nat.add_comm" in payload["code"]
            return {"diagnostics": []}
        raise KeyError(tool_name)

    service = _service(dispatch)

    first = service.record_mathlib_decl_checked(
        tmp_path,
        decl_name="Nat.add_comm",
        module_name="Init",
        summary="Commutativity of addition.",
        source="first source",
        kind="theorem",
        signature="Nat.add_comm : ...",
    )
    second = service.record_mathlib_decl_checked(
        tmp_path,
        decl_name="Nat.add_comm",
        module_name="Init",
        summary="Updated commutativity summary.",
    )

    assert first.ok, first.issues
    assert second.ok, second.issues
    assert second.issues == []
    assert second.value is not None
    assert second.value.kind == "theorem"
    assert second.value.signature == "theorem Nat.add_comm : True"
    assert second.value.snippet == "theorem Nat.add_comm : True := by trivial"
    assert second.value.summary == "Updated commutativity summary."
    assert second.value.note == "first source"


def test_record_mathlib_decl_checked_rejects_failed_check_and_module_conflict(tmp_path: Path) -> None:
    passed = False

    def dispatch(tool_name: str, payload: dict):
        if tool_name == "lean_explore.find":
            name = payload["query"]
            return {
                "results": [
                    {
                        "name": name,
                        "module": "Init",
                        "source_text": f"theorem {name} : True := by trivial",
                    }
                ]
            }
        if tool_name == "check_mathlib_name":
            return {"passed": passed, "diagnostics": [{"severity": "error", "message": "unknown declaration"}] if not passed else []}
        raise KeyError(tool_name)

    service = _service(dispatch)

    rejected = service.record_mathlib_decl_checked(
        tmp_path,
        decl_name="Nat.nope",
        module_name="Init",
        summary="Bad declaration.",
    )
    assert not rejected.ok
    assert rejected.issues[0].kind == "mathlib_decl_access_check_failed"
    assert not service.get_mathlib_decl_entry(tmp_path, name="Nat.nope").ok

    passed = True
    assert service.record_mathlib_decl_checked(tmp_path, decl_name="Nat.add_assoc", module_name="Init", summary="Assoc.").ok
    conflict = service.record_mathlib_decl_checked(
        tmp_path,
        decl_name="Nat.add_assoc",
        module_name="Mathlib.Data.Nat.Basic",
        summary="Conflicting module.",
    )
    assert not conflict.ok
    assert conflict.issues[0].kind == "mathlib_decl_module_conflict"


def test_record_mathlib_batch_checked_uses_one_lean_probe_and_records_all_entries(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, dict]] = []

    def dispatch(tool_name: str, payload: dict):
        calls.append((tool_name, payload))
        if tool_name == "lean_explore.find":
            assert payload["query"] == "Nat.add_assoc"
            return {
                "results": [
                    {
                        "name": "Nat.add_assoc",
                        "module": "Init",
                        "source_text": "theorem Nat.add_assoc : True := by trivial",
                    }
                ]
            }
        assert tool_name == "lsp.run_snippet"
        assert payload["code"] == (
            "import Mathlib.Data.Nat.Basic\n"
            "import Init\n"
            "#check Nat.add_assoc\n"
        )
        return {"diagnostics": []}

    service = _service(dispatch)
    recorded = service.record_mathlib_batch_checked(
        tmp_path,
        modules=[
            {
                "module_name": "Mathlib.Data.Nat.Basic",
                "summary": "Natural number basics.",
                "source": "batch test",
            }
        ],
        declarations=[
            {
                "decl_name": "Nat.add_assoc",
                "module_name": "Init",
                "summary": "Associativity.",
                "source": "batch test",
                "kind": "theorem",
                "signature": "Nat.add_assoc : ...",
                "snippet": "theorem Nat.add_assoc",
            }
        ],
    )

    assert recorded.ok, recorded.issues
    assert recorded.value is not None
    assert [item.module for item in recorded.value.modules] == ["Mathlib.Data.Nat.Basic"]
    assert [item.name for item in recorded.value.declarations] == ["Nat.add_assoc"]
    assert [name for name, _ in calls] == ["lean_explore.find", "lsp.run_snippet"]


def test_record_mathlib_batch_checked_repairs_stale_import_module_from_exact_navigation(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, dict]] = []

    def dispatch(tool_name: str, payload: dict):
        calls.append((tool_name, payload))
        if tool_name == "lean_explore.find":
            name = payload["query"]
            module = (
                "Mathlib.Data.Finset.Card"
                if name == "Finset.card_image_le"
                else "Init"
            )
            return {
                "results": [
                    {
                        "name": name,
                        "module": module,
                        "source_text": f"theorem {name} : True := by trivial",
                    }
                ]
            }
        if tool_name == "lsp.run_snippet":
            assert "import Mathlib.Data.Finset.Card" in payload["code"]
            assert "import Init" in payload["code"]
            assert "#check Finset.card_image_le" in payload["code"]
            assert "#check Nat.add_assoc" in payload["code"]
            return {"diagnostics": []}
        raise KeyError(tool_name)

    service = _service(dispatch)
    old_module = "Mathlib.Algebra.BigOperators.Group.Finset.Basic"
    assert service.upsert_mathlib_module_entry(tmp_path, module=old_module).ok
    assert service.add_module_important_decl(tmp_path, module=old_module, decl_name="Finset.card_image_le").ok
    assert service.upsert_mathlib_decl_entry(
        tmp_path,
        name="Finset.card_image_le",
        module=old_module,
        kind="theorem",
        signature="theorem card_image_le : True",
        snippet="theorem card_image_le",
    ).ok

    recorded = service.record_mathlib_batch_checked(
        tmp_path,
        modules=[],
        declarations=[
            {
                "decl_name": "Finset.card_image_le",
                "module_name": "Mathlib.Data.Finset.Card",
                "kind": "theorem",
                "summary": "Image-cardinality bound.",
            },
            {
                "decl_name": "Nat.add_assoc",
                "module_name": "Init",
                "kind": "theorem",
                "signature": "Nat.add_assoc : ...",
                "snippet": "theorem Nat.add_assoc",
                "summary": "Associativity.",
            },
        ],
    )

    assert recorded.ok, recorded.issues
    assert recorded.value is not None
    assert [item.name for item in recorded.value.declarations] == ["Finset.card_image_le", "Nat.add_assoc"]
    assert "mathlib_decl_module_repaired" in [issue.kind for issue in recorded.issues]
    repaired = service.get_mathlib_decl_entry(tmp_path, name="Finset.card_image_le")
    assert repaired.ok and repaired.value is not None
    assert repaired.value.module == "Mathlib.Data.Finset.Card"
    old_entry = service.get_mathlib_module_entry(tmp_path, module=old_module)
    assert old_entry.ok and old_entry.value is not None
    assert "Finset.card_image_le" not in old_entry.value.important_decl_names
    exact_entry = service.get_mathlib_module_entry(tmp_path, module="Mathlib.Data.Finset.Card")
    assert exact_entry.ok and exact_entry.value is not None
    assert "Finset.card_image_le" in exact_entry.value.important_decl_names
    assert [name for name, _ in calls] == [
        "lean_explore.find",
        "lean_explore.find",
        "lsp.run_snippet",
    ]


def test_record_mathlib_batch_missing_exact_module_writes_nothing(tmp_path: Path) -> None:
    def dispatch(tool_name: str, payload: dict):
        if tool_name == "lean_explore.find":
            name = payload["query"]
            return {
                "results": [
                    {
                        "name": name,
                        "module": "Init" if name == "Nat.add_assoc" else None,
                        "source_text": f"theorem {name} : True := by trivial",
                    }
                ]
            }
        raise KeyError(tool_name)

    service = _service(dispatch)
    recorded = service.record_mathlib_batch_checked(
        tmp_path,
        modules=[{"module_name": "Mathlib.Data.Nat.Basic"}],
        declarations=[
            {"decl_name": "Nat.add_assoc"},
            {"decl_name": "Nat.module_unknown"},
        ],
    )

    assert not recorded.ok
    assert recorded.issues[0].kind == "mathlib_decl_module_missing"
    assert not service.get_mathlib_module_entry(
        tmp_path,
        module="Mathlib.Data.Nat.Basic",
    ).ok
    assert not service.get_mathlib_decl_entry(tmp_path, name="Nat.add_assoc").ok


def test_missing_search_entry_uses_exact_local_module_and_checks_before_record(tmp_path: Path) -> None:
    calls = []
    name = "Real.binEntropy_strictMonoOn"
    module = "Mathlib.Analysis.SpecialFunctions.BinaryEntropy"

    def dispatch(tool_name, payload):
        calls.append(tool_name)
        if tool_name == "lean_explore.find":
            return {"results": []}
        if tool_name == "mathlib_nav.file_outline":
            assert payload["target"] == module
            return {"declarations": [{"full_name": name, "decl_kind": "lemma", "line_start": 422,
                                      "header_preview": "lemma binEntropy_strictMonoOn : StrictMonoOn binEntropy (Icc 0 2⁻¹) := by"}]}
        if tool_name == "lsp.run_snippet":
            assert f"import {module}" in payload["code"]
            assert f"#check {name}" in payload["code"]
            return {"diagnostics": []}
        raise KeyError(tool_name)

    service = _service(dispatch)
    recorded = service.record_mathlib_decl_checked(tmp_path, decl_name=name, module_name=module)
    assert recorded.ok, recorded.issues
    assert recorded.value.module == module
    assert recorded.value.kind == "lemma"
    assert any(i.kind == "mathlib_decl_local_navigation" for i in recorded.issues)
    assert "lsp.run_snippet" in calls
    # Subsequent name-only navigation can use the recorded module, still verified locally.
    navigation = service.inspect_mathlib_declaration(tmp_path, decl_name=name)
    assert navigation.ok
    assert "Not LeanExplore index metadata" in navigation.value.context
    assert service.toolkit_ingestion.resolve_mathlib_decl_entry(tmp_path, decl_name=name, module_name=module).ok


def test_local_curation_does_not_accept_short_name_match(tmp_path: Path) -> None:
    def dispatch(tool_name, payload):
        if tool_name == "lean_explore.find":
            return {"results": []}
        if tool_name == "mathlib_nav.file_outline":
            return {"declarations": [{"full_name": "Other.target", "header_preview": "lemma target : True := by"}]}
        raise AssertionError("Must not check or record a different declaration")

    service = _service(dispatch)
    result = service.record_mathlib_decl_checked(tmp_path, decl_name="Real.target", module_name="Mathlib.Test")
    assert not result.ok
    assert not service.get_mathlib_decl_entry(tmp_path, name="Real.target").ok


def test_local_curation_failed_lean_check_does_not_write(tmp_path: Path) -> None:
    def dispatch(tool_name, payload):
        if tool_name == "lean_explore.find":
            return {"results": []}
        if tool_name == "mathlib_nav.file_outline":
            return {"declarations": [{"full_name": "Real.target", "header_preview": "lemma target : True := by"}]}
        if tool_name == "lsp.run_snippet":
            return {"diagnostics": [{"severity": "error", "message": "unknown constant"}]}
        raise KeyError(tool_name)

    service = _service(dispatch)
    result = service.record_mathlib_decl_checked(tmp_path, decl_name="Real.target", module_name="Mathlib.Test")
    assert not result.ok
    assert result.issues[0].kind == "mathlib_decl_access_check_failed"
    assert not service.get_mathlib_decl_entry(tmp_path, name="Real.target").ok



def test_local_navigation_does_not_mask_tool_failure(tmp_path: Path) -> None:
    from lean_constellation.services.external_clients.lean_mcp_toolkit import ToolkitTimeoutError

    def dispatch(tool_name, payload):
        if tool_name == "lean_explore.find":
            raise ToolkitTimeoutError("offline")
        raise AssertionError("Transport failure must not trigger local fallback")

    service = _service(dispatch)
    result = service.record_mathlib_decl_checked(tmp_path, decl_name="Real.target", module_name="Mathlib.Test")
    assert not result.ok
    assert not service.get_mathlib_decl_entry(tmp_path, name="Real.target").ok


def test_core_declaration_navigation_requires_compiler_identity_and_persists_provenance(
    tmp_path: Path,
) -> None:
    name = "Int.toNat_of_nonneg"
    module = "Mathlib.Data.Int.Init"

    def dispatch(tool_name: str, payload: dict):
        assert tool_name == "lsp.run_snippet"
        assert f"#check {name}" in payload["code"]
        return {"diagnostics": []}

    service = _service(dispatch)
    toolchain = service.runtime.external.lean_toolchain
    toolchain.inspect_mathlib_declaration = lambda _root, _name: ToolchainDeclarationView(
        ok=False,
        provider="lean_mcp_toolkit",
        name=name,
        summary="not indexed",
        issue_code="declaration_not_found",
    )
    toolchain.inspect_local_mathlib_declaration = lambda *_args, **_kwargs: ToolchainDeclarationView(
        ok=False,
        provider="lake_command",
        name=name,
        module=module,
        summary="compiler identified a Lean core defining module",
        issue_code="declaration_not_found",
    )
    toolchain.inspect_core_declaration = lambda _root, *, module, decl_name: ToolchainDeclarationView(
        ok=True,
        provider="lake_command",
        name=decl_name,
        module=module,
        kind="theorem",
        signature="Int.toNat_of_nonneg {a : Int} (h : 0 <= a) : ...",
        code="Lean core defining module Init.Data.Int.Order (/toolchain/src/lean/Init/Data/Int/Order.lean)",
        summary="Compiler verified exact declaration; Mathlib module is import context.",
    )

    recorded = service.record_mathlib_decl_checked(tmp_path, decl_name=name, module_name=module)

    assert recorded.ok, recorded.issues
    assert recorded.value is not None
    assert recorded.value.module == module
    assert recorded.value.note is not None
    assert "Lean core" in recorded.value.note


def test_local_mathlib_compiler_navigation_persists_exact_source_provenance(
    tmp_path: Path,
) -> None:
    name = "MvPolynomial.coeff_mul"
    module = "Mathlib.Algebra.MvPolynomial.Basic"

    def dispatch(tool_name: str, payload: dict):
        if tool_name == "lean_explore.find":
            return {"results": []}
        if tool_name == "mathlib_nav.file_outline":
            return {"declarations": []}
        if tool_name == "lsp.run_snippet":
            assert f"#check {name}" in payload["code"]
            return {"diagnostics": []}
        raise AssertionError(f"unexpected toolkit call: {tool_name}")

    service = _service(dispatch)
    toolchain = service.runtime.external.lean_toolchain
    toolchain.inspect_local_mathlib_declaration = (
        lambda _root, *, module, decl_name: ToolchainDeclarationView(
            ok=True,
            provider="lake_command",
            name=decl_name,
            module=module,
            kind="theorem",
            signature="MvPolynomial.coeff_mul.{u, v} : ...",
            code="theorem MvPolynomial.coeff_mul.{u, v} : ...",
            summary="Compiler verified exact local Mathlib declaration.",
            raw_excerpt=(
                "Mathlib defining module Mathlib.Algebra.MvPolynomial.Basic "
                "(/repo/.lake/packages/mathlib/Mathlib/Algebra/MvPolynomial/Basic.lean)"
            ),
        )
    )
    toolchain.inspect_core_declaration = Mock(
        side_effect=AssertionError("core fallback must not run after exact local Mathlib verification")
    )

    recorded = service.record_mathlib_decl_checked(
        tmp_path,
        decl_name=name,
        module_name=module,
    )

    assert recorded.ok, recorded.issues
    assert recorded.value is not None
    assert recorded.value.module == module
    assert recorded.value.note is not None
    assert "Mathlib defining module" in recorded.value.note
    toolchain.inspect_core_declaration.assert_not_called()


def test_local_mathlib_compiler_navigation_normalizes_import_context_to_defining_module(
    tmp_path: Path,
) -> None:
    name = "Module.Finite.of_fg_top"
    import_module = "Mathlib.RingTheory.Finiteness.Basic"
    defining_module = "Mathlib.RingTheory.Finiteness.Defs"

    def dispatch(tool_name: str, payload: dict):
        if tool_name == "lean_explore.find":
            return {"results": []}
        if tool_name == "mathlib_nav.file_outline":
            return {"declarations": []}
        if tool_name == "lsp.run_snippet":
            assert f"#check {name}" in payload["code"]
            return {"diagnostics": []}
        raise AssertionError(f"unexpected toolkit call: {tool_name}")

    service = _service(dispatch)
    toolchain = service.runtime.external.lean_toolchain
    toolchain.inspect_local_mathlib_declaration = (
        lambda _root, *, module, decl_name: ToolchainDeclarationView(
            ok=True,
            provider="lake_command",
            name=decl_name,
            module=defining_module,
            kind="constructor",
            signature=f"{name}.{{u_1, u_4}} : ...",
            code=f"constructor {name}.{{u_1, u_4}} : ...",
            summary=f"Compiler verified {module} as import context.",
            raw_excerpt=f"Mathlib defining module {defining_module}",
        )
    )
    toolchain.inspect_core_declaration = Mock(
        side_effect=AssertionError("core fallback must not run after exact local Mathlib verification")
    )

    recorded = service.record_mathlib_decl_checked(
        tmp_path,
        decl_name=name,
        module_name=import_module,
    )

    assert recorded.ok, recorded.issues
    assert recorded.value is not None
    assert recorded.value.module == defining_module
    assert any(issue.kind == "mathlib_decl_requested_module_normalized" for issue in recorded.issues)
    toolchain.inspect_core_declaration.assert_not_called()


def test_core_navigation_does_not_accept_unverified_repository_name(tmp_path: Path) -> None:
    name = "Foo.target"
    module = "Mathlib.Test"

    def dispatch(tool_name: str, payload: dict):
        if tool_name == "lean_explore.find":
            return {"results": []}
        if tool_name == "mathlib_nav.file_outline":
            return {"declarations": []}
        raise AssertionError(f"unexpected toolkit call: {tool_name}")

    service = _service(dispatch)
    toolchain = service.runtime.external.lean_toolchain
    toolchain.inspect_core_declaration = lambda *_args, **_kwargs: ToolchainDeclarationView(
        ok=False,
        provider="lake_command",
        name=name,
        module=module,
        summary="no core provenance",
        issue_code="declaration_not_found",
    )
    toolchain.inspect_local_mathlib_declaration = lambda *_args, **_kwargs: ToolchainDeclarationView(
        ok=False,
        provider="lake_command",
        name=name,
        module=module,
        summary="no local Mathlib provenance",
        issue_code="declaration_not_found",
    )

    result = service.record_mathlib_decl_checked(tmp_path, decl_name=name, module_name=module)

    assert not result.ok
    assert result.issues[0].kind == "declaration_not_found"
    assert not service.get_mathlib_decl_entry(tmp_path, name=name).ok


def test_core_compiler_access_failure_does_not_write_index(tmp_path: Path) -> None:
    name = "Int.toNat_of_nonneg"
    module = "Mathlib.Data.Int.Init"

    def dispatch(tool_name: str, payload: dict):
        if tool_name == "lean_explore.find":
            return {"results": []}
        if tool_name == "mathlib_nav.file_outline":
            return {"declarations": []}
        raise AssertionError(f"unexpected toolkit call: {tool_name}")

    service = _service(dispatch)
    toolchain = service.runtime.external.lean_toolchain
    toolchain.inspect_mathlib_declaration = lambda _root, _name: ToolchainDeclarationView(
        ok=False,
        provider="lean_mcp_toolkit",
        name=name,
        summary="not indexed",
        issue_code="declaration_not_found",
    )
    toolchain.inspect_local_mathlib_declaration = lambda *_args, **_kwargs: ToolchainDeclarationView(
        ok=False,
        provider="lake_command",
        name=name,
        module=module,
        summary="compiler identified a Lean core defining module",
        issue_code="declaration_not_found",
    )
    toolchain.inspect_core_declaration = lambda *_args, **_kwargs: ToolchainDeclarationView(
        ok=False,
        provider="lake_command",
        name=name,
        module=module,
        summary="requested import context cannot access declaration",
        issue_code="core_decl_access_check_failed",
    )

    result = service.record_mathlib_decl_checked(tmp_path, decl_name=name, module_name=module)

    assert not result.ok
    assert result.issues[0].kind == "core_decl_access_check_failed"
    assert not service.get_mathlib_decl_entry(tmp_path, name=name).ok
