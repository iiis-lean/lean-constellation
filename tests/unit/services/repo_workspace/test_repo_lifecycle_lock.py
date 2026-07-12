from __future__ import annotations

import multiprocessing
from pathlib import Path
from threading import Event, Thread

from lean_constellation.app import create_app_runtime_services
from lean_constellation.services.repo_workspace.repo_lifecycle_lock import RepoLifecycleLockBusyError


def _hold_process_lock(runtime_root: str, repo_root: str, ready, release) -> None:  # noqa: ANN001
    runtime = create_app_runtime_services(runtime_root=Path(runtime_root))
    with runtime.repo_workspace.lifecycle_lock.locked(Path(repo_root)):
        ready.set()
        release.wait(10)


def test_repo_lifecycle_lock_rejects_competing_thread(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    root = tmp_path / "Repo"
    ready, release = Event(), Event()

    def hold() -> None:
        with runtime.repo_workspace.lifecycle_lock.locked(root):
            ready.set()
            release.wait(10)

    thread = Thread(target=hold)
    thread.start()
    assert ready.wait(5)
    try:
        with runtime.repo_workspace.lifecycle_lock.locked(root):
            raise AssertionError("competing thread unexpectedly acquired lifecycle lock")
    except RepoLifecycleLockBusyError:
        pass
    finally:
        release.set()
        thread.join(5)


def test_repo_lifecycle_lock_rejects_competing_process(tmp_path) -> None:
    runtime_root, root = tmp_path / ".runtime", tmp_path / "Repo"
    ctx = multiprocessing.get_context("fork")
    ready, release = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_hold_process_lock, args=(str(runtime_root), str(root), ready, release))
    process.start()
    assert ready.wait(5)
    runtime = create_app_runtime_services(runtime_root=runtime_root)
    try:
        with runtime.repo_workspace.lifecycle_lock.locked(root):
            raise AssertionError("competing process unexpectedly acquired lifecycle lock")
    except RepoLifecycleLockBusyError:
        pass
    finally:
        release.set()
        process.join(5)
    assert process.exitcode == 0
