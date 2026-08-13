"""Shared deterministic validation for natural-language origin references."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from lean_constellation.services.decl_graph.models import DeclOriginRef


def validate_nl_origin(
    runtime: Any,
    repo_root: Path,
    *,
    origin: DeclOriginRef,
    decl_name: str,
    stage: Literal["statement", "proof"],
):
    """Return one stage-specific issue, or ``None`` for a valid origin."""

    if origin.kind == "source":
        if origin.source_path is None or origin.start_line is None or origin.end_line is None:
            return runtime.foundation.issue(
                f"{stage}_origin_invalid",
                f"{stage.title()} NL source origin requires a source path and exact line range.",
                object_ref=decl_name,
            )
        validation = runtime.material.validate_source_range(
            repo_root,
            path=origin.source_path,
            start_line=origin.start_line,
            end_line=origin.end_line,
        )
        if not validation.ok or validation.value is None:
            return runtime.foundation.issue(
                f"{stage}_origin_source_missing",
                f"{stage.title()} NL source origin could not be validated against SourceCorpus truth.",
                object_ref=decl_name,
                details={
                    "source_path": origin.source_path,
                    "validation_issues": [issue.kind for issue in validation.issues],
                },
            )
        if not validation.value.valid:
            return runtime.foundation.issue(
                f"{stage}_origin_source_missing",
                validation.value.summary,
                object_ref=decl_name,
                details={
                    "source_path": origin.source_path,
                    "start_line": origin.start_line,
                    "end_line": origin.end_line,
                    "source_issue_code": validation.value.issue_code,
                },
            )
        return None

    if origin.kind == "resource":
        ref = origin.resource_key or origin.ref
        if ref is None:
            return runtime.foundation.issue(
                f"{stage}_origin_invalid",
                f"{stage.title()} NL resource origin must include a stable resource key.",
                object_ref=decl_name,
            )
        resource_key = ref.split("#", 1)[0].split(":", 1)[-1].strip()
        resource = (
            runtime.material.get_resource(repo_root, resource_key=resource_key)
            if hasattr(runtime.material, "get_resource")
            else runtime.material.resource_library.get_resource(repo_root, resource_key=resource_key)
        )
        if not resource.ok:
            return runtime.foundation.issue(
                f"{stage}_origin_resource_missing",
                f"{stage.title()} NL resource origin does not match an active resource: {ref}.",
                object_ref=decl_name,
            )
        return None

    return runtime.foundation.issue(
        f"{stage}_origin_kind_invalid",
        f"{stage.title()} NL origin kind is not a stable source/resource origin: {origin.kind}.",
        object_ref=decl_name,
    )
