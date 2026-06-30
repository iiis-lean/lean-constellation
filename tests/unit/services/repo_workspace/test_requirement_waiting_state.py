from __future__ import annotations

from pathlib import Path

from tests.unit_services_helpers import make_runtime


def _setup_consumer_provider(tmp_path: Path):
    runtime = make_runtime()
    workspace = tmp_path
    consumer = workspace / "consumer"
    provider = workspace / "provider"
    runtime.repo_workspace.metadata.ensure_repo_model(consumer)
    runtime.repo_workspace.metadata.ensure_repo_model(provider)
    created = runtime.repo_workspace.requirement.create_requirement(
        consumer,
        name="need_provider",
        target_repo="provider",
        reason="The current repo needs provider lemmas.",
    )
    assert created.ok
    return runtime, workspace, consumer, provider


def test_requirement_waiting_state_write_read_and_duplicate_idempotent(tmp_path: Path) -> None:
    runtime, _, consumer, _ = _setup_consumer_provider(tmp_path)

    waiting = runtime.repo_workspace.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider",
        reason="Submitted by Coordinator.",
    )
    repeated = runtime.repo_workspace.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider",
        reason="Submitted again.",
    )
    loaded = runtime.repo_workspace.requirement.get_requirement(consumer, name="need_provider")

    assert waiting.ok
    assert waiting.value is not None
    assert waiting.value.provider_repo == "provider"
    assert waiting.value.waiting is True
    assert waiting.value.result_observed is False
    assert repeated.ok
    assert repeated.value is not None
    assert "already waiting" in repeated.value.summary
    assert loaded.ok
    assert loaded.value is not None
    assert loaded.value.requirement.waiting_state.waiting is True
    assert loaded.value.requirement.waiting_state.provider_repo == "provider"
    assert loaded.value.requirement.waiting_state.reason == "Submitted by Coordinator."
    assert loaded.value.requirement.waiting_state.submitted_at is not None


def test_requirement_waiting_conflict_and_obsolete_failure(tmp_path: Path) -> None:
    runtime, _, consumer, _ = _setup_consumer_provider(tmp_path)

    first = runtime.repo_workspace.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider",
        provider_repo="provider",
    )
    conflict = runtime.repo_workspace.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider",
        provider_repo="other_provider",
    )
    runtime.repo_workspace.requirement.create_requirement(
        consumer,
        name="obsolete_need",
        target_repo="provider",
        reason="No longer needed.",
    )
    runtime.repo_workspace.requirement.mark_requirement_obsolete(
        consumer,
        requirement_name="obsolete_need",
        note="Superseded.",
    )
    obsolete = runtime.repo_workspace.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="obsolete_need",
    )

    assert first.ok
    assert not conflict.ok
    assert conflict.issues[0].kind == "requirement_waiting_conflict"
    assert not obsolete.ok
    assert obsolete.issues[0].kind == "requirement_obsolete_cannot_resume"


def test_requirement_resume_candidates_require_ready_provider(tmp_path: Path) -> None:
    runtime, workspace, consumer, provider = _setup_consumer_provider(tmp_path)
    runtime.repo_workspace.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider",
        reason="Submitted by Coordinator.",
    )
    runtime.repo_workspace.requirement.mark_requirement_satisfied(
        consumer,
        requirement_name="need_provider",
        provider_repo="provider",
        note="Provider completed.",
    )

    not_ready = runtime.repo_workspace.list_resume_candidates_for_requirement(
        workspace,
        provider_repo="provider",
    )
    ready = runtime.repo_workspace.metadata.set_provider_ready(
        provider,
        summary="Provider exposes the requested repo interface.",
    )
    candidates = runtime.repo_workspace.list_resume_candidates_for_requirement(
        workspace,
        provider_repo="provider",
    )

    assert not not_ready.ok
    assert not_ready.issues[0].kind == "provider_repo_not_ready"
    assert ready.ok
    assert candidates.ok
    assert candidates.value is not None
    assert len(candidates.value) == 1
    assert candidates.value[0].consumer_repo == "consumer"
    assert candidates.value[0].requirement_name == "need_provider"
    assert candidates.value[0].provider_repo == "provider"


def test_requirement_result_observed_removes_resume_candidate(tmp_path: Path) -> None:
    runtime, workspace, consumer, provider = _setup_consumer_provider(tmp_path)
    runtime.repo_workspace.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider",
    )
    too_early = runtime.repo_workspace.mark_requirement_result_observed(
        consumer,
        requirement_name="need_provider",
    )
    runtime.repo_workspace.requirement.mark_requirement_satisfied(
        consumer,
        requirement_name="need_provider",
        provider_repo="provider",
    )
    runtime.repo_workspace.metadata.set_provider_ready(provider, summary="Provider ready.")

    before = runtime.repo_workspace.list_resume_candidates_for_requirement(
        workspace,
        provider_repo="provider",
    )
    observed = runtime.repo_workspace.mark_requirement_result_observed(
        consumer,
        requirement_name="need_provider",
        note="Coordinator resume gate consumed the provider result.",
    )
    after = runtime.repo_workspace.list_resume_candidates_for_requirement(
        workspace,
        provider_repo="provider",
    )
    repeated = runtime.repo_workspace.mark_requirement_result_observed(
        consumer,
        requirement_name="need_provider",
    )

    assert not too_early.ok
    assert too_early.issues[0].kind == "requirement_not_resumable"
    assert before.ok and before.value is not None and len(before.value) == 1
    assert observed.ok
    assert observed.value is not None
    assert observed.value.result_observed is True
    assert after.ok and after.value == []
    assert repeated.ok
    assert repeated.value is not None
    assert "already observed" in repeated.value.summary


def test_obsolete_requirement_is_not_resume_candidate(tmp_path: Path) -> None:
    runtime, workspace, consumer, provider = _setup_consumer_provider(tmp_path)
    runtime.repo_workspace.requirement.mark_requirement_obsolete(
        consumer,
        requirement_name="need_provider",
        note="No longer needed.",
    )
    runtime.repo_workspace.metadata.set_provider_ready(provider, summary="Provider ready.")

    candidates = runtime.repo_workspace.list_resume_candidates_for_requirement(
        workspace,
        provider_repo="provider",
    )

    assert candidates.ok
    assert candidates.value == []
