from pathlib import Path

from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import ProofAvailability
from lean_constellation.services.decl_graph import DeclState, RepoReleaseHeads
from lean_constellation.services.foundation import WriteMode
from tests.unit.services.repo_workspace.test_repo_release import (
    _prepare_native_provider,
    _prepare_release_repo,
    _release,
    _write_decl,
)


def _set_active_contract_head(runtime, repo_root: Path, *, node_path: str, name: str, revision: int) -> None:
    current = runtime.node.contract.get_visible_contract(repo_root, node_path=node_path)
    assert current.ok and current.value is not None
    current.value.contract.decl_graph_head[name] = revision
    path = runtime.node.node_tree.node_store.contract_path(
        repo_root,
        node_id=current.value.node_id,
        version=current.value.contract.version,
    )
    assert runtime.foundation.store.write_json_atomic(
        path, current.value.contract, mode=WriteMode.UPDATE_EXISTING
    ).ok


def test_anchor_resolves_from_active_contract_head_not_working_decl_revision(tmp_path: Path) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    anchor = DeclRef(node="Main.Results", name="PublicResult", revision=1)
    _write_decl(tmp_path, node_path="Main.Results", name="PublicResult", revision=2)

    resolved = runtime.decl_graph.ref_compatibility.resolve_decl_ref(
        tmp_path,
        ref=anchor,
        required_availability=ProofAvailability.PROVED,
    )

    assert resolved.ok and resolved.value is not None
    assert resolved.value.compatible is True
    assert resolved.value.resolved_revision == 1
    assert resolved.value.reason == "exact_revision"
    assert anchor.revision == 1

    _set_active_contract_head(runtime, tmp_path, node_path="Main.Results", name="PublicResult", revision=2)
    advanced = runtime.decl_graph.ref_compatibility.resolve_decl_ref(
        tmp_path,
        ref=anchor,
        required_availability=ProofAvailability.PROVED,
    )
    assert advanced.ok and advanced.value is not None
    assert advanced.value.compatible is True
    assert advanced.value.resolved_revision == 2
    assert advanced.value.reason == "compatible_revision"


def test_anchor_reports_statement_change_and_state_too_low(tmp_path: Path) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    anchor = DeclRef(node="Main.Results", name="PublicResult", revision=1)
    _write_decl(tmp_path, node_path="Main.Results", name="PublicResult", revision=2)
    _set_active_contract_head(runtime, tmp_path, node_path="Main.Results", name="PublicResult", revision=2)
    revision = runtime.decl_graph.get_decl_revision(
        tmp_path, node_path="Main.Results", name="PublicResult", revision=2
    ).value
    revision.statement.formal.code = "theorem PublicResult : False := by\n  sorry\n"
    path = runtime.decl_graph.graph_store.revision_path(
        tmp_path, node_path="Main.Results", decl_name="PublicResult", revision=2
    )
    assert runtime.foundation.store.write_json_atomic(path, revision, mode=WriteMode.UPDATE_EXISTING).ok
    changed = runtime.decl_graph.ref_compatibility.resolve_decl_ref(
        tmp_path, ref=anchor, required_availability=ProofAvailability.DECLARED
    )
    assert changed.ok and changed.value.reason == "declared_api_changed"

    revision.statement.formal.code = "import Mathlib\n\ntheorem PublicResult : True := by\n  sorry\n"
    revision.state = DeclState.SPECIFIED
    assert runtime.foundation.store.write_json_atomic(path, revision, mode=WriteMode.UPDATE_EXISTING).ok
    too_low = runtime.decl_graph.ref_compatibility.resolve_decl_ref(
        tmp_path, ref=anchor, required_availability=ProofAvailability.DECLARED
    )
    assert too_low.ok and too_low.value.reason == "state_too_low"
    assert too_low.value.resolved_revision == 2


def test_release_target_uses_exact_historical_contract_head(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    assert runtime.repo_workspace.release.create_release(tmp_path, release=_release("r1", versions)).ok
    _write_decl(tmp_path, node_path="Main.Results", name="PublicResult", revision=2)
    anchor = DeclRef(node="Main.Results", name="PublicResult", revision=1)

    resolved = runtime.decl_graph.ref_compatibility.resolve_decl_ref(
        tmp_path,
        ref=anchor,
        required_availability=ProofAvailability.DECLARED,
        target=RepoReleaseHeads(release_id="r1"),
    )

    assert resolved.ok and resolved.value.compatible is True
    assert resolved.value.resolved_revision == 1
    assert resolved.value.reason == "exact_revision"


def test_scope_export_anchor_remains_valid_after_proof_only_progression(tmp_path: Path) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    _write_decl(tmp_path, node_path="Main.Results", name="PublicResult", revision=2)
    _set_active_contract_head(runtime, tmp_path, node_path="Main.Results", name="PublicResult", revision=2)

    exports = runtime.node.export.list_scope_exports(tmp_path, scope_path="Main")

    assert exports.ok and exports.value is not None
    assert len(exports.value) == 1
    assert exports.value[0].ref.revision == 1
    assert exports.value[0].resolved_revision == 2
    assert exports.value[0].resolution_reason == "compatible_revision"
    assert exports.value[0].valid is True
    public = runtime.node.public_decl_access.list_node_public_decls(
        tmp_path,
        node_path="Main",
        actor_role="coordinator",
    )
    assert public.ok and public.value is not None
    assert public.value[0].ref.revision == 1
    assert public.value[0].resolved_revision == 2
    assert _main_export_anchor_revision(runtime, tmp_path) == 1


def _main_export_anchor_revision(runtime, repo_root: Path) -> int:
    main = runtime.node.contract.get_visible_contract(repo_root, node_path="Main")
    assert main.ok and main.value is not None
    return main.value.contract.exports[0].revision


def test_proved_availability_is_kind_aware(tmp_path: Path) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    for name, kind in [
        ("Definition", "definition"),
        ("Structure", "structure"),
        ("Class", "class"),
        ("Instance", "instance"),
        ("Lemma", "lemma"),
    ]:
        _write_decl(
            tmp_path,
            node_path="Main.Results",
            name=name,
            kind=kind,
            state=DeclState.DECLARED,
        )
        _set_active_contract_head(runtime, tmp_path, node_path="Main.Results", name=name, revision=1)

    for name in ["Definition", "Structure", "Class", "Instance"]:
        resolved = runtime.decl_graph.ref_compatibility.resolve_decl_ref(
            tmp_path,
            ref=DeclRef(node="Main.Results", name=name, revision=1),
            required_availability=ProofAvailability.PROVED,
        )
        assert resolved.ok and resolved.value is not None and resolved.value.compatible

    lemma = runtime.decl_graph.ref_compatibility.resolve_decl_ref(
        tmp_path,
        ref=DeclRef(node="Main.Results", name="Lemma", revision=1),
        required_availability=ProofAvailability.PROVED,
    )
    assert lemma.ok and lemma.value is not None
    assert lemma.value.compatible is False
    assert lemma.value.reason == "state_too_low"


def test_public_ref_propagates_corrupt_provider_truth_failure(tmp_path: Path) -> None:
    runtime, provider_root = _prepare_native_provider(tmp_path)
    consumer_root = tmp_path / "Consumer"
    consumer_root.mkdir()
    runtime.repo_workspace.metadata._repo_publication_path(provider_root).write_text(
        "{not-json",
        encoding="utf-8",
    )

    resolved = runtime.decl_graph.ref_compatibility.resolve_public_decl_ref(
        consumer_root,
        ref=DeclRef(repo="Provider", node="Main.Results", name="PublicResult", revision=1),
        required_availability=ProofAvailability.DECLARED,
    )

    assert not resolved.ok
    assert any(issue.kind in {"invalid_json", "read_failed", "schema_validation_failed"} for issue in resolved.issues)


def test_public_ref_propagates_provider_availability_read_failure(tmp_path: Path, monkeypatch) -> None:
    runtime = _prepare_release_repo(tmp_path / "Consumer")[0]
    failure = runtime.foundation.fail(
        runtime.foundation.issue("read_failed", "Injected provider availability read failure.")
    )
    monkeypatch.setattr(
        runtime.repo_workspace.provider_availability,
        "check_provider_available",
        lambda _repo_root: failure,
    )

    resolved = runtime.decl_graph.ref_compatibility.resolve_public_decl_ref(
        tmp_path / "Consumer",
        ref=DeclRef(repo="Provider", node="Main.Results", name="PublicResult", revision=1),
        required_availability=ProofAvailability.DECLARED,
    )

    assert not resolved.ok
    assert resolved.issues[0].kind == "read_failed"


def test_public_ref_listing_returns_empty_warning_for_unavailable_provider(tmp_path: Path) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)

    listed = runtime.decl_graph.ref_compatibility.list_public_decl_refs(
        tmp_path,
        required_availability=ProofAvailability.DECLARED,
    )

    assert listed.ok and listed.value == []
    assert [issue.kind for issue in listed.issues] == ["provider_not_stable"]
