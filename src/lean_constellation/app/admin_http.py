"""Admin HTTP routes for a shared Lean Constellation runtime."""

from __future__ import annotations

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
    RequirementResumeInput,
    SnapshotCreateInput,
    SnapshotListInput,
    SnapshotRestoreInput,
    StartFlowInput,
    StartPreparationInput,
    StartRequirementGroupBootstrapInput,
    ValidateMainSourceCorpusInput,
    WriteMainRepoPreparationInput,
)
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.runtime import LeanRuntimeServices


def create_admin_http_routes(runtime: LeanRuntimeServices) -> list[Route]:
    admin = LeanAdminApi(runtime)

    async def health(request: Request) -> JSONResponse:
        del request
        return JSONResponse({"ok": True, "service": "lean_constellation_production_app"})

    async def status(request: Request) -> JSONResponse:
        del request
        return _service_result_response(admin.get_runtime_status())

    async def pause(request: Request) -> JSONResponse:
        data = await _json_or_empty(request)
        return _service_result_response(admin.pause_runtime(scope_id=data.get("scope_id")))

    async def resume(request: Request) -> JSONResponse:
        data = await _json_or_empty(request)
        return _service_result_response(admin.resume_runtime(scope_id=data.get("scope_id")))

    async def start_flow(request: Request) -> JSONResponse:
        return await _model_route(request, StartFlowInput, admin.start_arbitrary_flow)

    async def advance_flow(request: Request) -> JSONResponse:
        return await _model_route(request, AdminFlowAdvanceInput, admin.advance_flow_once)

    async def run_until_step(request: Request) -> JSONResponse:
        return await _model_route(request, AdminRunUntilStepCreatedInput, admin.run_until_step_created)

    async def start_step(request: Request) -> JSONResponse:
        return await _model_route(request, AdminStepStartInput, admin.start_step_once)

    async def wait_step(request: Request) -> JSONResponse:
        return await _model_route(request, AdminStepStartInput, admin.wait_step)

    async def start_native_preparation(request: Request) -> JSONResponse:
        return await _model_route(request, StartPreparationInput, admin.start_native_preparation)

    async def start_adapter_preparation(request: Request) -> JSONResponse:
        return await _model_route(request, StartPreparationInput, admin.start_adapter_preparation)

    async def start_requirement_bootstrap(request: Request) -> JSONResponse:
        return await _model_route(request, StartRequirementGroupBootstrapInput, admin.start_requirement_group_bootstrap)

    async def resume_requirement(request: Request) -> JSONResponse:
        return await _model_route(request, RequirementResumeInput, admin.resume_requirement)

    async def create_snapshot(request: Request) -> JSONResponse:
        return await _model_route(request, SnapshotCreateInput, admin.create_snapshot)

    async def list_snapshots(request: Request) -> JSONResponse:
        return await _model_route(request, SnapshotListInput, admin.list_snapshots)

    async def restore_snapshot(request: Request) -> JSONResponse:
        return await _model_route(request, SnapshotRestoreInput, admin.restore_snapshot)

    async def create_main_repo_shell(request: Request) -> JSONResponse:
        return await _model_route(request, CreateMainRepoShellInput, admin.create_main_repo_shell)

    async def write_main_repo_input(request: Request) -> JSONResponse:
        return await _model_route(request, WriteMainRepoPreparationInput, admin.write_main_repo_preparation_input)

    async def validate_main_source(request: Request) -> JSONResponse:
        return await _model_route(request, ValidateMainSourceCorpusInput, admin.validate_main_source_corpus)

    async def init_main_native_skeleton(request: Request) -> JSONResponse:
        return await _model_route(request, InitializeMainNativeSkeletonInput, admin.initialize_main_native_skeleton)

    async def bootstrap_main_native(request: Request) -> JSONResponse:
        return await _model_route(request, BootstrapMainNativeRepoInput, admin.bootstrap_main_native_repo)

    async def agent_rollout(request: Request) -> JSONResponse:
        return _service_result_response(admin.get_agent_rollout_info(request.path_params["agent_id"]))

    async def agent_turns(request: Request) -> JSONResponse:
        return _service_result_response(admin.list_agent_turns(request.path_params["agent_id"]))

    async def agent_turn(request: Request) -> JSONResponse:
        query = request.query_params
        try:
            index = _query_int(query.get("index"), field="index")
        except ValueError as exc:
            return _request_validation_response(str(exc))
        return _service_result_response(
            admin.get_agent_turn(
                request.path_params["agent_id"],
                turn_id=query.get("turn_id"),
                index=index,
                latest=_query_bool(query.get("latest")),
            )
        )

    async def agent_event(request: Request) -> JSONResponse:
        query = request.query_params
        try:
            index = _query_int(query.get("index"), field="index")
        except ValueError as exc:
            return _request_validation_response(str(exc))
        return _service_result_response(
            admin.get_agent_event(
                request.path_params["agent_id"],
                index=index,
                last=_query_bool(query.get("last")),
            )
        )

    async def agent_events_tail(request: Request) -> JSONResponse:
        query = request.query_params
        try:
            limit = _query_int(query.get("limit"), field="limit") or 20
        except ValueError as exc:
            return _request_validation_response(str(exc))
        return _service_result_response(
            admin.tail_agent_events(
                request.path_params["agent_id"],
                limit=limit,
                event_type=query.get("event_type"),
                payload_type=query.get("payload_type"),
            )
        )

    async def agent_responses(request: Request) -> JSONResponse:
        query = request.query_params
        return _service_result_response(
            admin.list_agent_response_texts(
                request.path_params["agent_id"],
                turn_id=query.get("turn_id"),
                latest=_query_bool(query.get("latest")),
            )
        )

    async def agent_latest_response(request: Request) -> JSONResponse:
        return _service_result_response(admin.get_latest_agent_response_text(request.path_params["agent_id"]))

    async def agent_tool_calls(request: Request) -> JSONResponse:
        query = request.query_params
        return _service_result_response(
            admin.list_agent_tool_calls(
                request.path_params["agent_id"],
                turn_id=query.get("turn_id"),
                latest=_query_bool(query.get("latest")),
            )
        )

    async def agent_latest_tool_calls(request: Request) -> JSONResponse:
        return _service_result_response(admin.list_agent_tool_calls(request.path_params["agent_id"], latest=True))

    async def agent_tool_call(request: Request) -> JSONResponse:
        query = request.query_params
        try:
            index = _query_int(query.get("index"), field="index")
        except ValueError as exc:
            return _request_validation_response(str(exc))
        return _service_result_response(
            admin.get_agent_tool_call(
                request.path_params["agent_id"],
                call_id=query.get("call_id"),
                index=index,
                last=_query_bool(query.get("last")),
            )
        )

    async def agent_trace_report(request: Request) -> JSONResponse:
        query = request.query_params
        return _service_result_response(
            admin.export_agent_trace_report(
                request.path_params["agent_id"],
                artifact_path=query.get("artifact_path"),
                output_path=query.get("output_path"),
                format=query.get("format") or "json",
            )
        )

    return [
        Route("/health", health, methods=["GET"]),
        Route("/admin/runtime/status", status, methods=["GET"]),
        Route("/admin/runtime/pause", pause, methods=["POST"]),
        Route("/admin/runtime/resume", resume, methods=["POST"]),
        Route("/admin/flows/start", start_flow, methods=["POST"]),
        Route("/admin/test-control/flows/advance", advance_flow, methods=["POST"]),
        Route("/admin/test-control/flows/run-until-step", run_until_step, methods=["POST"]),
        Route("/admin/test-control/steps/start", start_step, methods=["POST"]),
        Route("/admin/test-control/steps/wait", wait_step, methods=["POST"]),
        Route("/admin/preparation/native/start", start_native_preparation, methods=["POST"]),
        Route("/admin/preparation/adapter/start", start_adapter_preparation, methods=["POST"]),
        Route("/admin/requirements/bootstrap", start_requirement_bootstrap, methods=["POST"]),
        Route("/admin/requirements/resume", resume_requirement, methods=["POST"]),
        Route("/admin/snapshots/create", create_snapshot, methods=["POST"]),
        Route("/admin/snapshots/list", list_snapshots, methods=["POST"]),
        Route("/admin/snapshots/restore", restore_snapshot, methods=["POST"]),
        Route("/admin/main-repo/shell", create_main_repo_shell, methods=["POST"]),
        Route("/admin/main-repo/preparation-input", write_main_repo_input, methods=["POST"]),
        Route("/admin/main-repo/source-corpus/validate", validate_main_source, methods=["POST"]),
        Route("/admin/main-repo/native-skeleton/init", init_main_native_skeleton, methods=["POST"]),
        Route("/admin/main-repo/bootstrap-native", bootstrap_main_native, methods=["POST"]),
        Route("/admin/agents/{agent_id:str}/rollout", agent_rollout, methods=["GET"]),
        Route("/admin/agents/{agent_id:str}/turns", agent_turns, methods=["GET"]),
        Route("/admin/agents/{agent_id:str}/turn", agent_turn, methods=["GET"]),
        Route("/admin/agents/{agent_id:str}/event", agent_event, methods=["GET"]),
        Route("/admin/agents/{agent_id:str}/events/tail", agent_events_tail, methods=["GET"]),
        Route("/admin/agents/{agent_id:str}/responses", agent_responses, methods=["GET"]),
        Route("/admin/agents/{agent_id:str}/latest-response", agent_latest_response, methods=["GET"]),
        Route("/admin/agents/{agent_id:str}/tool-calls", agent_tool_calls, methods=["GET"]),
        Route("/admin/agents/{agent_id:str}/tool-calls/latest", agent_latest_tool_calls, methods=["GET"]),
        Route("/admin/agents/{agent_id:str}/tool-call", agent_tool_call, methods=["GET"]),
        Route("/admin/agents/{agent_id:str}/trace-report", agent_trace_report, methods=["GET"]),
    ]


async def _model_route(request: Request, model_type, handler: Callable[[Any], ServiceResult[Any]]) -> JSONResponse:  # noqa: ANN001
    try:
        data = await _json_or_empty(request)
        input_model = model_type.model_validate(data)
    except ValidationError as exc:
        return _request_validation_response(str(exc))
    return _service_result_response(handler(input_model))


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


__all__ = ["create_admin_http_routes"]
