"""AgentTypeSpec registry and resource validation."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum

from lean_constellation.agents.keys import ProductionAgentTypeKey, SkillKey
from lean_constellation.agents.models import (
    AgentResourceIssue,
    AgentResourceValidationReport,
    AgentTypeSpec,
)
from lean_constellation.agents.skills import SKILL_DEFINITIONS, known_skill_keys
from lean_constellation.services.tool_facade import ToolGroupSpec, ToolViewSpec
from lean_constellation.tools import (
    build_application_tool_groups,
    build_application_tool_specs,
    build_application_tool_views,
    build_submit_tool_groups,
    build_submit_tool_specs,
    build_submit_tool_views,
)
from lean_constellation.tools.keys import ApplicationToolViewKey as AppView
from lean_constellation.tools.keys import SubmitToolViewKey as SubmitView


StringKey = str | StrEnum


def _value(value: StringKey) -> str:
    return value.value if isinstance(value, StrEnum) else str(value)


def _values(values: Iterable[StringKey]) -> list[str]:
    return [_value(value) for value in values]


COMMON_FRAGMENTS = [
    "common.runtime_contract",
    "common.submit_contract",
]
BLOCKED_FRAGMENTS = [*COMMON_FRAGMENTS, "common.blocked_escalation_contract"]
REVIEWER_FRAGMENTS = [*COMMON_FRAGMENTS, "common.worker_reviewer_boundary"]
WORKER_FRAGMENTS = [*BLOCKED_FRAGMENTS, "common.worker_reviewer_boundary"]


def build_agent_type_specs(
    *,
    extra_specs: Iterable[AgentTypeSpec] | None = None,
) -> list[AgentTypeSpec]:
    return _resolve_agent_type_specs(extra_specs=extra_specs)


def get_agent_type_spec(
    agent_type: str,
    *,
    specs: Iterable[AgentTypeSpec] | None = None,
) -> AgentTypeSpec:
    key = agent_type.strip()
    for spec in _coerce_agent_type_specs(specs):
        if spec.agent_type == key:
            return spec
    raise KeyError(f"unknown AgentType: {key}")


def agent_skill_keys(
    *,
    specs: Iterable[AgentTypeSpec] | None = None,
) -> dict[str, list[str]]:
    return {spec.agent_type: list(spec.skill_keys) for spec in _coerce_agent_type_specs(specs)}


def derive_agent_type_spec(
    *,
    base_agent_type: str,
    agent_type: str,
    specs: Iterable[AgentTypeSpec] | None = None,
    **overrides: object,
) -> AgentTypeSpec:
    """Create a derived AgentTypeSpec that inherits ToolView permissions from a base type."""

    base = get_agent_type_spec(base_agent_type, specs=specs)
    data = base.model_dump()
    data.update(
        {
            "agent_type": agent_type,
            "extends_agent_type": base.agent_type,
        }
    )
    data.update(overrides)
    return AgentTypeSpec(**data)


def agent_type_permission_names(
    agent_type: str,
    *,
    specs: Iterable[AgentTypeSpec] | None = None,
) -> set[str]:
    """Return this AgentType, aliases, and inherited base AgentType names."""

    key = agent_type.strip()
    by_type = {spec.agent_type: spec for spec in _coerce_agent_type_specs(specs)}
    names: set[str] = set()
    visiting: set[str] = set()
    current = key
    while current:
        if current in visiting:
            raise ValueError(f"AgentType inheritance cycle includes {current}")
        visiting.add(current)
        spec = by_type.get(current)
        if spec is None:
            raise KeyError(f"unknown AgentType: {current}")
        names.update(spec.agent_type_aliases())
        current = spec.extends_agent_type or ""
    return names


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
    warnings: list[AgentResourceIssue] = []
    seen_agent_types: set[str] = set()
    agent_types = {spec.agent_type for spec in resolved_specs}
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
        if spec.extends_agent_type and spec.extends_agent_type not in agent_types:
            issues.append(
                AgentResourceIssue(
                    code="agent_type_extends_unknown",
                    message="AgentType extends an unknown base AgentType.",
                    agent_type=spec.agent_type,
                    resource_type="agent_type",
                    resource_key=spec.extends_agent_type,
                )
            )

        try:
            permission_names = agent_type_permission_names(spec.agent_type, specs=resolved_specs)
        except ValueError as exc:
            permission_names = set(spec.agent_type_aliases())
            issues.append(
                AgentResourceIssue(
                    code="agent_type_inheritance_cycle",
                    message="AgentType inheritance contains a cycle.",
                    agent_type=spec.agent_type,
                    resource_type="agent_type",
                    resource_key=spec.agent_type,
                    details={"error": str(exc)},
                )
            )
        except KeyError as exc:
            permission_names = set(spec.agent_type_aliases())
            issues.append(
                AgentResourceIssue(
                    code="agent_type_extends_unknown",
                    message="AgentType extends an unknown base AgentType.",
                    agent_type=spec.agent_type,
                    resource_type="agent_type",
                    resource_key=spec.extends_agent_type or spec.agent_type,
                    details={"error": str(exc)},
                )
            )

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
            permission_names=permission_names,
            issues=issues,
        )
        _validate_view(
            spec=spec,
            view_key=spec.submit_tool_view_key,
            view_by_key=submit_view_by_key,
            group_keys=submit_group_keys,
            view_kind="submit_tool_view",
            permission_names=permission_names,
            issues=issues,
        )
        _validate_skill_required_tool_group_coverage(
            spec=spec,
            application_view_by_key=app_view_by_key,
            submit_view_by_key=submit_view_by_key,
            warnings=warnings,
        )

    return AgentResourceValidationReport(ok=not issues, issues=issues, warnings=warnings)


def _validate_view(
    *,
    spec: AgentTypeSpec,
    view_key: str,
    view_by_key: dict[str, ToolViewSpec],
    group_keys: set[str],
    view_kind: str,
    permission_names: set[str],
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

    if permission_names.isdisjoint(view.allowed_agent_types):
        issues.append(
            AgentResourceIssue(
                code="agent_type_not_allowed_for_tool_view",
                message="ToolView does not list this AgentType, aliases, or inherited base AgentType.",
                agent_type=spec.agent_type,
                resource_type=view_kind,
                resource_key=view_key,
                details={
                    "permission_names": ",".join(sorted(permission_names)),
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


def _validate_skill_required_tool_group_coverage(
    *,
    spec: AgentTypeSpec,
    application_view_by_key: dict[str, ToolViewSpec],
    submit_view_by_key: dict[str, ToolViewSpec],
    warnings: list[AgentResourceIssue],
) -> None:
    app_view = application_view_by_key.get(spec.application_tool_view_key)
    submit_view = submit_view_by_key.get(spec.submit_tool_view_key)
    if app_view is None or submit_view is None:
        return
    visible_groups = set(app_view.group_keys) | set(submit_view.group_keys)
    for skill_key in spec.skill_keys:
        skill = SKILL_DEFINITIONS.get(skill_key)
        if skill is None:
            continue
        missing_groups = sorted(set(skill.required_tool_groups) - visible_groups)
        if not missing_groups:
            continue
        warnings.append(
            AgentResourceIssue(
                code="skill_required_tool_group_missing",
                message="AgentType declares a skill whose required tool groups are not covered by its ToolViews.",
                agent_type=spec.agent_type,
                resource_type="skill_required_tool_group",
                resource_key=skill_key,
                details={"missing_groups": ",".join(missing_groups)},
            )
        )


def _spec(
    *,
    agent_type: StringKey,
    role: str,
    lifecycle_group: str,
    context_scope: str,
    agent_step_type: str,
    fragments: list[str],
    skills: list[StringKey],
    app_view: StringKey,
    submit_view: StringKey,
    aliases: list[str] | None = None,
    stage: str | None = None,
) -> AgentTypeSpec:
    return AgentTypeSpec(
        agent_type=_value(agent_type),
        role=role,  # type: ignore[arg-type]
        lifecycle_group=lifecycle_group,  # type: ignore[arg-type]
        context_scope=context_scope,  # type: ignore[arg-type]
        agent_step_type=agent_step_type,
        instruction_fragment_keys=fragments,
        specific_instruction_key=_value(agent_type),
        skill_keys=_values(skills),
        application_tool_view_key=_value(app_view),
        submit_tool_view_key=_value(submit_view),
        tool_view_agent_aliases=aliases or [],
        stage=stage,
    )


def _coerce_agent_type_specs(specs: Iterable[AgentTypeSpec] | None) -> list[AgentTypeSpec]:
    return list(specs) if specs is not None else build_agent_type_specs()


def _resolve_agent_type_specs(
    *,
    extra_specs: Iterable[AgentTypeSpec] | None = None,
) -> list[AgentTypeSpec]:
    specs = [*AGENT_TYPE_SPECS, *list(extra_specs or ())]
    seen: set[str] = set()
    for spec in specs:
        if spec.agent_type in seen:
            raise ValueError(f"duplicate AgentType: {spec.agent_type}")
        seen.add(spec.agent_type)

    by_type = {spec.agent_type: spec for spec in specs}
    for spec in specs:
        current = spec.agent_type
        visiting: set[str] = set()
        while current:
            if current in visiting:
                raise ValueError(f"AgentType inheritance cycle includes {current}")
            visiting.add(current)
            current_spec = by_type.get(current)
            if current_spec is None:
                raise ValueError(f"unknown AgentType referenced by inheritance: {current}")
            current = current_spec.extends_agent_type or ""
    return specs


AGENT_TYPE_SPECS: tuple[AgentTypeSpec, ...] = (
    _spec(
        agent_type=ProductionAgentTypeKey.REPO_FORMAT_DISCOVERY,
        role="coordinator",
        lifecycle_group="repo_lifecycle",
        context_scope="repo",
        agent_step_type="repo_format_discovery_agent_step",
        fragments=[
            *COMMON_FRAGMENTS,
            "workspace.repo_workspace_context",
            "workspace.requirement_and_lake_dependency_context",
            "repo.native_repo_context",
            "repo.adapter_repo_context",
        ],
        skills=[],
        app_view=AppView.REPO_FORMAT_DISCOVERY,
        submit_view=SubmitView.REPO_FORMAT_DISCOVERY_SUBMIT,
        aliases=["RepoFormatDiscovery", "repo_format_discovery"],
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.SOURCE_CORPUS_PREPARE,
        role="worker",
        lifecycle_group="repo_lifecycle",
        context_scope="repo",
        agent_step_type="source_corpus_prepare_agent_step",
        fragments=[*BLOCKED_FRAGMENTS, "repo.native_repo_context", "source.source_corpus_context"],
        skills=[SkillKey.SOURCE_MATERIAL_ACQUISITION],
        app_view=AppView.SOURCE_CORPUS_PREPARE,
        submit_view=SubmitView.SOURCE_CORPUS_PREPARE_SUBMIT,
        aliases=["SourceCorpusPrepare", "source_corpus_prepare"],
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.SOURCE_INDEX_BUILDER,
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
        app_view=AppView.SOURCE_INDEX_BUILDER,
        submit_view=SubmitView.SOURCE_INDEX_BUILDER_SUBMIT,
        aliases=["SourceIndexBuilder", "source_index_builder"],
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.SOURCE_INDEX_REVIEWER,
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
        app_view=AppView.SOURCE_INDEX_REVIEWER,
        submit_view=SubmitView.SOURCE_INDEX_REVIEWER_SUBMIT,
        aliases=["SourceIndexReviewer", "source_index_reviewer"],
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.ROOT_INTERFACE_PREPARE,
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
        skills=[],
        app_view=AppView.ROOT_INTERFACE_PREPARE,
        submit_view=SubmitView.ROOT_INTERFACE_PREPARE_SUBMIT,
        aliases=["RootInterfacePrepare", "root_interface_prepare"],
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.ADAPTER_DECL_CATALOG,
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
        app_view=AppView.ADAPTER_REPO_IMPORT,
        submit_view=SubmitView.ADAPTER_REPO_IMPORT_SUBMIT,
        aliases=["AdapterDeclCatalog", "AdapterRepoImport", "AdapterRepoImportAgent", "adapter_repo_import"],
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.RESOURCE_CURATOR,
        role="worker",
        lifecycle_group="resource_request",
        context_scope="resource_request",
        agent_step_type="resource_curator_agent_step",
        fragments=[*BLOCKED_FRAGMENTS, "resource.resource_library_context"],
        skills=[SkillKey.RESOURCE_MATERIAL_ACQUISITION, SkillKey.RESOURCE_DRAFT_CURATION],
        app_view=AppView.RESOURCE_CURATOR,
        submit_view=SubmitView.RESOURCE_CURATOR_SUBMIT,
        aliases=["ResourceCurator", "resource_curator"],
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.COORDINATOR,
        role="coordinator",
        lifecycle_group="coordinator",
        context_scope="repo",
        agent_step_type="coordinator_agent_step",
        fragments=[
            *COMMON_FRAGMENTS,
            "workspace.repo_workspace_context",
            "workspace.requirement_and_lake_dependency_context",
            "repo.native_repo_context",
            "source.source_corpus_context",
            "source.source_index_context",
            "resource.resource_library_context",
            "node.scope_content_node_context",
            "node.node_contract_context",
            "node.node_tree_decomposition_policy",
            "scope.scope_contract_exports_context",
            "decl.proof_policy_satisfaction_context",
            "decl.identity_projection_context",
            "quality.source_fidelity",
        ],
        skills=[
            SkillKey.COORDINATOR_PROVED_FULL_GRAPH_MODE,
            SkillKey.COORDINATOR_DECLARED_FULL_GRAPH_MODE,
            SkillKey.COORDINATOR_DECLARED_INTERFACE_MODE,
            SkillKey.COORDINATOR_CONTENT_RESULT_CLOSEOUT,
            SkillKey.RESOURCE_RESULT_CLOSEOUT,
            SkillKey.COORDINATOR_REQUIREMENT_RESULT_CLOSEOUT,
            SkillKey.COORDINATOR_DEPENDENCY_READINESS,
            SkillKey.COORDINATOR_NODE_DECOMPOSITION,
            SkillKey.COORDINATOR_SCOPE_LIFECYCLE,
            SkillKey.COORDINATOR_CONTENT_TASK_DISPATCH,
            SkillKey.RESOURCE_REQUEST_SUBMISSION,
            SkillKey.COORDINATOR_PROVIDER_DEPENDENCY_LIFECYCLE,
            SkillKey.COORDINATOR_REPO_READY_LIFECYCLE,
            SkillKey.NODE_CONTRACT_DESIGN,
            SkillKey.SCOPE_EXPORT_INTERFACE_CURATION,
            SkillKey.EXTERNAL_RESOURCE_DISCOVERY,
            SkillKey.MATHLIB_INDEX_FIRST_RECON,
            SkillKey.MATHLIB_SEMANTIC_SEARCH_NAVIGATION,
            SkillKey.MATHLIB_INDEX_ENTRY_CURATION,
        ],
        app_view=AppView.NATIVE_REPO_COORDINATOR,
        submit_view=SubmitView.NATIVE_REPO_COORDINATOR_SUBMIT,
        aliases=["NativeRepoCoordinatorAgent", "NativeRepoCoordinator", "native_repo_coordinator", "coordinator"],
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.CONTENT_PLAN,
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
            "decl.identity_projection_context",
            "decl.proof_policy_satisfaction_context",
            "quality.source_fidelity",
        ],
        skills=[
            SkillKey.CONTENT_CONTRACT_READING,
            SkillKey.VISIBLE_NODE_DEPENDENCY_RECON,
            SkillKey.MATHLIB_INDEX_FIRST_RECON,
            SkillKey.MATHLIB_SEMANTIC_SEARCH_NAVIGATION,
            SkillKey.MATHLIB_INDEX_ENTRY_CURATION,
            SkillKey.CURRENT_NODE_MATHLIB_HINT_MAINTENANCE,
            SkillKey.EXTERNAL_RESOURCE_DISCOVERY,
            SkillKey.RESOURCE_REQUEST_SUBMISSION,
            SkillKey.RESOURCE_RESULT_CLOSEOUT,
            SkillKey.CONTENT_PREPARATION_ORCHESTRATION,
            SkillKey.CONTENT_PLAN_PROVED_FULL_GRAPH_MODE,
            SkillKey.CONTENT_PLAN_DECLARED_FULL_GRAPH_MODE,
            SkillKey.CONTENT_PLAN_DECLARED_INTERFACE_MODE,
            SkillKey.DECL_STRATEGY_PLANNING,
            SkillKey.DECL_ROUND_CHANGE_PLANNING,
            SkillKey.DECL_ROUND_CLOSEOUT,
            SkillKey.CONTENT_NODE_COMPLETION_DECISION,
        ],
        app_view=AppView.CONTENT_PLAN,
        submit_view=SubmitView.CONTENT_PLAN_SUBMIT,
        aliases=["ContentPlan", "content_plan", "plan"],
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.NODE_DIR_DEPENDENCY_RECON,
        role="worker",
        lifecycle_group="content_node_task",
        context_scope="content_node",
        agent_step_type="node_dir_dependency_recon_agent_step",
        fragments=[
            *COMMON_FRAGMENTS,
            "node.scope_content_node_context",
            "node.node_contract_context",
            "content.content_contract_task_context",
            "decl.identity_projection_context",
        ],
        skills=[SkillKey.CONTENT_CONTRACT_READING, SkillKey.VISIBLE_NODE_DEPENDENCY_RECON],
        app_view=AppView.NODE_DIR_DEPENDENCY_RECON,
        submit_view=SubmitView.NODE_DIR_DEPENDENCY_RECON_SUBMIT,
        aliases=["NodeDirDependencyRecon", "node_dir_dependency_recon"],
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.MATHLIB_RECON,
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
            SkillKey.CONTENT_CONTRACT_READING,
            SkillKey.MATHLIB_INDEX_FIRST_RECON,
            SkillKey.MATHLIB_SEMANTIC_SEARCH_NAVIGATION,
            SkillKey.MATHLIB_INDEX_ENTRY_CURATION,
            SkillKey.CURRENT_NODE_MATHLIB_HINT_MAINTENANCE,
        ],
        app_view=AppView.MATHLIB_RECON,
        submit_view=SubmitView.MATHLIB_RECON_SUBMIT,
        aliases=["MathlibRecon", "mathlib_recon"],
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.RESOURCE_RECON,
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
        skills=[
            SkillKey.CONTENT_CONTRACT_READING,
            SkillKey.EXTERNAL_RESOURCE_DISCOVERY,
            SkillKey.RESOURCE_REQUEST_SUBMISSION,
            SkillKey.RESOURCE_RESULT_CLOSEOUT,
        ],
        app_view=AppView.RESOURCE_RECON,
        submit_view=SubmitView.RESOURCE_RECON_SUBMIT,
        aliases=["ResourceRecon", "resource_recon"],
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.STATEMENT_NL_WORKER,
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
            "decl.identity_projection_context",
            "decl.proof_policy_satisfaction_context",
            "quality.source_fidelity",
        ],
        skills=[
            SkillKey.CONTENT_CONTRACT_READING,
            SkillKey.DECL_DEPENDENCY_ORIGIN_CURATION,
            SkillKey.MATHLIB_INDEX_FIRST_RECON,
            SkillKey.MATHLIB_SEMANTIC_SEARCH_NAVIGATION,
        ],
        app_view=AppView.STATEMENT_NL_WORKER,
        submit_view=SubmitView.DECL_STAGE_WORKER_SUBMIT,
        aliases=["StatementNlWorkerAgent", "StatementNlWorker", "statement_nl_worker"],
        stage="statement_nl",
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.STATEMENT_NL_REVIEWER,
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
            "decl.identity_projection_context",
            "decl.proof_policy_satisfaction_context",
            "quality.source_fidelity",
            "quality.review_contract",
        ],
        skills=[SkillKey.CONTENT_CONTRACT_READING, SkillKey.DECL_DEPENDENCY_ORIGIN_CURATION],
        app_view=AppView.STATEMENT_NL_REVIEWER,
        submit_view=SubmitView.DECL_STAGE_REVIEWER_SUBMIT,
        aliases=["StatementNlReviewerAgent", "StatementNlReviewer", "statement_nl_reviewer"],
        stage="statement_nl",
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.STATEMENT_FORMAL_WORKER,
        role="worker",
        lifecycle_group="decl_stage",
        context_scope="decl_stage",
        agent_step_type="decl_stage_worker_agent_step",
        fragments=[
            *WORKER_FRAGMENTS,
            "node.node_contract_context",
            "content.content_contract_task_context",
            "decl.stage_pipeline_context",
            "decl.identity_projection_context",
            "decl.proof_policy_satisfaction_context",
            "lean.formal_worker_capture_context",
            "quality.source_fidelity",
            "quality.lean_safety",
        ],
        skills=[
            SkillKey.CONTENT_CONTRACT_READING,
            SkillKey.DECL_OWNED_LEAN_FILE_CAPTURE_CHECK,
            SkillKey.LEAN_STATEMENT_FORMALIZATION,
            SkillKey.DECL_DEPENDENCY_ORIGIN_CURATION,
            SkillKey.MATHLIB_INDEX_FIRST_RECON,
            SkillKey.MATHLIB_SEMANTIC_SEARCH_NAVIGATION,
            SkillKey.MATHLIB_INDEX_ENTRY_CURATION,
            SkillKey.CURRENT_NODE_MATHLIB_HINT_MAINTENANCE,
        ],
        app_view=AppView.STATEMENT_FORMAL_WORKER,
        submit_view=SubmitView.DECL_STAGE_WORKER_SUBMIT,
        aliases=["StatementFormalWorker", "statement_formal_worker"],
        stage="statement_formal",
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.STATEMENT_FORMAL_REVIEWER,
        role="reviewer",
        lifecycle_group="decl_stage",
        context_scope="decl_stage",
        agent_step_type="decl_stage_reviewer_agent_step",
        fragments=[
            *REVIEWER_FRAGMENTS,
            "content.content_contract_task_context",
            "decl.stage_pipeline_context",
            "decl.identity_projection_context",
            "decl.proof_policy_satisfaction_context",
            "lean.formal_reviewer_evidence_context",
            "quality.source_fidelity",
            "quality.lean_safety",
            "quality.review_contract",
        ],
        skills=[SkillKey.CONTENT_CONTRACT_READING, SkillKey.DECL_DEPENDENCY_ORIGIN_CURATION],
        app_view=AppView.STATEMENT_FORMAL_REVIEWER,
        submit_view=SubmitView.DECL_STAGE_REVIEWER_SUBMIT,
        aliases=["StatementFormalReviewer", "statement_formal_reviewer"],
        stage="statement_formal",
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.PROOF_NL_WORKER,
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
            "decl.identity_projection_context",
            "decl.proof_policy_satisfaction_context",
            "quality.source_fidelity",
        ],
        skills=[
            SkillKey.CONTENT_CONTRACT_READING,
            SkillKey.VISIBLE_NODE_DEPENDENCY_RECON,
            SkillKey.DECL_DEPENDENCY_ORIGIN_CURATION,
            SkillKey.MATHLIB_INDEX_FIRST_RECON,
            SkillKey.MATHLIB_SEMANTIC_SEARCH_NAVIGATION,
            SkillKey.MATHLIB_INDEX_ENTRY_CURATION,
            SkillKey.CURRENT_NODE_MATHLIB_HINT_MAINTENANCE,
        ],
        app_view=AppView.PROOF_NL_WORKER,
        submit_view=SubmitView.DECL_STAGE_WORKER_SUBMIT,
        aliases=["ProofNlWorkerAgent", "ProofNlWorker", "proof_nl_worker"],
        stage="proof_nl",
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.PROOF_NL_REVIEWER,
        role="reviewer",
        lifecycle_group="decl_stage",
        context_scope="decl_stage",
        agent_step_type="decl_stage_reviewer_agent_step",
        fragments=[
            *REVIEWER_FRAGMENTS,
            "source.source_index_context",
            "resource.resource_library_context",
            "node.node_contract_context",
            "content.content_contract_task_context",
            "decl.stage_pipeline_context",
            "decl.identity_projection_context",
            "decl.proof_policy_satisfaction_context",
            "quality.source_fidelity",
            "quality.review_contract",
        ],
        skills=[SkillKey.CONTENT_CONTRACT_READING, SkillKey.DECL_DEPENDENCY_ORIGIN_CURATION],
        app_view=AppView.PROOF_NL_REVIEWER,
        submit_view=SubmitView.DECL_STAGE_REVIEWER_SUBMIT,
        aliases=["ProofNlReviewerAgent", "ProofNlReviewer", "proof_nl_reviewer"],
        stage="proof_nl",
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.PROOF_FORMAL_WORKER,
        role="worker",
        lifecycle_group="decl_stage",
        context_scope="decl_stage",
        agent_step_type="decl_stage_worker_agent_step",
        fragments=[
            *WORKER_FRAGMENTS,
            "node.node_contract_context",
            "content.content_contract_task_context",
            "decl.stage_pipeline_context",
            "decl.identity_projection_context",
            "decl.proof_policy_satisfaction_context",
            "lean.formal_worker_capture_context",
            "quality.source_fidelity",
            "quality.lean_safety",
        ],
        skills=[
            SkillKey.CONTENT_CONTRACT_READING,
            SkillKey.DECL_OWNED_LEAN_FILE_CAPTURE_CHECK,
            SkillKey.LEAN_PROOF_FORMALIZATION,
            SkillKey.DECL_DEPENDENCY_ORIGIN_CURATION,
            SkillKey.MATHLIB_INDEX_FIRST_RECON,
            SkillKey.MATHLIB_SEMANTIC_SEARCH_NAVIGATION,
            SkillKey.MATHLIB_INDEX_ENTRY_CURATION,
            SkillKey.CURRENT_NODE_MATHLIB_HINT_MAINTENANCE,
        ],
        app_view=AppView.PROOF_FORMAL_WORKER,
        submit_view=SubmitView.DECL_STAGE_WORKER_SUBMIT,
        aliases=["ProofFormalWorker", "proof_formal_worker"],
        stage="proof_formal",
    ),
    _spec(
        agent_type=ProductionAgentTypeKey.PROOF_FORMAL_REVIEWER,
        role="reviewer",
        lifecycle_group="decl_stage",
        context_scope="decl_stage",
        agent_step_type="decl_stage_reviewer_agent_step",
        fragments=[
            *REVIEWER_FRAGMENTS,
            "node.node_contract_context",
            "content.content_contract_task_context",
            "decl.stage_pipeline_context",
            "decl.identity_projection_context",
            "decl.proof_policy_satisfaction_context",
            "lean.formal_reviewer_evidence_context",
            "quality.source_fidelity",
            "quality.lean_safety",
            "quality.review_contract",
        ],
        skills=[SkillKey.CONTENT_CONTRACT_READING, SkillKey.DECL_DEPENDENCY_ORIGIN_CURATION],
        app_view=AppView.PROOF_FORMAL_REVIEWER,
        submit_view=SubmitView.DECL_STAGE_REVIEWER_SUBMIT,
        aliases=["ProofFormalReviewer", "proof_formal_reviewer"],
        stage="proof_formal",
    ),
)


__all__ = [
    "AGENT_TYPE_SPECS",
    "agent_skill_keys",
    "agent_type_permission_names",
    "build_agent_type_specs",
    "derive_agent_type_spec",
    "get_agent_type_spec",
    "validate_agent_resources",
]
