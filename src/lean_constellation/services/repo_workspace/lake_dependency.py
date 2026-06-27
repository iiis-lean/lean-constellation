"""Lake project skeleton and dependency operations."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.preparation import UpstreamDependencyInput
from lean_constellation.domain.repo import RepoFormat
from lean_constellation.services.external_clients import ExternalClientService
from lean_constellation.services.external_clients.lake_command import LakeCommandSummaryView, LeanCheckSummaryView
from lean_constellation.services.foundation import FoundationContext, FoundationService, GateReport, ServiceResult
from lean_constellation.services.repo_workspace.repo_metadata import RepoMetadataComponent


class LakeDependencyEntry(StrictModel):
    name: str
    source: str | None = None
    path: str | None = None
    git: str | None = None
    rev: str | None = None
    subdir: str | None = None
    raw: str | None = None


class LakeDependencyView(StrictModel):
    repo_root: str
    lakefile_path: str | None = None
    dependencies: list[LakeDependencyEntry] = Field(default_factory=list)
    summary: str


class LakeDependencyAttachView(StrictModel):
    consumer_repo_root: str
    provider_repo_key: str
    dependency: LakeDependencyEntry
    changed: bool
    lake_update_summary: str | None = None
    summary: str


class RepoSkeletonView(StrictModel):
    repo_format: RepoFormat = RepoFormat.NATIVE
    project_name: str
    lake_check_summary: str | None = None
    next_entry_flow: str = "native_repo_preparation"
    summary: str
    written_files: list[str] = Field(default_factory=list)


class AdapterSetupView(StrictModel):
    repo_format: RepoFormat = RepoFormat.ADAPTER
    upstream_summary: str
    lake_check_summary: str | None = None
    trusted_build: bool = False
    next_entry_flow: str = "adapter_repo_preparation"
    summary: str
    written_files: list[str] = Field(default_factory=list)


class LakeDependencyComponent:
    """Create deterministic Lean project skeletons and maintain Lake deps."""

    def __init__(
        self,
        foundation: FoundationService,
        external: ExternalClientService,
        metadata: RepoMetadataComponent,
    ) -> None:
        self.foundation = foundation
        self.external = external
        self.metadata = metadata

    def parse_lake_dependencies(self, repo_root: Path) -> ServiceResult[LakeDependencyView]:
        lakefile = self._lakefile(repo_root)
        if lakefile is None:
            return self.foundation.ok(
                LakeDependencyView(repo_root=str(Path(repo_root)), summary="No lakefile found.")
            )
        text = lakefile.read_text(encoding="utf-8", errors="replace")
        if lakefile.name == "lakefile.toml":
            deps = self._parse_toml_deps(text)
        else:
            deps = self._parse_lean_deps(text)
        return self.foundation.ok(
            LakeDependencyView(
                repo_root=str(Path(repo_root)),
                lakefile_path=str(lakefile),
                dependencies=deps,
                summary=f"Found {len(deps)} Lake dependencies.",
            )
        )

    def attach_workspace_repo_dependency(
        self,
        consumer_repo_root: Path,
        *,
        provider_repo_key: str,
    ) -> ServiceResult[LakeDependencyAttachView]:
        provider_repo_key = self.foundation.layout.ensure_safe_key(provider_repo_key)
        consumer_root = Path(consumer_repo_root)
        provider_root = consumer_root.parent / provider_repo_key
        if not provider_root.exists():
            return self.foundation.fail(
                self.foundation.issue(
                    "provider_repo_not_found",
                    f"Provider repo does not exist in workspace: {provider_repo_key}",
                    object_ref=str(provider_root),
                )
            )
        lakefile = consumer_root / "lakefile.toml"
        if not lakefile.exists():
            return self.foundation.fail(
                self.foundation.issue(
                    "lakefile_not_found",
                    "attach_workspace_repo_dependency currently requires lakefile.toml.",
                    object_ref=str(lakefile),
                )
            )
        deps = self.parse_lake_dependencies(consumer_root)
        if not deps.ok or deps.value is None:
            return self.foundation.fail(deps.issues)
        relative = provider_root.relative_to(consumer_root) if provider_root.is_relative_to(consumer_root) else None
        rel_path = relative.as_posix() if relative is not None else self._relative_path(provider_root, consumer_root)
        for dep in deps.value.dependencies:
            if dep.name == provider_repo_key:
                return self.foundation.fail(
                    self.foundation.issue(
                        "dependency_already_attached",
                        f"Lake dependency already attached: {provider_repo_key}",
                        object_ref=str(lakefile),
                    )
                )
        block = f'\n[[require]]\nname = "{provider_repo_key}"\npath = "{rel_path}"\n'
        lakefile.write_text(lakefile.read_text(encoding="utf-8") + block, encoding="utf-8")
        update = self.run_lake_update(consumer_root)
        if not update.ok or update.value is None:
            return self.foundation.fail(update.issues)
        dep = LakeDependencyEntry(name=provider_repo_key, source="path", path=rel_path)
        return self.foundation.ok(
            LakeDependencyAttachView(
                consumer_repo_root=str(consumer_root),
                provider_repo_key=provider_repo_key,
                dependency=dep,
                changed=True,
                lake_update_summary=update.value.summary,
                summary=f"Attached workspace provider dependency: {provider_repo_key}.",
            )
        )

    def initialize_native_repo_skeleton(
        self,
        repo_root: Path,
        *,
        project_name: str,
        lean_toolchain: str | None = None,
    ) -> ServiceResult[RepoSkeletonView]:
        normalized = self._normalize_module_name(project_name)
        if not normalized.ok or normalized.value is None:
            return self.foundation.fail(normalized.issues)
        project_name = normalized.value
        repo_root = Path(repo_root)
        ensured = self.metadata.ensure_repo_model(repo_root)
        if not ensured.ok:
            return self.foundation.fail(ensured.issues)
        fmt = self.metadata.set_repo_format(
            repo_root,
            repo_format=RepoFormat.NATIVE,
            reason="Initialize native Lean project skeleton.",
        )
        if not fmt.ok:
            return self.foundation.fail(fmt.issues)
        written = self._write_native_skeleton(repo_root, project_name, lean_toolchain)
        build = self.run_lake_build(repo_root)
        build_summary = build.value.summary if build.ok and build.value is not None else self._issue_summary(build.issues)
        return self.foundation.ok(
            RepoSkeletonView(
                project_name=project_name,
                lake_check_summary=build_summary,
                summary=f"Initialized native Lean project skeleton for {project_name}.",
                written_files=[str(path) for path in written],
            )
        )

    def initialize_adapter_repo_skeleton(
        self,
        repo_root: Path,
        *,
        project_name: str,
        upstream: UpstreamDependencyInput,
    ) -> ServiceResult[AdapterSetupView]:
        normalized = self._normalize_module_name(project_name)
        if not normalized.ok or normalized.value is None:
            return self.foundation.fail(normalized.issues)
        project_name = normalized.value
        repo_root = Path(repo_root)
        ensured = self.metadata.ensure_repo_model(repo_root)
        if not ensured.ok:
            return self.foundation.fail(ensured.issues)
        fmt = self.metadata.set_repo_format(
            repo_root,
            repo_format=RepoFormat.ADAPTER,
            reason="Initialize adapter Lean project skeleton.",
        )
        if not fmt.ok:
            return self.foundation.fail(fmt.issues)
        written = self._write_native_skeleton(repo_root, project_name, None)
        lakefile = repo_root / "lakefile.toml"
        package = upstream.package_name or self._package_from_git_url(upstream.git_url)
        dep_block = f'\n[[require]]\nname = "{package}"\ngit = "{upstream.git_url}"\n'
        if upstream.revision:
            dep_block += f'rev = "{upstream.revision}"\n'
        if upstream.subdir:
            dep_block += f'subDir = "{upstream.subdir}"\n'
        lakefile.write_text(lakefile.read_text(encoding="utf-8") + dep_block, encoding="utf-8")
        written.append(lakefile)
        update = self.run_lake_update(repo_root)
        module = upstream.module_name or package
        build = self.run_lake_build(repo_root, target=module)
        check = self.run_minimal_import_check(repo_root, module=module)
        summaries = [
            part
            for part in [
                update.value.summary if update.ok and update.value else self._issue_summary(update.issues),
                build.value.summary if build.ok and build.value else self._issue_summary(build.issues),
                check.value.summary if check.ok and check.value else self._issue_summary(check.issues),
            ]
            if part
        ]
        return self.foundation.ok(
            AdapterSetupView(
                upstream_summary=upstream.evidence_summary or f"Adapter upstream: {upstream.git_url}",
                lake_check_summary="; ".join(summaries) if summaries else None,
                trusted_build=bool(update.ok and build.ok and check.ok and check.value and check.value.ok),
                summary=f"Initialized adapter Lean project skeleton for {project_name}.",
                written_files=[str(path) for path in written],
            )
        )

    def check_native_repo_skeleton(self, repo_root: Path) -> ServiceResult[GateReport]:
        repo_root = Path(repo_root)
        project_name = self._project_name_from_lakefile(repo_root)
        project_root = repo_root / project_name
        issues = []
        required = [
            repo_root / "lakefile.toml",
            repo_root / "lean-toolchain",
            repo_root / f"{project_name}.lean",
            project_root / "Main" / "Prelude.lean",
            project_root / "Main" / "Interfaces.lean",
            repo_root / ".lean_constellation",
        ]
        for path in required:
            if not path.exists():
                issues.append(
                    self.foundation.issue(
                        "native_skeleton_missing_file",
                        f"Native skeleton file is missing: {path.name}",
                        object_ref=str(path),
                    )
                )
        if issues:
            return self.foundation.ok(
                self.foundation.gate_failed("native_repo_skeleton", issues, summary=f"{len(issues)} files missing.")
            )
        return self.foundation.ok(
            self.foundation.gate_passed("native_repo_skeleton", summary="Native repo skeleton is present.")
        )

    def run_lake_update(self, repo_root: Path) -> ServiceResult[LakeCommandSummaryView]:
        result = self.external.lake.run_lake_update(Path(repo_root))
        summary = self.external.lake.summarize_command_result(result)
        if not result.ok:
            return self.foundation.fail(
                self.foundation.issue(
                    "lake_update_failed",
                    summary.summary,
                    object_ref=str(repo_root),
                    details={"exit_code": str(summary.exit_code), "stderr": summary.stderr_excerpt or ""},
                )
            )
        return self.foundation.ok(summary)

    def run_lake_build(self, repo_root: Path, *, target: str | None = None) -> ServiceResult[LakeCommandSummaryView]:
        result = self.external.lake.run_lake_build(Path(repo_root), target=target)
        summary = self.external.lake.summarize_command_result(result)
        if not result.ok:
            return self.foundation.fail(
                self.foundation.issue(
                    "lake_build_failed",
                    summary.summary,
                    object_ref=str(repo_root),
                    details={"exit_code": str(summary.exit_code), "stderr": summary.stderr_excerpt or ""},
                )
            )
        return self.foundation.ok(summary)

    def run_minimal_import_check(self, repo_root: Path, *, module: str) -> ServiceResult[LeanCheckSummaryView]:
        result = self.external.lake.run_minimal_import_check(Path(repo_root), module)
        if not result.ok:
            return self.foundation.fail(
                self.foundation.issue(
                    "minimal_import_check_failed",
                    result.summary,
                    object_ref=str(repo_root),
                    details={"module": module, "diagnostics": result.diagnostics_excerpt or ""},
                )
            )
        return self.foundation.ok(result)

    def _write_native_skeleton(self, repo_root: Path, project_name: str, lean_toolchain: str | None) -> list[Path]:
        repo_root.mkdir(parents=True, exist_ok=True)
        (repo_root / ".lean_constellation").mkdir(parents=True, exist_ok=True)
        main_dir = repo_root / project_name / "Main"
        main_dir.mkdir(parents=True, exist_ok=True)
        files: dict[Path, str] = {
            repo_root / "lakefile.toml": (
                f'name = "{project_name}"\n'
                'version = "0.1.0"\n'
                f'defaultTargets = ["{project_name}"]\n\n'
                '[[lean_lib]]\n'
                f'name = "{project_name}"\n'
            ),
            repo_root / "lean-toolchain": (lean_toolchain or "leanprover/lean4:stable") + "\n",
            repo_root / f"{project_name}.lean": f"import {project_name}.Main.Prelude\nimport {project_name}.Main.Interfaces\n",
            main_dir / "Prelude.lean": "import Mathlib\n",
            main_dir / "Interfaces.lean": f"import {project_name}.Main.Prelude\n",
            main_dir / "Basic.lean": f"import {project_name}.Main.Prelude\n",
        }
        written: list[Path] = []
        for path, text in files.items():
            if not path.exists() or path.read_text(encoding="utf-8", errors="replace") != text:
                path.write_text(text, encoding="utf-8")
                written.append(path)
        return written

    def _lakefile(self, repo_root: Path) -> Path | None:
        repo_root = Path(repo_root)
        for name in ("lakefile.toml", "lakefile.lean"):
            path = repo_root / name
            if path.exists():
                return path
        return None

    def _parse_toml_deps(self, text: str) -> list[LakeDependencyEntry]:
        deps: list[LakeDependencyEntry] = []
        for block in re.split(r"(?m)^\s*\[\[require\]\]\s*$", text)[1:]:
            values: dict[str, str] = {}
            for line in block.splitlines():
                match = re.match(r'\s*([A-Za-z0-9_.-]+)\s*=\s*"([^"]*)"', line)
                if match:
                    values[match.group(1)] = match.group(2)
            name = values.get("name")
            if name:
                source = "path" if "path" in values else "git" if "git" in values else None
                deps.append(
                    LakeDependencyEntry(
                        name=name,
                        source=source,
                        path=values.get("path"),
                    git=values.get("git"),
                    rev=values.get("rev") or values.get("revision"),
                    subdir=values.get("subDir") or values.get("subdir"),
                )
            )
        return deps

    def _project_name_from_lakefile(self, repo_root: Path) -> str:
        lakefile = repo_root / "lakefile.toml"
        if lakefile.exists():
            text = lakefile.read_text(encoding="utf-8", errors="replace")
            match = re.search(r'(?m)^\s*name\s*=\s*"([A-Za-z][A-Za-z0-9_]*)"\s*$', text)
            if match:
                return match.group(1)
        return "Main"

    def _parse_lean_deps(self, text: str) -> list[LakeDependencyEntry]:
        deps: list[LakeDependencyEntry] = []
        for match in re.finditer(r'require\s+([A-Za-z0-9_.-]+)\s+from\s+("[^"]+"|git\s+"[^"]+"|path\s+"[^"]+")', text):
            name = match.group(1)
            raw = match.group(0)
            git_match = re.search(r'git\s+"([^"]+)"', raw)
            path_match = re.search(r'path\s+"([^"]+)"', raw)
            deps.append(
                LakeDependencyEntry(
                    name=name,
                    source="git" if git_match else "path" if path_match else None,
                    git=git_match.group(1) if git_match else None,
                    path=path_match.group(1) if path_match else None,
                    raw=raw,
                )
            )
        return deps

    def _normalize_module_name(self, value: str) -> ServiceResult[str]:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
            return self.foundation.fail(
                self.foundation.issue(
                    "invalid_lean_project_name",
                    "Lean project/module name must start with a letter and contain only letters, digits, or underscores.",
                    current=value,
                )
            )
        return self.foundation.ok(value)

    @staticmethod
    def _relative_path(target: Path, base: Path) -> str:
        import os

        return os.path.relpath(target, start=base)

    @staticmethod
    def _package_from_git_url(url: str) -> str:
        name = url.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return re.sub(r"[^A-Za-z0-9_]", "_", name) or "upstream"

    @staticmethod
    def _issue_summary(issues: list[object]) -> str:
        return "; ".join(getattr(issue, "message", str(issue)) for issue in issues)
