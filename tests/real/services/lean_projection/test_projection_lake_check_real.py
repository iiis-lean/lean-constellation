from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.external_clients import (
    LakeCommandClient,
    LakeCommandClientConfig,
    LeanMcpToolkitClient,
)
from lean_constellation.services.foundation import FoundationService, ServiceResult
from lean_constellation.services.lean_projection import LeanCheckView, LeanProjectionService
from lean_constellation.services.node import DeclPublicView, ExportComponent, NodeTreeComponent


class MutablePublicDeclProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation
        self.decls: dict[str, list[DeclPublicView]] = {}

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        del repo_root
        return self.foundation.ok(self.decls.get(node_path, []))


class DictRevisionProvider:
    def __init__(self, foundation: FoundationService, revisions: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.foundation = foundation
        self.revisions = revisions

    def get_current_decl_revision(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[Any]:
        del repo_root
        revision = self.revisions.get((node_path, decl_name))
        if revision is None:
            return self.foundation.fail(
                self.foundation.issue("decl_revision_missing", "Decl revision missing.", object_ref=f"{node_path}:{decl_name}")
            )
        return self.foundation.ok(revision)

    def save_statement_formal_capture(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
    ) -> ServiceResult[Any]:
        del repo_root
        revision = self.revisions[(node_path, decl_name)]
        revision.setdefault("statement", {})["formal"] = {
            "code": code,
            "check": check.model_dump(mode="python"),
        }
        revision["state"] = "declared"
        return self.foundation.ok(revision)

    def save_proof_formal_capture(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
    ) -> ServiceResult[Any]:
        del repo_root
        revision = self.revisions[(node_path, decl_name)]
        revision.setdefault("proof", {})["formal"] = {
            "code": code,
            "check": check.model_dump(mode="python"),
        }
        revision["state"] = "proved"
        return self.foundation.ok(revision)


def _require_lake_and_lean() -> int:
    for command in ("lake", "lean"):
        if shutil.which(command) is None:
            pytest.skip(f"`{command}` is required for real LeanProjection Lake tests.")
    return 120


def _write_minimal_lake_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "lakefile.toml").write_text(
        'name = "ProjectionReal"\n'
        'version = "0.1.0"\n'
        'defaultTargets = ["Main"]\n\n'
        '[[lean_lib]]\n'
        'name = "Main"\n',
        encoding="utf-8",
    )
    (repo_root / "Main.lean").write_text(
        "import Main.Topic.Core.Prelude\n"
        "import Main.Topic.Core.Interfaces\n",
        encoding="utf-8",
    )


def _create_content_node(repo_root: Path) -> None:
    tree = make_runtime().node.node_tree
    assert tree.ensure_root_scope_node(repo_root).ok
    assert tree.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    assert tree.create_content_node(
        repo_root,
        path="Main.Topic.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build the real LeanProjection smoke theorem.",
        success_criteria="The statement and proof formal captures compile through Lake.",
    ).ok


def _revision() -> dict[str, Any]:
    return {
        "name": "main_result",
        "kind": "theorem",
        "state": "specified",
        "statement": {
            "nl": {
                "text": "The real LeanProjection smoke statement asserts True.",
                "origin": {"kind": "real_test"},
            },
            "deps": [],
        },
        "proof": {
            "nl": {
                "text": "The theorem follows by triviality.",
                "origin": {"kind": "real_test"},
            },
            "deps": [],
        },
    }


@pytest.mark.real
def test_lean_projection_real_lake_decl_file_projection_and_policy_gates(tmp_path: Path) -> None:
    timeout = _require_lake_and_lean()
    repo_root = tmp_path / "ProjectionReal"
    _write_minimal_lake_repo(repo_root)
    _create_content_node(repo_root)

    runtime = make_runtime(
        external_overrides={
            "lake": LakeCommandClient(LakeCommandClientConfig(timeout_seconds=timeout)),
            "lean_mcp_toolkit": LeanMcpToolkitClient(),
        }
    )
    foundation = runtime.foundation
    public_provider = MutablePublicDeclProvider(foundation)
    revisions = {("Main.Topic.Core", "main_result"): _revision()}
    export = ExportComponent(runtime, public_decl_provider=public_provider)
    service = LeanProjectionService(
        runtime,
        decl_revision_provider=DictRevisionProvider(foundation, revisions),
    )
    service.node_projection.export = export

    prelude = service.node_projection.refresh_prelude(repo_root, node_path="Main.Topic.Core")
    assert prelude.ok, prelude.issues
    assert prelude.value is not None
    assert Path(prelude.value.path).exists()
    assert service.node_projection.check_prelude_sync(repo_root, node_path="Main.Topic.Core").value.passed is True  # type: ignore[union-attr]

    empty_interfaces = service.node_projection.refresh_interfaces(repo_root, node_path="Main.Topic.Core")
    assert empty_interfaces.ok, empty_interfaces.issues
    assert empty_interfaces.value is not None
    assert service.node_projection.check_interfaces_sync(repo_root, node_path="Main.Topic.Core").value.passed is True  # type: ignore[union-attr]

    initial_build = runtime.external.lake.run_lake_build(repo_root, timeout_seconds=timeout)
    assert initial_build.ok, initial_build.summary

    prepared_statement = service.prepare_statement_formal_stage_file(
        repo_root,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    )
    assert prepared_statement.ok, prepared_statement.issues
    assert prepared_statement.value is not None
    decl_path = Path(prepared_statement.value.path)
    statement_text = decl_path.read_text(encoding="utf-8")
    assert "lean-constellation target: main_result" in statement_text
    assert "stage: statement" in statement_text
    assert "import Main.Topic.Core.Prelude" in statement_text
    assert "sorry" in statement_text

    captured_statement = service.capture_statement_formal(
        repo_root,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    )
    assert captured_statement.ok, captured_statement.issues
    assert captured_statement.value is not None
    assert captured_statement.value.check.policy == "statement_formal"
    assert captured_statement.value.check.status == "passed"
    assert captured_statement.value.check.allow_sorry is True
    assert revisions[("Main.Topic.Core", "main_result")]["state"] == "declared"

    prepared_proof = service.prepare_proof_formal_stage_file(
        repo_root,
        node_path="Main.Topic.Core",
        decl_name="main_result",
    )
    assert prepared_proof.ok, prepared_proof.issues
    assert prepared_proof.value is not None
    proof_with_sorry = service.capture_proof_formal(repo_root, node_path="Main.Topic.Core", decl_name="main_result")
    assert not proof_with_sorry.ok
    assert proof_with_sorry.issues[0].kind == "proof_lean_check_failed"

    decl_path.write_text(decl_path.read_text(encoding="utf-8").replace("sorry", "trivial"), encoding="utf-8")
    captured_proof = service.capture_proof_formal(repo_root, node_path="Main.Topic.Core", decl_name="main_result")
    assert captured_proof.ok, captured_proof.issues
    assert captured_proof.value is not None
    assert captured_proof.value.check.policy == "proof_formal"
    assert captured_proof.value.check.status == "passed"
    assert captured_proof.value.check.allow_sorry is False
    assert revisions[("Main.Topic.Core", "main_result")]["state"] == "proved"

    decl_path.write_text(decl_path.read_text(encoding="utf-8") + "\naxiom forbidden_axiom : False\n", encoding="utf-8")
    proof_with_axiom = service.capture_proof_formal(repo_root, node_path="Main.Topic.Core", decl_name="main_result")
    assert not proof_with_axiom.ok
    assert proof_with_axiom.issues[0].kind == "proof_lean_check_failed"

    restored = service.decl_file.sync_decl_file_after_revision_reset(repo_root, node_path="Main.Topic.Core", decl_name="main_result")
    assert restored.ok, restored.issues
    assert "forbidden_axiom" not in decl_path.read_text(encoding="utf-8")
    sync_gate = service.decl_file.check_decl_file_snapshot_sync(
        repo_root,
        node_path="Main.Topic.Core",
        decl_name="main_result",
        stage="proof",
    )
    assert sync_gate.ok
    assert sync_gate.value is not None
    assert sync_gate.value.passed is True

    public_provider.decls["Main.Topic.Core"] = [
        DeclPublicView(
            ref=DeclRef(repo=None, node="Main.Topic.Core", name="main_result", revision=1),
            kind="theorem",
            summary="Real LeanProjection smoke theorem.",
        )
    ]
    interfaces = service.node_projection.refresh_interfaces(repo_root, node_path="Main.Topic.Core")
    assert interfaces.ok, interfaces.issues
    assert interfaces.value is not None
    interfaces_text = Path(interfaces.value.path).read_text(encoding="utf-8")
    assert "import Main.Topic.Core.Theorems.main_result" in interfaces_text
    assert service.node_projection.check_interfaces_sync(repo_root, node_path="Main.Topic.Core").value.passed is True  # type: ignore[union-attr]

    final_build = runtime.external.lake.run_lake_build(repo_root, timeout_seconds=timeout)
    assert final_build.ok, final_build.summary

    bad_file = repo_root / "Main" / "Topic" / "Core" / "BadDiagnostics.lean"
    bad_file.write_text("def badNat : Nat := true\n", encoding="utf-8")
    diagnostics = service.lean_check.run_file_diagnostics(repo_root, file_path=bad_file)
    assert diagnostics.ok, diagnostics.issues
    assert diagnostics.value is not None
    assert diagnostics.value.passed is False
    assert diagnostics.value.diagnostics
    assert diagnostics.value.raw_excerpt
