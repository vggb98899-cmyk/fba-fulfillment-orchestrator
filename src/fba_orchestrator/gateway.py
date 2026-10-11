from __future__ import annotations

from collections import deque

from .errors import PermanentReadError, TransientReadError
from .models import OperationResult, Snapshot, WriteResult


class FakeWarehouseGateway:
    """Deterministic external-system simulator used by examples and tests."""

    def __init__(self, snapshots: list[Snapshot], write_results: list[OperationResult] | None = None):
        self._snapshots = deque(snapshots)
        self._write_results = deque(write_results or [OperationResult(WriteResult.SUCCESS, "EXT-1")])
        self.read_failures: deque[Exception] = deque()
        self.write_calls = 0
        self.external_records: dict[str, dict] = {}

    def read_snapshot(self, target: str) -> Snapshot:
        if self.read_failures:
            raise self.read_failures.popleft()
        if not self._snapshots:
            raise PermanentReadError("no snapshot available")
        snapshot = self._snapshots[0]
        if snapshot.target != target:
            raise PermanentReadError("target mismatch")
        return snapshot

    def write_plan(self, request_fingerprint: str, payload: dict) -> OperationResult:
        self.write_calls += 1
        result = self._write_results.popleft()
        if result.result is WriteResult.SUCCESS and result.external_id:
            self.external_records[result.external_id] = {
                "request_fingerprint": request_fingerprint, **payload,
            }
        return result

    def readback(self, external_id: str) -> dict | None:
        return self.external_records.get(external_id)


__all__ = ["FakeWarehouseGateway", "TransientReadError", "PermanentReadError"]
