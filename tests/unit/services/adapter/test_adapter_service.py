from tests.unit_services_helpers import make_runtime

import inspect
from pathlib import Path
from typing import Any

import pytest

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import RepoFormat, RepoPublicationStatus
from lean_constellation.services.adapter import AdapterService, UpstreamMetadataComponent
from lean_constellation.services.external_clients import LeanCheckSummaryView, LeanMcpToolkitClient
from lean_constellation.services.foundation import FoundationContext, FoundationService


def test_adapter_service_public_wrappers_have_explicit_signatures() -> None:
    wrapper_names = [
        "write_adapter_upstream_metadata",
        "search_upstream_declarations",
        "search_upstream_modules",
        "list_upstream_module_declarations",
        "inspect_upstream_declaration",
        "read_upstream_source_context",
        "capture_upstream_declaration_code",
        "inspect_upstream_module_imports",
        "create_adapter_decl",
        "set_adapter_statement_formal",
        "set_adapter_statement_nl",
        "add_adapter_statement_origin",
        "add_adapter_statement_dep",
        "remove_adapter_statement_dep",
        "set_adapter_proof_formal",
        "set_adapter_proof_nl",
        "add_adapter_proof_origin",
        "add_adapter_proof_dep",
        "remove_adapter_proof_dep",
        "list_adapter_decls",
        "inspect_adapter_decl",
        "check_adapter_decl_completeness",
        "find_adapter_decl_by_upstream",
        "finalize_adapter_decl",
        "bind_adapter_interface",
        "unbind_adapter_interface",
        "check_adapter_catalog_ready_preflight",
    ]

    for name in wrapper_names:
        signature = inspect.signature(getattr(AdapterService, name))
        assert all(
            parameter.kind is not inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        ), name

    dep_signature = inspect.signature(AdapterService.add_adapter_statement_dep)
    assert set(dep_signature.parameters) == {"self", "repo_root", "name", "dep_name", "reason"}
    origin_signature = inspect.signature(AdapterService.add_adapter_statement_origin)
    assert set(origin_signature.parameters) == {"self", "repo_root", "name", "origin_text", "source_hint"}


class FakeToolkitDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, payload))
        if tool_name == "health":
            return {"ok": True}
        if tool_name == "repo_nav.local_decl.find":
            return {
                "results": [
                    {
                        "module_path": "Upstream.Basic",
                        "full_name": "Upstream.Basic.main_result",
                        "short_name": "main_result",
                        "decl_kind": "theorem",
                        "header_preview": "theorem main_result : True",
                        "match_reason": "name contains query",
                    }
                ]
            }
        if tool_name == "repo_nav.tree":
            return {"entries": [{"module_path": "Upstream.Basic", "relative_path": "Upstream/Basic.lean", "kind": "module"}]}
        if tool_name == "repo_nav.file_outline":
            return {
                "success": True,
                "imports": ["Mathlib"],
                "declarations": [
                    {
                        "name": "Upstream.Basic.main_result",
                        "short_name": "main_result",
                        "full_name": "Upstream.Basic.main_result",
                        "decl_kind": "theorem",
                        "signature": "True",
                        "header_preview": "theorem main_result : True",
                        "line_start": 3,
                        "line_end": 4,
                    }
                ],
                "scope_cmds": [{"kind": "namespace", "target": "Upstream"}],
            }
        if tool_name == "repo_nav.read":
            return {"text": "1 import Mathlib\n2\n3 theorem main_result : True := by\n4   trivial"}
        if tool_name == "declarations.extract":
            target = payload["target"]
            return {
                "success": True,
                "declarations": [
                    {
                        "name": "main_result",
                        "full_declaration": "theorem main_result : True := by\n  trivial",
                        "decl_start_pos": {"line": 3, "column": 1},
                        "decl_end_pos": {"line": 4, "column": 10},
                        "module": target,
                    }
                ],
            }
        # Legacy aliases remain supported only to keep this fake useful for
        # external client fallback tests.
        if tool_name == "extract_declaration":
            return {"module": payload["module_or_file"], "code": "theorem main_result : True := by\n  trivial"}
        if tool_name == "inspect_mathlib_module":
            return {
                "imports": ["Mathlib"],
                "declarations": [
                    {"name": "Upstream.Basic.main_result", "kind": "theorem", "statement": "True"}
                ],
            }
        raise KeyError(tool_name)


class FakeSemanticLake:
    def __init__(self, *, snippet_ok: bool = True) -> None:
        self.snippet_ok = snippet_ok
        self.snippet_calls: list[tuple[list[str], str]] = []

    def run_snippet_check(
        self,
        *,
        repo_root: Path,
        imports: list[str],
        code: str,
        timeout_seconds: int | None = None,
    ) -> LeanCheckSummaryView:
        del repo_root, timeout_seconds
        self.snippet_calls.append((imports, code))
        return LeanCheckSummaryView(
            ok=self.snippet_ok,
            command=["lake", "env", "lean"],
            summary="confirmed" if self.snippet_ok else "registered declaration not found in owning module",
        )


def _foundation() -> FoundationService:
    return make_runtime().foundation


def _write_adapter_preparation(
    tmp_path: Path,
    interfaces: list[DeclInterface] | None = None,
    *,
    foundation: FoundationService | None = None,
) -> None:
    foundation = foundation or _foundation()
    prep = RepoPreparationInput(
        goal="Expose an upstream theorem through an adapter repo.",
        source_corpus_mode=SourceCorpusMode.NONE,
        source_corpus_relpath=None,
        interface_inputs=interfaces or [],
    )
    path = foundation.layout.preparation_input_path(FoundationContext(repo_root=tmp_path))
    result = foundation.store.write_json_atomic(path, prep)
    assert result.ok


def _service(
    tmp_path: Path,
    interfaces: list[DeclInterface] | None = None,
    *,
    dispatcher: FakeToolkitDispatcher | None = None,
    semantic_lake: FakeSemanticLake | None = None,
    create_upstream_checkout: bool = True,
) -> AdapterService:
    dispatcher = dispatcher or FakeToolkitDispatcher()
    runtime = make_runtime(
        external_overrides={
            "lean_mcp_toolkit": LeanMcpToolkitClient(dispatcher=dispatcher),
            "lake": semantic_lake or FakeSemanticLake(),
        }
    )
    foundation = runtime.foundation
    _write_adapter_preparation(tmp_path, interfaces, foundation=foundation)
    repo_workspace = runtime.repo_workspace
    assert repo_workspace.metadata.ensure_repo_model(tmp_path).ok
    assert repo_workspace.metadata.set_repo_format(
        tmp_path,
        repo_format=RepoFormat.ADAPTER,
        reason="unit test adapter repo",
    ).ok
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    (tmp_path / "lakefile.toml").write_text(
        'name = "Adapter"\n\n[[require]]\nname = "upstream"\ngit = "https://github.com/example/upstream"\n',
        encoding="utf-8",
    )
    if create_upstream_checkout:
        upstream = tmp_path / ".lake" / "packages" / "upstream"
        (upstream / "Upstream").mkdir(parents=True)
        (upstream / "lakefile.toml").write_text('name = "upstream"\n', encoding="utf-8")
        (upstream / "Upstream" / "Basic.lean").write_text(
            "import Mathlib\n\n"
            "namespace Upstream.Basic\n\n"
            "theorem main_result : True := by\n"
            "  trivial\n\n"
            "def adapter_true : Prop := True\n\n"
            "end Upstream.Basic\n",
            encoding="utf-8",
        )
    service = runtime.adapter
    written = service.write_adapter_upstream_metadata(
        tmp_path,
        source_kind="git",
        git_url="https://github.com/example/upstream",
        package_name="upstream",
        dependency_name="upstream",
        evidence_summary="Unit test upstream.",
        visible_modules=["Upstream.Basic"],
    )
    assert written.ok
    trusted = service.mark_upstream_build_trusted(tmp_path, summary="lake update and minimal import check passed.")
    assert trusted.ok
    return service


def _finalize_theorem(
    service: AdapterService,
    repo_root: Path,
    *,
    name: str = "main_result",
    module: str = "Upstream.Basic",
) -> None:
    assert service.create_adapter_decl(
        repo_root,
        name=name,
        kind="theorem",
        module=module,
        lean_decl_name=f"{module}.{name}",
        summary=f"Expose theorem {name}.",
    ).ok
    assert service.set_adapter_statement_formal(
        repo_root,
        name=name,
        code=f"theorem {name} : True := by\n  sorry",
    ).ok
    assert service.set_adapter_statement_nl(repo_root, name=name, text=f"Statement for {name}.").ok
    assert service.set_adapter_proof_formal(
        repo_root,
        name=name,
        code=f"theorem {name} : True := by\n  trivial",
    ).ok
    assert service.set_adapter_proof_nl(repo_root, name=name, text=f"Proof for {name}.").ok
    assert service.finalize_adapter_decl(repo_root, name=name).ok


def _finalize_definition(
    service: AdapterService,
    repo_root: Path,
    *,
    name: str = "adapter_true",
    module: str = "Upstream.Basic",
) -> None:
    assert service.create_adapter_decl(
        repo_root,
        name=name,
        kind="definition",
        module=module,
        lean_decl_name=f"{module}.{name}",
        summary=f"Expose definition {name}.",
    ).ok
    assert service.set_adapter_statement_formal(
        repo_root,
        name=name,
        code=f"def {name} : Prop := True",
    ).ok
    assert service.set_adapter_statement_nl(repo_root, name=name, text=f"Definition for {name}.").ok
    assert service.finalize_adapter_decl(repo_root, name=name).ok


def test_upstream_metadata_roundtrip_and_validation(tmp_path: Path) -> None:
    component = UpstreamMetadataComponent(make_runtime())

    missing = component.get_adapter_upstream_metadata(tmp_path)
    assert not missing.ok
    assert missing.issues[0].kind == "adapter_upstream_missing"

    written = component.write_adapter_upstream_metadata(
        tmp_path,
        source_kind="git",
        git_url="https://github.com/example/upstream",
        package_name="upstream",
        dependency_name="upstream",
        evidence_summary="Found upstream.",
    )
    assert written.ok
    assert written.value is not None
    assert written.value.trusted_build is False
    assert "local_path" not in written.value.model_dump_json()

    gate = component.validate_upstream_metadata(tmp_path)
    assert gate.ok
    assert gate.value is not None
    assert gate.value.passed is False
    assert gate.value.issues[0].kind == "adapter_upstream_not_trusted"

    trusted = component.mark_upstream_build_trusted(tmp_path, summary="Minimal import passed.")
    assert trusted.ok
    assert trusted.value is not None
    assert trusted.value.trusted_build is True

    modules = component.record_visible_upstream_modules(tmp_path, modules=["Upstream.Basic", "Upstream.Basic"])
    assert modules.ok
    assert modules.value is not None
    assert modules.value.visible_module_count == 1

    passed = component.validate_upstream_metadata(tmp_path)
    assert passed.ok
    assert passed.value is not None
    assert passed.value.passed is True


def test_upstream_metadata_rejects_conflicting_sources_and_invalid_truth(tmp_path: Path) -> None:
    component = UpstreamMetadataComponent(make_runtime())

    git_conflict = component.write_adapter_upstream_metadata(
        tmp_path,
        source_kind="git",
        git_url="https://github.com/example/upstream",
        local_path="vendor/upstream",
        package_name="upstream",
        dependency_name="upstream",
    )
    assert not git_conflict.ok
    assert git_conflict.issues[0].kind == "adapter_upstream_invalid"

    local_conflict = component.write_adapter_upstream_metadata(
        tmp_path,
        source_kind="local_path",
        git_url="https://github.com/example/upstream",
        local_path="vendor/upstream",
        package_name="upstream",
        dependency_name="upstream",
    )
    assert not local_conflict.ok
    assert local_conflict.issues[0].kind == "adapter_upstream_invalid"

    missing_dependency = component.write_adapter_upstream_metadata(
        tmp_path,
        source_kind="git",
        git_url="https://github.com/example/upstream",
        package_name="upstream",
        dependency_name="",
    )
    assert not missing_dependency.ok
    assert missing_dependency.issues[0].kind == "adapter_upstream_invalid"

    local_upstream = tmp_path / "vendor" / "upstream"
    local_upstream.mkdir(parents=True)
    local_written = component.write_adapter_upstream_metadata(
        tmp_path,
        source_kind="local_path",
        local_path=str(local_upstream),
        package_name="upstream",
        dependency_name="upstream",
        evidence_summary="Local upstream checkout.",
    )
    assert local_written.ok
    status = component.get_adapter_upstream_status(tmp_path)
    assert status.ok
    assert status.value is not None
    assert str(local_upstream) not in status.value.model_dump_json()

    invalid_modules = component.record_visible_upstream_modules(tmp_path, modules=["Upstream.Basic", "../bad"])
    assert not invalid_modules.ok
    assert invalid_modules.issues[0].kind == "adapter_visible_module_invalid"

    trust_without_summary = component.mark_upstream_build_trusted(tmp_path, summary=" ")
    assert not trust_without_summary.ok
    assert trust_without_summary.issues[0].kind == "adapter_upstream_trust_summary_required"

    metadata_path = component._metadata_path(tmp_path)  # Service-level truth corruption fixture.
    metadata_path.write_text("{not valid json", encoding="utf-8")
    invalid = component.get_adapter_upstream_metadata(tmp_path)
    assert not invalid.ok
    assert invalid.issues[0].kind == "adapter_upstream_invalid"


def test_navigation_uses_toolkit_and_limits_agent_views(tmp_path: Path) -> None:
    dispatcher = FakeToolkitDispatcher()
    service = _service(tmp_path, dispatcher=dispatcher)

    search = service.search_upstream_declarations(tmp_path, query="main", limit=5)
    assert search.ok
    assert search.value is not None
    assert search.value.items[0].lean_decl_name == "Upstream.Basic.main_result"
    assert search.value.items[0].kind == "theorem"
    assert dispatcher.calls[-1][0] == "repo_nav.local_decl.find"
    assert dispatcher.calls[-1][1]["repo_root"].endswith(".lake/packages/upstream")

    modules = service.search_upstream_modules(tmp_path, query="Basic")
    assert modules.ok
    assert modules.value is not None
    assert modules.value.items[0].module == "Upstream.Basic"
    assert dispatcher.calls[-1][0] == "repo_nav.tree"

    decls = service.list_upstream_module_declarations(tmp_path, module="Upstream.Basic", kind_filter="theorem")
    assert decls.ok
    assert decls.value is not None
    assert decls.value.imports == ["Mathlib"]
    assert [item.lean_decl_name for item in decls.value.declarations] == ["Upstream.Basic.main_result"]

    inspected = service.inspect_upstream_declaration(tmp_path, module="Upstream.Basic", lean_decl_name="main_result")
    assert inspected.ok
    assert inspected.value is not None
    assert inspected.value.kind == "theorem"
    assert inspected.value.signature == "True"
    assert inspected.value.imports == ["Mathlib"]

    context = service.read_upstream_source_context(tmp_path, module="Upstream.Basic", lean_decl_name="main_result", line_window=3)
    assert context.ok
    assert context.value is not None
    assert "main_result" in context.value.text

    capture = service.capture_upstream_declaration_code(
        tmp_path,
        module="Upstream.Basic",
        lean_decl_name="main_result",
        capture_mode="full_declaration",
    )
    assert capture.ok
    assert capture.value is not None
    assert "trivial" in capture.value.code
    assert capture.value.scan.contains_sorry is False

    imports = service.inspect_upstream_module_imports(tmp_path, module="Upstream.Basic")
    assert imports.ok
    assert imports.value is not None
    assert imports.value.imports == ["Mathlib"]
    assert imports.value.namespace_hints == ["Upstream"]
    assert imports.value.package_hints == ["upstream"]


def test_navigation_requires_upstream_checkout_and_metadata(tmp_path: Path) -> None:
    service = _service(tmp_path, create_upstream_checkout=False)

    missing_root = service.search_upstream_declarations(tmp_path, query="main")
    assert not missing_root.ok
    assert missing_root.issues[0].kind == "adapter_upstream_root_missing"

    service_without_metadata = make_runtime().adapter
    missing_metadata = service_without_metadata.capture_upstream_declaration_code(
        tmp_path / "missing-metadata",
        module="Upstream.Basic",
        lean_decl_name="main_result",
        capture_mode="full_declaration",
    )
    assert not missing_metadata.ok
    assert missing_metadata.issues[0].kind == "adapter_upstream_missing"


def test_navigation_module_validation_and_visible_module_fallback(tmp_path: Path) -> None:
    class EmptyTreeDispatcher(FakeToolkitDispatcher):
        def __call__(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
            if tool_name == "repo_nav.tree":
                self.calls.append((tool_name, payload))
                return {"entries": []}
            return super().__call__(tool_name, payload)

    dispatcher = EmptyTreeDispatcher()
    service = _service(tmp_path, dispatcher=dispatcher)

    fallback = service.search_upstream_modules(tmp_path, query="Basic")
    assert fallback.ok
    assert fallback.value is not None
    assert fallback.value.items[0].match_reason == "visible_module"

    invalid_module = service.list_upstream_module_declarations(tmp_path, module="../bad")
    assert not invalid_module.ok
    assert invalid_module.issues[0].kind == "upstream_module_invalid"


def test_adapter_decl_catalog_finalize_and_completeness(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert service.ensure_flat_main_catalog(tmp_path).ok

    created = service.create_adapter_decl(
        tmp_path,
        name="main_result",
        kind="theorem",
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.main_result",
        summary="Expose the upstream main theorem.",
    )
    assert created.ok
    common_decl = service.runtime.decl_graph.get_decl(tmp_path, node_path="Main", name="main_result")
    assert common_decl.ok and common_decl.value is not None
    assert common_decl.value.name == "main_result"
    root_node = service.runtime.node.node_tree.node_store.resolve_active_node(tmp_path, path="Main")
    assert root_node.ok and root_node.value is not None
    legacy_flat_path = service.runtime.node.node_tree.node_store.node_dir(tmp_path, node_id=root_node.value.node_id) / "decls" / "main_result.json"
    assert not legacy_flat_path.exists()

    incomplete = service.check_adapter_decl_completeness(tmp_path, name="main_result")
    assert incomplete.ok
    assert incomplete.value is not None
    assert incomplete.value.complete is False
    assert {issue.kind for issue in incomplete.value.issues} >= {
        "adapter_statement_formal_missing",
        "adapter_statement_nl_missing",
        "adapter_proof_formal_missing",
        "adapter_proof_nl_missing",
    }

    assert service.set_adapter_statement_formal(
        tmp_path,
        name="main_result",
        code="theorem main_result : True := by\n  sorry",
    ).ok
    assert service.set_adapter_statement_nl(tmp_path, name="main_result", text="The upstream main theorem.").ok
    bad_proof = service.set_adapter_proof_formal(
        tmp_path,
        name="main_result",
        code="theorem main_result : True := by\n  sorry",
    )
    assert not bad_proof.ok
    assert bad_proof.issues[0].kind == "adapter_proof_forbidden_construct"

    assert service.set_adapter_proof_formal(
        tmp_path,
        name="main_result",
        code="theorem main_result : True := by\n  trivial",
    ).ok
    assert service.set_adapter_proof_nl(tmp_path, name="main_result", text="The theorem follows by trivial.").ok

    finalized = service.finalize_adapter_decl(tmp_path, name="main_result")
    assert finalized.ok
    assert finalized.value is not None
    assert finalized.value.finalized is True
    assert finalized.value.state == "proved"
    assert finalized.value.decl.public is True
    assert finalized.value.revision.status.value == "committed"

    modules = service.list_registered_adapter_modules(tmp_path)
    assert modules.ok
    assert modules.value is not None
    assert [item.module for item in modules.value.modules] == ["Upstream.Basic"]


def test_adapter_decl_catalog_create_and_field_failures(tmp_path: Path) -> None:
    service = _service(tmp_path)

    invalid_name = service.create_adapter_decl(
        tmp_path,
        name="bad/name",
        kind="theorem",
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.bad_name",
        summary="Invalid name.",
    )
    assert not invalid_name.ok
    assert invalid_name.issues[0].kind == "adapter_decl_name_invalid"

    for invalid_flat_name in ["Nested.name", "9startsWithDigit", "bad-name"]:
        invalid_flat = service.create_adapter_decl(
            tmp_path,
            name=invalid_flat_name,
            kind="theorem",
            module="Upstream.Basic",
            lean_decl_name="Upstream.Basic.bad_name",
            summary="Invalid flat name.",
        )
        assert not invalid_flat.ok
        assert invalid_flat.issues[0].kind == "adapter_decl_name_invalid"

    invalid_module = service.create_adapter_decl(
        tmp_path,
        name="bad_module",
        kind="theorem",
        module="../bad",
        lean_decl_name="Upstream.Basic.bad_module",
        summary="Invalid module.",
    )
    assert not invalid_module.ok
    assert invalid_module.issues[0].kind == "adapter_module_invalid"

    for invalid_lean_name in ["9Bad.result", "Upstream.bad-name"]:
        invalid_identity = service.create_adapter_decl(
            tmp_path,
            name="invalidIdentity",
            kind="theorem",
            module="Upstream.Basic",
            lean_decl_name=invalid_lean_name,
            summary="Invalid Lean identity.",
        )
        assert not invalid_identity.ok
        assert invalid_identity.issues[0].kind == "adapter_lean_decl_name_invalid"

    invalid_kind = service.create_adapter_decl(
        tmp_path,
        name="bad_kind",
        kind="corollary",
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.bad_kind",
        summary="Invalid kind.",
    )
    assert not invalid_kind.ok
    assert invalid_kind.issues[0].kind == "adapter_decl_kind_invalid"

    missing_summary = service.create_adapter_decl(
        tmp_path,
        name="missing_summary",
        kind="theorem",
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.missing_summary",
        summary=" ",
    )
    assert not missing_summary.ok
    assert missing_summary.issues[0].kind == "adapter_decl_summary_required"

    assert service.create_adapter_decl(
        tmp_path,
        name="main_result",
        kind="theorem",
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.main_result",
        summary="Expose theorem.",
    ).ok
    duplicate = service.create_adapter_decl(
        tmp_path,
        name="main_result",
        kind="theorem",
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.main_result",
        summary="Expose theorem again.",
    )
    assert not duplicate.ok
    assert duplicate.issues[0].kind == "adapter_decl_duplicate"

    duplicate_identity = service.create_adapter_decl(
        tmp_path,
        name="aliasResult",
        kind="theorem",
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.main_result",
        summary="Attempt to register the same upstream symbol under another key.",
    )
    assert not duplicate_identity.ok
    assert duplicate_identity.issues[0].kind == "adapter_upstream_identity_duplicate"

    empty_statement_code = service.set_adapter_statement_formal(tmp_path, name="main_result", code=" ")
    assert not empty_statement_code.ok
    assert empty_statement_code.issues[0].kind == "adapter_statement_code_required"

    empty_statement_nl = service.set_adapter_statement_nl(tmp_path, name="main_result", text=" ")
    assert not empty_statement_nl.ok
    assert empty_statement_nl.issues[0].kind == "adapter_statement_nl_invalid"

    missing_decl = service.inspect_adapter_decl(tmp_path, name="unknown")
    assert not missing_decl.ok
    assert missing_decl.issues[0].kind == "adapter_decl_missing"


def test_adapter_finalize_requires_compiler_confirmed_upstream_identity(tmp_path: Path) -> None:
    semantic_lake = FakeSemanticLake(snippet_ok=False)
    service = _service(tmp_path, semantic_lake=semantic_lake)
    assert service.create_adapter_decl(
        tmp_path,
        name="main_result",
        kind="theorem",
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.main_result",
        summary="Expose theorem.",
    ).ok
    assert service.set_adapter_statement_formal(
        tmp_path,
        name="main_result",
        code="theorem main_result : True := by\n  sorry",
    ).ok
    assert service.set_adapter_statement_nl(tmp_path, name="main_result", text="Statement.").ok
    assert service.set_adapter_proof_formal(
        tmp_path,
        name="main_result",
        code="theorem main_result : True := by\n  trivial",
    ).ok
    assert service.set_adapter_proof_nl(tmp_path, name="main_result", text="Proof.").ok

    finalized = service.finalize_adapter_decl(tmp_path, name="main_result")

    assert not finalized.ok
    assert finalized.issues[0].kind == "adapter_captured_decl_semantics_unconfirmed"
    assert semantic_lake.snippet_calls
    assert "lc_verify_captured_decl" in semantic_lake.snippet_calls[-1][1]
    assert "matches Upstream.Basic.main_result from Upstream.Basic" in semantic_lake.snippet_calls[-1][1]


def test_adapter_finalize_rejects_source_name_or_kind_mismatch(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert service.create_adapter_decl(
        tmp_path,
        name="main_result",
        kind="theorem",
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.main_result",
        summary="Expose theorem.",
    ).ok
    assert service.set_adapter_statement_formal(
        tmp_path,
        name="main_result",
        code="def main_result : Prop := True",
    ).ok
    assert service.set_adapter_statement_nl(tmp_path, name="main_result", text="Statement.").ok
    assert service.set_adapter_proof_formal(
        tmp_path,
        name="main_result",
        code="theorem main_result : True := by\n  trivial",
    ).ok
    assert service.set_adapter_proof_nl(tmp_path, name="main_result", text="Proof.").ok

    finalized = service.finalize_adapter_decl(tmp_path, name="main_result")

    assert not finalized.ok
    assert finalized.issues[0].kind == "adapter_source_decl_kind_mismatch"


def test_adapter_decl_catalog_origin_and_dep_lifecycle(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _finalize_theorem(service, tmp_path, name="support")
    assert service.create_adapter_decl(
        tmp_path,
        name="main_result",
        kind="theorem",
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.main_result",
        summary="Expose theorem.",
    ).ok

    origin = service.add_adapter_statement_origin(
        tmp_path,
        name="main_result",
        origin_text="Translated from upstream docstring.",
        source_hint="Upstream.Basic",
    )
    assert origin.ok
    duplicate_origin = service.add_adapter_statement_origin(
        tmp_path,
        name="main_result",
        origin_text="Translated from upstream docstring.",
        source_hint="Upstream.Basic",
    )
    assert duplicate_origin.ok
    assert duplicate_origin.issues
    assert duplicate_origin.issues[0].kind == "adapter_origin_duplicate"

    missing_dep = service.add_adapter_statement_dep(tmp_path, name="main_result", dep_name="missing", reason="Needed.")
    assert not missing_dep.ok
    assert missing_dep.issues[0].kind == "adapter_dep_missing"

    self_dep = service.add_adapter_statement_dep(tmp_path, name="main_result", dep_name="main_result", reason="Self.")
    assert not self_dep.ok
    assert self_dep.issues[0].kind == "adapter_dep_self"

    with pytest.raises(TypeError):
        service.add_adapter_statement_dep(  # type: ignore[call-arg]
            tmp_path,
            name="main_result",
            dependency_type="adapter_decl",
            target_reference="support",
            reason="Invalid generic dependency shape.",
        )

    with pytest.raises(TypeError):
        service.add_adapter_statement_origin(  # type: ignore[call-arg]
            tmp_path,
            name="main_result",
            origin_ref={"kind": "source", "path": "Upstream.Basic"},
        )

    dep = service.add_adapter_statement_dep(tmp_path, name="main_result", dep_name="support", reason="Uses support theorem.")
    assert dep.ok
    duplicate_dep = service.add_adapter_statement_dep(tmp_path, name="main_result", dep_name="support", reason="Uses support theorem.")
    assert duplicate_dep.ok
    assert duplicate_dep.issues[0].kind == "adapter_dep_duplicate"

    removed = service.remove_adapter_statement_dep(tmp_path, name="main_result", dep_name="support")
    assert removed.ok
    removed_again = service.remove_adapter_statement_dep(tmp_path, name="main_result", dep_name="support")
    assert not removed_again.ok
    assert removed_again.issues[0].kind == "adapter_dep_not_found"


def test_adapter_decl_catalog_non_theorem_and_filters(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _finalize_definition(service, tmp_path, name="adapter_true", module="Upstream.Basic")
    _finalize_theorem(service, tmp_path, name="main_result", module="Upstream.Basic")
    _finalize_theorem(service, tmp_path, name="second_result", module="Upstream.Other")

    proof_for_definition = service.set_adapter_proof_formal(
        tmp_path,
        name="adapter_true",
        code="theorem adapter_true : True := by\n  trivial",
    )
    assert not proof_for_definition.ok
    assert proof_for_definition.issues[0].kind == "adapter_proof_not_applicable"

    invalid_kind_filter = service.list_adapter_decls(tmp_path, kind_filter="corollary")
    assert not invalid_kind_filter.ok
    assert invalid_kind_filter.issues[0].kind == "adapter_decl_kind_filter_invalid"

    theorem_list = service.list_adapter_decls(tmp_path, kind_filter="theorem")
    assert theorem_list.ok
    assert theorem_list.value is not None
    assert [item.name for item in theorem_list.value] == ["main_result", "second_result"]

    module_filtered = service.list_adapter_decls(tmp_path, module_filter="Upstream.Basic", name_query="result")
    assert module_filtered.ok
    assert module_filtered.value is not None
    assert [item.name for item in module_filtered.value] == ["main_result"]

    inspected = service.inspect_adapter_decl(tmp_path, name="adapter_true")
    assert inspected.ok
    assert inspected.value is not None
    assert inspected.value.revision.revision == 1
    assert inspected.value.node_path == "Main"
    assert inspected.value.decl.node_path == "Main"

    modules = service.list_registered_adapter_modules(tmp_path)
    assert modules.ok
    assert modules.value is not None
    assert [(item.module, item.decl_names) for item in modules.value.modules] == [
        ("Upstream.Basic", ["adapter_true", "main_result"]),
        ("Upstream.Other", ["second_result"]),
    ]

    all_complete = service.check_adapter_decl_completeness(tmp_path)
    assert all_complete.ok
    assert all_complete.value is not None
    assert all_complete.value.complete is True

    exact = service.find_adapter_decl_by_upstream(
        tmp_path,
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.main_result",
    )
    assert exact.ok
    assert exact.value is not None
    assert [item.name for item in exact.value.matches] == ["main_result"]

    query = service.find_adapter_decl_by_upstream(
        tmp_path,
        module="Upstream.Basic",
        adapter_name_query="adapter",
    )
    assert query.ok
    assert query.value is not None
    assert [item.name for item in query.value.matches] == ["adapter_true"]

    no_query = service.find_adapter_decl_by_upstream(tmp_path, module="Upstream.Basic")
    assert no_query.ok
    assert no_query.value is not None
    assert no_query.value.matches == []


def test_adapter_interface_binding_projection_and_ready_gate(tmp_path: Path) -> None:
    interface = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Expose main theorem.")
    service = _service(tmp_path, interfaces=[interface])
    assert service.ensure_flat_main_catalog(tmp_path).ok
    assert service.create_adapter_decl(
        tmp_path,
        name="main_result",
        kind="theorem",
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.main_result",
        summary="Expose the upstream main theorem.",
    ).ok
    assert service.set_adapter_statement_formal(
        tmp_path,
        name="main_result",
        code="theorem main_result : True := by\n  sorry",
    ).ok
    assert service.set_adapter_statement_nl(tmp_path, name="main_result", text="Main theorem.").ok
    assert service.set_adapter_proof_formal(
        tmp_path,
        name="main_result",
        code="theorem main_result : True := by\n  trivial",
    ).ok
    assert service.set_adapter_proof_nl(tmp_path, name="main_result", text="Trivial proof.").ok
    assert service.finalize_adapter_decl(tmp_path, name="main_result").ok

    unbound = service.check_adapter_catalog_ready_preflight(tmp_path)
    assert unbound.ok
    assert unbound.value is not None
    assert unbound.value.passed is False
    assert any(issue.kind == "adapter_interface_unbound" for issue in unbound.value.issues)

    bound = service.bind_adapter_interface(
        tmp_path,
        interface_name="main_result",
        decl_name="main_result",
        binding_summary="The adapter decl satisfies the required theorem interface.",
    )
    assert bound.ok
    assert bound.value is not None
    assert bound.value.bound_decl is not None
    assert bound.value.bound_decl.name == "main_result"

    preview = service.preview_adapter_import_modules(tmp_path)
    assert preview.ok
    assert preview.value is not None
    assert preview.value.modules[0].module == "Upstream.Basic"

    missing_projection = service.check_adapter_projection(tmp_path)
    assert missing_projection.ok
    assert missing_projection.value is not None
    assert missing_projection.value.passed is False

    refreshed = service.refresh_adapter_projection(tmp_path)
    assert refreshed.ok
    assert refreshed.value is not None
    assert "public import Upstream.Basic" in (tmp_path / "Main" / "Interfaces.lean").read_text(encoding="utf-8")

    ready = service.check_adapter_ready(tmp_path)
    assert ready.ok
    assert ready.value is not None
    assert ready.value.passed is True
    provider_ready_before = service.runtime.repo_workspace.metadata.get_repo_publication(tmp_path)
    assert provider_ready_before.ok
    assert provider_ready_before.value is not None
    assert provider_ready_before.value.publication.status == RepoPublicationStatus.DEVELOPING

    submitted = service.submit_adapter_catalog_ready(tmp_path, summary="Adapter catalog is ready.")
    assert submitted.ok
    assert submitted.value is not None
    assert submitted.value.gate.passed is True
    assert "submission_type" not in type(submitted.value).model_fields
    assert "accepted" not in type(submitted.value).model_fields
    provider_ready_after = service.runtime.repo_workspace.metadata.get_repo_publication(tmp_path)
    assert provider_ready_after.ok
    assert provider_ready_after.value is not None
    assert provider_ready_after.value.publication.status == RepoPublicationStatus.DEVELOPING


def test_adapter_qualified_interface_requires_exact_lean_identity(tmp_path: Path) -> None:
    mismatching = DeclInterface(
        name="Expected.main_result",
        kind=DeclKind.THEOREM,
        summary="Expose main theorem under an exact Lean identity.",
    )
    service = _service(tmp_path, interfaces=[mismatching])
    assert service.ensure_flat_main_catalog(tmp_path).ok
    _finalize_theorem(service, tmp_path)

    rejected = service.bind_adapter_interface(
        tmp_path,
        interface_name="Expected.main_result",
        decl_name="main_result",
        binding_summary="Attempt the qualified binding.",
    )

    assert not rejected.ok
    assert rejected.issues[0].kind == "adapter_interface_lean_decl_name_mismatch"
    assert rejected.issues[0].current == "Upstream.Basic.main_result"
    assert rejected.issues[0].expected == "Expected.main_result"


def test_adapter_qualified_interface_accepts_exact_lean_identity(tmp_path: Path) -> None:
    matching = DeclInterface(
        name="Upstream.Basic.main_result",
        kind=DeclKind.THEOREM,
        summary="Expose main theorem under its exact Lean identity.",
    )
    service = _service(tmp_path, interfaces=[matching])
    assert service.ensure_flat_main_catalog(tmp_path).ok
    _finalize_theorem(service, tmp_path)

    bound = service.bind_adapter_interface(
        tmp_path,
        interface_name="Upstream.Basic.main_result",
        decl_name="main_result",
        binding_summary="Bind the exact qualified identity.",
    )

    assert bound.ok, bound.issues
    assert bound.value is not None
    assert bound.value.bound_decl is not None


def test_adapter_exact_interface_statement_contract_rejects_binding_and_later_drift(tmp_path: Path) -> None:
    matching_interface = DeclInterface(
        name="main_result",
        kind=DeclKind.THEOREM,
        summary="Expose the exact main theorem.",
        expected_statement_lean_code="theorem main_result : /- exact target -/ True := by sorry",
    )
    service = _service(tmp_path, interfaces=[matching_interface])
    assert service.ensure_flat_main_catalog(tmp_path).ok
    _finalize_theorem(service, tmp_path)
    bound = service.bind_adapter_interface(
        tmp_path,
        interface_name="main_result",
        decl_name="main_result",
        binding_summary="The exact upstream theorem is exposed.",
    )
    assert bound.ok, bound.issues

    assert service.set_adapter_proof_formal(
        tmp_path,
        name="main_result",
        code="theorem main_result : False := by\n  contradiction",
    ).ok
    drifted = service.validate_adapter_interface_bindings(tmp_path)

    assert drifted.ok and drifted.value is not None
    assert drifted.value.passed is False
    assert {issue.kind for issue in drifted.value.issues} == {
        "adapter_interface_statement_contract_mismatch"
    }

    mismatch_root = tmp_path / "mismatch"
    mismatching_interface = DeclInterface(
        name="main_result",
        kind=DeclKind.THEOREM,
        summary="Expose a different theorem.",
        expected_statement_lean_code="theorem main_result : False := by sorry",
    )
    mismatch_service = _service(mismatch_root, interfaces=[mismatching_interface])
    assert mismatch_service.ensure_flat_main_catalog(mismatch_root).ok
    _finalize_theorem(mismatch_service, mismatch_root)
    rejected = mismatch_service.bind_adapter_interface(
        mismatch_root,
        interface_name="main_result",
        decl_name="main_result",
        binding_summary="Attempt a mismatched binding.",
    )

    assert not rejected.ok
    assert rejected.issues[0].kind == "adapter_interface_statement_contract_mismatch"


def test_adapter_interface_binding_failures_unbind_and_validation(tmp_path: Path) -> None:
    interfaces = [
        DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Expose main theorem."),
        DeclInterface(name="def_iface", kind=DeclKind.DEFINITION, summary="Expose definition."),
    ]
    service = _service(tmp_path, interfaces=interfaces)
    assert service.ensure_flat_main_catalog(tmp_path).ok
    _finalize_definition(service, tmp_path, name="adapter_true")
    assert service.create_adapter_decl(
        tmp_path,
        name="draft_theorem",
        kind="theorem",
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.draft_theorem",
        summary="Draft theorem.",
    ).ok

    missing_summary = service.bind_adapter_interface(
        tmp_path,
        interface_name="main_result",
        decl_name="draft_theorem",
        binding_summary=" ",
    )
    assert not missing_summary.ok
    assert missing_summary.issues[0].kind == "adapter_binding_summary_required"

    missing_interface = service.bind_adapter_interface(
        tmp_path,
        interface_name="unknown",
        decl_name="draft_theorem",
        binding_summary="Bind draft.",
    )
    assert not missing_interface.ok
    assert missing_interface.issues[0].kind == "adapter_interface_missing"

    not_finalized = service.bind_adapter_interface(
        tmp_path,
        interface_name="main_result",
        decl_name="draft_theorem",
        binding_summary="Bind draft.",
    )
    assert not not_finalized.ok
    assert not_finalized.issues[0].kind == "adapter_decl_not_finalized"

    kind_mismatch = service.bind_adapter_interface(
        tmp_path,
        interface_name="main_result",
        decl_name="adapter_true",
        binding_summary="Wrong kind.",
    )
    assert not kind_mismatch.ok
    assert kind_mismatch.issues[0].kind == "adapter_interface_kind_mismatch"

    bound = service.bind_adapter_interface(
        tmp_path,
        interface_name="def_iface",
        decl_name="adapter_true",
        binding_summary="Definition satisfies interface.",
    )
    assert bound.ok
    assert bound.value is not None and bound.value.changed is True

    unbound_list = service.list_unbound_adapter_interfaces(tmp_path)
    assert unbound_list.ok
    assert unbound_list.value is not None
    assert unbound_list.value.interfaces == ["main_result"]

    missing_reason = service.unbind_adapter_interface(tmp_path, interface_name="def_iface", reason=" ")
    assert not missing_reason.ok
    assert missing_reason.issues[0].kind == "adapter_unbind_reason_required"

    unbound = service.unbind_adapter_interface(tmp_path, interface_name="def_iface", reason="Need a better decl.")
    assert unbound.ok
    assert unbound.value is not None and unbound.value.changed is True
    unbound_again = service.unbind_adapter_interface(tmp_path, interface_name="def_iface", reason="Need a better decl.")
    assert unbound_again.ok
    assert unbound_again.value is not None and unbound_again.value.changed is False

    gate = service.validate_adapter_interface_bindings(tmp_path)
    assert gate.ok
    assert gate.value is not None
    assert gate.value.passed is False
    assert {issue.kind for issue in gate.value.issues} == {"adapter_interface_unbound"}


def test_adapter_interface_binding_detects_missing_bound_target(tmp_path: Path) -> None:
    interface = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Expose main theorem.")
    service = _service(tmp_path, interfaces=[interface])
    assert service.ensure_flat_main_catalog(tmp_path).ok
    _finalize_theorem(service, tmp_path)
    assert service.bind_adapter_interface(
        tmp_path,
        interface_name="main_result",
        decl_name="main_result",
        binding_summary="The decl satisfies the interface.",
    ).ok

    service.adapter_decl_catalog._decl_path(tmp_path, "main_result").unlink()
    gate = service.validate_adapter_interface_bindings(tmp_path)
    assert gate.ok
    assert gate.value is not None
    assert gate.value.passed is False
    assert gate.value.issues[0].kind == "adapter_interface_target_missing"


def test_adapter_projection_preview_filters_finalized_and_detects_extra_import(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert service.ensure_flat_main_catalog(tmp_path).ok
    _finalize_theorem(service, tmp_path, name="main_result", module="Upstream.Basic")
    assert service.create_adapter_decl(
        tmp_path,
        name="draft_other",
        kind="theorem",
        module="Upstream.Other",
        lean_decl_name="Upstream.Other.draft_other",
        summary="Draft theorem.",
    ).ok

    preview = service.preview_adapter_import_modules(tmp_path)
    assert preview.ok
    assert preview.value is not None
    assert [(item.module, item.decl_names) for item in preview.value.modules] == [("Upstream.Basic", ["main_result"])]

    refreshed = service.refresh_adapter_projection(tmp_path)
    assert refreshed.ok
    interfaces_path = tmp_path / "Main" / "Interfaces.lean"
    interfaces_path.write_text(
        interfaces_path.read_text(encoding="utf-8") + "\npublic import Upstream.Extra\n",
        encoding="utf-8",
    )
    gate = service.check_adapter_projection(tmp_path)
    assert gate.ok
    assert gate.value is not None
    assert gate.value.passed is False
    assert gate.value.issues[0].kind == "adapter_projection_extra_import"


def test_adapter_ready_gate_aggregates_service_issues_and_submit_failures(tmp_path: Path) -> None:
    interface = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Expose main theorem.")
    service = _service(tmp_path, interfaces=[interface])
    assert service.ensure_flat_main_catalog(tmp_path).ok

    preflight = service.check_adapter_catalog_ready_preflight(tmp_path)
    assert preflight.ok
    assert preflight.value is not None
    assert preflight.value.passed is False
    issue_kinds = {issue.kind for issue in preflight.value.issues}
    assert "adapter_interface_unbound" in issue_kinds

    ready_without_summary = service.submit_adapter_catalog_ready(tmp_path, summary=" ")
    assert not ready_without_summary.ok
    assert ready_without_summary.issues[0].kind == "adapter_ready_summary_required"

    ready_with_failed_gate = service.submit_adapter_catalog_ready(tmp_path, summary="Ready.")
    assert not ready_with_failed_gate.ok
    assert any(issue.kind == "adapter_interface_unbound" for issue in ready_with_failed_gate.issues)

    blocked_without_reason = service.submit_adapter_catalog_blocked(tmp_path, reason=" ")
    assert not blocked_without_reason.ok
    assert blocked_without_reason.issues[0].kind == "adapter_blocked_reason_required"

    blocked_without_evidence = service.submit_adapter_catalog_blocked(
        tmp_path,
        reason="Need upstream theorem not present in catalog.",
        missing_interfaces=["main_result"],
    )
    assert not blocked_without_evidence.ok
    assert blocked_without_evidence.issues[0].kind == "adapter_blocked_evidence_required"

    blocked = service.submit_adapter_catalog_blocked(
        tmp_path,
        reason="Need upstream theorem not present in catalog.",
        missing_interfaces=["main_result"],
        evidence_summary="No matching declaration found.",
        suggested_next_action="Request a different upstream repo.",
    )
    assert blocked.ok
    assert blocked.value is not None
    assert blocked.value.reason == "Need upstream theorem not present in catalog."
    assert blocked.value.missing_interfaces == ["main_result"]
    assert "submission_type" not in type(blocked.value).model_fields
    assert "accepted" not in type(blocked.value).model_fields


def test_adapter_preparation_validation_checks_repo_format_source_mode_dependency_and_trust(tmp_path: Path) -> None:
    interface = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Expose main theorem.")
    service = _service(tmp_path, interfaces=[interface])

    validation = service.validate_adapter_preparation_input(tmp_path)

    assert validation.ok
    assert validation.value is not None
    assert validation.value.outcome == "passed"


def test_inspect_adapter_input_allows_missing_upstream_metadata(tmp_path: Path) -> None:
    runtime = make_runtime()
    foundation = runtime.foundation
    _write_adapter_preparation(tmp_path, foundation=foundation)
    repo_workspace = runtime.repo_workspace
    assert repo_workspace.metadata.ensure_repo_model(tmp_path).ok
    assert repo_workspace.metadata.set_repo_format(
        tmp_path,
        repo_format=RepoFormat.ADAPTER,
        reason="unit test adapter repo",
    ).ok
    service = runtime.adapter

    inspected = service.inspect_adapter_input(tmp_path)

    assert inspected.ok
    assert inspected.issues == []
    assert inspected.value is not None
    assert inspected.value.upstream_status is None
    assert "Upstream metadata is not available yet." in inspected.value.summary
