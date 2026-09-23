# Restructure execution

Restructure uses the existing repo-local ARK FlowService, AgentStep, submission,
and scheduler machinery. Prepare the workspace explicitly; there is no workspace
planning Agent. Repo Coordinators produce RepoPlans when those are not supplied.
Content Agents execute declared and proved stages.

Set `WorkspacePlan.max_agents = 8` and `max_builds = 1`. These workspace-wide
reservations also cover repo planning and persist across supervisor restarts.
Set application `max_concurrent_steps = 8` and sufficient
`max_concurrent_flow_advances` to avoid a lower runtime scheduling ceiling.
Configure the existing Agent Home overrides, for example in application TOML:

```toml
max_concurrent_steps = 8
max_concurrent_flow_advances = 8

[agent_home_overrides.RestructureCoordinatorAgent]
model = "gpt-5.6-sol"
model_reasoning_effort = "high"

[agent_home_overrides.RestructureContentPlanAgent]
model = "gpt-5.6-luna"
model_reasoning_effort = "max"

[agent_home_overrides.RestructureContentImplementationAgent]
model = "gpt-5.6-luna"
model_reasoning_effort = "max"
```

These overrides select execution models; available provider credentials and model
access still come from the normal application configuration.

Use the production `lean-constellation serve` entry point and its Admin
restructure operations. Prepare source references, target repository directories,
Lake configuration, dependencies, and optional RepoPlans before starting work.
Each repository gets its own runtime. The workspace supervisor only admits work,
tracks terminal results, and enforces barriers.

Declared tasks follow the dependency graph. Generated Prelude, Interfaces, and
root imports project explicit declaration metadata. Every repo must pass its
declared build before proof work starts. After that barrier, proof tasks can run
concurrently without waiting for upstream proofs. Local failures do not halt
independent tasks. Final builds follow repository dependencies.

Declared acceptance freezes declaration identity, statement, non-proof bodies,
and support files. Theorem templates delimit the editable proof with
`-- LC proof begin` and `-- LC proof end`. Managed import blocks are maintained by
tools. Changing the interface requires an explicit Content retry with
`reopen_declared = true`; this invalidates dependent Content and builds while
preserving source. Ordinary proof retry preserves the declared interface and
invalidates affected final builds. Reconcile terminal tasks before retrying;
active writers cannot be replaced. Suspended tasks retain their reservations
until resumed and reconciled through the existing runtime lifecycle.

Builds freeze source snapshots, compile all local Lean modules (plus explicit
requested targets), and reuse a private per-repo Lake cache under a build lock.
Compiled declaration auditing permits theorem placeholders at the declared
stage, rejects placeholder definitions and local axioms, and rejects final
`sorryAx` or nonstandard transitive axioms. Only `propext`, `Classical.choice`,
and `Quot.sound` are allowed by the final audit. Source changes during a build
invalidate its receipt.

Validation covers controlled ARK/ToolFacade execution, production HTTP dispatch,
concurrent admission, recovery, source contracts, and real Lean 4.32 fixtures.
It does not establish real Codex model quality, Ramsey migration performance,
or a multi-repo dependency installation workflow. Provider receipts are checked;
Lake dependency configuration must already resolve the intended provider sources.

### Batch compilation and automatic registration

Content and repository repair agents no longer call `capture_restructure_decl`
or supply file digests. `check_restructure_files` incrementally builds selected
Lean modules and their prerequisites. `check_restructure_content` builds and
checks the Content candidate, including its metadata and stage contract, before
registering it. A failed batch leaves registration metadata unchanged.
Submissions perform this check automatically; failed checks do not accept a
submission. Changes after a successful check must pass again.

Preflight and formal repository builds reuse the same persistent Lake cache.
Formal builds still cover the complete module inventory and compiled axiom audit.
Reports contain bounded multiline errors, warning counts and a report ID;
`read_restructure_build_report` pages through the durable full log. A module
marked `not_confirmed` must not be treated as successfully checked.

### Section metadata and migration

Business records contain no schema ID or schema-version marker. Readers validate
the field structure directly; `_version` remains a storage concurrency counter,
and plan `version` remains a business revision.

Declarations store `statement` and optional `proof` sections separately.
Each contains `nl.text`, `nl.origins`, `deps`, and a system-owned `formal.code`.
Formal captures are complete declaration-owned Lean files, matching the Native
LC convention: statement captures record the accepted declaration stage, while
proof captures record the accepted proof stage. They are not extracted proof
snippets. Agent section inputs exclude `formal`; batch checks populate it from
the actual source. Definitions remain `declared` even when their Content finishes
its proof stage.

After changing mathematical source, review the affected section's explanation,
origins and dependencies and set that section again. Unchanged metadata may be
confirmed by resubmitting it. Generated import changes do not invalidate this
review association. No per-edit capture or caller-computed digest is required.
Node `parent` records tree membership; required `boundary` describes mathematical
responsibility, with optional `constraints`. A parent path is not a boundary.

Legacy workspaces require an explicit migration into a new directory. The
migration command requires successful declared and final build IDs, their frozen
source views, and an item-complete mathematical review patch. It preserves the
old records and artifacts, uses accepted full-file snapshots rather than legacy
handwritten captures, and does not copy scheduler or Agent execution state.

```bash
python -m lean_constellation.services.restructure.migration \
  --source /path/to/v1-workspace --output /path/to/new-v2-workspace \
  --review /path/to/review.json \
  --declared-build Demo=build_declared --final-build Demo=build_final
```

The review JSON has `nodes` keyed by node path with `boundary`, optional
`constraints`, and `evidence`; `declarations` is keyed by node and declaration
name, with `statement_nl`, `proof_nl`, `statement_origins`, `proof_origins`,
`statement_dependencies`, `proof_dependencies`, and `evidence`. Multi-repo
reviews wrap these in `repos` keyed by repo key. Origins retain `source_refs`
and optional `note`; dependencies retain `ref`, `external`, and optional `reason`.
Section membership replaces the legacy free-form `role`. Original proofs may
have no external origin; migration must not invent one. See [LHF](lhf.md) for
sealing and exporting the migrated accepted result.
