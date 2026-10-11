from __future__ import annotations

import hashlib
import json
import time
import uuid

from .errors import ConflictError, PermanentReadError, SafetyGateError, TransientReadError, UnknownWriteError
from .models import ImmutablePlan, JobState, Snapshot, WriteResult
from .store import WorkflowStore


def fingerprint(value: object) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode()).hexdigest()


class FulfillmentEngine:
    SUPPORTED_SCHEMA = 1

    def __init__(self, store: WorkflowStore, gateway, *, snapshot_ttl: float = 300,
                 read_retry_limit: int = 3, retry_delay: float = 0) -> None:
        self.store = store
        self.gateway = gateway
        self.snapshot_ttl = snapshot_ttl
        self.read_retry_limit = read_retry_limit
        self.retry_delay = retry_delay

    def start(self, *, target: str, idempotency_key: str, expected_target: str,
              now: float | None = None) -> dict:
        if not target or target != expected_target:
            raise SafetyGateError("target mismatch")
        job, created = self.store.create_job(uuid.uuid4().hex, idempotency_key, target)
        if not created:
            if job["target"] != target:
                raise ConflictError("idempotency key belongs to a different target")
            return job
        snapshot = self._read_with_retry(target)
        now = time.time() if now is None else now
        self._validate_snapshot(snapshot, target, now)
        payload = {
            "target": snapshot.target,
            "items": [(item.sku, item.quantity, item.unit_weight_kg) for item in snapshot.items],
        }
        snap_hash = fingerprint(payload)
        self.store.save_snapshot(snapshot.snapshot_id, target, snapshot.schema_version,
                                 snap_hash, payload, snapshot.created_at)
        self.store.transition(job["job_id"], JobState.NEW, JobState.SNAPSHOT_VERIFIED,
                              snapshot_id=snapshot.snapshot_id)
        plan = self._build_plan(snapshot, snap_hash)
        self.store.save_plan(
            plan_id=plan.plan_id, job_id=job["job_id"], target=target,
            snapshot_id=plan.snapshot_id, fingerprint=plan.snapshot_fingerprint,
            total_quantity=plan.total_quantity, box_count=plan.box_count,
        )
        self.store.transition(job["job_id"], JobState.SNAPSHOT_VERIFIED,
                              JobState.PREVIEW_READY, plan_id=plan.plan_id)
        return self.store.job(job["job_id"])

    def confirm(self, job_id: str) -> dict:
        job = self.store.job(job_id)
        if job["state"] == JobState.CONFIRMED:
            return job
        if job["state"] != JobState.PREVIEW_READY:
            raise ConflictError("job is not awaiting confirmation")
        self.store.confirm_plan(job["plan_id"])
        self.store.transition(job_id, JobState.PREVIEW_READY, JobState.CONFIRMED)
        return self.store.job(job_id)

    def execute(self, job_id: str) -> dict:
        job = self.store.job(job_id)
        if job["state"] == JobState.COMPLETED:
            return job
        if job["state"] == JobState.UNKNOWN:
            raise UnknownWriteError("readback required before retry")
        if job["state"] != JobState.CONFIRMED:
            raise ConflictError("job is not confirmed")
        plan = self.store.plan(job["plan_id"])
        request = {"plan_id": plan["plan_id"], "target": plan["target"],
                   "quantity": plan["total_quantity"], "boxes": plan["box_count"]}
        request_hash = fingerprint(request)
        if request_hash != plan["fingerprint"]:
            raise SafetyGateError("immutable plan fingerprint mismatch")
        attempt_id = f"write:{job_id}"
        prior, created = self.store.begin_write(attempt_id, job_id, "CREATE_SHIPMENT", request_hash)
        if not created:
            if prior["result"] == WriteResult.SUCCESS:
                self.store.transition(job_id, JobState.CONFIRMED, JobState.COMPLETED,
                                      external_id=prior["external_id"])
                return self.store.job(job_id)
            raise UnknownWriteError("write intent already exists")
        self.store.transition(job_id, JobState.CONFIRMED, JobState.WRITE_INTENT)
        result = self.gateway.write_plan(request_hash, request)
        self.store.finish_write(attempt_id, result.result, result.external_id, result.reason)
        if result.result is WriteResult.UNKNOWN:
            self.store.transition(job_id, JobState.WRITE_INTENT, JobState.UNKNOWN,
                                  error=result.reason)
            raise UnknownWriteError("external result unknown; retry forbidden")
        if result.result is WriteResult.FAILED:
            self.store.transition(job_id, JobState.WRITE_INTENT, JobState.FAILED,
                                  error=result.reason)
            return self.store.job(job_id)
        actual = self.gateway.readback(result.external_id)
        if actual is None or actual.get("request_fingerprint") != request_hash:
            self.store.transition(job_id, JobState.WRITE_INTENT, JobState.FAILED,
                                  error="readback mismatch")
            return self.store.job(job_id)
        self.store.transition(job_id, JobState.WRITE_INTENT, JobState.COMPLETED,
                              external_id=result.external_id)
        return self.store.job(job_id)

    def reconcile_unknown(self, job_id: str, external_id: str) -> dict:
        job = self.store.job(job_id)
        if job["state"] != JobState.UNKNOWN:
            raise ConflictError("job is not UNKNOWN")
        plan = self.store.plan(job["plan_id"])
        actual = self.gateway.readback(external_id)
        expected = fingerprint({"plan_id": plan["plan_id"], "target": plan["target"],
                                "quantity": plan["total_quantity"], "boxes": plan["box_count"]})
        if actual is None or actual.get("request_fingerprint") != expected:
            raise UnknownWriteError("readback cannot prove outcome")
        self.store.transition(job_id, JobState.UNKNOWN, JobState.COMPLETED,
                              external_id=external_id, error="")
        return self.store.job(job_id)

    def _read_with_retry(self, target: str) -> Snapshot:
        for attempt in range(self.read_retry_limit):
            try:
                return self.gateway.read_snapshot(target)
            except TransientReadError:
                if attempt + 1 >= self.read_retry_limit:
                    raise
                if self.retry_delay:
                    time.sleep(self.retry_delay)
            except PermanentReadError:
                raise
        raise AssertionError("unreachable")

    def _validate_snapshot(self, snapshot: Snapshot, target: str, now: float) -> None:
        if snapshot.target != target:
            raise SafetyGateError("snapshot target mismatch")
        if snapshot.schema_version != self.SUPPORTED_SCHEMA:
            raise SafetyGateError("incompatible snapshot schema")
        if now - snapshot.created_at > self.snapshot_ttl:
            raise SafetyGateError("snapshot expired")
        skus = [item.sku for item in snapshot.items]
        if not skus or len(skus) != len(set(skus)) or any(not sku for sku in skus):
            raise SafetyGateError("missing or duplicate primary key")
        if any(item.quantity < 0 or item.unit_weight_kg <= 0 for item in snapshot.items):
            raise SafetyGateError("invalid inventory data")

    @staticmethod
    def _build_plan(snapshot: Snapshot, snap_hash: str) -> ImmutablePlan:
        total = sum(item.quantity for item in snapshot.items)
        boxes = max(1, (total + 59) // 60)
        plan_id = fingerprint({"snapshot": snapshot.snapshot_id, "target": snapshot.target,
                               "quantity": total, "boxes": boxes})[:24]
        request_hash = fingerprint({"plan_id": plan_id, "target": snapshot.target,
                                    "quantity": total, "boxes": boxes})
        return ImmutablePlan(plan_id, snapshot.target, snapshot.snapshot_id,
                             request_hash, total, boxes)
