# Switching provider identities at a paused boundary

A provider switch creates fresh Agents and sessions. It preserves Lean files, contracts,
submissions, completed Steps, and old sessions. It does not resume execution or enqueue
work. Grok-to-Codex and same-provider switches to a **different Home ID** are supported.
Changing a backend underneath an existing Home is not a supported migration.

## Stage the destination

Pause the repo runtime and drain running Steps, Agents, and Flow advances. Keep it
paused while deploying the destination configuration and materializing its Homes.
Existing CREATED Steps may remain. Use the same provider for all destination roles.

Each `agent_home_overrides` entry can set `home_id`, for example:

```json
{
  "default_agent_provider_type": "codex",
  "agent_home_overrides": {
    "ContentPlanAgent": {
      "provider_type": "codex",
      "home_id": "ContentPlanAgent_luna_bee",
      "model": "gpt-5.6-luna",
      "model_reasoning_effort": "max"
    }
  }
}
```

Configure every relevant role, including Coordinator, discovery, recon, ResourceCurator,
and all eight declaration worker/reviewer roles. The example is only one entry, not a
complete deployment configuration. Set the endpoint and credential reference in the
protected Codex base configuration; never put credentials into migration requests or
receipts. Preserve the subscription login. Home preflight verifies local materialization
and execution context; it does not verify remote quota or make a provider turn.

## Plan and apply

Use these operator-only endpoints on the repo's production server:

- `POST /admin/repos/{repo_key}/provider-switch/plan`
- `POST /admin/repos/{repo_key}/provider-switch/apply`

Both accept `source_agent_ids` (a nonempty, fixed list) and `target_provider`. Apply
also requires the plan's `expected_plan_hash`. Save the initial source IDs and reuse
them on retry; never regenerate the list by selecting all Agents after migration.
Include historical off-target Agents, since old ContentPlan/discovery identities can
otherwise be inherited by future tasks. The plan reports omitted off-target identities.

Plan returns the destination defaults, grouped future references, blockers, a CAS hash,
and `complete`. Apply requires a matching hash and no blockers. It preflights all
registered destination Homes, then migrates one old Agent's complete reference group
per Flow/Step store transaction. Every suspended consumer receives its own official
replacement Step, sharing the group's single new Agent. Existing suspended records
are not edited. Live Flow bindings, CREATED Step bindings, and unconsumed dispatch
Agent references move together. Unbound CREATED defaults are updated as well.

Only current, settled, submission-free SUSPENDED AgentSteps eligible for
`resume_suspended` are replaced. Running/lost executions, unresolved business failures,
partly consumed dispatches, unknown references, and cross-repo consumers block migration.
Resolve those through their existing recovery path first. Discovery's already-consumed
`input.agent_id` is retained as historical provenance; its live binding is migrated.

Old Agents are closed, not deleted. Closed ContentPlan/discovery identities cannot be
inherited. Completed history, including its bindings, is preserved.

## Failure and continuation

Apply returns per-group receipts, including replacement Agent/Step IDs, whether bindings
committed, and whether retirement finished. Agent creation/retirement is outside the
Flow/Step transaction. If a transaction fails, its Flow/Step edits roll back and an
unbound candidate is closed when possible. A retirement failure leaves the committed
bindings in place and reports incomplete; replanning with the original source IDs
allows retirement to be retried without creating another replacement.

This is exception rollback, not a crash-atomic repo transaction. After interruption or
any error, inspect a new plan before doing anything else. Do not resume while blockers,
unretired sources, or pending default updates remain. Save every plan and receipt in
the run's `control/` evidence directory.

Once the final plan is complete, issue the normal exact semantic lease. Semantic advance
rebuilds its queues from persisted CREATED Steps, including replacements. Do not use a
bare scheduler resume that skips this rebuild. Test one repo's first destination turn
and successful submission before expanding to other repos; local focused tests do not
prove remote-provider stability. There is no automatic provider fallback.

## BeeAPI and subscription-backed Codex

Both channels use ARK `provider_type: "codex"`. The channel is selected by the Codex
base configuration and the destination Home; changing `target_provider` alone cannot
select BeeAPI versus the subscription.

Maintain two deployment configurations with the same business roles and Luna/max settings:

| Setting | BeeAPI | Codex subscription |
| --- | --- | --- |
| Role Home ID (example) | `ContentPlanAgent_bee_v1` | `ContentPlanAgent_subscription_v1` |
| Role `base_config_path` | Protected BeeAPI Codex config | Protected subscription Codex config |
| Codex `model_provider` | `beeapi` | `openai` |
| Model / reasoning effort | `gpt-5.6-luna` / `max` | `gpt-5.6-luna` / `max` |
| Authentication | BeeAPI key through a protected environment reference | Existing subscription login reference |

For Codex, a role's explicit `base_config_path` takes precedence over the global
`codex_base_config_path`; the global path is the fallback only when no role override
is set. An explicit missing configuration fails preflight instead of silently choosing
the other channel. Every Codex role must resolve to an existing base config. The current
production bootstrap also requires an existing `codex_auth_json_path`; preserve that
login reference even when a Bee Home uses its own API credential.

Use `https://beeapi.ai/v1` and Responses in the protected Bee configuration. Explicitly
select `openai` in the subscription configuration, so a Bee default cannot leak into
it. Configure the Bee credential as an environment reference, not an embedded token;
ensure the subscription configuration has no Bee routing override. Staging a new Home
must not edit the previous Home or the user's global `~/.codex/config.toml`/`auth.json`.

To switch after a channel failure: pause/drain, deploy and materialize the destination
configuration while paused, then plan/apply with the source identities and
`target_provider: "codex"`. Verify `complete` before issuing an exact semantic lease.
Switching back follows the same procedure with the then-current source identities.
A closed Bee Agent is never reopened merely because its Home becomes the target again;
a fresh identity and native session take over. An unchanged, retained Bee Home may be
reused as configuration, but modifying its configuration requires a new Home version.

Focused tests cover production config materialization for Bee → subscription → Bee,
old config preservation, and live Flow/CREATED bindings through the same-provider
round trip. They use synthetic auth and make no remote requests. Remote availability
must still be checked by the first real destination turn. No implicit channel fallback
or hot-switch of an active native session is performed.

Destination startup also registers built-in provider bundles referenced by persisted
Agents, including retired identities. This preserves historical artifact/snapshot
access without making those providers the defaults or starting a provider turn.

### Settled incomplete Coordinator callback

The existing current-truth reset route accepts `expected_incomplete_step_id` together with `expected_agent_id`. This explicitly retries only a failed `coordinator_agent_incomplete` callback whose last Step completed without a submission. It requires paused, quiescent runtime state, creates a fresh Agent from current role defaults, preserves the old Step and all project truth, and does not enqueue work. Other terminal failures remain rejected. Follow with a deterministic logic lease to create the new initial Coordinator Step, then an exact AgentStep lease.

If multiple stale Scope exports block individual deletion, `remove_scope_export` accepts `additional_refs` with exact `{repo, node, name, revision}` references. They are deleted atomically with the primary reference. Interface binding, release compatibility and projection checks still apply.
