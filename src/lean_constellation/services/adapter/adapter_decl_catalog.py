"""Flat adapter declaration catalog under root Main."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.interface import DeclKind
from lean_constellation.services.foundation import (
    FoundationContext,
    FoundationService,
    IssueSeverity,
    ServiceIssue,
    ServiceResult,
    WriteMode,
)
from lean_constellation.services.lean_projection.lean_check import LeanCheckComponent, LeanCheckView


_THEOREM_LIKE = {DeclKind.THEOREM, DeclKind.LEMMA}


class AdapterNaturalLanguageContent(StrictModel):
    summary: str
    detail: str | None = None

    @field_validator("summary")
    @classmethod
    def _summary_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("summary must be non-empty")
        return value.strip()


class AdapterFormalContent(StrictModel):
    code: str
    upstream_decl_name: str | None = None

    @field_validator("code")
    @classmethod
    def _code_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("code must be non-empty")
        return value


class AdapterOrigin(StrictModel):
    origin_text: str
    source_hint: str | None = None

    @field_validator("origin_text")
    @classmethod
    def _origin_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("origin_text must be non-empty")
        return value.strip()


class AdapterDeclDep(StrictModel):
    dep_name: str
    reason: str

    @field_validator("dep_name", "reason")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("value must be non-empty")
        return value.strip()


class AdapterStatementRecord(StrictModel):
    formal: AdapterFormalContent | None = None
    nl: AdapterNaturalLanguageContent | None = None
    origins: list[AdapterOrigin] = Field(default_factory=list)
    deps: list[AdapterDeclDep] = Field(default_factory=list)


class AdapterProofRecord(StrictModel):
    formal: AdapterFormalContent | None = None
    nl: AdapterNaturalLanguageContent | None = None
    origins: list[AdapterOrigin] = Field(default_factory=list)
    deps: list[AdapterDeclDep] = Field(default_factory=list)


class AdapterDeclRecord(StrictModel):
    name: str
    kind: DeclKind
    module: str
    plan_summary: str
    node: str = "Main"
    revision: int = 1
    graph_round: int = 1
    public: bool = True
    active: bool = True
    finalized: bool = False
    state: Literal["draft", "declared", "proved"] = "draft"
    statement: AdapterStatementRecord = Field(default_factory=AdapterStatementRecord)
    proof: AdapterProofRecord | None = None
    lean_check: LeanCheckView | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class AdapterDeclView(StrictModel):
    record: AdapterDeclRecord
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
    """Maintain adapter public decl records as flat Main catalog."""

    def __init__(
        self,
        foundation: FoundationService | None = None,
        lean_check: LeanCheckComponent | None = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.lean_check = lean_check or LeanCheckComponent(self.foundation)

    def ensure_flat_main_catalog(self, repo_root: Path) -> ServiceResult[AdapterCatalogInitView]:
        root = self._decls_root(repo_root)
        ensured = self.foundation.store.ensure_dir(root)
        if not ensured.ok:
            return self.foundation.fail(ensured.issues)
        decls = self._load_all(repo_root)
        if not decls.ok or decls.value is None:
            return self.foundation.fail(decls.issues)
        active = [decl for decl in decls.value if decl.active]
        return self.foundation.ok(
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
        normalized_name = self._safe_decl_name(name)
        if not normalized_name.ok or normalized_name.value is None:
            return self.foundation.fail(normalized_name.issues)
        normalized_module = self._normalize_module(module)
        if normalized_module is None:
            return self.foundation.fail(
                self.foundation.issue("adapter_module_invalid", "Adapter decl module must be a valid Lean module name.", field="module")
            )
        try:
            decl_kind = DeclKind(kind)
        except ValueError:
            return self.foundation.fail(
                self.foundation.issue("adapter_decl_kind_invalid", "Adapter decl kind is invalid.", field="kind", current=str(kind))
            )
        if not plan_summary or not plan_summary.strip():
            return self.foundation.fail(
                self.foundation.issue("adapter_decl_plan_summary_required", "Adapter decl plan_summary is required.", field="plan_summary")
            )
        path = self._decl_path(repo_root, normalized_name.value)
        if path.exists():
            return self.foundation.fail(
                self.foundation.issue("adapter_decl_duplicate", f"Adapter decl already exists: {normalized_name.value}", object_ref=normalized_name.value)
            )
        record = AdapterDeclRecord(
            name=normalized_name.value,
            kind=decl_kind,
            module=normalized_module,
            plan_summary=plan_summary.strip(),
            proof=AdapterProofRecord() if decl_kind in _THEOREM_LIKE else None,
        )
        saved = self.foundation.store.write_json_atomic(path, record, mode=WriteMode.CREATE_ONLY)
        if not saved.ok:
            return self.foundation.fail(saved.issues)
        return self.foundation.ok(self._view(record, summary=f"Created adapter decl {record.name}."))

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
            return self.foundation.fail(loaded.issues)
        if not code or not code.strip():
            return self.foundation.fail(self.foundation.issue("adapter_statement_code_required", "Statement formal code is required.", field="code"))
        scan = self._scan(code)
        if not scan.ok or scan.value is None:
            return self.foundation.fail(scan.issues)
        forbidden = self._forbidden_statement_occurrences(loaded.value, scan.value)
        if forbidden:
            return self.foundation.fail(forbidden)
        loaded.value.statement.formal = AdapterFormalContent(code=code, upstream_decl_name=self._strip(upstream_decl_name))
        return self._save_and_view(repo_root, loaded.value, f"Updated statement formal code for {loaded.value.name}.")

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
            return self.foundation.fail(loaded.issues)
        try:
            loaded.value.statement.nl = AdapterNaturalLanguageContent(summary=summary, detail=self._strip(detail))
        except Exception as exc:  # noqa: BLE001
            return self.foundation.fail(self.foundation.issue("adapter_statement_nl_invalid", f"Statement NL is invalid: {exc}"))
        return self._save_and_view(repo_root, loaded.value, f"Updated statement natural language summary for {loaded.value.name}.")

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
            return self.foundation.fail(loaded.issues)
        return self._add_origin(repo_root, loaded.value, loaded.value.statement.origins, origin_text, source_hint, "statement")

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
            return self.foundation.fail(loaded.issues)
        return self._add_dep(repo_root, loaded.value, loaded.value.statement.deps, dep_name, reason, "statement")

    def remove_adapter_statement_dep(self, repo_root: Path, *, name: str, dep_name: str) -> ServiceResult[AdapterDeclView]:
        loaded = self._load(repo_root, name)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        return self._remove_dep(repo_root, loaded.value, loaded.value.statement.deps, dep_name, "statement")

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
            return self.foundation.fail(loaded.issues)
        if loaded.value.kind not in _THEOREM_LIKE:
            return self.foundation.fail(
                self.foundation.issue("adapter_proof_not_applicable", "Proof formal code is only valid for theorem-like adapter declarations.", object_ref=name)
            )
        if not code or not code.strip():
            return self.foundation.fail(self.foundation.issue("adapter_proof_code_required", "Proof formal code is required.", field="code"))
        scan = self._scan(code)
        if not scan.ok or scan.value is None:
            return self.foundation.fail(scan.issues)
        forbidden = self._forbidden_proof_occurrences(scan.value)
        if forbidden:
            return self.foundation.fail(forbidden)
        loaded.value.proof = loaded.value.proof or AdapterProofRecord()
        loaded.value.proof.formal = AdapterFormalContent(code=code, upstream_decl_name=self._strip(upstream_decl_name))
        return self._save_and_view(repo_root, loaded.value, f"Updated proof formal code for {loaded.value.name}.")

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
            return self.foundation.fail(loaded.issues)
        if loaded.value.kind not in _THEOREM_LIKE:
            return self.foundation.fail(
                self.foundation.issue("adapter_proof_not_applicable", "Proof NL is only valid for theorem-like adapter declarations.", object_ref=name)
            )
        try:
            loaded.value.proof = loaded.value.proof or AdapterProofRecord()
            loaded.value.proof.nl = AdapterNaturalLanguageContent(summary=summary, detail=self._strip(detail))
        except Exception as exc:  # noqa: BLE001
            return self.foundation.fail(self.foundation.issue("adapter_proof_nl_invalid", f"Proof NL is invalid: {exc}"))
        return self._save_and_view(repo_root, loaded.value, f"Updated proof natural language summary for {loaded.value.name}.")

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
            return self.foundation.fail(loaded.issues)
        if loaded.value.kind not in _THEOREM_LIKE:
            return self.foundation.fail(self.foundation.issue("adapter_proof_not_applicable", "Proof origin is only valid for theorem-like declarations.", object_ref=name))
        loaded.value.proof = loaded.value.proof or AdapterProofRecord()
        return self._add_origin(repo_root, loaded.value, loaded.value.proof.origins, origin_text, source_hint, "proof")

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
            return self.foundation.fail(loaded.issues)
        if loaded.value.kind not in _THEOREM_LIKE:
            return self.foundation.fail(self.foundation.issue("adapter_proof_not_applicable", "Proof dep is only valid for theorem-like declarations.", object_ref=name))
        loaded.value.proof = loaded.value.proof or AdapterProofRecord()
        return self._add_dep(repo_root, loaded.value, loaded.value.proof.deps, dep_name, reason, "proof")

    def remove_adapter_proof_dep(self, repo_root: Path, *, name: str, dep_name: str) -> ServiceResult[AdapterDeclView]:
        loaded = self._load(repo_root, name)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        if loaded.value.proof is None:
            return self.foundation.fail(self.foundation.issue("adapter_proof_dep_missing", "Proof deps are not initialized.", object_ref=name))
        return self._remove_dep(repo_root, loaded.value, loaded.value.proof.deps, dep_name, "proof")

    def finalize_adapter_decl(self, repo_root: Path, *, name: str) -> ServiceResult[AdapterDeclView]:
        loaded = self._load(repo_root, name)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        completeness = self.check_adapter_decl_completeness(repo_root, name=loaded.value.name)
        if not completeness.ok or completeness.value is None:
            return self.foundation.fail(completeness.issues)
        if not completeness.value.complete:
            return self.foundation.fail(completeness.value.issues)
        code = loaded.value.proof.formal.code if loaded.value.kind in _THEOREM_LIKE and loaded.value.proof and loaded.value.proof.formal else loaded.value.statement.formal.code  # type: ignore[union-attr]
        lean_check = self.lean_check.build_trusted_adapter_check(
            repo_root,
            module=loaded.value.module,
            code=code,
            theorem_like=loaded.value.kind in _THEOREM_LIKE,
        )
        if not lean_check.ok or lean_check.value is None:
            return self.foundation.fail(lean_check.issues)
        if lean_check.value.status != "passed":
            return self.foundation.fail(
                self.foundation.issue(
                    "adapter_trusted_check_failed",
                    lean_check.value.message,
                    object_ref=loaded.value.name,
                )
            )
        loaded.value.finalized = True
        loaded.value.state = "proved" if loaded.value.kind in _THEOREM_LIKE else "declared"
        loaded.value.lean_check = lean_check.value
        return self._save_and_view(repo_root, loaded.value, f"Finalized adapter decl {loaded.value.name}.")

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
            return self.foundation.fail(loaded.issues)
        try:
            kind = DeclKind(kind_filter) if kind_filter else None
        except ValueError:
            return self.foundation.fail(
                self.foundation.issue(
                    "adapter_decl_kind_filter_invalid",
                    "Adapter decl kind_filter is invalid.",
                    field="kind_filter",
                    current=str(kind_filter),
                )
            )
        module_value = self._strip(module_filter)
        query = self._strip(name_query)
        items = []
        for record in loaded.value:
            if module_value and record.module != module_value:
                continue
            if kind and record.kind != kind:
                continue
            if query and query.lower() not in record.name.lower():
                continue
            items.append(self._summary(record))
        items.sort(key=lambda item: (item.module, item.name))
        return self.foundation.ok(items)

    def inspect_adapter_decl(self, repo_root: Path, *, name: str) -> ServiceResult[AdapterDeclView]:
        loaded = self._load(repo_root, name)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        return self.foundation.ok(self._view(loaded.value, summary=f"Loaded adapter decl {loaded.value.name}."))

    def list_registered_adapter_modules(self, repo_root: Path) -> ServiceResult[AdapterModuleSummaryView]:
        loaded = self._load_all(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        by_module: dict[str, list[AdapterDeclRecord]] = {}
        for record in loaded.value:
            if record.active and record.finalized:
                by_module.setdefault(record.module, []).append(record)
        modules = [
            AdapterModuleSummaryItem(
                module=module,
                decl_names=sorted(record.name for record in records),
                finalized_decl_count=len(records),
                kinds=sorted({record.kind.value for record in records}),
            )
            for module, records in sorted(by_module.items())
        ]
        return self.foundation.ok(
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
                return self.foundation.fail(loaded_one.issues)
            records = [loaded_one.value]
        else:
            loaded = self._load_all(repo_root)
            if not loaded.ok or loaded.value is None:
                return self.foundation.fail(loaded.issues)
            records = [record for record in loaded.value if record.active]
        issues: list[ServiceIssue] = []
        for record in records:
            issues.extend(self._record_issues(record))
        return self.foundation.ok(
            AdapterDeclCompletenessView(
                checked_names=[record.name for record in records],
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
            return self.foundation.fail(self.foundation.issue("adapter_module_invalid", "Adapter module is invalid.", field="module"))
        loaded = self._load_all(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        upstream = self._strip(upstream_decl_name)
        query = self._strip(adapter_name_query)
        matches = []
        for record in loaded.value:
            if record.module != normalized_module:
                continue
            upstream_names = {
                item
                for item in [
                    record.statement.formal.upstream_decl_name if record.statement.formal else None,
                    record.proof.formal.upstream_decl_name if record.proof and record.proof.formal else None,
                ]
                if item
            }
            if upstream and upstream in upstream_names:
                matches.append(self._summary(record))
                continue
            if query and query.lower() in record.name.lower():
                matches.append(self._summary(record))
        return self.foundation.ok(
            AdapterDeclMatchView(
                matches=sorted(matches, key=lambda item: item.name),
                summary=f"Found {len(matches)} adapter decl matches.",
            )
        )

    def _add_origin(
        self,
        repo_root: Path,
        record: AdapterDeclRecord,
        target: list[AdapterOrigin],
        origin_text: str,
        source_hint: str | None,
        stage: str,
    ) -> ServiceResult[AdapterDeclView]:
        try:
            origin = AdapterOrigin(origin_text=origin_text, source_hint=self._strip(source_hint))
        except Exception as exc:  # noqa: BLE001
            return self.foundation.fail(self.foundation.issue("adapter_origin_invalid", f"Adapter origin is invalid: {exc}"))
        if any(item.model_dump() == origin.model_dump() for item in target):
            warning = self.foundation.issue(
                "adapter_origin_duplicate",
                "Adapter origin is already recorded.",
                severity=IssueSeverity.WARNING,
                object_ref=record.name,
            )
            return self.foundation.ok(self._view(record, summary=f"{stage.title()} origin already recorded."), warnings=[warning])
        target.append(origin)
        return self._save_and_view(repo_root, record, f"Added {stage} origin for {record.name}.")

    def _add_dep(
        self,
        repo_root: Path,
        record: AdapterDeclRecord,
        target: list[AdapterDeclDep],
        dep_name: str,
        reason: str,
        stage: str,
    ) -> ServiceResult[AdapterDeclView]:
        dep_key = self._safe_decl_name(dep_name)
        if not dep_key.ok or dep_key.value is None:
            return self.foundation.fail(dep_key.issues)
        dep = self._load(repo_root, dep_key.value)
        if not dep.ok or dep.value is None:
            return self.foundation.fail(
                self.foundation.issue("adapter_dep_missing", f"Adapter dependency does not exist: {dep_key.value}", object_ref=record.name)
            )
        if dep_key.value == record.name:
            return self.foundation.fail(self.foundation.issue("adapter_dep_self", "Adapter declaration cannot depend on itself.", object_ref=record.name))
        if not reason or not reason.strip():
            return self.foundation.fail(self.foundation.issue("adapter_dep_reason_required", "Adapter dependency reason is required.", field="reason"))
        if any(item.dep_name == dep_key.value for item in target):
            warning = self.foundation.issue("adapter_dep_duplicate", "Adapter dependency is already recorded.", severity=IssueSeverity.WARNING, object_ref=record.name)
            return self.foundation.ok(self._view(record, summary=f"{stage.title()} dependency already recorded."), warnings=[warning])
        target.append(AdapterDeclDep(dep_name=dep_key.value, reason=reason))
        return self._save_and_view(repo_root, record, f"Added {stage} dependency for {record.name}.")

    def _remove_dep(
        self,
        repo_root: Path,
        record: AdapterDeclRecord,
        target: list[AdapterDeclDep],
        dep_name: str,
        stage: str,
    ) -> ServiceResult[AdapterDeclView]:
        before = len(target)
        target[:] = [item for item in target if item.dep_name != dep_name]
        if len(target) == before:
            return self.foundation.fail(self.foundation.issue("adapter_dep_not_found", f"{stage.title()} dependency was not found.", object_ref=record.name, field=dep_name))
        return self._save_and_view(repo_root, record, f"Removed {stage} dependency for {record.name}.")

    def _record_issues(self, record: AdapterDeclRecord) -> list[ServiceIssue]:
        issues: list[ServiceIssue] = []
        if not record.module:
            issues.append(self._issue(record, "adapter_decl_module_missing", "module", "Adapter decl module is missing."))
        if record.statement.formal is None:
            issues.append(self._issue(record, "adapter_statement_formal_missing", "statement.formal", "Statement formal code is missing."))
        if record.statement.nl is None:
            issues.append(self._issue(record, "adapter_statement_nl_missing", "statement.nl", "Statement natural language summary is missing."))
        if record.statement.formal is not None:
            scan = self._scan(record.statement.formal.code)
            if scan.ok and scan.value is not None:
                issues.extend(self._forbidden_statement_occurrences(record, scan.value))
        if record.kind in _THEOREM_LIKE:
            if record.proof is None or record.proof.formal is None:
                issues.append(self._issue(record, "adapter_proof_formal_missing", "proof.formal", "Proof formal code is missing."))
            if record.proof is None or record.proof.nl is None:
                issues.append(self._issue(record, "adapter_proof_nl_missing", "proof.nl", "Proof natural language summary is missing."))
            if record.proof and record.proof.formal:
                scan = self._scan(record.proof.formal.code)
                if scan.ok and scan.value is not None:
                    issues.extend(self._forbidden_proof_occurrences(scan.value, record=record))
        return issues

    def _forbidden_statement_occurrences(self, record: AdapterDeclRecord, scan: object) -> list[ServiceIssue]:
        contains_sorry = bool(getattr(scan, "contains_sorry", False))
        contains_admit = bool(getattr(scan, "contains_admit", False))
        contains_axiom = bool(getattr(scan, "contains_axiom", False))
        contains_opaque = bool(getattr(scan, "contains_opaque", False))
        contains_unsafe = bool(getattr(scan, "contains_unsafe", False))
        issues = []
        if contains_admit or contains_axiom or contains_opaque or contains_unsafe:
            issues.append(self._issue(record, "adapter_statement_forbidden_construct", "statement.formal", "Statement formal code contains admit/axiom/opaque/unsafe."))
        if record.kind not in _THEOREM_LIKE and contains_sorry:
            issues.append(self._issue(record, "adapter_statement_sorry_forbidden", "statement.formal", "Non-theorem adapter statement cannot contain sorry."))
        return issues

    def _forbidden_proof_occurrences(self, scan: object, record: AdapterDeclRecord | None = None) -> list[ServiceIssue]:
        forbidden = any(
            bool(getattr(scan, field, False))
            for field in ["contains_sorry", "contains_admit", "contains_axiom", "contains_opaque", "contains_unsafe"]
        )
        if not forbidden:
            return []
        return [
            self.foundation.issue(
                "adapter_proof_forbidden_construct",
                "Proof formal code contains sorry/admit/axiom/opaque/unsafe.",
                object_ref=record.name if record is not None else None,
                field="proof.formal",
            )
        ]

    def _scan(self, code: str) -> ServiceResult[object]:
        return self.lean_check.detect_sorry_axiom(code)

    def _issue(self, record: AdapterDeclRecord, kind: str, field: str, message: str) -> ServiceIssue:
        return self.foundation.issue(kind, message, object_ref=record.name, field=field)

    def _load(self, repo_root: Path, name: str) -> ServiceResult[AdapterDeclRecord]:
        safe = self._safe_decl_name(name)
        if not safe.ok or safe.value is None:
            return self.foundation.fail(safe.issues)
        path = self._decl_path(repo_root, safe.value)
        loaded = self.foundation.store.read_json(path, AdapterDeclRecord)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(
                self.foundation.issue("adapter_decl_missing", f"Adapter decl is missing: {safe.value}", object_ref=safe.value)
            )
        return loaded

    def _load_all(self, repo_root: Path) -> ServiceResult[list[AdapterDeclRecord]]:
        root = self._decls_root(repo_root)
        if not root.exists():
            return self.foundation.ok([])
        return self.foundation.store.list_json(root, AdapterDeclRecord)

    def _save_and_view(self, repo_root: Path, record: AdapterDeclRecord, summary: str) -> ServiceResult[AdapterDeclView]:
        record.updated_at = utc_now_iso()
        saved = self.foundation.store.write_json_atomic(self._decl_path(repo_root, record.name), record, mode=WriteMode.OVERWRITE)
        if not saved.ok:
            return self.foundation.fail(saved.issues)
        return self.foundation.ok(self._view(record, summary=summary))

    def _decls_root(self, repo_root: Path) -> Path:
        ctx = FoundationContext(repo_root=Path(repo_root))
        return self.foundation.layout.node_metadata_dir(ctx, "Main") / "decls"

    def _decl_path(self, repo_root: Path, name: str) -> Path:
        return self._decls_root(repo_root) / f"{self.foundation.layout.ensure_safe_key(name)}.json"

    def _safe_decl_name(self, name: str) -> ServiceResult[str]:
        try:
            return self.foundation.ok(self.foundation.layout.ensure_safe_key(name.strip()))
        except Exception as exc:  # noqa: BLE001
            return self.foundation.fail(self.foundation.issue("adapter_decl_name_invalid", f"Adapter decl name is invalid: {exc}", field="name"))

    def _normalize_module(self, module: str) -> str | None:
        value = module.strip()
        if not value or any(ch.isspace() for ch in value):
            return None
        if "/" in value or "\\" in value or ".." in value:
            return None
        if any(not part for part in value.split(".")):
            return None
        return value

    def _view(self, record: AdapterDeclRecord, *, summary: str) -> AdapterDeclView:
        return AdapterDeclView(record=record, summary=summary)

    def _summary(self, record: AdapterDeclRecord) -> AdapterDeclSummaryView:
        upstream_decl_name = None
        if record.proof and record.proof.formal and record.proof.formal.upstream_decl_name:
            upstream_decl_name = record.proof.formal.upstream_decl_name
        elif record.statement.formal and record.statement.formal.upstream_decl_name:
            upstream_decl_name = record.statement.formal.upstream_decl_name
        return AdapterDeclSummaryView(
            name=record.name,
            kind=record.kind,
            module=record.module,
            finalized=record.finalized,
            state=record.state,
            plan_summary=record.plan_summary,
            upstream_decl_name=upstream_decl_name,
        )

    @staticmethod
    def _strip(value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None
