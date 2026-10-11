from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from .engine import FulfillmentEngine
from .gateway import FakeWarehouseGateway
from .health import serve
from .models import InventoryItem, Snapshot
from .store import WorkflowStore


def demo() -> None:
    path = Path(tempfile.mkdtemp()) / "demo.sqlite3"
    snapshot = Snapshot("snap-demo", "demo-store", 1, time.time(), (
        InventoryItem("DEMO-SKU-A", 60, 0.25),
        InventoryItem("DEMO-SKU-B", 30, 0.40),
    ))
    engine = FulfillmentEngine(WorkflowStore(path), FakeWarehouseGateway([snapshot]))
    job = engine.start(target="demo-store", expected_target="demo-store",
                       idempotency_key="demo-request")
    engine.confirm(job["job_id"])
    final = engine.execute(job["job_id"])
    print(json.dumps(final, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("demo", "health"), nargs="?", default="demo")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    demo() if args.command == "demo" else serve(port=args.port)


if __name__ == "__main__":
    main()
