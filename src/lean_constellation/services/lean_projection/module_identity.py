"""Lake module builds and compiler-confirmed primary declaration identity."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.lean_projection.annotation import LeanDeclarationLocationView
    from lean_constellation.services.runtime import LeanRuntimeServices


_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*$")


class ModuleBuildView(StrictModel):
    module: str
    target: str
    provider: str
    command: list[str]
    artifacts: list[str]
    summary: str


class DeclarationIdentityView(StrictModel):
    lean_decl_name: str
    module: str
    kind: str
    source_name: str
    declaration_line: int
    provider: str
    summary: str


class RegisteredDeclarationIdentityView(StrictModel):
    lean_decl_name: str
    module: str
    provider: str
    summary: str


class CapturedDeclarationIdentityView(StrictModel):
    lean_decl_name: str
    module: str
    probe_lean_decl_name: str
    provider: str
    summary: str


def module_artifact_relpaths(module: str) -> list[str]:
    """Return the standard Lake Lean artifacts owned by one module target."""

    module_path = module.replace(".", "/")
    return [
        f".lake/build/lib/lean/{module_path}.olean",
        f".lake/build/lib/lean/{module_path}.ilean",
    ]


class ModuleIdentityComponent:
    """Build a Lake module and confirm the marker-adjacent declaration in Lean."""

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def build_module(self, repo_root: Path, *, module: str) -> ServiceResult[ModuleBuildView]:
        if _MODULE_RE.fullmatch(module) is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_module_invalid",
                    "Decl.module is not a valid Lean module name.",
                    field="module",
                    current=module,
                )
            )
        target = f"+{module}"
        result = self.runtime.external.lean_toolchain.run_lake_build(Path(repo_root), target=target)
        if not result.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_module_build_failed",
                    result.summary,
                    object_ref=module,
                    details={
                        "target": target,
                        "provider": result.provider,
                        "issue_code": result.issue_code or "",
                    },
                )
            )
        artifacts = module_artifact_relpaths(module)
        return self.runtime.foundation.ok(
            ModuleBuildView(
                module=module,
                target=target,
                provider=result.provider,
                command=result.command,
                artifacts=artifacts,
                summary=f"Built Lean module target {target}.",
            )
        )
    def confirm_declaration_identity(
        self,
        repo_root: Path,
        *,
        module: str,
        location: LeanDeclarationLocationView,
    ) -> ServiceResult[DeclarationIdentityView]:
        candidate = location.candidate_full_name
        if _MODULE_RE.fullmatch(candidate) is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "lean_decl_name_candidate_invalid",
                    "The marker-adjacent declaration did not yield a valid Lean full-name candidate.",
                    object_ref=location.source_name,
                    current=candidate,
                )
            )
        verified = self.verify_registered_declaration(
            repo_root,
            module=module,
            lean_decl_name=candidate,
        )
        if not verified.ok or verified.value is None:
            return self.runtime.foundation.fail(verified.issues)
        return self.runtime.foundation.ok(
            DeclarationIdentityView(
                lean_decl_name=candidate,
                module=module,
                kind=location.kind,
                source_name=location.source_name,
                declaration_line=location.start_line,
                provider=verified.value.provider,
                summary=f"Lean confirmed that {candidate} is declared by {module}.",
            )
        )

    def verify_registered_declaration(
        self,
        repo_root: Path,
        *,
        module: str,
        lean_decl_name: str,
    ) -> ServiceResult[RegisteredDeclarationIdentityView]:
        """Verify an explicitly registered symbol and its owning imported module."""

        if _MODULE_RE.fullmatch(module) is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_module_invalid",
                    "Registered declaration module is not a valid Lean module name.",
                    field="module",
                    current=module,
                )
            )
        if _MODULE_RE.fullmatch(lean_decl_name) is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "lean_decl_name_invalid",
                    "Registered Lean declaration name is not a valid complete Lean name.",
                    field="lean_decl_name",
                    current=lean_decl_name,
                )
            )
        # Lean is authoritative: the command verifies both existence and owner,
        # so a same-named symbol from a transitive import cannot pass.
        semantic_query = f"""
open Lean Elab Command

elab "lc_verify_decl_module " decl:ident " from " moduleName:ident : command => do
  let env ← getEnv
  let some moduleIdx := env.getModuleIdxFor? decl.getId
    | throwError "declaration not imported: {{decl.getId}}"
  let some importedModule := env.header.modules[moduleIdx]?
    | throwError "declaration has invalid module index"
  unless importedModule.module == moduleName.getId do
    throwError "declaration belongs to {{importedModule.module}}, not {{moduleName.getId}}"

lc_verify_decl_module {lean_decl_name} from {module}
""".strip()
        check = self.runtime.external.lean_toolchain.run_snippet_check(
            Path(repo_root),
            imports=[module, "Lean"],
            code=semantic_query,
        )
        if not check.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "lean_decl_identity_unconfirmed",
                    "Lean could not confirm the marker-adjacent declaration full name after building its module.",
                    object_ref=lean_decl_name,
                    current=check.summary,
                    expected=f"a declaration exported by {module}",
                    details={"provider": check.provider, "issue_code": check.issue_code or ""},
                )
            )
        return self.runtime.foundation.ok(
            RegisteredDeclarationIdentityView(
                lean_decl_name=lean_decl_name,
                module=module,
                provider=check.provider,
                summary=f"Lean confirmed that {lean_decl_name} is declared by {module}.",
            )
        )

    def verify_captured_declaration(
        self,
        repo_root: Path,
        *,
        module: str,
        lean_decl_name: str,
        probe_code: str,
        probe_lean_decl_name: str,
    ) -> ServiceResult[CapturedDeclarationIdentityView]:
        """Compile a captured Adapter declaration and compare it with upstream truth."""

        for field, value in [
            ("module", module),
            ("lean_decl_name", lean_decl_name),
            ("probe_lean_decl_name", probe_lean_decl_name),
        ]:
            if _MODULE_RE.fullmatch(value) is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "lean_decl_semantic_probe_identity_invalid",
                        "Adapter semantic probe identity is not a valid Lean dotted name.",
                        field=field,
                        current=value,
                    )
                )
        semantic_query = f"""
{probe_code}

open Lean Elab Command Meta

private def lcAdapterConstantKind : ConstantInfo → String
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "definition"
  | .thmInfo _ => "theorem"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _ => "quotient"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"

elab "lc_verify_captured_decl " probe:ident " matches " expected:ident " from " moduleName:ident : command => do
  let env ← getEnv
  let some expectedInfo := env.find? expected.getId
    | throwError "registered declaration not imported: {{expected.getId}}"
  let some probeInfo := env.find? probe.getId
    | throwError "captured declaration probe was not elaborated: {{probe.getId}}"
  let some moduleIdx := env.getModuleIdxFor? expected.getId
    | throwError "registered declaration has no owning module"
  let some importedModule := env.header.modules[moduleIdx]?
    | throwError "registered declaration has invalid module index"
  unless importedModule.module == moduleName.getId do
    throwError "registered declaration belongs to {{importedModule.module}}, not {{moduleName.getId}}"
  unless lcAdapterConstantKind probeInfo == lcAdapterConstantKind expectedInfo do
    throwError "captured declaration kind does not match the registered declaration"
  unless probeInfo.levelParams.length == expectedInfo.levelParams.length do
    throwError "captured declaration universe parameter count does not match the registered declaration"
  liftTermElabM do
    let canonicalLevels := List.range probeInfo.levelParams.length |>.map fun index =>
      Level.param (Name.mkSimple s!"lc_adapter_u_{{index}}")
    let probeType := probeInfo.type.instantiateLevelParams probeInfo.levelParams canonicalLevels
    let expectedType := expectedInfo.type.instantiateLevelParams expectedInfo.levelParams canonicalLevels
    unless ← isDefEq probeType expectedType do
      throwError "captured declaration type does not match the registered declaration"

lc_verify_captured_decl {probe_lean_decl_name} matches {lean_decl_name} from {module}
""".strip()
        check = self.runtime.external.lean_toolchain.run_snippet_check(
            Path(repo_root),
            imports=[module, "Lean"],
            code=semantic_query,
        )
        if not check.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "adapter_captured_decl_semantics_unconfirmed",
                    "Lean could not confirm that the captured Adapter formal source has the registered declaration kind and type.",
                    object_ref=lean_decl_name,
                    current=check.summary,
                    expected=f"source elaborating to the declaration exported by {module}",
                    details={"provider": check.provider, "issue_code": check.issue_code or ""},
                )
            )
        return self.runtime.foundation.ok(
            CapturedDeclarationIdentityView(
                lean_decl_name=lean_decl_name,
                module=module,
                probe_lean_decl_name=probe_lean_decl_name,
                provider=check.provider,
                summary=f"Lean confirmed captured source semantics for {lean_decl_name}.",
            )
        )


__all__ = [
    "CapturedDeclarationIdentityView",
    "DeclarationIdentityView",
    "ModuleBuildView",
    "ModuleIdentityComponent",
    "RegisteredDeclarationIdentityView",
    "module_artifact_relpaths",
]
