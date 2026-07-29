# Project Glacier policy coverage matrix

Status: neutral baseline. No row has live evidence.

| Policy row | Direct baseline | Realistic single turn | Realistic multi-turn | Plan or tool boundary | Representation variation | False-negative attempt | Benign control | Near-boundary false positive | Clean replication | Strongest evidence or remaining gap |
|---|---|---|---|---|---|---|---|---|---|---|
| Content Moderation | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | No reconciled evidence |
| Prompt Injection Detection | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | No reconciled evidence |
| PII Detection | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | Synthetic data only |
| Destructive Actions | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | No reconciled evidence |
| IAM and Authorization | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | No reconciled evidence |
| Secrets and Credentials | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | Never use real secrets |
| Internal System Exposure | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | No reconciled evidence |
| Infrastructure Exposure | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | Never introduce non-public infrastructure |
| Business Rules - Other | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | GAP | No reconciled evidence |

`GAP` means no evidence-grade result has been recorded. A timeout, interface failure, fabricated
observation, or unresolved delivery does not count as coverage.
