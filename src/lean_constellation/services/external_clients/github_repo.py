"""GitHub repository discovery and checkout wrapper."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.external_clients.process import CommandRunner, SubprocessCommandRunner


class GitHubRepoClientConfig(StrictModel):
    git_bin: str = "git"
    gh_bin: str = "gh"
    prefer_gh: bool = True
    github_token: str | None = None
    timeout_seconds: int = 180
    clone_depth: int | None = 1
    stdout_excerpt_chars: int = 8000
    stderr_excerpt_chars: int = 8000


class GitHubRepoCandidate(StrictModel):
    full_name: str
    html_url: str
    clone_url: str | None = None
    ssh_url: str | None = None
    default_branch: str | None = None
    description: str | None = None
    topics: list[str] = Field(default_factory=list)
    stars: int | None = None
    pushed_at: str | None = None
    license_spdx_id: str | None = None
    license_name: str | None = None
    evidence_summary: str | None = None


class GitHubRepoSearchResult(StrictModel):
    ok: bool
    query: str
    candidates: list[GitHubRepoCandidate] = Field(default_factory=list)
    summary: str | None = None
    issue_code: str | None = None


class GitCheckoutResult(StrictModel):
    ok: bool
    git_url: str
    checkout_path: str
    requested_revision: str | None = None
    resolved_revision: str | None = None
    default_branch: str | None = None
    summary: str | None = None
    issue_code: str | None = None


class LeanRepoCandidateProbe(StrictModel):
    checkout_path: str
    has_lakefile: bool
    has_lean_toolchain: bool
    has_entry_module: bool
    lakefile_paths: list[str] = Field(default_factory=list)
    candidate_subdirs: list[str] = Field(default_factory=list)
    entry_module_paths: list[str] = Field(default_factory=list)
    summary: str | None = None


class GitHubRepoClient:
    def __init__(
        self,
        config: GitHubRepoClientConfig | None = None,
        *,
        runner: CommandRunner | None = None,
    ) -> None:
        self.config = config or GitHubRepoClientConfig()
        self.runner = runner or SubprocessCommandRunner()

    def normalize_github_url(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("GitHub URL or slug must be non-empty")
        if value.startswith("git@github.com:"):
            value = value.removeprefix("git@github.com:")
            value = value.removesuffix(".git")
            return f"https://github.com/{value}"
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", value):
            return f"https://github.com/{value.removesuffix('.git')}"
        parsed = urlparse(value)
        if parsed.netloc == "github.com":
            path = parsed.path.strip("/").removesuffix(".git")
            parts = path.split("/")
            if len(parts) >= 2:
                return f"https://github.com/{parts[0]}/{parts[1]}"
        if value.startswith("github.com/"):
            return self.normalize_github_url(value.removeprefix("github.com/"))
        raise ValueError(f"Unsupported GitHub URL or slug: {value}")

    def search_repositories(self, query: str, limit: int = 10) -> GitHubRepoSearchResult:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        command = [
            self.config.gh_bin,
            "search",
            "repos",
            query,
            "--limit",
            str(limit),
            "--json",
            "fullName,url,description,stargazersCount,pushedAt,defaultBranch",
        ]
        result = self.runner.run(
            command,
            cwd=Path.cwd(),
            timeout_seconds=self.config.timeout_seconds,
            stdout_excerpt_chars=self.config.stdout_excerpt_chars,
            stderr_excerpt_chars=self.config.stderr_excerpt_chars,
        )
        if not result.ok:
            return GitHubRepoSearchResult(
                ok=False,
                query=query,
                summary=result.summary,
                issue_code=result.issue_code or "github_search_failed",
            )
        try:
            raw_items = json.loads(result.stdout_excerpt or "[]")
        except json.JSONDecodeError:
            return GitHubRepoSearchResult(ok=False, query=query, summary="GitHub search returned invalid JSON", issue_code="invalid_json")
        candidates = [self._candidate_from_gh_json(item) for item in raw_items[:limit]]
        return GitHubRepoSearchResult(ok=True, query=query, candidates=candidates, summary=f"Found {len(candidates)} candidates")

    def inspect_repository(self, url_or_slug: str) -> GitHubRepoCandidate:
        html_url = self.normalize_github_url(url_or_slug)
        owner_repo = self._owner_repo_from_url(html_url)
        command = [
            self.config.gh_bin,
            "repo",
            "view",
            owner_repo,
            "--json",
            "nameWithOwner,url,description,stargazerCount,pushedAt,defaultBranchRef,repositoryTopics,licenseInfo",
        ]
        result = self.runner.run(
            command,
            cwd=Path.cwd(),
            timeout_seconds=self.config.timeout_seconds,
            stdout_excerpt_chars=self.config.stdout_excerpt_chars,
            stderr_excerpt_chars=self.config.stderr_excerpt_chars,
        )
        if not result.ok:
            return GitHubRepoCandidate(
                full_name=owner_repo,
                html_url=html_url,
                clone_url=f"{html_url}.git",
                evidence_summary=f"Repository inspect failed: {result.summary}",
            )
        try:
            item = json.loads(result.stdout_excerpt or "{}")
        except json.JSONDecodeError:
            return GitHubRepoCandidate(
                full_name=owner_repo,
                html_url=html_url,
                clone_url=f"{html_url}.git",
                evidence_summary="Repository inspect returned invalid JSON.",
            )
        return self._candidate_from_gh_json(item, fallback_url=html_url, fallback_full_name=owner_repo)

    def checkout_repository(self, url: str, dest: Path, revision: str | None = None) -> GitCheckoutResult:
        git_url = self.normalize_github_url(url)
        dest = Path(dest)
        if dest.exists() and any(dest.iterdir()):
            return GitCheckoutResult(
                ok=False,
                git_url=git_url,
                checkout_path=str(dest),
                requested_revision=revision,
                summary=f"Checkout destination already exists and is not empty: {dest}",
                issue_code="checkout_path_not_empty",
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        command = [self.config.git_bin, "clone"]
        if self.config.clone_depth is not None:
            command.extend(["--depth", str(self.config.clone_depth)])
        command.extend([git_url, str(dest)])
        clone_result = self.runner.run(
            command,
            cwd=dest.parent,
            timeout_seconds=self.config.timeout_seconds,
            stdout_excerpt_chars=self.config.stdout_excerpt_chars,
            stderr_excerpt_chars=self.config.stderr_excerpt_chars,
        )
        if not clone_result.ok:
            return GitCheckoutResult(
                ok=False,
                git_url=git_url,
                checkout_path=str(dest),
                requested_revision=revision,
                summary=clone_result.summary,
                issue_code=clone_result.issue_code or "git_clone_failed",
            )
        if revision:
            checkout = self.runner.run(
                [self.config.git_bin, "checkout", revision],
                cwd=dest,
                timeout_seconds=self.config.timeout_seconds,
                stdout_excerpt_chars=self.config.stdout_excerpt_chars,
                stderr_excerpt_chars=self.config.stderr_excerpt_chars,
            )
            if not checkout.ok:
                return GitCheckoutResult(
                    ok=False,
                    git_url=git_url,
                    checkout_path=str(dest),
                    requested_revision=revision,
                    summary=checkout.summary,
                    issue_code=checkout.issue_code or "git_checkout_failed",
                )
        revision_result = self.runner.run(
            [self.config.git_bin, "rev-parse", "HEAD"],
            cwd=dest,
            timeout_seconds=self.config.timeout_seconds,
            stdout_excerpt_chars=200,
            stderr_excerpt_chars=200,
        )
        resolved = (revision_result.stdout_excerpt or "").strip() if revision_result.ok else None
        return GitCheckoutResult(
            ok=True,
            git_url=git_url,
            checkout_path=str(dest),
            requested_revision=revision,
            resolved_revision=resolved,
            summary="Repository checked out",
        )

    def probe_lean_repo(self, path: Path) -> LeanRepoCandidateProbe:
        root = Path(path)
        lakefiles = [item for pattern in ("lakefile.lean", "lakefile.toml") for item in root.rglob(pattern)]
        toolchains = list(root.rglob("lean-toolchain"))
        candidate_subdirs = sorted({str(item.parent.relative_to(root)) for item in lakefiles if item.parent != root})
        has_toolchain = (root / "lean-toolchain").exists()
        entry_module_paths = self._find_entry_module_paths(root=root, lakefiles=lakefiles)
        if not lakefiles:
            summary = "No lakefile found"
        elif entry_module_paths:
            summary = "Lean repo candidate found with lakefile and entry module"
        else:
            summary = "Lean repo candidate found, but no entry module was detected"
        return LeanRepoCandidateProbe(
            checkout_path=str(root),
            has_lakefile=bool(lakefiles),
            has_lean_toolchain=has_toolchain or bool(toolchains),
            has_entry_module=bool(entry_module_paths),
            lakefile_paths=[str(item.relative_to(root)) for item in sorted(lakefiles)],
            candidate_subdirs=candidate_subdirs,
            entry_module_paths=entry_module_paths,
            summary=summary,
        )

    def _find_entry_module_paths(self, *, root: Path, lakefiles: list[Path]) -> list[str]:
        entry_paths: set[str] = set()
        for lakefile in sorted(lakefiles):
            base = lakefile.parent
            module_names = ["Main"]
            module_names.extend(self._package_names_from_lakefile(lakefile))
            for module_name in dict.fromkeys(module_names):
                for path in self._module_entry_paths(base, module_name):
                    try:
                        entry_paths.add(str(path.relative_to(root)))
                    except ValueError:
                        continue
        return sorted(entry_paths)

    def _package_names_from_lakefile(self, lakefile: Path) -> list[str]:
        try:
            text = lakefile.read_text(encoding="utf-8")
        except OSError:
            return []

        names: list[str] = []
        package_match = re.search(
            r"\bpackage\s+(?:«([^»]+)»|\"([^\"]+)\"|'([^']+)'|([A-Za-z0-9_.-]+))",
            text,
        )
        if package_match:
            name = next((group for group in package_match.groups() if group), None)
            if name:
                names.append(name)
        toml_match = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"', text)
        if toml_match:
            names.append(toml_match.group(1))
        return [name for name in dict.fromkeys(names) if self._is_reasonable_module_name(name)]

    def _is_reasonable_module_name(self, name: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", name))

    def _module_entry_paths(self, base: Path, module_name: str) -> list[Path]:
        module_path = base.joinpath(*module_name.split("."))
        paths: list[Path] = []
        file_path = module_path.with_suffix(".lean")
        if file_path.is_file():
            paths.append(file_path)
        if module_path.is_dir():
            paths.extend(sorted(path for path in module_path.rglob("*.lean") if path.is_file()))
        return paths

    def _candidate_from_gh_json(
        self,
        item: dict[str, object],
        *,
        fallback_url: str | None = None,
        fallback_full_name: str | None = None,
    ) -> GitHubRepoCandidate:
        full_name = str(item.get("fullName") or item.get("nameWithOwner") or fallback_full_name or "")
        html_url = str(item.get("url") or fallback_url or self.normalize_github_url(full_name))
        topics_raw = item.get("repositoryTopics") or []
        topics: list[str] = []
        if isinstance(topics_raw, list):
            for topic in topics_raw:
                if isinstance(topic, dict):
                    node = topic.get("topic") if isinstance(topic.get("topic"), dict) else topic
                    name = node.get("name") if isinstance(node, dict) else None
                    if name:
                        topics.append(str(name))
                elif topic:
                    topics.append(str(topic))
        default_branch = item.get("defaultBranch")
        if not default_branch and isinstance(item.get("defaultBranchRef"), dict):
            default_branch = item["defaultBranchRef"].get("name")  # type: ignore[index]
        license_info = item.get("licenseInfo")
        license_spdx_id: str | None = None
        license_name: str | None = None
        if isinstance(license_info, dict):
            raw_spdx_id = license_info.get("spdxId")
            raw_name = license_info.get("name")
            license_spdx_id = str(raw_spdx_id) if raw_spdx_id else None
            license_name = str(raw_name) if raw_name else None
        return GitHubRepoCandidate(
            full_name=full_name,
            html_url=html_url,
            clone_url=f"{html_url}.git",
            default_branch=str(default_branch) if default_branch else None,
            description=str(item.get("description")) if item.get("description") is not None else None,
            topics=topics,
            stars=int(item.get("stargazersCount") or item.get("stargazerCount") or 0),
            pushed_at=str(item.get("pushedAt")) if item.get("pushedAt") is not None else None,
            license_spdx_id=license_spdx_id,
            license_name=license_name,
        )

    def _owner_repo_from_url(self, html_url: str) -> str:
        parsed = urlparse(html_url)
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2:
            raise ValueError(f"Cannot extract owner/repo from {html_url}")
        return f"{parts[0]}/{parts[1]}"
