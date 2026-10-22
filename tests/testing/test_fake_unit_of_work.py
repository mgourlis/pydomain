"""Tests for FakeUnitOfWork — rollback tracking and async context manager."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from pydomain.cqrs import UnitOfWorkAlreadyCommittedError
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
    """The public ``rolled_back`` record reflects rollback() calls (B-94).

    Until DCE-126 these tests read the private ``_rolled_back`` (TEST-02).
    """

    @pytest.mark.anyio
    async def test_rolled_back_false_initially(self) -> None:
        """rolled_back starts as False."""
        uow = FakeUnitOfWork()
        assert uow.rolled_back is False

    @pytest.mark.anyio
    async def test_rollback_sets_flag(self) -> None:
        """Calling rollback() sets rolled_back to True."""
        uow = FakeUnitOfWork()
        await uow.rollback()
        assert uow.rolled_back is True

    @pytest.mark.anyio
    async def test_exit_without_commit_rolls_back(self) -> None:
        """Exiting the context manager without commit triggers rollback.

        Until DCE-116 this test asserted the opposite, blessing CQRS-24: the
        UnitOfWork Protocol says a clean exit without commit rolls back.
        """
        uow = FakeUnitOfWork()
        async with uow:
            pass
        assert uow.rolled_back is True

    @pytest.mark.anyio
    async def test_exit_after_commit_does_not_roll_back(self) -> None:
        uow = FakeUnitOfWork()
        async with uow:
            await uow.commit()
        assert uow.rolled_back is False

    @pytest.mark.anyio
    async def test_rollback_is_recorded_when_a_subclass_overrides_the_hook(
        self,
    ) -> None:
        """A subclass simulating a failing storage rollback keeps the record."""

        class FailingStorageUoW(FakeUnitOfWork):
            async def _rollback(self) -> None:
                raise RuntimeError("synthetic storage failure")

        uow = FailingStorageUoW()
        with pytest.raises(RuntimeError):
            await uow.rollback()
        assert uow.rolled_back is True

    @pytest.mark.anyio
    async def test_exit_with_exception_triggers_rollback(self) -> None:
        """Exiting with an exception triggers rollback in __aexit__."""
        uow = FakeUnitOfWork()
        with pytest.raises(RuntimeError, match="boom"):
            async with uow:
                raise RuntimeError("boom")
        assert uow.rolled_back is True

    @pytest.mark.anyio
    async def test_rollback_record_resets_on_entry(self) -> None:
        """Each scope reports only its own rollback."""
        uow = FakeUnitOfWork()
        with pytest.raises(RuntimeError):
            async with uow:
                raise RuntimeError("boom")

        async with uow:
            assert uow.rolled_back is False
            await uow.commit()
        assert uow.rolled_back is False

    def test_rolled_back_is_read_only(self) -> None:
        uow = FakeUnitOfWork()
        with pytest.raises(AttributeError):
            uow.rolled_back = True  # type: ignore[misc]


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
    async def test_failed_scope_event_never_reaches_next_commit(self) -> None:
        """The docs' wiring, one repository outliving the scope, leaks nothing."""
        repo: FakeRepository = FakeRepository()
        uow = FakeUnitOfWork(repository=repo)
        refused = _TestAggregate(id=uuid4())
        refused._add_event(_TestEvent(data="refused"))

        with pytest.raises(RuntimeError):
            async with uow:
                await repo.save(refused)
                raise RuntimeError("synthetic business failure")
        async with uow:
            await uow.commit()

        assert uow.collect_events() == []

    @pytest.mark.anyio
    async def test_rollback_drains_every_named_repository(self) -> None:
        orders: FakeRepository = FakeRepository()
        invoices: FakeRepository = FakeRepository()
        uow = FakeUnitOfWork(repositories={"orders": orders, "invoices": invoices})
        for repo in (orders, invoices):
            aggregate = _TestAggregate(id=uuid4())
            aggregate._add_event(_TestEvent(data="refused"))
            await repo.save(aggregate)

        async with uow:
            await uow.rollback()
            await uow.commit()

        assert uow.collect_events() == []

    @pytest.mark.anyio
    async def test_commit_marks_the_scope_committed(self) -> None:
        """After commit() the scope is committed: a second commit raises.

        Until DCE-126 this test read the private ``_committed`` flag (TEST-02).
        """
        uow = FakeUnitOfWork()
        async with uow:
            await uow.commit()
            with pytest.raises(UnitOfWorkAlreadyCommittedError, match="are persisted"):
                await uow.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Repository registration
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_repositories_default_entry_replaces_the_single_repository() -> None:
    """``repository=`` is stored under ``"default"``, as the docstring says.

    A ``repositories`` entry named ``"default"`` therefore replaces it, and
    only the replacement is collected from.
    """
    first: FakeRepository[_TestAggregate, object] = FakeRepository()
    second: FakeRepository[_TestAggregate, object] = FakeRepository()
    aggregate = _TestAggregate(id=uuid4())
    aggregate._add_event(_TestEvent(data="only in the first repository"))
    await first.save(aggregate)
    uow = FakeUnitOfWork(repository=first, repositories={"default": second})

    async with uow:
        await uow.commit()

    assert uow.collect_events() == []


# ═══════════════════════════════════════════════════════════════════════════
# Commit-failure injection (DCE-143, FAKE-04, B-127)
# ═══════════════════════════════════════════════════════════════════════════

_HOOKS = ("flush", "write_outbox", "commit")


class _InjectedFailure(Exception):
    """The failure a test hands to ``FakeUnitOfWork(failure=...)``."""


class _HookLogUoW(FakeUnitOfWork):
    """Logs each commit hook it reaches, before the fake's own hook runs."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.reached: list[str] = []

    async def _flush(self) -> None:
        self.reached.append("flush")
        await super()._flush()

    async def _write_outbox(self) -> None:
        self.reached.append("write_outbox")
        await super()._write_outbox()

    async def _commit(self) -> None:
        self.reached.append("commit")
        await super()._commit()


class TestFailAt:
    """``fail_at`` makes one hook of ``commit()`` raise, once (B-127)."""

    @pytest.mark.anyio
    @pytest.mark.parametrize("hook", _HOOKS)
    async def test_fake_unit_of_work_fail_at_raises_from_each_hook(
        self, hook: str
    ) -> None:
        failure = _InjectedFailure()
        uow = _HookLogUoW(fail_at=hook, failure=failure)

        async with uow:
            with pytest.raises(_InjectedFailure) as raised:
                await uow.commit()

        assert raised.value is failure
        assert uow.reached == list(_HOOKS[: _HOOKS.index(hook) + 1])
        assert uow.rolled_back

    @pytest.mark.anyio
    async def test_fake_unit_of_work_fail_at_fails_once_then_clears(self) -> None:
        """A scope that never reaches the hook keeps it armed; the next one
        that does fails, and the scope after that commits."""
        uow = FakeUnitOfWork(fail_at="commit", failure=_InjectedFailure())

        async with uow:
            pass
        async with uow:
            with pytest.raises(_InjectedFailure):
                await uow.commit()
        assert uow.fail_at is None
        async with uow:
            await uow.commit()

        assert not uow.rolled_back

    @pytest.mark.anyio
    async def test_fake_unit_of_work_failure_defaults_to_runtime_error(self) -> None:
        uow = FakeUnitOfWork(fail_at="flush")
        raised: list[BaseException] = []

        for _ in range(2):
            async with uow:
                with pytest.raises(RuntimeError) as error:
                    await uow.commit()
            raised.append(error.value)
            uow.fail_at = "flush"

        assert type(raised[0]) is RuntimeError
        assert raised[0] is not raised[1]

    @pytest.mark.anyio
    async def test_fake_unit_of_work_without_fail_at_commits(self) -> None:
        repo: FakeRepository[_TestAggregate, object] = FakeRepository()
        aggregate = _TestAggregate(id=uuid4())
        aggregate._add_event(_TestEvent())
        await repo.save(aggregate)
        uow = _HookLogUoW(repository=repo, fail_at=None)

        async with uow:
            await uow.commit()

        assert uow.reached == list(_HOOKS)
        assert len(uow.collect_events()) == 1
        assert not uow.rolled_back
