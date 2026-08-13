from pathlib import Path

import pytest
from pydantic import ValidationError

from lean_constellation.services.decl_graph.models import DeclOriginRef
from lean_constellation.services.decl_graph.origin_validation import validate_nl_origin
from tests.unit_services_helpers import make_runtime


def _write_source(repo_root: Path) -> None:
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "article.md").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")


def test_source_origin_requires_exact_range() -> None:
    with pytest.raises(ValidationError, match="explicit"):
        DeclOriginRef(kind="source", source_path="article.md")
    with pytest.raises(ValidationError, match="source_path"):
        DeclOriginRef(kind="source", ref="source:article.md#L1-L2")


def test_statement_and_proof_source_origins_use_source_corpus_without_source_index(
    tmp_path: Path,
) -> None:
    _write_source(tmp_path)
    runtime = make_runtime()
    origin = DeclOriginRef(
        kind="source",
        source_path="article.md",
        start_line=2,
        end_line=4,
    )

    statement_issue = validate_nl_origin(
        runtime,
        tmp_path,
        origin=origin,
        decl_name="result",
        stage="statement",
    )
    proof_issue = validate_nl_origin(
        runtime,
        tmp_path,
        origin=origin,
        decl_name="result",
        stage="proof",
    )

    assert statement_issue is None
    assert proof_issue is None
