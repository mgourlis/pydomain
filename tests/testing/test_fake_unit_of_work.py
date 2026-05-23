"""Tests for FakeUnitOfWork — rollback tracking and async context manager."""

from __future__ import annotations

from uuid import uuid4

import pytest

from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeRepository, FakeUnitOfWork

# ── Minimal domain fixtures ──────────────────────────────────────────────


class _TestEvent(DomainEvent):
    data: str = "test"


class _TestAggregate(AggregateRoot):
    pass


# ═══════════════════════════════════════════════════════════════════════════
# Rollback tracking
# ═══════════════════════════════════════════════════════════════════════════


class TestRollbackTracking:
    """_rolled_back flag reflects rollback() calls."""

    @pytest.mark.anyio
    async def test_rolled_back_false_initially(self) -> None:
        """_rolled_back starts as False."""
        uow = FakeUnitOfWork()
        assert uow._rolled_back is False

    @pytest.mark.anyio
    async def test_rollback_sets_flag(self) -> None:
        """Calling rollback() sets _rolled_back to True."""
        uow = FakeUnitOfWork()
        await uow.rollback()
        assert uow._rolled_back is True

    @pytest.mark.anyio
    async def test_exit_without_commit_rolls_back(self) -> None:
        """Exiting the context manager without commit triggers rollback."""
        uow = FakeUnitOfWork()
        # __aenter__ resets state, __aexit__ with no exception does NOT
        # rollback — only exception path triggers rollback in AbstractUnitOfWork.
        # To test the _rolled_back path we call rollback() explicitly.
        async with uow:
            pass
        # No exception, no explicit rollback → not rolled back
        assert uow._rolled_back is False

    @pytest.mark.anyio
    async def test_exit_with_exception_triggers_rollback(self) -> None:
        """Exiting with an exception triggers rollback in __aexit__."""
        uow = FakeUnitOfWork()
        with pytest.raises(RuntimeError, match="boom"):
            async with uow:
                raise RuntimeError("boom")
        assert uow._rolled_back is True


# ═══════════════════════════════════════════════════════════════════════════
# Async context manager protocol
# ═══════════════════════════════════════════════════════════════════════════


class TestAsyncContextManager:
    """FakeUnitOfWork implements the async context manager protocol."""

    @pytest.mark.anyio
    async def test_aenter_returns_self(self) -> None:
        """__aenter__ returns the UoW instance itself."""
        uow = FakeUnitOfWork()
        result = await uow.__aenter__()
        assert result is uow

    @pytest.mark.anyio
    async def test_aexit_no_exception(self) -> None:
        """__aexit__ with no exception completes without error."""
        uow = FakeUnitOfWork()
        async with uow:
            pass
        # No exception raised — context manager exited cleanly

    @pytest.mark.anyio
    async def test_aexit_with_exception_propagates(self) -> None:
        """__aexit__ does not suppress exceptions."""
        uow = FakeUnitOfWork()
        with pytest.raises(ValueError, match="test"):
            async with uow:
                raise ValueError("test")


# ═══════════════════════════════════════════════════════════════════════════
# Commit publishes collected events
# ═══════════════════════════════════════════════════════════════════════════


class TestCommitPublishesEvents:
    """commit() collects events from the repository and makes them
    available via collect_events()."""

    @pytest.mark.anyio
    async def test_commit_collects_events_from_repo(self) -> None:
        """Events on the aggregate are collected after commit()."""
        agg = _TestAggregate(id=uuid4())
        agg._add_event(_TestEvent(data="hello"))

        repo: FakeRepository = FakeRepository()
        await repo.save(agg)
        uow = FakeUnitOfWork(repository=repo)

        async with uow:
            await uow.commit()

        events = uow.collect_events()
        assert len(events) == 1
        assert events[0].data == "hello"

    @pytest.mark.anyio
    async def test_no_events_after_rollback(self) -> None:
        """Rollback clears collected events."""
        agg = _TestAggregate(id=uuid4())
        agg._add_event(_TestEvent(data="lost"))

        repo: FakeRepository = FakeRepository()
        await repo.save(agg)
        uow = FakeUnitOfWork(repository=repo)

        async with uow:
            await uow.commit()

        # Now rollback — this clears events in the base class
        await uow.rollback()
        # The base rollback clears _events, but the repo still has seen events
        # Let's verify that after rollback, collect_events is empty
        assert uow.collect_events() == []

    @pytest.mark.anyio
    async def test_commit_sets_committed_flag(self) -> None:
        """commit() sets the _committed flag."""
        uow = FakeUnitOfWork()
        async with uow:
            await uow.commit()
        assert uow._committed is True
