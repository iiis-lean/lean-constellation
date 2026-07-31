"""Host-neutral Git remote binding and exact Release publication."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.publication import (
    RepoPublicationReceipt,
    RepoRemoteBinding,
)
from lean_constellation.services.external_clients.process import (
    CommandRunner,
    ExternalCommandResult,
    SubprocessCommandRunner,
)
from lean_constellation.services.foundation import ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class RepoRemotePublicationPreview(StrictModel):
    schema_version: int = 1
    binding: RepoRemoteBinding
    release_id: str
    commit: str
    release_ref: str
    expected_head: str
    expected_existing_fetch_url: str | None = None
    expected_existing_push_url: str | None = None
    recovery_token: str
    summary: str


class RepoRemotePublicationComponent:
    """Configure a declared remote and optionally publish one immutable ref."""

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

    def preview(
        self,
        repo_root: Path,
        *,
        release_id: str,
    ) -> ServiceResult[RepoRemotePublicationPreview]:
        repo_root = Path(repo_root).resolve()
        policy = self.runtime.repo_workspace.publication.resolve_policy(repo_root)
        if not policy.ok or policy.value is None:
            return self.runtime.foundation.fail(policy.issues)
        fetch_url = policy.value.policy.canonical_fetch_url
        if fetch_url is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "publication_remote_not_configured",
                    "No canonical remote URL is configured for this repository.",
                    object_ref=repo_root.name,
                )
            )
        release = self.runtime.repo_workspace.release.get_release(
            repo_root, release_id=release_id
        )
        if not release.ok or release.value is None:
            return self.runtime.foundation.fail(release.issues)
        validated = self.runtime.repo_workspace.git_release.validate_release(
            repo_root, release=release.value.release
        )
        if not validated.ok or validated.value is None:
            return self.runtime.foundation.fail(validated.issues)
        state = self.runtime.repo_workspace.git_release.inspect_repo(repo_root)
        if not state.ok or state.value is None or state.value.head_commit is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "publication_git_head_missing",
                    "Remote publication requires a committed independent Git repository.",
                    object_ref=str(repo_root),
                )
            )
        remote_name = policy.value.policy.remote_name
        existing_fetch = self._remote_url(repo_root, remote_name, push=False)
        existing_push = self._remote_url(repo_root, remote_name, push=True)
        binding = RepoRemoteBinding(
            repo_key=repo_root.name,
            remote_name=remote_name,
            fetch_url=fetch_url,
            push_url=policy.value.policy.canonical_push_url,
            configured=existing_fetch == fetch_url
            and (
                policy.value.policy.canonical_push_url is None
                or existing_push == policy.value.policy.canonical_push_url
            ),
            summary=f"Resolved canonical remote binding for {repo_root.name}.",
        )
        payload = {
            "binding": binding.model_dump(mode="json"),
            "release_id": release_id,
            "commit": validated.value.commit,
            "release_ref": validated.value.release_ref,
            "head": state.value.head_commit,
            "existing_fetch": existing_fetch,
            "existing_push": existing_push,
        }
        token = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return self.runtime.foundation.ok(
            RepoRemotePublicationPreview(
                binding=binding,
                release_id=release_id,
                commit=validated.value.commit,
                release_ref=validated.value.release_ref,
                expected_head=state.value.head_commit,
                expected_existing_fetch_url=existing_fetch,
                expected_existing_push_url=existing_push,
                recovery_token=token,
                summary=f"Previewed remote publication for {release_id}.",
            )
        )

    def apply(
        self,
        repo_root: Path,
        *,
        preview: RepoRemotePublicationPreview,
        expected_recovery_token: str,
        push: bool,
    ) -> ServiceResult[RepoPublicationReceipt]:
        repo_root = Path(repo_root).resolve()
        current = self.preview(repo_root, release_id=preview.release_id)
        if (
            not current.ok
            or current.value is None
            or expected_recovery_token != preview.recovery_token
            or current.value.recovery_token != preview.recovery_token
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "remote_publication_token_mismatch",
                    "Remote publication truth changed after preview.",
                    object_ref=preview.release_id,
                )
            )
        binding = preview.binding
        configured = self._configure_remote(repo_root, binding)
        if not configured.ok:
            return self.runtime.foundation.fail(configured.issues)
        status = "configured"
        verified_at = None
        if push:
            pushed = self._run(
                [
                    "push",
                    binding.remote_name,
                    f"{preview.release_ref}:{preview.release_ref}",
                ],
                cwd=repo_root,
            )
            if not pushed.ok:
                return self._command_failure(
                    "remote_release_push_failed",
                    "Failed to push the immutable Release ref.",
                    repo_root,
                    pushed,
                )
            verified = self._run(
                ["ls-remote", "--refs", binding.remote_name, preview.release_ref],
                cwd=repo_root,
            )
            if (
                not verified.ok
                or not any(
                    line.split(maxsplit=1)[0] == preview.commit
                    for line in (verified.stdout_excerpt or "").splitlines()
                    if line.strip()
                )
            ):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "remote_release_verification_failed",
                        "The target remote does not expose the expected Release commit.",
                        object_ref=preview.release_ref,
                        expected=preview.commit,
                    )
                )
            status = "remote_verified"
            verified_at = utc_now_iso()
        receipt = RepoPublicationReceipt(
            repo_key=repo_root.name,
            release_id=preview.release_id,
            commit=preview.commit,
            remote_name=binding.remote_name,
            remote_url=binding.push_url or binding.fetch_url,
            status=status,
            verified_at=verified_at,
            summary=(
                f"Published and verified {preview.release_id}."
                if push
                else f"Configured remote for {preview.release_id} without pushing."
            ),
        )
        path = (
            repo_root
            / ".lean_constellation"
            / "publication"
            / "remote_receipts"
            / f"{preview.release_id}.json"
        )
        written = self.runtime.foundation.store.write_json_atomic(path, receipt)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(receipt)

    def _configure_remote(
        self, repo_root: Path, binding: RepoRemoteBinding
    ) -> ServiceResult[bool]:
        existing = self._remote_url(repo_root, binding.remote_name, push=False)
        changed = False
        if existing is None:
            result = self._run(
                ["remote", "add", binding.remote_name, binding.fetch_url],
                cwd=repo_root,
            )
            changed = True
        elif existing != binding.fetch_url:
            result = self._run(
                ["remote", "set-url", binding.remote_name, binding.fetch_url],
                cwd=repo_root,
            )
            changed = True
        else:
            result = None
        if result is not None and not result.ok:
            return self._command_failure(
                "remote_binding_failed",
                "Failed to configure the canonical Git fetch URL.",
                repo_root,
                result,
            )
        if binding.push_url is not None:
            current_push = self._remote_url(
                repo_root, binding.remote_name, push=True
            )
            if current_push != binding.push_url:
                push_result = self._run(
                    [
                        "remote",
                        "set-url",
                        "--push",
                        binding.remote_name,
                        binding.push_url,
                    ],
                    cwd=repo_root,
                )
                if not push_result.ok:
                    return self._command_failure(
                        "remote_push_binding_failed",
                        "Failed to configure the canonical Git push URL.",
                        repo_root,
                        push_result,
                    )
                changed = True
        return self.runtime.foundation.ok(changed)

    def _remote_url(
        self, repo_root: Path, remote_name: str, *, push: bool
    ) -> str | None:
        command = ["remote", "get-url"]
        if push:
            command.append("--push")
        command.append(remote_name)
        result = self._run(command, cwd=repo_root)
        return (result.stdout_excerpt or "").strip() if result.ok else None

    def _run(self, args: list[str], *, cwd: Path) -> ExternalCommandResult:
        return self.runner.run(
            [self.git_bin, *args],
            cwd=cwd,
            timeout_seconds=self.timeout_seconds,
            stdout_excerpt_chars=64_000,
            stderr_excerpt_chars=64_000,
        )

    def _command_failure(
        self,
        kind: str,
        message: str,
        repo_root: Path,
        result: ExternalCommandResult,
    ) -> ServiceResult:
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                kind,
                message,
                object_ref=str(repo_root),
                details={
                    "command": " ".join(result.command),
                    "stderr": result.stderr_excerpt or "",
                },
            )
        )


__all__ = [
    "RepoRemotePublicationComponent",
    "RepoRemotePublicationPreview",
]
