from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.external_clients import ExternalCommandResult, LeanCheckSummaryView
from lean_constellation.services.lean_projection.annotation import LeanDeclarationLocationView
from lean_constellation.services.lean_projection.module_identity import ModuleIdentityComponent


class RecordingLake:
    def __init__(self, *, snippet_ok: bool = True) -> None:
        self.snippet_ok = snippet_ok
        self.build_target: str | None = None
        self.imports: list[str] = []
        self.code = ""

    def run_lake_build(self, repo_root: Path, target: str | None = None, targets=None, timeout_seconds=None):  # noqa: ANN001, ANN201
        del targets, timeout_seconds
        self.build_target = target
        return ExternalCommandResult(
            ok=True,
            command=["lake", "build", target or ""],
            cwd=str(repo_root),
            exit_code=0,
            summary="built",
        )

    def run_snippet_check(self, *, repo_root: Path, imports: list[str], code: str, timeout_seconds: int | None = None) -> LeanCheckSummaryView:
        del repo_root, timeout_seconds
        self.imports = imports
        self.code = code
        return LeanCheckSummaryView(
            ok=self.snippet_ok,
            command=["lake", "env", "lean"],
            summary="confirmed" if self.snippet_ok else "wrong owner module",
        )


def _location() -> LeanDeclarationLocationView:
    return LeanDeclarationLocationView(
        source_name="actualResult",
        candidate_full_name="WeightedSieve.actualResult",
        namespace="WeightedSieve",
        kind="theorem",
        start_line=12,
        header_end_line=14,
        header="theorem actualResult : True := by",
        summary="candidate",
    )


def test_module_identity_builds_standard_target_and_queries_environment_owner(tmp_path: Path) -> None:
    lake = RecordingLake()
    component = ModuleIdentityComponent(make_runtime(external_overrides={"lake": lake}))
    module = "WeightedSieve.Main.Topic.Theorems.MainResult"

    built = component.build_module(tmp_path, module=module)
    identity = component.confirm_declaration_identity(tmp_path, module=module, location=_location())

    assert built.ok and built.value is not None
    assert lake.build_target == f"+{module}"
    assert built.value.artifacts == [
        ".lake/build/lib/lean/WeightedSieve/Main/Topic/Theorems/MainResult.olean",
        ".lake/build/lib/lean/WeightedSieve/Main/Topic/Theorems/MainResult.ilean",
    ]
    assert identity.ok and identity.value is not None
    assert lake.imports == [module, "Lean"]
    assert "getModuleIdxFor? decl.getId" in lake.code
    assert f"lc_verify_decl_module WeightedSieve.actualResult from {module}" in lake.code


def test_module_identity_rejects_candidate_not_owned_by_decl_module(tmp_path: Path) -> None:
    component = ModuleIdentityComponent(make_runtime(external_overrides={"lake": RecordingLake(snippet_ok=False)}))

    result = component.confirm_declaration_identity(
        tmp_path,
        module="WeightedSieve.Main.Topic.Theorems.MainResult",
        location=_location(),
    )

    assert not result.ok
    assert result.issues[0].kind == "lean_decl_identity_unconfirmed"


def test_module_identity_compares_compiled_probe_kind_and_type(tmp_path: Path) -> None:
    lake = RecordingLake()
    component = ModuleIdentityComponent(make_runtime(external_overrides={"lake": lake}))

    result = component.verify_captured_declaration(
        tmp_path,
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.main_result",
        probe_code="namespace Probe\ntheorem captured : True := by trivial\nend Probe",
        probe_lean_decl_name="Probe.captured",
    )

    assert result.ok and result.value is not None
    assert lake.imports == ["Upstream.Basic", "Lean"]
    assert "lcAdapterConstantKind probeInfo == lcAdapterConstantKind expectedInfo" in lake.code
    assert "probeInfo.levelParams.length == expectedInfo.levelParams.length" in lake.code
    assert "instantiateLevelParams probeInfo.levelParams canonicalLevels" in lake.code
    assert "instantiateLevelParams expectedInfo.levelParams canonicalLevels" in lake.code
    assert "isDefEq probeType expectedType" in lake.code
    assert "lc_verify_captured_decl Probe.captured matches Upstream.Basic.main_result from Upstream.Basic" in lake.code


def test_module_identity_rejects_unconfirmed_captured_semantics(tmp_path: Path) -> None:
    component = ModuleIdentityComponent(make_runtime(external_overrides={"lake": RecordingLake(snippet_ok=False)}))

    result = component.verify_captured_declaration(
        tmp_path,
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.main_result",
        probe_code="namespace Probe\ntheorem captured : False := by trivial\nend Probe",
        probe_lean_decl_name="Probe.captured",
    )

    assert not result.ok
    assert result.issues[0].kind == "adapter_captured_decl_semantics_unconfirmed"
