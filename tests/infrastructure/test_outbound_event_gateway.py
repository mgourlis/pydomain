"""Tests for OutboundEventGateway — the transactional outbox relay."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import anyio
import pytest

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_broker import MessageBroker, OutboundEventGateway
from pydomain.testing import FakeOutboxStore, InMemoryMessageBroker

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
        assert "broker unavailable" in store.failures[0][1]

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
        gateway = _gateway(FakeOutboxStore(), InMemoryMessageBroker())

        await gateway.stop()

    @pytest.mark.anyio
    async def test_run_publishes_then_exits_on_stop(self) -> None:
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

        assert "broker unavailable" in store.dead_lettered[entry.message_id]

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
