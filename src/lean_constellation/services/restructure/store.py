"""Durable JSON/CAS store for Restructure metadata."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from threading import RLock, local
from typing import Any, Iterator, TypeVar

from pydantic import BaseModel

from lean_constellation.domain.restructure import ArtifactManifest, BuildReceipt, ContentWork, RepoPlan, Reservation, WorkspacePlan, WorkspaceRun

T = TypeVar("T", bound=BaseModel)


class MetadataLock:
    """Reentrant lock shared by all service instances and server processes."""
    def __init__(self, path: Path):
        self.path = path
        self.thread_lock = RLock()
        self.state = local()

    def __enter__(self):
        self.thread_lock.acquire()
        try:
            if not getattr(self.state, "depth", 0):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.state.handle = self.path.open("a")
                fcntl.flock(self.state.handle, fcntl.LOCK_EX)
            self.state.depth = getattr(self.state, "depth", 0) + 1
        except BaseException:
            self.thread_lock.release()
            raise
        return self

    def __exit__(self, *exc):
        self.state.depth -= 1
        if not self.state.depth:
            fcntl.flock(self.state.handle, fcntl.LOCK_UN)
            self.state.handle.close()
        self.thread_lock.release()


_LOCKS: dict[Path, MetadataLock] = {}
_LOCKS_GUARD = RLock()


def metadata_lock(path: Path) -> MetadataLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(path.resolve(), MetadataLock(path.resolve()))


class StoreConflict(RuntimeError):
    """Raised when a metadata write uses an old version."""


class RestructureStore:
    """Recoverable metadata store; ARK remains execution truth."""

    def __init__(self, workspace_root: Path | str) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.root = self.workspace_root / ".lean_constellation" / "restructure"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = metadata_lock(self.root / "metadata.lock")

    @property
    def workspace_plan_path(self) -> Path:
        return self.root / "workspace.json"

    @property
    def run_path(self) -> Path:
        return self.root / "run.json"

    def repo_root(self, directory: str) -> Path:
        if len(Path(directory).parts) != 1:
            raise ValueError("restructure repos must be direct workspace children")
        candidate = (self.workspace_root / directory).resolve()
        if self.workspace_root not in (candidate, *candidate.parents):
            raise ValueError("repo directory escapes workspace root")
        return candidate

    def repo_metadata_root(self, directory: str) -> Path:
        return self.repo_root(directory) / ".lean_constellation" / "restructure"

    def read_json(self, path: Path, default: Any = None) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default

    def write_json(self, path: Path, value: Any, *, expected_version: int | None = None) -> int:
        with self._lock:
            payload = self.read_json(path, {}) or {}
            current = int(payload.get("_version", 0)) if isinstance(payload, dict) else 0
            if expected_version is not None and current != expected_version:
                raise StoreConflict(f"expected version {expected_version}, found {current}: {path}")
            version = current + 1
            encoded = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            if not isinstance(encoded, dict):
                encoded = {"value": encoded}
            encoded = {**encoded, "_version": version}
            self._atomic_write(path, encoded)
            return version

    def load_model(self, path: Path, model: type[T]) -> tuple[T | None, int]:
        payload = self.read_json(path)
        if payload is None:
            return None, 0
        if not isinstance(payload, dict):
            raise ValueError(f"metadata file is not a JSON object: {path}")
        data = dict(payload)
        version = int(data.pop("_version", 0))
        return model.model_validate(data), version

    def save_workspace_plan(self, plan: WorkspacePlan, *, expected_version: int | None = None) -> int:
        return self.write_json(self.workspace_plan_path, plan, expected_version=expected_version)

    def load_workspace_plan(self) -> tuple[WorkspacePlan | None, int]:
        return self.load_model(self.workspace_plan_path, WorkspacePlan)

    def save_run(self, run: WorkspaceRun, *, expected_version: int | None = None) -> int:
        return self.write_json(self.run_path, run, expected_version=expected_version)

    def load_run(self) -> tuple[WorkspaceRun | None, int]:
        return self.load_model(self.run_path, WorkspaceRun)

    def save_repo_plan(self, directory: str, plan: BaseModel, *, expected_version: int | None = None) -> int:
        root = self.repo_metadata_root(directory)
        root.mkdir(parents=True, exist_ok=True)
        return self.write_json(root / "plan.json", plan, expected_version=expected_version)

    def load_repo_plan(self, directory: str, model: type[T]) -> tuple[T | None, int]:
        return self.load_model(self.repo_metadata_root(directory) / "plan.json", model)

    def save_content(self, directory: str, node_path: str, value: BaseModel, *, expected_version: int | None = None) -> int:
        value = ContentWork.model_validate(value.model_dump(mode="json"))
        root = self.repo_metadata_root(directory) / "content"
        root.mkdir(parents=True, exist_ok=True)
        return self.write_json(root / f"{node_path.replace('.', '__')}.json", value, expected_version=expected_version)

    def load_content(self, directory: str, node_path: str, model: type[T]) -> tuple[T | None, int]:
        path = self.repo_metadata_root(directory) / "content" / f"{node_path.replace('.', '__')}.json"
        return self.load_model(path, model)

    def save_artifact(self, directory: str, artifact: ArtifactManifest) -> int:
        root = self.repo_metadata_root(directory) / "artifacts"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{artifact.artifact_id}.json"
        if path.exists():
            existing, version = self.load_model(path, ArtifactManifest)
            if existing is not None and existing.model_dump(mode="json") != artifact.model_dump(mode="json"):
                raise StoreConflict(f"artifact is immutable: {artifact.artifact_id}")
            return version
        return self.write_json(path, artifact)

    def load_artifact(self, directory: str, artifact_id: str) -> ArtifactManifest | None:
        artifact, _ = self.load_model(
            self.repo_metadata_root(directory) / "artifacts" / f"{artifact_id}.json",
            ArtifactManifest,
        )
        return artifact

    def save_build_receipt(self, directory: str, receipt: BuildReceipt) -> Path:
        root = self.repo_metadata_root(directory) / "builds"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{receipt.operation_id}.json"
        self._atomic_write(path, receipt.model_dump(mode="json"))
        return path

    def list_build_receipts(self, directory: str) -> list[BuildReceipt]:
        root = self.repo_metadata_root(directory) / "builds"
        if not root.is_dir():
            return []
        return [BuildReceipt.model_validate(self.read_json(path, {}) or {}) for path in sorted(root.glob("*.json"))]

    def load_build_receipt(self, directory: str, operation_id: str) -> BuildReceipt | None:
        path = self.repo_metadata_root(directory) / "builds" / f"{operation_id}.json"
        payload = self.read_json(path)
        return BuildReceipt.model_validate(payload) if payload is not None else None

    def reservation_path(self) -> Path:
        return self.root / "reservations.json"

    def load_reservations(self) -> list[Reservation]:
        payload = self.read_json(self.reservation_path(), []) or []
        if isinstance(payload, dict):
            payload = payload.get("reservations", [])
        return [Reservation.model_validate(item) for item in payload]

    def save_reservations(self, reservations: list[Reservation]) -> int:
        return self.write_json(self.reservation_path(), {"reservations": [item.model_dump(mode="json") for item in reservations]})

    def append_intent(self, directory: str, intent: dict[str, Any]) -> Path:
        root = self.repo_metadata_root(directory) / "intents"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{str(intent.get('request_id') or 'intent')}.json"
        self._atomic_write(path, intent)
        return path

    def list_intents(self, directory: str) -> list[dict[str, Any]]:
        root = self.repo_metadata_root(directory) / "intents"
        if not root.is_dir():
            return []
        return [self.read_json(path, {}) or {} for path in sorted(root.glob("*.json"))]

    def clear_intent(self, directory: str, request_id: str) -> None:
        path = self.repo_metadata_root(directory) / "intents" / f"{request_id}.json"
        path.unlink(missing_ok=True)

    @contextmanager
    def repo_lock(self, directory: str) -> Iterator[None]:
        self.repo_root(directory)
        with self._lock:
            yield

    @staticmethod
    def _atomic_write(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
            try:
                dir_fd = os.open(path.parent, os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except (AttributeError, OSError):
                pass
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass
