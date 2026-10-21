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
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from pydomain._describe import describe
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


class _Run:
    """One run of the relay loop: its own stop request and exit signal.

    Each run gets a fresh one, so a ``stop()`` aimed at a run that is
    winding down never reaches a run a later ``start()`` began (ADR-074).
    """

    def __init__(self) -> None:
        self.stop_requested = False
        self.stopped = asyncio.Event()


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
            support. ``OutboundEventGateway`` passes
            ``headers={"correlation_id": ..., "causation_id": ...}`` for a
            stamped event (ADR-074), so an implementation must accept
            ``**kwargs``.
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

    Stored errors and log lines never carry payload values (ADR-071). A
    validation failure is described by the model, the failing field
    locations and their Pydantic error codes; any other exception, including
    a broker or store error, by its type name only, and without a traceback.

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

    Raises
    ------
    ValueError
        If ``batch_size`` or ``max_attempts`` is below ``1``, a delay or
        sleep is negative, or ``max_delay`` is shorter than ``base_delay``
        (ADR-074). The message names the argument.

    Notes
    -----
    Before publishing, the relay stamps the event with the entry's
    ``correlation_id`` and ``causation_id`` when both are set, overriding
    any ids the translator put in the payload, and passes the same ids as
    ``headers={"correlation_id": ..., "causation_id": ...}``. An entry
    without them is published unstamped and without ``headers``
    (ADR-061, ADR-074).
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
        _check_arguments(
            batch_size=batch_size,
            max_attempts=max_attempts,
            base_delay=base_delay,
            max_delay=max_delay,
            poll_interval_seconds=poll_interval_seconds,
            failure_backoff_seconds=failure_backoff_seconds,
        )
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
        self._run_state: _Run | None = None
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> int:
        """Drain and publish one batch of unpublished entries.

        Deliveries are independent: a failed delivery (an unknown topic,
        an invalid payload, a broker error) does not prevent the rest of
        the batch from being delivered. A store error, from a mark or the
        fetch, ends the pass and propagates. The entries of the batch not yet
        delivered stay claimed, so a later fetch returns them once their
        claim goes stale (ADR-074).

        Each entry is marked published as soon as the broker accepts it,
        before the next entry is delivered (ADR-074). A store failure or a
        cancellation later in the pass therefore leaves earlier entries
        published; only the entry in flight can be delivered again, the
        at-least-once window ADR-063 accepts.

        A failed entry is never marked published. It is rescheduled with an
        exponential backoff until it exhausts ``max_attempts``, at which
        point it is dead-lettered and never fetched again.

        Returns
        -------
        int
            The number of entries successfully published.
        """
        entries = await self._store.fetch_unpublished(self._batch_size)
        published = 0

        for entry in entries:
            error = await self._deliver(entry)
            if error is None:
                await self._store.mark_published([entry.message_id])
                published += 1
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

        return published

    async def run(self) -> None:
        """Poll the outbox until :meth:`stop` is called.

        Designed to be driven as a background task. Between passes that
        publish nothing the relay sleeps ``poll_interval_seconds``, so an
        idle outbox costs one query per interval rather than a busy loop.

        A :meth:`stop` reaches this call only once it has begun; a stop made
        before a task you created for ``run()`` starts is lost (a known gap,
        ADR-074). Use :meth:`start` for a background relay.
        """
        run_state = _Run()
        self._run_state = run_state
        await self._run_loop(run_state)

    async def _run_loop(self, run_state: _Run) -> None:
        """Poll until *run_state* is asked to stop."""
        try:
            while not run_state.stop_requested:
                try:
                    published = await self.run_once()
                except Exception as exc:
                    logger.error(
                        "Outbox relay pass failed: %s; backing off", describe(exc)
                    )
                    if not run_state.stop_requested:
                        await asyncio.sleep(self._failure_backoff_seconds)
                    continue
                if published == 0 and not run_state.stop_requested:
                    await asyncio.sleep(self._poll_interval_seconds)
        finally:
            if self._run_state is run_state:
                self._run_state = None
            run_state.stopped.set()

    async def start(self) -> None:
        """Start the relay in the background.

        Schedules :meth:`run` as a task so ``bootstrap()`` can manage the
        gateway's lifecycle alongside the broker. Calling ``start`` twice
        is a no-op. A ``start()`` while a ``stop()`` is still draining the
        previous run starts a fresh relay (ADR-074).
        """
        if self._task is not None:
            return
        run_state = _Run()
        self._run_state = run_state
        self._task = asyncio.create_task(self._run_loop(run_state))

    async def stop(self) -> None:
        """Stop the relay and drain the in-flight pass.

        Returns once :meth:`run` has exited, so a caller that stops the
        relay before the broker can be sure no publish is still in flight.
        A stop made right after :meth:`start`, before the background task
        has begun, also takes effect: the task exits without a pass.
        Returns immediately when the relay is neither started nor running.

        The run is detached before ``stop()`` waits for it, so a
        :meth:`start` made meanwhile starts a fresh relay (ADR-074).
        """
        task, run_state = self._task, self._run_state
        self._task = None
        self._run_state = None
        if run_state is None:
            return
        run_state.stop_requested = True
        if task is None:
            await run_state.stopped.wait()
        elif not task.done():
            await task

    async def _deliver(self, entry: OutboxEntry) -> str | None:
        """Attempt to deliver one entry.

        Returns ``None`` on success, or a value-free description of the
        failure (ADR-071).

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
        except Exception as exc:
            # Not only ValidationError: whatever the payload makes validation
            # raise goes through the retry budget (ADR-074, B-70).
            description = describe(exc)
            logger.error(
                "Payload validation failed for %s on topic '%s' — requeued: %s",
                entry.message_id,
                entry.topic,
                description,
            )
            return f"Payload validation failed: {description}"

        headers: dict[str, str] | None = None
        if entry.correlation_id is not None and entry.causation_id is not None:
            event = event.stamp(
                correlation_id=entry.correlation_id,
                causation_id=entry.causation_id,
            )
            headers = {
                "correlation_id": entry.correlation_id,
                "causation_id": entry.causation_id,
            }

        try:
            if headers is None:
                await self._broker.publish(entry.topic, event)
            else:
                await self._broker.publish(entry.topic, event, headers=headers)
        except Exception as exc:
            description = describe(exc)
            logger.error(
                "Publish failed for %s on topic '%s' — requeued: %s",
                entry.message_id,
                entry.topic,
                description,
            )
            return f"Broker publish failed: {description}"

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


def _check_arguments(
    *,
    batch_size: int,
    max_attempts: int | None,
    base_delay: timedelta,
    max_delay: timedelta,
    poll_interval_seconds: float,
    failure_backoff_seconds: float,
) -> None:
    """Raise ``ValueError`` for a relay argument the relay cannot honour."""
    if type(batch_size) is not int or batch_size < 1:
        msg = f"batch_size must be an int of at least 1, got {batch_size!r}"
        raise ValueError(msg)
    if max_attempts is not None and (type(max_attempts) is not int or max_attempts < 1):
        msg = f"max_attempts must be an int of at least 1 or None, got {max_attempts!r}"
        raise ValueError(msg)
    if base_delay < timedelta(0):
        msg = "base_delay must not be negative"
        raise ValueError(msg)
    if max_delay < base_delay:
        msg = "max_delay must not be shorter than base_delay"
        raise ValueError(msg)
    if not math.isfinite(poll_interval_seconds) or poll_interval_seconds < 0:
        msg = "poll_interval_seconds must be finite and not negative"
        raise ValueError(msg)
    if not math.isfinite(failure_backoff_seconds) or failure_backoff_seconds < 0:
        msg = "failure_backoff_seconds must be finite and not negative"
        raise ValueError(msg)
