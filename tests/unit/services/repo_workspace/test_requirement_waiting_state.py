from __future__ import annotations

from pathlib import Path

from lean_constellation.domain.interface import DeclKind
from lean_constellation.domain.repo import ProofAvailability, RepoCompletionMode, RepoFormat
from tests.unit_services_helpers import make_runtime, publish_native_provider_release


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
    assert loaded.value.requirement.provider_repo == "provider"
    assert loaded.value.requirement.provider_request_submitted_at is not None
    assert loaded.value.requirement.provider_result_observed_at is None
    assert loaded.value.requirement.note == "Submitted by Coordinator."


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
    ready = publish_native_provider_release(
        runtime,
        provider,
        summary="Provider exposes the requested repo interface.",
    )
    candidates = runtime.repo_workspace.list_resume_candidates_for_requirement(
        workspace,
        provider_repo="provider",
    )

    assert not not_ready.ok
    assert not_ready.issues[0].kind == "provider_repo_not_ready"
    assert ready.release_id
    assert candidates.ok
    assert candidates.value is not None
    assert len(candidates.value) == 1
    assert candidates.value[0].consumer_repo == "consumer"
    assert candidates.value[0].requirement_name == "need_provider"
    assert candidates.value[0].provider_repo == "provider"


def test_requirement_resume_candidates_require_sufficient_provider_proof_availability(tmp_path: Path) -> None:
    runtime, workspace, consumer, provider = _setup_consumer_provider(tmp_path)
    configured_requirement = runtime.repo_workspace.requirement.create_requirement(
        consumer,
        name="need_proved_provider",
        target_repo="provider",
        required_proof_availability=ProofAvailability.PROVED,
        reason="Need proved provider.",
    )
    assert configured_requirement.ok
    runtime.repo_workspace.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_proved_provider",
    )
    runtime.repo_workspace.requirement.mark_requirement_satisfied(
        consumer,
        requirement_name="need_proved_provider",
        provider_repo="provider",
    )
    configured_provider = runtime.repo_workspace.metadata.update_repo_config(
        provider,
        completion_mode=RepoCompletionMode.INTERFACE_DECLARED,
    )
    assert configured_provider.ok
    publish_native_provider_release(runtime, provider, summary="Provider exposes declared interfaces.")

    candidates = runtime.repo_workspace.list_resume_candidates_for_requirement(
        workspace,
        provider_repo="provider",
    )

    assert candidates.ok and candidates.value is not None
    assert [candidate.requirement_name for candidate in candidates.value] == []


def test_stable_requirement_truth_uses_semantic_provider_public_boundary(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    from tests.unit.services.node.test_public_decl_access import _create_provider_repo

    consumer = tmp_path / "consumer"
    provider = tmp_path / "provider"
    runtime = make_runtime()
    assert runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    _create_provider_repo(
        provider,
        provider_name="provider",
        interface_name="provider_result",
    )
    assert runtime.repo_workspace.requirement.create_requirement(
        consumer,
        name="need_provider_result",
        target_repo="provider",
        reason="Use the released provider theorem.",
    ).ok
    assert runtime.repo_workspace.requirement.add_requirement_interface(
        consumer,
        requirement_name="need_provider_result",
        interface_name="provider_result",
        kind=DeclKind.THEOREM,
        summary="Released provider theorem.",
    ).ok
    monkeypatch.setattr(
        runtime.node.export,
        "list_scope_exports",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("working exports must not be read")),
    )

    valid = runtime.repo_workspace.requirement.validate_requirement_provider_truth(
        consumer,
        requirement_name="need_provider_result",
        provider_repo="provider",
        require_stable=True,
    )

    assert valid.ok


def test_stable_requirement_truth_accepts_theorem_requirement_bound_to_lemma(
    tmp_path: Path,
) -> None:
    from tests.unit.services.node.test_public_decl_access import _create_provider_repo

    consumer = tmp_path / "consumer"
    provider = tmp_path / "provider"
    runtime = make_runtime()
    assert runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    _create_provider_repo(
        provider,
        provider_name="provider",
        interface_name="provider_result",
        decl_kind="lemma",
    )
    assert runtime.repo_workspace.requirement.create_requirement(
        consumer,
        name="need_provider_result",
        target_repo="provider",
        reason="Use the released provider theorem-like declaration.",
    ).ok
    assert runtime.repo_workspace.requirement.add_requirement_interface(
        consumer,
        requirement_name="need_provider_result",
        interface_name="provider_result",
        kind=DeclKind.THEOREM,
        summary="Released provider theorem-like declaration.",
    ).ok

    valid = runtime.repo_workspace.requirement.validate_requirement_provider_truth(
        consumer,
        requirement_name="need_provider_result",
        provider_repo="provider",
        require_stable=True,
    )

    assert valid.ok, valid.issues


def test_direct_provider_ready_mark_is_rejected_for_release_managed_formats(tmp_path: Path) -> None:
    runtime = make_runtime()
    for repo_format, issue_kind in (
        (RepoFormat.NATIVE, "native_release_finalizer_required"),
        (RepoFormat.ADAPTER, "adapter_release_finalizer_required"),
    ):
        provider = tmp_path / repo_format.value
        assert runtime.repo_workspace.metadata.ensure_repo_model(provider).ok
        assert runtime.repo_workspace.metadata.set_repo_format(
            provider,
            repo_format=repo_format,
            reason="Release-managed provider fixture.",
        ).ok

        result = runtime.repo_workspace.mark_provider_repo_ready(
            provider,
            summary="Bypass the Release finalizer.",
        )
        publication = runtime.repo_workspace.metadata.get_repo_publication(provider)

        assert not result.ok
        assert result.issues[0].kind == issue_kind
        assert publication.ok and publication.value is not None
        assert publication.value.publication.status.value == "developing"
        assert publication.value.publication.latest_release_id is None


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
    publish_native_provider_release(runtime, provider, summary="Provider ready.")

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
    runtime.repo_workspace.metadata.mark_repo_developing(provider)
    repeated_after_provider_degraded = runtime.repo_workspace.mark_requirement_result_observed(
        consumer,
        requirement_name="need_provider",
    )

    assert not too_early.ok
    assert too_early.issues[0].kind == "requirement_not_resumable"
    assert before.ok and before.value is not None and len(before.value) == 1
    assert observed.ok
    assert observed.value is not None
    assert observed.value.result_observed is True
    loaded = runtime.repo_workspace.requirement.get_requirement(consumer, name="need_provider")
    assert loaded.ok and loaded.value is not None
    assert loaded.value.requirement.provider_result_observed_at is not None
    assert loaded.value.requirement.note == "Coordinator resume gate consumed the provider result."
    assert after.ok and after.value == []
    assert repeated.ok
    assert repeated.value is not None
    assert "already observed" in repeated.value.summary
    assert not repeated_after_provider_degraded.ok
    assert repeated_after_provider_degraded.issues[0].kind == "provider_repo_not_ready"


def test_obsolete_requirement_is_not_resume_candidate(tmp_path: Path) -> None:
    runtime, workspace, consumer, provider = _setup_consumer_provider(tmp_path)
    runtime.repo_workspace.requirement.mark_requirement_obsolete(
        consumer,
        requirement_name="need_provider",
        note="No longer needed.",
    )
    publish_native_provider_release(runtime, provider, summary="Provider ready.")

    candidates = runtime.repo_workspace.list_resume_candidates_for_requirement(
        workspace,
        provider_repo="provider",
    )

    assert candidates.ok
    assert candidates.value == []
