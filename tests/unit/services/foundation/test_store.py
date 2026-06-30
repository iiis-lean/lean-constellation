from __future__ import annotations

from tests.unit_services_helpers import make_runtime

from pathlib import Path

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import StoreComponent, WriteMode


class ExampleModel(StrictModel):
    name: str
    value: int


class VersionedModel(StrictModel):
    version: int
    version_status: str
    payload: str


def test_json_read_write_modes(tmp_path) -> None:
    store = make_runtime().foundation.store
    path = tmp_path / "item.json"

    created = store.write_json_atomic(path, ExampleModel(name="a", value=1), mode=WriteMode.CREATE_ONLY)
    duplicate = store.write_json_atomic(path, ExampleModel(name="b", value=2), mode=WriteMode.CREATE_ONLY)
    read = store.read_json(path, ExampleModel)
    missing_update = store.write_json_atomic(tmp_path / "missing.json", ExampleModel(name="c", value=3), mode=WriteMode.UPDATE_EXISTING)

    assert created.ok is True
    assert created.value is not None and created.value.created is True
    assert duplicate.ok is False
    assert read.ok is True and read.value == ExampleModel(name="a", value=1)
    assert missing_update.ok is False


def test_read_json_reports_missing_and_schema_validation_errors(tmp_path) -> None:
    store = make_runtime().foundation.store

    missing = store.read_json(tmp_path / "missing.json", ExampleModel)
    assert missing.ok is False
    assert missing.issues[0].kind == "missing_file"

    invalid_schema_path = tmp_path / "invalid_schema.json"
    invalid_schema_path.write_text('{"name": "missing value"}', encoding="utf-8")
    invalid_schema = store.read_json(invalid_schema_path, ExampleModel)
    assert invalid_schema.ok is False
    assert invalid_schema.issues[0].kind == "schema_validation_failed"


def test_list_json_reports_bad_files(tmp_path) -> None:
    store = make_runtime().foundation.store
    store.write_json_atomic(tmp_path / "b.json", ExampleModel(name="b", value=2))
    store.write_json_atomic(tmp_path / "a.json", ExampleModel(name="a", value=1))
    (tmp_path / "bad.json").write_text("{bad", encoding="utf-8")

    listed = store.list_json(tmp_path, ExampleModel)

    assert listed.ok is False
    assert listed.issues[0].kind == "invalid_json"


def test_delete_json_handles_missing_ok_and_missing_error(tmp_path) -> None:
    store = make_runtime().foundation.store
    path = tmp_path / "delete.json"

    missing_ok = store.delete_json(path, missing_ok=True)
    missing_error = store.delete_json(path)
    store.write_json_atomic(path, ExampleModel(name="old", value=0))
    deleted = store.delete_json(path)

    assert missing_ok.ok is True
    assert missing_ok.value is not None and missing_ok.value.created is False and missing_ok.value.overwritten is False
    assert missing_error.ok is False
    assert missing_error.issues[0].kind == "missing_file"
    assert deleted.ok is True
    assert deleted.value is not None and deleted.value.overwritten is True
    assert not path.exists()


def test_ensure_dir_reports_created_and_existing(tmp_path) -> None:
    store = make_runtime().foundation.store
    path = tmp_path / "nested" / "dir"

    created = store.ensure_dir(path)
    existing = store.ensure_dir(path)

    assert created.ok is True
    assert created.value is not None and created.value.created is True and created.value.overwritten is False
    assert existing.ok is True
    assert existing.value is not None and existing.value.created is False and existing.value.overwritten is False


def test_exists_reports_files_directories_and_missing_paths(tmp_path) -> None:
    store = make_runtime().foundation.store
    file_path = tmp_path / "file.json"
    dir_path = tmp_path / "dir"
    file_path.write_text("{}", encoding="utf-8")
    dir_path.mkdir()

    assert store.exists(file_path) is True
    assert store.exists(dir_path) is True
    assert store.exists(tmp_path / "missing") is False


def test_temp_dir_promote_and_cleanup(tmp_path) -> None:
    store = make_runtime().foundation.store
    temp = store.create_temp_dir(tmp_path / "tmp", "resource")
    assert temp.ok is True and temp.value is not None
    (Path(temp.value) / "file.txt").write_text("content", encoding="utf-8")

    promoted = store.promote_dir_atomic(temp.value, tmp_path / "items" / "r1")

    assert promoted.ok is True
    assert (tmp_path / "items" / "r1" / "file.txt").read_text(encoding="utf-8") == "content"
    assert not Path(temp.value).exists()
    assert store.cleanup_temp_dir(temp.value).ok is True


def test_promote_dir_atomic_rejects_missing_temp_existing_target_and_updates_existing(tmp_path) -> None:
    store = make_runtime().foundation.store

    missing_temp = store.promote_dir_atomic(tmp_path / "missing-temp", tmp_path / "final")
    assert missing_temp.ok is False
    assert missing_temp.issues[0].kind == "missing_temp_dir"

    final_dir = tmp_path / "items" / "r1"
    final_dir.mkdir(parents=True)
    (final_dir / "old.txt").write_text("old", encoding="utf-8")
    temp_for_create_only = tmp_path / "tmp-create"
    temp_for_create_only.mkdir()
    duplicate = store.promote_dir_atomic(temp_for_create_only, final_dir, mode=WriteMode.CREATE_ONLY)
    assert duplicate.ok is False
    assert duplicate.issues[0].kind == "duplicate_directory"
    assert temp_for_create_only.exists()

    temp_for_update = tmp_path / "tmp-update"
    temp_for_update.mkdir()
    (temp_for_update / "new.txt").write_text("new", encoding="utf-8")
    updated = store.promote_dir_atomic(temp_for_update, final_dir, mode=WriteMode.UPDATE_EXISTING)

    assert updated.ok is True
    assert updated.value is not None and updated.value.created is False and updated.value.overwritten is True
    assert not temp_for_update.exists()
    assert not (final_dir / "old.txt").exists()
    assert (final_dir / "new.txt").read_text(encoding="utf-8") == "new"

    temp_for_missing_update = tmp_path / "tmp-missing-update"
    temp_for_missing_update.mkdir()
    missing_update = store.promote_dir_atomic(temp_for_missing_update, tmp_path / "absent", mode=WriteMode.UPDATE_EXISTING)
    assert missing_update.ok is False
    assert missing_update.issues[0].kind == "missing_directory"
    assert temp_for_missing_update.exists()


def test_mutation_session_commits_staged_writes_and_deletes(tmp_path) -> None:
    store = make_runtime().foundation.store
    delete_path = tmp_path / "delete.json"
    store.write_json_atomic(delete_path, ExampleModel(name="old", value=0))

    with store.mutation("batch") as tx:
        tx.stage_json(tmp_path / "new.json", ExampleModel(name="new", value=1), mode=WriteMode.CREATE_ONLY)
        tx.stage_delete(delete_path)
        commit = tx.commit()

    assert commit.ok is True
    assert (tmp_path / "new.json").exists()
    assert not delete_path.exists()


def test_mutation_session_rolls_back_on_context_exit_without_commit(tmp_path) -> None:
    store = make_runtime().foundation.store
    staged_path = tmp_path / "staged.json"

    with store.mutation("rollback") as tx:
        tx.stage_json(staged_path, ExampleModel(name="new", value=1), mode=WriteMode.CREATE_ONLY)

    assert not staged_path.exists()


def test_mutation_session_cleans_prepared_temp_files_after_preflight_failure(tmp_path) -> None:
    store = make_runtime().foundation.store
    existing_path = tmp_path / "existing.json"
    first_path = tmp_path / "first.json"
    store.write_json_atomic(existing_path, ExampleModel(name="existing", value=1))

    with store.mutation("preflight_fail") as tx:
        tx.stage_json(first_path, ExampleModel(name="first", value=1), mode=WriteMode.CREATE_ONLY)
        tx.stage_json(existing_path, ExampleModel(name="duplicate", value=2), mode=WriteMode.CREATE_ONLY)
        commit = tx.commit()

    assert commit.ok is False
    assert commit.issues[0].kind == "duplicate_file"
    assert not first_path.exists()
    assert not list(tmp_path.glob(".first.json.*.tmp"))


def test_allocate_uuid_retries_collisions() -> None:
    store = make_runtime().foundation.store
    seen: set[str] = set()

    def exists(value: str) -> bool:
        if not seen:
            seen.add(value)
            return True
        return False

    allocated = store.allocate_uuid(exists, prefix="flow")

    assert allocated.ok is True
    assert allocated.value is not None and allocated.value.startswith("flow_")


def test_ensure_open_version_copies_committed(tmp_path) -> None:
    store = make_runtime().foundation.store
    latest = VersionedModel(version=1, version_status="committed", payload="old")

    def copy_committed(value: VersionedModel) -> VersionedModel:
        return VersionedModel(version=value.version + 1, version_status="open", payload=value.payload)

    result = store.ensure_open_version(
        load_latest=lambda: latest,
        copy_committed=copy_committed,
        path_for_version=lambda version: tmp_path / f"{version}.json",
    )

    assert result.ok is True
    assert result.value is not None
    assert result.value.version == 2
    assert (tmp_path / "2.json").exists()


def test_ensure_open_version_returns_existing_open_and_reports_missing_base(tmp_path) -> None:
    store = make_runtime().foundation.store
    open_version = VersionedModel(version=3, version_status="open", payload="draft")

    existing = store.ensure_open_version(
        load_latest=lambda: open_version,
        copy_committed=lambda value: VersionedModel(version=value.version + 1, version_status="open", payload=value.payload),
        path_for_version=lambda version: tmp_path / f"{version}.json",
    )
    missing = store.ensure_open_version(
        load_latest=lambda: None,
        copy_committed=lambda value: value,
        path_for_version=lambda version: tmp_path / f"{version}.json",
    )

    assert existing.ok is True
    assert existing.value is not None
    assert existing.value.value == open_version
    assert existing.value.version == 3
    assert existing.value.created_new_open is False
    assert not (tmp_path / "3.json").exists()
    assert missing.ok is False
    assert missing.issues[0].kind == "missing_version"
