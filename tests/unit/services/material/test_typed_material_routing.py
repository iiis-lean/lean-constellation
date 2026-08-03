from __future__ import annotations

import io
import tarfile
from pathlib import Path

from tests.unit_services_helpers import make_runtime


def _write_extensionless_tex_archive(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b"\\documentclass{article}\n\\begin{document}\nMain theorem.\n\\end{document}\n"
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("main.tex")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))


def test_resource_and_source_share_extensionless_arxiv_archive_routing(tmp_path: Path) -> None:
    service = make_runtime().material
    source_root = tmp_path / ".lean_constellation" / "source"
    source_artifact = source_root / "original" / "2407.12253-source"
    _write_extensionless_tex_archive(source_artifact)

    draft = service.allocate_resource_draft(tmp_path, target="2407.12253")
    assert draft.ok and draft.value is not None
    resource_artifact = Path(draft.value.original_dir) / "2407.12253-source"
    resource_artifact.parent.mkdir(parents=True, exist_ok=True)
    resource_artifact.write_bytes(source_artifact.read_bytes())

    source_extracted = service.extract_source_artifact(
        tmp_path,
        artifact_ref="original/2407.12253-source",
        acquisition_kind="arxiv_source",
        mime_type="application/gzip",
    )
    resource_extracted = service.extract_resource_artifact(
        tmp_path,
        draft_id=draft.value.draft.draft_id,
        artifact_ref="original/2407.12253-source",
        acquisition_kind="arxiv_source",
        mime_type="application/gzip",
    )

    assert source_extracted.ok and source_extracted.value is not None
    assert resource_extracted.ok and resource_extracted.value is not None
    assert source_extracted.value.resolved_artifact_kind == "tex_source_archive"
    assert resource_extracted.value.resolved_artifact_kind == "tex_source_archive"
    assert source_extracted.value.extraction_kind == "tex_source"
    assert resource_extracted.value.extraction_kind == "tex_source"
    assert source_extracted.value.primary_material_ref is not None
    assert resource_extracted.value.primary_material_ref is not None
    manifest = service.refresh_resource_draft_manifest(
        tmp_path,
        draft_id=draft.value.draft.draft_id,
    )
    assert manifest.ok and manifest.value is not None
    assert manifest.value.canonical_normalized_entry == resource_extracted.value.primary_material_ref
    assert manifest.value.extraction_relations[0].source_artifact_path == "original/2407.12253-source"


def test_explicit_text_normalize_cannot_override_pdf_magic(tmp_path: Path) -> None:
    service = make_runtime().material
    source_root = tmp_path / ".lean_constellation" / "source" / "original"
    source_root.mkdir(parents=True)
    (source_root / "renamed.txt").write_bytes(b"%PDF-1.4\nfixture")

    result = service.extract_source_artifact(
        tmp_path,
        artifact_ref="original/renamed.txt",
        extraction_kind="text_normalize",
    )

    assert not result.ok
    assert result.issues[0].kind == "material_extraction_kind_mismatch"
    assert not (tmp_path / ".lean_constellation" / "source" / "normalized" / "renamed.txt").exists()
