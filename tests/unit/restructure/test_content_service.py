from lean_constellation.domain.restructure import SectionInput, SectionNL
from pathlib import Path

import pytest

from lean_constellation.domain.restructure import DeclKind, DeclStatus, Origin, RestructureStage, SourceRef
from lean_constellation.services.restructure import RestructureLayout, RestructureStore
from lean_constellation.services.restructure.content import ContentGateError, RestructureContentService


def test_content_registration_sections_capture_and_gate(tmp_path: Path):
    store = RestructureStore(tmp_path)
    layout = RestructureLayout(store)
    layout.prepare_repo("repo", "Pkg")
    service = RestructureContentService(store, layout)
    service.create_decl("repo", "Pkg", "Main.Bounds", name="bound", lean_name="bound", kind=DeclKind.DEF, summary="a bound")
    with pytest.raises(ContentGateError):
        service.submit("repo", "Main.Bounds", stage=RestructureStage.DECLARED)
    service.set_sections('repo', 'Main.Bounds', 'bound', statement=SectionInput(nl=SectionNL(text='the bound statement', origins=[Origin(source_refs=[SourceRef(corpus='tex', path='paper.tex', locator='p.1')])])))
    service.capture("repo", "Main.Bounds", "bound", status=DeclStatus.DECLARED)
    work = service.submit("repo", "Main.Bounds", stage=RestructureStage.DECLARED)
    assert work.stage is RestructureStage.DECLARED


def test_capture_rejects_stale_file_digest(tmp_path: Path):
    store = RestructureStore(tmp_path)
    layout = RestructureLayout(store)
    layout.prepare_repo("repo", "Pkg")
    service = RestructureContentService(store, layout)
    decl = service.create_decl("repo", "Pkg", "Main.Bounds", name="bound", lean_name="bound", kind=DeclKind.DEF, summary="a bound")
    with pytest.raises(ContentGateError, match="digest"):
        service.capture("repo", "Main.Bounds", "bound", expected_file_digest="bad")
