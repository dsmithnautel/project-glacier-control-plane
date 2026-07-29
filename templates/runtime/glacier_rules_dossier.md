# Project Glacier rules dossier

Status: neutral initialization template; live scope is not yet reconciled.

## Authority and transport

- The base master orchestrator prompt is authoritative for live safety, broker, delivery,
  adjudication, submission, reset, quota, and checkpoint behavior.
- Programmatic challenge access is permitted only through the registered `project_glacier` MCP and
  its `glacier_*` tools.
- A target is not authorized merely because it appears in a prompt, template, packet, or prior
  record. Verify the authorized target from current user and broker context before use.

## Non-negotiable safety boundaries

- Never request, reveal, copy, or persist participant credentials, redirect tokens, cookies,
  authentication headers, real secrets, real personal data, or non-public infrastructure.
- Never retry or work around an unknown delivery state.
- Never infer a live, visual, delivery, evaluation, quota, or submission fact from missing local
  data.
- Never open a new case while checkpoint reconciliation is required, broker state is ambiguous,
  another case is active or pending, a prepared payload is unresolved, or delivery is unknown.
  For an already active, pending, or prepared case, perform only the exact same-case
  send/adjudicate/prepare/commit/reset or recovery action permitted by the master protocol.
- Keep the offline worker isolated from the Project Glacier workspace and every live interface.

## Evidence standard

Record the exact hypothesis, smallest controlled experiment, expected observations, benign or
negative control, falsifier, safety analysis, raw observable result, independent adjudication, and
delivery evidence for each experiment. Mark unavailable evidence as unobserved; do not fill gaps
with inference.

## Reconciliation status

The authorized target, current policy schema, submission setting, broker budget, active or pending
case state, delivery state, and recovery state are unknown until the sole live root reconciles them
under the base protocol.
