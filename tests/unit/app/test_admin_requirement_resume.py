from __future__ import annotations

from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from lean_constellation.app import LeanAdminApi, RequirementResumeInput, create_app_runtime_services, initialize_repo_runtime


def _prepare_satisfied_requirement(runtime, consumer, provider, *, name: str = "need_provider") -> None:
    assert initialize_repo_runtime(runtime, consumer).ok
    assert initialize_repo_runtime(runtime, provider).ok
    assert runtime.repo_workspace.create_requirement_with_interfaces(
        consumer,
        name=name,
        target_repo=provider.name,
        reason="Need provider result.",
    ).ok
    assert runtime.repo_workspace.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name=name,
        provider_repo=provider.name,
        reason="Waiting for provider.",
    ).ok
    assert runtime.repo_workspace.requirement.mark_requirement_satisfied(
        consumer,
        requirement_name=name,
        provider_repo=provider.name,
        note="Provider is ready.",
    ).ok
    assert runtime.repo_workspace.metadata.set_provider_ready(provider, summary="Provider ready.").ok


def _create_waiting_coordinator_flow(runtime, consumer, *, requirement_name: str = "need_provider", bind_agent: bool = True):
    scope_id = f"repo:{consumer.name}"
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id=scope_id,
            params={
                "repo_key": consumer.name,
                "repo_root": str(consumer),
                "start_mode": "admin_start",
                "start_reason": "unit",
            },
        ),
        enqueue=False,
    )
    agent = None
    if bind_agent:
        agent = runtime.ark.agent_service.store.create_agent_record(
            scope_id=scope_id,
            agent_type="CoordinatorAgent",
            cli_type="codex",
            home_id="CoordinatorAgent",
        )

    def patch(flow) -> None:
        flow.status = FlowStatus.WAITING
        flow.state.position.phase = "waiting_requirement"
        flow.state.waiting_requirement_name = requirement_name
        flow.state.waiting_reason = "Waiting for provider."
        if agent is not None:
            flow.agent_bindings.by_role["coordinator"] = agent.agent_id

    runtime.ark.flow_service.store.update_flow_record(flow_id, patch)
    return flow_id, agent


def test_admin_requirement_resume_marks_observed_and_enqueues_original_flow(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    consumer = tmp_path / "Consumer"
    provider = tmp_path / "Provider"
    _prepare_satisfied_requirement(runtime, consumer, provider)
    flow_id, agent = _create_waiting_coordinator_flow(runtime, consumer)
    flow_count = len(runtime.ark.flow_service.list_flows(flow_type="native_repo_coordinator"))

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
    assert result.value.resume_flow.flow_id == flow_id
    assert result.value.resume_flow.enqueued is True
    assert len(runtime.ark.flow_service.list_flows(flow_type="native_repo_coordinator")) == flow_count
    flow = runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.WAITING
    assert flow.state.position.phase == "waiting_requirement"
    assert flow.agent_bindings.get("coordinator") == agent.agent_id
    assert flow_id in runtime.ark.schedule_service.queued_flow_ids
    assert runtime.ark.flow_service.can_advance_flow(flow_id) is True
    waiting = runtime.repo_workspace.requirement.get_requirement(consumer, name="need_provider")
    assert waiting.value.requirement.provider_result_observed_at is not None
    assert waiting.value.requirement.note == "Resume after provider ready."


def test_admin_requirement_resume_enqueue_false_does_not_queue_original_flow(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    consumer = tmp_path / "Consumer"
    provider = tmp_path / "Provider"
    _prepare_satisfied_requirement(runtime, consumer, provider)
    flow_id, _ = _create_waiting_coordinator_flow(runtime, consumer)

    result = LeanAdminApi(runtime).resume_requirement(
        RequirementResumeInput(
            consumer_repo_root=consumer,
            requirement_name="need_provider",
            provider_repo="Provider",
            enqueue=False,
        )
    )

    assert result.ok and result.value is not None
    assert result.value.resume_flow.flow_id == flow_id
    assert result.value.resume_flow.enqueued is False
    assert flow_id not in runtime.ark.schedule_service.queued_flow_ids


def test_admin_requirement_resume_rejects_provider_mismatch_before_observed(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    consumer = tmp_path / "Consumer"
    provider = tmp_path / "Provider"
    _prepare_satisfied_requirement(runtime, consumer, provider)
    flow_id, _ = _create_waiting_coordinator_flow(runtime, consumer)

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
    assert [flow.flow_id for flow in runtime.ark.flow_service.list_flows(flow_type="native_repo_coordinator")] == [flow_id]


def test_admin_requirement_resume_requires_original_waiting_flow_before_observed(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    consumer = tmp_path / "Consumer"
    provider = tmp_path / "Provider"
    _prepare_satisfied_requirement(runtime, consumer, provider)

    result = LeanAdminApi(runtime).resume_requirement(
        RequirementResumeInput(
            consumer_repo_root=consumer,
            requirement_name="need_provider",
            provider_repo="Provider",
        )
    )

    assert not result.ok
    assert result.issues[0].kind == "waiting_coordinator_flow_not_found"
    waiting = runtime.repo_workspace.requirement.get_requirement(consumer, name="need_provider")
    assert waiting.value.requirement.provider_result_observed_at is None


def test_admin_requirement_resume_rejects_ambiguous_waiting_flows_before_observed(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    consumer = tmp_path / "Consumer"
    provider = tmp_path / "Provider"
    _prepare_satisfied_requirement(runtime, consumer, provider)
    first_flow_id, _ = _create_waiting_coordinator_flow(runtime, consumer)
    second_flow_id, _ = _create_waiting_coordinator_flow(runtime, consumer)

    result = LeanAdminApi(runtime).resume_requirement(
        RequirementResumeInput(
            consumer_repo_root=consumer,
            requirement_name="need_provider",
            provider_repo="Provider",
        )
    )

    assert not result.ok
    assert result.issues[0].kind == "waiting_coordinator_flow_ambiguous"
    assert first_flow_id in result.issues[0].details["flow_ids"]
    assert second_flow_id in result.issues[0].details["flow_ids"]
    waiting = runtime.repo_workspace.requirement.get_requirement(consumer, name="need_provider")
    assert waiting.value.requirement.provider_result_observed_at is None


def test_admin_requirement_resume_rejects_missing_binding_before_observed(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    consumer = tmp_path / "Consumer"
    provider = tmp_path / "Provider"
    _prepare_satisfied_requirement(runtime, consumer, provider)
    _create_waiting_coordinator_flow(runtime, consumer, bind_agent=False)

    result = LeanAdminApi(runtime).resume_requirement(
        RequirementResumeInput(
            consumer_repo_root=consumer,
            requirement_name="need_provider",
            provider_repo="Provider",
        )
    )

    assert not result.ok
    assert result.issues[0].kind == "waiting_coordinator_binding_invalid"
    waiting = runtime.repo_workspace.requirement.get_requirement(consumer, name="need_provider")
    assert waiting.value.requirement.provider_result_observed_at is None


def test_admin_requirement_resume_is_idempotent_after_flow_enters_resume_gate(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    consumer = tmp_path / "Consumer"
    provider = tmp_path / "Provider"
    _prepare_satisfied_requirement(runtime, consumer, provider)
    flow_id, _ = _create_waiting_coordinator_flow(runtime, consumer)
    admin = LeanAdminApi(runtime)
    first = admin.resume_requirement(
        RequirementResumeInput(
            consumer_repo_root=consumer,
            requirement_name="need_provider",
            provider_repo="Provider",
            enqueue=False,
        )
    )
    assert first.ok
    gate_step_id = runtime.ark.flow_service.advance_flow(flow_id)
    assert gate_step_id is not None
    assert runtime.ark.flow_service.get_flow(flow_id).state.position.phase == "requirement_resume_gate"

    second = admin.resume_requirement(
        RequirementResumeInput(
            consumer_repo_root=consumer,
            requirement_name="need_provider",
            provider_repo="Provider",
            enqueue=False,
        )
    )

    assert second.ok and second.value is not None
    assert second.value.resume_flow.flow_id == flow_id
    assert runtime.ark.flow_service.get_flow(flow_id).step_ids == [gate_step_id]
    assert len(runtime.ark.flow_service.list_flows(flow_type="native_repo_coordinator")) == 1
