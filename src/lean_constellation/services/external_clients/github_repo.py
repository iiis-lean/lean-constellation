"""GitHub repository discovery and checkout wrapper."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from urllib.parse import quote, urlparse

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
    primary_language: str | None = None
    languages: list[str] = Field(default_factory=list)
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


class GitHubTreeEntry(StrictModel):
    path: str
    type: str
    size: int | None = None
    sha: str | None = None


class GitHubRepositoryTreeView(StrictModel):
    git_url: str
    revision: str | None = None
    resolved_revision: str | None = None
    recursive: bool = True
    path_prefix: str | None = None
    entries: list[GitHubTreeEntry] = Field(default_factory=list)
    truncated: bool = False
    revision_issue_code: str | None = None
    summary: str | None = None
    issue_code: str | None = None


class GitHubRepositoryFileView(StrictModel):
    git_url: str
    path: str
    revision: str | None = None
    encoding: str | None = None
    size: int | None = None
    content_excerpt: str | None = None
    truncated: bool = False
    summary: str | None = None
    issue_code: str | None = None


class GitHubCodeSearchMatch(StrictModel):
    repository: str | None = None
    path: str
    url: str | None = None
    text_matches: list[str] = Field(default_factory=list)


class GitHubCodeSearchResult(StrictModel):
    ok: bool
    query: str
    repo: str | None = None
    matches: list[GitHubCodeSearchMatch] = Field(default_factory=list)
    summary: str | None = None
    issue_code: str | None = None


class GitHubLeanRepoProbeView(StrictModel):
    git_url: str
    normalized_git_url: str
    requested_revision: str | None = None
    resolved_revision: str | None = None
    requested_subdir: str | None = None
    is_lean_project: bool
    has_lakefile: bool
    has_lean_toolchain: bool
    has_lean_manifest: bool = False
    has_lean_files: bool = False
    primary_language: str | None = None
    languages: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    lean_signals: list[str] = Field(default_factory=list)
    lean_file_paths: list[str] = Field(default_factory=list)
    is_mathlib_repository: bool = False
    adapter_candidate: bool = False
    lakefile_paths: list[str] = Field(default_factory=list)
    lean_toolchain_paths: list[str] = Field(default_factory=list)
    candidate_subdirs: list[str] = Field(default_factory=list)
    selected_subdir: str | None = None
    package_name: str | None = None
    likely_import_modules: list[str] = Field(default_factory=list)
    lakefile_excerpt: str | None = None
    lean_toolchain: str | None = None
    readme_evidence: str | None = None
    evidence_summary: str
    known_risks: list[str] = Field(default_factory=list)
    truncated: bool = False
    summary: str | None = None


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
        limit = min(limit, 100)
        command = [
            self.config.gh_bin,
            "search",
            "repos",
            query,
            "--limit",
            str(limit),
            "--json",
            "fullName,url,description,stargazersCount,pushedAt,defaultBranch,language",
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
            "nameWithOwner,url,description,stargazerCount,pushedAt,defaultBranchRef,repositoryTopics,licenseInfo,primaryLanguage,languages",
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

    def get_repository(self, git_url: str) -> GitHubRepoCandidate:
        return self.inspect_repository(git_url)

    def list_repository_tree(
        self,
        git_url: str,
        revision: str | None = None,
        recursive: bool = True,
        path_prefix: str | None = None,
        limit: int = 5000,
    ) -> GitHubRepositoryTreeView:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        limit = min(limit, 5000)
        html_url = self.normalize_github_url(git_url)
        owner_repo = self._owner_repo_from_url(html_url)
        effective_revision = revision or self.inspect_repository(html_url).default_branch or "HEAD"
        resolved_revision, revision_issue_code = self._resolve_remote_revision(
            owner_repo,
            effective_revision,
        )
        endpoint = f"repos/{owner_repo}/git/trees/{quote(effective_revision, safe='')}"
        command = [self.config.gh_bin, "api", endpoint]
        if recursive:
            command.extend(["--method", "GET", "-f", "recursive=1"])
        result = self.runner.run(
            command,
            cwd=Path.cwd(),
            timeout_seconds=self.config.timeout_seconds,
            stdout_excerpt_chars=max(self.config.stdout_excerpt_chars, 200000),
            stderr_excerpt_chars=self.config.stderr_excerpt_chars,
        )
        if not result.ok:
            return GitHubRepositoryTreeView(
                git_url=html_url,
                revision=revision,
                recursive=recursive,
                path_prefix=path_prefix,
                summary=result.summary,
                issue_code=result.issue_code or "github_tree_failed",
            )
        try:
            payload = json.loads(result.stdout_excerpt or "{}")
        except json.JSONDecodeError:
            return GitHubRepositoryTreeView(
                git_url=html_url,
                revision=revision,
                recursive=recursive,
                path_prefix=path_prefix,
                summary="GitHub tree returned invalid JSON.",
                issue_code="invalid_json",
            )
        try:
            prefix = self._normalize_optional_repo_path(path_prefix)
        except ValueError as exc:
            return GitHubRepositoryTreeView(
                git_url=html_url,
                revision=revision,
                recursive=recursive,
                path_prefix=path_prefix,
                summary=str(exc),
                issue_code="invalid_github_path",
            )
        raw_entries = payload.get("tree") if isinstance(payload, dict) else []
        entries: list[GitHubTreeEntry] = []
        for item in raw_entries if isinstance(raw_entries, list) else []:
            if not isinstance(item, dict):
                continue
            raw_path = str(item.get("path") or "")
            if prefix and raw_path != prefix and not raw_path.startswith(prefix + "/"):
                continue
            entries.append(
                GitHubTreeEntry(
                    path=raw_path,
                    type=str(item.get("type") or ""),
                    size=int(item["size"]) if isinstance(item.get("size"), int) else None,
                    sha=str(item.get("sha")) if item.get("sha") else None,
                )
            )
            if len(entries) >= limit:
                break
        truncated = bool(payload.get("truncated")) if isinstance(payload, dict) else False
        truncated = truncated or (isinstance(raw_entries, list) and len(entries) >= limit and len(raw_entries) > limit)
        return GitHubRepositoryTreeView(
            git_url=html_url,
            revision=revision,
            resolved_revision=resolved_revision,
            recursive=recursive,
            path_prefix=prefix,
            entries=entries,
            truncated=truncated,
            revision_issue_code=revision_issue_code,
            summary=(
                f"Read {len(entries)} remote tree entries from {owner_repo}."
                + (
                    " Immutable commit resolution was unavailable."
                    if revision_issue_code
                    else ""
                )
            ),
        )

    def _resolve_remote_revision(
        self,
        owner_repo: str,
        revision: str,
    ) -> tuple[str | None, str | None]:
        endpoint = f"repos/{owner_repo}/commits/{quote(revision, safe='')}"
        result = self.runner.run(
            [self.config.gh_bin, "api", endpoint, "--jq", ".sha"],
            cwd=Path.cwd(),
            timeout_seconds=self.config.timeout_seconds,
            stdout_excerpt_chars=200,
            stderr_excerpt_chars=self.config.stderr_excerpt_chars,
        )
        if not result.ok:
            return None, result.issue_code or "github_revision_resolve_failed"
        raw = (result.stdout_excerpt or "").strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        sha = (
            str(payload.get("sha") or "")
            if isinstance(payload, dict)
            else str(payload)
        )
        if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", sha) is None:
            return None, "github_revision_not_immutable"
        return sha.lower(), None

    def read_repository_file(
        self,
        git_url: str,
        path: str,
        revision: str | None = None,
        max_chars: int = 20000,
    ) -> GitHubRepositoryFileView:
        max_chars = min(max(max_chars, 1), 50000)
        html_url = self.normalize_github_url(git_url)
        owner_repo = self._owner_repo_from_url(html_url)
        try:
            safe_path = self._normalize_repo_path(path)
        except ValueError as exc:
            return GitHubRepositoryFileView(
                git_url=html_url,
                path=path,
                revision=revision,
                summary=str(exc),
                issue_code="invalid_github_path",
            )
        endpoint = f"repos/{owner_repo}/contents/{quote(safe_path, safe='/')}"
        command = [self.config.gh_bin, "api", endpoint]
        if revision:
            command.extend(["--method", "GET", "-f", f"ref={revision}"])
        result = self.runner.run(
            command,
            cwd=Path.cwd(),
            timeout_seconds=self.config.timeout_seconds,
            stdout_excerpt_chars=max(self.config.stdout_excerpt_chars, max_chars * 2),
            stderr_excerpt_chars=self.config.stderr_excerpt_chars,
        )
        if not result.ok:
            return GitHubRepositoryFileView(
                git_url=html_url,
                path=safe_path,
                revision=revision,
                summary=result.summary,
                issue_code=result.issue_code or "github_file_read_failed",
            )
        try:
            payload = json.loads(result.stdout_excerpt or "{}")
        except json.JSONDecodeError:
            return GitHubRepositoryFileView(
                git_url=html_url,
                path=safe_path,
                revision=revision,
                summary="GitHub file read returned invalid JSON.",
                issue_code="invalid_json",
            )
        if not isinstance(payload, dict) or payload.get("type") not in {None, "file"}:
            return GitHubRepositoryFileView(
                git_url=html_url,
                path=safe_path,
                revision=revision,
                summary="GitHub path is not a readable file.",
                issue_code="github_path_not_file",
            )
        raw_content = str(payload.get("content") or "")
        encoding = str(payload.get("encoding") or "")
        if encoding == "base64":
            try:
                data = base64.b64decode(raw_content, validate=False)
                text = data.decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return GitHubRepositoryFileView(
                    git_url=html_url,
                    path=safe_path,
                    revision=revision,
                    encoding=encoding,
                    size=int(payload["size"]) if isinstance(payload.get("size"), int) else None,
                    summary="GitHub file is binary or not UTF-8 text.",
                    issue_code="github_file_not_text",
                )
        else:
            text = raw_content
        truncated = len(text) > max_chars
        return GitHubRepositoryFileView(
            git_url=html_url,
            path=safe_path,
            revision=revision,
            encoding=encoding or None,
            size=int(payload["size"]) if isinstance(payload.get("size"), int) else None,
            content_excerpt=text[:max_chars],
            truncated=truncated,
            summary=f"Read remote file {safe_path} from {owner_repo}.",
        )

    def search_code(self, query: str, repo: str | None = None, limit: int = 10) -> GitHubCodeSearchResult:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        limit = min(limit, 50)
        command = [
            self.config.gh_bin,
            "search",
            "code",
            query,
            "--limit",
            str(limit),
            "--json",
            "path,repository,url,textMatches",
        ]
        normalized_repo: str | None = None
        if repo:
            normalized_repo = self._owner_repo_from_url(self.normalize_github_url(repo))
            command.extend(["--repo", normalized_repo])
        result = self.runner.run(
            command,
            cwd=Path.cwd(),
            timeout_seconds=self.config.timeout_seconds,
            stdout_excerpt_chars=self.config.stdout_excerpt_chars,
            stderr_excerpt_chars=self.config.stderr_excerpt_chars,
        )
        if not result.ok:
            return GitHubCodeSearchResult(
                ok=False,
                query=query,
                repo=normalized_repo,
                summary=result.summary,
                issue_code=result.issue_code or "github_code_search_failed",
            )
        try:
            items = json.loads(result.stdout_excerpt or "[]")
        except json.JSONDecodeError:
            return GitHubCodeSearchResult(ok=False, query=query, repo=normalized_repo, summary="GitHub code search returned invalid JSON.", issue_code="invalid_json")
        matches = [self._code_match_from_gh_json(item) for item in items[:limit] if isinstance(item, dict)]
        return GitHubCodeSearchResult(ok=True, query=query, repo=normalized_repo, matches=matches, summary=f"Found {len(matches)} code matches.")

    def probe_github_lean_repo_candidate(
        self,
        git_url: str,
        revision: str | None = None,
        subdir: str | None = None,
        max_tree_entries: int = 5000,
        max_file_chars: int = 20000,
    ) -> GitHubLeanRepoProbeView:
        if max_tree_entries < 1 or max_file_chars < 1:
            raise ValueError("probe limits must be >= 1")
        max_tree_entries = min(max_tree_entries, 5000)
        max_file_chars = min(max_file_chars, 50000)
        html_url = self.normalize_github_url(git_url)
        metadata = self.inspect_repository(html_url)
        try:
            requested_subdir = self._normalize_optional_repo_path(subdir)
        except ValueError as exc:
            return GitHubLeanRepoProbeView(
                git_url=git_url,
                normalized_git_url=html_url,
                requested_revision=revision,
                requested_subdir=subdir,
                is_lean_project=False,
                has_lakefile=False,
                has_lean_toolchain=False,
                evidence_summary=str(exc),
                known_risks=["invalid_github_path"],
                summary=str(exc),
            )
        effective_revision = revision or metadata.default_branch or "HEAD"
        tree = self.list_repository_tree(
            html_url,
            revision=effective_revision,
            recursive=True,
            limit=max_tree_entries,
        )
        if tree.issue_code:
            return GitHubLeanRepoProbeView(
                git_url=git_url,
                normalized_git_url=html_url,
                requested_revision=revision,
                requested_subdir=requested_subdir,
                is_lean_project=False,
                has_lakefile=False,
                has_lean_toolchain=False,
                evidence_summary=tree.summary or "Remote tree probe failed.",
                known_risks=[tree.issue_code],
                truncated=tree.truncated,
                summary=tree.summary,
            )
        paths = [entry.path for entry in tree.entries]
        lakefile_paths = sorted(path for path in paths if Path(path).name in {"lakefile.lean", "lakefile.toml"})
        leanpkg_paths = sorted(path for path in paths if Path(path).name == "leanpkg.toml")
        toolchain_paths = sorted(path for path in paths if Path(path).name == "lean-toolchain")
        project_markers = [*lakefile_paths, *leanpkg_paths] or toolchain_paths
        candidate_subdirs = sorted(
            {
                "" if str(Path(path).parent) == "." else str(Path(path).parent)
                for path in project_markers
            }
        )
        candidate_subdirs = ["" if value in {".", ""} else value for value in candidate_subdirs]
        selected_subdir = requested_subdir
        if selected_subdir is None and candidate_subdirs:
            selected_subdir = "" if "" in candidate_subdirs else candidate_subdirs[0]
        scoped_lakefiles = [path for path in lakefile_paths if self._path_in_subdir(path, selected_subdir)]
        scoped_leanpkg = [path for path in leanpkg_paths if self._path_in_subdir(path, selected_subdir)]
        scoped_toolchains = [
            path
            for path in toolchain_paths
            if self._marker_applies_to_subdir(path, selected_subdir)
        ]
        prefix = (selected_subdir.rstrip("/") + "/") if selected_subdir else ""
        scoped_lean_files = sorted(
            path
            for path in paths
            if path.endswith(".lean") and (not prefix or path.startswith(prefix))
        )
        lakefile_path = scoped_lakefiles[0] if scoped_lakefiles else None
        lakefile_view = (
            self.read_repository_file(html_url, lakefile_path, revision=effective_revision, max_chars=max_file_chars)
            if lakefile_path
            else None
        )
        lakefile_text = lakefile_view.content_excerpt if lakefile_view and not lakefile_view.issue_code else None
        package_name = self._package_name_from_lakefile_text(lakefile_text or "")
        toolchain_path = scoped_toolchains[0] if scoped_toolchains else None
        toolchain_view = (
            self.read_repository_file(html_url, toolchain_path, revision=effective_revision, max_chars=1000)
            if toolchain_path
            else None
        )
        readme = self._read_readme_evidence(
            html_url,
            paths,
            revision=effective_revision,
            subdir=selected_subdir,
            max_chars=min(max_file_chars, 4000),
        )
        likely_modules = self._likely_import_modules(paths=paths, selected_subdir=selected_subdir, package_name=package_name)
        lean_topics = {
            "lean",
            "lean4",
            "mathlib",
            "formalization",
            "formal-methods",
            "theorem-proving",
        }
        languages = list(dict.fromkeys(metadata.languages))
        if metadata.primary_language and metadata.primary_language not in languages:
            languages.insert(0, metadata.primary_language)
        signals: list[str] = []
        if any(language.casefold() == "lean" for language in languages):
            signals.append("metadata:language=Lean")
        signals.extend(
            f"metadata:topic={topic}"
            for topic in metadata.topics
            if topic.casefold() in lean_topics
        )
        signals.extend(f"path:{path}" for path in scoped_lakefiles)
        signals.extend(f"path:{path}" for path in scoped_leanpkg)
        signals.extend(f"path:{path}" for path in scoped_toolchains)
        if scoped_lean_files:
            signals.append(f"tree:lean_files={len(scoped_lean_files)}")
        if readme and re.search(r"\b(lean\s*4|lean4|lake|mathlib|formalization|theorem prover)\b", readme, re.IGNORECASE):
            signals.append("readme:lean_or_lake_package")
        signals = list(dict.fromkeys(signals))
        risks: list[str] = []
        if tree.truncated:
            risks.append("Remote repository tree was truncated; candidate evidence may be incomplete.")
        if tree.revision_issue_code:
            risks.append(
                f"Immutable commit resolution failed: {tree.revision_issue_code}."
            )
        if not scoped_toolchains:
            risks.append("No lean-toolchain was found under the selected Lean project scope.")
        if not scoped_lakefiles:
            risks.append("No Lake manifest was found under the selected Lean project scope.")
        if not likely_modules:
            risks.append("No likely import module was inferred from remote tree evidence.")
        is_lean_project = bool(signals)
        owner_repo = self._owner_repo_from_url(html_url).casefold()
        is_mathlib_repository = owner_repo in {
            "leanprover-community/mathlib",
            "leanprover-community/mathlib4",
        }
        if is_mathlib_repository:
            risks.append(
                "Official Mathlib is the platform dependency and is not an adapter/provider candidate."
            )
        if is_lean_project:
            evidence_summary = (
                f"Remote probe found {len(signals)} independent Lean candidate signal(s) "
                f"in {selected_subdir or 'repo root'}."
            )
            if package_name:
                evidence_summary += f" Package appears to be {package_name}."
        else:
            evidence_summary = "Remote probe did not find a Lean language, topic, project marker, source file, or README signal."
        return GitHubLeanRepoProbeView(
            git_url=git_url,
            normalized_git_url=html_url,
            requested_revision=revision,
            resolved_revision=tree.resolved_revision,
            requested_subdir=requested_subdir,
            is_lean_project=is_lean_project,
            has_lakefile=bool(scoped_lakefiles),
            has_lean_toolchain=bool(scoped_toolchains),
            has_lean_manifest=bool(scoped_lakefiles or scoped_leanpkg),
            has_lean_files=bool(scoped_lean_files),
            primary_language=metadata.primary_language,
            languages=languages,
            topics=metadata.topics,
            lean_signals=signals,
            lean_file_paths=scoped_lean_files[:100],
            is_mathlib_repository=is_mathlib_repository,
            adapter_candidate=is_lean_project and not is_mathlib_repository,
            lakefile_paths=scoped_lakefiles,
            lean_toolchain_paths=scoped_toolchains,
            candidate_subdirs=candidate_subdirs,
            selected_subdir=selected_subdir,
            package_name=package_name,
            likely_import_modules=likely_modules,
            lakefile_excerpt=lakefile_text,
            lean_toolchain=(toolchain_view.content_excerpt.strip() if toolchain_view and toolchain_view.content_excerpt else None),
            readme_evidence=readme,
            evidence_summary=evidence_summary,
            known_risks=risks,
            truncated=tree.truncated,
            summary=evidence_summary,
        )

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
        language_names: list[str] = []
        primary_language_raw = item.get("primaryLanguage") or item.get("language")
        if isinstance(primary_language_raw, dict):
            raw_name = primary_language_raw.get("name")
            primary_language = str(raw_name) if raw_name else None
        elif primary_language_raw:
            primary_language = str(primary_language_raw)
        else:
            primary_language = None
        languages_raw = item.get("languages") or []
        if isinstance(languages_raw, list):
            for language in languages_raw:
                if isinstance(language, dict):
                    raw_name = language.get("name")
                    if raw_name:
                        language_names.append(str(raw_name))
                elif language:
                    language_names.append(str(language))
        if primary_language:
            language_names.insert(0, primary_language)
        language_names = list(dict.fromkeys(language_names))
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
            primary_language=primary_language,
            languages=language_names,
            topics=topics,
            stars=int(item.get("stargazersCount") or item.get("stargazerCount") or 0),
            pushed_at=str(item.get("pushedAt")) if item.get("pushedAt") is not None else None,
            license_spdx_id=license_spdx_id,
            license_name=license_name,
        )

    def _code_match_from_gh_json(self, item: dict[str, object]) -> GitHubCodeSearchMatch:
        repo_raw = item.get("repository")
        repo_name: str | None = None
        if isinstance(repo_raw, dict):
            repo_name = str(repo_raw.get("fullName") or repo_raw.get("nameWithOwner") or repo_raw.get("name") or "") or None
        text_matches: list[str] = []
        raw_matches = item.get("textMatches")
        if isinstance(raw_matches, list):
            for match in raw_matches:
                if not isinstance(match, dict):
                    continue
                fragment = match.get("fragment")
                if fragment:
                    text_matches.append(str(fragment)[:500])
        return GitHubCodeSearchMatch(
            repository=repo_name,
            path=str(item.get("path") or ""),
            url=str(item.get("url")) if item.get("url") else None,
            text_matches=text_matches,
        )

    def _normalize_repo_path(self, path: str) -> str:
        raw = path.strip().replace("\\", "/")
        if not raw or raw.startswith("/") or raw.startswith("~"):
            raise ValueError("GitHub repository path must be repo-relative")
        parts = [part for part in raw.split("/") if part not in {"", "."}]
        if any(part == ".." for part in parts):
            raise ValueError("GitHub repository path cannot contain '..'")
        normalized = "/".join(parts)
        if not normalized:
            raise ValueError("GitHub repository path must be non-empty")
        return normalized

    def _normalize_optional_repo_path(self, path: str | None) -> str | None:
        if path is None:
            return None
        return self._normalize_repo_path(path)

    def _path_in_subdir(self, path: str, subdir: str | None) -> bool:
        if not subdir:
            return "/" not in path or str(Path(path).parent) == "."
        return path == subdir or path.startswith(subdir.rstrip("/") + "/")

    @staticmethod
    def _marker_applies_to_subdir(path: str, subdir: str | None) -> bool:
        parent = Path(path).parent.as_posix()
        parent = "" if parent == "." else parent
        if not subdir:
            return parent == ""
        return parent == "" or subdir == parent or subdir.startswith(parent.rstrip("/") + "/")

    def _package_name_from_lakefile_text(self, text: str) -> str | None:
        if not text:
            return None
        package_match = re.search(
            r"\bpackage\s+(?:«([^»]+)»|\"([^\"]+)\"|'([^']+)'|([A-Za-z0-9_.-]+))",
            text,
        )
        if package_match:
            name = next((group for group in package_match.groups() if group), None)
            if name:
                return name
        toml_match = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"', text)
        if toml_match:
            return toml_match.group(1)
        return None

    def _likely_import_modules(self, *, paths: list[str], selected_subdir: str | None, package_name: str | None) -> list[str]:
        modules: list[str] = []
        if package_name and self._is_reasonable_module_name(package_name):
            modules.append(package_name)
        prefix = (selected_subdir.rstrip("/") + "/") if selected_subdir else ""
        scoped = [path.removeprefix(prefix) for path in paths if not prefix or path.startswith(prefix)]
        for path in scoped:
            if path == "Main.lean":
                modules.append("Main")
            if "/" not in path and path.endswith(".lean") and path != "lakefile.lean":
                name = path.removesuffix(".lean")
                if self._is_reasonable_module_name(name):
                    modules.append(name)
            if "/" in path and path.endswith(".lean"):
                top = path.split("/", 1)[0]
                if self._is_reasonable_module_name(top):
                    modules.append(top)
        return list(dict.fromkeys(modules))[:10]

    def _read_readme_evidence(
        self,
        git_url: str,
        paths: list[str],
        *,
        revision: str | None,
        subdir: str | None,
        max_chars: int,
    ) -> str | None:
        prefix = (subdir.rstrip("/") + "/") if subdir else ""
        readme_paths = [
            path
            for path in paths
            if path.removeprefix(prefix).lower() in {"readme.md", "readme.txt", "readme"}
            and (not prefix or path.startswith(prefix))
        ]
        if not readme_paths:
            return None
        view = self.read_repository_file(git_url, readme_paths[0], revision=revision, max_chars=max_chars)
        if view.issue_code or not view.content_excerpt:
            return None
        lines = [line.strip() for line in view.content_excerpt.splitlines() if line.strip()]
        relevant = [line for line in lines if re.search(r"\b(lean|lake|theorem|proof|formal|mathlib)\b", line, re.IGNORECASE)]
        return "\n".join((relevant or lines)[:8])[:max_chars]

    def _owner_repo_from_url(self, html_url: str) -> str:
        parsed = urlparse(html_url)
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2:
            raise ValueError(f"Cannot extract owner/repo from {html_url}")
        return f"{parts[0]}/{parts[1]}"
