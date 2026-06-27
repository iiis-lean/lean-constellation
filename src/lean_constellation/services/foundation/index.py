"""Reusable cache/index registry."""

from __future__ import annotations

from typing import Any, Generic, Protocol, TypeVar

from pydantic import Field

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.services.foundation.layout import FoundationContext, LayoutComponent
from lean_constellation.services.foundation.result_error import (
    MutationSummaryView,
    ResultErrorComponent,
    ServiceIssue,
    ServiceResult,
)
from lean_constellation.services.foundation.store import StoreComponent, WriteMode

T = TypeVar("T")


class IndexMetadata(StrictModel):
    index_name: str
    version: int = 1
    source_truth_refs: list[str] = Field(default_factory=list)
    rebuilt_at: str
    builder_name: str
    stale: bool = False


class IndexBundle(StrictModel, Generic[T]):
    metadata: IndexMetadata
    data: T | None = None


class IndexRebuildView(StrictModel):
    index_name: str
    rebuilt: bool
    summary: str
    warnings: list[ServiceIssue] = Field(default_factory=list)


class IndexBuildContext(StrictModel):
    repo_root: str
    reason: str | None = None
    requested_by: str | None = None


class IndexBuilder(Protocol[T]):
    index_name: str

    def build(self, ctx: IndexBuildContext) -> ServiceResult[IndexBundle[T]]:
        ...


class IndexComponent:
    """Register and rebuild cache indexes from truth files."""

    def __init__(
        self,
        *,
        store: StoreComponent,
        layout: LayoutComponent,
        result: ResultErrorComponent | None = None,
    ) -> None:
        self.store = store
        self.layout = layout
        self.result = result or store.result
        self._builders: dict[str, IndexBuilder[Any]] = {}

    def register_index_builder(self, builder: IndexBuilder[Any]) -> ServiceResult[MutationSummaryView]:
        index_name = self.layout.ensure_safe_key(builder.index_name)
        existing = self._builders.get(index_name)
        if existing is not None and existing is not builder:
            return self.result.fail(
                self.result.issue(
                    "duplicate_index_builder",
                    f"Index builder already registered: {index_name}",
                    object_ref=f"index:{index_name}",
                )
            )
        changed = existing is None
        self._builders[index_name] = builder
        return self.result.ok(
            self.result.mutation_view(
                object_ref=f"index:{index_name}",
                changed=changed,
                summary=("Registered index builder" if changed else "Index builder already registered"),
            )
        )

    def ensure_index(self, ctx: FoundationContext, index_name: str) -> ServiceResult[IndexBundle[Any]]:
        index_name = self.layout.ensure_safe_key(index_name)
        path = self.layout.index_cache_path(ctx, index_name)
        if path.exists():
            cached = self.store.read_json(path, IndexBundle[Any])
            if not cached.ok:
                return self.result.fail(
                    self.result.issue(
                        "index_cache_invalid",
                        f"Index cache is invalid: {index_name}",
                        object_ref=f"index:{index_name}",
                        details={"path": str(path)},
                    )
                )
            if cached.value is not None and not cached.value.metadata.stale:
                return cached
        rebuilt = self.rebuild_index(ctx, index_name, reason="ensure_index")
        if not rebuilt.ok:
            return self.result.fail(rebuilt.issues)
        return self.store.read_json(path, IndexBundle[Any])

    def read_index(self, ctx: FoundationContext, index_name: str) -> ServiceResult[IndexBundle[Any]]:
        return self.store.read_json(self.layout.index_cache_path(ctx, index_name), IndexBundle[Any])

    def rebuild_index(
        self,
        ctx: FoundationContext,
        index_name: str,
        *,
        reason: str | None = None,
    ) -> ServiceResult[IndexRebuildView]:
        index_name = self.layout.ensure_safe_key(index_name)
        builder = self._builders.get(index_name)
        if builder is None:
            return self.result.fail(
                self.result.issue("unknown_index", f"No builder registered for index: {index_name}")
            )
        build_result = builder.build(
            IndexBuildContext(
                repo_root=str(ctx.repo_root),
                reason=reason,
                requested_by=ctx.caller,
            )
        )
        if not build_result.ok or build_result.value is None:
            return self.result.fail(build_result.issues)

        bundle = self._normalized_bundle(index_name, builder, build_result.value)
        write = self.store.write_json_atomic(
            self.layout.index_cache_path(ctx, index_name),
            bundle,
            mode=WriteMode.OVERWRITE,
        )
        if not write.ok:
            return self.result.fail(write.issues)
        return self.result.ok(
            IndexRebuildView(
                index_name=index_name,
                rebuilt=True,
                summary=f"Rebuilt index {index_name}",
                warnings=bundle.metadata.stale and [] or [],
            )
        )

    def mark_index_stale(
        self,
        ctx: FoundationContext,
        index_name: str,
        reason: str,
    ) -> ServiceResult[MutationSummaryView]:
        index_name = self.layout.ensure_safe_key(index_name)
        path = self.layout.index_cache_path(ctx, index_name)
        if path.exists():
            current = self.store.read_json(path, IndexBundle[Any])
            if not current.ok or current.value is None:
                return self.result.fail(current.issues)
            bundle = current.value
            changed = not bundle.metadata.stale
            bundle.metadata.stale = True
        else:
            builder = self._builders.get(index_name)
            bundle = IndexBundle[Any](
                metadata=IndexMetadata(
                    index_name=index_name,
                    rebuilt_at=utc_now_iso(),
                    builder_name=builder.__class__.__name__ if builder else "",
                    stale=True,
                    source_truth_refs=[],
                ),
                data=None,
            )
            changed = True
        write = self.store.write_json_atomic(path, bundle, mode=WriteMode.OVERWRITE)
        if not write.ok:
            return self.result.fail(write.issues)
        return self.result.ok(
            self.result.mutation_view(
                object_ref=f"index:{index_name}",
                changed=changed,
                summary=f"Marked index stale: {reason}",
                changed_items=[reason],
            )
        )

    def delete_index_cache(
        self,
        ctx: FoundationContext,
        index_name: str,
        *,
        missing_ok: bool = True,
    ) -> ServiceResult[MutationSummaryView]:
        index_name = self.layout.ensure_safe_key(index_name)
        path = self.layout.index_cache_path(ctx, index_name)
        existed = path.exists()
        result = self.store.delete_json(path, missing_ok=missing_ok)
        if not result.ok:
            return self.result.fail(result.issues)
        return self.result.ok(
            self.result.mutation_view(
                object_ref=f"index:{index_name}",
                changed=existed,
                summary=("Deleted index cache" if existed else "Index cache already absent"),
            )
        )

    def list_index_metadata(self, ctx: FoundationContext) -> ServiceResult[list[IndexMetadata]]:
        root = self.layout.indexes_root(ctx)
        if not root.exists():
            return self.result.ok([])
        bundles = self.store.list_json(root, IndexBundle[Any])
        if not bundles.ok:
            return self.result.fail(bundles.issues)
        metadata = sorted((bundle.metadata for bundle in bundles.value or []), key=lambda item: item.index_name)
        return self.result.ok(metadata)

    def rebuild_all(self, ctx: FoundationContext) -> ServiceResult[list[IndexRebuildView]]:
        views: list[IndexRebuildView] = []
        issues = []
        for index_name in sorted(self._builders):
            result = self.rebuild_index(ctx, index_name, reason="rebuild_all")
            if result.ok and result.value is not None:
                views.append(result.value)
            else:
                issues.extend(result.issues)
        if issues:
            return self.result.fail(issues)
        return self.result.ok(views)

    def _normalized_bundle(
        self,
        index_name: str,
        builder: IndexBuilder[Any],
        bundle: IndexBundle[Any],
    ) -> IndexBundle[Any]:
        return IndexBundle[Any](
            metadata=IndexMetadata(
                index_name=index_name,
                version=bundle.metadata.version,
                source_truth_refs=bundle.metadata.source_truth_refs,
                rebuilt_at=bundle.metadata.rebuilt_at or utc_now_iso(),
                builder_name=bundle.metadata.builder_name or builder.__class__.__name__,
                stale=False,
            ),
            data=bundle.data,
        )
