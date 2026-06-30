"""Deterministic consistency checks across repository services."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import GateReport, IssueSeverity, ServiceIssue, ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.adapter import AdapterService
    from lean_constellation.services.lean_projection import LeanProjectionService
    from lean_constellation.services.material import MaterialService
    from lean_constellation.services.node import NodeService
    from lean_constellation.services.runtime import LeanRuntimeServices


ConsistencyCheckScope = Literal["repo", "node", "adapter", "decl"]


class ProjectionSyncSummaryView(StrictModel):
    """Small view for grouped projection sync checks."""

    scope: str
    reports: list[GateReport] = Field(default_factory=list)
    passed: bool
    summary: str


class FormalStageConsistencyProvider(Protocol):
    """Provider for DeclGraph-owned formal-stage consistency checks."""

    def check_formal_stage_consistency(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: str,
    ) -> ServiceResult[GateReport]:
        ...


class _MissingFormalStageConsistencyProvider:
    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def check_formal_stage_consistency(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: str,
    ) -> ServiceResult[GateReport]:
        del repo_root
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_failed(
                "formal_stage_consistency",
                self.runtime.foundation.issue(
                    "formal_stage_provider_missing",
                    "No DeclGraph formal-stage consistency provider is configured.",
                    object_ref=f"{node_path}:{decl_name}",
                    field=stage,
                    suggested_action="Inject a DeclGraph provider before checking formal stage consistency.",
                ),
                summary="Formal-stage consistency provider is missing.",
            )
        )


class ConsistencyCheckComponent:
    """Aggregate deterministic consistency checks without mutating truth."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        material: MaterialService | None = None,
        node: NodeService | None = None,
        adapter: AdapterService | None = None,
        lean_projection: LeanProjectionService | None = None,
        formal_stage_provider: FormalStageConsistencyProvider | None = None,
    ) -> None:
        self.runtime = runtime
        self._material_override = material
        self._node_override = node
        self._lean_projection_override = lean_projection
        self._adapter_override = adapter
        self.formal_stage_provider = formal_stage_provider or _MissingFormalStageConsistencyProvider(runtime)

    @property
    def material(self) -> MaterialService:
        return self._material_override or self.runtime.material

    @property
    def node(self) -> NodeService:
        return self._node_override or self.runtime.node

    @property
    def lean_projection(self) -> LeanProjectionService:
        return self._lean_projection_override or self.runtime.lean_projection

    @property
    def adapter(self) -> AdapterService:
        return self._adapter_override or self.runtime.adapter

    def check_source_corpus_consistency(self, repo_root: Path) -> ServiceResult[GateReport]:
        return self.material.check_source_corpus_draft(Path(repo_root), relpath=".lean_constellation/source")

    def check_source_index_consistency(self, repo_root: Path) -> ServiceResult[GateReport]:
        return self.material.validate_source_index(Path(repo_root))

    def check_contract_consistency(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        reports: list[GateReport] = []

        current = self.node.contract.get_current_contract(Path(repo_root), node_path=node_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)

        dep_gate = self.node.dependency.validate_node_deps(Path(repo_root), node_path=node_path)
        if not dep_gate.ok or dep_gate.value is None:
            return self.runtime.foundation.fail(dep_gate.issues)
        reports.append(dep_gate.value)

        if current.value.node_kind == "scope":
            export_gate = self.node.export.validate_scope_exports(Path(repo_root), scope_path=node_path)
            if not export_gate.ok or export_gate.value is None:
                return self.runtime.foundation.fail(export_gate.issues)
            reports.append(export_gate.value)

        material_refs = self.node.material_ref.list_node_material_refs(Path(repo_root), node_path=node_path)
        if not material_refs.ok or material_refs.value is None:
            return self.runtime.foundation.fail(material_refs.issues)

        warnings: list[ServiceIssue] = []
        if not current.value.contract.mathlib_modules and not current.value.contract.mathlib_decls:
            warnings.append(
                self.runtime.foundation.issue(
                    "contract_mathlib_uses_empty",
                    "No Mathlib module or declaration dependency is recorded on this contract.",
                    severity=IssueSeverity.WARNING,
                    object_ref=node_path,
                    suggested_action="This is acceptable if the node does not need Mathlib-specific imports.",
                )
            )
        reports.append(
            self.runtime.foundation.gate_passed(
                "contract_material_refs",
                summary=material_refs.value.summary,
                warnings=[*material_refs.issues, *warnings],
            )
        )
        return self.runtime.foundation.ok(self.runtime.foundation.merge_gate_reports("contract_consistency", reports))

    def check_formal_stage_consistency(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: Literal["statement", "proof"] | str,
    ) -> ServiceResult[GateReport]:
        provider = self.formal_stage_provider.check_formal_stage_consistency(
            Path(repo_root),
            node_path=node_path,
            decl_name=decl_name,
            stage=str(stage),
        )
        if not provider.ok or provider.value is None:
            return self.runtime.foundation.fail(provider.issues)
        if any(issue.kind == "formal_stage_provider_missing" for issue in provider.value.issues):
            return self.runtime.foundation.ok(provider.value)

        snapshot = self._current_file_snapshot_gate(
            Path(repo_root),
            node_path=node_path,
            decl_name=decl_name,
            stage=str(stage),
        )
        if not snapshot.ok or snapshot.value is None:
            return self.runtime.foundation.fail(snapshot.issues)
        return self.runtime.foundation.ok(
            self.runtime.foundation.merge_gate_reports(
                "formal_stage_consistency",
                [provider.value, snapshot.value],
            )
        )

    def _current_file_snapshot_gate(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: str,
    ) -> ServiceResult[GateReport]:
        normalized_stage = stage.strip().lower()
        if normalized_stage == "statement":
            revision = self.lean_projection.decl_file.revision_provider.get_current_decl_revision(
                Path(repo_root),
                node_path=node_path,
                decl_name=decl_name,
            )
            if not revision.ok or revision.value is None:
                return self.runtime.foundation.fail(revision.issues)
            if self._proof_formal_code(revision.value):
                return self.runtime.foundation.ok(
                    self.runtime.foundation.gate_passed(
                        "decl_file_snapshot_sync",
                        summary=(
                            "Statement formal metadata is checked against DeclGraph; current Lean file "
                            "sync is checked at proof stage because a proof snapshot supersedes the "
                            "statement-stage working file."
                        ),
                    )
                )
        return self.lean_projection.check_decl_file_snapshot_sync(
            Path(repo_root),
            node_path=node_path,
            decl_name=decl_name,
            stage=stage,
        )

    def _proof_formal_code(self, revision: object) -> str | None:
        proof_lean_code = getattr(revision, "proof_lean_code", None)
        if proof_lean_code:
            return str(proof_lean_code)
        proof = getattr(revision, "proof", None)
        if isinstance(proof, dict):
            formal = proof.get("formal")
            if isinstance(formal, dict):
                code = formal.get("code")
                if code:
                    return str(code)
        return None

    def check_adapter_decl_consistency(self, repo_root: Path, *, decl_name: str) -> ServiceResult[GateReport]:
        completeness = self.adapter.check_adapter_decl_completeness(Path(repo_root), name=decl_name)
        if not completeness.ok or completeness.value is None:
            return self.runtime.foundation.fail(completeness.issues)
        reports: list[GateReport] = []
        if completeness.value.complete:
            reports.append(
                self.runtime.foundation.gate_passed(
                    "adapter_decl_completeness",
                    summary=f"Adapter declaration is complete: {decl_name}.",
                )
            )
        else:
            reports.append(
                self.runtime.foundation.gate_failed(
                    "adapter_decl_completeness",
                    completeness.value.issues,
                    summary=completeness.value.summary,
                )
            )
        module = self.adapter.list_registered_adapter_modules(Path(repo_root))
        if not module.ok or module.value is None:
            return self.runtime.foundation.fail(module.issues)
        if not module.value.modules:
            reports.append(
                self.runtime.foundation.gate_failed(
                    "adapter_decl_modules",
                    self.runtime.foundation.issue(
                        "adapter_decl_module_missing",
                        "Adapter declaration catalog has no registered upstream module.",
                        object_ref=decl_name,
                    ),
                    summary="Adapter declaration module metadata is missing.",
                )
            )
        else:
            reports.append(
                self.runtime.foundation.gate_passed(
                    "adapter_decl_modules",
                    summary=f"{len(module.value.modules)} adapter upstream modules are registered.",
                )
            )
        projection = self.adapter.check_adapter_projection(Path(repo_root))
        if not projection.ok or projection.value is None:
            return self.runtime.foundation.fail(projection.issues)
        reports.append(projection.value)
        return self.runtime.foundation.ok(self.runtime.foundation.merge_gate_reports("adapter_decl_consistency", reports))

    def check_projection_sync(
        self,
        repo_root: Path,
        *,
        scope: str = "repo",
    ) -> ServiceResult[GateReport]:
        repo_root = Path(repo_root)
        if scope == "repo":
            return self.lean_projection.repair.full_projection_audit(repo_root)
        if scope == "adapter":
            return self.adapter.check_adapter_projection(repo_root)
        node = self.node.node_tree.get_node(repo_root, path=scope)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        reports = []
        prelude = self.lean_projection.node_projection.check_prelude_sync(repo_root, node_path=scope)
        if not prelude.ok or prelude.value is None:
            return self.runtime.foundation.fail(prelude.issues)
        reports.append(prelude.value)
        interfaces = self.lean_projection.node_projection.check_interfaces_sync(repo_root, node_path=scope)
        if not interfaces.ok or interfaces.value is None:
            return self.runtime.foundation.fail(interfaces.issues)
        reports.append(interfaces.value)
        return self.runtime.foundation.ok(self.runtime.foundation.merge_gate_reports("projection_sync", reports))
