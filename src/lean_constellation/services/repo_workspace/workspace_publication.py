"""Workspace-level topology and optional Git superproject publication."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.publication import GitCommitIdentity
from lean_constellation.services.external_clients.process import (
    CommandRunner,
    ExternalCommandResult,
    SubprocessCommandRunner,
)
from lean_constellation.services.foundation import ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class WorkspaceChildRelease(StrictModel):
    repo_key: str
    release_id: str
    commit: str
    clone_url: str | None = None
    dependencies: list[str] = Field(default_factory=list)


class WorkspacePublicationPreview(StrictModel):
    schema_version: int = 1
    workspace_key: str
    children: list[WorkspaceChildRelease]
    topological_repo_keys: list[str]
    superproject_required: bool
    output_root: str | None = None
    expected_output_head: str | None = None
    push_children: bool = False
    push_superproject: bool = False
    superproject_remote_name: str | None = None
    superproject_fetch_url: str | None = None
    superproject_push_url: str | None = None
    recovery_token: str
    summary: str


class WorkspaceReleaseManifest(StrictModel):
    schema_version: int = 1
    workspace_key: str
    children: list[WorkspaceChildRelease]
    topological_repo_keys: list[str]
    created_at: str | None = None


class WorkspacePublicationReceipt(StrictModel):
    schema_version: int = 1
    workspace_key: str
    child_release_ids: dict[str, str]
    child_commits: dict[str, str]
    superproject_created: bool
    superproject_root: str | None = None
    superproject_commit: str | None = None
    pushed_child_repo_keys: list[str] = Field(default_factory=list)
    superproject_remote_verified: bool = False
    changed: bool
    summary: str


class WorkspacePublicationComponent:
    """Reuse child Releases and create a superproject only for multi-repo sets."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        runner: CommandRunner | None = None,
        git_bin: str = "git",
    ) -> None:
        self.runtime = runtime
        self.runner = runner or SubprocessCommandRunner()
        self.git_bin = git_bin

    def preview(
        self,
        workspace_root: Path,
        *,
        repo_keys: list[str] | None = None,
        output_root: Path | None = None,
        push_children: bool = False,
        push_superproject: bool = False,
    ) -> ServiceResult[WorkspacePublicationPreview]:
        workspace_root = Path(workspace_root).resolve()
        keys = (
            sorted(
                path.name
                for path in workspace_root.iterdir()
                if path.is_dir()
                and (path / ".lean_constellation" / "repo.json").exists()
            )
            if repo_keys is None
            else sorted(
                {
                    self.runtime.foundation.layout.ensure_safe_key(key)
                    for key in repo_keys
                }
            )
        )
        if not keys:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "workspace_publication_empty",
                    "Workspace publication requires at least one LC repository.",
                    object_ref=str(workspace_root),
                )
            )
        children: list[WorkspaceChildRelease] = []
        internal_dependencies: dict[str, set[str]] = {key: set() for key in keys}
        for key in keys:
            repo_root = workspace_root / key
            latest = self.runtime.repo_workspace.release.get_latest_release(repo_root)
            if not latest.ok or latest.value is None:
                return self.runtime.foundation.fail(latest.issues)
            validated = self.runtime.repo_workspace.git_release.validate_release(
                repo_root, release=latest.value.release
            )
            if not validated.ok or validated.value is None:
                return self.runtime.foundation.fail(validated.issues)
            dependencies = (
                self.runtime.repo_workspace.lake_dependency.parse_lake_dependencies(
                    repo_root
                )
            )
            if not dependencies.ok or dependencies.value is None:
                return self.runtime.foundation.fail(dependencies.issues)
            dependency_keys = sorted(
                {
                    item.name
                    for item in dependencies.value.dependencies
                    if item.name in internal_dependencies
                }
            )
            internal_dependencies[key].update(dependency_keys)
            policy = self.runtime.repo_workspace.publication.resolve_policy(
                repo_root, repo_key=key
            )
            if not policy.ok or policy.value is None:
                return self.runtime.foundation.fail(policy.issues)
            children.append(
                WorkspaceChildRelease(
                    repo_key=key,
                    release_id=latest.value.release.release_id,
                    commit=validated.value.commit,
                    clone_url=policy.value.policy.canonical_fetch_url,
                    dependencies=dependency_keys,
                )
            )
        order = self._topological_order(internal_dependencies)
        if order is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "workspace_dependency_cycle",
                    "Workspace repository dependency graph contains a cycle.",
                    object_ref=workspace_root.name,
                )
            )
        superproject_required = len(children) > 1
        resolved_output = (
            Path(output_root).resolve()
            if output_root is not None
            else (
                workspace_root / "lean-constellation-workspace"
                if superproject_required
                else None
            )
        )
        if superproject_required:
            missing = [
                item.repo_key for item in children if item.clone_url is None
            ]
            if missing:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "workspace_child_clone_url_missing",
                        "Multi-repo superproject requires canonical clone URLs.",
                        details={"repo_keys": ",".join(missing)},
                    )
                )
        super_remote_name = None
        super_fetch_url = None
        super_push_url = None
        if push_superproject:
            if not superproject_required:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "workspace_superproject_not_required",
                        "A single-repo Workspace has no superproject to push.",
                        object_ref=workspace_root.name,
                    )
                )
            resolved_super = self._resolve_superproject_remote(
                workspace_root,
                output_root=resolved_output,
            )
            if not resolved_super.ok or resolved_super.value is None:
                return self.runtime.foundation.fail(resolved_super.issues)
            super_remote_name, super_fetch_url, super_push_url = (
                resolved_super.value
            )
        output_head = (
            self._resolve_optional_head(resolved_output)
            if resolved_output is not None
            else None
        )
        payload = {
            "workspace": workspace_root.name,
            "children": [item.model_dump(mode="json") for item in children],
            "order": order,
            "superproject": superproject_required,
            "output": str(resolved_output) if resolved_output is not None else None,
            "output_head": output_head,
            "push_children": push_children,
            "push_superproject": push_superproject,
            "superproject_remote_name": super_remote_name,
            "superproject_fetch_url": super_fetch_url,
            "superproject_push_url": super_push_url,
        }
        token = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return self.runtime.foundation.ok(
            WorkspacePublicationPreview(
                workspace_key=workspace_root.name,
                children=children,
                topological_repo_keys=order,
                superproject_required=superproject_required,
                output_root=(
                    str(resolved_output) if resolved_output is not None else None
                ),
                expected_output_head=output_head,
                push_children=push_children,
                push_superproject=push_superproject,
                superproject_remote_name=super_remote_name,
                superproject_fetch_url=super_fetch_url,
                superproject_push_url=super_push_url,
                recovery_token=token,
                summary=(
                    "Single-repo workspace reuses its repository Release."
                    if not superproject_required
                    else "Previewed multi-repo Git superproject."
                ),
            )
        )

    def apply(
        self,
        workspace_root: Path,
        *,
        preview: WorkspacePublicationPreview,
        expected_recovery_token: str,
    ) -> ServiceResult[WorkspacePublicationReceipt]:
        refreshed = self.preview(
            workspace_root,
            repo_keys=[item.repo_key for item in preview.children],
            output_root=(
                Path(preview.output_root)
                if preview.output_root is not None
                else None
            ),
            push_children=preview.push_children,
            push_superproject=preview.push_superproject,
        )
        if (
            not refreshed.ok
            or refreshed.value is None
            or expected_recovery_token != preview.recovery_token
            or refreshed.value.recovery_token != preview.recovery_token
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "workspace_publication_token_mismatch",
                    "Workspace publication truth changed after preview.",
                    object_ref=preview.workspace_key,
                )
            )
        release_ids = {
            item.repo_key: item.release_id for item in preview.children
        }
        commits = {item.repo_key: item.commit for item in preview.children}
        pushed_children: list[str] = []
        if preview.push_children:
            for repo_key in preview.topological_repo_keys:
                child = next(
                    item for item in preview.children if item.repo_key == repo_key
                )
                remote_preview = (
                    self.runtime.repo_workspace.remote_publication.preview(
                        Path(workspace_root) / repo_key,
                        release_id=child.release_id,
                    )
                )
                if not remote_preview.ok or remote_preview.value is None:
                    return self.runtime.foundation.fail(remote_preview.issues)
                pushed = self.runtime.repo_workspace.remote_publication.apply(
                    Path(workspace_root) / repo_key,
                    preview=remote_preview.value,
                    expected_recovery_token=remote_preview.value.recovery_token,
                    push=True,
                )
                if not pushed.ok:
                    return self.runtime.foundation.fail(pushed.issues)
                pushed_children.append(repo_key)
        if not preview.superproject_required:
            return self.runtime.foundation.ok(
                WorkspacePublicationReceipt(
                    workspace_key=preview.workspace_key,
                    child_release_ids=release_ids,
                    child_commits=commits,
                    superproject_created=False,
                    pushed_child_repo_keys=pushed_children,
                    changed=False,
                    summary="Single-repo workspace needs no superproject.",
                )
            )
        output_root = Path(preview.output_root or "").resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        initialized = self._run(["init", "."], cwd=output_root)
        if not initialized.ok:
            return self._failure(
                "workspace_git_init_failed",
                "Failed to initialize the Workspace superproject.",
                output_root,
                initialized,
            )
        current_head = self._resolve_optional_head(output_root)
        if current_head != preview.expected_output_head:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "workspace_publication_head_drift",
                    "Workspace superproject HEAD changed after preview.",
                    current=current_head or "<unborn>",
                    expected=preview.expected_output_head or "<unborn>",
                )
            )
        manifest = WorkspaceReleaseManifest(
            workspace_key=preview.workspace_key,
            children=preview.children,
            topological_repo_keys=preview.topological_repo_keys,
        )
        (output_root / "workspace.json").write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        (output_root / "README.md").write_text(
            self._render_readme(preview), encoding="utf-8"
        )
        (output_root / ".gitmodules").write_text(
            self._render_gitmodules(preview.children), encoding="utf-8"
        )
        (output_root / ".gitignore").write_text(
            "/repos/*\n", encoding="utf-8"
        )
        committed = self._commit_superproject(
            output_root,
            preview=preview,
            expected_head=current_head,
        )
        if not committed.ok or committed.value is None:
            return self.runtime.foundation.fail(committed.issues)
        commit_id, commit_changed = committed.value
        superproject_remote_verified = False
        if preview.push_superproject:
            remote = self._publish_superproject_remote(
                output_root,
                preview=preview,
                commit=commit_id,
            )
            if not remote.ok:
                return self.runtime.foundation.fail(remote.issues)
            superproject_remote_verified = True
        return self.runtime.foundation.ok(
            WorkspacePublicationReceipt(
                workspace_key=preview.workspace_key,
                child_release_ids=release_ids,
                child_commits=commits,
                superproject_created=True,
                superproject_root=str(output_root),
                superproject_commit=commit_id,
                pushed_child_repo_keys=pushed_children,
                superproject_remote_verified=superproject_remote_verified,
                changed=commit_changed,
                summary="Committed exact-child Workspace superproject.",
            )
        )

    def _commit_superproject(
        self,
        output_root: Path,
        *,
        preview: WorkspacePublicationPreview,
        expected_head: str | None,
    ) -> ServiceResult[tuple[str, bool]]:
        descriptor, index_path = tempfile.mkstemp(
            prefix="lean-constellation-workspace-index-"
        )
        os.close(descriptor)
        Path(index_path).unlink(missing_ok=True)
        env = {"GIT_INDEX_FILE": index_path}
        try:
            empty = self._run(["read-tree", "--empty"], cwd=output_root, env=env)
            if not empty.ok:
                return self._failure(
                    "workspace_index_init_failed",
                    "Failed to initialize the Workspace publication index.",
                    output_root,
                    empty,
                )
            added = self._run(
                ["add", "--force", ".gitignore", ".gitmodules", "README.md", "workspace.json"],
                cwd=output_root,
                env=env,
            )
            if not added.ok:
                return self._failure(
                    "workspace_files_stage_failed",
                    "Failed to stage Workspace publication documents.",
                    output_root,
                    added,
                )
            for child in preview.children:
                gitlink = self._run(
                    [
                        "update-index",
                        "--add",
                        "--cacheinfo",
                        f"160000,{child.commit},repos/{child.repo_key}",
                    ],
                    cwd=output_root,
                    env=env,
                )
                if not gitlink.ok:
                    return self._failure(
                        "workspace_gitlink_stage_failed",
                        "Failed to stage an exact child Git commit.",
                        output_root,
                        gitlink,
                    )
            tree = self._run(["write-tree"], cwd=output_root, env=env)
            if not tree.ok:
                return self._failure(
                    "workspace_tree_failed",
                    "Failed to write the Workspace Git tree.",
                    output_root,
                    tree,
                )
            tree_id = (tree.stdout_excerpt or "").strip()
            if expected_head is not None:
                previous_tree = self._run(
                    ["show", "-s", "--format=%T", expected_head],
                    cwd=output_root,
                )
                if (
                    previous_tree.ok
                    and (previous_tree.stdout_excerpt or "").strip() == tree_id
                ):
                    workspace_ref = (
                        "refs/lean-constellation/workspaces/"
                        f"{self.runtime.foundation.layout.ensure_safe_key(preview.workspace_key)}"
                    )
                    current_workspace = self._resolve_optional_commit(
                        output_root,
                        workspace_ref,
                    )
                    if current_workspace != expected_head:
                        update_workspace = self._run(
                            ["update-ref", workspace_ref, expected_head],
                            cwd=output_root,
                        )
                        if not update_workspace.ok:
                            return self._failure(
                                "workspace_ref_update_failed",
                                "Failed to publish the Workspace ref.",
                                output_root,
                                update_workspace,
                            )
                    return self.runtime.foundation.ok((expected_head, False))
            command = ["commit-tree", tree_id]
            if expected_head is not None:
                command.extend(["-p", expected_head])
            identity = (
                self.runtime.repo_workspace.workspace_config.publication.repo_defaults.commit_identity
                or GitCommitIdentity(
                    name="Lean Constellation",
                    email="lean-constellation@localhost",
                )
            )
            commit = self._run(
                command,
                cwd=output_root,
                env={
                    "GIT_AUTHOR_NAME": identity.name,
                    "GIT_AUTHOR_EMAIL": identity.email,
                    "GIT_COMMITTER_NAME": identity.name,
                    "GIT_COMMITTER_EMAIL": identity.email,
                },
                input_text="release(workspace): update exact child releases\n",
            )
            if not commit.ok:
                return self._failure(
                    "workspace_commit_failed",
                    "Failed to create the Workspace Git commit.",
                    output_root,
                    commit,
                )
            commit_id = (commit.stdout_excerpt or "").strip()
            branch = self._run(
                ["symbolic-ref", "-q", "HEAD"], cwd=output_root
            )
            branch_ref = (
                (branch.stdout_excerpt or "").strip()
                if branch.ok
                else "refs/heads/main"
            )
            workspace_ref = (
                "refs/lean-constellation/workspaces/"
                f"{self.runtime.foundation.layout.ensure_safe_key(preview.workspace_key)}"
            )
            commands = [f"update {branch_ref} {commit_id}"]
            if expected_head is None:
                commands[0] = f"create {branch_ref} {commit_id}"
            else:
                commands[0] += f" {expected_head}"
            previous_workspace_commit = self._resolve_optional_commit(
                output_root,
                workspace_ref,
            )
            if previous_workspace_commit is None:
                commands.append(f"create {workspace_ref} {commit_id}")
            else:
                commands.append(
                    f"update {workspace_ref} {commit_id} "
                    f"{previous_workspace_commit}"
                )
            commands.append("")
            update = self._run(
                ["update-ref", "--stdin"],
                cwd=output_root,
                input_text="\n".join(commands),
            )
            if not update.ok:
                return self._failure(
                    "workspace_ref_update_failed",
                    "Failed to publish the Workspace branch.",
                    output_root,
                    update,
                )
            self._run(["read-tree", commit_id], cwd=output_root)
            return self.runtime.foundation.ok((commit_id, True))
        finally:
            Path(index_path).unlink(missing_ok=True)

    def _resolve_superproject_remote(
        self,
        workspace_root: Path,
        *,
        output_root: Path | None,
    ) -> ServiceResult[tuple[str, str, str | None]]:
        policy = self.runtime.repo_workspace.workspace_config.publication
        profile_name = policy.superproject_remote_profile
        if profile_name is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "workspace_superproject_remote_missing",
                    "Superproject push requires a configured remote profile.",
                    object_ref=workspace_root.name,
                )
            )
        profile = policy.remote_profiles.get(profile_name)
        if profile is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "workspace_superproject_remote_profile_missing",
                    "Superproject publication references an unknown remote profile.",
                    object_ref=profile_name,
                )
            )
        repo_name = (
            policy.superproject_remote_name
            or (output_root.name if output_root is not None else workspace_root.name)
        )
        values = {
            **profile.values,
            "workspace_key": workspace_root.name,
            "workspace_slug": workspace_root.name,
            "repo_key": repo_name,
            "repo_slug": repo_name,
            "repo_name": repo_name,
        }
        try:
            fetch = profile.fetch_url_template.format(**values)
            push = (
                profile.push_url_template.format(**values)
                if profile.push_url_template is not None
                else None
            )
        except KeyError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "workspace_remote_template_value_missing",
                    f"Workspace remote profile value is missing: {exc.args[0]}",
                    object_ref=profile_name,
                )
            )
        return self.runtime.foundation.ok(("origin", fetch, push))

    def _publish_superproject_remote(
        self,
        output_root: Path,
        *,
        preview: WorkspacePublicationPreview,
        commit: str,
    ) -> ServiceResult[bool]:
        remote_name = preview.superproject_remote_name or "origin"
        fetch_url = preview.superproject_fetch_url
        if fetch_url is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "workspace_superproject_remote_missing",
                    "Superproject push is missing its previewed remote URL.",
                )
            )
        existing = self._run(
            ["remote", "get-url", remote_name],
            cwd=output_root,
        )
        if existing.ok:
            configured = self._run(
                ["remote", "set-url", remote_name, fetch_url],
                cwd=output_root,
            )
        else:
            configured = self._run(
                ["remote", "add", remote_name, fetch_url],
                cwd=output_root,
            )
        if not configured.ok:
            return self._failure(
                "workspace_remote_config_failed",
                "Failed to configure the Workspace superproject remote.",
                output_root,
                configured,
            )
        if preview.superproject_push_url is not None:
            configured_push = self._run(
                [
                    "remote",
                    "set-url",
                    "--push",
                    remote_name,
                    preview.superproject_push_url,
                ],
                cwd=output_root,
            )
            if not configured_push.ok:
                return self._failure(
                    "workspace_remote_push_url_failed",
                    "Failed to configure the Workspace superproject push URL.",
                    output_root,
                    configured_push,
                )
        workspace_ref = (
            "refs/lean-constellation/workspaces/"
            f"{self.runtime.foundation.layout.ensure_safe_key(preview.workspace_key)}"
        )
        pushed = self._run(
            ["push", remote_name, f"{workspace_ref}:{workspace_ref}"],
            cwd=output_root,
        )
        if not pushed.ok:
            return self._failure(
                "workspace_remote_push_failed",
                "Failed to push the Workspace publication ref.",
                output_root,
                pushed,
            )
        verified = self._run(
            ["ls-remote", "--refs", remote_name, workspace_ref],
            cwd=output_root,
        )
        if (
            not verified.ok
            or not any(
                line.split(maxsplit=1)[0] == commit
                for line in (verified.stdout_excerpt or "").splitlines()
                if line.strip()
            )
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "workspace_remote_verification_failed",
                    "Workspace remote does not expose the expected commit.",
                    object_ref=workspace_ref,
                    expected=commit,
                )
            )
        return self.runtime.foundation.ok(True)

    def _resolve_optional_commit(self, root: Path, ref: str) -> str | None:
        result = self._run(["rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=root)
        return (result.stdout_excerpt or "").strip() if result.ok else None

    @staticmethod
    def _topological_order(
        dependencies: dict[str, set[str]],
    ) -> list[str] | None:
        remaining = {key: set(value) for key, value in dependencies.items()}
        order: list[str] = []
        while remaining:
            ready = sorted(key for key, value in remaining.items() if not value)
            if not ready:
                return None
            order.extend(ready)
            for key in ready:
                remaining.pop(key)
            for value in remaining.values():
                value.difference_update(ready)
        return order

    @staticmethod
    def _render_gitmodules(children: list[WorkspaceChildRelease]) -> str:
        return "\n".join(
            (
                f'[submodule "repos/{item.repo_key}"]\n'
                f"\tpath = repos/{item.repo_key}\n"
                f"\turl = {item.clone_url}\n"
            )
            for item in children
        )

    @staticmethod
    def _render_readme(preview: WorkspacePublicationPreview) -> str:
        children = "\n".join(
            f"- `{item.repo_key}`: `{item.release_id}` / `{item.commit}`"
            for item in preview.children
        )
        return (
            f"# {preview.workspace_key}\n\n"
            "Lean Constellation Workspace publication.\n\n"
            "## Repositories\n\n"
            f"{children}\n"
        )

    def _resolve_optional_head(self, root: Path | None) -> str | None:
        if root is None or not root.exists():
            return None
        result = self._run(["rev-parse", "--verify", "HEAD"], cwd=root)
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
            cwd=cwd,
            timeout_seconds=120,
            stdout_excerpt_chars=64_000,
            stderr_excerpt_chars=64_000,
            env=env,
            input_text=input_text,
        )

    def _failure(
        self,
        kind: str,
        message: str,
        root: Path,
        result: ExternalCommandResult,
    ) -> ServiceResult:
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                kind,
                message,
                object_ref=str(root),
                details={"stderr": result.stderr_excerpt or ""},
            )
        )


__all__ = [
    "WorkspaceChildRelease",
    "WorkspacePublicationComponent",
    "WorkspacePublicationPreview",
    "WorkspacePublicationReceipt",
    "WorkspaceReleaseManifest",
]
