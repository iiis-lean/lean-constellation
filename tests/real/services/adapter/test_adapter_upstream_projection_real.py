from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import RepoFormat
from lean_constellation.services.external_clients import (
    LeanMcpToolkitClient,
    LeanMcpToolkitClientConfig,
)
from lean_constellation.services.foundation import FoundationContext


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def _write_minimal_upstream_repo(repo_root: Path) -> tuple[Path, str, str, DeclKind]:
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "lakefile.toml").write_text(
        'name = "AdapterUpstream"\n'
        'version = "0.1.0"\n'
        'defaultTargets = ["Upstream"]\n\n'
        '[[lean_lib]]\n'
        'name = "Upstream"\n',
        encoding="utf-8",
    )
    (repo_root / "Upstream.lean").write_text(
        "theorem upstreamSmoke : True := by\n"
        "  trivial\n",
        encoding="utf-8",
    )
    if shutil.which("lake") is not None:
        subprocess.run(
            ["lake", "build"],
            cwd=repo_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    return repo_root, "Upstream", "upstreamSmoke", DeclKind.THEOREM


@pytest.mark.real
@pytest.mark.real_toolkit
def test_adapter_upstream_projection_real(tmp_path: Path) -> None:
    upstream_root = _env("LEAN_CONSTELLATION_REAL_ADAPTER_UPSTREAM_REPO")
    toolkit_base_url = _env("LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL")
    module = _env("LEAN_CONSTELLATION_REAL_ADAPTER_UPSTREAM_MODULE")
    decl_name = _env("LEAN_CONSTELLATION_REAL_ADAPTER_UPSTREAM_DECL")
    kind_value = _env("LEAN_CONSTELLATION_REAL_ADAPTER_UPSTREAM_KIND") or "theorem"

    if not toolkit_base_url:
        pytest.skip(
            "Set LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL to run this real adapter upstream navigation test."
        )

    if upstream_root:
        upstream_path = Path(upstream_root).expanduser().resolve()
        if not upstream_path.is_dir():
            pytest.skip(f"Adapter upstream repo does not exist: {upstream_path}")
        if not module or not decl_name:
            pytest.skip(
                "Set LEAN_CONSTELLATION_REAL_ADAPTER_UPSTREAM_MODULE and "
                "LEAN_CONSTELLATION_REAL_ADAPTER_UPSTREAM_DECL when using an external upstream repo."
            )
        try:
            decl_kind = DeclKind(kind_value)
        except ValueError:
            pytest.skip(f"Unsupported LEAN_CONSTELLATION_REAL_ADAPTER_UPSTREAM_KIND={kind_value!r}")
    else:
        upstream_path, module, decl_name, decl_kind = _write_minimal_upstream_repo(tmp_path / "upstream")

    theorem_like = decl_kind in {DeclKind.THEOREM, DeclKind.LEMMA}
    adapter_name = _env("LEAN_CONSTELLATION_REAL_ADAPTER_DECL_NAME") or decl_name.rsplit(".", 1)[-1]
    repo_root = tmp_path / "adapter"
    repo_root.mkdir()

    runtime = make_runtime(
        external_overrides={
            "lean_mcp_toolkit": LeanMcpToolkitClient.from_config(
                LeanMcpToolkitClientConfig(
                    base_url=toolkit_base_url,
                    api_prefix=_env("LEAN_CONSTELLATION_REAL_TOOLKIT_API_PREFIX") or "/api/v1",
                )
            )
        }
    )
    foundation = runtime.foundation
    repo_workspace = runtime.repo_workspace
    assert repo_workspace.metadata.ensure_repo_model(repo_root).ok
    assert repo_workspace.metadata.set_repo_format(
        repo_root,
        repo_format=RepoFormat.ADAPTER,
        reason="real adapter upstream projection test",
    ).ok

    prep = RepoPreparationInput(
        goal="Expose a real upstream declaration through an adapter repo.",
        source_corpus_mode=SourceCorpusMode.NONE,
        source_corpus_relpath=None,
        interface_inputs=[
            DeclInterface(
                name=adapter_name,
                kind=decl_kind,
                summary=f"Expose upstream declaration {decl_name}.",
            )
        ],
    )
    prep_path = foundation.layout.preparation_input_path(FoundationContext(repo_root=repo_root))
    assert foundation.store.write_json_atomic(prep_path, prep).ok

    service = runtime.adapter
    assert service.write_adapter_upstream_metadata(
        repo_root,
        source_kind="local_path",
        local_path=str(upstream_path),
        package_name=upstream_path.name or "upstream",
        dependency_name=upstream_path.name or "upstream",
        evidence_summary="Real local upstream repo supplied by test environment.",
        visible_modules=[module],
    ).ok
    assert service.mark_upstream_build_trusted(
        repo_root,
        summary="Upstream repo is supplied by the real test environment and assumed build-ready.",
    ).ok
    assert service.ensure_flat_main_catalog(repo_root).ok

    outline = service.list_upstream_module_declarations(repo_root, module=module)
    assert outline.ok, outline.issues
    assert outline.value is not None
    assert any(item.decl_name in {decl_name, adapter_name, decl_name.rsplit(".", 1)[-1]} for item in outline.value.declarations)

    detail = service.inspect_upstream_declaration(repo_root, module=module, decl_name=decl_name)
    assert detail.ok, detail.issues
    assert detail.value is not None
    assert detail.value.code_excerpt

    statement = service.capture_upstream_declaration_code(
        repo_root,
        module=module,
        decl_name=decl_name,
        capture_mode="statement_only",
    )
    assert statement.ok, statement.issues
    assert statement.value is not None

    full = service.capture_upstream_declaration_code(
        repo_root,
        module=module,
        decl_name=decl_name,
        capture_mode="full_declaration",
    )
    assert full.ok, full.issues
    assert full.value is not None

    assert service.create_adapter_decl(
        repo_root,
        name=adapter_name,
        kind=decl_kind,
        module=module,
        plan_summary=f"Register upstream declaration {decl_name}.",
    ).ok
    assert service.set_adapter_statement_formal(
        repo_root,
        name=adapter_name,
        code=statement.value.code if theorem_like else full.value.code,
        upstream_decl_name=decl_name,
    ).ok
    assert service.set_adapter_statement_nl(
        repo_root,
        name=adapter_name,
        summary=f"Adapter statement for upstream declaration {decl_name}.",
    ).ok
    if theorem_like:
        assert service.set_adapter_proof_formal(
            repo_root,
            name=adapter_name,
            code=full.value.code,
            upstream_decl_name=decl_name,
        ).ok
        assert service.set_adapter_proof_nl(
            repo_root,
            name=adapter_name,
            summary=f"Adapter proof for upstream declaration {decl_name}.",
        ).ok

    finalized = service.finalize_adapter_decl(repo_root, name=adapter_name)
    assert finalized.ok, finalized.issues
    bound = service.bind_adapter_interface(
        repo_root,
        interface_name=adapter_name,
        decl_name=adapter_name,
        binding_summary="Real upstream declaration satisfies the requested adapter interface.",
    )
    assert bound.ok, bound.issues
    assert service.refresh_adapter_projection(repo_root).ok

    ready = service.check_adapter_ready(repo_root)
    assert ready.ok, ready.issues
    assert ready.value is not None
    assert ready.value.passed is True
