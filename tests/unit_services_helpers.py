"""Shared service-runtime helpers for unit tests."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from agent_runtime_kit.runtime import ARKServices

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from lean_constellation.services import LeanProviderOverrides, LeanRuntimeServices, create_test_runtime_services  # noqa: E402
from lean_constellation.domain.common import utc_now_iso  # noqa: E402
from lean_constellation.domain.lean_check import LeanCheck  # noqa: E402
from lean_constellation.services.decl_graph.models import DeclFormalSection  # noqa: E402

if TYPE_CHECKING:
    from lean_constellation.domain.repo_release import RepoRelease


class CleanDeclarationSoundnessDispatcher:
    """Return exact clean recursive-soundness evidence for unit fixtures."""

    def __init__(self, *, axioms_by_name: dict[str, list[str]] | None = None) -> None:
        self.axioms_by_name = axioms_by_name or {}

    def __call__(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if tool_name != "lsp.declaration_soundness_batch":
            raise KeyError(tool_name)
        items = [
            {
                "module": target["module"],
                "declaration_name": target["declaration_name"],
                "success": True,
                "source_file_path": target.get("source_file_path"),
                "error_message": None,
                "axioms": self.axioms_by_name.get(target["declaration_name"], []),
                "warnings": [],
                "axiom_count": len(
                    self.axioms_by_name.get(target["declaration_name"], [])
                ),
                "warning_count": 0,
            }
            for target in payload["declarations"]
        ]
        return {
            "success": True,
            "error_message": None,
            "items": items,
            "count": len(items),
            "success_count": len(items),
            "failure_count": 0,
        }


def initialize_native_test_repo(repo_root: Path, *, project_name: str = "TestProject") -> None:
    """Write the minimum native Lake identity required by module derivation tests."""

    repo_root = Path(repo_root)
    truth_root = repo_root / ".lean_constellation"
    truth_root.mkdir(parents=True, exist_ok=True)
    (truth_root / "repo_format.json").write_text(
        '{"repo_format":"native","reason":"unit test fixture"}\n',
        encoding="utf-8",
    )
    (repo_root / "lakefile.toml").write_text(
        f'name = "{project_name}"\n\n[[lean_lib]]\nname = "{project_name}"\n',
        encoding="utf-8",
    )


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
        "schema_version": 1,
        "status": status,
        "policy": "test",
        "allow_sorry": allow,
        "contains_sorry": contains_sorry,
        "contains_axiom": contains_axiom,
        "message": f"Lean check {status}.",
        "subject": {
            "repo_kind": "native",
            "stage": "proof",
            "repo_file_path": "TestProject/Main/Basic.lean",
            "module": "TestProject.Main.Basic",
            "declaration_name": None,
        },
        "fingerprint": {
            "source_sha256": "0" * 64,
            "environment_sha256": None,
            "upstream_revision": None,
        },
        "diagnostics": {
            "schema_version": 2,
            "repo_file_path": None,
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
        "managed_import_check": None,
        "declaration_soundness": None,
        "findings": [],
    }


def write_statement_formal_for_test(
    runtime: LeanRuntimeServices,
    repo_root: Path,
    *,
    node_path: str,
    round_id: str,
    decl_name: str,
    lean_code: str,
    lean_check: dict[str, object],
    deps: list[str] | None = None,
):
    """Seed statement formal truth in tests that intentionally bypass compiler capture."""

    component = runtime.decl_graph.stage_mutation
    if not lean_code or not lean_code.strip():
        return runtime.foundation.fail(
            runtime.foundation.issue("statement_formal_code_required", "Statement formal Lean code is required.", field="lean_code")
        )
    revision = component._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
    if not revision.ok or revision.value is None:
        return runtime.foundation.fail(revision.issues)
    if revision.value.statement is None or revision.value.statement.nl is None:
        return runtime.foundation.fail(
            runtime.foundation.issue("statement_nl_missing", "Statement NL must be written before statement formalization.", object_ref=decl_name)
        )
    revision.value.statement.formal = DeclFormalSection(
        code=lean_code.strip(),
        check=LeanCheck.model_validate(lean_check),
    )
    if deps is not None:
        revision.value.statement.deps = component._normalize_deps(deps)
    revision.value.updated_at = utc_now_iso()
    return component._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)


def write_proof_formal_for_test(
    runtime: LeanRuntimeServices,
    repo_root: Path,
    *,
    node_path: str,
    round_id: str,
    decl_name: str,
    lean_code: str,
    lean_check: dict[str, object],
    deps: list[str] | None = None,
):
    """Seed proof formal truth in tests that intentionally bypass compiler capture."""

    component = runtime.decl_graph.stage_mutation
    if not lean_code or not lean_code.strip():
        return runtime.foundation.fail(
            runtime.foundation.issue("proof_formal_code_required", "Proof formal Lean code is required.", field="lean_code")
        )
    theorem_like = component._require_theorem_like(repo_root, node_path=node_path, decl_name=decl_name)
    if not theorem_like.ok:
        return runtime.foundation.fail(theorem_like.issues)
    revision = component._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
    if not revision.ok or revision.value is None:
        return runtime.foundation.fail(revision.issues)
    if revision.value.proof is None or revision.value.proof.nl is None:
        return runtime.foundation.fail(
            runtime.foundation.issue("proof_nl_missing", "Proof NL must be written before proof formalization.", object_ref=decl_name)
        )
    revision.value.proof.formal = DeclFormalSection(
        code=lean_code.strip(),
        check=LeanCheck.model_validate(lean_check),
    )
    if deps is not None:
        revision.value.proof.deps = component._normalize_deps(deps)
    revision.value.updated_at = utc_now_iso()
    return component._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)


def set_current_decl_lean_name_for_test(
    runtime: LeanRuntimeServices,
    repo_root: Path,
    *,
    node_path: str,
    decl_name: str,
    lean_decl_name: str | None = None,
) -> None:
    """Stamp compiler-confirmed identity in fixtures that bypass formal capture."""

    from lean_constellation.services.foundation import WriteMode

    decl = runtime.decl_graph.get_decl(repo_root, node_path=node_path, name=decl_name)
    assert decl.ok and decl.value is not None, decl.issues
    revision = runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=node_path,
        name=decl_name,
        revision=decl.value.current_revision,
    )
    assert revision.ok and revision.value is not None, revision.issues
    revision.value.lean_decl_name = lean_decl_name or f"{Path(repo_root).name}.{decl_name}"
    path = runtime.decl_graph.graph_store.revision_path(
        repo_root,
        node_path=node_path,
        decl_name=decl_name,
        revision=revision.value.revision,
    )
    written = runtime.foundation.store.write_json_atomic(path, revision.value, mode=WriteMode.UPDATE_EXISTING)
    assert written.ok, written.issues


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
        RepoCompletionMode,
        RepoFormat,
        RepoPublicationState,
        RepoPublicationStatus,
    )
    from lean_constellation.domain.repo_release import RepoRelease
    from lean_constellation.services.decl_graph.models import DeclLifecycle, DeclRevisionStatus
    from lean_constellation.services.foundation import WriteMode
    from lean_constellation.services.node import NodeContractStatus, NodeKind

    repo_root = Path(repo_root)
    initialize_native_test_repo(repo_root, project_name=repo_root.name or "ProviderRepo")
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
    assert runtime.repo_workspace.metadata.set_repo_summary(repo_root, summary=summary).ok
    release = RepoRelease(
        release_id=release_id,
        node_contract_versions=versions,
        completion_mode=RepoCompletionMode.GRAPH_DECLARED,
        semantic_manifest_digest=runtime.validation_snapshot.release_finalizer.compute_semantic_manifest_digest(
            repo_root
        ),
        dependency_lock_digest=runtime.validation_snapshot.release_finalizer.compute_dependency_lock_digest(
            repo_root
        ),
        summary=summary,
    )
    assert runtime.repo_workspace.publication.refresh_managed_gitignore(repo_root).ok
    git_state = runtime.repo_workspace.git_release.ensure_independent_repo(repo_root)
    assert git_state.ok and git_state.value is not None
    assert runtime.repo_workspace.release.create_release(repo_root, release=release).ok

    publication = RepoPublicationState(
        status=RepoPublicationStatus.STABLE,
        latest_release_id=release_id,
    )
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(repo_root),
        publication,
        mode=WriteMode.OVERWRITE,
    ).ok
    candidate_files = [
        path.relative_to(repo_root).as_posix()
        for path in runtime.validation_snapshot.release_finalizer._candidate_files(repo_root)
    ]
    committed = runtime.repo_workspace.git_release.commit_release(
        repo_root,
        release=release,
        candidate_files=candidate_files,
        expected_head=git_state.value.head_commit,
        commit_message=f"release(test): {summary}",
    )
    assert committed.ok and committed.value is not None
    available = runtime.repo_workspace.provider_availability.check_provider_available(repo_root)
    assert available.ok and available.value is not None and available.value.passed
    return release


def publish_adapter_provider_release(
    runtime: LeanRuntimeServices,
    repo_root: Path,
    *,
    summary: str = "Adapter provider test release.",
    release_id: str | None = None,
) -> RepoRelease:
    """Materialize a real flat-Main Adapter RepoRelease for unit fixtures."""

    from lean_constellation.domain.repo import (
        RepoFormat,
        RepoPublicationState,
        RepoPublicationStatus,
    )
    from lean_constellation.domain.repo_release import RepoRelease
    from lean_constellation.services.foundation import WriteMode
    from lean_constellation.services.node import NodeContractStatus, NodeKind

    repo_root = Path(repo_root)
    repo_format = runtime.repo_workspace.metadata.get_repo_format(repo_root)
    assert repo_format.ok and repo_format.value is not None
    assert repo_format.value.repo_format is RepoFormat.ADAPTER
    main = runtime.node.node_tree.get_node(repo_root, path="Main")
    assert main.ok and main.value is not None
    assert main.value.kind is NodeKind.SCOPE
    loaded = runtime.node.contract.get_current_contract(repo_root, node_path="Main")
    assert loaded.ok and loaded.value is not None
    contract = loaded.value.contract.model_copy(deep=True)
    if contract.status is not NodeContractStatus.COMMITTED:
        contract.status = NodeContractStatus.COMMITTED
        contract.committed_at = "2026-08-08T00:00:00Z"
        assert runtime.foundation.store.write_json_atomic(
            runtime.node.node_tree.node_store.contract_path(
                repo_root,
                node_id=main.value.node_id,
                version=contract.version,
            ),
            contract,
            mode=WriteMode.UPDATE_EXISTING,
        ).ok
        metadata = runtime.node.node_tree.node_store.load_node_by_id(
            repo_root,
            node_id=main.value.node_id,
        )
        assert metadata.ok and metadata.value is not None
        metadata.value.current_contract_version = contract.version
        metadata.value.active_contract_version = contract.version
        metadata.value.open_contract_version = None
        assert runtime.node.node_tree.node_store.save_node(
            repo_root,
            metadata.value,
            mode=WriteMode.UPDATE_EXISTING,
        ).ok

    release_id = release_id or f"release_{uuid4().hex}"
    assert runtime.repo_workspace.metadata.set_repo_summary(repo_root, summary=summary).ok
    release = RepoRelease(
        release_id=release_id,
        node_contract_versions={main.value.node_id: contract.version},
        completion_mode=contract.task_completion_mode,
        semantic_manifest_digest=runtime.validation_snapshot.release_finalizer.compute_semantic_manifest_digest(
            repo_root
        ),
        dependency_lock_digest=runtime.validation_snapshot.release_finalizer.compute_dependency_lock_digest(
            repo_root
        ),
        summary=summary,
    )
    assert runtime.repo_workspace.publication.refresh_managed_gitignore(repo_root).ok
    git_state = runtime.repo_workspace.git_release.ensure_independent_repo(repo_root)
    assert git_state.ok and git_state.value is not None
    assert runtime.repo_workspace.release.create_release(repo_root, release=release).ok
    publication = RepoPublicationState(
        status=RepoPublicationStatus.STABLE,
        latest_release_id=release_id,
    )
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(repo_root),
        publication,
        mode=WriteMode.OVERWRITE,
    ).ok
    candidate_files = [
        path.relative_to(repo_root).as_posix()
        for path in runtime.validation_snapshot.release_finalizer._candidate_files(repo_root)
    ]
    committed = runtime.repo_workspace.git_release.commit_release(
        repo_root,
        release=release,
        candidate_files=candidate_files,
        expected_head=git_state.value.head_commit,
        commit_message=f"release(test): {summary}",
    )
    assert committed.ok and committed.value is not None
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
        git_url="https://example.invalid/upstream.git",
        revision="1" * 40,
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
    publish_adapter_provider_release(runtime, repo_root, summary=summary)


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


def valid_resource_readme(
    *,
    canonical_entry: str = "article/main.md",
    original_ref: str = "the supporting source material",
    title: str = "Curated resource fixture",
) -> str:
    """Return a static Resource README fixture."""

    return f"""# {title}

Title: {title}
Authors: Fixture Author
Version/date: 2026-08-03

## Source identity

Canonical locator: https://example.com/resource-fixture
This is a deterministic supporting-material fixture.

## License and access

License: test fixture terms. Access: local and unrestricted.

## Files

`{canonical_entry}` contains the durable material represented from `{original_ref}`.

## Reading order

Read `{canonical_entry}` first; it is the canonical entry.

## Selected scope

The resource contains the complete fixture text.

## Representation limits

No representation limits affect reading this fixture.

## Ownership

Supporting material only. The current repository retains formalization responsibility; this is not a provider.
"""
