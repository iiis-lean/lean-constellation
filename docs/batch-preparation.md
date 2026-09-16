# Independent-server preparation CLI

`scripts/batch_preparation.py` provides parallel observation and bounded advancement
through the existing production Admin API. It does not change scheduler behavior.
Use it for initial native preparation with automatic source-index preparation,
root-interface preparation and all three repository discovery flows.

## Configuration and commands

Supply a JSON list, or an object containing a `projects` list:

```json
{"projects": [{"key": "example", "admin_base_url": "http://127.0.0.1:18768", "repo_key": "Example"}]}
```

Services must already be started, repos loaded, and initial runs created through
the normal production launch procedure. An unloaded repo is reported without
implicitly loading it. This CLI does not create Homes or initial runs.

```bash
python scripts/batch_preparation.py status --catalog /path/projects.json --state-dir /path/monitoring
python scripts/batch_preparation.py advance-preparation --catalog /path/projects.json --state-dir /path/monitoring
python scripts/batch_preparation.py wait --catalog /path/projects.json --state-dir /path/monitoring --timeout 3600
```

Use `--project example` to select a project; repeat it for multiple projects.
Omitting it selects all projects, without an aggregate admission cap. Each
server retains its own runtime concurrency settings. In the local benchmark
environment, use `/root/miniconda3/envs/benchmark/bin/python`.

- `status` reads all endpoints concurrently and prints compact JSON summaries.
- `advance-preparation` issues at most one semantic lease per eligible repo.
  Known preparation AgentSteps receive an exact `step.agent` request;
  deterministic work receives `step.logic` without safety overrides (current
  server defaults: 500 Flow advances and 500 Step starts). Logic stops at an
  AgentStep boundary.
- `wait` waits on active lease versions and coalesces actionable changes across
  servers. Each endpoint has an independent observer. The first actionable
  event returns after a 50 ms coalescing window, without waiting for slow peers.
  Ordinary active progress stays quiet; saved state deduplicates unchanged
  conditions and detects process-instance changes. Lease waits use up to 20
  seconds per request, but another endpoint's event can wake the command sooner.
  Returned snapshots include only endpoints observed so far; `pending_projects`
  explicitly lists unobserved endpoints. Slow or unavailable projects must not
  be interpreted as completed. Read-only daemon observers are stopped on return;
  late HTTP responses cannot write cursor state. Reinvoke `wait` after handling
  events to watch all selected projects again.


## Monitor procedure

Use one monitor as the exclusive Admin writer for the selected projects, with
one shared state directory. Inspect status, advance eligible projects, then run
`wait` through a background completion/wakeup mechanism. On return, inspect the
coalesced events, handle routine completed boundaries, and repeat. Do not keep
a model polling while the command waits. JSON output carries per-project
dispositions; command success alone does not mean every project succeeded.

The CLI refuses to start `CoordinatorAgent`. A project is `prepared` only when
source indexing, root-interface preparation and all three discovery flows have
completed, the sole pending Step is the first unstarted CREATED Coordinator,
and the runtime is paused with no active Steps or Flow advances. Coordinator
Flow creation is allowed because that Flow owns discovery. Prepared is a
campaign stopping point, not mathematical completion or READY.

Running projects are left alone. Unknown Agent/Flow types, suspended work,
incomplete Coordinator gates and projects beyond preparation require inspection.
One unavailable endpoint does not suppress the other endpoints' results.
Escalate provider failures with project, instance, lease, Flow and Step IDs and
the available error evidence; do not change models or restore checkpoints
automatically. Shared dependency incidents need assessment across affected repos.

## Receipts and recovery

Each advancement writes an intent before POST and an acceptance receipt after
success. A transport failure or interruption can leave `uncertain` or `posting`;
further advancement is held even if the server appears idle. Inspect the actual
server lease and Step history to resolve whether the request was accepted.
Archive the local receipt with the incident evidence only after that audit,
then invoke advancement again. Never delete runtime history to clear a hold.
The receipt file records the latest attempt; retain incident evidence separately.

Local file locks prevent duplicate CLI writers sharing the state directory.
They do not coordinate unrelated Admin clients or different state directories.
Two live inspections reduce stale decisions but are not a server transaction;
exclusive writer ownership remains required. No server restart, recovery,
checkpoint restore or unbounded automatic advancement is implemented here.

## Verification

Focused tests: `tests/unit/scripts/test_batch_preparation.py`. They exercise
simulated API responses, Coordinator gates, unknown work, default logic safety,
duplicate writers, ambiguous POST responses, parallel event coalescing, restart
detection and failure isolation. They do not start real providers or validate
an entire preparation campaign.

A coordinator waiting for repository exploration is eligible for `step.logic`
when its own three discovery children are all completed. This lets the normal
scheduler leave waiting and create the first Coordinator AgentStep, where logic
automatically pauses. Completed discovery in unrelated flows does not qualify.

## Coordinator and Content execution mode

Preparation mode remains the default and still refuses Coordinator startup.
Execution must be explicitly selected:

```bash
python scripts/batch_preparation.py status --phase execution --catalog /path/projects.json --state-dir /path/monitoring
python scripts/batch_preparation.py advance-execution --catalog /path/projects.json --state-dir /path/monitoring
python scripts/batch_preparation.py wait --phase execution --catalog /path/projects.json --state-dir /path/monitoring --timeout 3600
```

`advance-execution` selects execution mode itself, regardless of `--phase`.
Each project must include its local `repo_root` in the catalog. Observation
uses Admin views plus the local persisted Coordinator Flow JSON, read only,
for dispatch identity (the compact Admin Flow view does not expose these fields).
The local record must match the Admin Flow ID, scope, phase, status and current
Step, and the configured repo root. No runtime object is edited directly.

The driver performs one of these actions per paused, idle repo:

- Start the exact unstarted CREATED Coordinator AgentStep.
- Submit one complete `content_batch`, identifying the Coordinator and expected
  source submission, and the dispatch Step when already known. The production
  server revalidates identities, independence, member contracts and task limits.
  A normal safety-cap boundary may continue the same batch from current truth.
- Advance Coordinator deterministic work using `step.logic`, which cannot start
  AgentSteps. This includes creating the next Coordinator and marking READY.

It never starts individual Content workers or plans separately. Full batch
policy owns their concurrency and stops at the Coordinator callback boundary.
Lease monitor review flags, invalid state, unsupported resource uses or requirement
phases and unknown Agent boundaries stop for monitor inspection. No automatic
recovery, provider change or checkpoint restore is performed. READY is reported
only with runtime idle, publication ready, no pending Steps and all observed
Flows completed; conservative holds with historical failures require review.

Use the same state directory for both advance commands so writer locks and
uncertain POST receipts remain shared. Waiting has separate preparation and
execution cursor files. Keep one active watcher for a campaign. After handling
first-event results, advance eligible projects and rearm execution wait. A
failed or held project must not prevent other projects from progressing.

Runtime `max_concurrent_steps` is loaded at process/repo construction. Editing
server configuration alone does not update an existing process. Reload only
at a paused zero-active boundary, verify the same pending Step and unchanged
business truth, then permit execution. The catalog task ceiling and Step cap
are separate: four Content tasks need up to four runtime Step slots. Keep
`max_concurrent_flow_advances=1`; it does not serialize ongoing AgentSteps.

Execution also handles normal `supporting_material` resource requests: Coordinator
snapshot/dispatch via logic, its own current resource_curation child via exact
ResourceCurator AgentStep, then logic to the Coordinator callback. Persisted
source submission identity and requested use are checked. Failed children,
non-supporting requests and provider requirements remain review boundaries.
This does not authorize replacing the primary corpus or broadening the theorem
scope; unavailable material may be rejected through the normal curator result.

### Reviewed resource pause

After an operator-requested pause, a completed supporting-resource child may leave a terminal `manual_pause` lease requiring review. Use `advance-execution --project KEY --reviewed-resource-lease LEASE_ID` after reviewing that boundary. The option requires one project, the exact current terminal manual-pause lease, zero active work, and the matching completed resource child. It admits only the existing parent logic action through semantic-advance; it does not call runtime/resume or clear general failure gates. The acknowledgement is recorded in the advance receipt.
