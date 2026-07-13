"""Shared service-runtime helpers for unit tests."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from agent_runtime_kit.runtime import ARKServices

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from lean_constellation.services import LeanProviderOverrides, LeanRuntimeServices, create_test_runtime_services  # noqa: E402

if TYPE_CHECKING:
    from lean_constellation.domain.repo_release import RepoRelease


def lean_check_payload(
    *,
    passed: bool = True,
    contains_sorry: bool = False,
    contains_axiom: bool = False,
    allow_sorry: bool | None = None,
) -> dict[str, object]:
    """Build the complete current LeanCheck truth shape for tests."""
    allow = contains_sorry if allow_sorry is None else allow_sorry
    status = "passed" if passed else "failed"
    return {
        "status": status,
        "policy": "test",
        "allow_sorry": allow,
        "contains_sorry": contains_sorry,
        "contains_axiom": contains_axiom,
        "message": f"Lean check {status}.",
        "diagnostics": {
            "repo_root": ".",
            "file_path": None,
            "passed": passed,
            "diagnostics": [],
            "summary": f"Diagnostics {status}.",
            "raw_excerpt": None,
        },
        "scan": {
            "contains_sorry": contains_sorry,
            "contains_admit": False,
            "contains_axiom": contains_axiom,
            "contains_opaque": False,
            "contains_unsafe": False,
            "sorry_count": int(contains_sorry),
            "admit_count": 0,
            "axiom_count": int(contains_axiom),
            "opaque_count": 0,
            "unsafe_count": 0,
            "occurrences": [],
            "summary": "Test source scan.",
            "limitation": "Test fixture.",
        },
    }


def publish_native_provider_release(
    runtime: LeanRuntimeServices,
    repo_root: Path,
    *,
    summary: str = "Native provider test release.",
    release_id: str | None = None,
) -> RepoRelease:
    """Materialize the minimum real native-release evidence used by unit fixtures.

    This intentionally mirrors the future release finalizer using existing production
    truth models.  It does not install a production test-only writer or fall back to
    the legacy provider-ready marker.
    """

    from lean_constellation.domain.repo import (
        ProofAvailability,
        RepoFormat,
        RepoPublicationState,
        RepoPublicationStatus,
    )
    from lean_constellation.domain.repo_release import RepoRelease
    from lean_constellation.services.decl_graph.models import DeclLifecycle, DeclRevisionStatus
    from lean_constellation.services.foundation import FoundationContext, WriteMode
    from lean_constellation.services.node import NodeContractStatus, NodeKind
    from lean_constellation.services.validation_snapshot.snapshot_restore import (
        RepoCheckpointKind,
        RepoCheckpointSnapshotManifest,
    )

    repo_root = Path(repo_root)
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
    assert runtime.repo_workspace.metadata.set_repo_format(
        repo_root,
        repo_format=RepoFormat.NATIVE,
        reason="unit fixture native provider",
    ).ok
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok

    tree = runtime.node.node_tree.get_node_tree(repo_root)
    assert tree.ok and tree.value is not None
    versions: dict[str, int] = {}
    for node in tree.value.nodes:
        loaded = runtime.node.contract.get_current_contract(repo_root, node_path=node.path)
        assert loaded.ok and loaded.value is not None
        contract = loaded.value.contract.model_copy(deep=True)
        if node.kind == NodeKind.CONTENT:
            decls = runtime.decl_graph.decl_catalog.list_decls(repo_root, node_path=node.path)
            assert decls.ok and decls.value is not None
            head: dict[str, int] = {}
            for decl in decls.value:
                if decl.lifecycle != DeclLifecycle.ACTIVE:
                    continue
                revision = runtime.decl_graph.decl_catalog.get_decl_revision(
                    repo_root,
                    node_path=node.path,
                    name=decl.name,
                    revision=decl.current_revision,
                )
                assert revision.ok and revision.value is not None
                if revision.value.status == DeclRevisionStatus.COMMITTED:
                    head[decl.name] = decl.current_revision
            contract.decl_graph_head = head
        else:
            contract.decl_graph_head = {}
        contract.status = NodeContractStatus.COMMITTED
        contract.committed_at = "2026-07-12T00:00:00Z"
        contract_path = runtime.node.node_tree.node_store.contract_path(
            repo_root,
            node_id=node.node_id,
            version=contract.version,
        )
        assert runtime.foundation.store.write_json_atomic(
            contract_path,
            contract,
            mode=WriteMode.UPDATE_EXISTING,
        ).ok
        metadata = runtime.node.node_tree.node_store.load_node_by_id(repo_root, node_id=node.node_id)
        assert metadata.ok and metadata.value is not None
        metadata.value.current_contract_version = contract.version
        metadata.value.active_contract_version = contract.version
        metadata.value.open_contract_version = None
        assert runtime.node.node_tree.node_store.save_node(
            repo_root,
            metadata.value,
            mode=WriteMode.UPDATE_EXISTING,
        ).ok
        versions[node.node_id] = contract.version

    release_id = release_id or f"release_{uuid4().hex}"
    checkpoint_id = f"checkpoint_{release_id}"
    release = RepoRelease(
        release_id=release_id,
        node_contract_versions=versions,
        target_proof_availability=ProofAvailability.DECLARED,
        repo_checkpoint_id=checkpoint_id,
        summary=summary,
    )
    assert runtime.repo_workspace.release.create_release(repo_root, release=release).ok

    checkpoint_root = (
        runtime.foundation.layout.snapshot_root(FoundationContext(repo_root=repo_root))
        / "repo_checkpoints"
        / checkpoint_id
    )
    manifest = RepoCheckpointSnapshotManifest(
        snapshot_id=checkpoint_id,
        checkpoint_kind=RepoCheckpointKind.MANUAL_TEST_STABLE_POINT,
        created_at="2026-07-12T00:00:00Z",
        repo_root=str(repo_root),
        ark_runtime_snapshot_id=f"ark_{checkpoint_id}",
        files_manifest_relpath="files.json",
        summary="Native provider release evidence fixture.",
    )
    assert runtime.foundation.store.write_json_atomic(checkpoint_root / "snapshot.json", manifest).ok
    assert runtime.repo_workspace.metadata.set_repo_summary(repo_root, summary=summary).ok
    publication = RepoPublicationState(
        status=RepoPublicationStatus.STABLE,
        latest_release_id=release_id,
    )
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(repo_root),
        publication,
        mode=WriteMode.OVERWRITE,
    ).ok
    available = runtime.repo_workspace.provider_availability.check_provider_available(repo_root)
    assert available.ok and available.value is not None and available.value.passed
    return release


def publish_adapter_provider_ready(
    runtime: LeanRuntimeServices,
    repo_root: Path,
    *,
    summary: str = "Adapter provider test publication.",
) -> None:
    """Create a no-interface adapter fixture that passes the complete ready gate."""

    from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
    from lean_constellation.domain.repo import RepoFormat

    repo_root = Path(repo_root)
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
    assert runtime.repo_workspace.metadata.set_repo_format(
        repo_root,
        repo_format=RepoFormat.ADAPTER,
        reason="unit fixture adapter provider",
    ).ok
    assert runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Expose a trusted upstream package through an adapter.",
            source_corpus_mode=SourceCorpusMode.NONE,
            source_corpus_relpath=None,
            interface_inputs=[],
        ),
    ).ok
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    (repo_root / "lakefile.toml").write_text(
        'name = "Adapter"\n\n'
        '[[require]]\n'
        'name = "upstream"\n'
        'path = ".lake/packages/upstream"\n',
        encoding="utf-8",
    )
    upstream = repo_root / ".lake" / "packages" / "upstream"
    (upstream / "Upstream").mkdir(parents=True, exist_ok=True)
    (upstream / "lakefile.toml").write_text('name = "upstream"\n', encoding="utf-8")
    (upstream / "Upstream" / "Basic.lean").write_text("import Mathlib\n", encoding="utf-8")
    assert runtime.adapter.write_adapter_upstream_metadata(
        repo_root,
        source_kind="local_path",
        local_path=str(upstream),
        package_name="upstream",
        dependency_name="upstream",
        evidence_summary="Unit fixture upstream checkout.",
        visible_modules=["Upstream.Basic"],
    ).ok
    assert runtime.adapter.mark_upstream_build_trusted(
        repo_root,
        summary="Unit fixture upstream build and import checks passed.",
    ).ok
    assert runtime.adapter.ensure_flat_main_catalog(repo_root).ok
    projection = runtime.adapter.refresh_adapter_projection(repo_root)
    assert projection.ok, projection.issues
    gate = runtime.adapter.check_adapter_ready(repo_root)
    assert gate.ok and gate.value is not None and gate.value.passed, gate.issues
    ready = runtime.repo_workspace.metadata.mark_repo_stable(repo_root, summary=summary)
    assert ready.ok and ready.value is not None
    assert ready.value.publication.status.value == "stable"


def make_runtime(
    *,
    external_config: object | None = None,
    external_overrides: dict[str, object] | None = None,
    providers: LeanProviderOverrides | None = None,
    native_lake_project_config: object | None = None,
    workspace_config: object | None = None,
) -> LeanRuntimeServices:
    """Create a real Lean runtime service graph backed by real ARKServices."""

    return create_test_runtime_services(
        ark_services=ARKServices(),
        external_config=external_config,
        external_overrides=external_overrides,
        providers=providers,
        native_lake_project_config=native_lake_project_config,
        workspace_config=workspace_config,
    )
