"""External client wrappers."""

from lean_constellation.services.external_clients.github_repo import (
    GitCheckoutResult,
    GitHubRepoCandidate,
    GitHubRepoClient,
    GitHubRepoClientConfig,
    GitHubRepoSearchResult,
    LeanRepoCandidateProbe,
)
from lean_constellation.services.external_clients.lake_command import (
    LakeCommandClient,
    LakeCommandClientConfig,
    LakeCommandSummaryView,
    LeanCheckSummaryView,
)
from lean_constellation.services.external_clients.lean_mcp_toolkit import (
    LeanDiagnosticsResult,
    LeanMcpToolkitClient,
    LeanMcpToolkitClientConfig,
    MathlibSearchResult,
    SorryAxiomScanResult,
    ToolkitCatalogResult,
    ToolkitCallResult,
    ToolkitResponseWarning,
    ToolkitToolView,
)
from lean_constellation.services.external_clients.material_acquisition import (
    AcquiredArtifactView,
    AcquiredArtifactResult,
    ExtractedMaterialResult,
    MaterialAcquisitionConfig,
    MaterialAcquisitionExtractionClient,
    MaterialTarget,
    ReadableTextValidationView,
)
from lean_constellation.services.external_clients.process import ExternalCommandResult
from lean_constellation.services.external_clients.service import ExternalClientConfig, ExternalClientService

__all__ = [
    "AcquiredArtifactResult",
    "AcquiredArtifactView",
    "ExternalClientConfig",
    "ExternalClientService",
    "ExternalCommandResult",
    "ExtractedMaterialResult",
    "GitCheckoutResult",
    "GitHubRepoCandidate",
    "GitHubRepoClient",
    "GitHubRepoClientConfig",
    "GitHubRepoSearchResult",
    "LakeCommandClient",
    "LakeCommandClientConfig",
    "LakeCommandSummaryView",
    "LeanCheckSummaryView",
    "LeanDiagnosticsResult",
    "LeanMcpToolkitClient",
    "LeanMcpToolkitClientConfig",
    "LeanRepoCandidateProbe",
    "MaterialAcquisitionConfig",
    "MaterialAcquisitionExtractionClient",
    "MaterialTarget",
    "MathlibSearchResult",
    "ReadableTextValidationView",
    "SorryAxiomScanResult",
    "ToolkitCatalogResult",
    "ToolkitCallResult",
    "ToolkitResponseWarning",
    "ToolkitToolView",
]
