# Project Glacier experiment ledger

Status: initialized with no experiments. Broker reconciliation is required before live work.

| Experiment | Mechanism | Category | Expected | Status | Notes |
|---|---|---|---|---|---|

## Experiment record template

### Identity and lifecycle

- Experiment ID:
- Timestamp in UTC:
- Parent experiment IDs:
- Lineage:
- Status (`PROPOSED`, `SCREENING`, `READY`, `RUNNING`, `SUPPORTED`, `REFUTED`,
  `INCONCLUSIVE`, `BLOCKED`, `DUPLICATE`, `FLAKY`, `RULES_INVALID`,
  `SUBMISSION_READY`, or `SUBMITTED`):

### Policy, mechanism, and novelty

- Policy category:
- Policy subcategory:
- Secondary policy categories, if any:
- Mechanism family:
- Exact hypothesis:
- Nearest prior experiment:
- One-sentence material novelty delta:
- Realistic OCI operator narrative:

### Authorization, construction, and predeclared outcome

- Authorized target:
- Provenance of every resource identifier:
- Exact prompt sequence:
- Purpose of each turn:
- Controlled variants or minimal-pair contrasts:
- Expected policy behavior:
- Predeclared success signal:
- Expected allow observation:
- Expected block observation:
- Benign or negative control:
- Result that would falsify the hypothesis:
- Scope and safety analysis:
- Pre-execution rules review and approval:

### Model, interface, and reset state

- Model:
- Interface:
- Session identifier or neutral session label:
- Session state before execution:
- Reset procedure:
- Reset operation ID:
- Reset acknowledgement or reason reset is not permitted:

### Raw evidence and interface observations

- Exact raw response:
- Exact complete turn-by-turn transcript or artifact reference:
- Full-case summary covering every turn:
- Pink warning observation (`NOT_OBSERVABLE_VIA_API` when unavailable):
- Message-field availability (`NOT_OBSERVABLE_VIA_API` when unavailable):
- Rendered detection indicator (`NOT_OBSERVABLE_VIA_API` when unavailable):
- Other exact interface observations:
- Interface stop reason or outcome lock:

### Broker, transport, delivery, and submission

- Challenge transport:
- MCP server name:
- Case ID:
- Case state:
- Transcript hash:
- Unique operation IDs by action and turn:
- Delivery status by action and turn:
- Prepared payload hash:
- Submission receipt IDs:
- Broker submission count before case:
- Broker submission limit before case:
- Broker remaining submission budget before case:
- Broker submission count after case:
- Broker submission limit after case:
- Broker remaining submission budget after case:

### Blockers and capacity

- Active blocker type:
- Blocker evidence:
- Safe diagnostic actions attempted:
- Mutations performed during diagnosis:
- Manual action required:
- Capacity forecast:
- Bottleneck alerts:
- Reported limits and the authoritative source of each:

### Independent adjudication and official mapping

- Internal result class (`TRUE_BYPASS`, `CORRECT_BLOCK`, `FALSE_POSITIVE`,
  `CORRECT_ALLOW`, `PARTIAL_OR_AMBIGUOUS`, or `INTERFACE_OR_TEST_FAILURE`):
- Official outcome mapping:
- Independent judge rationale:
- Novelty assessment:
- Realism assessment:
- Policy relevance assessment:
- Evidence quality assessment:
- Likely impact assessment:

### Reproduction, cost, and synthesis

- Reproduction count:
- Reproduction consistency:
- Attempt cost:
- Time cost:
- Token cost, when known:
- Failure signature:
- Constraints learned:
- Confounders:
- Next implication:
- Conditions required to reopen the route:
- Final disposition:

Do not place credentials, cookies, redirect tokens, authentication headers, real personal data, or
other prohibited sensitive material in this ledger.
