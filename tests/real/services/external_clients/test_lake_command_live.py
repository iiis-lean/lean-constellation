from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from lean_constellation.services.external_clients import LakeCommandClient, LakeCommandClientConfig


def _require_lake_and_lean() -> None:
    if shutil.which("lake") is None:
        pytest.skip("`lake` is required for live Lake command tests.")
    if shutil.which("lean") is None:
        pytest.skip("`lean` is required for live Lake command tests.")


def _write_minimal_lake_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "lakefile.toml").write_text(
        'name = "LakeClientReal"\n'
        'version = "0.1.0"\n'
        'defaultTargets = ["Main"]\n\n'
        '[[lean_lib]]\n'
        'name = "Main"\n',
        encoding="utf-8",
    )
    (repo_root / "Main.lean").write_text(
        "def smokeNat : Nat := 1\n"
        "theorem smokeTrue : True := by\n"
        "  trivial\n",
        encoding="utf-8",
    )


def _real_or_minimal_lean_repo_root(tmp_path: Path) -> tuple[Path, str | None]:
    _require_lake_and_lean()
    raw = os.environ.get("LEAN_CONSTELLATION_REAL_LEAN_REPO_ROOT")
    if raw:
        repo_root = Path(raw).expanduser().resolve()
        if not repo_root.is_dir():
            pytest.skip(f"LEAN_CONSTELLATION_REAL_LEAN_REPO_ROOT is not a directory: {repo_root}")
        return repo_root, os.environ.get("LEAN_CONSTELLATION_REAL_LEAN_IMPORT_MODULE")

    repo_root = tmp_path / "LakeClientReal"
    _write_minimal_lake_repo(repo_root)
    return repo_root, "Main"


@pytest.mark.real
def test_lake_live_build_lean_json_snippet_and_timeout(tmp_path: Path) -> None:
    repo_root, module = _real_or_minimal_lean_repo_root(tmp_path)
    client = LakeCommandClient(LakeCommandClientConfig(timeout_seconds=120))

    build = client.run_lake_build(repo_root, timeout_seconds=120)
    assert build.ok, build.summary

    if module:
        import_check = client.run_minimal_import_check(repo_root, module, timeout_seconds=60)
        assert import_check.ok, import_check.summary
        assert import_check.command[:3] == ["lake", "env", "lean"]

    snippet_ok = client.run_snippet_check(repo_root=repo_root, imports=[module] if module else [], code="#check Nat", timeout_seconds=60)
    assert snippet_ok.ok, snippet_ok.summary
    assert snippet_ok.command[:3] == ["lake", "env", "lean"]

    snippet_bad = client.run_snippet_check(
        repo_root=repo_root,
        imports=[module] if module else [],
        code="#check (true : Nat)",
        timeout_seconds=60,
    )
    assert snippet_bad.ok is False
    assert snippet_bad.issue_code == "command_failed"
    assert snippet_bad.diagnostics_excerpt

    timeout = client.run_command(
        repo_root,
        [sys.executable, "-c", "import time; time.sleep(2)"],
        timeout=1,
    )
    assert timeout.ok is False
    assert timeout.timed_out is True
    assert timeout.issue_code == "command_timeout"


@pytest.mark.real
def test_lake_live_lean_json_on_repo_file(tmp_path: Path) -> None:
    repo_root, _module = _real_or_minimal_lean_repo_root(tmp_path)
    candidate = next(
        (
            path
            for path in sorted(repo_root.rglob("*.lean"))
            if ".lake" not in path.parts and path.is_file()
        ),
        None,
    )
    if candidate is None:
        pytest.skip(f"No Lean file found under {repo_root}")
    rel_file = str(candidate.relative_to(repo_root))
    client = LakeCommandClient(LakeCommandClientConfig(timeout_seconds=120))

    result = client.run_lake_env_lean(repo_root=repo_root, rel_file=rel_file, json=True, timeout_seconds=60)

    assert result.command[:4] == ["lake", "env", "lean", "--json"]
    assert result.exit_code is not None
    assert result.stdout_excerpt is not None or result.stderr_excerpt is not None
