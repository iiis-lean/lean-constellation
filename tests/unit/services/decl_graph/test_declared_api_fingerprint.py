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
