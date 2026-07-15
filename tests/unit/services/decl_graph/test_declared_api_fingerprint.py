from pathlib import Path

from lean_constellation.services.foundation import WriteMode
from lean_constellation.services.decl_graph.models import Decl, DeclRevision
from tests.unit.services.repo_workspace.test_repo_release import _prepare_release_repo, _write_decl


def test_declared_api_fingerprint_uses_full_canonical_statement_source(tmp_path: Path) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    original = runtime.decl_graph.declared_api.fingerprint(
        tmp_path, node_path="Main.Results", decl_name="PublicResult", revision=1
    )
    assert original.ok and original.value is not None

    revision = runtime.decl_graph.get_decl_revision(
        tmp_path, node_path="Main.Results", name="PublicResult", revision=1
    ).value
    revision.statement.formal.code = "import Mathlib  \r\n\r\ntheorem PublicResult : True := by\r\n  sorry  \r\n\r\n"
    assert runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.revision_path(
            tmp_path, node_path="Main.Results", decl_name="PublicResult", revision=1
        ),
        revision,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    whitespace = runtime.decl_graph.declared_api.fingerprint(
        tmp_path, node_path="Main.Results", decl_name="PublicResult", revision=1
    )
    assert whitespace.ok and whitespace.value.sha256 == original.value.sha256

    revision.statement.formal.code = "/-- Public API. -/\n" + revision.statement.formal.code
    assert runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.revision_path(
            tmp_path, node_path="Main.Results", decl_name="PublicResult", revision=1
        ),
        revision,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok
    changed = runtime.decl_graph.declared_api.fingerprint(
        tmp_path, node_path="Main.Results", decl_name="PublicResult", revision=1
    )
    assert changed.ok and changed.value.sha256 != original.value.sha256
    assert changed.value.statement_formal_code.startswith("/-- Public API. -/\nimport Mathlib")


def test_proof_only_progression_keeps_declared_api_fingerprint(tmp_path: Path) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    first = runtime.decl_graph.declared_api.fingerprint(
        tmp_path, node_path="Main.Results", decl_name="PublicResult", revision=1
    )
    _write_decl(tmp_path, node_path="Main.Results", name="PublicResult", revision=2)
    revision = runtime.decl_graph.get_decl_revision(
        tmp_path, node_path="Main.Results", name="PublicResult", revision=2
    ).value
    revision.proof.formal.code = "theorem PublicResult : True := by\n  exact True.intro\n"
    assert runtime.foundation.store.write_json_atomic(
        runtime.decl_graph.graph_store.revision_path(
            tmp_path, node_path="Main.Results", decl_name="PublicResult", revision=2
        ),
        revision,
        mode=WriteMode.UPDATE_EXISTING,
    ).ok

    second = runtime.decl_graph.declared_api.fingerprint(
        tmp_path, node_path="Main.Results", decl_name="PublicResult", revision=2
    )

    assert first.ok and second.ok
    assert first.value.sha256 == second.value.sha256


def test_declared_api_fingerprint_directly_binds_node_path_and_decl_kind(tmp_path: Path) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    _write_decl(tmp_path, node_path="Main.Results", name="PathSensitive", kind="theorem")
    _write_decl(tmp_path, node_path="Main.Foundation.Defs", name="PathSensitive", kind="theorem")
    result_path = runtime.decl_graph.declared_api.fingerprint(
        tmp_path, node_path="Main.Results", decl_name="PathSensitive", revision=1
    )
    foundation_path = runtime.decl_graph.declared_api.fingerprint(
        tmp_path, node_path="Main.Foundation.Defs", decl_name="PathSensitive", revision=1
    )
    assert result_path.ok and foundation_path.ok
    assert result_path.value.node_path == "Main.Results"
    assert foundation_path.value.node_path == "Main.Foundation.Defs"
    assert result_path.value.sha256 != foundation_path.value.sha256

    decl_path = runtime.decl_graph.graph_store.decl_record_path(
        tmp_path, node_path="Main.Results", decl_name="PathSensitive"
    )
    decl = runtime.decl_graph.get_decl(tmp_path, node_path="Main.Results", name="PathSensitive").value
    decl.kind = "def"
    assert runtime.foundation.store.write_json_atomic(
        decl_path, decl, mode=WriteMode.UPDATE_EXISTING
    ).ok
    changed_kind = runtime.decl_graph.declared_api.fingerprint(
        tmp_path, node_path="Main.Results", decl_name="PathSensitive", revision=1
    )
    assert changed_kind.ok and changed_kind.value is not None
    assert changed_kind.value.decl_kind == "def"
    assert changed_kind.value.sha256 != result_path.value.sha256


def test_declared_api_fingerprint_binds_module_and_lean_decl_name(tmp_path: Path) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    original = runtime.decl_graph.declared_api.fingerprint(
        tmp_path, node_path="Main.Results", decl_name="PublicResult", revision=1
    )
    assert original.ok and original.value is not None

    decl_path = runtime.decl_graph.graph_store.decl_record_path(
        tmp_path, node_path="Main.Results", decl_name="PublicResult"
    )
    decl = runtime.decl_graph.get_decl(tmp_path, node_path="Main.Results", name="PublicResult").value
    decl.module = "TestProject.Main.Results.Theorems.RelocatedResult"
    assert runtime.foundation.store.write_json_atomic(decl_path, decl, mode=WriteMode.UPDATE_EXISTING).ok
    changed_module = runtime.decl_graph.declared_api.fingerprint(
        tmp_path, node_path="Main.Results", decl_name="PublicResult", revision=1
    )
    assert changed_module.ok and changed_module.value is not None
    assert changed_module.value.module == decl.module
    assert changed_module.value.sha256 != original.value.sha256

    decl.module = original.value.module
    assert runtime.foundation.store.write_json_atomic(decl_path, decl, mode=WriteMode.UPDATE_EXISTING).ok
    revision_path = runtime.decl_graph.graph_store.revision_path(
        tmp_path, node_path="Main.Results", decl_name="PublicResult", revision=1
    )
    revision = runtime.decl_graph.get_decl_revision(
        tmp_path, node_path="Main.Results", name="PublicResult", revision=1
    ).value
    revision.lean_decl_name = "TestProject.RenamedPublicResult"
    assert runtime.foundation.store.write_json_atomic(
        revision_path, revision, mode=WriteMode.UPDATE_EXISTING
    ).ok
    changed_lean_name = runtime.decl_graph.declared_api.fingerprint(
        tmp_path, node_path="Main.Results", decl_name="PublicResult", revision=1
    )
    assert changed_lean_name.ok and changed_lean_name.value is not None
    assert changed_lean_name.value.lean_decl_name == revision.lean_decl_name
    assert changed_lean_name.value.sha256 != original.value.sha256


def test_declared_api_fingerprint_requires_both_persisted_lean_identities(tmp_path: Path) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    decl_path = runtime.decl_graph.graph_store.decl_record_path(
        tmp_path, node_path="Main.Results", decl_name="PublicResult"
    )
    decl = runtime.decl_graph.get_decl(tmp_path, node_path="Main.Results", name="PublicResult").value
    original_module = decl.module
    decl.module = None
    assert runtime.foundation.store.write_json_atomic(decl_path, decl, mode=WriteMode.UPDATE_EXISTING).ok
    missing_module = runtime.decl_graph.declared_api.fingerprint(
        tmp_path, node_path="Main.Results", decl_name="PublicResult", revision=1
    )
    assert not missing_module.ok
    assert missing_module.issues[0].kind == "declared_api_module_missing"

    decl.module = original_module
    assert runtime.foundation.store.write_json_atomic(decl_path, decl, mode=WriteMode.UPDATE_EXISTING).ok
    revision_path = runtime.decl_graph.graph_store.revision_path(
        tmp_path, node_path="Main.Results", decl_name="PublicResult", revision=1
    )
    revision = runtime.decl_graph.get_decl_revision(
        tmp_path, node_path="Main.Results", name="PublicResult", revision=1
    ).value
    revision.lean_decl_name = None
    assert runtime.foundation.store.write_json_atomic(
        revision_path, revision, mode=WriteMode.UPDATE_EXISTING
    ).ok
    missing_lean_name = runtime.decl_graph.declared_api.fingerprint(
        tmp_path, node_path="Main.Results", decl_name="PublicResult", revision=1
    )
    assert not missing_lean_name.ok
    assert missing_lean_name.issues[0].kind == "declared_api_lean_decl_name_missing"


def test_decl_and_revision_identity_fields_survive_strict_json_roundtrip(tmp_path: Path) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    decl = runtime.decl_graph.get_decl(tmp_path, node_path="Main.Results", name="PublicResult").value
    revision = runtime.decl_graph.get_decl_revision(
        tmp_path, node_path="Main.Results", name="PublicResult", revision=1
    ).value

    restored_decl = Decl.model_validate_json(decl.model_dump_json())
    restored_revision = DeclRevision.model_validate_json(revision.model_dump_json())

    assert restored_decl.module == decl.module
    assert restored_revision.lean_decl_name == revision.lean_decl_name
    assert "decl_name" not in revision.model_dump()
    assert "module" not in revision.model_dump()
