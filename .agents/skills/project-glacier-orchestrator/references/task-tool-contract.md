# Protocol v2 Codex task bridge

Use this reference only after the skill's activation gate passes. The deterministic helper emits
task actions; R0 performs them through the host-provided Codex app tools. The helper never calls
these tools itself.

## Authority map

| Concern | Owner |
|---|---|
| Packet parsing, hashing, outbox, replay, CAS, lifecycle checks | Deterministic helper |
| Create/read/list/message/archive task calls | R0 through Codex app tools |
| Assignment meaning, novelty, scope, rules, acceptance | R0 |
| Rules, ledger, resume, and all other canonical writes | R0; helper may update only its allowlisted resume fields |
| Every live challenge action and server reconciliation | R0 through registered `glacier_*` tools under the base prompt |
| Offline analysis | One attested projectless worker |

## Declarative action mapping

Execute only a helper action that matches the current durable outbox and lifecycle state.

| Emitted tool name | R0 behavior |
|---|---|
| `codex_app__create_thread` | Create the single worker with the exact projectless input below. |
| `codex_app__send_message_to_thread` | Send the exact emitted closed control JSON to the recorded worker task once. |
| `codex_app__list_threads` | Diagnose an ambiguous create by locating candidate tasks; do not infer delivery from listing alone. |
| `codex_app__read_thread` | Read candidate/recorded task state and correlate exact message and response IDs. |
| `codex_app__set_thread_archived` | Archive only after a valid cleanup `STOP_ACK` and inactive task. |

Never replace these actions with a fork, project-scoped task, same-project worktree, local
app-server client, shell automation, browser automation, or peer-root message.

Each mutating action envelope has `action_schema_version: 1`, a unique `action_id`, exact `tool`
and `arguments`, a checkpoint precondition containing the committed version/hash and
the exact outbox message ID/type/payload digest plus `outbox_delivery_status: "PLANNED"`, and
explicit instructions for success, known failure, and unknown outcome. Immediately before every
task mutation, rerun `pending-action` and execute only the action it returns after rechecking pilot
mode, reconciliation, live quiescence, artifact continuity, and assignment snapshot. Do not
execute a stale action envelope copied from an earlier planning response.

## Exact create input

Call `codex_app__create_thread` with logically exactly:

```json
{
  "prompt": "<the helper-emitted complete bootstrap>",
  "target": {
    "type": "projectless"
  }
}
```

Omit `projectId`, `environment`, `startingState`, `directoryName`, `model`, and `thinking`. Never
use `fork_thread`. Persist the returned task ID through `record-worker-created` before later sends.

## Exact message, read, list, and archive inputs

For a send action, call `codex_app__send_message_to_thread` with the helper-emitted input:

```json
{"threadId":"recorded-thread-id","prompt":"<exact emitted packet>"}
```

Include `hostId` only when the create result supplied one and it was recorded. Do not reserialize,
wrap, summarize, or append prose to the emitted packet.

For a read action, use the recorded task identity, `turnLimit: 4`, and `includeOutputs: false`;
include the recorded `hostId` only when present. For ambiguous creation, use
`codex_app__list_threads` with the helper-supplied query and limit 20, then read plausible
candidates. A list result alone is not delivery proof.

For the archive action, call `codex_app__set_thread_archived` with exactly the recorded `threadId`
and `archived: true`. Never archive by title or an inferred task identity.

## Helper command sequence

Use current CLI `--help` output as the exact option contract. The protocol-v2 sequence is:

| Phase | Helper command | External action or R0 decision |
|---|---|---|
| Validate | `doctor` | No task or Glacier call. |
| Activate | `activate-pilot` | Require explicit `--user-authorized-offline-worker`; durably enter pilot before bootstrap. |
| Deactivate | `deactivate-pilot` | Restore single-root only while unregistered or fully archived. |
| Planned resume | `pending-action` | Recover the exact already planned action; do not regenerate it. |
| Unknown diagnosis | `diagnose-unknown` | Emit read-only list/read diagnostics; never resend unknown delivery. |
| Verified nondelivery recovery | `requeue-known-nondelivery` | Only from unknown/rejected non-create delivery, require exact nondelivery and inactive-task evidence, and preserve the same outbox ID/payload for `pending-action`. |
| Pre-worker prompt migration | `reconcile-prompt` | Adopt reviewed prompt bytes only while unregistered, with no task or outbox. |
| Bootstrap intent | `plan-bootstrap` | Commit outbox before create. |
| Create success | `record-worker-created` | Record the returned task identity; do not use for an unknown outcome. |
| Send result | `record-delivery` | Record `ACKNOWLEDGED`, `REJECTED`, or `UNKNOWN`; do not infer success. |
| Response-free control completion | `complete-control-turn` | After an acknowledged ACK/rebase send, require the matching source task and an independently observed inactive turn before clearing assignment/outbox and returning idle. |
| Attestation | `accept-attestation` | Accept only exact projectless/no-Glacier/no-workspace PASS from the recorded source after `read_thread` verifies the response turn inactive. |
| Assignment intent | `plan-assignment` | Commit one sanitized packet before send. |
| Result receipt | `accept-result` | After source-bound `read_thread` verifies an inactive response turn, validate closed schema, transport identity, replay, epoch, assignment snapshot, artifact digests, and protected live-state digest. |
| Result recovery | `verify-pending-result` | Read-only revalidation of the exact source-bound pending packet after a crash; reports current snapshot drift without changing state. |
| Protocol rejection | `accept-protocol-reject` | Require matching source and inactive response turn, then correlate the rejection and enter fail-closed protocol state. |
| Semantic disposition | `commit-result` | R0 supplies honest accepted/duplicate/rejected decision after canonical writes. |
| ACK intent | `plan-ack` | Build and journal the ACK only after durable semantic disposition. |
| Stop intent | `plan-stop` | Use reason `CLEANUP` for final shutdown. |
| Stop receipt | `accept-stop-ack` | Require correlated ID, epoch/assignment, zero children, no files/live actions, and R0's independent inactive-task verification. |
| Archive result | `record-archived` | Record acknowledged, rejected, or unknown outcome; only acknowledged/verified archive completes lifecycle. |

Planning is dry-run by default. Commit only with the latest expected canonical version/hash. Treat
all secret-safe helper errors as fail-closed codes; do not pass the rejected raw packet into logs.

## Outbox and ambiguity

Before any create or send, durably store the unique message ID, packet/action type, restricted
canonical payload digest, expected response type, and delivery state. `PLANNED` means no external
success has been observed. `ACKNOWLEDGED` requires direct task-tool evidence. `REJECTED` records a
known task-tool rejection. `UNKNOWN` requires diagnosis.

For an ambiguous create:

1. record ambiguity rather than calling create again;
2. list recent tasks;
3. read plausible candidates;
4. correlate the unique bootstrap message ID and exact payload; and
5. either record the one matching task or remain fail-closed.

For an ambiguous send, read the recorded destination and correlate the exact message ID. Never
send a logically new object with an old ID. Codex coordination ambiguity does not relax Glacier's
stricter unknown-delivery lock.

Once `UNKNOWN` or `REJECTED` is durable, changing it to another delivery status requires exact
task evidence and `--delivery-evidence-verified`. Without that assertion, `record-delivery` and
`record-archived` fail closed. Use the separate nondelivery requeue gate when the evidence proves
the message did not arrive.

If exact read evidence proves an `UNKNOWN` or `REJECTED` non-create action was not delivered and
the destination task is inactive, `requeue-known-nondelivery --nondelivery-verified
--task-inactive` changes only its delivery status back to `PLANNED`. The outbox ID, type, payload,
and digest remain byte-for-byte bound, and `pending-action` reconstructs that identical action.
Never use this command for create-task ambiguity: resolve one exact task or fall back.

Before a planned assignment send, load its durable packet and compare its message and assignment
IDs, encoded and explicit leadership epoch, base version/hash, prompt version/digest, rules and
ledger digests, protected live-state digest, and active checkpoint metadata. Epoch rollover or any
other mismatch makes the packet stale even if its outbox digest itself is intact.

Bootstrap reconstruction must match the current pinned worker-prompt bytes and bootstrap identity.
ACK and rebase sends must still match the current leadership epoch plus their durable assignment,
result, disposition, and processed-message correlations. STOP and archive are fail-safe controls:
execute only their exact durable outbox payloads under the cleanup lifecycle, never a regenerated
variant.

## Request/response turns

Each send starts a task turn. Read the task until that turn is terminal and validate exactly one
response object when one is required. ACK and `REBASE_REQUIRED` require no response object, but
acknowledged task-tool delivery leaves the lifecycle pending and emits a read action. R0 must read
the matching task, independently observe the control turn inactive, then commit
`complete-control-turn --source-thread-id <recorded-id> --task-inactive` with the latest fence.
Only that transition clears the assignment/outbox and returns the worker to `IDLE`; do not dispatch
another assignment first. There is no autonomous worker push, peer-to-peer root communication,
durable event subscription, or background heartbeat.

Every inbound acceptance is both source-bound and terminal-turn-bound. After `read_thread`, pass
the recorded task ID plus `--task-inactive` to `accept-attestation`, `accept-result`, or
`accept-protocol-reject`; use the same gate for `accept-stop-ack`. Never accept a packet copied from
another task or while its producing turn is still active.

## Cleanup gate

Cleanup is fail-safe control, not permission for new work. Unknown live Glacier delivery,
active/prepared state, reconciliation-required state, and a live mutation lock continue to block
bootstrap, assignments, results, ACK/rebase planning, and Glacier changes, but do not block the
exact STOP/ACK/archive sequence for an already registered worker. The narrower gate still requires
a valid hashed `MULTI_ROOT_PILOT` checkpoint, matching protocol-artifact bytes, CAS, and a known
Codex outbox state. It may not replace a planned or unknown task action; resolve or record that
exact outbox action first.

A closed `PROTOCOL_REJECT` from an acknowledged STOP is still an outcome that must be recorded.
Under adverse live recovery, accept it only from the recorded inactive task and only when its
reply, assignment, and epoch match the validated stop outbox payload. It transitions to
`PROTOCOL_BLOCKED` and clears that turn so a fresh CLEANUP stop can be planned; it is never cleanup
or archive evidence.

Final cleanup requires all of the following in order:

1. committed `STOP_ALL` outbox state with `reason_code: "CLEANUP"`;
2. acknowledged send to the recorded worker;
3. a closed `STOP_ACK` correlated to that stop message, epoch, and active assignment;
4. `children_running: 0`, `files_written: []`, and `live_actions: "NONE"`;
5. task read showing no active turn;
6. `accept-stop-ack` committing `ARCHIVE_PLANNED` and emitting the bound archive action;
7. one `codex_app__set_thread_archived` call and a recorded acknowledged, rejected, or unknown
   outcome; and
8. durable `record-archived` transition to `ARCHIVED` only after success or independent archived
   verification.

A non-`CLEANUP` stop is only an emergency fence. Its valid ACK transitions to `STOPPED_FENCED`,
not archive; a fresh durable CLEANUP stop/ACK handshake is still required. Results also require
`children_running: 0`, so no direct worker child may survive into another task turn.

An idle, timed-out, stale, or unresponsive worker is not proof of cleanup. Fence it, record the
blocker, and do not claim archive success without this gate.

## R0 continuity limitation

Productive pilot work may resume only in the same R0 Codex task. This implementation has no
owner-transfer or epoch-bump command, and no one may edit the epoch field manually. A different
root task may use read-only diagnosis and the exact fail-safe STOP/CLEANUP/archive sequence solely
to prevent an orphaned worker, then restore `SINGLE_ROOT`; it may not send queued productive
controls, accept worker output, or touch Glacier. A future productive takeover feature requires a
separate reviewed owner-identity and split-brain-fencing protocol.
