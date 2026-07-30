# Project Glacier MCP broker

This private repository is the canonical source for the local Project Glacier MCP broker. A pinned
copy is also vendored under `mcp_server/` in the private
[`project-glacier-control-plane`](https://github.com/dsmithnautel/project-glacier-control-plane)
repository.

The broker gives an MCP-capable host narrow, stateful access to the explicitly authorized Project
Glacier challenge interface. It is a local STDIO process, not an HTTP listener. It owns
authentication cookies in memory and exposes only seven guarded `glacier_*` operations.

This repository is internal/private and has no open-source license. Access to the source is not
authorization to use the challenge, and it does not grant redistribution rights.

## What standalone MCP mode means

Codex or another MCP-capable client can call the broker directly for individual operations without
loading the master orchestrator, experiment ledgers, multi-root scheduler, or offline-worker
protocol. A raw LLM API cannot connect to STDIO MCP by itself; it needs an MCP-capable host or
bridge.

Standalone mode is deliberately lower-level. The broker enforces transport and state invariants,
but the human or MCP client remains responsible for authorization, case selection, evidence
review, adjudication, durable records, and deciding whether a submission is appropriate.

## Security model

- The only permitted remote origin is `http://144.25.123.139:8653`.
- The participant user ID is loaded from `GLACIER_USER_ID` supplied by the host or the macOS
  Keychain service `project-glacier-user-id`; it is never returned by a tool.
- Authentication redirects are followed manually and must remain on the exact origin.
- Cookies and redirect tokens remain in memory and are never written to the operation journal.
- Every POST records an operation intent before network delivery. A timeout, connection loss,
  malformed success response, or server error produces an `UNKNOWN` state and is never retried.
- An operating-system lock permits only one broker process per state directory. Database
  invariants permit only one unfinished case and one in-flight operation at a time.
- The SQLite journal stores identifiers, hashes, state, and receipts only. It does not store
  prompts, replies, transcripts, credentials, cookies, or redirect tokens.
- Any unresolved POST makes the case read-only. Unknown clear or submission delivery requires
  manual reconciliation and cannot be retried through MCP.
- Submission quota accounting includes acknowledged commits and ambiguous submission intents, so
  an unknown receipt cannot silently free another quota slot.

The upstream service places authentication material in a URL and uses plain HTTP. The broker
cannot repair that upstream design. Run it only on the trusted corporate/VPN network, never log
request URLs, and never expose the broker or challenge service outside the local machine. See
[SECURITY.md](SECURITY.md) before use or modification.

## Requirements

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- macOS for the supported no-echo Keychain setup command
- a POSIX host for the broker lock (`fcntl`); Windows is not supported
- authorized corporate/VPN access for explicitly approved live use

On a non-macOS POSIX host, the MCP host must inject `GLACIER_USER_ID` securely. Do not put it in a
repository file, prompt, command argument, log, or checked-in MCP configuration.

## Install

From a private clone of this repository:

```bash
uv sync --locked --dev
uv run project-glacier-store-credential
```

The credential command delegates input to the native macOS Keychain no-echo prompt. It does not
place the value in shell history, the process environment, or child-process arguments.

## Register with Codex or another MCP host

Copy the structure in [`config/codex.toml.example`](config/codex.toml.example) into the appropriate
Codex configuration and replace every placeholder with an absolute local path. The important
pieces for any STDIO MCP host are:

```toml
[mcp_servers.project_glacier]
command = "/absolute/path/to/project-glacier-mcp/.venv/bin/python"
args = ["-m", "project_glacier_mcp.server"]
cwd = "/absolute/path/to/project-glacier-mcp"
required = true
default_tools_approval_mode = "prompt"

[mcp_servers.project_glacier.env]
GLACIER_KEYCHAIN_SERVICE = "project-glacier-user-id"
GLACIER_ALLOW_SUBMIT = "false"
GLACIER_MAX_SUBMISSIONS = "20"
GLACIER_MAX_TURNS = "20"
GLACIER_STATE_DIR = "/absolute/private/path/project-glacier-mcp-state"
```

Keep the state directory outside the clone, keep submission disabled unless an approved workflow
explicitly needs it, and never add `GLACIER_USER_ID` to this file. Restart or reload the MCP host
after changing its registration.

Running `project-glacier-mcp` directly in a terminal starts a machine-facing STDIO protocol
process. It is not an interactive shell.

## Tools and safe call order

| Tool | Purpose |
| --- | --- |
| `glacier_open_session` | Authenticate, verify schema, and recover a pending draft |
| `glacier_start_case` | Create the sole active case with immutable category metadata |
| `glacier_send_message` | Send one turn with a unique operation ID |
| `glacier_get_state` | Read local state and synchronize remote pending state |
| `glacier_prepare_submission` | Create an immutable local preview and payload hash |
| `glacier_commit_submission` | Commit exactly the prepared payload once |
| `glacier_reset_case` | Clear the intended non-pending case using its transcript hash |

Always call `glacier_open_session` first. Keep exactly one active case, use a fresh operation ID for
every message, submission, and reset, and stop all mutations after an `UNKNOWN` delivery. Treat
`glacier_prepare_submission` as a preview; commit only the exact independently reviewed payload
hash while submission is explicitly enabled and quota remains.

```text
ACTIVE -> PENDING_EVALUATION -> PREPARED -> SUBMITTED -> CLOSED
   |
   +-> *_DELIVERY_UNKNOWN -> stop; no blind retry
```

API responses cannot prove browser-only conditions. Tool results report those fields as
`NOT_OBSERVABLE_VIA_API` unless a separate observation supplies them.

Broker configuration, Keychain, or singleton-lock failures are resolved lazily on the first tool
call and returned as structured, secret-safe `glacier_*` errors. If a tool reports
`BROKER_ALREADY_RUNNING`, close the older host process only after confirming its journal has no
active case.

## Use with the full control plane

The control-plane repository adds authoritative prompts, deterministic coordination, ignored
checkpoint/ledger artifacts, and optional sanitized offline research scheduling. Its generated
Codex registration points to the vendored snapshot of this package. Use that repository when you
want the full orchestration protocol rather than manual MCP calls.

## Offline validation

These commands do not contact the challenge:

```bash
uv run ruff check .
uv run pytest
```

The suite covers exact-origin validation, redirect rejection, secret-safe failures, response
limits, category values, pending-state recovery, state gates, operation idempotency,
delivery-unknown behavior, submission hashing and quotas, the conservative example registration,
and an actual STDIO MCP initialize/list-tools handshake.

No live smoke command is included in this standalone repository. Live start/send/reset validation
belongs to the control-plane protocol and requires a separate explicit mutation acknowledgement.
