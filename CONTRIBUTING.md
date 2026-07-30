# Contributing

Changes must preserve the split between the deterministic orchestrator, the live MCP broker, and
ignored runtime state.

## Before editing

1. Read `AGENTS.md`, the relevant prompt, and [SECURITY.md](SECURITY.md).
2. Decide whether the change affects the root orchestrator, MCP broker, local configuration,
   runtime templates, or more than one boundary.
3. Keep both repositories private. Their current `dsmithnautel` GitHub destinations are
   company-approved storage; no public/open-source license or external redistribution grant has
   been selected.

## Change rules

- Make focused, reviewable changes and retain compatibility unless the protocol version is
  deliberately migrated.
- Never add credentials, cookies, tokens, authentication URLs, live case records, transcripts,
  receipts, tenancy identifiers, or machine-specific paths.
- Keep actual `.codex` files and populated runtime artifacts ignored. Change their templates or
  renderer instead.
- Preserve exact-origin validation, operation journaling, delivery-unknown lockout, one-case
  state, immutable submission hashes, and conservative quotas in the MCP broker.
- Keep the deterministic helper free of network, Codex, Glacier, browser, and application calls.
- Treat the live smoke test as a live mutation workflow, not a test-suite step.
- Update documentation and tests when behavior, configuration, templates, or protocol fields
  change.

## Validation

Run:

```bash
python3 scripts/validate_repository.py
python3 scripts/verify_mcp_snapshot.py
PYTHONPYCACHEPREFIX=/tmp/project-glacier-pycache \
  python3 -m unittest discover -s tests -v
uv run --project mcp_server ruff check --config mcp_server/pyproject.toml mcp_server
uv run --project mcp_server pytest mcp_server/tests
```

Also validate rendered configuration after registration changes:

```bash
python3 scripts/configure_codex.py --check
PROJECT_GLACIER_CODEX_CONFIG="$PWD/.codex/config.toml" \
  python3 -m unittest tests.test_mcp_registration -v
```

Do not interpret a missing dependency or skipped integration check as a pass; record it as a
validation gap.

## Updating the MCP subtree

Make broker changes in the canonical private `dsmithnautel/project-glacier-mcp` repository first.
After review and offline validation:

1. fetch the standalone repository without merging a moving branch;
2. record the exact full commit and confirm it descends from `MCP_SOURCE_REVISION`, unless a
   separately reviewed history migration is intentional;
3. review every source, dependency, and lockfile difference, especially transport, credentials,
   journaling, delivery ambiguity, submissions, quotas, and redaction;
4. merge the immutable commit with:

   ```bash
   git subtree merge --prefix=mcp_server <full-commit> --squash \
     --message "Sync MCP snapshot to <full-commit>"
   ```

5. require `git rev-parse HEAD:mcp_server` to equal
   `git rev-parse <full-commit>^{tree}`;
6. update `MCP_SOURCE_REVISION` and `MCP_SOURCE_TREE`;
7. run `python3 scripts/verify_mcp_snapshot.py --source-repo <standalone-clone>` plus every
   offline control-plane and MCP check;
8. never use the live smoke script as an import or compatibility test.

The subtree keeps control-plane clones self-contained. Do not replace it with a submodule or
manually maintained second copy. See [provenance](docs/PROVENANCE.md) for the current pin.
