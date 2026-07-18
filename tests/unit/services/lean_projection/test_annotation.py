from tests.unit_services_helpers import make_runtime

from lean_constellation.services.decl_graph import DeclFileRevisionView
from lean_constellation.services.lean_projection.annotation import (
    ResolvedMathlibDependencyProjection,
    ResolvedRepoDeclDependencyProjection,
)


def _revision() -> DeclFileRevisionView:
    return DeclFileRevisionView(
        decl_name="foo_bar",
        revision=1,
        kind="theorem",
        state="specified",
        version_status="open",
        module="Example.Main.Topic.Theorems.foo_bar",
        statement={
            "nl": {
                "text": "For every natural number n, n equals itself.\n\nThis is the reusable statement.",
                "origin": [
                    {"kind": "source", "source_path": "chapter.md", "start_line": 10, "end_line": 12},
                    {"kind": "resource", "resource_key": "paper", "start_locator": "§2", "end_locator": "Theorem 1"},
                ],
            },
            "deps": ["Nat.Coprime", "helper"],
            "dep_refs": [
                {"kind": "mathlib_decl", "ref": {"name": "Nat.Coprime", "module": "Mathlib.Data.Nat.GCD.Basic"}},
                {"kind": "repo_decl", "ref": {"node": "Main.Topic", "name": "helper", "revision": 1}},
            ],
        },
        proof={
            "nl": {"text": "Use reflexivity.", "origin": [{"kind": "reference", "ref": "Lean core"}]},
            "deps": [],
        },
    )


def _statement_dependencies():
    return [
        ResolvedMathlibDependencyProjection(
            lean_decl_name="Nat.Coprime",
            module="Mathlib.Data.Nat.GCD.Basic",
        ),
        ResolvedRepoDeclDependencyProjection(
            node_path="Main.Topic",
            decl_name="helper",
            module="Example.Main.Topic.Defs.helper",
            lean_decl_name="Example.helper",
            resolved_revision=1,
        ),
    ]


def _managed_source(component, declaration: str, *, proof: bool = False) -> str:
    revision = _revision()
    rendered = (
        component.render_proof_docstring(
            revision,
            statement_dependencies=_statement_dependencies(),
            proof_dependencies=[],
        )
        if proof
        else component.render_statement_docstring(revision, dependencies=_statement_dependencies())
    )
    assert rendered.ok and rendered.value is not None
    return rendered.value + "\n" + declaration + "\n"


def test_render_current_markdown_docstring_and_dependency_grammar() -> None:
    component = make_runtime().lean_projection.annotation
    statement = component.render_statement_docstring(_revision(), dependencies=_statement_dependencies())
    assert statement.ok and statement.value is not None
    text = statement.value
    assert "# lean-constellation target: `foo_bar`" in text
    assert "## Sources" in text
    assert "Source `chapter.md`, lines 10–12" in text
    assert "Resource `paper`, `§2`–`Theorem 1`" in text
    assert "## Statement dependencies" in text
    assert "`Nat.Coprime` from `Mathlib.Data.Nat.GCD.Basic`" in text
    assert "`Main.Topic::helper` → `Example.helper` from `Example.Main.Topic.Defs.helper`" in text
    assert "stage:" not in text
    assert "None" not in text
    assert "revision" not in text

    proof = component.render_proof_docstring(
        _revision(),
        statement_dependencies=_statement_dependencies(),
        proof_dependencies=[],
    )
    assert proof.ok and proof.value is not None
    assert "## Proof outline\n\nUse reflexivity." in proof.value
    assert "## Proof sources\n\n- Reference `Lean core`" in proof.value
    assert "## Proof dependencies" not in proof.value


def test_render_docstring_wraps_generated_prose_to_style_limit() -> None:
    component = make_runtime().lean_projection.annotation
    revision = _revision().model_copy(deep=True)
    revision.statement.nl.text = " ".join(["squarefree residual selection"] * 12)

    rendered = component.render_statement_docstring(revision, dependencies=_statement_dependencies())

    assert rendered.ok and rendered.value is not None
    assert max(len(line) for line in rendered.value.splitlines()) <= 100
    assert "squarefree residual selection" in rendered.value


def test_compare_unmanaged_expected_header_with_managed_statement() -> None:
    component = make_runtime().lean_projection.annotation
    managed = _managed_source(component, "theorem foo_bar : /- current -/ True := by\n  sorry")

    same = component.compare_expected_theorem_header(
        "theorem foo_bar : /- exact target -/ True := by sorry",
        managed,
        decl_name="foo_bar",
        lean_decl_name="foo_bar",
    )
    assert same.ok and same.value is not None and same.value.passed

    changed = component.compare_expected_theorem_header(
        "theorem foo_bar : False := by sorry",
        managed,
        decl_name="foo_bar",
        lean_decl_name="foo_bar",
    )
    assert changed.ok and changed.value is not None and not changed.value.passed


def test_render_requires_one_resolved_projection_per_structured_dependency() -> None:
    component = make_runtime().lean_projection.annotation
    missing = component.render_statement_docstring(_revision())
    assert not missing.ok
    assert missing.issues[0].kind == "dependency_projection_missing"


def test_parse_and_validate_current_target_marker() -> None:
    component = make_runtime().lean_projection.annotation
    expected = component.render_statement_docstring(_revision(), dependencies=_statement_dependencies()).value
    assert expected is not None
    marker = component.parse_target_marker(expected)
    assert marker.ok and marker.value is not None
    assert marker.value.decl_name == "foo_bar"
    assert marker.value.marker_line == 2

    old = component.parse_target_marker("/--\nlean-constellation target: foo_bar\n-/")
    assert not old.ok and old.issues[0].kind == "target_marker_missing"
    duplicate = component.parse_target_marker(expected + "\n" + expected)
    assert not duplicate.ok and duplicate.issues[0].kind == "target_marker_duplicate"

    valid = component.validate_docstring(
        expected + "\ntheorem actualResult : True := by trivial\n",
        decl_name="foo_bar",
        stage="statement",
        expected_docstring=expected,
    )
    assert valid.ok and valid.value is not None and valid.value.passed
    changed = expected.replace("reusable statement", "changed statement")
    invalid = component.validate_docstring(changed, decl_name="foo_bar", stage="statement", expected_docstring=expected)
    assert invalid.ok and invalid.value is not None and not invalid.value.passed
    assert invalid.value.issues[0].kind == "system_docstring_changed"


def test_locate_marker_adjacent_declaration_uses_namespace_and_not_decl_key() -> None:
    component = make_runtime().lean_projection.annotation
    source = "namespace Example\nnamespace Inner\n\n" + _managed_source(
        component,
        "theorem actualResult (n : Nat) : n = n := by\n  rfl\nend Inner\nend Example",
    )
    found = component.locate_target_declaration(source, decl_name="foo_bar")
    assert found.ok and found.value is not None, found.issues
    assert found.value.source_name == "actualResult"
    assert found.value.candidate_full_name == "Example.Inner.actualResult"
    assert found.value.kind == "theorem"

    changed_marker = source.replace("target: `foo_bar`", "target: `other`")
    mismatch = component.locate_target_declaration(changed_marker, decl_name="foo_bar")
    assert not mismatch.ok and mismatch.issues[0].kind == "target_marker_decl_mismatch"


def test_locate_rejects_missing_adjacent_or_later_top_level_declaration() -> None:
    component = make_runtime().lean_projection.annotation
    docstring = component.render_statement_docstring(_revision(), dependencies=_statement_dependencies()).value
    assert docstring is not None
    missing = component.locate_target_declaration(docstring + "\n-- no declaration\n", decl_name="foo_bar")
    assert not missing.ok and missing.issues[0].kind == "target_declaration_missing"
    later = component.locate_target_declaration(
        docstring + "\ntheorem actualResult : True := by trivial\n\ndef laterResult : Nat := 0\n",
        decl_name="foo_bar",
    )
    assert not later.ok and later.issues[0].kind == "target_declaration_not_last"


def test_compare_marker_adjacent_theorem_header() -> None:
    component = make_runtime().lean_projection.annotation
    statement = _managed_source(
        component,
        "theorem actualResult\n    (n : Nat) :\n    n = n := by\n  sorry",
    )
    proof = _managed_source(
        component,
        "theorem actualResult (n : Nat) : n = n := by\n  rfl",
        proof=True,
    )
    same = component.compare_theorem_header(statement, proof, decl_name="foo_bar")
    assert same.ok and same.value is not None and same.value.passed

    changed = proof.replace("n = n", "n + 0 = n")
    different = component.compare_theorem_header(statement, changed, decl_name="foo_bar")
    assert different.ok and different.value is not None and not different.value.passed
    assert different.value.issues[0].kind == "theorem_header_changed"


def test_external_declaration_probe_preserves_comment_offsets_and_namespace() -> None:
    component = make_runtime().lean_projection.annotation
    source = (
        "/- a comment containing theorem ignored : False -/\n"
        "namespace Upstream.Basic\n\n"
        "theorem main_result : True := by\n  trivial\n\n"
        "end Upstream.Basic\n"
    )

    located = component.locate_external_declaration(
        source,
        lean_decl_name="Upstream.Basic.main_result",
    )
    probe = component.build_external_declaration_probe(
        source,
        lean_decl_name="Upstream.Basic.main_result",
    )

    assert located.ok and located.value is not None
    assert located.value.source_name == "main_result"
    assert source[located.value.source_name_start_offset : located.value.source_name_end_offset] == "main_result"
    assert probe.ok and probe.value is not None
    assert probe.value.probe_lean_decl_name == "LeanConstellationAdapterProbe.captured"
    assert "theorem _root_.LeanConstellationAdapterProbe.captured : True" in probe.value.code
    assert "theorem main_result : True" not in probe.value.code


def test_external_probe_restores_namespace_for_extracted_short_declaration() -> None:
    component = make_runtime().lean_projection.annotation
    source = "theorem result (x : LocalType) : x = x := by rfl"

    probe = component.build_external_declaration_probe(
        source,
        lean_decl_name="Upstream.Basic.result",
    )

    assert probe.ok and probe.value is not None
    assert probe.value.code.startswith("namespace Upstream.Basic\n")
    assert "theorem _root_.LeanConstellationAdapterProbe.captured (x : LocalType)" in probe.value.code


def test_external_declaration_does_not_accept_short_name_inside_wrong_namespace() -> None:
    component = make_runtime().lean_projection.annotation
    source = "namespace Other\n\ntheorem main_result : True := by trivial\n\nend Other\n"

    result = component.locate_external_declaration(
        source,
        lean_decl_name="Upstream.Basic.main_result",
    )

    assert not result.ok
    assert result.issues[0].kind == "external_declaration_ambiguous"


def test_extract_primary_source_excludes_managed_docstring_and_helpers() -> None:
    component = make_runtime().lean_projection.annotation
    source = (
        "namespace Example\n\n"
        "private lemma helper : True := by trivial\n\n"
        + _managed_source(component, "theorem actualResult : True := by\n  trivial\n\nend Example")
    )

    extracted = component.extract_primary_declaration_source(
        source,
        decl_name="foo_bar",
        lean_decl_name="Example.actualResult",
        managed=True,
    )

    assert extracted.ok and extracted.value is not None, extracted.issues
    assert extracted.value.source_kind == "managed"
    assert extracted.value.code.startswith("theorem actualResult : True")
    assert "helper" not in extracted.value.code
    assert "lean-constellation target" not in extracted.value.code
    assert extracted.value.code.endswith("end Example")


def test_extract_external_primary_source_uses_registered_symbol() -> None:
    component = make_runtime().lean_projection.annotation
    source = (
        "namespace Upstream\n\n"
        "private lemma helper : True := by trivial\n\n"
        "theorem result : True := by trivial\n\n"
        "end Upstream\n"
    )

    extracted = component.extract_primary_declaration_source(
        source,
        decl_name="catalog_result",
        lean_decl_name="Upstream.result",
        managed=False,
    )

    assert extracted.ok and extracted.value is not None, extracted.issues
    assert extracted.value.source_kind == "external"
    assert extracted.value.code.startswith("theorem result : True")
    assert "helper" not in extracted.value.code
