# Security

This broker handles an authorized challenge credential and stateful mutations. Its source must
remain in approved private company storage with least-privilege access. It is not approved for
public forks, public package publication, or external redistribution.

## Never store or disclose

- participant credentials or Keychain contents;
- cookies, redirect tokens, authorization headers, or authentication URLs;
- prompts, replies, transcripts, live case state, operation receipts, or another participant's
  submission;
- generated MCP-host configuration containing machine paths;
- SQLite journals, locks, environment files, or logs from live use.

The provided `.gitignore` is a guardrail, not permission to place protected data in the repository
directory.

## Required invariants

Preserve:

- the exact authorized-origin allowlist and cross-origin redirect rejection;
- local STDIO operation only, never a network listener;
- disabled proxy-environment inheritance;
- Keychain or host-environment credential handling without tool disclosure;
- one broker process and one unfinished case per state directory;
- unique operation IDs and write-ahead intent before every POST;
- fail-closed `UNKNOWN` delivery states with no blind retry;
- immutable prepared-submission hashing and conservative quota accounting;
- journal and error redaction.

The upstream service uses plain HTTP and places authentication material in a URL. This broker
cannot add transport confidentiality that the service does not provide. Use it only on the trusted
corporate/VPN network, never log request URLs, and never expose the service or broker outside the
local machine.

Submission is disabled by default. Enabling it is an explicit consumer decision and does not
weaken the exact prepared-hash, quota, or unknown-delivery gates.

If protected data is accidentally committed, stop publication and follow the approved internal
incident and history-remediation process. Do not paste the data into an issue, pull request, or
chat while reporting it.
