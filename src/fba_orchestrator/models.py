from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class JobState(StrEnum):
    NEW = "NEW"
    SNAPSHOT_VERIFIED = "SNAPSHOT_VERIFIED"
    PREVIEW_READY = "PREVIEW_READY"
    CONFIRMED = "CONFIRMED"
    WRITE_INTENT = "WRITE_INTENT"
    UNKNOWN = "UNKNOWN"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class WriteResult(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class InventoryItem:
    sku: str
    quantity: int
    unit_weight_kg: float


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    target: str
    schema_version: int
    created_at: float
    items: tuple[InventoryItem, ...]


@dataclass(frozen=True)
class ImmutablePlan:
    plan_id: str
    target: str
    snapshot_id: str
    snapshot_fingerprint: str
    total_quantity: int
    box_count: int


@dataclass(frozen=True)
class OperationResult:
    result: WriteResult
    external_id: str | None = None
    reason: str = ""
