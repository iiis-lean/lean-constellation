"""AgentTypeSpec registry and resource validation."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from lean_constellation.agents.models import (
    AgentResourceIssue,
    AgentResourceValidationReport,
    AgentTypeSpec,
)
from lean_constellation.agents.skills import known_skill_keys
from lean_constellation.services.tool_facade import ToolGroupSpec, ToolViewSpec
from lean_constellation.tools import (
    build_application_tool_groups,
    build_application_tool_specs,
    build_application_tool_views,
    build_submit_tool_groups,
    build_submit_tool_specs,
    build_submit_tool_views,
)


COMMON_FRAGMENTS = [
    "common.runtime_contract",
    "common.truth_and_tool_contract",
    "common.submit_contract",
]
BLOCKED_FRAGMENTS = [*COMMON_FRAGMENTS, "common.blocked_escalation_contract"]
REVIEWER_FRAGMENTS = [*COMMON_FRAGMENTS, "common.worker_reviewer_boundary"]
WORKER_FRAGMENTS = [*BLOCKED_FRAGMENTS, "common.worker_reviewer_boundary"]


def build_agent_type_specs() -> list[AgentTypeSpec]:
    return list(AGENT_TYPE_SPECS)


def get_agent_type_spec(agent_type: str) -> AgentTypeSpec:
    key = agent_type.strip()
    for spec in AGENT_TYPE_SPECS:
        if spec.agent_type == key:
            return spec
    raise KeyError(f"unknown AgentType: {key}")


def agent_skill_keys() -> dict[str, list[str]]:
    return {spec.agent_type: list(spec.skill_keys) for spec in AGENT_TYPE_SPECS}


def validate_agent_resources(
    specs: Iterable[AgentTypeSpec] | None = None,
    *,
    skill_keys: Iterable[str] | None = None,
    application_groups: Sequence[ToolGroupSpec] | None = None,
    submit_groups: Sequence[ToolGroupSpec] | None = None,
    application_views: Sequence[ToolViewSpec] | None = None,
    submit_views: Sequence[ToolViewSpec] | None = None,
) -> AgentResourceValidationReport:
    """Validate AgentType skill and ToolView bindings against current registries."""

    resolved_specs = list(specs) if specs is not None else build_agent_type_specs()
    known_skills = set(skill_keys) if skill_keys is not None else known_skill_keys()
    app_groups = list(application_groups) if application_groups is not None else build_application_tool_groups(build_application_tool_specs())
    sub_groups = list(submit_groups) if submit_groups is not None else build_submit_tool_groups(build_submit_tool_specs())
    app_views = list(application_views) if application_views is not None else build_application_tool_views(app_groups)
    sub_views = list(submit_views) if submit_views is not None else build_submit_tool_views(sub_groups)

    app_group_keys = {group.key for group in app_groups}
    submit_group_keys = {group.key for group in sub_groups}
    app_view_by_key = {view.key: view for view in app_views}
    submit_view_by_key = {view.key: view for view in sub_views}

    issues: list[AgentResourceIssue] = []
    seen_agent_types: set[str] = set()
    for spec in resolved_specs:
        if spec.agent_type in seen_agent_types:
            issues.append(
                AgentResourceIssue(
                    code="duplicate_agent_type",
                    message="AgentType is registered more than once.",
                    agent_type=spec.agent_type,
                    resource_type="agent_type",
                    resource_key=spec.agent_type,
                )
            )
        seen_agent_types.add(spec.agent_type)

        for skill_key in spec.skill_keys:
            if skill_key not in known_skills:
                issues.append(
                    AgentResourceIssue(
                        code="skill_not_registered",
                        message="AgentType references an unknown skill.",
                        agent_type=spec.agent_type,
                        resource_type="skill",
                        resource_key=skill_key,
                    )
                )

        _validate_view(
            spec=spec,
            view_key=spec.application_tool_view_key,
            view_by_key=app_view_by_key,
            group_keys=app_group_keys,
            view_kind="application_tool_view",
            issues=issues,
        )
        _validate_view(
            spec=spec,
            view_key=spec.submit_tool_view_key,
            view_by_key=submit_view_by_key,
            group_keys=submit_group_keys,
            view_kind="submit_tool_view",
            issues=issues,
        )

    return AgentResourceValidationReport(ok=not issues, issues=issues)


def _validate_view(
    *,
    spec: AgentTypeSpec,
    view_key: str,
    view_by_key: dict[str, ToolViewSpec],
    group_keys: set[str],
    view_kind: str,
    issues: list[AgentResourceIssue],
) -> None:
    view = view_by_key.get(view_key)
    if view is None:
        issues.append(
            AgentResourceIssue(
                code="tool_view_not_registered",
                message="AgentType references an unknown ToolView.",
                agent_type=spec.agent_type,
                resource_type=view_kind,
                resource_key=view_key,
            )
        )
        return

    aliases = set(spec.agent_type_aliases())
    if aliases.isdisjoint(view.allowed_agent_types):
        issues.append(
            AgentResourceIssue(
                code="agent_type_not_allowed_for_tool_view",
                message="ToolView does not list this AgentType or one of its configured aliases.",
                agent_type=spec.agent_type,
                resource_type=view_kind,
                resource_key=view_key,
                details={
                    "aliases": ",".join(sorted(aliases)),
                    "allowed_agent_types": ",".join(sorted(view.allowed_agent_types)),
                },
            )
        )

    for group_key in view.group_keys:
        if group_key not in group_keys:
            issues.append(
                AgentResourceIssue(
                    code="tool_group_not_registered",
                    message="ToolView references an unknown ToolGroup.",
                    agent_type=spec.agent_type,
                    resource_type="tool_group",
                    resource_key=group_key,
                    details={"view_key": view_key},
                )
            )


def _spec(
    *,
    agent_type: str,
    role: str,
    lifecycle_group: str,
    context_scope: str,
    agent_step_type: str,
    fragments: list[str],
    skills: list[str],
    app_view: str,
    submit_view: str,
    aliases: list[str] | None = None,
    stage: str | None = None,
) -> AgentTypeSpec:
    return AgentTypeSpec(
        agent_type=agent_type,
        role=role,  # type: ignore[arg-type]
        lifecycle_group=lifecycle_group,  # type: ignore[arg-type]
        context_scope=context_scope,  # type: ignore[arg-type]
        agent_step_type=agent_step_type,
        instruction_fragment_keys=fragments,
        specific_instruction_key=agent_type,
        skill_keys=skills,
        application_tool_view_key=app_view,
        submit_tool_view_key=submit_view,
        tool_view_agent_aliases=aliases or [],
        stage=stage,
    )


AGENT_TYPE_SPECS: tuple[AgentTypeSpec, ...] = (
    _spec(
        agent_type="RepoFormatDiscoveryAgent",
        role="coordinator",
        lifecycle_group="repo_lifecycle",
        context_scope="repo",
        agent_step_type="repo_format_discovery_agent_step",
        fragments=[
            *COMMON_FRAGMENTS,
            "workspace.repo_workspace_context",
            "workspace.requirement_and_lake_dependency_context",
            "repo.adapter_repo_context",
        ],
        skills=[],
        app_view="repo_format_discovery",
        submit_view="repo_format_discovery_submit",
        aliases=["RepoFormatDiscovery", "repo_format_discovery"],
    ),
    _spec(
        agent_type="SourceCorpusPrepareAgent",
        role="worker",
        lifecycle_group="repo_lifecycle",
        context_scope="repo",
        agent_step_type="source_corpus_prepare_agent_step",
        fragments=[*BLOCKED_FRAGMENTS, "repo.native_repo_context", "source.source_corpus_context"],
        skills=["material-acquisition"],
        app_view="source_corpus_prepare",
        submit_view="source_corpus_prepare_submit",
        aliases=["SourceCorpusPrepare", "source_corpus_prepare"],
    ),
    _spec(
        agent_type="SourceIndexBuilderAgent",
        role="worker",
        lifecycle_group="repo_lifecycle",
        context_scope="repo",
        agent_step_type="source_index_builder_agent_step",
        fragments=[
            *COMMON_FRAGMENTS,
            "repo.native_repo_context",
            "source.source_corpus_context",
            "source.source_index_context",
            "quality.source_fidelity",
        ],
        skills=[],
        app_view="source_index_builder",
        submit_view="source_index_builder_submit",
        aliases=["SourceIndexBuilder", "source_index_builder"],
    ),
    _spec(
        agent_type="SourceIndexReviewerAgent",
        role="reviewer",
        lifecycle_group="repo_lifecycle",
        context_scope="repo",
        agent_step_type="source_index_reviewer_agent_step",
        fragments=[
            *REVIEWER_FRAGMENTS,
            "repo.native_repo_context",
            "source.source_corpus_context",
            "source.source_index_context",
            "quality.source_fidelity",
            "quality.review_contract",
        ],
        skills=[],
        app_view="source_index_reviewer",
        submit_view="source_index_reviewer_submit",
        aliases=["SourceIndexReviewer", "source_index_reviewer"],
    ),
    _spec(
        agent_type="RootInterfacePrepareAgent",
        role="worker",
        lifecycle_group="repo_lifecycle",
        context_scope="repo",
        agent_step_type="root_interface_prepare_agent_step",
        fragments=[
            *COMMON_FRAGMENTS,
            "repo.native_repo_context",
            "source.source_index_context",
            "scope.scope_contract_exports_context",
            "quality.source_fidelity",
        ],
        skills=["scope-export-interface-curation"],
        app_view="root_interface_prepare",
        submit_view="root_interface_prepare_submit",
        aliases=["RootInterfacePrepare", "root_interface_prepare"],
    ),
    _spec(
        agent_type="AdapterDeclCatalogAgent",
        role="worker",
        lifecycle_group="repo_lifecycle",
        context_scope="repo",
        agent_step_type="adapter_decl_catalog_agent_step",
        fragments=[
            *BLOCKED_FRAGMENTS,
            "workspace.requirement_and_lake_dependency_context",
            "repo.adapter_repo_context",
            "quality.source_fidelity",
            "quality.lean_safety",
        ],
        skills=[],
        app_view="adapter_repo_import",
        submit_view="adapter_repo_import_submit",
        aliases=["AdapterDeclCatalog", "AdapterRepoImport", "AdapterRepoImportAgent", "adapter_repo_import"],
    ),
    _spec(
        agent_type="ResourceCuratorAgent",
        role="worker",
        lifecycle_group="resource_request",
        context_scope="resource_request",
        agent_step_type="resource_curator_agent_step",
        fragments=[*BLOCKED_FRAGMENTS, "resource.resource_library_context"],
        skills=["material-acquisition", "resource-draft-curation"],
        app_view="resource_curator",
        submit_view="resource_curator_submit",
        aliases=["ResourceCurator", "resource_curator"],
    ),
    _spec(
        agent_type="CoordinatorAgent",
        role="coordinator",
        lifecycle_group="coordinator",
        context_scope="repo",
        agent_step_type="coordinator_agent_step",
        fragments=[
            *BLOCKED_FRAGMENTS,
            "workspace.repo_workspace_context",
            "workspace.requirement_and_lake_dependency_context",
            "repo.native_repo_context",
            "repo.adapter_repo_context",
            "source.source_index_context",
            "resource.resource_library_context",
            "node.scope_content_node_context",
            "node.node_tree_decomposition_policy",
            "scope.scope_contract_exports_context",
            "node.node_contract_context",
            "decl.readiness_policy_context",
        ],
        skills=[
            "coordinator-node-decomposition",
            "coordinator-scope-lifecycle",
            "coordinator-content-task-lifecycle",
            "node-contract-design",
            "scope-export-interface-curation",
            "resource-request-handling",
            "external-resource-discovery",
            "mathlib-index-first-recon",
            "mathlib-semantic-search-navigation",
            "mathlib-index-entry-curation",
        ],
        app_view="native_repo_coordinator",
        submit_view="native_repo_coordinator_submit",
        aliases=["NativeRepoCoordinatorAgent", "NativeRepoCoordinator", "native_repo_coordinator", "coordinator"],
    ),
    _spec(
        agent_type="ContentPlanAgent",
        role="plan",
        lifecycle_group="content_node_task",
        context_scope="content_node",
        agent_step_type="content_plan_agent_step",
        fragments=[
            *BLOCKED_FRAGMENTS,
            "repo.native_repo_context",
            "source.source_index_context",
            "resource.resource_library_context",
            "node.scope_content_node_context",
            "node.node_contract_context",
            "content.content_contract_task_context",
            "decl.strategy_round_revision_context",
            "decl.stage_pipeline_context",
            "decl.readiness_policy_context",
            "quality.source_fidelity",
        ],
        skills=[
            "content-contract-reading",
            "visible-node-dependency-recon",
            "mathlib-index-first-recon",
            "mathlib-semantic-search-navigation",
            "mathlib-index-entry-curation",
            "current-node-mathlib-hint-maintenance",
            "external-resource-discovery",
            "resource-request-handling",
            "content-preparation-orchestration",
            "decl-strategy-planning",
            "decl-round-change-planning",
            "decl-round-closeout",
            "content-node-completion-decision",
        ],
        app_view="content_plan",
        submit_view="content_plan_submit",
        aliases=["ContentPlan", "content_plan", "plan"],
    ),
    _spec(
        agent_type="NodeDirDependencyReconAgent",
        role="worker",
        lifecycle_group="content_node_task",
        context_scope="content_node",
        agent_step_type="node_dir_dependency_recon_agent_step",
        fragments=[
            *COMMON_FRAGMENTS,
            "node.scope_content_node_context",
            "node.node_contract_context",
            "content.content_contract_task_context",
        ],
        skills=["content-contract-reading", "visible-node-dependency-recon"],
        app_view="node_dir_dependency_recon",
        submit_view="node_dir_dependency_recon_submit",
        aliases=["NodeDirDependencyRecon", "node_dir_dependency_recon"],
    ),
    _spec(
        agent_type="MathlibReconAgent",
        role="worker",
        lifecycle_group="content_node_task",
        context_scope="content_node",
        agent_step_type="mathlib_recon_agent_step",
        fragments=[
            *COMMON_FRAGMENTS,
            "node.scope_content_node_context",
            "node.node_contract_context",
            "content.content_contract_task_context",
            "quality.source_fidelity",
        ],
        skills=[
            "content-contract-reading",
            "mathlib-index-first-recon",
            "mathlib-semantic-search-navigation",
            "mathlib-index-entry-curation",
            "current-node-mathlib-hint-maintenance",
        ],
        app_view="mathlib_recon",
        submit_view="mathlib_recon_submit",
        aliases=["MathlibRecon", "mathlib_recon"],
    ),
    _spec(
        agent_type="ResourceReconAgent",
        role="worker",
        lifecycle_group="content_node_task",
        context_scope="content_node",
        agent_step_type="resource_recon_agent_step",
        fragments=[
            *BLOCKED_FRAGMENTS,
            "source.source_index_context",
            "resource.resource_library_context",
            "node.node_contract_context",
            "content.content_contract_task_context",
        ],
        skills=["content-contract-reading", "external-resource-discovery", "resource-request-handling"],
        app_view="resource_recon",
        submit_view="resource_recon_submit",
        aliases=["ResourceRecon", "resource_recon"],
    ),
    _spec(
        agent_type="StatementNLWorkerAgent",
        role="worker",
        lifecycle_group="decl_stage",
        context_scope="decl_stage",
        agent_step_type="decl_stage_worker_agent_step",
        fragments=[
            *WORKER_FRAGMENTS,
            "source.source_index_context",
            "resource.resource_library_context",
            "node.node_contract_context",
            "content.content_contract_task_context",
            "decl.stage_pipeline_context",
            "quality.source_fidelity",
        ],
        skills=[
            "content-contract-reading",
            "decl-dependency-origin-curation",
            "mathlib-index-first-recon",
            "mathlib-semantic-search-navigation",
        ],
        app_view="statement_nl_worker",
        submit_view="decl_stage_worker_submit",
        aliases=["StatementNlWorkerAgent", "StatementNlWorker", "statement_nl_worker"],
        stage="statement_nl",
    ),
    _spec(
        agent_type="StatementNLReviewerAgent",
        role="reviewer",
        lifecycle_group="decl_stage",
        context_scope="decl_stage",
        agent_step_type="decl_stage_reviewer_agent_step",
        fragments=[
            *REVIEWER_FRAGMENTS,
            "source.source_index_context",
            "resource.resource_library_context",
            "content.content_contract_task_context",
            "decl.stage_pipeline_context",
            "quality.source_fidelity",
            "quality.review_contract",
        ],
        skills=["content-contract-reading", "decl-dependency-origin-curation"],
        app_view="statement_nl_reviewer",
        submit_view="decl_stage_reviewer_submit",
        aliases=["StatementNlReviewerAgent", "StatementNlReviewer", "statement_nl_reviewer"],
        stage="statement_nl_review",
    ),
    _spec(
        agent_type="StatementFormalWorkerAgent",
        role="worker",
        lifecycle_group="decl_stage",
        context_scope="decl_stage",
        agent_step_type="decl_stage_worker_agent_step",
        fragments=[
            *WORKER_FRAGMENTS,
            "node.node_contract_context",
            "content.content_contract_task_context",
            "decl.stage_pipeline_context",
            "lean.projection_capture_check_context",
            "quality.source_fidelity",
            "quality.lean_safety",
        ],
        skills=[
            "content-contract-reading",
            "decl-owned-lean-file-capture-check",
            "lean-statement-formalization",
            "decl-dependency-origin-curation",
            "mathlib-index-first-recon",
            "mathlib-semantic-search-navigation",
            "mathlib-index-entry-curation",
            "current-node-mathlib-hint-maintenance",
        ],
        app_view="statement_formal_worker",
        submit_view="decl_stage_worker_submit",
        aliases=["StatementFormalWorker", "statement_formal_worker"],
        stage="statement_formal",
    ),
    _spec(
        agent_type="StatementFormalReviewerAgent",
        role="reviewer",
        lifecycle_group="decl_stage",
        context_scope="decl_stage",
        agent_step_type="decl_stage_reviewer_agent_step",
        fragments=[
            *REVIEWER_FRAGMENTS,
            "content.content_contract_task_context",
            "decl.stage_pipeline_context",
            "lean.projection_capture_check_context",
            "quality.source_fidelity",
            "quality.lean_safety",
            "quality.review_contract",
        ],
        skills=["content-contract-reading", "decl-dependency-origin-curation"],
        app_view="statement_formal_reviewer",
        submit_view="decl_stage_reviewer_submit",
        aliases=["StatementFormalReviewer", "statement_formal_reviewer"],
        stage="statement_formal_review",
    ),
    _spec(
        agent_type="ProofNLWorkerAgent",
        role="worker",
        lifecycle_group="decl_stage",
        context_scope="decl_stage",
        agent_step_type="decl_stage_worker_agent_step",
        fragments=[
            *WORKER_FRAGMENTS,
            "source.source_index_context",
            "resource.resource_library_context",
            "content.content_contract_task_context",
            "decl.stage_pipeline_context",
            "quality.source_fidelity",
        ],
        skills=[
            "content-contract-reading",
            "decl-dependency-origin-curation",
            "mathlib-index-first-recon",
            "mathlib-semantic-search-navigation",
        ],
        app_view="proof_nl_worker",
        submit_view="decl_stage_worker_submit",
        aliases=["ProofNlWorkerAgent", "ProofNlWorker", "proof_nl_worker"],
        stage="proof_nl",
    ),
    _spec(
        agent_type="ProofNLReviewerAgent",
        role="reviewer",
        lifecycle_group="decl_stage",
        context_scope="decl_stage",
        agent_step_type="decl_stage_reviewer_agent_step",
        fragments=[
            *REVIEWER_FRAGMENTS,
            "source.source_index_context",
            "resource.resource_library_context",
            "content.content_contract_task_context",
            "decl.stage_pipeline_context",
            "quality.source_fidelity",
            "quality.review_contract",
        ],
        skills=["content-contract-reading", "decl-dependency-origin-curation"],
        app_view="proof_nl_reviewer",
        submit_view="decl_stage_reviewer_submit",
        aliases=["ProofNlReviewerAgent", "ProofNlReviewer", "proof_nl_reviewer"],
        stage="proof_nl_review",
    ),
    _spec(
        agent_type="ProofFormalWorkerAgent",
        role="worker",
        lifecycle_group="decl_stage",
        context_scope="decl_stage",
        agent_step_type="decl_stage_worker_agent_step",
        fragments=[
            *WORKER_FRAGMENTS,
            "content.content_contract_task_context",
            "decl.stage_pipeline_context",
            "lean.projection_capture_check_context",
            "quality.source_fidelity",
            "quality.lean_safety",
        ],
        skills=[
            "content-contract-reading",
            "decl-owned-lean-file-capture-check",
            "lean-proof-formalization",
            "decl-dependency-origin-curation",
            "mathlib-index-first-recon",
            "mathlib-semantic-search-navigation",
            "mathlib-index-entry-curation",
            "current-node-mathlib-hint-maintenance",
        ],
        app_view="proof_formal_worker",
        submit_view="decl_stage_worker_submit",
        aliases=["ProofFormalWorker", "proof_formal_worker"],
        stage="proof_formal",
    ),
    _spec(
        agent_type="ProofFormalReviewerAgent",
        role="reviewer",
        lifecycle_group="decl_stage",
        context_scope="decl_stage",
        agent_step_type="decl_stage_reviewer_agent_step",
        fragments=[
            *REVIEWER_FRAGMENTS,
            "content.content_contract_task_context",
            "decl.stage_pipeline_context",
            "lean.projection_capture_check_context",
            "quality.source_fidelity",
            "quality.lean_safety",
            "quality.review_contract",
        ],
        skills=["content-contract-reading", "decl-dependency-origin-curation"],
        app_view="proof_formal_reviewer",
        submit_view="decl_stage_reviewer_submit",
        aliases=["ProofFormalReviewer", "proof_formal_reviewer"],
        stage="proof_formal_review",
    ),
)


__all__ = [
    "AGENT_TYPE_SPECS",
    "agent_skill_keys",
    "build_agent_type_specs",
    "get_agent_type_spec",
    "validate_agent_resources",
]
