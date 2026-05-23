from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.checkpoint_store import CheckpointStore
from pydomain.es.event_store import EventStore
from pydomain.es.event_stream import EventStream

if TYPE_CHECKING:
    from pydomain.es.projection import EventSourcedProjection


logger = logging.getLogger(__name__)


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
    projection: EventSourcedProjection
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
        failure_backoff_seconds: float = 0.1,
    ) -> None:
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be >= 0")
        if failure_backoff_seconds < 0:
            raise ValueError("failure_backoff_seconds must be >= 0")
        self._event_store = event_store
        self._checkpoint_store = checkpoint_store
        self._poll_interval_seconds = poll_interval_seconds
        self._failure_backoff_seconds = failure_backoff_seconds
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

        Each iteration loads all subscription checkpoints, reads the
        global event log **once** from the furthest-behind checkpoint,
        then dispatches matching events to each subscription.
        When no new events are found, sleeps ``poll_interval_seconds``.
        """
        self._stop_requested = False
        while not self._stop_requested:
            had_events = await self._process_cycle()
            if not had_events and not self._stop_requested:
                await asyncio.sleep(self._poll_interval_seconds)

    async def run_once(self) -> None:
        """Single catch-up pass for all registered subscriptions.

        Reads the global event log once and dispatches to all
        subscriptions.  Useful for tests and controlled invocations
        where a polling loop is not desired.
        """
        await self._process_cycle()

    def stop(self) -> None:
        """Request graceful exit.

        The current batch completes before the :meth:`run` loop exits.
        """
        self._stop_requested = True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _process_cycle(self) -> bool:
        """Single processing cycle: one event-store read, all subscriptions.

        Loads all subscription checkpoints, reads the global event log
        once from the minimum checkpoint, then dispatches matching
        events to each subscription independently.

        Returns ``True`` if new events existed in the global log.
        """
        if not self._subscriptions:
            return False

        # Load all checkpoints
        checkpoints: dict[str, int] = {}
        for sub in self._subscriptions.values():
            checkpoints[sub.subscription_id] = await self._checkpoint_store.load(
                sub.subscription_id
            )

        # Single DB read from the furthest-behind position
        min_checkpoint = min(checkpoints.values())
        stream = await self._event_store.read_all(from_version=min_checkpoint)

        if not stream.events:
            return False

        # Process each subscription from the shared stream
        for sub in self._subscriptions.values():
            if self._stop_requested:
                break
            await self._dispatch_to_subscription(
                sub, stream, min_checkpoint, checkpoints[sub.subscription_id]
            )

        return True

    async def _dispatch_to_subscription(
        self,
        subscription: Subscription,
        stream: EventStream,
        stream_start: int,
        subscription_checkpoint: int,
    ) -> None:
        """Dispatch events to one subscription from a shared stream.

        Slices the stream from the subscription's checkpoint, filters
        by ``event_types``, and calls :meth:`process_batch`.  If
        ``process_batch`` raises, the checkpoint is **not** updated
        (at-least-once guarantee).
        """
        offset = subscription_checkpoint - stream_start
        sub_events = stream.events[offset:]

        if not sub_events:
            return

        matching = [e for e in sub_events if isinstance(e, subscription.event_types)]
        if matching:
            try:
                await self.process_batch(matching, subscription)
            except Exception:
                logger.warning(
                    "Subscription %r batch processing failed; will retry in %.1fs",
                    subscription.subscription_id,
                    self._failure_backoff_seconds,
                    exc_info=True,
                )
                await asyncio.sleep(self._failure_backoff_seconds)
                return

        await self._checkpoint_store.save(subscription.subscription_id, stream.version)
