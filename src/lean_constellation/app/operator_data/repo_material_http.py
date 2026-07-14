"""Strict route declarations for Repo/Material operator operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from lean_constellation.app.operator_data.common import OperatorInputModel
from lean_constellation.app.operator_data.http_support import parse_operator_body
from lean_constellation.app.operator_data.repo_material import (
    DependencyAttachInput,
    LakeBuildInput,
    NativeRepoCreateInput,
    MaterialContextInput,
    RepoConfigUpdateInput,
    ResourceGetInput,
    ResourceListInput,
    SourceCorpusLocalDirInput,
    SourceIndexBlockCreateInput,
    SourceIndexBlockLifecycleInput,
    SourceIndexBlockRefAddInput,
    SourceIndexBlockRefRemoveInput,
    SourceIndexBlockRefUpdateInput,
    SourceIndexBlockUpdateInput,
    SourceIndexCommitInput,
    SourceIndexFileIndexingInput,
    SourceIndexFileSurveyInput,
    SourceIndexLinkCreateInput,
    SourceIndexLinkUpdateInput,
    SourceIndexOpenInput,
    SourceIndexOverviewInput,
)


@dataclass(frozen=True, slots=True)
class RepoMaterialHttpRoute:
    method: Literal["GET", "POST", "PATCH"]
    path: str
    api_method: str
    input_model: type[OperatorInputModel] | None = None


REPO_MATERIAL_HTTP_ROUTES = (
    RepoMaterialHttpRoute("POST", "/admin/operator/workspace/repos/{repo_key}", "create_native_repo", NativeRepoCreateInput),
    RepoMaterialHttpRoute("GET", "/admin/operator/repos/{repo_key}", "inspect_repo"),
    RepoMaterialHttpRoute("GET", "/admin/operator/repos/{repo_key}/config", "get_repo_config"),
    RepoMaterialHttpRoute("PATCH", "/admin/operator/repos/{repo_key}/config", "update_repo_config", RepoConfigUpdateInput),
    RepoMaterialHttpRoute("GET", "/admin/operator/repos/{repo_key}/preparation", "get_preparation_input"),
    RepoMaterialHttpRoute("GET", "/admin/operator/repos/{repo_key}/publication", "get_repo_publication"),
    RepoMaterialHttpRoute("GET", "/admin/operator/repos/{repo_key}/skeleton", "check_native_skeleton"),
    RepoMaterialHttpRoute("GET", "/admin/operator/repos/{repo_key}/workspace", "inspect_workspace"),
    RepoMaterialHttpRoute("GET", "/admin/operator/repos/{repo_key}/availability", "check_provider_availability"),
    RepoMaterialHttpRoute("POST", "/admin/operator/repos/{repo_key}/dependencies", "attach_ready_dependency", DependencyAttachInput),
    RepoMaterialHttpRoute("POST", "/admin/operator/repos/{repo_key}/lake-build", "run_lake_build", LakeBuildInput),
    RepoMaterialHttpRoute("POST", "/admin/operator/repos/{repo_key}/materials/source-corpus/local-dir", "import_local_source_corpus", SourceCorpusLocalDirInput),
    RepoMaterialHttpRoute("GET", "/admin/operator/repos/{repo_key}/materials/source-corpus/manifest", "get_source_corpus_manifest"),
    RepoMaterialHttpRoute("POST", "/admin/operator/repos/{repo_key}/materials/resources/query", "list_resources", ResourceListInput),
    RepoMaterialHttpRoute("POST", "/admin/operator/repos/{repo_key}/materials/resources/get", "get_resource", ResourceGetInput),
    RepoMaterialHttpRoute("POST", "/admin/operator/repos/{repo_key}/materials/context", "get_material_context", MaterialContextInput),
    RepoMaterialHttpRoute("GET", "/admin/operator/repos/{repo_key}/materials/source-index", "get_source_index"),
    RepoMaterialHttpRoute("GET", "/admin/operator/repos/{repo_key}/materials/source-index/coverage", "get_source_index_coverage"),
    RepoMaterialHttpRoute("GET", "/admin/operator/repos/{repo_key}/materials/source-index/committed", "get_committed_source_index"),
    RepoMaterialHttpRoute("GET", "/admin/operator/repos/{repo_key}/materials/source-index/committed/coverage", "get_committed_source_index_coverage"),
    RepoMaterialHttpRoute("POST", "/admin/operator/repos/{repo_key}/materials/source-index/update", "open_source_index_update", SourceIndexOpenInput),
    RepoMaterialHttpRoute("PATCH", "/admin/operator/repos/{repo_key}/materials/source-index/overview", "set_source_index_overview", SourceIndexOverviewInput),
    RepoMaterialHttpRoute("PATCH", "/admin/operator/repos/{repo_key}/materials/source-index/files/survey", "set_source_file_survey", SourceIndexFileSurveyInput),
    RepoMaterialHttpRoute("PATCH", "/admin/operator/repos/{repo_key}/materials/source-index/files/indexing", "set_source_file_indexing", SourceIndexFileIndexingInput),
    RepoMaterialHttpRoute("POST", "/admin/operator/repos/{repo_key}/materials/source-index/blocks", "create_source_block", SourceIndexBlockCreateInput),
    RepoMaterialHttpRoute("PATCH", "/admin/operator/repos/{repo_key}/materials/source-index/blocks", "update_source_block", SourceIndexBlockUpdateInput),
    RepoMaterialHttpRoute("POST", "/admin/operator/repos/{repo_key}/materials/source-index/refs", "add_source_block_ref", SourceIndexBlockRefAddInput),
    RepoMaterialHttpRoute("PATCH", "/admin/operator/repos/{repo_key}/materials/source-index/refs", "update_source_block_ref", SourceIndexBlockRefUpdateInput),
    RepoMaterialHttpRoute("PATCH", "/admin/operator/repos/{repo_key}/materials/source-index/refs/remove", "remove_source_block_ref", SourceIndexBlockRefRemoveInput),
    RepoMaterialHttpRoute("POST", "/admin/operator/repos/{repo_key}/materials/source-index/blocks/refs-done", "mark_source_block_refs_done", SourceIndexBlockLifecycleInput),
    RepoMaterialHttpRoute("POST", "/admin/operator/repos/{repo_key}/materials/source-index/links", "create_source_link", SourceIndexLinkCreateInput),
    RepoMaterialHttpRoute("PATCH", "/admin/operator/repos/{repo_key}/materials/source-index/links", "update_source_link", SourceIndexLinkUpdateInput),
    RepoMaterialHttpRoute("POST", "/admin/operator/repos/{repo_key}/materials/source-index/blocks/links-done", "mark_source_block_links_done", SourceIndexBlockLifecycleInput),
    RepoMaterialHttpRoute("POST", "/admin/operator/repos/{repo_key}/materials/source-index/blocks/completed", "mark_source_block_completed", SourceIndexBlockLifecycleInput),
    RepoMaterialHttpRoute("POST", "/admin/operator/repos/{repo_key}/materials/source-index/commit", "validate_and_commit_source_index", SourceIndexCommitInput),
)


def invoke_repo_material_route(
    api: Any,
    route: RepoMaterialHttpRoute,
    *,
    repo_key: str,
    body: object | None = None,
):  # noqa: ANN201
    """Invoke one frozen route without registering an HTTP server."""

    method = getattr(api, route.api_method)
    if route.input_model is None:
        if body not in (None, {}):
            raise ValueError("Read-only operator routes do not accept a request body.")
        return method(repo_key)
    return method(repo_key, parse_operator_body(route.input_model, body))


__all__ = [
    "REPO_MATERIAL_HTTP_ROUTES",
    "RepoMaterialHttpRoute",
    "invoke_repo_material_route",
]
