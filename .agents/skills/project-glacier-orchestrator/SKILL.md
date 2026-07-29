---
name: project-glacier-orchestrator
description: Safely operate or recover Project Glacier's deterministic protocol-v2 multi-root pilot. Use when the user explicitly asks to start or resume Project Glacier in multi-root mode and create or reuse its projectless offline worker, or when Codex must inspect, validate, recover, stop, or archive that worker's coordination lifecycle. Do not use an ordinary Project Glacier start or resume request to create another task.
---

# Project Glacier Orchestrator

Coordinate one isolated projectless research task while preserving R0 as Project Glacier's sole
live operator and canonical writer.

## Activation gate

Create or reuse the worker only when the current user explicitly requests **multi-root pilot mode**
and explicitly requests creation or reuse of the offline worker task. Otherwise use the base
single-root protocol and do not call a create-task tool.

Reading, reviewing, testing, or repairing the deterministic helper does not itself activate live
Project Glacier work or worker creation.

After all reconciliation gates pass, use `activate-pilot --user-authorized-offline-worker` only
for the explicit activation request. This durable transition is required before `plan-bootstrap`.
Never supply that authorization flag by inference from a plain Project Glacier request.

## Load the authoritative sources

Before any pilot action, read these files completely in this order:

1. `../../../project_glacier_master_orchestrator_prompt.md`
2. `../../../project_glacier_multi_root_orchestrator_prompt.md`
3. `../../../project_glacier_offline_root_worker_prompt.md`
4. `../../../PROJECT_GLACIER_RESUME.txt`
5. `../../../glacier_rules_dossier.md`
6. `../../../glacier_experiment_ledger.md`

Treat the base master prompt as authoritative for every live, safety, broker, delivery,
adjudication, submission, reset, and checkpoint rule. Treat the multi-root prompt only as an
offline-scheduling overlay.

Read [task-tool-contract.md](references/task-tool-contract.md) before creating, messaging,
recovering, stopping, or archiving the worker.

## Preserve the control-plane split

Use the local `project_glacier_orchestrator` package only to validate, hash, generate packets,
journal the outbox, enforce lifecycle fences, and perform allowlisted checkpoint compare-and-swap
transitions. Run `python3 -m project_glacier_orchestrator --help` and the selected subcommand's
`--help` before use.

Keep planning dry-run unless an intended coordination transition must be durable. For every commit,
supply the checkpoint version and hash from the most recent validated read. On a compare-and-swap
conflict, reread and reconcile; never force an overwrite.

Never use the helper to call Codex, app-server, Glacier, browser, network, connectors, or external
systems. It emits declarative actions only. R0 reviews and performs each Codex app task call and
records the observed outcome. R0 alone performs all `glacier_*` calls and all semantic research,
ledger, and adjudication decisions.

## Reconcile before coordination

Run the helper's `doctor` path and reconcile the resume checkpoint, rules dossier, experiment
ledger, and server-authoritative broker state under the base prompt before worker dispatch, result
ingestion, or challenge mutation.

Fail closed when reconciliation is required, the checkpoint or artifact digests disagree, live
state is active/pending/prepared ambiguously, recovery is required, or delivery is unknown. Do not
call Glacier merely to test the offline helper.

## Follow the lifecycle

Use only the helper's current commands and emitted actions:

1. If prompt drift exists before any worker lifecycle, use `reconcile-prompt` with a reviewed
   compare-and-swap commit while still unregistered with no task or outbox.
2. For a new explicitly authorized pilot, use `activate-pilot` with
   `--user-authorized-offline-worker` and the latest checkpoint fence.
3. Use `pending-action` on an already active pilot resume when the outbox is `PLANNED`; execute only
   that bound action. If delivery is `UNKNOWN`, use `diagnose-unknown` for read-only list/read
   evidence and never resend. Only when exact evidence proves nondelivery and the destination task
   is inactive may `requeue-known-nondelivery`, with both `--nondelivery-verified` and
   `--task-inactive`, restore the same non-create action to `PLANNED`; it must preserve the
   ID/payload and must never requeue `CREATE_THREAD`. Changing an already recorded `UNKNOWN` or
   `REJECTED` delivery outcome requires exact evidence plus `--delivery-evidence-verified`.
4. Use `plan-bootstrap` to validate and durably plan the unique bootstrap before create-task.
5. Have R0 execute the emitted exact projectless create action once.
6. Use `record-worker-created` only after create returns the task identity. For ambiguous creation,
   use `record-delivery` with `UNKNOWN`, resolve through list/read evidence, and record the worker
   only after one exact match; never create blindly again.
7. Read the completed bootstrap turn, verify the recorded source task is inactive, and use
   `accept-attestation` with matching `--source-thread-id` and explicit `--task-inactive`. Fall back
   to direct subagents on any isolation uncertainty.
8. Use `plan-assignment` for one bounded sanitized assignment, then have R0 execute the emitted
   send action and use `record-delivery` for the observed outcome.
9. Read the completed worker turn, independently verify it inactive, and use `accept-result` with
   matching `--source-thread-id` and explicit `--task-inactive` for structural, identity, replay,
   snapshot, epoch, digest, and zero-running-child validation. If R0 resumes after this structural
   checkpoint but before disposition, reread the same task packet and use read-only
   `verify-pending-result` before continuing.
10. If the worker returns `PROTOCOL_REJECT`, use `accept-protocol-reject` with the same source and
    inactive-turn evidence and stop that workflow. Do not feed it to `accept-result` or retry
    blindly. This also records a rejection of an acknowledged STOP during adverse live recovery;
    it does not prove cleanup, so plan a fresh CLEANUP stop afterward.
11. If stale, execute only the durably planned rebase action; never merge the old result.
12. If current, have R0 perform semantic screening and any authorized canonical ledger/rules write.
   Then use `commit-result` with the honest `ACCEPTED`, `DUPLICATE`, or `REJECTED` disposition.
13. Use `plan-ack` only after that durable disposition, execute its emitted ACK, and record
    delivery. Acknowledged delivery keeps `ACK_PENDING` and emits a read action; independently
    observe the exact task inactive, then use `complete-control-turn` with its matching task ID.
    Apply the same gate to an acknowledged `REBASE_REQUIRED` turn. Do not dispatch another
    assignment before this command returns the worker to `IDLE`.
14. For final cleanup, use `plan-stop` with reason `CLEANUP`, send it, and record delivery.
    A non-cleanup emergency stop may fence work, but its ACK reaches only `STOPPED_FENCED`; perform
    a new CLEANUP stop/ACK handshake before archival. If live Glacier delivery is unknown,
    recovery/reconciliation is active, or the live mutation lock is set, keep every new-work and
    result gate closed but continue this exact fail-safe shutdown lifecycle.
15. Read the correlated `STOP_ACK`, independently verify through the task surface that its turn is
    inactive, then use `accept-stop-ack`. This command emits the archive action only after the
    packet and explicit inactive-task evidence pass.
16. Execute the emitted archive action and use `record-archived` with the observed
    `ACKNOWLEDGED`, `REJECTED`, or `UNKNOWN` outcome. Only acknowledged or independently verified
    archived state may complete the `ARCHIVED` transition; changing a prior unknown/rejected
    archive outcome requires `--delivery-evidence-verified`.
17. When ending or falling back from the pilot, use `deactivate-pilot` only after the lifecycle is
    still `UNREGISTERED` or fully `ARCHIVED`; it durably restores `SINGLE_ROOT`.

Persist every external task intent, including archive, in the outbox/checkpoint binding before R0
performs it. Treat `PLANNED` as intent, not delivery. On an ambiguous call, record `UNKNOWN` and
inspect the exact task/message identity; never retry blindly or reuse an ID for a logically new
message.

Immediately before every create, send, or archive mutation, rerun `pending-action` and execute
only its freshly revalidated action. For bootstrap, assignment, ACK, and rebase, this final gate
must still see active pilot mode, matching protocol artifacts, reconciled and quiescent live state,
the exact current leadership epoch and assignment snapshot when applicable, and the planned
outbox ID/type/digest. STOP and archive use the narrower fail-safe shutdown gate so adverse live or
recovery state cannot orphan the worker; they still require active pilot mode, matching protocol
artifacts, exact outbox binding, CAS, and the cleanup lifecycle fences.

The pilot resumes productive work only in the same R0 Codex task. It has no productive-root
takeover command, and the leadership epoch must never be edited manually. From a different root
task, use only read-only diagnosis and the fail-safe STOP/CLEANUP/archive path, then deactivate to
`SINGLE_ROOT`; do not dispatch a pending assignment/ACK/rebase or ingest a result.

## Respect turn-based synchronization

Send one message to an idle worker, let that worker turn finish, then explicitly read and validate
the response. The worker cannot push updates or heartbeat. If R0's turn ends, synchronization
pauses until R0 resumes. Never claim that another root is continuously running or synchronized.

## Fail closed and fall back

Fall back to R0's ordinary direct subagents when projectless creation, task messaging, isolation,
attestation, replay fencing, checkpoint compare-and-swap, or cleanup verification fails. Do not
substitute a same-project task, a fork, app-server, generic networking, or another live operator.
