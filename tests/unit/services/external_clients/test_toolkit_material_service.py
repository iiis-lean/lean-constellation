from __future__ import annotations

import io
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.external_clients import (
    LeanMcpToolkitClient,
    LeanMcpToolkitClientConfig,
    MaterialAcquisitionExtractionClient,
)
from lean_constellation.services.external_clients import lean_mcp_toolkit as toolkit_module
from lean_constellation.services.external_clients.lean_mcp_toolkit import (
    ToolkitCompiledDeclarationTarget,
)


def test_toolkit_dispatch_and_wrappers(tmp_path) -> None:
    def dispatch(tool_name: str, payload: dict):
        if tool_name == "search_mathlib":
            return {"items": [{"name": "Nat.add_assoc"}]}
        if tool_name == "inspect_mathlib_decl":
            return {"code": "theorem Nat.add_assoc := ...", "module": "Init"}
        if tool_name == "run_file_diagnostics":
            return {"diagnostics": [{"severity": "error"}]}
        raise KeyError(tool_name)

    client = LeanMcpToolkitClient(dispatcher=dispatch)

    search = client.search_mathlib("addition", ["theorem"], 10)
    decl = client.inspect_mathlib_decl("Nat.add_assoc")
    diagnostics = client.run_file_diagnostics(tmp_path, tmp_path / "A.lean")
    missing = client.search_arxiv_theorems("fixed point", 5)
    scan = client.scan_sorry_axiom("axiom bad : False\nexample : True := by sorry\n")

    assert search.ok is True and search.items[0]["name"] == "Nat.add_assoc"
    assert decl.ok is True and decl.module == "Init"
    assert diagnostics.ok is True and len(diagnostics.diagnostics) == 1
    assert missing.ok is False and missing.issue_code == "toolkit_tool_missing"
    assert scan.ok is False and scan.sorry_count == 1 and scan.axiom_count == 1


def test_toolkit_config_defaults_to_api_v1_and_unavailable_is_structured() -> None:
    client = LeanMcpToolkitClient()

    result = client.call_tool("lean_explore.find", {"query": "Nat"})

    assert client.config.api_prefix == "/api/v1"
    assert result.ok is False
    assert result.issue_code == "toolkit_unavailable"


def test_toolkit_canonical_mathlib_search_and_fallback(tmp_path) -> None:
    calls: list[tuple[str, dict]] = []

    def canonical_dispatch(tool_name: str, payload: dict):
        calls.append((tool_name, payload))
        if tool_name == "lean_explore.find":
            return {"results": [{"name": "Nat.add_assoc", "module": "Init"}], "count": 1}
        raise KeyError(tool_name)

    canonical = LeanMcpToolkitClient(dispatcher=canonical_dispatch).search_mathlib("addition", limit=3)

    fallback_calls: list[str] = []

    def fallback_dispatch(tool_name: str, payload: dict):
        fallback_calls.append(tool_name)
        if tool_name == "lean_explore.find":
            raise KeyError(tool_name)
        if tool_name == "search_mathlib":
            return {"items": [{"name": "Nat.mul_assoc"}]}
        raise KeyError(tool_name)

    fallback = LeanMcpToolkitClient(dispatcher=fallback_dispatch).search_mathlib("multiplication", ["theorem"], 3)

    assert canonical.ok is True
    assert calls[0][0] == "lean_explore.find"
    assert calls[0][1]["include_module"] is True
    assert canonical.items[0]["source_tool"] == "lean_explore.find"
    assert fallback.ok is True
    assert fallback_calls == ["lean_explore.find", "search_mathlib"]
    assert fallback.items[0]["source_tool"] == "search_mathlib"


def test_toolkit_canonical_failure_is_not_overwritten_by_legacy_fallback() -> None:
    calls: list[str] = []

    def dispatch(tool_name: str, payload: dict):
        del payload
        calls.append(tool_name)
        if tool_name == "lean_explore.find":
            raise RuntimeError("lean_explore backend unavailable")
        if tool_name == "search_mathlib":
            return {"items": [{"name": "Nat.add_assoc"}]}
        raise KeyError(tool_name)

    result = LeanMcpToolkitClient(dispatcher=dispatch).search_mathlib("Nat.add", limit=3)

    assert result.ok is False
    assert result.issue_code == "toolkit_call_failed"
    assert "lean_explore backend unavailable" in result.summary
    assert calls == ["lean_explore.find"]


def test_toolkit_canonical_decl_module_diagnostics_and_extract(tmp_path) -> None:
    calls: list[tuple[str, dict]] = []

    def dispatch(tool_name: str, payload: dict):
        calls.append((tool_name, payload))
        if tool_name == "lean_explore.find":
            return {
                "results": [
                    {"name": "Other", "module": "Init", "source_text": "theorem Other := by trivial"},
                    {"name": "Nat.add_assoc", "module": "Init", "source_text": "theorem Nat.add_assoc := by sorry"},
                ]
            }
        if tool_name == "mathlib_nav.file_outline":
            return {"imports": ["Init"], "declarations": [{"name": "Nat.add_assoc"}]}
        if tool_name == "diagnostics.file":
            return {"success": True, "items": [{"severity": "error"}], "error_count": 1}
        if tool_name == "declarations.extract":
            return {
                "success": True,
                "declarations": [
                    {
                        "name": "target_decl",
                        "full_declaration": "theorem target_decl : True := by trivial",
                        "decl_start_pos": {"line": 3, "column": 0},
                        "decl_end_pos": {"line": 3, "column": 40},
                    }
                ],
            }
        raise KeyError(tool_name)

    client = LeanMcpToolkitClient(dispatcher=dispatch)

    decl = client.inspect_mathlib_decl("Nat.add_assoc")
    module = client.inspect_mathlib_module("Init.Data.Nat")
    diagnostics = client.run_file_diagnostics(tmp_path, tmp_path / "Main.lean")
    extracted = client.extract_declaration(tmp_path, "Main.lean", "target_decl")

    assert decl.ok is True
    assert calls[0][1]["exact_name"] == "Nat.add_assoc"
    assert decl.name == "Nat.add_assoc"
    assert decl.code == "theorem Nat.add_assoc := by sorry"
    assert module.ok is True
    assert module.imports == ["Init"]
    assert module.declarations == [{"name": "Nat.add_assoc"}]
    assert diagnostics.ok is True
    assert diagnostics.diagnostics == [{"severity": "error"}]
    assert extracted.ok is True
    assert extracted.code == "theorem target_decl : True := by trivial"
    assert extracted.decl_start_pos == {"line": 3, "column": 0}
    assert [call[0] for call in calls] == [
        "lean_explore.find",
        "mathlib_nav.file_outline",
        "diagnostics.file",
        "declarations.extract",
    ]


def test_toolkit_mathlib_inspection_does_not_substitute_first_related_result() -> None:
    def dispatch(tool_name: str, payload: dict):
        assert tool_name == "lean_explore.find"
        assert payload["exact_name"] == "Int.natAbs_mul"
        return {
            "results": [
                {
                    "name": "norm_pow_natAbs",
                    "module": "Mathlib.Analysis.Normed.Group.Basic",
                    "source_text": "theorem norm_pow_natAbs : True := by trivial",
                }
            ]
        }

    result = LeanMcpToolkitClient(dispatcher=dispatch).inspect_mathlib_decl("Int.natAbs_mul")

    assert result.ok is False
    assert result.issue_code == "declaration_not_found"
    assert result.name == "Int.natAbs_mul"


def test_toolkit_call_exception_and_extract_missing_are_structured(tmp_path) -> None:
    def failing_dispatch(tool_name: str, payload: dict):
        raise RuntimeError("boom")

    failed = LeanMcpToolkitClient(dispatcher=failing_dispatch).call_tool("lean_explore.find", {"query": "x"})

    def missing_decl_dispatch(tool_name: str, payload: dict):
        if tool_name == "declarations.extract":
            return {"success": True, "declarations": [{"name": "other"}]}
        raise KeyError(tool_name)

    missing = LeanMcpToolkitClient(dispatcher=missing_decl_dispatch).extract_declaration(tmp_path, "Main.lean", "target")

    assert failed.ok is False
    assert failed.issue_code == "toolkit_call_failed"
    assert missing.ok is False
    assert missing.issue_code == "declaration_not_found"


def test_toolkit_compiled_declaration_batch_is_strict_and_preserves_provenance(
    tmp_path: Path,
) -> None:
    def dispatch(tool_name: str, payload: dict):
        assert tool_name == "lsp.compiled_declaration_batch"
        target = payload["declarations"][0]
        return {
            "success": True,
            "error_message": None,
            "items": [
                {
                    "module": target["module"],
                    "declaration_name": target["declaration_name"],
                    "success": True,
                    "error_message": None,
                    "owner_module": "Upstream.Basic",
                    "declaration_kind": "theorem",
                    "signature": "True",
                    "universe_count": 0,
                    "representation": "compiled_reference",
                    "reference_code": "#check _root_.Upstream.Basic.generated",
                    "generation_kind": "to_additive",
                    "generator_declaration": "Upstream.Basic.generator",
                    "provenance_error_message": None,
                }
            ],
            "count": 1,
            "success_count": 1,
            "failure_count": 0,
        }

    result = LeanMcpToolkitClient(dispatcher=dispatch).inspect_compiled_declarations(
        tmp_path,
        [
            ToolkitCompiledDeclarationTarget(
                module="Upstream.Basic",
                declaration_name="Upstream.Basic.generated",
            )
        ],
        include_to_additive_provenance=True,
    )

    assert result.protocol_ok is True
    assert result.batch_success is True
    assert result.items[0].generation_kind == "to_additive"
    assert result.items[0].generator_declaration == "Upstream.Basic.generator"
    assert result.items[0].representation == "compiled_reference"


def test_toolkit_compiled_declaration_batch_rejects_identity_mismatch(
    tmp_path: Path,
) -> None:
    def dispatch(tool_name: str, payload: dict):
        del tool_name, payload
        return {
            "success": True,
            "items": [
                {
                    "module": "Other.Module",
                    "declaration_name": "Other.target",
                    "success": True,
                    "owner_module": "Other.Module",
                    "declaration_kind": "theorem",
                    "signature": "True",
                    "universe_count": 0,
                    "representation": "compiled_reference",
                }
            ],
            "count": 1,
            "success_count": 1,
            "failure_count": 0,
        }

    result = LeanMcpToolkitClient(dispatcher=dispatch).inspect_compiled_declarations(
        tmp_path,
        [
            ToolkitCompiledDeclarationTarget(
                module="Upstream.Basic",
                declaration_name="Upstream.Basic.generated",
            )
        ],
    )

    assert result.protocol_ok is False
    assert result.issue_code == "compiled_declaration_invalid_response"
    assert "identity mismatch" in result.summary


def test_toolkit_normalizes_to_dict_response() -> None:
    class ResponseObject:
        def to_dict(self) -> dict:
            return {"results": [{"name": "Nat.zero_eq"}]}

    client = LeanMcpToolkitClient(dispatcher=lambda tool, payload: ResponseObject())

    result = client.search_mathlib("zero", limit=1)

    assert result.ok is True
    assert result.items[0]["name"] == "Nat.zero_eq"


def test_toolkit_probe_catalog_normalizes_tools_and_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_urls: list[str] = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "tools": [
                        {"canonical_name": "diagnostics.file", "summary": "Diagnostics"},
                        {"name": "declarations.extract", "description": "Extract decls"},
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout: int):
        seen_urls.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr(toolkit_module, "urlopen", fake_urlopen)
    client = LeanMcpToolkitClient(
        LeanMcpToolkitClientConfig(base_url="http://127.0.0.1:18080", api_prefix="/api/v1")
    )

    result = client.probe_tool_catalog(["diagnostics.file", "missing.tool"])

    assert result.ok is False
    assert result.issue_code == "toolkit_required_tools_missing"
    assert result.missing_tools == ["missing.tool"]
    assert [tool.name for tool in result.tools] == ["diagnostics.file", "declarations.extract"]
    assert seen_urls == ["http://127.0.0.1:18080/api/v1/meta/tools"]


def test_toolkit_probe_catalog_unconfigured_and_invalid_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self) -> bytes:
            return b'{"items": []}'

    monkeypatch.setattr(toolkit_module, "urlopen", lambda request, timeout: FakeResponse())

    unavailable = LeanMcpToolkitClient().probe_tool_catalog(["diagnostics.file"])
    invalid = LeanMcpToolkitClient(
        LeanMcpToolkitClientConfig(base_url="http://127.0.0.1:18080")
    ).probe_tool_catalog(["diagnostics.file"])

    assert unavailable.ok is False
    assert unavailable.issue_code == "toolkit_unavailable"
    assert unavailable.missing_tools == ["diagnostics.file"]
    assert invalid.ok is False
    assert invalid.issue_code == "toolkit_catalog_invalid_schema"


def test_toolkit_call_tool_uses_http_catalog_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout: int):
        seen.append((request.get_method(), request.full_url))
        if request.get_method() == "GET":
            return FakeResponse(
                {
                    "tools": [
                        {
                            "canonical_name": "diagnostics.file",
                            "api_path": "/diagnostics/file",
                            "aliases": ["diagnostics.file"],
                        }
                    ]
                }
            )
        return FakeResponse({"diagnostics": [], "success": True})

    monkeypatch.setattr(toolkit_module, "urlopen", fake_urlopen)
    client = LeanMcpToolkitClient(
        LeanMcpToolkitClientConfig(base_url="http://127.0.0.1:18080", api_prefix="/api/v1")
    )

    result = client.call_tool("diagnostics.file", {"project_root": "/repo", "file_path": "/repo/Main.lean"})

    assert result.ok is True
    assert result.value == {"diagnostics": [], "success": True}
    assert seen == [
        ("GET", "http://127.0.0.1:18080/api/v1/meta/tools"),
        ("POST", "http://127.0.0.1:18080/api/v1/diagnostics/file"),
    ]


def test_toolkit_dispatcher_missing_tool_falls_back_to_http_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatch_calls: list[str] = []
    http_calls: list[tuple[str, str]] = []

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    def dispatch(tool_name: str, payload: dict):
        dispatch_calls.append(tool_name)
        raise KeyError(tool_name)

    def fake_urlopen(request, timeout: int):
        http_calls.append((request.get_method(), request.full_url))
        if request.get_method() == "GET":
            return FakeResponse(
                {
                    "tools": [
                        {
                            "canonical_name": "repo_nav.file_outline",
                            "api_path": "/search/repo_nav/file_outline",
                        }
                    ]
                }
            )
        return FakeResponse({"success": True, "declarations": [{"name": "upstreamSmoke"}]})

    monkeypatch.setattr(toolkit_module, "urlopen", fake_urlopen)
    client = LeanMcpToolkitClient(
        LeanMcpToolkitClientConfig(base_url="http://127.0.0.1:18080", api_prefix="/api/v1"),
        dispatcher=dispatch,
    )

    result = client.call_tool("repo_nav.file_outline", {"repo_root": "/repo", "target": "Upstream"})

    assert result.ok is True
    assert result.value == {"success": True, "declarations": [{"name": "upstreamSmoke"}]}
    assert dispatch_calls == ["repo_nav.file_outline"]
    assert http_calls == [
        ("GET", "http://127.0.0.1:18080/api/v1/meta/tools"),
        ("POST", "http://127.0.0.1:18080/api/v1/search/repo_nav/file_outline"),
    ]


def test_scan_sorry_axiom_ignores_comments_strings_and_counts_admit() -> None:
    client = LeanMcpToolkitClient()
    text = '''
-- sorry
/- axiom hidden : False -/
def message := "sorry axiom"
example : True := by
  admit
axiom real_axiom : False
def sorryful := 1
'''

    result = client.scan_sorry_axiom(text)

    assert result.ok is False
    assert result.sorry_count == 0
    assert result.admit_count == 1
    assert result.axiom_count == 1


def test_material_local_file_html_tex_and_validation(tmp_path) -> None:
    client = MaterialAcquisitionExtractionClient()
    source = tmp_path / "note.html"
    source.write_text("<html><script>x</script><p>Hello <b>world</b></p></html>", encoding="utf-8")

    acquired = client.import_local_file(source_path=source, output_root=tmp_path / "draft")
    extracted = client.extract_web_main_text(html_path=Path(acquired.primary_artifact_path), output_root=tmp_path / "draft")
    validation = client.validate_readable_text(Path(extracted.primary_text_path))

    assert acquired.ok is True
    assert acquired.content_hash is not None
    assert extracted.ok is True
    assert "Hello world" in (extracted.text_preview or "")
    assert validation.ok is True

    tex = tmp_path / "paper.tex"
    tex.write_text("\\documentclass{article}\\begin{document}Main\\end{document}", encoding="utf-8")
    tex_extract = client.extract_arxiv_tex(source_root_or_archive=tex, output_root=tmp_path / "tex-draft")
    assert tex_extract.ok is True
    assert tex_extract.primary_text_path is not None


def test_material_fake_downloader(tmp_path) -> None:
    def downloader(url: str, path: Path, headers: dict[str, str], timeout: int) -> dict[str, str]:
        path.write_text(f"downloaded {url}", encoding="utf-8")
        return {"Content-Type": "text/html", "X-Test": "ok"}

    client = MaterialAcquisitionExtractionClient(downloader=downloader)

    result = client.fetch_web_page("https://example.com/a", output_root=tmp_path)

    assert result.ok is True
    assert "Content-Type: text/html" in result.metadata["headers_summary"]
    assert Path(result.primary_artifact_path).read_text(encoding="utf-8").startswith("downloaded")


def test_material_arxiv_downloaders_and_local_file_errors(tmp_path) -> None:
    calls: list[str] = []

    def downloader(url: str, path: Path, headers: dict[str, str], timeout: int) -> None:
        calls.append(url)
        path.write_bytes(b"payload")

    client = MaterialAcquisitionExtractionClient(downloader=downloader)

    source = client.fetch_arxiv_source("2401.00001", "v2", output_root=tmp_path / "source")
    pdf = client.fetch_arxiv_pdf("2401.00001", None, output_root=tmp_path / "pdf")
    missing_arg = client.import_local_file(output_root=tmp_path / "missing-arg")
    missing_file = client.import_local_file(source_path=tmp_path / "missing.txt", output_root=tmp_path / "missing-file")

    assert source.ok is True
    assert pdf.ok is True
    assert source.artifact_kind == "arxiv_source"
    assert pdf.artifact_kind == "arxiv_pdf"
    assert calls == ["https://arxiv.org/e-print/2401.00001v2", "https://arxiv.org/pdf/2401.00001.pdf"]
    assert missing_arg.ok is False
    assert missing_arg.issue_code == "missing_local_file_path"
    assert missing_file.ok is False
    assert missing_file.issue_code == "missing_local_file"


def test_material_import_local_dir_and_pdf_extract_branches(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = MaterialAcquisitionExtractionClient()
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    (local_dir / "a.txt").write_text("A", encoding="utf-8")

    imported = client.import_local_dir(source_path=local_dir, output_root=tmp_path / "dir-draft")

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")

    def fake_run_success(command, text: bool, stdout, stderr, check: bool):
        Path(command[-1]).write_text("extracted text", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run_success)
    extracted = client.extract_pdf_text(pdf_path=pdf, output_root=tmp_path / "pdf-ok")

    def fake_run_failure(command, text: bool, stdout, stderr, check: bool):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="bad pdf")

    monkeypatch.setattr(subprocess, "run", fake_run_failure)
    failed = client.extract_pdf_text(pdf_path=pdf, output_root=tmp_path / "pdf-fail")

    def fake_run_missing(command, text: bool, stdout, stderr, check: bool):
        raise OSError("missing")

    monkeypatch.setattr(subprocess, "run", fake_run_missing)
    unavailable = client.extract_pdf_text(pdf_path=pdf, output_root=tmp_path / "pdf-missing")

    assert imported.ok is True
    assert len(imported.artifact_paths) == 1
    assert extracted.ok is True
    assert extracted.text_preview == "extracted text"
    assert failed.ok is False
    assert failed.issue_code == "pdf_extract_failed"
    assert unavailable.ok is False
    assert unavailable.issue_code == "pdf_extractor_unavailable"


def test_material_extract_arxiv_tex_rejects_unsafe_archives(tmp_path) -> None:
    client = MaterialAcquisitionExtractionClient()
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("../evil.tex", "\\begin{document}bad\\end{document}")

    tar_path = tmp_path / "bad.tar"
    payload = b"\\begin{document}bad\\end{document}"
    with tarfile.open(tar_path, "w") as archive:
        info = tarfile.TarInfo("../evil.tex")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    zip_result = client.extract_arxiv_tex(source_root_or_archive=zip_path, output_root=tmp_path / "zip-out")
    tar_result = client.extract_arxiv_tex(source_root_or_archive=tar_path, output_root=tmp_path / "tar-out")

    assert zip_result.ok is False
    assert zip_result.issue_code == "arxiv_tex_extract_failed"
    assert tar_result.ok is False
    assert tar_result.issue_code == "arxiv_tex_extract_failed"
    assert (tmp_path / "evil.tex").exists() is False


def test_material_extract_arxiv_tex_from_safe_zip_and_tar_selects_main(tmp_path) -> None:
    client = MaterialAcquisitionExtractionClient()
    zip_path = tmp_path / "paper.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("support/macros.tex", "\\newcommand{\\x}{x}")
        archive.writestr("main.tex", "\\documentclass{article}\\begin{document}Main document\\end{document}")

    tar_path = tmp_path / "paper.tar"
    payload = b"\\documentclass{article}\\begin{document}Tar main\\end{document}"
    with tarfile.open(tar_path, "w") as archive:
        info = tarfile.TarInfo("main.tex")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    zip_result = client.extract_arxiv_tex(source_root_or_archive=zip_path, output_root=tmp_path / "zip-ok")
    tar_result = client.extract_arxiv_tex(source_root_or_archive=tar_path, output_root=tmp_path / "tar-ok")

    assert zip_result.ok is True
    assert Path(zip_result.primary_text_path).name == "main.tex"
    assert "Main document" in (zip_result.text_preview or "")
    assert tar_result.ok is True
    assert "Tar main" in (tar_result.text_preview or "")


def test_validate_readable_text_missing_empty_and_decode_replacement(tmp_path) -> None:
    client = MaterialAcquisitionExtractionClient()
    empty = tmp_path / "empty.txt"
    replacement = tmp_path / "replacement.txt"
    empty.write_text("", encoding="utf-8")
    replacement.write_text("bad\ufffdtext", encoding="utf-8")

    missing = client.validate_readable_text(tmp_path / "missing.txt")
    empty_result = client.validate_readable_text(empty)
    replacement_result = client.validate_readable_text(replacement)

    assert missing.ok is False
    assert missing.issue_code == "missing_text"
    assert empty_result.ok is False
    assert empty_result.issue_code == "empty_text"
    assert replacement_result.ok is False
    assert replacement_result.issue_code == "decode_replacement"


def test_external_client_service_allows_injected_clients() -> None:
    toolkit = LeanMcpToolkitClient(dispatcher=lambda tool, payload: {"ok": True})

    service = make_runtime(external_overrides={"lean_mcp_toolkit": toolkit}).external
    health = service.check_external_client_health(required_toolkit_groups=["mathlib"], required_toolkit_tools=["search"])

    assert service.lean_toolkit is toolkit
    assert health.lean_toolkit_available is True
    assert health.missing_toolkit_groups == ["mathlib"]
