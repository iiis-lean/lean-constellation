"""Static upstream compatibility verification for Adapter initialization."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.lake_project import NativeLakeProjectConfig
from lean_constellation.domain.preparation import (
    AdapterProviderRoute,
    VerifiedAdapterRouteReceipt,
)
from lean_constellation.services.external_clients import GitHubLeanRepoProbeView
from lean_constellation.services.foundation import ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class AdapterMathlibPin(StrictModel):
    present: bool
    source: str | None = None
    revision: str | None = None
    issue_code: str | None = None


class AdapterCompatibilityComponent:
    """Resolve one immutable upstream commit on the exact workspace baseline."""

    _MAX_HISTORY_CANDIDATES = 12
    _COMPATIBILITY_ISSUES = {
        "adapter_upstream_toolchain_missing",
        "adapter_upstream_toolchain_mismatch",
        "adapter_upstream_mathlib_pin_unresolved",
        "adapter_upstream_mathlib_source_mismatch",
        "adapter_upstream_mathlib_revision_mismatch",
        "adapter_upstream_mathlib_unexpected",
    }
    _OFFICIAL_MATHLIB_URLS = {
        "https://github.com/leanprover-community/mathlib",
        "https://github.com/leanprover-community/mathlib4",
    }

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        config: NativeLakeProjectConfig | None = None,
    ) -> None:
        self.runtime = runtime
        self.config = config or NativeLakeProjectConfig()

    def verify_adapter_provider_route(
        self,
        route: AdapterProviderRoute,
    ) -> ServiceResult[VerifiedAdapterRouteReceipt]:
        requested_revision = route.revision
        latest = self.runtime.external.github_repo.probe_github_lean_repo_candidate(
            route.git_url,
            revision=requested_revision,
            subdir=route.subdir,
        )
        latest_revision = (latest.resolved_revision or "").lower()
        checked = [latest_revision] if latest_revision else []
        evaluated = self._evaluate_probe(
            route,
            latest,
            resolution="explicit" if requested_revision else "latest",
            candidates_checked=checked,
        )
        if evaluated.ok or requested_revision is not None:
            return evaluated
        if not evaluated.issues or any(
            issue.kind not in self._COMPATIBILITY_ISSUES for issue in evaluated.issues
        ):
            return evaluated

        history = self.runtime.external.github_repo.list_repository_commits(
            route.git_url,
            limit=self._MAX_HISTORY_CANDIDATES,
        )
        if history.issue_code:
            return self.runtime.foundation.fail(
                [
                    *evaluated.issues,
                    self.runtime.foundation.issue(
                        history.issue_code,
                        history.summary or "Failed to enumerate upstream commit candidates.",
                        object_ref=route.git_url,
                    ),
                ]
            )
        for revision in history.commits:
            if revision in checked:
                continue
            checked.append(revision)
            probe = self.runtime.external.github_repo.probe_github_lean_repo_candidate(
                route.git_url,
                revision=revision,
                subdir=route.subdir,
            )
            candidate = self._evaluate_probe(
                route,
                probe,
                resolution="history",
                candidates_checked=checked,
            )
            if candidate.ok:
                return candidate
            if not candidate.issues or any(
                issue.kind not in self._COMPATIBILITY_ISSUES
                for issue in candidate.issues
            ):
                return candidate
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "adapter_upstream_no_exact_compatible_revision",
                "No bounded upstream commit candidate matched the workspace Lean/Mathlib baseline.",
                object_ref=route.git_url,
                expected=self.config.lean_toolchain,
                details={
                    "candidates_checked": ",".join(checked),
                    "candidate_limit": str(self._MAX_HISTORY_CANDIDATES),
                },
            )
        )

    def validate_verified_adapter_provider_route(
        self,
        route: AdapterProviderRoute,
        receipt: VerifiedAdapterRouteReceipt,
    ) -> ServiceResult[VerifiedAdapterRouteReceipt]:
        """Validate an immutable route receipt without repeating the remote probe."""

        expected_toolchain = self.config.lean_toolchain or f"leanprover/lean4:v{self.config.lean_version}"
        expected_mathlib_revision = self.config.mathlib_rev if self.config.mathlib_enabled else None
        exact_fields = {
            "git_url": (receipt.git_url, route.git_url),
            "subdir": (receipt.subdir, route.subdir),
        }
        for field_name, (current, expected) in exact_fields.items():
            if current != expected:
                return self._receipt_fail(
                    "adapter_verified_route_mismatch",
                    "Verified Adapter receipt does not match the selected provider route.",
                    route,
                    field=field_name,
                    current=current,
                    expected=expected,
                )
        if route.revision is not None and receipt.revision != route.revision:
            return self._receipt_fail(
                "adapter_verified_revision_mismatch",
                "Verified Adapter receipt changed the selected immutable revision.",
                route,
                field="revision",
                current=receipt.revision,
                expected=route.revision,
            )
        for field_name in ("package_name", "likely_import_module"):
            expected = getattr(route, field_name)
            current = getattr(receipt, field_name)
            if expected is not None and current != expected:
                return self._receipt_fail(
                    "adapter_verified_route_mismatch",
                    "Verified Adapter receipt does not match the selected provider route.",
                    route,
                    field=field_name,
                    current=current,
                    expected=expected,
                )
        if receipt.expected_lean_toolchain != expected_toolchain or receipt.lean_toolchain != expected_toolchain:
            return self._receipt_fail(
                "adapter_verified_baseline_changed",
                "The workspace Lean baseline changed after Adapter route verification.",
                route,
                field="lean_toolchain",
                current=f"{receipt.lean_toolchain} / expected {receipt.expected_lean_toolchain}",
                expected=expected_toolchain,
            )
        if receipt.expected_mathlib_revision != expected_mathlib_revision:
            return self._receipt_fail(
                "adapter_verified_baseline_changed",
                "The workspace Mathlib baseline changed after Adapter route verification.",
                route,
                field="mathlib_revision",
                current=receipt.expected_mathlib_revision,
                expected=expected_mathlib_revision,
            )
        if receipt.mathlib_revision is not None and receipt.mathlib_revision != expected_mathlib_revision:
            return self._receipt_fail(
                "adapter_verified_mathlib_mismatch",
                "The verified upstream Mathlib pin no longer matches the workspace baseline.",
                route,
                field="mathlib_revision",
                current=receipt.mathlib_revision,
                expected=expected_mathlib_revision,
            )
        return self.runtime.foundation.ok(receipt)

    def _evaluate_probe(
        self,
        route: AdapterProviderRoute,
        probe: GitHubLeanRepoProbeView,
        *,
        resolution: Literal["explicit", "latest", "history"],
        candidates_checked: list[str],
    ) -> ServiceResult[VerifiedAdapterRouteReceipt]:
        if probe.is_mathlib_repository:
            return self._fail(
                "adapter_provider_route_mathlib_forbidden",
                "Official Mathlib is the platform dependency and cannot be prepared as an adapter provider.",
                route,
            )
        if not probe.is_lean_project or not probe.has_lakefile:
            return self._fail(
                "adapter_provider_route_probe_failed",
                probe.summary or "The adapter route did not probe as a Lean Lake project.",
                route,
                details={"known_risks": "; ".join(probe.known_risks) or "not_a_lean_project"},
            )
        resolved_revision = (probe.resolved_revision or "").lower()
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", resolved_revision) is None:
            return self._fail(
                "adapter_provider_route_revision_unresolved",
                "The remote probe did not resolve an immutable upstream commit.",
                route,
                current=probe.resolved_revision,
            )
        if route.revision is not None and resolved_revision != route.revision:
            return self._fail(
                "adapter_provider_route_revision_mismatch",
                "The remote probe did not resolve to the explicitly requested immutable commit.",
                route,
                current=resolved_revision,
                expected=route.revision,
            )
        selected_subdir = probe.selected_subdir or None
        if route.subdir is not None and selected_subdir != route.subdir:
            return self._fail(
                "adapter_provider_route_subdir_mismatch",
                "The remote probe selected a different Lean project subdirectory.",
                route,
                current=selected_subdir,
                expected=route.subdir,
            )
        if route.package_name and probe.package_name != route.package_name:
            return self._fail(
                "adapter_provider_route_package_mismatch",
                "The remote Lake package does not match the confirmed route.",
                route,
                current=probe.package_name,
                expected=route.package_name,
            )
        if route.likely_import_module and route.likely_import_module not in probe.likely_import_modules:
            return self._fail(
                "adapter_provider_route_module_mismatch",
                "The confirmed import module was not found by the exact remote probe.",
                route,
                current=", ".join(probe.likely_import_modules),
                expected=route.likely_import_module,
            )
        if probe.lakefile_issue_code or probe.lakefile_truncated:
            return self._fail(
                "adapter_upstream_lakefile_unreadable",
                "The upstream Lake file must be completely readable for static compatibility verification.",
                route,
                current=probe.lakefile_issue_code or "truncated",
                expected="complete Lake file",
            )
        expected_toolchain = self.config.lean_toolchain or f"leanprover/lean4:v{self.config.lean_version}"
        actual_toolchain = (probe.lean_toolchain or "").strip()
        if not actual_toolchain:
            return self._fail(
                "adapter_upstream_toolchain_missing",
                "The upstream Lean project has no statically readable lean-toolchain.",
                route,
                expected=expected_toolchain,
            )
        if actual_toolchain != expected_toolchain:
            return self._fail(
                "adapter_upstream_toolchain_mismatch",
                "The upstream Lean toolchain does not exactly match the Adapter workspace baseline.",
                route,
                current=actual_toolchain,
                expected=expected_toolchain,
            )
        mathlib = self._mathlib_pin(probe)
        if mathlib.issue_code:
            return self._fail(
                mathlib.issue_code,
                "The upstream Mathlib dependency is not a statically exact immutable pin.",
                route,
                current=mathlib.source,
                expected=self.config.mathlib_rev,
            )
        if mathlib.present and not self.config.mathlib_enabled:
            return self._fail(
                "adapter_upstream_mathlib_unexpected",
                "The upstream requires Mathlib but the Adapter workspace baseline disables it.",
                route,
                current=mathlib.revision,
                expected="disabled",
            )
        if mathlib.present:
            if not self._mathlib_source_allowed(mathlib.source):
                return self._fail(
                    "adapter_upstream_mathlib_source_mismatch",
                    "The upstream Mathlib source does not match the workspace platform source.",
                    route,
                    current=mathlib.source,
                    expected=f"scope:{self.config.mathlib_scope}",
                )
            if mathlib.revision != self.config.mathlib_rev:
                return self._fail(
                    "adapter_upstream_mathlib_revision_mismatch",
                    "The upstream Mathlib revision does not exactly match the Adapter workspace baseline.",
                    route,
                    current=mathlib.revision,
                    expected=self.config.mathlib_rev,
                )
        package_name = (route.package_name or probe.package_name or "").strip()
        if not package_name:
            return self._fail(
                "adapter_upstream_package_missing",
                "The exact remote probe did not identify a Lake package.",
                route,
            )
        likely_modules = [module.strip() for module in probe.likely_import_modules if module.strip()]
        likely_import_module = route.likely_import_module or (likely_modules[0] if likely_modules else None)
        if likely_import_module is None:
            return self._fail(
                "adapter_upstream_import_module_missing",
                "The exact remote probe did not identify an importable Lean module.",
                route,
            )
        return self.runtime.foundation.ok(
            VerifiedAdapterRouteReceipt(
                git_url=probe.normalized_git_url,
                revision=resolved_revision,
                subdir=selected_subdir,
                package_name=package_name,
                likely_import_module=likely_import_module,
                lean_toolchain=actual_toolchain,
                mathlib_source=mathlib.source,
                mathlib_revision=mathlib.revision,
                expected_lean_toolchain=expected_toolchain,
                expected_mathlib_revision=self.config.mathlib_rev if self.config.mathlib_enabled else None,
                revision_resolution=resolution,
                candidates_checked=list(candidates_checked),
                evidence_summary=(
                    f"{probe.evidence_summary} Static compatibility matched "
                    f"{expected_toolchain}"
                    + (
                        f" / Mathlib {self.config.mathlib_rev}."
                        if self.config.mathlib_enabled and mathlib.present
                        else "."
                    )
                ),
            )
        )

    def _mathlib_pin(self, probe: GitHubLeanRepoProbeView) -> AdapterMathlibPin:
        text = probe.lakefile_excerpt or ""
        lakefile = probe.lakefile_paths[0] if probe.lakefile_paths else ""
        if lakefile.endswith(".toml"):
            return self._toml_mathlib_pin(text)
        return self._lean_mathlib_pin(text)

    def _toml_mathlib_pin(self, text: str) -> AdapterMathlibPin:
        blocks = re.finditer(
            r"(?ms)^\s*\[\[require\]\]\s*\n(?P<body>.*?)(?=^\s*\[|\Z)",
            text,
        )
        for block_match in blocks:
            block = block_match.group("body")
            values = {
                match.group(1): match.group(2)
                for line in block.splitlines()
                if (match := re.match(r'\s*([A-Za-z0-9_.-]+)\s*=\s*"([^"]*)"', line))
            }
            if values.get("name") != "mathlib":
                continue
            revision = values.get("rev") or values.get("revision")
            if values.get("scope"):
                source = f"scope:{values['scope']}"
            elif values.get("git"):
                source = f"git:{values['git'].removesuffix('.git')}"
            else:
                source = "path" if values.get("path") else None
            if not source or not revision or source == "path":
                return AdapterMathlibPin(
                    present=True,
                    source=source,
                    revision=revision,
                    issue_code="adapter_upstream_mathlib_pin_unresolved",
                )
            return AdapterMathlibPin(present=True, source=source, revision=revision)
        return AdapterMathlibPin(present=False)

    def _lean_mathlib_pin(self, text: str) -> AdapterMathlibPin:
        registry = re.search(
            r'(?m)^\s*require\s+"(?P<scope>[^"]+)"\s*/\s*"mathlib"\s*@\s*git\s*"(?P<rev>[^"]+)"',
            text,
        )
        if registry:
            return AdapterMathlibPin(
                present=True,
                source=f"scope:{registry.group('scope')}",
                revision=registry.group("rev"),
            )
        git = re.search(
            r'(?ms)^\s*require\s+mathlib\s+from\s+git\s*"(?P<url>[^"]+)"\s*@\s*"(?P<rev>[^"]+)"',
            text,
        )
        if git:
            return AdapterMathlibPin(
                present=True,
                source=f"git:{git.group('url').removesuffix('.git')}",
                revision=git.group("rev"),
            )
        if re.search(r"(?m)^\s*require\b[^\n]*\bmathlib\b|^\s*require\s+mathlib\b", text):
            return AdapterMathlibPin(
                present=True,
                issue_code="adapter_upstream_mathlib_pin_unresolved",
            )
        return AdapterMathlibPin(present=False)

    def _mathlib_source_allowed(self, source: str | None) -> bool:
        if source == f"scope:{self.config.mathlib_scope}":
            return True
        if source and source.startswith("git:"):
            return source.removeprefix("git:").removesuffix(".git") in self._OFFICIAL_MATHLIB_URLS
        return False

    def _fail(
        self,
        kind: str,
        message: str,
        route: AdapterProviderRoute,
        *,
        current: str | None = None,
        expected: str | None = None,
        details: dict[str, str] | None = None,
    ) -> ServiceResult[VerifiedAdapterRouteReceipt]:
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                kind,
                message,
                object_ref=route.git_url,
                field="provider_route",
                current=current,
                expected=expected,
                details=details or {},
            )
        )

    def _receipt_fail(
        self,
        kind: str,
        message: str,
        route: AdapterProviderRoute,
        *,
        field: str,
        current: str | None,
        expected: str | None,
    ) -> ServiceResult[VerifiedAdapterRouteReceipt]:
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                kind,
                message,
                object_ref=route.git_url,
                field=field,
                current=current,
                expected=expected,
            )
        )


__all__ = [
    "AdapterCompatibilityComponent",
    "AdapterMathlibPin",
]
