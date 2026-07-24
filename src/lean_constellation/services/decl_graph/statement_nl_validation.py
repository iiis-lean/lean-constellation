"""Deterministic validation for Statement NL stage candidates."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from lean_constellation.services.decl_graph.models import (
    DeclDep,
    DeclOriginRef,
    DeclRevision,
    DeclState,
    MathlibDeclDep,
    RepoDeclDep,
)
from lean_constellation.services.foundation import ServiceResult


def validate_statement_nl_candidate(
    runtime: Any,
    repo_root: Path,
    *,
    node_path: str,
    round_id: str,
    decl_name: str,
) -> ServiceResult[None]:
    loaded = _round_revision(runtime, repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
    if not loaded.ok or loaded.value is None:
        return runtime.foundation.fail(loaded.issues)
    revision = loaded.value
    if revision.statement.nl is None or not (revision.statement.nl.text or "").strip():
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "statement_nl_candidate_missing",
                "Statement NL candidate is missing.",
                object_ref=decl_name,
            )
        )
    issues = []
    for origin in revision.statement.nl.origin:
        issue = _validate_statement_origin(runtime, repo_root, origin, decl_name)
        if issue is not None:
            issues.append(issue)
    dependencies = validate_statement_deps(
        runtime,
        repo_root,
        node_path=node_path,
        round_id=round_id,
        decl_name=decl_name,
        deps=revision.statement.deps,
    )
    if not dependencies.ok:
        issues.extend(dependencies.issues)
    if issues:
        return runtime.foundation.fail(issues)
    return runtime.foundation.ok(None)


def validate_statement_deps(
    runtime: Any,
    repo_root: Path,
    *,
    node_path: str,
    round_id: str | None,
    decl_name: str,
    deps: Sequence[DeclDep],
) -> ServiceResult[None]:
    round_refs = _round_refs(runtime, repo_root, node_path=node_path, round_id=round_id)
    if not round_refs.ok or round_refs.value is None:
        return runtime.foundation.fail(round_refs.issues)
    issues = []
    for dep in deps:
        issue = _validate_statement_dep(
            runtime,
            repo_root,
            node_path,
            round_refs.value,
            dep,
            decl_name,
        )
        if issue is not None:
            issues.append(issue)
    if issues:
        return runtime.foundation.fail(issues)
    return runtime.foundation.ok(None)


def _validate_statement_origin(
    runtime: Any,
    repo_root: Path,
    origin: DeclOriginRef,
    decl_name: str,
):
    ref = origin.ref or origin.source_path or origin.resource_key
    if origin.kind == "source":
        if ref is None:
            return runtime.foundation.issue(
                "statement_origin_invalid",
                "Statement NL source origin must include a stable source path or ref.",
                object_ref=decl_name,
            )
        index = runtime.material.get_committed_source_index(repo_root)
        if not index.ok or index.value is None:
            return runtime.foundation.issue(
                "statement_origin_source_index_missing",
                "Statement NL source origin requires a committed SourceIndex.",
                object_ref=decl_name,
            )
        if not _source_origin_ref_exists(index.value, ref):
            return runtime.foundation.issue(
                "statement_origin_source_missing",
                f"Statement NL source origin does not match committed SourceIndex: {ref}.",
                object_ref=decl_name,
            )
        return None
    if origin.kind == "resource":
        if ref is None:
            return runtime.foundation.issue(
                "statement_origin_invalid",
                "Statement NL resource origin must include a stable resource key.",
                object_ref=decl_name,
            )
        resource_key = ref.split("#", 1)[0].split(":", 1)[-1].strip()
        resource = _get_resource(runtime, repo_root, resource_key)
        if not resource.ok:
            return runtime.foundation.issue(
                "statement_origin_resource_missing",
                f"Statement NL resource origin does not match an active resource: {ref}.",
                object_ref=decl_name,
            )
        return None
    return runtime.foundation.issue(
        "statement_origin_kind_invalid",
        f"Statement NL origin kind is not a stable source/resource origin: {origin.kind}.",
        object_ref=decl_name,
    )


def _validate_statement_dep(
    runtime: Any,
    repo_root: Path,
    node_path: str,
    round_refs: dict[str, int],
    dep: DeclDep,
    decl_name: str,
):
    if isinstance(dep, MathlibDeclDep):
        entry = runtime.mathlib.get_mathlib_decl_entry(repo_root, name=dep.ref.name)
        if not entry.ok:
            return runtime.foundation.issue(
                "statement_mathlib_dep_not_recorded",
                f"Statement Mathlib dependency is not recorded in MathlibIndex: {dep.ref.name}.",
                object_ref=decl_name,
            )
        return None
    assert isinstance(dep, RepoDeclDep)
    dep_name = dep.ref.name
    dep_node = _effective_node(dep.ref.node, node_path=node_path)
    if dep.ref.repo:
        repo_key = runtime.foundation.layout.ensure_safe_key(dep.ref.repo)
        public = runtime.node.public_decl_access.list_repo_public_decls(
            repo_root,
            repo_key=repo_key,
            actor_role="worker",
            current_node_path=node_path,
        )
        if not public.ok or public.value is None or not any(item.ref.name == dep_name for item in public.value):
            return runtime.foundation.issue(
                "statement_dep_not_visible",
                f"Statement dependency is not visible on provider repo public interface: {dep_name}.",
                object_ref=dep_name,
            )
        return None
    if dep_node != node_path:
        public = runtime.node.public_decl_access.list_node_public_decls(
            repo_root,
            node_path=dep_node,
            actor_role="worker",
            current_node_path=node_path,
        )
        if not public.ok or public.value is None or not any(item.ref.name == dep_name for item in public.value):
            return runtime.foundation.issue(
                "statement_dep_not_visible",
                f"Statement dependency is not visible on provider node public interface: {dep_name}.",
                object_ref=dep_name,
            )
        return None
    revision_number = round_refs.get(dep_name, dep.ref.revision)
    revision = runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=node_path,
        name=dep_name,
        revision=revision_number,
    )
    if not revision.ok or revision.value is None:
        return runtime.foundation.issue(
            "statement_dep_not_visible",
            f"Statement NL dependency is not a visible declaration revision: {dep_name}.",
            object_ref=dep_name,
        )
    if dep_name in round_refs and not _state_reaches(revision.value.state, DeclState.DECLARED):
        return runtime.foundation.issue(
            "statement_dep_same_round_not_declared",
            f"Statement NL dependency points to same-round declaration that is not accepted declared state: {dep_name}.",
            object_ref=dep_name,
            current=revision.value.state.value,
            expected=DeclState.DECLARED.value,
        )
    return None


def _round_revision(
    runtime: Any,
    repo_root: Path,
    *,
    node_path: str,
    round_id: str,
    decl_name: str,
) -> ServiceResult[DeclRevision]:
    refs = _round_refs(runtime, repo_root, node_path=node_path, round_id=round_id)
    if not refs.ok or refs.value is None:
        return runtime.foundation.fail(refs.issues)
    revision_number = refs.value.get(decl_name)
    if revision_number is None:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "decl_not_in_round",
                "Declaration is not part of the current round.",
                object_ref=decl_name,
            )
        )
    return runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=node_path,
        name=decl_name,
        revision=revision_number,
    )


def _round_refs(
    runtime: Any,
    repo_root: Path,
    *,
    node_path: str,
    round_id: str | None,
) -> ServiceResult[dict[str, int]]:
    if round_id is None:
        return runtime.foundation.ok({})
    round_record = runtime.decl_graph.get_round(repo_root, node_path=node_path, round_id=round_id)
    if not round_record.ok or round_record.value is None:
        return runtime.foundation.fail(round_record.issues)
    return runtime.foundation.ok({ref.decl_name: ref.revision for ref in round_record.value.revision_refs})


def _get_resource(runtime: Any, repo_root: Path, resource_key: str):
    if hasattr(runtime.material, "get_resource"):
        return runtime.material.get_resource(repo_root, resource_key=resource_key)
    return runtime.material.resource_library.get_resource(repo_root, resource_key=resource_key)


def statement_nl_validation_message(
    runtime: Any,
    repo_root: Path,
    *,
    node_path: str,
    round_id: str,
    decl_name: str,
) -> str | None:
    result = validate_statement_nl_candidate(
        runtime,
        repo_root,
        node_path=node_path,
        round_id=round_id,
        decl_name=decl_name,
    )
    if result.ok:
        return None
    if result.issues:
        return str(
            getattr(result.issues[0], "message", None)
            or getattr(result.issues[0], "summary", None)
            or "Statement NL candidate failed validation."
        )
    return "Statement NL candidate failed validation."


def _source_origin_ref_exists(index, ref: str) -> bool:
    if ref in getattr(index, "blocks", {}) or ref in getattr(index, "files", {}):
        return True
    ref_path = ref.split("#", 1)[0].split(":", 1)[0]
    if ref_path in getattr(index, "files", {}):
        return True
    return any(
        ref == getattr(block_ref, "ref_id", None) or ref_path == getattr(block_ref, "path", None)
        for block in getattr(index, "blocks", {}).values()
        for block_ref in getattr(block, "refs", [])
    )


def _effective_node(dep_node: str, *, node_path: str) -> str:
    return node_path if dep_node in {"", "Main"} and node_path != "Main" else dep_node


def _state_rank(state: DeclState) -> int:
    return {
        DeclState.OBSOLETE: -1,
        DeclState.PLANNED: 0,
        DeclState.SPECIFIED: 1,
        DeclState.DECLARED: 2,
        DeclState.PROOF_PLANNED: 3,
        DeclState.PROVED: 4,
    }[DeclState(state)]


def _state_reaches(current: DeclState, target: DeclState) -> bool:
    return _state_rank(current) >= _state_rank(target)
