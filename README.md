# FBA Fulfillment Orchestrator

A production-grade **reference implementation** for safely orchestrating inventory-to-fulfillment workflows when external systems are slow, stateful, and capable of returning ambiguous write results.

> **Non-production-source statement:** This repository is an independently written, sanitized reference implementation informed by general production engineering experience. It is not a copy of an employer's production source code. All external systems, identifiers, inventory, and business data are mocked or fictional. Proprietary integrations, selectors, routing identities, credentials, and company-specific rules are not published.

## The engineering problem

Warehouse automation is not simply “call an API.” A reliable workflow must prevent duplicate shipments, bind human confirmation to an immutable plan, survive crashes, isolate bad objects, and stop safely when a remote write has an UNKNOWN outcome.

This project demonstrates those mechanisms without connecting to a real ERP or messaging platform.

## Production experience vs. public implementation

| Area | Production experience represented | What this repository contains |
|---|---|---|
| Inventory and fulfillment orchestration | Multi-stage validation and recovery patterns | Independent generic state machine |
| External integrations | Browser/API/message adapters | Deterministic in-memory mock gateway |
| Data | Real inventory and operational evidence | Fictional sample data only |
| Rules | Organization-specific policies | Minimal generic safety rules |
| Operations | Worker health, leases, audit, reconciliation | Offline runnable equivalents |

## Core architecture

See [Architecture](docs/architecture.md).

```text
Trigger → Lease/idempotency → Verified snapshot → Preview/confirmation
        → Immutable plan → Durable write intent → External write
        → Read-back → Completed | UNKNOWN reconciliation
```

## Key mechanisms

- Durable idempotency keys prevent duplicate jobs.
- Snapshot freshness, schema, target, primary-key, quantity, and weight gates.
- Immutable execution plans protected by SHA-256 fingerprints.
- Explicit state transitions with compare-and-set semantics.
- Bounded retry for transient reads; permanent failures stop immediately.
- Write intent persisted before external mutation.
- UNKNOWN writes are never blindly repeated.
- Write-after-read verification before completion.
- Scheduler leases with stale-owner recovery.
- Object-level blast-radius isolation.
- Standard-library `/health` endpoint.
- Deterministic failure-injection tests.

## Safety design

The engine fails closed when identity, target, schema, freshness, or read-back cannot be proven. An ambiguous external write moves the job to `UNKNOWN`; only matching external read-back can reconcile it. The public gateway cannot contact a production service.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e .
fba-orchestrator demo
```

Health endpoint:

```bash
fba-orchestrator health --port 8080
# GET http://127.0.0.1:8080/health
```

## Tests

```bash
python -m unittest discover -s tests -v
```

The public suite covers success, idempotency, malformed keys, target mismatch, schema mismatch, stale snapshots, conflicts, bounded retries, UNKNOWN writes, read-back mismatch, crash recovery, lease competition, blast-radius limits, and API health. See [Testing](docs/testing.md).

## Example result

```json
{
  "state": "COMPLETED",
  "target": "demo-store",
  "external_id": "EXT-1"
}
```

## Failure operations

See [Incident response](docs/incident-response.md) and [Design decisions](docs/decisions.md).

## Known limitations

- The external gateway is intentionally mocked.
- The sample packing rule is generic and not a company policy.
- No real ERP, browser profile, messaging account, scheduler, or warehouse endpoint is included.
- SQLite is suitable for demonstrating transactional behavior, not high-volume distributed consensus.

## Interviewer reading path

1. Read this README and [Architecture](docs/architecture.md).
2. Review `engine.py` for safety gates and UNKNOWN handling.
3. Review `store.py` for idempotency, transactions, and leases.
4. Review the failure-injection tests.
5. Read the ADRs and incident-response matrix.

## Technology

Python 3.11+, SQLite, standard-library HTTP server, `unittest`, GitHub Actions.

## License

[MIT](LICENSE)
