"""Registry-bound admission and locking for Operator Data API operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from lean_constellation.app.operator_data.common import (
    OperatorAccess,
    OperatorAdmissionView,
    OperatorLockPolicy,
    OperatorOperationSpec,
)
from lean_constellation.app.repo_runtime_registry import RepoRuntimeRecord, RepoRuntimeRegistry
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.repo_workspace.repo_lifecycle_lock import RepoLifecycleLockBusyError
from lean_constellation.services.runtime import LeanRuntimeServices


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class OperatorExecutionContext:
    """Internal context resolved from a repo key after admission."""

    repo_key: str
    repo_root: Path
    runtime: LeanRuntimeServices
    admission: OperatorAdmissionView


OperatorAction = Callable[[OperatorExecutionContext], ServiceResult[T]]


class OperatorExecutionService(Generic[T]):
    """Execute typed operations under immutable application policy."""

    def __init__(self, registry: RepoRuntimeRegistry) -> None:
        self.registry = registry
        self.result = registry.result

    def prepare_repo_management(self, repo_key: str) -> ServiceResult[OperatorAdmissionView]:
        """Atomically load a repo paused, then prove that it is quiescent."""

        discovered = self._discover(repo_key)
        if not discovered.ok or discovered.value is None:
            return self.result.fail(discovered.issues)
        record = discovered.value
        with record.lock:
            loaded = self.registry.get_or_load_paused(record.repo_key, refresh_homes=False)
            if not loaded.ok or loaded.value is None:
                return self.result.fail(loaded.issues)
            stable = self.registry.check_operator_runtime_stable(record)
            if not stable.ok:
                return self.result.fail(stable.issues)
            return self.result.ok(self._paused_admission(record))

    def execute(
        self,
        repo_key: str,
        operation: OperatorOperationSpec,
        action: OperatorAction[T],
    ) -> ServiceResult[T]:
        """Resolve a repo key and execute one statically selected operation."""

        discovered = self._discover(repo_key)
        if not discovered.ok or discovered.value is None:
            return self.result.fail(discovered.issues)
        record = discovered.value
        with record.lock:
            if operation.access is not OperatorAccess.MUTATION:
                context = OperatorExecutionContext(
                    repo_key=record.repo_key,
                    repo_root=record.repo_root,
                    runtime=self.registry.workspace_runtime(),
                    admission=self._data_only_admission(record),
                )
                return self._call(operation, context, action)

            history = self.registry.runtime_history_exists(record)
            if record.runtime is None:
                if history:
                    return self.result.fail(
                        self.result.issue(
                            "operator_repo_runtime_history_unloaded",
                            "Repo runtime history exists but is not loaded for operator management.",
                            object_ref=record.repo_key,
                            suggested_action="Call prepare_repo_management before mutating this repo.",
                        )
                    )
                context = OperatorExecutionContext(
                    repo_key=record.repo_key,
                    repo_root=record.repo_root,
                    runtime=self.registry.workspace_runtime(),
                    admission=self._data_only_admission(record),
                )
            else:
                stable = self.registry.check_operator_runtime_stable(record)
                if not stable.ok:
                    return self.result.fail(stable.issues)
                context = OperatorExecutionContext(
                    repo_key=record.repo_key,
                    repo_root=record.repo_root,
                    runtime=record.runtime,
                    admission=self._paused_admission(record),
                )
            return self._call(operation, context, action)

    def _call(
        self,
        operation: OperatorOperationSpec,
        context: OperatorExecutionContext,
        action: OperatorAction[T],
    ) -> ServiceResult[T]:
        if operation.lock_policy is OperatorLockPolicy.SELF_MANAGED:
            return action(context)
        if operation.lock_policy is OperatorLockPolicy.NONE:
            return action(context)
        try:
            with context.runtime.repo_workspace.lifecycle_lock.locked(context.repo_root):
                return action(context)
        except RepoLifecycleLockBusyError as exc:
            return self.result.fail(
                self.result.issue(
                    "operator_lifecycle_lock_failed",
                    str(exc),
                    object_ref=context.repo_key,
                )
            )

    def _discover(self, repo_key: str) -> ServiceResult[RepoRuntimeRecord]:
        normalized = self.registry.normalize_repo_key(repo_key)
        if not normalized.ok or normalized.value is None:
            message = normalized.issues[0].message if normalized.issues else "Repo key is invalid."
            return self.result.fail(
                self.result.issue(
                    "operator_repo_key_invalid",
                    message,
                    field="repo_key",
                    current=str(repo_key),
                )
            )
        return self.registry.discover_repo(normalized.value)

    def _data_only_admission(self, record: RepoRuntimeRecord) -> OperatorAdmissionView:
        history = self.registry.runtime_history_exists(record)
        return OperatorAdmissionView(
            repo_key=record.repo_key,
            management_state="data_only",
            runtime_loaded=False,
            runtime_history=history,
            paused=False,
            stable=not history,
            summary="Repo is using the workspace data-service runtime without ARK history.",
        )

    def _paused_admission(self, record: RepoRuntimeRecord) -> OperatorAdmissionView:
        return OperatorAdmissionView(
            repo_key=record.repo_key,
            management_state="paused_runtime",
            runtime_loaded=True,
            runtime_history=True,
            paused=True,
            stable=True,
            summary="Repo runtime is paused and has no active ARK work.",
        )


__all__ = [
    "OperatorAction",
    "OperatorExecutionContext",
    "OperatorExecutionService",
]
