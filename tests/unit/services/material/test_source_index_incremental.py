from __future__ import annotations

import json
from pathlib import Path

from lean_constellation.domain.repo_run import SourceScope
from tests.unit_services_helpers import make_runtime


def _write_source(repo_root: Path) -> None:
    source_root = repo_root / ".lean_constellation" / "source"
    (source_root / "chapters").mkdir(parents=True)
    (source_root / "README.md").write_text(
        "# Entry\n\n"
        "Source provenance: local fixture.\n"
        "Reading order: start here, then read chapters as the main material.\n"
        "Main material: chapters contain definitions and theorems.\n"
        "Known gaps and extraction limits: no known gaps.\n",
        encoding="utf-8",
    )
    (source_root / "chapters" / "one.md").write_text("Definition A.\nTheorem B.\n", encoding="utf-8")
    (source_root / "chapters" / "two.md").write_text("Definition C.\nTheorem D.\n", encoding="utf-8")
    (source_root / "artifact.bin").write_bytes(b"\x00\x01")


def _prepare_source(repo_root: Path):
    runtime = make_runtime()
    _write_source(repo_root)
    prepared = runtime.material.submit_source_corpus_prepared(
        repo_root,
        entry_path="README.md",
        overview="Incremental source fixture.",
        preparation_summary="Prepared source fixture.",
    )
    assert prepared.ok, prepared.issues
    return runtime


def _complete_block(runtime, repo_root: Path, *, path: str, title: str) -> str:
    created = runtime.material.create_source_block(
        repo_root,
        parent_id="root",
        kind="statement",
        title=title,
        summary=f"Statement from {path}.",
    )
    assert created.ok and created.value is not None, created.issues
    block_id = created.value.block_id
    ref = runtime.material.add_source_block_ref(
        repo_root,
        block_id=block_id,
        path=path,
        start_line=1,
        end_line=2,
        role="primary",
    )
    assert ref.ok, ref.issues
    assert runtime.material.mark_block_refs_done(repo_root, block_id=block_id).value.passed
    assert runtime.material.mark_block_links_done(repo_root, block_id=block_id).value.passed
    assert runtime.material.mark_block_completed(repo_root, block_id=block_id).value.passed
    return block_id


def test_scope_resolver_supports_exact_directory_glob_all_and_none(tmp_path: Path) -> None:
    runtime = _prepare_source(tmp_path)

    exact = runtime.material.resolve_source_scope(
        tmp_path,
        source_scope=SourceScope(mode="selected", selectors=["chapters/one.md"]),
    )
    directory = runtime.material.resolve_source_scope(
        tmp_path,
        source_scope=SourceScope(mode="selected", selectors=["chapters"]),
    )
    globbed = runtime.material.resolve_source_scope(
        tmp_path,
        source_scope=SourceScope(mode="selected", selectors=["chapters/*.md"]),
    )
    all_files = runtime.material.resolve_source_scope(tmp_path, source_scope=SourceScope(mode="all"))
    none = runtime.material.resolve_source_scope(tmp_path, source_scope=SourceScope(mode="none"))

    assert exact.ok and exact.value.resolved_file_paths == ["chapters/one.md"]
    assert directory.ok and directory.value.resolved_file_paths == ["chapters/one.md", "chapters/two.md"]
    assert globbed.ok and globbed.value.resolved_file_paths == ["chapters/one.md", "chapters/two.md"]
    assert all_files.ok and all_files.value.resolved_file_paths == [
        "README.md",
        "artifact.bin",
        "chapters/one.md",
        "chapters/two.md",
    ]
    assert none.ok and none.value.resolved_file_paths == []
    assert all_files.value.artifact_file_paths == ["artifact.bin"]

    unsafe = runtime.material.resolve_source_scope(
        tmp_path,
        source_scope=SourceScope(mode="selected", selectors=["../outside.md"]),
    )
    unmatched = runtime.material.resolve_source_scope(
        tmp_path,
        source_scope=SourceScope(mode="selected", selectors=["missing/*.md"]),
    )
    assert not unsafe.ok and unsafe.issues[0].kind == "source_scope_selector_unsafe"
    assert not unmatched.ok and unmatched.issues[0].kind == "source_scope_selector_unmatched"

    nested = tmp_path / ".lean_constellation" / "source" / "chapters" / "nested" / "three.md"
    nested.parent.mkdir()
    nested.write_text("Nested theorem.\n", encoding="utf-8")
    single_level = runtime.material.resolve_source_scope(
        tmp_path,
        source_scope=SourceScope(mode="selected", selectors=["chapters/*.md"]),
    )
    recursive = runtime.material.resolve_source_scope(
        tmp_path,
        source_scope=SourceScope(mode="selected", selectors=["chapters/**/*.md"]),
    )
    assert single_level.ok and single_level.value.resolved_file_paths == [
        "chapters/one.md",
        "chapters/two.md",
    ]
    assert recursive.ok and recursive.value.resolved_file_paths == [
        "chapters/nested/three.md",
        "chapters/one.md",
        "chapters/two.md",
    ]


def test_scoped_initial_update_resumes_same_scope_and_ignores_unselected_pending(tmp_path: Path) -> None:
    runtime = _prepare_source(tmp_path)
    scope = runtime.material.resolve_source_scope(
        tmp_path,
        source_scope=SourceScope(mode="selected", selectors=["chapters/one.md"]),
    )
    assert scope.ok and scope.value is not None
    opened = runtime.material.open_source_index_update(
        tmp_path,
        resolved_scope=scope.value,
        index_policy="auto",
    )
    assert opened.ok and opened.value is not None and opened.value.outcome == "opened"
    original_baseline_digest = opened.value.baseline_digest
    assert original_baseline_digest == runtime.material.source_index.missing_source_index_digest()

    assert runtime.material.set_source_index_overview(
        tmp_path,
        overview="Partial in-flight update.",
    ).ok
    retried = runtime.material.open_source_index_update(
        tmp_path,
        resolved_scope=scope.value,
        index_policy="auto",
    )
    assert retried.ok and retried.value is not None
    assert retried.value.outcome == "already_open"
    assert retried.value.baseline_digest is None
    wrong_retry = runtime.material.open_source_index_update(
        tmp_path,
        resolved_scope=scope.value,
        index_policy="auto",
        expected_baseline_digest="wrong-baseline",
    )
    assert not wrong_retry.ok
    assert wrong_retry.issues[0].kind == "source_index_baseline_digest_mismatch"
    verified_retry = runtime.material.open_source_index_update(
        tmp_path,
        resolved_scope=scope.value,
        index_policy="auto",
        expected_baseline_digest=original_baseline_digest,
    )
    assert verified_retry.ok and verified_retry.value is not None
    assert verified_retry.value.outcome == "already_open"
    assert verified_retry.value.baseline_digest is None

    context = runtime.material.get_source_index_update_context(tmp_path)
    assert context.ok and context.value is not None
    assert context.value.active_file_scope == ["chapters/one.md"]
    assert list(context.value.files) == ["chapters/one.md"]
    assert context.value.files["chapters/one.md"].source_sha256
    assert not context.value.files["chapters/one.md"].committed
    assert "active_update_id" not in context.value.model_dump()
    assert "active_update_id" not in runtime.material.get_source_index(tmp_path).value.model_dump()

    _complete_block(runtime, tmp_path, path="chapters/one.md", title="First theorem")
    for status_method in (runtime.material.set_file_survey_status, runtime.material.set_file_indexing_status):
        kwargs = {"summary": "Surveyed."} if status_method == runtime.material.set_file_survey_status else {}
        status = "surveyed" if status_method == runtime.material.set_file_survey_status else "indexed"
        result = status_method(
            tmp_path,
            path="chapters/one.md",
            status=status,
            **kwargs,
        )
        assert result.ok, result.issues

    gate = runtime.material.validate_source_index_update(
        tmp_path,
        baseline_index=None,
        expected_baseline_digest=opened.value.baseline_digest,
        resolved_scope=["chapters/one.md"],
        require_completed=True,
    )
    assert gate.ok and gate.value is not None and gate.value.gate.passed, gate.issues
    assert "chapters/two.md" not in gate.value.gate_issue_kinds
    committed = runtime.material.commit_source_index_update(tmp_path, validated=gate.value)
    assert committed.ok and committed.value is not None
    assert committed.value.newly_committed_file_paths == ["chapters/one.md"]
    assert committed.value.coverage.pending_file_paths == ["README.md", "chapters/two.md"]

def test_append_only_update_rejects_old_payload_change_and_baseline_drift(tmp_path: Path) -> None:
    runtime = _prepare_source(tmp_path)
    first_scope = runtime.material.resolve_source_scope(
        tmp_path,
        source_scope=SourceScope(mode="selected", selectors=["chapters/one.md"]),
    ).value
    opened = runtime.material.open_source_index_update(
        tmp_path, resolved_scope=first_scope, index_policy="auto"
    ).value
    block_id = _complete_block(runtime, tmp_path, path="chapters/one.md", title="Stable theorem")
    first_model = runtime.material.source_index.get_source_index_model(tmp_path).value
    ref_id = first_model.blocks[block_id].refs[0].ref_id
    link = runtime.material.create_source_link(
        tmp_path,
        source_block_id=block_id,
        target_block_id=None,
        target_hint="Stable external target.",
        link_kind="supports",
        evidence_ref_ids=[ref_id],
    )
    assert link.ok and link.value is not None
    link_id = link.value.link_id
    assert runtime.material.mark_block_links_done(
        tmp_path, block_id=block_id
    ).value.passed
    assert runtime.material.mark_block_completed(
        tmp_path, block_id=block_id
    ).value.passed
    assert runtime.material.set_file_survey_status(
        tmp_path, path="chapters/one.md", status="surveyed"
    ).ok
    assert runtime.material.set_file_indexing_status(
        tmp_path, path="chapters/one.md", status="indexed"
    ).ok
    first_gate = runtime.material.validate_source_index_update(
        tmp_path,
        baseline_index=None,
        expected_baseline_digest=opened.baseline_digest,
        resolved_scope=["chapters/one.md"],
        require_completed=True,
    ).value
    assert first_gate.gate.passed, first_gate.gate.issues
    assert runtime.material.commit_source_index_update(tmp_path, validated=first_gate).ok
    baseline = runtime.material.source_index.get_source_index_model(tmp_path).value
    baseline_digest = runtime.material.source_index.canonical_source_index_digest(baseline)

    second_scope = runtime.material.resolve_source_scope(
        tmp_path,
        source_scope=SourceScope(mode="selected", selectors=["chapters/two.md"]),
    ).value
    second = runtime.material.open_source_index_update(
        tmp_path, resolved_scope=second_scope, index_policy="auto"
    )
    assert second.ok and second.value.baseline_digest == baseline_digest
    index_path = tmp_path / ".lean_constellation" / "source_index" / "index.json"
    before_overview_attempts = index_path.read_bytes()
    unchanged_overview = runtime.material.set_source_index_overview(
        tmp_path, overview=baseline.overview
    )
    assert unchanged_overview.ok
    assert index_path.read_bytes() == before_overview_attempts
    changed_overview = runtime.material.set_source_index_overview(
        tmp_path, overview="A changed committed overview."
    )
    assert not changed_overview.ok
    assert changed_overview.issues[0].kind == "source_index_baseline_overview_changed"
    assert index_path.read_bytes() == before_overview_attempts
    appended_old_ref = runtime.material.add_source_block_ref(
        tmp_path,
        block_id=block_id,
        path="chapters/two.md",
        start_line=1,
        end_line=2,
        role="additional",
    )
    assert appended_old_ref.ok
    changed = runtime.material.update_source_block(
        tmp_path,
        block_id=block_id,
        title="Changed theorem",
    )
    assert changed.ok
    changed_payload = json.loads(index_path.read_text(encoding="utf-8"))
    changed_payload["overview"] = "Direct baseline overview mutation."
    changed_payload["blocks"][block_id]["refs"][0]["role"] = "secondary"
    changed_payload["links"][link_id]["evidence_refs"][0]["ref"]["start_line"] = 2
    index_path.write_text(json.dumps(changed_payload, indent=2) + "\n", encoding="utf-8")
    gate = runtime.material.validate_source_index_update(
        tmp_path,
        baseline_index=baseline,
        expected_baseline_digest=baseline_digest,
        resolved_scope=["chapters/two.md"],
        require_completed=True,
    )
    assert gate.ok and gate.value is not None and not gate.value.gate.passed
    assert "source_index_baseline_overview_changed" in gate.value.gate_issue_kinds
    assert "source_index_baseline_block_changed" in gate.value.gate_issue_kinds
    assert "source_index_baseline_ref_adjacency_changed" in gate.value.gate_issue_kinds
    assert "source_index_baseline_ref_changed" in gate.value.gate_issue_kinds
    assert "source_index_baseline_link_changed" in gate.value.gate_issue_kinds

    drift = runtime.material.validate_source_index_update(
        tmp_path,
        baseline_index=baseline,
        expected_baseline_digest="0" * 64,
        resolved_scope=["chapters/two.md"],
        require_completed=True,
    )
    assert drift.ok and drift.value is not None and not drift.value.gate.passed
    assert "source_index_baseline_digest_mismatch" in drift.value.gate_issue_kinds

def test_committed_source_hash_change_blocks_incremental_open(tmp_path: Path) -> None:
    runtime = _prepare_source(tmp_path)
    scope = runtime.material.resolve_source_scope(
        tmp_path, source_scope=SourceScope(mode="selected", selectors=["chapters/one.md"])
    ).value
    opened = runtime.material.open_source_index_update(tmp_path, resolved_scope=scope, index_policy="auto").value
    _complete_block(runtime, tmp_path, path="chapters/one.md", title="Theorem")
    assert runtime.material.set_file_survey_status(
        tmp_path, path="chapters/one.md", status="surveyed"
    ).ok
    assert runtime.material.set_file_indexing_status(
        tmp_path, path="chapters/one.md", status="indexed"
    ).ok
    gate = runtime.material.validate_source_index_update(
        tmp_path,
        baseline_index=None,
        expected_baseline_digest=opened.baseline_digest,
        resolved_scope=["chapters/one.md"],
        require_completed=True,
    ).value
    assert runtime.material.commit_source_index_update(tmp_path, validated=gate).ok

    source_file = tmp_path / ".lean_constellation" / "source" / "chapters" / "one.md"
    source_file.write_text("Changed definition.\nChanged theorem.\n", encoding="utf-8")
    fresh_scope = runtime.material.resolve_source_scope(
        tmp_path, source_scope=SourceScope(mode="selected", selectors=["chapters/two.md"])
    ).value
    changed = runtime.material.open_source_index_update(
        tmp_path, resolved_scope=fresh_scope, index_policy="auto"
    )
    assert not changed.ok and changed.issues[0].kind == "committed_source_file_changed"

    source_file.write_text("Definition A.\nTheorem B.\n", encoding="utf-8")
    source_file.unlink()
    other_scope = runtime.material.resolve_source_scope(
        tmp_path,
        source_scope=SourceScope(mode="selected", selectors=["chapters/two.md"]),
    ).value
    missing = runtime.material.open_source_index_update(
        tmp_path, resolved_scope=other_scope, index_policy="auto"
    )
    assert not missing.ok and missing.issues[0].kind == "committed_source_file_missing"


def test_same_committed_file_can_append_new_blocks_without_changing_old_payload(tmp_path: Path) -> None:
    runtime = _prepare_source(tmp_path)
    scope = runtime.material.resolve_source_scope(
        tmp_path, source_scope=SourceScope(mode="selected", selectors=["chapters/one.md"])
    ).value
    first = runtime.material.open_source_index_update(
        tmp_path, resolved_scope=scope, index_policy="auto"
    ).value
    old_block_id = _complete_block(
        runtime, tmp_path, path="chapters/one.md", title="Public theorem"
    )
    assert runtime.material.set_file_survey_status(
        tmp_path, path="chapters/one.md", status="surveyed"
    ).ok
    assert runtime.material.set_file_indexing_status(
        tmp_path, path="chapters/one.md", status="indexed"
    ).ok
    first_gate = runtime.material.validate_source_index_update(
        tmp_path,
        baseline_index=None,
        expected_baseline_digest=first.baseline_digest,
        resolved_scope=["chapters/one.md"],
        require_completed=True,
    ).value
    assert runtime.material.commit_source_index_update(tmp_path, validated=first_gate).ok
    baseline = runtime.material.source_index.get_source_index_model(tmp_path).value
    old_payload = baseline.blocks[old_block_id].model_dump(mode="json")

    second = runtime.material.open_source_index_update(
        tmp_path, resolved_scope=scope, index_policy="update"
    )
    assert second.ok and second.value is not None and second.value.outcome == "opened"
    new_block_id = _complete_block(
        runtime, tmp_path, path="chapters/one.md", title="Proof detail"
    )
    second_gate = runtime.material.validate_source_index_update(
        tmp_path,
        baseline_index=baseline,
        expected_baseline_digest=second.value.baseline_digest,
        resolved_scope=["chapters/one.md"],
        require_completed=True,
    )
    assert second_gate.ok and second_gate.value is not None and second_gate.value.gate.passed
    assert second_gate.value.new_block_ids == [new_block_id]
    committed = runtime.material.commit_source_index_update(
        tmp_path, validated=second_gate.value
    )
    assert committed.ok and committed.value.appended_block_ids == [new_block_id]
    final = runtime.material.source_index.get_source_index_model(tmp_path).value
    assert final.blocks[old_block_id].model_dump(mode="json") == old_payload


def test_commit_rechecks_source_hash_after_successful_validation(tmp_path: Path) -> None:
    runtime = _prepare_source(tmp_path)
    scope = runtime.material.resolve_source_scope(
        tmp_path, source_scope=SourceScope(mode="selected", selectors=["chapters/one.md"])
    ).value
    opened = runtime.material.open_source_index_update(
        tmp_path, resolved_scope=scope, index_policy="auto"
    ).value
    _complete_block(runtime, tmp_path, path="chapters/one.md", title="Theorem")
    assert runtime.material.set_file_survey_status(
        tmp_path, path="chapters/one.md", status="surveyed"
    ).ok
    assert runtime.material.set_file_indexing_status(
        tmp_path, path="chapters/one.md", status="indexed"
    ).ok
    gate = runtime.material.validate_source_index_update(
        tmp_path,
        baseline_index=None,
        expected_baseline_digest=opened.baseline_digest,
        resolved_scope=["chapters/one.md"],
        require_completed=True,
    ).value
    assert gate.gate.passed
    (tmp_path / ".lean_constellation" / "source" / "chapters" / "one.md").write_text(
        "Changed after validation.\nTheorem.\n", encoding="utf-8"
    )
    committed = runtime.material.commit_source_index_update(tmp_path, validated=gate)
    assert not committed.ok and committed.issues[0].kind == "source_index_source_hash_changed"
    current = runtime.material.get_source_index(tmp_path)
    assert current.ok and current.value.status == "draft"
    assert current.value.active_file_scope == ["chapters/one.md"]


def test_commit_rejects_index_digest_change_after_successful_validation(tmp_path: Path) -> None:
    runtime = _prepare_source(tmp_path)
    scope = runtime.material.resolve_source_scope(
        tmp_path, source_scope=SourceScope(mode="selected", selectors=["chapters/one.md"])
    ).value
    opened = runtime.material.open_source_index_update(
        tmp_path, resolved_scope=scope, index_policy="auto"
    ).value
    _complete_block(runtime, tmp_path, path="chapters/one.md", title="Theorem")
    assert runtime.material.set_file_survey_status(
        tmp_path, path="chapters/one.md", status="surveyed"
    ).ok
    assert runtime.material.set_file_indexing_status(
        tmp_path, path="chapters/one.md", status="indexed"
    ).ok
    gate = runtime.material.validate_source_index_update(
        tmp_path,
        baseline_index=None,
        expected_baseline_digest=opened.baseline_digest,
        resolved_scope=["chapters/one.md"],
        require_completed=True,
    ).value
    assert gate.gate.passed

    changed = runtime.material.set_source_index_overview(
        tmp_path, overview="Changed after deterministic validation."
    )
    assert changed.ok
    committed = runtime.material.commit_source_index_update(tmp_path, validated=gate)

    assert not committed.ok
    assert committed.issues[0].kind == "source_index_validation_stale"


def test_none_and_reuse_policies_are_deterministic_no_ops(tmp_path: Path) -> None:
    runtime = _prepare_source(tmp_path)
    none_scope = runtime.material.resolve_source_scope(
        tmp_path, source_scope=SourceScope(mode="none")
    ).value
    none_open = runtime.material.open_source_index_update(
        tmp_path, resolved_scope=none_scope, index_policy="auto"
    )
    assert none_open.ok and none_open.value is not None and none_open.value.outcome == "no_op"
    assert not (tmp_path / ".lean_constellation" / "source_index" / "index.json").exists()

    selected = runtime.material.resolve_source_scope(
        tmp_path, source_scope=SourceScope(mode="selected", selectors=["chapters/one.md"])
    ).value
    reuse_missing = runtime.material.open_source_index_update(
        tmp_path, resolved_scope=selected, index_policy="reuse"
    )
    assert not reuse_missing.ok and reuse_missing.issues[0].kind == "source_index_scope_not_reusable"
