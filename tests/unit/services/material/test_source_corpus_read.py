import json
import struct
import subprocess

import pytest

from tests.unit_services_helpers import make_runtime

from pathlib import Path

from lean_constellation.services.external_clients import (
    AcquiredArtifactResult,
    ExtractedMaterialResult,
    MaterialAcquisitionExtractionClient,
    MaterialTarget,
    ResolvedArtifactKindView,
)
from lean_constellation.services.material import MaterialService
from lean_constellation.services.material import source_corpus as source_corpus_module
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode, SourceMaterialInput
from lean_constellation.domain.refs import SourceRef


def test_source_pdf_page_preview_validates_corpus_and_reuses_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = make_runtime()
    source_root = tmp_path / ".lean_constellation" / "source_draft" / "_work"
    source_root.mkdir(parents=True)
    (source_root / "paper.pdf").write_bytes(b"%PDF-1.4\nfixture")
    (source_root / "notes.txt").write_text("not a pdf\n", encoding="utf-8")
    calls: list[str] = []

    def fake_run(command, **kwargs):  # noqa: ANN001
        del kwargs
        calls.append(command[0])
        if command[0] == "pdfinfo":
            return subprocess.CompletedProcess(command, 0, stdout="Pages:          2\n", stderr="")
        output = Path(f"{command[-1]}.png")
        output.write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 640, 480)
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(source_corpus_module.subprocess, "run", fake_run)

    first = runtime.material.render_source_pdf_page(
        tmp_path,
        path="_work/paper.pdf",
        page_number=2,
    )
    second = runtime.material.render_source_pdf_page(
        tmp_path,
        path="_work/paper.pdf",
        page_number=2,
    )
    non_pdf = runtime.material.render_source_pdf_page(tmp_path, path="_work/notes.txt", page_number=1)
    escaped = runtime.material.render_source_pdf_page(tmp_path, path="../paper.pdf", page_number=1)
    out_of_range = runtime.material.render_source_pdf_page(tmp_path, path="_work/paper.pdf", page_number=3)

    assert first.ok and first.value is not None
    assert first.value.width == 640 and first.value.height == 480
    assert first.value.cache_reused is False
    assert second.ok and second.value is not None and second.value.cache_reused is True
    assert calls.count("pdftoppm") == 1
    assert not non_pdf.ok and non_pdf.issues[0].kind == "source_pdf_type_mismatch"
    assert not escaped.ok and escaped.issues[0].kind == "source_pdf_preview_path_escape"
    assert not out_of_range.ok and out_of_range.issues[0].kind == "source_pdf_page_out_of_range"
    assert first.value.image_path.startswith(str(source_root / "previews"))
    assert not (tmp_path / ".agent_runtime" / "material_previews").exists()


class FakeMaterialClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def normalize_target(self, target: str) -> MaterialTarget:
        if target.startswith("arxiv:"):
            return MaterialTarget(kind="arxiv", value=target.removeprefix("arxiv:"), version=None)
        if target.startswith("https://") or target.startswith("http://"):
            return MaterialTarget(kind="web_url", value=target)
        path = Path(target)
        if path.is_dir():
            return MaterialTarget(kind="local_dir", value=str(path))
        return MaterialTarget(kind="local_file", value=str(path))

    def resolve_artifact_kind(
        self,
        path: Path,
        *,
        acquisition_kind: str | None = None,
        mime_type: str | None = None,
        requested_extraction_kind: str | None = None,
    ) -> ResolvedArtifactKindView:
        del acquisition_kind, mime_type
        suffix = path.suffix.lower()
        if path.is_dir() or suffix in {".tex", ".tar", ".gz", ".tgz", ".zip"}:
            kind, automatic = "tex_source_archive", "tex_source"
        elif suffix == ".pdf":
            kind, automatic = "pdf", "pdf_text"
        elif suffix in {".html", ".htm"}:
            kind, automatic = "html", "html_main_text"
        else:
            kind, automatic = "plain_text", "text_normalize"
        compatible = requested_extraction_kind is None or requested_extraction_kind == automatic
        return ResolvedArtifactKindView(
            kind=kind,  # type: ignore[arg-type]
            extraction_kind=(requested_extraction_kind or automatic),  # type: ignore[arg-type]
            compatible=compatible,
            summary=f"Resolved fake artifact as {kind}.",
            issue_code=None if compatible else "material_extraction_kind_mismatch",
        )

    def validate_readable_text(self, path: Path):
        return MaterialAcquisitionExtractionClient().validate_readable_text(path)

    def fetch_arxiv_source(self, arxiv_id: str, version: str | None, *, output_root: Path) -> AcquiredArtifactResult:
        self.calls.append(("fetch_arxiv_source", arxiv_id))
        path = output_root / "original" / f"{arxiv_id}.tar"
        return self._acquired("arxiv", arxiv_id, version, path, "Fetched fake arXiv source.")

    def fetch_arxiv_pdf(self, arxiv_id: str, version: str | None, *, output_root: Path) -> AcquiredArtifactResult:
        self.calls.append(("fetch_arxiv_pdf", arxiv_id))
        path = output_root / "original" / f"{arxiv_id}.pdf"
        return self._acquired("arxiv", arxiv_id, version, path, "Fetched fake arXiv PDF.")

    def fetch_web_page(self, url: str, *, output_root: Path) -> AcquiredArtifactResult:
        self.calls.append(("fetch_web_page", url))
        path = output_root / "original" / "page.html"
        return self._acquired("web_url", url, None, path, "Fetched fake web page.")

    def import_local_file(self, source_path: Path, *, output_root: Path) -> AcquiredArtifactResult:
        self.calls.append(("import_local_file", str(source_path)))
        if not source_path.exists():
            return AcquiredArtifactResult(
                ok=False,
                target=MaterialTarget(kind="local_file", value=str(source_path)),
                summary="missing",
                issue_code="missing_local_file",
            )
        path = output_root / "original" / source_path.name
        return self._acquired("local_file", str(source_path), None, path, "Imported fake local file.")

    def import_local_dir(self, source_path: Path, output_root: Path) -> AcquiredArtifactResult:
        self.calls.append(("import_local_dir", str(source_path)))
        path = output_root / "original" / source_path.name / "copied.txt"
        return self._acquired("local_dir", str(source_path), None, path, "Imported fake local dir.")

    def extract_pdf_text(self, *, pdf_path: Path, output_root: Path) -> ExtractedMaterialResult:
        self.calls.append(("extract_pdf_text", str(pdf_path)))
        return self._extracted(pdf_path, output_root / "normalized" / f"{pdf_path.stem}.txt", "PDF text")

    def extract_web_main_text(
        self,
        *,
        html_path: Path,
        output_root: Path,
        acquisition_kind: str | None = None,
        mime_type: str | None = None,
    ) -> ExtractedMaterialResult:
        self.calls.append(("extract_web_main_text", str(html_path)))
        return self._extracted(html_path, output_root / "normalized" / f"{html_path.stem}.md", "HTML text")

    def extract_arxiv_tex(self, *, source_root_or_archive: Path, output_root: Path) -> ExtractedMaterialResult:
        self.calls.append(("extract_arxiv_tex", str(source_root_or_archive)))
        return self._extracted(source_root_or_archive, output_root / "normalized" / f"{source_root_or_archive.stem}.tex", "TeX text")

    def normalize_text_material(self, *, input_path: Path, output_root: Path) -> ExtractedMaterialResult:
        self.calls.append(("normalize_text_material", str(input_path)))
        if not input_path.exists():
            return ExtractedMaterialResult(
                ok=False,
                source_artifact_path=str(input_path),
                summary="missing",
                issue_code="missing_text",
            )
        return self._extracted(input_path, output_root / "normalized" / f"{input_path.stem}.txt", input_path.read_text(encoding="utf-8"))

    def _acquired(
        self,
        kind: str,
        value: str,
        version: str | None,
        path: Path,
        summary: str,
    ) -> AcquiredArtifactResult:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{summary}\n", encoding="utf-8")
        return AcquiredArtifactResult(
            ok=True,
            target=MaterialTarget(kind=kind, value=value, version=version),  # type: ignore[arg-type]
            artifact_paths=[str(path)],
            primary_artifact_path=str(path),
            artifact_kind={
                ".pdf": "arxiv_pdf",
                ".html": "web_page",
                ".tar": "arxiv_source",
            }.get(path.suffix.lower(), "local_file"),
            metadata={"provider": "fake"},
            content_hash="fake-hash",
            summary=summary,
        )

    def _extracted(self, source: Path, path: Path, text: str) -> ExtractedMaterialResult:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return ExtractedMaterialResult(
            ok=True,
            source_artifact_path=str(source),
            extracted_paths=[str(path)],
            primary_text_path=str(path),
            text_preview=text,
            metadata={"provider": "fake"},
            summary="Extracted fake material.",
        )


def _fake_material_service() -> tuple[MaterialService, FakeMaterialClient]:
    fake = FakeMaterialClient()
    runtime = make_runtime(external_overrides={"material_acquisition": fake})
    return runtime.material, fake


def _write_source_prepare_input(runtime, repo_root: Path, targets: list[str]) -> None:
    written = runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Prepare exact source fixtures.",
            source_corpus_mode=SourceCorpusMode.PREPARE,
            source_material_inputs=[
                SourceMaterialInput(
                    target=target,
                    included_scope="Complete unit-test fixture.",
                    role="primary_source",
                )
                for target in targets
            ],
        ),
    )
    assert written.ok


def _source_entry_text(*, main_path: str = "notes/section.md") -> str:
    return (
        "# Source corpus\n\n"
        "A static unit-test source corpus.\n\n"
        f"File inventory: README.md and `{main_path}`.\n"
        f"Reading order: start with this entry, then read `{main_path}` as the main material.\n"
        f"Main material: `{main_path}` contains the formal background used for indexing.\n"
        "Included scope: complete fixture.\n"
        "No representation limits affect reading this fixture.\n"
    )


def test_source_acquisition_uses_draft_work_and_preserves_canonical_destination(tmp_path: Path) -> None:
    fake = FakeMaterialClient()
    runtime = make_runtime(external_overrides={"material_acquisition": fake})
    local = tmp_path / "note.md"
    local.write_text(_source_entry_text(main_path="original/raw.md"), encoding="utf-8")
    written = runtime.repo_workspace.preparation.write_preparation_input(
        tmp_path,
        input=RepoPreparationInput(
            goal="Use a custom source root.",
            source_corpus_mode=SourceCorpusMode.PREPARE,
            source_corpus_relpath="custom_sources",
            source_material_inputs=[
                SourceMaterialInput(
                    target=str(local.resolve()),
                    included_scope="Complete local note.",
                    role="primary_source",
                )
            ],
        ),
    )
    assert written.ok

    imported = runtime.material.import_source_material(tmp_path, source_path=str(local), as_name="raw.md")
    normalized = runtime.material.normalize_source_text_material(tmp_path, material_ref="_work/original/raw.md")
    draft_root = tmp_path / ".lean_constellation" / "source_draft"
    draft_root.mkdir(parents=True, exist_ok=True)
    (draft_root / "article").mkdir()
    (draft_root / "article" / "raw.md").write_text("faithful source text\n", encoding="utf-8")
    (draft_root / "README.md").write_text(
        _source_entry_text(main_path="article/raw.md"),
        encoding="utf-8",
    )
    gate = runtime.material.check_source_corpus_draft(
        tmp_path,
        relpath=".lean_constellation/source_draft",
        entry_path="README.md",
    )

    assert imported.ok and imported.value is not None, imported.issues
    assert normalized.ok and normalized.value is not None, normalized.issues
    assert gate.ok and gate.value is not None and gate.value.passed
    assert (draft_root / "_work" / "original" / "raw.md").is_file()
    assert (draft_root / "_work" / "normalized" / "raw.txt").is_file()
    assert not (tmp_path / ".lean_constellation" / "source" / "original" / "raw.md").exists()


def _write_source(repo_root: Path) -> None:
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(_source_entry_text(), encoding="utf-8")
    notes = source_root / "notes"
    notes.mkdir()
    (notes / "section.md").write_text("alpha\nbeta theorem\ngamma\n", encoding="utf-8")


def test_source_corpus_prepare_manifest_and_read_search(tmp_path: Path) -> None:
    _write_source(tmp_path)
    service = make_runtime().material

    draft = service.check_source_corpus_draft(tmp_path, entry_path="README.md")
    assert draft.ok
    assert draft.value is not None
    assert draft.value.passed

    prepared = service.submit_source_corpus_prepared(
        tmp_path,
        entry_path="README.md",
        overview="A small source corpus.",
        preparation_summary="Prepared from local markdown files.",
    )
    assert prepared.ok
    assert prepared.value is not None
    assert prepared.value.manifest.entry_path == "README.md"
    assert (tmp_path / ".lean_constellation" / "source_corpus" / "manifest.json").exists()

    manifest = service.source_corpus.get_source_corpus_manifest(tmp_path)
    assert manifest.ok
    assert manifest.value is not None
    assert {item.path for item in manifest.value.files} == {"README.md", "notes/section.md"}

    read = service.read_source_range(tmp_path, path="notes/section.md", start_line=2, end_line=2, context_lines=1)
    assert read.ok
    assert read.value is not None
    assert "2: beta theorem" in read.value.text_with_line_numbers
    assert "1: alpha" in (read.value.before_context or "")
    assert "3: gamma" in (read.value.after_context or "")

    search = service.search_material_text(tmp_path, query="theorem", scope="source")
    assert search.ok
    assert search.value is not None
    assert len(search.value.hits) == 1
    assert search.value.hits[0].reusable_ref_fields["path"] == "notes/section.md"

    valid = service.material_read.validate_source_range(
        tmp_path,
        path="notes/section.md",
        start_line=1,
        end_line=2,
    )
    assert valid.ok
    assert valid.value is not None
    assert valid.value.valid

    preview = service.read_material_ref(
        tmp_path,
        ref={"kind": "source", "path": "notes/section.md", "start_line": 1, "end_line": 1},
    )
    assert preview.ok
    assert preview.value is not None
    assert "1: alpha" in preview.value.text_with_line_numbers


def test_source_corpus_gate_rejects_missing_entry(tmp_path: Path) -> None:
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "notes.md").write_text("content\n", encoding="utf-8")
    service = make_runtime().material

    gate = service.check_source_corpus_draft(tmp_path, entry_path="README.md")
    assert gate.ok
    assert gate.value is not None
    assert not gate.value.passed
    assert gate.value.issues[0].kind == "source_corpus_entry_not_found"

    submit = service.submit_source_corpus_prepared(
        tmp_path,
        entry_path="README.md",
        overview="Source.",
        preparation_summary="Tried to prepare.",
    )
    assert not submit.ok
    assert submit.issues[0].kind == "source_corpus_entry_not_found"


def test_source_corpus_gate_rejects_unexplained_single_note(tmp_path: Path) -> None:
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "note.txt").write_text("loose note\n", encoding="utf-8")
    service = make_runtime().material

    inferred = service.check_source_corpus_draft(tmp_path)
    explicit = service.check_source_corpus_draft(tmp_path, entry_path="note.txt")

    assert inferred.ok and inferred.value is not None
    assert not inferred.value.passed
    assert "source_corpus_entry_not_explanatory" in {issue.kind for issue in inferred.value.issues}
    assert explicit.ok and explicit.value is not None
    assert not explicit.value.passed
    assert "source_corpus_readme_missing" in {issue.kind for issue in explicit.value.issues}


def test_static_readme_passes_without_workflow_provenance(tmp_path: Path) -> None:
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text("# Source\n\nMain section overview. No missing material.\n", encoding="utf-8")
    (source_root / "notes.md").write_text("source notes\n", encoding="utf-8")
    service = make_runtime().material

    gate = service.check_source_corpus_draft(tmp_path, entry_path="README.md")

    assert gate.ok and gate.value is not None
    assert gate.value.passed


def test_source_corpus_gate_accepts_explained_single_file_entry(tmp_path: Path) -> None:
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "paper.md").write_text(
        "Main theorem statement and proof outline.\n",
        encoding="utf-8",
    )
    (source_root / "README.md").write_text(
        _source_entry_text(main_path="paper.md"),
        encoding="utf-8",
    )
    service = make_runtime().material

    gate = service.check_source_corpus_draft(tmp_path, entry_path="paper.md")

    assert gate.ok and gate.value is not None
    assert gate.value.passed


def test_source_corpus_gate_rejects_pdf_magic_renamed_as_text_entry(tmp_path: Path) -> None:
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_bytes(b"%PDF-1.4\nfixture")
    service = make_runtime().material

    gate = service.check_source_corpus_draft(tmp_path, entry_path="README.md")

    assert gate.ok and gate.value is not None
    assert not gate.value.passed
    assert "source_corpus_entry_not_readable" in {issue.kind for issue in gate.value.issues}


def test_material_service_uses_injected_fake_provider_for_acquire_and_extract(tmp_path: Path) -> None:
    service, fake = _fake_material_service()
    _write_source_prepare_input(service.runtime, tmp_path, ["https://example.test/paper"])

    acquired = service.acquire_source_material(tmp_path, target="https://example.test/paper", preferred_kind="web_page")
    assert acquired.ok
    assert acquired.value is not None
    assert acquired.value.primary_artifact_ref == "_work/original/page.html"
    assert acquired.value.acquisition_kind == "web_page"

    extracted = service.extract_source_artifact(
        tmp_path,
        artifact_ref="_work/original/page.html",
        acquisition_kind=acquired.value.acquisition_kind,
        mime_type=acquired.value.mime_type,
    )
    assert extracted.ok
    assert extracted.value is not None
    assert extracted.value.primary_material_ref == "_work/normalized/page.md"
    assert extracted.value.resolved_artifact_kind == "html"
    assert extracted.value.extraction_kind == "html_main_text"
    assert [call[0] for call in fake.calls] == ["fetch_web_page", "extract_web_main_text"]


@pytest.mark.parametrize(
    "request_target",
    [
        "arXiv:2401.00001",
        "doi:10.1000/unit-test",
        "A structured request for the paper by its title",
    ],
)
def test_acquire_source_accepts_resolved_locator_for_request(
    tmp_path: Path,
    request_target: str,
) -> None:
    service, fake = _fake_material_service()
    _write_source_prepare_input(
        service.runtime,
        tmp_path,
        [request_target],
    )

    acquired = service.acquire_source_material(
        tmp_path,
        target="https://example.test/resolved-paper",
        preferred_kind="web_page",
    )

    assert acquired.ok and acquired.value is not None
    assert acquired.value.primary_artifact_ref == "_work/original/page.html"
    assert fake.calls == [("fetch_web_page", "https://example.test/resolved-paper")]


def test_acquire_source_material_fake_provider_branches_and_kind_gate(tmp_path: Path) -> None:
    service, fake = _fake_material_service()
    local_file = tmp_path / "input.txt"
    local_file.write_text("input\n", encoding="utf-8")
    local_dir = tmp_path / "input-dir"
    local_dir.mkdir()
    _write_source_prepare_input(
        service.runtime,
        tmp_path,
        [
            "arxiv:2401.00001",
            "https://example.test/page",
            str(local_file),
            str(local_dir),
        ],
    )

    arxiv_source = service.acquire_source_material(tmp_path, target="arxiv:2401.00001", preferred_kind="arxiv_source")
    arxiv_pdf = service.acquire_source_material(tmp_path, target="arxiv:2401.00001", preferred_kind="arxiv_pdf")
    web_page = service.acquire_source_material(tmp_path, target="https://example.test/page")
    imported_file = service.acquire_source_material(tmp_path, target=str(local_file), preferred_kind="local_file")
    imported_dir = service.acquire_source_material(tmp_path, target=str(local_dir), preferred_kind="local_dir")
    mismatch = service.acquire_source_material(tmp_path, target="https://example.test/page", preferred_kind="arxiv_pdf")

    assert arxiv_source.ok and arxiv_pdf.ok and web_page.ok and imported_file.ok and imported_dir.ok
    assert not mismatch.ok
    assert mismatch.issues[0].kind == "preferred_kind_target_mismatch"
    assert [call[0] for call in fake.calls] == [
        "fetch_arxiv_source",
        "fetch_arxiv_pdf",
        "fetch_web_page",
        "import_local_file",
        "import_local_dir",
    ]


def test_extract_source_artifact_fake_provider_branches_and_invalid_ref(tmp_path: Path) -> None:
    service, fake = _fake_material_service()
    source_root = tmp_path / ".lean_constellation" / "source_draft" / "_work" / "original"
    source_root.mkdir(parents=True)
    for name in ("paper.pdf", "page.html", "paper.tex", "notes.txt"):
        (source_root / name).write_text("payload\n", encoding="utf-8")

    pdf = service.extract_source_artifact(tmp_path, artifact_ref="_work/original/paper.pdf")
    html = service.extract_source_artifact(tmp_path, artifact_ref="_work/original/page.html")
    tex = service.extract_source_artifact(tmp_path, artifact_ref="_work/original/paper.tex")
    text = service.extract_source_artifact(tmp_path, artifact_ref="_work/original/notes.txt")
    invalid = service.extract_source_artifact(tmp_path, artifact_ref="../outside.txt")

    assert pdf.ok and html.ok and tex.ok and text.ok
    assert not invalid.ok
    assert invalid.issues[0].kind == "source_artifact_ref_invalid"
    assert [call[0] for call in fake.calls] == [
        "extract_pdf_text",
        "extract_web_main_text",
        "extract_arxiv_tex",
        "normalize_text_material",
    ]


def test_import_source_material_success_missing_and_safe_filename(tmp_path: Path) -> None:
    service = make_runtime().material
    local = tmp_path / "paper draft.md"
    local.write_text("paper\n", encoding="utf-8")
    _write_source_prepare_input(service.runtime, tmp_path, [str(local.resolve())])

    imported = service.import_source_material(tmp_path, source_path=str(local))
    missing = service.import_source_material(tmp_path, source_path=str(tmp_path / "missing.md"))
    unsafe_name = service.import_source_material(tmp_path, source_path=str(local), as_name="bad/name.md")

    assert imported.ok
    assert imported.value is not None
    assert imported.value.primary_artifact_ref == "_work/original/paper_draft.md"
    assert not missing.ok
    assert missing.issues[0].kind == "missing_local_file"
    assert not unsafe_name.ok
    assert unsafe_name.issues[0].kind == "unsafe_source_filename"


def test_import_source_accepts_resolved_local_path(tmp_path: Path) -> None:
    service = make_runtime().material
    local = tmp_path / "resolved-paper.md"
    local.write_text("paper\n", encoding="utf-8")
    _write_source_prepare_input(
        service.runtime,
        tmp_path,
        ["The local manuscript identified by its title rather than an exact path"],
    )

    imported = service.import_source_material(tmp_path, source_path=str(local))

    assert imported.ok and imported.value is not None
    assert imported.value.primary_artifact_ref == "_work/original/resolved-paper.md"


def test_normalize_source_text_material_success_missing_and_invalid_ref(tmp_path: Path) -> None:
    service, _fake = _fake_material_service()
    source_root = tmp_path / ".lean_constellation" / "source_draft" / "_work" / "original"
    source_root.mkdir(parents=True)
    (source_root / "note.txt").write_text("alpha\n", encoding="utf-8")

    normalized = service.normalize_source_text_material(tmp_path, material_ref="_work/original/note.txt")
    missing = service.normalize_source_text_material(tmp_path, material_ref="_work/original/missing.txt")
    invalid = service.normalize_source_text_material(tmp_path, material_ref="../outside.txt")

    assert normalized.ok
    assert normalized.value is not None
    assert normalized.value.primary_material_ref == "_work/normalized/note.txt"
    assert not missing.ok
    assert missing.issues[0].kind == "missing_text"
    assert not invalid.ok
    assert invalid.issues[0].kind == "source_material_ref_invalid"


def test_source_corpus_gate_missing_empty_unreadable_and_binary_entry(tmp_path: Path) -> None:
    service = make_runtime().material

    missing = service.check_source_corpus_draft(tmp_path)
    assert missing.ok
    assert missing.value is not None
    assert not missing.value.passed
    assert missing.value.issues[0].kind == "source_corpus_missing"

    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    empty = service.check_source_corpus_draft(tmp_path)
    assert empty.ok
    assert empty.value is not None
    assert {issue.kind for issue in empty.value.issues} >= {"source_corpus_empty", "source_corpus_no_readable_text"}

    (source_root / "image.bin").write_bytes(b"\x00\x01")
    (source_root / "notes.md").write_text("usable\n", encoding="utf-8")
    binary_entry = service.check_source_corpus_draft(tmp_path, entry_path="image.bin")
    assert binary_entry.ok
    assert binary_entry.value is not None
    assert not binary_entry.value.passed
    assert "source_corpus_entry_not_readable" in {issue.kind for issue in binary_entry.value.issues}


def test_source_corpus_submit_prepared_and_blocked_gates(tmp_path: Path) -> None:
    _write_source(tmp_path)
    service = make_runtime().material

    missing_summary = service.submit_source_corpus_prepared(
        tmp_path,
        entry_path="README.md",
        overview="",
        preparation_summary="Prepared.",
    )
    prepared = service.submit_source_corpus_prepared(
        tmp_path,
        entry_path="README.md",
        overview="Overview.",
        preparation_summary="Prepared.",
    )
    blocked_missing_reason = service.submit_source_corpus_blocked(tmp_path, reason=" ")
    blocked = service.submit_source_corpus_blocked(
        tmp_path,
        reason="Could not find requested source.",
        attempted_targets=["missing paper"],
        missing_materials=["paper"],
        suggested_next_action="Ask Coordinator for a clearer source target.",
    )

    assert not missing_summary.ok
    assert missing_summary.issues[0].kind == "missing_summary"
    assert prepared.ok
    assert prepared.value is not None
    assert prepared.value.prepared
    assert not blocked_missing_reason.ok
    assert blocked_missing_reason.issues[0].kind == "missing_blocked_reason"
    assert blocked.ok
    assert blocked.value is not None
    assert blocked.value.blocked
    assert blocked.value.attempted_targets == ["missing paper"]


def test_get_manifest_falls_back_to_scan_and_validate_source_ref_errors(tmp_path: Path) -> None:
    _write_source(tmp_path)
    service = make_runtime().material

    fallback = service.source_corpus.get_source_corpus_manifest(tmp_path)
    outside = service.source_corpus.validate_source_ref(tmp_path, path="../outside.md", start_line=1, end_line=1)
    missing = service.source_corpus.validate_source_ref(tmp_path, path="missing.md", start_line=1, end_line=1)
    invalid_range = service.source_corpus.validate_source_ref(tmp_path, path="README.md", start_line=100, end_line=101)

    assert fallback.ok
    assert fallback.value is not None
    assert fallback.value.created_from_mode == "scan"
    assert outside.ok and outside.value is not None
    assert outside.value.issue_code == "source_ref_outside_root"
    assert missing.ok and missing.value is not None
    assert missing.value.issue_code == "source_ref_file_missing"
    assert invalid_range.ok and invalid_range.value is not None
    assert invalid_range.value.issue_code == "source_ref_range_invalid"


def test_validate_source_ref_requires_current_manifest_membership_and_readable_text(tmp_path: Path) -> None:
    _write_source(tmp_path)
    (tmp_path / ".lean_constellation" / "source" / "original.bin").write_bytes(b"\x00\xff")
    service = make_runtime().material
    scanned = service.source_corpus.scan_source_corpus(tmp_path)
    assert scanned.ok and scanned.value is not None
    manifest_path = service.source_corpus._manifest_path(tmp_path)
    assert service.runtime.foundation.store.write_json_atomic(manifest_path, scanned.value).ok

    source_root = tmp_path / ".lean_constellation" / "source"
    (source_root / "added_after_manifest.md").write_text("late\n", encoding="utf-8")
    absent = service.validate_source_range(
        tmp_path,
        path="added_after_manifest.md",
        start_line=1,
        end_line=1,
    )
    binary = service.validate_source_range(
        tmp_path,
        path="original.bin",
        start_line=1,
        end_line=1,
    )

    assert absent.ok and absent.value is not None
    assert absent.value.issue_code == "source_ref_not_in_corpus"
    assert binary.ok and binary.value is not None
    assert binary.value.issue_code == "source_ref_not_readable"


def test_validate_source_refs_reuses_manifest_lookup_and_actual_line_count_per_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_source(tmp_path)
    service = make_runtime().material
    scanned = service.source_corpus.scan_source_corpus(tmp_path)
    assert scanned.ok and scanned.value is not None
    assert service.runtime.foundation.store.write_json_atomic(
        service.source_corpus._manifest_path(tmp_path),
        scanned.value,
    ).ok
    target = tmp_path / ".lean_constellation" / "source" / "notes" / "section.md"
    calls: list[Path] = []
    original_line_count = service.source_corpus._line_count

    def counted_line_count(path: Path) -> int:
        calls.append(path)
        return original_line_count(path)

    monkeypatch.setattr(service.source_corpus, "_line_count", counted_line_count)
    validated = service.source_corpus.validate_source_refs(
        tmp_path,
        refs=[
            SourceRef(path="notes/section.md", start_line=1, end_line=1),
            SourceRef(path="notes/section.md", start_line=2, end_line=2),
            SourceRef(path="notes/section.md", start_line=2, end_line=3),
        ],
    )

    assert validated.ok and validated.value is not None
    assert [item.valid for item in validated.value] == [True, True, True]
    assert calls == [target]


def test_check_target_in_source_corpus_matches_path_and_sha(tmp_path: Path) -> None:
    _write_source(tmp_path)
    service = make_runtime().material
    manifest = service.source_corpus.get_source_corpus_manifest(tmp_path)
    assert manifest.ok
    assert manifest.value is not None
    notes = next(item for item in manifest.value.files if item.path == "notes/section.md")

    by_path = service.source_corpus.check_target_in_source_corpus(tmp_path, canonical_locator="notes/section")
    by_sha = service.source_corpus.check_target_in_source_corpus(tmp_path, canonical_locator=notes.sha256 or "")
    absent = service.source_corpus.check_target_in_source_corpus(tmp_path, canonical_locator="absent-locator")

    assert by_path.ok and by_path.value is not None
    assert by_path.value.duplicate
    assert by_sha.ok and by_sha.value is not None
    assert by_sha.value.duplicate
    assert absent.ok and absent.value is not None
    assert not absent.value.duplicate


def test_source_corpus_preserves_author_tex_tree_without_artificial_main_layout(tmp_path: Path) -> None:
    source_root = tmp_path / ".lean_constellation" / "source"
    article = source_root / "article"
    article.mkdir(parents=True)
    (source_root / "README.md").write_text(_source_entry_text(main_path="article/paper.tex"), encoding="utf-8")
    (article / "paper.tex").write_text(
        "\\section{Main result}\n\\begin{theorem}A faithful theorem.\\end{theorem}\n",
        encoding="utf-8",
    )
    (article / "macros.tex").write_text("\\newcommand{\\A}{A}\n", encoding="utf-8")
    (article / "refs.bib").write_text("@article{fixture,title={Fixture}}\n", encoding="utf-8")

    gate = make_runtime().material.check_source_corpus_draft(tmp_path, entry_path="README.md")

    assert gate.ok and gate.value is not None and gate.value.passed
    assert not (source_root / "main").exists()


def test_source_corpus_accepts_complete_local_tex_include_tree(tmp_path: Path) -> None:
    source_root = tmp_path / ".lean_constellation" / "source"
    sections = source_root / "article" / "sections"
    sections.mkdir(parents=True)
    (source_root / "README.md").write_text(
        _source_entry_text(main_path="article/main.tex"),
        encoding="utf-8",
    )
    (source_root / "article" / "main.tex").write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "% \\input{sections/commented_missing}\n"
        "\\input{\\dynamicSection}\n"
        "\\input{sections/01_introduction}\n"
        "\\include{sections/02_result.tex}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    (sections / "01_introduction.tex").write_text("\\section{Introduction}\nText.\n", encoding="utf-8")
    (sections / "02_result.tex").write_text("\\section{Result}\nTheorem.\n", encoding="utf-8")

    gate = make_runtime().material.check_source_corpus_draft(tmp_path, entry_path="README.md")

    assert gate.ok and gate.value is not None and gate.value.passed


def test_source_corpus_rejects_missing_or_escaping_literal_tex_includes(tmp_path: Path) -> None:
    source_root = tmp_path / ".lean_constellation" / "source"
    article = source_root / "article"
    article.mkdir(parents=True)
    (source_root / "README.md").write_text(
        _source_entry_text(main_path="article/main.tex"),
        encoding="utf-8",
    )
    (article / "main.tex").write_text(
        "\\input{sections/missing}\n"
        "\\input{../../outside}\n",
        encoding="utf-8",
    )

    gate = make_runtime().material.check_source_corpus_draft(tmp_path, entry_path="README.md")

    assert gate.ok and gate.value is not None and not gate.value.passed
    issues = [item for item in gate.value.issues if item.kind == "source_corpus_tex_include_invalid"]
    assert {item.details["reason"] for item in issues} == {"missing", "path_escape"}
    assert {item.details["target"] for item in issues} == {"sections/missing", "../../outside"}


def test_source_corpus_pdf_transcription_retains_structure_and_page_mapping(tmp_path: Path) -> None:
    source_root = tmp_path / ".lean_constellation" / "source"
    (source_root / "original").mkdir(parents=True)
    (source_root / "transcription").mkdir()
    (source_root / "README.md").write_text(
        _source_entry_text(main_path="transcription/paper.md").replace(
            "Original mapping:",
            "Original PDF page mapping: original/paper.pdf is preserved; Original mapping:",
        ),
        encoding="utf-8",
    )
    (source_root / "original" / "paper.pdf").write_bytes(b"%PDF-1.4\nfixture")
    transcription = (
        "# Section 2 (PDF page 3)\n\n"
        "## Theorem 2.1\nAssume h. Then conclusion C.\n\n"
        "Equation (2.4): `x + y = z`.\n\n"
        "Proof. First step; second step.\n"
    )
    (source_root / "transcription" / "paper.md").write_text(transcription, encoding="utf-8")

    gate = make_runtime().material.check_source_corpus_draft(tmp_path, entry_path="README.md")

    assert gate.ok and gate.value is not None and not gate.value.passed
    assert "source_corpus_raw_container_forbidden" in {issue.kind for issue in gate.value.issues}
    assert (source_root / "transcription" / "paper.md").read_text(encoding="utf-8") == transcription


def test_source_corpus_complete_transcription_filename_does_not_require_partial_scope(tmp_path: Path) -> None:
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    filename = "fan-yu-wang-2012-transcription.txt"
    readme = _source_entry_text(main_path=filename).replace(
        "Source boundary: complete fixture; omitted: none.\n",
        "Coverage: complete page-preserving transcription of all eight pages.\n",
    )
    (source_root / "README.md").write_text(readme, encoding="utf-8")
    (source_root / filename).write_text("Complete article transcription.\n", encoding="utf-8")

    gate = make_runtime().material.check_source_corpus_draft(
        tmp_path,
        entry_path="README.md",
    )

    assert gate.ok and gate.value is not None and gate.value.passed


def test_source_corpus_accepts_supplied_targets_solutions_and_descriptive_titles(tmp_path: Path) -> None:
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(_source_entry_text(main_path="summary.md"), encoding="utf-8")
    (source_root / "summary.md").write_text("# Generated summary\nAssumption h was silently removed.\n", encoding="utf-8")
    (source_root / "solution.tex").write_text("% Proposed agent solution\n", encoding="utf-8")
    (source_root / "formal_target.lean").write_text("theorem expected : True := by\n  sorry\n", encoding="utf-8")
    (source_root / "expected_answer.md").write_text("# Expected answer\nSupplied answer.\n", encoding="utf-8")
    (source_root / "expected_proof.md").write_text("# Expected proof\nSupplied proof.\n", encoding="utf-8")
    (source_root / "expected_node_tree.md").write_text("# Expected node tree\nSupplied structure.\n", encoding="utf-8")
    (source_root / "audit_hint.md").write_text("# Audit hint\nSupplied operator context.\n", encoding="utf-8")
    (source_root / "lean_probe.lean").write_text("#check Nat\n", encoding="utf-8")

    gate = make_runtime().material.check_source_corpus_draft(tmp_path, entry_path="README.md")

    assert gate.ok and gate.value is not None and gate.value.passed
    scanned = make_runtime().material.scan_source_corpus(tmp_path)
    assert scanned.ok and scanned.value is not None
    assert {item.path for item in scanned.value.files} >= {
        "formal_target.lean",
        "solution.tex",
        "expected_answer.md",
        "expected_proof.md",
        "expected_node_tree.md",
        "audit_hint.md",
        "lean_probe.lean",
    }


def test_source_draft_rejects_unauthorized_special_roles_and_control_text(tmp_path: Path) -> None:
    service = make_runtime().material
    _write_source_prepare_input(service.runtime, tmp_path, ["https://example.test/paper.pdf"])
    root = tmp_path / ".lean_constellation" / "source_draft"
    (root / "article").mkdir(parents=True)
    (root / "article" / "main.md").write_text("bad\x00text\n", encoding="utf-8")
    (root / "formal_target.lean").write_text("theorem target : True := by trivial\n", encoding="utf-8")
    (root / "solution.md").write_text("An unauthorized solution.\n", encoding="utf-8")
    (root / "expected_proof.md").write_text("An unauthorized proof.\n", encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets" / "figure.svg").write_text("<svg/>\n", encoding="utf-8")
    (root / "README.md").write_text(
        _source_entry_text(main_path="article/main.md")
        + "Downstream readers should inspect `_work/original/paper.pdf`.\n",
        encoding="utf-8",
    )

    gate = service.check_source_corpus_draft(
        tmp_path,
        relpath=".lean_constellation/source_draft",
        entry_path="README.md",
    )

    assert gate.ok and gate.value is not None and not gate.value.passed
    assert {
        "source_corpus_control_characters_forbidden",
        "source_corpus_formal_target_unauthorized",
        "source_corpus_solution_unauthorized",
        "source_corpus_proof_reference_unauthorized",
        "source_corpus_asset_unauthorized",
        "source_corpus_work_reference_forbidden",
    } <= {issue.kind for issue in gate.value.issues}


def test_corrected_final_text_needs_no_process_ledger(tmp_path: Path) -> None:
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(
        _source_entry_text(main_path="selected_excerpt.md"),
        encoding="utf-8",
    )
    (source_root / "selected_excerpt.md").write_text("selected source text\n", encoding="utf-8")
    service = make_runtime().material

    passed = service.check_source_corpus_draft(tmp_path, entry_path="README.md")
    assert passed.ok and passed.value is not None and passed.value.passed


def test_source_corpus_rejects_unbalanced_markdown_math_delimiters(tmp_path: Path) -> None:
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(_source_entry_text(main_path="article.md"), encoding="utf-8")
    article = source_root / "article.md"
    article.write_text(
        "A unit vector $X$ is a least vector of $G.\n",
        encoding="utf-8",
    )
    service = make_runtime().material

    rejected = service.check_source_corpus_draft(tmp_path, entry_path="README.md")

    assert rejected.ok and rejected.value is not None and not rejected.value.passed
    issue = next(
        item
        for item in rejected.value.issues
        if item.kind == "source_corpus_markdown_math_delimiter_unbalanced"
    )
    assert issue.object_ref == "article.md"
    assert issue.details == {"line_numbers": "1"}

    article.write_text(
        "A unit vector $X$ is a least vector of $G$.\n"
        "Display math follows.\n$$\nx + y = z\n$$\n"
        "A literal price is $5. Escaped \\$ and `$not_math` remain text.\n"
        "```text\n$also_not_math\n```\n",
        encoding="utf-8",
    )
    passed = service.check_source_corpus_draft(tmp_path, entry_path="README.md")

    assert passed.ok and passed.value is not None and passed.value.passed


def test_source_corpus_rejects_runtime_artifacts_symlinks_and_old_manifest_schema(tmp_path: Path) -> None:
    source_root = tmp_path / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(_source_entry_text(), encoding="utf-8")
    (source_root / "notes").mkdir()
    (source_root / "notes" / "section.md").write_text("source text\n", encoding="utf-8")
    (source_root / ".cache").mkdir()
    (source_root / ".cache" / "session.json").write_text("{}\n", encoding="utf-8")
    (source_root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (source_root / "auth.json").write_text("{}\n", encoding="utf-8")
    (source_root / "compiled.pyc").write_bytes(b"fixture")
    (source_root / "linked.md").symlink_to(source_root / "notes" / "section.md")
    service = make_runtime().material

    unsafe = service.check_source_corpus_draft(tmp_path, entry_path="README.md")
    assert unsafe.ok and unsafe.value is not None and not unsafe.value.passed
    assert {"source_corpus_artifact_forbidden", "source_corpus_symlink_forbidden"} <= {
        issue.kind for issue in unsafe.value.issues
    }
    assert {".cache", ".cache/session.json", ".env", "auth.json", "compiled.pyc"} <= {
        issue.object_ref for issue in unsafe.value.issues if issue.kind == "source_corpus_artifact_forbidden"
    }

    (source_root / "linked.md").unlink()
    (source_root / ".env").unlink()
    (source_root / "auth.json").unlink()
    (source_root / "compiled.pyc").unlink()
    (source_root / ".cache" / "session.json").unlink()
    (source_root / ".cache").rmdir()
    prepared = service.submit_source_corpus_prepared(
        tmp_path,
        entry_path="README.md",
        overview="Current source corpus.",
        preparation_summary="Prepared current source corpus.",
    )
    assert prepared.ok
    manifest_path = tmp_path / ".lean_constellation" / "source_corpus" / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload.pop("schema_version")
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    old = service.source_corpus.get_source_corpus_manifest(tmp_path)
    assert not old.ok
    assert any(issue.kind == "schema_version_missing" for issue in old.issues)
