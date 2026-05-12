from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from pydomain.cqrs.unit_of_work import AbstractUnitOfWork
from pydomain.ddd import DomainEvent

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class FakeAggregate:
    """Minimal aggregate stand-in with pull_events for event collection tests."""

    def __init__(self, events: list[DomainEvent] | None = None) -> None:
        self._pending: list[DomainEvent] = list(events or [])

    def pull_events(self) -> list[DomainEvent]:
        events, self._pending = self._pending, []
        return events


class ItemAddedToCart(DomainEvent):
    item_id: str
    quantity: int


# ---------------------------------------------------------------------------
# Context manager lifecycle
# ---------------------------------------------------------------------------


class TestContextManagerLifecycle:
    @pytest.mark.anyio
    async def test_aenter_returns_self(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow as ctx:
            assert ctx is uow

    @pytest.mark.anyio
    async def test_aenter_resets_committed(self) -> None:
        uow = AbstractUnitOfWork()
        uow._committed = True  # simulate dirty state
        async with uow:
            assert uow._committed is False

    @pytest.mark.anyio
    async def test_aenter_clears_events(self) -> None:
        uow = AbstractUnitOfWork()
        uow._events.append(ItemAddedToCart(item_id="abc", quantity=1))
        async with uow:
            assert uow._events == []

    @pytest.mark.anyio
    async def test_aexit_without_exception_does_not_rollback(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            uow._committed = True
        # After exit without exception, committed should remain True
        assert uow._committed is True

    @pytest.mark.anyio
    async def test_aenter_and_aexit_paired_correctly(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            pass
        # Exited cleanly — no exception means paired successfully


# ---------------------------------------------------------------------------
# Commit flag
# ---------------------------------------------------------------------------


class TestCommitFlag:
    @pytest.mark.anyio
    async def test_committed_is_false_before_commit(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            assert uow._committed is False

    @pytest.mark.anyio
    async def test_commit_sets_committed_flag(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            await uow.commit()
            assert uow._committed is True


# ---------------------------------------------------------------------------
# Rollback on exception
# ---------------------------------------------------------------------------


class TestRollbackOnException:
    @pytest.mark.anyio
    async def test_exception_triggers_rollback(self) -> None:
        uow = AbstractUnitOfWork()
        with pytest.raises(RuntimeError):
            async with uow:
                msg = "something went wrong"
                raise RuntimeError(msg)
        assert uow._committed is False

    @pytest.mark.anyio
    async def test_rollback_clears_events(self) -> None:
        uow = AbstractUnitOfWork()
        with pytest.raises(RuntimeError):
            async with uow:
                uow._events.append(ItemAddedToCart(item_id="abc", quantity=1))
                msg = "something went wrong"
                raise RuntimeError(msg)
        assert uow._events == []

    @pytest.mark.anyio
    async def test_committed_uow_does_not_rollback_on_exception(self) -> None:
        """When a UoW has already been committed, __aexit__ should NOT
        rollback even if an exception occurs."""
        uow = AbstractUnitOfWork()
        with pytest.raises(RuntimeError):
            async with uow:
                await uow.commit()
                msg = "something went wrong"
                raise RuntimeError(msg)
        assert uow._committed is True

    @pytest.mark.anyio
    async def test_explicit_rollback_clears_events(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            uow._events.append(ItemAddedToCart(item_id="abc", quantity=1))
            await uow.rollback()
            assert uow._events == []
            assert uow._committed is False

    @pytest.mark.anyio
    async def test_explicit_rollback_sets_committed_false(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            uow._committed = True
            await uow.rollback()
            assert uow._committed is False


# ---------------------------------------------------------------------------
# Event collection + stamping
# ---------------------------------------------------------------------------


class TestEventCollectionAndStamping:
    @pytest.mark.anyio
    async def test_collect_events_from_seen_aggregates(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            event = ItemAddedToCart(item_id="abc", quantity=2)
            agg = FakeAggregate(events=[event])
            uow._seen.add(agg)
            await uow.commit()
            collected = uow.collect_events()
            assert len(collected) == 1
            stamped = collected[0]
            assert isinstance(stamped, ItemAddedToCart)
            assert stamped.item_id == "abc"

    @pytest.mark.anyio
    async def test_events_stamped_with_correlation_id(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            cid = uuid4()
            caid = uuid4()
            uow._correlation_id = cid
            uow._causation_id = caid
            event = ItemAddedToCart(item_id="abc", quantity=2)
            agg = FakeAggregate(events=[event])
            uow._seen.add(agg)
            await uow.commit()
            collected = uow.collect_events()
            assert collected[0].correlation_id == cid

    @pytest.mark.anyio
    async def test_events_stamped_with_causation_id(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            cid = uuid4()
            caid = uuid4()
            uow._correlation_id = cid
            uow._causation_id = caid
            event = ItemAddedToCart(item_id="abc", quantity=2)
            agg = FakeAggregate(events=[event])
            uow._seen.add(agg)
            await uow.commit()
            collected = uow.collect_events()
            assert collected[0].causation_id == caid

    @pytest.mark.anyio
    async def test_stamp_allows_none_ids(self) -> None:
        """When no correlation/causation IDs are set, stamping stores None."""
        uow = AbstractUnitOfWork()
        async with uow:
            event = ItemAddedToCart(item_id="abc", quantity=2)
            agg = FakeAggregate(events=[event])
            uow._seen.add(agg)
            await uow.commit()
            collected = uow.collect_events()
            assert collected[0].correlation_id is None
            assert collected[0].causation_id is None

    @pytest.mark.anyio
    async def test_original_events_not_mutated(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            cid = uuid4()
            caid = uuid4()
            uow._correlation_id = cid
            uow._causation_id = caid
            event = ItemAddedToCart(item_id="abc", quantity=2)
            agg = FakeAggregate(events=[event])
            uow._seen.add(agg)
            await uow.commit()
            # Original event must still have None tracing IDs
            assert event.correlation_id is None
            assert event.causation_id is None

    @pytest.mark.anyio
    async def test_multiple_aggregates_collected(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            event1 = ItemAddedToCart(item_id="a", quantity=1)
            event2 = ItemAddedToCart(item_id="b", quantity=2)
            agg1 = FakeAggregate(events=[event1])
            agg2 = FakeAggregate(events=[event2])
            uow._seen.add(agg1)
            uow._seen.add(agg2)
            await uow.commit()
            collected = uow.collect_events()
            assert len(collected) == 2

    @pytest.mark.anyio
    async def test_aggregate_with_multiple_events(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            event1 = ItemAddedToCart(item_id="a", quantity=1)
            event2 = ItemAddedToCart(item_id="b", quantity=2)
            agg = FakeAggregate(events=[event1, event2])
            uow._seen.add(agg)
            await uow.commit()
            collected = uow.collect_events()
            assert len(collected) == 2

    @pytest.mark.anyio
    async def test_events_pulled_from_aggregate_are_cleared(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            event = ItemAddedToCart(item_id="abc", quantity=2)
            agg = FakeAggregate(events=[event])
            uow._seen.add(agg)
            await uow.commit()
            # After commit, aggregate should have no more events
            assert agg.pull_events() == []

    @pytest.mark.anyio
    async def test_empty_seen_produces_no_events(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            await uow.commit()
            assert uow.collect_events() == []

    @pytest.mark.anyio
    async def test_stamped_events_preserve_domain_fields(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            cid = uuid4()
            caid = uuid4()
            uow._correlation_id = cid
            uow._causation_id = caid
            event = ItemAddedToCart(item_id="abc", quantity=2)
            agg = FakeAggregate(events=[event])
            uow._seen.add(agg)
            await uow.commit()
            stamped = uow.collect_events()[0]
            assert isinstance(stamped, ItemAddedToCart)
            assert stamped.item_id == "abc"
            assert stamped.quantity == 2


# ---------------------------------------------------------------------------
# Hook overrides
# ---------------------------------------------------------------------------


class TestHookOverrides:
    @pytest.mark.anyio
    async def test_hooks_called_in_correct_order(self) -> None:
        """Verify that on_before_commit, _flush, and on_after_commit are
        invoked in sequence during commit."""

        class RecordingUoW(AbstractUnitOfWork):
            def __init__(self) -> None:
                super().__init__()
                self.call_chain: list[str] = []

            async def on_before_commit(self) -> None:
                self.call_chain.append("before")

            async def _flush(self) -> None:
                self.call_chain.append("flush")

            async def on_after_commit(self) -> None:
                self.call_chain.append("after")

        uow = RecordingUoW()
        async with uow:
            await uow.commit()
            assert uow.call_chain == ["before", "flush", "after"]

    @pytest.mark.anyio
    async def test_on_before_commit_called(self) -> None:
        class BeforeUoW(AbstractUnitOfWork):
            def __init__(self) -> None:
                super().__init__()
                self.called = False

            async def on_before_commit(self) -> None:
                self.called = True

        uow = BeforeUoW()
        async with uow:
            await uow.commit()
            assert uow.called is True

    @pytest.mark.anyio
    async def test_flush_called(self) -> None:
        class FlushUoW(AbstractUnitOfWork):
            def __init__(self) -> None:
                super().__init__()
                self.called = False

            async def _flush(self) -> None:
                self.called = True

        uow = FlushUoW()
        async with uow:
            await uow.commit()
            assert uow.called is True

    @pytest.mark.anyio
    async def test_on_after_commit_called(self) -> None:
        class AfterUoW(AbstractUnitOfWork):
            def __init__(self) -> None:
                super().__init__()
                self.called = False

            async def on_after_commit(self) -> None:
                self.called = True

        uow = AfterUoW()
        async with uow:
            await uow.commit()
            assert uow.called is True

    @pytest.mark.anyio
    async def test_check_idempotency_hook_exists(self) -> None:
        """_check_idempotency is an overridable extension point."""

        class IdempotentUoW(AbstractUnitOfWork):
            def __init__(self) -> None:
                super().__init__()
                self.called_with: UUID | None = None

            async def _check_idempotency(self, command_id: UUID) -> None:
                self.called_with = command_id

        uow = IdempotentUoW()
        cmd_id = uuid4()
        async with uow:
            await uow._check_idempotency(cmd_id)
            assert uow.called_with == cmd_id

    @pytest.mark.anyio
    async def test_write_outbox_hook_exists(self) -> None:
        """_write_outbox is an overridable extension point."""

        class OutboxUoW(AbstractUnitOfWork):
            def __init__(self) -> None:
                super().__init__()
                self.called_with: list[DomainEvent] | None = None

            async def _write_outbox(self, events: list[DomainEvent]) -> None:
                self.called_with = events

        uow = OutboxUoW()
        events: list[DomainEvent] = [ItemAddedToCart(item_id="abc", quantity=1)]
        async with uow:
            await uow._write_outbox(events)
            assert uow.called_with == events

    @pytest.mark.anyio
    async def test_flush_called_before_stamping(self) -> None:
        """_flush runs before _collect_and_stamp, so events should still be
        in the aggregate when flush runs."""

        class FlushBeforeStampUoW(AbstractUnitOfWork):
            def __init__(self) -> None:
                super().__init__()
                self.events_before_stamp: list[DomainEvent] | None = None

            async def _flush(self) -> None:
                # At flush time, events have NOT been collected yet
                self.events_before_stamp = list(self._events)

        uow = FlushBeforeStampUoW()
        async with uow:
            event = ItemAddedToCart(item_id="abc", quantity=1)
            agg = FakeAggregate(events=[event])
            uow._seen.add(agg)
            await uow.commit()
            assert uow.events_before_stamp == []

    @pytest.mark.anyio
    async def test_after_commit_called_after_stamping(self) -> None:
        """on_after_commit runs after _collect_and_stamp, so events should
        be available."""

        class AfterCommitUoW(AbstractUnitOfWork):
            def __init__(self) -> None:
                super().__init__()
                self.seen_events: list[DomainEvent] | None = None

            async def on_after_commit(self) -> None:
                self.seen_events = list(self._events)

        uow = AfterCommitUoW()
        async with uow:
            event = ItemAddedToCart(item_id="abc", quantity=1)
            agg = FakeAggregate(events=[event])
            uow._seen.add(agg)
            await uow.commit()
            assert uow.seen_events is not None
            assert len(uow.seen_events) == 1
            assert uow.seen_events is not None
            stamped = uow.seen_events[0]
            assert isinstance(stamped, ItemAddedToCart)
            assert stamped.item_id == "abc"


# ---------------------------------------------------------------------------
# collect_events before commit
# ---------------------------------------------------------------------------


class TestCollectEventsBeforeCommit:
    @pytest.mark.anyio
    async def test_collect_events_empty_before_commit(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            assert uow.collect_events() == []

    @pytest.mark.anyio
    async def test_collect_events_empty_with_seen_but_no_commit(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            event = ItemAddedToCart(item_id="abc", quantity=1)
            agg = FakeAggregate(events=[event])
            uow._seen.add(agg)
            # No commit happened yet
            assert uow.collect_events() == []

    @pytest.mark.anyio
    async def test_collect_events_still_returns_after_commit(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            event = ItemAddedToCart(item_id="abc", quantity=1)
            agg = FakeAggregate(events=[event])
            uow._seen.add(agg)
            await uow.commit()
            assert len(uow.collect_events()) == 1
            # Second call should still return the same events
            assert len(uow.collect_events()) == 1


# ---------------------------------------------------------------------------
# __aexit__ does not suppress exceptions
# ---------------------------------------------------------------------------


class TestAexitDoesNotSuppress:
    @pytest.mark.anyio
    async def test_exception_propagates(self) -> None:
        uow = AbstractUnitOfWork()
        with pytest.raises(RuntimeError, match="boom"):
            async with uow:
                raise RuntimeError("boom")

    @pytest.mark.anyio
    async def test_exception_propagates_after_rollback(self) -> None:
        uow = AbstractUnitOfWork()
        with pytest.raises(ValueError, match="invalid"):
            async with uow:
                raise ValueError("invalid")

    @pytest.mark.anyio
    async def test_multiple_exception_types_propagate(self) -> None:
        uow = AbstractUnitOfWork()
        with pytest.raises(TypeError):
            async with uow:
                msg: Any = 42
                _ = msg + "not allowed"
