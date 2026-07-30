# Project Glacier MCP repository instructions

This directory is the canonical local MCP broker when used as the standalone
`project-glacier-mcp` repository. It is also vendored byte-for-byte under `mcp_server/` in the
private Project Glacier control-plane repository.

Opening or maintaining this source does not authorize live challenge access. Do not run a live
smoke test, authenticate, start a case, send a message, reset a case, or submit unless the user
has explicitly authorized that live operation and the applicable control protocol has reconciled
state first.

For security-sensitive code or configuration changes, apply the available code-quality and
secure-coding skills. Preserve the exact-origin allowlist, local-STDIO-only boundary, Keychain or
host-environment credential boundary, one-process/one-case invariant, unique operation IDs,
write-ahead journal, unknown-delivery lockout, immutable submission hash, conservative quota
accounting, and secret-safe errors. Never add participant credentials, cookies, redirect tokens,
authentication URLs, prompts, replies, transcripts, live case state, or receipts to repository
files or logs.

Keep reusable examples submission-disabled and approval-prompted. Consumer-specific automation
policy belongs in the consuming control plane, not in this canonical broker repository.
