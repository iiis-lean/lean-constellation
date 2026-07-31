"""Explicit GitHub repository topics synchronization for publication metadata."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.external_clients.process import (
    CommandRunner,
    ExternalCommandResult,
    SubprocessCommandRunner,
)
from lean_constellation.services.foundation import ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


_TOPIC_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")


class RepoGitHubTopicsPreview(StrictModel):
    schema_version: Literal[1] = 1
    repo_key: str
    repository: str
    remote_name: str
    current_topics: list[str] = Field(default_factory=list)
    desired_topics: list[str] = Field(default_factory=list)
    topics_to_add: list[str] = Field(default_factory=list)
    topics_to_remove: list[str] = Field(default_factory=list)
    changed: bool
    recovery_token: str
    summary: str


class RepoGitHubTopicsReceipt(StrictModel):
    schema_version: Literal[1] = 1
    repo_key: str
    repository: str
    previous_topics: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    changed: bool
    verified: bool
    summary: str


class RepoGitHubTopicsComponent:
    """Preview and explicitly synchronize presentation topics to GitHub."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        runner: CommandRunner | None = None,
        git_bin: str | None = None,
        gh_bin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.runtime = runtime
        config = runtime.external.config.github_repo
        self.runner = runner or SubprocessCommandRunner()
        self.git_bin = git_bin or config.git_bin
        self.gh_bin = gh_bin or config.gh_bin
        self.timeout_seconds = timeout_seconds or config.timeout_seconds
        self.stdout_excerpt_chars = config.stdout_excerpt_chars
        self.stderr_excerpt_chars = config.stderr_excerpt_chars

    def preview(
        self,
        repo_root: Path,
        *,
        remote_name: str = "origin",
    ) -> ServiceResult[RepoGitHubTopicsPreview]:
        repo_root = Path(repo_root).resolve()
        presentation = self.runtime.repo_workspace.publication.get_presentation(
            repo_root
        )
        if not presentation.ok or presentation.value is None:
            return self.runtime.foundation.fail(presentation.issues)
        desired_topics = presentation.value.topics
        if not desired_topics:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "github_topics_not_configured",
                    "Publication presentation does not declare any GitHub topics.",
                    object_ref=repo_root.name,
                )
            )
        invalid_topics = [
            topic for topic in desired_topics if not _TOPIC_PATTERN.fullmatch(topic)
        ]
        if invalid_topics:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "github_topics_invalid",
                    "Publication topics must use lowercase letters, numbers, and hyphens.",
                    object_ref=repo_root.name,
                    details={"invalid_topics": invalid_topics},
                )
            )
        resolved = self._resolve_repository(repo_root, remote_name=remote_name)
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        repository = resolved.value
        current = self._read_topics(repo_root, repository=repository)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        current_topics = sorted(set(current.value))
        current_set = set(current_topics)
        desired_set = set(desired_topics)
        topics_to_add = [
            topic for topic in desired_topics if topic not in current_set
        ]
        topics_to_remove = [
            topic for topic in current_topics if topic not in desired_set
        ]
        payload = {
            "repo_key": repo_root.name,
            "repository": repository.lower(),
            "remote_name": remote_name,
            "current_topics": current_topics,
            "desired_topics": desired_topics,
        }
        recovery_token = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        changed = bool(topics_to_add or topics_to_remove)
        return self.runtime.foundation.ok(
            RepoGitHubTopicsPreview(
                repo_key=repo_root.name,
                repository=repository,
                remote_name=remote_name,
                current_topics=current_topics,
                desired_topics=desired_topics,
                topics_to_add=topics_to_add,
                topics_to_remove=topics_to_remove,
                changed=changed,
                recovery_token=recovery_token,
                summary=(
                    f"GitHub topics differ for {repository}."
                    if changed
                    else f"GitHub topics already match for {repository}."
                ),
            )
        )

    def apply(
        self,
        repo_root: Path,
        *,
        expected_recovery_token: str,
        remote_name: str = "origin",
    ) -> ServiceResult[RepoGitHubTopicsReceipt]:
        repo_root = Path(repo_root).resolve()
        preview = self.preview(repo_root, remote_name=remote_name)
        if (
            not preview.ok
            or preview.value is None
            or preview.value.recovery_token != expected_recovery_token
        ):
            if not preview.ok:
                return self.runtime.foundation.fail(preview.issues)
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "github_topics_token_mismatch",
                    "GitHub topics or publication presentation changed after preview.",
                    object_ref=repo_root.name,
                )
            )
        value = preview.value
        if value.changed:
            command = [
                self.gh_bin,
                "api",
                "--method",
                "PUT",
                f"repos/{value.repository}/topics",
            ]
            for topic in value.desired_topics:
                command.extend(["-f", f"names[]={topic}"])
            updated = self._run(command, cwd=repo_root)
            if not updated.ok:
                return self._command_failure(
                    "github_topics_update_failed",
                    "Failed to update GitHub repository topics.",
                    value.repository,
                    updated,
                )
        verified = self._read_topics(
            repo_root,
            repository=value.repository,
        )
        if not verified.ok or verified.value is None:
            return self.runtime.foundation.fail(verified.issues)
        verified_topics = sorted(set(verified.value))
        if set(verified_topics) != set(value.desired_topics):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "github_topics_verification_failed",
                    "GitHub repository topics do not match publication presentation.",
                    object_ref=value.repository,
                    expected=json.dumps(sorted(value.desired_topics)),
                    current=json.dumps(verified_topics),
                )
            )
        return self.runtime.foundation.ok(
            RepoGitHubTopicsReceipt(
                repo_key=repo_root.name,
                repository=value.repository,
                previous_topics=value.current_topics,
                topics=value.desired_topics,
                changed=value.changed,
                verified=True,
                summary=f"Verified GitHub topics for {value.repository}.",
            )
        )

    def _resolve_repository(
        self,
        repo_root: Path,
        *,
        remote_name: str,
    ) -> ServiceResult[str]:
        result = self._run(
            [self.git_bin, "remote", "get-url", remote_name],
            cwd=repo_root,
        )
        if not result.ok or not (result.stdout_excerpt or "").strip():
            return self._command_failure(
                "github_topics_remote_missing",
                "Failed to resolve the Git remote used for GitHub topics.",
                remote_name,
                result,
            )
        remote_url = (result.stdout_excerpt or "").strip().splitlines()[0]
        repository = self._github_repository_from_url(remote_url)
        if repository is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "github_topics_remote_not_github",
                    "The selected Git remote is not a supported GitHub repository URL.",
                    object_ref=remote_name,
                    current=remote_url,
                )
            )
        return self.runtime.foundation.ok(repository)

    def _read_topics(
        self,
        repo_root: Path,
        *,
        repository: str,
    ) -> ServiceResult[list[str]]:
        result = self._run(
            [
                self.gh_bin,
                "repo",
                "view",
                repository,
                "--json",
                "nameWithOwner,repositoryTopics",
            ],
            cwd=repo_root,
        )
        if not result.ok:
            return self._command_failure(
                "github_topics_read_failed",
                "Failed to read GitHub repository topics.",
                repository,
                result,
            )
        try:
            payload = json.loads(result.stdout_excerpt or "{}")
        except json.JSONDecodeError:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "github_topics_invalid_response",
                    "GitHub repository topics response is not valid JSON.",
                    object_ref=repository,
                )
            )
        actual_repository = str(payload.get("nameWithOwner") or "")
        if actual_repository.lower() != repository.lower():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "github_topics_repository_mismatch",
                    "GitHub returned metadata for a different repository.",
                    object_ref=repository,
                    current=actual_repository or None,
                )
            )
        raw_topics = payload.get("repositoryTopics") or []
        if not isinstance(raw_topics, list):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "github_topics_invalid_response",
                    "GitHub repository topics must be a list.",
                    object_ref=repository,
                )
            )
        topics: list[str] = []
        for item in raw_topics:
            if isinstance(item, dict):
                name = item.get("name")
                if name is None and isinstance(item.get("topic"), dict):
                    name = item["topic"].get("name")
            else:
                name = item
            if name:
                topic = str(name).strip().lower()
                if topic and topic not in topics:
                    topics.append(topic)
        return self.runtime.foundation.ok(topics)

    def _run(
        self,
        command: list[str],
        *,
        cwd: Path,
    ) -> ExternalCommandResult:
        return self.runner.run(
            command,
            cwd=cwd,
            timeout_seconds=self.timeout_seconds,
            stdout_excerpt_chars=self.stdout_excerpt_chars,
            stderr_excerpt_chars=self.stderr_excerpt_chars,
        )

    def _command_failure(
        self,
        kind: str,
        message: str,
        object_ref: str,
        result: ExternalCommandResult,
    ) -> ServiceResult:
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                kind,
                message,
                object_ref=object_ref,
                details={
                    "summary": result.summary,
                    "issue_code": result.issue_code,
                    "exit_code": result.exit_code,
                    "stderr_excerpt": result.stderr_excerpt,
                },
            )
        )

    @staticmethod
    def _github_repository_from_url(value: str) -> str | None:
        value = value.strip()
        if value.startswith("git@github.com:"):
            path = value.removeprefix("git@github.com:")
        else:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https", "ssh"}:
                return None
            if (parsed.hostname or "").lower() != "github.com":
                return None
            path = parsed.path
        parts = path.strip("/").removesuffix(".git").split("/")
        if len(parts) != 2 or not all(parts):
            return None
        if not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
            return None
        return f"{parts[0]}/{parts[1]}"


__all__ = [
    "RepoGitHubTopicsComponent",
    "RepoGitHubTopicsPreview",
    "RepoGitHubTopicsReceipt",
]
