from tests.unit_services_helpers import make_runtime, publish_native_provider_release

from pathlib import Path

from lean_constellation.domain.preparation import RepoDependencyRequirement, RepoDependencyRequirementStatus
from lean_constellation.domain.repo import (
    ProofAvailability,
    RepoFormat,
    RepoPublicationState,
    RepoPublicationStatus,
    RepoWorkMode,
)


def test_ensure_repo_model_is_idempotent_and_rejects_conflict(tmp_path: Path) -> None:
    component = make_runtime().repo_workspace.metadata

    created = component.ensure_repo_model(tmp_path)
    assert created.ok
    assert created.value is not None
    assert created.value.created is True
    assert created.value.main_node == "Main"

    repeated = component.ensure_repo_model(tmp_path)
    assert repeated.ok
    assert repeated.value is not None
    assert repeated.value.created is False

    conflict = component.ensure_repo_model(tmp_path, main_node="Other")
    assert not conflict.ok
    assert conflict.issues[0].kind == "repo_model_conflict"


def test_repo_format_policy_and_state_view(tmp_path: Path) -> None:
    component = make_runtime().repo_workspace.metadata
    component.ensure_repo_model(tmp_path)

    unknown = component.get_repo_format(tmp_path)
    assert unknown.ok
    assert unknown.value is not None
    assert unknown.value.repo_format == RepoFormat.UNKNOWN

    set_format = component.set_repo_format(tmp_path, repo_format=RepoFormat.NATIVE, reason="test")
    assert set_format.ok
    assert set_format.value is not None
    assert set_format.value.changed is True

    repeated = component.set_repo_format(tmp_path, repo_format=RepoFormat.NATIVE, reason="same")
    assert repeated.ok
    assert repeated.value is not None
    assert repeated.value.changed is False

    conflict = component.set_repo_format(tmp_path, repo_format=RepoFormat.ADAPTER, reason="bad")
    assert not conflict.ok
    assert conflict.issues[0].kind == "repo_format_conflict"

    policy = component.get_repo_policy(tmp_path)
    assert policy.ok
    assert policy.value is not None
    assert policy.value.policy.readiness_policy == "proved_closure"
    assert policy.value.policy.max_parallel_content_node_tasks == 1

    updated = component.update_repo_policy(
        tmp_path,
        max_parallel_content_node_tasks=3,
        readiness_policy="declared_closure",
    )
    assert updated.ok
    assert updated.value is not None
    assert updated.value.policy.max_parallel_content_node_tasks == 3
    assert updated.value.policy.readiness_policy == "declared_closure"
    config = component.get_repo_config(tmp_path)
    assert config.ok and config.value is not None
    assert config.value.config.max_parallel_content_node_tasks == 3

    ready = component.set_provider_ready(tmp_path, summary="Ready provider summary.")
    assert not ready.ok
    assert ready.issues[0].kind == "native_release_finalizer_required"
    publish_native_provider_release(component.runtime, tmp_path, summary="Ready provider summary.")

    model = component.get_repo_model(tmp_path)
    assert model.ok
    assert model.value is not None
    assert model.value.summary == "Ready provider summary."

    state = component.get_repo_state_view(tmp_path)
    assert state.ok
    assert state.value is not None
    assert state.value.main_node == "Main"
    assert state.value.repo_summary == "Ready provider summary."
    assert state.value.repo_format == RepoFormat.NATIVE
    assert state.value.provider_ready is True
    assert state.value.publication_status == RepoPublicationStatus.STABLE
    assert state.value.target_proof_availability == ProofAvailability.PROVED
    assert state.value.work_mode == RepoWorkMode.PROVED_FULL_GRAPH
    assert state.value.max_parallel_content_node_tasks == 3


def test_repo_config_and_publication_are_repo_local_truth(tmp_path: Path) -> None:
    component = make_runtime().repo_workspace.metadata
    assert component.ensure_repo_model(tmp_path).ok

    default_config = component.get_repo_config(tmp_path)
    assert default_config.ok and default_config.value is not None
    assert default_config.value.config.target_proof_availability == ProofAvailability.PROVED
    assert default_config.value.config.work_mode == RepoWorkMode.PROVED_FULL_GRAPH
    assert default_config.value.config.default_requirement_proof_availability == ProofAvailability.DECLARED

    declared = component.update_repo_config(
        tmp_path,
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
        default_requirement_proof_availability=ProofAvailability.PROVED,
        max_parallel_content_node_tasks=2,
    )
    assert declared.ok and declared.value is not None
    assert declared.value.config.target_proof_availability == ProofAvailability.DECLARED
    assert declared.value.config.work_mode == RepoWorkMode.DECLARED_INTERFACE
    assert declared.value.config.default_requirement_proof_availability == ProofAvailability.PROVED
    assert declared.value.config.max_parallel_content_node_tasks == 2

    work = component.get_repo_work_config(tmp_path)
    assert work.ok and work.value is not None
    assert work.value.repo_key == tmp_path.name
    assert work.value.target_proof_availability == ProofAvailability.DECLARED
    assert work.value.work_mode == RepoWorkMode.DECLARED_INTERFACE

    invalid_combo = component.update_repo_config(
        tmp_path,
        target_proof_availability=ProofAvailability.PROVED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
    )
    assert not invalid_combo.ok
    assert invalid_combo.issues[0].kind == "repo_config_invalid"

    developing = component.get_repo_publication(tmp_path)
    assert developing.ok and developing.value is not None
    assert developing.value.publication.status == RepoPublicationStatus.DEVELOPING
    assert developing.value.publication.stable_at is None

    stable = component.mark_repo_stable(tmp_path, summary="Stable declared provider.")
    assert stable.ok and stable.value is not None
    assert stable.value.publication.status == RepoPublicationStatus.STABLE
    assert stable.value.publication.stable_at is not None

    locked = component.update_repo_config(
        tmp_path,
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_FULL_GRAPH,
    )
    assert not locked.ok
    assert locked.issues[0].kind == "repo_config_locked"

    reopened = component.mark_repo_developing(tmp_path)
    assert reopened.ok and reopened.value is not None
    assert reopened.value.publication.status == RepoPublicationStatus.DEVELOPING
    assert reopened.value.publication.stable_at is None


def test_old_publication_json_loads_without_read_time_write_and_latest_is_visible(tmp_path: Path) -> None:
    component = make_runtime().repo_workspace.metadata
    publication_path = component._repo_publication_path(tmp_path)
    publication_path.parent.mkdir(parents=True, exist_ok=True)
    old_payload = '{\n  "status": "stable",\n  "stable_at": "2026-01-01T00:00:00Z"\n}\n'
    publication_path.write_text(old_payload, encoding="utf-8")

    old = component.get_repo_publication(tmp_path)

    assert old.ok and old.value is not None
    assert old.value.publication.latest_release_id is None
    assert publication_path.read_text(encoding="utf-8") == old_payload

    written = component.runtime.foundation.store.write_json_atomic(
        publication_path,
        RepoPublicationState(
            status=RepoPublicationStatus.STABLE,
            stable_at="2026-01-01T00:00:00Z",
            latest_release_id="release_2",
        ),
    )
    assert written.ok
    state = component.get_repo_state_view(tmp_path)
    assert state.ok and state.value is not None
    assert state.value.latest_release_id == "release_2"


def test_get_repo_model_missing_returns_structured_failure(tmp_path: Path) -> None:
    component = make_runtime().repo_workspace.metadata

    missing = component.get_repo_model(tmp_path)

    assert not missing.ok
    assert missing.issues[0].kind == "repo_model_missing"
    assert missing.issues[0].suggested_action == "Call ensure_repo_model before using this repo."


def test_get_repo_format_reads_adapter_state(tmp_path: Path) -> None:
    component = make_runtime().repo_workspace.metadata

    set_format = component.set_repo_format(tmp_path, repo_format=RepoFormat.ADAPTER, reason="upstream Lean repo")
    assert set_format.ok

    loaded = component.get_repo_format(tmp_path)
    assert loaded.ok
    assert loaded.value is not None
    assert loaded.value.repo_format == RepoFormat.ADAPTER
    assert loaded.value.reason == "upstream Lean repo"


def test_set_repo_format_rejects_missing_reason_unknown_and_invalid_values(tmp_path: Path) -> None:
    component = make_runtime().repo_workspace.metadata

    missing_reason = component.set_repo_format(tmp_path, repo_format=RepoFormat.NATIVE, reason=" ")
    assert not missing_reason.ok
    assert missing_reason.issues[0].kind == "missing_reason"

    unknown = component.set_repo_format(tmp_path, repo_format=RepoFormat.UNKNOWN, reason="cannot use unknown")
    assert not unknown.ok
    assert unknown.issues[0].kind == "invalid_repo_format"

    invalid = component.set_repo_format(tmp_path, repo_format="other", reason="cannot use other")
    assert not invalid.ok
    assert invalid.issues[0].kind == "invalid_repo_format"


def test_set_repo_format_allows_admin_overwrite_before_ready_but_locks_after_ready(tmp_path: Path) -> None:
    component = make_runtime().repo_workspace.metadata
    component.ensure_repo_model(tmp_path)
    component.set_repo_format(tmp_path, repo_format=RepoFormat.NATIVE, reason="first")

    overwritten = component.set_repo_format(
        tmp_path,
        repo_format=RepoFormat.ADAPTER,
        reason="admin correction",
        allow_overwrite=True,
    )
    assert overwritten.ok
    assert overwritten.value is not None
    assert overwritten.value.changed is True

    ready = component.set_provider_ready(tmp_path, summary="provider committed")
    assert ready.ok

    locked = component.set_repo_format(
        tmp_path,
        repo_format=RepoFormat.NATIVE,
        reason="too late",
        allow_overwrite=True,
    )
    assert not locked.ok
    assert locked.issues[0].kind == "repo_format_locked"


def test_update_repo_policy_rejects_invalid_parallelism(tmp_path: Path) -> None:
    component = make_runtime().repo_workspace.metadata

    result = component.update_repo_policy(tmp_path, max_parallel_content_node_tasks=0)
    assert not result.ok
    assert result.issues[0].kind == "policy_invalid"


def test_update_repo_policy_rejects_empty_readiness_policy(tmp_path: Path) -> None:
    component = make_runtime().repo_workspace.metadata

    result = component.update_repo_policy(tmp_path, readiness_policy="  ")

    assert not result.ok
    assert result.issues[0].kind == "policy_invalid"


def test_repo_state_view_counts_requirements_structurally_and_warns_on_missing_model(tmp_path: Path) -> None:
    component = make_runtime().repo_workspace.metadata
    foundation = component.runtime.foundation
    ctx = component._ctx(tmp_path)
    req_root = foundation.layout.requirements_root(ctx)
    foundation.store.ensure_dir(req_root)

    open_requirement = RepoDependencyRequirement(
        name="open_need",
        target_repo="provider",
        reason='contains text fragment "status": "handled"',
    )
    handled_requirement = RepoDependencyRequirement(
        name="handled_need",
        target_repo="provider",
        reason='contains text fragment "status": "open"',
        status=RepoDependencyRequirementStatus.HANDLED,
    )
    foundation.store.write_json_atomic(req_root / "open_need.json", open_requirement)
    foundation.store.write_json_atomic(req_root / "handled_need.json", handled_requirement)

    state = component.get_repo_state_view(tmp_path)
    assert state.ok
    assert state.value is not None
    assert state.value.main_node is None
    assert state.value.open_requirement_count == 1
    assert any(issue.kind == "repo_model_missing" for issue in state.issues)
