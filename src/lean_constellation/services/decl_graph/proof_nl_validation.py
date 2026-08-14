"""Deterministic validation for Proof NL stage candidates."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from time import perf_counter
from typing import Any

from lean_constellation.services.decl_graph.availability_policy import is_theorem_like
from lean_constellation.services.decl_graph.models import (
    DeclDep,
    DeclOriginRef,
    DeclRevision,
    DeclState,
    MathlibDeclDep,
    RepoDeclDep,
)
from lean_constellation.services.decl_graph.origin_validation import validate_nl_origin
from lean_constellation.services.foundation import ServiceResult


def validate_proof_nl_candidate(
    runtime: Any,
    repo_root: Path,
    *,
    node_path: str,
    round_id: str,
    decl_name: str,
    timing_sink: dict[str, float] | None = None,
    visibility_cache: dict[tuple[str, str], object] | None = None,
) -> ServiceResult[None]:
    loaded = _round_decl_and_revision(
        runtime,
        repo_root,
        node_path=node_path,
        round_id=round_id,
        decl_name=decl_name,
    )
    if not loaded.ok or loaded.value is None:
        return runtime.foundation.fail(loaded.issues)
    decl, revision = loaded.value
    issues = []
    if not is_theorem_like(decl.kind):
        issues.append(
            runtime.foundation.issue(
                "proof_nl_target_not_theorem_like",
                "Proof NL candidates are only valid for theorem-like declarations.",
                object_ref=decl_name,
            )
        )
    if revision.statement.formal is None or not (revision.statement.formal.code or "").strip():
        issues.append(
            runtime.foundation.issue(
                "proof_nl_statement_formal_missing",
                "Accepted statement formal code is required before proof planning.",
                object_ref=decl_name,
            )
        )
    if revision.proof is None or revision.proof.nl is None or not (revision.proof.nl.text or "").strip():
        issues.append(
            runtime.foundation.issue(
                "proof_nl_candidate_missing",
                "Proof NL candidate is missing.",
                object_ref=decl_name,
            )
        )
    if revision.proof is not None and revision.proof.nl is not None:
        origin_started = perf_counter()
        for origin in revision.proof.nl.origin:
            origin_validation = validate_proof_origin_ref(
                runtime,
                repo_root,
                origin=origin,
                decl_name=decl_name,
            )
            if not origin_validation.ok:
                issues.extend(origin_validation.issues)
        _record_timing(timing_sink, "nl_origin", origin_started)
    dependencies_started = perf_counter()
    dependencies = validate_proof_deps(
        runtime,
        repo_root,
        node_path=node_path,
        round_id=round_id,
        decl_name=decl_name,
        deps=revision.proof.deps if revision.proof is not None else [],
        visibility_cache=visibility_cache,
    )
    _record_timing(timing_sink, "dependency_visibility_readiness", dependencies_started)
    if not dependencies.ok:
        issues.extend(dependencies.issues)
    if issues:
        return runtime.foundation.fail(issues)
    return runtime.foundation.ok(None)


def validate_proof_deps(
    runtime: Any,
    repo_root: Path,
    *,
    node_path: str,
    round_id: str | None,
    decl_name: str,
    deps: Sequence[DeclDep],
    visible_dependencies: set[tuple[str, str, str]] | None = None,
    visibility_cache: dict[tuple[str, str], object] | None = None,
) -> ServiceResult[None]:
    round_refs = _round_refs(runtime, repo_root, node_path=node_path, round_id=round_id)
    if not round_refs.ok or round_refs.value is None:
        return runtime.foundation.fail(round_refs.issues)
    issues = []
    for dep in deps:
        issue = _validate_proof_dep(
            runtime,
            repo_root,
            node_path,
            round_refs.value,
            dep,
            decl_name,
            visible_dependencies,
            visibility_cache,
        )
        if issue is not None:
            issues.append(issue)
    if issues:
        return runtime.foundation.fail(issues)
    return runtime.foundation.ok(None)


def validate_proof_origin_ref(
    runtime: Any,
    repo_root: Path,
    *,
    origin: DeclOriginRef,
    decl_name: str,
) -> ServiceResult[None]:
    issue = _validate_proof_origin(runtime, repo_root, origin, decl_name)
    if issue is not None:
        return runtime.foundation.fail(issue)
    return runtime.foundation.ok(None)


def proof_nl_validation_message(
    runtime: Any,
    repo_root: Path,
    *,
    node_path: str,
    round_id: str,
    decl_name: str,
) -> str | None:
    result = validate_proof_nl_candidate(
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
            or "Proof NL candidate failed validation."
        )
    return "Proof NL candidate failed validation."


def _validate_proof_origin(
    runtime: Any,
    repo_root: Path,
    origin: DeclOriginRef,
    decl_name: str,
):
    return validate_nl_origin(
        runtime,
        repo_root,
        origin=origin,
        decl_name=decl_name,
        stage="proof",
    )


def _validate_proof_dep(
    runtime: Any,
    repo_root: Path,
    node_path: str,
    round_refs: dict[str, int],
    dep: DeclDep,
    decl_name: str,
    visible_dependencies: set[tuple[str, str, str]] | None = None,
    visibility_cache: dict[tuple[str, str], object] | None = None,
):
    if isinstance(dep, MathlibDeclDep):
        entry = runtime.mathlib.get_mathlib_decl_entry(repo_root, name=dep.ref.name)
        if not entry.ok or entry.value is None:
            return runtime.foundation.issue(
                "proof_mathlib_dep_not_recorded",
                f"Proof Mathlib dependency is not recorded in MathlibIndex: {dep.ref.name}.",
                object_ref=decl_name,
            )
        if not dep.ref.module:
            return runtime.foundation.issue(
                "proof_mathlib_dep_module_missing",
                f"Proof Mathlib dependency must include a module or be filled from MathlibIndex: {dep.ref.name}.",
                object_ref=decl_name,
            )
        if entry.value.module and dep.ref.module != entry.value.module:
            return runtime.foundation.issue(
                "proof_mathlib_dep_module_mismatch",
                f"Proof Mathlib dependency module does not match MathlibIndex for {dep.ref.name}.",
                object_ref=decl_name,
                current=dep.ref.module,
                expected=entry.value.module,
            )
        return None
    assert isinstance(dep, RepoDeclDep)
    dep_name = dep.ref.name
    dep_node = _effective_node(dep.ref.node, node_path=node_path)
    if dep.ref.repo:
        repo_key = runtime.foundation.layout.ensure_safe_key(dep.ref.repo)
        if visible_dependencies is not None:
            if ("repo", repo_key, dep_name) not in visible_dependencies:
                return runtime.foundation.issue(
                    "proof_dep_not_visible",
                    f"Proof dependency is not visible on provider repo public interface: {dep_name}.",
                    object_ref=dep_name,
                )
            return None
        cache_key = ("repo", repo_key)
        public = visibility_cache.get(cache_key) if visibility_cache is not None else None
        if public is None:
            public = runtime.node.public_decl_access.list_repo_public_decls(
                repo_root,
                repo_key=repo_key,
                actor_role="worker",
                current_node_path=node_path,
            )
            if visibility_cache is not None:
                visibility_cache[cache_key] = public
        if not public.ok or public.value is None or not any(item.ref.name == dep_name for item in public.value):
            return runtime.foundation.issue(
                "proof_dep_not_visible",
                f"Proof dependency is not visible on provider repo public interface: {dep_name}.",
                object_ref=dep_name,
            )
        return None
    if dep_node != node_path:
        if visible_dependencies is not None:
            if ("node", dep_node, dep_name) not in visible_dependencies:
                return runtime.foundation.issue(
                    "proof_dep_not_visible",
                    f"Proof dependency is not visible on provider node public interface: {dep_name}.",
                    object_ref=dep_name,
                )
            return None
        cache_key = ("node", dep_node)
        public = visibility_cache.get(cache_key) if visibility_cache is not None else None
        if public is None:
            public = runtime.node.public_decl_access.list_node_public_decls(
                repo_root,
                node_path=dep_node,
                actor_role="worker",
                stable_boundary=True,
                current_node_path=node_path,
            )
            if visibility_cache is not None:
                visibility_cache[cache_key] = public
        if not public.ok or public.value is None or not any(item.ref.name == dep_name for item in public.value):
            return runtime.foundation.issue(
                "proof_dep_not_visible",
                f"Proof dependency is not visible on provider node public interface: {dep_name}.",
                object_ref=dep_name,
            )
        return None
    revision_number = round_refs.get(dep_name, dep.ref.revision)
    dep_decl = runtime.decl_graph.get_decl(repo_root, node_path=node_path, name=dep_name)
    revision = runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=node_path,
        name=dep_name,
        revision=revision_number,
    )
    if not dep_decl.ok or dep_decl.value is None or not revision.ok or revision.value is None:
        return runtime.foundation.issue(
            "proof_dep_not_visible",
            f"Proof dependency is not a visible declaration revision: {dep_name}.",
            object_ref=dep_name,
        )
    theorem_like = is_theorem_like(dep_decl.value.kind)
    required_state = DeclState.PROOF_PLANNED if theorem_like else DeclState.DECLARED
    if not _state_reaches(revision.value.state, required_state):
        code = "proof_dep_same_round_not_proved" if dep_name in round_refs else "proof_dep_not_ready"
        return runtime.foundation.issue(
            code,
            f"Proof dependency has not reached its accepted dependency state: {dep_name}.",
            object_ref=dep_name,
            current=revision.value.state.value,
            expected=required_state.value,
        )
    return None


def _round_decl_and_revision(
    runtime: Any,
    repo_root: Path,
    *,
    node_path: str,
    round_id: str,
    decl_name: str,
) -> ServiceResult[tuple[Any, DeclRevision]]:
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
    decl = runtime.decl_graph.get_decl(repo_root, node_path=node_path, name=decl_name)
    if not decl.ok or decl.value is None:
        return runtime.foundation.fail(decl.issues)
    revision = runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=node_path,
        name=decl_name,
        revision=revision_number,
    )
    if not revision.ok or revision.value is None:
        return runtime.foundation.fail(revision.issues)
    return runtime.foundation.ok((decl.value, revision.value))


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


def _record_timing(sink: dict[str, float] | None, key: str, started: float) -> None:
    if sink is not None:
        sink[key] = sink.get(key, 0.0) + round((perf_counter() - started) * 1000, 3)


def _effective_node(dep_node: str, *, node_path: str) -> str:
    return node_path if dep_node in {"", "Main"} and node_path != "Main" else dep_node


def _state_rank(state: DeclState) -> int:
    return {
        DeclState.PLANNED: 0,
        DeclState.SPECIFIED: 1,
        DeclState.DECLARED: 2,
        DeclState.PROOF_PLANNED: 3,
        DeclState.PROVED: 4,
    }[DeclState(state)]


def _state_reaches(current: DeclState, target: DeclState) -> bool:
    return _state_rank(current) >= _state_rank(target)
