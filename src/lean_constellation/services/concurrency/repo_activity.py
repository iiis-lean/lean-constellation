"""Ephemeral ownership and short transaction locks for one repository runtime."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, IO, Iterator

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class RepoActivityConflictError(RuntimeError):
    """Raised when an incompatible repository activity is already reserved."""


class RepoActivityRecoveryRequiredError(RepoActivityConflictError):
    """Raised when persisted activity cannot be read or identified safely."""


@dataclass(frozen=True)
class ContentBatchReservation:
    batch_id: str
    repo_root: Path
    node_paths: tuple[str, ...]


class RepoRuntimeWriterLease:
    """Server-lifetime cross-process writer lease keyed by canonical repo root."""

    def __init__(self, repo_root: Path) -> None:
        canonical = Path(repo_root).resolve(strict=False)
        digest = hashlib.sha256(str(canonical).encode("utf-8")).hexdigest()
        lock_root = (
            canonical.parent
            / ".lean_constellation_workspace"
            / ".locks"
            / "runtime_writers"
        )
        self.path = lock_root / f"{digest}.lock"
        self.repo_root = canonical
        self._handle: IO[str] | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RepoActivityConflictError(
                f"A writable LC runtime already owns repository root {self.repo_root}."
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(self.repo_root))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class RepoActivityComponent:
    """Coordinate ephemeral batch ownership without creating durable truth."""

    _PERSISTED_BATCH_PHASES = {
        "before_content_task_dispatch_snapshot",
        "dispatch_content_tasks",
        "waiting_content_tasks",
        "after_content_task_batch_snapshot",
        "coordinator_callback",
    }

    def __init__(self, runtime: "LeanRuntimeServices | None" = None) -> None:
        self._runtime = runtime
        self._control = RLock()
        self._topology: dict[Path, RLock] = {}
        self._nodes: dict[tuple[Path, str], RLock] = {}
        self._catalogs: dict[tuple[Path, str], RLock] = {}
        self._build_caches: dict[Path, RLock] = {}
        self._batches: dict[tuple[Path, str], ContentBatchReservation] = {}
        self._node_owners: dict[tuple[Path, str], str] = {}
        self._persisted_batch_keys: set[tuple[Path, str]] = set()
        self._maintenance: dict[Path, str] = {}
        self._active_transactions: dict[Path, int] = {}

    @staticmethod
    def canonical_root(repo_root: Path) -> Path:
        return Path(repo_root).resolve(strict=False)

    def reserve_content_batch(
        self,
        repo_root: Path,
        *,
        batch_id: str,
        node_paths: list[str] | tuple[str, ...],
    ) -> ContentBatchReservation:
        root = self.canonical_root(repo_root)
        normalized = tuple(sorted(dict.fromkeys(path.strip() for path in node_paths if path.strip())))
        if not normalized:
            raise RepoActivityConflictError("Content batch reservation requires at least one node path.")
        self._rehydrate_persisted_batches(root)
        key = (root, batch_id)
        with self._control:
            existing = self._batches.get(key)
            if existing is not None:
                if existing.node_paths != normalized:
                    raise RepoActivityConflictError(
                        f"Content batch identity {batch_id} is already bound to different nodes."
                    )
                return existing
            maintenance = self._maintenance.get(root)
            if maintenance is not None:
                raise RepoActivityConflictError(
                    f"Repository maintenance is active and blocks content batch admission: {maintenance}."
                )
            conflicts = {
                path: self._node_owners[(root, path)]
                for path in normalized
                if (root, path) in self._node_owners
            }
            if conflicts:
                detail = ", ".join(f"{path}={owner}" for path, owner in sorted(conflicts.items()))
                raise RepoActivityConflictError(f"Content nodes already have active owners: {detail}.")
            reservation = ContentBatchReservation(batch_id=batch_id, repo_root=root, node_paths=normalized)
            self._batches[key] = reservation
            for path in normalized:
                self._node_owners[(root, path)] = batch_id
            return reservation

    def release_content_batch(self, repo_root: Path, *, batch_id: str) -> None:
        root = self.canonical_root(repo_root)
        with self._control:
            reservation = self._batches.pop((root, batch_id), None)
            self._persisted_batch_keys.discard((root, batch_id))
            if reservation is None:
                return
            for path in reservation.node_paths:
                owner_key = (root, path)
                if self._node_owners.get(owner_key) == batch_id:
                    self._node_owners.pop(owner_key, None)

    def release_all_content_batches(self) -> None:
        with self._control:
            self._batches.clear()
            self._node_owners.clear()
            self._persisted_batch_keys.clear()

    def active_batches(self, repo_root: Path) -> tuple[ContentBatchReservation, ...]:
        root = self.canonical_root(repo_root)
        self._rehydrate_persisted_batches(root)
        with self._control:
            return tuple(
                reservation
                for (candidate_root, _), reservation in sorted(
                    self._batches.items(), key=lambda item: item[0][1]
                )
                if candidate_root == root
            )

    def batch_for_node(self, repo_root: Path, node_path: str) -> ContentBatchReservation | None:
        root = self.canonical_root(repo_root)
        self._rehydrate_persisted_batches(root)
        with self._control:
            batch_id = self._node_owners.get((root, node_path))
            return self._batches.get((root, batch_id)) if batch_id is not None else None

    def has_active_transactions(self, repo_root: Path) -> bool:
        root = self.canonical_root(repo_root)
        with self._control:
            return self._active_transactions.get(root, 0) > 0

    @contextmanager
    def maintenance(
        self,
        repo_root: Path,
        *,
        owner: str,
        compatible_batch_id: str | None = None,
    ) -> Iterator[None]:
        root = self.canonical_root(repo_root)
        self._rehydrate_persisted_batches(root)
        with self._control:
            active_batch_ids = {
                batch_id for candidate_root, batch_id in self._batches if candidate_root == root
            }
            if active_batch_ids and active_batch_ids != {compatible_batch_id}:
                raise RepoActivityConflictError("Active content batch blocks repository maintenance.")
            if self._active_transactions.get(root, 0) > 0:
                raise RepoActivityConflictError("Active repository transactions block repository maintenance.")
            existing = self._maintenance.get(root)
            if existing is not None and existing != owner:
                raise RepoActivityConflictError(f"Repository maintenance is already owned by {existing}.")
            self._maintenance[root] = owner
        try:
            yield
        finally:
            with self._control:
                if self._maintenance.get(root) == owner:
                    self._maintenance.pop(root, None)

    @contextmanager
    def topology_write(self, repo_root: Path) -> Iterator[None]:
        root = self.canonical_root(repo_root)
        self._rehydrate_persisted_batches(root)
        with self._control:
            lock = self._topology.setdefault(root, RLock())
        with self._transaction(root, lock):
            yield

    @contextmanager
    def node_write(self, repo_root: Path, *node_paths: str) -> Iterator[None]:
        root = self.canonical_root(repo_root)
        normalized = tuple(sorted(dict.fromkeys(path.strip() for path in node_paths if path.strip())))
        with self._control:
            locks = [self._nodes.setdefault((root, path), RLock()) for path in normalized]
        with ExitStack() as stack:
            for lock in locks:
                stack.enter_context(self._transaction(root, lock))
            yield

    @contextmanager
    def catalog_write(self, repo_root: Path, catalog: str) -> Iterator[None]:
        root = self.canonical_root(repo_root)
        with self._control:
            lock = self._catalogs.setdefault((root, catalog), RLock())
        with self._transaction(root, lock):
            yield

    @contextmanager
    def catalog_then_nodes(
        self,
        repo_root: Path,
        catalog: str,
        *node_paths: str,
    ) -> Iterator[None]:
        with self.catalog_write(repo_root, catalog):
            with self.node_write(repo_root, *node_paths):
                yield

    @contextmanager
    def build_cache_write(self, repo_root: Path) -> Iterator[None]:
        """Serialize short shared Lean build/cache mutations for one repository."""

        root = self.canonical_root(repo_root)
        with self._control:
            lock = self._build_caches.setdefault(root, RLock())
        with self._transaction(root, lock):
            yield

    @contextmanager
    def _transaction(self, root: Path, lock: RLock) -> Iterator[None]:
        lock.acquire()
        try:
            with self._control:
                maintenance = self._maintenance.get(root)
                if maintenance is not None:
                    raise RepoActivityConflictError(
                        f"Repository maintenance is active and blocks new transactions: {maintenance}."
                    )
                self._active_transactions[root] = self._active_transactions.get(root, 0) + 1
        except Exception:
            lock.release()
            raise
        try:
            yield
        finally:
            with self._control:
                remaining = self._active_transactions.get(root, 1) - 1
                if remaining > 0:
                    self._active_transactions[root] = remaining
                else:
                    self._active_transactions.pop(root, None)
            lock.release()

    def _rehydrate_persisted_batches(self, repo_root: Path) -> None:
        """Rebuild ephemeral ownership from the current Coordinator frontier."""

        persisted = self._persisted_content_batches(repo_root)
        current_keys = {(repo_root, reservation.batch_id) for reservation in persisted}
        with self._control:
            stale_keys = {
                key
                for key in self._persisted_batch_keys
                if key[0] == repo_root and key not in current_keys
            }
            for key in stale_keys:
                reservation = self._batches.pop(key, None)
                if reservation is not None:
                    for path in reservation.node_paths:
                        owner_key = (repo_root, path)
                        if self._node_owners.get(owner_key) == reservation.batch_id:
                            self._node_owners.pop(owner_key, None)
                self._persisted_batch_keys.discard(key)
            for reservation in persisted:
                key = (repo_root, reservation.batch_id)
                if key in self._batches:
                    self._persisted_batch_keys.add(key)
                    continue
                conflicts = [
                    path
                    for path in reservation.node_paths
                    if (repo_root, path) in self._node_owners
                    and self._node_owners[(repo_root, path)] != reservation.batch_id
                ]
                if conflicts:
                    continue
                self._batches[key] = reservation
                self._persisted_batch_keys.add(key)
                for path in reservation.node_paths:
                    self._node_owners[(repo_root, path)] = reservation.batch_id

    def _persisted_content_batches(
        self,
        repo_root: Path,
    ) -> tuple[ContentBatchReservation, ...]:
        runtime = self._runtime
        if runtime is None:
            return ()
        if runtime.ark.flow_service is None:
            raise RepoActivityRecoveryRequiredError(
                "Repository activity recovery is required because the persisted Flow service is unavailable."
            )
        try:
            flows = runtime.list_flows(flow_type="native_repo_coordinator")
        except Exception as exc:  # noqa: BLE001 - convert runtime read failures to a stable recovery gate.
            raise RepoActivityRecoveryRequiredError(
                "Repository activity recovery is required because the persisted Coordinator frontier cannot be read."
            ) from exc
        reservations: list[ContentBatchReservation] = []
        for flow in flows:
            if self._enum_value(getattr(flow, "status", None)) in {"completed", "failed"}:
                continue
            input_model = getattr(flow, "input", None)
            flow_root = getattr(input_model, "repo_root", None)
            if not flow_root or self.canonical_root(Path(flow_root)) != repo_root:
                continue
            state = getattr(flow, "state", None)
            phase = getattr(getattr(state, "position", None), "phase", None)
            if (
                getattr(state, "pending_dispatch_kind", None) != "content_tasks"
                or phase not in self._PERSISTED_BATCH_PHASES
            ):
                continue
            node_paths = tuple(
                sorted(
                    dict.fromkeys(
                        path.strip()
                        for path in getattr(state, "pending_content_node_paths", ())
                        if path and path.strip()
                    )
                )
            )
            source_step_id = getattr(state, "pending_dispatch_source_step_id", None)
            source_submission_id = getattr(
                state,
                "pending_dispatch_source_submission_id",
                None,
            )
            if not node_paths or not source_step_id or not source_submission_id:
                raise RepoActivityRecoveryRequiredError(
                    "Persisted Content batch identity is incomplete; repository activity recovery is required."
                )
            if phase == "coordinator_callback" and self._has_created_callback_step(flow):
                continue
            payload = {
                "coordinator_flow_id": flow.flow_id,
                "source_step_id": source_step_id,
                "source_submission_id": source_submission_id,
                "node_paths": list(node_paths),
            }
            batch_id = "content_batch_" + hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:20]
            reservations.append(
                ContentBatchReservation(
                    batch_id=batch_id,
                    repo_root=repo_root,
                    node_paths=node_paths,
                )
            )
        return tuple(reservations)

    def _has_created_callback_step(self, flow: object) -> bool:
        step_id = getattr(flow, "current_step_id", None)
        if not step_id or self._runtime is None:
            return False
        try:
            step = self._runtime.get_step(step_id)
        except Exception as exc:  # noqa: BLE001 - unknown callback ownership must fail closed.
            raise RepoActivityRecoveryRequiredError(
                "Repository activity recovery is required because the current Coordinator callback Step cannot be read."
            ) from exc
        return (
            getattr(step, "step_type", None) == "coordinator_agent_step"
            and getattr(getattr(step, "state", None), "prompt_mode", None) == "callback"
        )

    @staticmethod
    def _enum_value(value: object) -> str:
        return str(getattr(value, "value", value))
