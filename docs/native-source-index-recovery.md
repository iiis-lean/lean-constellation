# Failed Native SourceIndex Recovery

Lean Constellation provides a narrow successor contract for one production
failure: a native preparation parent that has been formally reconciled to
`failed` because its SourceIndex child failed while starting a later builder
round after reviewer rejection.

This contract is not a general Flow retry API. It never changes or reruns the
failed parent, child, Step, Agent, session, turn, or provider artifacts. It
creates a new native preparation lineage, records the failed lineage in the
new Flow input, creates new Steps and Agents, and resumes the rejected draft
against the original pre-mutation checkpoint.

## Eligibility

Preview and apply fail unless all of these conditions still hold:

- the repo runtime is globally paused, with no active Flow advance or running
  Step;
- there is no non-terminal repo lifecycle Flow;
- the selected parent is a reconciled failed `native_repo_preparation` Flow
  whose error identifies exactly one failed `source_index_build` child;
- the child's last Step is a preserved failed
  `source_index_builder_agent_step`, with the same propagated
  `step_run_exception` message at child and parent, specifically the historical
  `home materialized file hash mismatch:` failure class;
- the child is waiting for builder round 2 or later, retains non-empty reviewer
  feedback, and has not exhausted its configured round limit;
- the parent and child identify the same `before_native_source_processing`
  checkpoint;
- the checkpoint baseline digest, source manifest, resolved scope, source
  hashes, and current rejected-draft digest are unchanged;
- the rejected draft still validates as a mutable delta against that archived
  baseline.

Failures outside this boundary require a separate diagnosis. In particular,
do not use this API for an arbitrary failed Flow, a first-round failure, an
approved draft, a reviewer Step failure, or a failure without preserved
reviewer feedback.

## Two-phase Admin API

Route-owned `repo_key` and `repo_root` must not appear in request bodies.

First request a read-only preview:

```http
POST /admin/repos/{repo_key}/runs/recover-source-index/preview
Content-Type: application/json

{
  "failed_parent_flow_id": "f_..."
}
```

The response exposes the complete recovery contract, including:

- failed parent, child, and Step IDs;
- the exact preserved failed-Step error type and message;
- the original pre-mutation checkpoint and baseline digest;
- the current rejected-draft digest;
- the source manifest, active file scope, and exact open-update
  new/committed/uncommitted file context;
- the exact review round, round limit, builder summary, and reviewer feedback;
- a `recovery_token` covering every field above.

Audit the preview before applying it. Then use its token exactly once:

```http
POST /admin/repos/{repo_key}/runs/recover-source-index
Content-Type: application/json

{
  "failed_parent_flow_id": "f_...",
  "expected_recovery_token": "<64-character token>",
  "enqueue": true
}
```

Apply holds the repo lifecycle lock, regenerates the preview, and compares the
token before creating a Flow. Any drift returns
`native_source_index_recovery_token_mismatch` and creates nothing. The new
parent Flow build independently regenerates and matches the contract, so the
generic Flow-start surface cannot bypass the recovery eligibility gate. The new
SourceIndex child repeats the same validation before it can create an
AgentStep, closing the interval between successor creation and execution.

The successor child starts at the preserved review round. Its Builder prompt
contains the original reviewer feedback. The immutable baseline is loaded
from the original pre-mutation checkpoint, not from the rejected draft, so
the Builder may repair existing draft blocks and refs before a new Reviewer
and deterministic commit gate run.

## Controlled production runbook

1. Keep the repo runtime paused. Confirm no lease, active Flow advance,
   running/created Step, or other non-terminal lifecycle Flow exists.
2. Record counts and hashes for the old parent, child, failed Step, Agents,
   provider artifacts, SourceIndex, source corpus, and Target.
3. Call preview only. Verify every lineage ID, checkpoint, digest, round, and
   the full reviewer feedback against the incident record.
4. Call apply once with the exact preview token. Do not restore a snapshot,
   edit the database, retry the failed Step, or call `/runs/initial`.
5. Confirm exactly one new `native_repo_preparation` Flow exists and all old
   records and provider artifact hashes are unchanged.
6. Advance only with bounded production tokens. Parent Flow advances and
   deterministic Step starts create and dispatch the successor SourceIndex
   child. One child Flow advance creates the recovery-validation Step and one
   bounded Step start runs it; only a later child Flow advance may create a new
   Builder AgentStep.
7. After every bounded action, wait for automatic pause and audit queues,
   lineage, Home integrity, Agent/provider locators, SourceIndex state, and
   source hashes before issuing another token.
8. Continue through Reviewer, deterministic SourceIndex commit, root-interface
   preparation, and the normal native handoff gates. Stop on any mismatch.

For the Pascal incident, the operator must additionally match the preview to
the preserved parent `f_b6bfbde6b0c8486585c3b731f6318ed8`, child
`f_f8c4b1c6ef35440dae468981c45016cb`, and failed Step
`scoped_source_index_builder_a66df06707ab4d928bd33b865d0e08b6` before apply.
The recovery implementation does not authorize or perform that live action;
the production coordinator must issue a separate, exact migration token.
