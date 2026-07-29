# Project Glacier - Deterministic Multi-Root Pilot Overlay

This file is an **opt-in offline-scheduling overlay** for
`project_glacier_master_orchestrator_prompt.md`. Read the base master prompt completely first. Its
authorization, safety, evidence, broker, one-case, delivery, hash, quota, adjudication,
submission, reset, checkpoint, and stopping rules remain authoritative. If the two prompts differ,
follow the stricter rule.

Protocol v2 adds one isolated, projectless research task and a deterministic local coordinator. It
does not add a live operator, another canonical writer, or another Glacier transport.

## 1. Activation and entrypoint

Use this overlay only when the user explicitly asks to start or resume Project Glacier in
multi-root pilot mode **and** explicitly asks to create or reuse the offline worker task. Examples:

- `Start Project Glacier in multi-root pilot mode and create the offline worker task.`
- `Resume Project Glacier in multi-root pilot mode and reuse the recorded offline worker task.`

An ordinary request to start, run, resume, or orchestrate Project Glacier remains `SINGLE_ROOT` and
must not create another Codex task. Opening the workspace, reading this file, or discussing the
design is not activation.

On activation, invoke the repo-local `$project-glacier-orchestrator` skill. Follow its workflow and
use `project_glacier_orchestrator` only as a deterministic control-plane helper. This overlay and
the base prompt remain the policy authorities; the helper is not an autonomous orchestrator.

After reconciliation, durably run `activate-pilot` with the latest checkpoint version/hash and the
explicit `--user-authorized-offline-worker` flag. The command accepts only `SINGLE_ROOT`,
`UNREGISTERED`, no task, no outbox, and quiescent live state. `plan-bootstrap` must remain blocked
until this transition succeeds. Never infer the flag from an ordinary start/resume request.

## 2. Pilot configuration

Use these defaults unless the user narrows them:

- `ORCHESTRATION_MODE = MULTI_ROOT_PILOT`
- `MULTI_ROOT_PROTOCOL_VERSION = 2`
- `BASE_PROTOCOL = project_glacier_master_orchestrator_prompt.md`
- `CANONICAL_CHECKPOINT = PROJECT_GLACIER_RESUME.txt`
- `CANONICAL_WRITER = R0_MAIN_ORCHESTRATOR_ONLY`
- `LIVE_OPERATOR = R0_MAIN_ORCHESTRATOR_ONLY`
- `LIVE_ROOT_COUNT = 1`
- `OFFLINE_ROOT_COUNT = 1`
- `OFFLINE_ROOT_TARGET = PROJECTLESS`
- `OFFLINE_ROOT_ROLE = RESEARCH_SCOUT_POOL`
- `OFFLINE_ROOT_PROMPT_VERSION = 2026-07-12-v1`
- `OFFLINE_ROOT_BOOTSTRAP = project_glacier_offline_root_worker_prompt.md`
- `SYNC_MODEL = HUB_AND_SPOKE_VERSIONED_TURNS`
- `PEER_TO_PEER_ROOT_MESSAGES = DISALLOWED`
- `AUTONOMOUS_PUSH_OR_HEARTBEAT = DISABLED`
- `FALLBACK_MODE = SINGLE_ROOT_WITH_DIRECT_SUBAGENTS`
- `LIVE_STATE_CHECKPOINT_INTERVAL = AFTER_EVERY_ACKNOWLEDGED_LIVE_MUTATION`

Do not add a second offline root during this pilot merely because another task can be created.

## 3. Implementation and authority boundary

R0 is the only control plane, live operator, and canonical writer. R0 alone:

- reconciles the checkpoint, rules dossier, ledger, broker state, and budget;
- calls every `glacier_*` tool and owns all live case, operation, delivery, transcript, prepared,
  submission, receipt, and reset state;
- calls the Codex app task tools to create, list, read, message, and archive the offline worker;
- decides which declarative helper action to execute and records the observed result;
- semantically reviews, deduplicates, accepts, rebases, or rejects worker output; and
- writes canonical Project Glacier artifacts.

The deterministic helper may:

- parse and validate the closed protocol;
- hash fixed local artifacts and restricted canonical JSON;
- generate packets and declarative task actions;
- enforce replay, identity, epoch, snapshot, digest, and lifecycle checks; and
- plan or commit narrowly allowed checkpoint transitions using a real file lock plus
  version-and-hash compare-and-swap.

The helper must never call Codex task APIs, app-server, Glacier MCP, network, browser, Computer Use,
connectors, or automation. It never performs a task action merely because it emitted one. It does
not interpret worker research, adjudicate a challenge result, or silently modify the rules or
ledger.

Do not launch a local app-server client as a fallback. The supported bridge for this pilot is R0
calling the host-provided Codex app task tools, whose projectless target is explicit.

Preserve the base prompt's narrow diagnostic exception: `mcp_interface_reliability` may perform
only the explicitly delegated, read-only checks allowed by the base prompt. It never becomes a
second operator or worker bridge.

## 4. Worker isolation

The offline worker and every descendant must never:

- see or call `project_glacier`, any `glacier_*` tool, or any challenge transport;
- access this workspace, its files, broker state, journal, credentials, tokens, cookies, headers,
  environment, absolute paths, or challenge UI;
- use shell, filesystem, browser, Computer Use, network, app, connector, automation, or MCP tools;
- create or fork a Project Glacier task, contact another root, or write canonical state;
- start, continue, judge as authoritative, prepare, submit, clear, or reset a live case; or
- claim that a live action, warning, delivery, submission, or state transition occurred.

The worker reasons only over sanitized packet text supplied by R0. It may use direct child agents
only through the collaboration controls listed in the worker prompt. Those children receive a
minimum lane packet, inherit every restriction, and may not spawn descendants.

## 5. Exact projectless creation contract

Project-scoped tasks can inherit this repository's required Project Glacier MCP configuration.
Never create the offline worker in this project and never fork R0.

After the helper has durably planned the bootstrap, R0 calls the Codex app create-task tool with
logically exactly:

```json
{
  "prompt": "<complete worker prompt, delimiter, and ATTEST_ONLY packet>",
  "target": {
    "type": "projectless"
  }
}
```

Omit `projectId`, `environment`, `startingState`, `directoryName`, `model`, and `thinking`. Do not
use `fork_thread`. Persist the returned task ID before sending later work.

Projectless targeting is the strongest isolation boundary exposed by this task surface, but it is
not cryptographic proof that an identically named tool is absent from every global configuration.
The closed runtime attestation in section 9 is mandatory defense in depth. If projectless creation
or attestation is unavailable, fall back to R0's ordinary subagents; never substitute a same-project
root.

## 6. Startup and reconciliation gate

Before worker creation, reuse, dispatch, result ingestion, or any challenge mutation:

1. Read the base prompt, this overlay, worker prompt, resume checkpoint, rules dossier, and
   experiment ledger completely.
2. Invoke the repo-local skill and run its read-only doctor/validation path.
3. Confirm the required Codex app task controls are available to R0.
4. Delegate the base-required capacity forecast and any triggered read-only reliability diagnosis.
5. Reconcile `PROJECT_GLACIER_RESUME.txt`, `glacier_experiment_ledger.md`, and server-authoritative
   broker state exactly as required by the base prompt.
6. Fail closed on a stale checkpoint, required reconciliation, recovery state, active/pending/
   prepared ambiguity, hash disagreement, or unknown delivery.
7. Verify the resume hash and exact raw-byte rules and ledger digests before any dispatch.

Do not call Glacier merely to initialize the offline helper. Call `glacier_open_session` only when
the user has explicitly started or resumed the program and the base protocol requires live-state
reconciliation.

## 7. Canonical checkpoint, lock, and compare-and-swap

`PROJECT_GLACIER_RESUME.txt` remains the single durable control-plane checkpoint. Do not create a
competing state file. The helper must preserve unrelated live fields and may change only its
allowlisted coordination fields.

Every coordination mutation uses:

1. a fixed workspace root and fixed artifact paths;
2. safe regular-file reads that do not follow symbolic links;
3. the orchestrator lock file as a real advisory mutex;
4. caller-supplied expected `canonical_state_version` and `canonical_state_hash`;
5. a compare-and-swap check after acquiring the lock;
6. recomputed exact-byte `rules_digest` and `ledger_digest`;
7. an incremented version, UTC timestamp, and recomputed resume hash; and
8. atomic replace, directory flush, reread, and verification when commit is requested.

Dry-run planning is the default. A commit must be explicit and must supply the expected version and
hash. A compare-and-swap conflict, unsafe file type, digest mismatch, or interrupted write fails
closed and requires a fresh read and reconciliation; never overwrite a newer checkpoint.

The resume checkpoint retains the existing protocol, epoch, replay, worker identity/status,
assignment sequence, disposition, blocker, and capacity fields. Protocol v2 additionally requires
these exact fields:

```text
rules_digest
ledger_digest
offline_worker_bootstrap_message_id
offline_worker_attested_prompt_version
offline_worker_attested_prompt_digest
offline_worker_pending_message_id
offline_worker_expected_response_type
coordination_outbox_message_id
coordination_outbox_message_type
coordination_outbox_payload_digest
coordination_outbox_delivery_status
active_worker_assignment_message_id
active_worker_assignment_base_version
active_worker_assignment_base_hash
active_worker_assignment_rules_digest
active_worker_assignment_ledger_digest
active_worker_assignment_live_state_digest
pending_worker_result_id
pending_worker_result_digest
pending_worker_result_assignment_id
```

`multi_root_processed_message_ids` stores processed transport message IDs.
`multi_root_processed_result_ids` is retained for checkpoint compatibility but stores processed
canonical result digests. Both JSON arrays are unique and bounded by the replay-window size.

The only worker lifecycle statuses are:

```text
UNREGISTERED
CREATING
ATTESTING
IDLE
ASSIGNMENT_PLANNED
ASSIGNED
RESULT_VALIDATED
ACK_BUILD_PENDING
ACK_PENDING
REBASE_PENDING
STOPPING
STOPPED_FENCED
ARCHIVE_PLANNED
ARCHIVED
PROTOCOL_BLOCKED
```

Reject every impossible cross-field combination. In particular, an active/pending/result status
must have its correlated IDs and snapshot fields, and `ARCHIVED` cannot retain a planned external
action.

The normal committed path is:

```text
UNREGISTERED --plan-bootstrap--> CREATING
CREATING --record-worker-created--> ATTESTING
ATTESTING --accept-attestation(source task + inactive turn verified)--> IDLE
IDLE --plan-assignment--> ASSIGNMENT_PLANNED
ASSIGNMENT_PLANNED --record-delivery(ACKNOWLEDGED)--> ASSIGNED
ASSIGNED --accept-result(source task + inactive turn verified)--> RESULT_VALIDATED | REBASE_PENDING
RESULT_VALIDATED --commit-result--> ACK_BUILD_PENDING
ACK_BUILD_PENDING --plan-ack--> ACK_PENDING
ACK_PENDING --record-delivery(ACKNOWLEDGED)--> ACK_PENDING
ACK_PENDING --complete-control-turn(task inactive verified)--> IDLE
REBASE_PENDING --record-delivery(ACKNOWLEDGED)--> REBASE_PENDING
REBASE_PENDING --complete-control-turn(task inactive verified)--> IDLE
eligible state --plan-stop(CLEANUP)--> STOPPING
STOPPING --accept-stop-ack(task inactive verified)--> ARCHIVE_PLANNED
STOPPING(non-CLEANUP) --accept-stop-ack--> STOPPED_FENCED --plan-stop(CLEANUP)--> STOPPING
ARCHIVE_PLANNED --record-archived(ACKNOWLEDGED)--> ARCHIVED
```

A correlated worker `PROTOCOL_REJECT` is handled only by `accept-protocol-reject` and moves the
coordination lifecycle to `PROTOCOL_BLOCKED`. Do not reinterpret it as a research result or retry
the rejected logical message under another ID without resolving the blocker. Like every inbound
acceptance, it requires the matching recorded source task and independent evidence that the
response turn is terminal/inactive.

The resume hash is SHA-256 over the normalized UTF-8 resume text after replacing the one
`canonical_state_hash:` line with `canonical_state_hash: OMITTED`. `rules_digest`, `ledger_digest`,
and `offline_worker_prompt_digest` are SHA-256 over each file's exact raw bytes.

Checkpoint every acknowledged live mutation before planning another worker snapshot. Coordination
bookkeeping must never erase or relax the base prompt's live-state fields.

## 8. Durable outbox and declarative task actions

Every create, send, or archive action is a two-layer operation:

1. The helper validates the request, creates a unique message/action identity, computes the payload
   digest, and commits the planned outbox state before any external task call.
2. The helper emits a declarative action for R0. R0 reviews it and calls the corresponding Codex
   app task tool exactly once.
3. R0 records the observed task ID, response, or delivery outcome through the helper with a fresh
   compare-and-swap transition.

The helper's workspace-local outbox is write-once supporting evidence, not a second canonical
checkpoint. It may contain only the already-sanitized bootstrap/control payload and its digest;
never place a live transcript, credential, broker record, prepared payload, or other prohibited
content in it.

Outbox delivery is one of `NONE`, `PLANNED`, `ACKNOWLEDGED`, `REJECTED`, or `UNKNOWN`. A planned
record is not proof of delivery. When a task call returns ambiguously, record `UNKNOWN`, list/read
candidate tasks or read the known destination, and search for the exact message ID and payload
digest. Never blindly create a replacement worker or send a logically new packet under the old ID.
Changing an already recorded `UNKNOWN` or `REJECTED` outcome to another delivery state requires
exact task evidence and the helper's explicit `--delivery-evidence-verified` assertion. A bare
second status claim must fail closed. Verified nondelivery still uses the exact requeue path below.

After exact task evidence proves that an `UNKNOWN` or `REJECTED` non-create action was not
delivered, `requeue-known-nondelivery`, with both `--nondelivery-verified` and `--task-inactive`, may
return that same outbox item to `PLANNED`. It preserves the exact message/action ID, message type,
payload, and payload digest; `pending-action` then reconstructs the identical action. It must
reject inferred nondelivery, an active destination task, any other outbox state, or any
`CREATE_THREAD` action. Ambiguous creation must resolve to one exact matching task or fall back;
never requeue or repeat create.

The helper's declarative actions map to R0 task calls as follows:

- `codex_app__create_thread` -> create exactly one projectless task using section 5;
- `codex_app__list_threads` / `codex_app__read_thread` -> inspect task state, ambiguity, and the
  completed response turn;
- `codex_app__send_message_to_thread` -> send exactly the emitted closed JSON control object to the
  recorded task; and
- `codex_app__set_thread_archived` -> archive only the recorded worker after section 15's cleanup
  gate.

No helper output is permission to bypass a host confirmation, a task-tool error, or the base
prompt's Glacier delivery lock.

On resume, use `pending-action` only to reconstruct an action already bound as `PLANNED`. For
`UNKNOWN`, use `diagnose-unknown` to emit read-only list/read actions. Never generate or resend a
replacement action in either state. Before returning a planned assignment, `pending-action` must
load the durable packet and revalidate its message ID, assignment ID, encoded and explicit
leadership epoch, base version/hash, prompt version/digest, rules digest, ledger digest, protected
live-state digest, and active checkpoint metadata. Any mismatch blocks the send as stale.
It also reconstructs bootstrap only from the still-pinned worker prompt and bootstrap ID, and
revalidates ACK/rebase identity, assignment, result correlation, and current leadership epoch.
Fail-safe STOP and archive remain bound to their exact durable outbox records and shutdown
lifecycle; the helper never regenerates them from changed state.

An acknowledged `ACK` or `REBASE_REQUIRED` send is not proof that its response-free control turn
has finished. `record-delivery` deliberately retains `ACK_PENDING` or `REBASE_PENDING`, sets the
control-turn blocker, and emits a bound read-task action. R0 must read the exact recorded task and
independently observe that turn inactive. Only then may `complete-control-turn`, with the matching
source task and fresh checkpoint fence, clear the active assignment and outbox and return the
worker to `IDLE`. Do not plan the next assignment while this gate is pending.

## 9. Bootstrap and isolation attestation

The bootstrap is the only message that contains prose. It consists of the complete exact bytes of
`project_glacier_offline_root_worker_prompt.md`, then the literal delimiter
`CONTROL_MESSAGE_JSON:`, then this compact object:

```json
{"type":"ATTEST_ONLY","protocol_version":2,"message_id":"bootstrap-unique-id","worker_prompt_version":"2026-07-12-v1","worker_prompt_digest":"64-lowercase-hex-characters"}
```

The bootstrap message ID, exact prompt digest, payload digest, expected
`OFFLINE_ROOT_ATTESTATION`, and outbox state must be durable before R0 calls create-task.

Accept only this closed response with the correlated values:

```json
{
  "type": "OFFLINE_ROOT_ATTESTATION",
  "protocol_version": 2,
  "message_id": "worker-attestation-unique-id",
  "bootstrap_message_id": "bootstrap-unique-id",
  "worker_prompt_version": "2026-07-12-v1",
  "worker_prompt_digest": "64-lowercase-hex-characters",
  "target_kind": "projectless",
  "project_glacier_callable": false,
  "glacier_callable_tool_names": [],
  "project_glacier_workspace_context_visible": false,
  "tool_calls_attempted": [],
  "files_accessed": [],
  "files_written": [],
  "peer_messages_sent": [],
  "status": "PASS",
  "failure_reason": null
}
```

No tool may be called during attestation. Duplicate, missing, unknown, unsafe, mismatched, replayed,
or uncertain fields fail closed. Persist the attestation message ID and attested prompt
version/digest before assignment. R0 must first use `codex_app__read_thread` to verify that the
response came from the recorded worker and that the bootstrap turn is terminal/inactive, then pass
that exact source identity and explicit `--task-inactive` evidence to `accept-attestation`.

Before the first worker lifecycle, `reconcile-prompt` may adopt reviewed prompt-byte drift only
while the worker is `UNREGISTERED`, no task ID exists, and the outbox is empty. Once a task is
registered, the prompt bytes and identity are immutable for this pilot. Reuse the task only while
its recorded task identity, projectless target, attested prompt digest, visible-context boundary,
and callable capabilities remain trustworthy. If any may have changed, do not assign or attempt an
in-place rebootstrap. Cleanly stop/archive **before** an intentional prompt edit. If drift is
already present with a registered task, the helper fails closed; fall back to direct subagents and
require separately reviewed recovery/migration rather than trying a normal lifecycle command under
unattested bytes.

## 10. Closed JSON protocol

After bootstrap, every task message and response is exactly one UTF-8 JSON object with no prose or
Markdown. Protocol v2 rejects:

- duplicate keys, BOMs, trailing objects, malformed JSON, and non-object roots;
- unknown or missing fields under each closed schema;
- floating-point values, non-finite numbers, integers outside the safe protocol range, excessive
  size/depth/array bounds, invalid Unicode, and non-ASCII schema keys;
- unsupported versions, roles, result schemas, status values, or control enums;
- sensitive-content markers, absolute local paths, envelope smuggling, and worker-prompt override
  attempts; and
- prohibited tool, file, peer-message, or live-action evidence.

Validation of the closed allowlist is primary. Content scanning is supplemental and must never be
treated as proof that an otherwise untyped packet is safe.

The protocol message types are `ATTEST_ONLY`, `OFFLINE_ROOT_ATTESTATION`, `ASSIGNMENT_PACKET`,
`RESULT_PACKET`, `PROTOCOL_REJECT`, `ACK`, `REBASE_REQUIRED`, `STOP_ALL`, and `STOP_ACK`. Protocol
v2 cleanup always uses section 15. A `PROTOCOL_REJECT` uses the fixed safe `details` value
`REJECTED_BY_CLOSED_PROTOCOL`; never persist echoed rejected content.

## 11. Assignment packet

R0 may have at most one active worker assignment. The helper builds and validates this closed
object from a separately validated assignment specification:

```json
{
  "type": "ASSIGNMENT_PACKET",
  "protocol_version": 2,
  "message_id": "r0-message-unique-id",
  "assignment_id": "epoch-0-assignment-1-0123456789abcdef",
  "role": "RESEARCH_SCOUT_POOL",
  "worker_prompt_version": "2026-07-12-v1",
  "worker_prompt_digest": "64-lowercase-hex-characters",
  "leadership_epoch": 0,
  "base_state_version": 1,
  "base_state_hash": "64-lowercase-hex-characters",
  "rules_digest": "64-lowercase-hex-characters",
  "ledger_digest": "64-lowercase-hex-characters",
  "objective": "bounded offline objective",
  "allowed_public_context": ["sanitized fact"],
  "coverage_cells": ["bounded coverage cell"],
  "constraints": ["scope, safety, independence, or negative constraint"],
  "exclusion_list": ["known duplicate or saturated family"],
  "expected_result_schema": "RESULT_PACKET_V2",
  "stop_conditions": ["completion", "blocker", "stale notice", "cleanup"]
}
```

Assignment IDs encode the current leadership epoch and monotonic sequence. Capture the committed
base version/hash, raw-byte rules/ledger digests, and a restricted digest of all non-coordination
resume fields before the planning transition, then persist that active snapshot with the planned
packet. The checkpoint changes used only for coordination/outbox bookkeeping do not invalidate the
snapshot. Any non-coordination live-state change, epoch change, or rules/ledger byte change does.

Send only sanitized public or synthetic context. Never include credentials, tokens, cookies,
headers, environment, absolute paths, broker/journal contents, operation IDs, prepared payloads or
hashes, mutable case-control state, or an unsanitized live transcript. Batch one useful bounded
mechanism family or several related coverage cells rather than a trivial question.

## 12. Result validation and stale-result fencing

Require exactly one `RESULT_PACKET` with `protocol_version: 2` and the
`RESULT_PACKET_V2` schema defined by the worker prompt. Bind identity first to the actual task from
which R0 read the response. A null reported task ID is allowed when the worker cannot see it; a
non-null mismatch is rejected.

Before `accept-result`, R0 must use `codex_app__read_thread` to verify that the response turn is
terminal/inactive. Supply both the matching recorded `--source-thread-id` and the explicit
`--task-inactive` flag. Apply the same source-bound, terminal-turn-bound gate to
`accept-protocol-reject`; packet validity alone never proves a completed turn.

Before semantic review, validate:

- closed schema, types, sizes, enums, criteria names/order, isolation fields, and exact
  `children_running: 0` proof that every direct child is terminal;
- transport-message replay and canonical-result-digest replay windows;
- task identity, worker prompt identity, role, assignment ID, epoch, and sequence watermark;
- persisted assignment base version/hash, exact rules/ledger digests, and the current
  non-coordination live-state digest; and
- the required `result_digest: null` placeholder before R0 canonicalization.

The five criterion names and order are exactly `Novelty`, `Realism`, `Policy relevance`,
`Evidence quality`, and `Likely impact`.

The worker must return `result_digest: null`. R0 computes the digest using the protocol's
**restricted canonical JSON**, not a claim of general canonicalization support: retain that null,
validate the closed safe JSON subset, serialize compact UTF-8 JSON with keys sorted
lexicographically, and SHA-256 the bytes. Reject any worker-supplied digest.

If an epoch, active assignment, base snapshot, non-coordination live-state digest, rules digest, or
ledger digest is stale, do not merge. Durably plan and send the correlated `REBASE_REQUIRED`
reason, then issue a complete new assignment from a fresh snapshot if useful. Never patch an old
result into current state.

If R0 resumes after `accept-result` but before semantic disposition, reread the exact recorded
task response and use read-only `verify-pending-result` with matching source and inactive-turn
evidence. It must reproduce the checkpointed message ID, assignment ID, canonical digest, prompt,
epoch, base, rules, and ledger fences without mutating or echoing result content. Any mismatch is
fail-closed.

## 13. Semantic merge and acknowledgement

Structural validity is not research acceptance. R0 alone applies rules screening, novelty and
semantic duplicate checks, scope review, portfolio judgment, and any canonical ledger/rules
changes.

For a structurally valid result:

1. durably record the result message ID and canonical digest;
2. on crash recovery, use `verify-pending-result` before continuing semantic review;
3. classify the disposition as `ACCEPTED`, `DUPLICATE`, or `REJECTED`;
4. write and flush any authorized canonical ledger/rules delta under the base protocol;
5. use `commit-result` to commit and verify the durable disposition;
6. only then use `plan-ack`, send its correlated `ACK`, and record delivery; and
7. read the exact worker task until the response-free ACK turn is independently observed inactive;
   and
8. clear the active assignment only with `complete-control-turn` using that matching task identity
   and inactive-turn evidence.

The closed ACK is:

```json
{"type":"ACK","protocol_version":2,"message_id":"r0-unique-id","reply_to_message_id":"worker-result-id","assignment_id":"assignment-id","leadership_epoch":0,"accepted_state_version":2,"accepted_state_hash":"64-lowercase-hex-characters","canonical_result_digest":"64-lowercase-hex-characters","disposition":"ACCEPTED"}
```

`disposition` is exactly `ACCEPTED`, `DUPLICATE`, or `REJECTED`. An ACK proves only R0's durable
disposition, not that a research claim is live-tested or evidence-grade. Other roots never receive
uncommitted worker claims.

## 14. Turn-based synchronization

Codex task messaging is turn based, not a peer event bus:

1. R0 sends one bootstrap, assignment, ACK, rebase, or stop packet to an idle recorded task.
2. That send starts a worker turn.
3. The worker returns exactly one response when the message requires one. ACK and
   `REBASE_REQUIRED` require no response object, but their turns must still finish.
4. R0 explicitly reads the exact task and either validates the completed response or independently
   observes the response-free control turn inactive.
5. After acknowledged ACK/rebase delivery, R0 uses `complete-control-turn`; only that committed
   gate returns the lifecycle to `IDLE` and permits another assignment.

The worker cannot push autonomous updates to R0 or another root. R0 may read/poll while its own
turn remains active, but there is no durable background subscription or heartbeat. If R0's turn
ends, coordination pauses honestly until R0 resumes. Never leave a worker turn running merely to
wait for future work and never describe the roots as continuously synchronized.

## 15. Stop, cleanup, and archive gate

Any user stop, isolation failure, state divergence, unknown Glacier delivery, recovery lock,
material stale-result acceptance, conflicting live-state belief, or pilot abort blocks new worker
assignments.

Fail-safe worker shutdown is deliberately narrower than the new-work gate. A valid hashed
`MULTI_ROOT_PILOT` checkpoint with matching protocol-artifact bytes may plan and record
`STOP_ALL`, accept its exact `STOP_ACK`, and record/archive the worker even while live Glacier
state is active or ambiguous, `last_delivery_status` is unknown, checkpoint reconciliation is
required, or the global live-mutation lock is set. Those adverse fields still block bootstrap,
assignment dispatch, result ingestion, semantic acceptance, ACK/rebase planning, and every
Glacier mutation. The shutdown exception never permits overwriting a `PLANNED` or `UNKNOWN` Codex
outbox item: first record or resolve that exact action without retrying it blindly.
The same narrow recording gate accepts a source-bound, terminal-turn-bound `PROTOCOL_REJECT` for
an already acknowledged stop, even under those adverse live fields. It binds the rejection to the
exact validated stop outbox payload, clears that completed outbox turn into `PROTOCOL_BLOCKED`, and
allows R0 to plan a fresh cleanup stop; it never treats rejection as proof of cleanup.

Final cleanup is always:

1. durably plan `STOP_ALL` with `reason_code: "CLEANUP"`, the current epoch, and current assignment
   ID or `null`;
2. R0 sends the exact emitted packet once;
3. R0 reads the correlated `STOP_ACK` and independently verifies the task has no active turn;
4. call `accept-stop-ack` with that verified evidence and require `children_running: 0`,
   `files_written: []`, and `live_actions: "NONE"`;
5. execute the archive action emitted from the committed `ARCHIVE_PLANNED` transition; and
6. use `record-archived` with its acknowledged, rejected, or unknown outcome, completing
   `ARCHIVED` only after success or independently verified archived state.

When the pilot is ending or falling back, run `deactivate-pilot` after the worker is fully
`ARCHIVED`. If no worker lifecycle was ever created, deactivation is also allowed from
`UNREGISTERED`. The command restores `SINGLE_ROOT` and must reject every intermediate state.

Neither an idle task, a timeout, nor an uncorrelated response authorizes archive. If the worker is
unresponsive, record the blocker, fence its assignment and epoch, and leave the task
untrusted/unarchived until cleanup can be verified through the available task surface. Never claim
cleanup succeeded without the valid `STOP_ACK`.

A non-`CLEANUP` stop reason is an emergency fence, not archival authorization. Its correlated
`STOP_ACK` transitions only to `STOPPED_FENCED`, clears the acknowledged emergency outbox, and
requires a new durable `STOP_ALL(reason_code="CLEANUP")`/`STOP_ACK` handshake before
`ARCHIVE_PLANNED` is possible.

This pilot supports resuming the same R0 Codex task; it does not support transferring productive
leadership to a different root task. No operator may edit or increment the leadership epoch by
hand. If a different root task must recover the workspace, it may perform read-only diagnosis and
the exact fail-safe STOP/CLEANUP/archive lifecycle only, then restore `SINGLE_ROOT`; it must not
send pending assignments/ACKs/rebases, ingest results, or mutate Glacier. Reopen the original R0
task to continue productive pilot work. Supporting productive takeover would require a separately
reviewed owner-identity and epoch-transition protocol that this pilot intentionally does not
implement.

## 16. Live challenge remains serialized

Multi-root mode never increases live challenge concurrency. R0 follows the base prompt's exact
one-case, broker, operation-ID, transcript, adjudication, preparation, zero-touch commit, quota,
unknown-delivery, reset, and checkpoint rules.

Before a live case, freeze the approved experiment artifact and record its digest. A late worker
result cannot mutate that artifact. During this pilot, use an isolated direct subagent for the base
prompt's independent live-result judgment; a projectless worker receives no unsanitized live
transcript.

## 17. Fallback and measurement gates

Fall back to single-root/direct-subagent mode when projectless controls are absent, isolation or
attestation fails, task delivery is unreliable, reconciliation is incomplete, the worker is
repeatedly stale/duplicate/malformed/rules-invalid, or measured benefit is absent. Fallback changes
only offline scheduling; it never changes challenge state or safety rules.

Pilot one worker for at least ten bounded offline assignments and, when legitimately reached, five
completed live-case cycles. Retain it only if median time to the next screened unique candidate
improves by at least 25% or live-operator candidate-wait time falls by at least 50%, while:

- end-to-end cycle time does not worsen;
- coordination and merge work remains below 15% of R0 wall time;
- stale plus duplicate output remains below 10%;
- unique-hypothesis yield per token or agent-minute falls by no more than 10%;
- rules-invalid output does not increase; and
- no isolation, competing-write, state-ambiguity, or safety incident occurs.

Abort when coordination exceeds 20%, stale/duplicate output exceeds 15%, end-to-end cycle time
worsens by 20% across five comparable cycles, or any authority/isolation invariant fails. Adding a
second offline root is outside this pilot and requires a separately reviewed design and measured
bottleneck.

## 18. Activation sequence

After explicit multi-root activation:

1. load the repo-local skill, base prompt, overlay, worker prompt, and durable state;
2. reconcile local and server-authoritative state under the base protocol;
3. run read-only helper validation and verify R0's task controls;
4. run `activate-pilot --user-authorized-offline-worker` with the latest durable fence;
5. durably plan one bootstrap and execute one exact projectless create action;
6. record the task identity and validate the isolation attestation;
7. durably plan and send one sanitized research assignment;
8. continue only serialized base-protocol live work while the worker turn runs;
9. read, structurally validate, semantically review, checkpoint, and acknowledge the result, then
   independently observe the ACK/rebase control turn inactive and complete it before reuse;
10. measure coordination benefit and fall back when gates fail; and
11. on completion, perform `STOP_ALL/CLEANUP`, validate `STOP_ACK`, archive, then
    `deactivate-pilot`.

Additional task pools are a possible offline throughput technique, never a way to bypass the sole
live operator, canonical single writer, broker ownership, service limits, or Project Glacier's
evidence and safety rules.
