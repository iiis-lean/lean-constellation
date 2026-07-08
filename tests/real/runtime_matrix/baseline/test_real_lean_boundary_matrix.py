from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.external_clients import LakeCommandClient, LakeCommandClientConfig, LeanMcpToolkitClient
from tests.unit_services_helpers import make_runtime


pytestmark = [pytest.mark.real, pytest.mark.slow]

NODE_PATH = "Main.Topic.Core"
DECL_NAME = "main_result"


def test_real_lean_lake_projection_capture_and_policy_matrix(tmp_path: Path) -> None:
    _require_lake_and_lean()
    repo_root = tmp_path / "RuntimeMatrixLeanReal"
    _write_real_lake_repo(repo_root)
    runtime = make_runtime(
        external_overrides={
            "lake": LakeCommandClient(LakeCommandClientConfig(timeout_seconds=120)),
            "lean_mcp_toolkit": LeanMcpToolkitClient(),
        }
    )
    round_id = _setup_decl_round(runtime, repo_root)

    build = runtime.external.lean_toolchain.run_lake_build(repo_root, timeout_seconds=120)
    assert build.ok, build.summary
    lean_json = runtime.external.lake.run_lake_env_lean(
        repo_root=repo_root,
        rel_file="Main/Topic/Core/Prelude.lean",
        json=True,
        timeout_seconds=60,
    )
    assert lean_json.ok, lean_json.summary
    snippet_ok = runtime.external.lean_toolchain.run_snippet_check(
        repo_root,
        code="#check Nat",
        imports=["Main"],
        timeout_seconds=60,
    )
    assert snippet_ok.ok, snippet_ok.summary
    snippet_fail = runtime.external.lean_toolchain.run_snippet_check(
        repo_root,
        code="#check (true : Nat)",
        imports=["Main"],
        timeout_seconds=60,
    )
    assert snippet_fail.ok is False

    refreshed = runtime.lean_projection.refresh_node_projection(repo_root, node_path=NODE_PATH)
    assert refreshed.ok, refreshed.issues
    prepared_statement = runtime.lean_projection.prepare_statement_formal_stage_file(
        repo_root,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
    )
    assert prepared_statement.ok, prepared_statement.issues
    statement_capture = runtime.lean_projection.capture_statement_formal(
        repo_root,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
    )
    assert statement_capture.ok, statement_capture.issues
    revision = _current_revision(runtime, repo_root)
    assert revision.state == DeclState.PLANNED
    assert revision.statement_lean_check is not None
    assert revision.statement_lean_check["policy"] == "statement_formal"
    assert revision.statement_lean_check["status"] == "passed"

    proof_nl = runtime.decl_graph.write_proof_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=DECL_NAME,
        nl="Use triviality.",
    )
    assert proof_nl.ok, proof_nl.issues
    prepared_proof = runtime.lean_projection.prepare_proof_formal_stage_file(
        repo_root,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
    )
    assert prepared_proof.ok and prepared_proof.value is not None, prepared_proof.issues
    decl_path = Path(prepared_proof.value.path)

    sorry_failure = runtime.lean_projection.capture_proof_formal(
        repo_root,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
    )
    assert not sorry_failure.ok
    assert "contains_sorry" in sorry_failure.issues[0].message

    decl_path.write_text(decl_path.read_text(encoding="utf-8").replace("sorry", "trivial"), encoding="utf-8")
    proof_capture = runtime.lean_projection.capture_proof_formal(
        repo_root,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
    )
    assert proof_capture.ok, proof_capture.issues
    revision = _current_revision(runtime, repo_root)
    assert revision.state == DeclState.PLANNED
    assert revision.proof_lean_check is not None
    assert revision.proof_lean_check["policy"] == "proof_formal"
    assert revision.proof_lean_check["status"] == "passed"

    decl_path.write_text(decl_path.read_text(encoding="utf-8") + "\naxiom forbidden_axiom : False\n", encoding="utf-8")
    axiom_failure = runtime.lean_projection.capture_proof_formal(
        repo_root,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
    )
    assert not axiom_failure.ok
    assert "contains_axiom" in axiom_failure.issues[0].message

    restored = runtime.lean_projection.sync_decl_file_after_revision_reset(
        repo_root,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
    )
    assert restored.ok, restored.issues
    decl_path.write_text(decl_path.read_text(encoding="utf-8") + "\ndef forbidden_admit : True := by admit\n", encoding="utf-8")
    admit_failure = runtime.lean_projection.capture_proof_formal(
        repo_root,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
    )
    assert not admit_failure.ok
    assert "contains_admit" in admit_failure.issues[0].message

    restored = runtime.lean_projection.sync_decl_file_after_revision_reset(
        repo_root,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
    )
    assert restored.ok, restored.issues
    sync_gate = runtime.lean_projection.check_decl_file_snapshot_sync(
        repo_root,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
        stage="proof",
    )
    assert sync_gate.ok and sync_gate.value is not None
    assert sync_gate.value.passed is True
    reset = runtime.lean_projection.restore_projection_to_active_graph(repo_root, node_path=NODE_PATH)
    assert reset.ok, reset.issues
    assert runtime.external.lean_toolchain.run_lake_build(repo_root, timeout_seconds=120).ok


def _require_lake_and_lean() -> None:
    for command in ("lake", "lean"):
        if shutil.which(command) is None:
            pytest.skip(f"`{command}` is required for Runtime Matrix real Lean boundary tests.")


def _write_real_lake_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "lakefile.toml").write_text(
        'name = "RuntimeMatrixLeanReal"\n'
        'version = "0.1.0"\n'
        'defaultTargets = ["Main"]\n\n'
        "[[lean_lib]]\n"
        'name = "Main"\n',
        encoding="utf-8",
    )
    (repo_root / "Main.lean").write_text(
        "import Main.Topic.Core.Prelude\n"
        "import Main.Topic.Core.Interfaces\n",
        encoding="utf-8",
    )
    prelude = repo_root / "Main" / "Topic" / "Core" / "Prelude.lean"
    prelude.parent.mkdir(parents=True, exist_ok=True)
    prelude.write_text(
        "namespace Main.Topic.Core\n\n"
        "def seedNat : Nat := 1\n\n"
        "theorem seedTrue : True := by\n"
        "  trivial\n\n"
        "end Main.Topic.Core\n",
        encoding="utf-8",
    )
    interfaces = repo_root / "Main" / "Topic" / "Core" / "Interfaces.lean"
    interfaces.write_text(
        "import Main.Topic.Core.Prelude\n\n"
        "namespace Main.Topic.Core\n\n"
        "theorem interfaceTrue : True := by\n"
        "  trivial\n\n"
        "end Main.Topic.Core\n",
        encoding="utf-8",
    )


def _setup_decl_round(runtime, repo_root: Path) -> str:
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    content = runtime.node.create_content_node(
        repo_root,
        path=NODE_PATH,
        goal="Core goal.",
        boundary="Core declarations only.",
        objective="Run real Runtime Matrix Decl-owned file capture/check.",
        success_criteria="Statement and proof captures are checked by real Lake diagnostics.",
    )
    assert content.ok, content.issues
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=NODE_PATH, objective="Real capture strategy.")
    assert strategy.ok and strategy.value is not None, strategy.issues
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Real capture round.",
    )
    assert round_record.ok and round_record.value is not None, round_record.issues
    created = runtime.decl_graph.create_decl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        name=DECL_NAME,
        kind="theorem",
        objective="Create a trivial theorem.",
        summary="A trivial theorem for real Runtime Matrix capture.",
        public=False,
        end_after_state=DeclState.PROVED,
    )
    assert created.ok, created.issues
    started = runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_record.value.round_id)
    assert started.ok, started.issues
    statement = runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        decl_name=DECL_NAME,
        nl="The main result states True.",
    )
    assert statement.ok, statement.issues
    return round_record.value.round_id


def _current_revision(runtime, repo_root: Path):
    revision = runtime.decl_graph.get_decl_revision(repo_root, node_path=NODE_PATH, name=DECL_NAME, revision=1)
    assert revision.ok and revision.value is not None, revision.issues
    return revision.value
