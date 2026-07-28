from __future__ import annotations

import json
import subprocess
from pathlib import Path

from starlette.applications import Starlette
from starlette.testclient import TestClient

from lean_constellation.app.operator_data.api import OperatorDataApi
from lean_constellation.app.operator_data.http import create_operator_data_http_routes
from lean_constellation.app.operator_data.decl_projection import (
    DeclCreateInput,
    DeclFileReadInput,
    DeclIdentityInput,
    FormalApplyInput,
    NaturalLanguageInput,
    NodeInput,
    ProjectionSyncInput,
    RoundCloseoutInput,
    RoundExecutionInput,
    RoundIdentityInput,
    RoundInput,
    StageGateInput,
    StrategyCloseInput,
    StrategyInput,
)
from lean_constellation.app.operator_data.node import (
    AddMaterialRefInput,
    AddScopeExportInput,
    CommitContractInput,
    CreateContentNodeInput,
    CreateScopeNodeInput,
    NodePathInput,
    SyncRootInterfacesInput,
)
from lean_constellation.app.operator_data.release import (
    CheckpointListInput,
    ReleaseCandidateInput,
)
from lean_constellation.app.operator_data.repo_material import (
    NativeRepoCreateInput,
    SourceCorpusLocalDirInput,
    SourceIndexBlockCreateInput,
    SourceIndexBlockLifecycleInput,
    SourceIndexBlockRefAddInput,
    SourceIndexCommitInput,
    SourceIndexFileIndexingInput,
    SourceIndexFileSurveyInput,
    SourceIndexOpenInput,
    SourceIndexOverviewInput,
)
from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.lake_project import NativeLakeProjectConfig
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import RepoCompletionMode
from lean_constellation.domain.repo_run import SourceScope
from lean_constellation.services.decl_graph import DeclState, RoundStageReview
from lean_constellation.services.external_clients import (
    ExternalCommandResult,
    LeanCheckSummaryView,
    LeanDiagnosticsResult,
)

from tests.unit.app.operator_data._helpers import make_registry


REPO_KEY = "SyntheticDeclaredRepo"
NODE_PATH = "Main.Core"


class _DeterministicToolkit:
    def run_file_diagnostics(self, repo_root: Path, file_path: Path) -> LeanDiagnosticsResult:
        return LeanDiagnosticsResult(
            ok=True,
            repo_root=str(repo_root),
            file_path=str(file_path),
            diagnostics=[],
            summary="Deterministic synthetic diagnostics passed.",
        )


class _DeterministicLake:
    def run_lake_env_lean(
        self,
        *,
        repo_root: Path,
        rel_file: str,
        json: bool = True,
        timeout_seconds: int | None = None,
    ) -> ExternalCommandResult:
        del json, timeout_seconds
        return ExternalCommandResult(
            ok=True,
            command=["lake", "env", "lean", "--json", rel_file],
            cwd=str(repo_root),
            exit_code=0,
            summary="Deterministic synthetic Lean check passed.",
        )

    def run_lake_build(
        self,
        repo_root: Path,
        target: str | None = None,
        targets: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> ExternalCommandResult:
        del target, targets, timeout_seconds
        return ExternalCommandResult(
            ok=True,
            command=["lake", "build"],
            cwd=str(repo_root),
            exit_code=0,
            summary="Deterministic synthetic Lake build passed.",
        )

    def run_snippet_check(
        self,
        *,
        repo_root: Path,
        imports: list[str],
        code: str,
        timeout_seconds: int | None = None,
    ) -> LeanCheckSummaryView:
        del repo_root, timeout_seconds
        return LeanCheckSummaryView(
            ok=True,
            command=["lake", "env", "lean"],
            summary=f"Deterministically confirmed {code} from {imports[0]}.",
        )


def _require(result):  # noqa: ANN001, ANN202
    assert result.ok and result.value is not None, result.issues
    return result.value


def _assert_public_output(payload, *, workspace: Path) -> None:  # noqa: ANN001
    def normalize(item):  # noqa: ANN001, ANN202
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        if isinstance(item, list):
            return [normalize(nested) for nested in item]
        if isinstance(item, dict):
            return {key: normalize(nested) for key, nested in item.items()}
        return item

    value = normalize(payload)
    encoded = json.dumps(value, sort_keys=True)
    assert str(workspace) not in encoded

    def walk(item):  # noqa: ANN001, ANN202
        if isinstance(item, dict):
            assert not {
                "repo_root",
                "workspace_root",
                "file_path",
                "resource_root",
                "archive_path",
                "lakefile_path",
                "manifest_path",
            }.intersection(item)
            for nested in item.values():
                walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)

    walk(value)


def _write_source_corpus(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "README.md").write_text(
        "# Synthetic Finite Arithmetic\n\n"
        "## Provenance\n\n"
        "This self-contained fixture was written for deterministic validation.\n\n"
        "## Reading order\n\n"
        "Read this file, then `core.md`.\n\n"
        "## Main material\n\n"
        "The file `core.md` gives one finite definition and one elementary theorem.\n\n"
        "## Known gaps and extraction limits\n\n"
        "The proof is intentionally omitted from the mathematical source.\n",
        encoding="utf-8",
    )
    (root / "core.md").write_text(
        "# Core statement\n\n"
        "Define the distinguished natural number to be seven.\n\n"
        "The principal proposition states that truth holds.\n",
        encoding="utf-8",
    )


def _index_source(api: OperatorDataApi) -> None:
    material = api.repo_material
    missing = material.workspace_runtime.material.source_index.missing_source_index_digest()
    opened = _require(
        material.open_source_index_update(
            REPO_KEY,
            SourceIndexOpenInput(
                source_scope=SourceScope(mode="selected", selectors=["core.md"]),
                expected_baseline_digest=missing,
            ),
        )
    )
    digest = opened.current_index_digest
    digest = _require(
        material.set_source_index_overview(
            REPO_KEY,
            SourceIndexOverviewInput(
                expected_current_digest=digest,
                overview="Index the finite definition and proposition.",
            ),
        )
    ).current_index_digest
    block = _require(
        material.create_source_block(
            REPO_KEY,
            SourceIndexBlockCreateInput(
                expected_current_digest=digest,
                parent_id="root",
                kind="theorem",
                title="Synthetic principal proposition",
                summary="The source's elementary public proposition.",
            ),
        )
    )
    digest = block.current_index_digest
    block_id = block.value.block_id
    ref = _require(
        material.add_source_block_ref(
            REPO_KEY,
            SourceIndexBlockRefAddInput(
                expected_current_digest=digest,
                block_id=block_id,
                path="core.md",
                start_line=1,
                end_line=5,
                role="primary statement",
            ),
        )
    )
    digest = ref.current_index_digest
    digest = _require(
        material.mark_source_block_refs_done(
            REPO_KEY,
            SourceIndexBlockLifecycleInput(
                expected_current_digest=digest,
                block_id=block_id,
            ),
        )
    ).current_index_digest
    digest = _require(
        material.mark_source_block_links_done(
            REPO_KEY,
            SourceIndexBlockLifecycleInput(
                expected_current_digest=digest,
                block_id=block_id,
            ),
        )
    ).current_index_digest
    digest = _require(
        material.mark_source_block_completed(
            REPO_KEY,
            SourceIndexBlockLifecycleInput(
                expected_current_digest=digest,
                block_id=block_id,
            ),
        )
    ).current_index_digest
    digest = _require(
        material.set_source_file_survey(
            REPO_KEY,
            SourceIndexFileSurveyInput(
                expected_current_digest=digest,
                path="core.md",
                status="surveyed",
                summary="Definition and theorem located.",
            ),
        )
    ).current_index_digest
    digest = _require(
        material.set_source_file_indexing(
            REPO_KEY,
            SourceIndexFileIndexingInput(
                expected_current_digest=digest,
                path="core.md",
                status="indexed",
            ),
        )
    ).current_index_digest
    committed = _require(
        material.validate_and_commit_source_index(
            REPO_KEY,
            SourceIndexCommitInput(expected_current_digest=digest),
        )
    )
    assert committed.newly_committed_file_paths == ["core.md"]


def _passed_review(round_id: str, stage: str, names: list[str]) -> RoundStageReview:
    return RoundStageReview(
        outcome="passed",
        round_id=round_id,
        node_path=NODE_PATH,
        stage=stage,
        reviewed_decl_names=names,
        summary=f"Accepted {stage}.",
    )


def test_operator_constructs_publishes_and_restores_synthetic_declared_repo(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "source"
    _write_source_corpus(source)
    registry = make_registry(
        workspace,
        external_overrides={
            "lean_mcp_toolkit": _DeterministicToolkit(),
            "lake": _DeterministicLake(),
        },
    )
    api = OperatorDataApi(registry)
    interface = DeclInterface(
        name="synthetic_result",
        kind=DeclKind.THEOREM,
        summary="The elementary public proposition.",
    )
    created = _require(
        api.create_native_repo(
            REPO_KEY,
            NativeRepoCreateInput(
                project_name="SyntheticDeclared",
                preparation_input=RepoPreparationInput(
                    goal="Publish a minimal declared finite-arithmetic interface.",
                    source_corpus_mode=SourceCorpusMode.PREPARE,
                    interface_inputs=[interface],
                ),
                completion_mode=RepoCompletionMode.INTERFACE_DECLARED,
                native_config=NativeLakeProjectConfig(mathlib_enabled=False),
            ),
        )
    )
    repo_root = workspace / created.repo_key
    _assert_public_output(created, workspace=workspace)
    assert not (repo_root / ".agent_runtime").exists()

    _require(
        api.repo_material.import_local_source_corpus(
            REPO_KEY,
            SourceCorpusLocalDirInput(
                source_dir=source,
                entry_path="README.md",
                overview="A minimal finite-arithmetic source corpus.",
                preparation_summary="Synthetic source corpus prepared.",
            ),
        )
    )
    _index_source(api)

    _require(
        api.node.create_scope_node(
            REPO_KEY,
            CreateScopeNodeInput(
                path="Main",
                goal="Expose the declared public proposition.",
                boundary="One content node and one root export.",
                objective="Organize the declared interface.",
                success_criteria="The public theorem is bound and exported.",
            ),
        )
    )
    _require(
        api.node.sync_root_interfaces(
            REPO_KEY,
            SyncRootInterfacesInput(expected_contract_version=1),
        )
    )
    _require(
        api.node.create_content_node(
            REPO_KEY,
            CreateContentNodeInput(
                path=NODE_PATH,
                goal="Formalize the finite definition and proposition.",
                boundary="Only the two synthetic declarations.",
                objective="Create declared Lean statements.",
                success_criteria="Both declarations reach declared state.",
                expected_parent_contract_version=1,
            ),
        )
    )
    _require(
        api.node.add_material_ref(
            REPO_KEY,
            AddMaterialRefInput(
                node_path=NODE_PATH,
                expected_contract_version=1,
                ref_scope="owned",
                material_kind="source",
                locator="core.md",
                start_line=1,
                end_line=5,
                reason="Primary mathematical statement.",
            ),
        )
    )

    strategy = _require(
        api.decl_projection.ensure_strategy(
            REPO_KEY,
            StrategyInput(
                node_path=NODE_PATH,
                objective="Declare the synthetic interface.",
            ),
        )
    )
    round_view = _require(
        api.decl_projection.create_round(
            REPO_KEY,
            RoundInput(
                node_path=NODE_PATH,
                strategy_id=strategy.strategy_id,
                objective="Create one definition and one theorem.",
            ),
        )
    )
    round_id = round_view.round_id
    declarations = [
        ("synthetic_value", "definition", False, "The distinguished natural number is seven."),
        ("synthetic_result", "theorem", True, "The public proposition states that truth holds."),
    ]
    for name, kind, public, _ in declarations:
        _require(
            api.decl_projection.create_decl(
                REPO_KEY,
                DeclCreateInput(
                    node_path=NODE_PATH,
                    round_id=round_id,
                    name=name,
                    kind=kind,
                    objective=f"Declare {name}.",
                    summary=f"Synthetic declaration {name}.",
                    public=public,
                    target_state=DeclState.DECLARED,
                ),
            )
        )
    _require(
        api.decl_projection.start_round(
            REPO_KEY,
            RoundIdentityInput(node_path=NODE_PATH, round_id=round_id),
        )
    )
    names = [item[0] for item in declarations]
    for name, _, _, statement in declarations:
        _require(
            api.decl_projection.write_statement_nl(
                REPO_KEY,
                NaturalLanguageInput(
                    node_path=NODE_PATH,
                    round_id=round_id,
                    decl_name=name,
                    expected_revision=1,
                    text=statement,
                ),
            )
        )
    nl_gate = _require(
        api.decl_projection.gate_and_advance_stage(
            REPO_KEY,
            StageGateInput(
                node_path=NODE_PATH,
                round_id=round_id,
                stage="statement_nl",
                target_decl_names=names,
                review=_passed_review(round_id, "statement_nl", names),
            ),
        )
    )
    assert nl_gate.outcome == "stage_passed"

    http_app = Starlette(routes=create_operator_data_http_routes(registry, api=api))
    with TestClient(http_app) as client:
        prepared_http = client.post(
            f"/admin/operator/repos/{REPO_KEY}/projection/statement/prepare",
            json={"node_path": NODE_PATH, "decl_name": "synthetic_result"},
        )
    assert prepared_http.status_code == 200
    http_value = prepared_http.json()["value"]
    assert "# lean-constellation target: `synthetic_result`" in http_value["content"]
    assert "path" not in http_value
    assert "repo_root" not in http_value

    for name in names:
        prepared_file = _require(
            api.decl_projection.prepare_statement_formal_file(
                REPO_KEY,
                DeclIdentityInput(node_path=NODE_PATH, decl_name=name),
            )
        )
        assert "lean-constellation target:" in prepared_file.content
        assert "path" not in prepared_file.model_dump(mode="json")
        assert "repo_root" not in prepared_file.model_dump(mode="json")
        lean_code = prepared_file.content.rstrip() + "\n\n"
        if name == "synthetic_value":
            lean_code += "def synthetic_value : Nat := 7\n"
        else:
            lean_code += "theorem synthetic_result : True := by\n  sorry\n"
        digest = _require(
            api.decl_projection.revision_digest(
                REPO_KEY,
                DeclIdentityInput(node_path=NODE_PATH, decl_name=name),
            )
        )
        applied = _require(
            api.decl_projection.apply_statement_formal_code(
                REPO_KEY,
                FormalApplyInput(
                    node_path=NODE_PATH,
                    round_id=round_id,
                    decl_name=name,
                    expected_revision=1,
                    expected_state=DeclState.SPECIFIED,
                    expected_revision_digest=digest,
                    lean_code=lean_code,
                ),
            )
        )
        kind_dir = "Defs" if name == "synthetic_value" else "Theorems"
        assert applied.module == f"SyntheticDeclared.Main.Core.{kind_dir}.{name}"
        assert applied.lean_decl_name == name
        assert applied.build.target == f"+{applied.module}"
        _assert_public_output(applied, workspace=workspace)
        owned_file = _require(
            api.decl_projection.read_decl_lean_file(
                REPO_KEY,
                DeclFileReadInput(node_path=NODE_PATH, decl_name=name),
            )
        )
        assert owned_file.module == applied.module
        assert owned_file.lean_decl_name == name
        assert owned_file.source == "physical_current"
        assert owned_file.content == lean_code.rstrip() + "\n"
        _assert_public_output(owned_file, workspace=workspace)
    formal_gate = _require(
        api.decl_projection.gate_and_advance_stage(
            REPO_KEY,
            StageGateInput(
                node_path=NODE_PATH,
                round_id=round_id,
                stage="statement_formal",
                target_decl_names=names,
                review=_passed_review(round_id, "statement_formal", names),
            ),
        )
    )
    assert formal_gate.outcome == "stage_passed"
    assert _require(
        api.decl_projection.audit_round_final(
            REPO_KEY,
            RoundIdentityInput(node_path=NODE_PATH, round_id=round_id),
        )
    ).passed
    runtime = registry.workspace_runtime()
    persisted_round = runtime.decl_graph.get_round(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
    )
    assert persisted_round.ok and persisted_round.value is not None
    for ref in persisted_round.value.revision_refs:
        assert runtime.decl_graph.write_decl_change_summary(
            repo_root,
            node_path=NODE_PATH,
            round_id=round_id,
            change_id=ref.change_id,
            summary=f"Declared {ref.decl_name}.",
        ).ok
    assert runtime.decl_graph.write_round_summary(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        summary="Declared the synthetic public interface.",
    ).ok
    _require(
        api.decl_projection.record_round_execution(
            REPO_KEY,
            RoundExecutionInput(
                node_path=NODE_PATH,
                round_id=round_id,
                outcome="completed",
            ),
        )
    )
    closeout = _require(
        api.decl_projection.closeout_round(
            REPO_KEY,
            RoundCloseoutInput(
                node_path=NODE_PATH,
                round_id=round_id,
                result_kind="success",
                acknowledged_by="test-content-plan",
            ),
        )
    )
    assert sorted(item.decl_name for item in closeout.committed_revision_refs) == sorted(names)
    _require(
        api.decl_projection.close_strategy(
            REPO_KEY,
            StrategyCloseInput(
                node_path=NODE_PATH,
                strategy_id=strategy.strategy_id,
                summary="Synthetic declaration strategy completed.",
            ),
        )
    )
    _require(
        api.node.commit_content_contract(
            REPO_KEY,
            CommitContractInput(
                node_path=NODE_PATH,
                expected_contract_version=1,
                summary="Synthetic declarations completed.",
            ),
        )
    )
    _require(
        api.node.add_scope_export(
            REPO_KEY,
            AddScopeExportInput(
                scope_path="Main",
                expected_contract_version=1,
                decl_node=NODE_PATH,
                decl_name="synthetic_result",
                bind_interface_name="synthetic_result",
            ),
        )
    )
    _require(
        api.node.commit_scope_contract(
            REPO_KEY,
            CommitContractInput(
                node_path="Main",
                expected_contract_version=1,
                summary="Public interface exported.",
            ),
        )
    )
    _require(
        api.decl_projection.refresh_node_projection(
            REPO_KEY,
            NodeInput(node_path="Main"),
        )
    )

    ready = _require(api.release_checkpoint.get_repo_ready_view(REPO_KEY))
    audit = _require(api.release_checkpoint.run_full_audit(REPO_KEY))
    assert ready.ready_to_submit and ready.gate.passed
    assert audit.passed
    preview = _require(
        api.release_checkpoint.preview_repo_release(
            REPO_KEY,
            ReleaseCandidateInput(summary="Synthetic declared release."),
        )
    )
    assert preview.gate.passed
    published = _require(
        api.release_checkpoint.publish_repo_release(
            REPO_KEY,
            ReleaseCandidateInput(summary="Synthetic declared release."),
        )
    )
    assert published.outcome == "published" and published.finalized is not None
    _assert_public_output(ready, workspace=workspace)
    _assert_public_output(audit, workspace=workspace)
    _assert_public_output(preview, workspace=workspace)
    _assert_public_output(published, workspace=workspace)
    release_id = published.finalized.release.release.release_id
    availability = _require(api.repo_material.check_provider_availability(REPO_KEY))
    assert availability.passed
    checkpoints = _require(
        api.release_checkpoint.list_checkpoints(REPO_KEY, CheckpointListInput())
    )
    assert published.finalized.checkpoint.snapshot_id in {
        checkpoint.snapshot_id for checkpoint in checkpoints
    }
    _assert_public_output(checkpoints, workspace=workspace)

    with TestClient(http_app) as client:
        inspected_http = client.get(f"/admin/operator/repos/{REPO_KEY}")
        lake_http = client.post(
            f"/admin/operator/repos/{REPO_KEY}/lake-build",
            json={},
        )
        release_http = client.post(
            f"/admin/operator/repos/{REPO_KEY}/releases/get",
            json={"release_id": release_id},
        )
        checkpoint_http = client.post(
            f"/admin/operator/repos/{REPO_KEY}/checkpoints/validate",
            json={"snapshot_id": published.finalized.checkpoint.snapshot_id},
        )
    for response in (inspected_http, lake_http, release_http, checkpoint_http):
        assert response.status_code == 200, response.text
        _assert_public_output(response.json(), workspace=workspace)

    before_restore = _require(
        api.decl_projection.check_projection_sync(
            REPO_KEY,
            ProjectionSyncInput(
                node_path=NODE_PATH,
                decl_name="synthetic_result",
                stage="statement",
            ),
        )
    )
    assert before_restore.passed
    lakefile = repo_root / "lakefile.toml"
    released_lakefile = lakefile.read_text(encoding="utf-8")
    lakefile.write_text(released_lakefile + "\n# post-release drift\n", encoding="utf-8")
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Lean Constellation Test",
            "-c",
            "user.email=lean-constellation@example.invalid",
            "add",
            "-A",
        ],
        cwd=repo_root,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Lean Constellation Test",
            "-c",
            "user.email=lean-constellation@example.invalid",
            "commit",
            "-m",
            "test: add post-release drift",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert (
        subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )
    restore_preview = _require(
        runtime.validation_snapshot.preview_repo_release_restore(
            repo_root,
            release_id=release_id,
        )
    )
    restored = _require(
        runtime.validation_snapshot.apply_repo_release_restore(
            repo_root,
            preview=restore_preview,
            expected_recovery_token=restore_preview.recovery_token,
        )
    )
    assert restored.dry_run is False
    assert lakefile.read_text(encoding="utf-8") == released_lakefile
    after_restore = _require(
        api.decl_projection.check_projection_sync(
            REPO_KEY,
            ProjectionSyncInput(
                node_path=NODE_PATH,
                decl_name="synthetic_result",
                stage="statement",
            ),
        )
    )
    assert after_restore.passed
    assert _require(api.node.list_interfaces(REPO_KEY, NodePathInput(node_path="Main")))
    assert not (repo_root / ".agent_runtime").exists()
