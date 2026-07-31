"""LeanProjectionService composition and public wrappers."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import TYPE_CHECKING

from lean_constellation.services.foundation import GateReport, MutationSummaryView, ServiceResult
from lean_constellation.services.lean_projection.adapter_facade import AdapterFacadeComponent, AdapterFacadeProvider
from lean_constellation.services.lean_projection.annotation import AnnotationComponent
from lean_constellation.services.lean_projection.decl_file import (
    DeclOwnedLeanFileView,
    DeclManagedProjectionRefreshView,
    DeclFileComponent,
    DeclFileRevisionProvider,
    FormalCaptureView,
    LeanFileView,
)
from lean_constellation.services.lean_projection.lean_check import LeanCheckComponent
from lean_constellation.services.lean_projection.module_identity import ModuleIdentityComponent
from lean_constellation.services.lean_projection.node_projection import NodeProjectionComponent, ProjectionView
from lean_constellation.services.lean_projection.repair import (
    ProjectionRepairView,
    RepairComponent,
    RepairDeclProvider,
)
from lean_constellation.services.lean_projection.safe_apply import (
    FormalApplyStage,
    SafeFormalApplyComponent,
    SafeFormalApplyView,
)
from lean_constellation.services.decl_graph.models import DeclDependencyMutationReceipt, DeclState

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class LeanProjectionService:
    """Composition root for Lean projection, checking, capture, and repair."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        adapter_facade: AdapterFacadeComponent | None = None,
        adapter_facade_provider: AdapterFacadeProvider | None = None,
        annotation: AnnotationComponent | None = None,
        lean_check: LeanCheckComponent | None = None,
        module_identity: ModuleIdentityComponent | None = None,
        decl_file: DeclFileComponent | None = None,
        decl_revision_provider: DeclFileRevisionProvider | None = None,
        node_projection: NodeProjectionComponent | None = None,
        repair: RepairComponent | None = None,
        repair_decl_provider: RepairDeclProvider | None = None,
        safe_apply: SafeFormalApplyComponent | None = None,
    ) -> None:
        self.runtime = runtime
        self.adapter_facade = adapter_facade or AdapterFacadeComponent(
            runtime,
            provider=adapter_facade_provider,
        )
        self.annotation = annotation or AnnotationComponent(runtime)
        self.lean_check = lean_check or LeanCheckComponent(runtime)
        self.module_identity = module_identity or ModuleIdentityComponent(runtime)
        self.decl_file = decl_file or DeclFileComponent(
            runtime,
            annotation=self.annotation,
            lean_check=self.lean_check,
            module_identity=self.module_identity,
            revision_provider=decl_revision_provider,
        )
        self.node_projection = node_projection or NodeProjectionComponent(runtime)
        self.repair = repair or RepairComponent(
            runtime,
            node_projection=self.node_projection,
            adapter_facade=self.adapter_facade,
            decl_file=self.decl_file,
            decl_provider=repair_decl_provider,
        )
        self.safe_apply = safe_apply or SafeFormalApplyComponent(
            runtime,
            decl_file=self.decl_file,
            repair=self.repair,
        )

    def current_revision_digest(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[str]:
        return self.safe_apply.current_revision_digest(repo_root, node_path=node_path, decl_name=decl_name)

    def apply_formal_code(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: FormalApplyStage,
        lean_code: str,
        expected_revision: int,
        expected_state: DeclState | str,
        expected_revision_digest: str,
    ) -> ServiceResult[SafeFormalApplyView]:
        return self.safe_apply.apply(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            stage=stage,
            lean_code=lean_code,
            expected_revision=expected_revision,
            expected_state=expected_state,
            expected_revision_digest=expected_revision_digest,
        )

    def apply_statement_formal_code(
        self,
        repo_root: Path,
        **kwargs,
    ) -> ServiceResult[SafeFormalApplyView]:
        return self.apply_formal_code(repo_root, stage="statement", **kwargs)

    def apply_proof_formal_code(
        self,
        repo_root: Path,
        **kwargs,
    ) -> ServiceResult[SafeFormalApplyView]:
        return self.apply_formal_code(repo_root, stage="proof", **kwargs)

    def recapture_reviewer_dependency_mutation(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: FormalApplyStage,
        mutate: Callable[[], ServiceResult[DeclDependencyMutationReceipt]],
    ) -> ServiceResult[DeclDependencyMutationReceipt]:
        return self.safe_apply.recapture_reviewer_dependency_mutation(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            stage=stage,
            mutate=mutate,
        )

    def prepare_statement_formal_stage_file(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
    ) -> ServiceResult[LeanFileView]:
        return self.decl_file.prepare_statement_formal_file(repo_root, node_path=node_path, decl_name=decl_name)

    def refresh_decl_managed_projection(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
    ) -> ServiceResult[DeclManagedProjectionRefreshView]:
        return self.decl_file.refresh_decl_managed_projection(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
        )

    def capture_statement_formal(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
    ) -> ServiceResult[FormalCaptureView]:
        return self.decl_file.capture_statement_formal_file(repo_root, node_path=node_path, decl_name=decl_name)

    def prepare_proof_formal_stage_file(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
    ) -> ServiceResult[LeanFileView]:
        return self.decl_file.prepare_proof_formal_file(repo_root, node_path=node_path, decl_name=decl_name)

    def capture_proof_formal(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
    ) -> ServiceResult[FormalCaptureView]:
        return self.decl_file.capture_proof_formal_file(repo_root, node_path=node_path, decl_name=decl_name)

    def check_decl_file_snapshot_sync(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: str,
    ) -> ServiceResult[GateReport]:
        return self.decl_file.check_decl_file_snapshot_sync(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            stage=stage,
        )

    def read_decl_owned_lean_file(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        revision: int | None = None,
    ) -> ServiceResult[DeclOwnedLeanFileView]:
        return self.decl_file.read_decl_owned_lean_file(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            revision=revision,
        )

    def check_decl_dependency_identity(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: str,
    ) -> ServiceResult[GateReport]:
        return self.decl_file.check_decl_dependency_identity(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            stage=stage,
        )

    def sync_decl_file_after_revision_reset(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
    ) -> ServiceResult[MutationSummaryView]:
        return self.decl_file.sync_decl_file_after_revision_reset(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
        )

    def remove_decl_file_for_delete(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
    ) -> ServiceResult[MutationSummaryView]:
        return self.decl_file.remove_decl_file_for_delete(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
        )

    def refresh_node_projection(self, repo_root: Path, *, node_path: str) -> ServiceResult[ProjectionRepairView]:
        return self.repair.repair_node_projection(repo_root, node_path=node_path)

    def refresh_adapter_projection(self, repo_root: Path) -> ServiceResult[ProjectionView]:
        return self.adapter_facade.refresh_adapter_interfaces(repo_root)

    def restore_projection_to_active_graph(self, repo_root: Path, *, node_path: str) -> ServiceResult[ProjectionRepairView]:
        return self.repair.restore_working_projection_to_active_graph(repo_root, node_path=node_path)
