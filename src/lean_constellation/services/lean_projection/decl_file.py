"""Decl-owned Lean file preparation, capture, and synchronization."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.repo import ProofAvailability, RepoFormat
from lean_constellation.services.decl_graph.models import DeclFileRevisionView
from lean_constellation.services.foundation import (
    DeclFileKey,
    FoundationContext,
    GateReport,
    MutationSummaryView,
    ServiceIssue,
    ServiceResult,
)
from lean_constellation.services.lean_projection.annotation import (
    AnnotationComponent,
    ResolvedDependencyProjection,
    ResolvedMathlibDependencyProjection,
    ResolvedRepoDeclDependencyProjection,
)
from lean_constellation.services.lean_projection.lean_check import LeanCheckComponent, LeanCheckView
from lean_constellation.services.foundation.module_layout import (
    NativeModuleLayoutError,
    decl_kind_dir,
    local_module_name,
    local_projection_path,
    validate_module_segment,
)
from lean_constellation.services.lean_projection.managed_file import ManagedDeclFileComponent
from lean_constellation.services.lean_projection.module_identity import ModuleBuildView, ModuleIdentityComponent

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


DeclFileStage = Literal["statement", "proof"]


class LeanPathView(StrictModel):
    node_path: str
    decl_name: str
    kind: str
    kind_dir: str
    path: str
    relative_path: str
    module: str
    summary: str


class LeanFileView(StrictModel):
    node_path: str
    decl_name: str
    stage: DeclFileStage
    path: str
    module: str
    changed: bool
    line_count: int
    summary: str


class DeclManagedProjectionRefreshView(StrictModel):
    """Result of refreshing only the system-managed regions of one Decl file."""

    node_path: str
    decl_name: str
    effective_stage: DeclFileStage | None = None
    changed: bool = False
    changed_files: list[str] = Field(default_factory=list)
    reread_required: bool = False
    projection_fingerprint: str | None = None
    summary: str


class FormalCaptureView(StrictModel):
    node_path: str
    decl_name: str
    stage: DeclFileStage
    path: str
    module: str
    lean_decl_name: str
    build: ModuleBuildView
    line_count: int
    check: LeanCheckView
    projection_fingerprint: str | None = None
    summary: str


class DeclFileCaptureSyncView(StrictModel):
    node_path: str
    decl_name: str
    stage: DeclFileStage
    path: str
    module: str
    synchronized: bool
    summary: str
    gate: GateReport


class DeclOwnedLeanFileView(StrictModel):
    """Complete captured Decl-owned Lean source for an authorized caller."""

    node_path: str
    decl_name: str
    revision: int
    stage: DeclFileStage
    path: str | None = None
    module: str
    lean_decl_name: str | None = None
    visibility: Literal["public", "private"]
    source: Literal["physical_current", "captured_revision"]
    content: str
    line_count: int
    summary: str


class DeclFileRevisionProvider(Protocol):
    """Minimal DeclGraph-facing provider used by DeclFileComponent."""

    def get_current_decl_revision(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[DeclFileRevisionView]:
        ...

    def save_statement_formal_capture(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
        lean_decl_name: str,
    ) -> ServiceResult[DeclFileRevisionView]:
        ...

    def save_proof_formal_capture(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
        lean_decl_name: str,
    ) -> ServiceResult[DeclFileRevisionView]:
        ...


class _MissingDeclFileRevisionProvider:
    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def _missing(self, *, node_path: str, decl_name: str) -> ServiceResult[DeclFileRevisionView]:
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "decl_revision_provider_missing",
                "No DeclRevision provider is configured for Decl-owned Lean file operations.",
                object_ref=f"{node_path}:{decl_name}",
            )
        )

    def get_current_decl_revision(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[DeclFileRevisionView]:
        del repo_root
        return self._missing(node_path=node_path, decl_name=decl_name)

    def save_statement_formal_capture(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
        lean_decl_name: str,
    ) -> ServiceResult[DeclFileRevisionView]:
        del repo_root, code, check, lean_decl_name
        return self._missing(node_path=node_path, decl_name=decl_name)

    def save_proof_formal_capture(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
        lean_decl_name: str,
    ) -> ServiceResult[DeclFileRevisionView]:
        del repo_root, code, check, lean_decl_name
        return self._missing(node_path=node_path, decl_name=decl_name)


class DeclFileComponent:
    """Prepare and capture controlled Decl-owned Lean files."""

    _THEOREM_LIKE = {"theorem", "lemma", "proposition", "corollary"}
    _ALLOWED_LEAN_KINDS = {
        "definition": {"def", "abbrev"},
        "def": {"def", "abbrev"},
        "type": {"structure", "class", "inductive"},
        "structure": {"structure"},
        "class": {"class"},
        "instance": {"instance"},
        "lemma": {"lemma", "theorem"},
        "theorem": {"theorem", "lemma"},
        "proposition": {"theorem", "lemma"},
        "corollary": {"theorem", "lemma"},
        "notation": {"def", "abbrev"},
        "axiom": {"axiom"},
    }

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        annotation: AnnotationComponent | None = None,
        lean_check: LeanCheckComponent | None = None,
        revision_provider: DeclFileRevisionProvider | None = None,
        managed_file: ManagedDeclFileComponent | None = None,
        module_identity: ModuleIdentityComponent | None = None,
    ) -> None:
        self.runtime = runtime
        self.annotation = annotation or AnnotationComponent(runtime)
        self.lean_check = lean_check or LeanCheckComponent(runtime)
        self.revision_provider = revision_provider or _MissingDeclFileRevisionProvider(runtime)
        self.managed_file = managed_file or ManagedDeclFileComponent(runtime, annotation=self.annotation)
        self.module_identity = module_identity or ModuleIdentityComponent(runtime)

    def derive_decl_file_path(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        kind: str,
    ) -> ServiceResult[LeanPathView]:
        try:
            kind_dir = self._kind_dir(kind)
            ctx = FoundationContext(repo_root=Path(repo_root))
            # Decl.name is a flat Constellation/module key.  Namespace-qualified
            # Lean names belong to DeclRevision.lean_decl_name instead.
            validate_module_segment(decl_name, label="Decl.name")
            logical_path = self.runtime.foundation.layout.decl_file_path(
                ctx,
                DeclFileKey(node_path=node_path, decl_kind=kind_dir, decl_name=decl_name),
            )
            path = local_projection_path(repo_root, logical_path)
            self.runtime.foundation.layout.assert_within(Path(repo_root), path)
            relative = path.relative_to(Path(repo_root).expanduser().resolve(strict=False)).as_posix()
            module = ".".join(Path(relative).with_suffix("").parts)
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_file_path_invalid",
                    f"Decl-owned Lean file path cannot be derived: {exc}",
                    object_ref=f"{node_path}:{decl_name}",
                )
            )
        return self.runtime.foundation.ok(
            LeanPathView(
                node_path=node_path,
                decl_name=decl_name,
                kind=kind,
                kind_dir=kind_dir,
                path=str(path),
                relative_path=relative,
                module=module,
                summary=f"Decl-owned Lean file path for {node_path}:{decl_name} is {relative}.",
            )
        )

    def read_decl_owned_lean_file(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        revision: int | None = None,
    ) -> ServiceResult[DeclOwnedLeanFileView]:
        """Read one current physical or historical captured Decl-owned file."""

        repo_root = Path(repo_root)
        decl = self.runtime.decl_graph.get_decl(repo_root, node_path=node_path, name=decl_name)
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        resolved_revision = revision or decl.value.current_revision
        revision_result = self.runtime.decl_graph.get_decl_revision(
            repo_root,
            node_path=node_path,
            name=decl_name,
            revision=resolved_revision,
        )
        if not revision_result.ok or revision_result.value is None:
            return self.runtime.foundation.fail(revision_result.issues)
        value = revision_result.value
        proof_code = value.proof.formal.code if value.proof is not None and value.proof.formal is not None else None
        statement_code = value.statement.formal.code if value.statement.formal is not None else None
        if proof_code:
            stage: DeclFileStage = "proof"
            content = proof_code
        elif statement_code:
            stage = "statement"
            content = statement_code
        else:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_formal_file_missing",
                    "The requested declaration revision has no captured formal Lean file.",
                    object_ref=f"{node_path}:{decl_name}@{resolved_revision}",
                )
            )
        if not decl.value.module:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_module_missing",
                    "The requested declaration has no registered Lean module.",
                    object_ref=f"{node_path}:{decl_name}",
                )
            )

        path: str | None = None
        source: Literal["physical_current", "captured_revision"] = "captured_revision"
        repo_format = self.runtime.repo_workspace.metadata.get_repo_format(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        if repo_format.value.repo_format == RepoFormat.NATIVE and resolved_revision == decl.value.current_revision:
            path_view = self.derive_decl_file_path(
                repo_root,
                node_path=node_path,
                decl_name=decl_name,
                kind=decl.value.kind,
            )
            if not path_view.ok or path_view.value is None:
                return self.runtime.foundation.fail(path_view.issues)
            physical = Path(path_view.value.path)
            if physical.exists():
                loaded = self._read_lean_file(physical, object_ref=f"{node_path}:{decl_name}")
                if not loaded.ok or loaded.value is None:
                    return self.runtime.foundation.fail(loaded.issues)
                content = loaded.value
                path = str(physical)
                source = "physical_current"
        return self.runtime.foundation.ok(
            DeclOwnedLeanFileView(
                node_path=node_path,
                decl_name=decl_name,
                revision=resolved_revision,
                stage=stage,
                path=path,
                module=decl.value.module,
                lean_decl_name=value.lean_decl_name,
                visibility="public" if decl.value.public else "private",
                source=source,
                content=content,
                line_count=len(content.splitlines()),
                summary=f"Read {source.replace('_', ' ')} for {node_path}::{decl_name}@{resolved_revision}.",
            )
        )

    def resolve_dependency_projections(
        self,
        repo_root: Path,
        *,
        consumer_node_path: str,
        dependencies: Sequence[object],
        require_complete: bool = False,
    ) -> ServiceResult[list[ResolvedDependencyProjection]]:
        """Resolve structured dependency truth for human or managed projection."""

        return self._resolve_dependencies(
            Path(repo_root),
            consumer_node_path=consumer_node_path,
            dependencies=dependencies,
            require_complete=require_complete,
        )

    def prepare_statement_formal_file(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[LeanFileView]:
        revision = self._load_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        kind = self._decl_kind(revision.value)
        if kind is None:
            return self.runtime.foundation.fail(self._missing_field_issue(node_path, decl_name, "kind"))
        open_issue = self._require_open_revision(revision.value, node_path=node_path, decl_name=decl_name)
        if open_issue is not None:
            return self.runtime.foundation.fail(open_issue)
        kind_issue = self._supported_kind_issue(kind, node_path=node_path, decl_name=decl_name)
        if kind_issue is not None:
            return self.runtime.foundation.fail(kind_issue)
        nl_issue = self._require_nl_text(revision.value, "statement", node_path=node_path, decl_name=decl_name)
        if nl_issue is not None:
            return self.runtime.foundation.fail(nl_issue)
        path_view = self.derive_decl_file_path(repo_root, node_path=node_path, decl_name=decl_name, kind=kind)
        if not path_view.ok or path_view.value is None:
            return self.runtime.foundation.fail(path_view.issues)
        dependencies = self._resolve_dependencies(
            repo_root,
            consumer_node_path=node_path,
            dependencies=revision.value.statement.deps,
            require_complete=False,
        )
        if not dependencies.ok or dependencies.value is None:
            return self.runtime.foundation.fail(dependencies.issues)
        docstring = self.annotation.render_statement_docstring(
            revision.value,
            dependencies=dependencies.value,
        )
        if not docstring.ok or docstring.value is None:
            return self.runtime.foundation.fail(docstring.issues)
        imports = self._managed_imports(
            repo_root,
            node_path=node_path,
            dependency_projections=dependencies.value,
        )
        text = self._render_or_refresh_file(
            Path(path_view.value.path),
            imports=imports,
            docstring=docstring.value,
        )
        if not text.ok or text.value is None:
            return self.runtime.foundation.fail(text.issues)
        return self._write_file_view(Path(path_view.value.path), text.value, path_view.value, "statement", changed_summary="Prepared statement formal Lean file.")

    def prepare_proof_formal_file(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[LeanFileView]:
        revision = self._load_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        kind = self._decl_kind(revision.value)
        if kind is None:
            return self.runtime.foundation.fail(self._missing_field_issue(node_path, decl_name, "kind"))
        open_issue = self._require_open_revision(revision.value, node_path=node_path, decl_name=decl_name)
        if open_issue is not None:
            return self.runtime.foundation.fail(open_issue)
        kind_issue = self._supported_kind_issue(kind, node_path=node_path, decl_name=decl_name)
        if kind_issue is not None:
            return self.runtime.foundation.fail(kind_issue)
        if not self._is_theorem_like(kind):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_not_theorem_like",
                    "Proof formal files are only valid for theorem-like declarations.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=kind,
                    expected="theorem-like kind",
                )
            )
        nl_issue = self._require_nl_text(revision.value, "proof", node_path=node_path, decl_name=decl_name)
        if nl_issue is not None:
            return self.runtime.foundation.fail(nl_issue)
        statement_code = self._formal_code(revision.value, "statement")
        if statement_code is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "statement_formal_capture_missing",
                    "Proof formal preparation requires a captured statement formal file.",
                    object_ref=f"{node_path}:{decl_name}",
                    field="statement.formal.code",
                )
            )
        statement_dependencies = self._resolve_dependencies(
            repo_root,
            consumer_node_path=node_path,
            dependencies=revision.value.statement.deps,
            require_complete=False,
        )
        if not statement_dependencies.ok or statement_dependencies.value is None:
            return self.runtime.foundation.fail(statement_dependencies.issues)
        proof_dependencies = self._resolve_dependencies(
            repo_root,
            consumer_node_path=node_path,
            dependencies=revision.value.proof.deps if revision.value.proof is not None else [],
            require_complete=False,
        )
        if not proof_dependencies.ok or proof_dependencies.value is None:
            return self.runtime.foundation.fail(proof_dependencies.issues)
        docstring = self.annotation.render_proof_docstring(
            revision.value,
            statement_dependencies=statement_dependencies.value,
            proof_dependencies=proof_dependencies.value,
        )
        if not docstring.ok or docstring.value is None:
            return self.runtime.foundation.fail(docstring.issues)
        imports = self._managed_imports(
            repo_root,
            node_path=node_path,
            dependency_projections=[*statement_dependencies.value, *proof_dependencies.value],
        )
        replaced = self.managed_file.refresh(statement_code, imports=imports, docstring=docstring.value)
        if not replaced.ok or replaced.value is None:
            return self.runtime.foundation.fail(replaced.issues)
        path_view = self.derive_decl_file_path(repo_root, node_path=node_path, decl_name=decl_name, kind=kind)
        if not path_view.ok or path_view.value is None:
            return self.runtime.foundation.fail(path_view.issues)
        return self._write_file_view(Path(path_view.value.path), replaced.value, path_view.value, "proof", changed_summary="Prepared proof formal Lean file from statement capture.")

    def refresh_decl_managed_projection(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
    ) -> ServiceResult[DeclManagedProjectionRefreshView]:
        """Refresh imports/docstring from current truth without touching Agent source."""

        revision = self._load_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        kind = self._decl_kind(revision.value)
        if kind is None:
            return self.runtime.foundation.fail(self._missing_field_issue(node_path, decl_name, "kind"))
        path_view = self.derive_decl_file_path(repo_root, node_path=node_path, decl_name=decl_name, kind=kind)
        if not path_view.ok or path_view.value is None:
            return self.runtime.foundation.fail(path_view.issues)
        path = Path(path_view.value.path)
        effective_stage = self._effective_projection_stage(revision.value)
        if not path.exists():
            return self.runtime.foundation.ok(
                DeclManagedProjectionRefreshView(
                    node_path=node_path,
                    decl_name=decl_name,
                    effective_stage=effective_stage,
                    projection_fingerprint=self.annotation.projection_fingerprint(),
                    summary="Truth was updated; no Decl-owned Lean file exists yet, so prepare will use the latest projection.",
                )
            )
        if effective_stage is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_projection_stage_missing",
                    "A Decl-owned Lean file exists but current truth has no natural-language statement stage.",
                    object_ref=f"{node_path}:{decl_name}",
                    details={"path": str(path)},
                )
            )

        statement_dependencies = self._resolve_dependencies(
            repo_root,
            consumer_node_path=node_path,
            dependencies=revision.value.statement.deps,
            require_complete=False,
        )
        if not statement_dependencies.ok or statement_dependencies.value is None:
            return self.runtime.foundation.fail(statement_dependencies.issues)
        dependencies: list[ResolvedDependencyProjection] = list(statement_dependencies.value)
        if effective_stage == "proof":
            proof_dependencies = self._resolve_dependencies(
                repo_root,
                consumer_node_path=node_path,
                dependencies=revision.value.proof.deps if revision.value.proof is not None else [],
                require_complete=False,
            )
            if not proof_dependencies.ok or proof_dependencies.value is None:
                return self.runtime.foundation.fail(proof_dependencies.issues)
            docstring = self.annotation.render_proof_docstring(
                revision.value,
                statement_dependencies=statement_dependencies.value,
                proof_dependencies=proof_dependencies.value,
            )
            dependencies.extend(proof_dependencies.value)
        else:
            docstring = self.annotation.render_statement_docstring(
                revision.value,
                dependencies=statement_dependencies.value,
            )
        if not docstring.ok or docstring.value is None:
            return self.runtime.foundation.fail(docstring.issues)
        existing = self._read_lean_file(path, object_ref=f"{node_path}:{decl_name}")
        if not existing.ok or existing.value is None:
            return self.runtime.foundation.fail(existing.issues)
        refreshed = self.managed_file.refresh(
            existing.value,
            imports=self._managed_imports(
                repo_root,
                node_path=node_path,
                dependency_projections=dependencies,
            ),
            docstring=docstring.value,
        )
        if not refreshed.ok or refreshed.value is None:
            return self.runtime.foundation.fail(refreshed.issues)
        changed = refreshed.value != existing.value
        if changed:
            written = self._write_text_if_changed(path, refreshed.value)
            if not written.ok or written.value is None:
                return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(
            DeclManagedProjectionRefreshView(
                node_path=node_path,
                decl_name=decl_name,
                effective_stage=effective_stage,
                changed=changed,
                changed_files=[str(path)] if changed else [],
                reread_required=changed,
                projection_fingerprint=self.annotation.projection_fingerprint(),
                summary=(
                    "Truth was updated successfully and the managed Lean projection changed. "
                    "Reread the file in this same AgentStep before any further edit or submission, then continue; "
                    "reread_required is not a blocker."
                    if changed
                    else "Truth was updated; the managed Lean projection was already current."
                ),
            )
        )

    def capture_statement_formal_file(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[FormalCaptureView]:
        revision = self._load_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        kind = self._decl_kind(revision.value)
        if kind is None:
            return self.runtime.foundation.fail(self._missing_field_issue(node_path, decl_name, "kind"))
        path_view = self.derive_decl_file_path(repo_root, node_path=node_path, decl_name=decl_name, kind=kind)
        if not path_view.ok or path_view.value is None:
            return self.runtime.foundation.fail(path_view.issues)
        file_text = self._read_lean_file(Path(path_view.value.path), object_ref=f"{node_path}:{decl_name}")
        if not file_text.ok or file_text.value is None:
            return self.runtime.foundation.fail(file_text.issues)
        managed = self.managed_file.validate(file_text.value)
        if not managed.ok:
            return self.runtime.foundation.fail(managed.issues)
        dependencies = self._resolve_dependencies(
            repo_root,
            consumer_node_path=node_path,
            dependencies=revision.value.statement.deps,
            require_complete=True,
        )
        if not dependencies.ok or dependencies.value is None:
            return self.runtime.foundation.fail(dependencies.issues)
        docstring = self.annotation.render_statement_docstring(
            revision.value,
            dependencies=dependencies.value,
        )
        if not docstring.ok or docstring.value is None:
            return self.runtime.foundation.fail(docstring.issues)
        gate = self.annotation.validate_docstring(file_text.value, decl_name=decl_name, stage="statement", expected_docstring=docstring.value)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        if not gate.value.passed:
            return self.runtime.foundation.fail(gate.value.issues)
        location = self.annotation.locate_target_declaration(file_text.value, decl_name=decl_name)
        if not location.ok or location.value is None:
            return self.runtime.foundation.fail(location.issues)
        kind_gate = self._check_decl_kind_match(kind, location.value.kind, node_path=node_path, decl_name=decl_name)
        if not kind_gate.ok:
            return self.runtime.foundation.fail(kind_gate.issues)
        module_issue = self._module_mismatch_issue(
            revision.value,
            actual_module=path_view.value.module,
            node_path=node_path,
            decl_name=decl_name,
        )
        if module_issue is not None:
            return self.runtime.foundation.fail(module_issue)
        built = self.module_identity.build_module(repo_root, module=path_view.value.module)
        if not built.ok or built.value is None:
            return self.runtime.foundation.fail(built.issues)
        identity = self.module_identity.confirm_declaration_identity(
            repo_root,
            module=path_view.value.module,
            location=location.value,
        )
        if not identity.ok or identity.value is None:
            return self.runtime.foundation.fail(identity.issues)
        check = self.lean_check.build_statement_lean_check(repo_root, file_path=Path(path_view.value.path), decl_kind=kind)
        if not check.ok or check.value is None:
            return self.runtime.foundation.fail(check.issues)
        if check.value.status != "passed":
            return self.runtime.foundation.fail(self._lean_check_failed_issue(node_path, decl_name, "statement", check.value))
        saved = self.revision_provider.save_statement_formal_capture(
            Path(repo_root),
            node_path=node_path,
            decl_name=decl_name,
            code=file_text.value,
            check=check.value,
            lean_decl_name=identity.value.lean_decl_name,
        )
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(
            FormalCaptureView(
                node_path=node_path,
                decl_name=decl_name,
                stage="statement",
                path=path_view.value.path,
                module=path_view.value.module,
                lean_decl_name=identity.value.lean_decl_name,
                build=built.value,
                line_count=len(file_text.value.splitlines()),
                check=check.value,
                projection_fingerprint=self.annotation.projection_fingerprint(),
                summary="Captured statement formal file.",
            )
        )

    def capture_proof_formal_file(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[FormalCaptureView]:
        revision = self._load_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        kind = self._decl_kind(revision.value)
        if kind is None:
            return self.runtime.foundation.fail(self._missing_field_issue(node_path, decl_name, "kind"))
        if not self._is_theorem_like(kind):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_not_theorem_like",
                    "Proof capture is only valid for theorem-like declarations.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=kind,
                )
            )
        statement_code = self._formal_code(revision.value, "statement")
        if statement_code is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "statement_formal_capture_missing",
                    "Proof capture requires a captured statement formal file.",
                    object_ref=f"{node_path}:{decl_name}",
                    field="statement.formal.code",
                )
            )
        path_view = self.derive_decl_file_path(repo_root, node_path=node_path, decl_name=decl_name, kind=kind)
        if not path_view.ok or path_view.value is None:
            return self.runtime.foundation.fail(path_view.issues)
        file_text = self._read_lean_file(Path(path_view.value.path), object_ref=f"{node_path}:{decl_name}")
        if not file_text.ok or file_text.value is None:
            return self.runtime.foundation.fail(file_text.issues)
        managed = self.managed_file.validate(file_text.value)
        if not managed.ok:
            return self.runtime.foundation.fail(managed.issues)
        statement_dependencies = self._resolve_dependencies(
            repo_root,
            consumer_node_path=node_path,
            dependencies=revision.value.statement.deps,
            require_complete=True,
        )
        if not statement_dependencies.ok or statement_dependencies.value is None:
            return self.runtime.foundation.fail(statement_dependencies.issues)
        proof_dependencies = self._resolve_dependencies(
            repo_root,
            consumer_node_path=node_path,
            dependencies=revision.value.proof.deps if revision.value.proof is not None else [],
            require_complete=True,
        )
        if not proof_dependencies.ok or proof_dependencies.value is None:
            return self.runtime.foundation.fail(proof_dependencies.issues)
        docstring = self.annotation.render_proof_docstring(
            revision.value,
            statement_dependencies=statement_dependencies.value,
            proof_dependencies=proof_dependencies.value,
        )
        if not docstring.ok or docstring.value is None:
            return self.runtime.foundation.fail(docstring.issues)
        doc_gate = self.annotation.validate_docstring(file_text.value, decl_name=decl_name, stage="proof", expected_docstring=docstring.value)
        if not doc_gate.ok or doc_gate.value is None:
            return self.runtime.foundation.fail(doc_gate.issues)
        if not doc_gate.value.passed:
            return self.runtime.foundation.fail(doc_gate.value.issues)
        header_gate = self.annotation.compare_theorem_header(statement_code, file_text.value, decl_name=decl_name)
        if not header_gate.ok or header_gate.value is None:
            return self.runtime.foundation.fail(header_gate.issues)
        if not header_gate.value.passed:
            return self.runtime.foundation.fail(header_gate.value.issues)
        location = self.annotation.locate_target_declaration(file_text.value, decl_name=decl_name)
        if not location.ok or location.value is None:
            return self.runtime.foundation.fail(location.issues)
        kind_gate = self._check_decl_kind_match(kind, location.value.kind, node_path=node_path, decl_name=decl_name)
        if not kind_gate.ok:
            return self.runtime.foundation.fail(kind_gate.issues)
        module_issue = self._module_mismatch_issue(
            revision.value,
            actual_module=path_view.value.module,
            node_path=node_path,
            decl_name=decl_name,
        )
        if module_issue is not None:
            return self.runtime.foundation.fail(module_issue)
        built = self.module_identity.build_module(repo_root, module=path_view.value.module)
        if not built.ok or built.value is None:
            return self.runtime.foundation.fail(built.issues)
        identity = self.module_identity.confirm_declaration_identity(
            repo_root,
            module=path_view.value.module,
            location=location.value,
        )
        if not identity.ok or identity.value is None:
            return self.runtime.foundation.fail(identity.issues)
        if revision.value.lean_decl_name is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "statement_lean_decl_name_missing",
                    "Proof capture requires the statement's compiler-confirmed Lean declaration name.",
                    object_ref=f"{node_path}:{decl_name}",
                )
            )
        if identity.value.lean_decl_name != revision.value.lean_decl_name:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "proof_lean_decl_name_changed",
                    "Proof formalization changed the primary Lean declaration identity.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=identity.value.lean_decl_name,
                    expected=revision.value.lean_decl_name,
                )
            )
        check = self.lean_check.build_proof_lean_check(repo_root, file_path=Path(path_view.value.path))
        if not check.ok or check.value is None:
            return self.runtime.foundation.fail(check.issues)
        if check.value.status != "passed":
            return self.runtime.foundation.fail(self._lean_check_failed_issue(node_path, decl_name, "proof", check.value))
        saved = self.revision_provider.save_proof_formal_capture(
            Path(repo_root),
            node_path=node_path,
            decl_name=decl_name,
            code=file_text.value,
            check=check.value,
            lean_decl_name=identity.value.lean_decl_name,
        )
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(
            FormalCaptureView(
                node_path=node_path,
                decl_name=decl_name,
                stage="proof",
                path=path_view.value.path,
                module=path_view.value.module,
                lean_decl_name=identity.value.lean_decl_name,
                build=built.value,
                line_count=len(file_text.value.splitlines()),
                check=check.value,
                projection_fingerprint=self.annotation.projection_fingerprint(),
                summary="Captured proof formal file.",
            )
        )

    def check_decl_file_snapshot_sync(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: DeclFileStage | str,
    ) -> ServiceResult[GateReport]:
        normalized_stage = self._normalize_stage(stage)
        if normalized_stage is None:
            return self.runtime.foundation.fail(self._invalid_stage_issue(stage))
        revision = self._load_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        kind = self._decl_kind(revision.value)
        if kind is None:
            return self.runtime.foundation.fail(self._missing_field_issue(node_path, decl_name, "kind"))
        path_view = self.derive_decl_file_path(repo_root, node_path=node_path, decl_name=decl_name, kind=kind)
        if not path_view.ok or path_view.value is None:
            return self.runtime.foundation.fail(path_view.issues)
        captured_code = self._formal_code(revision.value, normalized_stage)
        if captured_code is None:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "decl_file_capture_sync",
                    self.runtime.foundation.issue(
                        "formal_capture_missing",
                        "No captured formal file exists for the requested stage.",
                        object_ref=f"{node_path}:{decl_name}",
                        field=f"{normalized_stage}.formal.code",
                    ),
                    summary="Formal file capture is missing.",
                )
            )
        path = Path(path_view.value.path)
        if not path.exists():
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "decl_file_capture_sync",
                    self.runtime.foundation.issue(
                        "decl_file_missing",
                        "Decl-owned Lean file is missing.",
                        object_ref=f"{node_path}:{decl_name}",
                        details={"path": str(path)},
                    ),
                    summary="Decl-owned Lean file is missing.",
                )
            )
        file_text = self._read_lean_file(path, object_ref=f"{node_path}:{decl_name}")
        if not file_text.ok or file_text.value is None:
            return self.runtime.foundation.fail(file_text.issues)
        # Snapshot sync predates the managed-region contract and is also used by
        # small DeclGraph fixtures that intentionally store a raw Lean fragment.
        # Only a file that actually has managed regions has a projection whose
        # policy can be checked.  A managed file still gets the strict
        # policy/freshness validation below; raw fragments retain the historical
        # captured-code comparison and are not treated as a projection.
        managed_regions = self.managed_file.validate(file_text.value)
        if managed_regions.ok:
            policy = self.annotation.projection_policy()
            statement_dependencies_value: list[ResolvedDependencyProjection] = []
            if policy.include_dependencies:
                statement_dependencies = self._resolve_dependencies(
                    repo_root,
                    consumer_node_path=node_path,
                    dependencies=revision.value.statement.deps,
                    require_complete=True,
                )
                if not statement_dependencies.ok or statement_dependencies.value is None:
                    return self.runtime.foundation.fail(statement_dependencies.issues)
                statement_dependencies_value = statement_dependencies.value
            if normalized_stage == "proof":
                proof_dependencies_value: list[ResolvedDependencyProjection] = []
                if policy.include_dependencies:
                    proof_dependencies = self._resolve_dependencies(
                        repo_root,
                        consumer_node_path=node_path,
                        dependencies=revision.value.proof.deps if revision.value.proof is not None else [],
                        require_complete=True,
                    )
                    if not proof_dependencies.ok or proof_dependencies.value is None:
                        return self.runtime.foundation.fail(proof_dependencies.issues)
                    proof_dependencies_value = proof_dependencies.value
                expected_docstring = self.annotation.render_proof_docstring(
                    revision.value,
                    statement_dependencies=statement_dependencies_value,
                    proof_dependencies=proof_dependencies_value,
                    projection_policy=policy,
                )
            else:
                expected_docstring = self.annotation.render_statement_docstring(
                    revision.value,
                    dependencies=statement_dependencies_value,
                    projection_policy=policy,
                )
            if not expected_docstring.ok or expected_docstring.value is None:
                return self.runtime.foundation.fail(expected_docstring.issues)
            projection_gate = self.annotation.validate_docstring(
                file_text.value,
                decl_name=decl_name,
                stage=normalized_stage,
                expected_docstring=expected_docstring.value,
            )
            if not projection_gate.ok or projection_gate.value is None:
                return self.runtime.foundation.fail(projection_gate.issues)
            if not projection_gate.value.passed:
                return self.runtime.foundation.ok(
                    self.runtime.foundation.gate_failed(
                        "decl_file_capture_sync",
                        self.runtime.foundation.issue(
                            "decl_file_projection_stale",
                            "Decl-owned Lean file uses an outdated docstring projection policy or truth revision.",
                            object_ref=f"{node_path}:{decl_name}",
                            details={"projection_fingerprint": self.annotation.projection_fingerprint()},
                        ),
                        summary="Managed docstring projection is stale; reproject before using the captured file.",
                    )
                )
        if file_text.value != captured_code:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "decl_file_capture_sync",
                    self.runtime.foundation.issue(
                        "decl_file_capture_stale",
                        "Decl-owned Lean file was modified after the latest capture.",
                        object_ref=f"{node_path}:{decl_name}",
                        details={"path": str(path), "stage": normalized_stage},
                    ),
                    summary="Decl-owned Lean file is not synchronized with captured metadata.",
                )
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "decl_file_capture_sync",
                summary=f"{normalized_stage} formal file is synchronized with captured metadata.",
            )
        )

    def check_decl_dependency_identity(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: DeclFileStage | str,
    ) -> ServiceResult[GateReport]:
        """Require final module/full-name identity for all dependencies of a captured stage."""

        normalized_stage = self._normalize_stage(stage)
        if normalized_stage is None:
            return self.runtime.foundation.fail(self._invalid_stage_issue(stage))
        revision = self._load_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        kind = self._decl_kind(revision.value)
        if kind is None:
            return self.runtime.foundation.fail(self._missing_field_issue(node_path, decl_name, "kind"))
        path_view = self.derive_decl_file_path(repo_root, node_path=node_path, decl_name=decl_name, kind=kind)
        if not path_view.ok or path_view.value is None:
            return self.runtime.foundation.fail(path_view.issues)
        issues: list[ServiceIssue] = []
        module_issue = self._module_mismatch_issue(
            revision.value,
            actual_module=path_view.value.module,
            node_path=node_path,
            decl_name=decl_name,
        )
        if module_issue is not None:
            issues.append(module_issue)
        if not revision.value.lean_decl_name:
            issues.append(
                self.runtime.foundation.issue(
                    "lean_decl_name_missing",
                    "A captured declaration must have a compiler-confirmed Lean full name.",
                    object_ref=f"{node_path}:{decl_name}",
                    field="lean_decl_name",
                )
            )
        stages = [revision.value.statement.deps]
        if normalized_stage == "proof":
            stages.append(revision.value.proof.deps if revision.value.proof is not None else [])
        for dependencies in stages:
            resolved = self._resolve_dependencies(
                repo_root,
                consumer_node_path=node_path,
                dependencies=dependencies,
                require_complete=True,
            )
            if not resolved.ok:
                issues.extend(resolved.issues)
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "decl_dependency_identity",
                    issues,
                    summary=f"Dependency identity is incomplete for {node_path}:{decl_name}.",
                )
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "decl_dependency_identity",
                summary=f"Dependency identity is complete for {node_path}:{decl_name}.",
            )
        )

    def sync_decl_file_after_revision_reset(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[MutationSummaryView]:
        revision = self._load_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        kind = self._decl_kind(revision.value)
        if kind is None:
            return self.runtime.foundation.fail(self._missing_field_issue(node_path, decl_name, "kind"))
        path_view = self.derive_decl_file_path(repo_root, node_path=node_path, decl_name=decl_name, kind=kind)
        if not path_view.ok or path_view.value is None:
            return self.runtime.foundation.fail(path_view.issues)
        path = Path(path_view.value.path)
        proof_code = self._formal_code(revision.value, "proof")
        statement_code = self._formal_code(revision.value, "statement")
        if proof_code is not None:
            write = self._write_text_if_changed(path, proof_code)
            if not write.ok or write.value is None:
                return self.runtime.foundation.fail(write.issues)
            return self.runtime.foundation.ok(
                self.runtime.foundation.mutation_view(
                    object_ref=f"{node_path}:{decl_name}",
                    changed=write.value,
                    summary="Synchronized Decl-owned file to proof formal capture.",
                    changed_items=[str(path)] if write.value else [],
                )
            )
        if statement_code is not None:
            write = self._write_text_if_changed(path, statement_code)
            if not write.ok or write.value is None:
                return self.runtime.foundation.fail(write.issues)
            return self.runtime.foundation.ok(
                self.runtime.foundation.mutation_view(
                    object_ref=f"{node_path}:{decl_name}",
                    changed=write.value,
                    summary="Synchronized Decl-owned file to statement formal capture.",
                    changed_items=[str(path)] if write.value else [],
                )
            )
        changed = path.exists()
        try:
            if changed:
                path.unlink()
        except OSError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_file_delete_failed",
                    f"Failed to remove reset Decl-owned Lean file: {exc}",
                    object_ref=f"{node_path}:{decl_name}",
                    details={"path": str(path)},
                )
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.mutation_view(
                object_ref=f"{node_path}:{decl_name}",
                changed=changed,
                summary="Removed Decl-owned Lean file because the current revision has no formal capture.",
                changed_items=[str(path)] if changed else [],
            )
        )

    def remove_decl_file_for_delete(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[MutationSummaryView]:
        revision = self._load_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        kind = self._decl_kind(revision.value) or "definition"
        path_view = self.derive_decl_file_path(repo_root, node_path=node_path, decl_name=decl_name, kind=kind)
        if not path_view.ok or path_view.value is None:
            return self.runtime.foundation.fail(path_view.issues)
        path = Path(path_view.value.path)
        changed = path.exists()
        try:
            if changed:
                path.unlink()
        except OSError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_file_delete_failed",
                    f"Failed to remove Decl-owned Lean file: {exc}",
                    object_ref=f"{node_path}:{decl_name}",
                    details={"path": str(path)},
                )
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.mutation_view(
                object_ref=f"{node_path}:{decl_name}",
                changed=changed,
                summary=("Removed Decl-owned Lean file." if changed else "Decl-owned Lean file was already absent."),
                changed_items=[str(path)] if changed else [],
            )
        )

    def _load_revision(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[DeclFileRevisionView]:
        return self.revision_provider.get_current_decl_revision(Path(repo_root), node_path=node_path, decl_name=decl_name)

    def _render_or_refresh_file(
        self,
        path: Path,
        *,
        imports: list[str],
        docstring: str,
    ) -> ServiceResult[str]:
        if not path.exists():
            return self.runtime.foundation.ok(self.managed_file.render_new(imports=imports, docstring=docstring))
        existing = self._read_lean_file(path, object_ref=str(path))
        if not existing.ok or existing.value is None:
            return self.runtime.foundation.fail(existing.issues)
        return self.managed_file.refresh(existing.value, imports=imports, docstring=docstring)

    def _managed_imports(
        self,
        repo_root: Path,
        *,
        node_path: str,
        dependency_projections: Sequence[ResolvedDependencyProjection],
    ) -> list[str]:
        prelude = local_module_name(repo_root, f"{node_path}.Prelude")
        imports: list[str] = []
        for dependency in dependency_projections:
            if dependency.kind == "mathlib_decl":
                if dependency.module:
                    imports.append(dependency.module)
                continue
            if dependency.repo_key is None and dependency.node_path == node_path and dependency.module:
                imports.append(dependency.module)
        return [prelude, *sorted(set(imports) - {prelude})]

    def _resolve_dependencies(
        self,
        repo_root: Path,
        *,
        consumer_node_path: str,
        dependencies: Sequence[object],
        require_complete: bool,
    ) -> ServiceResult[list[ResolvedDependencyProjection]]:
        values: list[ResolvedDependencyProjection] = []
        for dependency in dependencies:
            if getattr(dependency, "kind", None) == "mathlib_decl":
                ref = dependency.ref
                if require_complete and not ref.module:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "mathlib_dependency_module_missing",
                            "Formal capture requires an exact Mathlib module for every Mathlib declaration dependency.",
                            object_ref=ref.name,
                        )
                    )
                values.append(
                    ResolvedMathlibDependencyProjection(
                        lean_decl_name=ref.name,
                        module=ref.module or "",
                    )
                )
                continue
            if getattr(dependency, "kind", None) != "repo_decl":
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "decl_dependency_kind_invalid",
                        "Decl projection received an unsupported dependency kind.",
                        current=str(getattr(dependency, "kind", None)),
                    )
                )
            ref = dependency.ref
            resolved = self._resolve_repo_dependency(
                repo_root,
                consumer_node_path=consumer_node_path,
                ref=ref,
            )
            if not resolved.ok or resolved.value is None:
                return self.runtime.foundation.fail(resolved.issues)
            if require_complete and (not resolved.value.module or not resolved.value.lean_decl_name):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "decl_dependency_identity_incomplete",
                        "Formal capture requires dependency module and Lean full name to be resolved.",
                        object_ref=f"{ref.repo + '::' if ref.repo else ''}{ref.node}::{ref.name}",
                    )
                )
            values.append(resolved.value)
        return self.runtime.foundation.ok(values)

    def _resolve_repo_dependency(
        self,
        repo_root: Path,
        *,
        consumer_node_path: str,
        ref,
    ) -> ServiceResult[ResolvedRepoDeclDependencyProjection]:  # noqa: ANN001
        if ref.repo is None and ref.node == consumer_node_path:
            revision = self._load_revision(repo_root, node_path=ref.node, decl_name=ref.name)
            if not revision.ok or revision.value is None:
                return self.runtime.foundation.fail(revision.issues)
            if revision.value.revision != ref.revision:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "same_node_decl_dependency_revision_mismatch",
                        "A same-node dependency must reference the current working declaration revision.",
                        object_ref=f"{ref.node}::{ref.name}",
                        current=str(ref.revision),
                        expected=str(revision.value.revision),
                    )
                )
            return self.runtime.foundation.ok(
                ResolvedRepoDeclDependencyProjection(
                    node_path=ref.node,
                    decl_name=ref.name,
                    module=revision.value.module or "",
                    lean_decl_name=revision.value.lean_decl_name,
                    resolved_revision=revision.value.revision,
                )
            )
        boundary = self.runtime.node.dependency.list_node_deps(repo_root, node_path=consumer_node_path)
        if not boundary.ok or boundary.value is None:
            return self.runtime.foundation.fail(boundary.issues)
        boundary_node = "Main" if ref.repo is not None else ref.node
        if not any(
            item.target_repo == ref.repo and item.target_node == boundary_node
            for item in boundary.value.deps
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_dependency_node_boundary_missing",
                    "A cross-node declaration dependency requires a matching Node dependency so Prelude imports the provider Interfaces boundary.",
                    object_ref=f"{ref.repo + '::' if ref.repo else ''}{ref.node}::{ref.name}",
                    suggested_action="Add the provider node boundary dependency before adding or capturing this declaration dependency.",
                )
            )
        target_root = Path(repo_root)
        resolved_revision = ref.revision
        if ref.repo is not None:
            compatibility = self.runtime.decl_graph.ref_compatibility.resolve_public_decl_ref(
                Path(repo_root),
                ref=ref,
                required_availability=ProofAvailability.DECLARED,
            )
            if not compatibility.ok or compatibility.value is None:
                return self.runtime.foundation.fail(compatibility.issues)
            if not compatibility.value.compatible or compatibility.value.resolved_revision is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "external_decl_dependency_unavailable",
                        "The external declaration dependency is not available through the provider public boundary.",
                        object_ref=f"{ref.repo}::{ref.node}::{ref.name}",
                        current=compatibility.value.reason,
                    )
                )
            try:
                repo_key = self.runtime.foundation.layout.ensure_safe_key(ref.repo)
            except ValueError as exc:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "decl_dependency_repo_invalid",
                        str(exc),
                        object_ref=ref.repo,
                    )
                )
            target_root = Path(repo_root).parent / repo_key
            resolved_revision = compatibility.value.resolved_revision
        decl = self.runtime.decl_graph.decl_catalog.get_decl(target_root, node_path=ref.node, name=ref.name)
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        revision = self.runtime.decl_graph.decl_catalog.get_decl_revision(
            target_root,
            node_path=ref.node,
            name=ref.name,
            revision=resolved_revision,
        )
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        return self.runtime.foundation.ok(
            ResolvedRepoDeclDependencyProjection(
                repo_key=ref.repo,
                node_path=ref.node,
                decl_name=ref.name,
                module=decl.value.module or "",
                lean_decl_name=revision.value.lean_decl_name,
                resolved_revision=resolved_revision,
            )
        )

    def _module_mismatch_issue(
        self,
        revision: DeclFileRevisionView,
        *,
        actual_module: str,
        node_path: str,
        decl_name: str,
    ) -> ServiceIssue | None:
        if revision.module == actual_module:
            return None
        return self.runtime.foundation.issue(
            "decl_module_projection_mismatch",
            "Stored Decl.module does not match the canonical native file module.",
            object_ref=f"{node_path}:{decl_name}",
            current=revision.module,
            expected=actual_module,
        )

    def _write_file_view(
        self,
        path: Path,
        text: str,
        path_view: LeanPathView,
        stage: DeclFileStage,
        *,
        changed_summary: str,
    ) -> ServiceResult[LeanFileView]:
        write = self._write_text_if_changed(path, text)
        if not write.ok or write.value is None:
            return self.runtime.foundation.fail(write.issues)
        return self.runtime.foundation.ok(
            LeanFileView(
                node_path=path_view.node_path,
                decl_name=path_view.decl_name,
                stage=stage,
                path=path_view.path,
                module=path_view.module,
                changed=write.value,
                line_count=len(text.splitlines()),
                summary=(changed_summary if write.value else "Decl-owned Lean file is already synchronized."),
            )
        )

    def _write_text_if_changed(self, path: Path, text: str) -> ServiceResult[bool]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            old = path.read_text(encoding="utf-8") if path.exists() else None
            changed = old != text
            if changed:
                path.write_text(text, encoding="utf-8")
            return self.runtime.foundation.ok(changed)
        except OSError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_file_write_failed",
                    f"Failed to write Decl-owned Lean file: {exc}",
                    details={"path": str(path)},
                )
            )

    def _read_lean_file(self, path: Path, *, object_ref: str) -> ServiceResult[str]:
        if not path.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("decl_file_missing", "Decl-owned Lean file is missing.", object_ref=object_ref, details={"path": str(path)})
            )
        try:
            return self.runtime.foundation.ok(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("decl_file_not_utf8", f"Decl-owned Lean file is not UTF-8: {exc}", object_ref=object_ref, details={"path": str(path)})
            )
        except OSError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("decl_file_read_failed", f"Failed to read Decl-owned Lean file: {exc}", object_ref=object_ref, details={"path": str(path)})
            )

    def _formal_code(self, revision: DeclFileRevisionView, stage: DeclFileStage) -> str | None:
        stage_view = revision.statement if stage == "statement" else revision.proof
        value = stage_view.formal.code if stage_view is not None and stage_view.formal is not None else None
        if value is not None and value.strip():
            return value
        return None

    def _effective_projection_stage(self, revision: DeclFileRevisionView) -> DeclFileStage | None:
        if revision.proof is not None and revision.proof.nl.text:
            return "proof"
        if revision.statement.nl.text:
            return "statement"
        return None

    def _decl_kind(self, revision: DeclFileRevisionView) -> str | None:
        return revision.kind.strip() if revision.kind.strip() else None

    def _require_nl_text(self, revision: DeclFileRevisionView, stage: DeclFileStage, *, node_path: str, decl_name: str) -> ServiceIssue | None:
        stage_view = revision.statement if stage == "statement" else revision.proof
        value = stage_view.nl.text if stage_view is not None else None
        if value is not None and value.strip():
            return None
        return self.runtime.foundation.issue(
            f"{stage}_nl_missing",
            f"{stage.capitalize()} natural-language content is required before preparing the formal Lean file.",
            object_ref=f"{node_path}:{decl_name}",
            field=f"{stage}.nl.text",
        )

    def _check_decl_kind_match(self, expected_kind: str, lean_kind: str, *, node_path: str, decl_name: str) -> ServiceResult[None]:
        normalized = self._normalize_kind(expected_kind)
        allowed = self._ALLOWED_LEAN_KINDS.get(normalized, {normalized})
        if lean_kind not in allowed:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "target_declaration_kind_mismatch",
                    "Target Lean declaration kind does not match Decl metadata kind.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=lean_kind,
                    expected=", ".join(sorted(allowed)),
                )
            )
        return self.runtime.foundation.ok(None)

    def _require_open_revision(self, revision: DeclFileRevisionView, *, node_path: str, decl_name: str) -> ServiceIssue | None:
        version_status = revision.version_status
        if version_status == "open":
            return None
        return self.runtime.foundation.issue(
            "decl_revision_not_open",
            "Preparing a formal Lean file requires the current declaration revision to be open.",
            object_ref=f"{node_path}:{decl_name}",
            field="version_status",
            current=version_status,
            expected="open",
        )

    def _supported_kind_issue(self, kind: str, *, node_path: str, decl_name: str) -> ServiceIssue | None:
        try:
            decl_kind_dir(self._normalize_kind(kind))
            return None
        except NativeModuleLayoutError:
            pass
        return self.runtime.foundation.issue(
            "decl_kind_unsupported",
            "Decl-owned Lean file preparation does not support this declaration kind.",
            object_ref=f"{node_path}:{decl_name}",
            field="kind",
            current=kind,
            expected="a supported Lean declaration kind",
        )

    def _lean_check_failed_issue(self, node_path: str, decl_name: str, stage: DeclFileStage, check: LeanCheckView) -> ServiceIssue:
        return self.runtime.foundation.issue(
            f"{stage}_lean_check_failed",
            check.message,
            object_ref=f"{node_path}:{decl_name}",
            details={"policy": check.policy},
        )

    def _missing_field_issue(self, node_path: str, decl_name: str, field: str) -> ServiceIssue:
        return self.runtime.foundation.issue(
            "decl_revision_field_missing",
            f"DeclRevision is missing required field: {field}.",
            object_ref=f"{node_path}:{decl_name}",
            field=field,
        )

    def _invalid_stage_issue(self, stage: object) -> ServiceIssue:
        return self.runtime.foundation.issue(
            "decl_file_stage_invalid",
            "Decl file stage must be statement or proof.",
            field="stage",
            current=str(stage),
            expected="statement | proof",
        )

    def _normalize_stage(self, stage: DeclFileStage | str) -> DeclFileStage | None:
        if stage in {"statement", "proof"}:
            return stage  # type: ignore[return-value]
        return None

    def _kind_dir(self, kind: str) -> str:
        return decl_kind_dir(self._normalize_kind(kind))

    def _is_theorem_like(self, kind: str) -> bool:
        return self._normalize_kind(kind) in self._THEOREM_LIKE

    def _normalize_kind(self, kind: str) -> str:
        return kind.strip().lower().replace("-", "_")
