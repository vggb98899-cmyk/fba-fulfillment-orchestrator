# Testing strategy

The public test suite is fully offline and uses only Python's standard library.

Coverage includes success, duplicate requests, missing and duplicate keys, target/schema/freshness gates, state conflicts, bounded transient retry, permanent failures, UNKNOWN writes, read-back mismatch, crash-safe leases, blast-radius isolation, immutable-plan tampering, and the health API.

Run:

```bash
python -m unittest discover -s tests -v
```

Test counts reported for this repository are generated from this public suite only. Historical production test counts are not reused.
