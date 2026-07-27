"""Mathlib service composition and public wrappers."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.refs import MathlibRef
from lean_constellation.services.decl_graph.models import DeclDependencyMutationReceipt, MathlibDeclDep
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
    MathlibCandidateDetailView,
    MathlibCheckView,
    MathlibExternalSearchView,
    MathlibModuleNavigationView,
    MathlibNavigationView,
    MathlibSemanticSearchView,
    ToolkitIngestionComponent,
)

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices

class MathlibDependencyRequest:
    """Normalized service input kept internal to the application handler."""

    def __init__(self, *, name: str, module: str | None, reason: str | None) -> None:
        self.name = name
        self.module = module
        self.reason = reason


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

    def add_decl_dependencies_transaction(
        self,
        repo_root: Path,
        *,
        requests: list[MathlibDependencyRequest],
        dependency_stage: str,
        add_dependencies: Callable[[list[MathlibDeclDep]], ServiceResult[DeclDependencyMutationReceipt]],
    ) -> ServiceResult[DeclDependencyMutationReceipt]:
        """Ensure index truth and add exact dependencies with byte-for-byte rollback."""

        if dependency_stage not in {"statement", "proof"}:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_dependency_stage_invalid",
                    "Mathlib dependency stage must be statement or proof.",
                    current=dependency_stage,
                )
            )
        if not requests:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_dependency_batch_empty",
                    "At least one Mathlib declaration dependency is required.",
                )
            )

        normalized: dict[str, MathlibDependencyRequest] = {}
        for item in requests:
            name = item.name.strip()
            module = item.module.strip() if item.module else None
            reason = item.reason.strip() if item.reason else None
            if not name:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "mathlib_decl_name_empty",
                        "Mathlib declaration name must be non-empty.",
                        field="name",
                    )
                )
            previous = normalized.get(name)
            if previous is not None:
                previous_module = previous.module.strip() if previous.module else None
                if previous_module != module or previous.reason != reason:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "mathlib_dependency_duplicate_conflict",
                            "Duplicate Mathlib dependency requests disagree on module or reason.",
                            object_ref=name,
                        )
                    )
                continue
            normalized[name] = MathlibDependencyRequest(name=name, module=module, reason=reason)

        resolved_entries: list[MathlibDeclEntryView] = []
        dependencies: list[MathlibDeclDep] = []
        for item in normalized.values():
            existing = self.mathlib_index.get_mathlib_decl_entry(repo_root, name=item.name)
            complete_existing = (
                existing.ok
                and existing.value is not None
                and existing.value.module is not None
                and existing.value.kind is not None
                and existing.value.signature is not None
            )
            if complete_existing:
                assert existing.value is not None
                if item.module is not None and item.module != existing.value.module:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "mathlib_decl_module_conflict",
                            "Requested Mathlib module conflicts with the canonical repo index entry.",
                            object_ref=item.name,
                            current=item.module,
                            expected=existing.value.module,
                        )
                    )
                entry = existing.value
            else:
                resolved = self.toolkit_ingestion.resolve_mathlib_decl_entry(
                    repo_root,
                    decl_name=item.name,
                    module_name=item.module,
                )
                if not resolved.ok or resolved.value is None:
                    return self.runtime.foundation.fail(resolved.issues)
                entry = resolved.value
            if entry.module is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "mathlib_decl_module_missing",
                        "Verified Mathlib declaration metadata has no defining module.",
                        object_ref=item.name,
                    )
                )
            resolved_entries.append(entry)
            dependencies.append(
                MathlibDeclDep(
                    ref=MathlibRef(name=entry.name, module=entry.module),
                    reason=item.reason,
                )
            )

        index_path = self.mathlib_index.index_path(repo_root)
        index_existed = index_path.exists()
        try:
            index_bytes = index_path.read_bytes() if index_existed else None
        except OSError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_index_snapshot_failed",
                    f"Failed to snapshot MathlibIndex before dependency transaction: {exc}",
                    details={"path": str(index_path)},
                )
            )

        ensured = self.mathlib_index.ensure_mathlib_decl_entries(
            repo_root,
            entries=resolved_entries,
        )
        if not ensured.ok or ensured.value is None:
            return self.runtime.foundation.fail(ensured.issues)
        added = add_dependencies(dependencies)
        if not added.ok or added.value is None:
            rollback_issues = self._restore_index(
                path=index_path,
                existed=index_existed,
                contents=index_bytes,
            )
            return self.runtime.foundation.fail([*added.issues, *rollback_issues])

        receipt = added.value.model_copy(
            update={
                "changed": added.value.changed or ensured.value.changed,
                "dependency_stage": dependency_stage,
                "mathlib_index": ensured.value.model_dump(mode="json"),
            }
        )
        return self.runtime.foundation.ok(
            receipt,
            warnings=[*ensured.issues, *added.issues],
        )

    def _restore_index(
        self,
        *,
        path: Path,
        existed: bool,
        contents: bytes | None,
    ) -> list:
        temp_path: Path | None = None
        try:
            if not existed:
                path.unlink(missing_ok=True)
                return []
            if contents is None:
                raise RuntimeError("existing MathlibIndex snapshot has no contents")
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, raw_path = tempfile.mkstemp(prefix=f".{path.name}.rollback-", dir=path.parent)
            temp_path = Path(raw_path)
            with os.fdopen(fd, "wb") as handle:
                handle.write(contents)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            return []
        except Exception as exc:  # noqa: BLE001 - normalized into ServiceIssue.
            return [
                self.runtime.foundation.issue(
                    "mathlib_index_rollback_failed",
                    f"Failed to restore MathlibIndex after dependency transaction failure: {exc}",
                    details={"path": str(path)},
                )
            ]
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

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

    def inspect_mathlib_search_candidate(
        self,
        repo_root: Path,
        *,
        candidate_id: str,
        include_source_excerpt: bool = False,
    ) -> ServiceResult[MathlibCandidateDetailView]:
        return self.toolkit_ingestion.inspect_mathlib_search_candidate(
            repo_root,
            candidate_id=candidate_id,
            include_source_excerpt=include_source_excerpt,
        )

    def inspect_mathlib_declaration(self, repo_root: Path, *, decl_name: str) -> ServiceResult[MathlibNavigationView]:
        return self.toolkit_ingestion.inspect_mathlib_declaration(repo_root, decl_name=decl_name)

    def inspect_mathlib_module(
        self,
        repo_root: Path,
        *,
        module: str | None = None,
        module_name: str | None = None,
        pattern: str | None = None,
        limit: int = 20,
        include_imports: bool = False,
        include_source_excerpt: bool = False,
    ) -> ServiceResult[MathlibModuleNavigationView]:
        return self.toolkit_ingestion.inspect_mathlib_module(
            repo_root,
            module=module_name or module or "",
            pattern=pattern,
            limit=limit,
            include_imports=include_imports,
            include_source_excerpt=include_source_excerpt,
        )

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
        actor_check = self.node_mathlib_use._normalize_actor(actor)
        if not actor_check.ok:
            return self.runtime.foundation.fail(actor_check.issues)
        normalized = self.node_mathlib_use._normalize_dotted_name(
            module,
            field="module",
            issue_prefix="mathlib_module",
        )
        if not normalized.ok or normalized.value is None:
            return self.runtime.foundation.fail(normalized.issues)
        verified = self._verify_hint_module(repo_root, module=normalized.value)
        if not verified.ok or verified.value is None:
            return self.runtime.foundation.fail(verified.issues)
        return self._with_hint_index_transaction(
            repo_root,
            entries=[],
            modules=[verified.value],
            mutate=lambda: self.node_mathlib_use.add_mathlib_module_use(
                repo_root,
                node_path=node_path,
                module=normalized.value,
                reason=reason,
                actor=actor,
            ),
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
        actor_check = self.node_mathlib_use._normalize_actor(actor)
        if not actor_check.ok:
            return self.runtime.foundation.fail(actor_check.issues)
        normalized = self.node_mathlib_use._normalize_dotted_name(
            decl_name,
            field="decl_name",
            issue_prefix="mathlib_decl",
        )
        if not normalized.ok or normalized.value is None:
            return self.runtime.foundation.fail(normalized.issues)
        resolved = self._resolve_hint_decl(repo_root, decl_name=normalized.value)
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        return self._with_hint_index_transaction(
            repo_root,
            entries=[resolved.value],
            modules=[],
            mutate=lambda: self.node_mathlib_use.add_mathlib_decl_use(
                repo_root,
                node_path=node_path,
                decl_name=normalized.value,
                reason=reason,
                actor=actor,
            ),
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
        verified_modules: list[str] = []
        for module, _reason in modules:
            verified = self._verify_hint_module(repo_root, module=module)
            if not verified.ok or verified.value is None:
                return self.runtime.foundation.fail(verified.issues)
            verified_modules.append(verified.value)
        entries: list[MathlibDeclEntryView] = []
        for decl_name, _reason in declarations:
            resolved = self._resolve_hint_decl(repo_root, decl_name=decl_name)
            if not resolved.ok or resolved.value is None:
                return self.runtime.foundation.fail(resolved.issues)
            entries.append(resolved.value)
        return self._with_hint_index_transaction(
            repo_root,
            entries=entries,
            modules=verified_modules,
            mutate=lambda: self.node_mathlib_use.add_mathlib_hints(
                repo_root,
                node_path=node_path,
                modules=modules,
                declarations=declarations,
                actor=actor,
            ),
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
        return self.add_mathlib_module_use(
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
        return self.add_mathlib_decl_use(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            reason=reason,
            actor=actor,
        )

    def _verify_hint_module(
        self,
        repo_root: Path,
        *,
        module: str,
    ) -> ServiceResult[str]:
        existing = self.mathlib_index.get_mathlib_module_entry(repo_root, module=module)
        if existing.ok and existing.value is not None:
            return self.runtime.foundation.ok(existing.value.module)
        checked = self.toolkit_ingestion.check_mathlib_accessible(
            repo_root,
            name_or_module=module,
            target_kind="module",
        )
        if not checked.ok or checked.value is None:
            return self.runtime.foundation.fail(checked.issues)
        if not checked.value.passed:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_module_access_check_failed",
                    "Mathlib module is not accessible from the current repo.",
                    object_ref=module,
                    details={"diagnostics": "\n".join(checked.value.diagnostics)},
                )
            )
        return self.runtime.foundation.ok(module)

    def _resolve_hint_decl(
        self,
        repo_root: Path,
        *,
        decl_name: str,
    ) -> ServiceResult[MathlibDeclEntryView]:
        existing = self.mathlib_index.get_mathlib_decl_entry(repo_root, name=decl_name)
        if (
            existing.ok
            and existing.value is not None
            and existing.value.module is not None
            and existing.value.kind is not None
        ):
            return self.runtime.foundation.ok(existing.value)
        return self.toolkit_ingestion.resolve_mathlib_decl_entry(
            repo_root,
            decl_name=decl_name,
        )

    def _with_hint_index_transaction(
        self,
        repo_root: Path,
        *,
        entries: list[MathlibDeclEntryView],
        modules: list[str],
        mutate: Callable[[], ServiceResult],
    ) -> ServiceResult:
        index_path = self.mathlib_index.index_path(repo_root)
        index_existed = index_path.exists()
        try:
            index_bytes = index_path.read_bytes() if index_existed else None
        except OSError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "mathlib_index_snapshot_failed",
                    f"Failed to snapshot MathlibIndex before hint transaction: {exc}",
                    details={"path": str(index_path)},
                )
            )
        ensured = self.mathlib_index.ensure_mathlib_decl_entries(
            repo_root,
            entries=entries,
            modules=modules,
        )
        if not ensured.ok or ensured.value is None:
            return self.runtime.foundation.fail(ensured.issues)
        mutated = mutate()
        if not mutated.ok or mutated.value is None:
            rollback_issues = self._restore_index(
                path=index_path,
                existed=index_existed,
                contents=index_bytes,
            )
            return self.runtime.foundation.fail([*mutated.issues, *rollback_issues])
        value = mutated.value
        if hasattr(value, "model_copy"):
            value = value.model_copy(
                update={
                    "changed": bool(getattr(value, "changed", False) or ensured.value.changed),
                    "mathlib_index": ensured.value.model_dump(mode="json"),
                }
            )
        return self.runtime.foundation.ok(
            value,
            warnings=[*ensured.issues, *mutated.issues],
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
