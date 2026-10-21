"""Ordering spy: one shared, ordered log of what happened.

Wrappers and fakes append to the same log, so a test can assert the order of
side effects across collaborators (commit, rollback, lock release,
idempotency set, publish …)::

    spy = OrderingSpy()
    locks = spy.wrap(FakeLockProvider(), "lock")
    outbox = spy.wrap(FakeOutboxStore(), "outbox")
    ...
    spy.assert_order("outbox.append", "lock.release")
"""

from __future__ import annotations

from collections.abc import Collection


class OrderingSpy:
    """An ordered log shared by every wrapper and fake that records into it."""

    def __init__(self) -> None:
        raise NotImplementedError

    @property
    def entries(self) -> tuple[str, ...]:
        """The log, oldest entry first."""
        raise NotImplementedError

    def record(self, entry: str) -> None:
        """Append *entry* to the log. For fakes that record themselves."""
        raise NotImplementedError

    def wrap[T](
        self, target: T, label: str, *, methods: Collection[str] | None = None
    ) -> T:
        """Return a proxy of *target* that records its awaited calls.

        Each awaited call to a coroutine method appends ``"<label>.<method>"``
        when it returns, or ``"<label>.<method> raised <ExceptionType>"`` when
        it raises (the exception propagates). Sync methods and attributes pass
        through unrecorded. *methods* limits recording to those names.
        """
        raise NotImplementedError

    def assert_order(self, *expected: str) -> None:
        """Assert that *expected* occur in the log in this order.

        Other entries may come between them.
        """
        raise NotImplementedError

    def assert_entries(self, *expected: str) -> None:
        """Assert that the log is exactly *expected*."""
        raise NotImplementedError
