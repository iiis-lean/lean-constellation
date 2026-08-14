"""Resource curation support for resource-request flows."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.external_clients import AcquiredArtifactResult, ExtractedMaterialResult
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.material.resource_library import (
    ResourceDuplicateView,
    ResourceLibraryComponent,
    ResourceMetadataInput,
    ResourceTargetView,
    ResourceView,
)
from lean_constellation.services.material.source_corpus import SourceAcquisitionView, SourceCorpusComponent, SourceExtractionView

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class ResourceArtifactView(StrictModel):
    ok: bool
    target: ResourceTargetView
    artifact_paths: list[str] = Field(default_factory=list)
    primary_artifact_path: str | None = None
    acquisition_kind: str | None = None
    mime_type: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    content_hash: str | None = None
    summary: str
    issue_code: str | None = None


class ResourceExtractedMaterialView(StrictModel):
    ok: bool
    source_artifact_path: str
    extracted_paths: list[str] = Field(default_factory=list)
    primary_text_path: str | None = None
    resolved_artifact_kind: Literal[
        "pdf",
        "html",
        "tex_source_archive",
        "plain_text",
        "directory",
        "unknown_binary",
    ]
    extraction_kind: Literal["pdf_text", "html_main_text", "tex_source", "text_normalize"]
    preview: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    summary: str
    issue_code: str | None = None


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
    suggested_repo_name: str | None = None
    source_description: str | None = None
    required_interfaces_hint: str | None = None
    canonical_entry: str | None = None
    classification_reason: str | None = None
    resource_role: str | None = None
    consumer_formalization_scope: str | None = None
    relation_to_current_repo_or_node: str | None = None
    consumer_need: str | None = None
    provider_scope: str | None = None
    existing_lean_repo_signal: str | None = None
    reason: str | None = None
    summary: str

    @model_validator(mode="after")
    def _validate_boundary_fields(self):
        if self.kind == "local_resource_created":
            required = {
                "canonical_entry": self.canonical_entry,
                "classification_reason": self.classification_reason,
                "resource_role": self.resource_role,
                "consumer_formalization_scope": self.consumer_formalization_scope,
            }
        elif self.kind == "external_repo_required":
            required = {
                "classification_reason": self.classification_reason,
                "relation_to_current_repo_or_node": self.relation_to_current_repo_or_node,
                "consumer_need": self.consumer_need,
                "provider_scope": self.provider_scope,
            }
        else:
            required = {}
        missing = [field for field, value in required.items() if not value or not value.strip()]
        if missing:
            raise ValueError(f"{self.kind} requires boundary fields: {', '.join(missing)}")
        return self


class ResourceCurationComponent:
    """Deterministic helper logic for ResourceCurationFlow."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        resource_library: ResourceLibraryComponent,
        source_corpus: SourceCorpusComponent,
    ) -> None:
        self.runtime = runtime
        self.resource_library = resource_library
        self.source_corpus = source_corpus

    def prepare_resource_target(
        self,
        *,
        target_kind: Literal["web", "arxiv", "local_file", "local_dir"],
        target: str,
        arxiv_version: str | None = None,
    ) -> ServiceResult[ResourceTargetView]:
        if target_kind not in {"web", "arxiv", "local_file", "local_dir"}:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_resource_target_kind", f"Unsupported resource target_kind: {target_kind}"))
        if not isinstance(target, str) or not target.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("missing_resource_target", "Resource target must be non-empty."))
        try:
            normalized_input = self._target_to_normalizable_string(target_kind, target, arxiv_version)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_resource_target_kind", str(exc)))
        normalized = self.resource_library.normalize_resource_target(normalized_input)
        if not normalized.ok or normalized.value is None:
            return self.runtime.foundation.fail(normalized.issues)
        return self.runtime.foundation.ok(normalized.value)

    def acquire_material_artifact(self, target: ResourceTargetView, *, temp_root: Path) -> ServiceResult[ResourceArtifactView]:
        temp_root = Path(temp_root)
        if target.kind == "arxiv":
            result = self.runtime.external.material.fetch_arxiv_source(target.target, target.version, output_root=temp_root)
        elif target.kind == "web_url":
            result = self.runtime.external.material.fetch_web_page(target.target, output_root=temp_root)
        elif target.kind == "local_dir":
            result = self.runtime.external.material.import_local_dir(Path(target.target), temp_root)
        elif target.kind == "local_file":
            result = self.runtime.external.material.import_local_file(source_path=Path(target.target), output_root=temp_root)
        else:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_resource_target_kind", f"Unsupported normalized target kind: {target.kind}"))
        view = self._artifact_view(target, result)
        if not result.ok:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(result.issue_code or "resource_acquisition_failed", result.summary or "Resource acquisition failed."))
        return self.runtime.foundation.ok(view)

    def extract_readable_material(
        self,
        artifact: ResourceArtifactView,
        *,
        temp_root: Path,
    ) -> ServiceResult[ResourceExtractedMaterialView]:
        if not artifact.primary_artifact_path:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("resource_artifact_missing", "Acquired artifact has no primary artifact path."))
        source = Path(artifact.primary_artifact_path)
        acquisition_kind = artifact.acquisition_kind or ("arxiv_source" if artifact.target.kind == "arxiv" else None)
        resolution = self.runtime.external.material.resolve_artifact_kind(
            source,
            acquisition_kind=acquisition_kind,
            mime_type=artifact.mime_type,
        )
        if not resolution.compatible or resolution.extraction_kind is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    resolution.issue_code or "resource_artifact_kind_unknown",
                    resolution.summary,
                    object_ref=str(source),
                    details={"evidence": resolution.evidence},
                )
            )
        if resolution.extraction_kind == "pdf_text":
            result = self.runtime.external.material.extract_pdf_text(pdf_path=source, output_root=Path(temp_root))
        elif resolution.extraction_kind == "html_main_text":
            result = self.runtime.external.material.extract_web_main_text(
                html_path=source,
                output_root=Path(temp_root),
                acquisition_kind=acquisition_kind,
                mime_type=artifact.mime_type,
            )
        elif resolution.extraction_kind == "tex_source":
            result = self.runtime.external.material.extract_arxiv_tex(source_root_or_archive=source, output_root=Path(temp_root))
        else:
            result = self.runtime.external.material.normalize_text_material(input_path=source, output_root=Path(temp_root))
        view = self._extracted_view(
            result,
            resolved_artifact_kind=resolution.kind,
            extraction_kind=resolution.extraction_kind,
        )
        if not result.ok:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(result.issue_code or "resource_extraction_failed", result.summary or "Resource extraction failed."))
        return self.runtime.foundation.ok(view)

    def decide_local_or_external(
        self,
        *,
        target: ResourceTargetView,
        duplicate: ResourceDuplicateView | None,
        repo_root: Path | None = None,
        prefer_external_repo: bool = False,
    ) -> ServiceResult[ResourceCurationDecisionView]:
        if target.kind not in {"arxiv", "web_url", "local_file", "local_dir"}:
            return self.runtime.foundation.ok(
                ResourceCurationDecisionView(
                    decision="rejected",
                    target=target,
                    reason=f"Unsupported normalized target kind: {target.kind}.",
                    suggested_next_action="Use a supported target kind: arxiv, web_url, local_file, or local_dir.",
                    summary="Resource target was rejected.",
                )
            )
        if duplicate and duplicate.duplicate:
            return self.runtime.foundation.ok(
                ResourceCurationDecisionView(
                    decision="duplicate",
                    target=target,
                    duplicate_resource_key=duplicate.resource_key,
                    reason="The resource library already has this target.",
                    summary=duplicate.summary,
                )
            )
        source_duplicate = (
            self.source_corpus.check_target_in_source_corpus(repo_root, canonical_locator=target.canonical_locator)
            if repo_root is not None
            else self.runtime.foundation.ok(None)
        )
        if source_duplicate.ok and source_duplicate.value and source_duplicate.value.duplicate:
            return self.runtime.foundation.ok(
                ResourceCurationDecisionView(
                    decision="duplicate",
                    target=target,
                    duplicate_source_paths=source_duplicate.value.matching_paths,
                    reason="The source corpus already contains this target.",
                    summary=source_duplicate.value.summary,
                )
            )
        if target.kind == "local_dir":
            return self.runtime.foundation.ok(
                ResourceCurationDecisionView(
                    decision="external_repo_required",
                    target=target,
                    reason="Directory-shaped resource targets are too large for the local resource library.",
                    suggested_next_action="Create or request a dedicated provider repo if this directory is required.",
                    summary="Resource target should be handled as an external repo.",
                )
            )
        if target.kind == "arxiv" and prefer_external_repo:
            return self.runtime.foundation.ok(
                ResourceCurationDecisionView(
                    decision="external_repo_required",
                    target=target,
                    reason="Caller requested external repo handling for this arXiv target.",
                    suggested_next_action="Create a provider repo for this arXiv source if needed.",
                    summary="Resource target is delegated to external repo preparation.",
                )
            )
        return self.runtime.foundation.ok(
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
        classification_reason: str | None = None,
        resource_role: str | None = None,
        consumer_formalization_scope: str | None = None,
        relation_to_current_repo_or_node: str | None = None,
        consumer_need: str | None = None,
        provider_scope: str | None = None,
        existing_lean_repo_signal: str | None = None,
    ) -> ServiceResult[ResourceCurationResultView]:
        if decision.decision == "duplicate":
            return self.runtime.foundation.ok(
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
            boundary_fields = self._required_external_boundary_fields(
                classification_reason=classification_reason or "",
                relation_to_current_repo_or_node=relation_to_current_repo_or_node or "",
                consumer_need=consumer_need or "",
                provider_scope=provider_scope or "",
            )
            if not boundary_fields.ok or boundary_fields.value is None:
                return self.runtime.foundation.fail(boundary_fields.issues)
            return self.runtime.foundation.ok(
                ResourceCurationResultView(
                    kind="external_repo_required",
                    target=decision.target,
                    classification_reason=boundary_fields.value[0],
                    relation_to_current_repo_or_node=boundary_fields.value[1],
                    consumer_need=boundary_fields.value[2],
                    provider_scope=boundary_fields.value[3],
                    existing_lean_repo_signal=existing_lean_repo_signal,
                    reason=decision.reason,
                    summary=decision.summary,
                )
            )
        if decision.decision == "rejected":
            return self.runtime.foundation.ok(
                ResourceCurationResultView(
                    kind="rejected",
                    target=decision.target,
                    reason=decision.reason,
                    summary=decision.summary,
                )
            )
        if resource is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("resource_required", "Local resource result requires a registered resource.")
            )
        boundary_fields = self._required_boundary_fields(
            classification_reason=classification_reason or "",
            resource_role=resource_role or "",
            consumer_formalization_scope=consumer_formalization_scope or "",
        )
        if not boundary_fields.ok or boundary_fields.value is None:
            return self.runtime.foundation.fail(boundary_fields.issues)
        return self.runtime.foundation.ok(
            ResourceCurationResultView(
                kind="local_resource_created",
                target=decision.target,
                resource_key=resource.resource.resource_key,
                canonical_entry=resource.resource.canonical_entry,
                classification_reason=boundary_fields.value[0],
                resource_role=boundary_fields.value[1],
                consumer_formalization_scope=boundary_fields.value[2],
                reason=decision.reason,
                summary=f"Created local resource {resource.resource.resource_key}.",
            )
        )

    def submit_resource_duplicate(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
        existing_kind: Literal["resource", "source"],
        duplicate_reason: str,
        existing_resource_key: str | None = None,
        existing_source_path: str | None = None,
        preview: str | None = None,
    ) -> ServiceResult[ResourceCurationResultView]:
        reason = self._required_text(duplicate_reason, field="duplicate_reason", issue_kind="resource_duplicate_reason_required")
        if not reason.ok or reason.value is None:
            return self.runtime.foundation.fail(reason.issues)
        if existing_kind == "resource":
            if not existing_resource_key or not existing_resource_key.strip():
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("resource_duplicate_key_required", "existing_resource_key is required for resource duplicate.")
                )
            existing = self.resource_library.get_resource(repo_root, resource_key=existing_resource_key)
            if not existing.ok or existing.value is None:
                return self.runtime.foundation.fail(existing.issues)
            return self.runtime.foundation.ok(
                ResourceCurationResultView(
                    kind="duplicate",
                    target=target,
                    duplicate_resource_key=existing.value.resource.resource_key,
                    reason=reason.value,
                    summary=preview.strip() if preview and preview.strip() else f"Target duplicates existing resource {existing.value.resource.resource_key}.",
                )
            )
        if existing_kind == "source":
            if not existing_source_path or not existing_source_path.strip():
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("source_duplicate_path_required", "existing_source_path is required for source duplicate.")
                )
            manifest = self.source_corpus.get_source_corpus_manifest(repo_root)
            if not manifest.ok or manifest.value is None:
                return self.runtime.foundation.fail(manifest.issues)
            source_paths = {item.path for item in manifest.value.files}
            if existing_source_path not in source_paths:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "source_duplicate_path_not_found",
                        f"Source duplicate path is not in the source corpus: {existing_source_path}",
                        object_ref=existing_source_path,
                    )
                )
            return self.runtime.foundation.ok(
                ResourceCurationResultView(
                    kind="duplicate",
                    target=target,
                    duplicate_source_paths=[existing_source_path],
                    reason=reason.value,
                    summary=preview.strip() if preview and preview.strip() else f"Target duplicates source corpus material {existing_source_path}.",
                )
            )
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue("invalid_resource_duplicate_kind", f"Unsupported existing_kind: {existing_kind}")
        )

    def submit_local_resource_created(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
        draft_id: str,
        summary: str,
        classification_reason: str,
        resource_role: str,
        consumer_formalization_scope: str,
    ) -> ServiceResult[ResourceCurationResultView]:
        checked = self.check_local_resource_created(
            repo_root,
            target=target,
            draft_id=draft_id,
            summary=summary,
            classification_reason=classification_reason,
            resource_role=resource_role,
            consumer_formalization_scope=consumer_formalization_scope,
        )
        if not checked.ok or checked.value is None:
            return self.runtime.foundation.fail(checked.issues)
        finalized = self.resource_library.finalize_resource_draft(repo_root, draft_id=draft_id, summary=summary)
        if not finalized.ok or finalized.value is None:
            return self.runtime.foundation.fail(finalized.issues)
        return self.runtime.foundation.ok(
            ResourceCurationResultView(
                kind="local_resource_created",
                target=target,
                resource_key=finalized.value.resource.resource_key,
                canonical_entry=finalized.value.resource.canonical_entry,
                classification_reason=classification_reason.strip(),
                resource_role=resource_role.strip(),
                consumer_formalization_scope=consumer_formalization_scope.strip(),
                reason="Target was curated into the local resource library.",
                summary=summary.strip(),
            )
        )

    def check_local_resource_created(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
        draft_id: str,
        summary: str,
        classification_reason: str,
        resource_role: str,
        consumer_formalization_scope: str,
    ) -> ServiceResult[ResourceCurationResultView]:
        text = self._required_text(summary, field="summary", issue_kind="resource_local_summary_required")
        if not text.ok or text.value is None:
            return self.runtime.foundation.fail(text.issues)
        boundary_fields = self._required_boundary_fields(
            classification_reason=classification_reason,
            resource_role=resource_role,
            consumer_formalization_scope=consumer_formalization_scope,
        )
        if not boundary_fields.ok or boundary_fields.value is None:
            return self.runtime.foundation.fail(boundary_fields.issues)
        draft = self.resource_library.get_resource_draft(repo_root, draft_id=draft_id)
        if not draft.ok or draft.value is None:
            return self.runtime.foundation.fail(draft.issues)
        if draft.value.draft.target.canonical_locator != target.canonical_locator:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_request_target_mismatch",
                    "Resource draft target does not match the current resource curation request.",
                    object_ref=draft_id,
                    details={
                        "draft_target": draft.value.draft.target.canonical_locator,
                        "request_target": target.canonical_locator,
                    },
                )
            )
        gate = self.resource_library.check_resource_draft(repo_root, draft_id=draft_id, update_status=False)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        if not gate.value.passed:
            return self.runtime.foundation.fail(gate.value.issues)
        duplicate = self.resource_library.find_duplicate_resource(repo_root, target=draft.value.draft.target)
        if not duplicate.ok or duplicate.value is None:
            return self.runtime.foundation.fail(duplicate.issues)
        if duplicate.value.duplicate:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("resource_duplicate", duplicate.value.summary, object_ref=duplicate.value.resource_key)
            )
        resource_key = self.resource_library.resource_key_for_target(draft.value.draft.target)
        if not resource_key.ok or resource_key.value is None:
            return self.runtime.foundation.fail(resource_key.issues)
        canonical_entry = self.resource_library.get_resource_draft_canonical_entry(
            repo_root,
            draft_id=draft_id,
        )
        if not canonical_entry.ok or canonical_entry.value is None:
            return self.runtime.foundation.fail(canonical_entry.issues)
        return self.runtime.foundation.ok(
            ResourceCurationResultView(
                kind="local_resource_created",
                target=target,
                resource_key=resource_key.value,
                canonical_entry=canonical_entry.value,
                classification_reason=boundary_fields.value[0],
                resource_role=boundary_fields.value[1],
                consumer_formalization_scope=boundary_fields.value[2],
                reason="Target was curated into the local resource library.",
                summary=text.value,
            )
        )

    def submit_external_repo_required(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
        reason: str,
        source_description: str,
        classification_reason: str,
        relation_to_current_repo_or_node: str,
        consumer_need: str,
        provider_scope: str,
        suggested_repo_name: str | None = None,
        required_interfaces_hint: str | None = None,
        existing_lean_repo_signal: str | None = None,
    ) -> ServiceResult[ResourceCurationResultView]:
        del repo_root
        reason_text = self._required_text(reason, field="reason", issue_kind="resource_external_reason_required")
        if not reason_text.ok or reason_text.value is None:
            return self.runtime.foundation.fail(reason_text.issues)
        source_text = self._required_text(
            source_description,
            field="source_description",
            issue_kind="resource_external_source_description_required",
        )
        if not source_text.ok or source_text.value is None:
            return self.runtime.foundation.fail(source_text.issues)
        boundary_fields = self._required_external_boundary_fields(
            classification_reason=classification_reason,
            relation_to_current_repo_or_node=relation_to_current_repo_or_node,
            consumer_need=consumer_need,
            provider_scope=provider_scope,
        )
        if not boundary_fields.ok or boundary_fields.value is None:
            return self.runtime.foundation.fail(boundary_fields.issues)
        repo_hint = suggested_repo_name.strip() if suggested_repo_name and suggested_repo_name.strip() else None
        if repo_hint is not None:
            try:
                repo_hint = self.runtime.foundation.layout.ensure_safe_key(repo_hint)
            except ValueError as exc:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("invalid_suggested_repo_name", str(exc), field="suggested_repo_name")
                )
        return self.runtime.foundation.ok(
            ResourceCurationResultView(
                kind="external_repo_required",
                target=target,
                suggested_repo_name=repo_hint,
                source_description=source_text.value,
                required_interfaces_hint=required_interfaces_hint.strip() if required_interfaces_hint and required_interfaces_hint.strip() else None,
                classification_reason=boundary_fields.value[0],
                relation_to_current_repo_or_node=boundary_fields.value[1],
                consumer_need=boundary_fields.value[2],
                provider_scope=boundary_fields.value[3],
                existing_lean_repo_signal=(
                    existing_lean_repo_signal.strip()
                    if existing_lean_repo_signal and existing_lean_repo_signal.strip()
                    else None
                ),
                reason=reason_text.value,
                summary=f"Resource target requires an external provider repo: {reason_text.value}",
            )
        )

    def submit_resource_rejected(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
        reason: str,
    ) -> ServiceResult[ResourceCurationResultView]:
        del repo_root
        reason_text = self._required_text(reason, field="reason", issue_kind="resource_rejected_reason_required")
        if not reason_text.ok or reason_text.value is None:
            return self.runtime.foundation.fail(reason_text.issues)
        return self.runtime.foundation.ok(
            ResourceCurationResultView(
                kind="rejected",
                target=target,
                reason=reason_text.value,
                summary=f"Resource target rejected: {reason_text.value}",
            )
        )

    def acquire_resource_material(
        self,
        repo_root: Path,
        *,
        draft_id: str,
        target: str,
        preferred_kind: Literal["arxiv_source", "arxiv_pdf", "web_page", "local_file", "local_dir"] | None = None,
    ) -> ServiceResult[SourceAcquisitionView]:
        draft_root = self._active_draft_root(repo_root, draft_id)
        if not draft_root.ok or draft_root.value is None:
            return self.runtime.foundation.fail(draft_root.issues)
        root = draft_root.value
        acquisition_root = root / "_work"
        ensured = self.runtime.foundation.store.ensure_dir(acquisition_root)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        try:
            normalized = self.runtime.external.material.normalize_target(target)
        except Exception as exc:  # noqa: BLE001
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_material_target", str(exc)))
        mismatch = self._preferred_kind_mismatch(preferred_kind, normalized.kind)
        if mismatch is not None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "preferred_kind_target_mismatch",
                    f"preferred_kind={preferred_kind} cannot be used with target kind {normalized.kind}.",
                    details={"preferred_kind": str(preferred_kind), "target_kind": normalized.kind},
                )
            )

        if preferred_kind == "arxiv_source" or (preferred_kind is None and normalized.kind == "arxiv"):
            result = self.runtime.external.material.fetch_arxiv_source(normalized.value, normalized.version, output_root=acquisition_root)
        elif preferred_kind == "arxiv_pdf":
            result = self.runtime.external.material.fetch_arxiv_pdf(normalized.value, normalized.version, output_root=acquisition_root)
        elif preferred_kind == "web_page" or (preferred_kind is None and normalized.kind == "web_url"):
            result = self.runtime.external.material.fetch_web_page(normalized.value, output_root=acquisition_root)
        elif preferred_kind == "local_dir" or normalized.kind == "local_dir":
            result = self.runtime.external.material.import_local_dir(Path(normalized.value), acquisition_root)
        else:
            result = self.runtime.external.material.import_local_file(source_path=Path(normalized.value), output_root=acquisition_root)
        view = self._source_acquisition_view(target, result, root)
        if not result.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(result.issue_code or "resource_acquisition_failed", result.summary or "Resource acquisition failed")
            )
        return self.runtime.foundation.ok(view)

    def import_resource_material(
        self,
        repo_root: Path,
        *,
        draft_id: str,
        source_path: str,
        as_name: str | None = None,
    ) -> ServiceResult[SourceAcquisitionView]:
        draft_root = self._active_draft_root(repo_root, draft_id)
        if not draft_root.ok or draft_root.value is None:
            return self.runtime.foundation.fail(draft_root.issues)
        root = draft_root.value
        source = Path(source_path).expanduser().resolve(strict=False)
        if not source.exists() or not source.is_file():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("missing_local_file", f"Local resource material not found: {source}")
            )
        try:
            dest_name = self.runtime.foundation.layout.ensure_safe_key(as_name) if as_name else self._safe_material_filename(source.name)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("unsafe_resource_filename", str(exc)))
        dest = root / "_work" / "original" / dest_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copy2(source, dest)
        result = AcquiredArtifactResult(
            ok=True,
            target=self.runtime.external.material.normalize_target(str(source)),
            artifact_paths=[str(dest)],
            primary_artifact_path=str(dest),
            metadata={"source_path": str(source)},
            content_hash=self._hash_file(dest),
            summary="Imported local resource material.",
        )
        return self.runtime.foundation.ok(self._source_acquisition_view(str(source), result, root))

    def extract_resource_artifact(
        self,
        repo_root: Path,
        *,
        draft_id: str,
        artifact_ref: str,
        extraction_kind: Literal["pdf_text", "html_main_text", "tex_source", "text_normalize"] | None = None,
        acquisition_kind: str | None = None,
        mime_type: str | None = None,
    ) -> ServiceResult[SourceExtractionView]:
        draft_root = self._active_draft_root(repo_root, draft_id)
        if not draft_root.ok or draft_root.value is None:
            return self.runtime.foundation.fail(draft_root.issues)
        root = draft_root.value
        try:
            artifact = self._resolve_inside(root, artifact_ref)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("resource_artifact_ref_invalid", str(exc)))
        work_root = root / "_work"
        ensured = self.runtime.foundation.store.ensure_dir(work_root)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        resolution = self.runtime.external.material.resolve_artifact_kind(
            artifact,
            acquisition_kind=acquisition_kind,
            mime_type=mime_type,
            requested_extraction_kind=extraction_kind,
        )
        if not resolution.compatible or resolution.extraction_kind is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    resolution.issue_code or "resource_artifact_kind_unknown",
                    resolution.summary,
                    object_ref=artifact_ref,
                    details={"evidence": resolution.evidence},
                )
            )
        extraction_kind = resolution.extraction_kind
        if extraction_kind == "pdf_text":
            result = self.runtime.external.material.extract_pdf_text(pdf_path=artifact, output_root=work_root)
        elif extraction_kind == "html_main_text":
            result = self.runtime.external.material.extract_web_main_text(
                html_path=artifact,
                output_root=work_root,
                acquisition_kind=acquisition_kind,
                mime_type=mime_type,
            )
        elif extraction_kind == "tex_source":
            result = self.runtime.external.material.extract_arxiv_tex(source_root_or_archive=artifact, output_root=work_root)
        else:
            result = self.runtime.external.material.normalize_text_material(input_path=artifact, output_root=work_root)
        if not result.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(result.issue_code or "resource_extraction_failed", result.summary or "Resource extraction failed")
            )
        if result.primary_text_path is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_extracted_entry_missing",
                    "Resource extraction did not return a canonical readable entry.",
                    object_ref=artifact_ref,
                )
            )
        view = self._source_extraction_view(
            artifact_ref,
            result,
            root,
            resolved_artifact_kind=resolution.kind,
            extraction_kind=extraction_kind,
        )
        return self.runtime.foundation.ok(view)

    def normalize_resource_text_material(self, repo_root: Path, *, draft_id: str, material_ref: str) -> ServiceResult[SourceExtractionView]:
        draft_root = self._active_draft_root(repo_root, draft_id)
        if not draft_root.ok or draft_root.value is None:
            return self.runtime.foundation.fail(draft_root.issues)
        root = draft_root.value
        try:
            source = self._resolve_inside(root, material_ref)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("resource_material_ref_invalid", str(exc)))
        work_root = root / "_work"
        ensured = self.runtime.foundation.store.ensure_dir(work_root)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        result = self.runtime.external.material.normalize_text_material(input_path=source, output_root=work_root)
        if not result.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(result.issue_code or "resource_text_normalization_failed", result.summary or "Resource text normalization failed")
            )
        if result.primary_text_path is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_extracted_entry_missing",
                    "Resource normalization did not return a canonical readable entry.",
                    object_ref=material_ref,
                )
            )
        view = self._source_extraction_view(
            material_ref,
            result,
            root,
            resolved_artifact_kind="plain_text",
            extraction_kind="text_normalize",
        )
        return self.runtime.foundation.ok(view)

    def curate_local_resource(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
        temp_root: Path,
        metadata: ResourceMetadataInput | None = None,
    ) -> ServiceResult[ResourceView]:
        candidate_root = Path(temp_root)
        work_root = candidate_root / "_work"
        artifact = self.acquire_material_artifact(target, temp_root=work_root)
        if not artifact.ok or artifact.value is None:
            return self.runtime.foundation.fail(artifact.issues)
        extracted = self.extract_readable_material(artifact.value, temp_root=work_root)
        if not extracted.ok or extracted.value is None or extracted.value.primary_text_path is None:
            return self.runtime.foundation.fail(extracted.issues)
        extracted_entry = Path(extracted.value.primary_text_path)
        article_root = candidate_root / "article"
        article_root.mkdir(parents=True, exist_ok=True)
        canonical_name = extracted_entry.name or "main.md"
        if Path(canonical_name).suffix.lower() not in {".md", ".txt", ".tex", ".lean"}:
            canonical_name = f"{canonical_name}.txt"
        shutil.copy2(extracted_entry, article_root / canonical_name)
        return self.resource_library.register_local_resource(
            repo_root,
            target=target,
            temp_dir=candidate_root,
            metadata=metadata or ResourceMetadataInput(title=target.target),
        )

    def _active_draft_root(self, repo_root: Path, draft_id: str) -> ServiceResult[Path]:
        draft = self.resource_library.get_resource_draft(repo_root, draft_id=draft_id)
        if not draft.ok or draft.value is None:
            return self.runtime.foundation.fail(draft.issues)
        root = Path(draft.value.draft_root)
        try:
            self.runtime.foundation.layout.assert_within(Path(repo_root), root)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("resource_draft_path_escape", str(exc), object_ref=draft_id))
        return self.runtime.foundation.ok(root)

    def _artifact_view(self, target: ResourceTargetView, result: AcquiredArtifactResult) -> ResourceArtifactView:
        return ResourceArtifactView(
            ok=result.ok,
            target=target,
            artifact_paths=result.artifact_paths,
            primary_artifact_path=result.primary_artifact_path,
            acquisition_kind=result.artifact_kind,
            mime_type=result.mime_type,
            metadata=result.metadata,
            content_hash=result.content_hash,
            summary=result.summary or "",
            issue_code=result.issue_code,
        )

    @staticmethod
    def _extracted_view(
        result: ExtractedMaterialResult,
        *,
        resolved_artifact_kind: Literal[
            "pdf",
            "html",
            "tex_source_archive",
            "plain_text",
            "directory",
            "unknown_binary",
        ],
        extraction_kind: Literal["pdf_text", "html_main_text", "tex_source", "text_normalize"],
    ) -> ResourceExtractedMaterialView:
        return ResourceExtractedMaterialView(
            ok=result.ok,
            source_artifact_path=result.source_artifact_path,
            extracted_paths=result.extracted_paths,
            primary_text_path=result.primary_text_path,
            resolved_artifact_kind=resolved_artifact_kind,
            extraction_kind=extraction_kind,
            preview=result.text_preview,
            metadata=result.metadata,
            summary=result.summary or "",
            issue_code=result.issue_code,
        )

    def _source_acquisition_view(self, target: str, result: AcquiredArtifactResult, root: Path) -> SourceAcquisitionView:
        return SourceAcquisitionView(
            ok=result.ok,
            target=target,
            artifact_refs=[self._rel_or_abs(root, Path(path)) for path in result.artifact_paths],
            primary_artifact_ref=self._rel_or_abs(root, Path(result.primary_artifact_path)) if result.primary_artifact_path else None,
            acquisition_kind=result.artifact_kind,
            mime_type=result.mime_type,
            metadata=result.metadata,
            content_hash=result.content_hash,
            summary=result.summary or "",
            issue_code=result.issue_code,
        )

    def _source_extraction_view(
        self,
        artifact_ref: str,
        result: ExtractedMaterialResult,
        root: Path,
        *,
        resolved_artifact_kind: Literal[
            "pdf",
            "html",
            "tex_source_archive",
            "plain_text",
            "directory",
            "unknown_binary",
        ],
        extraction_kind: Literal["pdf_text", "html_main_text", "tex_source", "text_normalize"],
    ) -> SourceExtractionView:
        return SourceExtractionView(
            ok=result.ok,
            artifact_ref=artifact_ref,
            material_refs=[self._rel_or_abs(root, Path(path)) for path in result.extracted_paths],
            primary_material_ref=self._rel_or_abs(root, Path(result.primary_text_path)) if result.primary_text_path else None,
            resolved_artifact_kind=resolved_artifact_kind,
            extraction_kind=extraction_kind,
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

    def _resolve_inside(self, root: Path, ref: str) -> Path:
        relative = self.runtime.foundation.layout.ensure_relative_path(ref)
        path = root / relative
        self.runtime.foundation.layout.assert_within(root, path)
        return path

    def _safe_material_filename(self, filename: str) -> str:
        import hashlib
        import re

        try:
            return self.runtime.foundation.layout.ensure_safe_key(filename)
        except ValueError:
            sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename.strip())
            sanitized = sanitized.strip("._-")
            if not sanitized:
                sanitized = f"resource_{hashlib.sha256(filename.encode('utf-8')).hexdigest()[:12]}"
            if not sanitized[0].isalnum():
                sanitized = f"resource_{sanitized}"
            return self.runtime.foundation.layout.ensure_safe_key(sanitized)


    @staticmethod
    def _preferred_kind_mismatch(
        preferred_kind: Literal["arxiv_source", "arxiv_pdf", "web_page", "local_file", "local_dir"] | None,
        target_kind: str,
    ) -> str | None:
        if preferred_kind is None:
            return None
        expected = {
            "arxiv_source": "arxiv",
            "arxiv_pdf": "arxiv",
            "web_page": "web_url",
            "local_file": "local_file",
            "local_dir": "local_dir",
        }[preferred_kind]
        return None if expected == target_kind else expected

    @staticmethod
    def _rel_or_abs(root: Path, path: Path) -> str:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return str(path)

    @staticmethod
    def _hash_file(path: Path) -> str:
        import hashlib

        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _required_text(self, value: str, *, field: str, issue_kind: str) -> ServiceResult[str]:
        if not isinstance(value, str) or not value.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue(issue_kind, f"{field} is required.", field=field))
        return self.runtime.foundation.ok(value.strip())

    def _required_boundary_fields(
        self,
        *,
        classification_reason: str,
        resource_role: str,
        consumer_formalization_scope: str,
    ) -> ServiceResult[tuple[str, str, str]]:
        values = []
        for field, value in (
            ("classification_reason", classification_reason),
            ("resource_role", resource_role),
            ("consumer_formalization_scope", consumer_formalization_scope),
        ):
            checked = self._required_text(
                value,
                field=field,
                issue_kind=f"resource_local_{field}_required",
            )
            if not checked.ok or checked.value is None:
                return self.runtime.foundation.fail(checked.issues)
            values.append(checked.value)
        return self.runtime.foundation.ok((values[0], values[1], values[2]))

    def _required_external_boundary_fields(
        self,
        *,
        classification_reason: str,
        relation_to_current_repo_or_node: str,
        consumer_need: str,
        provider_scope: str,
    ) -> ServiceResult[tuple[str, str, str, str]]:
        values = []
        for field, value in (
            ("classification_reason", classification_reason),
            ("relation_to_current_repo_or_node", relation_to_current_repo_or_node),
            ("consumer_need", consumer_need),
            ("provider_scope", provider_scope),
        ):
            checked = self._required_text(
                value,
                field=field,
                issue_kind=f"resource_external_{field}_required",
            )
            if not checked.ok or checked.value is None:
                return self.runtime.foundation.fail(checked.issues)
            values.append(checked.value)
        return self.runtime.foundation.ok((values[0], values[1], values[2], values[3]))
