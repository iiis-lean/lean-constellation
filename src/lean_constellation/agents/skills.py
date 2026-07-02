"""Application SkillSpec registry for Lean Constellation Agent homes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from agent_runtime_kit.agent.skills import SkillSpec, write_skill_spec

from lean_constellation.agents.keys import SkillKey
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
from lean_constellation.tools.keys import SubmitToolGroupKey as SubmitGroup


StringKey = str | StrEnum


def _value(value: StringKey) -> str:
    return value.value if isinstance(value, StrEnum) else str(value)


def _groups(*groups: StringKey) -> tuple[str, ...]:
    return tuple(_value(group) for group in groups)


@dataclass(frozen=True)
class LeanSkillDefinition:
    name: str
    description: str
    group: str
    body: str
    required_tool_groups: tuple[str, ...] = ()
    source_design_doc: str | None = None

    def to_ark_skill_spec(self) -> SkillSpec:
        return SkillSpec(
            name=self.name,
            description=self.description,
            body=self.body,
            group=self.group,
            title=self.name,
            metadata_short_description=self.description.split(".")[0],
            files=_reference_files(self),
        )


def build_skill_specs(skill_keys: Iterable[StringKey] | None = None) -> dict[str, SkillSpec]:
    """Build ARK SkillSpec objects for all or selected Lean skills."""

    selected = {_value(skill_key) for skill_key in skill_keys} if skill_keys is not None else set(SKILL_DEFINITIONS)
    missing = sorted(selected - set(SKILL_DEFINITIONS))
    if missing:
        raise KeyError(f"unknown skill keys: {', '.join(missing)}")
    return {
        key: SKILL_DEFINITIONS[key].to_ark_skill_spec()
        for key in sorted(selected)
    }


def materialize_skill_specs(
    target_root: Path,
    skill_keys: Iterable[StringKey] | None = None,
) -> dict[str, Path]:
    """Write selected skills as Codex-compatible directories."""

    root = Path(target_root)
    specs = build_skill_specs(skill_keys)
    paths: dict[str, Path] = {}
    for key, spec in specs.items():
        paths[key] = write_skill_spec(spec, root / key)
    return paths


def known_skill_keys() -> set[str]:
    return set(SKILL_DEFINITIONS)


def required_tool_groups_for_skill(skill_key: StringKey) -> tuple[str, ...]:
    try:
        return SKILL_DEFINITIONS[_value(skill_key)].required_tool_groups
    except KeyError as exc:
        raise KeyError(f"unknown skill: {skill_key}") from exc


def _reference_files(definition: LeanSkillDefinition) -> Mapping[str, str]:
    lines = [
        f"# {definition.name}",
        "",
        "This reference file is generated from the Lean Constellation application skill registry.",
        "It records the tool-group expectations that should stay aligned with ToolFacade views.",
        "",
        "## Required Tool Groups",
        "",
    ]
    if definition.required_tool_groups:
        lines.extend(f"- `{group}`" for group in definition.required_tool_groups)
    else:
        lines.append("- None required beyond the Agent-specific submit workflow.")
    if definition.source_design_doc:
        lines.extend(["", "## Source Design Document", "", f"- `{definition.source_design_doc}`"])
    return {"references/tool_groups.md": "\n".join(lines) + "\n"}


def _body(title: str, purpose: str, steps: tuple[str, ...], boundaries: tuple[str, ...]) -> str:
    lines = [
        f"# {title}",
        "",
        "## Purpose",
        "",
        purpose,
        "",
        "## Workflow",
        "",
    ]
    lines.extend(f"{index}. {step}" for index, step in enumerate(steps, start=1))
    lines.extend(["", "## Boundaries", ""])
    lines.extend(f"- {boundary}" for boundary in boundaries)
    return "\n".join(lines) + "\n"


SKILL_DEFINITIONS: dict[str, LeanSkillDefinition] = {
    SkillKey.NODE_CONTRACT_DESIGN.value: LeanSkillDefinition(
        name="node-contract-design",
        description="Designs and updates Lean Constellation scope and content node contracts.",
        group="node",
        required_tool_groups=_groups(
            AppGroup.NODE_CONTRACT_READ_COORDINATOR,
            AppGroup.NODE_CONTRACT_CORE_COORDINATOR_WRITE,
            AppGroup.NODE_TREE_COORDINATOR_WRITE,
            AppGroup.SCOPE_EXPORT_INTERFACE_WRITE,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "node-contract-design",
            "Use this skill when planning node tree structure, creating child nodes, preparing content node tasks, or updating contract goals, boundaries, objectives, materials, dependencies, constraints, or interfaces.",
            (
                "Read the current scope or content contract with `get_node_contract` before changing it.",
                "Write goals and boundaries in mathematical terms rather than file-layout terms.",
                "Make sibling boundaries explicit and avoid duplicate ownership.",
                "Use `create_scope_node`, `create_content_node`, and `update_node_contract_text` for durable contract changes.",
                "Prepare content tasks with enough objective, material, dependency, and interface context for node-local work.",
                "Check previews such as `preview_delete_node` before destructive node changes.",
            ),
            (
                "Do not leave durable contract decisions only in conversation.",
                "Do not rewrite a child content node boundary from a lower-level worker role.",
            ),
        ),
    ),
    SkillKey.CONTENT_CONTRACT_READING.value: LeanSkillDefinition(
        name="content-contract-reading",
        description="Interprets Lean Constellation content node contracts as task input.",
        group="node",
        required_tool_groups=_groups(AppGroup.NODE_CONTRACT_READ_CURRENT),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "content-contract-reading",
            "Use this skill when a content plan, recon agent, declaration worker, or reviewer must understand a node goal, boundary, objective, materials, dependencies, Mathlib references, and interfaces before acting.",
            (
                "Read the current content contract first with `get_current_node_contract`.",
                "Separate owned material from context material.",
                "Use `list_current_node_deps`, `list_node_material_refs`, and `get_current_node_mathlib_hints` as the allowed working context when those tools are visible.",
                "Keep local changes inside the current task authority.",
                "Block or return feedback when the contract is unclear or inconsistent.",
            ),
            (
                "Do not silently redefine the Coordinator-owned goal or boundary.",
                "Do not treat a summary as a substitute for current contract state.",
            ),
        ),
    ),
    SkillKey.VISIBLE_NODE_DEPENDENCY_RECON.value: LeanSkillDefinition(
        name="visible-node-dependency-recon",
        description="Finds and records useful visible node dependencies for a Lean Constellation content node.",
        group="node",
        required_tool_groups=_groups(
            AppGroup.NODE_CONTRACT_READ_CURRENT,
            AppGroup.NODE_BOUNDARY_READ_CURRENT,
            AppGroup.NODE_CONTRACT_DEPENDENCY_CURRENT_WRITE,
            AppGroup.DECL_READINESS_READ,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "visible-node-dependency-recon",
            "Use this skill when inspecting ready same-repository boundaries or already attached provider boundaries and adding current-node dependencies when justified.",
            (
                "Start from the current contract and objective with `get_current_node_contract`.",
                "Inspect same-repository ready boundaries before provider boundaries with `list_current_visible_node_boundaries`.",
                "Inspect public declarations selectively with `list_content_public_decls` and `compute_decl_dependency_closure`.",
                "Add dependencies with `add_current_node_dep` only when they support the node objective and are visible.",
                "Report unresolved needs without inventing unavailable dependencies.",
            ),
            (
                "Do not use non-ready private declarations.",
                "Do not create repository requirements or resource requests from this skill.",
            ),
        ),
    ),
    SkillKey.SCOPE_EXPORT_INTERFACE_CURATION.value: LeanSkillDefinition(
        name="scope-export-interface-curation",
        description="Curates Lean Constellation scope exports and interface bindings.",
        group="node",
        required_tool_groups=_groups(
            AppGroup.SCOPE_EXPORT_INTERFACE_READ,
            AppGroup.SCOPE_EXPORT_INTERFACE_WRITE,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "scope-export-interface-curation",
            "Use this skill when closing a scope node, selecting public declarations from ready children, binding scope interfaces, checking projection/readiness, or committing a scope contract summary.",
            (
                "Check child readiness before curating exports.",
                "Read required interfaces and current export candidates with `list_node_interfaces`, `list_scope_export_candidates`, and `list_scope_exports`.",
                "Choose exports that belong to the scope public view and write them with `add_scope_export` or `remove_scope_export`.",
                "Bind interfaces only to declarations that satisfy their meaning with `bind_node_interface`.",
                "Run `check_content_node_ready` or available projection checks before submit.",
            ),
            (
                "Do not export unstable private implementation details.",
                "Do not use exports to hide an unsatisfied interface.",
            ),
        ),
    ),
    SkillKey.MATERIAL_ACQUISITION.value: LeanSkillDefinition(
        name="material-acquisition",
        description="Guides agents using material acquisition and extraction tools for arXiv, web, PDF, HTML, archive, and local-file targets.",
        group="material",
        required_tool_groups=(),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "material-acquisition",
            "Use this skill before organizing fetched artifacts into a source corpus or local resource draft.",
            (
                "Normalize the target kind before acquiring material.",
                "Fetch or import source targets with `acquire_source_material` or `import_source_material`; fetch or import resource targets with `acquire_material_resource` or `import_material_file`.",
                "Extract readable text or project files with `extract_source_artifact` or `extract_material_artifact` when needed.",
                "Normalize text with `normalize_source_text_material` or `normalize_material_text` before downstream use.",
                "Preserve useful originals and normalized outputs separately.",
                "Treat tool failures as concrete repair or blocked information.",
            ),
            (
                "Do not replace the repository source target with unrelated material.",
                "Do not rely on a raw PDF or archive when normalized text is required by downstream work.",
            ),
        ),
    ),
    SkillKey.EXTERNAL_RESOURCE_DISCOVERY.value: LeanSkillDefinition(
        name="external-resource-discovery",
        description="Guides agents that are allowed to discover new external material targets before submitting explicit resource requests.",
        group="resource",
        required_tool_groups=(),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "external-resource-discovery",
            "Use this skill when existing source and resource material is insufficient and the agent must find a precise arXiv, web, or local-file target without broad duplicate searching.",
            (
                "Start from existing source, resources, Mathlib, and visible dependencies with `get_material_context`, `search_material_text`, and available Mathlib index tools.",
                "Keep search narrow and tied to the current mathematical need.",
                "Prefer reliable mathematical sources and stable URLs.",
                "Use `search_arxiv_theorems` only for arXiv theorem-like candidates, and choose one accurate target rather than a broad search result list.",
                "Submit or report the target with evidence for why it is needed.",
            ),
            (
                "Do not create duplicate resources for material already available.",
                "Do not use vague web search output as if it were curated evidence.",
            ),
        ),
    ),
    SkillKey.RESOURCE_REQUEST_HANDLING.value: LeanSkillDefinition(
        name="resource-request-handling",
        description="Guides callers of Lean Constellation resource requests.",
        group="resource",
        required_tool_groups=_groups(SubmitGroup.RESOURCE_REQUEST_SUBMIT),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "resource-request-handling",
            "Use this skill when an agent can submit an explicit resource target to ResourceCurationFlow and later interpret duplicate, local resource, external repo required, or rejected callback results.",
            (
                "Check existing material before submitting a request.",
                "Call `submit_resource_request` only for precise targets with a clear reason.",
                "After a duplicate result, use the returned existing reference when relevant.",
                "After a local resource result, decide whether the current node should attach it with `add_current_material_ref` when that tool is visible.",
                "After an external repository result, decide whether the current task must return to Coordinator-level handling.",
            ),
            (
                "Do not let declaration workers directly dispatch resource curation in the first version.",
                "Do not treat a rejected resource as evidence.",
            ),
        ),
    ),
    SkillKey.RESOURCE_DRAFT_CURATION.value: LeanSkillDefinition(
        name="resource-draft-curation",
        description="Guides resource curation agents when preparing a local Resource draft.",
        group="resource",
        required_tool_groups=_groups(AppGroup.RESOURCE_DRAFT_WRITE, SubmitGroup.RESOURCE_CURATOR_SUBMIT),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "resource-draft-curation",
            "Use this skill for local Resource draft layout, README requirements, normalized text requirements, draft checks, and local_resource_created submit readiness.",
            (
                "Allocate or inspect the current resource draft with `allocate_resource_draft` or `get_resource_draft`.",
                "Place originals, normalized text, and notes in predictable locations using material acquisition tools.",
                "Write README content that identifies source, license or access notes, and reading order.",
                "Run `check_resource_draft` and repair failures within your authority.",
                "Call `submit_local_resource_created` only after the draft is coherent.",
            ),
            (
                "Do not use this skill for duplicate, external-repo-required, or rejected outcomes.",
                "Do not attach the resource to a content node from the curator role.",
            ),
        ),
    ),
    SkillKey.COORDINATOR_NODE_DECOMPOSITION.value: LeanSkillDefinition(
        name="coordinator-node-decomposition",
        description="Guides a native repo Coordinator in decomposing the repository node tree from Main into scope nodes and content nodes.",
        group="coordinator",
        required_tool_groups=_groups(
            AppGroup.NODE_TREE_COORDINATOR_READ,
            AppGroup.NODE_TREE_COORDINATOR_WRITE,
            AppGroup.NODE_CONTRACT_READ_COORDINATOR,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "coordinator-node-decomposition",
            "Use this skill when deciding whether to create or revise a Scope node or Content node, splitting mathematical regions, assigning sibling boundaries, or repairing an over-broad or over-fragmented node structure.",
            (
                "Start from root interfaces, source index structure, current tree from `get_node_tree`, and ready provider context from `list_ready_provider_repos`.",
                "Choose scope nodes for broad mathematical areas and content nodes for focused declaration work.",
                "Write node contracts through `create_scope_node`, `create_content_node`, and `update_node_contract_text`.",
                "Use `preview_delete_node` before deleting or replacing tree structure.",
                "Leave the tree in a state that can dispatch content tasks without hidden boundary guesses.",
            ),
            (
                "Do not split only by source file layout.",
                "Do not put actual declaration work directly in scope nodes.",
            ),
        ),
    ),
    SkillKey.COORDINATOR_SCOPE_LIFECYCLE.value: LeanSkillDefinition(
        name="coordinator-scope-lifecycle",
        description="Guides a Coordinator through the lifecycle of one Scope node.",
        group="coordinator",
        required_tool_groups=_groups(AppGroup.SCOPE_EXPORT_INTERFACE_READ, AppGroup.SCOPE_EXPORT_INTERFACE_WRITE, AppGroup.SCOPE_CLOSE_READ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "coordinator-scope-lifecycle",
            "Use this skill when creating or updating a scope contract, analyzing child nodes, curating exports and interface bindings, committing the scope contract, and deciding follow-up work.",
            (
                "Create or update the scope contract before child work depends on it.",
                "Plan required public interfaces and child responsibilities.",
                "Analyze children with `get_scope_close_view` when they become ready.",
                "Use `list_scope_export_candidates`, `add_scope_export`, and `bind_node_interface` for export and binding details.",
                "Commit scope summaries only after readiness and projection checks pass through the available scope tools.",
            ),
            (
                "Do not close a scope while required child work is unresolved.",
                "Do not export declarations merely because they exist.",
            ),
        ),
    ),
    SkillKey.COORDINATOR_CONTENT_TASK_LIFECYCLE.value: LeanSkillDefinition(
        name="coordinator-content-task-lifecycle",
        description="Guides a Coordinator through one content node task cycle.",
        group="coordinator",
        required_tool_groups=_groups(SubmitGroup.COORDINATOR_SUBMIT, AppGroup.CONTENT_TASK_ADMISSION_READ, AppGroup.NODE_CONTRACT_READ_COORDINATOR),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "coordinator-content-task-lifecycle",
            "Use this skill when preparing content node contracts before dispatch, submitting runnable content tasks, processing callbacks, committing summaries, and deciding follow-up actions.",
            (
                "Check that candidate content nodes are runnable with `check_content_task_admission` before dispatch.",
                "Prepare each task contract with current objective and constraints.",
                "Submit runnable tasks through `submit_content_node_tasks`.",
                "Process ready, blocked, or failed callbacks against current repository truth.",
                "Decide whether to update contracts, create follow-up tasks, call `submit_resource_request`, call `submit_repo_requirement`, or close scopes.",
            ),
            (
                "Do not dispatch tasks whose dependencies are not visible or whose boundaries are unclear.",
                "Do not handle content-node internal worker stages from the Coordinator role.",
            ),
        ),
    ),
    SkillKey.MATHLIB_INDEX_FIRST_RECON.value: LeanSkillDefinition(
        name="mathlib-index-first-recon",
        description="Use repo-level MathlibIndex before running broader Mathlib search.",
        group="mathlib",
        required_tool_groups=_groups(AppGroup.MATHLIB_INDEX_READ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "mathlib-index-first-recon",
            "Use this skill when finding Mathlib modules or declarations for a Lean Constellation node while avoiding repeated global search.",
            (
                "Read current node Mathlib hints first with `get_current_node_mathlib_hints`.",
                "Translate the node objective into search directions.",
                "Search the repo MathlibIndex with `search_mathlib_index` before external search.",
                "Identify index gaps explicitly.",
                "Report useful local coverage and unresolved needs.",
            ),
            (
                "Do not skip the local index and repeat broad search by default.",
                "Do not treat a name match as semantic equivalence without inspection.",
            ),
        ),
    ),
    SkillKey.MATHLIB_SEMANTIC_SEARCH_NAVIGATION.value: LeanSkillDefinition(
        name="mathlib-semantic-search-navigation",
        description="Search Mathlib with LeanExplore and inspect Mathlib modules/source context through navigation tools.",
        group="mathlib",
        required_tool_groups=_groups(AppGroup.MATHLIB_SEMANTIC_SEARCH, AppGroup.MATHLIB_NAVIGATION),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "mathlib-semantic-search-navigation",
            "Use this skill after current node hints and repo-level MathlibIndex are insufficient.",
            (
                "Use `search_mathlib_declarations` for mathematical concepts and `search_external_mathlib` when additional toolkit-backed backends are appropriate.",
                "Inspect candidate declarations and modules with `inspect_mathlib_search_candidate`, `inspect_mathlib_declaration`, and `inspect_mathlib_module` before relying on them.",
                "Confirm namespace, assumptions, typeclasses, imports, and theorem direction.",
                "Record useful candidates later through `ingest_mathlib_candidate`, `record_mathlib_decl`, or `record_mathlib_module` when write tools are visible.",
                "Report unresolved directions when search is inconclusive.",
            ),
            (
                "Do not silently substitute a near match for the source concept.",
                "Do not add Mathlib hints for unverified candidates.",
            ),
        ),
    ),
    SkillKey.MATHLIB_INDEX_ENTRY_CURATION.value: LeanSkillDefinition(
        name="mathlib-index-entry-curation",
        description="Record verified Mathlib modules and declarations into the repo-level MathlibIndex.",
        group="mathlib",
        required_tool_groups=_groups(AppGroup.MATHLIB_INDEX_WRITE),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "mathlib-index-entry-curation",
            "Use this skill after search or navigation has identified reusable Mathlib knowledge.",
            (
                "Record modules with concise purpose and import relevance through `record_mathlib_module`.",
                "Record declarations with statement meaning and usage notes through `record_mathlib_decl` or `ingest_mathlib_candidate`.",
                "Attach important declarations to module entries with `add_mathlib_module_important_decl` when useful.",
                "Keep entries lightweight and reusable across nodes.",
                "Re-read entries after writing with `get_mathlib_module_entry` or `get_mathlib_decl_entry`.",
            ),
            (
                "Do not use the index as a dumping ground for every search result.",
                "Do not record candidates whose semantics are still unclear.",
            ),
        ),
    ),
    SkillKey.CURRENT_NODE_MATHLIB_HINT_MAINTENANCE.value: LeanSkillDefinition(
        name="current-node-mathlib-hint-maintenance",
        description="Add or remove Mathlib module and declaration hints for a Lean Constellation node contract.",
        group="mathlib",
        required_tool_groups=_groups(AppGroup.NODE_MATHLIB_HINT_READ, AppGroup.NODE_MATHLIB_HINT_WRITE),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "current-node-mathlib-hint-maintenance",
            "Use this skill after relevant Mathlib entries are known or recorded in MathlibIndex.",
            (
                "Understand whether a module hint or declaration hint is appropriate.",
                "Add module hints for imports that are broadly useful to the node with `add_current_mathlib_module_hint`.",
                "Add declaration hints for specific facts or definitions that support planned work with `add_current_mathlib_decl_hint`.",
                "Remove stale hints conservatively with `remove_current_mathlib_module_hint` or `remove_current_mathlib_decl_hint`.",
                "Validate current hints with `validate_current_node_mathlib_hints`.",
            ),
            (
                "Do not add broad imports only because they compile.",
                "Do not use hints to bypass node dependency or source-fidelity review.",
            ),
        ),
    ),
    SkillKey.CONTENT_PREPARATION_ORCHESTRATION.value: LeanSkillDefinition(
        name="content-preparation-orchestration",
        description="Use when the ContentPlanAgent decides whether to dispatch node dependency, Mathlib, or resource recon child flows.",
        group="content_plan",
        required_tool_groups=_groups(SubmitGroup.CONTENT_PLAN_SUBMIT),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "Content Preparation Orchestration",
            "Use this skill when the current content node needs preparation before a declaration round.",
            (
                "Read current contract, DeclGraph state, source/resource context, dependencies, and Mathlib hints.",
                "Decide whether node dependency, Mathlib, or resource recon is actually needed.",
                "Call `submit_content_preparation_recon` with only objective and context_summary for each child flow.",
                "Run each preparation kind at most once per content task.",
                "After callback, update the plan or proceed to declaration round planning.",
            ),
            (
                "Do not dispatch preparation flows just to gather vague context.",
                "Do not place full contracts or graph dumps into child prompts.",
            ),
        ),
    ),
    SkillKey.DECL_STRATEGY_PLANNING.value: LeanSkillDefinition(
        name="decl-strategy-planning",
        description="Use when the ContentPlanAgent creates, continues, closes, or replaces a DeclGraph strategy.",
        group="content_plan",
        required_tool_groups=_groups(AppGroup.DECL_GRAPH_READ_CURRENT, AppGroup.DECL_STRATEGY_WRITE),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "Decl Strategy Planning",
            "Use this skill to maintain the current plan for how the content node will make mathematical progress.",
            (
                "Read current truth with `get_current_decl_graph_store`, `list_decl_strategies`, and `get_decl_strategy` before changing strategy.",
                "Analyze node objective, existing declarations, dependencies, resource gaps, and round history.",
                "Create a strategy with `ensure_open_decl_strategy` and a clear purpose.",
                "Close or supersede a strategy with `close_decl_strategy` based on actual results.",
                "Keep targeted supplements small and justified.",
            ),
            (
                "Do not keep an obsolete strategy open after it has failed or been replaced.",
                "Do not encode vague aspirations as actionable strategy.",
            ),
        ),
    ),
    SkillKey.DECL_ROUND_CHANGE_PLANNING.value: LeanSkillDefinition(
        name="decl-round-change-planning",
        description="Use when the ContentPlanAgent prepares create, update, or delete changes for the next DeclGraph round.",
        group="content_plan",
        required_tool_groups=_groups(AppGroup.DECL_ROUND_CHANGE_WRITE, SubmitGroup.CONTENT_PLAN_SUBMIT),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "Decl Round Change Planning",
            "Use this skill when preparing the concrete declaration changes for the next DeclGraphRoundFlow.",
            (
                "Choose a small independent batch aligned with the current strategy.",
                "Write create changes with `plan_create_decl` and clear mathematical objectives.",
                "Write update changes with `plan_update_decl` and the stage that should be repaired.",
                "Preview delete closure with `preview_decl_delete_closure` before `plan_delete_decl`.",
                "Call `validate_decl_round_draft` before `submit_current_decl_round`.",
            ),
            (
                "Do not choose ready as a planned declaration state.",
                "Do not hide important helper lemmas as untracked local code.",
            ),
        ),
    ),
    SkillKey.DECL_ROUND_CLOSEOUT.value: LeanSkillDefinition(
        name="decl-round-closeout",
        description="Use when the ContentPlanAgent receives a DeclGraphRoundFlow callback and must summarize and commit the round.",
        group="content_plan",
        required_tool_groups=_groups(AppGroup.DECL_ROUND_CLOSEOUT_WRITE, AppGroup.DECL_GRAPH_READ_CURRENT),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "Decl Round Closeout",
            "Use this skill before planning the next action after a round terminal callback.",
            (
                "Read the terminal context and changed declarations.",
                "Write change summaries one by one with `write_decl_change_summary`.",
                "Write the round summary with `write_decl_round_summary` and success, blocked, or failed meaning.",
                "Commit terminal closeout with `mark_decl_round_terminal`.",
                "Decide whether to plan another round, run preparation, or complete the content task.",
            ),
            (
                "Do not start a new round before closeout is recorded.",
                "Do not hide blocked causes in a generic summary.",
            ),
        ),
    ),
    SkillKey.CONTENT_NODE_COMPLETION_DECISION.value: LeanSkillDefinition(
        name="content-node-completion-decision",
        description="Use when the ContentPlanAgent decides whether the current content node task should end as ready, blocked, or failed.",
        group="content_plan",
        required_tool_groups=_groups(SubmitGroup.CONTENT_COMPLETION_SUBMIT, AppGroup.DECL_READINESS_READ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "Content Node Completion Decision",
            "Use this skill when deciding whether to submit ready, blocked, or failed for a content node task.",
            (
                "Check contract satisfaction, declaration readiness, dependencies, interfaces, and unresolved callbacks with `check_content_node_ready`.",
                "Call `submit_content_node_ready` only when current tools show the node satisfies its contract.",
                "Call `submit_content_node_blocked` when upstream Coordinator action or external prerequisite is required.",
                "Call `submit_content_node_failed` when the current automated route is exhausted under the allowed strategy.",
                "Stop state-changing work after an accepted completion submit.",
            ),
            (
                "Do not mark ready based only on the PlanAgent's narrative.",
                "Do not use failed for missing external prerequisites that should be blocked.",
            ),
        ),
    ),
    SkillKey.DECL_DEPENDENCY_ORIGIN_CURATION.value: LeanSkillDefinition(
        name="decl-dependency-origin-curation",
        description="Curate source/resource origins and declaration dependencies for Lean Constellation declaration stage artifacts.",
        group="decl_stage",
        required_tool_groups=_groups(AppGroup.DECL_DETAIL_READ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "decl-dependency-origin-curation",
            "Use this skill when a declaration worker or reviewer must connect a statement or proof idea to evidence, visible project declarations, Mathlib declarations, and blocked follow-up needs.",
            (
                "Start from the declaration task and stage objective with `get_decl`.",
                "Search source, resources, visible declarations, and Mathlib with `search_material_text`, `list_content_public_decls`, and Mathlib index tools in the right order.",
                "Choose origins that actually support the statement or proof.",
                "Choose project and Mathlib dependencies for real mathematical use.",
                "Differentiate statement dependencies from proof dependencies.",
            ),
            (
                "Do not invent source support for generated ideas.",
                "Do not dispatch new resource curation from declaration worker stages in the first version.",
            ),
        ),
    ),
    SkillKey.DECL_OWNED_LEAN_FILE_CAPTURE_CHECK.value: LeanSkillDefinition(
        name="decl-owned-lean-file-capture-check",
        description="Work safely with Lean Constellation declaration-owned Lean files, manual Lean checks, and capture/check tools.",
        group="lean",
        required_tool_groups=_groups(AppGroup.FORMAL_DIAGNOSTICS_READ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "decl-owned-lean-file-capture-check",
            "Use this skill when a formal worker edits a tracked declaration file or when a reviewer needs to understand formal capture semantics.",
            (
                "Use `prepare_statement_formal_file` or `prepare_proof_formal_file` before editing and stay inside the assigned declaration-owned file.",
                "Keep system markers and prepared structure intact.",
                "Use `run_lean_file_diagnostics`, `check_statement_formal_policy`, or `check_proof_formal_policy` for debugging and policy checks.",
                "Capture formal code through `capture_statement_formal_file` or `capture_proof_formal_file` after editing.",
                "Use `check_formal_stage_consistency` before worker submit when it is available.",
                "Respect safety policy before worker submit.",
            ),
            (
                "Do not rely on uncaptured edits as accepted formal content.",
                "Do not use sorry, admit, axiom, or equivalent shortcuts in completed work.",
            ),
        ),
    ),
    SkillKey.LEAN_STATEMENT_FORMALIZATION.value: LeanSkillDefinition(
        name="lean-statement-formalization",
        description="Formalize an accepted natural-language declaration statement into a Lean declaration.",
        group="lean",
        required_tool_groups=_groups(
            AppGroup.DECL_STAGE_STATEMENT_FORMAL_FILE,
            AppGroup.DECL_STAGE_STATEMENT_FORMAL_FILE_WRITE,
            AppGroup.FORMAL_DIAGNOSTICS_READ,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "lean-statement-formalization",
            "Use this skill for Statement Formal workers after statement NL has been accepted.",
            (
                "Start from the accepted statement and declared objective.",
                "Map variables, assumptions, definitions, and conclusions to Lean deliberately.",
                "Search dependencies in visible project context and Mathlib before adding imports or hints.",
                "Prepare and edit the declaration-owned file with `prepare_statement_formal_file`, then run `run_lean_file_diagnostics` and `check_statement_formal_policy`.",
                "Capture with `capture_statement_formal_file`, check consistency with `check_formal_stage_consistency`, and refine dependencies before `submit_stage_worker_completed`.",
            ),
            (
                "Do not silently change statement meaning to make Lean easier.",
                "Do not complete theorem proofs in the statement formalization stage.",
            ),
        ),
    ),
    SkillKey.LEAN_PROOF_FORMALIZATION.value: LeanSkillDefinition(
        name="lean-proof-formalization",
        description="Formalize a reviewed natural-language proof route into a Lean proof while preserving the accepted formal statement.",
        group="lean",
        required_tool_groups=_groups(
            AppGroup.DECL_STAGE_PROOF_FORMAL_FILE,
            AppGroup.DECL_STAGE_PROOF_FORMAL_FILE_WRITE,
            AppGroup.FORMAL_DIAGNOSTICS_READ,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "lean-proof-formalization",
            "Use this skill for Proof Formal workers after proof NL has been accepted.",
            (
                "Start from the frozen statement and reviewed proof route.",
                "Search formal dependencies in visible project context and Mathlib.",
                "Prepare the assigned file with `prepare_proof_formal_file` and edit the proof body without changing the accepted statement.",
                "Use `run_lean_file_diagnostics` and `check_proof_formal_policy` iteratively, then capture at the durable boundary with `capture_proof_formal_file`.",
                "Use `check_formal_stage_consistency` before submit, and call `submit_stage_worker_blocked` when the route needs planning changes.",
            ),
            (
                "Do not alter the theorem statement to make the proof work.",
                "Do not leave sorry, admit, axiom, or equivalent shortcuts in completed proof work.",
            ),
        ),
    ),
}


__all__ = [
    "LeanSkillDefinition",
    "SKILL_DEFINITIONS",
    "build_skill_specs",
    "known_skill_keys",
    "materialize_skill_specs",
    "required_tool_groups_for_skill",
]
