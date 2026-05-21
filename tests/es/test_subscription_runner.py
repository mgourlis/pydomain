"""Tests for the Subscription dataclass and SubscriptionRunner ABC.

Covers the Subscription model, SubscriptionRunner orchestration
including event filtering by type, checkpoint tracking across runs,
subscription isolation, the ``run()`` polling loop, ``stop()`` graceful
exit, and at-least-once checkpoint semantics.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Sequence
from typing import ClassVar

import pytest

from pydomain.cqrs.projection import Projection
from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.checkpoint_store import CheckpointStore
from pydomain.es.event_store import EventStore
from pydomain.es.projection import EventSourcedProjection
from pydomain.infrastructure.subscription import Subscription, SubscriptionRunner
from pydomain.testing.fake_checkpoint_store import FakeCheckpointStore
from pydomain.testing.fake_event_store import FakeEventStore

# ---------------------------------------------------------------------------
# Module-level DomainEvent subclasses for testing
# ---------------------------------------------------------------------------


class OrderPlaced(DomainEvent):
    order_id: str
    amount: int


class PaymentReceived(DomainEvent):
    order_id: str


class InventoryAdjusted(DomainEvent):
    sku: str
    delta: int


# ---------------------------------------------------------------------------
# Module-level test projection and runner
# ---------------------------------------------------------------------------


class CountingProjection(EventSourcedProjection):
    """A test projection that records which events were applied.

    Subclasses :class:`EventSourcedProjection` so that ``apply()`` delegates
    to ``handle()`` which dispatches to ``_when_*`` handlers by convention.
    The base class manages checkpoint tracking — no need to override
    ``apply``, ``rebuild``, or ``checkpoint``.
    """

    __test__ = False  # pytest: not a test class

    name: ClassVar[str] = "counting_projection"
    version: ClassVar[int] = 1

    def __init__(self) -> None:
        super().__init__()
        self.applied: list[DomainEvent] = []

    # -- _when_* handlers (called by base-class dispatch) --

    async def _when_OrderPlaced(self, event: OrderPlaced) -> None:
        self.applied.append(event)

    async def _when_PaymentReceived(self, event: PaymentReceived) -> None:
        self.applied.append(event)

    async def _when_InventoryAdjusted(self, event: InventoryAdjusted) -> None:
        self.applied.append(event)


class ProjectingSubscriptionRunner(SubscriptionRunner):
    """Concrete runner that applies matching events to a projection."""

    async def process_batch(
        self,
        events: Sequence[DomainEvent],
        subscription: Subscription,
    ) -> None:
        for event in events:
            await subscription.projection.apply(event)


class FailingBatchRunner(SubscriptionRunner):
    """Runner whose ``process_batch`` raises on the first call, then
    succeeds on subsequent calls.  Used to verify at-least-once
    checkpoint semantics."""

    def __init__(
        self,
        event_store: EventStore,
        checkpoint_store: CheckpointStore,
        *,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        super().__init__(
            event_store,
            checkpoint_store,
            poll_interval_seconds=poll_interval_seconds,
        )
        self.fail_count = 0
        self.call_count = 0

    async def process_batch(
        self,
        events: Sequence[DomainEvent],
        subscription: Subscription,
    ) -> None:
        self.call_count += 1
        if self.fail_count > 0:
            self.fail_count -= 1
            raise RuntimeError("Simulated processing failure")
        for event in events:
            await subscription.projection.apply(event)


# ===================================================================
# Subscription Model
# ===================================================================


class TestSubscriptionModel:
    def test_construction_and_attributes(self) -> None:
        proj = CountingProjection()
        sub = Subscription(
            subscription_id="test-sub",
            projection=proj,
            event_types=(OrderPlaced,),
        )
        assert sub.subscription_id == "test-sub"
        assert sub.projection is proj
        assert sub.event_types == (OrderPlaced,)

    def test_isinstance_dataclass(self) -> None:
        proj = CountingProjection()
        sub = Subscription(
            subscription_id="test-sub",
            projection=proj,
            event_types=(OrderPlaced,),
        )
        assert dataclasses.is_dataclass(sub)

    def test_event_types_tuple_of_types(self) -> None:
        sub = Subscription(
            subscription_id="filter-test",
            projection=CountingProjection(),
            event_types=(OrderPlaced, PaymentReceived),
        )
        assert len(sub.event_types) == 2
        assert OrderPlaced in sub.event_types
        assert PaymentReceived in sub.event_types
        assert InventoryAdjusted not in sub.event_types

    def test_multiple_event_types(self) -> None:
        sub = Subscription(
            subscription_id="multi",
            projection=CountingProjection(),
            event_types=(OrderPlaced, PaymentReceived),
        )
        assert sub.event_types == (OrderPlaced, PaymentReceived)


class TestCountingProjectionProtocol:
    def test_passes_cqrs_projection_protocol(self) -> None:
        """CountingProjection satisfies the CQRS Projection Protocol."""
        proj = CountingProjection()
        assert isinstance(proj, Projection)

    def test_passes_es_projection_isinstance(self) -> None:
        """CountingProjection is a true subclass of EventSourcedProjection."""
        proj = CountingProjection()
        assert isinstance(proj, EventSourcedProjection)


# ===================================================================
# SubscriptionRunner ABC
# ===================================================================


class TestSubscriptionRunnerABC:
    def test_cannot_instantiate_abstract(self) -> None:
        """SubscriptionRunner cannot be instantiated directly — it is ABC."""
        with pytest.raises(TypeError):
            SubscriptionRunner(  # type: ignore[abstract]
                event_store=FakeEventStore(),
                checkpoint_store=FakeCheckpointStore(),
            )

    def test_subclass_can_instantiate(self) -> None:
        """A concrete subclass that implements process_batch can be
        instantiated."""
        runner = ProjectingSubscriptionRunner(
            event_store=FakeEventStore(),
            checkpoint_store=FakeCheckpointStore(),
        )
        assert isinstance(runner, SubscriptionRunner)

    def test_negative_failure_backoff_raises_value_error(self) -> None:
        """Construction with negative failure_backoff_seconds raises
        ValueError."""
        with pytest.raises(ValueError):
            ProjectingSubscriptionRunner(
                event_store=FakeEventStore(),
                checkpoint_store=FakeCheckpointStore(),
                failure_backoff_seconds=-1,
            )

    @pytest.mark.anyio
    async def test_subclass_with_process_batch(self) -> None:
        """Events are passed to process_batch for the subclass to handle."""
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()
        projection = CountingProjection()

        await event_store.append_to_stream(
            "order-1",
            [OrderPlaced(order_id="ord-1", amount=100)],
            expected_version=0,
        )

        subscription = Subscription(
            subscription_id="orders",
            projection=projection,
            event_types=(OrderPlaced,),
        )

        runner = ProjectingSubscriptionRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
        )
        runner.add_subscription(subscription)
        await runner.run_once()

        assert len(projection.applied) == 1
        assert projection.applied[0].order_id == "ord-1"  # type: ignore[attr-defined]


# ===================================================================
# SubscriptionRunner -- run_once
# ===================================================================


class TestSubscriptionRunnerRunOnce:
    @pytest.mark.anyio
    async def test_runner_processes_matching_events(self) -> None:
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()
        projection = CountingProjection()

        await event_store.append_to_stream(
            "order-1",
            [OrderPlaced(order_id="ord-1", amount=100)],
            expected_version=0,
        )
        await event_store.append_to_stream(
            "order-2",
            [OrderPlaced(order_id="ord-2", amount=50)],
            expected_version=0,
        )

        subscription = Subscription(
            subscription_id="orders",
            projection=projection,
            event_types=(OrderPlaced,),
        )

        runner = ProjectingSubscriptionRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
        )
        runner.add_subscription(subscription)
        await runner.run_once()

        assert len(projection.applied) == 2
        assert projection.applied[0].order_id == "ord-1"  # type: ignore[attr-defined]
        assert projection.applied[1].order_id == "ord-2"  # type: ignore[attr-defined]
        assert projection.checkpoint == 2

    @pytest.mark.anyio
    async def test_runner_skips_non_matching_events(self) -> None:
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()
        projection = CountingProjection()

        await event_store.append_to_stream(
            "order-1",
            [OrderPlaced(order_id="ord-1", amount=100)],
            expected_version=0,
        )
        await event_store.append_to_stream(
            "payment-1",
            [PaymentReceived(order_id="ord-1")],
            expected_version=0,
        )

        subscription = Subscription(
            subscription_id="orders",
            projection=projection,
            event_types=(OrderPlaced,),
        )

        runner = ProjectingSubscriptionRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
        )
        runner.add_subscription(subscription)
        await runner.run_once()

        assert len(projection.applied) == 1
        assert isinstance(projection.applied[0], OrderPlaced)
        assert projection.applied[0].order_id == "ord-1"
        assert projection.checkpoint == 1

        saved = await checkpoint_store.load("orders")
        assert saved == 2

    @pytest.mark.anyio
    async def test_runner_updates_checkpoint_after_run(self) -> None:
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()
        projection = CountingProjection()

        await event_store.append_to_stream(
            "order-1",
            [OrderPlaced(order_id="ord-1", amount=100)],
            expected_version=0,
        )

        subscription = Subscription(
            subscription_id="order-summary",
            projection=projection,
            event_types=(OrderPlaced,),
        )

        runner = ProjectingSubscriptionRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
        )
        runner.add_subscription(subscription)
        await runner.run_once()

        saved = await checkpoint_store.load("order-summary")
        assert saved == 1

    @pytest.mark.anyio
    async def test_runner_multiple_subscriptions_independent(self) -> None:
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()
        proj_orders = CountingProjection()
        proj_payments = CountingProjection()

        await event_store.append_to_stream(
            "order-1",
            [OrderPlaced(order_id="ord-1", amount=100)],
            expected_version=0,
        )
        await event_store.append_to_stream(
            "payment-1",
            [PaymentReceived(order_id="ord-1")],
            expected_version=0,
        )
        await event_store.append_to_stream(
            "order-2",
            [OrderPlaced(order_id="ord-2", amount=50)],
            expected_version=0,
        )

        sub_orders = Subscription(
            subscription_id="sub-orders",
            projection=proj_orders,
            event_types=(OrderPlaced,),
        )
        sub_payments = Subscription(
            subscription_id="sub-payments",
            projection=proj_payments,
            event_types=(PaymentReceived,),
        )

        runner = ProjectingSubscriptionRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
        )
        runner.add_subscription(sub_orders)
        runner.add_subscription(sub_payments)
        await runner.run_once()

        assert len(proj_orders.applied) == 2
        assert isinstance(proj_orders.applied[0], OrderPlaced)
        assert isinstance(proj_orders.applied[1], OrderPlaced)
        assert proj_orders.checkpoint == 2

        assert len(proj_payments.applied) == 1
        assert isinstance(proj_payments.applied[0], PaymentReceived)
        assert proj_payments.checkpoint == 1

        assert await checkpoint_store.load("sub-orders") == 3
        assert await checkpoint_store.load("sub-payments") == 3

    @pytest.mark.anyio
    async def test_runner_empty_event_store_no_crash(self) -> None:
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()
        projection = CountingProjection()

        subscription = Subscription(
            subscription_id="empty",
            projection=projection,
            event_types=(OrderPlaced,),
        )

        runner = ProjectingSubscriptionRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
        )
        runner.add_subscription(subscription)
        await runner.run_once()

        assert len(projection.applied) == 0
        assert projection.checkpoint == 0
        assert await checkpoint_store.load("empty") == 0

    @pytest.mark.anyio
    async def test_runner_respects_checkpoint_only_new_events(self) -> None:
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()
        projection = CountingProjection()

        await event_store.append_to_stream(
            "order-1",
            [OrderPlaced(order_id="ord-1", amount=100)],
            expected_version=0,
        )

        subscription = Subscription(
            subscription_id="orders",
            projection=projection,
            event_types=(OrderPlaced,),
        )

        runner = ProjectingSubscriptionRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
        )
        runner.add_subscription(subscription)
        await runner.run_once()

        assert len(projection.applied) == 1
        assert projection.applied[0].order_id == "ord-1"  # type: ignore[attr-defined]

        await event_store.append_to_stream(
            "order-2",
            [OrderPlaced(order_id="ord-2", amount=50)],
            expected_version=0,
        )

        await runner.run_once()

        assert len(projection.applied) == 2
        assert projection.applied[1].order_id == "ord-2"  # type: ignore[attr-defined]

        saved = await checkpoint_store.load("orders")
        assert saved == 2

    @pytest.mark.anyio
    async def test_runner_handles_zero_matching_events(self) -> None:
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()
        projection = CountingProjection()

        await event_store.append_to_stream(
            "payment-1",
            [PaymentReceived(order_id="ord-1")],
            expected_version=0,
        )

        subscription = Subscription(
            subscription_id="no-match",
            projection=projection,
            event_types=(OrderPlaced,),
        )

        runner = ProjectingSubscriptionRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
        )
        runner.add_subscription(subscription)
        await runner.run_once()

        assert len(projection.applied) == 0
        assert projection.checkpoint == 0

        saved = await checkpoint_store.load("no-match")
        assert saved == 1

    @pytest.mark.anyio
    async def test_runner_with_multiple_event_types(self) -> None:
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()
        projection = CountingProjection()

        await event_store.append_to_stream(
            "order-1",
            [OrderPlaced(order_id="ord-1", amount=100)],
            expected_version=0,
        )
        await event_store.append_to_stream(
            "payment-1",
            [PaymentReceived(order_id="ord-1")],
            expected_version=0,
        )
        await event_store.append_to_stream(
            "inv-1",
            [InventoryAdjusted(sku="ABC", delta=-1)],
            expected_version=0,
        )

        subscription = Subscription(
            subscription_id="multi-type",
            projection=projection,
            event_types=(OrderPlaced, PaymentReceived),
        )

        runner = ProjectingSubscriptionRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
        )
        runner.add_subscription(subscription)
        await runner.run_once()

        assert len(projection.applied) == 2
        assert isinstance(projection.applied[0], OrderPlaced)
        assert isinstance(projection.applied[1], PaymentReceived)
        assert projection.checkpoint == 2
        assert await checkpoint_store.load("multi-type") == 3

    @pytest.mark.anyio
    async def test_runner_no_subscriptions_no_crash(self) -> None:
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()

        runner = ProjectingSubscriptionRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
        )
        await runner.run_once()

    @pytest.mark.anyio
    async def test_runner_multiple_runs_no_new_events(self) -> None:
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()
        projection = CountingProjection()

        await event_store.append_to_stream(
            "order-1",
            [OrderPlaced(order_id="ord-1", amount=100)],
            expected_version=0,
        )

        subscription = Subscription(
            subscription_id="idempotent",
            projection=projection,
            event_types=(OrderPlaced,),
        )

        runner = ProjectingSubscriptionRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
        )
        runner.add_subscription(subscription)

        await runner.run_once()
        assert len(projection.applied) == 1

        await runner.run_once()
        assert len(projection.applied) == 1
        assert projection.checkpoint == 1
        assert await checkpoint_store.load("idempotent") == 1


# ===================================================================
# SubscriptionRunner -- At-Least-Once (process_batch raises)
# ===================================================================


class TestSubscriptionRunnerAtLeastOnce:
    @pytest.mark.anyio
    async def test_checkpoint_not_updated_when_process_batch_raises(
        self,
    ) -> None:
        """When process_batch raises, the checkpoint is NOT advanced
        (at-least-once guarantee)."""
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()
        projection = CountingProjection()

        await event_store.append_to_stream(
            "order-1",
            [OrderPlaced(order_id="ord-1", amount=100)],
            expected_version=0,
        )

        subscription = Subscription(
            subscription_id="failing",
            projection=projection,
            event_types=(OrderPlaced,),
        )

        runner = FailingBatchRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
        )
        runner.fail_count = 1
        runner.add_subscription(subscription)

        # run_once dispatches via the optimised _process_cycle;
        # process_batch failures are caught and the checkpoint is
        # NOT advanced (at-least-once guarantee).
        await runner.run_once()

        # Checkpoint NOT advanced (at-least-once guarantee)
        assert await checkpoint_store.load("failing") == 0

        # No events applied to projection
        assert len(projection.applied) == 0

    @pytest.mark.anyio
    async def test_retry_after_failure_receives_same_events(self) -> None:
        """After a failure, the next run re-reads the same events because
        the checkpoint was not advanced."""
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()
        projection = CountingProjection()

        await event_store.append_to_stream(
            "order-1",
            [OrderPlaced(order_id="ord-1", amount=100)],
            expected_version=0,
        )

        subscription = Subscription(
            subscription_id="retry",
            projection=projection,
            event_types=(OrderPlaced,),
        )

        runner = FailingBatchRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
        )
        runner.fail_count = 1
        runner.add_subscription(subscription)

        # First run_once fails internally, checkpoint not advanced
        await runner.run_once()
        assert await checkpoint_store.load("retry") == 0

        # Second run_once succeeds (fail_count exhausted)
        await runner.run_once()

        assert projection.checkpoint == 1
        assert await checkpoint_store.load("retry") == 1

    def test_custom_failure_backoff_is_used(self) -> None:
        """A custom failure_backoff_seconds is accepted and stored on the
        runner instance."""
        runner = ProjectingSubscriptionRunner(
            event_store=FakeEventStore(),
            checkpoint_store=FakeCheckpointStore(),
            failure_backoff_seconds=5.0,
        )
        assert runner._failure_backoff_seconds == 5.0

    def test_custom_failure_backoff_negative_raises(self) -> None:
        """Construction with a negative failure_backoff_seconds raises
        ValueError."""
        with pytest.raises(ValueError):
            ProjectingSubscriptionRunner(
                event_store=FakeEventStore(),
                checkpoint_store=FakeCheckpointStore(),
                failure_backoff_seconds=-1,
            )


# ===================================================================
# SubscriptionRunner -- run() Polling Loop
# ===================================================================


class TestSubscriptionRunnerRun:
    @pytest.mark.anyio
    async def test_run_loop_processes_events_and_exits_on_stop(
        self,
    ) -> None:
        """The ``run()`` method processes events and exits when
        ``stop()`` is called."""
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()
        projection = CountingProjection()

        await event_store.append_to_stream(
            "order-1",
            [OrderPlaced(order_id="ord-1", amount=100)],
            expected_version=0,
        )

        subscription = Subscription(
            subscription_id="orders",
            projection=projection,
            event_types=(OrderPlaced,),
        )

        runner = ProjectingSubscriptionRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
            poll_interval_seconds=0.01,
        )
        runner.add_subscription(subscription)

        # Run in background, stop after a short delay
        task = asyncio.ensure_future(runner.run())

        # Wait for events to be processed, then stop
        await asyncio.sleep(0.05)
        runner.stop()
        await task

        assert len(projection.applied) == 1
        assert await checkpoint_store.load("orders") == 1

    @pytest.mark.anyio
    async def test_run_polls_when_no_events(self) -> None:
        """When no events exist, ``run()`` sleeps and does not busy-loop."""
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()
        projection = CountingProjection()

        subscription = Subscription(
            subscription_id="empty",
            projection=projection,
            event_types=(OrderPlaced,),
        )

        runner = ProjectingSubscriptionRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
            poll_interval_seconds=0.01,
        )
        runner.add_subscription(subscription)

        task = asyncio.ensure_future(runner.run())
        await asyncio.sleep(0.05)
        runner.stop()
        await task

        assert len(projection.applied) == 0
        assert await checkpoint_store.load("empty") == 0

    @pytest.mark.anyio
    async def test_run_exits_immediately_when_stopped(self) -> None:
        """When ``stop()`` is called during the polling loop, ``run()``
        exits gracefully without hanging."""
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()
        projection = CountingProjection()

        await event_store.append_to_stream(
            "order-1",
            [OrderPlaced(order_id="ord-1", amount=100)],
            expected_version=0,
        )

        subscription = Subscription(
            subscription_id="orders",
            projection=projection,
            event_types=(OrderPlaced,),
        )

        runner = ProjectingSubscriptionRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
            poll_interval_seconds=0.01,
        )
        runner.add_subscription(subscription)

        task = asyncio.ensure_future(runner.run())
        # Yield control so run() can start executing before we stop
        await asyncio.sleep(0)
        runner.stop()
        await task

        # run() returned — the loop exited gracefully

    @pytest.mark.anyio
    async def test_run_at_least_once_on_failure(self) -> None:
        """When process_batch raises during the polling loop, the
        checkpoint is not advanced and the same events are retried on
        the next iteration."""
        event_store = FakeEventStore()
        checkpoint_store = FakeCheckpointStore()

        await event_store.append_to_stream(
            "order-1",
            [OrderPlaced(order_id="ord-1", amount=100)],
            expected_version=0,
        )

        projection = CountingProjection()
        subscription = Subscription(
            subscription_id="failing",
            projection=projection,
            event_types=(OrderPlaced,),
        )

        runner = FailingBatchRunner(
            event_store=event_store,
            checkpoint_store=checkpoint_store,
            poll_interval_seconds=0.01,
        )
        runner.fail_count = 1  # First batch raises, second succeeds
        runner.add_subscription(subscription)

        task = asyncio.ensure_future(runner.run())

        # Wait for at least two iterations (0.1s failure backoff + retry)
        await asyncio.sleep(0.3)
        runner.stop()
        await task

        # Despite the failure, the event was eventually processed
        assert projection.checkpoint == 1
        assert await checkpoint_store.load("failing") == 1


# ===================================================================
# Coverage gap: negative poll_interval_seconds
# ===================================================================


class TestNegativePollInterval:
    """Construction with negative poll_interval_seconds raises ValueError."""

    def test_negative_poll_interval_raises(self) -> None:
        with pytest.raises(ValueError, match="poll_interval_seconds"):
            ProjectingSubscriptionRunner(
                event_store=FakeEventStore(),
                checkpoint_store=FakeCheckpointStore(),
                poll_interval_seconds=-0.5,
            )


# ===================================================================
# Coverage gap: stop mid-cycle
# ===================================================================


class TestStopMidCycle:
    """stop() during multi-subscription cycle skips remaining subscriptions."""

    @pytest.mark.anyio
    async def test_stop_mid_cycle_skips_remaining_subscriptions(self) -> None:
        """When stop() is called inside process_batch, remaining subs
        in the same cycle are skipped."""
        event_store = FakeEventStore()
        _checkpoint_store = FakeCheckpointStore()  # noqa: F841
        proj1 = CountingProjection()
        proj2 = CountingProjection()

        await event_store.append_to_stream(
            "order-1",
            [OrderPlaced(order_id="ord-1", amount=100)],
            expected_version=0,
        )

        sub1 = Subscription(
            subscription_id="sub-1",
            projection=proj1,
            event_types=(OrderPlaced,),
        )
        sub2 = Subscription(
            subscription_id="sub-2",
            projection=proj2,
            event_types=(OrderPlaced,),
        )

        class StoppingRunner(SubscriptionRunner):
            """Stops the runner during the first subscription's batch."""

            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)  # type: ignore[arg-type]
                self._seen_subs: list[str] = []

            async def process_batch(
                self,
                events: Sequence[DomainEvent],
                subscription: Subscription,
            ) -> None:
                self._seen_subs.append(subscription.subscription_id)
                if subscription.subscription_id == "sub-1":
                    self.stop()
                for event in events:
                    await subscription.projection.apply(event)

        runner = StoppingRunner(
            event_store=event_store,
            checkpoint_store=FakeCheckpointStore(),
        )
        runner.add_subscription(sub1)
        runner.add_subscription(sub2)
        await runner.run_once()

        # Only sub-1 was processed; sub-2 was skipped because stop was requested
        assert "sub-1" in runner._seen_subs
        assert "sub-2" not in runner._seen_subs
