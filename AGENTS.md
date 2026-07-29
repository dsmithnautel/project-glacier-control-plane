# Project Glacier workspace instructions

Assume the user is new to this domain and explain important concepts, risks, and decisions in
plain language.

This repository stores reusable runtime templates under `templates/runtime/`. The canonical
root-level checkpoint, ledger, rules dossier, coverage, findings, and local `.codex` registration
are deliberately Git-ignored because they can contain machine paths or challenge state. If those
local files are absent, initialize them with `python3 scripts/initialize_workspace.py`, render
local registration with `python3 scripts/configure_codex.py`, and reconcile before any challenge
mutation. Never substitute a version-controlled example for server-authoritative broker state.

When the user asks to run, resume, or orchestrate Project Glacier:

1. Read `project_glacier_master_orchestrator_prompt.md` completely before taking challenge action.
   A plain request uses the existing `SINGLE_ROOT` mode and must not create a separate Codex task.
   If the user explicitly requests multi-root pilot mode and explicitly asks to create or reuse the
   offline worker task, also read `project_glacier_multi_root_orchestrator_prompt.md` and
   `project_glacier_offline_root_worker_prompt.md` completely and invoke the repo-local
   `$project-glacier-orchestrator` skill. Follow the multi-root file only as an offline-scheduling
   overlay; the base master prompt remains authoritative for all live, safety, broker, delivery,
   adjudication, submission, reset, and checkpoint rules.
2. Follow that prompt as the operating protocol and load its durable checkpoint/resume artifacts
   before starting new experiments.
3. Use only the registered `project_glacier` MCP server and its `glacier_*` tools for programmatic
   challenge access. Do not replace it with generic HTTP, shell networking, or browser scripting.
4. Never request, reveal, copy, or persist the participant credential, redirect token, or cookies.
5. Respect the broker's one-case rule, operation IDs, hashes, delivery states, quotas, and
   fail-closed errors. Never retry or work around an unknown delivery state.
6. Treat `glacier_prepare_submission` as a preview only. This workspace explicitly authorizes
   zero-touch `glacier_commit_submission` calls when the broker reports submission enabled. Do not
   ask for per-case confirmation, but enforce the exact prepared hash, honest adjudication,
   configured quota, and all unknown-delivery stop rules.
7. When MCP startup, tool discovery, broker, Codex UI, or challenge-interface blockers arise,
   delegate read-only diagnosis to the project custom agent `mcp_interface_reliability`. Its
   Computer Use capability is for observation and troubleshooting only; it must never type into or
   activate challenge controls, replace the Glacier MCP transport, expose credentials, or bypass
   confirmations, restrictions, delivery locks, or policy.
8. At orientation, checkpoints, material throughput changes, and broker-budget thresholds, delegate
   read-only forecasting to `capacity_bottleneck_optimizer`. It may identify and propose fixes for
   submission, latency, concurrency, token, context, and workflow bottlenecks, but it must not
   silently change configuration, quotas, challenge state, safety gates, or adjudication rules.
9. In multi-root pilot mode, this Project Glacier task is R0, the sole live operator and canonical
   writer. Create at most one offline research root during the pilot, with task target exactly
   `projectless`; never fork R0 or create a same-project worker. R0 alone calls the Codex app task
   tools. Use the deterministic helper only to validate, hash, journal, compare-and-swap, and emit
   declarative task actions; it must never call Codex, Glacier, app-server, browser, network, or
   external systems. The worker receives only sanitized protocol-v2 packets, has no
   Glacier/workspace access, and may not write canonical artifacts or communicate peer-to-peer. If
   task isolation or attestation fails, fall back to R0's ordinary subagents. Supply the helper's
   `--user-authorized-offline-worker` activation flag only for the explicit multi-root request, and
   restore `SINGLE_ROOT` through its guarded deactivation command after full cleanup or before any
   worker lifecycle begins. Productive work may resume in this same R0 task, but the pilot does not
   support leadership transfer to another root task. A different task may only diagnose and
   complete exact fail-safe worker cleanup/archive before restoring `SINGLE_ROOT`; it must not edit
   the epoch, dispatch work, ingest results, or mutate Glacier.
10. Before worker dispatch, result ingestion, or any challenge mutation, reconcile
    `PROJECT_GLACIER_RESUME.txt`, `glacier_experiment_ledger.md`, and server-authoritative broker
    state under the base protocol. A stale checkpoint, active/pending/prepared ambiguity, recovery
    state, or unknown delivery is fail-closed. Checkpoint every acknowledged live mutation before
    dispatching another worker snapshot. These adverse states must not prevent fail-safe stop and
    archive of an already registered offline worker: the helper's narrower shutdown gate may only
    record an already planned task outcome or perform the exact STOP/ACK/archive lifecycle, never
    dispatch new work, ingest a result, or mutate Glacier.
11. In multi-root mode, persist each bootstrap or control packet in the durable outbox and record
    its metadata in the checkpoint before R0 sends it. Treat task turns as explicit
    request/response exchanges, not autonomous push or continuous synchronization. After an
    acknowledged ACK or rebase send, independently read the exact task as inactive and use the
    helper's guarded control-turn completion command before another assignment. On cleanup, require
    `STOP_ALL` with reason `CLEANUP`, a correlated valid `STOP_ACK`, zero running worker children,
    and an inactive task before R0 archives it.
12. Accept attestation, result, protocol-rejection, and stop-response packets only from the recorded
    worker task after a task read independently verifies the producing turn is terminal/inactive.
    Retry an unknown or rejected non-create task action only when exact evidence proves
    nondelivery and task inactivity, preserving the identical outbox ID and payload. Never retry or
    requeue ambiguous task creation.

Opening this workspace alone is not a request to begin live challenge experiments. Wait until the
user explicitly asks to run or resume Project Glacier.
