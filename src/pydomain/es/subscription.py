from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.checkpoint_store import CheckpointStore
from pydomain.es.event_store import EventStore

if TYPE_CHECKING:
    from pydomain.cqrs.projection import Projection


@dataclass
class Subscription:
    """Binds a projection to the event types it handles.

    Attributes
    ----------
    subscription_id:
        Unique identity for this subscription.
    projection:
        The projection that receives matching events.
    event_types:
        Tuple of ``DomainEvent`` subclasses to filter on.
    """

    subscription_id: str
    projection: Projection
    event_types: tuple[type[DomainEvent], ...]


class SubscriptionRunner(ABC):
    """Coordinates catch-up subscriptions from EventStore to projections.

    Abstract base class — subclasses must implement
    :meth:`process_batch` to define how matching events are handled.

    For each registered subscription, reads new events from the
    EventStore global log starting from the last checkpoint, filters by
    event type, and delegates to :meth:`process_batch`.
    """

    def __init__(
        self,
        event_store: EventStore,
        checkpoint_store: CheckpointStore,
        *,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be >= 0")
        self._event_store = event_store
        self._checkpoint_store = checkpoint_store
        self._poll_interval_seconds = poll_interval_seconds
        self._subscriptions: dict[str, Subscription] = {}
        self._stop_requested = False

    def add_subscription(self, subscription: Subscription) -> None:
        """Register a subscription with the runner."""
        self._subscriptions[subscription.subscription_id] = subscription

    @abstractmethod
    async def process_batch(
        self,
        events: Sequence[DomainEvent],
        subscription: Subscription,
    ) -> None:
        """Process a batch of matching events.

        Subclasses implement projection application, external dispatch,
        or any other side-effect here.  If this method raises, the
        checkpoint is **not** updated (at-least-once guarantee).
        """
        ...

    async def run(self) -> None:
        """Polling loop — runs until :meth:`stop` is called.

        Each iteration: load checkpoints, read global log, filter by
        event type, call :meth:`process_batch`, save checkpoints.
        When no events are found, sleeps ``poll_interval_seconds``.
        """
        self._stop_requested = False
        while not self._stop_requested:
            had_events = False
            for subscription in self._subscriptions.values():
                if self._stop_requested:
                    break
                had_events |= await self._process_subscription(subscription)
            if not had_events and not self._stop_requested:
                await asyncio.sleep(self._poll_interval_seconds)

    async def run_once(self) -> None:
        """Single catch-up pass for all registered subscriptions.

        Useful for tests and controlled invocations where a polling
        loop is not desired.
        """
        for subscription in self._subscriptions.values():
            await self._process_subscription(subscription)

    def stop(self) -> None:
        """Request graceful exit.

        The current batch completes before the :meth:`run` loop exits.
        """
        self._stop_requested = True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _process_subscription(self, subscription: Subscription) -> bool:
        """Process a single subscription. Returns ``True`` if events existed."""
        checkpoint = await self._checkpoint_store.load(subscription.subscription_id)
        stream = await self._event_store.read_all(from_version=checkpoint)
        if not stream.events:
            return False
        matching = [e for e in stream.events if isinstance(e, subscription.event_types)]
        if matching:
            try:
                await self.process_batch(matching, subscription)
            except Exception:
                # At-least-once: do NOT advance checkpoint on failure.
                # Brief pause to avoid a busy-loop if the failure is
                # permanent (poison event, broken projection, etc.).
                await asyncio.sleep(0.1)
                return True
        await self._checkpoint_store.save(subscription.subscription_id, stream.version)
        return True
