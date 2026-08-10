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
  the required public interfaces and their formal Statement prerequisites. Do not
  create proof-only helper nodes.
- graph_declared: represent the selected source scope as a complete declaration
  graph, including important definitions, theorem statements, and intermediate
  lemma statements. Do not raise the repository requirement to proof completion.
- graph_proved: represent the selected source scope as a complete proof graph.
  Derive the Source-visible dependency frontier, assign lower stages first, and
  dispatch an upper theorem only after its owned lower declarations are ready,
  visible through the required boundary, and signature-compatible. Theorem-like
  outputs and proof-relevant helpers must eventually be proved; non-theorem
  foundations must be declared.

The run objective and source/interface contracts define *what* is in scope;
completion mode defines *how far* that scope must be completed. Preserve every
released state floor and release-protected statement. Use scope nodes for broad
mathematical regions, content nodes for coherent declaration work, explicit
exports for sibling reuse, and contracts that state the completion expected for
their declarations. A Content contract version may explicitly select a shallower
task completion mode for theorem-statement staging. That task target does not
change this repository target: a partial result remains ineligible as a node
dependency, Scope export source, or Scope-close child until a later contract
version reaches the repository mode. Definitions, types, instances, and canonical
constructions still require strict bottom-up ownership and readiness; only theorem
proof order may be staged. A public root is incomplete when a current-repository
declaration required by its formal Statement remains private or is missing from
the enclosing Scope export chain. Proof-only dependencies remain implementation
detail unless independently selected as stable API.
"""


_CONTENT_PLAN_COMPLETION_POLICY_BODY = """# content-plan-completion-policy

Read `get_current_node_contract` and `get_current_repo_completion_policy`. Treat
the contract's task completion mode as this task's terminal depth and the
repository completion mode as the node's eventual provider depth:

- interface_declared: declare the contract-required public interfaces and
  the formal Statement foundations needed to state and compile them. Those
  prerequisites must be public in the current node. Do not plan proof-only
  hidden helpers.
- graph_declared: declare every important definition, theorem statement, and
  intermediate lemma statement in the node's selected source scope. Stop
  theorem-like declarations at declared state unless existing release truth is
  already stronger.
- graph_proved: build bottom-up. Declare foundations, prove reusable helpers
  once their dependencies are accepted, and only then prove their consumers.
  A declared parent may be an intentional intermediate round, but it is not a
  terminal Content result while owned theorem-like work remains unproved.

The contract and source references determine which mathematical material this
node owns. The task target determines the required state for this contract
version. Definitions, types, instances, and canonical constructions must still be
declared bottom-up before any theorem statement that uses them; a shallower task
target changes theorem proof scheduling, not definition ownership or visibility.
Never lower a released state floor or rewrite a release-protected statement.
Before targeting `proved`, verify source proof stages and accepted dependency
closure; do not turn missing helpers into repeated unbounded parent retries.
Before Content completion, inspect the current-node public Statement closure and
use add-only visibility promotion for already-ready local prerequisites. Report
the task target, repository target, and remaining repository gap honestly in the
terminal result. A partial task is progress, not provider-ready truth, and cannot
justify another node's dispatch. Never raise interface_declared or graph_declared
to proof completion merely because a stronger state is possible.
"""


SKILL_DEFINITIONS: dict[str, LeanSkillDefinition] = {
    SkillKey.REPO_FORMAT_DISCOVERY.value: LeanSkillDefinition(
        name="repo-format-discovery",
        description="Choose a verified Adapter route or a searched Native route for one requirement repository.",
        group="repo-lifecycle",
        required_tool_groups=_groups(
            AppGroup.REPO_PREPARATION_INPUT_READ,
            AppGroup.REPO_PREPARATION_START_PREFLIGHT_READ,
            AppGroup.REPO_PREPARATION_REQUIREMENT_READ,
            AppGroup.WORKSPACE_OVERVIEW_READ,
            AppGroup.UPSTREAM_REPO_SEARCH,
            AppGroup.GITHUB_REPOSITORY_READ,
            SubmitGroup.REPO_FORMAT_DISCOVERY_SUBMIT,
        ),
        source_design_doc="dev_docs/implementation/repo_discovery_agent_surface_hardening",
        body=_body(
            "repo-format-discovery",
            "Choose exactly one repository format from concrete remote evidence while leaving deterministic compatibility facts to the backend.",
            (
                "Read `get_preparation_input`, then inspect only its allowed requirement refs with `list_preparation_requirements` or `get_preparation_requirement`; use the prompt refs only as navigation hints.",
                "Search real GitHub candidates with `search_github_lean_repositories`; inspect promising repositories and use `probe_github_lean_repo_candidate` for Lean/Lake, toolchain, manifest, tree, and module evidence. Use the narrower repository tree/file/code tools only when the probe leaves a concrete question.",
                "Choose Adapter only when one real upstream Lean/Lake project is mathematically relevant and plausible on the required boundary. Submit only git URL, optional immutable revision/subdir, evidence summary, and known risks. The terminal handler derives and verifies the exact commit, package, import module, Lean toolchain, and Mathlib pin.",
                "Choose Native when the bounded search found no suitable upstream. Submit a concise summary and concrete non-empty searched targets; do not construct rejected-candidate dossiers.",
                "If a submit returns typed field or compatibility issues, correct the actual target or route in the same AgentStep. Do not probe the schema with placeholders. Before retrying, recheck that the route still matches the inspected evidence.",
                "After one route submit is accepted, stop immediately and let the deterministic Apply step consume the verified receipt.",
            ),
            (
                "Official Mathlib is platform infrastructure, not an Adapter candidate.",
                "Do not clone or check out upstream repositories, initialize Lake files, mutate SourceCorpus mode, prepare materials, or write repository truth.",
                "Do not submit package names, import modules, resolved commits, toolchain facts, or other values owned by the backend probe.",
            ),
        ),
    ),
    SkillKey.COORDINATOR_REPO_EXPLORATION.value: LeanSkillDefinition(
        name="coordinator-repo-exploration",
        description="Use when reconciling the fixed initial exploration batch or selecting a later repository-level exploration batch.",
        group="coordinator",
        required_tool_groups=_groups(SubmitGroup.COORDINATOR_SUBMIT),
        source_design_doc="dev_docs/implementation/repo_discovery_agent_surface_hardening",
        body=_body(
            "coordinator-repo-exploration",
            "Use this skill on the callback from the Flow-owned initial exploration batch, or when later progress exposes a genuinely new repository-wide external question.",
            (
                "Read the repository goal, completion policy, SourceCorpus and SourceIndex overview, existing Resources, workspace providers, requirements, Lake dependencies, and MathlibIndex before deciding whether exploration is useful.",
                "For the initial callback, consume all resource, Lean-provider, and Mathlib outcomes in the fixed batch. Classify useful findings, no useful findings, and incomplete exploration separately; retain useful findings even when another category was incomplete.",
                "Do not submit another exploration batch merely to reconcile that initial callback. Enter the Coordinator next-action loop after classification.",
                "During later work, explore only after a new topic, major unresolved external dependency, failed candidate, repeated repo-wide Mathlib representation issue, or materially changed source direction.",
                "For a later batch, fill one to three of resource_objective, lean_provider_objective, and mathlib_objective with focused, non-overlapping, verifiable goals; add one short shared context_summary only when needed, call `submit_repo_exploration` once, and stop.",
                "On every callback, preflight resource candidates before requesting them, use a direct adapter requirement only for exact immutable verified Lean evidence, and do not duplicate MathlibIndex writes already performed by recon.",
            ),
            (
                "The initial batch is Flow-owned and fixed; later exploration is optional and selective.",
                "A local tactic failure, ordinary worker retry, or a single missing Mathlib lemma does not justify broad repository exploration.",
                "Each Coordinator AgentStep still submits exactly one terminal action.",
            ),
        ),
    ),
    SkillKey.REPO_RESOURCE_DISCOVERY.value: LeanSkillDefinition(
        name="repo-resource-discovery",
        description="Discover source-attributed supporting materials without acquiring or registering them.",
        group="resource",
        required_tool_groups=_groups(
            AppGroup.REPO_PREPARATION_INPUT_READ,
            AppGroup.REPO_COMPLETION_POLICY_READ,
            AppGroup.SOURCE_CORPUS_READ,
            AppGroup.SOURCE_INDEX_NAVIGATION_READ,
            AppGroup.SOURCE_MATERIAL_TEXT_READ,
            AppGroup.RESOURCE_LIBRARY_READ,
            AppGroup.RESOURCE_TARGET_PREFLIGHT_READ,
            AppGroup.EXTERNAL_RESOURCE_DISCOVERY_READ,
            AppGroup.EXTERNAL_THEOREM_SEARCH_READ,
            SubmitGroup.REPO_RESOURCE_DISCOVERY_SUBMIT,
        ),
        source_design_doc="dev_docs/implementation/repo_discovery_agent_surface_hardening",
        body=_body(
            "repo-resource-discovery",
            "Find trustworthy external supporting resources for one repository-level objective.",
            (
                "Read current preparation input, completion policy, SourceCorpus/SourceIndex and registered Resource truth first so existing material is not suggested again.",
                "Search with bounded metadata, then call exact resource inspection for each promising target. A search hit is not durable candidate truth. Use theorem search only for precise statement-level support.",
                "Read `material-boundary-classification` before deciding ownership. Use local_resource for supporting material owned here, provider_requirement for an independent reusable formal boundary, and inspect_later only for a real inspected target whose usefulness or ownership remains unresolved.",
                "For each retained target, submit only its locator, concrete support summary, handling, risks/gaps, and conditional consumer need/provider scope. The terminal handler re-inspects it and supplies canonical title, authors, kind, version, locator, and source URLs.",
                "Omit irrelevant, duplicate, inaccessible, or unreliable hits instead of submitting ignore objects. Keep at most five useful candidates in recommendation order.",
                "Use no_useful_findings with no candidates when none survives review. If the submit returns a typed field or inspection issue, correct the real target or judgment in the same AgentStep; never use placeholder locators to probe schema. After an accepted submit, stop.",
            ),
            (
                "Do not acquire, normalize, draft, or register Resources.",
                "Do not create requirements, providers, nodes, contracts, declarations, or local candidate registries.",
                "Do not return full HTML, PDF, TeX, or unbounded abstracts.",
            ),
        ),
    ),
    SkillKey.MATERIAL_BOUNDARY_CLASSIFICATION.value: LeanSkillDefinition(
        name="material-boundary-classification",
        description="Use when classifying a material target as local supporting material or an independent provider responsibility.",
        group="resource",
        source_design_doc="dev_docs/implementation/four_case_exploration_material_followups",
        body="""# Material Boundary Classification

Use this Skill before recommending, requesting, or closing out an external material target.

## Classify Ownership

Choose exactly one current handling:

- local resource: supporting explanation, examples, data, background, or proof guidance whose mathematical formalization remains owned by the current repo;
- provider requirement: a separately nameable and reusable theorem or theory package that should expose a small stable Lean API to this consumer;
- inspect later: locator, scope, ownership, or Lean evidence is still insufficient;
- ignore: duplicate, irrelevant, inaccessible, or unreliable for the current objective.

Record the concrete consumer need and a classification reason. For a provider boundary, also state provider scope, minimal required interfaces, any existing-Lean signal, and whether focused Lean-provider discovery is warranted. For a local Resource, state its narrow resource role and what formalization responsibility remains in this repo.

## Preserve One Canonical Owner

Do not place an entire independent theory in a consumer Resource while also requesting a provider for the same responsibility. A narrow excerpt is allowed only for a genuinely different supporting use that is explained in the Resource README and does not reassign provider proof ownership.

Classification is advisory evidence for the caller's next action, not permission to create a Resource or requirement from this Skill. Use only the role-appropriate submit or lifecycle Skill after classification is complete.
""",
    ),
    SkillKey.FAITHFUL_MATERIAL_PRESERVATION.value: LeanSkillDefinition(
        name="faithful-material-preservation",
        description="Use when converting acquired source or resource artifacts into durable readable material without changing their truth.",
        group="resource",
        source_design_doc="dev_docs/implementation/four_case_exploration_material_followups",
        body="""# Faithful Material Preservation

Use this Skill whenever acquired material is copied, extracted, normalized, or organized into durable SourceCorpus or Resource content.

## Preserve Source Truth

Keep the acquired original when access permits. Produce normalized text by mechanical decoding, extraction, line-ending cleanup, or faithful structural organization. Preserve the source's claims, hierarchy, notation, assumptions, attribution, and relevant surrounding context. A normalized entry is source truth for downstream range reads; it is not an agent summary, commentary, formalization plan, or newly proposed proof.

When only a range, section, appendix, or selected file is retained, record the exact included scope and what was omitted. Never present a partial extraction as the complete work. Keep figures, tables, appendices, source archives, or other supporting artifacts under their declared logical locations when they materially affect interpretation.

## Record Mechanical Intervention

Record canonical provenance, version or date, license and access conditions, acquisition route, original-to-normalized mapping, reading order, extraction or OCR limits, and unreadable regions. If text is corrected beyond mechanical normalization, preserve the original wording and add a separate correction ledger that identifies every change and reason.

Deterministic manifests and checks verify paths, bytes, readability, and required records. They do not certify mathematical fidelity. Before declaring material ready, compare the normalized entry against the retained original or canonical source and repair omissions, invented connective prose, or altered mathematical meaning.
""",
    ),
    SkillKey.REPO_LEAN_PROVIDER_DISCOVERY.value: LeanSkillDefinition(
        name="repo-lean-provider-discovery",
        description="Discover and verify existing Lean repositories that may serve as independent providers.",
        group="workspace",
        required_tool_groups=_groups(
            AppGroup.REPO_PREPARATION_INPUT_READ,
            AppGroup.REPO_COMPLETION_POLICY_READ,
            AppGroup.SOURCE_CORPUS_READ,
            AppGroup.SOURCE_INDEX_NAVIGATION_READ,
            AppGroup.SOURCE_MATERIAL_TEXT_READ,
            AppGroup.WORKSPACE_OVERVIEW_READ,
            AppGroup.WORKSPACE_PROVIDER_CATALOG_READ,
            AppGroup.WORKSPACE_REQUIREMENT_READ,
            AppGroup.LAKE_DEPENDENCY_READ,
            AppGroup.UPSTREAM_REPO_SEARCH,
            AppGroup.GITHUB_REPOSITORY_READ,
            SubmitGroup.REPO_LEAN_PROVIDER_DISCOVERY_SUBMIT,
        ),
        source_design_doc="dev_docs/implementation/repo_discovery_agent_surface_hardening",
        body=_body(
            "repo-lean-provider-discovery",
            "Find importable Lean repository candidates for one repository-level mathematical objective.",
            (
                "Read existing providers, requirements, and dependencies before remote search.",
                "Search GitHub broadly without relying only on `language:lean`; admit a bounded candidate when metadata topics/languages, lean-toolchain, Lake/leanpkg manifest, Lean files, or README package evidence supports it.",
                "Inspect only a small relevant pool with GitHub probe and focused tree/code/file reads. Use these reads to judge mathematical capability and identify precise relevant declaration names or clues; do not copy technical probe fields into the terminal call.",
                "Recommend direct_adapter_requirement only when the inspected target has a real Lean/Lake project, a relevant declaration clue, and no known gap. Use generic_requirement for an independent provider need whose exact route still needs discovery, or inspect_later for a real probed target needing more evidence.",
                "Submit only Git URL, optional immutable revision/subdir, capability summary, relevant declarations, recommendation, gaps and risks. The terminal handler repeats the canonical probe and derives normalized URL, exact commit, package, modules, toolchain, manifest and Lean-source evidence.",
                "Omit unsuitable candidates. Route official Mathlib evidence to RepoMathlibRecon instead of returning it as any provider candidate.",
                "If the submit returns a typed field or probe/readiness issue, correct the real target or recommendation in the same AgentStep. Never use placeholder repositories or schema-probe calls. After an accepted submit, stop.",
            ),
            (
                "Do not clone, attach, import, or mutate remote repositories.",
                "Do not create requirements or change Lake dependencies.",
                "Keywords, stars, README claims, and repository names are not substitutes for Lean declaration evidence.",
            ),
        ),
    ),
    SkillKey.REPO_MATHLIB_RECON.value: LeanSkillDefinition(
        name="repo-mathlib-recon",
        description="Curate checked repository-wide MathlibIndex support for a focused exploration objective.",
        group="mathlib",
        required_tool_groups=_groups(
            AppGroup.REPO_PREPARATION_INPUT_READ,
            AppGroup.REPO_COMPLETION_POLICY_READ,
            AppGroup.SOURCE_CORPUS_READ,
            AppGroup.SOURCE_INDEX_NAVIGATION_READ,
            AppGroup.SOURCE_MATERIAL_TEXT_READ,
            AppGroup.MATHLIB_INDEX_READ,
            AppGroup.MATHLIB_INDEX_WRITE,
            AppGroup.MATHLIB_SEMANTIC_SEARCH,
            AppGroup.MATHLIB_NAVIGATION,
            SubmitGroup.REPO_MATHLIB_RECON_SUBMIT,
        ),
        source_design_doc="dev_docs/implementation/repo_discovery_agent_surface_hardening",
        body=_body(
            "repo-mathlib-recon",
            "Find and record checked Mathlib modules and declarations that are reusable across the current repository.",
            (
                "Read the preparation objective and relevant SourceCorpus/SourceIndex context without copying their contents into the result.",
                "Use `$mathlib-index-first-recon` to reuse current repository knowledge before any broader search.",
                "For an actual index gap, use `$mathlib-semantic-search-navigation`; for each verified reusable entry, use `$mathlib-index-entry-curation`.",
                "Re-read every objective-relevant module or declaration from the current MathlibIndex after recording. Terminal submit references only canonical indexed names plus unresolved questions and usage notes; it does not report created/reused operation history.",
                "If terminal validation reports an unindexed name, record or correct it and retry in the same AgentStep. After an accepted submit, stop.",
            ),
            (
                "Do not write node hints, node dependencies, declaration dependencies, Resources, requirements, contracts, declarations, or Lean code.",
                "Do not record speculative search results or unrelated APIs.",
            ),
        ),
    ),
    SkillKey.NODE_CONTRACT_DESIGN.value: LeanSkillDefinition(
        name="node-contract-design",
        description="Use when a Coordinator must design or update the semantic contract of a Scope or Content node.",
        group="node",
        required_tool_groups=_groups(
            AppGroup.NODE_CONTRACT_READ_BY_NODE,
            AppGroup.NODE_CONTRACT_TEXT_WRITE_BY_NODE,
            AppGroup.NODE_CONTRACT_TASK_TARGET_WRITE_BY_NODE,
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
                "A new Content contract version defaults its task completion mode to the repository mode. Use `set_node_contract_task_completion_mode` only on an open Content contract when a deliberate theorem-statement staging boundary is justified.",
                "Do not lower a task target to defer definitions, types, instances, or canonical constructions. Before choosing interface_declared or graph_declared under a deeper repo target, verify that every definition used by the staged statements already comes from a repo-ready visible boundary or is fully declared in this task's bottom-up layers.",
                "Record that a shallower task target is partial progress: it cannot become another node's dependency, feed a Scope export, or close its parent Scope until a later contract version reaches the repo target.",
                "Before adding or changing a source ref, call `validate_source_range` and `preview_source_ref`, read the excerpt, and confirm that it supports the ref reason, target interface, and node boundary; a structurally valid range is not semantic evidence.",
                "Treat SourceCorpus locators such as `article/sections/...` as semantic-tool identities, not paths relative to the current workdir.",
                "Attach durable source or resource context to a target node with `add_node_material_ref` or remove stale entries with `remove_node_material_ref`.",
                "Record visible same-repo or provider node dependencies with `add_node_dep`, and remove stale dependency entries with `remove_node_dep`.",
                "Record target-node Mathlib module or declaration hints with `add_node_mathlib_module_hint` and `add_node_mathlib_decl_hint` after the candidates are verified or recorded in the repo MathlibIndex.",
                "Prepare content tasks with enough objective, material, dependency, and interface context for node-local work.",
                "Name canonical owners for shared domain types, indices, instances, equivalences, dependent families, and constructions. Provider contracts expose capability shapes translated from consumer needs, never consumer-private implementation details.",
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
            AppGroup.REPO_PUBLIC_CLOSURE_READ,
            AppGroup.REPO_PUBLIC_VISIBILITY_WRITE,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body=_body(
            "scope-export-interface-curation",
            "Use this skill when closing a scope node, selecting public declarations from ready children, binding scope interfaces, checking projection/readiness, or preparing a scope contract commit.",
            (
                "Read required interfaces and current export candidates with `list_node_interfaces`, `list_scope_export_candidates`, and `list_scope_exports`.",
                "Preserve historical public export chains as compatible anchors; append new exports without silently replacing a released boundary.",
                "Choose exports that belong to the scope public view and write them with `add_scope_export` or exact-reference `remove_scope_export`.",
                "Inspect the Scope or repository formal Statement closure. Every current-repository declaration required to state a selected public root must be public and exported through each enclosing Scope.",
                "Use `revise_content_decl_visibility` with the observed visibility and an audit reason for one reviewed Content declaration, or `promote_public_statement_closure` for an add-only repair whose exact declarations are already anchored by active committed Content contracts. Promotion creates and commits only fresh add-only intermediate Scope revisions when those Scopes have no open edits; it writes the requested target Scope's current candidate but leaves that target open. Resolve an intermediate-open or intermediate-uncommitted blocker through its owner instead of merging unrelated edits.",
                "Before making a declaration private, remove or revise every active/open interface binding, Scope/Main export, Node dependency, public Statement consumer, admitted round consumer, and stable Release boundary that still requires it. Proof-only helpers may remain private only when no admitted consumer or other boundary requires them.",
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
                "Apply `faithful-material-preservation` before using `normalize_source_text_material`; normalization is mechanical and must not summarize or rewrite source truth.",
                "Treat acquisition and extraction outputs as intermediate material; preserve an author's existing TeX tree and file boundaries when they are already coherent.",
                "Keep original material and readable extraction distinct, and explain their mapping and extraction limits in the SourceCorpus README.",
            ),
            (
                "Do not register ResourceLibrary entries from source preparation.",
                "Do not write SourceIndex, NodeContract, or Lean files.",
                "Do not rely on raw PDF, HTML, or image-only material as the only prepared source corpus content.",
            ),
        ),
    ),
    SkillKey.SOURCE_CORPUS_FAITHFUL_PREPARATION.value: LeanSkillDefinition(
        name="source-corpus-faithful-preparation",
        description="Guides SourceCorpusPrepare agents preserving supplied material and documenting the corpus boundary without inventing formalization answers.",
        group="source",
        required_tool_groups=_groups(
            AppGroup.SOURCE_CORPUS_READ,
            AppGroup.SOURCE_ACQUISITION,
            SubmitGroup.SOURCE_CORPUS_PREPARE_SUBMIT,
        ),
        source_design_doc="dev_docs/implementation/four_case_exploration_material_followups",
        body=_body(
            "source-corpus-faithful-preparation",
            "Use this skill to turn acquired artifacts into a preservation-first SourceCorpus without designing the later formalization.",
            (
                "Read `faithful-material-preservation` and keep an existing author TeX tree, macro files, bibliography, assets, sections, theorem statements, and proof structure intact.",
                "Maintain a root README with source/author/version/canonical locator, file inventory, reading order, main material entry, original-to-extracted mapping, extraction/OCR/correction status, missing pages/assets/bibliography, and the included/omitted source boundary.",
                "Use `article/`, `original/`, `assets/`, and `supplementary/` only when they match the source; do not force a coherent author tree into an artificial `main/` layout.",
                "Record partial sections or transcripts exactly and add a supplementary correction ledger for any non-mechanical correction.",
                "Preserve supplied Lean specifications, formal targets, solutions, and proof references as source truth; a supplied file containing `sorry` remains an input constraint rather than a completed project artifact.",
                "Run `scan_source_corpus` and `check_source_corpus_draft`, repair authorized preparation failures, then submit prepared or blocked exactly once.",
            ),
            (
                "Do not invent targets, answers, proofs, expected node trees, Lean probes, or audit hints, and do not present Agent-authored summaries or commentary as supplied source truth.",
                "Do not strengthen or weaken statements, drop assumptions, merge separate conclusions, or invent connective proof text.",
                "Do not build SourceIndex, root interfaces, NodeTree, DeclGraph, Resources, or Lean project files.",
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
                "Pass the acquisition kind and MIME returned by acquisition through to extraction; do not guess from the filename.",
                "Apply `faithful-material-preservation` before normalizing readable text with `normalize_resource_text_material`.",
                "Treat acquisition and extraction outputs as intermediate material; place canonical originals under `original/`, faithful readable text under `normalized/`, and auxiliary material under `assets/` or `supplementary/`.",
                "Maintain `README.md` with identity, provenance, license/access, material mapping, reading order, selected scope, consumer need, extraction limits, correction status, and ownership.",
                "Use `refresh_resource_draft_manifest` only when multiple validated normalized outputs require an explicit canonical entry; ordinary successful extraction refreshes it automatically.",
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

Use this Skill after the caller has identified one precise paper, webpage, or local target and has applied `material-boundary-classification`.

## Preflight

1. Call `get_material_context` and confirm that current source and accepted Resources do not already cover the need.
2. Call `normalize_resource_target` for the proposed target.
3. Call `find_duplicate_resource` with the normalized target.
4. If accepted source or Resource material already covers the target, do not dispatch a duplicate request. Return to the caller's current workflow and use the stable existing reference.

## Submit

Call `submit_resource_request` only when the target is explicit, narrow, trustworthy enough to inspect, and accompanied by requested use, a concrete consumer need, and a concise context summary. Use supporting material for a local-Resource candidate, formal dependency for a provider-shaped need that Curator must confirm, and unknown only when acquisition is necessary to resolve ownership.

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
- For a local Resource, verify the finalized Resource, canonical entry, classification reason, resource role, and remaining consumer formalization scope. Record it through the caller's role-appropriate semantic material mutation only when that ownership matches current truth.
- For an external repository result, verify its consumer need, provider scope, required-interface hint, current-repo relation, and existing-Lean signal. Do not treat the target as a Resource. A Coordinator may return to its provider-dependency action; a node-scoped caller must carry the need to its own Coordinator-facing blocked or completion boundary.
- For a rejected result, record the reason as an unresolved direction when relevant, but never use the rejected target as evidence.

Do not name caller-private material-write tools in this shared procedure. Apply only mutations authorized by the current role and Instruction.

## Postcondition

Closeout is complete when the terminal outcome has been checked against current truth, every authorized durable material change has been made, and any external-repository or rejected boundary is explicit.

Then stop using this Skill and return to the caller's next-action loop in the same AgentStep. A ResourceRecon caller may next use `resource-request-submission` for a different precise unresolved target, but it must not request anything before this closeout is complete and must never repeat a target already requested by the same recon Flow.
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
                "Apply `faithful-material-preservation`; place originals in `original/`, faithful readable text in `normalized/`, and auxiliary files in `assets/` or `supplementary/`.",
                "Write README content that identifies title, authors, version/date, source provenance and canonical locator, license/access, original-to-normalized mapping, canonical reading order, selected scope and consumer need, extraction/OCR limits, correction status, and supporting-material ownership.",
                "If no original is retained, state the exact reason. If any correction is made, add a supplementary correction ledger.",
                "Ensure the deterministic manifest names the intended canonical normalized entry; use `refresh_resource_draft_manifest` to resolve multiple readable outputs.",
                "Run `check_resource_draft` and repair failures within your authority.",
                "Call `submit_local_resource_created` only after the draft is coherent.",
            ),
            (
                "Do not use this skill for duplicate, external-repo-required, or rejected outcomes.",
                "Treat formal_dependency as strong provider evidence rather than an irreversible classification; choose a local Resource only when inspected truth shows narrow supporting material and record the corrected ownership in the README and submission.",
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
3. Estimate likely Lean scale from important source definitions, lemmas, proof stages, consumers, and expected Lean-specific helpers. Declaration count is context, never a mechanical split threshold.
4. Use the current mode policy to choose the required graph granularity.
5. Choose Scope nodes for broad mathematical regions and Content nodes for coherent declaration work.
6. Make sibling ownership disjoint and preserve protected root interfaces.

Before assigning theorem work, enumerate the definition frontier visible in Source,
the current graph, and known consumers: domain types and subtypes, indices,
structures/classes, instances, canonical equivalences, and dependent constructions.
Assign each shared object to its lowest coherent semantic owner rather than the first
consumer that happens to need it. Statement-level definitions must be repo-ready and
visible before an upper Content task can depend on them; proof-only theorem support
may follow the selected theorem-proof schedule. If an upper consumer already owns a
duplicate representation, repair ownership instead of preserving both definitions
with casts or one-off bridges.

## Decide Where Missing Work Belongs

A blocked Content result does not by itself justify a new node. After recovering the authoritative consumer and dependency frontier, choose the smallest coherent action:

1. continue the current Content node when the missing tracked work is naturally inside its boundary and scale;
2. reuse an existing Content node when that node already owns the relevant Source or mathematical region;
3. create a new ordinary Content node only when the work is an independently meaningful mathematical package with a stable boundary;
4. repair an existing interface, declaration route, or contract when the mismatch comes from semantic drift and a new node would only preserve the drift behind a one-off wrapper.

Treat missing work as a coherent package when it has layered definitions or lemmas, multiple consumers, a new Source/Resource/provider responsibility, a boundary outside the current contract, a change to core representation/index/typeclass/proof architecture, or unknown dependency depth. Shared type, index, instance, equivalence, or dependent-family architecture normally belongs to a lower foundation/provider package rather than a consumer-local repair. One signal may be decisive when it changes ownership; a large declaration count alone is not. A few ordered local helpers with a clear semantic boundary remain current-node work when they need no new material/provider, create no stable cross-node API, and do not change the node's core architecture.

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
            AppGroup.REPO_PUBLIC_CLOSURE_READ,
            AppGroup.REPO_PUBLIC_VISIBILITY_WRITE,
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

Call `inspect_public_statement_closure` for the Scope before commit. A selected public theorem or definition must expose its current-repository formal Statement dependency closure through the Scope chain; proof-only dependencies remain private. Use add-only promotion only for already-ready declarations. Then call `commit_scope_contract` only after required children are complete, exports and bindings satisfy the contract, and the close view reports stable projection and readiness. If the gate rejects, repair only Coordinator-owned semantic state and re-read the close view.

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

For each current result, identify the node path, ready/blocked/failed outcome, returned contract version, task target,
repository target, remaining repository gap, summary, reason, and any current state that must be inspected before
commitment. A ready partial-task result establishes progress only; do not treat it as a provider-ready boundary.

## Inspect Current State Selectively

Read the current node contract. When a result is suspicious or incomplete, use `get_node_decl_graph_index`, `list_node_decls`, and `inspect_node_decl` only for declarations that determine the outcome.

When a blocked result says that missing mathematical work may cross the current Content boundary, or before assigning that work to a different Content node, private consumer inspection is mandatory. Use `inspect_node_decl` for the affected revision and recover its accepted statement, Proof NL route, change/round summary, and declaration dependencies. If the primary projection is insufficient, use `read_visible_decl_lean_file` for the smallest necessary declaration-owned range. Inspect the actual signatures of existing declarations named in the blocker. The blocked reason is an index into authoritative truth, not sufficient contract authority by itself.

For blocked or failed results, classify the concrete consequence without solving it inside closeout: a Source-visible stage that should already have been planned, missing source or Resource, missing Mathlib support, current-node Lean-emergent work, work owned by another existing node, a coherent new mathematical boundary, provider need, incorrect contract, or Scope/interface work. Record the evidence and a candidate ownership branch, but make the structural choice only in the Coordinator next-action loop. If the same consumer must not be retried until a dependency package is repaired, route to `coordinator-blocked-consumer-replan` after closeout.

For a ready result, audit contract coverage before accepting its consequences: selected source stages and success criteria, required interface bindings, task-target declaration depth, public Statement closure, and an honest remaining repo gap. When the result is intended to discharge another Content node's dependency, inspect every actual bound public declaration needed by known consumers and re-read the original private consumer. Compare exact objects/types, indices, parameters, assumptions, conclusion direction, equivalence orientation, coercions, dependent-family/instance convention, and whether several low-level facts compose directly. The ready result establishes its own task contract; it does not establish consumer applicability merely by name, summary, or dependency reason, and a remaining repo gap also prevents provider readiness.

## Commit Every Reviewed Result

Call `commit_content_contract` once for each reviewed terminal result. Write a concise Coordinator summary that states what was established, the accepted outcome, the resulting boundary, and any unresolved prerequisite.

Check the returned finalize view. If a claimed ready result fails its deterministic gate, do not weaken the gate or hide the inconsistency. Identify it as child-result or control-plane inconsistency unless Coordinator-owned semantic state can legitimately repair it.

Collect consequences as candidates, not a persisted or immutable action list.

## Postcondition

Every terminal result in the callback batch has been reviewed, every reviewable result is finalized and committed, and every deterministic inconsistency is explicit. Stop using this Skill and return to the Coordinator next-action loop in the same AgentStep.

Do not call a normal Coordinator submit from inside this closeout.
""",
    ),
    SkillKey.COORDINATOR_BLOCKED_CONSUMER_REPLAN.value: LeanSkillDefinition(
        name="coordinator-blocked-consumer-replan",
        description="Use after closeout when a blocked consumer may require dependency-closure repair across a Content boundary.",
        group="coordinator",
        required_tool_groups=_groups(
            AppGroup.DECL_GRAPH_READ_BY_NODE,
            AppGroup.NODE_TREE_READ,
            AppGroup.NODE_CONTRACT_READ_BY_NODE,
            AppGroup.VISIBLE_DECL_LEAN_FILE_READ,
            AppGroup.VISIBLE_NODE_PUBLIC_DECL_READ,
            AppGroup.IMPORTED_REPO_PUBLIC_DECL_READ,
            AppGroup.SOURCE_INDEX_NAVIGATION_READ,
            AppGroup.MATHLIB_INDEX_READ,
            AppGroup.LAKE_DEPENDENCY_READ,
            AppGroup.WORKSPACE_PROVIDER_CATALOG_READ,
        ),
        source_design_doc="dev_docs/implementation/bottom_up_provider_contract_and_canonical_decl_optimization/03_Coordinator与ContentPlan工作流设计.md",
        body="""# Coordinator Blocked Consumer Replan

Use this Skill only after the terminal Content result has been reviewed and
committed. It rebuilds the complete dependency frontier before any retry or new
provider contract; it does not itself mutate the node tree or dispatch work.

## Freeze The Consumer Retry

Do not immediately rerun the affected consumer or add only the helper named by
the last error. Identify the exact consumer node, declaration, revision, sibling
consumers already known to need the same capability, and the retry condition that
must be satisfied first.

## Rebuild From Authoritative Truth

1. Inspect the private consumer's exact accepted Statement, Proof NL route, typed
   dependencies, round/change history, and current blocker. Read the smallest
   declaration-owned Lean range only when the structured record is insufficient.
2. Enumerate the complete known frontier: Source-visible mathematical stages;
   domain types and indices; representation and equivalence orientation;
   instances, coercions, and dependent families; Mathlib facts; existing same-repo
   and attached-provider APIs; and the current consumer plus known siblings.
3. Inspect exact provider signatures. Distinguish truly missing work from a
   visibility gap, stale revision, incompatible assumptions/indices/conclusion,
   duplicated canonical construction, or low-level APIs that cannot compose in
   the consumer's fixed context.
4. Classify the gap as a proof-local helper, a current-node shared tracked Decl,
   an existing provider repair, a coherent lower provider/foundation package, a
   source/statement correction, or an external provider requirement.

## Define A Complete Provider Package Once

When a package boundary is justified, define its canonical ownership, stable
public interfaces, expected internal definition/theorem layers, exact consumer
use shape, relevant sibling uses, rough declaration scale, task/repo completion
boundary, and stopping condition. Translate private consumer evidence into a
capability contract using visible canonical lower refs; never expose the private
consumer implementation to the provider Agent. Definitions, types, instances,
and canonical constructions must be completed bottom-up before theorem consumers.

Return to `coordinator-node-decomposition`, `node-contract-design`, dependency
readiness, and Content dispatch for actual mutations. Resume the consumer only
after the provider is committed at the repository target and closeout coverage
confirms its public interface and exact consumer shape. If provider work exposes
another gap, rebuild this full frontier again instead of appending one new helper
and retrying immediately.

## Postcondition

The complete known frontier and one ownership branch are explicit, and the
consumer remains paused until provider coverage succeeds. Return to the
Coordinator next-action loop and use the existing decomposition, contract,
dependency, or provider workflow for the selected mutation.

## Boundaries

- Do not turn proof difficulty alone into a new node or repository.
- Do not infer a provider theorem from only the blocker summary.
- Do not give provider work direct access to consumer-private declarations.
- Do not preserve duplicate core representations with accumulating casts,
  `HEq`, instance transport, or one-off wrappers when ownership should be repaired.
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

For each candidate, derive the Source-visible dependency frontier from Source, current graph truth, and known consumer shapes. Enumerate definitions, types, indices, structures/classes, instances, canonical equivalences/constructions, and theorem stages separately. Definitions needed by an upper Statement must already have a lowest coherent owner and be repo-ready and visible; a shallower theorem task target does not relax that definition frontier. For graph_proved theorem work, require each known lower theorem stage to have an owner and a usable declaration that is ready to the required state, visible through the consumer boundary, and signature-compatible. Plan an unresolved lower stage first. For graph_declared and interface_declared theorem work, require only the depth selected by the task contract; do not over-raise the target to proved.

Distinguish Source-visible gaps from Lean-emergent gaps. A dependency already evident in Source, the contract, or current graph belongs in the frontier and must not be deferred as implementation discovery. A representation or elaboration helper first exposed by formalization may return as a precise Content blocker; it does not retroactively make an upper dispatch ready.

Visibility and proof state are not enough for a consumer-facing dependency. Compare the available declaration's assumptions, parameter and index representation, and conclusion direction with the upcoming consumer shape; also check equivalence orientation, coercions, and dependent-family/instance convention. Verify that several intended low-level declarations can actually compose under the same canonical objects. Treat a proved public declaration with an incompatible shape as unresolved rather than as readiness evidence.

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
2. Read its task target, repository target, and remaining gap. A partial committed task is never a runnable dependency provider; dispatch its next contract version or a different lower provider before any consumer that depends on it.
3. Preview every owned source ref with `preview_source_ref`; confirm each excerpt agrees with the current goal, boundary, interfaces, and recorded reason. Source locators are semantic-tool identities, not workdir-relative file paths.
4. Confirm that context refs have not been used as owned contract evidence.
5. Confirm the contract names its expected important declarations or source stages, a rough scale, and the boundary that prevents unbounded helper expansion.
6. Confirm every definition, type, instance, and canonical construction used by planned Statements is already repo-ready in a lower visible provider or is owned by earlier bottom-up layers of this task. Do not use graph_declared staging to forward-reference an unfinished definition node.
7. When work originated from another Content node's blocker, confirm that the contract was rebuilt through `coordinator-blocked-consumer-replan` from the authoritative private consumer and known siblings rather than only from the blocker summary. Its interface summary and statement hint must identify the consumer anchor, expected input/output shape, canonical lower refs, and conditions that must not drift. A vague request such as "provide the missing theorem" is not dispatch-ready.
8. Confirm that declared node dependencies expose public declarations whose actual assumptions, indices, conclusions, representations, and composed use fit the planned work.
9. Call `check_content_task_admission` for each candidate.
10. Use `list_runnable_content_nodes` for orientation when several nodes may run.
11. Call `check_content_node_batch` for the exact proposed batch.
12. If a ref is misplaced or admission fails, repair Coordinator-owned structure or contracts and return to the next-action loop. Do not submit a partially invalid batch.

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
            AppGroup.REPO_PUBLIC_CLOSURE_READ,
            AppGroup.REPO_PUBLIC_VISIBILITY_WRITE,
            SubmitGroup.COORDINATOR_SUBMIT,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Coordinator Repository Ready Lifecycle

Use this Skill only when all expected Content work is reconciled, required Scopes are committed, protected interfaces and public exports appear satisfied, dependencies are stable, and proof-policy obligations match the repository work mode.

## Verify Readiness

1. Re-read `get_current_repo_run_context`, `get_preparation_input`, `get_node_tree`, and current Lake dependencies. Confirm the bound release baseline still matches current truth.
2. Call `get_scope_close_view` for Main and any relevant unverified Scope.
3. Check Main interfaces and exports against protected root contracts.
4. Treat Main as the root Scope, not as a separate repository-only boundary. Call `inspect_public_statement_closure` with `boundary=scope` and `scope_path=Main`. Every selected Main export's current-repository formal Statement dependencies must be public in their Content nodes and exported through every intervening Scope through Main. Apply `scope-export-interface-curation` before any repair: promotion starts from active committed Content heads, refuses to absorb an intermediate Scope's open edits, and leaves the requested target Scope open for owner review. The same operation applies to any other Scope and stops at that Scope rather than propagating to its parent.
5. Call `get_repo_ready_node_view`. It is a lightweight structural intent view: it checks committed Main/active contracts/open requirements/publication policy but deliberately performs no projection mutation or Lean build.
6. If the structural view has blockers, repair only the owning semantic state and return to the next-action loop. Do not poll for a hidden heavy preview.

## Submit

Call `submit_repo_ready` when the structural intent view is clear. This submit expresses candidate intent only; the following deterministic Flow step owns the single authoritative closure/projection/build/candidate audit. Audit failure returns a candidate-blocked callback to this same Coordinator session. Audit success marks ready under manual policy or continues Release under on-completion policy. If the submit is rejected, repair its immediate runtime/ownership issue; if accepted, stop immediately.

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
                "For `record_mathlib_decl`, pass the exact declaration name and optional summary/source only; checked navigation derives module, kind, signature, and snippet.",
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
        required_tool_groups=_groups(
            AppGroup.NODE_CONTRACT_READ_CURRENT,
            AppGroup.REPO_COMPLETION_POLICY_READ,
        ),
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
- the definition frontier and canonical owner for every shared type, index, instance, equivalence, dependent family, matrix/object construction, or coercion convention used by planned Statements;
- whether a staged theorem-statement task is justified and how a later contract version will close the reported repository gap.

For each construction, first search current active declarations, visible public
providers, contract/interface refs, and Mathlib. Keep it local only when it serves
one declaration and disappears entirely inside that proof. Use a tracked private
Decl when two or more current-node declarations must share its exact term or when
it fixes dependent indices, equivalence orientation, instance choice, or
representation architecture. Make it public only when another node's Statement
must reference it or the contract selects it as stable API. Prefer one canonical
lowest coherent owner plus exact dependencies over several mathematically equal local
reconstructions.

## Classify A Lean-Emergent Gap

When formalization exposes a missing item that was not already Source-visible, inspect in this order:

1. If Mathlib already supplies the required semantics, repair the query, import, or Mathlib dependency.
2. If a current repository node or attached provider already supplies it, repair visibility, interface fit, or the declaration dependency.
3. Keep a small helper in this Content node only when it has a clear local semantic boundary, a bounded route of a few provider-before-consumer rounds, no new Source/Resource/provider responsibility, no stable cross-node API, and no change to the node's core representation, index discipline, typeclass design, or proof architecture.
4. Report blocked for Coordinator ownership when the gap forms a coherent package: layered definitions or lemmas, multiple consumers, new material/provider responsibility, work outside the contract, an architectural representation change, or dependency depth that is not yet bounded.

No declaration-count cutoff decides between a helper and a package. Record the consumer and frontier anchors, why the item is local or boundary-external, and the checks that ruled out Mathlib and visible providers before choosing the route.

## Reassess Before Continue

Re-read this Skill after every preparation callback, every round terminal callback, and before creating each new round. Reassessment is mandatory; replacing the strategy is not. Reassess when the source route changed, a blocker exposed a missing dependency stage, the declaration graph materially outgrew the strategy's scale assumptions, an interface or public boundary changed, repeated parent retries did not close known blockers, or current graph truth contains superseded branches the strategy no longer explains.

The strategy objective and rationale should record the selected Source route, bottom-up/top-down choice and reason, known major dependency stages, canonical construction owners and exact consumers, intended public/interface output and consumer shape, scope and scale assumptions, and any explicit state-only intermediate closure plan. Top-down staging may freeze theorem Statements only after their definitions are available; it cannot move a definition out of its lower provider or allow a forward reference. If the required lower work forms a package outside the current Content boundary, close out current truth and report the boundary decision to the Coordinator instead of expanding the strategy without limit.

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

When a SourceIndex ref or source tool supplies an exact inclusive line range, pass that exact range to `read_source_range`. Do not round or pad its end line as a discovery probe. If the indexed range is insufficient, first locate or validate a separate authorized range, then read that range exactly.

## Interface Fit

For every change, state which already accepted lower declarations or Mathlib facts it consumes and which upper declaration, public interface, or contract goal it must serve. Prefer repairing a private declaration whose source-derived interface does not connect over adding an unexplained bridge. Create a tracked bridge/helper only when it has independent mathematical meaning, a clear source/Lean role, or multiple real consumers.

When a contract statement hint gives a consumer-side Lean shape, copy the relevant objects, binders, assumptions, index representation, and conclusion direction into the change objective. Do not add assumptions the consumer cannot supply, replace a required object/index with an easier one, or weaken the required conclusion. The worker may choose the exact Lean header inside that semantic boundary, but a qualified interface name fixes the compiler-confirmed Lean full name and therefore the required namespace.

## Target Readiness

Before choosing `target_state=proved`, check whether the source proof stages and known dependency closure are available. If important lower source-derived declarations are still missing, either plan those bottom-up first or, in the narrow top-down case, declare the stable parent statement without asking the same round to prove it. A blocker first discoverable only through Lean implementation may justify a later helper round; a dependency visible from source/graph truth should be planned before parent proof dispatch.

If satisfying the target now requires a coherent package outside the current Content boundary, follow the strategy's Lean-emergent gap classification. Do not grow the round or strategy indefinitely. Close out the current round when necessary and submit a precise Content blocker for Coordinator ownership review.

## Canonical Constructions And Local Helpers

Before creating a definition, instance, equivalence, embedding, dependent family,
matrix block, inverse/reindex object, or reusable helper, search current active
Decls, visible public providers, contract refs, and Mathlib for an existing
canonical owner. Keep a helper local only when one declaration uses it, it appears
in no other Statement, and it leaves no reusable dependent term or representation
choice. Track it as a private Decl when multiple declarations must share the exact
term, it fixes indices/instances/equivalence orientation, or it has independent
mathematical meaning; make it public only for a real cross-node Statement/API need.

Use a named instance Decl only for a unique, stable, non-overlapping construction.
When typeclass search could be ambiguous or cyclic, use a named definition and
install it explicitly with `letI` in each consumer, or encapsulate the complete
dependent mathematical object behind a canonical definition. If a canonical
provider and its consumer are both planned, preserve the anticipated edge and
split them into provider-before-consumer rounds. Do not omit the edge or replace
the shared object with `HEq`, accumulating casts, repeated `Subsingleton.elim`, or
another locally regenerated representation merely to pass validation.

## Create Changes

Use `plan_create_decl` for new declarations. Each create change should have:

- a flat `Decl.name` and clear kind; the name is one Lean module filename segment, so it cannot contain dots or path separators;
- a concise catalog summary distinct from the current round objective;
- visibility appropriate for the node contract;
- a concise mathematical objective;
- target_state;
- require_target_state_satisfied.

Before the mutation, enumerate every current-node declaration already known from source, contract, or graph truth to be required by the new Statement or proof. Record all of them in the anticipated_statement_dep_names or anticipated_proof_dep_names field. Passing an empty list is an explicit assertion that this check found no known dependency; it is not permission to postpone a visible edge until Worker execution. If a listed provider is also created or advanced in the draft, submit neither edge as a hidden same-round package: validate the disclosed graph, discard the rejected draft, and split provider before consumer.

Choose visibility from the intended stable API rather than proof implementation convenience. Contract interface outputs, stable reusable constructions, and declarations required by the formal Statement of an intended public root should be public. Proof-only helpers remain private by default. Existing committed declarations that only need added visibility are repaired during public-boundary curation and do not require a new update round.

For a native repository, do not plan or guess `Decl.module` or the Lean full declaration name. The system derives the module from repo/node/kind/name and formal capture discovers the full name.

Helper lemmas and constructions that matter to later work should be tracked as their own declarations. Do not hide important helpers as untracked local Lean code. Same-node reusable canonical declarations are private by default; cross-node Statement dependencies require a public provider boundary.

## Update Changes

Use `plan_update_decl` when an existing declaration needs a targeted repair or stage advancement. The execution interval is (reset_to_state, target_state] over the fixed pipeline:

`planned --Statement NL--> specified --Statement Formal--> declared --Proof NL--> proof_planned --Proof Formal--> proved`.

By default, the service copies the current committed head and uses that base revision's state as reset_to_state. If that base already reaches target_state, you must explicitly choose a lower reset_to_state to describe the redo range. Use optional base_revision only to copy a specific older committed revision; this creates a new monotonic revision and does not move history backward.

reset_to_state is a retained boundary, not a stage to run: reset_to_state=declared and target_state=proved begins with Proof NL. For a release-protected declaration it cannot cross beneath the accepted formal statement. A proof_planned reset retains the proof plan and clears only proof-formal artifacts/checks. Include the intended target_state and require_target_state_satisfied.

Apply the same anticipated-dependency rule to updates: preserve every already-known current-node Statement and proof edge in the corresponding anticipated dependency list, including an edge whose provider is another planned change. Never omit a known edge to bypass round topology validation.

Do not use an update change to silently change a previously accepted mathematical meaning.

## Target Satisfaction

Use `target_state=declared` when the round should produce or repair the statement layer. Use `target_state=proved` when the round should produce or repair the proof layer for a theorem-like declaration.

Keep `require_target_state_satisfied=true` unless the selected mode skill explicitly justifies a state-only intermediate change. A state-only intermediate change must be followed by provider-before-consumer rounds that make its dependency closure proof-policy satisfied. It is not terminal Content completion in graph_proved mode.

## Delete Changes

Before deleting a declaration, call `preview_decl_delete_closure`. Never plan deletion of a release-protected declaration. Private declarations may be removed only when current references and the preview permit it.

## Validation And Submit

Call `validate_decl_round_draft` before submitting. If validation rejects the unsubmitted draft because its batch shape, dependency order, or planned changes must be replaced, call `discard_decl_round_draft` with the concrete validation reason. Discard rolls back the whole draft atomically; it is not a partial edit tool. Re-read current truth, then create smaller or dependency-ordered replacement rounds. Never discard a running, awaiting-closeout, or committed round.

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

For blocked changes, classify the blocker as source/contract evidence, visible or provider dependency, predictable tracked helper, Lean-emergent helper, statement/interface drift, scope overflow, or runtime failure. Preserve the worker or reviewer evidence: affected declaration and revision, round and target state, unresolved consumer-side local goal or formal shape, checked Mathlib/current-node/provider declarations, and each precise mismatch in objects, indices, parameters, assumptions, conclusion, representation, or visibility. Record every concrete missing item, the dependency frontier and consumers it blocks, Source/contract/interface anchors, and the conditions that must hold before retrying a parent declaration. State why the gap appears local or package-shaped and the recommended repair, new-node, or provider branch; ownership remains a Coordinator decision. Mark superseded candidates and cleanup targets for the next planning decision; closeout itself does not delete them.

Do not hide an important blocked cause inside a generic success or failure summary, and do not compress a concrete formal mismatch into only "needs a helper" or "needs a dependency".

## Round Summary

Use `write_decl_round_summary` to summarize the whole round. The round summary should explain:

- whether the strategy made progress;
- which declarations reached their target state;
- which declarations became proof-policy satisfied under the current repo target;
- which declarations still need work;
- whether any intentionally state-only intermediate change still needs dependency closure work;
- for a blocked parent, the complete known retry conditions and whether each missing item is Source-visible planned work or a Lean-specific implementation helper;
- the Mathlib, current-node, and provider API checks that support that classification, including concrete signature mismatches;
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
    SkillKey.CURRENT_NODE_PUBLIC_BOUNDARY_CURATION.value: LeanSkillDefinition(
        name="current-node-public-boundary-curation",
        description="Use when ContentPlan must inspect or repair the current Content node's public formal Statement surface.",
        group="content_plan",
        required_tool_groups=_groups(
            AppGroup.CURRENT_NODE_PUBLIC_DECL_READ,
            AppGroup.CURRENT_NODE_PUBLIC_CLOSURE_READ,
            AppGroup.CURRENT_NODE_PUBLIC_VISIBILITY_WRITE,
        ),
        source_design_doc="dev_docs/implementation/public_statement_closure_and_visibility_promotion/02_Agent工具权限与文本更新.md",
        body="""# Current Node Public Boundary Curation

Use this Skill during round planning and Content closeout to keep the current node's public API formally usable.

## Select Public Roots

Public roots include contract interfaces, stable reusable constructions or structures, and independently useful theorem results. A small stable API is preferred, but it cannot hide declarations that are required to state those roots.

Proof-only dependencies and local proof helpers remain private unless they are independently useful public API.

## Inspect And Repair

1. Read current public declarations and call `inspect_current_node_public_statement_closure`.
2. Inspect each reported private formal Statement prerequisite.
3. If one already-ready declaration in the active committed Content head needs a deliberate visibility change, call `revise_current_decl_visibility` with the visibility you just observed, the requested visibility, and a concrete reason.
4. For a complete add-only local repair over exact revisions already anchored by the active committed Content head, call `promote_current_node_public_statement_closure`. During an initial open-only task, choose correct visibility during declaration planning; closeout promotion cannot turn uncommitted declarations into a stable boundary.
5. Reinspect before the Content completion gate.

Visibility revision changes no declaration code, Decl revision, proof, round, or contract dependency. Making a declaration private is allowed only after deterministic gates confirm that no active/open interface, Scope/Main export, Node dependency, public Statement consumer, running/awaiting-closeout round consumer, or stable Release boundary still requires it. A proof-only helper can be made private when no admitted consumer or other boundary requires it. This operation cannot repair unfinished work and never silently removes exports or bindings.

## Boundary

These tools are restricted to the current Content node. If a formal Statement dependency belongs to another node and is not public through that provider boundary, report a Coordinator blocker. Do not create or export parent Scope boundaries from this Skill.
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
            AppGroup.CURRENT_NODE_PUBLIC_CLOSURE_READ,
            AppGroup.CURRENT_NODE_PUBLIC_VISIBILITY_WRITE,
        ),
        source_design_doc="dev_docs/design/agents/skill_bundles",
        body="""# Content Node Completion Decision

## Purpose

Use this skill when deciding whether the current content node task should end as ready, blocked, or failed.

A natural-language claim is not enough. Use the content completion gate and submit tools to complete the task through the workflow.

## Graph Hygiene And Scope Audit

Before interface binding or terminal submission, inspect active consumers and historical private branches. If the node contains safely deletable superseded private declarations, obsolete route artifacts, or retry-version names, plan a small cleanup round first. If the remaining graph contains an independent mathematical package or has expanded far beyond the contract's expected source stages and reasonable helper allowance, submit blocked with a concrete split/contract-review recommendation instead of growing the node without bound.

Read `content-plan-completion-policy` before choosing ready. Audit the current contract's task target separately from the repository target. The current task is terminal only when its owned declarations reach the task-required depth; report any remaining repository gap and never describe a staged result as provider-ready. Before terminal submit, confirm required interfaces and public/private choices, unresolved provider frontier, and whether multiple active declarations reconstruct a shared type, instance, equivalence, dependent family, or representation that should instead have one canonical tracked owner. In graph_proved task mode, a declared parent or other state-only intermediate round is progress and must not be submitted as ready while owned theorem-like work remains unproved. Do not raise graph_declared or interface_declared to proof completion.

## Current-Node Interface Binding

Before the readiness gate, inspect the current contract and current public declarations. For every required interface without a binding, choose the semantically matching public declaration on this same Content node and call `bind_current_node_interface`. The service validates declaration visibility, readiness, kind, formal statement, and current revision.

This is ContentPlan closeout work. Do not treat an unbound current-node interface as a Coordinator blocker when a valid declaration is available. Do not alter the interface requirement, bind a declaration from another node, or bind parent Scope exports; those remain outside this tool's authority.

## Public Statement Closure

Before the readiness gate, call `inspect_current_node_public_statement_closure`. Every public declaration must expose every same-node declaration named by its formal Statement dependencies. Proof-only dependencies remain private unless independently selected as stable API.

Apply `current-node-public-boundary-curation` before mutating visibility; its committed-head eligibility and admitted-consumer blockers are authoritative. A visibility revision creates no Decl round or revision and never silently removes an interface or export. If another node's required declaration is not already public through its boundary, report a precise Coordinator blocker instead of expanding authority.

## Interface Semantic Fit

For each required interface, read the actual bound public declaration and compare it with the interface summary and statement hint. Preserve the hinted consumer objects, binders, assumptions, index representation, and conclusion direction. If a hint includes a partial Lean snippet, verify that the bound declaration supplies that capability semantically; name similarity is not evidence, and the hint is not an exact header equality requirement. If the declaration is clearly mismatched, continue current-node work or report a contract blocker instead of submitting ready.

## Ready

Use `check_current_content_node_completion` when you need a detailed diagnostic report for the contract, public Statement closure, proof policy, dependency identities, managed projection, interfaces, or unresolved callbacks. Do not poll the same heavy report repeatedly before ready.

Call `submit_content_node_ready` when current truth appears complete. The submit records intent; a deterministic closeout Step then runs the authoritative completion audit exactly once. After an accepted ready submit, stop. If that audit fails, the next callback includes its structured report; repair within your authority or choose blocked/failed.

If a diagnostic check or deterministic completion callback rejects readiness, do not force ready. Fix issues within your authority, run another round, dispatch allowed preparation, or choose blocked/failed when appropriate.

## Blocked

Call `submit_content_node_blocked` when the current task cannot responsibly continue because it needs action outside the ContentPlanAgent authority. Typical blocked reasons include:

- Coordinator must revise the node boundary, objective, interfaces, or node tree;
- an external provider repository is required;
- source or resource material is missing and cannot be acquired from this task;
- a proof route requires higher-level decomposition;
- preparation found a prerequisite that this content node cannot create.

When the blocker requires a decision beyond the current Content boundary, first re-read the affected revision and authoritative round closeout. Use a structured multiline reason that faithfully carries its blocked object, concrete gap, consumer/frontier anchors, checks and mismatches, local-versus-package rationale, retry conditions, and requested Coordinator ownership decision. Do not weaken detailed round evidence into a generic request.

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
6. For Statement NL workers, add project dependencies with the statement repo dependency tool and Mathlib dependencies with the statement Mathlib dependency tool. Use the singular form for one item and the plural form for a small known batch. Statement NL text, origins, and dependencies update declaration truth only; the Flow projects them once when Statement Formal begins. The NL worker does not prepare, edit, or capture Lean files. Use dependency removal or clearing only to repair the current candidate. Do not use a flat mixed dependency list or an untyped origin dictionary.
7. For Statement Formal workers, refine typed statement dependencies when the final formal statement uses a project or Mathlib declaration that is not already recorded. Exact Mathlib dependency addition verifies or reuses canonical index truth and updates the managed projection atomically; do not separately curate the repo index for that dependency. Do not mutate statement text, statement origins, proof routes, or proof artifacts while doing this.
8. For Proof NL workers, write proof-route text first, then add only stable typed source/resource origins that support the proof route itself. Add project and Mathlib proof dependencies through their separate typed tools. Proof NL mutations update declaration truth only and leave the accepted Statement Formal file and capture unchanged; the Flow projects them once when Proof Formal begins. Do not use a flat mixed dependency list or an untyped origin dictionary.
9. For Proof Formal workers, refine typed proof dependencies when the final formal proof actually uses a project or Mathlib declaration that is not already recorded. Do not mutate proof-route text or proof origins while doing this.
10. For reviewers, inspect typed source/resource origins and dependencies as artifacts under review. When the candidate is otherwise acceptable and only a small number of already verified Mathlib dependencies were omitted, use the stage-matching add-only Mathlib dependency tool. In a Formal review stage this transaction refreshes the managed projection, rebuilds, and recaptures the unchanged formal candidate internally; the reviewer still does not prepare, edit, diagnose, or capture files. Do not alter project dependencies, remove or replace dependencies, or use this repair for semantic, helper, or boundary gaps.
11. Record origins only for source/resource ranges that actually support the statement or proof. Generated or agent-authored text may have no origin; do not overclaim support.
12. Keep statement dependencies and proof dependencies separate. Statement dependencies are only the project or Mathlib declarations needed to express the statement; proof-only helper lemmas belong to proof dependencies.
13. Do not use unfinished same-round declarations as stable dependencies unless the current truth already marks them accepted and suitable for this stage.
14. Before submit, self-check that origins are stable, dependencies are visible, source support is not invented, and blocked needs name the missing material, dependency, helper declaration, resource, provider repo, or planning change.
15. When a blocked need requires Planner or Coordinator action, preserve the affected declaration, unresolved consumer-side goal or formal shape, the project/Mathlib declarations checked, their precise semantic or signature mismatch, and the conditions required before retry. Distinguish work repairable in the current stage, tracked work inside the current Content node, a coherent package outside the current boundary, and external provider/resource work.
16. A worker may include a local Lean goal or code fragment as evidence, but must not design the final public theorem or repository node tree for another Content node.

## Dependency Display And Projection

Project dependencies are displayed as `[repo-key::]node-path::Decl.name` → `Lean full name` from `Lean module`. Use the left locator with Constellation inspect tools, the arrow target in Lean expressions, and the module for imports. Current-repo locators omit the repo key; external-repo locators include it. Mathlib dependencies are displayed as `Lean full name` from `module`.

The revision/reason remains structured truth and is not copied into the docstring. Formal capture requires every dependency to resolve to a module and full name. NL-stage dependency mutations defer managed docstring/import projection until the corresponding Formal stage begins. Formal-stage dependency mutations refresh the working projection immediately. A Formal worker retains explicit capture ownership and must re-read the file whenever the mutation result reports that rereading is required; do not hand-write an import that the managed projection derives.

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
                "Reuse the exact tracked dependency for any canonical type, index, instance, equivalence, dependent family, or construction named by the plan. Do not regenerate a mathematically equal local version; block for planning when the required canonical Decl is missing or incompatible.",
                "If target metadata identifies a qualified contract interface, place the declaration in the namespace required by that interface; capture must discover that exact Lean full name.",
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
                "Reuse exact canonical project dependencies for shared types, indices, instances, equivalences, dependent families, and constructed objects. A proof-local `letI` may install a named canonical instance definition, but must not rebuild a competing construction when later declarations need the same term.",
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
