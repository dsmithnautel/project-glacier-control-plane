# Provenance

## MCP import baseline

The `mcp_server/` component was imported from a clean local checkout of the
`project-glacier-mcp` repository at:

```text
14881b23b046dc08bdd3d53ad99f6fec63106b08
```

The same value is stored as machine-readable text in `mcp_server/SOURCE_REVISION`.

The import copied tracked source only: package code, tests, scripts, README, scoped instructions,
`pyproject.toml`, `uv.lock`, and `.gitignore`. It intentionally excluded the upstream `.git`
directory, virtual environment, caches, and generated files.

The source, tests, smoke-test source, package manifest, and lockfile were byte-compared with that
checkout before monorepo-specific changes. The local tree updates the component documentation and
adds fail-closed rejection of a relative broker state directory to prevent accidental
working-directory-relative journal placement. The resulting implementation passed its offline
Ruff and pytest suites.

## Why history was not nested

A nested Git repository would make staging, review, and provenance ambiguous. The parent
repository therefore owns the imported files directly, while `SOURCE_REVISION` provides the
upstream anchor.

## Updating the import

For a future update:

1. verify the upstream checkout is clean and record its full commit;
2. compare its tracked-file list with `mcp_server/`;
3. copy tracked content only;
4. preserve or deliberately review monorepo-specific README, `AGENTS.md`, state-path hardening,
   and tests;
5. update `SOURCE_REVISION`;
6. review code and dependency diffs, especially transport, credentials, journaling, delivery
   ambiguity, submissions, quotas, and redaction;
7. rebuild the nested environment from `uv.lock`;
8. run every offline check and the repository-boundary validator;
9. do not run the live smoke test as an import-verification shortcut.

## Repository publication

No remote, hosting provider, visibility, or license is implied by this import. The broker contains
an intentional internal exact-origin allowlist. Confirm classification and an approved private
destination before publication.
