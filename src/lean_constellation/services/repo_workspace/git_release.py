"""Git-backed immutable repository Release storage."""

from __future__ import annotations

import os
import hashlib
import json
import tempfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.publication import GitCommitIdentity
from lean_constellation.domain.repo_release import RepoRelease
from lean_constellation.services.external_clients.process import (
    CommandRunner,
    ExternalCommandResult,
    SubprocessCommandRunner,
)
from lean_constellation.services.foundation import IssueSeverity, ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


_RELEASE_REF_PREFIX = "refs/lean-constellation/releases"
_EXCLUDED_TOP_LEVEL = {".agent_runtime", ".git", ".lake", ".runtime"}
_EXCLUDED_CONSTELLATION_DIRS = {"locks", "snapshots", "staging"}


class GitRepoStateView(StrictModel):
    repo_root: str
    initialized: bool
    independent: bool
    object_format: str | None = None
    current_branch_ref: str | None = None
    head_commit: str | None = None
    summary: str


class GitReleaseCommitView(StrictModel):
    repo_root: str
    release_id: str
    release_ref: str
    commit: str
    tree: str
    parent_commit: str | None = None
    branch_ref: str | None = None
    branch_updated: bool = False
    published_files: list[str] = Field(default_factory=list)
    summary: str


class GitReleaseValidationView(StrictModel):
    repo_root: str
    release_id: str
    release_ref: str
    commit: str
    tree: str
    manifest_path: str
    summary: str


class GitReleaseRestoreView(StrictModel):
    repo_root: str
    release_id: str
    commit: str
    dry_run: bool
    previous_head: str | None = None
    detached_head: bool
    summary: str


class GitReleaseRestorePreview(StrictModel):
    schema_version: int = 1
    repo_root: str
    release_id: str
    commit: str
    expected_head: str | None = None
    expected_worktree_digest: str
    recovery_token: str
    summary: str


class GitReleaseComponent:
    """Create and validate immutable Release commits without staging runtime data."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        runner: CommandRunner | None = None,
        git_bin: str = "git",
        timeout_seconds: int = 120,
    ) -> None:
        self.runtime = runtime
        self.runner = runner or SubprocessCommandRunner()
        self.git_bin = git_bin
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def release_ref(release_id: str) -> str:
        if not release_id or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" for character in release_id):
            raise ValueError("release_id must be a safe non-empty key")
        return f"{_RELEASE_REF_PREFIX}/{release_id}"

    def inspect_repo(self, repo_root: Path) -> ServiceResult[GitRepoStateView]:
        repo_root = Path(repo_root).resolve()
        top = self._run(["rev-parse", "--show-toplevel"], cwd=repo_root)
        if not top.ok:
            return self.runtime.foundation.ok(
                GitRepoStateView(
                    repo_root=str(repo_root),
                    initialized=False,
                    independent=False,
                    summary="Repository has no independent Git worktree.",
                )
            )
        top_level = Path((top.stdout_excerpt or "").strip()).resolve()
        object_format_result = self._run(["rev-parse", "--show-object-format"], cwd=repo_root)
        object_format = (
            (object_format_result.stdout_excerpt or "").strip()
            if object_format_result.ok
            else "sha1"
        )
        branch = self._run(["symbolic-ref", "-q", "HEAD"], cwd=repo_root)
        head = self._resolve_optional_commit(repo_root, "HEAD")
        independent = top_level == repo_root
        return self.runtime.foundation.ok(
            GitRepoStateView(
                repo_root=str(repo_root),
                initialized=True,
                independent=independent,
                object_format=object_format,
                current_branch_ref=(branch.stdout_excerpt or "").strip() if branch.ok else None,
                head_commit=head,
                summary=(
                    "Repository has an independent Git worktree."
                    if independent
                    else f"Repository is nested inside Git worktree {top_level}."
                ),
            )
        )

    def ensure_independent_repo(
        self,
        repo_root: Path,
        *,
        initial_branch: str = "main",
    ) -> ServiceResult[GitRepoStateView]:
        repo_root = Path(repo_root).resolve()
        repo_root.mkdir(parents=True, exist_ok=True)
        inspected = self.inspect_repo(repo_root)
        if not inspected.ok or inspected.value is None:
            return self.runtime.foundation.fail(inspected.issues)
        if inspected.value.initialized and inspected.value.independent:
            return inspected
        initialized = self._run(["init", "."], cwd=repo_root)
        if not initialized.ok:
            return self._command_failure(
                "git_init_failed",
                "Failed to initialize an independent Git repository.",
                repo_root,
                initialized,
            )
        branch = self._run(
            ["symbolic-ref", "HEAD", f"refs/heads/{initial_branch}"],
            cwd=repo_root,
        )
        if not branch.ok:
            return self._command_failure(
                "git_initial_branch_failed",
                "Failed to set the initial Git branch.",
                repo_root,
                branch,
            )
        verified = self.inspect_repo(repo_root)
        if not verified.ok or verified.value is None or not verified.value.independent:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_repo_not_independent",
                    "Git initialization did not create an independent repository worktree.",
                    object_ref=str(repo_root),
                )
            )
        return verified

    def resolve_release_commit(
        self,
        repo_root: Path,
        *,
        release_id: str,
    ) -> ServiceResult[str]:
        try:
            release_ref = self.release_ref(release_id)
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "release_id_invalid",
                    str(exc),
                    object_ref=release_id,
                )
            )
        result = self._run(["rev-parse", "--verify", f"{release_ref}^{{commit}}"], cwd=Path(repo_root))
        if not result.ok:
            return self._command_failure(
                "git_release_ref_missing",
                "Git Release ref does not resolve to a commit.",
                Path(repo_root),
                result,
                object_ref=release_ref,
            )
        return self.runtime.foundation.ok((result.stdout_excerpt or "").strip())

    def list_release_refs(self, repo_root: Path) -> ServiceResult[dict[str, str]]:
        result = self._run(
            [
                "for-each-ref",
                "--format=%(refname) %(objectname)",
                _RELEASE_REF_PREFIX,
            ],
            cwd=Path(repo_root),
        )
        if not result.ok:
            return self._command_failure(
                "git_release_refs_unreadable",
                "Git Release refs could not be listed.",
                Path(repo_root),
                result,
            )
        refs: dict[str, str] = {}
        for line in (result.stdout_excerpt or "").splitlines():
            ref, separator, commit = line.partition(" ")
            if not separator or not ref.startswith(f"{_RELEASE_REF_PREFIX}/"):
                continue
            refs[ref.removeprefix(f"{_RELEASE_REF_PREFIX}/")] = commit.strip()
        return self.runtime.foundation.ok(refs)

    def list_worktree_changes(self, repo_root: Path) -> ServiceResult[list[str]]:
        result = self._run(
            ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=Path(repo_root),
        )
        if not result.ok:
            return self._command_failure(
                "git_worktree_status_failed",
                "Could not inspect the Git worktree.",
                Path(repo_root),
                result,
            )
        paths: list[str] = []
        for record in (result.stdout_excerpt or "").split("\0"):
            if not record:
                continue
            path = record[3:] if len(record) >= 4 else record
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            paths.append(path)
        return self.runtime.foundation.ok(sorted(set(paths)))

    def delete_release_ref(
        self,
        repo_root: Path,
        *,
        release_id: str,
    ) -> ServiceResult[bool]:
        release_ref = self.release_ref(release_id)
        commit = self._resolve_optional_commit(Path(repo_root), release_ref)
        if commit is None:
            return self.runtime.foundation.ok(False)
        deleted = self._run(
            ["update-ref", "-d", release_ref, commit],
            cwd=Path(repo_root),
        )
        if not deleted.ok:
            return self._command_failure(
                "git_release_ref_delete_failed",
                "Failed to delete the unpublished Git Release ref.",
                Path(repo_root),
                deleted,
                object_ref=release_ref,
            )
        return self.runtime.foundation.ok(True)

    def commit_release(
        self,
        repo_root: Path,
        *,
        release: RepoRelease,
        candidate_files: list[str],
        expected_head: str | None,
        commit_message: str | None = None,
        update_current_branch: bool = True,
        commit_identity: GitCommitIdentity | None = None,
    ) -> ServiceResult[GitReleaseCommitView]:
        repo_root = Path(repo_root).resolve()
        inspected = self.inspect_repo(repo_root)
        if (
            not inspected.ok
            or inspected.value is None
            or not inspected.value.initialized
            or not inspected.value.independent
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_repo_not_independent",
                    "Release commits require an independent Git repository.",
                    object_ref=str(repo_root),
                )
            )
        current_head = inspected.value.head_commit
        if current_head != expected_head:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_release_head_drift",
                    "Repository HEAD changed after Release preparation.",
                    object_ref=str(repo_root),
                    current=current_head or "<unborn>",
                    expected=expected_head or "<unborn>",
                )
            )
        release_ref = self.release_ref(release.release_id)
        if self._resolve_optional_commit(repo_root, release_ref) is not None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_release_ref_exists",
                    "Git Release ref already exists.",
                    object_ref=release_ref,
                )
            )
        staged = self._run(["diff", "--cached", "--quiet", "--"], cwd=repo_root)
        if staged.exit_code not in {0, 1}:
            return self._command_failure(
                "git_index_check_failed",
                "Could not verify that the Git index is clean.",
                repo_root,
                staged,
            )
        if staged.exit_code == 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_index_not_clean",
                    "Release requires an empty user staging area.",
                    object_ref=str(repo_root),
                )
            )
        normalized_files = self._normalize_candidate_files(repo_root, candidate_files)
        if isinstance(normalized_files, ServiceResult):
            return normalized_files
        if not normalized_files:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_release_candidate_empty",
                    "Release candidate file allowlist must be non-empty.",
                    object_ref=release.release_id,
                )
            )
        manifest_path = f".lean_constellation/releases/{release.release_id}.json"
        if manifest_path not in normalized_files:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_release_manifest_not_staged",
                    "Release candidate must include its immutable Release manifest.",
                    object_ref=manifest_path,
                )
            )
        branch_ref = inspected.value.current_branch_ref if update_current_branch else None
        if update_current_branch and branch_ref is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_release_detached_head",
                    "Updating the current branch requires a symbolic Git HEAD.",
                    object_ref=str(repo_root),
                )
            )

        descriptor, temporary_index = tempfile.mkstemp(prefix="lean-constellation-release-index-")
        os.close(descriptor)
        Path(temporary_index).unlink(missing_ok=True)
        git_env = {"GIT_INDEX_FILE": temporary_index}
        try:
            empty = self._run(["read-tree", "--empty"], cwd=repo_root, env=git_env)
            if not empty.ok:
                return self._command_failure(
                    "git_release_index_init_failed",
                    "Failed to initialize the temporary Release index.",
                    repo_root,
                    empty,
                )
            pathspec = "".join(f"{path}\0" for path in normalized_files)
            added = self._run(
                ["add", "--force", "--pathspec-from-file=-", "--pathspec-file-nul"],
                cwd=repo_root,
                env=git_env,
                input_text=pathspec,
            )
            if not added.ok:
                return self._command_failure(
                    "git_release_stage_failed",
                    "Failed to stage the exact Release publication allowlist.",
                    repo_root,
                    added,
                )
            tree_result = self._run(["write-tree"], cwd=repo_root, env=git_env)
            if not tree_result.ok:
                return self._command_failure(
                    "git_release_tree_failed",
                    "Failed to write the Release Git tree.",
                    repo_root,
                    tree_result,
                )
            tree = (tree_result.stdout_excerpt or "").strip()
            commit_command = ["commit-tree", tree]
            if expected_head is not None:
                commit_command.extend(["-p", expected_head])
            identity = commit_identity or GitCommitIdentity(
                name="Lean Constellation",
                email="lean-constellation@localhost",
            )
            commit_result = self._run(
                commit_command,
                cwd=repo_root,
                env={
                    "GIT_AUTHOR_NAME": identity.name,
                    "GIT_AUTHOR_EMAIL": identity.email,
                    "GIT_COMMITTER_NAME": identity.name,
                    "GIT_COMMITTER_EMAIL": identity.email,
                },
                input_text=(commit_message or f"release: {release.release_id}") + "\n",
            )
            if not commit_result.ok:
                return self._command_failure(
                    "git_release_commit_failed",
                    "Failed to create the immutable Release commit.",
                    repo_root,
                    commit_result,
                )
            commit = (commit_result.stdout_excerpt or "").strip()
            transaction_lines = [f"create {release_ref} {commit}"]
            if branch_ref is not None:
                if expected_head is None:
                    transaction_lines.append(f"create {branch_ref} {commit}")
                else:
                    transaction_lines.append(f"update {branch_ref} {commit} {expected_head}")
            transaction_lines.append("")
            updated = self._run(
                ["update-ref", "--stdin"],
                cwd=repo_root,
                input_text="\n".join(transaction_lines),
            )
            if not updated.ok:
                return self._command_failure(
                    "git_release_ref_update_failed",
                    "Failed to atomically publish the Release ref and branch.",
                    repo_root,
                    updated,
                )
            warnings = []
            if branch_ref is not None:
                refreshed = self._run(["read-tree", commit], cwd=repo_root)
                if not refreshed.ok:
                    warnings.append(
                        self.runtime.foundation.issue(
                            "git_index_refresh_failed",
                            "Release was published, but the user index could not be refreshed to the new HEAD.",
                            severity=IssueSeverity.WARNING,
                            object_ref=str(repo_root),
                            suggested_action="Run `git reset --mixed HEAD` before staging further changes.",
                        )
                    )
            view = GitReleaseCommitView(
                repo_root=str(repo_root),
                release_id=release.release_id,
                release_ref=release_ref,
                commit=commit,
                tree=tree,
                parent_commit=expected_head,
                branch_ref=branch_ref,
                branch_updated=branch_ref is not None,
                published_files=normalized_files,
                summary=f"Published Git-backed Release {release.release_id} at {commit}.",
            )
            return self.runtime.foundation.ok(view, warnings=warnings)
        finally:
            Path(temporary_index).unlink(missing_ok=True)

    def validate_release(
        self,
        repo_root: Path,
        *,
        release: RepoRelease,
    ) -> ServiceResult[GitReleaseValidationView]:
        repo_root = Path(repo_root).resolve()
        resolved = self.resolve_release_commit(repo_root, release_id=release.release_id)
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        commit = resolved.value
        tree_result = self._run(["show", "-s", "--format=%T", commit], cwd=repo_root)
        if not tree_result.ok:
            return self._command_failure(
                "git_release_tree_missing",
                "Release commit tree could not be resolved.",
                repo_root,
                tree_result,
                object_ref=release.release_id,
            )
        manifest_path = f".lean_constellation/releases/{release.release_id}.json"
        manifest = self._run(["show", f"{commit}:{manifest_path}"], cwd=repo_root)
        if not manifest.ok:
            return self._command_failure(
                "git_release_manifest_missing",
                "Release commit does not contain its immutable Release manifest.",
                repo_root,
                manifest,
                object_ref=manifest_path,
            )
        try:
            captured = RepoRelease.model_validate_json(manifest.stdout_excerpt or "")
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_release_manifest_invalid",
                    f"Release commit contains an invalid manifest: {exc}",
                    object_ref=manifest_path,
                )
            )
        if captured != release:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_release_manifest_mismatch",
                    "Release commit manifest differs from current immutable Release truth.",
                    object_ref=release.release_id,
                )
            )
        return self.runtime.foundation.ok(
            GitReleaseValidationView(
                repo_root=str(repo_root),
                release_id=release.release_id,
                release_ref=self.release_ref(release.release_id),
                commit=commit,
                tree=(tree_result.stdout_excerpt or "").strip(),
                manifest_path=manifest_path,
                summary=f"Validated Git-backed Release {release.release_id}.",
            )
        )

    def read_release_manifest(
        self,
        repo_root: Path,
        *,
        release_id: str,
    ) -> ServiceResult[RepoRelease]:
        repo_root = Path(repo_root).resolve()
        resolved = self.resolve_release_commit(repo_root, release_id=release_id)
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        manifest_path = f".lean_constellation/releases/{release_id}.json"
        manifest = self._run(
            ["show", f"{resolved.value}:{manifest_path}"],
            cwd=repo_root,
        )
        if not manifest.ok:
            return self._command_failure(
                "git_release_manifest_missing",
                "Release commit does not contain its immutable Release manifest.",
                repo_root,
                manifest,
                object_ref=manifest_path,
            )
        try:
            release = RepoRelease.model_validate_json(manifest.stdout_excerpt or "")
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_release_manifest_invalid",
                    f"Release commit contains an invalid manifest: {exc}",
                    object_ref=manifest_path,
                )
            )
        if release.release_id != release_id:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "release_identity_mismatch",
                    "Git Release manifest identity does not match its ref.",
                    object_ref=release_id,
                    current=release.release_id,
                )
            )
        return self.runtime.foundation.ok(release)

    def preview_restore_release(
        self,
        repo_root: Path,
        *,
        release_id: str,
    ) -> ServiceResult[GitReleaseRestorePreview]:
        repo_root = Path(repo_root).resolve()
        inspected = self.inspect_repo(repo_root)
        if (
            not inspected.ok
            or inspected.value is None
            or not inspected.value.independent
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_repo_not_independent",
                    "Git Release restore requires an independent repository.",
                    object_ref=str(repo_root),
                )
            )
        status = self._run(
            ["status", "--porcelain=v1", "--untracked-files=all"],
            cwd=repo_root,
        )
        if not status.ok:
            return self._command_failure(
                "git_restore_status_failed",
                "Could not verify the repository worktree before Release restore.",
                repo_root,
                status,
            )
        dirty = [line for line in (status.stdout_excerpt or "").splitlines() if line.strip()]
        if dirty:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_restore_worktree_not_clean",
                    "Release restore requires a clean non-ignored worktree and index.",
                    object_ref=str(repo_root),
                    details={"entries": "\n".join(dirty[:100])},
                )
            )
        resolved = self.resolve_release_commit(repo_root, release_id=release_id)
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        worktree_digest = hashlib.sha256(
            (status.stdout_excerpt or "").encode("utf-8")
        ).hexdigest()
        payload = {
            "repo_root": str(repo_root),
            "release_id": release_id,
            "commit": resolved.value,
            "head": inspected.value.head_commit,
            "worktree_digest": worktree_digest,
        }
        recovery_token = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return self.runtime.foundation.ok(
            GitReleaseRestorePreview(
                repo_root=str(repo_root),
                release_id=release_id,
                commit=resolved.value,
                expected_head=inspected.value.head_commit,
                expected_worktree_digest=worktree_digest,
                recovery_token=recovery_token,
                summary=f"Previewed Release restore to {resolved.value}.",
            )
        )

    def apply_restore_release(
        self,
        repo_root: Path,
        *,
        preview: GitReleaseRestorePreview,
        expected_recovery_token: str,
    ) -> ServiceResult[GitReleaseRestoreView]:
        repo_root = Path(repo_root).resolve()
        refreshed = self.preview_restore_release(
            repo_root,
            release_id=preview.release_id,
        )
        if (
            not refreshed.ok
            or refreshed.value is None
            or expected_recovery_token != preview.recovery_token
            or refreshed.value.recovery_token != preview.recovery_token
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_release_restore_token_mismatch",
                    "Git Release restore truth changed after preview.",
                    object_ref=preview.release_id,
                )
            )
        checkout = self._run(
            ["checkout", "--detach", preview.commit],
            cwd=repo_root,
        )
        if not checkout.ok:
            return self._command_failure(
                "git_release_restore_failed",
                "Failed to restore the Git Release commit in the current worktree.",
                repo_root,
                checkout,
                object_ref=preview.release_id,
            )
        return self.runtime.foundation.ok(
            GitReleaseRestoreView(
                repo_root=str(repo_root),
                release_id=preview.release_id,
                commit=preview.commit,
                dry_run=False,
                previous_head=preview.expected_head,
                detached_head=True,
                summary=(
                    f"Restored Release {preview.release_id} at detached HEAD "
                    f"{preview.commit}."
                ),
            )
        )


    def _normalize_candidate_files(
        self,
        repo_root: Path,
        candidate_files: list[str],
    ) -> list[str] | ServiceResult[GitReleaseCommitView]:
        normalized: set[str] = set()
        for raw_path in candidate_files:
            path = PurePosixPath(str(raw_path).replace("\\", "/"))
            if path.is_absolute() or not path.parts or ".." in path.parts:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "git_release_path_invalid",
                        "Release candidate paths must be safe repository-relative paths.",
                        object_ref=str(raw_path),
                    )
                )
            if path.parts[0] in _EXCLUDED_TOP_LEVEL:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "git_release_path_excluded",
                        "Release candidate contains an excluded runtime path.",
                        object_ref=path.as_posix(),
                    )
                )
            if (
                len(path.parts) >= 2
                and path.parts[0] == ".lean_constellation"
                and path.parts[1] in _EXCLUDED_CONSTELLATION_DIRS
            ):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "git_release_path_excluded",
                        "Release candidate contains an excluded local recovery path.",
                        object_ref=path.as_posix(),
                    )
                )
            absolute = repo_root.joinpath(*path.parts)
            if not absolute.is_file() or absolute.is_symlink():
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "git_release_file_missing",
                        "Release candidate file must exist and may not be a symlink.",
                        object_ref=path.as_posix(),
                    )
                )
            normalized.add(path.as_posix())
        return sorted(normalized)

    def _resolve_optional_commit(self, repo_root: Path, revision: str) -> str | None:
        result = self._run(["rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=repo_root)
        return (result.stdout_excerpt or "").strip() if result.ok else None

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        input_text: str | None = None,
    ) -> ExternalCommandResult:
        return self.runner.run(
            [self.git_bin, *args],
            cwd=Path(cwd),
            timeout_seconds=self.timeout_seconds,
            stdout_excerpt_chars=10_000_000,
            stderr_excerpt_chars=20_000,
            env=env,
            input_text=input_text,
        )

    def _command_failure(
        self,
        kind: str,
        message: str,
        repo_root: Path,
        result: ExternalCommandResult,
        *,
        object_ref: str | None = None,
        details: dict[str, str] | None = None,
    ) -> ServiceResult:
        command_details = {
            "command": " ".join(result.command),
            "exit_code": str(result.exit_code),
            "stderr": result.stderr_excerpt or "",
            **(details or {}),
        }
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                kind,
                message,
                object_ref=object_ref or str(repo_root),
                details=command_details,
            )
        )


__all__ = [
    "GitReleaseCommitView",
    "GitReleaseComponent",
    "GitReleaseRestoreView",
    "GitReleaseValidationView",
    "GitRepoStateView",
]
