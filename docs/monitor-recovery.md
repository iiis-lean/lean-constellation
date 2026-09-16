# Monitor recovery policy

Applies to Ramsey, Andrews–Dhar and the eight-project execution monitor.

A monitor may autonomously attempt at most **two recovery executions per interrupted logical task**, including replacement Steps. Do not reset the counter merely because a new Step ID was created. Reset after a valid business submission. Record attempts and evidence in the project monitoring state.

Eligible failures require concrete provider/transport evidence: Codex `TransportClosedError` (local process stdout EOF), connection refusal/reset or a provider timeout, or the specific upstream HTTP 404 `no enabled channel for model`. A bare HTTP 404, generic unexpected exception, unknown stream disconnection, or an LC/ARK invariant failure is not sufficient. Local EOF identifies a transport failure, not proof of upstream outage.

Before each recovery, allow natural drain and verify paused runtime, zero running/active work, settled runner, idle Agent, no trusted submission, and a fresh recovery assessment/token offering `resume_suspended`. Respect context-maintenance gates. Use `agent_mode=reuse`; never bypass gates, mutate truth, restore a checkpoint, change channels, or silently use fresh. If reuse is unavailable, escalate.

Wait 60 seconds before the first recovery and 180 seconds before the second, using a background wait rather than active model polling. For confirmed channel-unavailable errors, use the approved bounded availability probe before recovery; failed probes consume the bounded recovery opportunity and must not loop indefinitely. Existing in-Step ARK retries are separate from these two post-suspension attempts.

Start only the exact replacement Step with the formal semantic lease. Read back ambiguous POST results before considering another request. After a valid submission, resume the same complete Content batch or normal Coordinator boundary. Preserve all completed writes.

After two unsuccessful recovery attempts, or any non-eligible error, stop that project at a safe boundary and report sanitized exception type/fingerprint, provider code, session/Step/lease IDs, attempts and recovery assessment to the root coordinator. Authentication failures, HTTP 401/403, payment/quota exhaustion, protocol/schema errors, lost executions and business failures are not transient retry candidates. Other projects continue.

Remain quiet for successful routine recovery. Report only exhausted recovery, a new implementation fault, completion, or a required operator decision.


## Watcher continuity after messages

A running observer process and an armed completion wake are separate states. A user message or coordinator delegation can disarm the defer-and-resume registration without stopping its worker. An acknowledgement such as “continuing to wait” does not restore the wake.

After any ordinary message, and before ending a turn that leaves authorized work under observation:

1. Apply the message first. If it cancels, holds, or completes monitoring, do not re-arm against that instruction.
2. Locate the existing watcher for the current lease (or batch pool) from the monitoring state. Run `defer.py status --task-dir <existing-task-dir>` once; confirm its owning thread and lease mapping. Do not start a second worker because waiting was disarmed.
3. If the worker is running and waiting is disarmed, run `defer.py arm --task-dir <existing-task-dir>` from the owning monitor task. Read back `wait_state=armed` or `waiting`, and preserve the original worker/task/lease IDs. Then finish the turn so the Stop Hook can wait.
4. If the worker is already terminal, inspect and consume its result now, then advance or close monitoring according to the actual runtime boundary. Do not restart the completed worker or duplicate its lease.
5. If the worker is missing or failed, distinguish an observer failure from a runtime failure. Read the current lease before replacing only the observer; never infer that the mathematical task failed.

Only an actual `Cache keepalive wake` uses the no-tool immediate-return rule. Ordinary user/delegation messages are not keepalives, even when they say to remain quiet or require no acknowledgement.

When registering a new watcher, record the task directory, owning thread, lease (or pool), worker PID, and waiting state before yielding. Successful `start` alone is not evidence of a future wake. Keep at most one intended observer per lease; a pool watcher may cover several leases. When a project is complete, consume its final result and close its wait instead of re-arming indefinitely.

This rule requires one local registration check at message boundaries, not repeated model polling or periodic progress reports. Coordinators should use read-only status inspection for routine status questions and avoid unnecessary messages to waiting monitors.
