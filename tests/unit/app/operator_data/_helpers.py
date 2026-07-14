from __future__ import annotations

from pathlib import Path

from lean_constellation.app.config import LeanAppConfig
from lean_constellation.app.operator_data.common import (
    OperatorAccess,
    OperatorLockPolicy,
    OperatorOperationSpec,
)
from lean_constellation.app.repo_runtime_registry import RepoRuntimeRegistry


READ_OPERATION = OperatorOperationSpec(
    name="test.read",
    access=OperatorAccess.READ,
    lock_policy=OperatorLockPolicy.NONE,
)
MUTATION_OPERATION = OperatorOperationSpec(
    name="test.mutation",
    access=OperatorAccess.MUTATION,
    lock_policy=OperatorLockPolicy.OPERATOR,
    requires_stable_runtime=True,
)
SELF_MANAGED_OPERATION = OperatorOperationSpec(
    name="test.self_managed",
    access=OperatorAccess.MUTATION,
    lock_policy=OperatorLockPolicy.SELF_MANAGED,
    requires_stable_runtime=True,
)


def make_repo(workspace: Path, name: str = "MainRepo") -> Path:
    repo_root = workspace / name
    (repo_root / ".lean_constellation").mkdir(parents=True)
    return repo_root


def make_registry(
    workspace: Path,
    *,
    server_start_paused: bool = False,
    external_overrides: dict[str, object] | None = None,
) -> RepoRuntimeRegistry:
    return RepoRuntimeRegistry(
        LeanAppConfig(
            workspace_root=workspace,
            materialize_agent_homes=False,
            server_start_paused=server_start_paused,
        ),
        external_overrides=external_overrides,
    )
