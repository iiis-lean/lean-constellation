from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.external_clients import LeanMcpToolkitClient
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
