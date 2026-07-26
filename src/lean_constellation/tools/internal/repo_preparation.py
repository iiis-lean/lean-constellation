"""Repo preparation and workspace Agent-facing tools."""

from __future__ import annotations

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.preparation import (
    DeclInterface,
    RepoDependencyRequirement,
    RepoPreparationRequirementsView,
    RepoRequirementRef,
    SourceCorpusMode,
)
from lean_constellation.domain.repo import (
    ProofAvailability,
    RepoFormat,
    RepoPublicationStatus,
    RepoWorkMode,
)
from lean_constellation.services.tool_facade import ToolCapability, ToolExecutionContext, ToolSpec
from lean_constellation.tools.args import (
    ExpectedFormatArgs,
    GitHubCodeSearchArgs,
    GitHubFileReadArgs,
    GitHubLeanRepoProbeArgs,
    GitHubRepoArgs,
    GitHubTreeArgs,
    NoArgs,
    PreparationRequirementRefArgs,
    ProviderRepoArgs,
    QueryLimitArgs,
    RequirementNameArgs,
    TargetRepoArgs,
    UrlOrSlugArgs,
)
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
from lean_constellation.tools.specs import direct_tool, handler_tool


class WorkspaceRepoAgentView(StrictModel):
    repo_key: str
    repo_summary: str | None = None
    repo_format: RepoFormat
    publication_status: RepoPublicationStatus
    target_proof_availability: ProofAvailability
    work_mode: RepoWorkMode
    provider_ready: bool
    open_requirement_count: int


class GitHubRepoCandidateAgentView(StrictModel):
    full_name: str
    canonical_https_locator: str
    default_branch: str | None = None
    description: str | None = None
    topics: list[str] = Field(default_factory=list)
    stars: int | None = None
    pushed_at: str | None = None
    license_spdx_id: str | None = None
    evidence_summary: str | None = None


class GitHubRepoSearchAgentView(StrictModel):
    ok: bool
    query: str
    candidates: list[GitHubRepoCandidateAgentView] = Field(default_factory=list)
    summary: str | None = None
    issue_code: str | None = None


class WorkspaceCoordinatorAgentView(StrictModel):
    current_repo: str
    repositories: list[WorkspaceRepoAgentView] = Field(default_factory=list)
    summary: str


class RequirementGroupSummaryAgentView(StrictModel):
    target_repo: str
    required_proof_availability: ProofAvailability
    provider_work_mode: RepoWorkMode
    requirement_count: int
    consumer_repos: list[str] = Field(default_factory=list)
    interface_names: list[str] = Field(default_factory=list)


class RequirementGroupItemAgentView(StrictModel):
    consumer_repo: str
    requirement: RepoDependencyRequirement


class PreparationInputAgentView(StrictModel):
    goal: str
    source_corpus_mode: SourceCorpusMode
    logical_source_corpus_path: str
    source_description: str | None = None
    interface_inputs: list[DeclInterface] = Field(default_factory=list)
    allow_interface_supplement: bool
    requirement_refs: list[RepoRequirementRef] = Field(default_factory=list)
    notes: str | None = None
    summary: str


class PreparationRequirementAgentView(StrictModel):
    consumer_repo: str
    name: str
    target_repo: str
    required_proof_availability: ProofAvailability
    source_description: str | None = None
    reason: str | None = None
    interfaces: list[DeclInterface] = Field(default_factory=list)
    status: str
    satisfaction_mode: str
    provider_repo: str | None = None
    note: str | None = None


class PreparationRequirementsAgentView(StrictModel):
    target_repo: str
    requirement_refs: list[RepoRequirementRef] = Field(default_factory=list)
    requirements: list[PreparationRequirementAgentView] = Field(default_factory=list)
    missing_refs: list[RepoRequirementRef] = Field(default_factory=list)
    summary: str


class RequirementDetailAgentView(StrictModel):
    requirement: RepoDependencyRequirement


class RequirementGroupAgentView(StrictModel):
    target_repo: str
    required_proof_availability: ProofAvailability
    provider_work_mode: RepoWorkMode
    requirements: list[RequirementGroupItemAgentView] = Field(default_factory=list)
    summary: str


def _workspace_root(ctx):
    return ctx.repo_root.parent


def _github_candidate_agent_view(value) -> GitHubRepoCandidateAgentView:
    return GitHubRepoCandidateAgentView(
        full_name=value.full_name,
        canonical_https_locator=value.html_url,
        default_branch=value.default_branch,
        description=value.description,
        topics=value.topics,
        stars=value.stars,
        pushed_at=value.pushed_at,
        license_spdx_id=value.license_spdx_id,
        evidence_summary=value.evidence_summary,
    )


def _search_github_lean_repositories(runtime, ctx, args: QueryLimitArgs):
    del ctx
    searched = runtime.external.github_repo.search_repositories(
        args.query, limit=args.limit
    )
    return runtime.foundation.ok(
        GitHubRepoSearchAgentView(
            ok=searched.ok,
            query=searched.query,
            candidates=[
                _github_candidate_agent_view(item) for item in searched.candidates
            ],
            summary=searched.summary,
            issue_code=searched.issue_code,
        )
    )


def _inspect_github_lean_repository(runtime, ctx, args: UrlOrSlugArgs):
    del ctx
    inspected = runtime.external.github_repo.inspect_repository(args.url_or_slug)
    return runtime.foundation.ok(_github_candidate_agent_view(inspected))


def _get_preparation_input(runtime, ctx, args: NoArgs):
    del args
    loaded = runtime.repo_workspace.preparation.get_preparation_input(ctx.repo_root)
    if not loaded.ok or loaded.value is None:
        return runtime.foundation.fail(loaded.issues)
    value = loaded.value.input
    return runtime.foundation.ok(
        PreparationInputAgentView(
            goal=value.goal,
            source_corpus_mode=value.source_corpus_mode,
            logical_source_corpus_path=(
                value.source_corpus_relpath or ".lean_constellation/source"
            ),
            source_description=value.source_description,
            interface_inputs=value.interface_inputs,
            allow_interface_supplement=value.allow_interface_supplement,
            requirement_refs=value.requirement_refs,
            notes=value.notes,
            summary=loaded.value.summary,
        ),
        warnings=loaded.issues,
    )


def _preparation_requirement_agent_view(item) -> PreparationRequirementAgentView:
    requirement = item.requirement
    return PreparationRequirementAgentView(
        consumer_repo=item.consumer_repo,
        name=requirement.name,
        target_repo=requirement.target_repo,
        required_proof_availability=requirement.required_proof_availability,
        source_description=requirement.source_description,
        reason=requirement.reason,
        interfaces=requirement.interfaces,
        status=requirement.status.value,
        satisfaction_mode=requirement.satisfaction_mode.value,
        provider_repo=requirement.provider_repo,
        note=requirement.note,
    )


def _list_open_requirement_groups(runtime, ctx, args: NoArgs):
    del args
    listed = runtime.repo_workspace.workspace_catalog.list_open_requirement_groups(_workspace_root(ctx))
    if not listed.ok or listed.value is None:
        return runtime.foundation.fail(listed.issues)
    return runtime.foundation.ok(
        [
            RequirementGroupSummaryAgentView(
                target_repo=item.target_repo,
                required_proof_availability=item.required_proof_availability,
                provider_work_mode=item.provider_work_mode,
                requirement_count=item.requirement_count,
                consumer_repos=item.consumer_repos,
                interface_names=item.interface_names,
            )
            for item in listed.value
        ],
        warnings=listed.issues,
    )


def _get_requirement_group(runtime, ctx, args: TargetRepoArgs):
    group = runtime.repo_workspace.workspace_catalog.get_requirement_group(_workspace_root(ctx), target_repo=args.target_repo)
    if not group.ok or group.value is None:
        return runtime.foundation.fail(group.issues)
    return runtime.foundation.ok(
        RequirementGroupAgentView(
            target_repo=group.value.target_repo,
            required_proof_availability=group.value.required_proof_availability,
            provider_work_mode=group.value.provider_work_mode,
            requirements=[
                RequirementGroupItemAgentView(
                    consumer_repo=item.consumer_repo,
                    requirement=item.requirement,
                )
                for item in group.value.requirements
            ],
            summary=group.value.summary,
        ),
        warnings=group.issues,
    )


def _get_current_repo_requirement(runtime, ctx, args: RequirementNameArgs):
    loaded = runtime.repo_workspace.requirement.get_requirement(
        ctx.repo_root,
        name=args.requirement_name,
    )
    if not loaded.ok or loaded.value is None:
        return runtime.foundation.fail(loaded.issues)
    return runtime.foundation.ok(
        RequirementDetailAgentView(requirement=loaded.value.requirement),
        warnings=loaded.issues,
    )


def _list_ready_provider_repos(runtime, ctx, args: NoArgs):
    del args
    listed = runtime.repo_workspace.workspace_catalog.list_ready_provider_repos(
        _workspace_root(ctx),
        current_repo=ctx.repo_root.name,
    )
    if not listed.ok or listed.value is None:
        return runtime.foundation.fail(listed.issues)
    return runtime.foundation.ok(
        [
            WorkspaceRepoAgentView(
                repo_key=item.repo_key,
                repo_summary=item.repo_summary,
                repo_format=item.repo_format,
                publication_status=item.publication_status,
                target_proof_availability=item.target_proof_availability,
                work_mode=item.work_mode,
                provider_ready=True,
                open_requirement_count=item.open_requirement_count,
            )
            for item in listed.value
        ],
        warnings=listed.issues,
    )


def _inspect_workspace_for_coordinator(runtime, ctx, args: NoArgs):
    del args
    inspected = runtime.repo_workspace.inspect_workspace_for_coordinator(ctx.repo_root)
    if not inspected.ok or inspected.value is None:
        return runtime.foundation.fail(inspected.issues)
    ready_keys = {item.repo_key for item in inspected.value.ready_provider_repos}
    repositories = [
        WorkspaceRepoAgentView(
            repo_key=item.repo_key,
            repo_summary=item.repo_summary,
            repo_format=item.repo_format,
            publication_status=item.publication_status,
            target_proof_availability=item.target_proof_availability,
            work_mode=item.work_mode,
            provider_ready=item.repo_key in ready_keys,
            open_requirement_count=item.open_requirement_count,
        )
        for item in inspected.value.catalog.repos
    ]
    return runtime.foundation.ok(
        WorkspaceCoordinatorAgentView(
            current_repo=ctx.repo.repo_key,
            repositories=repositories,
            summary=f"Loaded {len(repositories)} workspace repositories.",
        ),
        warnings=inspected.issues,
    )


def _load_scoped_preparation_requirements(runtime, ctx):
    prepared = runtime.repo_workspace.preparation.get_preparation_input(ctx.repo_root)
    if not prepared.ok or prepared.value is None:
        return runtime.foundation.fail(prepared.issues)
    refs = list(prepared.value.input.requirement_refs)
    allowed = {(ref.consumer_repo, ref.requirement_name) for ref in refs}
    group = runtime.repo_workspace.workspace_catalog.get_requirement_group(_workspace_root(ctx), target_repo=ctx.repo_root.name)
    if not group.ok or group.value is None:
        return runtime.foundation.fail(group.issues)
    requirements = [
        item
        for item in group.value.requirements
        if (item.consumer_repo, item.requirement.name) in allowed
    ]
    found = {(item.consumer_repo, item.requirement.name) for item in requirements}
    missing = [
        RepoRequirementRef(consumer_repo=ref.consumer_repo, requirement_name=ref.requirement_name)
        for ref in refs
        if (ref.consumer_repo, ref.requirement_name) not in found
    ]
    return runtime.foundation.ok(
        RepoPreparationRequirementsView(
            repo_root=str(ctx.repo_root),
            target_repo=ctx.repo_root.name,
            requirement_refs=refs,
            requirements=requirements,
            missing_refs=missing,
            summary=f"Loaded {len(requirements)} requirements from {len(refs)} current preparation refs.",
        )
    )


def _list_preparation_requirements(runtime, ctx, args: NoArgs):
    del args
    scoped = _load_scoped_preparation_requirements(runtime, ctx)
    if not scoped.ok or scoped.value is None:
        return scoped
    return runtime.foundation.ok(
        PreparationRequirementsAgentView(
            target_repo=scoped.value.target_repo,
            requirement_refs=scoped.value.requirement_refs,
            requirements=[
                _preparation_requirement_agent_view(item)
                for item in scoped.value.requirements
            ],
            missing_refs=scoped.value.missing_refs,
            summary=scoped.value.summary,
        ),
        warnings=scoped.issues,
    )


def _get_preparation_requirement(runtime, ctx, args: PreparationRequirementRefArgs):
    scoped = _load_scoped_preparation_requirements(runtime, ctx)
    if not scoped.ok or scoped.value is None:
        return scoped
    allowed = {(ref.consumer_repo, ref.requirement_name) for ref in scoped.value.requirement_refs}
    requested = (args.consumer_repo, args.requirement_name)
    if requested not in allowed:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "preparation_requirement_ref_not_allowed",
                "Requirement ref is not part of the current preparation input.",
                object_ref=f"{args.consumer_repo}:{args.requirement_name}",
            )
        )
    for item in scoped.value.requirements:
        if (item.consumer_repo, item.requirement.name) == requested:
            return runtime.foundation.ok(
                _preparation_requirement_agent_view(item), warnings=scoped.issues
            )
    return runtime.foundation.fail(
        runtime.foundation.issue(
            "preparation_requirement_ref_missing",
            "Requirement ref is listed in preparation input but the requirement detail was not found.",
            object_ref=f"{args.consumer_repo}:{args.requirement_name}",
        )
    )


def _get_current_repo_run_context(runtime, ctx: ToolExecutionContext, args: NoArgs):
    del args
    flow_id = ctx.runtime.flow_id
    if not flow_id:
        return runtime.foundation.fail(
            runtime.foundation.issue("repo_run_flow_context_required", "Repository run context requires the current Coordinator Flow.")
        )
    flow = runtime.get_flow(flow_id)
    if getattr(flow, "flow_type", None) != "native_repo_coordinator":
        return runtime.foundation.fail(
            runtime.foundation.issue("repo_run_flow_context_invalid", "Current Flow is not a native repository Coordinator Flow.")
        )
    flow_input = getattr(flow, "input", None)
    if str(getattr(flow_input, "repo_root", "")) != str(ctx.repo_root):
        return runtime.foundation.fail(
            runtime.foundation.issue("repo_run_repo_context_mismatch", "Current Coordinator Flow is bound to a different repository.")
        )
    run_context = getattr(flow_input, "run_context", None)
    if run_context is None:
        return runtime.foundation.ok(
            {
                "start_mode": getattr(flow_input, "start_mode", None),
                "start_reason": getattr(flow_input, "start_reason", None),
                "run_context_available": False,
                "summary": "This Coordinator run predates structured RepoRunContext handoff.",
            }
        )
    publication = runtime.repo_workspace.metadata.get_repo_publication(ctx.repo_root)
    if not publication.ok or publication.value is None:
        return runtime.foundation.fail(publication.issues)
    latest_release_id = publication.value.publication.latest_release_id
    work_config = runtime.repo_workspace.metadata.get_repo_work_config(ctx.repo_root)
    if not work_config.ok or work_config.value is None:
        return runtime.foundation.fail(work_config.issues)
    requested_proof = run_context.run_spec.target_proof_availability
    requested_work_mode = run_context.run_spec.work_mode
    current_proof = work_config.value.target_proof_availability
    current_work_mode = work_config.value.work_mode
    return runtime.foundation.ok(
        {
            "start_mode": getattr(flow_input, "start_mode", None),
            "start_kind": run_context.start_kind,
            "run_objective": run_context.run_spec.run_objective,
            "requested": {
                "target_proof_availability": requested_proof.value,
                "work_mode": requested_work_mode.value,
            },
            "current_repository": {
                "target_proof_availability": current_proof.value,
                "work_mode": current_work_mode.value,
            },
            "configuration_matches_run": (
                requested_proof == current_proof and requested_work_mode == current_work_mode
            ),
            "source_scope": run_context.run_spec.source_scope.model_dump(mode="json"),
            "resolved_source_files": list(run_context.resolved_source_files),
            "source_index_delta_summary": run_context.source_index_delta_summary,
            "root_interface_delta_summary": run_context.root_interface_delta_summary,
            "config_change_summary": run_context.config_change_summary,
            "base_release_id": run_context.base_release_id,
            "latest_release_compatible_with_base": latest_release_id == run_context.base_release_id,
            "publication_status": publication.value.publication.status.value,
            "summary": "Current structured repository run context.",
        },
        warnings=[*publication.issues, *work_config.issues],
    )


def build_tool_specs() -> list[ToolSpec]:
    roles = {"coordinator", "plan", "worker", "reviewer", "admin"}
    return [
        handler_tool(
            name="get_preparation_input",
            description="Read the current repo preparation input.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="repo_preparation_input_detail",
            groups={AppGroup.REPO_PREPARATION_INPUT_READ},
            roles=roles,
            handler=_get_preparation_input,
        ),
        direct_tool(
            name="get_current_repo_work_config",
            description="Read the current repo proof availability target and work mode.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="repo_workspace",
            backing_component="metadata",
            backing_method="get_repo_work_config",
            result_view="repo_work_config",
            groups={AppGroup.REPO_WORK_CONFIG_READ},
            roles={"coordinator", "plan", "admin"},
        ),
        handler_tool(
            name="get_current_repo_run_context",
            description="Read the objective, target, work mode, source responsibility, preparation deltas, and release baseline for the current repository run.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="repo_run_context",
            groups={AppGroup.REPO_RUN_CONTEXT_READ},
            roles={"coordinator", "admin"},
            handler=_get_current_repo_run_context,
        ),
        direct_tool(
            name="get_preparation_start_preflight",
            description="Check whether the current repo can start native or adapter preparation.",
            args_model=ExpectedFormatArgs,
            capability=ToolCapability.READ,
            backing_service="repo_workspace",
            backing_method="get_preparation_start_preflight",
            result_view="preparation_start_preflight",
            groups={AppGroup.REPO_PREPARATION_START_PREFLIGHT_READ},
            roles={"coordinator", "admin"},
        ),
        direct_tool(
            name="check_root_main_handoff_interfaces",
            description="Check that the Main scope interfaces are valid for native handoff to the Coordinator.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="node",
            backing_method="check_root_main_handoff_interfaces",
            result_view="gate_report",
            groups={AppGroup.ROOT_INTERFACE_PREPARE_READ},
            roles={"worker", "coordinator", "admin"},
        ),
        handler_tool(
            name="inspect_workspace_for_coordinator",
            description="Read each workspace repository once with compact publication, work-mode, and provider-readiness state.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="workspace_coordinator_overview",
            groups={AppGroup.WORKSPACE_OVERVIEW_READ},
            roles={"coordinator", "admin"},
            handler=_inspect_workspace_for_coordinator,
        ),
        handler_tool(
            name="list_ready_provider_repos",
            description="List stable provider repos in the current workspace, excluding the current repo.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="workspace_repo_overviews",
            groups={AppGroup.WORKSPACE_PROVIDER_CATALOG_READ},
            roles={"coordinator", "admin"},
            handler=_list_ready_provider_repos,
        ),
        handler_tool(
            name="list_open_requirement_groups",
            description="List open dependency requirement groups across the current workspace.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="requirement_group_overviews",
            groups={AppGroup.WORKSPACE_REQUIREMENT_READ},
            roles={"coordinator", "admin"},
            handler=_list_open_requirement_groups,
        ),
        handler_tool(
            name="list_preparation_requirements",
            description="Read only the requirement details referenced by the current repo preparation input.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="repo_preparation_requirement_list",
            groups={AppGroup.REPO_PREPARATION_REQUIREMENT_READ},
            roles={"coordinator", "worker", "admin"},
            handler=_list_preparation_requirements,
        ),
        handler_tool(
            name="get_preparation_requirement",
            description="Read one requirement detail only when it is referenced by the current repo preparation input.",
            args_model=PreparationRequirementRefArgs,
            capability=ToolCapability.READ,
            result_view="repo_preparation_requirement_detail",
            groups={AppGroup.REPO_PREPARATION_REQUIREMENT_READ},
            roles={"coordinator", "worker", "admin"},
            handler=_get_preparation_requirement,
        ),
        handler_tool(
            name="get_requirement_group",
            description="Read one open dependency requirement group by target repo.",
            args_model=TargetRepoArgs,
            capability=ToolCapability.READ,
            result_view="requirement_group_detail",
            groups={AppGroup.WORKSPACE_REQUIREMENT_READ},
            roles={"coordinator", "admin"},
            handler=_get_requirement_group,
        ),
        handler_tool(
            name="get_current_repo_requirement",
            description="Read one dependency requirement from the current repo by requirement name.",
            args_model=RequirementNameArgs,
            capability=ToolCapability.READ,
            result_view="requirement_detail",
            groups={AppGroup.WORKSPACE_REQUIREMENT_READ},
            roles={"coordinator", "admin"},
            handler=_get_current_repo_requirement,
        ),
        direct_tool(
            name="list_current_lake_dependencies",
            description="List Lake dependencies declared by the current repo.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="repo_workspace",
            backing_component="workspace_catalog",
            backing_method="list_current_lake_dependency_repos",
            result_view="lake_dependencies",
            groups={AppGroup.LAKE_DEPENDENCY_READ},
            roles={"coordinator", "admin"},
        ),
        direct_tool(
            name="attach_ready_workspace_repo_dependency",
            description="Attach one stable existing workspace repo as a Lake dependency without creating a requirement.",
            args_model=ProviderRepoArgs,
            capability=ToolCapability.WRITE,
            backing_service="repo_workspace",
            backing_method="attach_ready_workspace_repo_dependency",
            result_view="lake_dependency_attach",
            groups={AppGroup.LAKE_DEPENDENCY_WRITE},
            roles={"coordinator", "admin"},
        ),
        handler_tool(
            name="search_github_lean_repositories",
            description="Search candidate GitHub Lean repositories with one canonical HTTPS locator per result.",
            args_model=QueryLimitArgs,
            capability=ToolCapability.READ,
            result_view="github_repo_candidate_list",
            groups={AppGroup.UPSTREAM_REPO_SEARCH},
            roles={"coordinator", "admin"},
            required_context=set(),
            handler=_search_github_lean_repositories,
        ),
        handler_tool(
            name="inspect_github_lean_repository",
            description="Inspect a candidate GitHub Lean repository by URL or owner/name slug.",
            args_model=UrlOrSlugArgs,
            capability=ToolCapability.READ,
            result_view="github_repo_candidate_detail",
            groups={AppGroup.UPSTREAM_REPO_SEARCH},
            roles={"coordinator", "admin"},
            required_context=set(),
            handler=_inspect_github_lean_repository,
        ),
        direct_tool(
            name="probe_github_lean_repo_candidate",
            description="Probe a GitHub repository remotely for Lean/Lake project evidence without cloning it.",
            args_model=GitHubLeanRepoProbeArgs,
            capability=ToolCapability.READ,
            backing_service="external",
            backing_component="github_repo",
            backing_method="probe_github_lean_repo_candidate",
            result_view="github_lean_repo_probe",
            groups={AppGroup.UPSTREAM_REPO_SEARCH},
            roles={"coordinator", "admin"},
            required_context=set(),
        ),
        direct_tool(
            name="get_github_repository",
            description="Read GitHub repository metadata by URL or owner/name slug.",
            args_model=GitHubRepoArgs,
            capability=ToolCapability.READ,
            backing_service="external",
            backing_component="github_repo",
            backing_method="get_repository",
            result_view="github_repository",
            groups={AppGroup.GITHUB_REPOSITORY_READ},
            roles={"coordinator", "admin"},
            required_context=set(),
        ),
        direct_tool(
            name="list_github_repository_tree",
            description="Read a GitHub repository tree remotely.",
            args_model=GitHubTreeArgs,
            capability=ToolCapability.READ,
            backing_service="external",
            backing_component="github_repo",
            backing_method="list_repository_tree",
            result_view="github_repository_tree",
            groups={AppGroup.GITHUB_REPOSITORY_READ},
            roles={"coordinator", "admin"},
            required_context=set(),
        ),
        direct_tool(
            name="read_github_repository_file",
            description="Read one repository-relative file from GitHub remotely.",
            args_model=GitHubFileReadArgs,
            capability=ToolCapability.READ,
            backing_service="external",
            backing_component="github_repo",
            backing_method="read_repository_file",
            result_view="github_repository_file",
            groups={AppGroup.GITHUB_REPOSITORY_READ},
            roles={"coordinator", "admin"},
            required_context=set(),
        ),
        direct_tool(
            name="search_github_code",
            description="Search GitHub code, preferably scoped to a candidate repository.",
            args_model=GitHubCodeSearchArgs,
            capability=ToolCapability.READ,
            backing_service="external",
            backing_component="github_repo",
            backing_method="search_code",
            result_view="github_code_search",
            groups={AppGroup.GITHUB_REPOSITORY_READ},
            roles={"coordinator", "admin"},
            required_context=set(),
        ),
    ]
