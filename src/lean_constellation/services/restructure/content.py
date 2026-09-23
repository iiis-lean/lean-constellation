"""Explicit declaration registration and text capture for Content Agents."""

from __future__ import annotations

from pathlib import Path
from functools import wraps
import re

from lean_constellation.domain.restructure import (
    ContentWork,
    DeclKind,
    DeclRecord,
    DeclStatus,
    SectionInput,
    DeclarationSection,
    FormalSnapshot,
    RestructureStage,
    file_digest,
    utc_now_iso,
)
from lean_constellation.services.restructure.layout import RestructureLayout
from lean_constellation.services.restructure.store import RestructureStore


def content_transaction(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self.store._lock:
            return method(self, *args, **kwargs)
    return locked


class ContentGateError(ValueError):
    """Raised when Content metadata is not ready for a stage submission."""


class RestructureContentService:
    def __init__(self, store: RestructureStore, layout: RestructureLayout | None = None) -> None:
        self.store = store
        self.layout = layout or RestructureLayout(store)

    def load(self, directory: str, node_path: str) -> tuple[ContentWork, int]:
        work, version = self.store.load_content(directory, node_path, ContentWork)
        if work is None:
            raise KeyError(f"content work not found: {directory}/{node_path}")
        return work, version

    @content_transaction
    def create_decl(
        self,
        directory: str,
        module_root: str,
        node_path: str,
        *,
        name: str,
        lean_name: str,
        kind: DeclKind,
        summary: str,
        file_group: str | None = None,
        expected_version: int | None = None,
    ) -> DeclRecord:
        workspace, _ = self.store.load_workspace_plan()
        if workspace:
            repo = workspace.repos[workspace.repo_key_for_directory(directory)]
            if repo.plan is None or node_path not in {n.path for n in repo.plan.content_nodes()}:
                raise ContentGateError("declaration owner is not a planned Content node")
        work, version = self._load_or_create(directory, node_path, expected_version)
        if name in work.decls:
            raise ContentGateError(f"declaration already exists: {name}")
        path = self.layout.decl_file(directory, module_root, node_path, kind, name)
        relative = path.relative_to(self.store.repo_root(directory)).as_posix()
        path.write_text(self.layout.decl_template(kind, lean_name), encoding="utf-8")
        decl = DeclRecord(
            name=name,
            lean_name=lean_name,
            kind=kind,
            summary=summary,
            file=relative,
            file_group=file_group,
        )
        work.decls[name] = decl
        self.store.save_content(directory, node_path, work, expected_version=version)
        from .layout import refresh_projections
        refresh_projections(self.store, directory, node_path)
        return decl

    def read_decl_file(self, directory: str, node_path: str, name: str) -> str:
        work, _ = self.load(directory, node_path)
        decl = work.decls.get(name)
        if decl is None:
            raise KeyError(f"declaration not found: {name}")
        path = self._bound_path(directory, decl.file)
        return path.read_text(encoding="utf-8")

    @content_transaction
    def edit_decl_file(
        self,
        directory: str,
        node_path: str,
        name: str,
        content: str,
        *,
        expected_file_digest: str | None = None,
        stage: RestructureStage = RestructureStage.DECLARED,
    ) -> None:
        """Write a bound declaration file with an optimistic digest check.

        The service does not parse Lean.  The stage only controls whether a
        theorem-like proof placeholder may remain. Agent checks/submissions
        compile and register sources automatically.
        """

        work, _ = self.load(directory, node_path)
        decl = work.decls.get(name)
        if decl is None:
            raise KeyError(f"declaration not found: {name}")
        path = self._bound_path(directory, decl.file)
        if expected_file_digest is not None and expected_file_digest != file_digest(path.read_bytes()):
            raise ContentGateError("source file digest does not match expected digest")
        if stage in {RestructureStage.PROVED, RestructureStage.FINAL} and decl.is_theorem_like and re.search(r"\bsorry\b|sorryAx", content):
            raise ContentGateError("proved declaration may not contain a placeholder")
        path.write_text(content, encoding="utf-8")

    @content_transaction
    def register_support_file(self, directory: str, node_path: str, relative_file: str) -> ContentWork:
        work, version = self.load(directory, node_path)
        relative_file = relative_file.replace("\\", "/")
        from lean_constellation.domain.restructure import safe_relpath
        relative_file = safe_relpath(relative_file)
        path = self._bound_path(directory, relative_file)
        workspace, _ = self.store.load_workspace_plan()
        if workspace:
            repo = workspace.repos[workspace.repo_key_for_directory(directory)]
            owned = self.layout.node_root(directory, repo.module_root, node_path).resolve()
            if owned not in path.parents or path.name in {"Prelude.lean", "Interfaces.lean"}:
                raise ContentGateError("support file must belong to the current Content")
        if relative_file not in work.support_files:
            work.support_files.append(relative_file)
        self.store.save_content(directory, node_path, work, expected_version=version)
        return work

    @content_transaction
    def set_sections(
        self,
        directory: str,
        node_path: str,
        name: str,
        *,
        statement: SectionInput | None = None,
        proof: SectionInput | None = None,
        expected_version: int | None = None,
    ) -> DeclRecord:
        work, version = self.load(directory, node_path)
        if expected_version is not None and version != expected_version:
            raise ContentGateError(f"stale content version: expected {expected_version}, found {version}")
        try:
            decl = work.decls[name]
        except KeyError as exc:
            raise KeyError(f"declaration not found: {name}") from exc
        if statement is None and proof is None:
            raise ContentGateError("provide statement or proof metadata to review")
        for section_name, candidate in (("statement", statement), ("proof", proof)):
            if candidate is not None:
                candidate = SectionInput.model_validate(candidate.model_dump())
                previous = getattr(decl, section_name)
                section = DeclarationSection(**candidate.model_dump())
                section.formal = previous.formal if previous else None
                setattr(decl, section_name, section)
        from .source_contract import metadata_review_digest
        text = self.read_decl_file(directory, node_path, name)
        if statement is not None:
            decl.statement_review_digest = metadata_review_digest(decl, text, "statement")
        if proof is not None:
            decl.proof_review_digest = metadata_review_digest(decl, text, "proof")
        work.decls[name] = decl
        self.store.save_content(directory, node_path, work, expected_version=version)
        from .layout import refresh_projections
        refresh_projections(self.store, directory, node_path)
        return decl

    @content_transaction
    def capture(
        self,
        directory: str,
        node_path: str,
        name: str,
        *,
        status: DeclStatus | None = None,
        expected_file_digest: str | None = None,
        expected_version: int | None = None,
    ) -> DeclRecord:
        work, version = self.load(directory, node_path)
        if expected_version is not None and version != expected_version:
            raise ContentGateError(f"stale content version: expected {expected_version}, found {version}")
        decl = work.decls.get(name)
        if decl is None:
            raise KeyError(f"declaration not found: {name}")
        from .layout import refresh_projections
        refresh_projections(self.store, directory, node_path)
        path = self.store.repo_root(directory) / decl.file
        if not path.is_file():
            raise ContentGateError(f"declaration file does not exist: {decl.file}")
        data = path.read_bytes()
        digest = file_digest(data)
        if expected_file_digest is not None and digest != expected_file_digest:
            raise ContentGateError("source file digest does not match expected digest")
        if (status or decl.status) is DeclStatus.PROVED and decl.is_theorem_like:
            if decl.proof is None:
                decl.proof = DeclarationSection()
            decl.proof.formal = FormalSnapshot(code=data.decode("utf-8"))
        else:
            decl.statement.formal = FormalSnapshot(code=data.decode("utf-8"))
        decl.file_digest = digest
        decl.captured_at = utc_now_iso()
        if status is not None:
            if status is DeclStatus.DRAFT and decl.status in {DeclStatus.DECLARED, DeclStatus.PROVED}:
                raise ContentGateError("a captured declaration cannot regress to draft")
            decl.status = status
        work.decls[name] = decl
        self.store.save_content(directory, node_path, work, expected_version=version)
        return decl

    def check_submission(self, directory: str, node_path: str, *, stage: RestructureStage,
                         allow_declared_repair: bool = False, work: ContentWork | None = None) -> list[str]:
        if work is None:
            work, _ = self.load(directory, node_path)
        issues: list[str] = []
        workspace, _ = self.store.load_workspace_plan()
        if workspace:
            spec = workspace.repos[workspace.repo_key_for_directory(directory)]
            if spec.plan is None or node_path not in {n.path for n in spec.plan.content_nodes()}:
                return ["Content node is absent from the accepted plan"]
        if not work.decls:
            issues.append("content node has no registered declarations")
        for name, decl in work.decls.items():
            if not decl.summary or not decl.statement_nl or not decl.statement.formal:
                issues.append(f"{name}: summary, statement.nl.text and statement.formal are required")
            if decl.file_digest is None:
                issues.append(f"{name}: source capture is missing")
            else:
                path = self._bound_path(directory, decl.file)
                if not path.is_file() or file_digest(path.read_bytes()) != decl.file_digest:
                    issues.append(f"{name}: source changed after capture")
            if stage in {RestructureStage.DECLARED, RestructureStage.PROVED, RestructureStage.FINAL}:
                if decl.status not in {DeclStatus.DECLARED, DeclStatus.PROVED}:
                    issues.append(f"{name}: declaration is still draft")
            if stage in {RestructureStage.PROVED, RestructureStage.FINAL} and decl.kind in {
                DeclKind.LEMMA,
                DeclKind.THEOREM,
                DeclKind.COROLLARY,
            } and decl.status is not DeclStatus.PROVED:
                issues.append(f"{name}: theorem-like declaration is not proved")
            path = self._bound_path(directory, decl.file)
            text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
            from .source_contract import metadata_review_digest
            for section_name in (["statement", "proof"] if decl.is_theorem_like and stage in {RestructureStage.PROVED, RestructureStage.FINAL} else ["statement"]):
                section = getattr(decl, section_name)
                if section is None or not section.nl.text.strip():
                    issues.append(f"{name}: {section_name}.nl.text is required")
                else:
                    try:
                        current_review = metadata_review_digest(decl, text, section_name)
                    except ValueError as exc:
                        issues.append(f"{name}: {exc}")
                        continue
                    if getattr(decl, section_name + "_review_digest") != current_review:
                        issues.append(f"{name}: review {section_name} NL, origins and deps against current source, then set that section again (unchanged metadata is allowed)")
            current_section = decl.proof if decl.is_theorem_like and stage in {RestructureStage.PROVED, RestructureStage.FINAL} else decl.statement
            if current_section is None or current_section.formal is None or current_section.formal.code != text:
                issues.append(f"{name}: accepted formal snapshot does not match the current complete file")
            if decl.is_theorem_like and decl.statement.formal and stage in {RestructureStage.PROVED, RestructureStage.FINAL}:
                from .source_contract import contract_digest
                try:
                    if contract_digest(decl, decl.statement.formal.code) != contract_digest(decl, text):
                        issues.append(f"{name}: statement snapshot differs from current declared interface")
                except ValueError as exc:
                    issues.append(f"{name}: {exc}")
            placeholder = bool(re.search(r"\bsorry\b|sorryAx|\badmit\b", text))
            if decl.requires_real_declared_body and placeholder:
                issues.append(f"{name}: non-theorem declaration contains a placeholder")
            if stage in {RestructureStage.PROVED, RestructureStage.FINAL} and placeholder:
                issues.append(f"{name}: proved declaration contains a placeholder")
            if stage in {RestructureStage.PROVED, RestructureStage.FINAL} and decl.is_theorem_like and not decl.proof_nl:
                issues.append(f"{name}: proof_nl is required for proved theorem-like declaration")
        from .source_contract import contract_digest
        for name, digest in work.declared_baseline.items():
            decl = work.decls.get(name)
            try:
                if name.startswith("support:"):
                    path = self._bound_path(directory, name.removeprefix("support:"))
                    if not path.is_file() or file_digest(path.read_bytes()) != digest:
                        issues.append(f"{name}: declared support changed; replan required")
                    continue
                current = contract_digest(decl, self.read_decl_file(directory, node_path, name)) if decl else None
                if current != digest and not (allow_declared_repair and stage is RestructureStage.DECLARED):
                    issues.append(f"{name}: declared interface changed; replan required")
            except ValueError as exc:
                issues.append(f"{name}: {exc}")
        workspace, _ = self.store.load_workspace_plan()
        if workspace is not None:
            spec = workspace.repos[workspace.repo_key_for_directory(directory)]
            if spec.plan:
                for ref in [*spec.plan.interface_seeds, *spec.plan.main_exports, *spec.plan.nodes[node_path].exports]:
                    if ref.repo == spec.key and ref.node == node_path and ref.name not in work.decls:
                        issues.append(f"missing required interface: {ref.name}")
        return issues

    @content_transaction
    def submit(self, directory: str, node_path: str, *, stage: RestructureStage) -> ContentWork:
        issues = self.check_submission(directory, node_path, stage=stage)
        if issues:
            raise ContentGateError("; ".join(issues))
        work, version = self.load(directory, node_path)
        order = {
            RestructureStage.PLAN: 0,
            RestructureStage.DECLARED: 1,
            RestructureStage.PROVED: 2,
            RestructureStage.FINAL: 3,
        }
        if order[stage] < order[work.stage]:
            raise ContentGateError(f"content stage cannot regress from {work.stage.value} to {stage.value}")
        if not work.declared_baseline:
            if stage is not RestructureStage.DECLARED:
                raise ContentGateError("declared baseline is required before proving")
            from .source_contract import contract_digest
            try:
                work.declared_baseline = {name: contract_digest(decl, self.read_decl_file(directory, node_path, name))
                                          for name, decl in work.decls.items()}
                for relative in work.support_files:
                    path = self._bound_path(directory, relative)
                    if not path.is_file():
                        raise ContentGateError(f"missing support file: {relative}")
                    work.declared_baseline["support:" + relative] = file_digest(path.read_bytes())
            except ValueError as exc:
                raise ContentGateError(str(exc)) from exc
        work.stage = stage
        self.store.save_content(directory, node_path, work, expected_version=version)
        return work

    def _bound_path(self, directory: str, relative: str) -> Path:
        root = self.store.repo_root(directory)
        path = (root / relative).resolve()
        if root != path and root not in path.parents:
            raise ContentGateError("declaration file escapes the repository")
        return path

    def _load_or_create(self, directory: str, node_path: str, expected_version: int | None) -> tuple[ContentWork, int]:
        work, version = self.store.load_content(directory, node_path, ContentWork)
        if work is None:
            workspace, _ = self.store.load_workspace_plan()
            key = workspace.repo_key_for_directory(directory) if workspace else directory
            work = ContentWork(repo_key=key, node_path=node_path)
            version = 0
        if expected_version is not None and version != expected_version:
            raise ContentGateError(f"stale content version: expected {expected_version}, found {version}")
        return work, version


__all__ = ["ContentGateError", "RestructureContentService"]
