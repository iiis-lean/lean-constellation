"""Format-aware provider availability checks."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.repo import RepoFormat, RepoPublicationStatus
from lean_constellation.services.foundation import GateReport, ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.repo_workspace.repo_metadata import RepoMetadataComponent
    from lean_constellation.services.repo_workspace.repo_release import RepoReleaseComponent
    from lean_constellation.services.runtime import LeanRuntimeServices


class ProviderAvailabilityComponent:
    """Resolve provider readiness without duplicating format-specific truth."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        metadata: RepoMetadataComponent,
        release: RepoReleaseComponent,
    ) -> None:
        self.runtime = runtime
        self.metadata = metadata
        self.release = release

    def check_provider_available(self, repo_root: Path) -> ServiceResult[GateReport]:
        repo_root = Path(repo_root)
        repo_format = self.metadata.get_repo_format(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        publication = self.metadata.get_repo_publication(repo_root)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        state = publication.value.publication
        if state.status != RepoPublicationStatus.STABLE:
            return self._blocked(
                "provider_not_stable",
                "Provider repo is not stably published.",
                repo_root,
                current=state.status.value,
                expected=RepoPublicationStatus.STABLE.value,
            )
        if repo_format.value.repo_format == RepoFormat.UNKNOWN:
            return self._blocked("provider_format_unknown", "Unknown-format repos cannot be providers.", repo_root)
        if repo_format.value.repo_format not in {RepoFormat.NATIVE, RepoFormat.ADAPTER}:
            return self._blocked("provider_format_unknown", "Unsupported-format repos cannot be providers.", repo_root)
        issue_prefix = (
            "provider_native"
            if repo_format.value.repo_format == RepoFormat.NATIVE
            else "provider_adapter"
        )
        if state.latest_release_id is None:
            return self._blocked(
                f"{issue_prefix}_stable_release_missing",
                "Stable providers require a current repository release baseline.",
                repo_root,
            )
        release = self.release.get_release(repo_root, release_id=state.latest_release_id)
        if not release.ok or release.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    f"{issue_prefix}_release_missing",
                    "Provider publication points to a missing or unreadable release.",
                    object_ref=state.latest_release_id,
                )
            )
        git_release = self.runtime.repo_workspace.git_release.validate_release(
            repo_root,
            release=release.value.release,
        )
        if not git_release.ok or git_release.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    f"{issue_prefix}_git_release_invalid",
                    "Provider Git Release ref, commit, or manifest is missing or unreadable.",
                    object_ref=state.latest_release_id,
                    details={
                        "issues": "; ".join(issue.kind for issue in git_release.issues)
                    },
                )
            )
        if repo_format.value.repo_format == RepoFormat.ADAPTER:
            ready = self.runtime.adapter.check_adapter_ready(repo_root)
            if not ready.ok or ready.value is None:
                if any(issue.kind in {"invalid_json", "read_failed", "schema_validation_failed"} for issue in ready.issues):
                    return self.runtime.foundation.fail(ready.issues)
                issue = self.runtime.foundation.issue(
                    "provider_adapter_not_ready",
                    "Adapter provider has not passed its ready gate.",
                    object_ref=str(repo_root),
                    details={"adapter_issues": "; ".join(item.kind for item in ready.issues)},
                )
                return self.runtime.foundation.ok(
                    self.runtime.foundation.gate_failed("provider_availability", [issue, *ready.issues])
                )
            if not ready.value.passed:
                issue = self.runtime.foundation.issue(
                    "provider_adapter_not_ready",
                    "Adapter provider has not passed its ready gate.",
                    object_ref=str(repo_root),
                    details={"adapter_issue_count": str(len(ready.value.issues))},
                )
                return self.runtime.foundation.ok(
                    self.runtime.foundation.gate_failed("provider_availability", [issue, *ready.value.issues])
                )
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_passed(
                    "provider_availability",
                    summary=f"Adapter provider release {state.latest_release_id} is available.",
                )
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "provider_availability",
                summary=f"Native provider release {state.latest_release_id} is available.",
            )
        )

    def _blocked(
        self,
        kind: str,
        message: str,
        repo_root: Path,
        *,
        current: str | None = None,
        expected: str | None = None,
    ) -> ServiceResult[GateReport]:
        issue = self.runtime.foundation.issue(
            kind,
            message,
            object_ref=str(repo_root),
            current=current,
            expected=expected,
        )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_failed("provider_availability", issue, summary=message)
        )


__all__ = ["ProviderAvailabilityComponent"]
