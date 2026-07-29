# Project Glacier MCP component instructions

This directory contains the local MCP broker implementation inside the Project Glacier control
plane monorepo. Maintain and test the broker here. Run challenge orchestration only from the
monorepo root, whose `AGENTS.md`, master prompt, generated `.codex/config.toml`, and ignored state
directory provide the experiment context.

For security-sensitive code or configuration changes, apply the available code-quality and
secure-coding skills. Preserve the exact-origin allowlist, Keychain credential boundary,
one-process/one-case invariant, operation journal, unknown-delivery lockout, immutable submission
hash, and conservative quota accounting. Never add participant credentials, cookies, redirect
tokens, prompts, replies, or transcripts to repository files or logs.
