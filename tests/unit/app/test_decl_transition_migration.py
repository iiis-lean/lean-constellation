from __future__ import annotations

import json
from pathlib import Path

import pytest

from lean_constellation.app import decl_transition_migration as migration
from lean_constellation.app.decl_transition_migration import (
    DeclTransitionMigrationError,
    migrate_decl_transition_schema,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _revision_payload(
    revision: int,
    *,
    kind: str,
    state: str,
    status: str,
    reset: str | None,
    target: str,
) -> dict[str, object]:
    return {
        "lean_decl_name": "Example.main_result"
        if state in {"declared", "proof_planned", "proved"}
        else None,
        "revision": revision,
        "state": state,
        "status": status,
        "change": {
            "kind": kind,
            "start_before_state": reset,
            "end_after_state": target,
            "require_target_state_satisfied": True,
            "objective": "Migrate the declaration.",
            "summary": None,
        },
        "statement": {"nl": None, "formal": None, "deps": []},
        "proof": None,
        "updated_at": "2026-07-18T00:00:00Z",
    }


def _repo_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    repo = tmp_path / "Example"
    revisions = (
        repo / ".lean_constellation/nodes/node_1/decl_graph/decls/main_result/revisions"
    )
    first = revisions / "1.json"
    second = revisions / "2.json"
    scope = repo / ".agent_runtime/scopes/repo_Example/scope.json"
    snapshot = (
        repo / ".lean_constellation/snapshots/repo_checkpoints/cp_1/files/revision.json"
    )
    _write_json(
        first,
        _revision_payload(
            1,
            kind="create",
            state="declared",
            status="committed",
            reset=None,
            target="declared",
        ),
    )
    _write_json(
        second,
        _revision_payload(
            2,
            kind="update",
            state="declared",
            status="committed",
            reset="declared",
            target="proved",
        ),
    )
    _write_json(
        scope,
        {
            "schema_version": 1,
            "object_type": "scope",
            "scope_id": "repo:Example",
            "scope_key": "repo_Example",
            "created_at": "2026-07-18T00:00:00Z",
            "updated_at": "2026-07-18T00:00:00Z",
            "metadata": {
                "start_before_state": "declared",
                "end_after_state": "proved",
                "prompt": "Historical text says start_before_state and end_after_state and must remain exact.",
            },
        },
    )
    _write_json(
        snapshot, {"start_before_state": "planned", "end_after_state": "proved"}
    )
    return repo, first, second, snapshot


def test_decl_transition_migration_dry_run_apply_validate_and_idempotence(
    tmp_path: Path,
) -> None:
    repo, first, second, snapshot = _repo_fixture(tmp_path)
    snapshot_before = snapshot.read_bytes()

    dry_run = migrate_decl_transition_schema(repo, mode="dry-run")

    assert len(dry_run.changed_files) == 3
    assert dry_run.old_structured_key_count == 6
    revision_rewrite = next(
        item for item in dry_run.changed_files if item.relative_path.endswith("revisions/2.json")
    )
    assert sorted(
        (item.json_path, item.old_key, item.new_key)
        for item in revision_rewrite.key_rewrites
    ) == [
        ("$.change.end_after_state", "end_after_state", "target_state"),
        ("$.change.start_before_state", "start_before_state", "reset_to_state"),
    ]
    assert first.read_text(encoding="utf-8").find("start_before_state") >= 0
    applied = migrate_decl_transition_schema(
        repo,
        mode="apply",
        manifest_dir=tmp_path / "manifests",
        rebuild_runtime_indexes=True,
    )
    assert applied.indexes_rebuilt is True
    assert applied.backup_root is not None
    assert Path(applied.backup_root, "BEFORE_MANIFEST.json").is_file()
    assert Path(applied.backup_root, "RESULT_MANIFEST.json").is_file()
    first_payload = json.loads(first.read_text(encoding="utf-8"))
    second_payload = json.loads(second.read_text(encoding="utf-8"))
    scope_payload = json.loads(
        (repo / ".agent_runtime/scopes/repo_Example/scope.json").read_text(
            encoding="utf-8"
        )
    )
    assert first_payload["change"]["base_revision"] is None
    assert second_payload["change"]["base_revision"] == 1
    assert second_payload["change"]["reset_to_state"] == "declared"
    assert second_payload["change"]["target_state"] == "proved"
    assert "start_before_state" not in second_payload["change"]
    assert scope_payload["metadata"]["reset_to_state"] == "declared"
    assert scope_payload["metadata"]["prompt"] == (
        "Historical text says start_before_state and end_after_state and must remain exact."
    )
    assert snapshot.read_bytes() == snapshot_before
    validated = migrate_decl_transition_schema(repo, mode="validate")
    assert validated.changed_files == []
    repeated = migrate_decl_transition_schema(repo, mode="dry-run")
    assert repeated.changed_files == []


def test_decl_transition_migration_rejects_key_collision(tmp_path: Path) -> None:
    repo, first, _second, _snapshot = _repo_fixture(tmp_path)
    payload = json.loads(first.read_text(encoding="utf-8"))
    payload["change"]["reset_to_state"] = "declared"
    _write_json(first, payload)

    with pytest.raises(DeclTransitionMigrationError, match="collision"):
        migrate_decl_transition_schema(repo, mode="dry-run")


def test_decl_transition_migration_rejects_unverified_update_lineage(
    tmp_path: Path,
) -> None:
    repo, first, _second, _snapshot = _repo_fixture(tmp_path)
    payload = json.loads(first.read_text(encoding="utf-8"))
    payload["status"] = "open"
    _write_json(first, payload)

    with pytest.raises(DeclTransitionMigrationError, match="committed predecessor"):
        migrate_decl_transition_schema(repo, mode="dry-run")


def test_decl_transition_migration_rolls_back_when_post_write_validation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, first, second, _snapshot = _repo_fixture(tmp_path)
    before = {first: first.read_bytes(), second: second.read_bytes()}

    def fail_validation(*_args, **_kwargs) -> None:
        raise RuntimeError("injected validation failure")

    monkeypatch.setattr(migration, "_validate_transformed_payloads", fail_validation)
    with pytest.raises(DeclTransitionMigrationError, match="rolled back"):
        migrate_decl_transition_schema(
            repo, mode="apply", manifest_dir=tmp_path / "manifests"
        )

    assert first.read_bytes() == before[first]
    assert second.read_bytes() == before[second]


def test_decl_transition_migration_refuses_snapshot_as_repo_root(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "snapshots" / "captured_repo"
    (repo / ".lean_constellation").mkdir(parents=True)

    with pytest.raises(DeclTransitionMigrationError, match="protected/archive"):
        migrate_decl_transition_schema(repo, mode="dry-run")
