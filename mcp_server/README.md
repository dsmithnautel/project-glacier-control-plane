# Project Glacier MCP broker

This is the live-transport component of the Project Glacier control-plane monorepo. The repository
root contains the authoritative prompts, deterministic orchestrator, portable configuration
templates, neutral runtime templates, and operating documentation.

This package gives a local Codex orchestrator narrow, stateful access to the explicitly
authorized Project Glacier challenge interface. It is a local STDIO MCP process, not an HTTP
listener. The process owns authentication cookies and exposes only the approved `glacier_*`
operations.

## Security model

- The only permitted remote origin is `http://144.25.123.139:8653`.
- The participant user ID is loaded from `GLACIER_USER_ID` or the macOS Keychain service
  `project-glacier-user-id`; it is never returned by a tool.
- Authentication redirects are followed manually and must remain on the exact origin.
- Cookies and redirect tokens remain in memory and are never written to the operation journal.
- Each POST records an operation intent before network delivery. A timeout, connection loss,
  malformed success response, or server error produces an `UNKNOWN` state and is never retried.
- An operating-system lock permits only one broker process for a state directory. Database
  invariants also permit only one unfinished case and one in-flight operation at a time.
- The SQLite journal stores IDs, hashes, state, and receipts only. It does not store prompts,
  replies, transcripts, cookies, tokens, or the participant user ID.
- Any unresolved POST makes the case read-only. Only a matching server-authoritative pending
  draft can reconcile an unknown chat; unknown clear or submission delivery requires manual
  reconciliation and cannot be reset through MCP.
- Submission quota accounting includes acknowledged commits and ambiguous submission intents, so
  an unknown receipt cannot silently free another quota slot.

The upstream service places authentication material in a URL and uses plain HTTP. The broker
cannot repair that upstream design. Run it only on the trusted corporate/VPN network, never log
request URLs, and do not expose the service outside the local machine.

## Install

From this directory:

```bash
uv sync --dev
uv run project-glacier-store-credential
```

The credential command delegates input to the native macOS Keychain no-echo prompt and stores it
without placing the value in shell history, environment, or child-process arguments. As an
alternative, export `GLACIER_USER_ID` into the Codex host environment. Do not put the value in the
repository or the orchestrator prompt.

## Tools

| Tool | Purpose |
| --- | --- |
| `glacier_open_session` | Authenticate, verify schema, and recover a pending draft |
| `glacier_start_case` | Create the sole active case with immutable category metadata |
| `glacier_send_message` | Send one turn with a unique operation ID |
| `glacier_get_state` | Read local state and synchronize remote pending state |
| `glacier_prepare_submission` | Create an immutable local preview and payload hash |
| `glacier_commit_submission` | Commit exactly the prepared payload once |
| `glacier_reset_case` | Clear/close the intended non-pending case using its transcript hash |

The case state machine is:

```text
ACTIVE -> PENDING_EVALUATION -> PREPARED -> SUBMITTED -> CLOSED
   |                                   \
   +-> *_DELIVERY_UNKNOWN              +-> no blind retry
```

API responses cannot prove browser-only conditions. Tool results therefore report pink-warning,
input-availability, and rendered-indicator fields as `NOT_OBSERVABLE_VIA_API` unless a separate
browser observation supplies them.

## Configuration

Codex reads the generated registration from the monorepo root's `.codex/config.toml`; that file's
`command` and `cwd` must point to this directory's virtual environment and root. Render it from
the versioned templates with `python3 scripts/configure_codex.py` at the monorepo root. The current
consumer configuration explicitly permits zero-touch leaderboard submission and auto-approves
`glacier_commit_submission`, subject to the broker's immutable-preview check, exactly-once journal,
single-case rule, and configured emergency ceiling of 1,000. Set `GLACIER_ALLOW_SUBMIT` to `false`
to restore the submission gate. Restart or reload Codex after changing MCP configuration.

Useful environment settings:

| Setting | Default |
| --- | --- |
| `GLACIER_BASE_URL` | exact authorized origin |
| `GLACIER_ALLOW_SUBMIT` | `false` in code; explicitly `true` in this project configuration |
| `GLACIER_MAX_SUBMISSIONS` | `20` |
| `GLACIER_MAX_TURNS` | `20` |
| `GLACIER_STATE_DIR` | `~/.local/state/project-glacier-mcp` |

## Hand off to the root orchestrator

Start a new Codex task with the workspace set to the monorepo root, then say:

```text
Run Project Glacier using project_glacier_master_orchestrator_prompt.md.
```

The root `AGENTS.md` makes the new task load the full master prompt and use this MCP broker. A new
task or Codex restart is required after an MCP path or configuration change so the tool registry is
refreshed. Working in this component directory gives code-maintenance context, not authority to
start live research.

## Validation

From the monorepo root:

```bash
uv run --project mcp_server ruff check --config mcp_server/pyproject.toml mcp_server
uv run --project mcp_server pytest mcp_server/tests
```

The test suite covers configuration allowlisting, redirect validation, secret-safe failures,
response limits, exact category values, pending-state recovery, state gates, operation
idempotency, delivery-unknown behavior, submission hashing/quotas, and an actual STDIO MCP
initialize/list-tools handshake.

Broker configuration, Keychain, or singleton-lock failures are resolved lazily on the first tool
call. They are returned as structured `glacier_*` errors instead of closing STDIO before the MCP
initialize response, so Codex can still create the task and explain the corrective action. If a
tool reports `BROKER_ALREADY_RUNNING`, close the older Project Glacier task or terminate its stale
MCP process only after confirming that its journal contains no active case.

Live validation should proceed in this order:

1. `glacier_open_session` and confirm the authenticated schema/pending state.
2. Start one benign control case, send one harmless message, and reset it.
3. Test submission completely against a fake transport; do not create a dummy leaderboard entry.
4. Allow the first real orchestrated terminal case to exercise live submission.

The bundled smoke test performs steps 1 and 2 through the real STDIO MCP protocol, prints only
non-sensitive status metadata, keeps submission disabled, and refuses to mutate anything if it
finds a pre-existing pending or active case:

```bash
mcp_server/.venv/bin/python mcp_server/scripts/live_mcp_smoke.py
```
