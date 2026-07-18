"""Admin HTTP routes for a shared Lean Constellation runtime."""

from __future__ import annotations

from pathlib import Path
import inspect
from typing import Any, Callable

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from lean_constellation.app.admin_api import (
    AdminFlowAdvanceInput,
    AdminRunUntilStepCreatedInput,
    AdminStepStartInput,
    BootstrapMainNativeRepoInput,
    CreateMainRepoShellInput,
    InitializeMainNativeSkeletonInput,
    LeanAdminApi,
    MainNativeRepoBootstrapView,
    RepoConfigUpdateInput,
    RepoReleaseIdInput,
    RepoReleaseOrphanCleanupInput,
    RepoReleasePreviewInput,
    RepoReleaseRestoreInput,
    RepoRunRequestInput,
    RepoRunStartInput,
    RequirementResumeInput,
    RunningAgentRepairInput,
    RuntimePauseView,
    RuntimeResumeInput,
    RuntimeSemanticAdvanceInput,
    SnapshotCreateInput,
    SnapshotListInput,
    SnapshotRestoreInput,
    StartFlowInput,
    StartPreparationInput,
    StartRequirementGroupBootstrapInput,
    StandaloneRootInterfaceRunInput,
    StandaloneSourceIndexRunInput,
    ValidateMainSourceCorpusInput,
    WriteMainRepoPreparationInput,
)
from lean_constellation.app.repo_runtime_registry import RepoRuntimeRegistry
from lean_constellation.services.foundation import ServiceResult


def create_workspace_admin_http_routes(
    registry: RepoRuntimeRegistry,
    *,
    toolkit_state: object | None = None,
    on_repo_unload: Callable[[str], None] | None = None,
) -> list[Route]:
    """Create Admin HTTP routes for a workspace server with repo-local runtimes."""

    async def workspace_external_health(request: Request) -> JSONResponse:
        query = request.query_params
        admin = LeanAdminApi(
            registry.workspace_runtime(),
            workspace_root=registry.workspace_root,
            toolkit_state=toolkit_state,
        )
        return _service_result_response(
            admin.get_external_health(
                required_toolkit_groups=_query_csv(query.get("required_toolkit_groups")),
                required_toolkit_tools=_query_csv(query.get("required_toolkit_tools")),
            )
        )

    async def workspace_status(request: Request) -> JSONResponse:
        del request
        return _service_result_response(registry.list_status())

    async def workspace_repos(request: Request) -> JSONResponse:
        del request
        return _service_result_response(registry.list_status())

    async def workspace_waiting_requirements(request: Request) -> JSONResponse:
        query = request.query_params
        admin = LeanAdminApi(registry.workspace_runtime(), workspace_root=registry.workspace_root)
        return _service_result_response(
            admin.list_waiting_requirements(
                workspace_root=_query_path(query.get("workspace_root")) or registry.workspace_root,
                repo_root=_query_path(query.get("repo_root")),
                provider_repo=query.get("provider_repo"),
            )
        )

    async def workspace_requirement_resume_candidates(request: Request) -> JSONResponse:
        query = request.query_params
        provider_repo = query.get("provider_repo")
        if not provider_repo:
            return _request_validation_response("Query parameter 'provider_repo' is required.")
        admin = LeanAdminApi(registry.workspace_runtime(), workspace_root=registry.workspace_root)
        return _service_result_response(
            admin.list_requirement_resume_candidates(
                provider_repo=provider_repo,
                workspace_root=_query_path(query.get("workspace_root")) or registry.workspace_root,
            )
        )

    async def repo_status(request: Request) -> JSONResponse:
        return _service_result_response(registry.get_status(request.path_params["repo_key"]))

    async def repo_load(request: Request) -> JSONResponse:
        repo_key = request.path_params["repo_key"]
        loaded = registry.get_or_load(repo_key)
        if not loaded.ok:
            return _service_result_response(loaded)
        return _service_result_response(registry.get_status(repo_key))

    async def repo_unload(request: Request) -> JSONResponse:
        data = await _json_or_empty(request)
        repo_key = request.path_params["repo_key"]
        result = registry.unload(
            repo_key,
            require_stable=not _body_bool(data.get("force")),
        )
        if result.ok and on_repo_unload is not None:
            cleanup = on_repo_unload(repo_key)
            if inspect.isawaitable(cleanup):
                await cleanup
        return _service_result_response(result)

    async def repo_pause(request: Request) -> JSONResponse:
        return _service_result_response(registry.pause(request.path_params["repo_key"]))

    async def repo_resume(request: Request) -> JSONResponse:
        try:
            data = await _json_or_empty(request)
            input_model = RuntimeResumeInput.model_validate(data)
        except ValidationError as exc:
            return _request_validation_response(str(exc))
        if input_model.scope_id is not None:
            return _request_validation_response("Workspace repo resume is global and cannot specify scope_id.")
        return _service_result_response(
            registry.resume(
                request.path_params["repo_key"],
                budget=input_model.budget,
                rebuild_queues=not input_model.skip_rebuild,
            )
        )

    async def workspace_start_requirement_bootstrap(request: Request) -> JSONResponse:
        try:
            data = await _json_or_empty(request)
            input_model = StartRequirementGroupBootstrapInput.model_validate(data)
        except ValidationError as exc:
            return _request_validation_response(str(exc))
        control_admin = LeanAdminApi(
            registry.workspace_runtime(),
            workspace_root=registry.workspace_root,
            repo_runtime_registry=registry,
        )
        result = control_admin.start_requirement_group_bootstrap(input_model)
        return _service_result_response(result)

    async def workspace_resume_requirement(request: Request) -> JSONResponse:
        try:
            data = await _json_or_empty(request)
            input_model = RequirementResumeInput.model_validate(data)
        except ValidationError as exc:
            return _request_validation_response(str(exc))
        repo_key = input_model.consumer_repo_root.name
        loaded = registry.get_or_load(repo_key, refresh_homes=False)
        if not loaded.ok or loaded.value is None:
            return _service_result_response(loaded)
        admin = LeanAdminApi(loaded.value, workspace_root=registry.workspace_root)
        return _service_result_response(admin.resume_requirement(input_model))

    async def workspace_main_repo_status(request: Request) -> JSONResponse:
        query = request.query_params
        repo_root = _query_path(query.get("repo_root"))
        if repo_root is None:
            return _request_validation_response("Query parameter 'repo_root' is required.")
        loaded = registry.get_or_load(repo_root.name, refresh_homes=False)
        runtime = loaded.value if loaded.ok and loaded.value is not None else registry.workspace_runtime()
        admin = LeanAdminApi(runtime, workspace_root=registry.workspace_root)
        return _service_result_response(admin.get_main_repo_status(repo_root))

    async def workspace_create_main_repo_shell(request: Request) -> JSONResponse:
        admin = LeanAdminApi(registry.workspace_runtime(), workspace_root=registry.workspace_root)
        return await _model_route(request, CreateMainRepoShellInput, admin.create_main_repo_shell)

    async def workspace_write_main_repo_input(request: Request) -> JSONResponse:
        admin = LeanAdminApi(registry.workspace_runtime(), workspace_root=registry.workspace_root)
        return await _model_route(request, WriteMainRepoPreparationInput, admin.write_main_repo_preparation_input)

    async def workspace_validate_main_source(request: Request) -> JSONResponse:
        admin = LeanAdminApi(registry.workspace_runtime(), workspace_root=registry.workspace_root)
        return await _model_route(request, ValidateMainSourceCorpusInput, admin.validate_main_source_corpus)

    async def workspace_init_main_native_skeleton(request: Request) -> JSONResponse:
        admin = LeanAdminApi(registry.workspace_runtime(), workspace_root=registry.workspace_root)
        return await _model_route(request, InitializeMainNativeSkeletonInput, admin.initialize_main_native_skeleton)

    async def workspace_bootstrap_main_native(request: Request) -> JSONResponse:
        try:
            data = await _json_or_empty(request)
            input_model = BootstrapMainNativeRepoInput.model_validate(data)
        except ValidationError as exc:
            return _request_validation_response(str(exc))
        control_admin = LeanAdminApi(registry.workspace_runtime(), workspace_root=registry.workspace_root)
        shell = control_admin.create_main_repo_shell(
            CreateMainRepoShellInput(
                workspace_root=input_model.workspace_root,
                repo_name=input_model.repo_name,
                project_name=input_model.project_name,
            )
        )
        if not shell.ok or shell.value is None:
            return _service_result_response(shell)
        repo_root = Path(shell.value.repo_root)
        written = control_admin.write_main_repo_preparation_input(
            WriteMainRepoPreparationInput(repo_root=repo_root, input=input_model.preparation_input)
        )
        if not written.ok or written.value is None:
            return _service_result_response(written)
        source_validation = None
        if input_model.validate_source_corpus:
            validated = control_admin.validate_main_source_corpus(ValidateMainSourceCorpusInput(repo_root=repo_root))
            if not validated.ok or validated.value is None:
                return _service_result_response(validated)
            source_validation = validated.value
        skeleton = control_admin.initialize_main_native_skeleton(
            InitializeMainNativeSkeletonInput(repo_root=repo_root, project_name=input_model.project_name)
        )
        if not skeleton.ok or skeleton.value is None:
            return _service_result_response(skeleton)
        loaded = registry.get_or_load(input_model.repo_name)
        if not loaded.ok or loaded.value is None:
            return _service_result_response(loaded)
        repo_admin_instance = LeanAdminApi(loaded.value, workspace_root=registry.workspace_root)
        preparation = repo_admin_instance.start_native_preparation(
            StartPreparationInput(
                repo_root=repo_root,
                repo_key=input_model.repo_name,
                start_reason="admin",
                admin_notes="Started by main native repo bootstrap.",
                enqueue=input_model.enqueue,
            )
        )
        if not preparation.ok or preparation.value is None:
            return _service_result_response(preparation)
        return _service_result_response(
            loaded.value.foundation.ok(
                MainNativeRepoBootstrapView(
                    shell=shell.value,
                    preparation_input=written.value,
                    source_corpus_validation=source_validation,
                    skeleton=skeleton.value,
                    preparation_flow=preparation.value,
                    summary=f"Bootstrapped main native repo {input_model.repo_name}.",
                )
            )
        )

    async def repo_runtime_status(request: Request) -> JSONResponse:
        loaded = registry.get_or_load(request.path_params["repo_key"], refresh_homes=False)
        if not loaded.ok or loaded.value is None:
            return _service_result_response(loaded)
        admin = LeanAdminApi(loaded.value, workspace_root=registry.workspace_root)
        return _service_result_response(admin.get_runtime_status())

    async def repo_runtime_pause(request: Request) -> JSONResponse:
        loaded = registry.get_or_load(request.path_params["repo_key"], refresh_homes=False)
        if not loaded.ok or loaded.value is None:
            return _service_result_response(loaded)
        data = await _json_or_empty(request)
        admin = LeanAdminApi(loaded.value, workspace_root=registry.workspace_root)
        record = registry.discover_repo(request.path_params["repo_key"])
        if not record.ok or record.value is None:
            return _service_result_response(record)
        with record.value.lock:
            result = admin.pause_runtime(scope_id=data.get("scope_id"))
            if not result.ok:
                return _service_result_response(result)
            paused_record = registry.pause(request.path_params["repo_key"])
            if not paused_record.ok:
                return _service_result_response(paused_record)
            controller = loaded.value.ark.pause_controller
            schedule_service = loaded.value.ark.schedule_service
            scope_id = data.get("scope_id")
            paused = bool(controller.is_paused(scope_id)) if controller is not None else False
            run_control = (
                schedule_service.get_run_control_view()
                if schedule_service is not None and hasattr(schedule_service, "get_run_control_view")
                else None
            )
            return _service_result_response(
                loaded.value.foundation.ok(
                    RuntimePauseView(
                        paused=paused,
                        scope_id=scope_id,
                        run_control=run_control,
                        summary="Paused runtime scheduling.",
                    )
                )
            )

    async def repo_runtime_resume(request: Request) -> JSONResponse:
        try:
            data = await _json_or_empty(request)
            input_model = RuntimeResumeInput.model_validate(data)
        except ValidationError as exc:
            return _request_validation_response(str(exc))
        loaded = registry.get_or_load(request.path_params["repo_key"], refresh_homes=False)
        if not loaded.ok or loaded.value is None:
            return _service_result_response(loaded)
        admin = LeanAdminApi(loaded.value, workspace_root=registry.workspace_root)
        record = registry.discover_repo(request.path_params["repo_key"])
        if not record.ok or record.value is None:
            return _service_result_response(record)
        with record.value.lock:
            if input_model.scope_id is None:
                resumed = registry.resume(
                    request.path_params["repo_key"],
                    budget=input_model.budget,
                    rebuild_queues=not input_model.skip_rebuild,
                )
            else:
                scoped = admin.resume_runtime(input_model)
                if not scoped.ok:
                    return _service_result_response(scoped)
                resumed = registry.resume(
                    request.path_params["repo_key"],
                    rebuild_queues=False,
                )
            if not resumed.ok:
                return _service_result_response(resumed)
            controller = loaded.value.ark.pause_controller
            schedule_service = loaded.value.ark.schedule_service
            paused = bool(controller.is_paused(input_model.scope_id)) if controller is not None else False
            run_control = (
                schedule_service.get_run_control_view()
                if schedule_service is not None and hasattr(schedule_service, "get_run_control_view")
                else None
            )
            result = loaded.value.foundation.ok(
                RuntimePauseView(
                    paused=paused,
                    scope_id=input_model.scope_id,
                    run_control=run_control,
                    summary="Resumed runtime scheduling.",
                )
            )
            return _service_result_response(result)

    async def repo_runtime_semantic_advance(request: Request) -> JSONResponse:
        try:
            data = await _json_or_empty(request)
            input_model = RuntimeSemanticAdvanceInput.model_validate(data)
        except ValidationError as exc:
            return _request_validation_response(str(exc))
        loaded = registry.get_or_load(request.path_params["repo_key"], refresh_homes=False)
        if not loaded.ok or loaded.value is None:
            return _service_result_response(loaded)
        record = registry.discover_repo(request.path_params["repo_key"])
        if not record.ok or record.value is None:
            return _service_result_response(record)
        with record.value.lock:
            admin = LeanAdminApi(loaded.value, workspace_root=registry.workspace_root)
            return _service_result_response(admin.semantic_advance(input_model))

    def repo_admin(request: Request) -> ServiceResult[LeanAdminApi]:
        loaded = registry.get_or_load(request.path_params["repo_key"], refresh_homes=False)
        if not loaded.ok or loaded.value is None:
            return registry.result.fail(loaded.issues)
        return registry.result.ok(LeanAdminApi(loaded.value, workspace_root=registry.workspace_root))

    async def repo_flow_tree(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        query = request.query_params
        return _service_result_response(
            admin_result.value.list_flow_tree(
                scope_id=query.get("scope_id"),
                include_terminal=not _query_bool(query.get("nonterminal_only")),
            )
        )

    async def repo_flow_monitor(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        return _service_result_response(admin_result.value.get_flow_monitor(request.path_params["flow_id"]))

    async def repo_step_monitor(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        return _service_result_response(admin_result.value.get_step_monitor(request.path_params["step_id"]))

    async def repo_agents_monitor(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        query = request.query_params
        return _service_result_response(
            admin_result.value.list_agent_monitor(
                scope_id=query.get("scope_id"),
                agent_type=query.get("agent_type"),
                status=query.get("status"),
            )
        )

    async def repo_running_agents_audit(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        return _service_result_response(
            admin_result.value.audit_running_agents(repo_key=request.path_params["repo_key"])
        )

    async def repo_running_agent_repair(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        try:
            input_model = RunningAgentRepairInput.model_validate(await _json_or_empty(request))
        except ValidationError as exc:
            return _request_validation_response(str(exc))
        return _service_result_response(
            admin_result.value.repair_running_agent(
                request.path_params["agent_id"],
                input_model,
                repo_key=request.path_params["repo_key"],
            )
        )

    async def repo_config(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        record = registry.discover_repo(request.path_params["repo_key"])
        if not record.ok or record.value is None:
            return _service_result_response(record)
        return _service_result_response(admin_result.value.get_repo_config(record.value.repo_root))

    async def repo_update_config(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        record = registry.discover_repo(request.path_params["repo_key"])
        if not record.ok or record.value is None:
            return _service_result_response(record)
        try:
            data = await _json_or_empty(request)
            data["repo_root"] = str(record.value.repo_root)
            input_model = RepoConfigUpdateInput.model_validate(data)
        except ValidationError as exc:
            return _request_validation_response(str(exc))
        return _service_result_response(admin_result.value.update_repo_config(input_model))

    async def repo_publication(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        record = registry.discover_repo(request.path_params["repo_key"])
        if not record.ok or record.value is None:
            return _service_result_response(record)
        return _service_result_response(admin_result.value.get_repo_publication(record.value.repo_root))

    async def repo_agent_report_index(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        return _service_result_response(admin_result.value.get_agent_report_index(request.path_params["agent_id"]))

    async def repo_start_flow(request: Request) -> JSONResponse:
        return await _repo_model_route(request, registry, StartFlowInput, LeanAdminApi.start_arbitrary_flow)

    async def repo_advance_flow(request: Request) -> JSONResponse:
        return await _repo_model_route(request, registry, AdminFlowAdvanceInput, LeanAdminApi.advance_flow_once)

    async def repo_run_until_step(request: Request) -> JSONResponse:
        return await _repo_model_route(
            request,
            registry,
            AdminRunUntilStepCreatedInput,
            LeanAdminApi.run_until_step_created,
        )

    async def repo_start_step(request: Request) -> JSONResponse:
        return await _repo_model_route(request, registry, AdminStepStartInput, LeanAdminApi.start_step_once)

    async def repo_wait_step(request: Request) -> JSONResponse:
        return await _repo_model_route(request, registry, AdminStepStartInput, LeanAdminApi.wait_step)

    async def repo_start_native_preparation(request: Request) -> JSONResponse:
        return await _repo_lifecycle_model_route(request, registry, StartPreparationInput, LeanAdminApi.start_native_preparation)

    async def repo_run_initial(request: Request) -> JSONResponse:
        return await _repo_semantic_model_route(
            request, registry, RepoRunStartInput, LeanAdminApi.start_initial_native_repo_run
        )

    async def repo_continue_native(request: Request) -> JSONResponse:
        return await _repo_lifecycle_model_route(request, registry, RepoRunRequestInput, LeanAdminApi.continue_native_repo)

    async def repo_run_continue(request: Request) -> JSONResponse:
        return await _repo_semantic_model_route(
            request, registry, RepoRunRequestInput, LeanAdminApi.start_native_repo_continuation
        )

    async def repo_run_status(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        record = registry.discover_repo(request.path_params["repo_key"])
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        if not record.ok or record.value is None:
            return _service_result_response(record)
        return _service_result_response(
            admin_result.value.get_repo_run_status(record.value.repo_root, repo_key=record.value.repo_key)
        )

    async def repo_start_source_index(request: Request) -> JSONResponse:
        return await _repo_lifecycle_model_route(
            request, registry, StandaloneSourceIndexRunInput, LeanAdminApi.start_standalone_source_index
        )

    async def repo_run_source_index(request: Request) -> JSONResponse:
        return await _repo_semantic_model_route(
            request, registry, StandaloneSourceIndexRunInput, LeanAdminApi.start_source_index_run
        )

    async def repo_start_root_interfaces(request: Request) -> JSONResponse:
        return await _repo_lifecycle_model_route(
            request, registry, StandaloneRootInterfaceRunInput, LeanAdminApi.start_standalone_root_interfaces
        )

    async def repo_run_root_interfaces(request: Request) -> JSONResponse:
        return await _repo_semantic_model_route(
            request, registry, StandaloneRootInterfaceRunInput, LeanAdminApi.start_root_interface_run
        )

    async def repo_releases(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        record = registry.discover_repo(request.path_params["repo_key"])
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        if not record.ok or record.value is None:
            return _service_result_response(record)
        with record.value.lock:
            return _service_result_response(admin_result.value.list_repo_releases(record.value.repo_root))

    async def repo_release(request: Request) -> JSONResponse:
        return await _repo_path_model_route(
            request,
            registry,
            RepoReleaseIdInput,
            lambda admin, model: admin.get_repo_release(model.repo_root, release_id=model.release_id),
        )

    async def repo_release_preview(request: Request) -> JSONResponse:
        return await _repo_semantic_model_route(
            request,
            registry,
            RepoReleasePreviewInput,
            lambda admin, model: admin.preview_repo_release(model.repo_root, summary=model.summary),
        )

    async def repo_release_restore(request: Request) -> JSONResponse:
        return await _repo_path_model_route(
            request, registry, RepoReleaseRestoreInput, LeanAdminApi.restore_repo_release
        )

    async def repo_release_audit(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        record = registry.discover_repo(request.path_params["repo_key"])
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        if not record.ok or record.value is None:
            return _service_result_response(record)
        with record.value.lock:
            return _service_result_response(admin_result.value.audit_repo_releases(record.value.repo_root))


    async def repo_release_cleanup_orphans(request: Request) -> JSONResponse:
        return await _repo_root_semantic_model_route(
            request,
            registry,
            RepoReleaseOrphanCleanupInput,
            LeanAdminApi.cleanup_repo_release_orphans,
        )

    async def repo_release_reconcile_requirements(request: Request) -> JSONResponse:
        return await _repo_path_model_route(
            request,
            registry,
            RepoReleaseIdInput,
            lambda admin, model: admin.reconcile_repo_requirements(
                model.repo_root, release_id=model.release_id
            ),
        )

    async def repo_start_adapter_preparation(request: Request) -> JSONResponse:
        return await _repo_lifecycle_model_route(
            request,
            registry,
            StartPreparationInput,
            LeanAdminApi.start_adapter_preparation,
        )

    async def repo_create_snapshot(request: Request) -> JSONResponse:
        return await _repo_root_semantic_model_route(
            request,
            registry,
            SnapshotCreateInput,
            LeanAdminApi.create_snapshot,
        )

    async def repo_list_snapshots(request: Request) -> JSONResponse:
        return await _repo_root_semantic_model_route(
            request,
            registry,
            SnapshotListInput,
            LeanAdminApi.list_snapshots,
        )

    async def repo_restore_snapshot(request: Request) -> JSONResponse:
        return await _repo_root_semantic_model_route(
            request,
            registry,
            SnapshotRestoreInput,
            LeanAdminApi.restore_snapshot,
        )

    async def repo_agent_rollout(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        return _service_result_response(admin_result.value.get_agent_rollout_info(request.path_params["agent_id"]))

    async def repo_agent_turns(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        return _service_result_response(admin_result.value.list_agent_turns(request.path_params["agent_id"]))

    async def repo_agent_turn(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        query = request.query_params
        try:
            index = _query_int(query.get("index"), field="index")
        except ValueError as exc:
            return _request_validation_response(str(exc))
        return _service_result_response(
            admin_result.value.get_agent_turn(
                request.path_params["agent_id"],
                turn_id=query.get("turn_id"),
                index=index,
                latest=_query_bool(query.get("latest")),
            )
        )

    async def repo_agent_event(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        query = request.query_params
        try:
            index = _query_int(query.get("index"), field="index")
        except ValueError as exc:
            return _request_validation_response(str(exc))
        return _service_result_response(
            admin_result.value.get_agent_event(
                request.path_params["agent_id"],
                index=index,
                last=_query_bool(query.get("last")),
            )
        )

    async def repo_agent_events_tail(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        query = request.query_params
        try:
            limit = _query_int(query.get("limit"), field="limit") or 20
        except ValueError as exc:
            return _request_validation_response(str(exc))
        return _service_result_response(
            admin_result.value.tail_agent_events(
                request.path_params["agent_id"],
                limit=limit,
                event_type=query.get("event_type"),
                payload_type=query.get("payload_type"),
            )
        )

    async def repo_agent_responses(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        query = request.query_params
        return _service_result_response(
            admin_result.value.list_agent_response_texts(
                request.path_params["agent_id"],
                turn_id=query.get("turn_id"),
                latest=_query_bool(query.get("latest")),
            )
        )

    async def repo_agent_latest_response(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        return _service_result_response(admin_result.value.get_latest_agent_response_text(request.path_params["agent_id"]))

    async def repo_agent_tool_calls(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        query = request.query_params
        return _service_result_response(
            admin_result.value.list_agent_tool_calls(
                request.path_params["agent_id"],
                turn_id=query.get("turn_id"),
                latest=_query_bool(query.get("latest")),
            )
        )

    async def repo_agent_tool_call(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        query = request.query_params
        try:
            index = _query_int(query.get("index"), field="index")
        except ValueError as exc:
            return _request_validation_response(str(exc))
        return _service_result_response(
            admin_result.value.get_agent_tool_call(
                request.path_params["agent_id"],
                call_id=query.get("call_id"),
                index=index,
                last=_query_bool(query.get("last")),
            )
        )

    async def repo_agent_trace_report(request: Request) -> JSONResponse:
        admin_result = repo_admin(request)
        if not admin_result.ok or admin_result.value is None:
            return _service_result_response(admin_result)
        query = request.query_params
        return _service_result_response(
            admin_result.value.export_agent_trace_report(
                request.path_params["agent_id"],
                artifact_path=query.get("artifact_path"),
                output_path=query.get("output_path"),
                format=query.get("format") or "json",
                rebuild=_query_bool(query.get("rebuild")),
            )
        )

    return [
        Route("/admin/workspace/status", workspace_status, methods=["GET"]),
        Route("/admin/workspace/repos", workspace_repos, methods=["GET"]),
        Route("/admin/workspace/external/health", workspace_external_health, methods=["GET"]),
        Route("/admin/workspace/requirements/waiting", workspace_waiting_requirements, methods=["GET"]),
        Route("/admin/workspace/requirements/resume-candidates", workspace_requirement_resume_candidates, methods=["GET"]),
        Route("/admin/workspace/requirements/bootstrap", workspace_start_requirement_bootstrap, methods=["POST"]),
        Route("/admin/workspace/requirements/resume", workspace_resume_requirement, methods=["POST"]),
        Route("/admin/workspace/main-repo/status", workspace_main_repo_status, methods=["GET"]),
        Route("/admin/workspace/main-repo/shell", workspace_create_main_repo_shell, methods=["POST"]),
        Route("/admin/workspace/main-repo/preparation-input", workspace_write_main_repo_input, methods=["POST"]),
        Route("/admin/workspace/main-repo/source-corpus/validate", workspace_validate_main_source, methods=["POST"]),
        Route("/admin/workspace/main-repo/native-skeleton/init", workspace_init_main_native_skeleton, methods=["POST"]),
        Route("/admin/workspace/main-repo/bootstrap-native", workspace_bootstrap_main_native, methods=["POST"]),
        Route("/admin/workspace/repos/{repo_key:str}", repo_status, methods=["GET"]),
        Route("/admin/workspace/repos/{repo_key:str}/load", repo_load, methods=["POST"]),
        Route("/admin/workspace/repos/{repo_key:str}/unload", repo_unload, methods=["POST"]),
        Route("/admin/workspace/repos/{repo_key:str}/pause", repo_pause, methods=["POST"]),
        Route("/admin/workspace/repos/{repo_key:str}/resume", repo_resume, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/runtime/status", repo_runtime_status, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/config", repo_config, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/config", repo_update_config, methods=["PATCH"]),
        Route("/admin/repos/{repo_key:str}/publication", repo_publication, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/runtime/pause", repo_runtime_pause, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/runtime/resume", repo_runtime_resume, methods=["POST"]),
        Route(
            "/admin/repos/{repo_key:str}/runtime/semantic-advance",
            repo_runtime_semantic_advance,
            methods=["POST"],
        ),
        Route("/admin/repos/{repo_key:str}/flows/tree", repo_flow_tree, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/flows/{flow_id:str}", repo_flow_monitor, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/steps/{step_id:str}", repo_step_monitor, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/agents", repo_agents_monitor, methods=["GET"]),
        Route(
            "/admin/repos/{repo_key:str}/agents/running-audit",
            repo_running_agents_audit,
            methods=["GET"],
        ),
        Route(
            "/admin/repos/{repo_key:str}/agents/{agent_id:str}/repair-running",
            repo_running_agent_repair,
            methods=["POST"],
        ),
        Route("/admin/repos/{repo_key:str}/agents/{agent_id:str}/report-index", repo_agent_report_index, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/flows/start", repo_start_flow, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/test-control/flows/advance", repo_advance_flow, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/test-control/flows/run-until-step", repo_run_until_step, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/test-control/steps/start", repo_start_step, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/test-control/steps/wait", repo_wait_step, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/preparation/native/start", repo_start_native_preparation, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/runs/initial", repo_run_initial, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/runs/continue", repo_run_continue, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/runs/source-index", repo_run_source_index, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/runs/root-interfaces", repo_run_root_interfaces, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/continue", repo_continue_native, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/run/status", repo_run_status, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/source-index/update", repo_start_source_index, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/root-interfaces/prepare", repo_start_root_interfaces, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/releases", repo_releases, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/releases/preview", repo_release_preview, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/releases/audit", repo_release_audit, methods=["GET"]),
        Route(
            "/admin/repos/{repo_key:str}/releases/cleanup-orphans",
            repo_release_cleanup_orphans,
            methods=["POST"],
        ),
        Route("/admin/repos/{repo_key:str}/releases/{release_id:str}", repo_release, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/releases/{release_id:str}/restore", repo_release_restore, methods=["POST"]),
        Route(
            "/admin/repos/{repo_key:str}/releases/{release_id:str}/reconcile-requirements",
            repo_release_reconcile_requirements,
            methods=["POST"],
        ),
        Route("/admin/repos/{repo_key:str}/preparation/adapter/start", repo_start_adapter_preparation, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/snapshots/create", repo_create_snapshot, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/snapshots/list", repo_list_snapshots, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/snapshots/restore", repo_restore_snapshot, methods=["POST"]),
        Route("/admin/repos/{repo_key:str}/agents/{agent_id:str}/rollout", repo_agent_rollout, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/agents/{agent_id:str}/turns", repo_agent_turns, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/agents/{agent_id:str}/turn", repo_agent_turn, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/agents/{agent_id:str}/event", repo_agent_event, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/agents/{agent_id:str}/events/tail", repo_agent_events_tail, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/agents/{agent_id:str}/responses", repo_agent_responses, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/agents/{agent_id:str}/latest-response", repo_agent_latest_response, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/agents/{agent_id:str}/tool-calls", repo_agent_tool_calls, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/agents/{agent_id:str}/tool-call", repo_agent_tool_call, methods=["GET"]),
        Route("/admin/repos/{repo_key:str}/agents/{agent_id:str}/trace-report", repo_agent_trace_report, methods=["GET"]),
        Route("/admin/main-repo/status", workspace_main_repo_status, methods=["GET"]),
        Route("/admin/main-repo/shell", workspace_create_main_repo_shell, methods=["POST"]),
        Route("/admin/main-repo/preparation-input", workspace_write_main_repo_input, methods=["POST"]),
        Route("/admin/main-repo/source-corpus/validate", workspace_validate_main_source, methods=["POST"]),
        Route("/admin/main-repo/native-skeleton/init", workspace_init_main_native_skeleton, methods=["POST"]),
        Route("/admin/main-repo/bootstrap-native", workspace_bootstrap_main_native, methods=["POST"]),
    ]


async def _model_route(request: Request, model_type, handler: Callable[[Any], ServiceResult[Any]]) -> JSONResponse:  # noqa: ANN001
    try:
        data = await _json_or_empty(request)
        input_model = model_type.model_validate(data)
    except ValidationError as exc:
        return _request_validation_response(str(exc))
    return _service_result_response(handler(input_model))


async def _repo_model_route(request: Request, registry: RepoRuntimeRegistry, model_type, handler: Callable[[Any, Any], ServiceResult[Any]]) -> JSONResponse:  # noqa: ANN001
    loaded = registry.get_or_load(request.path_params["repo_key"], refresh_homes=False)
    if not loaded.ok or loaded.value is None:
        return _service_result_response(loaded)
    try:
        data = await _json_or_empty(request)
        input_model = model_type.model_validate(data)
    except ValidationError as exc:
        return _request_validation_response(str(exc))
    admin = LeanAdminApi(loaded.value, workspace_root=registry.workspace_root)
    record = registry.discover_repo(request.path_params["repo_key"])
    if not record.ok or record.value is None:
        return _service_result_response(record)
    with record.value.lock:
        return _service_result_response(handler(admin, input_model))


async def _repo_lifecycle_model_route(request: Request, registry: RepoRuntimeRegistry, model_type, handler: Callable[[Any, Any], ServiceResult[Any]]) -> JSONResponse:  # noqa: ANN001
    discovered = registry.discover_repo(request.path_params["repo_key"])
    if not discovered.ok or discovered.value is None:
        return _service_result_response(discovered)
    record = discovered.value
    loaded = registry.get_or_load(record.repo_key, refresh_homes=False)
    if not loaded.ok or loaded.value is None:
        return _service_result_response(loaded)
    data = await _json_or_empty(request)
    supplied_key = data.get("repo_key")
    supplied_root = data.get("repo_root")
    if supplied_key is not None and supplied_key != record.repo_key:
        return _request_validation_response("Body repo_key must match the repo key in the route path.")
    if supplied_root is not None and Path(supplied_root).expanduser().resolve() != record.repo_root.resolve():
        return _request_validation_response("Body repo_root must match the repo root selected by the route path.")
    data["repo_key"] = record.repo_key
    data["repo_root"] = str(record.repo_root)
    try:
        input_model = model_type.model_validate(data)
    except ValidationError as exc:
        return _request_validation_response(str(exc))
    admin = LeanAdminApi(loaded.value, workspace_root=registry.workspace_root)
    with record.lock:
        return _service_result_response(handler(admin, input_model))


async def _repo_semantic_model_route(
    request: Request,
    registry: RepoRuntimeRegistry,
    model_type,
    handler: Callable[[Any, Any], ServiceResult[Any]],
) -> JSONResponse:  # noqa: ANN001
    discovered = registry.discover_repo(request.path_params["repo_key"])
    if not discovered.ok or discovered.value is None:
        return _service_result_response(discovered)
    record = discovered.value
    loaded = registry.get_or_load(record.repo_key, refresh_homes=False)
    if not loaded.ok or loaded.value is None:
        return _service_result_response(loaded)
    data = await _json_or_empty(request)
    forbidden = sorted({"repo_root", "repo_key"}.intersection(data))
    if forbidden:
        return _request_validation_response(
            f"Body must not provide route-owned fields: {', '.join(forbidden)}."
        )
    data["repo_key"] = record.repo_key
    data["repo_root"] = str(record.repo_root)
    try:
        input_model = model_type.model_validate(data)
    except ValidationError as exc:
        return _request_validation_response(str(exc))
    admin = LeanAdminApi(loaded.value, workspace_root=registry.workspace_root)
    with record.lock:
        return _service_result_response(handler(admin, input_model))


async def _repo_root_semantic_model_route(
    request: Request,
    registry: RepoRuntimeRegistry,
    model_type,
    handler: Callable[[Any, Any], ServiceResult[Any]],
) -> JSONResponse:  # noqa: ANN001
    """Bind only route-owned repo_root for strict DTOs without a repo_key field."""
    discovered = registry.discover_repo(request.path_params["repo_key"])
    if not discovered.ok or discovered.value is None:
        return _service_result_response(discovered)
    record = discovered.value
    loaded = registry.get_or_load(record.repo_key, refresh_homes=False)
    if not loaded.ok or loaded.value is None:
        return _service_result_response(loaded)
    data = await _json_or_empty(request)
    forbidden = sorted({"repo_root", "repo_key"}.intersection(data))
    if forbidden:
        return _request_validation_response(
            f"Body must not provide route-owned fields: {', '.join(forbidden)}."
        )
    data["repo_root"] = str(record.repo_root)
    try:
        input_model = model_type.model_validate(data)
    except ValidationError as exc:
        return _request_validation_response(str(exc))
    admin = LeanAdminApi(loaded.value, workspace_root=registry.workspace_root)
    with record.lock:
        return _service_result_response(handler(admin, input_model))


async def _repo_path_model_route(
    request: Request,
    registry: RepoRuntimeRegistry,
    model_type,
    handler: Callable[[Any, Any], ServiceResult[Any]],
) -> JSONResponse:  # noqa: ANN001
    discovered = registry.discover_repo(request.path_params["repo_key"])
    if not discovered.ok or discovered.value is None:
        return _service_result_response(discovered)
    record = discovered.value
    loaded = registry.get_or_load(record.repo_key, refresh_homes=False)
    if not loaded.ok or loaded.value is None:
        return _service_result_response(loaded)
    data = await _json_or_empty(request)
    forbidden = sorted({"repo_root", "repo_key", "release_id"}.intersection(data))
    if forbidden:
        return _request_validation_response(
            f"Body must not provide route-owned fields: {', '.join(forbidden)}."
        )
    data["repo_root"] = str(record.repo_root)
    data["release_id"] = request.path_params["release_id"]
    try:
        input_model = model_type.model_validate(data)
    except ValidationError as exc:
        return _request_validation_response(str(exc))
    admin = LeanAdminApi(loaded.value, workspace_root=registry.workspace_root)
    with record.lock:
        return _service_result_response(handler(admin, input_model))


async def _json_or_empty(request: Request) -> dict[str, Any]:
    if not request.headers.get("content-length") and request.method != "GET":
        return {}
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 - malformed or empty JSON.
        return {}
    return data if isinstance(data, dict) else {}


def _service_result_response(result: ServiceResult[Any]) -> JSONResponse:
    status_code = 200 if result.ok else 400
    return JSONResponse(result.model_dump(mode="json"), status_code=status_code)


def _request_validation_response(message: str) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "value": None,
            "issues": [
                {
                    "kind": "request_validation_failed",
                    "message": message,
                    "severity": "error",
                }
            ],
        },
        status_code=422,
    )


def _query_int(value: str | None, *, field: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Query parameter {field!r} must be an integer, got {value!r}.") from exc


def _query_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.lower() in {"1", "true", "yes", "y", "on"}


def _body_bool(value: object | None) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "y", "on"}


def _query_path(value: str | None) -> Path | None:
    if value is None or not value.strip():
        return None
    return Path(value).expanduser()


def _query_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


__all__ = ["create_workspace_admin_http_routes"]
