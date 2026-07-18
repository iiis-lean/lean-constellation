from __future__ import annotations

import os
from pathlib import Path
import shutil

import pytest

from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.external_clients import (
    LakeCommandClient,
    LakeCommandClientConfig,
    LeanMcpToolkitClient,
    LeanMcpToolkitClientConfig,
    LeanToolchainClient,
    LeanToolchainClientConfig,
    LeanToolchainProviderPolicy,
)
from tests.real.lean_tool_latency.bench import (
    LatencyRecorder,
    artifact_dirs,
    has_error_diagnostic,
    latency_iterations,
    latency_timeout,
    service_failed,
    service_ok,
)
from tests.unit_services_helpers import make_runtime


pytestmark = [pytest.mark.real, pytest.mark.slow, pytest.mark.lean_latency]

NODE_PATH = "Main.Topic.Core"
DECL_NAME = "main_result"


def test_local_lake_toolchain_and_formal_capture_latency_matrix(tmp_path: Path) -> None:
    timeout = _require_lake_and_lean()
    iterations = latency_iterations(default=2)
    artifact_dir, mirror_dir = artifact_dirs(tmp_path, "local_lake_toolchain_and_formal_capture")
    recorder = LatencyRecorder(
        test_name="local_lake_toolchain_and_formal_capture",
        artifact_dir=artifact_dir,
        mirror_dir=mirror_dir,
    )
    repo_root = tmp_path / "TinyLake"
    _write_tiny_lake_repo(repo_root)
    lake = LakeCommandClient(LakeCommandClientConfig(timeout_seconds=timeout))
    toolchain_default = LeanToolchainClient(lake=lake, toolkit=LeanMcpToolkitClient())
    toolchain_lake_only = LeanToolchainClient(
        lake=lake,
        toolkit=LeanMcpToolkitClient(),
        config=LeanToolchainClientConfig(
            provider_policy=LeanToolchainProviderPolicy(
                diagnostics_prefer_toolkit=False,
                diagnostics_fallback_to_lake=True,
                mathlib_check_prefer_lake_project=True,
                mathlib_check_fallback_to_toolkit=True,
            )
        ),
    )

    for iteration in range(1, iterations + 1):
        recorder.measure(
            case_id="tiny_lake_build",
            fixture="tiny_lake",
            operation="lake build",
            backend="lake_command",
            iteration=iteration,
            func=lambda: lake.run_lake_build(repo_root, timeout_seconds=timeout),
            validate=service_ok,
        )
        recorder.measure(
            case_id="tiny_direct_lean_good",
            fixture="tiny_lake",
            operation="lake env lean --json Main.lean",
            backend="lake_command",
            iteration=iteration,
            func=lambda: lake.run_lake_env_lean(repo_root=repo_root, rel_file="Main.lean", json=True, timeout_seconds=timeout),
            validate=service_ok,
        )
        recorder.measure(
            case_id="tiny_direct_lean_broken",
            fixture="tiny_lake",
            operation="lake env lean --json Broken.lean",
            backend="lake_command",
            iteration=iteration,
            func=lambda: lake.run_lake_env_lean(repo_root=repo_root, rel_file="Broken.lean", json=True, timeout_seconds=timeout),
            validate=service_failed,
        )
        recorder.measure(
            case_id="tiny_snippet_ok",
            fixture="tiny_lake",
            operation="snippet import Main #check smokeNat",
            backend="lake_command",
            iteration=iteration,
            func=lambda: toolchain_default.run_snippet_check(
                repo_root,
                imports=["Main"],
                code="#check Main.Topic.Core.smokeNat",
                timeout_seconds=timeout,
            ),
            validate=service_ok,
        )
        recorder.measure(
            case_id="tiny_snippet_fail",
            fixture="tiny_lake",
            operation="snippet import Main expected type error",
            backend="lake_command",
            iteration=iteration,
            func=lambda: toolchain_default.run_snippet_check(
                repo_root,
                imports=["Main"],
                code="#check (true : Nat)",
                timeout_seconds=timeout,
            ),
            validate=service_failed,
        )
        recorder.measure(
            case_id="tiny_toolchain_diagnostics_good_fallback",
            fixture="tiny_lake",
            operation="toolchain diagnostics good file with unconfigured toolkit fallback",
            backend="lean_toolchain_default",
            iteration=iteration,
            func=lambda: toolchain_default.run_file_diagnostics(
                repo_root,
                repo_root / "Main.lean",
                rel_file="Main.lean",
                timeout_seconds=timeout,
            ),
            validate=lambda value: service_ok(value) and not has_error_diagnostic(value),
        )
        recorder.measure(
            case_id="tiny_toolchain_diagnostics_good_lake_only",
            fixture="tiny_lake",
            operation="toolchain diagnostics good file lake-only policy",
            backend="lean_toolchain_lake_only",
            iteration=iteration,
            func=lambda: toolchain_lake_only.run_file_diagnostics(
                repo_root,
                repo_root / "Main.lean",
                rel_file="Main.lean",
                timeout_seconds=timeout,
            ),
            validate=lambda value: service_ok(value) and not has_error_diagnostic(value),
        )
        recorder.measure(
            case_id="tiny_toolchain_diagnostics_broken",
            fixture="tiny_lake",
            operation="toolchain diagnostics broken file",
            backend="lean_toolchain_default",
            iteration=iteration,
            func=lambda: toolchain_default.run_file_diagnostics(
                repo_root,
                repo_root / "Broken.lean",
                rel_file="Broken.lean",
                timeout_seconds=timeout,
            ),
            validate=lambda value: service_ok(value) and has_error_diagnostic(value),
        )
        recorder.measure(
            case_id="tiny_check_mathlib_module_init",
            fixture="tiny_lake",
            operation="check_mathlib_module Init",
            backend="lean_toolchain_lake_snippet",
            iteration=iteration,
            func=lambda: toolchain_default.check_mathlib_module(repo_root, module="Init", timeout_seconds=timeout),
            validate=service_ok,
        )

    runtime = make_runtime(
        external_overrides={
            "lake": lake,
            "lean_mcp_toolkit": LeanMcpToolkitClient(),
        }
    )
    round_id = _setup_decl_round(runtime, repo_root)
    refreshed = runtime.lean_projection.refresh_node_projection(repo_root, node_path=NODE_PATH)
    assert refreshed.ok, refreshed.issues

    recorder.measure(
        case_id="tiny_record_mathlib_module_init",
        fixture="tiny_lake",
        operation="record_mathlib_module Init",
        backend="mathlib_service_checked_record",
        iteration=1,
        func=lambda: runtime.mathlib.record_mathlib_module_checked(
            repo_root,
            module_name="Init",
            summary="Latency benchmark Init module.",
            source="Lean latency matrix.",
        ),
        validate=service_ok,
    )
    recorder.measure(
        case_id="tiny_record_mathlib_decl_true_intro",
        fixture="tiny_lake",
        operation="record_mathlib_decl True.intro",
        backend="mathlib_service_checked_record",
        iteration=1,
        func=lambda: runtime.mathlib.record_mathlib_decl_checked(
            repo_root,
            decl_name="True.intro",
            module_name="Init",
            summary="Latency benchmark True.intro.",
            source="Lean latency matrix.",
            kind="theorem",
            signature="True.intro : True",
            snippet="example : True := True.intro",
        ),
        validate=service_ok,
    )
    prepared_statement = recorder.measure(
        case_id="tiny_prepare_statement_formal",
        fixture="tiny_lake",
        operation="prepare_statement_formal_stage_file",
        backend="lean_projection",
        iteration=1,
        func=lambda: runtime.lean_projection.prepare_statement_formal_stage_file(
            repo_root,
            node_path=NODE_PATH,
            decl_name=DECL_NAME,
        ),
        validate=service_ok,
    )
    statement_path = Path(prepared_statement.value.path)
    recorder.measure(
        case_id="tiny_check_statement_formal_policy",
        fixture="tiny_lake",
        operation="build_statement_lean_check prepared statement",
        backend="lean_projection_lean_check",
        iteration=1,
        func=lambda: runtime.lean_projection.lean_check.build_statement_lean_check(
            repo_root,
            file_path=statement_path,
            decl_kind="theorem",
        ),
        validate=service_ok,
    )
    recorder.measure(
        case_id="tiny_capture_statement_formal",
        fixture="tiny_lake",
        operation="capture_statement_formal_file",
        backend="lean_projection_capture",
        iteration=1,
        func=lambda: runtime.lean_projection.capture_statement_formal(
            repo_root,
            node_path=NODE_PATH,
            decl_name=DECL_NAME,
        ),
        validate=service_ok,
    )
    proof_nl = runtime.decl_graph.write_proof_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=DECL_NAME,
        nl="Use triviality.",
        origin=[{"kind": "lean_latency"}],
        deps=[],
    )
    assert proof_nl.ok, proof_nl.issues
    prepared_proof = recorder.measure(
        case_id="tiny_prepare_proof_formal",
        fixture="tiny_lake",
        operation="prepare_proof_formal_stage_file",
        backend="lean_projection",
        iteration=1,
        func=lambda: runtime.lean_projection.prepare_proof_formal_stage_file(
            repo_root,
            node_path=NODE_PATH,
            decl_name=DECL_NAME,
        ),
        validate=service_ok,
    )
    proof_path = Path(prepared_proof.value.path)
    proof_path.write_text(proof_path.read_text(encoding="utf-8").replace("sorry", "trivial"), encoding="utf-8")
    recorder.measure(
        case_id="tiny_check_proof_formal_policy",
        fixture="tiny_lake",
        operation="build_proof_lean_check completed proof",
        backend="lean_projection_lean_check",
        iteration=1,
        func=lambda: runtime.lean_projection.lean_check.build_proof_lean_check(
            repo_root,
            file_path=proof_path,
        ),
        validate=service_ok,
    )
    recorder.measure(
        case_id="tiny_capture_proof_formal",
        fixture="tiny_lake",
        operation="capture_proof_formal_file after policy check",
        backend="lean_projection_capture",
        iteration=1,
        func=lambda: runtime.lean_projection.capture_proof_formal(
            repo_root,
            node_path=NODE_PATH,
            decl_name=DECL_NAME,
        ),
        validate=service_ok,
    )

    recorder.export()
    recorder.assert_required_validated()


def test_mathlib_cached_repo_lake_and_checked_record_latency_matrix(tmp_path: Path) -> None:
    timeout = _require_lake_and_lean()
    iterations = _mathlib_iterations(default=1)
    template_root = _mathlib_template_root()
    if template_root is None:
        pytest.skip("No Mathlib template repo found. Set LEAN_CONSTELLATION_REAL_LEAN_TEMPLATE_ROOT.")
    artifact_dir, mirror_dir = artifact_dirs(tmp_path, "mathlib_cached_repo_lake_and_checked_record")
    recorder = LatencyRecorder(
        test_name="mathlib_cached_repo_lake_and_checked_record",
        artifact_dir=artifact_dir,
        mirror_dir=mirror_dir,
    )
    repo_root = tmp_path / "MathlibCached"
    _write_mathlib_cached_repo(repo_root, template_root)
    lake = LakeCommandClient(LakeCommandClientConfig(timeout_seconds=timeout))
    toolchain = LeanToolchainClient(lake=lake, toolkit=LeanMcpToolkitClient())
    runtime = make_runtime(
        external_overrides={
            "lake": lake,
            "lean_mcp_toolkit": LeanMcpToolkitClient(),
        }
    )

    for iteration in range(1, iterations + 1):
        recorder.measure(
            case_id="mathlib_direct_lean_full_import",
            fixture="mathlib_cached_repo",
            operation="lake env lean --json MathlibSmoke.lean",
            backend="lake_command",
            iteration=iteration,
            func=lambda: lake.run_lake_env_lean(
                repo_root=repo_root,
                rel_file="MathlibSmoke.lean",
                json=True,
                timeout_seconds=timeout,
            ),
            validate=service_ok,
        )
        recorder.measure(
            case_id="mathlib_direct_lean_broken",
            fixture="mathlib_cached_repo",
            operation="lake env lean --json MathlibBroken.lean",
            backend="lake_command",
            iteration=iteration,
            func=lambda: lake.run_lake_env_lean(
                repo_root=repo_root,
                rel_file="MathlibBroken.lean",
                json=True,
                timeout_seconds=timeout,
            ),
            validate=service_failed,
        )
        recorder.measure(
            case_id="mathlib_toolchain_diagnostics_full_import",
            fixture="mathlib_cached_repo",
            operation="toolchain diagnostics MathlibSmoke.lean",
            backend="lean_toolchain_default",
            iteration=iteration,
            func=lambda: toolchain.run_file_diagnostics(
                repo_root,
                repo_root / "MathlibSmoke.lean",
                rel_file="MathlibSmoke.lean",
                timeout_seconds=timeout,
            ),
            validate=lambda value: service_ok(value) and not has_error_diagnostic(value),
        )
        recorder.measure(
            case_id="mathlib_snippet_import_mathlib",
            fixture="mathlib_cached_repo",
            operation="snippet import Mathlib #check Nat.add_assoc",
            backend="lake_command",
            iteration=iteration,
            func=lambda: toolchain.run_snippet_check(
                repo_root,
                imports=["Mathlib"],
                code="#check Nat.add_assoc",
                timeout_seconds=timeout,
            ),
            validate=service_ok,
        )
        recorder.measure(
            case_id="mathlib_check_module_mathlib",
            fixture="mathlib_cached_repo",
            operation="check_mathlib_module Mathlib",
            backend="lean_toolchain_lake_snippet",
            iteration=iteration,
            func=lambda: toolchain.check_mathlib_module(repo_root, module="Mathlib", timeout_seconds=timeout),
            validate=service_ok,
        )
        recorder.measure(
            case_id="mathlib_check_name_nat_add_assoc",
            fixture="mathlib_cached_repo",
            operation="check_mathlib_name Nat.add_assoc from Mathlib",
            backend="lean_toolchain_lake_snippet",
            iteration=iteration,
            func=lambda: toolchain.check_mathlib_name(
                repo_root,
                module="Mathlib",
                decl_name="Nat.add_assoc",
                timeout_seconds=timeout,
            ),
            validate=service_ok,
        )

    recorder.measure(
        case_id="mathlib_record_module_mathlib",
        fixture="mathlib_cached_repo",
        operation="record_mathlib_module Mathlib",
        backend="mathlib_service_checked_record",
        iteration=1,
        func=lambda: runtime.mathlib.record_mathlib_module_checked(
            repo_root,
            module_name="Mathlib",
            summary="Latency benchmark full Mathlib import.",
            source="Lean latency matrix.",
        ),
        validate=service_ok,
    )
    recorder.measure(
        case_id="mathlib_record_decl_nat_add_assoc",
        fixture="mathlib_cached_repo",
        operation="record_mathlib_decl Nat.add_assoc",
        backend="mathlib_service_checked_record",
        iteration=1,
        func=lambda: runtime.mathlib.record_mathlib_decl_checked(
            repo_root,
            decl_name="Nat.add_assoc",
            module_name="Mathlib",
            summary="Latency benchmark Nat.add_assoc.",
            source="Lean latency matrix.",
            kind="theorem",
            signature="Nat.add_assoc : forall (n m k : Nat), n + m + k = n + (m + k)",
            snippet="#check Nat.add_assoc",
        ),
        validate=service_ok,
    )

    recorder.export()
    recorder.assert_required_validated()


@pytest.mark.real_toolkit
def test_live_toolkit_latency_matrix(tmp_path: Path) -> None:
    timeout = latency_timeout(default=180)
    base_url = os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL")
    if not base_url:
        pytest.skip("Set LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL to run live Toolkit latency matrix.")
    iterations = _toolkit_iterations(default=1)
    artifact_dir, mirror_dir = artifact_dirs(tmp_path, "live_toolkit")
    recorder = LatencyRecorder(test_name="live_toolkit", artifact_dir=artifact_dir, mirror_dir=mirror_dir)
    toolkit = LeanMcpToolkitClient.from_config(
        LeanMcpToolkitClientConfig(
            base_url=base_url,
            api_prefix=os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_API_PREFIX", "/api/v1"),
            auth_token=os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_AUTH_TOKEN"),
            timeout_seconds=timeout,
        )
    )
    template_root = _mathlib_template_root()
    repo_root = tmp_path / "ToolkitVisible"
    if template_root is not None:
        _write_mathlib_cached_repo(repo_root, template_root)
        target = "MathlibSmoke.lean"
    else:
        _write_tiny_lake_repo(repo_root)
        target = "Main.lean"
    lake = LakeCommandClient(LakeCommandClientConfig(timeout_seconds=timeout))
    toolchain = LeanToolchainClient(lake=lake, toolkit=toolkit)

    recorder.measure(
        case_id="toolkit_probe_catalog",
        fixture="live_toolkit",
        operation="probe_tool_catalog diagnostics/search/navigation",
        backend="lean_mcp_toolkit",
        iteration=1,
        func=lambda: toolkit.probe_tool_catalog(
            [
                "diagnostics.file",
                "lsp.run_snippet",
                "run_snippet",
                "lean_explore.find",
                "mathlib_nav.file_outline",
            ]
        ),
        validate=lambda value: service_ok(value) or getattr(value, "issue_code", None) == "toolkit_required_tools_missing",
    )
    for iteration in range(1, iterations + 1):
        recorder.measure(
            case_id="toolkit_direct_diagnostics",
            fixture="live_toolkit",
            operation=f"toolkit diagnostics.file {target}",
            backend="lean_mcp_toolkit",
            iteration=iteration,
            func=lambda: toolkit.run_file_diagnostics(repo_root, repo_root / target),
            validate=service_ok,
        )
        recorder.measure(
            case_id="toolchain_toolkit_diagnostics",
            fixture="live_toolkit",
            operation=f"toolchain diagnostics via toolkit {target}",
            backend="lean_toolchain_toolkit_preferred",
            iteration=iteration,
            func=lambda: toolchain.run_file_diagnostics(
                repo_root,
                repo_root / target,
                rel_file=target,
                timeout_seconds=timeout,
            ),
            validate=service_ok,
        )
        recorder.measure(
            case_id="toolkit_lsp_snippet",
            fixture="live_toolkit",
            operation="toolkit lsp.run_snippet import Mathlib",
            backend="lean_mcp_toolkit",
            iteration=iteration,
            func=lambda: toolkit.call_tool(
                "lsp.run_snippet",
                {"repo_root": str(repo_root), "code": "import Mathlib\n#check Nat.add_assoc\n", "include_diagnostics": True},
            ),
            validate=service_ok,
        )
        recorder.measure(
            case_id="toolkit_run_snippet",
            fixture="live_toolkit",
            operation="toolkit run_snippet import Mathlib",
            backend="lean_mcp_toolkit",
            iteration=iteration,
            func=lambda: toolkit.call_tool(
                "run_snippet",
                {"repo_root": str(repo_root), "code": "import Mathlib\n#check Nat.add_assoc\n", "include_diagnostics": True},
            ),
            validate=service_ok,
        )
        recorder.measure(
            case_id="toolkit_mathlib_search",
            fixture="live_toolkit",
            operation="toolchain search_mathlib_declarations Nat.add_assoc",
            backend="lean_mcp_toolkit",
            iteration=iteration,
            func=lambda: toolchain.search_mathlib_declarations("Nat.add_assoc", limit=5),
            validate=service_ok,
        )
        recorder.measure(
            case_id="toolkit_inspect_mathlib_module",
            fixture="live_toolkit",
            operation="toolchain inspect_mathlib_module Mathlib",
            backend="lean_mcp_toolkit",
            iteration=iteration,
            func=lambda: toolchain.inspect_mathlib_module("Mathlib"),
            validate=service_ok,
        )

    recorder.export()
    required = {
        "toolkit_probe_catalog",
        "toolkit_direct_diagnostics",
        "toolchain_toolkit_diagnostics",
    }
    recorder.assert_required_validated(case_ids=required)


def _require_lake_and_lean() -> int:
    for command in ("lake", "lean"):
        if shutil.which(command) is None:
            pytest.skip(f"`{command}` is required for Lean latency benchmarks.")
    return latency_timeout(default=180)


def _mathlib_iterations(*, default: int) -> int:
    return max(1, int(os.environ.get("LEAN_CONSTELLATION_LEAN_LATENCY_MATHLIB_ITERATIONS", str(default))))


def _toolkit_iterations(*, default: int) -> int:
    return max(1, int(os.environ.get("LEAN_CONSTELLATION_LEAN_LATENCY_TOOLKIT_ITERATIONS", str(default))))


def _write_tiny_lake_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "lakefile.toml").write_text(
        'name = "TinyLake"\n'
        'version = "0.1.0"\n'
        'defaultTargets = ["Main"]\n\n'
        "[[lean_lib]]\n"
        'name = "Main"\n',
        encoding="utf-8",
    )
    (repo_root / "Main.lean").write_text(
        "import Main.Topic.Core.Prelude\n"
        "import Main.Topic.Core.Interfaces\n",
        encoding="utf-8",
    )
    prelude = repo_root / "Main" / "Topic" / "Core" / "Prelude.lean"
    prelude.parent.mkdir(parents=True, exist_ok=True)
    prelude.write_text(
        "namespace Main.Topic.Core\n\n"
        "def smokeNat : Nat := 1\n\n"
        "theorem smokeTrue : True := by\n"
        "  trivial\n\n"
        "end Main.Topic.Core\n",
        encoding="utf-8",
    )
    interfaces = repo_root / "Main" / "Topic" / "Core" / "Interfaces.lean"
    interfaces.write_text(
        "import Main.Topic.Core.Prelude\n\n"
        "namespace Main.Topic.Core\n\n"
        "theorem interfaceTrue : True := by\n"
        "  trivial\n\n"
        "end Main.Topic.Core\n",
        encoding="utf-8",
    )
    (repo_root / "Broken.lean").write_text("def brokenNat : Nat := true\n", encoding="utf-8")


def _write_mathlib_cached_repo(repo_root: Path, template_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    for name in ("lakefile.toml", "lake-manifest.json", "lean-toolchain"):
        source = template_root / name
        if source.exists():
            shutil.copyfile(source, repo_root / name)
    lake_cache = template_root / ".lake"
    if lake_cache.exists() and not (repo_root / ".lake").exists():
        (repo_root / ".lake").symlink_to(lake_cache, target_is_directory=True)
    (repo_root / "MathlibSmoke.lean").write_text(
        "import Mathlib\n\n"
        "#check Nat.add_assoc\n\n"
        "example (n : Nat) : n + 0 = n := by\n"
        "  simpa\n",
        encoding="utf-8",
    )
    (repo_root / "MathlibBroken.lean").write_text(
        "import Mathlib\n\n"
        "def brokenNat : Nat := true\n",
        encoding="utf-8",
    )


def _mathlib_template_root() -> Path | None:
    explicit = os.environ.get("LEAN_CONSTELLATION_REAL_LEAN_TEMPLATE_ROOT") or os.environ.get(
        "LEAN_CONSTELLATION_LOCAL_LAKE_CACHE_PROJECT_ROOT"
    )
    if explicit:
        root = Path(explicit).expanduser()
        return root if root.is_dir() else None
    return None


def _setup_decl_round(runtime, repo_root: Path) -> str:
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    created_scope = runtime.node.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal.", boundary="Topic boundary.")
    if not created_scope.ok:
        assert any(issue.kind == "node_path_exists" for issue in created_scope.issues), created_scope.issues
    created_content = runtime.node.create_content_node(
        repo_root,
        path=NODE_PATH,
        goal="Core goal.",
        boundary="Core declarations only.",
        objective="Run Lean latency capture/check.",
        success_criteria="Statement and proof captures are checked by real diagnostics.",
    )
    if not created_content.ok:
        assert any(issue.kind == "node_path_exists" for issue in created_content.issues), created_content.issues
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=NODE_PATH, objective="Lean latency strategy.")
    assert strategy.ok and strategy.value is not None, strategy.issues
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Lean latency declaration round.",
    )
    assert round_record.ok and round_record.value is not None, round_record.issues
    created = runtime.decl_graph.create_decl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        name=DECL_NAME,
        kind="theorem",
        objective="Create a trivial theorem.",
        summary="A trivial theorem for Lean latency capture.",
        public=False,
        target_state=DeclState.PROVED,
    )
    assert created.ok, created.issues
    started = runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_record.value.round_id)
    assert started.ok, started.issues
    statement = runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        decl_name=DECL_NAME,
        nl="The main result states True.",
        origin=[{"kind": "lean_latency"}],
        deps=[],
    )
    assert statement.ok, statement.issues
    return round_record.value.round_id
