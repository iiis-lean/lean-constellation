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
    """Write selected skills as provider-neutral skill directories."""

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
    del definition
    return {}


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


_COORDINATOR_COMPLETION_POLICY_BODY = """# coordinator-completion-policy

Read `get_current_repo_completion_policy` and apply its completion mode to the
source and interface scope named by the current run:

- interface_declared: build the smallest stable node subtree needed to expose
  the required public interfaces and their declaration prerequisites. Do not
  create proof-only helper nodes.
- graph_declared: represent the selected source scope as a complete
  declaration graph, including important definitions, theorem statements, and
  intermediate lemma statements. Proof completion is not required.
- graph_proved: represent the selected source scope as a complete proof graph.
  Theorem-like outputs and proof-relevant helpers must eventually be proved;
  non-theorem foundations must be declared.

The run objective and source/interface contracts define *what* is in scope;
completion mode defines *how far* that scope must be completed. Preserve every
released state floor and release-protected statement. Use scope nodes for broad
mathematical regions, content nodes for coherent declaration work, explicit
exports for sibling reuse, and contracts that state the completion expected for
their declarations.
"""


_CONTENT_PLAN_COMPLETION_POLICY_BODY = """# content-plan-completion-policy

Read `get_current_repo_completion_policy`, then plan rounds for the current
contract and source ownership:

- interface_declared: declare the contract-required public interfaces and
  only the foundations needed to state and compile them. Do not plan proof-only
  hidden helpers.
- graph_declared: declare every important definition, theorem statement, and
  intermediate lemma statement in the node's selected source scope. Stop
  theorem-like declarations at declared state unless existing release truth is
  already stronger.
- graph_proved: build bottom-up by default. Declare foundations, prove
  reusable helpers once their dependencies are ready, and then prove public or
  contract-required theorem-like outputs.

The contract and source references determine which mathematical material this
node owns. Completion mode determines the required state of that material.
Never lower a released state floor or rewrite a release-protected statement.
Before targeting `proved`, verify source proof stages and accepted dependency
closure; do not turn missing helpers into repeated unbounded parent retries.
"""


SKILL_DEFINITIONS: dict[str, LeanSkillDefinition] = {
    SkillKey.NODE_CONTRACT_DESIGN.value: LeanSkillDefinition(
        name="node-contract-design",
        description="Use when a Coordinator must design or update the semantic contract of a Scope or Content node.",
        group="node",
        required_tool_groups=_groups(
            AppGroup.NODE_CONTRACT_READ_BY_NODE,
            AppGroup.NODE_CONTRACT_TEXT_WRITE_BY_NODE,
            AppGroup.NODE_CONTRACT_DEPENDENCY_WRITE_BY_NODE,
            AppGroup.NODE_CONTRACT_MATERIAL_WRITE_BY_NODE,
            AppGroup.SOURCE_MATERIAL_TEXT_READ,
            AppGroup.NODE_CONTRACT_MATHLIB_WRITE_BY_NODE,
            AppGroup.NODE_TREE_WRITE,
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
                "Record expected important declarations, major SourceIndex/source stages, and a rough Lean declaration range in the existing goal/boundary/objective text; this is guidance, not a hard count gate.",
                "Use `create_scope_node`, `create_content_node`, and `update_node_contract_text` for durable contract changes.",
                "Before adding or changing a source ref, call `validate_source_range` and `preview_source_ref`, read the excerpt, and confirm that it supports the ref reason, target interface, and node boundary; a structurally valid range is not semantic evidence.",
                "Treat SourceCorpus locators such as `article/sections/...` as semantic-tool identities, not paths relative to the current workdir.",
                "Attach durable source or resource context to a target node with `add_node_material_ref` or remove stale entries with `remove_node_material_ref`.",
                "Record visible same-repo or provider node dependencies with `add_node_dep`, and remove stale dependency entries with `remove_node_dep`.",
                "Record target-node Mathlib module or declaration hints with `add_node_mathlib_module_hint` and `add_node_mathlib_decl_hint` after the candidates are verified or recorded in the repo MathlibIndex.",
                "Prepare content tasks with enough objective, material, dependency, and interface context for node-local work.",
                "For an interface intended to satisfy an existing consumer, write a statement hint that identifies the consumer declaration or revision, expected input/output shape, lower public declaration refs, assumptions or indices that must not be strengthened or replaced, conclusion direction that must not be weakened, relevant Source stage, and a minimal consumer-side Lean snippet when useful.",
                "Treat a statement hint as semantic guidance rather than an exact Lean header: the ContentPlan may choose a natural public declaration decomposition, but it must preserve the required capability.",
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
                "Treat every interface statement hint as contract guidance. Preserve its consumer-side objects, binders, assumptions, index representation, and conclusion direction; a Lean snippet describes the required semantic shape but is not an exact header to copy blindly.",
                "Do not strengthen a hinted requirement with assumptions the consumer cannot provide, weaken its conclusion, substitute a different object or index merely because it is easier to prove, or infer a special node category from a path, historical use, or name.",
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
            AppGroup.CURRENT_NODE_PUBLIC_DECL_READ,
            AppGroup.VISIBLE_NODE_PUBLIC_DECL_READ,
            AppGroup.IMPORTED_REPO_PUBLIC_DECL_READ,
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
                "When a dependency is justified by specific provider public declarations, put those names in expected_public_decl_names so the dependency carries structured evidence.",
                "Use `remove_current_node_dep` conservatively, only for worker-owned dependencies that are clearly stale, wrong, or outside the current objective.",
                "Report unclear cases through unresolved_within_visible_boundaries without inventing unavailable dependencies or deleting uncertain dependencies.",
            ),
            (
                "Do not use non-ready private declarations.",
                "Do not create repository requirements or resource requests from this skill.",
            ),
        ),
    ),
    SkillKey.SCOPE_EXPORT_INTERFACE_CURATION.value: LeanSkillDefinition(
        name="scope-export-interface-curation",
        description="Use inside Scope lifecycle work when concrete public exports or interface bindings must be curated.",
        group="node",
        required_tool_groups=_groups(
            AppGroup.SCOPE_EXPORT_INTERFACE_READ,
            AppGroup.SCOPE_EXPORT_INTERFACE_WRITE,
            AppGroup.SCOPE_CLOSE_READ,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "scope-export-interface-curation",
            "Use this skill when closing a scope node, selecting public declarations from ready children, binding scope interfaces, checking projection/readiness, or preparing a scope contract commit.",
            (
                "Read required interfaces and current export candidates with `list_node_interfaces`, `list_scope_export_candidates`, and `list_scope_exports`.",
                "Preserve historical public export chains as compatible anchors; append new exports without silently replacing a released boundary.",
                "Choose exports that belong to the scope public view and write them with `add_scope_export` or `remove_scope_export`.",
                "Bind interfaces only to declarations that satisfy their meaning with `bind_node_interface`.",
                "Use `get_scope_close_view` to confirm exports, interface bindings, child readiness, and projection/readiness checks are stable before commit.",
            ),
            (
                "Do not export unstable private implementation details.",
                "Do not use exports to hide an unsatisfied interface.",
            ),
        ),
    ),
    SkillKey.SOURCE_MATERIAL_ACQUISITION.value: LeanSkillDefinition(
        name="source-material-acquisition",
        description="Guides SourceCorpusPrepare agents using source acquisition and extraction tools.",
        group="material",
        required_tool_groups=_groups(AppGroup.SOURCE_ACQUISITION),
        source_design_doc="dev_docs/design/agents/skill_bundles/04_SourceCorpusPrepareSkills.md",
        body=_body(
            "source-material-acquisition",
            "Use this skill before organizing fetched artifacts into the repository source corpus.",
            (
                "Acquire source targets with `acquire_source_material` or import local source files with `import_source_material`.",
                "Extract readable text or project files with `extract_source_artifact` when the acquired artifact is a PDF, HTML page, TeX archive, or similar container.",
                "Normalize readable text with `normalize_source_text_material` before treating it as durable source corpus text.",
                "Treat acquisition and extraction outputs as intermediate material; organize useful content into the current source corpus root yourself.",
                "Keep original material and normalized text separate, and explain extraction limits in the source corpus README.",
            ),
            (
                "Do not register ResourceLibrary entries from source preparation.",
                "Do not write SourceIndex, NodeContract, or Lean files.",
                "Do not rely on raw PDF, HTML, or image-only material as the only prepared source corpus content.",
            ),
        ),
    ),
    SkillKey.RESOURCE_MATERIAL_ACQUISITION.value: LeanSkillDefinition(
        name="resource-material-acquisition",
        description="Guides ResourceCurator agents using resource acquisition tools inside an active resource draft.",
        group="resource",
        required_tool_groups=_groups(AppGroup.RESOURCE_ACQUISITION),
        source_design_doc="dev_docs/design/agents/skill_bundles/05_ResourceCurationSkills.md",
        body=_body(
            "resource-material-acquisition",
            "Use this skill when curating local resource material inside the current active resource draft.",
            (
                "Acquire resource targets with `acquire_resource_material` or import local files with `import_resource_material`.",
                "Extract readable text or project files with `extract_resource_artifact` when the artifact is a PDF, HTML page, TeX archive, or similar container.",
                "Normalize readable text with `normalize_resource_text_material` before draft checking.",
                "Treat acquisition and extraction outputs as intermediate material; place canonical originals under `original/` and readable text under `normalized/`.",
                "Maintain `README.md` with source, access notes, reading order, extraction limits, and why the local resource is useful.",
            ),
            (
                "Do not write SourceCorpus files from resource curation.",
                "Do not finalize a full paper, reusable theory, or formal dependency as a small local resource when it should be an external provider repo.",
                "Do not call local-resource submit until the active draft passes its deterministic check.",
            ),
        ),
    ),
    SkillKey.EXTERNAL_RESOURCE_DISCOVERY.value: LeanSkillDefinition(
        name="external-resource-discovery",
        description="Use when existing evidence is insufficient and an Agent must discover a precise arXiv theorem-like resource target.",
        group="resource",
        required_tool_groups=_groups(AppGroup.EXTERNAL_THEOREM_SEARCH_READ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# External Resource Discovery

Use this Skill only after current source material, accepted Resources, visible dependencies, and Mathlib context do not answer a concrete mathematical need.

## Workflow

1. State the theorem-like concept, assumptions, and evidence gap precisely.
2. Call `search_arxiv_theorems` with a narrow query tied to that gap.
3. Compare candidates by mathematical scope, assumptions, version, and source reliability.
4. Select one accurate arXiv target or report that the search was inconclusive.
5. Carry the selected identifier and the reason it matters into the caller's resource-request workflow.

## Capability Boundary

This Skill supports arXiv theorem-like discovery through the registered tool. It does not discover arbitrary websites or local files. A web URL or local path may be requested only when it already comes from user input, prepared source, or another visible and trustworthy result.

Do not treat search snippets as curated evidence, and do not request a broad result list as one Resource.
""",
    ),
    SkillKey.RESOURCE_REQUEST_SUBMISSION.value: LeanSkillDefinition(
        name="resource-request-submission",
        description="Use when an Agent has identified one precise supporting-material target and may submit a Resource curation request.",
        group="resource",
        required_tool_groups=_groups(
            AppGroup.MATERIAL_CONTEXT_READ,
            AppGroup.RESOURCE_TARGET_PREFLIGHT_READ,
            SubmitGroup.RESOURCE_REQUEST_SUBMIT,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Resource Request Submission

Use this Skill after the caller has identified one precise paper, webpage, or local target that should remain supporting material rather than become an independent Lean repository.

## Preflight

1. Call `get_material_context` and confirm that current source and accepted Resources do not already cover the need.
2. Call `normalize_resource_target` for the proposed target.
3. Call `find_duplicate_resource` with the normalized target.
4. If accepted source or Resource material already covers the target, do not dispatch a duplicate request. Return to the caller's current workflow and use the stable existing reference.

## Submit

Call `submit_resource_request` only when the target is explicit, narrow, trustworthy enough to curate, and accompanied by a concrete mathematical reason.

If the submit is rejected, read the returned issues, repair the target or reason within the same AgentStep, and repeat the preflight against current truth.

If the submit is accepted, stop immediately. ResourceCurationFlow owns acquisition, duplicate classification, local Resource creation, external-repository classification, and rejection.

## Postcondition

The Skill ends in one of two states:

- no request was needed, so the caller returns to its next-action loop with an existing stable material reference; or
- one Resource request was accepted, so the AgentStep stops and waits for the callback.

Do not continue state-changing calls after an accepted submit.
""",
    ),
    SkillKey.RESOURCE_RESULT_CLOSEOUT.value: LeanSkillDefinition(
        name="resource-result-closeout",
        description="Use after a Resource curation callback to reconcile duplicate, local-resource, external-repository, or rejected outcomes.",
        group="resource",
        required_tool_groups=_groups(
            AppGroup.MATERIAL_CONTEXT_READ,
            AppGroup.RESOURCE_LIBRARY_READ,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Resource Result Closeout

Use this Skill when the current turn follows a terminal ResourceCurationFlow callback. The callback is a locator; re-read current material truth before recording consequences.

## Reconcile The Outcome

Call `get_material_context` first. Use `get_resource` when a returned Resource key needs inspection.

Handle exactly the outcome that was returned:

- For a duplicate, verify the existing source or Resource reference and use that stable reference when it supports the current work.
- For a local Resource, verify the finalized Resource and record it through the caller's role-appropriate semantic material mutation when it belongs to the caller's contract.
- For an external repository result, do not treat the target as a Resource. A Coordinator may return to its provider-dependency action; a node-scoped caller must carry the need to its own Coordinator-facing blocked or completion boundary.
- For a rejected result, record the reason as an unresolved direction when relevant, but never use the rejected target as evidence.

Do not name caller-private material-write tools in this shared procedure. Apply only mutations authorized by the current role and Instruction.

## Postcondition

Closeout is complete when the terminal outcome has been checked against current truth, every authorized durable material change has been made, and any external-repository or rejected boundary is explicit.

Then stop using this Skill and return to the caller's next-action loop in the same AgentStep. Do not submit a second Resource request from inside result closeout.
""",
    ),
    SkillKey.RESOURCE_DRAFT_CURATION.value: LeanSkillDefinition(
        name="resource-draft-curation",
        description="Guides resource curation agents when preparing a local Resource draft.",
        group="resource",
        required_tool_groups=_groups(
            AppGroup.RESOURCE_DRAFT_CURRENT_READ,
            AppGroup.RESOURCE_ACQUISITION,
            SubmitGroup.RESOURCE_CURATOR_SUBMIT,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "resource-draft-curation",
            "Use this skill for local Resource draft layout, README requirements, normalized text requirements, draft checks, and local_resource_created submit readiness.",
            (
                "Inspect the current system-created resource draft with `get_resource_draft` before local resource work.",
                "Place originals in `original/`, readable text in `normalized/`, and notes in predictable locations using resource acquisition tools.",
                "Write README content that identifies source, license or access notes, extraction limits, and reading order.",
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
        description="Use when a Coordinator must create, split, merge, repair, or remove Scope and Content node boundaries.",
        group="coordinator",
        required_tool_groups=_groups(
            AppGroup.REPO_PREPARATION_INPUT_READ,
            AppGroup.REPO_COMPLETION_POLICY_READ,
            AppGroup.NODE_TREE_READ,
            AppGroup.NODE_TREE_WRITE,
            AppGroup.NODE_CONTRACT_READ_BY_NODE,
            AppGroup.NODE_CONTRACT_TEXT_WRITE_BY_NODE,
            AppGroup.SOURCE_CORPUS_READ,
            AppGroup.SOURCE_INDEX_NAVIGATION_READ,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Coordinator Node Decomposition

Use this Skill when the current repository needs a new mathematical boundary, an existing node is too broad or fragmented, sibling ownership overlaps, or obsolete structure must be removed.

## Establish The Boundary

1. Read `get_current_repo_completion_policy`, `get_preparation_input`, and the current tree with `get_node_tree`.
2. Read the relevant node contracts and source/index regions rather than decomposing from filenames alone.
3. Estimate likely Lean scale from important source definitions, lemmas, proof stages, and expected Lean-specific helpers. As a soft default, prefer a focused Content node around roughly 5--15 important declarations; treat substantially larger independent packages as split candidates rather than a hard rejection.
4. Use the current mode policy to choose the required graph granularity.
5. Choose Scope nodes for broad mathematical regions and Content nodes for coherent declaration work.
6. Make sibling ownership disjoint and preserve protected root interfaces.

## Decide Where Missing Work Belongs

A blocked Content result does not by itself justify a new node. After recovering the authoritative consumer and dependency frontier, choose the smallest coherent action:

1. continue the current Content node when the missing tracked work is naturally inside its boundary and scale;
2. reuse an existing Content node when that node already owns the relevant Source or mathematical region;
3. create a new ordinary Content node only when the work is an independently meaningful mathematical package with a stable boundary;
4. repair an existing interface, declaration route, or contract when the mismatch comes from semantic drift and a new node would only preserve the drift behind a one-off wrapper.

Describe every Content node by its mathematical boundary. Do not introduce node categories based on why another task currently depends on it.

## Apply Structural Changes

Use `create_scope_node`, `create_content_node`, and `update_node_contract_text` for semantic changes. Use `node-contract-design` when detailed goals, boundaries, objectives, success criteria, material references, dependencies, Mathlib hints, or interfaces must be written.

When a running Content task reports scope overflow or boundary-external dependency work, decide from current source, graph, and private consumer truth whether an independent package should become another node, an existing node should own it, the current contract should be explicitly expanded, or a drifted interface/route should be repaired. Do not silently redispatch the same underspecified boundary.

Before deletion, call `preview_delete_node`. A node containing release-protected declarations or a protected historical Scope chain cannot be deleted. A historical private node is a warning rather than an automatic blocker when no protected declaration or current dependency still needs it. Delete only when the deterministic preview permits it.

Do not place declaration implementation work in a Scope node, and do not create an oversized Content node merely to avoid deciding a mathematical boundary.

## Postcondition

The current tree and affected contracts express stable mathematical ownership without hidden boundary guesses. Re-read `get_node_tree`, then return to the Coordinator next-action loop. Do not dispatch Content work from this Skill.
""",
    ),
    SkillKey.COORDINATOR_SCOPE_LIFECYCLE.value: LeanSkillDefinition(
        name="coordinator-scope-lifecycle",
        description="Use when a Coordinator must create, maintain, or close a Scope contract and its public boundary.",
        group="coordinator",
        required_tool_groups=_groups(
            AppGroup.NODE_CONTRACT_READ_BY_NODE,
            AppGroup.NODE_TREE_READ,
            AppGroup.SCOPE_EXPORT_INTERFACE_READ,
            AppGroup.SCOPE_EXPORT_INTERFACE_WRITE,
            AppGroup.SCOPE_CONTRACT_COMMIT,
            AppGroup.SCOPE_CLOSE_READ,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Coordinator Scope Lifecycle

Use this Skill for one Scope node after its mathematical boundary exists. Scope work owns child-boundary coherence, required interfaces, selected exports, interface bindings, projection state, and contract commitment.

## Maintain The Scope

1. Read the Scope contract, current child tree, interfaces, and exports.
2. Confirm that child goals cover the Scope boundary without unresolved overlap.
3. Update the Scope contract or child structure before downstream work relies on an incorrect boundary.
4. Call `get_scope_close_view` when children may be ready for closeout.

When concrete export selection or interface binding is required, read `scope-export-interface-curation` and follow that supporting guidance inside this same Scope workflow. It is not a separate runtime action.

## Commit

Call `commit_scope_contract` only after required children are complete, exports and bindings satisfy the contract, and the close view reports stable projection and readiness. If the gate rejects, repair only Coordinator-owned semantic state and re-read the close view.

Do not export a declaration merely because it exists, and do not hide an unsatisfied interface behind an unrelated export.

## Postcondition

The Scope is either durably maintained but not yet closable, or its stable contract is committed. Re-read the affected Scope and return to the Coordinator next-action loop. Do not select follow-up work inside this Skill.
""",
    ),
    SkillKey.COORDINATOR_CONTENT_RESULT_CLOSEOUT.value: LeanSkillDefinition(
        name="coordinator-content-result-closeout",
        description="Use after terminal Content task callbacks to reconcile every result before choosing another repository action.",
        group="coordinator",
        required_tool_groups=_groups(
            AppGroup.CONTENT_TASK_RESULT_FINALIZE,
            AppGroup.DECL_GRAPH_READ_BY_NODE,
            AppGroup.NODE_CONTRACT_READ_BY_NODE,
            AppGroup.VISIBLE_DECL_LEAN_FILE_READ,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Coordinator Content Result Closeout

Use this Skill when the current Coordinator turn follows one or more terminal Content node task results. Consume every returned result and commit each reviewed Content contract before planning new work.

## Read The Complete Callback Batch

The callback prompt is the authoritative receipt for the current batch. Process every child result shown there without
calling the history tools merely to repeat it. Use `list_recent_content_task_results` only when recovering an older
callback or when the current callback receipt is unavailable; `inspect_content_task_result` is the same historical
projection for one selected node, not a source of additional declaration evidence.

For each current result, identify the node path, ready/blocked/failed outcome, returned contract version, summary,
reason, and any current state that must be inspected before commitment.

## Inspect Current State Selectively

Read the current node contract. When a result is suspicious or incomplete, use `get_node_decl_graph_index`, `list_node_decls`, and `inspect_node_decl` only for declarations that determine the outcome.

When a blocked result says that missing mathematical work may cross the current Content boundary, or before assigning that work to a different Content node, private consumer inspection is mandatory. Use `inspect_node_decl` for the affected revision and recover its accepted statement, Proof NL route, change/round summary, and declaration dependencies. If the primary projection is insufficient, use `read_visible_decl_lean_file` for the smallest necessary declaration-owned range. Inspect the actual signatures of existing declarations named in the blocker. The blocked reason is an index into authoritative truth, not sufficient contract authority by itself.

For blocked or failed results, classify the concrete consequence without solving it inside closeout: missing source or Resource, missing Mathlib support, current-node tracked work, work owned by another existing node, a coherent new mathematical boundary, provider need, incorrect contract, or Scope/interface work. Record a candidate ownership decision, but make the structural choice only in the Coordinator next-action loop.

For a ready result that is intended to discharge another Content node's dependency, inspect the actual bound public declaration and re-read the original private consumer. Compare the relevant objects, indices, parameters, assumptions, and conclusion direction. The ready result establishes its own contract; it does not establish consumer applicability merely by name, summary, or dependency reason.

## Commit Every Reviewed Result

Call `commit_content_contract` once for each reviewed terminal result. Write a concise Coordinator summary that states what was established, the accepted outcome, the resulting boundary, and any unresolved prerequisite.

Check the returned finalize view. If a claimed ready result fails its deterministic gate, do not weaken the gate or hide the inconsistency. Identify it as child-result or control-plane inconsistency unless Coordinator-owned semantic state can legitimately repair it.

Collect consequences as candidates, not a persisted or immutable action list.

## Postcondition

Every terminal result in the callback batch has been reviewed, every reviewable result is finalized and committed, and every deterministic inconsistency is explicit. Stop using this Skill and return to the Coordinator next-action loop in the same AgentStep.

Do not call a normal Coordinator submit from inside this closeout.
""",
    ),
    SkillKey.COORDINATOR_REQUIREMENT_RESULT_CLOSEOUT.value: LeanSkillDefinition(
        name="coordinator-requirement-result-closeout",
        description="Use after requirement resume to reconcile the automatically attached provider dependency before choosing follow-up work.",
        group="coordinator",
        required_tool_groups=_groups(
            AppGroup.WORKSPACE_REQUIREMENT_READ,
            AppGroup.LAKE_DEPENDENCY_READ,
            AppGroup.WORKSPACE_PROVIDER_CATALOG_READ,
            AppGroup.VISIBLE_NODE_PUBLIC_DECL_READ,
            AppGroup.IMPORTED_REPO_PUBLIC_DECL_READ,
            AppGroup.NODE_TREE_READ,
            AppGroup.NODE_CONTRACT_READ_BY_NODE,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Coordinator Requirement Result Closeout

Use this Skill only on the Coordinator turn resumed after a provider requirement became available. The requirement resume gate has already revalidated publication, proof availability, requested interfaces, exact statements, Lake attachment, and handled status.

## Reconcile Current Truth

1. Call `get_current_repo_requirement` for the resumed requirement and confirm its effective provider and handled status.
2. Call `list_current_lake_dependencies` and confirm the provider is attached.
3. Call `list_ready_provider_repos`, then inspect the effective provider through `list_repo_public_decls` and `inspect_repo_public_decl` as needed.
4. Call `get_node_tree` and re-read the contracts that originally exposed the mathematical gap.
5. Identify which current Content or Scope boundaries the attached public API now enables.

Do not attach the provider again. Do not silently add the dependency to every node, and do not treat provider-private declarations as visible.

## Postcondition

The accepted requirement, attached Lake dependency, stable provider public API, and current node tree have been reconciled. Candidate node-contract updates or runnable work are understood but not persisted as a fixed queue. Return to the Coordinator next-action loop in the same AgentStep.
""",
    ),
    SkillKey.COORDINATOR_DEPENDENCY_READINESS.value: LeanSkillDefinition(
        name="coordinator-dependency-readiness",
        description="Use when upcoming repository work may lack source evidence, Resources, Mathlib support, or a provider dependency boundary.",
        group="coordinator",
        required_tool_groups=_groups(
            AppGroup.NODE_TREE_READ,
            AppGroup.NODE_CONTRACT_READ_BY_NODE,
            AppGroup.SOURCE_INDEX_NAVIGATION_READ,
            AppGroup.RESOURCE_LIBRARY_READ,
            AppGroup.MATERIAL_CONTEXT_READ,
            AppGroup.MATHLIB_INDEX_READ,
            AppGroup.LAKE_DEPENDENCY_READ,
            AppGroup.WORKSPACE_PROVIDER_CATALOG_READ,
            AppGroup.WORKSPACE_REQUIREMENT_READ,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Coordinator Dependency Readiness

Use this Skill before dispatching a mathematical region when its evidence or dependency boundary is uncertain.

## Inspect In Increasing Scope

1. Read `get_node_tree` and the relevant node contract.
2. Start with `get_source_index_overview`. Use `list_source_index_files` or `list_source_blocks` to locate candidates, `get_source_block` for the selected block's complete refs and links, and `read_source_range` for exact evidence. Use full `get_source_index` only when a cross-block or global consistency concern cannot be resolved from compact navigation.
3. Read `get_material_context` and accepted Resources.
4. Search the repo MathlibIndex with `search_mathlib_index`; use the Mathlib recon/search/curation Skills only when the index is insufficient.
5. Call `list_current_lake_dependencies` and check already attached public APIs.
6. Call `list_ready_provider_repos` and current requirement reads only if an external Lean boundary may be needed.

For proved full-graph work, derive the major dependency frontier from Source and current graph truth and prefer bottom-up readiness. Before dispatching an upper theorem toward proved, check whether its source-visible lower stages already have an owner and usable declarations. A large dependency visible before Lean implementation should be planned first; a small representation helper discoverable only while formalizing may legitimately return through a later blocker.

Visibility and proof state are not enough for a consumer-facing dependency. Compare the available declaration's assumptions, parameter and index representation, and conclusion direction with the upcoming consumer shape. Treat a proved public declaration with an incompatible shape as unresolved rather than as readiness evidence.

Classify the smallest unresolved need:

- an existing source or Resource reference;
- a precise supporting Resource target;
- Mathlib knowledge or index curation;
- same-repository node or contract work;
- an already attached provider boundary;
- a stable workspace repository that can be directly attached;
- a new independently meaningful provider requirement.

Use `resource-request-submission` only for supporting material. Use `coordinator-provider-dependency-lifecycle` only for an independent Lean repository boundary. Do not turn proof difficulty alone into an external dependency.

## Postcondition

The dependency gap is either resolved synchronously or classified into one precise action branch. If no submit was accepted, return to the Coordinator next-action loop and choose the appropriate action from current truth.
""",
    ),
    SkillKey.COORDINATOR_CONTENT_TASK_DISPATCH.value: LeanSkillDefinition(
        name="coordinator-content-task-dispatch",
        description="Use when one or more Content nodes have stable contracts and may form a runnable dispatch batch.",
        group="coordinator",
        required_tool_groups=_groups(
            AppGroup.CONTENT_TASK_ADMISSION_READ,
            AppGroup.NODE_CONTRACT_READ_BY_NODE,
            AppGroup.NODE_TREE_READ,
            AppGroup.SOURCE_MATERIAL_TEXT_READ,
            SubmitGroup.COORDINATOR_SUBMIT,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Coordinator Content Task Dispatch

Use this Skill only after candidate Content nodes have clear mathematical boundaries, current contracts, visible dependencies, and sufficient source/resource/Mathlib context.

## Admission And Batch

1. Re-read each candidate contract and current tree position immediately before dispatch.
2. Preview every owned source ref with `preview_source_ref`; confirm each excerpt agrees with the current goal, boundary, interfaces, and recorded reason. Source locators are semantic-tool identities, not workdir-relative file paths.
3. Confirm that context refs have not been used as owned contract evidence.
4. Confirm the contract names its expected important declarations or source stages, a rough scale, and the boundary that prevents unbounded helper expansion.
5. When work originated from another Content node's blocker, confirm that the contract was rebuilt from the authoritative private consumer rather than only from the blocker summary. Its interface summary and statement hint must identify the consumer anchor, expected input/output shape, lower public refs, and conditions that must not drift. A vague request such as "provide the missing theorem" is not dispatch-ready.
6. Confirm that declared node dependencies expose public declarations whose actual assumptions, indices, and conclusions fit the planned work.
7. Call `check_content_task_admission` for each candidate.
8. Use `list_runnable_content_nodes` for orientation when several nodes may run.
9. Call `check_content_node_batch` for the exact proposed batch.
10. If a ref is misplaced or admission fails, repair Coordinator-owned structure or contracts and return to the next-action loop. Do not submit a partially invalid batch.

## Submit

Call `submit_content_node_tasks` only for the validated runnable batch. If rejected, read the issues, re-check current truth, repair within authority, and retry in the same AgentStep.

If accepted, stop immediately and wait for all child task results.

## Postcondition

Either no dispatch occurred and the Coordinator returns to its next-action loop with an explicit admission gap, or exactly one Content batch submit was accepted and the AgentStep stops.
""",
    ),
    SkillKey.COORDINATOR_PROVIDER_DEPENDENCY_LIFECYCLE.value: LeanSkillDefinition(
        name="coordinator-provider-dependency-lifecycle",
        description="Use when a Coordinator must reuse or request an independent mathematical Lean repository dependency.",
        group="coordinator",
        required_tool_groups=_groups(
            AppGroup.WORKSPACE_PROVIDER_CATALOG_READ,
            AppGroup.WORKSPACE_REQUIREMENT_READ,
            AppGroup.VISIBLE_NODE_PUBLIC_DECL_READ,
            AppGroup.IMPORTED_REPO_PUBLIC_DECL_READ,
            AppGroup.LAKE_DEPENDENCY_READ,
            AppGroup.LAKE_DEPENDENCY_WRITE,
            SubmitGroup.COORDINATOR_SUBMIT,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Coordinator Provider Dependency Lifecycle

Use this Skill after deciding that a missing boundary should be supplied by another independently meaningful Lean repository. Do not use it for ordinary current-repo work, a same-repo Content node, a Mathlib fact, supporting material, or a temporary proof-search direction.

## Define The Public Boundary

Describe the smallest reusable provider API from current consumer contracts, protected interfaces, source context, work mode, and proof-availability policy. Request public mathematical objects, not the consumer's private proof plan. When a theorem header is immutable, use the exact-statement field exposed by the submit schema.

## Check Current Dependencies And Workspace Repositories

1. Call `list_current_lake_dependencies`. If the needed provider is already attached, do not attach it again; return to the next-action loop.
2. Call `list_ready_provider_repos` before creating a requirement.
3. For each plausible stable candidate, call `list_repo_public_decls` and selectively `inspect_repo_public_decl`.
4. Check mathematical meaning, declaration kind, namespace, assumptions, proof availability, and exact-statement obligations. Never rely on private declarations or near matches.

If a stable existing repository supplies the boundary, call `attach_ready_workspace_repo_dependency`. This explicit-consent path has no prior requirement contract. Confirm with `list_current_lake_dependencies`, then return to the Coordinator next-action loop. Node-level dependency registration is a later contract decision.

If direct attachment is rejected, inspect the reported publication, compatibility, or argument issue. Repair a stale argument or re-check current publication truth when possible. Do not bypass a failed stable-publication or interface check by submitting a weaker requirement for the same unsuitable repository.

## Avoid Duplicate Requirements

Call `get_current_repo_requirement` when a known consumer-local requirement may already represent the need. Use the broader requirement reads when necessary. Do not submit an equivalent open, waiting, satisfied, or handled requirement.

## Submit A New Requirement

Call `submit_repo_requirement` only when no current dependency or stable workspace repository satisfies a precise, independently useful provider boundary. Follow the tool schema for requirement identity, repository identity, source description, reason, interfaces, and exact statements; do not restate those field-format rules here.

If rejected, repair the requirement from returned issues and re-check current truth. If accepted, stop immediately and wait.

Do not attach the future provider from this Skill. The requirement resume gate later validates the satisfied contract, automatically attaches the provider, marks the requirement handled, and resumes the same Coordinator Flow. The resumed turn uses `coordinator-requirement-result-closeout`.

## Postconditions

- Already attached: return to the next-action loop.
- Existing stable repository directly attached and verified: return to the next-action loop.
- New requirement accepted: stop the AgentStep and wait.
""",
    ),
    SkillKey.COORDINATOR_REPO_READY_LIFECYCLE.value: LeanSkillDefinition(
        name="coordinator-repo-ready-lifecycle",
        description="Use when the Main Scope and repository-level obligations appear complete and the Coordinator may submit repository readiness.",
        group="coordinator",
        required_tool_groups=_groups(
            AppGroup.REPO_RUN_CONTEXT_READ,
            AppGroup.REPO_PREPARATION_INPUT_READ,
            AppGroup.NODE_TREE_READ,
            AppGroup.SCOPE_EXPORT_INTERFACE_READ,
            AppGroup.SCOPE_CLOSE_READ,
            AppGroup.LAKE_DEPENDENCY_READ,
            AppGroup.REPO_READY_READ,
            SubmitGroup.COORDINATOR_SUBMIT,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Coordinator Repository Ready Lifecycle

Use this Skill only when all expected Content work is reconciled, required Scopes are committed, protected interfaces and public exports appear satisfied, dependencies are stable, and proof-policy obligations match the repository work mode.

## Verify Readiness

1. Re-read `get_current_repo_run_context`, `get_preparation_input`, `get_node_tree`, and current Lake dependencies. Confirm the bound release baseline still matches current truth.
2. Call `get_scope_close_view` for Main and any relevant unverified Scope.
3. Check Main interfaces and exports against protected root contracts.
4. Call `get_repo_ready_node_view`. It previews the authoritative candidate release gate across active contract heads, DeclGraphs, files/projections, compatibility, target policy, and build preconditions; inspect every reported issue.
5. If the deterministic view is not ready, repair only the owning semantic state and return to the next-action loop.

## Submit

Call `submit_repo_ready` only when the preview passes. This submit expresses candidate intent only; the following deterministic Flow step owns build, checkpoint, release creation, and publication. If rejected, use the returned issues to repair current truth and re-run the readiness view. If accepted, stop immediately.

## Postcondition

Either readiness remains unresolved and the Coordinator returns to its next-action loop with a concrete gate issue, or one repository-ready submit is accepted and the AgentStep stops.
""",
    ),
    SkillKey.COORDINATOR_COMPLETION_POLICY.value: LeanSkillDefinition(
        name="coordinator-completion-policy",
        description="Use when applying the current repository completion mode to Coordinator node and dispatch planning.",
        group="coordinator",
        required_tool_groups=_groups(AppGroup.REPO_COMPLETION_POLICY_READ),
        source_design_doc="dev_docs/implementation/repo_completion_mode_and_checkpoint_migration/03_Agent输入与自然语言说明设计.md",
        body=_COORDINATOR_COMPLETION_POLICY_BODY,
    ),
    SkillKey.MATHLIB_INDEX_FIRST_RECON.value: LeanSkillDefinition(
        name="mathlib-index-first-recon",
        description="Use when Mathlib support may be needed so repo-level MathlibIndex is checked before broader search.",
        group="mathlib",
        required_tool_groups=_groups(AppGroup.MATHLIB_INDEX_READ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "mathlib-index-first-recon",
            "Use this skill when finding Mathlib modules or declarations for a Lean Constellation node while avoiding repeated global search.",
            (
                "Read curated node-local Mathlib hints first when the current task context includes them.",
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
        description="Use when MathlibIndex is insufficient and semantic search plus module/declaration inspection is required.",
        group="mathlib",
        required_tool_groups=_groups(AppGroup.MATHLIB_SEMANTIC_SEARCH, AppGroup.MATHLIB_NAVIGATION),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "mathlib-semantic-search-navigation",
            "Use this skill after current node hints and repo-level MathlibIndex are insufficient.",
            (
                "Use `search_mathlib_declarations` for mathematical concepts before considering any additional toolkit-backed search backend that is visible to your role.",
                "Inspect plausible candidates with `inspect_mathlib_search_candidate`; navigate a module with `inspect_mathlib_module` only when module-level declarations are needed, and request imports or source excerpts only when the default compact result is insufficient.",
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
        description="Use after verifying reusable Mathlib knowledge to record stable module and declaration entries.",
        group="mathlib",
        required_tool_groups=_groups(AppGroup.MATHLIB_INDEX_WRITE),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "mathlib-index-entry-curation",
            "Use this skill after search or navigation has identified reusable Mathlib knowledge.",
            (
                "Record modules with concise purpose and import relevance through `record_mathlib_module`.",
                "Record declarations with statement meaning and usage notes through `record_mathlib_decl` or `ingest_mathlib_candidate`.",
                "When two or more already-understood entries can share one accessibility probe, use `record_mathlib_batch` (maximum 25 total entries); fall back to individual checked records to isolate a failed combined probe.",
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
                "Add already-verified module and declaration hints in one `add_current_mathlib_hints` batch, then reread current hints once.",
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

You may perform targeted searches, inspections, and small verified index corrections to answer a concrete planning question. Delegate systematic, multi-candidate, or persistence-worthy dependency, Mathlib, or resource recon to the dedicated child flow so that its findings are recorded and reviewed separately. Your job is to decide when that child is needed, give it a focused objective, and interpret its callback result.

The callback turn already contains the current child input/result once, followed by short routing and current-state guidance. Do not rebuild or paste a second summary. Use `list_content_preparation_results` only when an older attempt matters, then use `get_content_preparation_result` for the one selected attempt.

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
2. Treat the current child result in this callback as the primary closeout evidence.
3. Re-read relevant DeclGraph, dependency, Mathlib, source, or resource state through available tools.
4. Read historical preparation only when comparing an older attempt or resolving a contradiction.
5. Decide whether the result is sufficient.
6. If only a small correction is needed, use current-node scoped mutation tools.
7. Do not rerun the same preparation kind in the same task.
8. Continue the preparation checklist, start strategy planning, or complete/block/fail the task.

## Boundaries

- Do not dispatch preparation flows just to gather vague context.
- Do not ask one preparation flow to perform a different preparation kind.
- Do not place full contracts or graph dumps into child prompts.
- Do not use current-node correction tools as a substitute for broad child-flow recon.
""",
    ),
    SkillKey.CONTENT_PLAN_COMPLETION_POLICY.value: LeanSkillDefinition(
        name="content-plan-completion-policy",
        description="Use when applying the current repository completion mode to ContentPlan round strategy.",
        group="content_plan",
        required_tool_groups=_groups(AppGroup.REPO_COMPLETION_POLICY_READ),
        source_design_doc="dev_docs/implementation/repo_completion_mode_and_checkpoint_migration/03_Agent输入与自然语言说明设计.md",
        body=_CONTENT_PLAN_COMPLETION_POLICY_BODY,
    ),
    SkillKey.DECL_STRATEGY_PLANNING.value: LeanSkillDefinition(
        name="decl-strategy-planning",
        description="Use when the ContentPlanAgent creates, continues, closes, or replaces a DeclGraph strategy.",
        group="content_plan",
        required_tool_groups=_groups(
            AppGroup.DECL_GRAPH_CURRENT_NAVIGATION_READ,
            AppGroup.DECL_STAGE_ROUND_READ,
            AppGroup.DECL_STRATEGY_WRITE,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Decl Strategy Planning

## Purpose

Use this skill before preparing a new DeclGraph round, after a preparation callback, or after a previous round has completed, blocked, or failed.

A strategy is a high-level route for making progress inside the current content node. It may target the whole node, one required interface, a proof decomposition, a bottom-up foundation segment, or an intermediate theorem. It is not itself a statement, proof, or Lean artifact.

## Read Current Truth First

Before creating or changing a strategy, read current state with:

- `get_current_node_contract` for the current task boundary and objective;
- `get_current_repo_completion_policy` for the current completion mode;
- DeclGraph read tools for graph state, active declarations, and round history;
- strategy read tools for existing strategy state.

Follow `content-plan-completion-policy`. The strategy should explain how its route satisfies the current completion mode inside the current contract and source scope.

Do not rely on conversation memory when tools can show current truth.

## What To Analyze

Analyze:

- the current node goal, boundary, objective, materials, dependencies, and interfaces;
- which required interfaces or useful public declarations are not proof-policy satisfied;
- which previous rounds succeeded, blocked, failed, or changed the graph;
- whether the current route should be bottom-up, top-down, helper-decomposition based, declared skeleton, declared interface, or a small repair route;
- whether missing support should first be handled through node dependencies, Mathlib, resources, or Coordinator escalation.
- whether an interface statement hint supplies a consumer-side shape that the strategy must serve without strengthening its assumptions, replacing its objects or indices, or weakening its conclusion.

## Reassess Before Continue

Re-read this Skill after every preparation callback, every round terminal callback, and before creating each new round. Reassessment is mandatory; replacing the strategy is not. Reassess when the source route changed, a blocker exposed a missing dependency stage, the declaration graph materially outgrew the strategy's scale assumptions, an interface or public boundary changed, repeated parent retries did not close known blockers, or current graph truth contains superseded branches the strategy no longer explains.

The strategy objective and rationale should record the selected Source route, bottom-up/top-down choice and reason, known major dependency stages, intended public/interface output and consumer shape, scope and scale assumptions, and any explicit state-only intermediate closure plan. If the required lower work forms a package outside the current Content boundary, close out current truth and report the boundary decision to the Coordinator instead of expanding the strategy without limit.

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

## Semantic Declaration Planning

For every source- or contract-derived create, update, or delete, inspect the committed SourceIndex and relevant SourceCorpus range before mutation. Keep the stable catalog summary distinct from the current change objective. The change objective must identify the semantic boundary to preserve, the relevant source/contract reference, and why this action belongs in the current route. External Resources may clarify an explicit gap but do not silently replace source semantics.

## Interface Fit

For every change, state which already accepted lower declarations or Mathlib facts it consumes and which upper declaration, public interface, or contract goal it must serve. Prefer repairing a private declaration whose source-derived interface does not connect over adding an unexplained bridge. Create a tracked bridge/helper only when it has independent mathematical meaning, a clear source/Lean role, or multiple real consumers.

When a contract statement hint gives a consumer-side Lean shape, copy the relevant objects, binders, assumptions, index representation, and conclusion direction into the change objective. Do not add assumptions the consumer cannot supply, replace a required object/index with an easier one, or weaken the required conclusion. The worker may choose the exact Lean header inside that semantic boundary.

## Target Readiness

Before choosing `target_state=proved`, check whether the source proof stages and known dependency closure are available. If important lower source-derived declarations are still missing, either plan those bottom-up first or, in the narrow top-down case, declare the stable parent statement without asking the same round to prove it. A blocker first discoverable only through Lean implementation may justify a later helper round; a dependency visible from source/graph truth should be planned before parent proof dispatch.

If satisfying the target now requires a coherent package outside the current Content boundary, do not grow the round or strategy indefinitely. Close out the current round when necessary and submit a precise Content blocker for Coordinator ownership review.

## Create Changes

Use `plan_create_decl` for new declarations. Each create change should have:

- a flat `Decl.name` and clear kind; the name is one Lean module filename segment, so it cannot contain dots or path separators;
- a concise catalog summary distinct from the current round objective;
- visibility appropriate for the node contract;
- a concise mathematical objective;
- target_state;
- require_target_state_satisfied.

For a native repository, do not plan or guess `Decl.module` or the Lean full declaration name. The system derives the module from repo/node/kind/name and formal capture discovers the full name.

Helper lemmas that matter to later work should be tracked as their own declarations. Do not hide important helper lemmas as untracked local Lean code.

## Update Changes

Use `plan_update_decl` when an existing declaration needs a targeted repair or stage advancement. The execution interval is (reset_to_state, target_state] over the fixed pipeline:

`planned --Statement NL--> specified --Statement Formal--> declared --Proof NL--> proof_planned --Proof Formal--> proved`.

By default, the service copies the current committed head and uses that base revision's state as reset_to_state. If that base already reaches target_state, you must explicitly choose a lower reset_to_state to describe the redo range. Use optional base_revision only to copy a specific older committed revision; this creates a new monotonic revision and does not move history backward.

reset_to_state is a retained boundary, not a stage to run: reset_to_state=declared and target_state=proved begins with Proof NL. For a release-protected declaration it cannot cross beneath the accepted formal statement. A proof_planned reset retains the proof plan and clears only proof-formal artifacts/checks. Include the intended target_state and require_target_state_satisfied.

Do not use an update change to silently change a previously accepted mathematical meaning.

## Target Satisfaction

Use `target_state=declared` when the round should produce or repair the statement layer. Use `target_state=proved` when the round should produce or repair the proof layer for a theorem-like declaration.

Keep `require_target_state_satisfied=true` unless the selected mode skill explicitly justifies a state-only intermediate change. A state-only intermediate change must be followed by later changes that make its dependency closure proof-policy satisfied.

## Delete Changes

Before deleting a declaration, call `preview_decl_delete_closure`. Never plan deletion of a release-protected declaration. Private declarations may be removed only when current references and the preview permit it.

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
        required_tool_groups=_groups(
            AppGroup.DECL_ROUND_CLOSEOUT_WRITE,
            AppGroup.DECL_GRAPH_CURRENT_NAVIGATION_READ,
            AppGroup.DECL_STAGE_ROUND_READ,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Decl Round Closeout

## Purpose

Use this skill after a DeclGraphRoundFlow terminal callback and before planning the next action.

The round Flow records only its structured execution outcome and leaves the round waiting for ContentPlan closeout; it does not write semantic summaries, commit revisions, or refresh the final node projection. Round closeout is the ContentPlan-owned synchronous state sequence that records meaning and atomically commits accepted truth. After closeout, re-read truth and continue planning unless a submit action is the next justified step.

## Required Order

1. Read the callback result and confirm the current round is awaiting closeout.
2. Re-read the current round, changed declarations, affected revisions, and relevant graph state; do not duplicate the detailed callback payload already present in the turn.
3. Write one summary per changed declaration with `write_decl_change_summary`.
4. Write the round summary with `write_decl_round_summary`.
5. Commit terminal closeout with `mark_decl_round_terminal`.
6. Re-read current truth.
7. Decide whether to plan another round, run preparation, check content completion, report blocked, or fail.

Do not start a new round, dispatch preparation, close the strategy, or submit a Content terminal result before closeout is recorded.

## Change Summaries

Use `write_decl_change_summary` to record what happened to each declaration in the round. Mention the intended change, the terminal outcome, accepted state changes, and concrete blocker or failure details when relevant.

For blocked changes, classify the blocker as source/contract evidence, visible or provider dependency, predictable tracked helper, Lean-specific helper, statement/interface drift, scope overflow, or runtime failure. Preserve the worker or reviewer evidence: affected declaration, unresolved consumer-side local goal or formal shape, checked declarations, and the precise mismatch in objects, indices, parameters, assumptions, conclusion, representation, or visibility. Record every concrete missing dependency and the conditions that must hold before retrying a parent declaration. Mark superseded candidates and cleanup targets for the next planning decision; closeout itself does not delete them.

Do not hide an important blocked cause inside a generic success or failure summary, and do not compress a concrete formal mismatch into only "needs a helper" or "needs a dependency".

## Round Summary

Use `write_decl_round_summary` to summarize the whole round. The round summary should explain:

- whether the strategy made progress;
- which declarations reached their target state;
- which declarations became proof-policy satisfied under the current repo target;
- which declarations still need work;
- whether any intentionally state-only intermediate change still needs dependency closure work;
- for a blocked parent, the complete known retry conditions and whether each missing item is Source-visible planned work or a Lean-specific implementation helper;
- whether the next step is another round, preparation, completion check, blocked, or failed.

## Terminal Commit

Use `mark_decl_round_terminal` only after the change summaries and round summary are written. A successful execution may be conservatively closed as success, blocked, or failed; blocked may become blocked or failed; failed may only remain failed. The operation commits open revisions and the round atomically, applies successful delete lifecycle, refreshes or safely defers the final projection, and records your closeout acknowledgement. Replaying the exact same closeout returns unchanged rather than an error. After marking terminal, read current truth again and re-read the current mode Skill and `decl-strategy-planning` before any new planning action.

## Boundaries

- Do not start a new round or perform another business action before closeout is recorded.
- Do not change statement or proof artifacts during closeout.
- Do not use closeout tools as a substitute for worker or reviewer results.
- Do not hide blocked causes in a generic summary.
""",
    ),
    SkillKey.CONTENT_NODE_COMPLETION_DECISION.value: LeanSkillDefinition(
        name="content-node-completion-decision",
        description="Use when the ContentPlanAgent decides whether the current content node task should end as ready, blocked, or failed.",
        group="content_plan",
        required_tool_groups=_groups(
            SubmitGroup.CONTENT_COMPLETION_SUBMIT,
            AppGroup.CONTENT_INTERFACE_CURRENT_WRITE,
            AppGroup.CONTENT_COMPLETION_GATE_READ,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Content Node Completion Decision

## Purpose

Use this skill when deciding whether the current content node task should end as ready, blocked, or failed.

A natural-language claim is not enough. Use the content completion gate and submit tools to complete the task through the workflow.

## Graph Hygiene And Scope Audit

Before interface binding or terminal submission, inspect active consumers and historical private branches. If the node contains safely deletable superseded private declarations, obsolete route artifacts, or retry-version names, plan a small cleanup round first. If the remaining graph contains an independent mathematical package or has expanded far beyond the contract's expected source stages and reasonable helper allowance, submit blocked with a concrete split/contract-review recommendation instead of growing the node without bound.

## Current-Node Interface Binding

Before the readiness gate, inspect the current contract and current public declarations. For every required interface without a binding, choose the semantically matching public declaration on this same Content node and call `bind_current_node_interface`. The service validates declaration visibility, readiness, kind, formal statement, and current revision.

This is ContentPlan closeout work. Do not treat an unbound current-node interface as a Coordinator blocker when a valid declaration is available. Do not alter the interface requirement, bind a declaration from another node, or bind parent Scope exports; those remain outside this tool's authority.

## Interface Semantic Fit

For each required interface, read the actual bound public declaration and compare it with the interface summary and statement hint. Preserve the hinted consumer objects, binders, assumptions, index representation, and conclusion direction. If a hint includes a partial Lean snippet, verify that the bound declaration supplies that capability semantically; name similarity is not evidence, and the hint is not an exact header equality requirement. If the declaration is clearly mismatched, continue current-node work or report a contract blocker instead of submitting ready.

## Ready

Before ready, call `check_current_content_node_completion`. Use the returned gate report as the authority for whether the current node satisfies its contract, proof-policy requirements, dependency identities, managed-file synchronization, interfaces, and unresolved callback requirements. The deterministic gate refreshes the node boundary and builds its `Interfaces` module so every current public declaration is checked through the actual import surface and standard artifacts are generated.

Call `submit_content_node_ready` only when the current tools show the node satisfies its contract. After an accepted ready submit, stop.

If `check_current_content_node_completion` rejects readiness, do not force ready. Fix issues within your authority, run another round, dispatch allowed preparation, or choose blocked/failed when appropriate.

## Blocked

Call `submit_content_node_blocked` when the current task cannot responsibly continue because it needs action outside the ContentPlanAgent authority. Typical blocked reasons include:

- Coordinator must revise the node boundary, objective, interfaces, or node tree;
- an external provider repository is required;
- source or resource material is missing and cannot be acquired from this task;
- a proof route requires higher-level decomposition;
- preparation found a prerequisite that this content node cannot create.

When the blocker requires a decision beyond the current Content boundary, first re-read the affected revision and authoritative round closeout. Use a structured multiline reason that preserves:

- the blocked node, declaration, revision, and current/target state;
- the concrete mathematical gap and why it is not a reasonable current-node local helper;
- the consumer-side formal goal, equation, binders, hypotheses/conclusion, or accepted Proof NL fragment;
- the existing declarations checked and each concrete mismatch;
- current contract ownership, completed/estimated scale, and why Coordinator review is needed;
- a requested decision among current-node continuation, existing-node ownership, a coherent new boundary, or interface/route repair;
- Source, contract, interface, and declaration anchors.

Recommend ownership only as evidence; do not create or decide the repository node tree, and do not design the final public theorem for another Content node. After an accepted blocked submit, stop.

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
        required_tool_groups=_groups(
            AppGroup.CURRENT_NODE_DECL_READ,
            AppGroup.CURRENT_NODE_PUBLIC_DECL_READ,
            AppGroup.VISIBLE_NODE_PUBLIC_DECL_READ,
            AppGroup.IMPORTED_REPO_PUBLIC_DECL_READ,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# decl-dependency-origin-curation

## Purpose

Use this skill when a declaration worker or reviewer must connect a statement or proof artifact to stable evidence, visible project declarations, Mathlib declarations, and concrete blocked follow-up needs.

## Workflow

1. Start from the current declaration truth with `inspect_current_node_decl`, then read the current node contract as the boundary and material authority.
2. Treat the DeclRevision, change objective, target state, declaration kind, visibility, current stage, and previous revision as the first source of truth for what may be written or reviewed.
3. Prefer stable evidence in the source corpus, resource library, visible public declarations, and recorded Mathlib index before broader semantic search.
4. Use semantic search or external theorem discovery only as discovery. A search hit is not a stable origin until it is already represented in source corpus or resource library truth.
5. For Statement NL workers, write statement text first, then add only stable typed source/resource evidence ranges. Use origin removal or clearing only to repair the current candidate.
6. For Statement NL workers, add project dependencies with the statement repo dependency tool and Mathlib dependencies with the statement Mathlib dependency tool. Use the singular form for one item and the plural form for a small known batch. Use dependency removal or clearing only to repair the current candidate. Do not use a flat mixed dependency list or an untyped origin dictionary.
7. For Statement Formal workers, refine typed statement dependencies when the final formal statement uses a project or Mathlib declaration that is not already recorded. Exact Mathlib dependency addition verifies or reuses canonical index truth and updates the managed projection atomically; do not separately curate the repo index for that dependency. Do not mutate statement text, statement origins, proof routes, or proof artifacts while doing this.
8. For Proof NL workers, write proof-route text first, then add only stable typed source/resource origins that support the proof route itself. Add project and Mathlib proof dependencies through their separate typed tools; do not use a flat mixed dependency list or an untyped origin dictionary.
9. For Proof Formal workers, refine typed proof dependencies when the final formal proof actually uses a project or Mathlib declaration that is not already recorded. Do not mutate proof-route text or proof origins while doing this.
10. For reviewers, inspect typed source/resource origins and dependencies as artifacts under review. When the candidate is otherwise acceptable and only a small number of already verified Mathlib dependencies were omitted, use the stage-matching add-only Mathlib dependency tool. Do not alter project dependencies, remove or replace dependencies, or use this repair for semantic, helper, or boundary gaps.
11. Record origins only for source/resource ranges that actually support the statement or proof. Generated or agent-authored text may have no origin; do not overclaim support.
12. Keep statement dependencies and proof dependencies separate. Statement dependencies are only the project or Mathlib declarations needed to express the statement; proof-only helper lemmas belong to proof dependencies.
13. Do not use unfinished same-round declarations as stable dependencies unless the current truth already marks them accepted and suitable for this stage.
14. Before submit, self-check that origins are stable, dependencies are visible, source support is not invented, and blocked needs name the missing material, dependency, helper declaration, resource, provider repo, or planning change.
15. When a blocked need requires Planner or Coordinator action, preserve the affected declaration, unresolved consumer-side goal or formal shape, the project/Mathlib declarations checked, their precise semantic or signature mismatch, and the conditions required before retry. Distinguish work repairable in the current stage, tracked work inside the current Content node, a coherent package outside the current boundary, and external provider/resource work.
16. A worker may include a local Lean goal or code fragment as evidence, but must not design the final public theorem or repository node tree for another Content node.

## Dependency Display And Projection

Project dependencies are displayed as `[repo-key::]node-path::Decl.name` → `Lean full name` from `Lean module`. Use the left locator with Constellation inspect tools, the arrow target in Lean expressions, and the module for imports. Current-repo locators omit the repo key; external-repo locators include it. Mathlib dependencies are displayed as `Lean full name` from `module`.

The revision/reason remains structured truth and is not copied into the docstring. Formal capture requires every dependency to resolve to a module and full name. Dependency mutation refreshes managed docstrings and imports. A Formal worker must re-read the file whenever the mutation result reports that rereading is required; do not hand-write an import that the managed projection derives.

## Boundaries

- Do not invent source support for generated ideas.
- Do not write external search hits directly as origins.
- Do not mix proof-only dependencies into statement dependency fields.
- Do not add dependencies on same-round declarations that are not accepted or satisfied for the current need.
- Do not mutate node contracts unless the current Agent instruction explicitly grants narrow current-node dependency maintenance for a verified provider dependency. Do not dispatch resource curation or create helper declarations from declaration worker stages; block or return the gap to planning instead.
""",
    ),
    SkillKey.DECL_OWNED_LEAN_FILE_CAPTURE_CHECK.value: LeanSkillDefinition(
        name="decl-owned-lean-file-capture-check",
        description="Work safely with Lean Constellation declaration-owned Lean files, manual Lean checks, and capture/check tools.",
        group="lean",
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "decl-owned-lean-file-capture-check",
            "Use this skill when a formal worker edits a tracked declaration file or when a reviewer needs to understand formal capture semantics.",
            (
                "Use the current stage's prepare tool only to generate or recover the legal declaration-owned working file when the scaffold, marker, docstring, or file structure is missing or damaged.",
                "Treat prepare as destructive for uncaptured working-file edits.",
                "Read the prepared file first. Keep the managed imports and managed docstring unchanged; helpers go before the target docstring, and the marker-adjacent primary declaration follows it as the last principal declaration.",
                "A native `Decl.name` is only the flat module filename key. Do not manually set the Lean full name; statement capture discovers it from the marker-adjacent declaration and confirms it with Lean. Proof capture requires the same full name and theorem header.",
                "After a dependency or Mathlib mutation reports that rereading is required, reload the file before further edits because managed imports/docstrings may have changed.",
                "Use the current stage's diagnostics while iterating.",
                "Use the current stage's capture tool to build the exact module, generate standard `.olean`/`.ilean` artifacts, confirm identity, and save durable formal state after editing.",
                "After capture, edit only if you will capture again.",
                "Use the current stage's consistency gate before worker submit when it is available, and require it to pass.",
                "Statement formal theorem-like declarations may use the workflow's statement-stage proof placeholder; proof formal completed work must not contain sorry, admit, axiom, opaque, unsafe, or equivalent shortcuts.",
            ),
            (
                "Do not rely on uncaptured edits as accepted formal content.",
                "Do not let reviewers prepare, capture, or mutate formal files; reviewers record review marks and submit review results.",
                "Do not treat a diagnostic or consistency tool call as sufficient; the result must pass before submit.",
            ),
        ),
    ),
    SkillKey.LEAN_STATEMENT_FORMALIZATION.value: LeanSkillDefinition(
        name="lean-statement-formalization",
        description="Formalize an accepted natural-language declaration statement into a Lean declaration.",
        group="lean",
        required_tool_groups=_groups(
            AppGroup.DECL_STAGE_FORMAL_READ,
            AppGroup.DECL_STAGE_STATEMENT_FORMAL_FILE_WRITE,
            AppGroup.DECL_STATEMENT_DEPENDENCY_READ,
            AppGroup.DECL_STATEMENT_REPO_DEPENDENCY_WRITE,
            AppGroup.DECL_STATEMENT_MATHLIB_DEPENDENCY_WRITE,
            AppGroup.DECL_FORMAL_CONSISTENCY_READ,
            AppGroup.LEAN_FILE_DIAGNOSTICS_READ,
            AppGroup.STATEMENT_FORMAL_POLICY_READ,
            AppGroup.NODE_CONTRACT_DEPENDENCY_CURRENT_WRITE,
            AppGroup.MATHLIB_INDEX_READ,
            AppGroup.MATHLIB_SEMANTIC_SEARCH,
            AppGroup.MATHLIB_NAVIGATION,
            AppGroup.NODE_MATHLIB_HINT_READ,
            AppGroup.NODE_MATHLIB_HINT_WRITE,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "lean-statement-formalization",
            "Use this skill for Statement Formal workers after statement NL has been accepted.",
            (
                "Start from the accepted statement and declared objective.",
                "Map variables, assumptions, definitions, and conclusions to Lean deliberately.",
                "Search dependencies in visible project context and Mathlib before adding imports, dependencies, or hints.",
                "Prepare the declaration-owned file with `prepare_statement_formal_file` only to recover missing or damaged scaffold, marker, docstring, or file structure. Do not call it casually after valid uncaptured edits because it rewrites the working file.",
                "Preserve managed imports/docstring; place small local helpers before the target docstring and the primary declaration immediately after it. Keep reusable helpers as separate tracked Decls.",
                "Use `run_lean_file_diagnostics` while iterating. Do not guess or report the Lean full name: `capture_statement_formal_file` builds the exact module and discovers/compiler-confirms it.",
                "Read the exact set with `list_statement_dependencies`; add project dependencies with `add_statement_repo_dependency` or its plural form, and Mathlib dependencies with `add_statement_mathlib_dependency` or its plural form. Use remove/clear only for repair. If a mutation refreshes the file, re-read it before editing and do not duplicate derived imports.",
                "Capture with `capture_statement_formal_file`, require `check_formal_stage_consistency` to pass, and then call `submit_stage_worker_completed`.",
                "Read current node Mathlib hints first, then the repo MathlibIndex; only use broader search/navigation when those are insufficient.",
                "For a Mathlib candidate used by the current statement, add the exact typed dependency through the Mathlib dependency tool; it verifies the declaration and ensures canonical MathlibIndex truth atomically.",
                "Record confirmed current-node module and declaration relevance in `add_current_mathlib_hints` only when the knowledge is broadly reusable across the node.",
                "Use `add_current_node_dep` only when the current formal statement actually needs a provider node dependency that is not already available.",
            ),
            (
                "Do not silently change statement meaning to make Lean easier.",
                "Do not complete theorem proofs in the statement formalization stage.",
                "Do not record guessed Mathlib entries, raw search scratch, future proof-only lemmas, or APIs unrelated to the current statement.",
            ),
        ),
    ),
    SkillKey.LEAN_PROOF_FORMALIZATION.value: LeanSkillDefinition(
        name="lean-proof-formalization",
        description="Formalize a reviewed natural-language proof route into a Lean proof while preserving the accepted formal statement.",
        group="lean",
        required_tool_groups=_groups(
            AppGroup.DECL_STAGE_FORMAL_READ,
            AppGroup.DECL_STAGE_PROOF_FORMAL_FILE_WRITE,
            AppGroup.DECL_PROOF_DEPENDENCY_READ,
            AppGroup.DECL_PROOF_REPO_DEPENDENCY_WRITE,
            AppGroup.DECL_PROOF_MATHLIB_DEPENDENCY_WRITE,
            AppGroup.DECL_FORMAL_CONSISTENCY_READ,
            AppGroup.LEAN_FILE_DIAGNOSTICS_READ,
            AppGroup.PROOF_FORMAL_POLICY_READ,
            AppGroup.NODE_CONTRACT_DEPENDENCY_CURRENT_WRITE,
            AppGroup.MATHLIB_INDEX_READ,
            AppGroup.MATHLIB_SEMANTIC_SEARCH,
            AppGroup.MATHLIB_NAVIGATION,
            AppGroup.NODE_MATHLIB_HINT_READ,
            AppGroup.NODE_MATHLIB_HINT_WRITE,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "lean-proof-formalization",
            "Use this skill for Proof Formal workers after proof NL has been accepted.",
            (
                "Start from the accepted formal statement, reviewed proof route, proof origins/deps, current decl history, and prior feedback.",
                "Inspect the prepared proof formal file first. Use `prepare_proof_formal_file` only to repair missing or damaged scaffold, marker, docstring, theorem header, or file structure; it rewrites from accepted statement formal capture and discards uncaptured proof edits.",
                "Preserve the registered Lean full name and theorem header. Put small proof-local helpers before the target docstring; block for planning when a helper is major, reusable, or mathematically meaningful enough to be tracked as a declaration.",
                "Use `run_lean_file_diagnostics` and `check_proof_formal_policy` while iterating; proof formal completed work must satisfy strict proof policy.",
                "Capture at the durable boundary with `capture_proof_formal_file`; if any edit happens after capture, capture again.",
                "Before submit, require `check_formal_stage_consistency` to pass.",
                "Read the exact set with `list_proof_dependencies`; add project dependencies with `add_proof_repo_dependency` or its plural form, and Mathlib dependencies with `add_proof_mathlib_dependency` or its plural form. Use `remove_proof_dep` / `clear_proof_deps` only for repair. A successful mutation with `managed_projection_changed=true` or `reread_required=true` is not a blocker: re-read the declaration-owned file once after the batch in the same AgentStep and continue.",
                "Read current node Mathlib hints first, then repo MathlibIndex; search or navigate only when those are insufficient.",
                "For a Mathlib candidate used in the current proof, add the exact typed dependency through the Mathlib dependency tool; it verifies or reuses canonical MathlibIndex truth atomically. Record current-node relevance with hint tools only when the knowledge is broadly reusable across the node.",
                "Use `add_current_node_dep` only when the final proof actually needs a verified provider public declaration that is not already available.",
            ),
            (
                "Do not alter the theorem statement to make the proof work.",
                "Do not mutate proof NL, proof origins, statement fields, reviewer marks, or round plans.",
                "Do not record guessed Mathlib entries, search scratch, future-proof possibilities, or APIs unrelated to the current proof.",
                "Do not leave sorry, admit, axiom, opaque, unsafe, or equivalent shortcuts in completed proof work.",
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
