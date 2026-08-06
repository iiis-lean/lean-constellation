from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.lean_check import LeanCheck, LeanDiagnostics
from lean_constellation.services.external_clients import (
    ExternalCommandResult,
    LeanDiagnosticsResult,
    ToolchainDeclarationSoundnessItem,
)
from lean_constellation.services.lean_projection import LeanCheckComponent


class FakeToolkit:
    def __init__(self, diagnostics: LeanDiagnosticsResult) -> None:
        self.diagnostics = diagnostics
        self.calls: list[tuple[Path, Path]] = []

    def run_file_diagnostics(self, repo_root: Path, file_path: Path) -> LeanDiagnosticsResult:
        self.calls.append((Path(repo_root), Path(file_path)))
        return self.diagnostics


class FakeLake:
    def __init__(self, result: ExternalCommandResult | None = None) -> None:
        self.result = result or ExternalCommandResult(
            ok=True,
            command=["lake", "env", "lean", "--json", "Main.lean"],
            cwd=".",
            exit_code=0,
            summary="lake diagnostics ok",
        )
        self.calls: list[tuple[Path, str]] = []

    def run_lake_env_lean(self, *, repo_root: Path, rel_file: str, json: bool = True, timeout_seconds: int | None = None) -> ExternalCommandResult:
        del json, timeout_seconds
        self.calls.append((Path(repo_root), rel_file))
        return self.result


class FakeExternal:
    def __init__(self, toolkit: FakeToolkit, lake: FakeLake | None = None) -> None:
        self.lean_mcp_toolkit = toolkit
        self.lean_toolkit = toolkit
        self.lake = lake or FakeLake()


def _component(tmp_path: Path, diagnostics: list[dict] | None = None, ok: bool = True) -> LeanCheckComponent:
    toolkit = FakeToolkit(
        LeanDiagnosticsResult(
            ok=ok,
            repo_root=str(tmp_path),
            file_path=str(tmp_path / "Main.lean"),
            diagnostics=diagnostics or [],
            summary="toolkit diagnostics",
            issue_code=None if ok else "toolkit_unavailable",
        )
    )
    runtime = make_runtime(external_overrides={"lean_mcp_toolkit": toolkit, "lake": FakeLake()})
    return runtime.lean_projection.lean_check


def test_lean_diagnostics_reject_legacy_absolute_path_schema() -> None:
    with pytest.raises(ValidationError):
        LeanDiagnostics.model_validate(
            {
                "schema_version": 1,
                "repo_root": "/legacy/Repo",
                "file_path": "/legacy/Repo/Main.lean",
                "passed": True,
                "summary": "Legacy diagnostics.",
            }
        )


def test_lean_check_rejects_pre_evidence_schema() -> None:
    with pytest.raises(ValidationError):
        LeanCheck.model_validate(
            {
                "status": "passed",
                "policy": "legacy",
                "allow_sorry": False,
                "contains_sorry": False,
                "contains_axiom": False,
                "message": "Legacy check.",
                "diagnostics": {
                    "schema_version": 2,
                    "passed": True,
                    "summary": "Legacy diagnostics.",
                },
                "scan": {
                    "contains_sorry": False,
                    "contains_admit": False,
                    "contains_axiom": False,
                    "contains_opaque": False,
                    "contains_unsafe": False,
                    "sorry_count": 0,
                    "admit_count": 0,
                    "axiom_count": 0,
                    "opaque_count": 0,
                    "unsafe_count": 0,
                    "summary": "Legacy scan.",
                    "limitation": "Legacy scan.",
                },
            }
        )


def test_run_file_diagnostics_uses_toolkit_and_reports_errors(tmp_path: Path) -> None:
    lean_file = tmp_path / "Main.lean"
    lean_file.write_text("example : True := by trivial\n", encoding="utf-8")
    component = _component(tmp_path, diagnostics=[{"severity": "error", "message": "type mismatch", "line": 1, "column": 10}])

    result = component.run_file_diagnostics(tmp_path, file_path=lean_file)

    assert result.ok
    assert result.value is not None
    assert result.value.passed is False
    assert result.value.diagnostics[0].message == "type mismatch"


def test_run_file_diagnostics_falls_back_to_lake_when_toolkit_unavailable(tmp_path: Path) -> None:
    lean_file = tmp_path / "Main.lean"
    lean_file.write_text("example : True := by trivial\n", encoding="utf-8")
    toolkit = FakeToolkit(LeanDiagnosticsResult(ok=False, repo_root=str(tmp_path), file_path=str(lean_file), summary="no toolkit", issue_code="toolkit_unavailable"))
    lake = FakeLake(
        ExternalCommandResult(
            ok=False,
            command=["lake", "env", "lean", "--json", "Main.lean"],
            cwd=str(tmp_path),
            exit_code=1,
            stdout_excerpt='{"severity":"error","message":"unknown identifier","pos":{"line":1,"column":5}}\n',
            summary="lake failed",
            issue_code="command_failed",
        )
    )
    component = make_runtime(external_overrides={"lean_mcp_toolkit": toolkit, "lake": lake}).lean_projection.lean_check

    result = component.run_file_diagnostics(tmp_path, file_path=Path("Main.lean"))

    assert result.ok
    assert result.value is not None
    assert result.value.passed is False
    assert result.value.diagnostics[0].line == 1
    assert lake.calls == [(tmp_path, "Main.lean")]


def test_run_file_diagnostics_fallback_uses_plain_stderr_excerpt(tmp_path: Path) -> None:
    lean_file = tmp_path / "Main.lean"
    lean_file.write_text("example : True := by trivial\n", encoding="utf-8")
    toolkit = FakeToolkit(LeanDiagnosticsResult(ok=False, repo_root=str(tmp_path), file_path=str(lean_file), summary="no toolkit", issue_code="toolkit_unavailable"))
    lake = FakeLake(
        ExternalCommandResult(
            ok=False,
            command=["lake", "env", "lean", "--json", "Main.lean"],
            cwd=str(tmp_path),
            exit_code=1,
            stderr_excerpt="Main.lean:1:10: error: type mismatch",
            summary="lake failed",
            issue_code="command_failed",
        )
    )
    component = make_runtime(external_overrides={"lean_mcp_toolkit": toolkit, "lake": lake}).lean_projection.lean_check

    result = component.run_file_diagnostics(tmp_path, file_path=lean_file)

    assert result.ok
    assert result.value is not None
    assert result.value.passed is False
    assert result.value.diagnostics[0].severity == "error"
    assert result.value.diagnostics[0].message == "Main.lean:1:10: error: type mismatch"
    assert result.value.raw_excerpt == "Main.lean:1:10: error: type mismatch"


def test_detect_sorry_axiom_ignores_comments_and_strings(tmp_path: Path) -> None:
    component = _component(tmp_path)
    text = '-- sorry in comment\n#eval "axiom in string"\naxiom bad : False\nexample : True := by admit\nopaque hidden : Nat\nunsafe def x := 1\n'

    result = component.detect_sorry_axiom(text)

    assert result.ok
    assert result.value is not None
    assert result.value.sorry_count == 0
    assert result.value.admit_count == 1
    assert result.value.axiom_count == 1
    assert result.value.opaque_count == 1
    assert result.value.unsafe_count == 1


def test_detect_sorry_axiom_handles_nested_comments_strings_and_identifier_boundaries(tmp_path: Path) -> None:
    component = _component(tmp_path)
    text = (
        "/- outer sorry /- nested axiom -/ admit -/\n"
        "#eval \"unsafe axiom sorry\"\n"
        "def sorryful := 1\n"
        "def my_sorry := 1\n"
        "example : True := by\n"
        "  sorry\n"
    )

    result = component.detect_sorry_axiom(text)

    assert result.ok
    assert result.value is not None
    assert result.value.sorry_count == 1
    assert result.value.admit_count == 0
    assert result.value.axiom_count == 0
    assert result.value.unsafe_count == 0
    assert result.value.occurrences[0].line == 6


def test_statement_policy_allows_theorem_sorry_but_not_def_sorry(tmp_path: Path) -> None:
    lean_file = tmp_path / "Main.lean"
    lean_file.write_text("theorem foo : True := by\n  sorry\n", encoding="utf-8")
    component = _component(tmp_path)

    theorem_check = component.build_statement_lean_check(tmp_path, file_path=lean_file, decl_kind="theorem")
    assert theorem_check.ok
    assert theorem_check.value is not None
    assert theorem_check.value.status == "passed"
    assert theorem_check.value.allow_sorry is True

    def_check = component.build_statement_lean_check(tmp_path, file_path=lean_file, decl_kind="def")
    assert def_check.ok
    assert def_check.value is not None
    assert def_check.value.status == "failed"
    assert "contains_sorry" in def_check.value.message


def test_native_managed_import_gate_rejects_imports_after_source_marker(tmp_path: Path) -> None:
    lean_file = tmp_path / "Main.lean"
    lean_file.write_text(
        "-- lean-constellation: managed-imports-begin\n"
        "import Mathlib\n"
        "-- lean-constellation: managed-imports-end\n\n"
        "-- lean-constellation: declaration-source-begin\n"
        "public import Mathlib.Data.Nat.Prime\n"
        "theorem foo : True := by trivial\n",
        encoding="utf-8",
    )
    component = _component(tmp_path)

    result = component.build_proof_lean_check(tmp_path, file_path=lean_file)

    assert result.ok and result.value is not None
    assert result.value.status == "failed"
    assert result.value.managed_import_check is not None
    assert result.value.managed_import_check.checked is True
    assert result.value.managed_import_check.passed is False
    assert result.value.managed_import_check.unmanaged_imports[0].command == "public import"
    assert result.value.managed_import_check.unmanaged_imports[0].line == 6
    finding = next(
        finding
        for finding in result.value.findings
        if finding.code == "decl_unmanaged_import_forbidden" and finding.line is not None
    )
    assert (finding.line, finding.column) == (6, 1)


def test_native_managed_import_gate_ignores_comments_and_strings(tmp_path: Path) -> None:
    lean_file = tmp_path / "Main.lean"
    lean_file.write_text(
        "-- lean-constellation: managed-imports-begin\n"
        "import Mathlib\n"
        "-- lean-constellation: managed-imports-end\n\n"
        "-- lean-constellation: declaration-source-begin\n"
        "-- import Forbidden.Comment\n"
        '#eval "public import Forbidden.String"\n'
        "theorem foo : True := by trivial\n",
        encoding="utf-8",
    )
    component = _component(tmp_path)

    result = component.build_proof_lean_check(tmp_path, file_path=lean_file)

    assert result.ok and result.value is not None
    assert result.value.status == "passed"
    assert result.value.managed_import_check is not None
    assert result.value.managed_import_check.passed is True


def test_native_source_escape_rejects_sorry_ax_despite_statement_sorry_allowance(
    tmp_path: Path,
) -> None:
    lean_file = tmp_path / "Main.lean"
    lean_file.write_text(
        "theorem foo : True := by\n  exact _root_.sorryAx True true\n",
        encoding="utf-8",
    )
    component = _component(tmp_path)

    result = component.build_statement_lean_check(
        tmp_path,
        file_path=lean_file,
        decl_kind="theorem",
    )

    assert result.ok and result.value is not None
    assert result.value.allow_sorry is True
    assert result.value.contains_sorry is False
    assert result.value.status == "failed"
    finding = next(
        finding for finding in result.value.findings if finding.code == "decl_sorry_ax_forbidden"
    )
    assert (finding.line, finding.column) == (2, 9)


def test_native_source_metaprogramming_warning_does_not_fail_check(tmp_path: Path) -> None:
    lean_file = tmp_path / "Main.lean"
    lean_file.write_text(
        'run_cmd Lean.logInfo "review"\ntheorem foo : True := by trivial\n',
        encoding="utf-8",
    )
    component = _component(tmp_path)

    result = component.build_proof_lean_check(tmp_path, file_path=lean_file)

    assert result.ok and result.value is not None
    assert result.value.status == "passed"
    finding = next(
        finding for finding in result.value.findings if finding.code == "decl_run_cmd_review_required"
    )
    assert finding.severity == "warning"


def test_formal_policies_reject_long_line_linter_warning(tmp_path: Path) -> None:
    lean_file = tmp_path / "Main.lean"
    lean_file.write_text("theorem foo : True := by\n  sorry\n", encoding="utf-8")
    component = _component(
        tmp_path,
        diagnostics=[
            {
                "severity": "warning",
                "message": (
                    "This line exceeds the 100 character limit, please shorten it!\n\n"
                    "Note: This linter can be disabled with `set_option linter.style.longLine false`"
                ),
                "line": 1,
                "column": 101,
            }
        ],
    )

    statement = component.build_statement_lean_check(tmp_path, file_path=lean_file, decl_kind="theorem")
    assert statement.ok and statement.value is not None
    assert statement.value.allow_sorry is True
    assert statement.value.status == "failed"
    assert "linter_style_long_line" in statement.value.message

    proof = component.build_proof_lean_check(tmp_path, file_path=lean_file)
    assert proof.ok and proof.value is not None
    assert proof.value.status == "failed"
    assert "linter_style_long_line" in proof.value.message


def test_formal_policies_allow_long_system_managed_imports(tmp_path: Path) -> None:
    lean_file = tmp_path / "Main.lean"
    lean_file.write_text(
        "-- lean-constellation: managed-imports-begin\n"
        "import Project.A.Very.Long.System.Managed.Module.Name.That.Cannot.Be.Wrapped.In.Lean.Source\n"
        "-- lean-constellation: managed-imports-end\n\n"
        "theorem foo : True := by\n"
        "  trivial\n",
        encoding="utf-8",
    )
    component = _component(
        tmp_path,
        diagnostics=[
            {
                "severity": "warning",
                "message": "This line exceeds the 100 character limit, please shorten it!",
                "line": 2,
                "column": 101,
            }
        ],
    )

    proof = component.build_proof_lean_check(tmp_path, file_path=lean_file)

    assert proof.ok and proof.value is not None
    assert proof.value.status == "passed"


def test_formal_policies_allow_only_parser_confirmed_target_marker_line(tmp_path: Path) -> None:
    lean_file = tmp_path / "Main.lean"
    long_name = "target_" + ("x" * 100)
    lean_file.write_text(
        "/--\n"
        f"# lean-constellation target: `{long_name}`\n"
        "-/\n"
        "theorem foo : True := by\n"
        "  trivial\n",
        encoding="utf-8",
    )
    component = _component(
        tmp_path,
        diagnostics=[
            {
                "severity": "warning",
                "message": "This line exceeds the 100 character limit, please shorten it!",
                "line": 2,
                "column": 101,
            }
        ],
    )

    proof = component.build_proof_lean_check(tmp_path, file_path=lean_file)

    assert proof.ok and proof.value is not None
    assert proof.value.status == "passed"


def test_formal_policies_do_not_exempt_other_docstring_long_lines(tmp_path: Path) -> None:
    lean_file = tmp_path / "Main.lean"
    lean_file.write_text(
        "/--\n"
        "# lean-constellation target: `foo`\n"
        f"{'ordinary docstring text ' * 8}\n"
        "-/\n"
        "theorem foo : True := by\n"
        "  trivial\n",
        encoding="utf-8",
    )
    component = _component(
        tmp_path,
        diagnostics=[
            {
                "severity": "warning",
                "message": "This line exceeds the 100 character limit, please shorten it!",
                "line": 3,
                "column": 101,
            }
        ],
    )

    proof = component.build_proof_lean_check(tmp_path, file_path=lean_file)

    assert proof.ok and proof.value is not None
    assert proof.value.status == "failed"
    assert "linter_style_long_line" in proof.value.message


def test_formal_policies_do_not_exempt_pseudo_marker_outside_docstring(tmp_path: Path) -> None:
    lean_file = tmp_path / "Main.lean"
    long_name = "target_" + ("x" * 100)
    lean_file.write_text(
        f"# lean-constellation target: `{long_name}`\n"
        "theorem foo : True := by\n"
        "  trivial\n",
        encoding="utf-8",
    )
    component = _component(
        tmp_path,
        diagnostics=[
            {
                "severity": "warning",
                "message": "This line exceeds the 100 character limit, please shorten it!",
                "line": 1,
                "column": 101,
            }
        ],
    )

    proof = component.build_proof_lean_check(tmp_path, file_path=lean_file)

    assert proof.ok and proof.value is not None
    assert proof.value.status == "failed"
    assert "linter_style_long_line" in proof.value.message


def test_formal_policies_do_not_exempt_non_import_lines_inside_managed_region(tmp_path: Path) -> None:
    lean_file = tmp_path / "Main.lean"
    lean_file.write_text(
        "-- lean-constellation: managed-imports-begin\n"
        "-- malformed non-import content remains actionable\n"
        "-- lean-constellation: managed-imports-end\n\n"
        "theorem foo : True := by\n"
        "  trivial\n",
        encoding="utf-8",
    )
    component = _component(
        tmp_path,
        diagnostics=[
            {
                "severity": "warning",
                "message": "This line exceeds the 100 character limit, please shorten it!",
                "line": 2,
                "column": 101,
            }
        ],
    )

    proof = component.build_proof_lean_check(tmp_path, file_path=lean_file)

    assert proof.ok and proof.value is not None
    assert proof.value.status == "failed"
    assert "linter_style_long_line" in proof.value.message


def test_formal_policies_reject_disabling_long_line_linter(tmp_path: Path) -> None:
    lean_file = tmp_path / "Main.lean"
    lean_file.write_text(
        "set_option linter.style.longLine false\n\ntheorem foo : True := by\n  sorry\n",
        encoding="utf-8",
    )
    component = _component(tmp_path)

    statement = component.build_statement_lean_check(tmp_path, file_path=lean_file, decl_kind="theorem")

    assert statement.ok and statement.value is not None
    assert statement.value.status == "failed"
    assert "linter_style_long_line_disabled" in statement.value.message


@pytest.mark.parametrize(
    ("source", "issue"),
    [
        (
            "set_option linter.unusedDecidableInType false\n\n"
            "theorem foo : True := by\n"
            "  trivial\n",
            "linter_unused_decidable_in_type_disabled",
        ),
        (
            "@[nolint unusedDecidableInType]\n"
            "theorem foo : True := by\n"
            "  trivial\n",
            "linter_unused_decidable_in_type_suppressed",
        ),
    ],
)
def test_formal_policies_reject_unused_decidable_linter_suppression(
    tmp_path: Path,
    source: str,
    issue: str,
) -> None:
    lean_file = tmp_path / "Main.lean"
    lean_file.write_text(source, encoding="utf-8")
    component = _component(tmp_path)

    proof = component.build_proof_lean_check(tmp_path, file_path=lean_file)

    assert proof.ok and proof.value is not None
    assert proof.value.status == "failed"
    assert issue in proof.value.message


def test_proof_policy_and_adapter_trusted_check_are_strict(tmp_path: Path) -> None:
    lean_file = tmp_path / "Main.lean"
    lean_file.write_text("theorem foo : True := by\n  sorry\n", encoding="utf-8")
    component = _component(tmp_path)

    proof = component.build_proof_lean_check(tmp_path, file_path=lean_file)
    assert proof.ok
    assert proof.value is not None
    assert proof.value.status == "failed"

    adapter_pass = component.build_trusted_adapter_check(tmp_path, module="Upstream.Basic", code="theorem foo : True := by trivial", theorem_like=True)
    assert adapter_pass.ok
    assert adapter_pass.value is not None
    assert adapter_pass.value.status == "passed"

    adapter_fail = component.build_trusted_adapter_check(tmp_path, module="Upstream.Basic", code="theorem foo : True := by sorry", theorem_like=True)
    assert adapter_fail.ok
    assert adapter_fail.value is not None
    assert adapter_fail.value.status == "failed"

    adapter_non_theorem_fail = component.build_trusted_adapter_check(tmp_path, module="Upstream.Basic", code="def foo : Nat := by\n  sorry", theorem_like=False)
    assert adapter_non_theorem_fail.ok
    assert adapter_non_theorem_fail.value is not None
    assert adapter_non_theorem_fail.value.status == "failed"
    assert adapter_non_theorem_fail.value.allow_sorry is False


@pytest.mark.parametrize(
    ("axioms", "theorem_like", "expected_status", "expected_code"),
    [
        (["Classical.choice", "propext", "Quot.sound"], True, "passed", "allowed_foundational_axioms"),
        (["sorryAx"], True, "passed", "recursive_sorry_axiom"),
        (["sorryAx"], False, "failed", "recursive_sorry_axiom"),
        (["Upstream.customAxiom"], True, "failed", "forbidden_recursive_axioms"),
    ],
)
def test_adapter_declaration_check_classifies_recursive_axioms(
    tmp_path: Path,
    axioms: list[str],
    theorem_like: bool,
    expected_status: str,
    expected_code: str,
) -> None:
    component = _component(tmp_path)
    result = component.build_adapter_declaration_check(
        tmp_path,
        module="Upstream.Basic",
        declaration_name="Upstream.result",
        code=(
            "theorem result : True := by sorry"
            if theorem_like and "sorryAx" in axioms
            else "theorem result : True := by trivial"
            if theorem_like
            else "def result : Nat := 1"
        ),
        theorem_like=theorem_like,
        soundness=ToolchainDeclarationSoundnessItem(
            module="Upstream.Basic",
            declaration_name="Upstream.result",
            success=True,
            axioms=axioms,
        ),
        raw_excerpt="raw report",
        upstream_revision="abc123",
    )

    assert result.ok and result.value is not None
    assert result.value.status == expected_status
    assert result.value.subject.declaration_name == "Upstream.result"
    assert result.value.fingerprint.upstream_revision == "abc123"
    assert result.value.declaration_soundness is not None
    assert result.value.declaration_soundness.axioms == axioms
    assert expected_code in {finding.code for finding in result.value.findings}
