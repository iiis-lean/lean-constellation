# Retrying a failed post-content stable snapshot

`POST /admin/repos/{repo_key}/flows/{flow_id}/coordinator/retry-content-snapshot`

Body: `{"expected_step_id": "after_content_task_batch_snapshot_..."}`.

This operator action requires a paused runtime with zero active Agents, Steps, and Flow advances. The Flow must have failed specifically with `coordinator_stable_snapshot_failed`, be positioned at `coordinator_callback` with no current Step, and its last Step must be the specified completed content-batch snapshot Step with no checkpoint id. The paired-restore interlock remains enforced.

The operation reopens the same Flow, invokes only the stable snapshot hook, and queues continuation after checkpoint creation. It does not rerun content Tasks, create a new Coordinator Agent, rewrite node contracts, or restore an older checkpoint. A failed attempt leaves the Flow failed. Record the response; after an uncertain transport outcome inspect Flow and Step truth before retrying.

Legacy Grok startup records may contain a session id without a native locator. The artifact adapter can archive their actual files only when exactly one matching session directory exists under the declared Home, with a valid encoded absolute workdir and the existing path/symlink checks. It does not change persisted Agent identity or make a partial session eligible for resume. Missing, ambiguous, or malformed locations remain errors.

Snapshot failure issues now include per-scope safe exception diagnostics. These also propagate into the Coordinator Flow error details. Previously discarded errors cannot be reconstructed from the old generic message alone.
