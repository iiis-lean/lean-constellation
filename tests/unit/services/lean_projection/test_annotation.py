from lean_constellation.services.lean_projection import AnnotationComponent


def _revision() -> dict:
    return {
        "name": "foo_bar",
        "statement": {
            "nl": {
                "text": "For every natural number n, n equals itself.",
                "origin": {"kind": "source", "path": "chapter.md", "start_line": 10, "end_line": 12},
            },
            "deps": [{"decl": "Nat", "reason": "domain"}],
        },
        "proof": {
            "nl": {"text": "Use reflexivity.", "origin": {"kind": "source", "path": "chapter.md", "start_line": 13}},
            "deps": [{"decl": "rfl", "reason": "reflexivity"}],
        },
    }


def test_render_statement_and_proof_docstrings_include_marker_and_refs() -> None:
    component = AnnotationComponent()

    statement = component.render_statement_docstring(_revision())
    assert statement.ok
    assert statement.value is not None
    assert "lean-constellation target: foo_bar" in statement.value
    assert "statement.nl:" in statement.value
    assert "chapter.md" in statement.value
    assert '"decl": "Nat"' in statement.value

    proof = component.render_proof_docstring(_revision())
    assert proof.ok
    assert proof.value is not None
    assert "stage: proof" in proof.value
    assert "proof.nl:" in proof.value
    assert "Use reflexivity." in proof.value
    assert '"decl": "rfl"' in proof.value


def test_parse_target_marker_missing_duplicate_and_found() -> None:
    component = AnnotationComponent()
    assert not component.parse_target_marker("theorem foo : True := by trivial").ok

    one = component.parse_target_marker("/--\nlean-constellation target: foo\n-/\ntheorem foo : True := by trivial\n")
    assert one.ok
    assert one.value is not None
    assert one.value.decl_name == "foo"
    assert one.value.marker_line == 2

    duplicate = component.parse_target_marker(
        "/--\nlean-constellation target: foo\n-/\n/--\nlean-constellation target: bar\n-/\n"
    )
    assert not duplicate.ok
    assert duplicate.issues[0].kind == "target_marker_duplicate"


def test_validate_docstring_detects_changed_docstring() -> None:
    component = AnnotationComponent()
    expected = component.render_statement_docstring(_revision()).value
    assert expected is not None
    valid = component.validate_docstring(expected + "\ntheorem foo_bar : True := by trivial\n", decl_name="foo_bar", stage="statement", expected_docstring=expected)
    assert valid.ok
    assert valid.value is not None
    assert valid.value.passed

    changed_text = expected.replace("For every natural number n", "For every integer n")
    changed = component.validate_docstring(changed_text, decl_name="foo_bar", stage="statement", expected_docstring=expected)
    assert changed.ok
    assert changed.value is not None
    assert not changed.value.passed
    assert changed.value.issues[0].kind == "system_docstring_changed"


def test_validate_docstring_invalid_stage_missing_marker_and_decl_mismatch() -> None:
    component = AnnotationComponent()
    expected = component.render_statement_docstring(_revision()).value
    assert expected is not None

    invalid_stage = component.validate_docstring(expected, decl_name="foo_bar", stage="draft", expected_docstring=expected)
    assert not invalid_stage.ok
    assert invalid_stage.issues[0].kind == "docstring_stage_invalid"

    missing_marker = component.validate_docstring(
        "theorem foo_bar : True := by trivial",
        decl_name="foo_bar",
        stage="statement",
        expected_docstring=expected,
    )
    assert missing_marker.ok
    assert missing_marker.value is not None
    assert not missing_marker.value.passed
    assert missing_marker.value.issues[0].kind == "target_marker_missing"

    mismatched_docstring = expected.replace("lean-constellation target: foo_bar", "lean-constellation target: other_decl")
    mismatch = component.validate_docstring(
        mismatched_docstring,
        decl_name="foo_bar",
        stage="statement",
        expected_docstring=expected,
    )
    assert mismatch.ok
    assert mismatch.value is not None
    assert not mismatch.value.passed
    assert {issue.kind for issue in mismatch.value.issues} == {"target_marker_decl_mismatch", "system_docstring_changed"}


def test_locate_target_declaration_found_and_missing() -> None:
    component = AnnotationComponent()
    file_text = "import Mathlib\n\n/-- doc -/\ntheorem foo_bar (n : Nat) : n = n := by\n  rfl\n"
    found = component.locate_target_declaration(file_text, decl_name="foo_bar")
    assert found.ok
    assert found.value is not None
    assert found.value.kind == "theorem"
    assert found.value.start_line == 4

    missing = component.locate_target_declaration(file_text, decl_name="missing")
    assert not missing.ok
    assert missing.issues[0].kind == "target_declaration_missing"


def test_locate_target_declaration_duplicate_and_modifier_quoted_name() -> None:
    component = AnnotationComponent()
    duplicate_text = "theorem foo_bar : True := by trivial\n\ntheorem foo_bar : True := by trivial\n"
    duplicate = component.locate_target_declaration(duplicate_text, decl_name="foo_bar")
    assert not duplicate.ok
    assert duplicate.issues[0].kind == "target_declaration_duplicate"

    modified_quoted_text = "private noncomputable theorem `foo_bar`\n    (n : Nat) : n = n := by\n  rfl\n"
    found = component.locate_target_declaration(modified_quoted_text, decl_name="foo_bar")
    assert found.ok
    assert found.value is not None
    assert found.value.kind == "theorem"
    assert found.value.header_end_line == 2


def test_compare_theorem_header_passes_and_detects_changes() -> None:
    component = AnnotationComponent()
    statement_code = "theorem foo_bar\n    (n : Nat) :\n    n = n := by\n  sorry\n"
    proof_code = "theorem foo_bar (n : Nat) : n = n := by\n  rfl\n"
    same = component.compare_theorem_header(statement_code, proof_code, decl_name="foo_bar")
    assert same.ok
    assert same.value is not None
    assert same.value.passed

    changed_code = "theorem foo_bar (n : Nat) : n + 0 = n := by\n  simpa\n"
    changed = component.compare_theorem_header(statement_code, changed_code, decl_name="foo_bar")
    assert changed.ok
    assert changed.value is not None
    assert not changed.value.passed
    assert changed.value.issues[0].kind == "theorem_header_changed"


def test_compare_theorem_header_non_theorem_and_multiline_by_terminator() -> None:
    component = AnnotationComponent()
    non_theorem = component.compare_theorem_header("def foo_bar : Nat := 0", "def foo_bar : Nat := 1", decl_name="foo_bar")
    assert non_theorem.ok
    assert non_theorem.value is not None
    assert not non_theorem.value.passed
    assert non_theorem.value.issues[0].kind == "target_not_theorem_like"

    statement_code = "lemma foo_bar\n    (h : True) :\n    True\n  by\n    exact h\n"
    proof_code = "lemma foo_bar (h : True) : True by\n  exact h\n"
    same = component.compare_theorem_header(statement_code, proof_code, decl_name="foo_bar")
    assert same.ok
    assert same.value is not None
    assert same.value.passed
