from __future__ import annotations

import pytest

from lean_constellation.services.foundation import DeclFileKey, FoundationContext, LayoutComponent


def test_layout_uses_constellation_material_roots(tmp_path) -> None:
    layout = LayoutComponent()
    ctx = FoundationContext(repo_root=tmp_path)

    assert layout.constellation_root(ctx) == tmp_path / ".lean_constellation"
    assert layout.repo_metadata_path(ctx) == tmp_path / ".lean_constellation" / "repo.json"
    assert layout.preparation_input_path(ctx) == tmp_path / ".lean_constellation" / "preparation_input.json"
    assert layout.source_corpus_root(ctx) == tmp_path / ".lean_constellation" / "source"
    assert layout.resources_root(ctx) == tmp_path / ".lean_constellation" / "resources"
    assert layout.resource_dir(ctx, "arxiv_1234") == tmp_path / ".lean_constellation" / "resources" / "items" / "arxiv_1234"
    assert layout.snapshot_root(ctx) == tmp_path / ".lean_constellation" / "snapshots"


def test_layout_rejects_repo_escape_and_unsafe_keys(tmp_path) -> None:
    layout = LayoutComponent()
    ctx = FoundationContext(repo_root=tmp_path)

    with pytest.raises(ValueError):
        layout.source_corpus_root(ctx, "../outside")
    with pytest.raises(ValueError):
        layout.source_corpus_entry_path(ctx, ".lean_constellation/source", "../outside.md")
    with pytest.raises(ValueError):
        layout.resource_dir(ctx, "bad/key")


def test_node_and_projection_paths_are_stable(tmp_path) -> None:
    layout = LayoutComponent()
    ctx = FoundationContext(repo_root=tmp_path)

    encoded = layout.encode_dot_path("Main.Algebra.Order")

    assert encoded.startswith("n_")
    assert layout.decode_dot_path(encoded) == "Main.Algebra.Order"
    assert layout.node_contract_path(ctx, "Main.Algebra", 1) == (
        tmp_path / ".lean_constellation" / "nodes" / layout.encode_dot_path("Main.Algebra") / "contracts" / "1.json"
    )
    assert layout.prelude_path(ctx, "Main.Algebra") == tmp_path / "Main" / "Algebra" / "Prelude.lean"
    assert layout.interfaces_path(ctx, "Main.Algebra") == tmp_path / "Main" / "Algebra" / "Interfaces.lean"
    assert layout.adapter_interfaces_path(ctx) == tmp_path / "Main" / "Interfaces.lean"


def test_decl_file_path_uses_safe_kind_and_name(tmp_path) -> None:
    layout = LayoutComponent()
    ctx = FoundationContext(repo_root=tmp_path)

    path = layout.decl_file_path(ctx, DeclFileKey(node_path="Main.Topic", decl_kind="Theorems", decl_name="fixed_point"))

    assert path == tmp_path / "Main" / "Topic" / "Theorems" / "fixed_point.lean"
