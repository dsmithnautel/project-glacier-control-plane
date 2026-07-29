# Contributing

Changes must preserve the split between the deterministic orchestrator, the live MCP broker, and
ignored runtime state.

## Before editing

1. Read `AGENTS.md`, the relevant prompt, and [SECURITY.md](SECURITY.md).
2. Decide whether the change affects the root orchestrator, MCP broker, local configuration,
   runtime templates, or more than one boundary.
3. Keep the repository local/private. Do not create or push a remote without an approved
   destination, classification, and license decision.

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
PYTHONPYCACHEPREFIX=/tmp/project-glacier-pycache \
  python3 -m unittest discover -s tests -v
uv run --project mcp_server ruff check --config mcp_server/pyproject.toml mcp_server
uv run --project mcp_server pytest mcp_server/tests
```

Also validate rendered configuration after registration changes:

```bash
python3 scripts/configure_codex.py --check
PROJECT_GLACIER_CODEX_CONFIG="$PWD/.codex/config.toml" \
  uv run --project mcp_server pytest -q mcp_server/tests/test_project_registration.py
```

Do not interpret a missing dependency or skipped integration check as a pass; record it as a
validation gap.

## Updating the imported MCP source

`mcp_server/SOURCE_REVISION` identifies the exact upstream source revision used for the import.
When importing a newer revision:

1. begin from a clean, reviewed upstream checkout;
2. copy tracked source only, never its `.git`, `.venv`, caches, or state;
3. update the full 40-character revision;
4. review every source difference, lockfile change, and security invariant;
5. recreate the nested environment and run the complete offline validation;
6. never use the live smoke script merely to prove that an import built.

See [provenance](docs/PROVENANCE.md) for the current baseline.
