"""External client service composition."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.external_clients.github_repo import GitHubRepoClient, GitHubRepoClientConfig
from lean_constellation.services.external_clients.lake_command import LakeCommandClient, LakeCommandClientConfig
from lean_constellation.services.external_clients.lean_mcp_toolkit import (
    LeanMcpToolkitClient,
    LeanMcpToolkitClientConfig,
    ToolkitToolView,
)
from lean_constellation.services.external_clients.lean_toolchain import LeanToolchainClient, LeanToolchainClientConfig
from lean_constellation.services.external_clients.material_acquisition import (
    MaterialAcquisitionConfig,
    MaterialAcquisitionExtractionClient,
)

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class ExternalClientConfig(StrictModel):
    lake: LakeCommandClientConfig = Field(default_factory=LakeCommandClientConfig)
    lean_toolkit: LeanMcpToolkitClientConfig = Field(default_factory=LeanMcpToolkitClientConfig)
    lean_toolchain: LeanToolchainClientConfig = Field(default_factory=LeanToolchainClientConfig)
    material: MaterialAcquisitionConfig = Field(default_factory=MaterialAcquisitionConfig)
    github_repo: GitHubRepoClientConfig = Field(default_factory=GitHubRepoClientConfig)


class ExternalClientHealthView(StrictModel):
    lake_available: bool
    lean_available: bool
    git_available: bool
    gh_available: bool
    lean_toolkit_available: bool
    enabled_toolkit_groups: list[str] = Field(default_factory=list)
    missing_toolkit_groups: list[str] = Field(default_factory=list)
    missing_toolkit_tools: list[str] = Field(default_factory=list)
    summary: str


class ExternalClientService:
    def __init__(
        self,
        runtime: LeanRuntimeServices,
        config: ExternalClientConfig | None = None,
        *,
        github_repo: GitHubRepoClient | None = None,
        lake: LakeCommandClient | None = None,
        lean_mcp_toolkit: LeanMcpToolkitClient | None = None,
        lean_toolchain: LeanToolchainClient | None = None,
        material_acquisition: MaterialAcquisitionExtractionClient | None = None,
    ) -> None:
        self.runtime = runtime
        self.config = config or ExternalClientConfig()
        self.github_repo = github_repo or GitHubRepoClient(self.config.github_repo)
        self.lake = lake or LakeCommandClient(self.config.lake)
        self.lean_mcp_toolkit = lean_mcp_toolkit or LeanMcpToolkitClient.from_config(self.config.lean_toolkit)
        self.lean_toolkit = self.lean_mcp_toolkit
        self.lean_toolchain = lean_toolchain or LeanToolchainClient(
            lake=self.lake,
            toolkit=self.lean_mcp_toolkit,
            config=self.config.lean_toolchain,
        )
        self.material_acquisition = material_acquisition or MaterialAcquisitionExtractionClient(self.config.material)

    @property
    def material(self) -> MaterialAcquisitionExtractionClient:
        """Compatibility alias for the material acquisition client."""

        return self.material_acquisition

    def check_external_client_health(
        self,
        *,
        required_toolkit_groups: list[str] | None = None,
        required_toolkit_tools: list[str] | None = None,
    ) -> ExternalClientHealthView:
        required_toolkit_groups = required_toolkit_groups or []
        required_toolkit_tools = required_toolkit_tools or []
        enabled = set(self.config.lean_toolkit.enabled_groups)
        missing_groups = [group for group in required_toolkit_groups if group not in enabled]
        if self.config.lean_toolkit.base_url:
            catalog = self.lean_mcp_toolkit.probe_tool_catalog(required_toolkit_tools)
            toolkit_available = catalog.ok or catalog.issue_code == "toolkit_required_tools_missing"
            missing_tools = catalog.missing_tools if toolkit_available else list(required_toolkit_tools)
        else:
            toolkit_available = self.lean_mcp_toolkit.call_tool("health", {}).ok
            missing_tools = [] if toolkit_available else list(required_toolkit_tools)
        ok_count = sum(
            [
                shutil.which(self.config.lake.lake_bin) is not None,
                shutil.which(self.config.lake.lean_bin) is not None,
                shutil.which(self.config.github_repo.git_bin) is not None,
                shutil.which(self.config.github_repo.gh_bin) is not None,
                toolkit_available,
            ]
        )
        return ExternalClientHealthView(
            lake_available=shutil.which(self.config.lake.lake_bin) is not None,
            lean_available=shutil.which(self.config.lake.lean_bin) is not None,
            git_available=shutil.which(self.config.github_repo.git_bin) is not None,
            gh_available=shutil.which(self.config.github_repo.gh_bin) is not None,
            lean_toolkit_available=toolkit_available,
            enabled_toolkit_groups=sorted(enabled),
            missing_toolkit_groups=missing_groups,
            missing_toolkit_tools=missing_tools,
            summary=f"{ok_count}/5 external client checks available",
        )


def create_external_client_service(runtime: LeanRuntimeServices, config: ExternalClientConfig) -> ExternalClientService:
    return ExternalClientService(runtime, config)
