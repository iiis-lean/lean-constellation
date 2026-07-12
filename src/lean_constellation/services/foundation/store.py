"""Atomic JSON store and small mutation helper."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel, Field, TypeAdapter

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation.result_error import (
    ResultErrorComponent,
    ServiceResult,
)

T = TypeVar("T")

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class WriteMode(StrEnum):
    CREATE_ONLY = "create_only"
    OVERWRITE = "overwrite"
    UPDATE_EXISTING = "update_existing"


class StoreWriteResult(StrictModel):
    path: str
    created: bool
    overwritten: bool
    summary: str


class MutationCommitResult(StrictModel):
    written_paths: list[str] = Field(default_factory=list)
    deleted_paths: list[str] = Field(default_factory=list)
    summary: str


class OpenVersionResult(StrictModel, Generic[T]):
    value: T
    version: int
    created_new_open: bool
    path: str


class _StagedWrite(StrictModel):
    path: Path
    value: Any
    mode: WriteMode


class _StagedDelete(StrictModel):
    path: Path
    missing_ok: bool


@dataclass(frozen=True)
class _OriginalFileState:
    existed: bool
    contents: bytes | None


class MutationSession:
    """A small single-process staging helper for related store writes."""

    def __init__(self, store: "StoreComponent", action_name: str) -> None:
        if not action_name or not action_name.strip():
            raise ValueError("action_name must be non-empty")
        self._store = store
        self.action_name = action_name.strip()
        self._staged: list[_StagedWrite | _StagedDelete] = []
        self._committed = False
        self._closed = False

    def __enter__(self) -> "MutationSession":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if not self._committed:
            self.rollback()

    def read(self, path: Path, model_type: type[T]) -> T:
        result = self._store.read_json(path, model_type)
        if not result.ok:
            messages = "; ".join(issue.message for issue in result.issues)
            raise RuntimeError(f"mutation read failed: {messages}")
        return result.value  # type: ignore[return-value]

    def stage_json(self, path: Path, value: Any, *, mode: WriteMode = WriteMode.OVERWRITE) -> None:
        self._assert_open()
        self._staged.append(_StagedWrite(path=Path(path), value=value, mode=WriteMode(mode)))

    def stage_delete(self, path: Path, *, missing_ok: bool = False) -> None:
        self._assert_open()
        self._staged.append(_StagedDelete(path=Path(path), missing_ok=missing_ok))

    def commit(self) -> ServiceResult[MutationCommitResult]:
        self._assert_open()
        prepared_writes: list[tuple[_StagedWrite, Path]] = []
        original_states: dict[Path, _OriginalFileState] = {}
        applied_paths: list[Path] = []
        try:
            for item in self._staged:
                if item.path not in original_states:
                    original_states[item.path] = self._capture_original_state(item.path)
                if isinstance(item, _StagedWrite):
                    preflight = self._store._check_write_mode(item.path, item.mode)
                    if not preflight.ok:
                        self._cleanup_prepared_writes(prepared_writes)
                        return preflight  # type: ignore[return-value]
                    prepared_writes.append((item, self._store._write_temp_json(item.path, item.value)))
                else:
                    if not item.path.exists() and not item.missing_ok:
                        self._cleanup_prepared_writes(prepared_writes)
                        return self._store.result.fail(
                            self._store.result.issue(
                                "missing_file",
                                f"Cannot delete missing file: {item.path}",
                                details={"path": str(item.path)},
                            )
                        )

            written: list[str] = []
            deleted: list[str] = []
            temp_by_path = {write.path: temp_path for write, temp_path in prepared_writes}
            for item in self._staged:
                if isinstance(item, _StagedWrite):
                    os.replace(temp_by_path[item.path], item.path)
                    applied_paths.append(item.path)
                    self._store._fsync_parent(item.path)
                    written.append(str(item.path))
                else:
                    if item.path.exists():
                        item.path.unlink()
                        applied_paths.append(item.path)
                        self._store._fsync_parent(item.path)
                        deleted.append(str(item.path))

            self._committed = True
            self._closed = True
            return self._store.result.ok(
                MutationCommitResult(
                    written_paths=written,
                    deleted_paths=deleted,
                    summary=f"{self.action_name}: {len(written)} writes, {len(deleted)} deletes",
                )
            )
        except Exception as exc:  # noqa: BLE001 - converted to ServiceResult.
            rollback_failures = self._restore_original_states(original_states, applied_paths)
            self._cleanup_prepared_writes(prepared_writes)
            issues = [
                self._store.result.issue(
                    "mutation_commit_failed",
                    f"Mutation commit failed: {exc}",
                    details={"action_name": self.action_name},
                )
            ]
            if rollback_failures:
                issues.append(
                    self._store.result.issue(
                        "mutation_rollback_failed",
                        "Mutation rollback also failed after the commit error.",
                        details={
                            "action_name": self.action_name,
                            "failures": "; ".join(rollback_failures),
                        },
                    )
                )
            return self._store.result.fail(issues)

    def rollback(self) -> None:
        self._staged.clear()
        self._closed = True

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("mutation session is closed")

    @staticmethod
    def _capture_original_state(path: Path) -> _OriginalFileState:
        if not path.exists():
            return _OriginalFileState(existed=False, contents=None)
        return _OriginalFileState(existed=True, contents=path.read_bytes())

    def _restore_original_states(
        self,
        original_states: dict[Path, _OriginalFileState],
        applied_paths: list[Path],
    ) -> list[str]:
        failures: list[str] = []
        restored: set[Path] = set()
        for path in reversed(applied_paths):
            if path in restored:
                continue
            restored.add(path)
            state = original_states[path]
            rollback_temp: Path | None = None
            try:
                if state.existed:
                    if state.contents is None:
                        raise RuntimeError("existing file snapshot has no contents")
                    rollback_temp = self._write_temp_bytes(path, state.contents)
                    os.replace(rollback_temp, path)
                    self._store._fsync_parent(path)
                elif path.exists():
                    path.unlink()
                    self._store._fsync_parent(path)
            except Exception as exc:  # noqa: BLE001 - preserve the original commit issue too.
                failures.append(f"{path}: {exc}")
            finally:
                if rollback_temp is not None and rollback_temp.exists():
                    rollback_temp.unlink(missing_ok=True)
        return failures

    @staticmethod
    def _write_temp_bytes(path: Path, contents: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_path = tempfile.mkstemp(prefix=f".{path.name}.rollback-", dir=path.parent)
        temp_path = Path(raw_path)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(contents)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        return temp_path

    @staticmethod
    def _cleanup_prepared_writes(prepared_writes: list[tuple[_StagedWrite, Path]]) -> None:
        for _, temp_path in prepared_writes:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)


class StoreComponent:
    """Read, write, and stage JSON truth files."""

    def __init__(self, runtime: LeanRuntimeServices, result: ResultErrorComponent | None = None) -> None:
        self.runtime = runtime
        self.result = result or ResultErrorComponent()

    def read_json(self, path: Path, model_type: type[T]) -> ServiceResult[T]:
        path = Path(path)
        if not path.exists():
            return self.result.fail(
                self.result.issue("missing_file", f"JSON file does not exist: {path}", details={"path": str(path)})
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return self.result.fail(
                self.result.issue(
                    "invalid_json",
                    f"Invalid JSON in {path}: {exc.msg}",
                    details={"path": str(path), "line": str(exc.lineno), "column": str(exc.colno)},
                )
            )
        except OSError as exc:
            return self.result.fail(
                self.result.issue("read_failed", f"Failed to read {path}: {exc}", details={"path": str(path)})
            )
        try:
            value = TypeAdapter(model_type).validate_python(raw)
        except Exception as exc:  # noqa: BLE001 - pydantic raises several validation exceptions.
            return self.result.fail(
                self.result.issue(
                    "schema_validation_failed",
                    f"JSON schema validation failed for {path}: {exc}",
                    details={"path": str(path), "model_type": getattr(model_type, "__name__", str(model_type))},
                )
            )
        return self.result.ok(value)

    def write_json_atomic(
        self,
        path: Path,
        value: Any,
        *,
        mode: WriteMode = WriteMode.OVERWRITE,
    ) -> ServiceResult[StoreWriteResult]:
        path = Path(path)
        mode = WriteMode(mode)
        preflight = self._check_write_mode(path, mode)
        if not preflight.ok:
            return preflight
        existed = path.exists()
        try:
            temp_path = self._write_temp_json(path, value)
            os.replace(temp_path, path)
            self._fsync_parent(path)
        except Exception as exc:  # noqa: BLE001 - normalized as ServiceResult.
            if "temp_path" in locals() and temp_path.exists():
                temp_path.unlink(missing_ok=True)
            return self.result.fail(
                self.result.issue("write_failed", f"Failed to write JSON {path}: {exc}", details={"path": str(path)})
            )
        return self.result.ok(
            StoreWriteResult(
                path=str(path),
                created=not existed,
                overwritten=existed,
                summary=f"Wrote JSON file {path}",
            )
        )

    def delete_json(self, path: Path, *, missing_ok: bool = False) -> ServiceResult[StoreWriteResult]:
        path = Path(path)
        if not path.exists():
            if missing_ok:
                return self.result.ok(
                    StoreWriteResult(path=str(path), created=False, overwritten=False, summary="File already absent")
                )
            return self.result.fail(
                self.result.issue("missing_file", f"Cannot delete missing file: {path}", details={"path": str(path)})
            )
        try:
            path.unlink()
            self._fsync_parent(path)
        except OSError as exc:
            return self.result.fail(
                self.result.issue("delete_failed", f"Failed to delete {path}: {exc}", details={"path": str(path)})
            )
        return self.result.ok(
            StoreWriteResult(path=str(path), created=False, overwritten=True, summary=f"Deleted {path}")
        )

    def ensure_dir(self, path: Path) -> ServiceResult[StoreWriteResult]:
        path = Path(path)
        existed = path.exists()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return self.result.fail(
                self.result.issue("ensure_dir_failed", f"Failed to create directory {path}: {exc}")
            )
        return self.result.ok(
            StoreWriteResult(
                path=str(path),
                created=not existed,
                overwritten=False,
                summary=("Directory already exists" if existed else f"Created directory {path}"),
            )
        )

    def list_json(self, path: Path, model_type: type[T]) -> ServiceResult[list[T]]:
        path = Path(path)
        if not path.exists():
            return self.result.fail(
                self.result.issue("missing_directory", f"JSON directory does not exist: {path}")
            )
        if not path.is_dir():
            return self.result.fail(self.result.issue("not_directory", f"Path is not a directory: {path}"))
        values: list[T] = []
        issues = []
        for item in sorted(path.glob("*.json")):
            result = self.read_json(item, model_type)
            if result.ok:
                values.append(result.value)  # type: ignore[arg-type]
            else:
                issues.extend(result.issues)
        if issues:
            return self.result.fail(issues)
        return self.result.ok(values)

    def exists(self, path: Path) -> bool:
        return Path(path).exists()

    def create_temp_dir(self, base: Path, prefix: str) -> ServiceResult[Path]:
        try:
            Path(base).mkdir(parents=True, exist_ok=True)
            temp_dir = tempfile.mkdtemp(prefix=f"{prefix}-", dir=base)
        except OSError as exc:
            return self.result.fail(
                self.result.issue("temp_dir_failed", f"Failed to create temp dir under {base}: {exc}")
            )
        return self.result.ok(Path(temp_dir))

    def promote_dir_atomic(
        self,
        temp_dir: Path,
        final_dir: Path,
        *,
        mode: WriteMode = WriteMode.CREATE_ONLY,
    ) -> ServiceResult[StoreWriteResult]:
        temp_dir = Path(temp_dir)
        final_dir = Path(final_dir)
        mode = WriteMode(mode)
        if not temp_dir.exists() or not temp_dir.is_dir():
            return self.result.fail(self.result.issue("missing_temp_dir", f"Temp directory does not exist: {temp_dir}"))
        if mode == WriteMode.CREATE_ONLY and final_dir.exists():
            return self.result.fail(self.result.issue("duplicate_directory", f"Directory already exists: {final_dir}"))
        if mode == WriteMode.UPDATE_EXISTING and not final_dir.exists():
            return self.result.fail(self.result.issue("missing_directory", f"Directory does not exist: {final_dir}"))
        existed = final_dir.exists()
        try:
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            if existed:
                shutil.rmtree(final_dir)
            os.replace(temp_dir, final_dir)
            self._fsync_parent(final_dir)
        except OSError as exc:
            return self.result.fail(
                self.result.issue(
                    "promote_dir_failed",
                    f"Failed to promote {temp_dir} to {final_dir}: {exc}",
                    details={"temp_dir": str(temp_dir), "final_dir": str(final_dir)},
                )
            )
        return self.result.ok(
            StoreWriteResult(
                path=str(final_dir),
                created=not existed,
                overwritten=existed,
                summary=f"Promoted directory {temp_dir} to {final_dir}",
            )
        )

    def cleanup_temp_dir(self, temp_dir: Path) -> ServiceResult[StoreWriteResult]:
        temp_dir = Path(temp_dir)
        if temp_dir.exists():
            try:
                shutil.rmtree(temp_dir)
            except OSError as exc:
                return self.result.fail(
                    self.result.issue("cleanup_temp_dir_failed", f"Failed to remove {temp_dir}: {exc}")
                )
        return self.result.ok(
            StoreWriteResult(path=str(temp_dir), created=False, overwritten=False, summary="Temp directory removed")
        )

    def mutation(self, action_name: str) -> MutationSession:
        return MutationSession(self, action_name)

    def ensure_open_version(
        self,
        *,
        load_latest: Callable[[], Any],
        copy_committed: Callable[[Any], Any],
        path_for_version: Callable[[int], Path],
    ) -> ServiceResult[OpenVersionResult[Any]]:
        latest = load_latest()
        if latest is None:
            return self.result.fail(self.result.issue("missing_version", "No latest version exists."))

        status = getattr(latest, "version_status", getattr(latest, "status", None))
        version = int(getattr(latest, "version", 1))
        if status == "open":
            return self.result.ok(
                OpenVersionResult(value=latest, version=version, created_new_open=False, path=str(path_for_version(version)))
            )

        new_value = copy_committed(latest)
        new_version = int(getattr(new_value, "version", version + 1))
        write = self.write_json_atomic(path_for_version(new_version), new_value, mode=WriteMode.CREATE_ONLY)
        if not write.ok:
            return write  # type: ignore[return-value]
        return self.result.ok(
            OpenVersionResult(
                value=new_value,
                version=new_version,
                created_new_open=True,
                path=str(path_for_version(new_version)),
            )
        )

    def allocate_uuid(
        self,
        exists: Callable[[str], bool],
        *,
        prefix: str | None = None,
        max_attempts: int = 20,
    ) -> ServiceResult[str]:
        for _ in range(max_attempts):
            candidate = uuid.uuid4().hex
            if prefix:
                candidate = f"{prefix}_{candidate}"
            if not exists(candidate):
                return self.result.ok(candidate)
        return self.result.fail(self.result.issue("id_allocation_failed", "Could not allocate a unique id."))

    def _check_write_mode(self, path: Path, mode: WriteMode) -> ServiceResult[StoreWriteResult]:
        exists = Path(path).exists()
        if mode == WriteMode.CREATE_ONLY and exists:
            return self.result.fail(self.result.issue("duplicate_file", f"File already exists: {path}"))
        if mode == WriteMode.UPDATE_EXISTING and not exists:
            return self.result.fail(self.result.issue("missing_file", f"File must exist before update: {path}"))
        return self.result.ok(
            StoreWriteResult(path=str(path), created=not exists, overwritten=exists, summary="Write preflight passed")
        )

    def _write_temp_json(self, path: Path, value: Any) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        payload = self._jsonable(value)
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return temp_path

    def _jsonable(self, value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        return TypeAdapter(Any).dump_python(value, mode="json")

    def _fsync_parent(self, path: Path) -> None:
        try:
            directory_fd = os.open(Path(path).parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
