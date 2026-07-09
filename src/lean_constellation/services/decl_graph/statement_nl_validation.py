"""Deterministic validation for Statement NL stage candidates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lean_constellation.services.decl_graph.models import DeclState
from lean_constellation.services.foundation import ServiceResult


def validate_statement_nl_candidate(
    runtime: Any,
    repo_root: Path,
    *,
    node_path: str,
    round_id: str,
    decl_name: str,
) -> ServiceResult[None]:
    graph = runtime.decl_graph
    decl = graph.get_decl(repo_root, node_path=node_path, name=decl_name)
    if not decl.ok or decl.value is None:
        return runtime.foundation.fail(decl.issues)
    revision = graph.get_decl_revision(repo_root, node_path=node_path, name=decl_name, revision=decl.value.current_revision)
    if not revision.ok or revision.value is None:
        return runtime.foundation.fail(revision.issues)
    if not revision.value.statement_nl or not revision.value.statement_nl.strip():
        return runtime.foundation.fail(runtime.foundation.issue("statement_nl_candidate_missing", "Statement NL candidate is missing.", object_ref=decl_name))

    round_record = graph.get_round(repo_root, node_path=node_path, round_id=round_id)
    if not round_record.ok or round_record.value is None:
        return runtime.foundation.fail(round_record.issues)
    round_ref_names = {ref.decl_name for ref in round_record.value.revision_refs}

    issues = []
    origins = list(revision.value.statement.nl.origin) if revision.value.statement.nl is not None else []
    for origin in origins:
        issue = _validate_statement_origin(runtime, repo_root, origin, decl_name)
        if issue is not None:
            issues.append(issue)

    dep_validation = validate_statement_deps(runtime, repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, deps=revision.value.statement.deps)
    if not dep_validation.ok:
        issues.extend(dep_validation.issues)

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
    deps: list[object],
) -> ServiceResult[None]:
    round_ref_names: set[str] = set()
    if round_id:
        round_record = runtime.decl_graph.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return runtime.foundation.fail(round_record.issues)
        round_ref_names = {ref.decl_name for ref in round_record.value.revision_refs}

    issues = []
    for dep in deps:
        issue = _validate_statement_dep(runtime, repo_root, node_path, round_ref_names, dep, decl_name)
        if issue is not None:
            issues.append(issue)
    if issues:
        return runtime.foundation.fail(issues)
    return runtime.foundation.ok(None)


def _validate_statement_origin(runtime: Any, repo_root: Path, origin: object, decl_name: str):
    kind, ref = _origin_kind_ref(origin)
    if kind == "source":
        if ref is None:
            return runtime.foundation.issue("statement_origin_invalid", "Statement NL source origin must include a stable source path or ref.", object_ref=decl_name)
        index = runtime.material.get_committed_source_index(repo_root)
        if not index.ok or index.value is None:
            return runtime.foundation.issue("statement_origin_source_index_missing", "Statement NL source origin requires a committed SourceIndex.", object_ref=decl_name)
        if not _source_origin_ref_exists(index.value, ref):
            return runtime.foundation.issue("statement_origin_source_missing", f"Statement NL source origin does not match committed SourceIndex: {ref}.", object_ref=decl_name)
        return None
    if kind == "resource":
        if ref is None:
            return runtime.foundation.issue("statement_origin_invalid", "Statement NL resource origin must include a stable resource key.", object_ref=decl_name)
        resource_key = ref.split("#", 1)[0].split(":", 1)[-1].strip()
        resource = _get_resource(runtime, repo_root, resource_key)
        if not resource.ok:
            return runtime.foundation.issue("statement_origin_resource_missing", f"Statement NL resource origin does not match an active resource: {ref}.", object_ref=decl_name)
        return None
    return runtime.foundation.issue("statement_origin_kind_invalid", f"Statement NL origin kind is not a stable source/resource origin: {kind}.", object_ref=decl_name)


def _validate_statement_dep(runtime: Any, repo_root: Path, node_path: str, round_ref_names: set[str], dep: object, decl_name: str):
    kind = _dep_kind(dep)
    dep_name = _dep_name(dep)
    if not dep_name:
        return runtime.foundation.issue("statement_dep_invalid", "Statement NL dependency is missing a declaration name.", object_ref=decl_name)
    if kind == "mathlib_decl":
        entry = runtime.mathlib.get_mathlib_decl_entry(repo_root, name=dep_name)
        if not entry.ok:
            return runtime.foundation.issue("statement_mathlib_dep_not_recorded", f"Statement Mathlib dependency is not recorded in MathlibIndex: {dep_name}.", object_ref=decl_name)
        return None
    if kind != "repo_decl":
        return runtime.foundation.issue("statement_dep_kind_invalid", f"Statement NL dependency has unsupported dependency kind: {kind}.", object_ref=decl_name)

    dep_node = _dep_node(dep) or node_path
    dep_repo = _dep_repo(dep)
    if dep_repo:
        repo_key = runtime.foundation.layout.ensure_safe_key(dep_repo)
        public = runtime.node.public_decl_access.list_repo_public_decls(repo_root, repo_key=repo_key, actor_role="worker", current_node_path=node_path)
        if not public.ok or public.value is None:
            return runtime.foundation.issue("statement_dep_not_visible", f"Statement dependency provider repo is not visible: {repo_key}.", object_ref=dep_name)
        if not any(item.ref.name == dep_name for item in public.value):
            return runtime.foundation.issue("statement_dep_not_visible", f"Statement dependency is not visible on provider repo public interface: {dep_name}.", object_ref=dep_name)
        return None

    if dep_node not in {"", "Main", node_path}:
        public = runtime.node.public_decl_access.list_node_public_decls(repo_root, node_path=dep_node, actor_role="worker", current_node_path=node_path)
        if not public.ok or public.value is None:
            return runtime.foundation.issue("statement_dep_not_visible", f"Statement dependency provider node is not visible: {dep_node}.", object_ref=dep_name)
        if not any(item.ref.name == dep_name for item in public.value):
            return runtime.foundation.issue("statement_dep_not_visible", f"Statement dependency is not visible on provider node public interface: {dep_name}.", object_ref=dep_name)
        return None

    dep_view = runtime.decl_graph.current_decl_revision_view(repo_root, node_path=node_path, name=dep_name)
    if not dep_view.ok or dep_view.value is None:
        return runtime.foundation.issue("statement_dep_not_visible", f"Statement NL dependency is not a visible current declaration: {dep_name}.", object_ref=dep_name)
    dep_state = DeclState(dep_view.value.state)
    if dep_name in round_ref_names and not _state_reaches(dep_state, DeclState.DECLARED):
        return runtime.foundation.issue("statement_dep_same_round_not_declared", f"Statement NL dependency points to same-round declaration that is not accepted declared state: {dep_name}.", object_ref=dep_name)
    return None


def _get_resource(runtime: Any, repo_root: Path, resource_key: str):
    if hasattr(runtime.material, "get_resource"):
        return runtime.material.get_resource(repo_root, resource_key=resource_key)
    return runtime.material.resource_library.get_resource(repo_root, resource_key=resource_key)


def statement_nl_validation_message(runtime: Any, repo_root: Path, *, node_path: str, round_id: str, decl_name: str) -> str | None:
    result = validate_statement_nl_candidate(runtime, repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
    if result.ok:
        return None
    if result.issues:
        return str(getattr(result.issues[0], "message", None) or getattr(result.issues[0], "summary", None) or "Statement NL candidate failed validation.")
    return "Statement NL candidate failed validation."


def _origin_kind_ref(origin: object) -> tuple[str, str | None]:
    if isinstance(origin, dict):
        kind = str(origin.get("kind", "")).strip()
        if kind == "source":
            raw_ref = origin.get("ref") or origin.get("source_path")
        elif kind == "resource":
            raw_ref = origin.get("ref") or origin.get("resource_key")
        else:
            raw_ref = origin.get("ref")
    else:
        kind = str(getattr(origin, "kind", "")).strip()
        if kind == "source":
            raw_ref = getattr(origin, "ref", None) or getattr(origin, "source_path", None)
        elif kind == "resource":
            raw_ref = getattr(origin, "ref", None) or getattr(origin, "resource_key", None)
        else:
            raw_ref = getattr(origin, "ref", None)
    ref = str(raw_ref).strip() if raw_ref is not None else None
    return kind, ref or None


def _source_origin_ref_exists(index, ref: str) -> bool:
    if ref in getattr(index, "blocks", {}):
        return True
    if ref in getattr(index, "files", {}):
        return True
    ref_path = ref.split("#", 1)[0].split(":", 1)[0]
    if ref_path in getattr(index, "files", {}):
        return True
    for block in getattr(index, "blocks", {}).values():
        for block_ref in getattr(block, "refs", []):
            if ref == getattr(block_ref, "ref_id", None):
                return True
            if ref_path == getattr(block_ref, "path", None):
                return True
    return False


def _dep_kind(dep_item: object) -> str:
    if isinstance(dep_item, str):
        return "repo_decl"
    if isinstance(dep_item, dict):
        return str(dep_item.get("kind", "repo_decl")).strip() or "repo_decl"
    return str(getattr(dep_item, "kind", "repo_decl")).strip() or "repo_decl"


def _dep_name(dep_item: object) -> str | None:
    if isinstance(dep_item, str):
        return dep_item.strip() or None
    ref = dep_item.get("ref") if isinstance(dep_item, dict) else getattr(dep_item, "ref", None)
    if isinstance(ref, dict):
        raw_name = ref.get("name")
    elif ref is not None:
        raw_name = getattr(ref, "name", None)
    else:
        raw_name = dep_item.get("name") if isinstance(dep_item, dict) else getattr(dep_item, "name", None)
    return str(raw_name).strip() if raw_name is not None and str(raw_name).strip() else None


def _dep_node(dep_item: object) -> str | None:
    if isinstance(dep_item, str):
        return None
    ref = dep_item.get("ref") if isinstance(dep_item, dict) else getattr(dep_item, "ref", None)
    raw_node = ref.get("node") if isinstance(ref, dict) else getattr(ref, "node", None)
    return str(raw_node).strip() if raw_node is not None and str(raw_node).strip() else None


def _dep_repo(dep_item: object) -> str | None:
    if isinstance(dep_item, str):
        return None
    ref = dep_item.get("ref") if isinstance(dep_item, dict) else getattr(dep_item, "ref", None)
    raw_repo = ref.get("repo") if isinstance(ref, dict) else getattr(ref, "repo", None)
    return str(raw_repo).strip() if raw_repo is not None and str(raw_repo).strip() else None


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
