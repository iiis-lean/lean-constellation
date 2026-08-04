"""Lean projection service components."""

from lean_constellation.services.lean_projection.adapter_facade import (
    AdapterFacadeComponent,
    AdapterFacadeProvider,
    AdapterModuleListView,
)
from lean_constellation.services.lean_projection.annotation import (
    AnnotationComponent,
    ExternalDeclarationLocationView,
    ExternalDeclarationProbeView,
    LeanDeclarationLocationView,
    PrimaryDeclarationSourceView,
    TargetMarkerView,
)
from lean_constellation.services.lean_projection.decl_file import (
    DeclFileComponent,
    DeclFileRevisionProvider,
    DeclFileCaptureSyncView,
    DeclOwnedLeanFileView,
    FormalCaptureView,
    LeanFileView,
    LeanPathView,
)
from lean_constellation.services.lean_projection.lean_check import (
    LeanCheckComponent,
    LeanCheckView,
    LeanDiagnosticItemView,
    LeanDiagnosticsView,
    SorryAxiomOccurrenceView,
    SorryAxiomScanView,
)
from lean_constellation.services.lean_projection.node_projection import (
    NodeProjectionComponent,
    ProjectionView,
)
from lean_constellation.services.lean_projection.module_identity import (
    CapturedDeclarationIdentityView,
    DeclarationIdentityView,
    ModuleBuildView,
    ModuleIdentityComponent,
    RegisteredDeclarationIdentityView,
)
from lean_constellation.services.lean_projection.repair import (
    ProjectionAuditView,
    ProjectionRepairAction,
    ProjectionRepairView,
    RepairComponent,
    RepairDeclProvider,
)
from lean_constellation.services.lean_projection.service import LeanProjectionService
from lean_constellation.services.lean_projection.safe_apply import (
    FormalApplyStage,
    SafeFormalApplyComponent,
    SafeFormalApplyView,
)
from lean_constellation.services.lean_projection.source_metrics import (
    DeclStatisticsView,
    LeanSourceStatisticsView,
    NodeEntryStatisticsView,
    NodeStatisticsView,
    SourceLineRiskView,
    SourceMarkerAnalysisView,
    SourceMetricView,
    SourceStatisticsError,
    SourceLayerView,
    build_source_statistics,
    render_source_statistics_markdown,
)

__all__ = [
    "AdapterFacadeComponent",
    "AdapterFacadeProvider",
    "AdapterModuleListView",
    "AnnotationComponent",
    "ExternalDeclarationLocationView",
    "ExternalDeclarationProbeView",
    "PrimaryDeclarationSourceView",
    "DeclFileComponent",
    "DeclFileRevisionProvider",
    "DeclFileCaptureSyncView",
    "DeclOwnedLeanFileView",
    "FormalCaptureView",
    "LeanFileView",
    "LeanPathView",
    "LeanCheckComponent",
    "LeanCheckView",
    "LeanDeclarationLocationView",
    "LeanDiagnosticItemView",
    "LeanDiagnosticsView",
    "LeanProjectionService",
    "DeclarationIdentityView",
    "CapturedDeclarationIdentityView",
    "ModuleBuildView",
    "ModuleIdentityComponent",
    "RegisteredDeclarationIdentityView",
    "FormalApplyStage",
    "SafeFormalApplyComponent",
    "SafeFormalApplyView",
    "DeclStatisticsView",
    "LeanSourceStatisticsView",
    "NodeEntryStatisticsView",
    "NodeStatisticsView",
    "SourceLineRiskView",
    "SourceMarkerAnalysisView",
    "SourceMetricView",
    "SourceStatisticsError",
    "SourceLayerView",
    "build_source_statistics",
    "render_source_statistics_markdown",
    "NodeProjectionComponent",
    "ProjectionView",
    "ProjectionAuditView",
    "ProjectionRepairAction",
    "ProjectionRepairView",
    "RepairComponent",
    "RepairDeclProvider",
    "SorryAxiomOccurrenceView",
    "SorryAxiomScanView",
    "TargetMarkerView",
]
