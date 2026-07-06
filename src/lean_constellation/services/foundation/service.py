"""Foundation service façade."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from lean_constellation.services.foundation.index import IndexBuilder, IndexComponent
from lean_constellation.services.foundation.layout import FoundationContext, LayoutComponent
from lean_constellation.services.foundation.ref_resolver import (
    RefKind,
    RefResolveContext,
    RefResolver,
    RefResolverComponent,
)
from lean_constellation.services.foundation.result_error import (
    GateReport,
    IssueSeverity,
    ResultErrorComponent,
    ServiceIssue,
)
from lean_constellation.services.foundation.store import StoreComponent, WriteMode

T = TypeVar("T")

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class FoundationService:
    """Composition root for bottom-level service utilities."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        result_error: ResultErrorComponent | None = None,
        layout: LayoutComponent | None = None,
        store: StoreComponent | None = None,
        index: IndexComponent | None = None,
        ref_resolver: RefResolverComponent | None = None,
    ) -> None:
        self.runtime = runtime
        self.result_error = result_error or ResultErrorComponent()
        self.result = self.result_error
        self.layout = layout or LayoutComponent()
        self.store = store or StoreComponent(runtime, self.result_error)
        self.index = index or IndexComponent(runtime, store=self.store, layout=self.layout, result=self.result_error)
        self.ref_resolver = ref_resolver or RefResolverComponent(runtime, self.result_error)
        self.refs = self.ref_resolver

    def issue(self, *args: Any, **kwargs: Any) -> ServiceIssue:
        return self.result_error.issue(*args, **kwargs)

    def ok(self, *args: Any, **kwargs: Any) -> Any:
        return self.result_error.ok(*args, **kwargs)

    def fail(self, *args: Any, **kwargs: Any) -> Any:
        return self.result_error.fail(*args, **kwargs)

    def gate_passed(self, *args: Any, **kwargs: Any) -> GateReport:
        return self.result_error.gate_passed(*args, **kwargs)

    def gate_failed(self, *args: Any, **kwargs: Any) -> GateReport:
        return self.result_error.gate_failed(*args, **kwargs)

    def merge_gate_reports(self, gate_name: str, reports: Sequence[GateReport]) -> GateReport:
        return self.result_error.merge_gate_reports(gate_name, reports)

    def mutation_view(self, *args: Any, **kwargs: Any) -> Any:
        return self.result_error.mutation_view(*args, **kwargs)

    def gate_report_view(self, report: GateReport) -> Any:
        return self.result_error.gate_report_view(report)

    def constellation_root(self, ctx: FoundationContext) -> Path:
        return self.layout.constellation_root(ctx)

    def repo_metadata_path(self, ctx: FoundationContext) -> Path:
        return self.layout.repo_metadata_path(ctx)

    def preparation_input_path(self, ctx: FoundationContext) -> Path:
        return self.layout.preparation_input_path(ctx)

    def source_corpus_root(self, ctx: FoundationContext, relpath: str = ".lean_constellation/source") -> Path:
        return self.layout.source_corpus_root(ctx, relpath)

    def resource_dir(self, ctx: FoundationContext, resource_key: str) -> Path:
        return self.layout.resource_dir(ctx, resource_key)

    def node_contract_path(self, ctx: FoundationContext, node_path: str, version: int) -> Path:
        node_service = getattr(self.runtime, "node", None)
        node_tree = getattr(node_service, "node_tree", None)
        node_store = getattr(node_tree, "node_store", None)
        if node_store is not None:
            node = node_store.resolve_active_node(ctx.repo_root, path=node_path)
            if node.ok and node.value is not None:
                return node_store.contract_path(ctx.repo_root, node_id=node.value.node_id, version=version)
        return self.layout.node_contract_path(ctx, node_path, version)

    def node_contract_path_by_id(self, ctx: FoundationContext, node_id: str, version: int) -> Path:
        return self.layout.node_contract_path_by_id(ctx, node_id, version)

    def prelude_path(self, ctx: FoundationContext, node_path: str) -> Path:
        return self.layout.prelude_path(ctx, node_path)

    def interfaces_path(self, ctx: FoundationContext, node_path: str) -> Path:
        return self.layout.interfaces_path(ctx, node_path)

    def index_cache_path(self, ctx: FoundationContext, index_name: str) -> Path:
        return self.layout.index_cache_path(ctx, index_name)

    def read_json(self, path: Path, model_type: type[T]) -> Any:
        return self.store.read_json(path, model_type)

    def write_json_atomic(self, path: Path, value: Any, *, mode: WriteMode = WriteMode.OVERWRITE) -> Any:
        return self.store.write_json_atomic(path, value, mode=mode)

    def mutation(self, action_name: str) -> Any:
        return self.store.mutation(action_name)

    def allocate_uuid(self, exists: Callable[[str], bool], *, prefix: str | None = None) -> Any:
        return self.store.allocate_uuid(exists, prefix=prefix)

    def ensure_open_version(self, **kwargs: Any) -> Any:
        return self.store.ensure_open_version(**kwargs)

    def register_index_builder(self, builder: IndexBuilder[Any]) -> Any:
        return self.index.register_index_builder(builder)

    def ensure_index(self, ctx: FoundationContext, index_name: str) -> Any:
        return self.index.ensure_index(ctx, index_name)

    def rebuild_index(self, ctx: FoundationContext, index_name: str, *, reason: str | None = None) -> Any:
        return self.index.rebuild_index(ctx, index_name, reason=reason)

    def mark_index_stale(self, ctx: FoundationContext, index_name: str, reason: str) -> Any:
        return self.index.mark_index_stale(ctx, index_name, reason)

    def register_ref_resolver(self, resolver: RefResolver) -> Any:
        return self.ref_resolver.register_ref_resolver(resolver)

    def resolve_ref(self, ctx: RefResolveContext, ref: Any, *, kind: RefKind | str | None = None) -> Any:
        return self.ref_resolver.resolve_ref(ctx, ref, kind=kind)

    def validate_ref(self, ctx: RefResolveContext, ref: Any, *, kind: RefKind | str | None = None) -> Any:
        return self.ref_resolver.validate_ref(ctx, ref, kind=kind)

    def resolve_refs(self, ctx: RefResolveContext, refs: Sequence[Any]) -> Any:
        return self.ref_resolver.resolve_refs(ctx, refs)
