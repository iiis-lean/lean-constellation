from __future__ import annotations

from pathlib import Path

from lean_constellation.app.operator_data.execution import OperatorExecutionService
from lean_constellation.app.operator_data.repo_material import (
    RepoMaterialOperatorApi,
    SourceIndexBlockCreateInput,
    SourceIndexBlockLifecycleInput,
    SourceIndexBlockRefAddInput,
    SourceIndexBlockRefUpdateInput,
    SourceIndexCommitInput,
    SourceIndexFileIndexingInput,
    SourceIndexFileSurveyInput,
    SourceIndexOpenInput,
    SourceIndexOverviewInput,
    SourceIndexLinkCreateInput,
    SourceIndexLinkUpdateInput,
)
from lean_constellation.app.operator_data.repo_material_http import (
    REPO_MATERIAL_HTTP_ROUTES,
    invoke_repo_material_route,
)
from lean_constellation.domain.repo_run import SourceScope

from tests.unit.app.operator_data._helpers import make_registry, make_repo


def _write_source(repo_root: Path) -> None:
    source = repo_root / ".lean_constellation/source"
    source.mkdir(parents=True)
    (source / "README.md").write_text(
        "# Corpus\n\n"
        "Source provenance: local fixture.\n"
        "Reading order: start here, then read chapter.md.\n"
        "Main material: chapter.md contains the theorem.\n"
        "Known gaps and extraction limits: none.\n",
        encoding="utf-8",
    )
    (source / "chapter.md").write_text("Definition A.\nTheorem B.\n", encoding="utf-8")


def _api(workspace: Path) -> RepoMaterialOperatorApi:
    registry = make_registry(workspace)
    return RepoMaterialOperatorApi(
        OperatorExecutionService(registry),
        workspace_root=workspace,
        workspace_runtime=registry.workspace_runtime(),
    )


def _digest(result) -> str:  # noqa: ANN001
    assert result.ok and result.value is not None, result.issues
    return result.value.current_index_digest


def test_source_index_granular_operator_update_survives_facade_restart(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = make_repo(workspace)
    _write_source(repo_root)
    api = _api(workspace)
    prepared = api.workspace_runtime.material.submit_source_corpus_prepared(
        repo_root,
        entry_path="README.md",
        overview="Operator fixture.",
        preparation_summary="Prepared fixture.",
    )
    assert prepared.ok, prepared.issues
    missing = api.workspace_runtime.material.source_index.missing_source_index_digest()

    opened = api.open_source_index_update(
        "MainRepo",
        SourceIndexOpenInput(
            source_scope=SourceScope(mode="selected", selectors=["chapter.md"]),
            expected_baseline_digest=missing,
        ),
    )
    assert opened.ok and opened.value is not None, opened.issues
    assert opened.value.opened.new_file_paths == ["chapter.md"]
    assert "baseline_locator" not in opened.value.model_dump(mode="json")
    assert not (repo_root / ".agent_runtime").exists()
    digest = opened.value.current_index_digest

    # A new registry/runtime reconstructs all state from persisted repo truth.
    api = _api(workspace)
    digest = _digest(
        api.set_source_index_overview(
            "MainRepo",
            SourceIndexOverviewInput(
                expected_current_digest=digest,
                overview="Indexed chapter.",
            ),
        )
    )
    created = api.create_source_block(
        "MainRepo",
        SourceIndexBlockCreateInput(
            expected_current_digest=digest,
            parent_id="root",
            kind="theorem",
            title="Theorem B",
            summary="The principal theorem.",
        ),
    )
    digest = _digest(created)
    block_id = created.value.value.block_id
    added = api.add_source_block_ref(
        "MainRepo",
        SourceIndexBlockRefAddInput(
            expected_current_digest=digest,
            block_id=block_id,
            path="chapter.md",
            start_line=1,
            end_line=2,
            role="primary",
        ),
    )
    digest = _digest(added)
    ref_id = added.value.value.refs[0].ref_id
    digest = _digest(
        api.update_source_block_ref(
            "MainRepo",
            SourceIndexBlockRefUpdateInput(
                expected_current_digest=digest,
                block_id=block_id,
                ref_id=ref_id,
                path="chapter.md",
                start_line=1,
                end_line=2,
                role="primary evidence",
            ),
        )
    )
    digest = _digest(
        api.mark_source_block_refs_done(
            "MainRepo",
            SourceIndexBlockLifecycleInput(
                expected_current_digest=digest,
                block_id=block_id,
            ),
        )
    )
    linked = api.create_source_link(
        "MainRepo",
        SourceIndexLinkCreateInput(
            expected_current_digest=digest,
            source_block_id=block_id,
            target_hint="External consequence.",
            link_kind="supports",
            evidence_ref_ids=[ref_id],
        ),
    )
    digest = _digest(linked)
    digest = _digest(
        api.update_source_link(
            "MainRepo",
            SourceIndexLinkUpdateInput(
                expected_current_digest=digest,
                link_id=linked.value.value.link_id,
                target_hint="Updated external consequence.",
                link_kind="implies",
                evidence_ref_ids=[ref_id],
            ),
        )
    )
    digest = _digest(
        api.mark_source_block_links_done(
            "MainRepo",
            SourceIndexBlockLifecycleInput(
                expected_current_digest=digest,
                block_id=block_id,
            ),
        )
    )
    digest = _digest(
        api.mark_source_block_completed(
            "MainRepo",
            SourceIndexBlockLifecycleInput(
                expected_current_digest=digest,
                block_id=block_id,
            ),
        )
    )
    digest = _digest(
        api.set_source_file_survey(
            "MainRepo",
            SourceIndexFileSurveyInput(
                expected_current_digest=digest,
                path="chapter.md",
                status="surveyed",
            ),
        )
    )
    digest = _digest(
        api.set_source_file_indexing(
            "MainRepo",
            SourceIndexFileIndexingInput(
                expected_current_digest=digest,
                path="chapter.md",
                status="indexed",
            ),
        )
    )
    committed = api.validate_and_commit_source_index(
        "MainRepo",
        SourceIndexCommitInput(expected_current_digest=digest),
    )
    assert committed.ok and committed.value is not None, committed.issues
    assert committed.value.newly_committed_file_paths == ["chapter.md"]
    assert not (repo_root / ".lean_constellation/source_index/operator_baseline.json").exists()


def test_source_index_mutation_rejects_stale_digest_without_truth_change(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = make_repo(workspace)
    _write_source(repo_root)
    api = _api(workspace)
    assert api.workspace_runtime.material.submit_source_corpus_prepared(
        repo_root,
        entry_path="README.md",
        overview="Operator fixture.",
        preparation_summary="Prepared fixture.",
    ).ok
    opened = api.open_source_index_update(
        "MainRepo",
        SourceIndexOpenInput(
            source_scope=SourceScope(mode="selected", selectors=["chapter.md"]),
            expected_baseline_digest=api.workspace_runtime.material.source_index.missing_source_index_digest(),
        ),
    )
    assert opened.ok and opened.value is not None
    before = (repo_root / ".lean_constellation/source_index/index.json").read_bytes()

    stale = api.set_source_index_overview(
        "MainRepo",
        SourceIndexOverviewInput(expected_current_digest="stale", overview="No write."),
    )

    assert not stale.ok
    assert stale.issues[0].kind == "source_index_current_digest_mismatch"
    assert (repo_root / ".lean_constellation/source_index/index.json").read_bytes() == before


def test_repo_material_route_declarations_keep_repo_identity_out_of_body() -> None:
    assert all(route.path.startswith("/admin/operator/") for route in REPO_MATERIAL_HTTP_ROUTES)
    create = next(
        route for route in REPO_MATERIAL_HTTP_ROUTES if route.api_method == "create_native_repo"
    )
    assert create.path == "/admin/operator/workspace/repos/{repo_key}"
    read = next(route for route in REPO_MATERIAL_HTTP_ROUTES if route.api_method == "inspect_repo")
    api = type("Api", (), {"inspect_repo": lambda self, repo_key: repo_key})()

    assert invoke_repo_material_route(api, read, repo_key="MainRepo") == "MainRepo"
    try:
        invoke_repo_material_route(api, read, repo_key="MainRepo", body={"repo_key": "forged"})
    except ValueError as exc:
        assert "do not accept" in str(exc)
    else:
        raise AssertionError("read route accepted an identity-bearing body")


def test_check_native_skeleton_projects_gate_report(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    make_repo(workspace)
    api = _api(workspace)

    result = api.check_native_skeleton("MainRepo")

    assert result.ok and result.value is not None, result.issues
    assert result.value.gate_name == "native_repo_skeleton"
    assert isinstance(result.value.passed, bool)
