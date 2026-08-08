from __future__ import annotations

from pathlib import Path
import pytest

from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import ProofAvailability, RepoFormat
from lean_constellation.services.decl_graph import DeclState, RepoDeclDep
from lean_constellation.services.external_clients import LeanCheckSummaryView, LeanMcpToolkitClient
from tests.unit.flows.decl_round._helpers import (
    NODE_PATH,
    commit_content_contract_head,
    create_round_with_decl,
    make_decl_round_runtime,
    seed_committed_theorem,
)
from tests.unit_services_helpers import (
    CleanDeclarationSoundnessDispatcher,
    lean_check_payload,
    publish_adapter_provider_release,
    set_current_decl_lean_name_for_test,
    write_proof_formal_for_test,
    write_statement_formal_for_test,
)


class _AdapterIdentityLake:
    def __init__(self, delegate: object | None = None) -> None:
        self.delegate = delegate

    def __getattr__(self, name: str):  # noqa: ANN204
        if self.delegate is None:
            raise AttributeError(name)
        return getattr(self.delegate, name)

    def run_snippet_check(
        self,
        *,
        repo_root: Path,
        imports: list[str],
        code: str,
        timeout_seconds: int | None = None,
    ) -> LeanCheckSummaryView:
        del repo_root, imports, code, timeout_seconds
        return LeanCheckSummaryView(ok=True, command=["lean"], summary="registered declaration identity confirmed")


def test_same_round_new_decl_dependency_uses_exact_round_revision(tmp_path: Path) -> None:
    _flow_runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    _strategy_id, round_id, _round_index = create_round_with_decl(lean_runtime, repo_root, decl_name="A")
    created = lean_runtime.decl_graph.create_decl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        name="B",
        kind="theorem",
        objective="Create B.",
        summary="B summary.",
        target_state=DeclState.PROVED,
    )
    assert created.ok, created.issues
    assert lean_runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id).ok
    _write_proved_round_theorem(lean_runtime, repo_root, round_id=round_id, decl_name="B")
    _write_proved_round_theorem(
        lean_runtime,
        repo_root,
        round_id=round_id,
        decl_name="A",
        proof_ref=DeclRef(node=NODE_PATH, name="B", revision=1),
    )

    satisfied, reason = _check_round_decl(lean_runtime, repo_root, round_id=round_id, decl_name="A")

    assert satisfied is True, reason
    assert reason is None


def test_same_round_update_historical_anchor_uses_semantic_resolver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _flow_runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    seed_committed_theorem(lean_runtime, repo_root, decl_name="B")
    commit_content_contract_head(lean_runtime, repo_root, decl_graph_head={"B": 1})
    _strategy_id, round_id, _round_index = create_round_with_decl(lean_runtime, repo_root, decl_name="A")
    updated = lean_runtime.decl_graph.open_decl_update(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        name="B",
        objective="Keep B proved.",
        reset_to_state=DeclState.PROOF_PLANNED,
        target_state=DeclState.PROVED,
    )
    assert updated.ok, updated.issues
    assert lean_runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id).ok
    assert write_proof_formal_for_test(
        lean_runtime,
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="B",
        lean_code="theorem B : True := by trivial",
        lean_check=lean_check_payload(),
    ).ok
    assert lean_runtime.decl_graph.advance_stage_state(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        stage="proof_formal",
        decl_names=["B"],
    ).ok
    assert lean_runtime.decl_graph.commit_decl_revision(repo_root, node_path=NODE_PATH, name="B").ok
    _write_proved_round_theorem(
        lean_runtime,
        repo_root,
        round_id=round_id,
        decl_name="A",
        proof_ref=DeclRef(node=NODE_PATH, name="B", revision=1),
    )

    semantic_refs: list[DeclRef] = []
    resolver = lean_runtime.decl_graph.ref_compatibility.resolve_decl_ref

    def record_semantic_ref(*args, **kwargs):
        semantic_refs.append(kwargs["ref"])
        return resolver(*args, **kwargs)

    monkeypatch.setattr(lean_runtime.decl_graph.ref_compatibility, "resolve_decl_ref", record_semantic_ref)
    satisfied, reason = _check_round_decl(lean_runtime, repo_root, round_id=round_id, decl_name="A")

    assert satisfied is True
    assert reason is None
    assert semantic_refs == [DeclRef(node=NODE_PATH, name="B", revision=1)]


def test_same_round_dependency_wrong_revision_is_rejected(tmp_path: Path) -> None:
    _flow_runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    _strategy_id, round_id, _round_index = create_round_with_decl(lean_runtime, repo_root, decl_name="A")
    created = lean_runtime.decl_graph.create_decl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        name="B",
        kind="theorem",
        objective="Create B.",
        summary="B summary.",
        target_state=DeclState.PROVED,
    )
    assert created.ok, created.issues
    assert lean_runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id).ok
    _write_proved_round_theorem(lean_runtime, repo_root, round_id=round_id, decl_name="B")
    _write_proved_round_theorem(
        lean_runtime,
        repo_root,
        round_id=round_id,
        decl_name="A",
        proof_ref=DeclRef(node=NODE_PATH, name="B", revision=2),
    )

    satisfied, reason = _check_round_decl(lean_runtime, repo_root, round_id=round_id, decl_name="A")

    assert satisfied is False
    assert reason == "Dependency B could not be resolved or its provider is not stable."


def test_same_round_dependency_wrong_stage_is_rejected(tmp_path: Path) -> None:
    _flow_runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    _strategy_id, round_id, _round_index = create_round_with_decl(lean_runtime, repo_root, decl_name="A")
    created = lean_runtime.decl_graph.create_decl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        name="B",
        kind="theorem",
        objective="Create B.",
        summary="B summary.",
        target_state=DeclState.PROVED,
    )
    assert created.ok, created.issues
    assert lean_runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id).ok
    _write_declared_round_theorem(lean_runtime, repo_root, round_id=round_id, decl_name="B")
    _write_proved_round_theorem(
        lean_runtime,
        repo_root,
        round_id=round_id,
        decl_name="A",
        proof_ref=DeclRef(node=NODE_PATH, name="B", revision=1),
    )

    satisfied, reason = _check_round_decl(lean_runtime, repo_root, round_id=round_id, decl_name="A")

    assert satisfied is False
    assert reason == "B is declared; proved is required."


def test_ready_adapter_bound_decl_is_accepted_as_external_dependency(tmp_path: Path) -> None:
    _flow_runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    _prepare_ready_adapter_provider(lean_runtime, tmp_path / "Provider", bind_interface=True)
    round_id = _create_external_dependency_round(lean_runtime, repo_root)

    satisfied, reason = _check_round_decl(lean_runtime, repo_root, round_id=round_id, decl_name="A")

    assert satisfied is True, reason
    assert reason is None


def test_declared_adapter_entrypoint_satisfies_declared_but_not_proved_dependency(
    tmp_path: Path,
) -> None:
    _flow_runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    _prepare_ready_adapter_provider(
        lean_runtime,
        tmp_path / "Provider",
        bind_interface=True,
        recursive_axioms=["sorryAx"],
    )
    ref = DeclRef(repo="Provider", node="Main", name="main_result", revision=1)

    declared = lean_runtime.decl_graph.ref_compatibility.resolve_public_decl_ref(
        repo_root,
        ref=ref,
        required_availability=ProofAvailability.DECLARED,
    )
    proved = lean_runtime.decl_graph.ref_compatibility.resolve_public_decl_ref(
        repo_root,
        ref=ref,
        required_availability=ProofAvailability.PROVED,
    )

    assert declared.ok and declared.value is not None
    assert declared.value.compatible is True
    assert declared.value.current_state == "declared"
    assert proved.ok and proved.value is not None
    assert proved.value.compatible is False
    assert proved.value.reason == "state_too_low"

    round_id = _create_external_dependency_round(lean_runtime, repo_root)
    satisfied, reason = _check_round_decl(
        lean_runtime,
        repo_root,
        round_id=round_id,
        decl_name="A",
    )
    assert satisfied is False
    assert reason == "Provider:Main:main_result is declared; proved is required."


def test_ready_adapter_exported_decl_does_not_require_interface_binding(tmp_path: Path) -> None:
    _flow_runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    _prepare_ready_adapter_provider(lean_runtime, tmp_path / "Provider", bind_interface=False)
    round_id = _create_external_dependency_round(lean_runtime, repo_root)

    satisfied, reason = _check_round_decl(lean_runtime, repo_root, round_id=round_id, decl_name="A")

    assert satisfied is True, reason
    assert reason is None


def test_external_provider_read_failure_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _flow_runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    _prepare_ready_adapter_provider(lean_runtime, tmp_path / "Provider", bind_interface=True)
    round_id = _create_external_dependency_round(lean_runtime, repo_root)
    issue = lean_runtime.foundation.issue("adapter_provider_failed", "Adapter provider public boundary read failed.")

    def fail_provider_read(*args, **kwargs):
        return lean_runtime.foundation.fail([issue])

    monkeypatch.setattr(
        lean_runtime.decl_graph.ref_compatibility,
        "resolve_public_decl_ref",
        fail_provider_read,
    )
    satisfied, reason = _check_round_decl(lean_runtime, repo_root, round_id=round_id, decl_name="A")

    assert satisfied is False
    assert reason == "Adapter provider public boundary read failed."


def _write_declared_round_theorem(lean_runtime, repo_root: Path, *, round_id: str, decl_name: str) -> None:
    assert lean_runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=decl_name,
        nl=f"{decl_name} states True.",
    ).ok
    assert write_statement_formal_for_test(lean_runtime,
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=decl_name,
        lean_code=f"theorem {decl_name} : True := by sorry",
        lean_check=lean_check_payload(contains_sorry=True),
    ).ok
    set_current_decl_lean_name_for_test(
        lean_runtime,
        repo_root,
        node_path=NODE_PATH,
        decl_name=decl_name,
    )
    advanced = lean_runtime.decl_graph.advance_stage_state(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        stage="statement_formal",
        decl_names=[decl_name],
    )
    assert advanced.ok, advanced.issues


def _create_external_dependency_round(lean_runtime, repo_root: Path) -> str:
    _strategy_id, round_id, _round_index = create_round_with_decl(lean_runtime, repo_root, decl_name="A")
    assert lean_runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id).ok
    _write_proved_round_theorem(
        lean_runtime,
        repo_root,
        round_id=round_id,
        decl_name="A",
        proof_ref=DeclRef(repo="Provider", node="Main", name="main_result", revision=1),
    )
    return round_id


def _prepare_ready_adapter_provider(
    lean_runtime,
    provider_root: Path,
    *,
    bind_interface: bool,
    recursive_axioms: list[str] | None = None,
) -> None:
    lean_runtime.external.lean_toolchain.lake = _AdapterIdentityLake(lean_runtime.external.lean_toolchain.lake)
    toolkit = LeanMcpToolkitClient(
        dispatcher=CleanDeclarationSoundnessDispatcher(
            axioms_by_name={"Upstream.Basic.main_result": recursive_axioms or []}
        )
    )
    lean_runtime.external.lean_mcp_toolkit = toolkit
    lean_runtime.external.lean_toolkit = toolkit
    lean_runtime.external.lean_toolchain.toolkit = toolkit
    interface = DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Expose the adapter theorem.")
    assert lean_runtime.repo_workspace.metadata.ensure_repo_model(provider_root).ok
    assert lean_runtime.repo_workspace.metadata.set_repo_format(
        provider_root,
        repo_format=RepoFormat.ADAPTER,
        reason="Decl round adapter dependency fixture.",
    ).ok
    preparation = RepoPreparationInput(
        goal="Expose a trusted upstream theorem.",
        source_corpus_mode=SourceCorpusMode.NONE,
        source_corpus_relpath=None,
        interface_inputs=[interface] if bind_interface else [],
    )
    assert lean_runtime.repo_workspace.preparation.write_preparation_input(provider_root, input=preparation).ok
    assert lean_runtime.node.node_tree.ensure_root_scope_node(provider_root).ok
    (provider_root / "lakefile.toml").write_text(
        'name = "Adapter"\n\n[[require]]\nname = "upstream"\npath = ".lake/packages/upstream"\n',
        encoding="utf-8",
    )
    upstream = provider_root / ".lake" / "packages" / "upstream"
    (upstream / "Upstream").mkdir(parents=True, exist_ok=True)
    (upstream / "lakefile.toml").write_text('name = "upstream"\n', encoding="utf-8")
    (upstream / "Upstream" / "Basic.lean").write_text("import Mathlib\n", encoding="utf-8")
    assert lean_runtime.adapter.write_adapter_upstream_metadata(
        provider_root,
        git_url="https://example.invalid/upstream.git",
        revision="1" * 40,
        package_name="upstream",
        dependency_name="upstream",
        evidence_summary="Decl round fixture upstream checkout.",
        visible_modules=["Upstream.Basic"],
    ).ok
    assert lean_runtime.adapter.mark_upstream_build_trusted(
        provider_root,
        summary="Decl round fixture upstream checks passed.",
    ).ok
    assert lean_runtime.adapter.ensure_flat_main_catalog(provider_root).ok
    assert lean_runtime.adapter.create_adapter_decl(
        provider_root,
        name="main_result",
        kind="theorem",
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.main_result",
        summary="Expose the upstream theorem.",
    ).ok
    assert lean_runtime.adapter.set_adapter_statement_formal(
        provider_root,
        name="main_result",
        code="theorem main_result : True := by\n  sorry",
    ).ok
    assert lean_runtime.adapter.set_adapter_statement_nl(
        provider_root,
        name="main_result",
        text="The adapter theorem states True.",
    ).ok
    assert lean_runtime.adapter.set_adapter_proof_formal(
        provider_root,
        name="main_result",
        code="theorem main_result : True := by\n  trivial",
    ).ok
    assert lean_runtime.adapter.set_adapter_proof_nl(
        provider_root,
        name="main_result",
        text="Use triviality.",
    ).ok
    assert lean_runtime.adapter.finalize_adapter_decl(provider_root, name="main_result").ok
    if bind_interface:
        bound = lean_runtime.adapter.bind_adapter_interface(
            provider_root,
            interface_name="main_result",
            decl_name="main_result",
            binding_summary="The adapter theorem satisfies the public interface.",
        )
        assert bound.ok, bound.issues
    synchronized = lean_runtime.adapter.sync_adapter_public_exports(provider_root)
    assert synchronized.ok, synchronized.issues
    projection = lean_runtime.adapter.refresh_adapter_projection(provider_root)
    assert projection.ok, projection.issues
    ready = lean_runtime.adapter.check_adapter_ready(provider_root)
    assert ready.ok and ready.value is not None and ready.value.passed, ready.issues
    publish_adapter_provider_release(
        lean_runtime,
        provider_root,
        summary="Adapter provider is ready for Decl Round dependency tests.",
    )


def _write_proved_round_theorem(
    lean_runtime,
    repo_root: Path,
    *,
    round_id: str,
    decl_name: str,
    proof_ref: DeclRef | None = None,
) -> None:
    _write_declared_round_theorem(lean_runtime, repo_root, round_id=round_id, decl_name=decl_name)
    assert lean_runtime.decl_graph.write_proof_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=decl_name,
        nl="Use triviality.",
    ).ok
    assert write_proof_formal_for_test(lean_runtime,
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=decl_name,
        lean_code=f"theorem {decl_name} : True := by trivial",
        lean_check=lean_check_payload(),
    ).ok
    if proof_ref is not None:
        added = lean_runtime.decl_graph.add_proof_dep(
            repo_root,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name=decl_name,
            dep=RepoDeclDep(ref=proof_ref),
        )
        assert added.ok, added.issues
    advanced = lean_runtime.decl_graph.advance_stage_state(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        stage="proof_formal",
        decl_names=[decl_name],
    )
    assert advanced.ok, advanced.issues


def _check_round_decl(lean_runtime, repo_root: Path, *, round_id: str, decl_name: str) -> tuple[bool, str | None]:
    report = lean_runtime.decl_graph.check_round_decl_ready(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=decl_name,
        required_availability=ProofAvailability.PROVED,
    )
    assert report.ok and report.value is not None, report.issues
    return report.value.ready, report.value.blocker.message if report.value.blocker else None
