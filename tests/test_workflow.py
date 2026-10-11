from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fba_orchestrator.engine import FulfillmentEngine, fingerprint
from fba_orchestrator.errors import ConflictError, PermanentReadError, SafetyGateError, TransientReadError, UnknownWriteError
from fba_orchestrator.gateway import FakeWarehouseGateway
from fba_orchestrator.models import InventoryItem, OperationResult, Snapshot, WriteResult
from fba_orchestrator.store import WorkflowStore


def make_snapshot(*, snapshot_id="snap-1", target="store-a", age=0,
                  schema=1, items=None):
    return Snapshot(snapshot_id, target, schema, time.time() - age, tuple(items or (
        InventoryItem("SKU-A", 60, 0.2), InventoryItem("SKU-B", 30, 0.4),
    )))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = WorkflowStore(Path(self.temp.name) / "workflow.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def engine(self, snapshot=None, results=None):
        gateway = FakeWarehouseGateway([snapshot or make_snapshot()], results)
        return FulfillmentEngine(self.store, gateway), gateway

    def ready_job(self, engine):
        job = engine.start(target="store-a", expected_target="store-a", idempotency_key="key-1")
        return engine.confirm(job["job_id"])

    def test_success_path_and_readback(self):
        engine, gateway = self.engine()
        job = self.ready_job(engine)
        final = engine.execute(job["job_id"])
        self.assertEqual(final["state"], "COMPLETED")
        self.assertEqual(gateway.write_calls, 1)

    def test_idempotent_start_returns_same_job(self):
        engine, _ = self.engine()
        first = engine.start(target="store-a", expected_target="store-a", idempotency_key="same")
        second = engine.start(target="store-a", expected_target="store-a", idempotency_key="same")
        self.assertEqual(first["job_id"], second["job_id"])

    def test_idempotency_key_cannot_cross_targets(self):
        engine, _ = self.engine()
        engine.start(target="store-a", expected_target="store-a", idempotency_key="same-target")
        with self.assertRaises(ConflictError):
            engine.start(target="store-b", expected_target="store-b", idempotency_key="same-target")

    def test_completed_execute_is_idempotent(self):
        engine, gateway = self.engine()
        job = self.ready_job(engine)
        final = engine.execute(job["job_id"])
        again = engine.execute(job["job_id"])
        self.assertEqual(again["external_id"], final["external_id"])
        self.assertEqual(gateway.write_calls, 1)

    def test_missing_primary_key_is_blocked(self):
        engine, _ = self.engine(make_snapshot(items=(InventoryItem("", 1, 0.2),)))
        with self.assertRaises(SafetyGateError):
            engine.start(target="store-a", expected_target="store-a", idempotency_key="bad")

    def test_duplicate_primary_key_is_blocked(self):
        items = (InventoryItem("A", 1, 0.2), InventoryItem("A", 2, 0.2))
        engine, _ = self.engine(make_snapshot(items=items))
        with self.assertRaises(SafetyGateError):
            engine.start(target="store-a", expected_target="store-a", idempotency_key="dup")

    def test_target_mismatch_is_blocked_before_read(self):
        engine, _ = self.engine()
        with self.assertRaises(SafetyGateError):
            engine.start(target="store-a", expected_target="store-b", idempotency_key="target")

    def test_schema_incompatible_is_blocked(self):
        engine, _ = self.engine(make_snapshot(schema=2))
        with self.assertRaises(SafetyGateError):
            engine.start(target="store-a", expected_target="store-a", idempotency_key="schema")

    def test_stale_snapshot_is_blocked(self):
        engine, _ = self.engine(make_snapshot(age=600))
        with self.assertRaises(SafetyGateError):
            engine.start(target="store-a", expected_target="store-a", idempotency_key="stale")

    def test_state_conflict_is_fail_closed(self):
        engine, _ = self.engine()
        job = engine.start(target="store-a", expected_target="store-a", idempotency_key="conflict")
        with self.assertRaises(ConflictError):
            engine.execute(job["job_id"])

    def test_transient_read_has_bounded_retry(self):
        engine, gateway = self.engine()
        gateway.read_failures.extend((TransientReadError("one"), TransientReadError("two")))
        job = engine.start(target="store-a", expected_target="store-a", idempotency_key="retry")
        self.assertEqual(job["state"], "PREVIEW_READY")

    def test_transient_read_stops_at_limit(self):
        engine, gateway = self.engine()
        gateway.read_failures.extend(TransientReadError(str(i)) for i in range(3))
        with self.assertRaises(TransientReadError):
            engine.start(target="store-a", expected_target="store-a", idempotency_key="limit")

    def test_permanent_read_does_not_retry(self):
        engine, gateway = self.engine()
        gateway.read_failures.extend((PermanentReadError("no"), TransientReadError("unused")))
        with self.assertRaises(PermanentReadError):
            engine.start(target="store-a", expected_target="store-a", idempotency_key="permanent")
        self.assertEqual(len(gateway.read_failures), 1)

    def test_unknown_write_never_retries(self):
        engine, gateway = self.engine(results=[OperationResult(WriteResult.UNKNOWN, reason="timeout")])
        job = self.ready_job(engine)
        with self.assertRaises(UnknownWriteError):
            engine.execute(job["job_id"])
        with self.assertRaises(UnknownWriteError):
            engine.execute(job["job_id"])
        self.assertEqual(gateway.write_calls, 1)

    def test_unknown_reconciles_only_from_matching_readback(self):
        engine, gateway = self.engine(results=[OperationResult(WriteResult.UNKNOWN, reason="timeout")])
        job = self.ready_job(engine)
        with self.assertRaises(UnknownWriteError):
            engine.execute(job["job_id"])
        with self.assertRaises(UnknownWriteError):
            engine.reconcile_unknown(job["job_id"], "missing")

    def test_unknown_reconciliation_completes_from_matching_readback(self):
        engine, gateway = self.engine(results=[OperationResult(WriteResult.UNKNOWN, reason="timeout")])
        job = self.ready_job(engine)
        with self.assertRaises(UnknownWriteError):
            engine.execute(job["job_id"])
        plan = self.store.plan(job["plan_id"])
        request = {"plan_id": plan["plan_id"], "target": plan["target"],
                   "quantity": plan["total_quantity"], "boxes": plan["box_count"]}
        gateway.external_records["EXT-RECOVERED"] = {
            "request_fingerprint": fingerprint(request), **request,
        }
        final = engine.reconcile_unknown(job["job_id"], "EXT-RECOVERED")
        self.assertEqual(final["state"], "COMPLETED")
        self.assertEqual(final["external_id"], "EXT-RECOVERED")
        self.assertEqual(gateway.write_calls, 1)

    def test_readback_mismatch_fails(self):
        engine, gateway = self.engine()
        job = self.ready_job(engine)
        original = gateway.readback
        gateway.readback = lambda _external_id: {"request_fingerprint": "wrong"}
        final = engine.execute(job["job_id"])
        self.assertEqual(final["state"], "FAILED")
        gateway.readback = original

    def test_immutable_plan_tampering_is_blocked(self):
        engine, gateway = self.engine()
        job = self.ready_job(engine)
        with self.store.connection() as connection:
            connection.execute("UPDATE plans SET total_quantity=999 WHERE plan_id=?", (job["plan_id"],))
        with self.assertRaises(SafetyGateError):
            engine.execute(job["job_id"])
        self.assertEqual(gateway.write_calls, 0)

    def test_blast_radius_is_one_job(self):
        bad_engine, _ = self.engine(make_snapshot(schema=2))
        with self.assertRaises(SafetyGateError):
            bad_engine.start(target="store-a", expected_target="store-a", idempotency_key="bad-one")
        good_engine, _ = self.engine(make_snapshot(snapshot_id="snap-good"))
        good = good_engine.start(target="store-a", expected_target="store-a", idempotency_key="good-one")
        self.assertEqual(good["state"], "PREVIEW_READY")


if __name__ == "__main__":
    unittest.main()
