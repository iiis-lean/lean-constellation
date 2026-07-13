from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lean_constellation.app import create_app_runtime_services
from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import RepoFormat, RepoPublicationState, RepoPublicationStatus
from lean_constellation.domain.repo_run import SourceScope
from lean_constellation.services.foundation import FoundationContext, WriteMode
from tests.unit.services.repo_workspace.test_repo_release import _prepare_release_repo


def _prepare_source_index(runtime, repo_root: Path) -> None:  # noqa: ANN001
    preparation = RepoPreparationInput(
        goal="Expose a small proved Lean result.",
        source_corpus_mode=SourceCorpusMode.PREPARE,
        source_corpus_relpath=".lean_constellation/source",
        source_description="Local real legacy-adoption fixture.",
        interface_inputs=[
            DeclInterface(name="PublicResult", kind=DeclKind.THEOREM, summary="Expose the public result."),
        ],
    )
    preparation_path = runtime.foundation.layout.preparation_input_path(FoundationContext(repo_root=repo_root))
    assert runtime.foundation.store.write_json_atomic(preparation_path, preparation).ok
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "README.md").write_text(
        "# Entry\n\n"
        "Source provenance: local real legacy-adoption fixture.\n"
        "Reading order: start here, then read `chapter.md`.\n"
        "Main material: `chapter.md` states the fixture theorem.\n"
        "Known gaps and extraction limits: none.\n",
        encoding="utf-8",
    )
    (source_root / "chapter.md").write_text("Theorem: True.\n", encoding="utf-8")
    assert runtime.material.submit_source_corpus_prepared(
        repo_root,
        entry_path="README.md",
        overview="A minimal real source corpus.",
        preparation_summary="Prepared the real legacy-adoption source corpus.",
    ).ok
    scope = runtime.material.resolve_source_scope(repo_root, source_scope=SourceScope(mode="all"))
    assert scope.ok and scope.value is not None
    opened = runtime.material.open_source_index_update(
        repo_root,
        update_id="legacy-adoption-real",
        resolved_scope=scope.value,
        index_policy="auto",
    )
    assert opened.ok and opened.value is not None
    block = runtime.material.create_source_block(
        repo_root,
        parent_id="root",
        kind="section",
        title="Fixture theorem",
        summary="The source statement used by the real adoption fixture.",
        expected_update_id="legacy-adoption-real",
    )
    assert block.ok and block.value is not None
    assert runtime.material.add_source_block_ref(
        repo_root,
        block_id=block.value.block_id,
        path="chapter.md",
        start_line=1,
        end_line=1,
        role="primary",
        expected_update_id="legacy-adoption-real",
    ).ok
    assert runtime.material.mark_block_refs_done(
        repo_root, block_id=block.value.block_id, expected_update_id="legacy-adoption-real"
    ).ok
    assert runtime.material.mark_block_links_done(
        repo_root, block_id=block.value.block_id, expected_update_id="legacy-adoption-real"
    ).ok
    assert runtime.material.mark_block_completed(
        repo_root, block_id=block.value.block_id, expected_update_id="legacy-adoption-real"
    ).ok
    for path in ("README.md", "chapter.md"):
        assert runtime.material.set_file_survey_status(
            repo_root,
            path=path,
            status="surveyed",
            summary=f"Surveyed {path}.",
            expected_update_id="legacy-adoption-real",
        ).ok
        assert runtime.material.set_file_indexing_status(
            repo_root,
            path=path,
            status="indexed",
            expected_update_id="legacy-adoption-real",
        ).ok
    validated = runtime.material.validate_source_index_update(
        repo_root,
        update_id="legacy-adoption-real",
        baseline_index=None,
        expected_baseline_digest=opened.value.baseline_digest,
        resolved_scope=scope.value.resolved_file_paths,
        require_completed=True,
    )
    assert validated.ok and validated.value is not None and validated.value.gate.passed
    assert runtime.material.commit_source_index_update(
        repo_root,
        update_id="legacy-adoption-real",
        validated=validated.value,
    ).ok


def _prepare_legacy_repo(repo_root: Path):  # noqa: ANN201
    _prepare_release_repo(repo_root)
    runtime = create_app_runtime_services(
        runtime_root=repo_root / ".agent_runtime",
        start_paused=True,
        register_application_tools=False,
        register_submit_tools=False,
    )
    skeleton = runtime.repo_workspace.initialize_repo_as_native(
        repo_root,
        project_name="LegacyAdoptionReal",
    )
    assert skeleton.ok, [(issue.kind, issue.message) for issue in skeleton.issues]
    _prepare_source_index(runtime, repo_root)
    index_path = repo_root / ".lean_constellation" / "source_index" / "index.json"
    v2_payload = json.loads(index_path.read_text(encoding="utf-8"))
    v2_payload["schema_version"] = 2
    v2_payload.pop("active_update_id")
    v2_payload.pop("active_file_scope")
    for file_payload in v2_payload["files"].values():
        file_payload.pop("source_sha256")
        file_payload.pop("committed")
    index_path.write_text(json.dumps(v2_payload, indent=2) + "\n", encoding="utf-8")
    legacy_v2_bytes = index_path.read_bytes()
    inspected = runtime.material.inspect_source_index_schema(repo_root)
    assert inspected.ok and inspected.value is not None
    assert inspected.value.stored_schema_version == 2 and inspected.value.migration_required
    public_revision = runtime.decl_graph.get_decl_revision(
        repo_root, node_path="Main.Results", name="PublicResult", revision=1
    )
    assert public_revision.ok and public_revision.value is not None
    public_revision.value.statement.deps = []
    public_revision.value.proof.deps = []
    assert runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.revision_path(
            repo_root, node_path="Main.Results", decl_name="PublicResult", revision=1
        ),
        public_revision.value,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    tree = runtime.node.node_tree.get_node_tree(repo_root)
    assert tree.ok and tree.value is not None
    old_contract_bytes: dict[str, bytes] = {}
    for node in tree.value.nodes:
        if node.path == "Main":
            main_view = runtime.node.contract.get_visible_contract(repo_root, node_path="Main")
            assert main_view.ok and main_view.value is not None
            main_contract = main_view.value.contract
            main_contract.interfaces = [
                DeclInterface(
                    name="PublicResult",
                    kind=DeclKind.THEOREM,
                    summary="Expose the public result.",
                    bound_decl=DeclRef(node="Main.Results", name="PublicResult", revision=1),
                )
            ]
            main_path = runtime.node.node_tree.node_store.contract_path(
                repo_root, node_id=node.node_id, version=main_contract.version
            )
            assert runtime.foundation.store.write_json_atomic(
                main_path, main_contract, mode=WriteMode.UPDATE_EXISTING
            ).ok
        if node.kind.value == "content":
            contract_view = runtime.node.contract.get_visible_contract(repo_root, node_path=node.path)
            assert contract_view.ok and contract_view.value is not None
            contract = contract_view.value.contract
            contract.decl_graph_head = {}
            contract_path = runtime.node.node_tree.node_store.contract_path(
                repo_root, node_id=node.node_id, version=contract.version
            )
            assert runtime.foundation.store.write_json_atomic(
                contract_path, contract, mode=WriteMode.UPDATE_EXISTING
            ).ok
            old_contract_bytes[node.node_id] = contract_path.read_bytes()
        repaired = runtime.lean_projection.refresh_node_projection(repo_root, node_path=node.path)
        assert repaired.ok, [(issue.kind, issue.message) for issue in repaired.issues]
        if node.kind.value == "content":
            decl_files = runtime.lean_projection.repair.repair_decl_files_from_active_graph(
                repo_root, node_path=node.path
            )
            assert decl_files.ok, [(issue.kind, issue.message) for issue in decl_files.issues]
    assert runtime.repo_workspace.metadata.set_repo_format(
        repo_root, repo_format=RepoFormat.NATIVE, reason="real legacy adoption fixture"
    ).ok
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(repo_root),
        RepoPublicationState(status=RepoPublicationStatus.STABLE),
        mode=WriteMode.OVERWRITE,
    ).ok
    assert runtime.external.lean_toolchain.run_lake_build(repo_root).ok
    return runtime, old_contract_bytes, legacy_v2_bytes


@pytest.mark.real
def test_legacy_stable_repo_adoption_real_lake_and_ark_snapshot(tmp_path: Path) -> None:
    if shutil.which("lake") is None or shutil.which("lean") is None:
        pytest.skip("real legacy adoption requires lake and lean")
    repo_root = tmp_path / "LegacyAdoptionReal"
    runtime, old_contract_bytes, legacy_v2_bytes = _prepare_legacy_repo(repo_root)

    adopted = runtime.validation_snapshot.adopt_legacy_stable_repo(
        repo_root,
        summary="Adopt the real legacy stable repository.",
        dry_run=False,
    )

    assert adopted.ok, [(issue.kind, issue.message) for issue in adopted.issues]
    assert adopted.value is not None and adopted.value.outcome == "adopted"
    assert adopted.value.finalized is not None
    release = adopted.value.finalized.release.release
    assert release.parent_release_id is None
    assert adopted.value.pre_adoption_checkpoint_id is not None
    pre_adoption_root = runtime.validation_snapshot.snapshot_restore._snapshot_dir(
        repo_root, adopted.value.pre_adoption_checkpoint_id
    )
    archived_v2 = (
        pre_adoption_root / "files" / "lean_constellation" / "source_index" / "index.json"
    )
    assert archived_v2.read_bytes() == legacy_v2_bytes
    live_schema = runtime.material.inspect_source_index_schema(repo_root)
    assert live_schema.ok and live_schema.value is not None
    assert live_schema.value.stored_schema_version == 3 and not live_schema.value.migration_required
    publication = runtime.repo_workspace.metadata.get_repo_publication(repo_root).value.publication
    assert publication.status == RepoPublicationStatus.STABLE
    assert publication.latest_release_id == release.release_id
    checkpoints = runtime.validation_snapshot.list_repo_checkpoint_snapshots(repo_root)
    assert checkpoints.ok and checkpoints.value is not None
    checkpoint = next(item for item in checkpoints.value if item.snapshot_id == release.repo_checkpoint_id)
    assert checkpoint.ark_runtime_snapshot_id
    final_checkpoint_index = (
        Path(checkpoint.root) / "files" / "lean_constellation" / "source_index" / "index.json"
    )
    assert json.loads(final_checkpoint_index.read_text(encoding="utf-8"))["schema_version"] == 3
    for node in runtime.node.node_tree.get_node_tree(repo_root).value.nodes:
        if node.kind.value != "content":
            continue
        current = runtime.node.contract.get_visible_contract(repo_root, node_path=node.path).value
        assert current.version == 2
        assert current.contract.decl_graph_head
        old_path = runtime.node.node_tree.node_store.contract_path(repo_root, node_id=node.node_id, version=1)
        assert old_path.read_bytes() == old_contract_bytes[node.node_id]
    availability = runtime.repo_workspace.provider_availability.check_provider_available(repo_root)
    assert availability.ok and availability.value is not None and availability.value.passed

    project_file = repo_root / "LegacyAdoptionReal" / "Main" / "Interfaces.lean"
    original = project_file.read_text(encoding="utf-8")
    project_file.write_text("this is not Lean\n", encoding="utf-8")
    restored = runtime.validation_snapshot.restore_repo_release(repo_root, release_id=release.release_id)
    assert restored.ok, [(issue.kind, issue.message) for issue in restored.issues]
    assert project_file.read_text(encoding="utf-8") == original
    restored_schema = runtime.material.inspect_source_index_schema(repo_root)
    assert restored_schema.ok and restored_schema.value is not None
    assert restored_schema.value.stored_schema_version == 3
    assert runtime.external.lean_toolchain.run_lake_build(repo_root).ok
