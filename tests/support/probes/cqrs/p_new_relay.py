"""NEW: relay drops entry tracing; ValidationError text carries payload content."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import *  # noqa: F403
from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxWriter
from pydomain.testing import FakeOutboxStore


class OrderPlacedV1(IntegrationEvent):
    order_id: int  # stricter than what was written


async def main() -> None:
    r = OutboundEventRegistry()
    r.register_translation(OrderPlaced, OrderPlacedV1, "orders.placed",
                           lambda e: OrderPlacedV1(order_id=7))
    store = FakeOutboxStore()
    u = SpyUoW([], {}, outbox_writer=OutboxWriter(store, r))

    async def h(cmd, uow):
        o = Order(id=cmd.order_id); o.place(); await uow.orders.save(o)
        return PlaceOrderResult(order_id=cmd.order_id)

    bus = CommandBus(); bus.register(PlaceOrder, h, lambda: u)
    await bus.dispatch(PlaceOrder(order_id=new_id()))
    e = store.entries[0]
    ev = r.resolve(e.topic).model_validate(e.payload)  # what the relay does (message_broker.py:289)
    print(f"NEW relay: entry.correlation_id set={e.correlation_id is not None}; "
          f"rehydrated event correlation_id={ev.correlation_id}")
    bad = dict(e.payload, order_id="SENTINEL-XYZ")
    try:
        OrderPlacedV1.model_validate(bad)
    except Exception as x:  # noqa: BLE001
        print(f"NEW relay failure text f'Payload validation failed: {{exc}}' contains sentinel: {'SENTINEL-XYZ' in str(x)}")

asyncio.run(main())
