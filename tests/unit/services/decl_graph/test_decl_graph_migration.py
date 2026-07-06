import json
from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.decl_graph import Decl, DeclChangeKind, DeclRoundResultKind, DeclRoundStatus, DeclState
from lean_constellation.services.foundation import WriteMode


def _create_content_node(tmp_path: Path, *, node_path: str = "Main.Topic.Core") -> None:
    runtime = make_runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(
        tmp_path,
        path="Main.Topic",
        goal="Topic goal",
        boundary="Topic boundary",
    ).ok
    assert runtime.node.create_content_node(
        tmp_path,
        path=node_path,
        goal="Core goal",
        boundary="Core boundary",
        objective="Build the core declarations.",
        success_criteria="The core declarations are ready.",
    ).ok


def test_decl_graph_migration_converts_flat_revision_changes_rounds_and_archives_reviews(tmp_path: Path) -> None:
    node_path = "Main.Topic.Core"
    _create_content_node(tmp_path, node_path=node_path)
    runtime = make_runtime()
    service = runtime.decl_graph
    assert service.ensure_decl_graph(tmp_path, node_path=node_path).ok
    strategy = service.ensure_open_strategy(tmp_path, node_path=node_path, objective="Legacy migration strategy.")
    assert strategy.ok and strategy.value is not None

    decl = Decl(name="legacy_decl", node_path=node_path, kind="theorem", current_revision=1, revision_ids=[1])
    decl_path = service.graph_store.decl_record_path(tmp_path, node_path=node_path, decl_name=decl.name)
    decl_path.parent.mkdir(parents=True, exist_ok=True)
    assert runtime.foundation.store.write_json_atomic(decl_path, decl, mode=WriteMode.CREATE_ONLY).ok

    revision_path = service.graph_store.revision_path(tmp_path, node_path=node_path, decl_name=decl.name, revision=1)
    revision_path.parent.mkdir(parents=True, exist_ok=True)
    revision_path.write_text(
        json.dumps(
            {
                "decl_name": "legacy_decl",
                "revision": 1,
                "state": "declared",
                "version_status": "committed",
                "change_kind": "create",
                "statement_nl": "Legacy statement text.",
                "statement_origin": [{"kind": "source", "ref": "source.tex:1-4"}],
                "statement_deps": ["supporting_lemma"],
                "statement_lean_code": "theorem legacy_decl : True := by trivial",
                "statement_lean_check": {"passed": "true"},
                "proof_nl": "Legacy proof text.",
                "proof_origin": [{"kind": "source", "ref": "source.tex:5-7"}],
                "proof_deps": ["supporting_lemma"],
                "proof_lean_code": "by trivial",
                "proof_lean_check": {"passed": "true"},
                "decl_deps": ["supporting_lemma"],
                "module": "Main.Topic.Core",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    graph_root = service.graph_store.graph_root(tmp_path, node_path=node_path)
    changes_dir = graph_root / "changes"
    changes_dir.mkdir()
    (changes_dir / "chg_legacy_create.json").write_text(
        json.dumps(
            {
                "change_id": "chg_legacy_create",
                "round_id": "round_legacy",
                "kind": "create",
                "decl_name": "legacy_decl",
                "target_revision": 1,
                "objective": "Create the legacy declaration.",
                "summary": "Legacy change summary.",
                "end_after_state": "proved",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    reviews_dir = graph_root / "reviews" / "round_legacy" / "statement_nl"
    reviews_dir.mkdir(parents=True)
    (reviews_dir / "legacy_decl.json").write_text('{"passed": true}', encoding="utf-8")

    round_path = service.graph_store.round_path(tmp_path, node_path=node_path, round_id="round_legacy")
    round_path.parent.mkdir(parents=True, exist_ok=True)
    round_path.write_text(
        json.dumps(
            {
                "round_id": "round_legacy",
                "node_path": node_path,
                "strategy_id": strategy.value.strategy_id,
                "round_index": 1,
                "status": "completed",
                "objective": "Legacy round objective.",
                "change_ids": ["chg_legacy_create"],
                "change_summaries": {},
                "completed_at": "2026-01-01T00:00:00Z",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    migrated = service.migrate_legacy_decl_graph_storage(tmp_path, node_path=node_path)

    assert migrated.ok and migrated.value is not None
    assert migrated.value.migrated_revisions == ["legacy_decl:1"]
    assert migrated.value.migrated_changes == ["chg_legacy_create"]
    assert migrated.value.migrated_rounds == ["round_legacy"]
    assert not changes_dir.exists()
    assert not (graph_root / "reviews").exists()
    assert (graph_root / "legacy_changes_archive" / "chg_legacy_create.json").is_file()
    assert (graph_root / "legacy_reviews_archive" / "round_legacy" / "statement_nl" / "legacy_decl.json").is_file()

    raw_revision = json.loads(revision_path.read_text(encoding="utf-8"))
    assert "statement_nl" not in raw_revision
    assert "proof_nl" not in raw_revision
    assert "decl_deps" not in raw_revision
    assert raw_revision["statement"]["nl"]["text"] == "Legacy statement text."
    assert raw_revision["statement"]["deps"] == [{"kind": "repo_decl", "ref": {"repo": None, "node": "Main", "name": "supporting_lemma", "revision": 1}, "reason": None}]
    assert raw_revision["proof"]["nl"]["text"] == "Legacy proof text."
    assert raw_revision["change"]["kind"] == "create"
    assert raw_revision["change"]["summary"] == "Legacy change summary."

    revision = service.get_decl_revision(tmp_path, node_path=node_path, name="legacy_decl", revision=1)
    assert revision.ok and revision.value is not None
    assert revision.value.statement_nl == "Legacy statement text."
    assert revision.value.proof_nl == "Legacy proof text."
    assert revision.value.change is not None
    assert revision.value.change.kind == DeclChangeKind.CREATE
    assert revision.value.change.end_after_state == DeclState.PROVED

    raw_round = json.loads(round_path.read_text(encoding="utf-8"))
    assert "change_ids" not in raw_round
    assert "completed_at" not in raw_round
    assert raw_round["revision_refs"] == [{"change_id": "chg_legacy_create", "decl_name": "legacy_decl", "revision": 1}]
    assert raw_round["status"] == "committed"
    assert raw_round["result_kind"] == "success"

    round_record = service.get_round(tmp_path, node_path=node_path, round_id="round_legacy")
    assert round_record.ok and round_record.value is not None
    assert round_record.value.status == DeclRoundStatus.COMMITTED
    assert round_record.value.result_kind == DeclRoundResultKind.SUCCESS
    assert round_record.value.change_ids == ["chg_legacy_create"]

    index = service.get_decl_graph_index(tmp_path, node_path=node_path)
    assert index.ok and index.value is not None
    assert index.value.decl_names == ["legacy_decl"]
    assert index.value.round_ids == ["round_legacy"]
