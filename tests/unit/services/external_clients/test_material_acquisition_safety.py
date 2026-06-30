from __future__ import annotations

import os
from pathlib import Path

import pytest

from lean_constellation.services.external_clients import MaterialAcquisitionConfig, MaterialAcquisitionExtractionClient


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
