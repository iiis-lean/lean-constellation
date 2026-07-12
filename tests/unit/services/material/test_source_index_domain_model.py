from __future__ import annotations

import json
from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.repo_run import SourceScope


def _prepare_source(repo_root: Path) -> None:
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(
        "# Entry\n\n"
        "Source provenance: local markdown fixture.\n"
        "Reading order: start here, then read `chapter.md` as the main material.\n"
        "Main material: `chapter.md` contains the indexed definitions and lemmas.\n"
        "Known gaps and extraction limits: no missing source sections are known.\n",
        encoding="utf-8",
    )
    (source_root / "chapter.md").write_text("Definition A.\nLemma B.\nTheorem C.\n", encoding="utf-8")


def test_source_index_persists_domain_model_and_returns_view(tmp_path: Path) -> None:
    runtime = make_runtime()
    service = runtime.material
    _prepare_source(tmp_path)
    prepared = service.submit_source_corpus_prepared(
        tmp_path,
        entry_path="README.md",
        overview="Indexed source corpus.",
        preparation_summary="Prepared source files.",
    )
    assert prepared.ok
    scope = service.resolve_source_scope(tmp_path, source_scope=SourceScope(mode="all"))
    assert scope.ok and scope.value is not None
    assert service.open_source_index_update(
        tmp_path,
        update_id="domain-model-test",
        resolved_scope=scope.value,
        index_policy="auto",
    ).ok

    block = service.create_source_block(
        tmp_path,
        parent_id="root",
        kind="section",
        subtype=None,
        title="Chapter theorem",
        summary="The part of the source containing Definition A, Lemma B, and Theorem C.",
        expected_update_id="domain-model-test",
    )
    assert block.ok and block.value is not None
    ref = service.add_source_block_ref(
        tmp_path,
        block_id=block.value.block_id,
        path="chapter.md",
        start_line=1,
        end_line=3,
        role="primary",
        expected_update_id="domain-model-test",
    )
    assert ref.ok and ref.value is not None
    link = service.create_source_link(
        tmp_path,
        source_block_id=block.value.block_id,
        target_block_id=None,
        target_hint="The theorem statement.",
        link_kind="supports",
        evidence_ref_ids=[ref.value.refs[0].ref_id],
        expected_update_id="domain-model-test",
    )
    assert link.ok and link.value is not None

    index_json = tmp_path / ".lean_constellation" / "source_index" / "index.json"
    persisted = json.loads(index_json.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == 3
    assert persisted["active_update_id"] == "domain-model-test"
    assert persisted["active_file_scope"] == ["README.md", "chapter.md"]
    assert persisted["files"]["chapter.md"]["source_sha256"] is not None
    assert persisted["files"]["chapter.md"]["committed"] is False
    assert "repo_root" not in persisted
    persisted_ref = persisted["blocks"][block.value.block_id]["refs"][0]
    assert "path" not in persisted_ref
    assert persisted_ref["material_ref"] == {
        "kind": "source",
        "ref": {"path": "chapter.md", "start_line": 1, "end_line": 3},
    }
    persisted_link = persisted["links"][link.value.link_id]
    assert "evidence_ref_ids" not in persisted_link
    assert persisted_link["evidence_refs"] == [persisted_ref["material_ref"]]

    view = service.get_source_index(tmp_path)
    assert view.ok and view.value is not None
    view_ref = view.value.blocks[block.value.block_id].refs[0]
    assert view_ref.path == "chapter.md"
    assert view_ref.start_line == 1
    assert view_ref.end_line == 3
    assert view.value.links[link.value.link_id].evidence_ref_ids == [view_ref.ref_id]
