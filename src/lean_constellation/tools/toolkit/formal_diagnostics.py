"""Agent-facing formal diagnostics helpers backed by LeanCheckComponent."""

from __future__ import annotations

from pathlib import Path

from lean_constellation.services.tool_facade import ToolCapability, ToolSpec
from lean_constellation.tools.args import FormalPolicyCheckArgs, RepoRelativeFileArgs
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
from lean_constellation.tools.specs import handler_tool


def _repo_file(runtime, ctx, file_path: str):
    try:
        rel = runtime.foundation.layout.ensure_relative_path(file_path)
        path = Path(ctx.repo_root) / rel
        runtime.foundation.layout.assert_within(ctx.repo_root, path)
    except ValueError as exc:
        return runtime.foundation.fail(runtime.foundation.issue("tool_file_path_rejected", str(exc), field="file_path"))
    return runtime.foundation.ok(path)


def _run_file_diagnostics(runtime, ctx, args: RepoRelativeFileArgs):
    path = _repo_file(runtime, ctx, args.file_path)
    if not path.ok or path.value is None:
        return path
    return runtime.lean_projection.lean_check.run_file_diagnostics(
        ctx.repo_root,
        file_path=path.value,
    )


def _scan_sorry_axiom(runtime, ctx, args: RepoRelativeFileArgs):
    resolved = _repo_file(runtime, ctx, args.file_path)
    if not resolved.ok or resolved.value is None:
        return resolved
    path = resolved.value
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return runtime.foundation.fail(runtime.foundation.issue("lean_file_read_failed", str(exc), field="file_path"))
    except UnicodeDecodeError as exc:
        return runtime.foundation.fail(runtime.foundation.issue("lean_file_not_utf8", str(exc), field="file_path"))
    return runtime.lean_projection.lean_check.detect_sorry_axiom(text)


def _check_statement_policy(runtime, ctx, args: FormalPolicyCheckArgs):
    if not args.decl_kind or not args.decl_kind.strip():
        return runtime.foundation.fail(runtime.foundation.issue("decl_kind_required", "Statement policy checks require decl_kind.", field="decl_kind"))
    path = _repo_file(runtime, ctx, args.file_path)
    if not path.ok or path.value is None:
        return path
    return runtime.lean_projection.lean_check.build_statement_lean_check(
        ctx.repo_root,
        file_path=path.value,
        decl_kind=args.decl_kind,
    )


def _check_proof_policy(runtime, ctx, args: RepoRelativeFileArgs):
    path = _repo_file(runtime, ctx, args.file_path)
    if not path.ok or path.value is None:
        return path
    return runtime.lean_projection.lean_check.build_proof_lean_check(
        ctx.repo_root,
        file_path=path.value,
    )


def build_tool_specs() -> list[ToolSpec]:
    roles = {"worker", "reviewer", "plan", "admin"}
    return [
        handler_tool(
            name="run_lean_file_diagnostics",
            description="Run Lean diagnostics for a repo-relative Lean file through the configured toolkit or Lake fallback without saving DeclGraph state.",
            args_model=RepoRelativeFileArgs,
            capability=ToolCapability.READ,
            result_view="lean_diagnostics",
            groups={AppGroup.LEAN_FILE_DIAGNOSTICS_READ},
            roles=roles,
            handler=_run_file_diagnostics,
        ),
        handler_tool(
            name="scan_lean_sorry_axiom",
            description="Scan a repo-relative Lean file for sorry, admit, axiom, opaque, and unsafe tokens outside comments and strings without invoking Lean.",
            args_model=RepoRelativeFileArgs,
            capability=ToolCapability.READ,
            result_view="sorry_axiom_scan",
            groups={AppGroup.LEAN_FILE_DIAGNOSTICS_READ},
            roles=roles,
            handler=_scan_sorry_axiom,
        ),
        handler_tool(
            name="check_statement_formal_policy",
            description="Run statement-formal diagnostics plus declaration-kind and sorry/axiom policy checks on a repo-relative Lean file without saving captured formal state.",
            args_model=FormalPolicyCheckArgs,
            capability=ToolCapability.READ,
            result_view="lean_check",
            groups={AppGroup.STATEMENT_FORMAL_POLICY_READ},
            roles=roles,
            handler=_check_statement_policy,
        ),
        handler_tool(
            name="check_proof_formal_policy",
            description="Run proof-formal diagnostics plus no-sorry/no-axiom safety policy checks on a repo-relative Lean file without saving captured proof state.",
            args_model=RepoRelativeFileArgs,
            capability=ToolCapability.READ,
            result_view="lean_check",
            groups={AppGroup.PROOF_FORMAL_POLICY_READ},
            roles=roles,
            handler=_check_proof_policy,
        ),
    ]
