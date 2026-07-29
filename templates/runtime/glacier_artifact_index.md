# Project Glacier artifact index

Status: neutral initialization set.

| Logical artifact | Durable file | Initial state |
|---|---|---|
| `RULES_DOSSIER` | `glacier_rules_dossier.md` | Safety baseline; live scope unreconciled |
| `COVERAGE_MATRIX` | `glacier_coverage_matrix.md` | All policy rows are gaps |
| `HYPOTHESIS_REGISTRY` | `glacier_hypothesis_registry.md` | Empty |
| `EXPERIMENT_LEDGER` | `glacier_experiment_ledger.md` | Empty |
| `ARTIFACT_INDEX` | `glacier_artifact_index.md` | This file |
| `FINDINGS_QUEUE` | `glacier_findings_queue.md` | Empty |
| `CURRENT_CHECKPOINT` | `CURRENT_CHECKPOINT.md` | Reconciliation required |
| `RESUME_PACKET` | `PROJECT_GLACIER_RESUME.txt` | Fail-closed single-root state |

The offline worker prompt is a versioned protocol artifact. Its digest, the rules digest, and the
ledger digest are bound into the resume packet at initialization.
