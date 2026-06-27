"""Foundation service and components."""

from lean_constellation.services.foundation.index import (
    IndexBuildContext,
    IndexBuilder,
    IndexBundle,
    IndexComponent,
    IndexMetadata,
    IndexRebuildView,
)
from lean_constellation.services.foundation.layout import (
    DeclFileKey,
    FoundationContext,
    LayoutComponent,
    LayoutPathView,
    PathBoundaryView,
    RepoLayoutView,
)
from lean_constellation.services.foundation.ref_resolver import (
    RefKind,
    RefResolveContext,
    RefResolver,
    RefResolverComponent,
    RefValidationResult,
    ResolvedRef,
    ResolvedRefView,
)
from lean_constellation.services.foundation.result_error import (
    GateReport,
    IssueSeverity,
    MutationSummaryView,
    ResultErrorComponent,
    ServiceIssue,
    ServiceResult,
    ToolResultView,
)
from lean_constellation.services.foundation.service import FoundationService
from lean_constellation.services.foundation.store import (
    MutationCommitResult,
    MutationSession,
    OpenVersionResult,
    StoreComponent,
    StoreWriteResult,
    WriteMode,
)

__all__ = [
    "DeclFileKey",
    "FoundationContext",
    "FoundationService",
    "GateReport",
    "IndexBuildContext",
    "IndexBuilder",
    "IndexBundle",
    "IndexComponent",
    "IndexMetadata",
    "IndexRebuildView",
    "IssueSeverity",
    "LayoutComponent",
    "LayoutPathView",
    "MutationCommitResult",
    "MutationSession",
    "MutationSummaryView",
    "OpenVersionResult",
    "PathBoundaryView",
    "RefKind",
    "RefResolveContext",
    "RefResolver",
    "RefResolverComponent",
    "RefValidationResult",
    "RepoLayoutView",
    "ResolvedRef",
    "ResolvedRefView",
    "ResultErrorComponent",
    "ServiceIssue",
    "ServiceResult",
    "StoreComponent",
    "StoreWriteResult",
    "ToolResultView",
    "WriteMode",
]
