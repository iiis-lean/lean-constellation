"""Composition root for the registry-bound Operator Data API."""

from __future__ import annotations

from lean_constellation.app.operator_data.decl_projection import DeclProjectionOperator
from lean_constellation.app.operator_data.common import OperatorAdmissionView, project_operator_result
from lean_constellation.app.operator_data.execution import OperatorExecutionService
from lean_constellation.app.operator_data.node import NodeOperatorApi
from lean_constellation.app.operator_data.repo_material import (
    NativeRepoCreateInput,
    OperatorNativeRepoCreationView,
    RepoMaterialOperatorApi,
)
from lean_constellation.app.operator_data.release import ReleaseCheckpointOperatorApi
from lean_constellation.app.repo_runtime_registry import RepoRuntimeRegistry
from lean_constellation.services.foundation import ServiceResult


class OperatorDataApi:
    """Single application entry point for typed operator business operations."""

    def __init__(self, registry: RepoRuntimeRegistry) -> None:
        self._registry = registry
        self._execution = OperatorExecutionService(registry)
        self.repo_material = RepoMaterialOperatorApi(
            self._execution,
            workspace_root=registry.workspace_root,
            workspace_runtime=registry.workspace_runtime(),
        )
        self.node = NodeOperatorApi(registry)
        self.decl_projection = DeclProjectionOperator(self._execution)
        self.release_checkpoint = ReleaseCheckpointOperatorApi(self._execution)

    def prepare_repo_management(self, repo_key: str) -> ServiceResult[OperatorAdmissionView]:
        """Atomically enter paused management mode for a repo with ARK history."""

        return project_operator_result(self._execution.prepare_repo_management(repo_key))

    def create_native_repo(
        self,
        repo_key: str,
        request: NativeRepoCreateInput,
    ) -> ServiceResult[OperatorNativeRepoCreationView]:
        """Create a repo before a repo-local runtime record can exist."""

        normalized = self._registry.normalize_repo_key(repo_key)
        if not normalized.ok or normalized.value is None:
            message = normalized.issues[0].message if normalized.issues else "Repo key is invalid."
            return project_operator_result(self._registry.result.fail(
                self._registry.result.issue(
                    "operator_repo_key_invalid",
                    message,
                    field="repo_key",
                    current=str(repo_key),
                )
            ))
        return self.repo_material.create_native_repo(normalized.value, request)


__all__ = ["OperatorDataApi"]
