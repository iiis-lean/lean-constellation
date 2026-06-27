from __future__ import annotations

from typing import Any

from lean_constellation.domain.common import utc_now_iso
from lean_constellation.services.foundation import (
    FoundationContext,
    FoundationService,
    IndexBuildContext,
    IndexBundle,
    IndexMetadata,
    RefKind,
    RefResolveContext,
    RefValidationResult,
    ResolvedRef,
    ResultErrorComponent,
)


class CountingBuilder:
    index_name = "demo_index"

    def __init__(self) -> None:
        self.calls = 0
        self.result = ResultErrorComponent()

    def build(self, ctx: IndexBuildContext):
        self.calls += 1
        return self.result.ok(
            IndexBundle[dict[str, Any]](
                metadata=IndexMetadata(
                    index_name=self.index_name,
                    rebuilt_at=utc_now_iso(),
                    builder_name="CountingBuilder",
                    source_truth_refs=[ctx.repo_root],
                ),
                data={"calls": self.calls},
            )
        )


class SameNameBuilder(CountingBuilder):
    pass


class FailingBuilder:
    index_name = "failing_index"

    def __init__(self) -> None:
        self.result = ResultErrorComponent()

    def build(self, ctx: IndexBuildContext):
        return self.result.fail(self.result.issue("builder_failed", f"builder failed for {ctx.repo_root}"))


class NodeResolver:
    kind = RefKind.NODE

    def __init__(self) -> None:
        self.result = ResultErrorComponent()

    def resolve(self, ctx: RefResolveContext, ref: Any):
        node_path = ref["node_path"]
        if node_path == "Missing":
            return self.result.fail(self.result.issue("ref_not_found", "node missing"))
        return self.result.ok(
            ResolvedRef(
                kind=RefKind.NODE,
                canonical_ref=f"node:{node_path}",
                display_ref=node_path,
                target_summary=f"Node {node_path} in {ctx.repo_root}",
            )
        )

    def validate(self, ctx: RefResolveContext, ref: Any):
        resolved = self.resolve(ctx, ref)
        if resolved.ok:
            return self.result.ok(RefValidationResult(resolved=resolved.value))
        return self.result.ok(RefValidationResult(issues=resolved.issues))


class AnotherNodeResolver(NodeResolver):
    pass


class GenericResolver:
    def __init__(self, kind: RefKind) -> None:
        self.kind = kind
        self.result = ResultErrorComponent()

    def resolve(self, ctx: RefResolveContext, ref: Any):
        return self.result.ok(
            ResolvedRef(
                kind=self.kind,
                canonical_ref=f"{self.kind.value}:{ref}",
                display_ref=f"display:{self.kind.value}",
                target_summary=f"{self.kind.value} in {ctx.repo_root}",
                metadata={"internal_path": "/tmp/hidden"},
            )
        )

    def validate(self, ctx: RefResolveContext, ref: Any):
        resolved = self.resolve(ctx, ref)
        return self.result.ok(RefValidationResult(resolved=resolved.value))


class RaisingResolver:
    kind = RefKind.NODE

    def resolve(self, ctx: RefResolveContext, ref: Any):
        raise RuntimeError("boom")

    def validate(self, ctx: RefResolveContext, ref: Any):
        raise RuntimeError("boom")


class ResolveOnlyResolver:
    kind = RefKind.NODE

    def __init__(self) -> None:
        self.result = ResultErrorComponent()

    def resolve(self, ctx: RefResolveContext, ref: Any):
        if ref.get("node_path") == "Missing":
            return self.result.fail(self.result.issue("ref_not_found", "node missing"))
        return self.result.ok(
            ResolvedRef(
                kind=RefKind.NODE,
                canonical_ref=f"node:{ref['node_path']}",
                display_ref=ref["node_path"],
                target_summary=f"Node {ref['node_path']}",
            )
        )


def test_foundation_service_wires_components() -> None:
    service = FoundationService()

    assert service.result is service.result_error
    assert service.store.result is service.result_error
    assert service.index.store is service.store
    assert service.refs is service.ref_resolver


def test_index_ensure_cache_hit_and_stale_rebuild(tmp_path) -> None:
    service = FoundationService()
    ctx = FoundationContext(repo_root=tmp_path, caller="test")
    builder = CountingBuilder()

    assert service.register_index_builder(builder).ok is True
    first = service.ensure_index(ctx, "demo_index")
    second = service.ensure_index(ctx, "demo_index")
    stale = service.mark_index_stale(ctx, "demo_index", "truth changed")
    third = service.ensure_index(ctx, "demo_index")

    assert first.ok is True and first.value is not None and first.value.data == {"calls": 1}
    assert second.ok is True and second.value is not None and second.value.data == {"calls": 1}
    assert stale.ok is True
    assert third.ok is True and third.value is not None and third.value.data == {"calls": 2}
    assert builder.calls == 2
    metadata = service.index.list_index_metadata(ctx)
    assert metadata.ok is True
    assert [item.index_name for item in metadata.value or []] == ["demo_index"]


def test_register_index_builder_allows_same_instance_and_rejects_conflict() -> None:
    service = FoundationService()
    builder = CountingBuilder()

    first = service.register_index_builder(builder)
    second = service.register_index_builder(builder)
    conflict = service.register_index_builder(SameNameBuilder())

    assert first.ok is True
    assert first.value is not None and first.value.changed is True
    assert second.ok is True
    assert second.value is not None and second.value.changed is False
    assert conflict.ok is False
    assert conflict.issues[0].kind == "duplicate_index_builder"


def test_rebuild_index_direct_success_unknown_builder_builder_fail_and_write_fail(tmp_path) -> None:
    service = FoundationService()
    ctx = FoundationContext(repo_root=tmp_path, caller="test")
    builder = CountingBuilder()
    failing_builder = FailingBuilder()

    service.register_index_builder(builder)
    service.register_index_builder(failing_builder)

    rebuilt = service.rebuild_index(ctx, "demo_index", reason="manual")
    unknown = service.rebuild_index(ctx, "missing_index", reason="manual")
    failed = service.rebuild_index(ctx, "failing_index", reason="manual")

    blocked_root = tmp_path / "blocked"
    blocked_ctx = FoundationContext(repo_root=blocked_root, caller="test")
    indexes_path = blocked_root / ".lean_constellation" / "indexes"
    indexes_path.parent.mkdir(parents=True)
    indexes_path.write_text("not a directory", encoding="utf-8")
    write_failed = service.rebuild_index(blocked_ctx, "demo_index", reason="manual")

    assert rebuilt.ok is True
    assert rebuilt.value is not None and rebuilt.value.rebuilt is True
    assert builder.calls == 2
    cached = service.index.read_index(ctx, "demo_index")
    assert cached.ok is True
    assert cached.value is not None and cached.value.metadata.stale is False
    assert cached.value.data == {"calls": 1}
    assert unknown.ok is False
    assert unknown.issues[0].kind == "unknown_index"
    assert failed.ok is False
    assert failed.issues[0].kind == "builder_failed"
    assert write_failed.ok is False
    assert write_failed.issues[0].kind == "write_failed"


def test_mark_index_stale_creates_stale_metadata_when_cache_is_missing(tmp_path) -> None:
    service = FoundationService()
    ctx = FoundationContext(repo_root=tmp_path, caller="test")
    builder = CountingBuilder()
    service.register_index_builder(builder)

    stale = service.mark_index_stale(ctx, "demo_index", "truth changed")
    cached = service.index.read_index(ctx, "demo_index")

    assert stale.ok is True
    assert stale.value is not None
    assert stale.value.changed is True
    assert stale.value.changed_items == ["truth changed"]
    assert cached.ok is True
    assert cached.value is not None
    assert cached.value.metadata.index_name == "demo_index"
    assert cached.value.metadata.builder_name == "CountingBuilder"
    assert cached.value.metadata.stale is True
    assert cached.value.data is None


def test_ref_resolver_register_resolve_validate_and_batch(tmp_path) -> None:
    service = FoundationService()
    ctx = RefResolveContext(repo_root=str(tmp_path), purpose="node_dep")
    resolver = NodeResolver()

    registered = service.register_ref_resolver(resolver)
    resolved = service.resolve_ref(ctx, {"kind": "node", "node_path": "Main"})
    valid = service.validate_ref(ctx, {"node_path": "Main"}, kind=RefKind.NODE)
    batch = service.resolve_refs(ctx, [{"kind": "node", "node_path": "Main"}, {"kind": "node", "node_path": "Missing"}])

    assert registered.ok is True
    assert resolved.ok is True and resolved.value is not None
    assert resolved.value.canonical_ref == "node:Main"
    assert valid.ok is True and valid.value is not None and valid.value.resolved is not None
    assert batch.ok is False
    assert batch.issues[0].kind == "ref_not_found"


def test_ref_resolver_duplicate_registration_and_conflict() -> None:
    service = FoundationService()
    resolver = NodeResolver()

    first = service.register_ref_resolver(resolver)
    second = service.register_ref_resolver(resolver)
    conflict = service.register_ref_resolver(AnotherNodeResolver())

    assert first.ok is True
    assert first.value is not None and first.value.changed is True
    assert second.ok is True
    assert second.value is not None and second.value.changed is False
    assert conflict.ok is False
    assert conflict.issues[0].kind == "duplicate_ref_resolver"


def test_ref_resolver_explicit_kind_unregistered_exception_and_structural_inference(tmp_path) -> None:
    service = FoundationService()
    ctx = RefResolveContext(repo_root=str(tmp_path))
    for kind in [RefKind.SOURCE, RefKind.RESOURCE, RefKind.NODE, RefKind.DECL, RefKind.MATHLIB]:
        assert service.register_ref_resolver(GenericResolver(kind)).ok is True

    explicit = service.resolve_ref(ctx, {"node_path": "Main"}, kind=RefKind.NODE)
    inferred_source = service.resolve_ref(ctx, {"path": "source.md", "start_line": 1, "end_line": 2})
    inferred_resource = service.resolve_ref(ctx, {"resource_key": "r1"})
    inferred_decl = service.resolve_ref(ctx, {"repo": "R", "name": "foo"})
    inferred_mathlib = service.resolve_ref(ctx, {"module": "Mathlib.Data.Nat.Basic"})

    assert explicit.ok is True and explicit.value is not None and explicit.value.kind == RefKind.NODE
    assert inferred_source.ok is True and inferred_source.value is not None and inferred_source.value.kind == RefKind.SOURCE
    assert inferred_resource.ok is True and inferred_resource.value is not None and inferred_resource.value.kind == RefKind.RESOURCE
    assert inferred_decl.ok is True and inferred_decl.value is not None and inferred_decl.value.kind == RefKind.DECL
    assert inferred_mathlib.ok is True and inferred_mathlib.value is not None and inferred_mathlib.value.kind == RefKind.MATHLIB

    unregistered = FoundationService().resolve_ref(ctx, {"path": "source.md", "start_line": 1, "end_line": 2})
    assert unregistered.ok is False
    assert unregistered.issues[0].kind == "resolver_not_registered"

    raising_service = FoundationService()
    raising_service.register_ref_resolver(RaisingResolver())
    raised = raising_service.resolve_ref(ctx, {"node_path": "Main"}, kind=RefKind.NODE)
    assert raised.ok is False
    assert raised.issues[0].kind == "ref_resolver_failed"


def test_ref_resolver_fallback_validate_returns_invalid_result_as_success(tmp_path) -> None:
    service = FoundationService()
    ctx = RefResolveContext(repo_root=str(tmp_path))
    service.register_ref_resolver(ResolveOnlyResolver())

    valid = service.validate_ref(ctx, {"node_path": "Main"}, kind=RefKind.NODE)
    invalid = service.validate_ref(ctx, {"node_path": "Missing"}, kind=RefKind.NODE)

    assert valid.ok is True
    assert valid.value is not None and valid.value.resolved is not None
    assert invalid.ok is True
    assert invalid.value is not None
    assert invalid.value.resolved is None
    assert [issue.kind for issue in invalid.value.issues] == ["ref_not_found"]


def test_resolved_ref_view_has_stable_fields_and_hides_metadata() -> None:
    service = FoundationService()
    resolved = ResolvedRef(
        kind=RefKind.RESOURCE,
        canonical_ref="resource:r1",
        display_ref="r1",
        target_summary="Resource r1",
        metadata={"local_path": "/tmp/secret"},
    )

    view = service.ref_resolver.resolved_ref_view(resolved)
    dumped = view.model_dump()

    assert dumped == {"ref": "r1", "summary": "Resource r1", "kind": RefKind.RESOURCE, "warnings": []}
    assert "metadata" not in dumped
    assert "local_path" not in dumped


def test_ref_resolver_rejects_unknown_kind(tmp_path) -> None:
    service = FoundationService()
    ctx = RefResolveContext(repo_root=str(tmp_path))

    result = service.resolve_ref(ctx, {"kind": "unknown", "value": "x"})

    assert result.ok is False
    assert result.issues[0].kind == "unknown_ref_kind"
