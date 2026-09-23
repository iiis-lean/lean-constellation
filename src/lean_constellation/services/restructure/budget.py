"""Workspace-wide reservations used by restructure Agent and build steps."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from lean_constellation.domain.restructure import Reservation, WorkspacePlan
from lean_constellation.services.restructure.store import RestructureStore


class RestructureBudget:
    def __init__(self, store: RestructureStore, plan: WorkspacePlan) -> None:
        self.store = store
        self.plan = plan

    def active(self, kind: str | None = None) -> list[Reservation]:
        reservations = [item for item in self.store.load_reservations() if item.released_at is None]
        return [item for item in reservations if kind is None or item.kind == kind]

    def acquire(self, *, run_id: str, kind: str, owner_ref: str) -> Reservation | None:
        with self.store._lock:  # shared metadata lock; ARK owns execution truth
            limit = self.plan.max_builds if kind == "build" else self.plan.max_agents
            current = self.active(kind)
            for item in current:
                if item.run_id == run_id and item.owner_ref == owner_ref:
                    return item
            if len(current) >= limit:
                return None
            reservation = Reservation(
                reservation_id=f"reservation_{uuid.uuid4().hex}",
                run_id=run_id,
                kind=kind,
                owner_ref=owner_ref,
            )
            self.store.save_reservations([*self.store.load_reservations(), reservation])
            return reservation

    def release(self, reservation_id: str) -> bool:
        with self.store._lock:
            reservations = self.store.load_reservations()
            changed = False
            updated: list[Reservation] = []
            for item in reservations:
                if item.reservation_id == reservation_id and item.released_at is None:
                    item = item.model_copy(update={"released_at": datetime.now(timezone.utc).isoformat()})
                    changed = True
                updated.append(item)
            if changed:
                self.store.save_reservations(updated)
            return changed

    def release_run(self, run_id: str) -> int:
        count = 0
        for item in list(self.active()):
            if item.run_id == run_id and self.release(item.reservation_id):
                count += 1
        return count


__all__ = ["RestructureBudget"]
