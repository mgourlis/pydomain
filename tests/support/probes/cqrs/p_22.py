"""Probe for CQRS-22: re-applying the same event to a projection."""
import asyncio
import sys
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).parent))
from common import OrderPlaced, new_id  # noqa: E402
from pydomain.es.projection import EventSourcedProjection  # noqa: E402


class OrderCount(EventSourcedProjection):
    name: ClassVar[str] = "order-count"
    version: ClassVar[int] = 1

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    async def _when_OrderPlaced(self, event: OrderPlaced) -> None:
        self.count += 1


async def main() -> None:
    p = OrderCount()
    ev = OrderPlaced(order_id=new_id())
    await p.apply(ev)
    await p.apply(ev)  # redelivery (ADR-048 at-least-once)
    print(f"CQRS-22 same event applied twice: count={p.count}, checkpoint={p.checkpoint}")

asyncio.run(main())
