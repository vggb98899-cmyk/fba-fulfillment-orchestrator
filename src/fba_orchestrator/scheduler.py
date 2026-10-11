from __future__ import annotations

from contextlib import contextmanager

from .store import WorkflowStore


class LeaseManager:
    def __init__(self, store: WorkflowStore, ttl: float = 30) -> None:
        self.store = store
        self.ttl = ttl

    @contextmanager
    def claim(self, key: str, owner: str, now: float | None = None):
        acquired = self.store.acquire_lease(key, owner, self.ttl, now)
        if not acquired:
            raise RuntimeError("lease already owned")
        try:
            yield
        finally:
            self.store.release_lease(key, owner)
