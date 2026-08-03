from __future__ import annotations

import io
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from lean_constellation.services.external_clients import MaterialAcquisitionConfig, MaterialAcquisitionExtractionClient


def test_typed_resolver_uses_magic_and_acquisition_truth_before_suffix(tmp_path: Path) -> None:
    client = MaterialAcquisitionExtractionClient()
    archive = tmp_path / "2407.12253-source"
    payload = b"\\documentclass{article}\\begin{document}Main\\end{document}"
    with tarfile.open(archive, "w:gz") as handle:
        info = tarfile.TarInfo("main.tex")
        info.size = len(payload)
        handle.addfile(info, io.BytesIO(payload))
    renamed_pdf = tmp_path / "paper.txt"
    renamed_pdf.write_bytes(b"%PDF-1.4\nfixture")
    tex_without_suffix = tmp_path / "single-source"
    tex_without_suffix.write_text(payload.decode("utf-8"), encoding="utf-8")
    false_pdf = tmp_path / "false.pdf"
    false_pdf.write_text("plain text with a misleading suffix\n", encoding="utf-8")
    arxiv_error = tmp_path / "arxiv-source"
    arxiv_error.write_text("<html><body>rate limited</body></html>\n", encoding="utf-8")

    archive_resolution = client.resolve_artifact_kind(archive, acquisition_kind="arxiv_source")
    pdf_resolution = client.resolve_artifact_kind(renamed_pdf)
    tex_resolution = client.resolve_artifact_kind(tex_without_suffix, acquisition_kind="arxiv_source")
    magic_over_mime = client.resolve_artifact_kind(renamed_pdf, mime_type="text/html")
    false_pdf_resolution = client.resolve_artifact_kind(false_pdf)
    arxiv_error_resolution = client.resolve_artifact_kind(
        arxiv_error,
        acquisition_kind="arxiv_source",
        mime_type="text/html",
    )

    assert archive_resolution.kind == "tex_source_archive"
    assert archive_resolution.extraction_kind == "tex_source"
    assert pdf_resolution.kind == "pdf"
    assert pdf_resolution.extraction_kind == "pdf_text"
    assert tex_resolution.kind == "tex_source_archive"
    assert tex_resolution.extraction_kind == "tex_source"
    assert magic_over_mime.kind == "pdf"
    assert false_pdf_resolution.kind == "unknown_binary"
    assert false_pdf_resolution.compatible is False
    assert arxiv_error_resolution.kind == "unknown_binary"
    assert arxiv_error_resolution.issue_code == "material_artifact_kind_conflict"


def test_html_mime_is_preserved_through_extraction_for_extensionless_fragment(tmp_path: Path) -> None:
    source = tmp_path / "page-snapshot"
    source.write_text("<main><h1>Title</h1><p>Faithful body.</p></main>\n", encoding="utf-8")

    result = MaterialAcquisitionExtractionClient().extract_web_main_text(
        html_path=source,
        output_root=tmp_path / "draft",
        acquisition_kind="web_page",
        mime_type="text/html",
    )

    assert result.ok
    assert result.primary_text_path is not None
    assert Path(result.primary_text_path).read_text(encoding="utf-8") == "Title Faithful body.\n"


@pytest.mark.parametrize(
    ("payload", "issue_code"),
    [
        (b"%PDF-1.4\nfixture", "material_extraction_kind_mismatch"),
        (b"\x1f\x8b\x08\x00binary", "material_extraction_kind_mismatch"),
        (b"\xff\xfeinvalid", "material_extraction_kind_mismatch"),
        (b"alpha\x00beta", "material_extraction_kind_mismatch"),
        (b"   \n\t", "material_extraction_kind_mismatch"),
    ],
)
def test_strict_text_normalization_rejects_non_text_payloads(
    tmp_path: Path,
    payload: bytes,
    issue_code: str,
) -> None:
    source = tmp_path / "renamed.txt"
    source.write_bytes(payload)

    result = MaterialAcquisitionExtractionClient().normalize_text_material(
        input_path=source,
        output_root=tmp_path / "draft",
    )

    assert result.ok is False
    assert result.issue_code == issue_code
    assert not (tmp_path / "draft" / "normalized" / "renamed.txt").exists()


def test_readable_validation_rejects_replacement_and_binary_controls(tmp_path: Path) -> None:
    client = MaterialAcquisitionExtractionClient()
    replacement = tmp_path / "replacement.md"
    replacement.write_text("alpha\ufffdbeta", encoding="utf-8")
    controls = tmp_path / "controls.md"
    controls.write_text("\x01\x02\x03", encoding="utf-8")

    replacement_result = client.validate_readable_text(replacement)
    controls_result = client.validate_readable_text(controls)

    assert replacement_result.ok is False
    assert replacement_result.issue_code == "decode_replacement"
    assert controls_result.ok is False
    assert controls_result.issue_code == "binary_control_text"


def test_local_file_import_returns_safe_normalized_artifact_view(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("# Note\n", encoding="utf-8")
    output_root = tmp_path / "draft"

    result = MaterialAcquisitionExtractionClient().import_local_file(source_path=source, output_root=output_root)

    assert result.ok is True
    assert result.artifact_kind == "text"
    assert result.output_root == str(output_root.resolve())
    assert result.local_source_path == str(source)
    assert result.mime_type == "text/markdown"
    assert result.artifact_view is not None
    assert result.artifact_view.phase == "acquired"
    assert result.artifact_view.artifact_kind == "text"
    assert result.artifact_view.primary_artifact_path == result.primary_artifact_path
    assert Path(result.primary_artifact_path).resolve().is_relative_to(output_root.resolve())  # type: ignore[arg-type]


def test_download_rejects_symlinked_output_subdir_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    output_root = tmp_path / "draft"
    output_root.mkdir()
    os.symlink(outside, output_root / "original")
    calls: list[str] = []

    def downloader(url: str, path: Path, headers: dict[str, str], timeout: int) -> dict[str, str]:
        calls.append(url)
        path.write_text("outside write", encoding="utf-8")
        return {}

    result = MaterialAcquisitionExtractionClient(downloader=downloader).fetch_web_page(
        "https://example.com/paper",
        output_root=output_root,
    )

    assert result.ok is False
    assert result.issue_code == "artifact_path_escape"
    assert result.artifact_view is not None
    assert result.artifact_view.output_root == str(output_root.resolve())
    assert calls == []
    assert not any(outside.iterdir())


def test_extraction_rejects_symlinked_normalized_subdir_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    output_root = tmp_path / "draft"
    output_root.mkdir()
    os.symlink(outside, output_root / "normalized")
    html_path = tmp_path / "page.html"
    html_path.write_text("<p>Hello</p>", encoding="utf-8")

    result = MaterialAcquisitionExtractionClient().extract_web_main_text(html_path=html_path, output_root=output_root)

    assert result.ok is False
    assert result.issue_code == "artifact_path_escape"
    assert result.artifact_view is not None
    assert result.artifact_view.phase == "extracted"
    assert result.artifact_view.output_root == str(output_root.resolve())
    assert not any(outside.iterdir())


def test_web_fetch_and_extract_views_normalize_metadata_and_preview(tmp_path: Path) -> None:
    def downloader(url: str, path: Path, headers: dict[str, str], timeout: int) -> dict[str, str]:
        path.write_text("<html><body><p>Hello <b>world</b></p><p>Second line</p></body></html>", encoding="utf-8")
        return {"Content-Type": "text/html; charset=utf-8"}

    client = MaterialAcquisitionExtractionClient(
        MaterialAcquisitionConfig(text_preview_chars=8),
        downloader=downloader,
    )
    output_root = tmp_path / "draft"

    acquired = client.fetch_web_page("https://example.com/article", output_root=output_root)
    extracted = client.extract_web_main_text(html_path=Path(acquired.primary_artifact_path), output_root=output_root)

    assert acquired.ok is True
    assert acquired.artifact_kind == "web_page"
    assert acquired.source_url == "https://example.com/article"
    assert acquired.mime_type == "text/html"
    assert acquired.artifact_view is not None
    assert acquired.artifact_view.source_url == "https://example.com/article"
    assert acquired.artifact_view.mime_type == "text/html"
    assert extracted.ok is True
    assert extracted.material_kind == "markdown"
    assert extracted.mime_type in {"text/markdown", "text/x-markdown"}
    assert extracted.text_preview == "Hello wo"
    assert extracted.artifact_view is not None
    assert extracted.artifact_view.text_preview == "Hello wo"
    assert Path(extracted.primary_text_path).resolve().is_relative_to(output_root.resolve())  # type: ignore[arg-type]


def test_pdf_extraction_requires_readable_postcondition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")

    def fake_run(command, text: bool, stdout, stderr, check: bool):
        Path(command[-1]).write_text("   \n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = MaterialAcquisitionExtractionClient().extract_pdf_text(
        pdf_path=pdf,
        output_root=tmp_path / "draft",
    )

    assert result.ok is False
    assert result.issue_code == "empty_text"
