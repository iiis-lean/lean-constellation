from lean_constellation.flows.common.checkpoint_policy import REPO_FLOW_BOUNDARY_CHECKPOINT_KINDS
from lean_constellation.services.validation_snapshot import RepoCheckpointKind, SnapshotRestoreComponent


def test_repo_flow_boundary_checkpoint_membership_is_exact() -> None:
    assert REPO_FLOW_BOUNDARY_CHECKPOINT_KINDS == {
        "before_native_coordinator_dispatch",
        "coordinator_requirement_waiting",
        "before_content_task_dispatch",
        "after_content_task_batch_terminal",
        "before_resource_request_dispatch",
        "after_resource_request_terminal",
        "before_repo_exploration_dispatch",
        "after_repo_exploration_terminal",
        "after_initial_repo_exploration_callback",
    }


def test_checkpoint_registry_includes_new_content_progress_and_keeps_mandatory_kinds_outside_optional_group() -> None:
    policies = SnapshotRestoreComponent.checkpoint_policies()

    assert set(policies) == set(RepoCheckpointKind)
    assert RepoCheckpointKind.AFTER_CONTENT_PREPARATION_TERMINAL in policies
    assert RepoCheckpointKind.AFTER_CONTENT_DECL_ROUND_TERMINAL in policies
    assert RepoCheckpointKind.AFTER_INITIAL_REPO_EXPLORATION_CALLBACK in policies
    assert RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL.value not in REPO_FLOW_BOUNDARY_CHECKPOINT_KINDS
    assert RepoCheckpointKind.BEFORE_NATIVE_RUN_MUTATION.value not in REPO_FLOW_BOUNDARY_CHECKPOINT_KINDS
    assert RepoCheckpointKind.REPO_RELEASE.value not in REPO_FLOW_BOUNDARY_CHECKPOINT_KINDS
