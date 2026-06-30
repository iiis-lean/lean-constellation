from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from lean_constellation.flows.common.flow_requests import (
    build_content_node_task_request,
    build_decl_round_request,
    build_preparation_recon_request,
    build_resource_curation_request,
)


class _StrictParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResourceCurationParams(_StrictParams):
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"]
    target: str
    arxiv_version: str | None = None
    requested_by: str | None = None
    context_summary: str | None = None


class ContentNodeTaskParams(_StrictParams):
    repo_key: str
    node_path: str
    contract_version: int | None = None
    task_mode: str


class PreparationReconParams(_StrictParams):
    repo_key: str
    node_path: str
    objective: str | None = None
    context_summary: str | None = None


class DeclRoundParams(_StrictParams):
    repo_key: str
    node_path: str
    strategy_id: str
    round_id: str
    round_index: int | None = None
    summary: str | None = None


def test_flow_request_builders_preserve_business_params() -> None:
    resource = build_resource_curation_request(
        scope_id="repo:Main",
        target_kind="arxiv",
        target="2501.12345",
        arxiv_version="v2",
        requested_by="plan",
        context_summary="Need proof.",
    )
    assert resource.flow_type == "resource_curation"
    assert resource.params["target_kind"] == "arxiv"
    assert resource.params["context_summary"] == "Need proof."

    content = build_content_node_task_request(repo_key="Repo", node_path="Main.Core", scope_id="scope_node", contract_version=2)
    assert content.flow_type == "content_node_task"
    assert content.params["node_path"] == "Main.Core"
    assert content.params["contract_version"] == 2

    recon = build_preparation_recon_request(recon_kind="mathlib", repo_key="Repo", node_path="Main.Core", scope_id="scope_node")
    assert recon.flow_type == "mathlib_recon"

    decl = build_decl_round_request(repo_key="Repo", node_path="Main.Core", scope_id="scope_node", strategy_id="s1", round_id="r1")
    assert decl.flow_type == "decl_graph_round"
    assert decl.params["strategy_id"] == "s1"


def test_flow_request_builder_params_validate_against_child_flow_input_models() -> None:
    resource = build_resource_curation_request(
        scope_id="repo:Main",
        target_kind="arxiv",
        target="2501.12345",
        arxiv_version="v2",
        requested_by="plan",
        context_summary="Need proof.",
    )
    assert ResourceCurationParams.model_validate(resource.params).target_kind == "arxiv"

    content = build_content_node_task_request(
        repo_key="Repo",
        node_path="Main.Core",
        scope_id="scope_node",
        contract_version=2,
        task_mode="rerun",
    )
    assert ContentNodeTaskParams.model_validate(content.params).contract_version == 2

    recon = build_preparation_recon_request(
        recon_kind="node_dir_dependency",
        repo_key="Repo",
        node_path="Main.Core",
        scope_id="scope_node",
        objective="Check reusable siblings.",
        context_summary="Initial content task.",
    )
    assert PreparationReconParams.model_validate(recon.params).objective == "Check reusable siblings."

    decl = build_decl_round_request(
        repo_key="Repo",
        node_path="Main.Core",
        scope_id="scope_node",
        strategy_id="s1",
        round_id="r1",
        round_index=3,
        summary="Start round.",
    )
    assert DeclRoundParams.model_validate(decl.params).round_index == 3
