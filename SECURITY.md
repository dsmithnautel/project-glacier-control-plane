# Security model

Project Glacier combines a live challenge transport with an offline orchestration control plane.
The central security rule is separation: version-controlled source describes how the system
works, while credentials and live challenge state stay outside Git.

## Protected data

Never request, print, screenshot, log, copy, commit, or send to an offline worker:

- the participant credential or Keychain contents;
- redirect tokens, cookies, authorization headers, or authentication URLs;
- real secrets, personal data, or non-public infrastructure details;
- live case IDs, operation IDs, transcript hashes, prepared payload hashes, or receipts unless
  they are required in the ignored canonical local artifacts;
- raw challenge prompts, replies, transcripts, or another participant’s submissions.

The broker may read the participant credential from its configured macOS Keychain service or from
the MCP process environment. The repository templates never contain that value.

## Trust boundaries

### Codex root task

The root task is the sole live operator. It reads the authoritative prompt and local checkpoint,
performs independent adjudication, calls the registered `glacier_*` tools, and writes canonical
local artifacts.

### Deterministic orchestrator

`project_glacier_orchestrator/` handles closed-schema packets, canonical hashes, compare-and-swap
checkpoint updates, and a write-once outbox. It must remain free of Codex, Glacier, browser,
network, connector, and external-system calls.

### MCP broker

`mcp_server/` is the only programmatic challenge transport. Preserve these invariants:

- exact authorized-origin validation;
- local STDIO operation, never a network listener;
- Keychain/environment credential boundary without tool disclosure;
- one broker process and one unfinished case per state directory;
- write-ahead operation intent and unique operation IDs;
- no blind retry after unknown POST delivery;
- immutable prepared submission hash;
- conservative quota accounting, including ambiguous submission delivery;
- journal redaction: no prompts, replies, transcripts, credentials, cookies, or tokens.

The upstream service uses plain HTTP and may carry authentication material in a URL. The broker
cannot fix that upstream design. Use it only on the trusted corporate/VPN network, never log
request URLs, and never expose the service or broker outside the local machine.

### Optional offline worker

The protocol-v2 worker must be projectless and receive sanitized packets only. It has no challenge
or Project Glacier workspace access, cannot write canonical artifacts, cannot communicate
peer-to-peer, and must return zero running children before cleanup. Any isolation or attestation
uncertainty causes a fallback to ordinary subagents in the root task.

## Delivery and submission safety

`glacier_prepare_submission` is a preview. A commit is valid only for the exact independently
reviewed payload hash, an available broker-reported budget, and an enabled broker configuration.

An unknown POST delivery is not a normal retry condition. Preserve the operation ID and local
state, reconcile only through the broker’s permitted read path, and stop mutations when the
protocol requires it. Unknown clear or submission delivery remains a manual stop.

## Filesystem controls

Generated Codex registration and initialized runtime artifacts use owner-only permissions. The
setup scripts reject symlink destinations and use same-directory durable writes or create-only
links to avoid following an attacker-controlled path.

Git ignores all current runtime state. `scripts/validate_repository.py` examines the exact tracked
plus non-ignored candidate set and fails when live state, generated configuration, machine paths,
or tenancy identifiers could be committed. Run it immediately before every commit.

Ignoring a file is not permission to share it through another channel. The local broker journal
contains operational metadata even though it intentionally omits message bodies and credentials.

## Dependencies and live tests

Install the MCP environment from `mcp_server/uv.lock`. Review dependency changes and retain the
exact version constraint for the MCP SDK unless a tested migration intentionally changes it.

Unit tests and the STDIO handshake are offline. `scripts/live_mcp_smoke.py` is different: it
authenticates and can create and reset a real case, and it requires an explicit
`--acknowledge-live-mutation` flag. Never run it in CI or as a routine install check.

## Repository classification

The source broker intentionally includes an internal exact-origin allowlist. The private
`dsmithnautel/project-glacier-control-plane` and `dsmithnautel/project-glacier-mcp` repositories
are company-approved storage for this material. Neither repository has a public/open-source
license or approval for public forks, public package publication, or external redistribution.

If protected data is accidentally committed, stop publication and coordinate approved history
remediation. Do not paste the data into an issue, pull request, or chat while reporting it.
