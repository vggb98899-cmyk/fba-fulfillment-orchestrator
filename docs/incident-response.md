# Failure handling

| Failure | System response | Operator action |
|---|---|---|
| Transient read | Retry within a fixed budget | None unless exhausted |
| Permanent read | Stop immediately | Correct source/configuration |
| Stale or incompatible snapshot | Block before preview | Start a fresh verified scan |
| Duplicate request | Return the original job | None |
| External write FAILED | Mark job failed | Inspect reason; create a new authorized job |
| External write UNKNOWN | Persist UNKNOWN; never repeat write | Read back external state and reconcile |
| Read-back mismatch | Fail closed | Compare immutable request and external record |
| Worker crash | Lease expires; durable state remains | New worker resumes from stored state |
