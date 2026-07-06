from __future__ import annotations

from lean_constellation.app import LeanAdminApi, RequirementResumeInput, create_app_runtime_services, initialize_repo_runtime


def test_admin_requirement_resume_marks_observed_and_starts_resume_flow(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    consumer = tmp_path / "Consumer"
    provider = tmp_path / "Provider"
    assert initialize_repo_runtime(runtime, consumer).ok
    assert initialize_repo_runtime(runtime, provider).ok
    assert runtime.repo_workspace.create_requirement_with_interfaces(
        consumer,
        name="need_provider",
        target_repo="Provider",
        reason="Need provider result.",
    ).ok
    assert runtime.repo_workspace.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider",
        provider_repo="Provider",
        reason="Waiting for provider.",
    ).ok
    assert runtime.repo_workspace.requirement.mark_requirement_satisfied(
        consumer,
        requirement_name="need_provider",
        provider_repo="Provider",
        note="Provider is ready.",
    ).ok
    assert runtime.repo_workspace.metadata.set_provider_ready(provider, summary="Provider ready.").ok

    result = LeanAdminApi(runtime).resume_requirement(
        RequirementResumeInput(
            consumer_repo_root=consumer,
            requirement_name="need_provider",
            provider_repo="Provider",
            admin_note="Resume after provider ready.",
        )
    )

    assert result.ok and result.value is not None
    assert result.value.observed is True
    flow = runtime.ark.flow_service.get_flow(result.value.resume_flow.flow_id)
    assert flow.flow_type == "native_repo_coordinator"
    assert flow.input.start_mode == "requirement_resume"
    waiting = runtime.repo_workspace.requirement.get_requirement(consumer, name="need_provider")
    assert waiting.value.requirement.provider_result_observed_at is not None
    assert waiting.value.requirement.note == "Resume after provider ready."


def test_admin_requirement_resume_rejects_provider_mismatch(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    consumer = tmp_path / "Consumer"
    assert initialize_repo_runtime(runtime, consumer).ok
    assert runtime.repo_workspace.create_requirement_with_interfaces(
        consumer,
        name="need_provider",
        target_repo="Provider",
        reason="Need provider result.",
    ).ok
    assert runtime.repo_workspace.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider",
        provider_repo="Provider",
        reason="Waiting for provider.",
    ).ok
    assert runtime.repo_workspace.requirement.mark_requirement_satisfied(
        consumer,
        requirement_name="need_provider",
        provider_repo="Provider",
        note="Provider is ready.",
    ).ok

    result = LeanAdminApi(runtime).resume_requirement(
        RequirementResumeInput(
            consumer_repo_root=consumer,
            requirement_name="need_provider",
            provider_repo="OtherProvider",
        )
    )

    assert not result.ok
    assert result.issues[0].kind == "requirement_provider_mismatch"
    waiting = runtime.repo_workspace.requirement.get_requirement(consumer, name="need_provider")
    assert waiting.value.requirement.provider_result_observed_at is None
    assert runtime.ark.flow_service.list_flows(flow_type="native_repo_coordinator") == []
