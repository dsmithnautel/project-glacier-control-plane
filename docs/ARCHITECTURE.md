# Architecture

## Purpose

The repository joins related source and documentation without joining their privileges. The
orchestrator decides and records; the MCP broker transports; an optional offline worker researches
only the sanitized snapshot it receives.

## Components

### Root Codex task (R0)

R0 is the sole live operator and canonical writer. It owns user intent, broker reconciliation,
experiment selection, independent adjudication, live `glacier_*` calls, and local durable
artifacts. A second root is never allowed to become a parallel live operator.

### Authoritative prompts

The master prompt defines all live rules. A normal request uses `SINGLE_ROOT`. The multi-root
prompt is an offline scheduling overlay, and the worker prompt is the isolated worker contract.
Neither overlay can weaken the master prompt.

### Deterministic helper

`project_glacier_orchestrator/` is a local protocol engine:

- strict and bounded JSON parsing;
- closed packet schemas and identity correlation;
- canonical hashing and replay detection;
- compare-and-swap checkpoint transitions;
- durable outbox records written before external task actions;
- explicit bootstrap, assignment, ACK, stop, and archive lifecycle gates.

It emits declarative Codex task actions. It does not execute those actions and has no challenge or
network transport.

### MCP broker

`mcp_server/` is a local STDIO process exposing seven narrow tools. It validates the exact target
origin, owns the credential and cookies, serializes live cases, journals mutation intent before
delivery, freezes ambiguous operations, prepares immutable submission previews, and accounts for
quota conservatively.

The nested package and lockfile keep broker dependencies separate from the standard-library
orchestrator.

### Offline worker

Multi-root pilot mode permits at most one projectless offline worker. Communication is not shared
memory or continuous synchronization. R0 writes a packet to its local outbox, sends one explicit
task turn, waits until that turn is terminal/inactive, independently reads the returned packet,
validates it, and then decides whether to accept, reject, or rebase it.

The worker never receives credentials, case state, raw transcripts, workspace access, or live
tools. It cannot communicate directly with another root.

## State model

Two kinds of state deliberately coexist:

1. **Versioned reusable source**: prompts, code, tests, schemas, templates, and config templates.
2. **Ignored canonical runtime state**: checkpoint, experiment artifacts, outbox, broker database,
   generated machine paths, and any delivery or receipt metadata.

The neutral initializer creates the second category with
`checkpoint_reconciliation_required = true`. “No local case” is not treated as proof that the
server is clean.

## Single-root versus multi-root

Single-root mode is the default because it has fewer coordination and stale-snapshot failure
modes. Multi-root mode can increase offline research throughput, but the live channel remains
serialized by the broker’s one-case rule. Its purpose is to overlap bounded offline work, not to
create concurrent live cases.

The protocol pays for that overlap with packet construction, hashing, outbox journaling,
attestation, stale-result detection, explicit ACK turns, stop/cleanup handshakes, and task
archival. If those costs exceed the useful offline work, R0 should use ordinary subagents instead.

## Failure posture

The system fails closed:

- missing or stale checkpoints block mutation;
- artifact or prompt digest drift requires explicit reconciliation;
- unknown delivery is never blindly retried;
- stale worker results are rebased rather than merged;
- task isolation uncertainty disables the offline worker;
- cleanup requires a correlated stop acknowledgment, zero worker children, an inactive task, and
  verified archival;
- broker authentication, schema, quota, or recovery ambiguity stops live mutation.
