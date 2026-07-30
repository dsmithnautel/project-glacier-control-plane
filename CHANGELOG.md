# Changelog

## 0.1.0

- Established the private standalone MCP repository as the canonical broker source.
- Kept submission disabled and tool approvals prompted in the reusable example.
- Added standalone ignores for local state, environment files, generated Codex configuration,
  SQLite journals, and logs.
- Removed control-plane-specific zero-touch registration policy and live mutation tooling from the
  standalone package.
- Preserved the exact-origin, credential, journaling, delivery-unknown, single-case, immutable
  submission, quota, and redaction controls from the original broker.
