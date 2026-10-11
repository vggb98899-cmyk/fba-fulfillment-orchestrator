# Design decisions

## ADR-001: Write intent before external mutation

Every external mutation receives a stable attempt ID and durable intent before it is called. An UNKNOWN result is never retried automatically; read-back reconciliation must prove the outcome.

## ADR-002: Immutable snapshot and plan

Preview and execution share a fingerprinted snapshot. Target, schema, freshness, primary keys, quantities, and weights are validated before plan creation. Plan tampering is detected before any write.

## ADR-003: One connection per store operation

SQLite connections never cross worker boundaries. Each operation opens, commits or rolls back, and closes its own connection.

## ADR-004: Side-effect-free public adapter

The public repository includes a mock gateway only. Production integrations, selectors, credentials, routing identifiers, and proprietary rules are deliberately excluded.
