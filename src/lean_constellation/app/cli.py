"""Minimal local CLI wrapper for Lean Constellation Admin API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lean_constellation.app.admin_api import LeanAdminApi, SnapshotCreateInput, StartFlowInput
from lean_constellation.app.config import load_app_config
from lean_constellation.app.external_takeover import (
    ExternalTakeoverCompleteInput,
    ExternalTakeoverToolCallInput,
    ExternalTakeoverToolListInput,
)
from lean_constellation.app.runtime import create_app_runtime_from_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lean-constellation", description="Lean Constellation admin CLI")
    parser.add_argument("--config", type=Path, default=None, help="Path to JSON/TOML app config.")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("config-view", help="Print redacted config view.")

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

    runtime = create_app_runtime_from_config(config)
    admin = LeanAdminApi(runtime)
    if args.command == "start-flow":
        result = admin.start_arbitrary_flow(
            StartFlowInput(
                flow_type=args.flow_type,
                scope_id=args.scope_id,
                params=_parse_params(args.param),
                enqueue=not args.no_enqueue,
            )
        )
        return _print_result(result)
    if args.command == "snapshot":
        result = admin.create_snapshot(
            SnapshotCreateInput(repo_root=args.repo_root, checkpoint_kind=args.kind, label=args.label)
        )
        return _print_result(result)
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
    elif isinstance(value, list):
        print(json.dumps([_jsonable(item) for item in value], indent=2))
    elif isinstance(value, dict):
        print(json.dumps(value, indent=2))
    else:
        print(value)
    return 0


def _jsonable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
