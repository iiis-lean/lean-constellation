from pathlib import Path

from lean_constellation.services.foundation import WriteMode
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
