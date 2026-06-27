"""Decl-owned Lean file preparation, capture, and synchronization."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Protocol

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import (
    DeclFileKey,
    FoundationContext,
    FoundationService,
    GateReport,
    MutationSummaryView,
    ServiceResult,
)
from lean_constellation.services.lean_projection.annotation import AnnotationComponent
from lean_constellation.services.lean_projection.lean_check import LeanCheckComponent, LeanCheckView


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


class FormalCaptureView(StrictModel):
    node_path: str
    decl_name: str
    stage: DeclFileStage
    path: str
    module: str
    line_count: int
    check: LeanCheckView
    summary: str


class DeclFileSnapshotView(StrictModel):
    node_path: str
    decl_name: str
    stage: DeclFileStage
    path: str
    module: str
    synchronized: bool
    summary: str
    gate: GateReport


class DeclFileRevisionProvider(Protocol):
    """Minimal DeclGraph-facing provider used by DeclFileComponent."""

    def get_current_decl_revision(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[Any]:
        ...

    def save_statement_formal_snapshot(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
    ) -> ServiceResult[Any]:
        ...

    def save_proof_formal_snapshot(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
    ) -> ServiceResult[Any]:
        ...


class _MissingDeclFileRevisionProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def _missing(self, *, node_path: str, decl_name: str) -> ServiceResult[Any]:
        return self.foundation.fail(
            self.foundation.issue(
                "decl_revision_provider_missing",
                "No DeclRevision provider is configured for Decl-owned Lean file operations.",
                object_ref=f"{node_path}:{decl_name}",
            )
        )

    def get_current_decl_revision(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[Any]:
        del repo_root
        return self._missing(node_path=node_path, decl_name=decl_name)

    def save_statement_formal_snapshot(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
    ) -> ServiceResult[Any]:
        del repo_root, code, check
        return self._missing(node_path=node_path, decl_name=decl_name)

    def save_proof_formal_snapshot(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
    ) -> ServiceResult[Any]:
        del repo_root, code, check
        return self._missing(node_path=node_path, decl_name=decl_name)


class DeclFileComponent:
    """Prepare and capture controlled Decl-owned Lean files."""

    _THEOREM_LIKE = {"theorem", "lemma", "proposition", "corollary"}
    _KIND_DIRS = {
        "definition": "Defs",
        "def": "Defs",
        "defs": "Defs",
        "abbrev": "Defs",
        "type": "Types",
        "types": "Types",
        "structure": "Types",
        "class": "Types",
        "inductive": "Types",
        "instance": "Instances",
        "instances": "Instances",
        "lemma": "Lemmas",
        "lemmas": "Lemmas",
        "theorem": "Theorems",
        "theorems": "Theorems",
        "proposition": "Theorems",
        "corollary": "Theorems",
        "notation": "Defs",
        "axiom": "Defs",
    }
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
        foundation: FoundationService | None = None,
        annotation: AnnotationComponent | None = None,
        lean_check: LeanCheckComponent | None = None,
        revision_provider: DeclFileRevisionProvider | None = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.annotation = annotation or AnnotationComponent(self.foundation)
        self.lean_check = lean_check or LeanCheckComponent(self.foundation)
        self.revision_provider = revision_provider or _MissingDeclFileRevisionProvider(self.foundation)

    def derive_decl_file_path(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        kind: str,
    ) -> ServiceResult[LeanPathView]:
        kind_dir = self._kind_dir(kind)
        try:
            ctx = FoundationContext(repo_root=Path(repo_root))
            path = self.foundation.layout.decl_file_path(
                ctx,
                DeclFileKey(node_path=node_path, decl_kind=kind_dir, decl_name=decl_name),
            )
            self.foundation.layout.assert_within(Path(repo_root), path)
            relative = path.relative_to(Path(repo_root).expanduser().resolve(strict=False)).as_posix()
            module = ".".join(Path(relative).with_suffix("").parts)
        except ValueError as exc:
            return self.foundation.fail(
                self.foundation.issue(
                    "decl_file_path_invalid",
                    f"Decl-owned Lean file path cannot be derived: {exc}",
                    object_ref=f"{node_path}:{decl_name}",
                )
            )
        return self.foundation.ok(
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

    def prepare_statement_formal_file(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[LeanFileView]:
        revision = self._load_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.foundation.fail(revision.issues)
        kind = self._decl_kind(revision.value)
        if kind is None:
            return self.foundation.fail(self._missing_field_issue(node_path, decl_name, "kind"))
        nl_issue = self._require_nl_text(revision.value, "statement", node_path=node_path, decl_name=decl_name)
        if nl_issue is not None:
            return self.foundation.fail(nl_issue)
        path_view = self.derive_decl_file_path(repo_root, node_path=node_path, decl_name=decl_name, kind=kind)
        if not path_view.ok or path_view.value is None:
            return self.foundation.fail(path_view.issues)
        docstring = self.annotation.render_statement_docstring(revision.value)
        if not docstring.ok or docstring.value is None:
            return self.foundation.fail(docstring.issues)
        text = self._render_statement_file(
            node_path=node_path,
            decl_name=decl_name,
            kind=kind,
            docstring=docstring.value,
        )
        return self._write_file_view(Path(path_view.value.path), text, path_view.value, "statement", changed_summary="Prepared statement formal Lean file.")

    def prepare_proof_formal_file(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[LeanFileView]:
        revision = self._load_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.foundation.fail(revision.issues)
        kind = self._decl_kind(revision.value)
        if kind is None:
            return self.foundation.fail(self._missing_field_issue(node_path, decl_name, "kind"))
        if not self._is_theorem_like(kind):
            return self.foundation.fail(
                self.foundation.issue(
                    "decl_not_theorem_like",
                    "Proof formal files are only valid for theorem-like declarations.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=kind,
                    expected="theorem-like kind",
                )
            )
        nl_issue = self._require_nl_text(revision.value, "proof", node_path=node_path, decl_name=decl_name)
        if nl_issue is not None:
            return self.foundation.fail(nl_issue)
        statement_code = self._formal_code(revision.value, "statement")
        if statement_code is None:
            return self.foundation.fail(
                self.foundation.issue(
                    "statement_formal_snapshot_missing",
                    "Proof formal preparation requires a captured statement formal snapshot.",
                    object_ref=f"{node_path}:{decl_name}",
                    field="statement.formal.code",
                )
            )
        docstring = self.annotation.render_proof_docstring(revision.value)
        if not docstring.ok or docstring.value is None:
            return self.foundation.fail(docstring.issues)
        replaced = self._replace_target_docstring(statement_code, docstring.value)
        if not replaced.ok or replaced.value is None:
            return self.foundation.fail(replaced.issues)
        path_view = self.derive_decl_file_path(repo_root, node_path=node_path, decl_name=decl_name, kind=kind)
        if not path_view.ok or path_view.value is None:
            return self.foundation.fail(path_view.issues)
        return self._write_file_view(Path(path_view.value.path), replaced.value, path_view.value, "proof", changed_summary="Prepared proof formal Lean file from statement snapshot.")

    def capture_statement_formal_file(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[FormalCaptureView]:
        revision = self._load_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.foundation.fail(revision.issues)
        kind = self._decl_kind(revision.value)
        if kind is None:
            return self.foundation.fail(self._missing_field_issue(node_path, decl_name, "kind"))
        path_view = self.derive_decl_file_path(repo_root, node_path=node_path, decl_name=decl_name, kind=kind)
        if not path_view.ok or path_view.value is None:
            return self.foundation.fail(path_view.issues)
        file_text = self._read_lean_file(Path(path_view.value.path), object_ref=f"{node_path}:{decl_name}")
        if not file_text.ok or file_text.value is None:
            return self.foundation.fail(file_text.issues)
        docstring = self.annotation.render_statement_docstring(revision.value)
        if not docstring.ok or docstring.value is None:
            return self.foundation.fail(docstring.issues)
        gate = self.annotation.validate_docstring(file_text.value, decl_name=decl_name, stage="statement", expected_docstring=docstring.value)
        if not gate.ok or gate.value is None:
            return self.foundation.fail(gate.issues)
        if not gate.value.passed:
            return self.foundation.fail(gate.value.issues)
        location = self.annotation.locate_target_declaration(file_text.value, decl_name=decl_name)
        if not location.ok or location.value is None:
            return self.foundation.fail(location.issues)
        kind_gate = self._check_decl_kind_match(kind, location.value.kind, node_path=node_path, decl_name=decl_name)
        if not kind_gate.ok:
            return self.foundation.fail(kind_gate.issues)
        check = self.lean_check.build_statement_lean_check(repo_root, file_path=Path(path_view.value.path), decl_kind=kind)
        if not check.ok or check.value is None:
            return self.foundation.fail(check.issues)
        if check.value.status != "passed":
            return self.foundation.fail(self._lean_check_failed_issue(node_path, decl_name, "statement", check.value))
        saved = self.revision_provider.save_statement_formal_snapshot(
            Path(repo_root),
            node_path=node_path,
            decl_name=decl_name,
            code=file_text.value,
            check=check.value,
        )
        if not saved.ok:
            return self.foundation.fail(saved.issues)
        return self.foundation.ok(
            FormalCaptureView(
                node_path=node_path,
                decl_name=decl_name,
                stage="statement",
                path=path_view.value.path,
                module=path_view.value.module,
                line_count=len(file_text.value.splitlines()),
                check=check.value,
                summary="Captured statement formal whole-file snapshot.",
            )
        )

    def capture_proof_formal_file(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[FormalCaptureView]:
        revision = self._load_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.foundation.fail(revision.issues)
        kind = self._decl_kind(revision.value)
        if kind is None:
            return self.foundation.fail(self._missing_field_issue(node_path, decl_name, "kind"))
        if not self._is_theorem_like(kind):
            return self.foundation.fail(
                self.foundation.issue(
                    "decl_not_theorem_like",
                    "Proof capture is only valid for theorem-like declarations.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=kind,
                )
            )
        statement_code = self._formal_code(revision.value, "statement")
        if statement_code is None:
            return self.foundation.fail(
                self.foundation.issue(
                    "statement_formal_snapshot_missing",
                    "Proof capture requires a captured statement formal snapshot.",
                    object_ref=f"{node_path}:{decl_name}",
                    field="statement.formal.code",
                )
            )
        path_view = self.derive_decl_file_path(repo_root, node_path=node_path, decl_name=decl_name, kind=kind)
        if not path_view.ok or path_view.value is None:
            return self.foundation.fail(path_view.issues)
        file_text = self._read_lean_file(Path(path_view.value.path), object_ref=f"{node_path}:{decl_name}")
        if not file_text.ok or file_text.value is None:
            return self.foundation.fail(file_text.issues)
        docstring = self.annotation.render_proof_docstring(revision.value)
        if not docstring.ok or docstring.value is None:
            return self.foundation.fail(docstring.issues)
        doc_gate = self.annotation.validate_docstring(file_text.value, decl_name=decl_name, stage="proof", expected_docstring=docstring.value)
        if not doc_gate.ok or doc_gate.value is None:
            return self.foundation.fail(doc_gate.issues)
        if not doc_gate.value.passed:
            return self.foundation.fail(doc_gate.value.issues)
        header_gate = self.annotation.compare_theorem_header(statement_code, file_text.value, decl_name=decl_name)
        if not header_gate.ok or header_gate.value is None:
            return self.foundation.fail(header_gate.issues)
        if not header_gate.value.passed:
            return self.foundation.fail(header_gate.value.issues)
        check = self.lean_check.build_proof_lean_check(repo_root, file_path=Path(path_view.value.path))
        if not check.ok or check.value is None:
            return self.foundation.fail(check.issues)
        if check.value.status != "passed":
            return self.foundation.fail(self._lean_check_failed_issue(node_path, decl_name, "proof", check.value))
        saved = self.revision_provider.save_proof_formal_snapshot(
            Path(repo_root),
            node_path=node_path,
            decl_name=decl_name,
            code=file_text.value,
            check=check.value,
        )
        if not saved.ok:
            return self.foundation.fail(saved.issues)
        return self.foundation.ok(
            FormalCaptureView(
                node_path=node_path,
                decl_name=decl_name,
                stage="proof",
                path=path_view.value.path,
                module=path_view.value.module,
                line_count=len(file_text.value.splitlines()),
                check=check.value,
                summary="Captured proof formal whole-file snapshot.",
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
            return self.foundation.fail(self._invalid_stage_issue(stage))
        revision = self._load_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.foundation.fail(revision.issues)
        kind = self._decl_kind(revision.value)
        if kind is None:
            return self.foundation.fail(self._missing_field_issue(node_path, decl_name, "kind"))
        path_view = self.derive_decl_file_path(repo_root, node_path=node_path, decl_name=decl_name, kind=kind)
        if not path_view.ok or path_view.value is None:
            return self.foundation.fail(path_view.issues)
        snapshot = self._formal_code(revision.value, normalized_stage)
        if snapshot is None:
            return self.foundation.ok(
                self.foundation.gate_failed(
                    "decl_file_snapshot_sync",
                    self.foundation.issue(
                        "formal_snapshot_missing",
                        "No captured formal snapshot exists for the requested stage.",
                        object_ref=f"{node_path}:{decl_name}",
                        field=f"{normalized_stage}.formal.code",
                    ),
                    summary="Formal snapshot is missing.",
                )
            )
        path = Path(path_view.value.path)
        if not path.exists():
            return self.foundation.ok(
                self.foundation.gate_failed(
                    "decl_file_snapshot_sync",
                    self.foundation.issue(
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
            return self.foundation.fail(file_text.issues)
        if file_text.value != snapshot:
            return self.foundation.ok(
                self.foundation.gate_failed(
                    "decl_file_snapshot_sync",
                    self.foundation.issue(
                        "decl_file_snapshot_stale",
                        "Decl-owned Lean file was modified after the latest capture.",
                        object_ref=f"{node_path}:{decl_name}",
                        details={"path": str(path), "stage": normalized_stage},
                    ),
                    summary="Decl-owned Lean file is not synchronized with captured metadata.",
                )
            )
        return self.foundation.ok(
            self.foundation.gate_passed(
                "decl_file_snapshot_sync",
                summary=f"{normalized_stage} formal file is synchronized with captured metadata.",
            )
        )

    def sync_decl_file_after_revision_reset(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[MutationSummaryView]:
        revision = self._load_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.foundation.fail(revision.issues)
        kind = self._decl_kind(revision.value)
        if kind is None:
            return self.foundation.fail(self._missing_field_issue(node_path, decl_name, "kind"))
        path_view = self.derive_decl_file_path(repo_root, node_path=node_path, decl_name=decl_name, kind=kind)
        if not path_view.ok or path_view.value is None:
            return self.foundation.fail(path_view.issues)
        path = Path(path_view.value.path)
        proof_code = self._formal_code(revision.value, "proof")
        statement_code = self._formal_code(revision.value, "statement")
        if proof_code is not None:
            write = self._write_text_if_changed(path, proof_code)
            if not write.ok or write.value is None:
                return self.foundation.fail(write.issues)
            return self.foundation.ok(
                self.foundation.mutation_view(
                    object_ref=f"{node_path}:{decl_name}",
                    changed=write.value,
                    summary="Synchronized Decl-owned file to proof formal snapshot.",
                    changed_items=[str(path)] if write.value else [],
                )
            )
        if statement_code is not None:
            write = self._write_text_if_changed(path, statement_code)
            if not write.ok or write.value is None:
                return self.foundation.fail(write.issues)
            return self.foundation.ok(
                self.foundation.mutation_view(
                    object_ref=f"{node_path}:{decl_name}",
                    changed=write.value,
                    summary="Synchronized Decl-owned file to statement formal snapshot.",
                    changed_items=[str(path)] if write.value else [],
                )
            )
        changed = path.exists()
        try:
            if changed:
                path.unlink()
        except OSError as exc:
            return self.foundation.fail(
                self.foundation.issue(
                    "decl_file_delete_failed",
                    f"Failed to remove reset Decl-owned Lean file: {exc}",
                    object_ref=f"{node_path}:{decl_name}",
                    details={"path": str(path)},
                )
            )
        return self.foundation.ok(
            self.foundation.mutation_view(
                object_ref=f"{node_path}:{decl_name}",
                changed=changed,
                summary="Removed Decl-owned Lean file because the current revision has no formal snapshot.",
                changed_items=[str(path)] if changed else [],
            )
        )

    def remove_decl_file_for_delete(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[MutationSummaryView]:
        revision = self._load_revision(repo_root, node_path=node_path, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.foundation.fail(revision.issues)
        kind = self._decl_kind(revision.value) or "definition"
        path_view = self.derive_decl_file_path(repo_root, node_path=node_path, decl_name=decl_name, kind=kind)
        if not path_view.ok or path_view.value is None:
            return self.foundation.fail(path_view.issues)
        path = Path(path_view.value.path)
        changed = path.exists()
        try:
            if changed:
                path.unlink()
        except OSError as exc:
            return self.foundation.fail(
                self.foundation.issue(
                    "decl_file_delete_failed",
                    f"Failed to remove Decl-owned Lean file: {exc}",
                    object_ref=f"{node_path}:{decl_name}",
                    details={"path": str(path)},
                )
            )
        return self.foundation.ok(
            self.foundation.mutation_view(
                object_ref=f"{node_path}:{decl_name}",
                changed=changed,
                summary=("Removed Decl-owned Lean file." if changed else "Decl-owned Lean file was already absent."),
                changed_items=[str(path)] if changed else [],
            )
        )

    def _load_revision(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[Any]:
        return self.revision_provider.get_current_decl_revision(Path(repo_root), node_path=node_path, decl_name=decl_name)

    def _render_statement_file(self, *, node_path: str, decl_name: str, kind: str, docstring: str) -> str:
        prelude_import = f"import {node_path}.Prelude"
        lines = [
            prelude_import,
            "",
            docstring,
            self._statement_skeleton(kind, decl_name),
            "",
        ]
        return "\n".join(lines)

    def _statement_skeleton(self, kind: str, decl_name: str) -> str:
        normalized = self._normalize_kind(kind)
        if normalized in {"theorem", "lemma", "proposition", "corollary"}:
            keyword = "lemma" if normalized == "lemma" else "theorem"
            return f"{keyword} {decl_name} : True := by\n  sorry"
        if normalized in {"type", "structure"}:
            return f"structure {decl_name} where\n  value : Unit"
        if normalized == "class":
            return f"class {decl_name} where\n  value : Unit"
        if normalized == "instance":
            return f"instance {decl_name} : Inhabited Unit := ⟨()⟩"
        if normalized == "axiom":
            return f"axiom {decl_name} : True"
        return f"def {decl_name} : Unit := ()"

    def _replace_target_docstring(self, file_text: str, new_docstring: str) -> ServiceResult[str]:
        marker = self.annotation.parse_target_marker(file_text)
        if not marker.ok or marker.value is None:
            return self.foundation.fail(marker.issues)
        lines = file_text.splitlines()
        start = marker.value.docstring_start_line - 1
        end = marker.value.docstring_end_line
        new_lines = new_docstring.splitlines()
        return self.foundation.ok("\n".join([*lines[:start], *new_lines, *lines[end:]]).rstrip() + "\n")

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
            return self.foundation.fail(write.issues)
        return self.foundation.ok(
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
            return self.foundation.ok(changed)
        except OSError as exc:
            return self.foundation.fail(
                self.foundation.issue(
                    "decl_file_write_failed",
                    f"Failed to write Decl-owned Lean file: {exc}",
                    details={"path": str(path)},
                )
            )

    def _read_lean_file(self, path: Path, *, object_ref: str) -> ServiceResult[str]:
        if not path.exists():
            return self.foundation.fail(
                self.foundation.issue("decl_file_missing", "Decl-owned Lean file is missing.", object_ref=object_ref, details={"path": str(path)})
            )
        try:
            return self.foundation.ok(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError as exc:
            return self.foundation.fail(
                self.foundation.issue("decl_file_not_utf8", f"Decl-owned Lean file is not UTF-8: {exc}", object_ref=object_ref, details={"path": str(path)})
            )
        except OSError as exc:
            return self.foundation.fail(
                self.foundation.issue("decl_file_read_failed", f"Failed to read Decl-owned Lean file: {exc}", object_ref=object_ref, details={"path": str(path)})
            )

    def _formal_code(self, revision: Any, stage: DeclFileStage) -> str | None:
        value = self._get_path(revision, (stage, "formal", "code"))
        if not isinstance(value, str):
            value = self._get_path(revision, (f"{stage}_formal", "code"))
        if not isinstance(value, str):
            value = self._get_path(revision, (f"{stage}_formal_code",))
        if isinstance(value, str) and value.strip():
            return value
        return None

    def _decl_kind(self, revision: Any) -> str | None:
        for path in (("kind",), ("decl_kind",), ("decl", "kind")):
            value = self._text_or_none(self._get_path(revision, path))
            if value:
                return value
        return None

    def _require_nl_text(self, revision: Any, stage: DeclFileStage, *, node_path: str, decl_name: str) -> Any | None:
        value = self._get_path(revision, (stage, "nl", "text"))
        if isinstance(value, str) and value.strip():
            return None
        return self.foundation.issue(
            f"{stage}_nl_missing",
            f"{stage.capitalize()} natural-language content is required before preparing the formal Lean file.",
            object_ref=f"{node_path}:{decl_name}",
            field=f"{stage}.nl.text",
        )

    def _check_decl_kind_match(self, expected_kind: str, lean_kind: str, *, node_path: str, decl_name: str) -> ServiceResult[None]:
        normalized = self._normalize_kind(expected_kind)
        allowed = self._ALLOWED_LEAN_KINDS.get(normalized, {normalized})
        if lean_kind not in allowed:
            return self.foundation.fail(
                self.foundation.issue(
                    "target_declaration_kind_mismatch",
                    "Target Lean declaration kind does not match Decl metadata kind.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=lean_kind,
                    expected=", ".join(sorted(allowed)),
                )
            )
        return self.foundation.ok(None)

    def _lean_check_failed_issue(self, node_path: str, decl_name: str, stage: DeclFileStage, check: LeanCheckView) -> Any:
        return self.foundation.issue(
            f"{stage}_lean_check_failed",
            check.message,
            object_ref=f"{node_path}:{decl_name}",
            details={"policy": check.policy},
        )

    def _missing_field_issue(self, node_path: str, decl_name: str, field: str) -> Any:
        return self.foundation.issue(
            "decl_revision_field_missing",
            f"DeclRevision is missing required field: {field}.",
            object_ref=f"{node_path}:{decl_name}",
            field=field,
        )

    def _invalid_stage_issue(self, stage: object) -> Any:
        return self.foundation.issue(
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
        return self._KIND_DIRS.get(self._normalize_kind(kind), "Defs")

    def _is_theorem_like(self, kind: str) -> bool:
        return self._normalize_kind(kind) in self._THEOREM_LIKE

    def _normalize_kind(self, kind: str) -> str:
        return kind.strip().lower().replace("-", "_")

    def _get_path(self, value: Any, path: tuple[str, ...]) -> Any:
        current = value
        for key in path:
            if current is None:
                return None
            current = self._get_one(current, key)
        return current

    def _get_one(self, value: Any, key: str) -> Any:
        if isinstance(value, dict):
            return value.get(key)
        if hasattr(value, "model_dump"):
            dumped = value.model_dump(mode="python")
            if isinstance(dumped, dict):
                return dumped.get(key)
        return getattr(value, key, None)

    def _text_or_none(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return str(value)
