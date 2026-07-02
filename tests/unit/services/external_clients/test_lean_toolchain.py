from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.external_clients import (
    ExternalCommandResult,
    LeanCheckSummaryView,
    LeanMcpToolkitClient,
    LeanToolchainClient,
    LeanToolchainClientConfig,
    LeanToolchainProviderPolicy,
)


class RecordingLake:
    def __init__(self) -> None:
        self.updated: list[Path] = []
        self.built: list[tuple[Path, str | None, list[str] | None]] = []
        self.imports: list[tuple[Path, str]] = []
        self.diagnostics: list[tuple[Path, str]] = []
        self.snippets: list[tuple[Path, list[str], str]] = []
        self.diagnostics_result = ExternalCommandResult(
            ok=True,
            command=["lake", "env", "lean", "--json", "Main.lean"],
            cwd=".",
            exit_code=0,
            stdout_excerpt="",
            summary="lake diagnostics ok",
        )
        self.snippet_ok = True

    def run_lake_update(self, repo_root: Path, timeout_seconds: int | None = None) -> ExternalCommandResult:
        del timeout_seconds
        self.updated.append(Path(repo_root))
        return ExternalCommandResult(ok=True, command=["lake", "update"], cwd=str(repo_root), exit_code=0, summary="update ok")

    def run_lake_build(
        self,
        repo_root: Path,
        target: str | None = None,
        targets: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> ExternalCommandResult:
        del timeout_seconds
        self.built.append((Path(repo_root), target, targets))
        return ExternalCommandResult(ok=True, command=["lake", "build"], cwd=str(repo_root), exit_code=0, summary="build ok")

    def run_minimal_import_check(self, repo_root: Path, module: str, timeout_seconds: int | None = None) -> LeanCheckSummaryView:
        del timeout_seconds
        self.imports.append((Path(repo_root), module))
        return LeanCheckSummaryView(ok=True, module=module, command=["lean"], summary="import ok")

    def run_lake_env_lean(
        self,
        *,
        repo_root: Path,
        rel_file: str,
        json: bool = True,
        timeout_seconds: int | None = None,
    ) -> ExternalCommandResult:
        del json, timeout_seconds
        self.diagnostics.append((Path(repo_root), rel_file))
        return self.diagnostics_result

    def run_snippet_check(
        self,
        *,
        repo_root: Path,
        imports: list[str],
        code: str,
        timeout_seconds: int | None = None,
    ) -> LeanCheckSummaryView:
        del timeout_seconds
        self.snippets.append((Path(repo_root), list(imports), code))
        return LeanCheckSummaryView(
            ok=self.snippet_ok,
            command=["lake", "env", "lean"],
            summary="snippet ok" if self.snippet_ok else "snippet failed",
            diagnostics_excerpt=None if self.snippet_ok else "unknown constant",
            issue_code=None if self.snippet_ok else "command_failed",
        )


def test_runtime_default_toolchain_uses_overridden_lake_and_toolkit(tmp_path: Path) -> None:
    lake = RecordingLake()
    toolkit = LeanMcpToolkitClient(dispatcher=lambda tool_name, payload: {"diagnostics": []})

    runtime = make_runtime(external_overrides={"lake": lake, "lean_mcp_toolkit": toolkit})

    assert runtime.external.lean_toolchain.lake is lake
    assert runtime.external.lean_toolchain.toolkit is toolkit
    build = runtime.external.lean_toolchain.run_lake_build(tmp_path, target="Main")
    assert build.ok
    assert build.provider == "lake_command"
    assert lake.built == [(tmp_path, "Main", None)]


def test_runtime_accepts_direct_fake_toolchain_override() -> None:
    class FakeToolchain:
        pass

    fake = FakeToolchain()
    runtime = make_runtime(external_overrides={"lean_toolchain": fake})

    assert runtime.external.lean_toolchain is fake


def test_diagnostics_uses_toolkit_success_without_lake_fallback(tmp_path: Path) -> None:
    lake = RecordingLake()
    toolkit = LeanMcpToolkitClient(dispatcher=lambda tool_name, payload: {"diagnostics": [{"severity": "error", "message": "bad"}]})
    client = LeanToolchainClient(lake=lake, toolkit=toolkit)

    result = client.run_file_diagnostics(tmp_path, tmp_path / "Main.lean")

    assert result.ok
    assert result.provider == "lean_mcp_toolkit"
    assert result.diagnostics == [{"severity": "error", "message": "bad"}]
    assert lake.diagnostics == []


def test_diagnostics_falls_back_to_lake_and_parses_json_lines(tmp_path: Path) -> None:
    lake = RecordingLake()
    lake.diagnostics_result = ExternalCommandResult(
        ok=False,
        command=["lake", "env", "lean", "--json", "Main.lean"],
        cwd=str(tmp_path),
        exit_code=1,
        stdout_excerpt='{"severity":"error","message":"unknown identifier","pos":{"line":1,"column":5}}\n',
        summary="lake failed",
        issue_code="command_failed",
    )
    toolkit = LeanMcpToolkitClient()
    client = LeanToolchainClient(lake=lake, toolkit=toolkit)

    result = client.run_file_diagnostics(tmp_path, tmp_path / "Main.lean", rel_file="Main.lean")

    assert result.ok
    assert result.provider == "lake_command"
    assert result.fallback_provider == "lean_mcp_toolkit"
    assert result.fallback_reason == "toolkit_unavailable"
    assert result.diagnostics[0]["message"] == "unknown identifier"
    assert lake.diagnostics == [(tmp_path, "Main.lean")]


def test_declaration_repo_nav_mathlib_and_policy_wrappers(tmp_path: Path) -> None:
    calls: list[tuple[str, dict]] = []

    def dispatch(tool_name: str, payload: dict):
        calls.append((tool_name, payload))
        if tool_name == "declarations.extract":
            return {"success": True, "declarations": [{"name": "foo", "full_declaration": "theorem foo : True := by trivial"}]}
        if tool_name == "repo_nav.file_outline":
            return {"success": True, "imports": ["Init"], "declarations": [{"name": "foo"}]}
        if tool_name == "lean_explore.find":
            return {"results": [{"name": payload["query"], "module": "Init"}]}
        raise KeyError(tool_name)

    client = LeanToolchainClient(lake=RecordingLake(), toolkit=LeanMcpToolkitClient(dispatcher=dispatch))

    extracted = client.extract_declaration(tmp_path, "Main", "foo")
    outline = client.outline_repo_file(tmp_path, "Main")
    search = client.search_mathlib_declarations("Nat.add_assoc", limit=1)
    scan = client.scan_sorry_axiom('-- sorry in comment\naxiom bad : False\nexample : True := by admit\n')

    assert extracted.ok
    assert extracted.code == "theorem foo : True := by trivial"
    assert outline.ok and outline.value["imports"] == ["Init"]
    assert search.ok and search.items[0]["name"] == "Nat.add_assoc"
    assert scan.axiom_count == 1
    assert scan.admit_count == 1
    assert scan.sorry_count == 0
    assert [tool for tool, _ in calls] == ["declarations.extract", "repo_nav.file_outline", "lean_explore.find"]


def test_repo_navigation_falls_back_to_local_lean_files_when_toolkit_unavailable(tmp_path: Path) -> None:
    (tmp_path / "Demo.lean").write_text(
        "import Init\n\n"
        "namespace Demo\n\n"
        "theorem smoke : True := by\n"
        "  trivial\n\n"
        "def answer : Nat := 42\n\n"
        "end Demo\n",
        encoding="utf-8",
    )
    client = LeanToolchainClient(lake=RecordingLake(), toolkit=LeanMcpToolkitClient())

    tree = client.list_repo_tree(tmp_path, name_filter="Demo", limit=5)
    outline = client.outline_repo_file(tmp_path, "Demo")
    found = client.find_repo_declarations(tmp_path, query="smoke", module_filter="Demo", limit=5)
    source = client.read_repo_source_window(tmp_path, "Demo", start_line=5, end_line=6)
    extracted = client.extract_declaration(tmp_path, "Demo", "smoke")

    assert tree.ok and tree.provider == "local_repo_fallback"
    assert tree.value["items"][0]["module"] == "Demo"
    assert outline.ok and outline.value["imports"] == ["Init"]
    assert [item["name"] for item in outline.value["declarations"]] == ["smoke", "answer"]
    assert found.ok and found.value["results"][0]["name"] == "smoke"
    assert "5: theorem smoke" in source.value["text"]
    assert extracted.ok
    assert extracted.provider == "local_repo_fallback"
    assert extracted.fallback_provider == "lean_mcp_toolkit"
    assert "theorem smoke" in (extracted.code or "")


def test_mathlib_accessibility_prefers_toolkit_by_default(tmp_path: Path) -> None:
    (tmp_path / "lakefile.toml").write_text('name = "demo"\n', encoding="utf-8")
    lake = RecordingLake()
    lake.snippet_ok = False
    calls: list[tuple[str, dict]] = []

    def dispatch(tool_name: str, payload: dict):
        calls.append((tool_name, payload))
        return {"passed": True, "diagnostics": []}

    toolkit = LeanMcpToolkitClient(dispatcher=dispatch)
    client = LeanToolchainClient(lake=lake, toolkit=toolkit)

    result = client.check_mathlib_name(tmp_path, module="Init", decl_name="Nat.add_assoc")

    assert result.ok
    assert result.passed is True
    assert result.provider == "lean_mcp_toolkit"
    assert result.toolkit_tool == "check_mathlib_name"
    assert calls == [
        (
            "check_mathlib_name",
            {
                "repo_root": str(tmp_path),
                "module": "Init",
                "decl_name": "Nat.add_assoc",
                "code": "import Init\n#check Nat.add_assoc\n",
            },
        )
    ]
    assert lake.snippets == []


def test_mathlib_accessibility_falls_back_to_lake_when_toolkit_unavailable(tmp_path: Path) -> None:
    (tmp_path / "lakefile.toml").write_text('name = "demo"\n', encoding="utf-8")
    lake = RecordingLake()
    toolkit = LeanMcpToolkitClient()
    client = LeanToolchainClient(lake=lake, toolkit=toolkit)

    result = client.check_mathlib_name(tmp_path, module="Init", decl_name="Nat.add_assoc")

    assert result.ok
    assert result.passed is True
    assert result.provider == "lake_command"
    assert result.fallback_provider == "lean_mcp_toolkit"
    assert result.fallback_reason == "mathlib_check_unavailable"
    assert lake.snippets == [(tmp_path, ["Init"], "#check Nat.add_assoc")]


def test_mathlib_accessibility_preserves_lake_provider_failure_after_toolkit_unavailable(tmp_path: Path) -> None:
    class BrokenLake(RecordingLake):
        def run_snippet_check(
            self,
            *,
            repo_root: Path,
            imports: list[str],
            code: str,
            timeout_seconds: int | None = None,
        ) -> LeanCheckSummaryView:
            del timeout_seconds
            self.snippets.append((Path(repo_root), list(imports), code))
            return LeanCheckSummaryView(
                ok=False,
                command=["lake", "env", "lean"],
                summary="lake failed to start",
                issue_code="command_start_failed",
            )

    (tmp_path / "lakefile.toml").write_text('name = "demo"\n', encoding="utf-8")
    lake = BrokenLake()
    client = LeanToolchainClient(lake=lake, toolkit=LeanMcpToolkitClient())

    result = client.check_mathlib_name(tmp_path, module="Init", decl_name="Nat.add_assoc")

    assert result.ok is False
    assert result.passed is False
    assert result.provider == "lake_command"
    assert result.issue_code == "command_start_failed"
    assert result.fallback_provider == "lean_mcp_toolkit"
    assert lake.snippets == [(tmp_path, ["Init"], "#check Nat.add_assoc")]


def test_mathlib_accessibility_can_force_lake_project_first(tmp_path: Path) -> None:
    (tmp_path / "lakefile.toml").write_text('name = "demo"\n', encoding="utf-8")
    lake = RecordingLake()
    lake.snippet_ok = False
    toolkit = LeanMcpToolkitClient(dispatcher=lambda tool_name, payload: {"passed": True, "diagnostics": []})
    client = LeanToolchainClient(
        lake=lake,
        toolkit=toolkit,
        config=LeanToolchainClientConfig(
            provider_policy=LeanToolchainProviderPolicy(mathlib_check_prefer_lake_project=True)
        ),
    )

    result = client.check_mathlib_name(tmp_path, module="Init", decl_name="Nat.nope")

    assert result.ok
    assert result.passed is False
    assert result.provider == "lake_command"
    assert result.diagnostics_excerpt == "unknown constant"
    assert lake.snippets == [(tmp_path, ["Init"], "#check Nat.nope")]
