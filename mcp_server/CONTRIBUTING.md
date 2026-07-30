# Contributing

This is a private, internal repository with no open-source license. Make broker changes here first;
the Project Glacier control plane consumes a pinned, byte-identical snapshot.

Before changing source or dependencies:

1. read `AGENTS.md` and `SECURITY.md`;
2. confirm the change preserves the exact-origin, credential, one-case, write-ahead journal,
   unknown-delivery, immutable-hash, quota, and redaction boundaries;
3. keep reusable configuration submission-disabled and approval-prompted;
4. never add credentials, cookies, tokens, authentication URLs, live case state, transcripts,
   receipts, machine paths, generated host configuration, or local journals;
5. review every dependency and lockfile difference.

Run the complete offline suite:

```bash
uv sync --locked --dev
uv run ruff check .
uv run pytest
```

Do not add live mutation commands to normal tests or CI. After a reviewed canonical commit is
pushed, the control-plane maintainer must fetch that immutable commit, merge it as the
`mcp_server/` subtree, compare the two Git tree IDs, update the external pin, and rerun both
repositories' offline validation.
