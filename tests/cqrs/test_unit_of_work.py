from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from pydomain.cqrs.exceptions import UnitOfWorkAlreadyCommittedError
from pydomain.cqrs.unit_of_work import AbstractUnitOfWork
from pydomain.ddd import DomainEvent

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class FakeRepo:
    """Minimal repo stand-in with pull_events for event collection tests."""

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
    async def test_collect_events_from_repos(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            event = ItemAddedToCart(item_id="abc", quantity=2)
            repo = FakeRepo(events=[event])
            uow._repos["default"] = repo
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
            repo = FakeRepo(events=[event])
            uow._repos["default"] = repo
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
            repo = FakeRepo(events=[event])
            uow._repos["default"] = repo
            await uow.commit()
            collected = uow.collect_events()
            assert collected[0].causation_id == caid

    @pytest.mark.anyio
    async def test_stamp_allows_none_ids(self) -> None:
        """When no correlation/causation IDs are set, stamping stores None."""
        uow = AbstractUnitOfWork()
        async with uow:
            event = ItemAddedToCart(item_id="abc", quantity=2)
            repo = FakeRepo(events=[event])
            uow._repos["default"] = repo
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
            repo = FakeRepo(events=[event])
            uow._repos["default"] = repo
            await uow.commit()
            # Original event must still have None tracing IDs
            assert event.correlation_id is None
            assert event.causation_id is None

    @pytest.mark.anyio
    async def test_aggregate_with_multiple_events(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            event1 = ItemAddedToCart(item_id="a", quantity=1)
            event2 = ItemAddedToCart(item_id="b", quantity=2)
            repo = FakeRepo(events=[event1, event2])
            uow._repos["default"] = repo
            await uow.commit()
            collected = uow.collect_events()
            assert len(collected) == 2

    @pytest.mark.anyio
    async def test_events_pulled_from_aggregate_are_cleared(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            event = ItemAddedToCart(item_id="abc", quantity=2)
            repo = FakeRepo(events=[event])
            uow._repos["default"] = repo
            await uow.commit()
            # After commit, repo should have no more events
            assert repo.pull_events() == []

    @pytest.mark.anyio
    async def test_empty_repos_produces_no_events(self) -> None:
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
            repo = FakeRepo(events=[event])
            uow._repos["default"] = repo
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
        """Verify that _flush, _collect_and_stamp, _write_outbox, and
        _commit are invoked in sequence during commit."""

        class RecordingUoW(AbstractUnitOfWork):
            def __init__(self) -> None:
                super().__init__()
                self.call_chain: list[str] = []

            async def _flush(self) -> None:
                self.call_chain.append("flush")

            async def _write_outbox(self) -> None:
                self.call_chain.append("outbox")

            async def _commit(self) -> None:
                self.call_chain.append("commit")

        uow = RecordingUoW()
        async with uow:
            await uow.commit()
            assert uow.call_chain == ["flush", "outbox", "commit"]

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
    async def test_commit_called(self) -> None:
        class CommitUoW(AbstractUnitOfWork):
            def __init__(self) -> None:
                super().__init__()
                self.called = False

            async def _commit(self) -> None:
                self.called = True

        uow = CommitUoW()
        async with uow:
            await uow.commit()
            assert uow.called is True

    @pytest.mark.anyio
    async def test_write_outbox_hook_exists(self) -> None:
        """_write_outbox is an overridable extension point."""

        class OutboxUoW(AbstractUnitOfWork):
            def __init__(self) -> None:
                super().__init__()
                self.was_called = False

            async def _write_outbox(self) -> None:
                self.was_called = True

        uow = OutboxUoW()
        async with uow:
            await uow.commit()
            assert uow.was_called is True

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
            repo = FakeRepo(events=[event])
            uow._repos["default"] = repo
            await uow.commit()
            assert uow.events_before_stamp == []

    @pytest.mark.anyio
    async def test_commit_called_after_outbox_and_stamping(self) -> None:
        """_commit runs after _write_outbox and _collect_and_stamp,
        so events should be available."""

        class CommitAfterStampUoW(AbstractUnitOfWork):
            def __init__(self) -> None:
                super().__init__()
                self.seen_events: list[DomainEvent] | None = None

            async def _commit(self) -> None:
                self.seen_events = list(self._events)

        uow = CommitAfterStampUoW()
        async with uow:
            event = ItemAddedToCart(item_id="abc", quantity=1)
            repo = FakeRepo(events=[event])
            uow._repos["default"] = repo
            await uow.commit()
            assert uow.seen_events is not None
            assert len(uow.seen_events) == 1
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
    async def test_collect_events_empty_with_repos_but_no_commit(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            event = ItemAddedToCart(item_id="abc", quantity=1)
            repo = FakeRepo(events=[event])
            uow._repos["default"] = repo
            # No commit happened yet
            assert uow.collect_events() == []

    @pytest.mark.anyio
    async def test_collect_events_still_returns_after_commit(self) -> None:
        uow = AbstractUnitOfWork()
        async with uow:
            event = ItemAddedToCart(item_id="abc", quantity=1)
            repo = FakeRepo(events=[event])
            uow._repos["default"] = repo
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


# ---------------------------------------------------------------------------
# DCE-116 (ADR-068): rollback drains event buffers and is idempotent
# ---------------------------------------------------------------------------


class _RecordingWriter:
    """Outbox writer stand-in that records each write's item ids."""

    def __init__(self) -> None:
        self.writes: list[list[str]] = []

    async def write(self, events: list[DomainEvent]) -> None:
        self.writes.append(_item_ids(events))


class _CartUoW(AbstractUnitOfWork):
    """A unit of work over a repository that may outlive it, with call records."""

    def __init__(
        self, repo: FakeRepo | None = None, writer: _RecordingWriter | None = None
    ) -> None:
        super().__init__(outbox_writer=writer)  # type: ignore[arg-type]
        self.carts = repo if repo is not None else FakeRepo()
        self._repos = {"carts": self.carts}
        self.calls: list[str] = []
        self.storage_rollbacks = 0

    def stage(self, item_id: str) -> None:
        """Buffer an event in the repository, as a repository's ``save()`` does."""
        self.carts._pending.append(ItemAddedToCart(item_id=item_id, quantity=1))

    async def _commit(self) -> None:
        self.calls.append("commit")

    async def _rollback(self) -> None:
        self.storage_rollbacks += 1

    async def rollback(self) -> None:
        self.calls.append("rollback")
        await super().rollback()


def _item_ids(events: list[DomainEvent]) -> list[str]:
    return [e.item_id for e in events if isinstance(e, ItemAddedToCart)]


class _StorageFailed(Exception):
    """Raised by a storage rollback or a repository's ``pull_events()``."""


class TestCommittedScopeDrainsOnExit:
    """ADR-068, author decision 1: a committed scope still drains on exit."""

    @pytest.mark.anyio
    async def test_change_staged_after_commit_never_reaches_a_later_scope(
        self,
    ) -> None:
        uow = _CartUoW()
        async with uow:
            uow.stage("committed")
            await uow.commit()
            uow.stage("staged after commit")

        assert uow.calls == ["commit"]
        assert uow.storage_rollbacks == 0
        assert _item_ids(uow.collect_events()) == ["committed"]

        async with uow:
            await uow.commit()
        assert _item_ids(uow.collect_events()) == []


class TestRollbackHook:
    """ADR-068, author decision 2: ``_rollback()`` is the storage hook."""

    @pytest.mark.anyio
    async def test_rollback_calls_the_rollback_hook_once(self) -> None:
        uow = _CartUoW()
        async with uow:
            uow.stage("refused")
            await uow.rollback()
            await uow.commit()

        assert uow.storage_rollbacks == 1

    @pytest.mark.anyio
    async def test_explicit_rollback_then_exit_rolls_back_again(self) -> None:
        """The scope after an explicit rollback is uncommitted, so exit rolls back."""
        uow = _CartUoW()
        async with uow:
            uow.stage("refused")
            await uow.rollback()

        assert uow.calls == ["rollback", "rollback"]
        assert uow.storage_rollbacks == 2

    @pytest.mark.anyio
    async def test_raising_rollback_hook_still_drains_and_starts_a_new_scope(
        self,
    ) -> None:
        class FailingStorageUoW(_CartUoW):
            async def _rollback(self) -> None:
                raise _StorageFailed

        uow = FailingStorageUoW()
        uow.stage("committed")
        await uow.commit()
        uow.stage("refused")

        with pytest.raises(_StorageFailed):
            await uow.rollback()

        await uow.commit()  # a new scope: committing again is allowed
        assert _item_ids(uow.collect_events()) == []


class TestDrainEveryRepository:
    """A repository whose ``pull_events()`` raises does not stop the drain."""

    @pytest.mark.anyio
    async def test_rollback_drains_every_repository_even_if_one_raises(
        self,
    ) -> None:
        class BadRepo:
            def __init__(self) -> None:
                self.pulls = 0

            def pull_events(self) -> list[DomainEvent]:
                self.pulls += 1
                if self.pulls == 2:
                    raise _StorageFailed
                return []

        good = FakeRepo()
        uow = _CartUoW(good)
        uow._repos = {"bad": BadRepo(), "good": good}
        await uow.commit()
        uow.stage("refused")

        with pytest.raises(_StorageFailed):
            await uow.rollback()

        assert good.pull_events() == []
        await uow.commit()  # the scope was reset despite the error
        assert _item_ids(uow.collect_events()) == []

    @pytest.mark.anyio
    async def test_first_pull_events_error_is_the_one_raised(self) -> None:
        class FailingRepo:
            def __init__(self, error: Exception) -> None:
                self.error = error

            def pull_events(self) -> list[DomainEvent]:
                raise self.error

        first, second = _StorageFailed("first"), _StorageFailed("second")
        uow = _CartUoW()
        uow._repos = {"a": FailingRepo(first), "b": FailingRepo(second)}

        with pytest.raises(_StorageFailed) as raised:
            await uow.rollback()

        assert raised.value is first

    @pytest.mark.anyio
    async def test_committed_exit_drains_even_if_one_repository_raises(
        self,
    ) -> None:
        class BadRepo:
            def pull_events(self) -> list[DomainEvent]:
                raise _StorageFailed

        good = FakeRepo()
        uow = _CartUoW(good)
        await uow.__aenter__()
        await uow.commit()
        uow._repos = {"bad": BadRepo(), "good": good}
        uow.stage("staged after commit")

        with pytest.raises(_StorageFailed):
            await uow.__aexit__(None, None, None)

        assert good.pull_events() == []
        assert uow.storage_rollbacks == 0


class TestRollbackDrainsRepositoryBuffers:
    @pytest.mark.anyio
    async def test_rollback_drains_repository_event_buffers(self) -> None:
        uow = _CartUoW()
        async with uow:
            uow.stage("refused")
            await uow.rollback()
            await uow.commit()

        assert _item_ids(uow.collect_events()) == []

    @pytest.mark.anyio
    async def test_failed_scope_events_never_reach_a_later_unit_of_work(self) -> None:
        shared = FakeRepo()
        with pytest.raises(RuntimeError):
            async with _CartUoW(shared) as failed:
                assert isinstance(failed, _CartUoW)
                failed.stage("refused")
                raise RuntimeError("synthetic business failure")

        later = _CartUoW(shared)
        async with later:
            later.stage("accepted")
            await later.commit()

        assert _item_ids(later.collect_events()) == ["accepted"]

    @pytest.mark.anyio
    async def test_rollback_twice_is_harmless(self) -> None:
        uow = _CartUoW()
        async with uow:
            uow.stage("refused")
            await uow.rollback()
            await uow.rollback()
            uow.stage("accepted")
            await uow.commit()

        assert _item_ids(uow.collect_events()) == ["accepted"]


class TestExitWithoutCommit:
    @pytest.mark.anyio
    async def test_exit_without_commit_rolls_back(self) -> None:
        uow = _CartUoW()
        async with uow:
            uow.stage("abandoned")

        assert uow.calls == ["rollback"]
        async with uow:
            await uow.commit()
        assert _item_ids(uow.collect_events()) == []

    @pytest.mark.anyio
    async def test_exit_after_commit_does_not_roll_back(self) -> None:
        uow = _CartUoW()
        async with uow:
            uow.stage("kept")
            await uow.commit()

        assert uow.calls == ["commit"]
        assert _item_ids(uow.collect_events()) == ["kept"]

    @pytest.mark.anyio
    async def test_exception_exit_rolls_back_once_and_drains(self) -> None:
        uow = _CartUoW()
        with pytest.raises(RuntimeError):
            async with uow:
                uow.stage("refused")
                raise RuntimeError("synthetic business failure")

        assert uow.calls == ["rollback"]
        async with uow:
            await uow.commit()
        assert _item_ids(uow.collect_events()) == []


class TestCollectEventsCopy:
    @pytest.mark.anyio
    async def test_collect_events_returns_a_copy(self) -> None:
        uow = _CartUoW()
        async with uow:
            uow.stage("first")
            await uow.commit()

        held = uow.collect_events()
        held.clear()
        assert _item_ids(uow.collect_events()) == ["first"]

        returned = uow.collect_events()
        async with uow:  # a later scope on the same unit of work
            await uow.commit()
        assert _item_ids(returned) == ["first"]


class TestSecondCommit:
    @pytest.mark.anyio
    async def test_second_commit_raises_and_writes_nothing(self) -> None:
        writer = _RecordingWriter()
        uow = _CartUoW(writer=writer)
        async with uow:
            uow.stage("first")
            await uow.commit()
            uow.stage("second")
            with pytest.raises(UnitOfWorkAlreadyCommittedError):
                await uow.commit()

        assert writer.writes == [["first"]]
        assert uow.calls == ["commit"]
        assert _item_ids(uow.collect_events()) == ["first"]

    @pytest.mark.anyio
    async def test_commit_retried_without_rollback_raises(self) -> None:
        class FailingCommitUoW(_CartUoW):
            async def _commit(self) -> None:
                await super()._commit()
                if self.calls.count("commit") == 1:
                    raise RuntimeError("synthetic storage failure")

        writer = _RecordingWriter()
        uow = FailingCommitUoW(writer=writer)
        async with uow:
            uow.stage("first")
            with pytest.raises(RuntimeError):
                await uow.commit()
            with pytest.raises(UnitOfWorkAlreadyCommittedError):
                await uow.commit()

        assert writer.writes == [["first"]]
        assert uow.calls == ["commit", "rollback"]

    @pytest.mark.anyio
    async def test_commit_after_rollback_starts_a_new_scope(self) -> None:
        writer = _RecordingWriter()
        uow = _CartUoW(writer=writer)
        async with uow:
            uow.stage("first")
            await uow.commit()
            await uow.rollback()
            uow.stage("second")
            await uow.commit()

        assert writer.writes == [["first"], ["second"]]

    @pytest.mark.anyio
    async def test_second_commit_error_says_the_first_commit_is_persisted(
        self,
    ) -> None:
        uow = _CartUoW()
        await uow.commit()

        with pytest.raises(UnitOfWorkAlreadyCommittedError) as raised:
            await uow.commit()

        assert str(raised.value) == (
            "commit() was already called in this unit-of-work scope; changes "
            "from the first commit are persisted. Handlers must not call "
            "commit() (ADR-020)."
        )

    @pytest.mark.anyio
    async def test_commit_retry_error_says_to_roll_back_first(self) -> None:
        class FailingCommitUoW(_CartUoW):
            async def _commit(self) -> None:
                raise RuntimeError("synthetic storage failure")

        uow = FailingCommitUoW()
        with pytest.raises(RuntimeError):
            await uow.commit()

        with pytest.raises(UnitOfWorkAlreadyCommittedError) as raised:
            await uow.commit()

        assert str(raised.value) == (
            "commit() was already called in this unit-of-work scope and failed; "
            "nothing from it is guaranteed to be persisted. Call rollback(), or "
            "enter the unit of work again, to start a new scope."
        )

    @pytest.mark.anyio
    async def test_each_scope_commits_once(self) -> None:
        writer = _RecordingWriter()
        uow = _CartUoW(writer=writer)
        for item_id in ("first", "second"):
            async with uow:
                uow.stage(item_id)
                await uow.commit()

        assert writer.writes == [["first"], ["second"]]


class TestFreshUnitOfWork:
    """A unit of work used before it is ever entered starts as an open scope."""

    @pytest.mark.anyio
    async def test_first_commit_of_a_fresh_unit_of_work_succeeds(self) -> None:
        uow = _CartUoW()
        uow.stage("first")

        await uow.commit()

        assert uow.calls == ["commit"]
        assert _item_ids(uow.collect_events()) == ["first"]

    @pytest.mark.anyio
    async def test_fresh_unit_of_work_exit_without_commit_rolls_back(self) -> None:
        uow = _CartUoW()
        uow.stage("abandoned")

        await uow.__aexit__(None, None, None)

        assert uow.calls == ["rollback"]
        await uow.commit()
        assert _item_ids(uow.collect_events()) == []
