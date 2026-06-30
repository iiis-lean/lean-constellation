"""Minimal local CLI wrapper for Lean Constellation Admin API."""

from __future__ import annotations

import argparse
from pathlib import Path

from lean_constellation.app.admin_api import LeanAdminApi, SnapshotCreateInput, StartFlowInput
from lean_constellation.app.config import load_app_config
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
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
