from __future__ import annotations

from pydantic import ValidationError
import pytest

from lean_constellation.domain.lake_project import NativeLakeProjectConfig
from lean_constellation.domain.preparation import AdapterProviderRoute
from lean_constellation.services.external_clients import (
    GitHubCommitHistoryView,
    GitHubLeanRepoProbeView,
)
from tests.unit_services_helpers import make_runtime


LATEST = "a" * 40
OLDER = "b" * 40
COMPATIBLE = "c" * 40


class FakeGitHubRepo:
    def __init__(self, probes: dict[str, GitHubLeanRepoProbeView], *, history: list[str] | None = None) -> None:
        self.probes = probes
        self.history = history or []
        self.probe_calls: list[str | None] = []
        self.history_calls = 0

    def probe_github_lean_repo_candidate(
        self,
        git_url: str,
        revision: str | None = None,
        subdir: str | None = None,
    ) -> GitHubLeanRepoProbeView:
        del git_url, subdir
        self.probe_calls.append(revision)
        return self.probes[revision or "latest"]

    def list_repository_commits(
        self,
        git_url: str,
        *,
        limit: int,
    ) -> GitHubCommitHistoryView:
        self.history_calls += 1
        return GitHubCommitHistoryView(
            git_url=git_url,
            commits=self.history[:limit],
            summary="fixture history",
        )


def _probe(
    revision: str,
    *,
    toolchain: str = "leanprover/lean4:v4.32.0",
    mathlib_revision: str = "v4.32.0",
) -> GitHubLeanRepoProbeView:
    return GitHubLeanRepoProbeView(
        git_url="https://github.com/example/provider",
        normalized_git_url="https://github.com/example/provider",
        resolved_revision=revision,
        is_lean_project=True,
        has_lakefile=True,
        has_lean_toolchain=True,
        package_name="Provider",
        likely_import_modules=["Provider"],
        lakefile_paths=["lakefile.toml"],
        lakefile_excerpt=(
            'name = "Provider"\n\n'
            '[[require]]\nname = "mathlib"\n'
            'scope = "leanprover-community"\n'
            f'rev = "{mathlib_revision}"\n'
            '\n[[lean_lib]]\nname = "Provider"\n'
        ),
        lean_toolchain=toolchain,
        evidence_summary="Remote Lean project fixture.",
    )


def _route(*, revision: str | None) -> AdapterProviderRoute:
    return AdapterProviderRoute(
        git_url="https://github.com/example/provider",
        revision=revision,
        package_name="Provider",
        likely_import_module="Provider",
        evidence_summary="Provider fixture.",
    )


def _runtime(fake: FakeGitHubRepo):
    return make_runtime(
        external_overrides={"github_repo": fake},
        native_lake_project_config=NativeLakeProjectConfig(lean_version="4.32.0"),
    )


def test_explicit_immutable_revision_exact_match_is_preserved() -> None:
    fake = FakeGitHubRepo({COMPATIBLE: _probe(COMPATIBLE)})
    runtime = _runtime(fake)

    result = runtime.repo_workspace.verify_adapter_provider_route(
        _route(revision=COMPATIBLE)
    )

    assert result.ok and result.value is not None
    assert result.value.revision == COMPATIBLE
    assert result.value.revision_resolution == "explicit"
    assert result.value.mathlib_revision == "v4.32.0"
    assert result.value.candidates_checked == [COMPATIBLE]
    assert fake.probe_calls == [COMPATIBLE]
    assert fake.history_calls == 0


def test_probe_derives_package_and_import_module_when_agent_omits_them() -> None:
    fake = FakeGitHubRepo({COMPATIBLE: _probe(COMPATIBLE)})
    runtime = _runtime(fake)
    route = AdapterProviderRoute(
        git_url="https://github.com/example/provider",
        revision=COMPATIBLE,
        evidence_summary="Exact provider candidate.",
    )

    result = runtime.repo_workspace.verify_adapter_provider_route(route)

    assert result.ok and result.value is not None
    assert result.value.package_name == "Provider"
    assert result.value.likely_import_module == "Provider"


def test_verified_receipt_validation_does_not_repeat_remote_probe() -> None:
    fake = FakeGitHubRepo({COMPATIBLE: _probe(COMPATIBLE)})
    runtime = _runtime(fake)
    route = _route(revision=COMPATIBLE)
    verified = runtime.repo_workspace.verify_adapter_provider_route(route)
    assert verified.ok and verified.value is not None
    fake.probe_calls.clear()

    validated = runtime.repo_workspace.validate_verified_adapter_provider_route(
        route,
        verified.value,
    )

    assert validated.ok and validated.value == verified.value
    assert fake.probe_calls == []


def test_explicit_revision_mismatch_fails_without_history_fallback() -> None:
    fake = FakeGitHubRepo(
        {
            LATEST: _probe(
                LATEST,
                toolchain="leanprover/lean4:v4.28.0",
                mathlib_revision="v4.28.0",
            )
        },
        history=[COMPATIBLE],
    )
    runtime = _runtime(fake)

    result = runtime.repo_workspace.verify_adapter_provider_route(
        _route(revision=LATEST)
    )

    assert not result.ok
    assert result.issues[0].kind == "adapter_upstream_toolchain_mismatch"
    assert fake.history_calls == 0


def test_optional_revision_searches_latest_then_bounded_history() -> None:
    fake = FakeGitHubRepo(
        {
            "latest": _probe(
                LATEST,
                toolchain="leanprover/lean4:v4.28.0",
                mathlib_revision="v4.28.0",
            ),
            OLDER: _probe(
                OLDER,
                toolchain="leanprover/lean4:v4.30.0",
                mathlib_revision="v4.30.0",
            ),
            COMPATIBLE: _probe(COMPATIBLE),
        },
        history=[LATEST, OLDER, COMPATIBLE],
    )
    runtime = _runtime(fake)

    result = runtime.repo_workspace.verify_adapter_provider_route(
        _route(revision=None)
    )

    assert result.ok and result.value is not None
    assert result.value.revision == COMPATIBLE
    assert result.value.revision_resolution == "history"
    assert result.value.candidates_checked == [LATEST, OLDER, COMPATIBLE]
    assert fake.probe_calls == [None, OLDER, COMPATIBLE]
    assert fake.history_calls == 1


def test_mathlib_mismatch_is_a_static_fail_fast() -> None:
    fake = FakeGitHubRepo(
        {COMPATIBLE: _probe(COMPATIBLE, mathlib_revision="v4.31.0")}
    )
    runtime = _runtime(fake)

    result = runtime.repo_workspace.verify_adapter_provider_route(
        _route(revision=COMPATIBLE)
    )

    assert not result.ok
    assert result.issues[0].kind == "adapter_upstream_mathlib_revision_mismatch"
    assert result.issues[0].current == "v4.31.0"
    assert result.issues[0].expected == "v4.32.0"


def test_adapter_route_revision_is_optional_but_explicit_values_are_immutable() -> None:
    assert _route(revision=None).revision is None
    with pytest.raises(ValidationError):
        _route(revision="main")
