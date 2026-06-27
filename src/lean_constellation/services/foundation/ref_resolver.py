"""Stable reference resolver registry."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation.result_error import (
    MutationSummaryView,
    ResultErrorComponent,
    ServiceIssue,
    ServiceResult,
)


class RefKind(StrEnum):
    SOURCE = "source"
    RESOURCE = "resource"
    NODE = "node"
    DECL = "decl"
    MATHLIB = "mathlib"


class RefResolveContext(StrictModel):
    repo_root: str
    current_repo: str | None = None
    current_node: str | None = None
    purpose: str | None = None


class ResolvedRef(StrictModel):
    kind: RefKind
    canonical_ref: str
    display_ref: str
    target_summary: str
    metadata: dict[str, str] = Field(default_factory=dict)


class RefValidationResult(StrictModel):
    resolved: ResolvedRef | None = None
    issues: list[ServiceIssue] = Field(default_factory=list)


class ResolvedRefView(StrictModel):
    ref: str
    summary: str
    kind: RefKind
    warnings: list[ServiceIssue] = Field(default_factory=list)


class RefResolver(Protocol):
    kind: RefKind

    def resolve(self, ctx: RefResolveContext, ref: Any) -> ServiceResult[ResolvedRef]:
        ...

    def validate(self, ctx: RefResolveContext, ref: Any) -> ServiceResult[RefValidationResult]:
        ...


class RefResolverComponent:
    """Delegate ref validation to business-specific resolvers."""

    def __init__(self, result: ResultErrorComponent | None = None) -> None:
        self.result = result or ResultErrorComponent()
        self._resolvers: dict[RefKind, RefResolver] = {}

    def register_ref_resolver(self, resolver: RefResolver) -> ServiceResult[MutationSummaryView]:
        kind = RefKind(resolver.kind)
        existing = self._resolvers.get(kind)
        if existing is not None and existing is not resolver:
            return self.result.fail(
                self.result.issue(
                    "duplicate_ref_resolver",
                    f"Resolver already registered for ref kind: {kind}",
                    object_ref=f"ref_kind:{kind}",
                )
            )
        changed = existing is None
        self._resolvers[kind] = resolver
        return self.result.ok(
            self.result.mutation_view(
                object_ref=f"ref_kind:{kind}",
                changed=changed,
                summary=("Registered ref resolver" if changed else "Ref resolver already registered"),
            )
        )

    def register_resolver(self, resolver: RefResolver) -> ServiceResult[MutationSummaryView]:
        return self.register_ref_resolver(resolver)

    def resolve_ref(
        self,
        ctx: RefResolveContext,
        ref: Any,
        *,
        kind: RefKind | str | None = None,
    ) -> ServiceResult[ResolvedRef]:
        ref_kind = self._normalize_or_infer_kind(ref, kind)
        if not ref_kind.ok or ref_kind.value is None:
            return self.result.fail(ref_kind.issues)
        resolver = self._resolvers.get(ref_kind.value)
        if resolver is None:
            return self.result.fail(
                self.result.issue(
                    "resolver_not_registered",
                    f"No resolver registered for ref kind: {ref_kind.value}",
                    object_ref=f"ref_kind:{ref_kind.value}",
                )
            )
        try:
            return resolver.resolve(ctx, ref)
        except Exception as exc:  # noqa: BLE001 - service boundary normalizes failures.
            return self.result.fail(
                self.result.issue(
                    "ref_resolver_failed",
                    f"Ref resolver failed for kind {ref_kind.value}: {exc}",
                    object_ref=f"ref_kind:{ref_kind.value}",
                )
            )

    def resolve(self, ctx: RefResolveContext, ref: Any, *, kind: RefKind | str | None = None) -> ServiceResult[ResolvedRef]:
        return self.resolve_ref(ctx, ref, kind=kind)

    def validate_ref(
        self,
        ctx: RefResolveContext,
        ref: Any,
        *,
        kind: RefKind | str | None = None,
    ) -> ServiceResult[RefValidationResult]:
        ref_kind = self._normalize_or_infer_kind(ref, kind)
        if not ref_kind.ok or ref_kind.value is None:
            return self.result.fail(ref_kind.issues)
        resolver = self._resolvers.get(ref_kind.value)
        if resolver is None:
            return self.result.fail(
                self.result.issue("resolver_not_registered", f"No resolver registered for ref kind: {ref_kind.value}")
            )
        if hasattr(resolver, "validate"):
            try:
                return resolver.validate(ctx, ref)
            except Exception as exc:  # noqa: BLE001
                return self.result.fail(
                    self.result.issue("ref_validation_failed", f"Ref validation failed: {exc}")
                )
        resolved = self.resolve_ref(ctx, ref, kind=ref_kind.value)
        if resolved.ok:
            return self.result.ok(RefValidationResult(resolved=resolved.value))
        return self.result.ok(RefValidationResult(issues=resolved.issues))

    def validate(self, ctx: RefResolveContext, ref: Any, *, kind: RefKind | str | None = None) -> ServiceResult[RefValidationResult]:
        return self.validate_ref(ctx, ref, kind=kind)

    def resolve_refs(self, ctx: RefResolveContext, refs: Sequence[Any]) -> ServiceResult[list[ResolvedRef]]:
        resolved_refs: list[ResolvedRef] = []
        issues: list[ServiceIssue] = []
        for ref in refs:
            result = self.resolve_ref(ctx, ref)
            if result.ok and result.value is not None:
                resolved_refs.append(result.value)
            else:
                issues.extend(result.issues)
        if issues:
            return self.result.fail(issues)
        return self.result.ok(resolved_refs)

    def resolve_many(self, ctx: RefResolveContext, refs: Sequence[Any]) -> ServiceResult[list[ResolvedRef]]:
        return self.resolve_refs(ctx, refs)

    def resolved_ref_view(self, resolved: ResolvedRef) -> ResolvedRefView:
        return ResolvedRefView(ref=resolved.display_ref, summary=resolved.target_summary, kind=resolved.kind)

    def _normalize_or_infer_kind(self, ref: Any, kind: RefKind | str | None) -> ServiceResult[RefKind]:
        if kind is not None:
            try:
                return self.result.ok(RefKind(kind))
            except ValueError:
                return self.result.fail(self.result.issue("unknown_ref_kind", f"Unknown ref kind: {kind}"))

        inferred = self._infer_kind(ref)
        if inferred is None:
            return self.result.fail(
                self.result.issue(
                    "unknown_ref_kind",
                    "Could not infer ref kind. Pass kind explicitly.",
                    details={"ref_type": type(ref).__name__},
                )
            )
        return self.result.ok(inferred)

    def _infer_kind(self, ref: Any) -> RefKind | None:
        value: Any = None
        if isinstance(ref, dict):
            value = ref.get("kind")
        else:
            value = getattr(ref, "kind", None)
        if value is not None:
            try:
                return RefKind(value)
            except ValueError:
                return None

        keys = set(ref.keys()) if isinstance(ref, dict) else set()
        if {"path", "start_line", "end_line"} <= keys:
            return RefKind.SOURCE
        if "resource_key" in keys or "resource_id" in keys:
            return RefKind.RESOURCE
        if "node_path" in keys and "name" not in keys:
            return RefKind.NODE
        if "decl_name" in keys or {"repo", "name"} <= keys:
            return RefKind.DECL
        if "module" in keys or "mathlib_name" in keys:
            return RefKind.MATHLIB
        return None
