"""Tests for OutboundEventGateway — the transactional outbox relay."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import anyio
import pytest

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry, OutboxWriter
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_broker import MessageBroker, OutboundEventGateway
from pydomain.testing import FakeOutboxStore, InMemoryMessageBroker
from tests.support.fault_injection import FaultInjector

# ── Sample events ───────────────────────────────────────────────────────


class OrderPlaced(DomainEvent):
    order_id: UUID
    total: int


class OrderPlacedIntegration(IntegrationEvent):
    order_id: str
    total: int


def _to_integration(event: DomainEvent) -> OrderPlacedIntegration:
    """Translate an OrderPlaced domain event into its integration event."""
    placed = cast(OrderPlaced, event)
    return OrderPlacedIntegration(
        order_id=str(placed.order_id),
        total=placed.total,
    )


def _registry() -> OutboundEventRegistry:
    """Registry with one registered translation."""
    registry = OutboundEventRegistry()
    registry.register_translation(
        OrderPlaced,
        OrderPlacedIntegration,
        "orders.placed",
        _to_integration,
    )
    return registry


def _payload(event_id: str, /) -> dict[str, Any]:
    """A payload that validates against OrderPlacedIntegration."""
    event = OrderPlacedIntegration(event_id=event_id, order_id="abc", total=1)
    return event.model_dump()


class _MutableClock:
    """Controllable time source, so retry timing is deterministic."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        """Move the clock forward."""
        self.now += delta


def _entry(
    topic: str = "orders.placed",
    payload: dict[str, Any] | None = None,
    message_id: str | None = None,
) -> OutboxEntry:
    """Build a stored outbox entry.

    The default payload mirrors a real row: ``message_id`` is the
    integration event's own ``event_id``.
    """
    mid = message_id or str(uuid4())
    return OutboxEntry(
        message_id=mid,
        topic=topic,
        payload=payload if payload is not None else _payload(mid),
        occurred_at=datetime.now(UTC),
    )


class _FailingBroker:
    """Broker double whose ``publish`` always raises."""

    def __init__(self) -> None:
        self.attempts = 0

    async def publish(self, topic: str, event: IntegrationEvent, **kwargs: Any) -> None:
        self.attempts += 1
        msg = "broker unavailable"
        raise RuntimeError(msg)

    async def start(self) -> None:
        """No-op."""

    async def stop(self) -> None:
        """No-op."""


def _gateway(
    store: FakeOutboxStore,
    broker: MessageBroker,
    *,
    clock: Callable[[], datetime] | None = None,
) -> OutboundEventGateway:
    return OutboundEventGateway(store, broker, _registry(), batch_size=100, clock=clock)


# ── Happy path ──────────────────────────────────────────────────────────


class TestOutboundEventGatewayPublishing:
    """Published entries reach the broker and are marked published."""

    @pytest.mark.anyio
    async def test_run_once_publishes_a_stored_entry(self) -> None:
        store = FakeOutboxStore()
        broker = InMemoryMessageBroker()
        entry = _entry()
        await store.append([entry])

        await _gateway(store, broker).run_once()

        assert [topic for topic, _event, _headers in broker.published] == [
            "orders.placed"
        ]

    @pytest.mark.anyio
    async def test_published_payload_is_rehydrated_into_the_registered_class(
        self,
    ) -> None:
        store = FakeOutboxStore()
        broker = InMemoryMessageBroker()
        await store.append([_entry()])

        await _gateway(store, broker).run_once()

        _topic, event, _headers = broker.published[0]
        assert isinstance(event, OrderPlacedIntegration)

    @pytest.mark.anyio
    async def test_run_once_returns_the_number_published(self) -> None:
        store = FakeOutboxStore()
        await store.append([_entry(), _entry()])

        published = await _gateway(store, InMemoryMessageBroker()).run_once()

        assert published == 2

    @pytest.mark.anyio
    async def test_successful_entry_is_marked_published(self) -> None:
        store = FakeOutboxStore()
        entry = _entry()
        await store.append([entry])

        await _gateway(store, InMemoryMessageBroker()).run_once()

        assert store.is_published(entry.message_id)

    @pytest.mark.anyio
    async def test_published_entry_is_not_fetched_again(self) -> None:
        store = FakeOutboxStore()
        await store.append([_entry()])
        gateway = _gateway(store, InMemoryMessageBroker())

        await gateway.run_once()

        assert await store.fetch_unpublished() == []

    @pytest.mark.anyio
    async def test_empty_outbox_publishes_nothing(self) -> None:
        assert (
            await _gateway(FakeOutboxStore(), InMemoryMessageBroker()).run_once() == 0
        )


# ── Failure handling ────────────────────────────────────────────────────


class TestOutboundEventGatewayFailureHandling:
    """An entry is marked published only when the broker accepted it."""

    @pytest.mark.anyio
    async def test_broker_failure_leaves_the_entry_unpublished(self) -> None:
        store = FakeOutboxStore()
        entry = _entry()
        await store.append([entry])

        await _gateway(store, _FailingBroker()).run_once()

        assert not store.is_published(entry.message_id)

    @pytest.mark.anyio
    async def test_broker_failure_returns_zero(self) -> None:
        store = FakeOutboxStore()
        await store.append([_entry()])

        assert await _gateway(store, _FailingBroker()).run_once() == 0

    @pytest.mark.anyio
    async def test_failed_entry_is_not_immediately_refetchable(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        entry = _entry()
        await store.append([entry])

        await _gateway(store, _FailingBroker(), clock=clock).run_once()

        assert await store.fetch_unpublished() == []

    @pytest.mark.anyio
    async def test_failed_entry_is_refetchable_after_the_backoff_elapses(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        entry = _entry()
        await store.append([entry])
        await _gateway(store, _FailingBroker(), clock=clock).run_once()

        clock.advance(timedelta(seconds=6))
        remaining = await store.fetch_unpublished()

        assert [e.message_id for e in remaining] == [entry.message_id]

    @pytest.mark.anyio
    async def test_failure_records_an_error_and_increments_attempts(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        entry = _entry()
        await store.append([entry])

        await _gateway(store, _FailingBroker(), clock=clock).run_once()

        assert store.attempts[entry.message_id] == 1
        assert store.failures[0][1] == "Broker publish failed: RuntimeError"

    @pytest.mark.anyio
    async def test_backoff_grows_with_repeated_failures(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        await store.append([_entry()])
        gateway = _gateway(store, _FailingBroker(), clock=clock)

        await gateway.run_once()
        first_delay = store.failures[0][2] - clock.now

        clock.advance(first_delay + timedelta(seconds=1))
        await gateway.run_once()
        second_delay = store.failures[1][2] - clock.now

        assert second_delay > first_delay

    @pytest.mark.anyio
    async def test_backoff_is_capped_at_max_delay(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        entry = _entry()
        await store.append([entry])
        store.attempts[entry.message_id] = 50
        gateway = OutboundEventGateway(
            store,
            _FailingBroker(),
            _registry(),
            base_delay=timedelta(seconds=5),
            max_delay=timedelta(seconds=60),
            max_attempts=None,
            clock=clock,
        )

        await gateway.run_once()

        assert store.failures[-1][2] - clock.now == timedelta(seconds=60)

    @pytest.mark.anyio
    async def test_one_failure_does_not_block_the_rest_of_the_batch(self) -> None:
        store = FakeOutboxStore()
        good = _entry()
        bad = _entry(topic="unregistered.topic")
        await store.append([bad, good])

        await _gateway(store, InMemoryMessageBroker()).run_once()

        assert store.is_published(good.message_id)

    @pytest.mark.anyio
    async def test_unknown_topic_is_never_marked_published(self) -> None:
        store = FakeOutboxStore()
        entry = _entry(topic="unregistered.topic")
        await store.append([entry])

        await _gateway(store, InMemoryMessageBroker()).run_once()

        assert not store.is_published(entry.message_id)

    @pytest.mark.anyio
    async def test_unknown_topic_is_not_published_to_the_broker(self) -> None:
        store = FakeOutboxStore()
        broker = InMemoryMessageBroker()
        await store.append([_entry(topic="unregistered.topic")])

        await _gateway(store, broker).run_once()

        assert broker.published == []

    @pytest.mark.anyio
    async def test_invalid_payload_is_never_marked_published(self) -> None:
        store = FakeOutboxStore()
        entry = _entry(payload={"unexpected": "shape"})
        await store.append([entry])

        await _gateway(store, InMemoryMessageBroker()).run_once()

        assert not store.is_published(entry.message_id)

    @pytest.mark.anyio
    async def test_invalid_payload_is_requeued_with_a_failure_record(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        entry = _entry(payload={"unexpected": "shape"})
        await store.append([entry])

        await _gateway(store, InMemoryMessageBroker(), clock=clock).run_once()

        assert not store.is_published(entry.message_id)
        assert store.attempts[entry.message_id] == 1

    @pytest.mark.anyio
    async def test_unknown_topic_is_requeued_with_a_failure_record(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        entry = _entry(topic="unregistered.topic")
        await store.append([entry])

        await _gateway(store, InMemoryMessageBroker(), clock=clock).run_once()

        assert not store.is_published(entry.message_id)
        assert "No translation registered" in store.failures[0][1]


# ── Lifecycle ───────────────────────────────────────────────────────


class _BlockingBroker:
    """Broker double whose ``publish`` blocks until released."""

    def __init__(self) -> None:
        self.entered = anyio.Event()
        self.release = anyio.Event()
        self.published: list[str] = []

    async def publish(self, topic: str, event: IntegrationEvent, **kwargs: Any) -> None:
        self.entered.set()
        await self.release.wait()
        self.published.append(topic)

    async def start(self) -> None:
        """No-op."""

    async def stop(self) -> None:
        """No-op."""


class TestOutboundEventGatewayLifecycle:
    """The relay runs as a background pump and drains before it stops."""

    @pytest.mark.anyio
    async def test_stop_returns_immediately_when_not_running(self) -> None:
        with anyio.fail_after(10):
            gateway = _gateway(FakeOutboxStore(), InMemoryMessageBroker())

            await gateway.stop()

    @pytest.mark.anyio
    async def test_run_publishes_then_exits_on_stop(self) -> None:
        with anyio.fail_after(10):
            store = FakeOutboxStore()
            broker = InMemoryMessageBroker()
            await store.append([_entry()])
            gateway = OutboundEventGateway(
                store, broker, _registry(), poll_interval_seconds=0.01
            )

            with anyio.move_on_after(2.0):
                async with anyio.create_task_group() as tg:
                    tg.start_soon(gateway.run)
                    while not broker.published:
                        await anyio.sleep(0.005)
                    await gateway.stop()

            assert len(broker.published) == 1

    @pytest.mark.anyio
    async def test_stop_waits_for_the_in_flight_publish(self) -> None:
        with anyio.fail_after(10):
            store = FakeOutboxStore()
            broker = _BlockingBroker()
            await store.append([_entry()])
            gateway = OutboundEventGateway(
                store, broker, _registry(), poll_interval_seconds=0.01
            )

            with anyio.move_on_after(2.0):
                async with anyio.create_task_group() as tg:
                    tg.start_soon(gateway.run)
                    await broker.entered.wait()
                    tg.start_soon(gateway.stop)
                    await anyio.sleep(0.05)
                    # stop() is still waiting: the publish has not completed.
                    assert broker.published == []
                    broker.release.set()

            assert broker.published == ["orders.placed"]

    @pytest.mark.anyio
    async def test_start_and_stop_drive_the_background_task(self) -> None:
        with anyio.fail_after(10):
            store = FakeOutboxStore()
            broker = InMemoryMessageBroker()
            await store.append([_entry()])
            gateway = OutboundEventGateway(
                store, broker, _registry(), poll_interval_seconds=0.01
            )

            await gateway.start()
            with anyio.move_on_after(2.0):
                while not broker.published:
                    await anyio.sleep(0.005)
            await gateway.stop()

            assert len(broker.published) == 1

    @pytest.mark.anyio
    async def test_a_failing_pass_does_not_kill_the_loop(self) -> None:
        with anyio.fail_after(10):
            store = FakeOutboxStore()
            store.fail_next_fetch = True
            broker = InMemoryMessageBroker()
            gateway = OutboundEventGateway(
                store,
                broker,
                _registry(),
                poll_interval_seconds=0.01,
                failure_backoff_seconds=0.01,
            )

            await gateway.start()
            await anyio.sleep(0.05)
            await store.append([_entry()])
            with anyio.move_on_after(2.0):
                while not broker.published:
                    await anyio.sleep(0.005)
            await gateway.stop()

            assert len(broker.published) == 1


# ── Dead-letter queue ──────────────────────────────────────────────────


class _FlakyBroker:
    """Broker double that fails a fixed number of times, then succeeds."""

    def __init__(self, failures: int) -> None:
        self._remaining = failures
        self.published: list[str] = []

    async def publish(self, topic: str, event: IntegrationEvent, **kwargs: Any) -> None:
        if self._remaining > 0:
            self._remaining -= 1
            msg = "transient broker error"
            raise RuntimeError(msg)
        self.published.append(topic)

    async def start(self) -> None:
        """No-op."""

    async def stop(self) -> None:
        """No-op."""


class _SelectiveBroker:
    """Broker double that fails only for one specific event id."""

    def __init__(self, fail_for: str) -> None:
        self._fail_for = fail_for
        self.published: list[str] = []

    async def publish(self, topic: str, event: IntegrationEvent, **kwargs: Any) -> None:
        if event.event_id == self._fail_for:
            msg = "selective broker error"
            raise RuntimeError(msg)
        self.published.append(topic)

    async def start(self) -> None:
        """No-op."""

    async def stop(self) -> None:
        """No-op."""


def _budget_gateway(
    store: FakeOutboxStore,
    broker: MessageBroker,
    clock: _MutableClock,
    *,
    max_attempts: int | None = 2,
) -> OutboundEventGateway:
    """Gateway with a small, deterministic retry budget."""
    return OutboundEventGateway(
        store,
        broker,
        _registry(),
        base_delay=timedelta(seconds=1),
        max_delay=timedelta(seconds=10),
        max_attempts=max_attempts,
        clock=clock,
    )


class TestOutboundEventGatewayDeadLetter:
    """Entries that exhaust their retry budget are dead-lettered."""

    @pytest.mark.anyio
    async def test_entry_survives_its_first_failure(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        entry = _entry()
        await store.append([entry])

        await _budget_gateway(store, _FailingBroker(), clock).run_once()

        assert not store.is_dead_lettered(entry.message_id)
        assert store.attempts[entry.message_id] == 1

    @pytest.mark.anyio
    async def test_entry_is_dead_lettered_when_the_budget_is_exhausted(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        entry = _entry()
        await store.append([entry])
        gateway = _budget_gateway(store, _FailingBroker(), clock)

        await gateway.run_once()
        clock.advance(timedelta(seconds=2))
        await gateway.run_once()

        assert store.is_dead_lettered(entry.message_id)

    @pytest.mark.anyio
    async def test_dead_lettered_entry_is_never_fetched_again(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        entry = _entry()
        await store.append([entry])
        gateway = _budget_gateway(store, _FailingBroker(), clock)

        await gateway.run_once()
        clock.advance(timedelta(seconds=2))
        await gateway.run_once()
        clock.advance(timedelta(days=1))

        assert await store.fetch_unpublished() == []

    @pytest.mark.anyio
    async def test_dead_lettered_entry_is_never_reported_as_published(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        entry = _entry()
        await store.append([entry])
        gateway = _budget_gateway(store, _FailingBroker(), clock)

        await gateway.run_once()
        clock.advance(timedelta(seconds=2))
        await gateway.run_once()

        assert not store.is_published(entry.message_id)

    @pytest.mark.anyio
    async def test_dead_letter_records_the_last_error(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        entry = _entry()
        await store.append([entry])
        gateway = _budget_gateway(store, _FailingBroker(), clock)

        await gateway.run_once()
        clock.advance(timedelta(seconds=2))
        await gateway.run_once()

        assert store.dead_lettered[entry.message_id] == (
            "Broker publish failed: RuntimeError"
        )

    @pytest.mark.anyio
    async def test_dead_letter_counts_the_attempt_that_exhausted_the_budget(
        self,
    ) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        entry = _entry()
        await store.append([entry])
        gateway = _budget_gateway(store, _FailingBroker(), clock)

        await gateway.run_once()
        clock.advance(timedelta(seconds=2))
        await gateway.run_once()

        assert store.attempts[entry.message_id] == 2

    @pytest.mark.anyio
    async def test_entry_recovering_within_budget_is_published(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        entry = _entry()
        await store.append([entry])
        broker = _FlakyBroker(failures=1)
        gateway = _budget_gateway(store, broker, clock)

        await gateway.run_once()
        clock.advance(timedelta(seconds=2))
        await gateway.run_once()

        assert store.is_published(entry.message_id)
        assert not store.is_dead_lettered(entry.message_id)
        assert broker.published == ["orders.placed"]

    @pytest.mark.anyio
    async def test_none_max_attempts_retries_indefinitely(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        entry = _entry()
        await store.append([entry])
        gateway = _budget_gateway(store, _FailingBroker(), clock, max_attempts=None)

        for _ in range(5):
            await gateway.run_once()
            clock.advance(timedelta(hours=1))

        assert not store.is_dead_lettered(entry.message_id)
        assert store.attempts[entry.message_id] == 5

    @pytest.mark.anyio
    async def test_unknown_topic_also_exhausts_its_budget(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        entry = _entry(topic="unregistered.topic")
        await store.append([entry])
        gateway = _budget_gateway(store, InMemoryMessageBroker(), clock)

        await gateway.run_once()
        clock.advance(timedelta(seconds=2))
        await gateway.run_once()

        assert store.is_dead_lettered(entry.message_id)

    @pytest.mark.anyio
    async def test_one_dead_letter_does_not_block_the_rest_of_the_batch(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        doomed = _entry(message_id="doomed")
        healthy = _entry(message_id="healthy")
        await store.append([doomed, healthy])
        broker = _SelectiveBroker(fail_for="doomed")
        gateway = _budget_gateway(store, broker, clock)

        await gateway.run_once()
        clock.advance(timedelta(seconds=2))
        await gateway.run_once()

        assert store.is_dead_lettered("doomed")
        assert store.is_published("healthy")


# ── Failure descriptions (ADR-071) ──────────────────────────────────────

_INVALID_PAYLOAD = (
    "ValidationError: 2 errors for OrderPlacedIntegration "
    "at order_id (missing), total (int_parsing)"
)


class _FetchFailsOnceStore(FakeOutboxStore):
    """Outbox whose first fetch raises; the second signals and returns nothing."""

    def __init__(self) -> None:
        super().__init__()
        self.fetches = 0
        self.fetched_again = anyio.Event()

    async def fetch_unpublished(self, limit: int) -> list[OutboxEntry]:
        self.fetches += 1
        if self.fetches == 1:
            msg = "outbox table unavailable"
            raise ConnectionError(msg)
        self.fetched_again.set()
        return []


def _relay_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "pydomain.message_broker"]


class TestOutboundEventGatewayFailureDescriptions:
    """Stored errors and log lines name types and locations, never values."""

    @pytest.mark.anyio
    async def test_invalid_payload_stores_model_locations_and_error_codes(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.ERROR, logger="pydomain.message_broker")
        store = FakeOutboxStore()
        await store.append([_entry(message_id="m1", payload={"total": "many"})])

        await _gateway(store, InMemoryMessageBroker()).run_once()

        assert store.failures[0][1] == f"Payload validation failed: {_INVALID_PAYLOAD}"
        assert [r.getMessage() for r in _relay_records(caplog)] == [
            "Payload validation failed for m1 on topic 'orders.placed' — "
            f"requeued: {_INVALID_PAYLOAD}"
        ]

    @pytest.mark.anyio
    async def test_dead_letter_log_names_entry_topic_attempts_and_description(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        await store.append([_entry(message_id="m1", payload={"total": "many"})])
        gateway = _budget_gateway(store, InMemoryMessageBroker(), clock)
        await gateway.run_once()
        clock.advance(timedelta(seconds=2))
        caplog.set_level(logging.ERROR, logger="pydomain.message_broker")

        await gateway.run_once()

        error = f"Payload validation failed: {_INVALID_PAYLOAD}"
        assert store.dead_lettered["m1"] == error
        assert (
            "Outbox entry m1 on topic 'orders.placed' exhausted 2 attempts "
            f"— dead-lettered: {error}"
        ) in [r.getMessage() for r in _relay_records(caplog)]

    @pytest.mark.anyio
    async def test_broker_failure_stores_and_logs_its_type_without_traceback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.ERROR, logger="pydomain.message_broker")
        store = FakeOutboxStore()
        await store.append([_entry(message_id="m1")])

        await _gateway(store, _FailingBroker()).run_once()

        assert store.failures[0][1] == "Broker publish failed: RuntimeError"
        [record] = _relay_records(caplog)
        assert record.getMessage() == (
            "Publish failed for m1 on topic 'orders.placed' — requeued: RuntimeError"
        )
        assert record.exc_info is None

    @pytest.mark.anyio
    async def test_failed_pass_logs_its_type_without_traceback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.ERROR, logger="pydomain.message_broker")
        store = _FetchFailsOnceStore()
        gateway = OutboundEventGateway(
            store,
            InMemoryMessageBroker(),
            _registry(),
            poll_interval_seconds=0.01,
            failure_backoff_seconds=0,
        )

        async with anyio.create_task_group() as tg:
            tg.start_soon(gateway.run)
            with anyio.fail_after(5):  # a relay that stops after a failure hangs here
                await store.fetched_again.wait()
            await gateway.stop()

        [record] = _relay_records(caplog)
        assert record.getMessage() == (
            "Outbox relay pass failed: ConnectionError; backing off"
        )
        assert record.exc_info is None

    @pytest.mark.anyio
    async def test_unknown_topic_logs_topic_and_entry(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.ERROR, logger="pydomain.message_broker")
        store = FakeOutboxStore()
        await store.append([_entry(topic="unregistered.topic", message_id="m1")])

        await _gateway(store, InMemoryMessageBroker()).run_once()

        assert store.failures[0][1] == (
            "No translation registered for topic 'unregistered.topic'"
        )
        assert [r.getMessage() for r in _relay_records(caplog)] == [
            "No translation registered for topic 'unregistered.topic' — m1 requeued"
        ]


class _CountingBroker(InMemoryMessageBroker):
    """Broker that signals once it has accepted ``expected`` events."""

    def __init__(self, expected: int) -> None:
        super().__init__()
        self._expected = expected
        self.done = anyio.Event()

    async def publish(self, topic: str, event: IntegrationEvent, **kwargs: Any) -> None:
        await super().publish(topic, event, **kwargs)
        if len(self.published) >= self._expected:
            self.done.set()


class _TimedFailOnceStore(_FetchFailsOnceStore):
    """Records when each fetch happened."""

    def __init__(self) -> None:
        super().__init__()
        self.fetched_at: list[float] = []

    async def fetch_unpublished(self, limit: int) -> list[OutboxEntry]:
        self.fetched_at.append(anyio.current_time())
        return await super().fetch_unpublished(limit)


class TestOutboundEventGatewayPolling:
    """run(): back off after a failed pass, never idle after a productive one."""

    @pytest.mark.anyio
    async def test_failed_pass_backs_off_before_the_next_pass(self) -> None:
        with anyio.fail_after(10):
            store = _TimedFailOnceStore()
            gateway = OutboundEventGateway(
                store,
                InMemoryMessageBroker(),
                _registry(),
                poll_interval_seconds=0.01,
                failure_backoff_seconds=0.05,
            )

            async with anyio.create_task_group() as tg:
                tg.start_soon(gateway.run)
                with anyio.fail_after(5):
                    await store.fetched_again.wait()
                await gateway.stop()

            first, second = store.fetched_at[:2]
            assert second - first >= 0.04

    @pytest.mark.anyio
    async def test_pass_that_published_is_followed_without_the_idle_sleep(
        self,
    ) -> None:
        with anyio.fail_after(10):
            store = FakeOutboxStore()
            await store.append([_entry(message_id="m1"), _entry(message_id="m2")])
            broker = _CountingBroker(expected=2)
            gateway = OutboundEventGateway(
                store, broker, _registry(), batch_size=1, poll_interval_seconds=60
            )

            async with anyio.create_task_group() as tg:
                tg.start_soon(gateway.run)
                with anyio.fail_after(5):
                    await broker.done.wait()
                tg.cancel_scope.cancel()

            assert [message_id for message_id in store.published] == ["m1", "m2"]


# ── DCE-122: per-entry marking, tracing at delivery, stop before run ──

_INF13 = "DCE-122 INF-13/CQRS-14: delivery ignores the entry's tracing ids"


class _RecordingBroker:
    """Broker double that records accepted ids and fails or cancels on demand."""

    def __init__(
        self,
        *,
        fail_once_for: str | None = None,
        cancel_once_for: str | None = None,
    ) -> None:
        self._fail_once_for = fail_once_for
        self._cancel_once_for = cancel_once_for
        self.accepted: list[str] = []

    async def publish(self, topic: str, event: IntegrationEvent, **kwargs: Any) -> None:
        if event.event_id == self._fail_once_for:
            self._fail_once_for = None
            msg = "selective broker error"
            raise RuntimeError(msg)
        if event.event_id == self._cancel_once_for:
            self._cancel_once_for = None
            raise asyncio.CancelledError
        self.accepted.append(event.event_id)

    async def start(self) -> None:
        """No-op."""

    async def stop(self) -> None:
        """No-op."""


class TestOutboundEventGatewayPerEntryMarking:
    """Each entry is marked when its own delivery finishes (ADR-074, INF-09)."""

    @pytest.mark.anyio
    async def test_failed_mark_does_not_republish_batch(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        await store.append([_entry(message_id=f"m{n}") for n in (1, 2, 3)])
        faulty = FaultInjector(
            store, at=1, raises=ConnectionError("outbox down"), methods={"mark_failed"}
        )
        broker = _RecordingBroker(fail_once_for="m2")
        gateway = OutboundEventGateway(
            faulty.proxy, broker, _registry(), clock=clock, max_attempts=None
        )

        with contextlib.suppress(ConnectionError):
            await gateway.run_once()
        clock.advance(timedelta(days=1))
        await gateway.run_once()

        assert faulty.fired
        assert sorted(broker.accepted) == ["m1", "m2", "m3"]

    @pytest.mark.anyio
    async def test_cancel_mid_batch_does_not_republish_delivered_entries(
        self,
    ) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        await store.append([_entry(message_id=f"m{n}") for n in (1, 2, 3)])
        broker = _RecordingBroker(cancel_once_for="m2")
        gateway = _gateway(store, broker, clock=clock)

        with pytest.raises(asyncio.CancelledError):
            await gateway.run_once()
        # The crashed pass's claims on m2 and m3 go stale (B-57).
        clock.advance(timedelta(minutes=5))
        await gateway.run_once()

        assert sorted(broker.accepted) == ["m1", "m2", "m3"]


class TestOutboundEventGatewayTracing:
    """Delivery applies the entry's tracing; the cause is the domain event."""

    @pytest.mark.anyio
    async def test_delivered_event_carries_entry_tracing(self) -> None:
        correlation_id, command_id = uuid4(), uuid4()
        domain_event = OrderPlaced(order_id=uuid4(), total=5).stamp(
            correlation_id=correlation_id, causation_id=command_id
        )
        store = FakeOutboxStore()
        await OutboxWriter(store, _registry()).write([domain_event])
        broker = InMemoryMessageBroker()

        await _gateway(store, broker).run_once()

        (_topic, published, _headers) = broker.published[0]
        assert published.correlation_id == str(correlation_id)
        assert published.causation_id == str(domain_event.event_id)

    @pytest.mark.anyio
    async def test_entry_tracing_wins_over_the_translators(self) -> None:
        mid = str(uuid4())
        translated = OrderPlacedIntegrationEvent_with_ids(mid)
        store = FakeOutboxStore()
        await store.append(
            [
                OutboxEntry(
                    message_id=mid,
                    topic="orders.placed",
                    payload=translated.model_dump(),
                    occurred_at=datetime.now(UTC),
                    correlation_id="entry-correlation",
                    causation_id="entry-causation",
                )
            ]
        )
        broker = InMemoryMessageBroker()

        await _gateway(store, broker).run_once()

        (_topic, published, _headers) = broker.published[0]
        assert published.correlation_id == "entry-correlation"
        assert published.causation_id == "entry-causation"

    @pytest.mark.anyio
    async def test_entry_without_correlation_is_not_stamped(self) -> None:
        mid = str(uuid4())
        translated = OrderPlacedIntegrationEvent_with_ids(mid)
        store = FakeOutboxStore()
        await store.append(
            [
                OutboxEntry(
                    message_id=mid,
                    topic="orders.placed",
                    payload=translated.model_dump(),
                    occurred_at=datetime.now(UTC),
                    causation_id="entry-causation",
                )
            ]
        )
        broker = InMemoryMessageBroker()

        await _gateway(store, broker).run_once()

        (_topic, published, _headers) = broker.published[0]
        assert published.correlation_id == "translator-correlation"
        assert published.causation_id == "translator-causation"


class TestOutboundEventGatewayTracingHeaders:
    """The applied ids also travel as message headers (ADR-074, B-63)."""

    @pytest.mark.anyio
    async def test_stamped_event_is_published_with_tracing_headers(self) -> None:
        correlation_id = uuid4()
        domain_event = OrderPlaced(order_id=uuid4(), total=5).stamp(
            correlation_id=correlation_id, causation_id=uuid4()
        )
        store = FakeOutboxStore()
        await OutboxWriter(store, _registry()).write([domain_event])
        broker = InMemoryMessageBroker()

        await _gateway(store, broker).run_once()

        (topic, _published, headers) = broker.published[0]
        assert topic == "orders.placed"
        assert headers == {
            "correlation_id": str(correlation_id),
            "causation_id": str(domain_event.event_id),
        }

    @pytest.mark.anyio
    async def test_unstamped_event_is_published_without_headers(self) -> None:
        calls: list[dict[str, Any]] = []

        class _KwargsBroker(InMemoryMessageBroker):
            async def publish(
                self, topic: str, event: IntegrationEvent, **kwargs: Any
            ) -> None:
                calls.append(kwargs)

        store = FakeOutboxStore()
        await store.append([_entry()])

        await _gateway(store, _KwargsBroker()).run_once()

        assert calls == [{}]


def OrderPlacedIntegrationEvent_with_ids(event_id: str) -> OrderPlacedIntegration:  # noqa: N802
    """An integration event the translator already stamped."""
    return OrderPlacedIntegration(event_id=event_id, order_id="abc", total=1).stamp(
        correlation_id="translator-correlation",
        causation_id="translator-causation",
    )


_B59 = "DCE-122 INF-12/INF-18 (B-59): the relay accepts invalid arguments"


class TestOutboundEventGatewayArguments:
    """The relay refuses arguments it cannot honour (ADR-074, B-59)."""

    @pytest.mark.parametrize(
        ("kwargs", "name"),
        [
            ({"batch_size": 0}, "batch_size"),
            ({"batch_size": -1}, "batch_size"),
            ({"max_attempts": 0}, "max_attempts"),
            ({"max_attempts": -3}, "max_attempts"),
            ({"base_delay": timedelta(seconds=-1)}, "base_delay"),
            (
                {"base_delay": timedelta(0), "max_delay": timedelta(seconds=-1)},
                "max_delay",
            ),
            ({"poll_interval_seconds": -0.1}, "poll_interval_seconds"),
            ({"failure_backoff_seconds": -0.1}, "failure_backoff_seconds"),
            (
                {
                    "base_delay": timedelta(seconds=10),
                    "max_delay": timedelta(seconds=5),
                },
                "max_delay",
            ),
        ],
    )
    def test_rejects_an_invalid_argument(
        self, kwargs: dict[str, Any], name: str
    ) -> None:
        with pytest.raises(ValueError, match=rf"^{name} "):
            OutboundEventGateway(
                FakeOutboxStore(), InMemoryMessageBroker(), _registry(), **kwargs
            )

    def test_accepts_the_boundary_values(self) -> None:
        OutboundEventGateway(
            FakeOutboxStore(),
            InMemoryMessageBroker(),
            _registry(),
            batch_size=1,
            max_attempts=1,
            base_delay=timedelta(0),
            max_delay=timedelta(0),
            poll_interval_seconds=0,
            failure_backoff_seconds=0,
        )

    def test_accepts_unbounded_attempts(self) -> None:
        OutboundEventGateway(
            FakeOutboxStore(), InMemoryMessageBroker(), _registry(), max_attempts=None
        )


class TestOutboundEventGatewayStoreFailure:
    """A store error ends the pass (B-64)."""

    @pytest.mark.anyio
    async def test_store_error_ends_the_pass(self) -> None:
        store = FakeOutboxStore()
        await store.append([_entry(message_id=f"m{n}") for n in (1, 2)])
        faulty = FaultInjector(
            store, at=1, raises=ConnectionError("outbox down"), methods={"mark_failed"}
        )
        broker = _RecordingBroker(fail_once_for="m1")
        gateway = OutboundEventGateway(faulty.proxy, broker, _registry())

        with pytest.raises(ConnectionError):
            await gateway.run_once()

        assert broker.accepted == []


class TestOutboundEventGatewayStopBeforeRun:
    """stop() takes effect even before the background task has run (INF-11)."""

    @pytest.mark.anyio
    async def test_stop_right_after_start_stops_the_relay(self) -> None:
        with anyio.fail_after(10):
            store = FakeOutboxStore()
            broker = InMemoryMessageBroker()
            gateway = OutboundEventGateway(
                store, broker, _registry(), poll_interval_seconds=0.01
            )

            await gateway.start()
            await gateway.stop()
            try:
                await store.append([_entry()])
                await anyio.sleep(0.05)

                assert broker.published == []
            finally:
                await gateway.stop()

    @pytest.mark.anyio
    async def test_stop_after_the_task_ended_returns_without_raising(self) -> None:
        with anyio.fail_after(10):
            store = FakeOutboxStore()
            await store.append([_entry(message_id="m1")])
            broker = _RecordingBroker(cancel_once_for="m1")
            gateway = OutboundEventGateway(
                store, broker, _registry(), poll_interval_seconds=0.01
            )

            await gateway.start()
            await anyio.sleep(0.05)

            await gateway.stop()


# ── DCE-122 hardening: mutants in the relay ───────────────────────────


class TestOutboundEventGatewayDefaults:
    """The documented defaults hold (ADR-063, ADR-065)."""

    @pytest.mark.anyio
    async def test_default_batch_size_is_100(self) -> None:
        store = FakeOutboxStore()
        await store.append([_entry() for _ in range(101)])
        broker = InMemoryMessageBroker()

        published = await OutboundEventGateway(store, broker, _registry()).run_once()

        assert published == 100

    @pytest.mark.anyio
    async def test_default_budget_dead_letters_on_the_tenth_failure(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        entry = _entry()
        await store.append([entry])
        gateway = OutboundEventGateway(
            store, _FailingBroker(), _registry(), clock=clock
        )

        for _ in range(9):
            await gateway.run_once()
            clock.advance(timedelta(hours=1))
        assert not store.is_dead_lettered(entry.message_id)

        await gateway.run_once()
        assert store.is_dead_lettered(entry.message_id)

    @pytest.mark.anyio
    async def test_default_clock_is_utc(self) -> None:
        store = FakeOutboxStore()
        await store.append([_entry()])

        await OutboundEventGateway(store, _FailingBroker(), _registry()).run_once()

        (_message_id, _error, next_attempt_at) = store.failures[0]
        assert next_attempt_at.tzinfo is UTC

    @pytest.mark.anyio
    async def test_backoff_doubles_from_base_delay(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        await store.append([_entry()])
        gateway = OutboundEventGateway(
            store,
            _FailingBroker(),
            _registry(),
            base_delay=timedelta(seconds=1),
            max_delay=timedelta(minutes=1),
            max_attempts=None,
            clock=clock,
        )

        delays = []
        for _ in range(3):
            await gateway.run_once()
            delays.append(store.failures[-1][2] - clock.now)
            clock.advance(timedelta(minutes=5))

        assert delays == [timedelta(seconds=n) for n in (1, 2, 4)]

    @pytest.mark.anyio
    async def test_dead_letter_first_in_batch_does_not_stop_the_pass(self) -> None:
        store = FakeOutboxStore()
        doomed, healthy = _entry(message_id="m1"), _entry(message_id="m2")
        await store.append([doomed, healthy])
        broker = _RecordingBroker(fail_once_for="m1")
        gateway = OutboundEventGateway(store, broker, _registry(), max_attempts=1)

        await gateway.run_once()

        assert store.is_dead_lettered("m1")
        assert broker.accepted == ["m2"]

    @pytest.mark.anyio
    async def test_start_after_a_dead_task_was_stopped_restarts_the_relay(
        self,
    ) -> None:
        with anyio.fail_after(10):
            store = FakeOutboxStore()
            await store.append([_entry(message_id="m1")])
            broker = _RecordingBroker(cancel_once_for="m1")
            gateway = OutboundEventGateway(
                store, broker, _registry(), poll_interval_seconds=0.01
            )
            await gateway.start()
            await anyio.sleep(0.05)
            await gateway.stop()

            await store.append([_entry(message_id="m2")])
            await gateway.start()
            with anyio.move_on_after(2.0):
                while "m2" not in broker.accepted:
                    await anyio.sleep(0.005)
            await gateway.stop()

            assert "m2" in broker.accepted


# ── DCE-122 review round (PR #113) ────────────────────────────────────

_B70R = "DCE-122 B-70: a payload past the recursion limit poisons the pass"
_B71R = "DCE-122 B-71: a late failure dead-letters an entry another relay published"
_B72R = "DCE-122 B-72: start() during an in-flight stop() is a no-op"
_ARGS = "DCE-122 B-59 (review ⑨-6): bool, float and non-finite arguments are accepted"


class _AnyPayload(IntegrationEvent):
    data: Any = None


def _any_registry() -> OutboundEventRegistry:
    registry = OutboundEventRegistry()
    registry.register_translation(
        OrderPlaced, _AnyPayload, "orders.any", lambda event: _AnyPayload()
    )
    return registry


class TestOutboundEventGatewayDeepPayload:
    @pytest.mark.anyio
    async def test_payload_too_deep_to_validate_is_retried_then_dead_lettered(
        self,
    ) -> None:
        deep: Any = "leaf"
        for _ in range(5000):
            deep = [deep]
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock)
        poison = _AnyPayload().model_dump() | {"data": deep}
        healthy = _AnyPayload()
        await store.append(
            [
                OutboxEntry(
                    message_id=poison["event_id"],
                    topic="orders.any",
                    payload=poison,
                    occurred_at=clock.now,
                ),
                OutboxEntry(
                    message_id=healthy.event_id,
                    topic="orders.any",
                    payload=healthy.model_dump(),
                    occurred_at=clock.now,
                ),
            ]
        )
        broker = _RecordingBroker()
        gateway = OutboundEventGateway(
            store, broker, _any_registry(), clock=clock, max_attempts=2
        )

        for _ in range(2):
            await gateway.run_once()
            clock.advance(timedelta(hours=1))

        assert store.is_dead_lettered(poison["event_id"])
        assert broker.accepted == [healthy.event_id]
        assert store.failures[0][1].startswith("Payload validation failed: ")


class _GatedBroker:
    """Broker whose first publish waits for a release, then fails or succeeds."""

    def __init__(self, *, fail: bool) -> None:
        self.entered = anyio.Event()
        self.release = anyio.Event()
        self._fail = fail

    async def publish(self, topic: str, event: IntegrationEvent, **kwargs: Any) -> None:
        self.entered.set()
        await self.release.wait()
        if self._fail:
            msg = "late synthetic failure"
            raise RuntimeError(msg)

    async def start(self) -> None:
        """No-op."""

    async def stop(self) -> None:
        """No-op."""


class TestOutboundEventGatewayStaleClaim:
    """A publish that outlasts its claim (B-71)."""

    @pytest.mark.anyio
    async def test_late_failure_does_not_unpublish_an_entry_another_relay_sent(
        self,
    ) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock, claim_timeout=timedelta(seconds=1))
        entry = _entry()
        await store.append([entry])
        slow = _GatedBroker(fail=True)
        fast = InMemoryMessageBroker()
        relay_a = OutboundEventGateway(
            store, slow, _registry(), clock=clock, max_attempts=1
        )
        relay_b = OutboundEventGateway(store, fast, _registry(), clock=clock)

        with anyio.fail_after(5):
            async with anyio.create_task_group() as tg:
                tg.start_soon(relay_a.run_once)
                await slow.entered.wait()
                clock.advance(timedelta(seconds=2))
                assert await relay_b.run_once() == 1
                slow.release.set()

        assert store.is_published(entry.message_id)
        assert not store.is_dead_lettered(entry.message_id)

    @pytest.mark.anyio
    async def test_publish_outlasting_its_claim_is_delivered_twice(self) -> None:
        clock = _MutableClock()
        store = FakeOutboxStore(clock=clock, claim_timeout=timedelta(seconds=1))
        entry = _entry()
        await store.append([entry])
        slow = _GatedBroker(fail=False)
        fast = InMemoryMessageBroker()
        relay_a = OutboundEventGateway(store, slow, _registry(), clock=clock)
        relay_b = OutboundEventGateway(store, fast, _registry(), clock=clock)

        with anyio.fail_after(5):
            async with anyio.create_task_group() as tg:
                tg.start_soon(relay_a.run_once)
                await slow.entered.wait()
                clock.advance(timedelta(seconds=2))
                await relay_b.run_once()
                slow.release.set()

        assert len(fast.published) == 1
        assert store.is_published(entry.message_id)


class TestOutboundEventGatewayStartDuringStop:
    @pytest.mark.anyio
    async def test_start_during_an_in_flight_stop_starts_a_fresh_relay(self) -> None:
        store = FakeOutboxStore()
        broker = _BlockingBroker()
        await store.append([_entry(message_id="m1")])
        gateway = OutboundEventGateway(
            store, broker, _registry(), poll_interval_seconds=0.01
        )

        with anyio.fail_after(5):
            await gateway.start()
            await broker.entered.wait()
            async with anyio.create_task_group() as tg:
                tg.start_soon(gateway.stop)
                await anyio.sleep(0.01)
                await gateway.start()
                broker.release.set()
            await store.append([_entry(message_id="m2")])
            with anyio.move_on_after(1):
                while not store.is_published("m2"):
                    await anyio.sleep(0.005)
            await gateway.stop()

        assert store.is_published("m2")


class TestOutboundEventGatewayArgumentTypes:
    @pytest.mark.parametrize(
        ("kwargs", "name"),
        [
            ({"batch_size": True}, "batch_size"),
            ({"batch_size": 1.5}, "batch_size"),
            ({"max_attempts": True}, "max_attempts"),
            ({"max_attempts": 2.0}, "max_attempts"),
            ({"poll_interval_seconds": math.nan}, "poll_interval_seconds"),
            ({"poll_interval_seconds": math.inf}, "poll_interval_seconds"),
            ({"failure_backoff_seconds": math.nan}, "failure_backoff_seconds"),
            ({"failure_backoff_seconds": math.inf}, "failure_backoff_seconds"),
        ],
    )
    def test_rejects_a_non_int_count_or_non_finite_sleep(
        self, kwargs: dict[str, Any], name: str
    ) -> None:
        with pytest.raises(ValueError, match=rf"^{name} "):
            OutboundEventGateway(
                FakeOutboxStore(), InMemoryMessageBroker(), _registry(), **kwargs
            )


class TestOutboundEventGatewayCancelledBeforeItRan:
    @pytest.mark.anyio
    async def test_stop_after_the_task_was_cancelled_before_it_ran(self) -> None:
        gateway = OutboundEventGateway(
            FakeOutboxStore(), InMemoryMessageBroker(), _registry()
        )
        before = asyncio.all_tasks()

        with anyio.fail_after(5):
            await gateway.start()
            (relay_task,) = asyncio.all_tasks() - before
            relay_task.cancel()
            await asyncio.sleep(0)

            await gateway.stop()

        assert relay_task.cancelled()


class _SleepCalled(Exception):
    """Raised by the patched sleep to end run() at its first sleep."""


class TestOutboundEventGatewayDefaultSleeps:
    """The documented default sleeps hold: 1 s idle, 0.1 s after a failed pass."""

    @staticmethod
    def _record_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
        seen: list[float] = []

        async def fake_sleep(delay: float) -> None:
            seen.append(delay)
            raise _SleepCalled

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        return seen

    @pytest.mark.anyio
    async def test_default_poll_interval_is_one_second(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gateway = OutboundEventGateway(
            FakeOutboxStore(), InMemoryMessageBroker(), _registry()
        )
        seen = self._record_sleeps(monkeypatch)

        with pytest.raises(_SleepCalled):
            await gateway.run()

        assert seen == [1.0]

    @pytest.mark.anyio
    async def test_default_failure_backoff_is_a_tenth_of_a_second(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = FakeOutboxStore()
        store.fail_next_fetch = True
        gateway = OutboundEventGateway(store, InMemoryMessageBroker(), _registry())
        seen = self._record_sleeps(monkeypatch)

        with pytest.raises(_SleepCalled):
            await gateway.run()

        assert seen == [0.1]


class TestOutboundEventGatewayConcurrentStops:
    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-122 review 3 ⑨-A: a second stop() returns before the run exits",
    )
    @pytest.mark.anyio
    async def test_every_concurrent_stop_waits_for_the_in_flight_publish(
        self,
    ) -> None:
        store = FakeOutboxStore()
        broker = _BlockingBroker()
        await store.append([_entry()])
        gateway = OutboundEventGateway(
            store, broker, _registry(), poll_interval_seconds=0.01
        )
        returned: list[str] = []

        async def stop_and_record(name: str) -> None:
            await gateway.stop()
            returned.append(name)

        with anyio.fail_after(5):
            await gateway.start()
            await broker.entered.wait()
            async with anyio.create_task_group() as tg:
                tg.start_soon(stop_and_record, "first")
                await anyio.sleep(0.01)
                tg.start_soon(stop_and_record, "second")
                await anyio.sleep(0.05)
                in_flight_returns = list(returned)
                broker.release.set()

        assert in_flight_returns == []
        assert sorted(returned) == ["first", "second"]
