"""Complete module builds in an isolated, reusable repo build directory."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from lean_constellation.domain.restructure import BuildReceipt, RestructureStage, file_digest
from lean_constellation.services.restructure.store import RestructureStore, metadata_lock


@dataclass(frozen=True)
class BuildView:
    operation_id: str
    root: Path
    files: dict[str, str]


@dataclass
class BuildResult:
    receipt: BuildReceipt
    view: BuildView
    output: str = ""


class RestructureBuildService:
    def __init__(self, store: RestructureStore) -> None:
        self.store = store

    @staticmethod
    def input_files(root: Path):
        for current, directories, files in os.walk(root):
            directories[:] = sorted(d for d in directories if d not in {".lake", ".agent_runtime", ".git", ".lean_constellation"})
            if any((Path(current) / d).is_symlink() for d in directories):
                raise ValueError("build source directories must not be symlinks")
            for name in sorted(files):
                yield Path(current) / name

    def freeze_view(self, directory: str, *, operation_id: str | None = None) -> BuildView:
        operation_id = operation_id or f"build_{uuid.uuid4().hex}"
        if Path(operation_id).name != operation_id or operation_id in {".", ".."}:
            raise ValueError("operation_id must be a single path component")
        source = self.store.repo_root(directory)
        root = self.store.repo_metadata_root(directory) / "build_views" / operation_id
        files = {}
        with self.store._lock:
            root.mkdir(parents=True, exist_ok=False)
            for path in self.input_files(source):
                relative = path.relative_to(source)
                if any(p in {".lake", ".agent_runtime", ".git", ".lean_constellation"} for p in relative.parts):
                    continue
                if path.is_symlink():
                    raise ValueError(f"build input must not be a symlink: {relative}")
                if path.is_file():
                    destination = root / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    data = path.read_bytes()
                    destination.write_bytes(data)
                    files[relative.as_posix()] = file_digest(data)
        return BuildView(operation_id, root, files)

    def _sync_build_directory(self, directory: str, view: BuildView) -> Path:
        root = self.store.repo_metadata_root(directory) / "build_work"
        root.mkdir(parents=True, exist_ok=True)
        environment_changed = any((root / relative).is_file() and
            file_digest((root / relative).read_bytes()) != digest
            for relative, digest in view.files.items() if relative in {"lakefile.toml", "lakefile.lean", "lean-toolchain"})
        # Preserve Lake's resolved manifest when the template did not supply one.
        if environment_changed and "lake-manifest.json" not in view.files:
            (root / "lake-manifest.json").unlink(missing_ok=True)
        # Preserve .lake and unchanged source mtimes; remove obsolete modules.
        for path in self.input_files(root):
            relative = path.relative_to(root)
            if ".lake" not in relative.parts and relative.as_posix() != "lake-manifest.json" and path.is_file() and relative.as_posix() not in view.files:
                path.unlink()
        for relative, digest in view.files.items():
            destination = root / relative
            if destination.is_file() and file_digest(destination.read_bytes()) == digest:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(view.root / relative, destination)
        return root

    def build(self, directory: str, *, operation_id=None, targets=None,
              stage=RestructureStage.PROVED, repo_key=None, provider_refs=None,
              allow_declared_repair=False) -> BuildResult:
        stage = RestructureStage(stage)
        lock = metadata_lock(self.store.repo_metadata_root(directory) / "build.lock")
        with lock:
            with self.store._lock:
                catalogue_findings = self._validate_catalogue(directory, stage, allow_declared_repair=allow_declared_repair)
                view = self.freeze_view(directory, operation_id=operation_id)
            findings = self._validate_provider_refs(provider_refs or {}) if stage is RestructureStage.FINAL else []
            findings.extend(catalogue_findings)
            modules = sorted(relative[:-5].replace("/", ".") for relative in view.files
                             if relative.endswith(".lean") and relative != "lakefile.lean")
            if not modules:
                findings.append("build inventory contains no Lean modules")
            output = ""
            if not findings:
                root = self._sync_build_directory(directory, view)
                log = self.store.repo_metadata_root(directory) / "builds" / f"{view.operation_id}.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                command = ["lake", "build", *dict.fromkeys([*(targets or []), *("+" + m for m in modules)])]
                with log.open("w") as handle:
                    proc = subprocess.run(command, cwd=root, stdout=handle, stderr=subprocess.STDOUT, check=False)
                output = log.read_text(errors="replace")
                if proc.returncode:
                    # Keep concise errors in the Flow; the full report stays on disk.
                    from .checks import diagnostic_summary
                    findings.extend([item[:6000] for item in diagnostic_summary(output)[:20]] or ["Build failed; inspect its durable log."])
                else:
                    audit = root / "LCRestructureAudit.lean"
                    try:
                        audit.write_text(self._audit_source(modules, final=stage in {RestructureStage.PROVED, RestructureStage.FINAL}))
                        with log.open("a") as handle:
                            proc = subprocess.run(["lake", "env", "lean", str(audit)], cwd=root,
                                                  stdout=handle, stderr=subprocess.STDOUT, check=False)
                        output = log.read_text(errors="replace")
                        if proc.returncode:
                            from .checks import diagnostic_summary
                            findings.extend([item[:6000] for item in diagnostic_summary(output)[:20]] or ["Audit failed; inspect its durable log."])
                    finally:
                        # This is a build-only audit harness.  It must never become
                        # part of the source tree or a later release candidate.
                        audit.unlink(missing_ok=True)
            live = self.store.repo_root(directory)
            current_files = {p.relative_to(live).as_posix(): file_digest(p.read_bytes()) for p in self.input_files(live)
                             if p.is_file() and not any(part in {".lake", ".agent_runtime", ".git", ".lean_constellation"}
                                                        for part in p.relative_to(live).parts)}
            if current_files != view.files:
                findings.append("build inputs changed during compilation; retry the current candidate")
            receipt = BuildReceipt(artifact_id=view.operation_id, operation_id=view.operation_id,
                stage=stage, repo_key=repo_key, success=not findings, diagnostics=findings,
                provider_refs=provider_refs or {}, files=view.files, module_inventory=modules)
            return BuildResult(receipt=receipt, view=view, output=output)

    def _validate_catalogue(self, directory, stage, *, allow_declared_repair=False):
        from .content import RestructureContentService
        workspace, _ = self.store.load_workspace_plan()
        if workspace is None:
            return []
        repo = workspace.repos[workspace.repo_key_for_directory(directory)]
        if repo.plan is None:
            return []
        content = RestructureContentService(self.store)
        issues, declared = [], {}
        for node in repo.plan.content_nodes():
            try:
                work, _ = content.load(directory, node.path)
                issues.extend(content.check_submission(directory, node.path, stage=stage,
                                                       allow_declared_repair=allow_declared_repair))
                declared.update({(node.path, name): decl for name, decl in work.decls.items()})
            except KeyError:
                issues.append(f"missing Content: {node.path}")
        for ref in [*repo.plan.interface_seeds, *repo.plan.main_exports]:
            matches = [decl for (node, name), decl in declared.items()
                       if ref.repo == repo.key and name == ref.name and (ref.node is None or node == ref.node)]
            if len(matches) != 1:
                issues.append(f"required interface is missing or ambiguous: {ref.name}")
        return issues

    @staticmethod
    def _audit_source(modules: list[str], *, final: bool) -> str:
        imports = "\n".join(f"import {module}" for module in modules)
        names = ", ".join(json.dumps(m) for m in modules)
        test = '''
        let axioms ← Lean.collectAxioms name
        for ax in axioms do
          if !(["propext", "Classical.choice", "Quot.sound"].contains ax.toString) then
            Lean.logError m!"{name}: forbidden axiom {ax}"
''' if final else '''
        match info with
        | .thmInfo _ => pure ()
        | .axiomInfo _ => Lean.logError m!"{name}: new axiom in declared output"
        | _ =>
          if info.type.hasSorry || (info.value? true).any Lean.Expr.hasSorry then
            Lean.logError m!"{name}: non-theorem placeholder"
'''
        return f'''import Lean
{imports}
open Lean Elab Command
run_cmd do
  let env ← getEnv
  let modules : List String := [{names}]
  for (name, info) in env.constants.toList do
    if let some index := env.getModuleIdxFor? name then
      if modules.contains (env.allImportedModuleNames[index]!).toString then
{test}
'''

    def _validate_provider_refs(self, provider_refs):
        findings = []
        workspace, _ = self.store.load_workspace_plan()
        if workspace is None:
            return ["provider refs require a prepared restructure workspace"]
        for key, operation_id in provider_refs.items():
            provider = workspace.repos.get(key)
            if provider is None:
                findings.append(f"unknown provider repo: {key}")
                continue
            receipt = self.store.load_build_receipt(provider.directory, operation_id)
            if receipt is None:
                findings.append(f"provider pin is missing: {key}/{operation_id}")
            elif not receipt.success or receipt.stage is not RestructureStage.FINAL:
                findings.append(f"provider pin is not a successful final build: {key}/{operation_id}")
            elif not (self.store.repo_metadata_root(provider.directory) / "build_views" / operation_id).is_dir():
                findings.append(f"provider build view is missing: {key}/{operation_id}")
        return findings

    def audit_placeholders(self, directory: str, *, final=False):
        """Optional early diagnostics; the compiled audit is the acceptance gate."""
        findings = []
        root = self.store.repo_root(directory)
        for path in root.rglob("*.lean"):
            if any(p in {".lean_constellation", ".lake"} for p in path.relative_to(root).parts):
                continue
            text = path.read_text(errors="replace")
            if final and re.search(r"\bsorry\b|sorryAx", text):
                findings.append(f"{path.relative_to(root)}: placeholder remains")
            elif not final:
                for number, line in enumerate(text.splitlines(), 1):
                    if re.search(r"\b(def|abbrev|structure|class|inductive|instance)\b", line) and re.search(r"\bsorry\b", line):
                        findings.append(f"{path.relative_to(root)}:{number + 1}: non-theorem placeholder")
        return findings


__all__ = ["BuildResult", "BuildView", "RestructureBuildService"]
