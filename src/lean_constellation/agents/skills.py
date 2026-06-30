"""Application SkillSpec registry for Lean Constellation Agent homes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from agent_runtime_kit.agent.skills import SkillSpec, write_skill_spec


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


def build_skill_specs(skill_keys: Iterable[str] | None = None) -> dict[str, SkillSpec]:
    """Build ARK SkillSpec objects for all or selected Lean skills."""

    selected = set(skill_keys) if skill_keys is not None else set(SKILL_DEFINITIONS)
    missing = sorted(selected - set(SKILL_DEFINITIONS))
    if missing:
        raise KeyError(f"unknown skill keys: {', '.join(missing)}")
    return {
        key: SKILL_DEFINITIONS[key].to_ark_skill_spec()
        for key in sorted(selected)
    }


def materialize_skill_specs(
    target_root: Path,
    skill_keys: Iterable[str] | None = None,
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


def required_tool_groups_for_skill(skill_key: str) -> tuple[str, ...]:
    try:
        return SKILL_DEFINITIONS[skill_key].required_tool_groups
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
    "node-contract-design": LeanSkillDefinition(
        name="node-contract-design",
        description="Designs and updates Lean Constellation scope and content node contracts.",
        group="node",
        required_tool_groups=(
            "node_contract_read_coordinator",
            "node_contract_core_coordinator_write",
            "node_tree_coordinator_write",
            "scope_export_interface_write",
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "node-contract-design",
            "Use this skill when planning node tree structure, creating child nodes, preparing content node tasks, or updating contract goals, boundaries, objectives, materials, dependencies, constraints, or interfaces.",
            (
                "Read the current scope or content contract before changing it.",
                "Write goals and boundaries in mathematical terms rather than file-layout terms.",
                "Make sibling boundaries explicit and avoid duplicate ownership.",
                "Prepare content tasks with enough objective, material, dependency, and interface context for node-local work.",
                "Check previews or gates before submit whenever the tools provide them.",
            ),
            (
                "Do not leave durable contract decisions only in conversation.",
                "Do not rewrite a child content node boundary from a lower-level worker role.",
            ),
        ),
    ),
    "content-contract-reading": LeanSkillDefinition(
        name="content-contract-reading",
        description="Interprets Lean Constellation content node contracts as task input.",
        group="node",
        required_tool_groups=("node_contract_read_current",),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "content-contract-reading",
            "Use this skill when a content plan, recon agent, declaration worker, or reviewer must understand a node goal, boundary, objective, materials, dependencies, Mathlib references, and interfaces before acting.",
            (
                "Read the current content contract first.",
                "Separate owned material from context material.",
                "Use visible dependencies and Mathlib references as the allowed working context.",
                "Keep local changes inside the current task authority.",
                "Block or return feedback when the contract is unclear or inconsistent.",
            ),
            (
                "Do not silently redefine the Coordinator-owned goal or boundary.",
                "Do not treat a summary as a substitute for current contract state.",
            ),
        ),
    ),
    "visible-node-dependency-recon": LeanSkillDefinition(
        name="visible-node-dependency-recon",
        description="Finds and records useful visible node dependencies for a Lean Constellation content node.",
        group="node",
        required_tool_groups=(
            "node_contract_read_current",
            "node_boundary_read_current",
            "node_contract_dependency_current_write",
            "decl_readiness_read",
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "visible-node-dependency-recon",
            "Use this skill when inspecting ready same-repository boundaries or already attached provider boundaries and adding current-node dependencies when justified.",
            (
                "Start from the current contract and objective.",
                "Inspect same-repository ready boundaries before provider boundaries.",
                "Inspect public declarations selectively for relevance.",
                "Add dependencies only when they support the node objective and are visible.",
                "Report unresolved needs without inventing unavailable dependencies.",
            ),
            (
                "Do not use non-ready private declarations.",
                "Do not create repository requirements or resource requests from this skill.",
            ),
        ),
    ),
    "scope-export-interface-curation": LeanSkillDefinition(
        name="scope-export-interface-curation",
        description="Curates Lean Constellation scope exports and interface bindings.",
        group="node",
        required_tool_groups=(
            "scope_export_interface_read",
            "scope_export_interface_write",
            "root_interface_prepare_read",
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "scope-export-interface-curation",
            "Use this skill when closing a scope node, selecting public declarations from ready children, binding scope interfaces, checking projection/readiness, or committing a scope contract summary.",
            (
                "Check child readiness before curating exports.",
                "Read required interfaces and current export candidates.",
                "Choose exports that belong to the scope public view.",
                "Bind interfaces only to declarations that satisfy their meaning.",
                "Run available projection or readiness checks before submit.",
            ),
            (
                "Do not export unstable private implementation details.",
                "Do not use exports to hide an unsatisfied interface.",
            ),
        ),
    ),
    "material-acquisition": LeanSkillDefinition(
        name="material-acquisition",
        description="Guides agents using material acquisition and extraction tools for arXiv, web, PDF, HTML, archive, and local-file targets.",
        group="material",
        required_tool_groups=("material_acquisition", "source_acquisition"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "material-acquisition",
            "Use this skill before organizing fetched artifacts into a source corpus or local resource draft.",
            (
                "Normalize the target kind before acquiring material.",
                "Fetch or import the artifact through the available acquisition tools.",
                "Extract readable text or project files when needed.",
                "Preserve useful originals and normalized outputs separately.",
                "Treat tool failures as concrete repair or blocked information.",
            ),
            (
                "Do not replace the repository source target with unrelated material.",
                "Do not rely on a raw PDF or archive when normalized text is required by downstream work.",
            ),
        ),
    ),
    "external-resource-discovery": LeanSkillDefinition(
        name="external-resource-discovery",
        description="Guides agents that are allowed to discover new external material targets before submitting explicit resource requests.",
        group="resource",
        required_tool_groups=("external_resource_discovery", "external_theorem_search"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "external-resource-discovery",
            "Use this skill when existing source and resource material is insufficient and the agent must find a precise arXiv, web, or local-file target without broad duplicate searching.",
            (
                "Start from existing source, resources, Mathlib, and visible dependencies.",
                "Keep search narrow and tied to the current mathematical need.",
                "Prefer reliable mathematical sources and stable URLs.",
                "Choose one accurate target rather than a broad search result list.",
                "Submit or report the target with evidence for why it is needed.",
            ),
            (
                "Do not create duplicate resources for material already available.",
                "Do not use vague web search output as if it were curated evidence.",
            ),
        ),
    ),
    "resource-request-handling": LeanSkillDefinition(
        name="resource-request-handling",
        description="Guides callers of Lean Constellation resource requests.",
        group="resource",
        required_tool_groups=("resource_request_submit", "node_contract_material_current_write"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "resource-request-handling",
            "Use this skill when an agent can submit an explicit resource target to ResourceCurationFlow and later interpret duplicate, local resource, external repo required, or rejected callback results.",
            (
                "Check existing material before submitting a request.",
                "Submit only precise targets with a clear reason.",
                "After a duplicate result, use the returned existing reference when relevant.",
                "After a local resource result, decide whether the current node should attach the resource.",
                "After an external repository result, decide whether the current task must return to Coordinator-level handling.",
            ),
            (
                "Do not let declaration workers directly dispatch resource curation in the first version.",
                "Do not treat a rejected resource as evidence.",
            ),
        ),
    ),
    "resource-draft-curation": LeanSkillDefinition(
        name="resource-draft-curation",
        description="Guides resource curation agents when preparing a local Resource draft.",
        group="resource",
        required_tool_groups=("resource_draft_write", "material_acquisition", "resource_curator_submit"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "resource-draft-curation",
            "Use this skill for local Resource draft layout, README requirements, normalized text requirements, draft checks, and local_resource_created submit readiness.",
            (
                "Allocate or inspect the current resource draft.",
                "Place originals, normalized text, and notes in predictable locations.",
                "Write README content that identifies source, license or access notes, and reading order.",
                "Run draft checks and repair failures within your authority.",
                "Submit local_resource_created only after the draft is coherent.",
            ),
            (
                "Do not use this skill for duplicate, external-repo-required, or rejected outcomes.",
                "Do not attach the resource to a content node from the curator role.",
            ),
        ),
    ),
    "coordinator-node-decomposition": LeanSkillDefinition(
        name="coordinator-node-decomposition",
        description="Guides a native repo Coordinator in decomposing the repository node tree from Main into scope nodes and content nodes.",
        group="coordinator",
        required_tool_groups=("node_tree_coordinator_read", "node_tree_coordinator_write", "node_contract_read_coordinator"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "coordinator-node-decomposition",
            "Use this skill when deciding whether to create or revise a Scope node or Content node, splitting mathematical regions, assigning sibling boundaries, or repairing an over-broad or over-fragmented node structure.",
            (
                "Start from root interfaces, source index structure, current tree, and ready provider context.",
                "Choose scope nodes for broad mathematical areas and content nodes for focused declaration work.",
                "Write node contracts through node-contract-design.",
                "Revise the tree when boundaries overlap or missing shared foundations are discovered.",
                "Leave the tree in a state that can dispatch content tasks without hidden boundary guesses.",
            ),
            (
                "Do not split only by source file layout.",
                "Do not put actual declaration work directly in scope nodes.",
            ),
        ),
    ),
    "coordinator-scope-lifecycle": LeanSkillDefinition(
        name="coordinator-scope-lifecycle",
        description="Guides a Coordinator through the lifecycle of one Scope node.",
        group="coordinator",
        required_tool_groups=("scope_export_interface_read", "scope_export_interface_write", "scope_close_read"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "coordinator-scope-lifecycle",
            "Use this skill when creating or updating a scope contract, analyzing child nodes, curating exports and interface bindings, committing the scope contract, and deciding follow-up work.",
            (
                "Create or update the scope contract before child work depends on it.",
                "Plan required public interfaces and child responsibilities.",
                "Analyze children when they become ready.",
                "Use scope-export-interface-curation for export and binding details.",
                "Commit scope summaries only after readiness and projection checks pass.",
            ),
            (
                "Do not close a scope while required child work is unresolved.",
                "Do not export declarations merely because they exist.",
            ),
        ),
    ),
    "coordinator-content-task-lifecycle": LeanSkillDefinition(
        name="coordinator-content-task-lifecycle",
        description="Guides a Coordinator through one content node task cycle.",
        group="coordinator",
        required_tool_groups=("content_task_admission_read", "coordinator_submit", "node_contract_read_coordinator"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "coordinator-content-task-lifecycle",
            "Use this skill when preparing content node contracts before dispatch, submitting runnable content tasks, processing callbacks, committing summaries, and deciding follow-up actions.",
            (
                "Check that candidate content nodes are runnable before dispatch.",
                "Prepare each task contract with current objective and constraints.",
                "Submit runnable tasks through the coordinator submit tool.",
                "Process ready, blocked, or failed callbacks against current repository truth.",
                "Decide whether to update contracts, create follow-up tasks, request resources, or close scopes.",
            ),
            (
                "Do not dispatch tasks whose dependencies are not visible or whose boundaries are unclear.",
                "Do not handle content-node internal worker stages from the Coordinator role.",
            ),
        ),
    ),
    "mathlib-index-first-recon": LeanSkillDefinition(
        name="mathlib-index-first-recon",
        description="Use repo-level MathlibIndex before running broader Mathlib search.",
        group="mathlib",
        required_tool_groups=("node_mathlib_hint_read", "mathlib_index_read"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "mathlib-index-first-recon",
            "Use this skill when finding Mathlib modules or declarations for a Lean Constellation node while avoiding repeated global search.",
            (
                "Read current node Mathlib hints first.",
                "Translate the node objective into search directions.",
                "Search the repo MathlibIndex before external search.",
                "Identify index gaps explicitly.",
                "Report useful local coverage and unresolved needs.",
            ),
            (
                "Do not skip the local index and repeat broad search by default.",
                "Do not treat a name match as semantic equivalence without inspection.",
            ),
        ),
    ),
    "mathlib-semantic-search-navigation": LeanSkillDefinition(
        name="mathlib-semantic-search-navigation",
        description="Search Mathlib with LeanExplore and inspect Mathlib modules/source context through navigation tools.",
        group="mathlib",
        required_tool_groups=("mathlib_semantic_search", "mathlib_navigation"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "mathlib-semantic-search-navigation",
            "Use this skill after current node hints and repo-level MathlibIndex are insufficient.",
            (
                "Use semantic search for the mathematical concept, not only expected names.",
                "Inspect candidate declarations and modules before relying on them.",
                "Confirm namespace, assumptions, typeclasses, imports, and theorem direction.",
                "Record useful candidates for later index curation.",
                "Report unresolved directions when search is inconclusive.",
            ),
            (
                "Do not silently substitute a near match for the source concept.",
                "Do not add Mathlib hints for unverified candidates.",
            ),
        ),
    ),
    "mathlib-index-entry-curation": LeanSkillDefinition(
        name="mathlib-index-entry-curation",
        description="Record verified Mathlib modules and declarations into the repo-level MathlibIndex.",
        group="mathlib",
        required_tool_groups=("mathlib_index_write",),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "mathlib-index-entry-curation",
            "Use this skill after search or navigation has identified reusable Mathlib knowledge.",
            (
                "Record modules with concise purpose and import relevance.",
                "Record declarations with statement meaning and usage notes.",
                "Attach important declarations to module entries when useful.",
                "Keep entries lightweight and reusable across nodes.",
                "Re-read entries after writing when the tool provides a view.",
            ),
            (
                "Do not use the index as a dumping ground for every search result.",
                "Do not record candidates whose semantics are still unclear.",
            ),
        ),
    ),
    "current-node-mathlib-hint-maintenance": LeanSkillDefinition(
        name="current-node-mathlib-hint-maintenance",
        description="Add or remove Mathlib module and declaration hints for a Lean Constellation node contract.",
        group="mathlib",
        required_tool_groups=("node_mathlib_hint_read", "node_mathlib_hint_write"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "current-node-mathlib-hint-maintenance",
            "Use this skill after relevant Mathlib entries are known or recorded in MathlibIndex.",
            (
                "Understand whether a module hint or declaration hint is appropriate.",
                "Add module hints for imports that are broadly useful to the node.",
                "Add declaration hints for specific facts or definitions that support planned work.",
                "Remove hints conservatively when they are stale or misleading.",
                "Validate current hints when the tool provides a check.",
            ),
            (
                "Do not add broad imports only because they compile.",
                "Do not use hints to bypass node dependency or source-fidelity review.",
            ),
        ),
    ),
    "content-preparation-orchestration": LeanSkillDefinition(
        name="content-preparation-orchestration",
        description="Use when the ContentPlanAgent decides whether to dispatch node dependency, Mathlib, or resource recon child flows.",
        group="content_plan",
        required_tool_groups=("content_plan_submit",),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "Content Preparation Orchestration",
            "Use this skill when the current content node needs preparation before a declaration round.",
            (
                "Read current contract, DeclGraph state, source/resource context, dependencies, and Mathlib hints.",
                "Decide whether node dependency, Mathlib, or resource recon is actually needed.",
                "Give each child flow only objective and context_summary.",
                "Run each preparation kind at most once per content task.",
                "After callback, update the plan or proceed to declaration round planning.",
            ),
            (
                "Do not dispatch preparation flows just to gather vague context.",
                "Do not place full contracts or graph dumps into child prompts.",
            ),
        ),
    ),
    "decl-strategy-planning": LeanSkillDefinition(
        name="decl-strategy-planning",
        description="Use when the ContentPlanAgent creates, continues, closes, or replaces a DeclGraph strategy.",
        group="content_plan",
        required_tool_groups=("decl_graph_read_current", "decl_strategy_write"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "Decl Strategy Planning",
            "Use this skill to maintain the current plan for how the content node will make mathematical progress.",
            (
                "Read current truth before changing strategy.",
                "Analyze node objective, existing declarations, dependencies, resource gaps, and round history.",
                "Create a strategy with a clear purpose and expected path.",
                "Continue, close, or supersede a strategy based on actual results.",
                "Keep targeted supplements small and justified.",
            ),
            (
                "Do not keep an obsolete strategy open after it has failed or been replaced.",
                "Do not encode vague aspirations as actionable strategy.",
            ),
        ),
    ),
    "decl-round-change-planning": LeanSkillDefinition(
        name="decl-round-change-planning",
        description="Use when the ContentPlanAgent prepares create, update, or delete changes for the next DeclGraph round.",
        group="content_plan",
        required_tool_groups=("decl_round_change_write", "decl_catalog_plan_write", "content_plan_submit"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "Decl Round Change Planning",
            "Use this skill when preparing the concrete declaration changes for the next DeclGraphRoundFlow.",
            (
                "Choose a small independent batch aligned with the current strategy.",
                "Write create changes with clear mathematical objectives.",
                "Write update changes with the stage that should be repaired.",
                "Preview delete closure before planning deletion.",
                "Submit the round only after validation accepts the draft.",
            ),
            (
                "Do not choose ready as a planned declaration state.",
                "Do not hide important helper lemmas as untracked local code.",
            ),
        ),
    ),
    "decl-round-closeout": LeanSkillDefinition(
        name="decl-round-closeout",
        description="Use when the ContentPlanAgent receives a DeclGraphRoundFlow callback and must summarize and commit the round.",
        group="content_plan",
        required_tool_groups=("decl_round_closeout_write", "decl_graph_read_current"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "Decl Round Closeout",
            "Use this skill before planning the next action after a round terminal callback.",
            (
                "Read the terminal context and changed declarations.",
                "Write change summaries one by one.",
                "Write the round summary with success, blocked, or failed meaning.",
                "Commit closeout through the provided tools.",
                "Decide whether to plan another round, run preparation, or complete the content task.",
            ),
            (
                "Do not start a new round before closeout is recorded.",
                "Do not hide blocked causes in a generic summary.",
            ),
        ),
    ),
    "content-node-completion-decision": LeanSkillDefinition(
        name="content-node-completion-decision",
        description="Use when the ContentPlanAgent decides whether the current content node task should end as ready, blocked, or failed.",
        group="content_plan",
        required_tool_groups=("content_completion_submit", "decl_readiness_read"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "Content Node Completion Decision",
            "Use this skill when deciding whether to submit ready, blocked, or failed for a content node task.",
            (
                "Check contract satisfaction, declaration readiness, dependencies, interfaces, and unresolved callbacks.",
                "Submit ready only when current tools show the node satisfies its contract.",
                "Submit blocked when upstream Coordinator action or external prerequisite is required.",
                "Submit failed when the current automated route is exhausted under the allowed strategy.",
                "Stop state-changing work after an accepted completion submit.",
            ),
            (
                "Do not mark ready based only on the PlanAgent's narrative.",
                "Do not use failed for missing external prerequisites that should be blocked.",
            ),
        ),
    ),
    "decl-dependency-origin-curation": LeanSkillDefinition(
        name="decl-dependency-origin-curation",
        description="Curate source/resource origins and declaration dependencies for Lean Constellation declaration stage artifacts.",
        group="decl_stage",
        required_tool_groups=("decl_detail_read", "decl_history_read", "resource_library_read", "mathlib_index_read"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "decl-dependency-origin-curation",
            "Use this skill when a declaration worker or reviewer must connect a statement or proof idea to evidence, visible project declarations, Mathlib declarations, and blocked follow-up needs.",
            (
                "Start from the declaration task and stage objective.",
                "Search source, resources, visible declarations, and Mathlib in the right order.",
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
    "decl-owned-lean-file-capture-check": LeanSkillDefinition(
        name="decl-owned-lean-file-capture-check",
        description="Work safely with Lean Constellation declaration-owned Lean files, manual Lean checks, and capture/check tools.",
        group="lean",
        required_tool_groups=("decl_stage_statement_formal_file", "decl_stage_proof_formal_file", "formal_diagnostics_read"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "decl-owned-lean-file-capture-check",
            "Use this skill when a formal worker edits a tracked declaration file or when a reviewer needs to understand formal capture semantics.",
            (
                "Stay inside the assigned declaration-owned file.",
                "Keep system markers and prepared structure intact.",
                "Use manual Lean checks for debugging but do not treat them as durable acceptance.",
                "Capture formal code through workflow tools after editing.",
                "Respect safety policy before worker submit.",
            ),
            (
                "Do not rely on uncaptured edits as accepted formal content.",
                "Do not use sorry, admit, axiom, or equivalent shortcuts in completed work.",
            ),
        ),
    ),
    "lean-statement-formalization": LeanSkillDefinition(
        name="lean-statement-formalization",
        description="Formalize an accepted natural-language declaration statement into a Lean declaration.",
        group="lean",
        required_tool_groups=("decl_stage_statement_formal_file", "formal_diagnostics_read"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "lean-statement-formalization",
            "Use this skill for Statement Formal workers after statement NL has been accepted.",
            (
                "Start from the accepted statement and declared objective.",
                "Map variables, assumptions, definitions, and conclusions to Lean deliberately.",
                "Search dependencies in visible project context and Mathlib before adding imports or hints.",
                "Edit the declaration-owned file and run diagnostics.",
                "Capture, check, and refine dependencies before completed submit.",
            ),
            (
                "Do not silently change statement meaning to make Lean easier.",
                "Do not complete theorem proofs in the statement formalization stage.",
            ),
        ),
    ),
    "lean-proof-formalization": LeanSkillDefinition(
        name="lean-proof-formalization",
        description="Formalize a reviewed natural-language proof route into a Lean proof while preserving the accepted formal statement.",
        group="lean",
        required_tool_groups=("decl_stage_proof_formal_file", "formal_diagnostics_read"),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "lean-proof-formalization",
            "Use this skill for Proof Formal workers after proof NL has been accepted.",
            (
                "Start from the frozen statement and reviewed proof route.",
                "Search formal dependencies in visible project context and Mathlib.",
                "Edit the proof body without changing the accepted statement.",
                "Use Lean checks iteratively and capture at the durable boundary.",
                "Restore or block when the working file is damaged or the route needs planning changes.",
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
