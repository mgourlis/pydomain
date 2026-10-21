"""NEW-INF-1 and NEW-INF-2 probes: real bootstrap()/Application. Synthetic data only."""

import asyncio
import logging
from uuid import UUID

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.bootstrap import bootstrap
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.testing import InMemoryMessageSubscriber

logging.basicConfig(level=logging.CRITICAL)
ORDER = "00000000-0000-4000-8000-000000000001"


class ShipmentFailedIE(IntegrationEvent):
    order_id: str


class ShipmentFailed(DomainEvent):
    order_id: UUID


async def main() -> None:
    app = await bootstrap()
    public = sorted(n for n in dir(app) if not n.startswith("_"))
    print("NEW-INF-1 public Application members:", public)
    for attr in ("message_bus", "event_registry"):
        try:
            getattr(app, attr)
            print(f"app.{attr}: OK")
        except AttributeError as exc:
            print(f"app.{attr}: AttributeError: {exc}")

    # NEW-INF-2: how-to configure-inbound-event-gateway steps 4-6, with the bus the gateway needs
    # created explicitly (step 4 cannot use app.message_bus: app does not exist yet).
    gw_bus = MessageBus()
    sub = InMemoryMessageSubscriber()
    gw = InboundEventGateway(sub, gw_bus)
    gw.register_translation("shipping.failed", ShipmentFailedIE,
                            lambda ie: ShipmentFailed(order_id=UUID(ie.order_id)))
    app2 = await bootstrap(inbound_gateways=[gw])  # step 5: no message_bus=
    print("NEW-INF-2 app bus is gateway bus:", app2._message_bus is gw_bus)
    handled: list[object] = []

    async def h(e: ShipmentFailed) -> None:
        handled.append(e)

    app2._message_bus.register_event(ShipmentFailed, h)  # step 6 intent: register on the app's bus
    try:
        await sub.simulate_message("shipping.failed", {"order_id": ORDER})
        print("NEW-INF-2 inbound delivered; handler on app bus called:", len(handled))
    except Exception as exc:  # noqa: BLE001
        print("NEW-INF-2 simulate raised", type(exc).__name__, exc)
    await app2.shutdown()


asyncio.run(main())
