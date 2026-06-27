"""Resource curation support for resource-request flows."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.external_clients import AcquiredArtifactResult, ExternalClientService, ExtractedMaterialResult
from lean_constellation.services.foundation import FoundationContext, FoundationService, ServiceResult
from lean_constellation.services.material.resource_library import (
    ResourceDuplicateView,
    ResourceLibraryComponent,
    ResourceMetadataInput,
    ResourceTargetView,
    ResourceView,
)
from lean_constellation.services.material.source_corpus import SourceCorpusComponent


class ResourceArtifactView(StrictModel):
    ok: bool
    target: ResourceTargetView
    artifact_paths: list[str] = Field(default_factory=list)
    primary_artifact_path: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    content_hash: str | None = None
    summary: str
    issue_code: str | None = None


class ResourceExtractedMaterialView(StrictModel):
    ok: bool
    source_artifact_path: str
    extracted_paths: list[str] = Field(default_factory=list)
    primary_text_path: str | None = None
    preview: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    summary: str
    issue_code: str | None = None


class ResourceCurationFlowInputView(StrictModel):
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"]
    target: str
    arxiv_version: str | None = None
    normalized_target: ResourceTargetView
    caller_repo_root: str | None = None
    caller_node: str | None = None
    summary: str


class ResourceCurationDecisionView(StrictModel):
    decision: Literal["duplicate", "local_resource", "external_repo_required", "rejected"]
    target: ResourceTargetView
    duplicate_resource_key: str | None = None
    duplicate_source_paths: list[str] = Field(default_factory=list)
    reason: str
    suggested_next_action: str | None = None
    summary: str


class ResourceCurationResultView(StrictModel):
    kind: Literal["duplicate", "local_resource_created", "external_repo_required", "rejected"]
    target: ResourceTargetView
    resource_key: str | None = None
    duplicate_resource_key: str | None = None
    duplicate_source_paths: list[str] = Field(default_factory=list)
    reason: str | None = None
    summary: str


class ResourceCurationComponent:
    """Deterministic helper logic for ResourceCurationFlow."""

    def __init__(
        self,
        foundation: FoundationService,
        external: ExternalClientService,
        resource_library: ResourceLibraryComponent,
        source_corpus: SourceCorpusComponent,
    ) -> None:
        self.foundation = foundation
        self.external = external
        self.resource_library = resource_library
        self.source_corpus = source_corpus

    def build_resource_curation_flow_input(
        self,
        ctx: Any,
        *,
        target_kind: Literal["web", "arxiv", "local_file", "local_dir"],
        target: str,
        arxiv_version: str | None = None,
    ) -> ServiceResult[ResourceCurationFlowInputView]:
        if target_kind not in {"web", "arxiv", "local_file", "local_dir"}:
            return self.foundation.fail(self.foundation.issue("invalid_resource_target_kind", f"Unsupported resource target_kind: {target_kind}"))
        if not isinstance(target, str) or not target.strip():
            return self.foundation.fail(self.foundation.issue("missing_resource_target", "Resource target must be non-empty."))
        try:
            normalized_input = self._target_to_normalizable_string(target_kind, target, arxiv_version)
        except ValueError as exc:
            return self.foundation.fail(self.foundation.issue("invalid_resource_target_kind", str(exc)))
        normalized = self.resource_library.normalize_resource_target(normalized_input)
        if not normalized.ok or normalized.value is None:
            return self.foundation.fail(normalized.issues)
        return self.foundation.ok(
            ResourceCurationFlowInputView(
                target_kind=target_kind,
                target=target.strip(),
                arxiv_version=arxiv_version,
                normalized_target=normalized.value,
                caller_repo_root=self._ctx_value(ctx, "repo_root"),
                caller_node=self._ctx_value(ctx, "current_node") or self._ctx_value(ctx, "node_path"),
                summary=f"Prepared resource curation input for {target_kind} target.",
            )
        )

    def acquire_material_artifact(self, target: ResourceTargetView, *, temp_root: Path) -> ServiceResult[ResourceArtifactView]:
        temp_root = Path(temp_root)
        if target.kind == "arxiv":
            result = self.external.material.fetch_arxiv_source(target.target, target.version, output_root=temp_root)
        elif target.kind == "web_url":
            result = self.external.material.fetch_web_page(target.target, output_root=temp_root)
        elif target.kind == "local_dir":
            result = self.external.material.import_local_dir(Path(target.target), temp_root)
        elif target.kind == "local_file":
            result = self.external.material.import_local_file(source_path=Path(target.target), output_root=temp_root)
        else:
            return self.foundation.fail(self.foundation.issue("invalid_resource_target_kind", f"Unsupported normalized target kind: {target.kind}"))
        view = self._artifact_view(target, result)
        if not result.ok:
            return self.foundation.fail(self.foundation.issue(result.issue_code or "resource_acquisition_failed", result.summary or "Resource acquisition failed."))
        return self.foundation.ok(view)

    def extract_readable_material(
        self,
        artifact: ResourceArtifactView,
        *,
        temp_root: Path,
    ) -> ServiceResult[ResourceExtractedMaterialView]:
        if not artifact.primary_artifact_path:
            return self.foundation.fail(self.foundation.issue("resource_artifact_missing", "Acquired artifact has no primary artifact path."))
        source = Path(artifact.primary_artifact_path)
        if artifact.target.kind == "arxiv":
            result = self.external.material.extract_arxiv_tex(source_root_or_archive=source, output_root=Path(temp_root))
        elif source.suffix.lower() == ".pdf":
            result = self.external.material.extract_pdf_text(pdf_path=source, output_root=Path(temp_root))
        elif source.suffix.lower() in {".html", ".htm"}:
            result = self.external.material.extract_web_main_text(html_path=source, output_root=Path(temp_root))
        elif source.is_dir():
            result = self.external.material.extract_arxiv_tex(source_root_or_archive=source, output_root=Path(temp_root))
        else:
            result = self.external.material.normalize_text_material(input_path=source, output_root=Path(temp_root))
        view = self._extracted_view(result)
        if not result.ok:
            return self.foundation.fail(self.foundation.issue(result.issue_code or "resource_extraction_failed", result.summary or "Resource extraction failed."))
        return self.foundation.ok(view)

    def decide_local_or_external(
        self,
        ctx: Any,
        *,
        target: ResourceTargetView,
        duplicate: ResourceDuplicateView | None,
    ) -> ServiceResult[ResourceCurationDecisionView]:
        if target.kind not in {"arxiv", "web_url", "local_file", "local_dir"}:
            return self.foundation.ok(
                ResourceCurationDecisionView(
                    decision="rejected",
                    target=target,
                    reason=f"Unsupported normalized target kind: {target.kind}.",
                    suggested_next_action="Use a supported target kind: arxiv, web_url, local_file, or local_dir.",
                    summary="Resource target was rejected.",
                )
            )
        if duplicate and duplicate.duplicate:
            return self.foundation.ok(
                ResourceCurationDecisionView(
                    decision="duplicate",
                    target=target,
                    duplicate_resource_key=duplicate.resource_key,
                    reason="The resource library already has this target.",
                    summary=duplicate.summary,
                )
            )
        source_duplicate = self._source_duplicate(ctx, target)
        if source_duplicate.ok and source_duplicate.value and source_duplicate.value.duplicate:
            return self.foundation.ok(
                ResourceCurationDecisionView(
                    decision="duplicate",
                    target=target,
                    duplicate_source_paths=source_duplicate.value.matching_paths,
                    reason="The source corpus already contains this target.",
                    summary=source_duplicate.value.summary,
                )
            )
        if target.kind == "local_dir":
            return self.foundation.ok(
                ResourceCurationDecisionView(
                    decision="external_repo_required",
                    target=target,
                    reason="Directory-shaped resource targets are too large for the local resource library.",
                    suggested_next_action="Create or request a dedicated provider repo if this directory is required.",
                    summary="Resource target should be handled as an external repo.",
                )
            )
        if target.kind == "arxiv" and self._ctx_value(ctx, "prefer_external_repo") == "true":
            return self.foundation.ok(
                ResourceCurationDecisionView(
                    decision="external_repo_required",
                    target=target,
                    reason="Caller requested external repo handling for this arXiv target.",
                    suggested_next_action="Create a provider repo for this arXiv source if needed.",
                    summary="Resource target is delegated to external repo preparation.",
                )
            )
        return self.foundation.ok(
            ResourceCurationDecisionView(
                decision="local_resource",
                target=target,
                reason="Target can be normalized into the local resource library.",
                summary="Resource target should be curated locally.",
            )
        )

    def build_curator_result(
        self,
        decision: ResourceCurationDecisionView,
        *,
        resource: ResourceView | None = None,
    ) -> ServiceResult[ResourceCurationResultView]:
        if decision.decision == "duplicate":
            return self.foundation.ok(
                ResourceCurationResultView(
                    kind="duplicate",
                    target=decision.target,
                    duplicate_resource_key=decision.duplicate_resource_key,
                    duplicate_source_paths=decision.duplicate_source_paths,
                    reason=decision.reason,
                    summary=decision.summary,
                )
            )
        if decision.decision == "external_repo_required":
            return self.foundation.ok(
                ResourceCurationResultView(
                    kind="external_repo_required",
                    target=decision.target,
                    reason=decision.reason,
                    summary=decision.summary,
                )
            )
        if decision.decision == "rejected":
            return self.foundation.ok(
                ResourceCurationResultView(
                    kind="rejected",
                    target=decision.target,
                    reason=decision.reason,
                    summary=decision.summary,
                )
            )
        if resource is None:
            return self.foundation.fail(
                self.foundation.issue("resource_required", "Local resource result requires a registered resource.")
            )
        return self.foundation.ok(
            ResourceCurationResultView(
                kind="local_resource_created",
                target=decision.target,
                resource_key=resource.resource.resource_key,
                reason=decision.reason,
                summary=f"Created local resource {resource.resource.resource_key}.",
            )
        )

    def curate_local_resource(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
        temp_root: Path,
        metadata: ResourceMetadataInput | None = None,
    ) -> ServiceResult[ResourceView]:
        artifact = self.acquire_material_artifact(target, temp_root=temp_root)
        if not artifact.ok or artifact.value is None:
            return self.foundation.fail(artifact.issues)
        extracted = self.extract_readable_material(artifact.value, temp_root=temp_root)
        if not extracted.ok:
            return self.foundation.fail(extracted.issues)
        return self.resource_library.register_local_resource(
            repo_root,
            target=target,
            temp_dir=Path(temp_root),
            metadata=metadata or ResourceMetadataInput(title=target.target),
        )

    def _source_duplicate(self, ctx: Any, target: ResourceTargetView):
        repo_root = self._ctx_value(ctx, "repo_root")
        if not repo_root:
            return self.foundation.ok(None)
        return self.source_corpus.check_target_in_source_corpus(Path(repo_root), canonical_locator=target.canonical_locator)

    def _artifact_view(self, target: ResourceTargetView, result: AcquiredArtifactResult) -> ResourceArtifactView:
        return ResourceArtifactView(
            ok=result.ok,
            target=target,
            artifact_paths=result.artifact_paths,
            primary_artifact_path=result.primary_artifact_path,
            metadata=result.metadata,
            content_hash=result.content_hash,
            summary=result.summary or "",
            issue_code=result.issue_code,
        )

    @staticmethod
    def _extracted_view(result: ExtractedMaterialResult) -> ResourceExtractedMaterialView:
        return ResourceExtractedMaterialView(
            ok=result.ok,
            source_artifact_path=result.source_artifact_path,
            extracted_paths=result.extracted_paths,
            primary_text_path=result.primary_text_path,
            preview=result.text_preview,
            metadata=result.metadata,
            summary=result.summary or "",
            issue_code=result.issue_code,
        )

    @staticmethod
    def _target_to_normalizable_string(target_kind: str, target: str, arxiv_version: str | None) -> str:
        target = target.strip()
        if target_kind == "web":
            return target
        if target_kind == "arxiv":
            version = arxiv_version or ""
            return f"{target}{version}"
        if target_kind in {"local_file", "local_dir"}:
            return target
        raise ValueError(f"unsupported target_kind: {target_kind}")

    @staticmethod
    def _ctx_value(ctx: Any, name: str) -> str | None:
        if ctx is None:
            return None
        if isinstance(ctx, dict):
            value = ctx.get(name)
        else:
            value = getattr(ctx, name, None)
        if value is None:
            return None
        if isinstance(value, Path):
            return str(value)
        return str(value)
