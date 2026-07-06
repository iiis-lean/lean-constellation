"""Adapter declaration catalog backed by the common Decl/DeclRevision model."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.interface import DeclKind
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.decl_graph.models import (
    Decl,
    DeclFormalSection,
    DeclGraphStoreView,
    DeclLifecycle,
    DeclNaturalLanguageSection,
    DeclOriginRef,
    DeclProof,
    DeclRevision,
    DeclRevisionChange,
    DeclRevisionStatus,
    DeclState,
    DeclStatement,
    DeclChangeKind,
    RepoDeclDep,
)
from lean_constellation.services.foundation import IssueSeverity, ServiceIssue, ServiceResult, WriteMode
from lean_constellation.services.lean_projection.lean_check import LeanCheckComponent

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


_ADAPTER_NODE_PATH = "Main"
_THEOREM_LIKE = {DeclKind.THEOREM, DeclKind.LEMMA}


class AdapterDeclView(StrictModel):
    decl: Decl
    revision: DeclRevision
    node_path: str = _ADAPTER_NODE_PATH
    name: str
    kind: DeclKind
    module: str
    finalized: bool
    state: str
    plan_summary: str
    upstream_decl_name: str | None = None
    summary: str


class AdapterDeclSummaryView(StrictModel):
    name: str
    kind: DeclKind
    module: str
    finalized: bool
    state: str
    plan_summary: str
    upstream_decl_name: str | None = None


class AdapterModuleSummaryItem(StrictModel):
    module: str
    decl_names: list[str] = Field(default_factory=list)
    finalized_decl_count: int = 0
    kinds: list[str] = Field(default_factory=list)


class AdapterModuleSummaryView(StrictModel):
    modules: list[AdapterModuleSummaryItem] = Field(default_factory=list)
    module_count: int
    summary: str


class AdapterDeclCompletenessIssue(StrictModel):
    decl_name: str
    issue_code: str
    field: str | None = None
    message: str


class AdapterDeclCompletenessView(StrictModel):
    checked_names: list[str] = Field(default_factory=list)
    complete: bool
    issues: list[ServiceIssue] = Field(default_factory=list)
    summary: str


class AdapterDeclMatchView(StrictModel):
    matches: list[AdapterDeclSummaryView] = Field(default_factory=list)
    summary: str


class AdapterCatalogInitView(StrictModel):
    main_catalog_ready: bool
    active_decl_count: int = 0
    summary: str
    issue_code: str | None = None


class AdapterDeclCatalogComponent:
    """Maintain adapter public decls in the root Main common DeclGraph catalog."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        lean_check: LeanCheckComponent | None = None,
    ) -> None:
        self.runtime = runtime
        self.lean_check = lean_check or self.runtime.require_app_service("lean_projection").lean_check

    def ensure_flat_main_catalog(self, repo_root: Path) -> ServiceResult[AdapterCatalogInitView]:
        ensured = self._ensure_catalog_graph(repo_root)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        decls = self._load_all(repo_root)
        if not decls.ok or decls.value is None:
            return self.runtime.foundation.fail(decls.issues)
        active = [decl for decl, _revision in decls.value if decl.lifecycle == DeclLifecycle.ACTIVE]
        return self.runtime.foundation.ok(
            AdapterCatalogInitView(
                main_catalog_ready=True,
                active_decl_count=len(active),
                summary=f"Adapter Main flat catalog is ready with {len(active)} active declarations.",
            )
        )

    def create_adapter_decl(
        self,
        repo_root: Path,
        *,
        name: str,
        kind: str | DeclKind,
        module: str,
        plan_summary: str,
    ) -> ServiceResult[AdapterDeclView]:
        ensured = self._ensure_catalog_graph(repo_root)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        normalized_name = self._safe_decl_name(name)
        if not normalized_name.ok or normalized_name.value is None:
            return self.runtime.foundation.fail(normalized_name.issues)
        normalized_module = self._normalize_module(module)
        if normalized_module is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("adapter_module_invalid", "Adapter decl module must be a valid Lean module name.", field="module")
            )
        try:
            decl_kind = DeclKind(kind)
        except ValueError:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("adapter_decl_kind_invalid", "Adapter decl kind is invalid.", field="kind", current=str(kind))
            )
        if not plan_summary or not plan_summary.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("adapter_decl_plan_summary_required", "Adapter decl plan_summary is required.", field="plan_summary")
            )
        if self._decl_path(repo_root, normalized_name.value).exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("adapter_decl_duplicate", f"Adapter decl already exists: {normalized_name.value}", object_ref=normalized_name.value)
            )
        decl = Decl(
            name=normalized_name.value,
            node_path=_ADAPTER_NODE_PATH,
            kind=decl_kind.value,
            public=True,
            current_revision=1,
            revision_ids=[1],
            module=normalized_module,
            summary=plan_summary.strip(),
        )
        revision = DeclRevision(
            decl_name=normalized_name.value,
            revision=1,
            state=DeclState.PLANNED,
            status=DeclRevisionStatus.OPEN,
            change=DeclRevisionChange(
                kind=DeclChangeKind.CREATE,
                end_after_state=DeclState.PROVED if decl_kind in _THEOREM_LIKE else DeclState.DECLARED,
                objective=plan_summary.strip(),
                summary=plan_summary.strip(),
            ),
            statement=DeclStatement(),
            proof=DeclProof() if decl_kind in _THEOREM_LIKE else None,
            module=normalized_module,
        )
        saved = self._write_new_pair(repo_root, decl, revision)
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self._view(decl, revision, summary=f"Created adapter decl {decl.name}."))

    def set_adapter_statement_formal(
        self,
        repo_root: Path,
        *,
        name: str,
        code: str,
        upstream_decl_name: str | None = None,
    ) -> ServiceResult[AdapterDeclView]:
        loaded = self._load(repo_root, name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        decl, revision = loaded.value
        if not code or not code.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_statement_code_required", "Statement formal code is required.", field="code"))
        scan = self._scan(code)
        if not scan.ok or scan.value is None:
            return self.runtime.foundation.fail(scan.issues)
        forbidden = self._forbidden_statement_occurrences(decl, scan.value)
        if forbidden:
            return self.runtime.foundation.fail(forbidden)
        check = revision.statement.formal.check if revision.statement.formal is not None else None
        revision.statement.formal = DeclFormalSection(
            code=code,
            check=check,
            upstream_decl_name=self._strip(upstream_decl_name),
        )
        revision.updated_at = utc_now_iso()
        return self._save_and_view(repo_root, decl, revision, f"Updated statement formal code for {decl.name}.")

    def set_adapter_statement_nl(
        self,
        repo_root: Path,
        *,
        name: str,
        summary: str,
        detail: str | None = None,
    ) -> ServiceResult[AdapterDeclView]:
        loaded = self._load(repo_root, name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        decl, revision = loaded.value
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_statement_nl_invalid", "Statement NL is invalid: summary must be non-empty"))
        origin = revision.statement.nl.origin if revision.statement.nl is not None else []
        revision.statement.nl = DeclNaturalLanguageSection(text=summary.strip(), detail=self._strip(detail), origin=origin)
        revision.updated_at = utc_now_iso()
        return self._save_and_view(repo_root, decl, revision, f"Updated statement natural language summary for {decl.name}.")

    def add_adapter_statement_origin(
        self,
        repo_root: Path,
        *,
        name: str,
        origin_text: str,
        source_hint: str | None = None,
    ) -> ServiceResult[AdapterDeclView]:
        loaded = self._load(repo_root, name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        return self._add_origin(repo_root, loaded.value[0], loaded.value[1], "statement", origin_text, source_hint)

    def add_adapter_statement_dep(
        self,
        repo_root: Path,
        *,
        name: str,
        dep_name: str,
        reason: str,
    ) -> ServiceResult[AdapterDeclView]:
        loaded = self._load(repo_root, name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        return self._add_dep(repo_root, loaded.value[0], loaded.value[1], "statement", dep_name, reason)

    def remove_adapter_statement_dep(self, repo_root: Path, *, name: str, dep_name: str) -> ServiceResult[AdapterDeclView]:
        loaded = self._load(repo_root, name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        return self._remove_dep(repo_root, loaded.value[0], loaded.value[1], "statement", dep_name)

    def set_adapter_proof_formal(
        self,
        repo_root: Path,
        *,
        name: str,
        code: str,
        upstream_decl_name: str | None = None,
    ) -> ServiceResult[AdapterDeclView]:
        loaded = self._load(repo_root, name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        decl, revision = loaded.value
        if self._decl_kind(decl) not in _THEOREM_LIKE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("adapter_proof_not_applicable", "Proof formal code is only valid for theorem-like adapter declarations.", object_ref=name)
            )
        if not code or not code.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_proof_code_required", "Proof formal code is required.", field="code"))
        scan = self._scan(code)
        if not scan.ok or scan.value is None:
            return self.runtime.foundation.fail(scan.issues)
        forbidden = self._forbidden_proof_occurrences(scan.value, decl=decl)
        if forbidden:
            return self.runtime.foundation.fail(forbidden)
        proof = self._ensure_proof(revision)
        check = proof.formal.check if proof.formal is not None else None
        proof.formal = DeclFormalSection(
            code=code,
            check=check,
            upstream_decl_name=self._strip(upstream_decl_name),
        )
        revision.updated_at = utc_now_iso()
        return self._save_and_view(repo_root, decl, revision, f"Updated proof formal code for {decl.name}.")

    def set_adapter_proof_nl(
        self,
        repo_root: Path,
        *,
        name: str,
        summary: str,
        detail: str | None = None,
    ) -> ServiceResult[AdapterDeclView]:
        loaded = self._load(repo_root, name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        decl, revision = loaded.value
        if self._decl_kind(decl) not in _THEOREM_LIKE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("adapter_proof_not_applicable", "Proof NL is only valid for theorem-like adapter declarations.", object_ref=name)
            )
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_proof_nl_invalid", "Proof NL is invalid: summary must be non-empty"))
        proof = self._ensure_proof(revision)
        origin = proof.nl.origin if proof.nl is not None else []
        proof.nl = DeclNaturalLanguageSection(text=summary.strip(), detail=self._strip(detail), origin=origin)
        revision.updated_at = utc_now_iso()
        return self._save_and_view(repo_root, decl, revision, f"Updated proof natural language summary for {decl.name}.")

    def add_adapter_proof_origin(
        self,
        repo_root: Path,
        *,
        name: str,
        origin_text: str,
        source_hint: str | None = None,
    ) -> ServiceResult[AdapterDeclView]:
        loaded = self._load(repo_root, name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        decl, revision = loaded.value
        if self._decl_kind(decl) not in _THEOREM_LIKE:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_proof_not_applicable", "Proof origin is only valid for theorem-like declarations.", object_ref=name))
        self._ensure_proof(revision)
        return self._add_origin(repo_root, decl, revision, "proof", origin_text, source_hint)

    def add_adapter_proof_dep(
        self,
        repo_root: Path,
        *,
        name: str,
        dep_name: str,
        reason: str,
    ) -> ServiceResult[AdapterDeclView]:
        loaded = self._load(repo_root, name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        decl, revision = loaded.value
        if self._decl_kind(decl) not in _THEOREM_LIKE:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_proof_not_applicable", "Proof dep is only valid for theorem-like declarations.", object_ref=name))
        self._ensure_proof(revision)
        return self._add_dep(repo_root, decl, revision, "proof", dep_name, reason)

    def remove_adapter_proof_dep(self, repo_root: Path, *, name: str, dep_name: str) -> ServiceResult[AdapterDeclView]:
        loaded = self._load(repo_root, name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        decl, revision = loaded.value
        if revision.proof is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_proof_dep_missing", "Proof deps are not initialized.", object_ref=name))
        return self._remove_dep(repo_root, decl, revision, "proof", dep_name)

    def finalize_adapter_decl(self, repo_root: Path, *, name: str) -> ServiceResult[AdapterDeclView]:
        loaded = self._load(repo_root, name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        decl, revision = loaded.value
        completeness = self.check_adapter_decl_completeness(repo_root, name=decl.name)
        if not completeness.ok or completeness.value is None:
            return self.runtime.foundation.fail(completeness.issues)
        if not completeness.value.complete:
            return self.runtime.foundation.fail(completeness.value.issues)
        decl_kind = self._decl_kind(decl)
        code = revision.proof.formal.code if decl_kind in _THEOREM_LIKE and revision.proof and revision.proof.formal else revision.statement.formal.code  # type: ignore[union-attr]
        lean_check = self.lean_check.build_trusted_adapter_check(
            repo_root,
            module=self._module(decl, revision),
            code=code,
            theorem_like=decl_kind in _THEOREM_LIKE,
        )
        if not lean_check.ok or lean_check.value is None:
            return self.runtime.foundation.fail(lean_check.issues)
        if lean_check.value.status != "passed":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "adapter_trusted_check_failed",
                    lean_check.value.message,
                    object_ref=decl.name,
                )
            )
        if decl_kind in _THEOREM_LIKE:
            proof = self._ensure_proof(revision)
            assert proof.formal is not None
            proof.formal = proof.formal.model_copy(update={"check": lean_check.value})
            revision.state = DeclState.PROVED
        else:
            assert revision.statement.formal is not None
            revision.statement.formal = revision.statement.formal.model_copy(update={"check": lean_check.value})
            revision.state = DeclState.DECLARED
        revision.status = DeclRevisionStatus.COMMITTED
        revision.updated_at = utc_now_iso()
        decl.current_revision = revision.revision
        decl.updated_at = utc_now_iso()
        return self._save_and_view(repo_root, decl, revision, f"Finalized adapter decl {decl.name}.")

    def list_adapter_decls(
        self,
        repo_root: Path,
        *,
        module_filter: str | None = None,
        kind_filter: str | None = None,
        name_query: str | None = None,
    ) -> ServiceResult[list[AdapterDeclSummaryView]]:
        loaded = self._load_all(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        try:
            kind = DeclKind(kind_filter) if kind_filter else None
        except ValueError:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "adapter_decl_kind_filter_invalid",
                    "Adapter decl kind_filter is invalid.",
                    field="kind_filter",
                    current=str(kind_filter),
                )
            )
        module_value = self._strip(module_filter)
        query = self._strip(name_query)
        items = []
        for decl, revision in loaded.value:
            if decl.lifecycle != DeclLifecycle.ACTIVE:
                continue
            module = self._module(decl, revision)
            decl_kind = self._decl_kind(decl)
            if module_value and module != module_value:
                continue
            if kind and decl_kind != kind:
                continue
            if query and query.lower() not in decl.name.lower():
                continue
            items.append(self._summary(decl, revision))
        items.sort(key=lambda item: (item.module, item.name))
        return self.runtime.foundation.ok(items)

    def inspect_adapter_decl(self, repo_root: Path, *, name: str) -> ServiceResult[AdapterDeclView]:
        loaded = self._load(repo_root, name)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        return self.runtime.foundation.ok(self._view(loaded.value[0], loaded.value[1], summary=f"Loaded adapter decl {loaded.value[0].name}."))

    def list_registered_adapter_modules(self, repo_root: Path) -> ServiceResult[AdapterModuleSummaryView]:
        loaded = self._load_all(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        by_module: dict[str, list[tuple[Decl, DeclRevision]]] = {}
        for decl, revision in loaded.value:
            if decl.lifecycle == DeclLifecycle.ACTIVE and self._finalized(decl, revision):
                by_module.setdefault(self._module(decl, revision), []).append((decl, revision))
        modules = [
            AdapterModuleSummaryItem(
                module=module,
                decl_names=sorted(decl.name for decl, _revision in records),
                finalized_decl_count=len(records),
                kinds=sorted({self._decl_kind(decl).value for decl, _revision in records}),
            )
            for module, records in sorted(by_module.items())
        ]
        return self.runtime.foundation.ok(
            AdapterModuleSummaryView(
                modules=modules,
                module_count=len(modules),
                summary=f"Registered {len(modules)} adapter modules.",
            )
        )

    def check_adapter_decl_completeness(self, repo_root: Path, *, name: str | None = None) -> ServiceResult[AdapterDeclCompletenessView]:
        if name is not None:
            loaded_one = self._load(repo_root, name)
            if not loaded_one.ok or loaded_one.value is None:
                return self.runtime.foundation.fail(loaded_one.issues)
            records = [loaded_one.value]
        else:
            loaded = self._load_all(repo_root)
            if not loaded.ok or loaded.value is None:
                return self.runtime.foundation.fail(loaded.issues)
            records = [(decl, revision) for decl, revision in loaded.value if decl.lifecycle == DeclLifecycle.ACTIVE]
        issues: list[ServiceIssue] = []
        for decl, revision in records:
            issues.extend(self._record_issues(decl, revision))
        return self.runtime.foundation.ok(
            AdapterDeclCompletenessView(
                checked_names=[decl.name for decl, _revision in records],
                complete=not issues,
                issues=issues,
                summary=("Adapter decl completeness checks passed." if not issues else f"{len(issues)} adapter decl completeness issues found."),
            )
        )

    def find_adapter_decl_by_upstream(
        self,
        repo_root: Path,
        *,
        module: str,
        upstream_decl_name: str | None = None,
        adapter_name_query: str | None = None,
    ) -> ServiceResult[AdapterDeclMatchView]:
        normalized_module = self._normalize_module(module)
        if normalized_module is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_module_invalid", "Adapter module is invalid.", field="module"))
        loaded = self._load_all(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        upstream = self._strip(upstream_decl_name)
        query = self._strip(adapter_name_query)
        matches = []
        for decl, revision in loaded.value:
            if self._module(decl, revision) != normalized_module:
                continue
            upstream_names = {
                item
                for item in [
                    revision.statement.formal.upstream_decl_name if revision.statement.formal else None,
                    revision.proof.formal.upstream_decl_name if revision.proof and revision.proof.formal else None,
                ]
                if item
            }
            if upstream and upstream in upstream_names:
                matches.append(self._summary(decl, revision))
                continue
            if query and query.lower() in decl.name.lower():
                matches.append(self._summary(decl, revision))
        return self.runtime.foundation.ok(
            AdapterDeclMatchView(
                matches=sorted(matches, key=lambda item: item.name),
                summary=f"Found {len(matches)} adapter decl matches.",
            )
        )

    def _add_origin(
        self,
        repo_root: Path,
        decl: Decl,
        revision: DeclRevision,
        stage: str,
        origin_text: str,
        source_hint: str | None,
    ) -> ServiceResult[AdapterDeclView]:
        if not origin_text or not origin_text.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_origin_invalid", "Adapter origin is invalid: origin_text must be non-empty"))
        origin_ref = origin_text.strip()
        source = self._strip(source_hint)
        if source:
            origin_ref = f"{source}: {origin_ref}"
        origin = DeclOriginRef(kind="adapter_origin", ref=origin_ref)
        section = revision.statement if stage == "statement" else self._ensure_proof(revision)
        nl = section.nl or DeclNaturalLanguageSection()
        if any(item.model_dump(mode="json") == origin.model_dump(mode="json") for item in nl.origin):
            warning = self.runtime.foundation.issue(
                "adapter_origin_duplicate",
                "Adapter origin is already recorded.",
                severity=IssueSeverity.WARNING,
                object_ref=decl.name,
            )
            return self.runtime.foundation.ok(self._view(decl, revision, summary=f"{stage.title()} origin already recorded."), warnings=[warning])
        nl.origin.append(origin)
        section.nl = nl
        revision.updated_at = utc_now_iso()
        return self._save_and_view(repo_root, decl, revision, f"Added {stage} origin for {decl.name}.")

    def _add_dep(
        self,
        repo_root: Path,
        decl: Decl,
        revision: DeclRevision,
        stage: str,
        dep_name: str,
        reason: str,
    ) -> ServiceResult[AdapterDeclView]:
        dep_key = self._safe_decl_name(dep_name)
        if not dep_key.ok or dep_key.value is None:
            return self.runtime.foundation.fail(dep_key.issues)
        dep = self._load(repo_root, dep_key.value)
        if not dep.ok or dep.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("adapter_dep_missing", f"Adapter dependency does not exist: {dep_key.value}", object_ref=decl.name)
            )
        if dep_key.value == decl.name:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_dep_self", "Adapter declaration cannot depend on itself.", object_ref=decl.name))
        if not reason or not reason.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_dep_reason_required", "Adapter dependency reason is required.", field="reason"))
        section = revision.statement if stage == "statement" else self._ensure_proof(revision)
        if dep_key.value in self._dep_names(section.deps):
            warning = self.runtime.foundation.issue("adapter_dep_duplicate", "Adapter dependency is already recorded.", severity=IssueSeverity.WARNING, object_ref=decl.name)
            return self.runtime.foundation.ok(self._view(decl, revision, summary=f"{stage.title()} dependency already recorded."), warnings=[warning])
        section.deps.append(RepoDeclDep(ref=DeclRef(repo=None, node=_ADAPTER_NODE_PATH, name=dep_key.value, revision=1), reason=reason.strip()))
        section.deps = sorted(section.deps, key=lambda item: self._dep_key(item))
        revision.updated_at = utc_now_iso()
        return self._save_and_view(repo_root, decl, revision, f"Added {stage} dependency for {decl.name}.")

    def _remove_dep(
        self,
        repo_root: Path,
        decl: Decl,
        revision: DeclRevision,
        stage: str,
        dep_name: str,
    ) -> ServiceResult[AdapterDeclView]:
        section = revision.statement if stage == "statement" else self._ensure_proof(revision)
        if dep_name not in self._dep_names(section.deps):
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_dep_not_found", f"{stage.title()} dependency was not found.", object_ref=decl.name, field=dep_name))
        section.deps = [item for item in section.deps if self._dep_name(item) != dep_name]
        revision.updated_at = utc_now_iso()
        return self._save_and_view(repo_root, decl, revision, f"Removed {stage} dependency for {decl.name}.")

    def _record_issues(self, decl: Decl, revision: DeclRevision) -> list[ServiceIssue]:
        issues: list[ServiceIssue] = []
        if not self._module(decl, revision):
            issues.append(self._issue(decl, "adapter_decl_module_missing", "module", "Adapter decl module is missing."))
        if revision.statement.formal is None or not revision.statement.formal.code:
            issues.append(self._issue(decl, "adapter_statement_formal_missing", "statement.formal", "Statement formal code is missing."))
        if revision.statement.nl is None or not revision.statement.nl.text:
            issues.append(self._issue(decl, "adapter_statement_nl_missing", "statement.nl", "Statement natural language summary is missing."))
        if revision.statement.formal is not None and revision.statement.formal.code is not None:
            scan = self._scan(revision.statement.formal.code)
            if scan.ok and scan.value is not None:
                issues.extend(self._forbidden_statement_occurrences(decl, scan.value))
        if self._decl_kind(decl) in _THEOREM_LIKE:
            if revision.proof is None or revision.proof.formal is None or not revision.proof.formal.code:
                issues.append(self._issue(decl, "adapter_proof_formal_missing", "proof.formal", "Proof formal code is missing."))
            if revision.proof is None or revision.proof.nl is None or not revision.proof.nl.text:
                issues.append(self._issue(decl, "adapter_proof_nl_missing", "proof.nl", "Proof natural language summary is missing."))
            if revision.proof and revision.proof.formal and revision.proof.formal.code:
                scan = self._scan(revision.proof.formal.code)
                if scan.ok and scan.value is not None:
                    issues.extend(self._forbidden_proof_occurrences(scan.value, decl=decl))
        return issues

    def _forbidden_statement_occurrences(self, decl: Decl, scan: object) -> list[ServiceIssue]:
        contains_sorry = bool(getattr(scan, "contains_sorry", False))
        contains_admit = bool(getattr(scan, "contains_admit", False))
        contains_axiom = bool(getattr(scan, "contains_axiom", False))
        contains_opaque = bool(getattr(scan, "contains_opaque", False))
        contains_unsafe = bool(getattr(scan, "contains_unsafe", False))
        issues = []
        if contains_admit or contains_axiom or contains_opaque or contains_unsafe:
            issues.append(self._issue(decl, "adapter_statement_forbidden_construct", "statement.formal", "Statement formal code contains admit/axiom/opaque/unsafe."))
        if self._decl_kind(decl) not in _THEOREM_LIKE and contains_sorry:
            issues.append(self._issue(decl, "adapter_statement_sorry_forbidden", "statement.formal", "Non-theorem adapter statement cannot contain sorry."))
        return issues

    def _forbidden_proof_occurrences(self, scan: object, *, decl: Decl | None = None) -> list[ServiceIssue]:
        forbidden = any(
            bool(getattr(scan, field, False))
            for field in ["contains_sorry", "contains_admit", "contains_axiom", "contains_opaque", "contains_unsafe"]
        )
        if not forbidden:
            return []
        return [
            self.runtime.foundation.issue(
                "adapter_proof_forbidden_construct",
                "Proof formal code contains sorry/admit/axiom/opaque/unsafe.",
                object_ref=decl.name if decl is not None else None,
                field="proof.formal",
            )
        ]

    def _scan(self, code: str) -> ServiceResult[object]:
        return self.lean_check.detect_sorry_axiom(code)

    def _issue(self, decl: Decl, kind: str, field: str, message: str) -> ServiceIssue:
        return self.runtime.foundation.issue(kind, message, object_ref=decl.name, field=field)

    def _ensure_catalog_graph(self, repo_root: Path) -> ServiceResult[DeclGraphStoreView]:
        root = self.runtime.node.node_tree.ensure_root_scope_node(repo_root)
        if not root.ok:
            return self.runtime.foundation.fail(root.issues)
        return self.runtime.decl_graph.ensure_decl_graph(repo_root, node_path=_ADAPTER_NODE_PATH)

    def _load(self, repo_root: Path, name: str) -> ServiceResult[tuple[Decl, DeclRevision]]:
        safe = self._safe_decl_name(name)
        if not safe.ok or safe.value is None:
            return self.runtime.foundation.fail(safe.issues)
        ensured = self._ensure_catalog_graph(repo_root)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        path = self._decl_path(repo_root, safe.value)
        if not path.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("adapter_decl_missing", f"Adapter decl is missing: {safe.value}", object_ref=safe.value)
            )
        decl = self.runtime.foundation.store.read_json(path, Decl)
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        revision = self.runtime.foundation.store.read_json(self._revision_path(repo_root, decl.value.name, decl.value.current_revision), DeclRevision)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        return self.runtime.foundation.ok((decl.value, revision.value))

    def _load_all(self, repo_root: Path) -> ServiceResult[list[tuple[Decl, DeclRevision]]]:
        ensured = self._ensure_catalog_graph(repo_root)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        decls = self.runtime.decl_graph.list_decls(repo_root, node_path=_ADAPTER_NODE_PATH)
        if not decls.ok or decls.value is None:
            return self.runtime.foundation.fail(decls.issues)
        pairs: list[tuple[Decl, DeclRevision]] = []
        issues = []
        for decl in decls.value:
            revision = self.runtime.foundation.store.read_json(self._revision_path(repo_root, decl.name, decl.current_revision), DeclRevision)
            if revision.ok and revision.value is not None:
                pairs.append((decl, revision.value))
            else:
                issues.extend(revision.issues)
        if issues:
            return self.runtime.foundation.fail(issues)
        return self.runtime.foundation.ok(sorted(pairs, key=lambda item: item[0].name))

    def _write_new_pair(self, repo_root: Path, decl: Decl, revision: DeclRevision) -> ServiceResult[tuple[Decl, DeclRevision]]:
        ensured = self.runtime.foundation.store.ensure_dir(
            self.runtime.decl_graph.graph_store.decl_revisions_dir(repo_root, node_path=_ADAPTER_NODE_PATH, decl_name=decl.name)
        )
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        written_decl = self.runtime.foundation.store.write_json_atomic(self._decl_path(repo_root, decl.name), decl, mode=WriteMode.CREATE_ONLY)
        if not written_decl.ok:
            return self.runtime.foundation.fail(written_decl.issues)
        written_revision = self.runtime.foundation.store.write_json_atomic(self._revision_path(repo_root, decl.name, revision.revision), revision, mode=WriteMode.CREATE_ONLY)
        if not written_revision.ok:
            return self.runtime.foundation.fail(written_revision.issues)
        rebuilt = self.runtime.decl_graph.rebuild_decl_graph_index(repo_root, node_path=_ADAPTER_NODE_PATH)
        if not rebuilt.ok:
            return self.runtime.foundation.fail(rebuilt.issues)
        return self.runtime.foundation.ok((decl, revision))

    def _save_and_view(self, repo_root: Path, decl: Decl, revision: DeclRevision, summary: str) -> ServiceResult[AdapterDeclView]:
        decl.updated_at = utc_now_iso()
        revision.updated_at = utc_now_iso()
        written_decl = self.runtime.foundation.store.write_json_atomic(self._decl_path(repo_root, decl.name), decl, mode=WriteMode.UPDATE_EXISTING)
        if not written_decl.ok:
            return self.runtime.foundation.fail(written_decl.issues)
        written_revision = self.runtime.foundation.store.write_json_atomic(
            self._revision_path(repo_root, revision.decl_name, revision.revision),
            revision,
            mode=WriteMode.UPDATE_EXISTING,
        )
        if not written_revision.ok:
            return self.runtime.foundation.fail(written_revision.issues)
        rebuilt = self.runtime.decl_graph.rebuild_decl_graph_index(repo_root, node_path=_ADAPTER_NODE_PATH)
        if not rebuilt.ok:
            return self.runtime.foundation.fail(rebuilt.issues)
        return self.runtime.foundation.ok(self._view(decl, revision, summary=summary))

    def _decl_path(self, repo_root: Path, name: str) -> Path:
        return self.runtime.decl_graph.graph_store.decl_record_path(repo_root, node_path=_ADAPTER_NODE_PATH, decl_name=name)

    def _revision_path(self, repo_root: Path, name: str, revision: int) -> Path:
        return self.runtime.decl_graph.graph_store.revision_path(repo_root, node_path=_ADAPTER_NODE_PATH, decl_name=name, revision=revision)

    def _safe_decl_name(self, name: str) -> ServiceResult[str]:
        try:
            return self.runtime.foundation.ok(self.runtime.foundation.layout.ensure_safe_key(name.strip()))
        except Exception as exc:  # noqa: BLE001
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_decl_name_invalid", f"Adapter decl name is invalid: {exc}", field="name"))

    def _normalize_module(self, module: str) -> str | None:
        value = module.strip()
        if not value or any(ch.isspace() for ch in value):
            return None
        if "/" in value or "\\" in value or ".." in value:
            return None
        if any(not part for part in value.split(".")):
            return None
        return value

    def _view(self, decl: Decl, revision: DeclRevision, *, summary: str) -> AdapterDeclView:
        return AdapterDeclView(
            decl=decl,
            revision=revision,
            node_path=_ADAPTER_NODE_PATH,
            name=decl.name,
            kind=self._decl_kind(decl),
            module=self._module(decl, revision),
            finalized=self._finalized(decl, revision),
            state=self._adapter_state(revision),
            plan_summary=self._plan_summary(decl, revision),
            upstream_decl_name=self._upstream_decl_name(revision),
            summary=summary,
        )

    def _summary(self, decl: Decl, revision: DeclRevision) -> AdapterDeclSummaryView:
        return AdapterDeclSummaryView(
            name=decl.name,
            kind=self._decl_kind(decl),
            module=self._module(decl, revision),
            finalized=self._finalized(decl, revision),
            state=self._adapter_state(revision),
            plan_summary=self._plan_summary(decl, revision),
            upstream_decl_name=self._upstream_decl_name(revision),
        )

    def _decl_kind(self, decl: Decl) -> DeclKind:
        return DeclKind(decl.kind)

    def _module(self, decl: Decl, revision: DeclRevision) -> str:
        return revision.module or decl.module or ""

    def _plan_summary(self, decl: Decl, revision: DeclRevision) -> str:
        if revision.change and revision.change.objective:
            return revision.change.objective
        return decl.summary or ""

    def _upstream_decl_name(self, revision: DeclRevision) -> str | None:
        if revision.proof and revision.proof.formal and revision.proof.formal.upstream_decl_name:
            return revision.proof.formal.upstream_decl_name
        if revision.statement.formal and revision.statement.formal.upstream_decl_name:
            return revision.statement.formal.upstream_decl_name
        return None

    def _adapter_state(self, revision: DeclRevision) -> str:
        if revision.state in {DeclState.PLANNED, DeclState.SPECIFIED, DeclState.PROOF_PLANNED}:
            return "draft"
        return revision.state.value

    def _finalized(self, decl: Decl, revision: DeclRevision) -> bool:
        return (
            decl.lifecycle == DeclLifecycle.ACTIVE
            and revision.status == DeclRevisionStatus.COMMITTED
            and revision.state in {DeclState.DECLARED, DeclState.PROVED}
        )

    def _ensure_proof(self, revision: DeclRevision) -> DeclProof:
        if revision.proof is None:
            revision.proof = DeclProof()
        return revision.proof

    def _dep_names(self, deps: list[object]) -> list[str]:
        return [self._dep_name(dep) for dep in deps]

    def _dep_name(self, dep: object) -> str:
        if isinstance(dep, RepoDeclDep):
            return dep.ref.name
        if isinstance(dep, str):
            return dep
        name = getattr(getattr(dep, "ref", None), "name", None)
        return str(name) if name else str(dep)

    def _dep_key(self, dep: object) -> str:
        if isinstance(dep, RepoDeclDep):
            return f"{dep.ref.repo or ''}:{dep.ref.node}:{dep.ref.name}:{dep.ref.revision}"
        return self._dep_name(dep)

    @staticmethod
    def _strip(value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None
