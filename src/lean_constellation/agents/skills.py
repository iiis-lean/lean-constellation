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


_COORDINATOR_PROVED_FULL_GRAPH_MODE_BODY = """# coordinator-proved-full-graph-mode

Use this skill when the current repo work mode is proved_full_graph.

The goal is a complete proof-oriented native repository. Design the node tree so theorem-like public outputs and proof-relevant internal lemmas can eventually become proved-level proof-policy satisfied.

Coordinator responsibilities:

1. Build a node tree that covers definitions, statements, intermediate lemmas, proof helper areas, and final public theorems.
2. Use scope nodes for broad mathematical regions and content nodes for coherent declaration/proof work.
3. Assign content node contracts with explicit public interfaces, required declarations, source/material references, visible dependencies, and expected proof completion.
4. Plan exports for lemmas or theorems that must be reused across sibling nodes.
5. Dispatch content node tasks only when their boundaries and visible dependencies are clear enough for node-local work.
6. After callbacks, use current repo truth and completion gates to decide whether to update contracts, create follow-up nodes, dispatch more content tasks, request providers/resources, or submit repo ready.

Non-theorem-like foundations such as definitions, structures, predicates, namespaces, notation, and theorem statement prerequisites must be declared/formalized when they are needed by public interfaces or theorem statements. Do not hide proof work inside scope nodes or oversized content nodes.
"""


_COORDINATOR_DECLARED_FULL_GRAPH_MODE_BODY = """# coordinator-declared-full-graph-mode

Use this skill when the current repo work mode is declared_full_graph.

The goal is a full declaration skeleton for the mathematical source, not a completed proof graph. The node tree should be close to the future proved_full_graph structure, but theorem-like declarations may stop at declared statements.

Coordinator responsibilities:

1. Design the scope/content node tree at roughly the same structural granularity expected for a later proved repo.
2. Include important definitions, theorem statements, lemma statements, and intermediate mathematical interfaces that appear in the source material.
3. If the source material omits intermediate lemmas but the future proof structure strongly suggests them, plan plausible declaration skeletons when they are useful for future proved work.
4. Assign content node contracts that ask for statement-level declaration work and public interfaces, not formal proof completion unless the repo config changes.
5. Keep public/export boundaries compatible with a future upgrade to proved_full_graph.

This mode is not a minimal provider shortcut. It should preserve the mathematical structure needed for later proof-oriented work.
"""


_COORDINATOR_DECLARED_INTERFACE_MODE_BODY = """# coordinator-declared-interface-mode

Use this skill when the current repo work mode is declared_interface.

The goal is the smallest stable provider boundary that can satisfy external consumers. Create only the node tree needed to expose the required public interface, while keeping the structure compatible with future expansion into a full proved graph.

Coordinator responsibilities:

1. Identify the public definitions and theorem-like statements that the provider must expose to consumers.
2. Create content nodes for foundational non-theorem-like definitions required by those public statements.
3. Create content nodes for the required public theorem statements.
4. Create only the auxiliary definition nodes needed to make the public statements meaningful and compilable.
5. Avoid creating proof-only internal lemma nodes in the initial declared-interface pass.
6. Use source proof structure to choose scope boundaries that leave room for later proof expansion.

The resulting tree should be a stable subtree or skeleton of the future proved_full_graph, not a dead-end shortcut.
"""


_CONTENT_PLAN_PROVED_FULL_GRAPH_MODE_BODY = """# content-plan-proved-full-graph-mode

Use this skill when the current repo work mode is proved_full_graph.

The goal is a complete proof-oriented content node. Public or contract-required theorem-like declarations should eventually become proved-level proof-policy satisfied. Non-theorem-like foundations should become declared-level satisfied.

Use bottom-up, top-down, or mixed strategy according to the mathematical structure.

Bottom-up strategy:

1. Create or update needed foundations with `end_after_state=declared` and `require_target_state_satisfied=true`.
2. Prove low-level helper lemmas with `end_after_state=proved` and `require_target_state_satisfied=true` when they can be proved from already satisfied dependencies.
3. Continue upward through intermediate lemmas and final theorem-like outputs.

Top-down strategy:

1. Create the top theorem statement with `end_after_state=declared` and usually `require_target_state_satisfied=true`.
2. Create helper lemma statements with `end_after_state=declared` and usually `require_target_state_satisfied=true`.
3. If the top theorem can be proved using helper lemmas whose proofs are not yet satisfied, update the top theorem with `end_after_state=proved` and `require_target_state_satisfied=false`.
4. Prove the helper lemmas. If a helper still needs smaller helper lemmas, recursively apply the same pattern until the dependency closure is satisfied.

Keep require_target_state_satisfied=true by default. Use false only for explicit top-down intermediate proof changes, and make the follow-up dependency-closure plan explicit in the strategy, round objective, or summary.
"""


_CONTENT_PLAN_DECLARED_FULL_GRAPH_MODE_BODY = """# content-plan-declared-full-graph-mode

Use this skill when the current repo work mode is declared_full_graph.

The goal is a full declaration skeleton for the content node, not completed formal proofs. Include important definitions, theorem statements, lemma statements, and intermediate mathematical declarations from the source material, while theorem-like declarations may stop at declared-level satisfaction.

Bottom-up skeleton strategy:

1. Create foundations first: definitions, structures, predicates, notation, and statement prerequisites.
2. Then create low-level lemma statements.
3. Then create intermediate theorem or lemma statements.
4. Finally create the public or contract-required theorem statements.
5. Each change should normally target declared-level satisfaction.

Top-down skeleton strategy:

1. Create the top theorem statements first with `end_after_state=declared` and `require_target_state_satisfied=true`.
2. Then create helper lemma statements expected to support future proofs, also with `end_after_state=declared` and `require_target_state_satisfied=true`.
3. Recursively add lower-level helper statements when they clarify the future proof structure.

Do not add proof formal stages merely to link these statements. Future proof relationships belong in strategy rationale, round objective, or summary text unless a real proof artifact is created.
"""


_CONTENT_PLAN_DECLARED_INTERFACE_MODE_BODY = """# content-plan-declared-interface-mode

Use this skill when the current repo work mode is declared_interface.

The goal is the smallest useful declared interface for this content node. Expose the public declarations required by its contract and external consumers while avoiding proof-only internal lemma work in the initial pass.

Typical round order:

1. Create or update foundational non-theorem-like declarations with `end_after_state=declared` and `require_target_state_satisfied=true`.
2. Create public theorem-like statements with `end_after_state=declared` and `require_target_state_satisfied=true`.
3. Add only extra declarations required for those public statements to compile and be semantically meaningful.

Do not create proof-only hidden helper lemmas unless they are necessary to state the public interface. Do not enter proof NL or proof formal stages for theorem-like declarations under this mode.

The node can complete when contract-required public declarations reach declared-level satisfaction. A future proved pass may expand this node or neighboring nodes with internal helper lemmas and proofs.
"""


SKILL_DEFINITIONS: dict[str, LeanSkillDefinition] = {
    SkillKey.NODE_CONTRACT_DESIGN.value: LeanSkillDefinition(
        name="node-contract-design",
        description="Designs and updates Lean Constellation scope and content node contracts.",
        group="node",
        required_tool_groups=_groups(
            AppGroup.NODE_CONTRACT_READ_COORDINATOR,
            AppGroup.NODE_CONTRACT_CORE_COORDINATOR_WRITE,
            AppGroup.NODE_CONTRACT_DEPENDENCY_COORDINATOR_WRITE,
            AppGroup.NODE_CONTRACT_MATERIAL_COORDINATOR_WRITE,
            AppGroup.NODE_CONTRACT_MATHLIB_COORDINATOR_WRITE,
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
                "Attach durable source or resource context to a target node with `add_node_material_ref` or remove stale entries with `remove_node_material_ref`.",
                "Record visible same-repo or provider node dependencies with `add_node_dep`, and remove stale dependency entries with `remove_node_dep`.",
                "Record target-node Mathlib module or declaration hints with `add_node_mathlib_module_hint` and `add_node_mathlib_decl_hint` after the candidates are verified or recorded in the repo MathlibIndex.",
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
                "After a child-flow callback or reviewer callback, re-read the current contract and relevant state before deciding the next action.",
                "Separate owned material from context material.",
                "Use current-node dependency reads, material refs, and role-appropriate hint tools as working context when those tools are visible.",
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
            AppGroup.NODE_VISIBILITY_READ_CURRENT,
            AppGroup.PUBLIC_DECL_READ,
            AppGroup.NODE_CONTRACT_DEPENDENCY_CURRENT_WRITE,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "visible-node-dependency-recon",
            "Use this skill when inspecting ready same-repository boundaries or already attached provider boundaries and adding current-node dependencies when justified.",
            (
                "Start from the current contract and objective with `get_current_node_contract`.",
                "Use `list_visible_nodes` and `list_imported_repos` to find visible dependency sources.",
                "Inspect public declarations selectively with node/repo public declaration tools before recording a visible dependency.",
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
            "Use this skill when closing a scope node, selecting public declarations from ready children, binding scope interfaces, checking projection/readiness, or preparing a scope contract commit.",
            (
                "Read required interfaces and current export candidates with `list_node_interfaces`, `list_scope_export_candidates`, and `list_scope_exports`.",
                "Choose exports that belong to the scope public view and write them with `add_scope_export` or `remove_scope_export`.",
                "Bind interfaces only to declarations that satisfy their meaning with `bind_node_interface`.",
                "When your role has scope-close read tools, use the available scope close/readiness view to confirm exports, interface bindings, child readiness, and projection/readiness checks are stable before commit.",
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
        required_tool_groups=_groups(AppGroup.EXTERNAL_RESOURCE_DISCOVERY),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "external-resource-discovery",
            "Use this skill when existing source and resource material is insufficient and the agent must find a precise arXiv, web, or local-file target without broad duplicate searching.",
            (
                "Use external discovery only after the current source, resources, visible dependencies, and Mathlib context are not enough for the task.",
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
                "After a local resource result, decide whether your current role should attach it through visible node material or contract tools, or report it for the role that owns that attachment.",
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
            AppGroup.SOURCE_CORPUS_READ,
            AppGroup.SOURCE_INDEX_COMMITTED_READ,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "coordinator-node-decomposition",
            "Use this skill when deciding whether to create or revise a Scope node or Content node, splitting mathematical regions, assigning sibling boundaries, or repairing an over-broad or over-fragmented node structure.",
            (
                "Start from repo work config, preparation input, root interfaces, SourceCorpus, committed SourceIndex, current tree from `get_node_tree`, and stable provider context from `list_ready_provider_repos`.",
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
        required_tool_groups=_groups(
            AppGroup.SCOPE_EXPORT_INTERFACE_READ,
            AppGroup.SCOPE_EXPORT_INTERFACE_WRITE,
            AppGroup.SCOPE_CONTRACT_COORDINATOR_COMMIT,
            AppGroup.SCOPE_CLOSE_READ,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "coordinator-scope-lifecycle",
            "Use this skill when creating or updating a scope contract, analyzing child nodes, curating exports and interface bindings, committing the scope contract, and deciding follow-up work.",
            (
                "Create or update the scope contract before child work depends on it.",
                "Plan required public interfaces and child responsibilities.",
                "Analyze children with `get_scope_close_view` when they become ready.",
                "Use `list_scope_export_candidates`, `add_scope_export`, and `bind_node_interface` for export and binding details.",
                "Call `commit_scope_contract` only after readiness and projection checks pass through the available scope tools.",
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
        required_tool_groups=_groups(
            SubmitGroup.COORDINATOR_SUBMIT,
            AppGroup.CONTENT_TASK_ADMISSION_READ,
            AppGroup.NODE_CONTRACT_READ_COORDINATOR,
            AppGroup.CONTENT_TASK_RESULT_COORDINATOR_FINALIZE,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "coordinator-content-task-lifecycle",
            "Use this skill when preparing content node contracts before dispatch, submitting runnable content tasks, processing callbacks, committing summaries, and deciding follow-up actions.",
            (
                "Check that candidate content nodes are runnable with `check_content_task_admission` before dispatch.",
                "Prepare each task contract with current objective and constraints.",
                "Submit runnable tasks through `submit_content_node_tasks`.",
                "After callbacks, read terminal task results with `list_recent_content_task_results` or `inspect_content_task_result`.",
                "Call `commit_content_contract` for each reviewed ready, blocked, or failed Content task result before planning follow-up work.",
                "Decide whether to update contracts, create follow-up tasks, call `submit_resource_request`, call `submit_repo_requirement`, or close scopes.",
            ),
            (
                "Do not dispatch tasks whose dependencies are not visible or whose boundaries are unclear.",
                "Do not handle content-node internal worker stages from the Coordinator role.",
            ),
        ),
    ),
    SkillKey.COORDINATOR_PROVED_FULL_GRAPH_MODE.value: LeanSkillDefinition(
        name="coordinator-proved-full-graph-mode",
        description="Guides Coordinator node-tree planning for proved full graph repositories.",
        group="coordinator",
        source_design_doc="dev_docs/design/repo_maturity_modes/04_Coordinator工作模式设计.md",
        body=_COORDINATOR_PROVED_FULL_GRAPH_MODE_BODY,
    ),
    SkillKey.COORDINATOR_DECLARED_FULL_GRAPH_MODE.value: LeanSkillDefinition(
        name="coordinator-declared-full-graph-mode",
        description="Guides Coordinator node-tree planning for declared full graph repositories.",
        group="coordinator",
        source_design_doc="dev_docs/design/repo_maturity_modes/04_Coordinator工作模式设计.md",
        body=_COORDINATOR_DECLARED_FULL_GRAPH_MODE_BODY,
    ),
    SkillKey.COORDINATOR_DECLARED_INTERFACE_MODE.value: LeanSkillDefinition(
        name="coordinator-declared-interface-mode",
        description="Guides Coordinator node-tree planning for declared interface repositories.",
        group="coordinator",
        source_design_doc="dev_docs/design/repo_maturity_modes/04_Coordinator工作模式设计.md",
        body=_COORDINATOR_DECLARED_INTERFACE_MODE_BODY,
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
                "Read node-local Mathlib hints first when current-node hint tools are visible.",
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
                "Use `search_mathlib_declarations` for mathematical concepts before considering any additional toolkit-backed search backend that is visible to your role.",
                "Inspect candidate declarations and modules with `inspect_mathlib_search_candidate`, `inspect_mathlib_declaration`, and `inspect_mathlib_module` before relying on them.",
                "Confirm namespace, assumptions, typeclasses, imports, and theorem direction.",
                "When your role has MathlibIndex write permissions, record verified candidates through the dedicated MathlibIndex curation workflow rather than treating search results as committed truth.",
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
        body="""# Content Preparation Orchestration

## Purpose

Use this skill when the current content node task may need preparation before entering or continuing DeclGraph work.

Preparation recon is delegated work. Your job is to decide whether a child flow is needed, give it a focused objective, and interpret its callback result. Do not do broad dependency, Mathlib, or resource recon inside the ContentPlanAgent context when a dedicated child flow should do it.

## Recommended Order

For a first task on a content node, consider preparation in this order:

1. Visible node dependency recon.
2. Mathlib recon.
3. Resource recon.

For a follow-up task, use the same order as a decision checklist, but each step is optional. Skip a preparation kind when current truth already gives enough context for strategy or round planning.

## Child Flow Submit

Call `submit_content_preparation_recon` only when handing off to a preparation child flow. Provide:

- `objective`: the concrete question or gap for that specific recon flow.
- context summary: short background about current progress, previous callback results, or why this recon is needed now.

Do not pass full contract JSON, full DeclGraph state, complete source text, or large previous results. The child flow can query detailed state through its own tools.

If `submit_content_preparation_recon` is accepted, stop. The runtime will run the child flow and callback this planning agent when it completes.

## One Run Per Kind

In one content node task, dispatch each preparation kind at most once:

- node directory dependency recon;
- Mathlib recon;
- resource recon.

If a kind has already run, interpret the callback result, perform only targeted current-node corrections when justified, and then continue planning.

## After Callback

After a preparation child flow returns:

1. Re-read current node truth with `get_current_node_contract`.
2. Re-read relevant DeclGraph, dependency, Mathlib, source, or resource state through available tools.
3. Decide whether the result is sufficient.
4. If only a small correction is needed, use current-node scoped mutation tools.
5. Do not rerun the same preparation kind in the same task.
6. Continue the preparation checklist, start strategy planning, or complete/block/fail the task.

## Boundaries

- Do not dispatch preparation flows just to gather vague context.
- Do not ask one preparation flow to perform a different preparation kind.
- Do not place full contracts or graph dumps into child prompts.
- Do not use current-node correction tools as a substitute for broad child-flow recon.
""",
    ),
    SkillKey.CONTENT_PLAN_PROVED_FULL_GRAPH_MODE.value: LeanSkillDefinition(
        name="content-plan-proved-full-graph-mode",
        description="Guides ContentPlan round strategy for proved full graph content nodes.",
        group="content_plan",
        source_design_doc="dev_docs/design/repo_maturity_modes/05_ContentPlanStrategy与Round模式设计.md",
        body=_CONTENT_PLAN_PROVED_FULL_GRAPH_MODE_BODY,
    ),
    SkillKey.CONTENT_PLAN_DECLARED_FULL_GRAPH_MODE.value: LeanSkillDefinition(
        name="content-plan-declared-full-graph-mode",
        description="Guides ContentPlan round strategy for declared full graph content nodes.",
        group="content_plan",
        source_design_doc="dev_docs/design/repo_maturity_modes/05_ContentPlanStrategy与Round模式设计.md",
        body=_CONTENT_PLAN_DECLARED_FULL_GRAPH_MODE_BODY,
    ),
    SkillKey.CONTENT_PLAN_DECLARED_INTERFACE_MODE.value: LeanSkillDefinition(
        name="content-plan-declared-interface-mode",
        description="Guides ContentPlan round strategy for declared interface content nodes.",
        group="content_plan",
        source_design_doc="dev_docs/design/repo_maturity_modes/05_ContentPlanStrategy与Round模式设计.md",
        body=_CONTENT_PLAN_DECLARED_INTERFACE_MODE_BODY,
    ),
    SkillKey.DECL_STRATEGY_PLANNING.value: LeanSkillDefinition(
        name="decl-strategy-planning",
        description="Use when the ContentPlanAgent creates, continues, closes, or replaces a DeclGraph strategy.",
        group="content_plan",
        required_tool_groups=_groups(AppGroup.DECL_GRAPH_READ_CURRENT, AppGroup.DECL_STRATEGY_WRITE),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Decl Strategy Planning

## Purpose

Use this skill before preparing a new DeclGraph round, after a preparation callback, or after a previous round has completed, blocked, or failed.

A strategy is a high-level route for making progress inside the current content node. It may target the whole node, one required interface, a proof decomposition, a bottom-up foundation segment, or an intermediate theorem. It is not itself a statement, proof, or Lean artifact.

## Read Current Truth First

Before creating or changing a strategy, read current state with:

- `get_current_node_contract` for the current task boundary and objective;
- `get_current_repo_work_config` for target_proof_availability and work_mode;
- DeclGraph read tools for graph state, active declarations, and round history;
- strategy read tools for existing strategy state.

Select and follow the ContentPlan mode skill matching the current work_mode. The strategy should say whether it is bottom-up, top-down, mixed, declared-skeleton, or declared-interface oriented.

Do not rely on conversation memory when tools can show current truth.

## What To Analyze

Analyze:

- the current node goal, boundary, objective, materials, dependencies, and interfaces;
- which required interfaces or useful public declarations are not proof-policy satisfied;
- which previous rounds succeeded, blocked, failed, or changed the graph;
- whether the current route should be bottom-up, top-down, helper-decomposition based, declared skeleton, declared interface, or a small repair route;
- whether missing support should first be handled through node dependencies, Mathlib, resources, or Coordinator escalation.

## Creating Or Continuing A Strategy

Use `ensure_open_decl_strategy` when no viable open strategy exists or when the current route needs to be made explicit. The strategy objective should name the mathematical route, not just say continue work.

Continue an open strategy only if it still explains the next useful round. If the strategy remains valid after a blocked or failed round, close out that round first, repair prerequisites if possible, and then continue under the same strategy.

## Closing Or Replacing A Strategy

Use `close_decl_strategy` when:

- the strategy achieved its intended graph state;
- the route is no longer viable;
- a better strategy supersedes it;
- the content node is complete, blocked, or failed.

Rounds belonging to the strategy should be summarized and committed before closing the strategy.

## Targeted Supplement

During strategy planning, you may do targeted supplement to answer a concrete planning question:

- inspect visible boundaries and public declarations;
- inspect current node dependencies and material refs;
- search the repo MathlibIndex;
- use Mathlib semantic search or navigation for a narrow concept;
- inspect source or resource material;
- request one explicit resource target when necessary.

Do not turn strategy planning into broad recon when a preparation child flow should do that work.

## Boundaries

- Do not write statement or proof artifacts.
- Do not edit Lean files.
- Do not bind scope exports.
- Do not create repository requirements.
- Do not keep an obsolete strategy open after it has failed or been replaced.
- Do not encode vague aspirations as actionable strategy.
""",
    ),
    SkillKey.DECL_ROUND_CHANGE_PLANNING.value: LeanSkillDefinition(
        name="decl-round-change-planning",
        description="Use when the ContentPlanAgent prepares create, update, or delete changes for the next DeclGraph round.",
        group="content_plan",
        required_tool_groups=_groups(AppGroup.DECL_ROUND_CHANGE_WRITE, SubmitGroup.CONTENT_PLAN_SUBMIT),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Decl Round Change Planning

## Purpose

Use this skill when an open strategy exists and you need to prepare the next DeclGraphRoundFlow.

Prepare a round by editing graph truth with small tools. Do not submit one large nested round object, and do not ask workers to infer hidden graph changes from prose.

## Round Setup

Before planning changes:

1. Re-read the current node contract with `get_current_node_contract`.
2. Re-read graph, round history, and strategy state with the available DeclGraph read tools.
3. Ensure the next batch is small, coherent, and aligned with the open strategy.
4. Create or reuse the draft round with `create_decl_round_draft`.

## Create Changes

Use `plan_create_decl` for new declarations. Each create change should have:

- a clear declaration name and kind;
- visibility appropriate for the node contract;
- a concise mathematical objective;
- end_after_state;
- require_target_state_satisfied.

Helper lemmas that matter to later work should be tracked as their own declarations. Do not hide important helper lemmas as untracked local Lean code.

## Update Changes

Use `plan_update_decl` when an existing declaration needs a targeted repair or stage advancement. Say which part should be repaired or advanced, such as statement meaning, formal statement, proof idea, proof dependencies, or formal proof. Include start_before_state when the update depends on the current state, plus the intended end_after_state and require_target_state_satisfied.

Do not use an update change to silently change a previously accepted mathematical meaning.

## Target Satisfaction

Use `end_after_state=declared` when the round should produce or repair the statement layer. Use `end_after_state=proved` when the round should produce or repair the proof layer for a theorem-like declaration.

Keep `require_target_state_satisfied=true` unless the selected mode skill explicitly justifies a state-only intermediate change. A state-only intermediate change must be followed by later changes that make its dependency closure proof-policy satisfied.

## Delete Changes

Before deleting a declaration, call `preview_decl_delete_closure`. Use `plan_delete_decl` only when the closure is acceptable and the deletion is part of the current strategy.

## Validation And Submit

Call `validate_decl_round_draft` before submitting. If validation rejects the draft, fix the draft or choose a different next action.

Call `submit_current_decl_round` only when the draft is valid and ready for DeclGraphRoundFlow execution. After an accepted submit, stop.

## Boundaries

- Do not choose ready as a planned declaration state.
- Do not write statement text, formal statements, proof text, or Lean proof code yourself.
- Do not bypass reviewer stages by encoding accepted artifacts in the round plan.
- Do not submit a broad unfocused batch when a smaller independent batch is available.
""",
    ),
    SkillKey.DECL_ROUND_CLOSEOUT.value: LeanSkillDefinition(
        name="decl-round-closeout",
        description="Use when the ContentPlanAgent receives a DeclGraphRoundFlow callback and must summarize and commit the round.",
        group="content_plan",
        required_tool_groups=_groups(AppGroup.DECL_ROUND_CLOSEOUT_WRITE, AppGroup.DECL_GRAPH_READ_CURRENT),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Decl Round Closeout

## Purpose

Use this skill after a DeclGraphRoundFlow terminal callback and before planning the next action.

Round closeout records what happened. It is a synchronous state update sequence, not a submit action. After closeout, re-read truth and continue planning unless a submit action is the next justified step.

## Required Order

1. Read the terminal callback result.
2. Re-read the current round, changed declarations, and relevant graph state.
3. Write one summary per changed declaration with `write_decl_change_summary`.
4. Write the round summary with `write_decl_round_summary`.
5. Commit terminal closeout with `mark_decl_round_terminal`.
6. Re-read current truth.
7. Decide whether to plan another round, run preparation, check content completion, report blocked, or fail.

Do not start a new round before closeout is recorded.

## Change Summaries

Use `write_decl_change_summary` to record what happened to each declaration in the round. Mention the intended change, the terminal outcome, accepted state changes, and concrete blocker or failure details when relevant.

Do not hide an important blocked cause inside a generic success or failure summary.

## Round Summary

Use `write_decl_round_summary` to summarize the whole round. The round summary should explain:

- whether the strategy made progress;
- which declarations reached their target state;
- which declarations became proof-policy satisfied under the current repo target;
- which declarations still need work;
- whether any intentionally state-only intermediate change still needs dependency closure work;
- whether the next step is another round, preparation, completion check, blocked, or failed.

## Terminal Commit

Use `mark_decl_round_terminal` only after the change summaries and round summary are written. After marking terminal, read current truth again before any new planning action.

## Boundaries

- Do not start a new round before closeout is recorded.
- Do not change statement or proof artifacts during closeout.
- Do not use closeout tools as a substitute for worker or reviewer results.
- Do not hide blocked causes in a generic summary.
""",
    ),
    SkillKey.CONTENT_NODE_COMPLETION_DECISION.value: LeanSkillDefinition(
        name="content-node-completion-decision",
        description="Use when the ContentPlanAgent decides whether the current content node task should end as ready, blocked, or failed.",
        group="content_plan",
        required_tool_groups=_groups(SubmitGroup.CONTENT_COMPLETION_SUBMIT, AppGroup.CONTENT_COMPLETION_GATE_READ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Content Node Completion Decision

## Purpose

Use this skill when deciding whether the current content node task should end as ready, blocked, or failed.

A natural-language claim is not enough. Use the content completion gate and submit tools to complete the task through the workflow.

## Ready

Before ready, call `check_current_content_node_completion`. Use the returned gate report as the authority for whether the current node satisfies its contract, proof-policy requirements, dependencies, interfaces, and unresolved callback requirements.

Call `submit_content_node_ready` only when the current tools show the node satisfies its contract. After an accepted ready submit, stop.

If `check_current_content_node_completion` rejects readiness, do not force ready. Fix issues within your authority, run another round, dispatch allowed preparation, or choose blocked/failed when appropriate.

## Blocked

Call `submit_content_node_blocked` when the current task cannot responsibly continue because it needs action outside the ContentPlanAgent authority. Typical blocked reasons include:

- Coordinator must revise the node boundary, objective, interfaces, or node tree;
- an external provider repository is required;
- source or resource material is missing and cannot be acquired from this task;
- a proof route requires higher-level decomposition;
- preparation found a prerequisite that this content node cannot create.

State the concrete blocker, what you checked, why it is necessary, and what action would unblock the task. After an accepted blocked submit, stop.

## Failed

Call `submit_content_node_failed` only when the current automated route is exhausted and the reason is not an external prerequisite or Coordinator action. A failed result should explain the route that was tried, why it cannot continue, and why blocked is not the correct outcome.

After an accepted failed submit, stop.

## Boundaries

- Do not mark ready based only on the PlanAgent's narrative.
- Do not use failed for missing external prerequisites that should be blocked.
- Do not continue state-changing work after an accepted terminal submit.
""",
    ),
    SkillKey.DECL_DEPENDENCY_ORIGIN_CURATION.value: LeanSkillDefinition(
        name="decl-dependency-origin-curation",
        description="Curate source/resource origins and declaration dependencies for Lean Constellation declaration stage artifacts.",
        group="decl_stage",
        required_tool_groups=_groups(AppGroup.CURRENT_NODE_DECL_READ, AppGroup.PUBLIC_DECL_READ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "decl-dependency-origin-curation",
            "Use this skill when a declaration worker or reviewer must connect a statement or proof idea to evidence, visible project declarations, Mathlib declarations, and blocked follow-up needs.",
            (
                "Start from the declaration task and stage objective with `inspect_current_node_decl`.",
                "Search source, resources, visible public declarations, and Mathlib with the available material, public declaration, and Mathlib index tools in the right order.",
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
