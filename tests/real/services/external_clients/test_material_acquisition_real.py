from __future__ import annotations

import io
import os
import shutil
import tarfile
import zipfile
from pathlib import Path

import pytest

from lean_constellation.services.external_clients import MaterialAcquisitionExtractionClient


def _write_minimal_pdf(path: Path, text: str) -> None:
    try:
        from reportlab.pdfgen import canvas
    except Exception as exc:  # noqa: BLE001 - optional real-test fixture dependency.
        pytest.skip(f"`reportlab` is required to generate a local PDF fixture: {exc}")
    pdf = canvas.Canvas(str(path))
    pdf.drawString(72, 720, text)
    pdf.save()


@pytest.mark.real
def test_material_acquisition_real_local_fixture_extraction(tmp_path: Path) -> None:
    client = MaterialAcquisitionExtractionClient()
    draft = tmp_path / "draft"
    html_source = tmp_path / "source.html"
    text_source = tmp_path / "notes.txt"
    tex_source = tmp_path / "paper.tex"
    local_dir = tmp_path / "local-dir"
    html_source.write_text(
        "<html><head><style>.x{}</style></head><body><script>x</script><p>Hello <b>world</b></p></body></html>",
        encoding="utf-8",
    )
    text_source.write_text("A readable local note.\nSecond line.\n", encoding="utf-8")
    tex_source.write_text(
        "\\documentclass{article}\\begin{document}Main theorem text.\\end{document}",
        encoding="utf-8",
    )
    local_dir.mkdir()
    (local_dir / "a.txt").write_text("A", encoding="utf-8")

    acquired_html = client.import_local_file(source_path=html_source, output_root=draft)
    extracted_html = client.extract_web_main_text(html_path=Path(acquired_html.primary_artifact_path), output_root=draft)
    normalized_text = client.normalize_text_material(input_path=text_source, output_root=draft)
    extracted_tex = client.extract_arxiv_tex(source_root_or_archive=tex_source, output_root=draft)
    imported_dir = client.import_local_dir(source_path=local_dir, output_root=draft)
    validation = client.validate_readable_text(Path(extracted_html.primary_text_path))

    assert acquired_html.ok is True
    assert acquired_html.content_hash
    assert acquired_html.artifact_view is not None
    assert acquired_html.artifact_view.phase == "acquired"
    assert acquired_html.artifact_view.output_root == str(draft.resolve())
    assert acquired_html.artifact_view.mime_type == "text/html"
    assert extracted_html.ok is True
    assert extracted_html.artifact_view is not None
    assert extracted_html.artifact_view.phase == "extracted"
    assert extracted_html.artifact_view.output_root == str(draft.resolve())
    assert "Hello world" in (extracted_html.text_preview or "")
    assert normalized_text.ok is True
    assert normalized_text.artifact_view is not None
    assert normalized_text.material_kind == "text"
    assert "Second line" in (normalized_text.text_preview or "")
    assert extracted_tex.ok is True
    assert extracted_tex.artifact_view is not None
    assert extracted_tex.material_kind == "tex_source"
    assert Path(extracted_tex.primary_text_path).name == "paper.tex"
    assert imported_dir.ok is True
    assert imported_dir.artifact_view is not None
    assert imported_dir.artifact_kind == "directory"
    assert len(imported_dir.artifact_paths) == 1
    assert validation.ok is True


@pytest.mark.real
def test_material_acquisition_real_archive_extraction_and_safety(tmp_path: Path) -> None:
    client = MaterialAcquisitionExtractionClient()
    safe_zip = tmp_path / "safe.zip"
    safe_tar = tmp_path / "safe.tar"
    bad_zip = tmp_path / "bad.zip"
    bad_tar = tmp_path / "bad.tar"

    with zipfile.ZipFile(safe_zip, "w") as archive:
        archive.writestr("macros.tex", "\\newcommand{\\x}{x}")
        archive.writestr("main.tex", "\\documentclass{article}\\begin{document}Zip main.\\end{document}")
    payload = b"\\documentclass{article}\\begin{document}Tar main.\\end{document}"
    with tarfile.open(safe_tar, "w") as archive:
        info = tarfile.TarInfo("main.tex")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    with zipfile.ZipFile(bad_zip, "w") as archive:
        archive.writestr("../evil.tex", "\\begin{document}bad\\end{document}")
    with tarfile.open(bad_tar, "w") as archive:
        info = tarfile.TarInfo("../evil.tex")
        bad_payload = b"\\begin{document}bad\\end{document}"
        info.size = len(bad_payload)
        archive.addfile(info, io.BytesIO(bad_payload))

    zip_ok = client.extract_arxiv_tex(source_root_or_archive=safe_zip, output_root=tmp_path / "zip-ok")
    tar_ok = client.extract_arxiv_tex(source_root_or_archive=safe_tar, output_root=tmp_path / "tar-ok")
    zip_bad = client.extract_arxiv_tex(source_root_or_archive=bad_zip, output_root=tmp_path / "zip-bad")
    tar_bad = client.extract_arxiv_tex(source_root_or_archive=bad_tar, output_root=tmp_path / "tar-bad")

    assert zip_ok.ok is True
    assert "Zip main" in (zip_ok.text_preview or "")
    assert tar_ok.ok is True
    assert "Tar main" in (tar_ok.text_preview or "")
    assert zip_bad.ok is False
    assert zip_bad.issue_code == "arxiv_tex_extract_failed"
    assert tar_bad.ok is False
    assert tar_bad.issue_code == "arxiv_tex_extract_failed"
    assert (tmp_path / "evil.tex").exists() is False


@pytest.mark.real
def test_material_acquisition_real_pdf_fixture_when_configured(tmp_path: Path) -> None:
    raw_pdf = os.environ.get("LEAN_CONSTELLATION_REAL_PDF_FIXTURE")
    if shutil.which("pdftotext") is None:
        pytest.skip("`pdftotext` is required for PDF extraction.")
    if raw_pdf:
        pdf_path = Path(raw_pdf).expanduser().resolve()
        if not pdf_path.is_file():
            pytest.skip(f"LEAN_CONSTELLATION_REAL_PDF_FIXTURE is not a file: {pdf_path}")
    else:
        pdf_path = tmp_path / "generated.pdf"
        _write_minimal_pdf(pdf_path, "Generated PDF text for material acquisition.")
    client = MaterialAcquisitionExtractionClient()

    extracted = client.extract_pdf_text(pdf_path=pdf_path, output_root=tmp_path / "pdf")

    assert extracted.ok, extracted.summary
    assert extracted.artifact_view is not None
    assert extracted.artifact_view.output_root == str((tmp_path / "pdf").resolve())
    assert extracted.material_kind == "text"
    assert extracted.primary_text_path
    extracted_text = Path(extracted.primary_text_path).read_text(encoding="utf-8", errors="replace")
    assert "Generated PDF text" in extracted_text or raw_pdf
    assert client.validate_readable_text(Path(extracted.primary_text_path)).ok


@pytest.mark.real
def test_material_acquisition_real_network_opt_in(tmp_path: Path) -> None:
    if os.environ.get("LEAN_CONSTELLATION_REAL_NETWORK") != "1":
        pytest.skip("Set LEAN_CONSTELLATION_REAL_NETWORK=1 to run live material network acquisition.")
    client = MaterialAcquisitionExtractionClient()
    url = os.environ.get("LEAN_CONSTELLATION_REAL_WEB_URL", "https://example.com/")

    page = client.fetch_web_page(url, output_root=tmp_path / "web")
    assert page.ok, page.summary
    assert page.artifact_view is not None
    assert page.artifact_view.source_url == url
    extracted = client.extract_web_main_text(html_path=Path(page.primary_artifact_path), output_root=tmp_path / "web")
    assert extracted.ok, extracted.summary
    assert extracted.artifact_view is not None
    assert extracted.text_preview

    arxiv_id = os.environ.get("LEAN_CONSTELLATION_REAL_ARXIV_ID")
    if arxiv_id:
        source = client.fetch_arxiv_source(arxiv_id, os.environ.get("LEAN_CONSTELLATION_REAL_ARXIV_VERSION"), output_root=tmp_path / "arxiv")
        assert source.ok, source.summary
