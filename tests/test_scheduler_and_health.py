from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from fba_orchestrator.health import HealthHandler
from fba_orchestrator.scheduler import LeaseManager
from fba_orchestrator.store import WorkflowStore
from http.server import ThreadingHTTPServer


class SchedulerAndHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = WorkflowStore(Path(self.temp.name) / "workflow.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_lease_competition_allows_one_owner(self):
        self.assertTrue(self.store.acquire_lease("job", "worker-a", 30, now=100))
        self.assertFalse(self.store.acquire_lease("job", "worker-b", 30, now=101))

    def test_expired_lease_can_be_recovered(self):
        self.assertTrue(self.store.acquire_lease("job", "dead-worker", 5, now=100))
        self.assertTrue(self.store.acquire_lease("job", "recovery-worker", 5, now=106))

    def test_lease_context_releases_on_failure(self):
        manager = LeaseManager(self.store)
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with manager.claim("job", "worker-a", now=100):
                raise RuntimeError("boom")
        self.assertTrue(self.store.acquire_lease("job", "worker-b", 30, now=101))

    def test_health_api(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/health", timeout=2
            ) as response:
                payload = json.loads(response.read())
            self.assertEqual(payload["status"], "healthy")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
