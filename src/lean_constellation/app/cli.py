"""Minimal local CLI wrapper for Lean Constellation Admin API."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from json import JSONDecodeError
from pathlib import Path
from urllib import error, request
from urllib.parse import urlencode

import anyio

from lean_constellation.app.admin_api import LeanAdminApi
from lean_constellation.app.config import load_app_config
from lean_constellation.app.external_takeover import (
    ExternalTakeoverCompleteInput,
    ExternalTakeoverToolCallInput,
    ExternalTakeoverToolListInput,
)
from lean_constellation.app.runtime import create_app_runtime_from_config
from lean_constellation.app.server import run_production_app_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lean-constellation", description="Lean Constellation admin CLI")
    parser.add_argument("--config", type=Path, default=None, help="Path to JSON/TOML app config.")
    parser.add_argument("--admin-base-url", default=None, help="Admin HTTP base URL. Defaults to config admin_http_base_url.")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("config-view", help="Print redacted config view.")
    sub.add_parser("status", help="Read production server runtime status over Admin HTTP.")
    pause = sub.add_parser("pause", help="Pause production server scheduler over Admin HTTP.")
    pause.add_argument("--scope-id", default=None)
    resume = sub.add_parser("resume", help="Resume production server scheduler over Admin HTTP.")
    resume.add_argument("--scope-id", default=None)

    serve = sub.add_parser("serve", help="Run the unified production Admin HTTP + MCP HTTP server.")
    serve.add_argument("--host", default=None, help="Admin HTTP bind host. Defaults to config admin_http_host.")
    serve.add_argument("--port", type=int, default=None, help="Admin HTTP bind port. Defaults to config admin_http_port.")
    serve.add_argument("--mcp-base-url", default=None, help="Advertised MCP base URL for Agent home materialization.")
    serve.add_argument("--view-key", action="append", default=None, help="Limit the server to selected ToolView keys.")
    serve.add_argument("--log-level", default="info")

    start = sub.add_parser("start-flow", help="Start a registered ARK flow.")
    start.add_argument("flow_type")
    start.add_argument("scope_id")
    start.add_argument("--param", action="append", default=[], help="Flow param as key=value. Can be repeated.")
    start.add_argument("--no-enqueue", action="store_true", help="Create the flow without enqueueing it.")

    snapshot = sub.add_parser("snapshot", help="Create a repo checkpoint snapshot.")
    snapshot.add_argument("repo_root", type=Path)
    snapshot.add_argument("--kind", default="requirement_bootstrap_terminal")
    snapshot.add_argument("--label", default=None)

    external_list = sub.add_parser("external-list", help="List external takeover handoffs.")
    external_list.add_argument("--status", default=None, help="Filter by pending/completed/failed/cancelled.")
    external_list.add_argument("--handoff-dirname", default="external_turns")

    external_complete = sub.add_parser("external-complete", help="Complete an external takeover handoff.")
    external_complete.add_argument("handoff_id")
    external_complete.add_argument("--status", choices=["completed", "failed", "cancelled"], default="completed")
    external_complete.add_argument("--final-response", default=None)
    external_complete.add_argument("--error", default=None)
    external_complete.add_argument("--thread-id", default=None)
    external_complete.add_argument("--turn-id", default=None)
    external_complete.add_argument("--rollout-relpath", default=None)
    external_complete.add_argument("--metadata", action="append", default=[], help="Completion metadata as key=value. Can be repeated.")
    external_complete.add_argument("--handoff-dirname", default="external_turns")

    external_tools = sub.add_parser("external-tools", help="List MCP tools available to an external takeover handoff.")
    external_tools.add_argument("handoff_id")
    external_tools.add_argument("--view-kind", choices=["application", "submit"], default="submit")
    external_tools.add_argument("--handoff-dirname", default="external_turns")

    external_call = sub.add_parser("external-call", help="Call an MCP tool for an external takeover handoff.")
    external_call.add_argument("handoff_id")
    external_call.add_argument("tool_name")
    external_call.add_argument("--view-kind", choices=["application", "submit"], default="submit")
    external_call.add_argument("--arg", action="append", default=[], help="Tool argument as key=value. Can be repeated.")
    external_call.add_argument("--handoff-dirname", default="external_turns")

    agent_rollout = sub.add_parser("agent-rollout-info", help="Read Agent rollout location and event count.")
    agent_rollout.add_argument("agent_id")

    agent_turns = sub.add_parser("agent-turns", help="List parsed Agent turns from rollout.")
    agent_turns.add_argument("agent_id")

    agent_turn = sub.add_parser("agent-turn", help="Read one parsed Agent turn.")
    agent_turn.add_argument("agent_id")
    agent_turn.add_argument("--latest", action="store_true")
    agent_turn.add_argument("--turn-id", default=None)
    agent_turn.add_argument("--index", type=int, default=None)

    agent_event = sub.add_parser("agent-event", help="Read one Agent rollout event.")
    agent_event.add_argument("agent_id")
    agent_event.add_argument("--last", action="store_true")
    agent_event.add_argument("--index", type=int, default=None)

    agent_events_tail = sub.add_parser("agent-events-tail", help="Read recent Agent rollout events.")
    agent_events_tail.add_argument("agent_id")
    agent_events_tail.add_argument("--limit", type=int, default=20)
    agent_events_tail.add_argument("--event-type", default=None)
    agent_events_tail.add_argument("--payload-type", default=None)

    agent_response = sub.add_parser("agent-response-text", help="Read Agent response text.")
    agent_response.add_argument("agent_id")
    agent_response.add_argument("--latest", action="store_true")
    agent_response.add_argument("--turn-id", default=None)

    agent_tool_calls = sub.add_parser("agent-tool-calls", help="List parsed Agent tool calls.")
    agent_tool_calls.add_argument("agent_id")
    agent_tool_calls.add_argument("--latest", action="store_true")
    agent_tool_calls.add_argument("--turn-id", default=None)

    agent_tool_call = sub.add_parser("agent-tool-call", help="Read one parsed Agent tool call.")
    agent_tool_call.add_argument("agent_id")
    agent_tool_call.add_argument("--last", action="store_true")
    agent_tool_call.add_argument("--call-id", default=None)
    agent_tool_call.add_argument("--index", type=int, default=None)

    agent_trace_report = sub.add_parser("agent-trace-report", help="Build or export an Agent trace report.")
    agent_trace_report.add_argument("agent_id")
    agent_trace_report.add_argument("--artifact-path", type=Path, default=None)
    agent_trace_report.add_argument("--out", type=Path, default=None)
    agent_trace_report.add_argument("--format", choices=["json", "markdown"], default="json")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    config = load_app_config(args.config)
    if args.command == "config-view":
        print(config.redacted_view().model_dump_json(indent=2))
        return 0

    admin_base_url = (args.admin_base_url or config.admin_http_effective_base_url()).rstrip("/")
    if args.command == "serve":
        bind_host = args.host or config.admin_http_host
        bind_port = config.admin_http_port if args.port is None else args.port
        config.admin_http_host = bind_host
        config.admin_http_port = bind_port
        if args.mcp_base_url:
            config.mcp_http_base_url = args.mcp_base_url
        print(
            json.dumps(
                {
                    "command": "serve",
                    "bind_host": bind_host,
                    "bind_port": bind_port,
                    "admin_base_url": config.admin_http_effective_base_url(),
                    "mcp_base_url": config.mcp_http_effective_base_url(),
                    "view_keys": args.view_key or "all",
                    "config": config.redacted_view().model_dump(mode="json"),
                },
                indent=2,
                sort_keys=True,
            ),
            flush=True,
        )
        anyio.run(
            lambda: run_production_app_server(
                config,
                view_keys=args.view_key,
                log_level=args.log_level,
            )
        )
        return 0

    if args.command == "status":
        return _print_http_result(_request_json("GET", f"{admin_base_url}/admin/runtime/status"))
    if args.command == "pause":
        return _print_http_result(_request_json("POST", f"{admin_base_url}/admin/runtime/pause", {"scope_id": args.scope_id}))
    if args.command == "resume":
        return _print_http_result(_request_json("POST", f"{admin_base_url}/admin/runtime/resume", {"scope_id": args.scope_id}))
    if args.command == "start-flow":
        return _print_http_result(
            _request_json(
                "POST",
                f"{admin_base_url}/admin/flows/start",
                {
                    "flow_type": args.flow_type,
                    "scope_id": args.scope_id,
                    "params": _parse_params(args.param),
                    "enqueue": not args.no_enqueue,
                },
            )
        )
    if args.command == "snapshot":
        return _print_http_result(
            _request_json(
                "POST",
                f"{admin_base_url}/admin/snapshots/create",
                {
                    "repo_root": str(args.repo_root),
                    "checkpoint_kind": args.kind,
                    "label": args.label,
                },
            )
        )
    if args.command == "agent-rollout-info":
        return _print_http_result(_request_json("GET", f"{admin_base_url}/admin/agents/{args.agent_id}/rollout"))
    if args.command == "agent-turns":
        return _print_http_result(_request_json("GET", f"{admin_base_url}/admin/agents/{args.agent_id}/turns"))
    if args.command == "agent-turn":
        return _print_http_result(
            _request_json(
                "GET",
                _url_with_query(
                    f"{admin_base_url}/admin/agents/{args.agent_id}/turn",
                    {
                        "turn_id": args.turn_id,
                        "index": args.index,
                        "latest": args.latest or (args.turn_id is None and args.index is None),
                    },
                ),
            )
        )
    if args.command == "agent-event":
        return _print_http_result(
            _request_json(
                "GET",
                _url_with_query(
                    f"{admin_base_url}/admin/agents/{args.agent_id}/event",
                    {"index": args.index, "last": args.last or args.index is None},
                ),
            )
        )
    if args.command == "agent-events-tail":
        return _print_http_result(
            _request_json(
                "GET",
                _url_with_query(
                    f"{admin_base_url}/admin/agents/{args.agent_id}/events/tail",
                    {
                        "limit": args.limit,
                        "event_type": args.event_type,
                        "payload_type": args.payload_type,
                    },
                ),
            )
        )
    if args.command == "agent-response-text":
        if args.latest or args.turn_id is None:
            return _print_http_result(_request_json("GET", f"{admin_base_url}/admin/agents/{args.agent_id}/latest-response"))
        return _print_http_result(
            _request_json(
                "GET",
                _url_with_query(
                    f"{admin_base_url}/admin/agents/{args.agent_id}/responses",
                    {"turn_id": args.turn_id},
                ),
            )
        )
    if args.command == "agent-tool-calls":
        return _print_http_result(
            _request_json(
                "GET",
                _url_with_query(
                    f"{admin_base_url}/admin/agents/{args.agent_id}/tool-calls",
                    {"turn_id": args.turn_id, "latest": args.latest},
                ),
            )
        )
    if args.command == "agent-tool-call":
        return _print_http_result(
            _request_json(
                "GET",
                _url_with_query(
                    f"{admin_base_url}/admin/agents/{args.agent_id}/tool-call",
                    {
                        "call_id": args.call_id,
                        "index": args.index,
                        "last": args.last or (args.call_id is None and args.index is None),
                    },
                ),
            )
        )
    if args.command == "agent-trace-report":
        return _print_http_result(
            _request_json(
                "GET",
                _url_with_query(
                    f"{admin_base_url}/admin/agents/{args.agent_id}/trace-report",
                    {
                        "artifact_path": str(args.artifact_path) if args.artifact_path is not None else None,
                        "output_path": str(args.out) if args.out is not None else None,
                        "format": args.format,
                    },
                ),
            )
        )

    runtime = create_app_runtime_from_config(config)
    admin = LeanAdminApi(runtime)
    if args.command == "external-list":
        return _print_result(
            admin.list_external_takeovers(handoff_dirname=args.handoff_dirname, status=args.status)
        )
    if args.command == "external-complete":
        return _print_result(
            admin.complete_external_takeover(
                ExternalTakeoverCompleteInput(
                    handoff_id=args.handoff_id,
                    status=args.status,
                    final_response=args.final_response,
                    error=args.error,
                    thread_id=args.thread_id,
                    turn_id=args.turn_id,
                    rollout_relpath=args.rollout_relpath,
                    metadata=_parse_params(args.metadata),
                    handoff_dirname=args.handoff_dirname,
                )
            )
        )
    if args.command == "external-tools":
        return _print_result(
            admin.list_external_takeover_tools(
                ExternalTakeoverToolListInput(
                    handoff_id=args.handoff_id,
                    view_kind=args.view_kind,
                    handoff_dirname=args.handoff_dirname,
                )
            )
        )
    if args.command == "external-call":
        return _print_result(
            admin.call_external_takeover_tool(
                ExternalTakeoverToolCallInput(
                    handoff_id=args.handoff_id,
                    view_kind=args.view_kind,
                    tool_name=args.tool_name,
                    arguments=_parse_params(args.arg),
                    handoff_dirname=args.handoff_dirname,
                )
            )
        )
    parser.error(f"unknown command: {args.command}")
    return 2


def _parse_params(values: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"param must use key=value format: {item}")
        key, value = item.split("=", 1)
        params[key] = value
    return params


def _print_result(result) -> int:
    if not result.ok:
        print(result.model_dump_json(indent=2))
        return 1
    value = result.value
    if hasattr(value, "model_dump_json"):
        print(value.model_dump_json(indent=2))
    else:
        print(json.dumps(_jsonable(value), indent=2))
    return 0


def _request_json(method: str, url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=30) as response:  # noqa: S310 - local admin endpoint by configuration.
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
    except error.URLError as exc:
        return {
            "ok": False,
            "value": None,
            "issues": [
                {
                    "kind": "admin_http_request_failed",
                    "message": f"Failed to reach Admin HTTP server at {url}: {exc.reason}",
                    "severity": "error",
                }
            ],
        }
    if not raw:
        return {
            "ok": False,
            "value": None,
            "issues": [{"kind": "empty_admin_response", "message": "Admin server returned an empty response.", "severity": "error"}],
        }
    try:
        return json.loads(raw)
    except JSONDecodeError as exc:
        return {
            "ok": False,
            "value": None,
            "issues": [
                {
                    "kind": "admin_http_invalid_json",
                    "message": f"Admin server returned invalid JSON: {exc}",
                    "severity": "error",
                }
            ],
        }


def _print_http_result(payload: dict) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok") is True else 1


def _url_with_query(url: str, params: dict[str, object | None]) -> str:
    clean = {key: value for key, value in params.items() if value is not None and value is not False}
    if not clean:
        return url
    return f"{url}?{urlencode(clean)}"


def _jsonable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
