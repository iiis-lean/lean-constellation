"""Adapter upstream metadata truth and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import (
    FoundationContext,
    FoundationService,
    GateReport,
    ServiceResult,
    WriteMode,
)


class AdapterUpstreamMetadata(StrictModel):
    """Repo-level truth for the single upstream Lean repo adapted by this repo."""

    source_kind: Literal["git", "local_path"] = "git"
    git_url: str | None = None
    revision: str | None = None
    subdir: str | None = None
    local_path: str | None = None
    package_name: str | None = None
    dependency_name: str | None = None
    evidence_summary: str | None = None
    setup_summary: str | None = None
    trusted_build: bool = False
    visible_modules: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_source(self) -> "AdapterUpstreamMetadata":
        if self.source_kind == "git" and not (self.git_url and self.git_url.strip()):
            raise ValueError("git adapter upstream requires git_url")
        if self.source_kind == "git" and self.local_path and self.local_path.strip():
            raise ValueError("git adapter upstream must not also set local_path")
        if self.source_kind == "local_path" and not (self.local_path and self.local_path.strip()):
            raise ValueError("local_path adapter upstream requires local_path")
        if self.source_kind == "local_path" and self.git_url and self.git_url.strip():
            raise ValueError("local_path adapter upstream must not also set git_url")
        if not (self.package_name and self.package_name.strip()):
            raise ValueError("package_name is required")
        if not (self.dependency_name and self.dependency_name.strip()):
            raise ValueError("dependency_name is required")
        return self

    @field_validator("visible_modules")
    @classmethod
    def _dedupe_modules(cls, value: list[str]) -> list[str]:
        return sorted({item.strip() for item in value if item and item.strip()})


class AdapterUpstreamView(StrictModel):
    source_kind: Literal["git", "local_path"]
    git_url: str | None = None
    revision: str | None = None
    subdir: str | None = None
    package_name: str
    dependency_name: str
    evidence_summary: str | None = None
    setup_summary: str | None = None
    trusted_build: bool
    visible_module_count: int = 0
    source_summary: str
    summary: str


class AdapterUpstreamStatusView(StrictModel):
    source_summary: str
    package_name: str
    dependency_name: str
    trusted_build: bool
    setup_summary: str | None = None
    visible_module_count: int = 0
    summary: str


class UpstreamMetadataComponent:
    """Read, write, and validate adapter upstream metadata."""

    def __init__(self, foundation: FoundationService | None = None) -> None:
        self.foundation = foundation or FoundationService()

    def write_adapter_upstream_metadata(
        self,
        repo_root: Path,
        *,
        source_kind: Literal["git", "local_path"] = "git",
        git_url: str | None = None,
        revision: str | None = None,
        subdir: str | None = None,
        local_path: str | None = None,
        package_name: str,
        dependency_name: str,
        evidence_summary: str | None = None,
        setup_summary: str | None = None,
        visible_modules: list[str] | None = None,
    ) -> ServiceResult[AdapterUpstreamView]:
        try:
            metadata = AdapterUpstreamMetadata(
                source_kind=source_kind,
                git_url=self._strip(git_url),
                revision=self._strip(revision),
                subdir=self._strip(subdir),
                local_path=self._strip(local_path),
                package_name=self._strip(package_name),
                dependency_name=self._strip(dependency_name),
                evidence_summary=self._strip(evidence_summary),
                setup_summary=self._strip(setup_summary),
                trusted_build=False,
                visible_modules=visible_modules or [],
            )
        except Exception as exc:  # noqa: BLE001 - normalized as ServiceResult.
            return self.foundation.fail(
                self.foundation.issue(
                    "adapter_upstream_invalid",
                    f"Adapter upstream metadata is invalid: {exc}",
                )
            )
        path = self._metadata_path(repo_root)
        self.foundation.store.ensure_dir(path.parent)
        written = self.foundation.store.write_json_atomic(path, metadata, mode=WriteMode.OVERWRITE)
        if not written.ok:
            return self.foundation.fail(written.issues)
        return self.foundation.ok(self._view(metadata))

    def get_adapter_upstream_metadata(self, repo_root: Path) -> ServiceResult[AdapterUpstreamView]:
        loaded = self._load_metadata(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        return self.foundation.ok(self._view(loaded.value))

    def get_adapter_upstream_status(self, repo_root: Path) -> ServiceResult[AdapterUpstreamStatusView]:
        loaded = self._load_metadata(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        metadata = loaded.value
        return self.foundation.ok(
            AdapterUpstreamStatusView(
                source_summary=self._source_summary(metadata),
                package_name=metadata.package_name or "",
                dependency_name=metadata.dependency_name or "",
                trusted_build=metadata.trusted_build,
                setup_summary=metadata.setup_summary,
                visible_module_count=len(metadata.visible_modules),
                summary=("Upstream build is trusted." if metadata.trusted_build else "Upstream build is not trusted yet."),
            )
        )

    def mark_upstream_build_trusted(self, repo_root: Path, *, summary: str) -> ServiceResult[AdapterUpstreamView]:
        if not summary or not summary.strip():
            return self.foundation.fail(
                self.foundation.issue(
                    "adapter_upstream_trust_summary_required",
                    "mark_upstream_build_trusted requires a non-empty summary.",
                    field="summary",
                )
            )
        loaded = self._load_metadata(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        metadata = loaded.value
        metadata.trusted_build = True
        metadata.setup_summary = summary.strip()
        written = self.foundation.store.write_json_atomic(
            self._metadata_path(repo_root),
            metadata,
            mode=WriteMode.UPDATE_EXISTING,
        )
        if not written.ok:
            return self.foundation.fail(written.issues)
        return self.foundation.ok(self._view(metadata))

    def record_visible_upstream_modules(
        self,
        repo_root: Path,
        *,
        modules: list[str],
        summary: str | None = None,
    ) -> ServiceResult[AdapterUpstreamView]:
        loaded = self._load_metadata(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        normalized: list[str] = []
        invalid: list[str] = []
        for module in modules:
            value = self._normalize_module(module)
            if value is None:
                invalid.append(module)
            else:
                normalized.append(value)
        if invalid:
            return self.foundation.fail(
                self.foundation.issue(
                    "adapter_visible_module_invalid",
                    "Visible upstream modules contain invalid Lean module names.",
                    field="modules",
                    current=", ".join(invalid),
                )
            )
        metadata = loaded.value
        metadata.visible_modules = sorted(set(normalized))
        if summary and summary.strip():
            metadata.setup_summary = summary.strip()
        written = self.foundation.store.write_json_atomic(
            self._metadata_path(repo_root),
            metadata,
            mode=WriteMode.UPDATE_EXISTING,
        )
        if not written.ok:
            return self.foundation.fail(written.issues)
        return self.foundation.ok(self._view(metadata))

    def list_visible_upstream_modules(self, repo_root: Path) -> ServiceResult[list[str]]:
        loaded = self._load_metadata(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(loaded.issues)
        if not loaded.value.visible_modules:
            return self.foundation.fail(
                self.foundation.issue(
                    "adapter_visible_modules_missing",
                    "Adapter upstream visible module set is missing.",
                    suggested_action="Record visible upstream modules from upstream navigation before projection checks.",
                )
            )
        return self.foundation.ok(list(loaded.value.visible_modules))

    def validate_upstream_metadata(self, repo_root: Path) -> ServiceResult[GateReport]:
        loaded = self._load_metadata(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.foundation.ok(
                self.foundation.gate_failed(
                    "adapter_upstream_metadata",
                    loaded.issues,
                    summary="Adapter upstream metadata is missing or invalid.",
                )
            )
        metadata = loaded.value
        issues = []
        if not metadata.trusted_build:
            issues.append(
                self.foundation.issue(
                    "adapter_upstream_not_trusted",
                    "Adapter upstream build has not been marked trusted.",
                    object_ref=metadata.dependency_name,
                )
            )
        if not metadata.setup_summary:
            issues.append(
                self.foundation.issue(
                    "adapter_upstream_setup_summary_missing",
                    "Adapter upstream setup summary is missing.",
                    object_ref=metadata.dependency_name,
                    field="setup_summary",
                )
            )
        if issues:
            return self.foundation.ok(
                self.foundation.gate_failed(
                    "adapter_upstream_metadata",
                    issues,
                    summary=f"{len(issues)} upstream metadata checks failed.",
                )
            )
        return self.foundation.ok(
            self.foundation.gate_passed(
                "adapter_upstream_metadata",
                summary=f"Adapter upstream metadata is trusted for dependency {metadata.dependency_name}.",
            )
        )

    def _metadata_path(self, repo_root: Path) -> Path:
        ctx = FoundationContext(repo_root=Path(repo_root))
        return self.foundation.layout.constellation_root(ctx) / "adapter_upstream.json"

    def _load_metadata(self, repo_root: Path) -> ServiceResult[AdapterUpstreamMetadata]:
        path = self._metadata_path(repo_root)
        loaded = self.foundation.store.read_json(path, AdapterUpstreamMetadata)
        if loaded.ok and loaded.value is not None:
            return loaded
        if loaded.issues and loaded.issues[0].kind == "missing_file":
            return self.foundation.fail(
                self.foundation.issue(
                    "adapter_upstream_missing",
                    "Adapter upstream metadata is missing.",
                    object_ref=str(path),
                )
            )
        original = loaded.issues[0] if loaded.issues else None
        return self.foundation.fail(
            self.foundation.issue(
                "adapter_upstream_invalid",
                "Adapter upstream metadata is invalid.",
                object_ref=str(path),
                details={
                    "source_issue": original.kind if original is not None else "unknown",
                    "source_message": original.message if original is not None else "unknown metadata load failure",
                },
            )
        )

    def _view(self, metadata: AdapterUpstreamMetadata) -> AdapterUpstreamView:
        source_summary = self._source_summary(metadata)
        return AdapterUpstreamView(
            source_kind=metadata.source_kind,
            git_url=metadata.git_url,
            revision=metadata.revision,
            subdir=metadata.subdir,
            package_name=metadata.package_name or "",
            dependency_name=metadata.dependency_name or "",
            evidence_summary=metadata.evidence_summary,
            setup_summary=metadata.setup_summary,
            trusted_build=metadata.trusted_build,
            visible_module_count=len(metadata.visible_modules),
            source_summary=source_summary,
            summary=f"Adapter upstream {metadata.dependency_name} from {source_summary}.",
        )

    def _source_summary(self, metadata: AdapterUpstreamMetadata) -> str:
        if metadata.source_kind == "git":
            parts = [metadata.git_url or "git upstream"]
            if metadata.revision:
                parts.append(f"rev={metadata.revision}")
            if metadata.subdir:
                parts.append(f"subdir={metadata.subdir}")
            return ", ".join(parts)
        return "local path upstream"

    def _normalize_module(self, module: str) -> str | None:
        value = module.strip()
        if not value or any(ch.isspace() for ch in value):
            return None
        if "/" in value or "\\" in value or ".." in value:
            return None
        if any(not part for part in value.split(".")):
            return None
        return value

    @staticmethod
    def _strip(value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None
