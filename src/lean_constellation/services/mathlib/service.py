"""Mathlib service composition and public wrappers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.services.foundation import GateReport, ServiceResult
from lean_constellation.services.mathlib.mathlib_index import (
    MathlibDeclEntryView,
    MathlibIndexComponent,
    MathlibModuleEntryView,
    MathlibSearchView,
)
from lean_constellation.services.mathlib.node_mathlib_use import NodeMathlibUseComponent
from lean_constellation.services.mathlib.node_mathlib_use import (
    NodeMathlibHintMutationReceipt,
    NodeMathlibHintsBatchReceipt,
    NodeMathlibHintView,
)
from lean_constellation.services.mathlib.toolkit_ingestion import (
    MathlibAccessCheckView,
    MathlibBatchRecordView,
    MathlibCheckView,
    MathlibExternalSearchView,
    MathlibModuleNavigationView,
    MathlibNavigationView,
    MathlibCandidateView,
    MathlibSemanticSearchView,
    ToolkitIngestionComponent,
)

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class MathlibService:
    """Composition root for Mathlib-related repo services."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        mathlib_index: MathlibIndexComponent | None = None,
        toolkit_ingestion: ToolkitIngestionComponent | None = None,
        node_mathlib_use: NodeMathlibUseComponent | None = None,
    ) -> None:
        self.runtime = runtime
        self.mathlib_index = mathlib_index or MathlibIndexComponent(runtime)
        self.toolkit_ingestion = toolkit_ingestion or ToolkitIngestionComponent(
            runtime,
            self.mathlib_index,
        )
        self.node_mathlib_use = node_mathlib_use or NodeMathlibUseComponent(
            runtime,
            mathlib_index=self.mathlib_index,
        )

    def search_mathlib_index(
        self,
        repo_root: Path,
        *,
        query: str,
        regex: bool = False,
        entry_kind: str = "all",
        limit: int = 20,
    ) -> ServiceResult[MathlibSearchView]:
        return self.mathlib_index.search_mathlib_index(
            repo_root,
            query=query,
            regex=regex,
            entry_kind=entry_kind,
            limit=limit,
        )

    def get_mathlib_module_entry(self, repo_root: Path, *, module: str) -> ServiceResult[MathlibModuleEntryView]:
        return self.mathlib_index.get_mathlib_module_entry(repo_root, module=module)

    def upsert_mathlib_module_entry(
        self,
        repo_root: Path,
        *,
        module: str,
        summary: str | None = None,
        note: str | None = None,
    ) -> ServiceResult[MathlibModuleEntryView]:
        return self.mathlib_index.upsert_mathlib_module_entry(repo_root, module=module, summary=summary, note=note)

    def add_module_important_decl(
        self,
        repo_root: Path,
        *,
        module: str,
        decl_name: str,
    ) -> ServiceResult[MathlibModuleEntryView]:
        return self.mathlib_index.add_module_important_decl(repo_root, module=module, decl_name=decl_name)

    def get_mathlib_decl_entry(self, repo_root: Path, *, name: str) -> ServiceResult[MathlibDeclEntryView]:
        return self.mathlib_index.get_mathlib_decl_entry(repo_root, name=name)

    def upsert_mathlib_decl_entry(
        self,
        repo_root: Path,
        *,
        name: str,
        module: str | None = None,
        kind: str | None = None,
        signature: str | None = None,
        summary: str | None = None,
        note: str | None = None,
        snippet: str | None = None,
    ) -> ServiceResult[MathlibDeclEntryView]:
        return self.mathlib_index.upsert_mathlib_decl_entry(
            repo_root,
            name=name,
            module=module,
            kind=kind,
            signature=signature,
            summary=summary,
            note=note,
            snippet=snippet,
        )

    def search_external_mathlib(
        self,
        repo_root: Path,
        *,
        query: str,
        search_kinds: list[str],
        limit: int = 20,
    ) -> ServiceResult[MathlibExternalSearchView]:
        return self.toolkit_ingestion.search_external_mathlib(
            repo_root,
            query=query,
            search_kinds=search_kinds,
            limit=limit,
        )

    def search_mathlib_declarations(
        self,
        repo_root: Path,
        *,
        query: str,
        limit: int = 20,
    ) -> ServiceResult[MathlibSemanticSearchView]:
        return self.toolkit_ingestion.search_mathlib_declarations(repo_root, query=query, limit=limit)

    def inspect_mathlib_search_candidate(self, repo_root: Path, *, candidate_id: str) -> ServiceResult[MathlibCandidateView]:
        return self.toolkit_ingestion.inspect_mathlib_search_candidate(repo_root, candidate_id=candidate_id)

    def inspect_mathlib_declaration(self, repo_root: Path, *, decl_name: str) -> ServiceResult[MathlibNavigationView]:
        return self.toolkit_ingestion.inspect_mathlib_declaration(repo_root, decl_name=decl_name)

    def inspect_mathlib_module(
        self,
        repo_root: Path,
        *,
        module: str | None = None,
        module_name: str | None = None,
        pattern: str | None = None,
    ) -> ServiceResult[MathlibModuleNavigationView]:
        return self.toolkit_ingestion.inspect_mathlib_module(repo_root, module=module_name or module or "", pattern=pattern)

    def check_mathlib_name(
        self,
        repo_root: Path,
        *,
        module: str | None,
        decl_name: str,
    ) -> ServiceResult[MathlibCheckView]:
        return self.toolkit_ingestion.check_mathlib_name(repo_root, module=module, decl_name=decl_name)

    def check_mathlib_accessible(
        self,
        repo_root: Path,
        *,
        name_or_module: str,
        module: str | None = None,
        target_kind: str = "declaration",
    ) -> ServiceResult[MathlibAccessCheckView]:
        return self.toolkit_ingestion.check_mathlib_accessible(
            repo_root,
            name_or_module=name_or_module,
            module=module,
            target_kind=target_kind,
        )

    def ingest_mathlib_candidate(
        self,
        repo_root: Path,
        *,
        candidate_id: str,
        summary: str,
        note: str | None = None,
    ) -> ServiceResult[MathlibDeclEntryView]:
        return self.toolkit_ingestion.ingest_mathlib_candidate(repo_root, candidate_id=candidate_id, summary=summary, note=note)

    def record_mathlib_module_checked(
        self,
        repo_root: Path,
        *,
        module_name: str,
        summary: str | None = None,
        source: str | None = None,
    ) -> ServiceResult[MathlibModuleEntryView]:
        return self.toolkit_ingestion.record_mathlib_module_checked(
            repo_root,
            module_name=module_name,
            summary=summary,
            source=source,
        )

    def record_mathlib_decl_checked(
        self,
        repo_root: Path,
        *,
        decl_name: str,
        module_name: str | None = None,
        summary: str | None = None,
        source: str | None = None,
        kind: str | None = None,
        signature: str | None = None,
        snippet: str | None = None,
    ) -> ServiceResult[MathlibDeclEntryView]:
        return self.toolkit_ingestion.record_mathlib_decl_checked(
            repo_root,
            decl_name=decl_name,
            module_name=module_name,
            summary=summary,
            source=source,
            kind=kind,
            signature=signature,
            snippet=snippet,
        )

    def record_mathlib_batch_checked(
        self,
        repo_root: Path,
        *,
        modules: list[dict[str, object]],
        declarations: list[dict[str, object]],
    ) -> ServiceResult[MathlibBatchRecordView]:
        return self.toolkit_ingestion.record_mathlib_batch_checked(
            repo_root,
            modules=modules,
            declarations=declarations,
        )

    def add_mathlib_module_use(
        self,
        repo_root: Path,
        *,
        node_path: str,
        module: str,
        reason: str | None,
        actor: str,
    ) -> ServiceResult[NodeMathlibHintMutationReceipt]:
        return self.node_mathlib_use.add_mathlib_module_use(
            repo_root,
            node_path=node_path,
            module=module,
            reason=reason,
            actor=actor,
        )

    def remove_mathlib_module_use(
        self,
        repo_root: Path,
        *,
        node_path: str,
        module: str,
        actor: str,
    ) -> ServiceResult[NodeMathlibHintMutationReceipt]:
        return self.node_mathlib_use.remove_mathlib_module_use(
            repo_root,
            node_path=node_path,
            module=module,
            actor=actor,
        )

    def add_mathlib_decl_use(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        reason: str | None,
        actor: str,
    ) -> ServiceResult[NodeMathlibHintMutationReceipt]:
        return self.node_mathlib_use.add_mathlib_decl_use(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            reason=reason,
            actor=actor,
        )

    def remove_mathlib_decl_use(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        actor: str,
    ) -> ServiceResult[NodeMathlibHintMutationReceipt]:
        return self.node_mathlib_use.remove_mathlib_decl_use(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            actor=actor,
        )

    def validate_node_mathlib_uses(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        return self.node_mathlib_use.validate_node_mathlib_uses(repo_root, node_path=node_path)

    def get_node_mathlib_hint_view(self, repo_root: Path, *, node_path: str) -> ServiceResult[NodeMathlibHintView]:
        return self.node_mathlib_use.get_node_mathlib_hint_view(repo_root, node_path=node_path)

    def add_mathlib_hints(
        self,
        repo_root: Path,
        *,
        node_path: str,
        modules: list[tuple[str, str | None]],
        declarations: list[tuple[str, str | None]],
        actor: str,
    ) -> ServiceResult[NodeMathlibHintsBatchReceipt]:
        return self.node_mathlib_use.add_mathlib_hints(
            repo_root,
            node_path=node_path,
            modules=modules,
            declarations=declarations,
            actor=actor,
        )

    def add_node_mathlib_module_hint(
        self,
        repo_root: Path,
        *,
        node_path: str,
        module: str,
        reason: str | None,
        actor: str,
    ) -> ServiceResult[NodeMathlibHintMutationReceipt]:
        return self.node_mathlib_use.add_node_mathlib_module_hint(
            repo_root,
            node_path=node_path,
            module=module,
            reason=reason,
            actor=actor,
        )

    def remove_node_mathlib_module_hint(
        self,
        repo_root: Path,
        *,
        node_path: str,
        module: str,
        actor: str,
    ) -> ServiceResult[NodeMathlibHintMutationReceipt]:
        return self.node_mathlib_use.remove_node_mathlib_module_hint(
            repo_root,
            node_path=node_path,
            module=module,
            actor=actor,
        )

    def add_node_mathlib_decl_hint(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        reason: str | None,
        actor: str,
    ) -> ServiceResult[NodeMathlibHintMutationReceipt]:
        return self.node_mathlib_use.add_node_mathlib_decl_hint(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            reason=reason,
            actor=actor,
        )

    def remove_node_mathlib_decl_hint(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        actor: str,
    ) -> ServiceResult[NodeMathlibHintMutationReceipt]:
        return self.node_mathlib_use.remove_node_mathlib_decl_hint(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            actor=actor,
        )
