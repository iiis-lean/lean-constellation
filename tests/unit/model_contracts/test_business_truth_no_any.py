from __future__ import annotations

import inspect
from typing import Any, get_args, get_origin

import pytest
from pydantic import ValidationError

from lean_constellation.domain.preparation import RepoDependencyRequirement, RepoPreparationInput, SourceMaterialInput
from lean_constellation.domain.repo_release import RepoRelease
from lean_constellation.services.decl_graph import (
    Decl,
    DeclGraphIndex,
    DeclGraphRound,
    DeclGraphStrategy,
    DeclRevision,
    DeclRevisionChange,
)
from lean_constellation.domain.lean_check import LeanCheck, LeanDiagnosticItem, LeanDiagnostics, SorryAxiomOccurrence, SorryAxiomScan
from lean_constellation.services.decl_graph.models import DeclProof, DeclStatement
from lean_constellation.services.material.resource_library import ResourceDraft, ResourceMetadata, ResourceTarget
from lean_constellation.services.material.source_index import SourceBlock, SourceBlockRef, SourceFileIndex, SourceIndex, SourceLink
from lean_constellation.services.node.contract_fields import ContractMaterialRef, NodeDep, NodeMathlibDeclUse, NodeMathlibModuleUse
from lean_constellation.services.node.node_tree import NodeContract, NodeMetadata
from lean_constellation.services.validation_snapshot.snapshot_restore import RepoCheckpointSnapshotManifest


BUSINESS_TRUTH_MODELS = [
    NodeMetadata,
    NodeContract,
    ContractMaterialRef,
    NodeDep,
    NodeMathlibModuleUse,
    NodeMathlibDeclUse,
    DeclGraphIndex,
    DeclGraphStrategy,
    DeclGraphRound,
    Decl,
    DeclRevision,
    DeclRevisionChange,
    DeclStatement,
    DeclProof,
    LeanDiagnosticItem,
    LeanDiagnostics,
    SorryAxiomOccurrence,
    SorryAxiomScan,
    LeanCheck,
    SourceIndex,
    SourceBlock,
    SourceBlockRef,
    SourceLink,
    SourceFileIndex,
    ResourceTarget,
    ResourceDraft,
    ResourceMetadata,
    RepoDependencyRequirement,
    RepoPreparationInput,
    RepoCheckpointSnapshotManifest,
    RepoRelease,
]


def test_business_truth_models_do_not_embed_bare_any_or_view_types() -> None:
    offenders: list[str] = []
    for model in BUSINESS_TRUTH_MODELS:
        for field_name, field in model.model_fields.items():
            annotation = field.annotation
            if _contains_bare_any(annotation):
                offenders.append(f"{model.__name__}.{field_name}: contains Any")
            if _contains_view_model(annotation):
                offenders.append(f"{model.__name__}.{field_name}: embeds View model")

    assert offenders == []


@pytest.mark.parametrize(
    ("model", "payload", "legacy_field"),
    [
        (
            DeclRevision,
            {
                "decl_name": "legacy_decl",
                "revision": 1,
                "state": "declared",
                "decl_deps": ["supporting_lemma"],
            },
            "decl_deps",
        ),
        (
            SourceIndex,
            {
                "status": "draft",
                "overview": "Index.",
                "repo_root": "/tmp/repo",
            },
            "repo_root",
        ),
        (
            ResourceMetadata,
            {
                "resource_key": "r_demo",
                "target": {
                    "kind": "web_url",
                    "target": "https://example.com",
                    "canonical_locator": "https://example.com",
                    "summary": "View-only field.",
                },
                "canonical_entry": "normalized/main.md",
            },
            "summary",
        ),
        (
            RepoDependencyRequirement,
            {
                "name": "needs_provider",
                "target_repo": "provider",
                "waiting_state": {"provider_repo": "provider", "waiting": True},
            },
            "waiting_state",
        ),
        (
            RepoCheckpointSnapshotManifest,
            {
                "snapshot_id": "snap_demo",
                "checkpoint_kind": "manual_test",
                "created_at": "2026-01-01T00:00:00Z",
                "repo_root": "/tmp/repo",
                "ark_runtime_snapshot_id": "ark_snap",
                "node_paths": ["Main.Topic"],
                "files_manifest_relpath": "files_manifest.json",
                "summary": "Legacy snapshot manifest.",
            },
            "node_paths",
        ),
    ],
)
def test_business_truth_models_reject_known_legacy_or_view_fields(model: type, payload: dict[str, object], legacy_field: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        model.model_validate(payload)
    assert legacy_field in str(exc_info.value)


def test_source_material_input_requires_current_flat_fields_and_role() -> None:
    item = SourceMaterialInput(
        target="  https://example.test/paper.pdf  ",
        included_scope="  Complete paper.  ",
        role="primary_source",
    )
    assert item.target == "https://example.test/paper.pdf"
    assert item.included_scope == "Complete paper."

    with pytest.raises(ValidationError):
        SourceMaterialInput.model_validate({"target": "https://example.test/paper.pdf", "role": "primary_source"})
    with pytest.raises(ValidationError):
        SourceMaterialInput.model_validate(
            {"target": "https://example.test/paper.pdf", "included_scope": "Complete paper.", "role": "unknown"}
        )
    with pytest.raises(ValidationError):
        RepoPreparationInput(
            goal="Prepare source.",
            source_corpus_mode="prepare",
            source_material_inputs=[
                SourceMaterialInput(target="x", included_scope="all", role="primary_source"),
                SourceMaterialInput(target="x", included_scope="all", role="primary_source"),
            ],
        )


def _contains_bare_any(annotation: object) -> bool:
    if annotation is Any:
        return True
    return any(_contains_bare_any(arg) for arg in get_args(annotation) if _is_type_like(arg))


def _contains_view_model(annotation: object) -> bool:
    if inspect.isclass(annotation) and annotation.__name__.endswith("View"):
        return True
    origin = get_origin(annotation)
    if inspect.isclass(origin) and origin.__name__.endswith("View"):
        return True
    return any(_contains_view_model(arg) for arg in get_args(annotation) if _is_type_like(arg))


def _is_type_like(value: object) -> bool:
    if value is None or value is Ellipsis:
        return False
    if isinstance(value, (str, bytes, int, float, bool)):
        return False
    return True
