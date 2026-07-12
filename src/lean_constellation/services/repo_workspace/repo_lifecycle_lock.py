"""Short-lived cross-process lock for repo lifecycle mutations."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path
from typing import Iterator, TYPE_CHECKING

from lean_constellation.services.foundation import FoundationContext

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class RepoLifecycleLockBusyError(RuntimeError):
    pass


class RepoLifecycleLockComponent:
    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    @contextmanager
    def locked(self, repo_root: Path) -> Iterator[Path]:
        path = self.runtime.foundation.layout.repo_lifecycle_lock_path(
            FoundationContext(repo_root=Path(repo_root))
        )
        self.runtime.foundation.store.ensure_dir(path.parent)
        handle = path.open("a+", encoding="utf-8")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RepoLifecycleLockBusyError(f"Repo lifecycle lock is busy: {path}") from exc
            yield path
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


__all__ = ["RepoLifecycleLockBusyError", "RepoLifecycleLockComponent"]
