# Operations

## Fresh installation

1. Confirm Python 3.12 and `uv` are available.
2. Run `uv sync --project mcp_server --dev`.
3. Run `python3 scripts/initialize_workspace.py` once.
4. Complete the local rules dossier with verified authorization context. Keep identifiers local.
5. Run `python3 scripts/configure_codex.py`.
6. If needed, invoke `uv run --project mcp_server project-glacier-store-credential` and enter the
   value only in the native Keychain prompt.
7. Run `python3 scripts/configure_codex.py --check`.
8. Start a new Codex task or restart Codex.

The initializer has no overwrite mode. If any destination exists, it creates nothing and asks you
to reconcile the existing workspace instead.

## Existing workspace migration

Do not replace or regenerate current checkpoints and ledgers. Create the nested MCP environment,
render the new `.codex` registrations with `python3 scripts/configure_codex.py --force` only after
reviewing the local difference, validate the registration, and start a new task.

Changing a TOML file does not move a broker process already owned by a running task. Before closing
an older task or broker, verify that its journal has no unresolved active, pending, prepared, or
unknown-delivery state.

## Normal validation

Run the boundary validator first and last:

```bash
python3 scripts/validate_repository.py
python3 scripts/verify_mcp_snapshot.py
```

Then run the offline suites:

```bash
PYTHONPYCACHEPREFIX=/tmp/project-glacier-pycache \
  python3 -m unittest discover -s tests -v
uv run --project mcp_server ruff check --config mcp_server/pyproject.toml mcp_server
uv run --project mcp_server pytest mcp_server/tests
```

The STDIO protocol tests launch the local server with fake or unavailable broker state; they do
not contact the challenge. The registration test needs
`PROJECT_GLACIER_CODEX_CONFIG="$PWD/.codex/config.toml"`.

## Starting work

For a plain run or resume request, R0 reads the complete master prompt and local durable artifacts,
reconciles them with broker-authoritative state, and remains in `SINGLE_ROOT`.

For multi-root pilot mode, the user must explicitly request the mode and explicitly authorize
creation or reuse of the projectless worker. R0 then follows the repo-local skill and protocol
overlay. No ordinary request should create a second task.

## Common safe recovery actions

### Generated config differs

Run `python3 scripts/configure_codex.py --check`. Review the actual file without exposing secret
values. If the difference is only the intended generated registration, run the renderer with
`--force`, re-run `--check`, and start a new task.

### MCP executable is missing

Recreate it with `uv sync --project mcp_server --dev`. Do not point configuration at a copied
virtual environment.

### Broker already running

Do not kill it automatically. Identify the owning task and verify that the journal has no live or
ambiguous state. A second process is blocked intentionally.

### Checkpoint or artifact digest drift

Stop task dispatch and challenge mutation. Read the current prompt, checkpoint, rules, and ledger;
determine whether the change was intentional; then use only the helper’s reviewed reconciliation
path with the latest version/hash fence.

### Unknown delivery

Preserve the exact operation ID and state. Use only the broker’s permitted reconciliation read.
Never resend the mutation. Unknown clear or submission delivery requires manual reconciliation.

### Offline worker cleanup

Stop new assignments, send a durably planned `STOP_ALL` with reason `CLEANUP`, validate a
correlated `STOP_ACK`, prove zero running children and an inactive worker task, archive it, record
the archive outcome, and only then restore `SINGLE_ROOT`.

## Live smoke test

`scripts/live_mcp_smoke.py` authenticates and can create and reset a real case. It is not an
install check, unit test, or CI command. It refuses to run without
`--acknowledge-live-mutation`. Use it only when the user has explicitly authorized the live
workflow, all base-protocol reconciliation gates pass, and no current case is unresolved.
