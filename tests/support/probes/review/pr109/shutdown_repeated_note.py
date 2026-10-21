"""B-90 (DCE-128): shutdown() adds a note the exception already carries.

Case D2 of the PR #109 review probe break.py, on its own: two stops raise
the same exception instance, then shutdown() runs a second time. Synthetic
data only.
"""
import asyncio

from pydomain.infrastructure import MessageBus, bootstrap
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.testing import InMemoryMessageSubscriber


class FailingStop(InMemoryMessageSubscriber):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def stop(self) -> None:
        raise self.error


async def main() -> None:
    bus = MessageBus()
    shared = RuntimeError("synthetic stop failure")
    app = await bootstrap(
        message_bus=bus,
        inbound_gateways=[
            InboundEventGateway(FailingStop(shared), bus),
            InboundEventGateway(FailingStop(shared), bus),
        ],
    )
    for attempt in ("first", "second"):
        try:
            await app.shutdown()
        except Exception as exc:  # noqa: BLE001
            print(f"{attempt} shutdown notes:", getattr(exc, "__notes__", []))


asyncio.run(main())
