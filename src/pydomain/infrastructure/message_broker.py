"""Message broker protocol and outbound event gateway.

The ``MessageBroker`` protocol defines the contract for publishing integration
events to external message brokers (RabbitMQ, Kafka, etc.).

``OutboundEventGateway`` is the relay that drains the transactional outbox and
publishes what it finds — the outbound counterpart to ``InboundEventGateway``.
See ADR-063.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import (
    OutboundEventRegistry,
    OutboxEntry,
    OutboxStore,
)

logger = logging.getLogger("pydomain.message_broker")

__all__ = [
    "MessageBroker",
    "OutboundEventGateway",
]

_BACKOFF_EXPONENT_CAP = 16


@runtime_checkable
class MessageBroker(Protocol):
    """Protocol for publishing integration events to external brokers.

    Implementations wrap production brokers (RabbitMQ, Kafka) for real
    event publishing. The protocol is runtime-checkable so that tests
    can use ``isinstance()`` checks.
    """

    async def publish(
        self,
        topic: str,
        event: IntegrationEvent,
        **kwargs: Any,
    ) -> None:
        """Publish an integration event to the given topic.

        Parameters
        ----------
        topic:
            The topic or routing key to publish to.
        event:
            The integration event to publish.  Should be stamped with
            ``correlation_id`` and ``causation_id`` via
            :meth:`IntegrationEvent.stamp` for cross-service traceability.
        **kwargs:
            Additional transport-level keyword arguments (e.g.
            ``headers`` for W3C trace context propagation).  Concrete
            broker implementations should extract and propagate
            recognised keyword arguments, ignoring those they do not
            support.
        """

    async def start(self) -> None:
        """Initialize connection or resources.

        Called at application startup.
        """

    async def stop(self) -> None:
        """Graceful shutdown and resource cleanup.

        Called at application shutdown.
        """


class OutboundEventGateway:
    """Relays unpublished integration events from the outbox to a broker.

    The gateway is deliberately domain-ignorant: it knows topics and
    ``IntegrationEvent`` classes, never domain types.  It resolves the
    integration class from the entry's ``topic``, rehydrates the stored
    payload, publishes, and marks the entry published.

    **An entry is marked published only after the broker accepts it.**
    Every other outcome — an unknown topic, a payload that fails
    validation, or a broker error — leaves the entry unpublished.
    Marking a failed publish as delivered would silently lose the message.

    Failures are retried with an exponential backoff until the entry has
    spent ``max_attempts`` attempts, after which it is **dead-lettered**:
    it stops competing for the relay's attention but stays visible to
    operators, who can inspect it or replay it after fixing the cause.
    Exhaustion is logged at ``ERROR`` level — a dead letter is a delivery
    the system has given up on and it should never be silent.

    The retry budget does not distinguish failure kinds. An unknown topic
    is usually recoverable by deploying the missing translation, and a
    validation failure is usually terminal, but neither is knowable from
    the failure itself, so both are bounded by the same budget.

    The gateway must run as its own pump, **not** as an ``EventBus``
    handler: ``EventBus`` logs and swallows handler exceptions (ADR-046),
    which is correct for in-process reactions and wrong for a
    non-replayable side effect.

    Parameters
    ----------
    store:
        The outbox the gateway drains.
    broker:
        The broker integration events are published to.
    registry:
        Resolves the integration event class for a stored topic.
    batch_size:
        Maximum number of entries claimed per pass.
    base_delay:
        Delay before the first retry of a failed entry.
    max_delay:
        Ceiling for the exponential backoff applied to repeated failures.
    max_attempts:
        Failed attempts allowed before an entry is dead-lettered. ``None``
        retries forever. The budget counts deliveries that raised, so the
        entry is dead-lettered on the attempt that exhausts it.
    clock:
        Injectable time source, for deterministic tests.
    poll_interval_seconds:
        Sleep between passes that publish nothing.
    failure_backoff_seconds:
        Sleep after a pass raises before trying again.
    """

    def __init__(
        self,
        store: OutboxStore,
        broker: MessageBroker,
        registry: OutboundEventRegistry,
        *,
        batch_size: int = 100,
        base_delay: timedelta = timedelta(seconds=5),
        max_delay: timedelta = timedelta(minutes=30),
        max_attempts: int | None = 10,
        clock: Callable[[], datetime] | None = None,
        poll_interval_seconds: float = 1.0,
        failure_backoff_seconds: float = 0.1,
    ) -> None:
        self._store = store
        self._broker = broker
        self._registry = registry
        self._batch_size = batch_size
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._max_attempts = max_attempts
        self._clock = clock or (lambda: datetime.now(UTC))
        self._poll_interval_seconds = poll_interval_seconds
        self._failure_backoff_seconds = failure_backoff_seconds
        self._stop_requested = False
        self._running = False
        self._stopped = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> int:
        """Drain and publish one batch of unpublished entries.

        Entries are processed independently: a failure on one does not
        prevent the rest of the batch from being delivered.

        A failed entry is never marked published. It is rescheduled with an
        exponential backoff until it exhausts ``max_attempts``, at which
        point it is dead-lettered and never fetched again.

        Returns
        -------
        int
            The number of entries successfully published.
        """
        entries = await self._store.fetch_unpublished(self._batch_size)
        published: list[str] = []

        for entry in entries:
            error = await self._deliver(entry)
            if error is None:
                published.append(entry.message_id)
                continue

            attempts = entry.attempts + 1
            if self._max_attempts is not None and attempts >= self._max_attempts:
                logger.error(
                    "Outbox entry %s on topic '%s' exhausted %d attempts "
                    "— dead-lettered: %s",
                    entry.message_id,
                    entry.topic,
                    self._max_attempts,
                    error,
                )
                await self._store.mark_dead_lettered(entry.message_id, error=error)
                continue

            await self._store.mark_failed(
                entry.message_id,
                error=error,
                next_attempt_at=self._next_attempt_at(entry.attempts),
            )

        if published:
            await self._store.mark_published(published)

        return len(published)

    async def run(self) -> None:
        """Poll the outbox until :meth:`stop` is called.

        Designed to be driven as a background task. Between passes that
        publish nothing the relay sleeps ``poll_interval_seconds``, so an
        idle outbox costs one query per interval rather than a busy loop.
        """
        self._stopped = asyncio.Event()
        self._running = True
        self._stop_requested = False
        try:
            while not self._stop_requested:
                try:
                    published = await self.run_once()
                except Exception:
                    logger.exception("Outbox relay pass failed; backing off")
                    if not self._stop_requested:
                        await asyncio.sleep(self._failure_backoff_seconds)
                    continue
                if published == 0 and not self._stop_requested:
                    await asyncio.sleep(self._poll_interval_seconds)
        finally:
            self._running = False
            self._stopped.set()

    async def start(self) -> None:
        """Start the relay in the background.

        Schedules :meth:`run` as a task so ``bootstrap()`` can manage the
        gateway's lifecycle alongside the broker. Calling ``start`` twice
        is a no-op.
        """
        if self._task is not None:
            return
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        """Stop the relay and drain the in-flight pass.

        Returns once :meth:`run` has exited, so a caller that stops the
        relay before the broker can be sure no publish is still in flight.
        Returns immediately when the relay is not running.
        """
        if not self._running:
            return
        self._stop_requested = True
        await self._stopped.wait()
        if self._task is not None:
            await self._task
            self._task = None

    async def _deliver(self, entry: OutboxEntry) -> str | None:
        """Attempt to deliver one entry.

        Returns ``None`` on success, or a description of the failure.

        Every failure mode — an unknown topic, an invalid payload, or a
        broker error — leaves the entry unpublished. Whether the failure is
        retried or dead-lettered is decided by the retry budget in
        :meth:`run_once`, not by the failure's kind.
        """
        try:
            integration_class = self._registry.resolve(entry.topic)
        except KeyError:
            logger.error(
                "No translation registered for topic '%s' — %s requeued",
                entry.topic,
                entry.message_id,
            )
            return f"No translation registered for topic '{entry.topic}'"

        try:
            event = integration_class.model_validate(entry.payload)
        except ValidationError as exc:
            logger.error(
                "Payload validation failed for %s on topic '%s' — requeued",
                entry.message_id,
                entry.topic,
            )
            return f"Payload validation failed: {exc}"

        try:
            await self._broker.publish(entry.topic, event)
        except Exception as exc:
            logger.exception(
                "Publish failed for %s on topic '%s' — requeued",
                entry.message_id,
                entry.topic,
            )
            return f"Broker publish failed: {exc}"

        return None

    def _next_attempt_at(self, previous_attempts: int) -> datetime:
        """Return the next attempt time using capped exponential backoff.

        The exponent is clamped before evaluation so a long-failing row
        cannot overflow, and the resulting delay is capped at
        ``max_delay``.
        """
        exponent = min(previous_attempts, _BACKOFF_EXPONENT_CAP)
        seconds = min(
            self._base_delay.total_seconds() * (2**exponent),
            self._max_delay.total_seconds(),
        )
        return self._clock() + timedelta(seconds=seconds)
