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

from collections.abc import Awaitable, Callable, Collection
from typing import Any, cast

from tests.support._proxy import make_proxy


class OrderingSpy:
    """An ordered log shared by every wrapper and fake that records into it."""

    def __init__(self) -> None:
        self._entries: list[str] = []

    @property
    def entries(self) -> tuple[str, ...]:
        """The log, oldest entry first."""
        return tuple(self._entries)

    def record(self, entry: str) -> None:
        """Append *entry* to the log. For fakes that record themselves."""
        self._entries.append(entry)

    def wrap[T](
        self, target: T, label: str, *, methods: Collection[str] | None = None
    ) -> T:
        """Return a proxy of *target* that records its awaited calls.

        Each awaited call to a coroutine method appends ``"<label>.<method>"``
        when it returns, or ``"<label>.<method> raised <ExceptionType>"`` when
        it raises (the exception propagates). Sync methods and attributes pass
        through unrecorded. *methods* limits recording to those names.
        """
        names = None if methods is None else frozenset(methods)

        async def intercept(name: str, call: Callable[[], Awaitable[Any]]) -> Any:
            if names is not None and name not in names:
                return await call()
            try:
                result = await call()
            except BaseException as exc:
                self.record(f"{label}.{name} raised {type(exc).__name__}")
                raise
            self.record(f"{label}.{name}")
            return result

        return cast(T, make_proxy(target, intercept))

    def assert_order(self, *expected: str) -> None:
        """Assert that *expected* occur in the log in this order.

        Other entries may come between them; a repeated entry must occur as
        many times as it is expected.
        """
        remaining = iter(self._entries)
        for position, entry in enumerate(expected):
            if not any(seen == entry for seen in remaining):
                raise AssertionError(
                    f"expected {entry!r} (#{position + 1} of {list(expected)}) "
                    f"after the earlier entries; log was {self._entries}"
                )

    def assert_entries(self, *expected: str) -> None:
        """Assert that the log is exactly *expected*."""
        if self._entries != list(expected):
            raise AssertionError(f"expected log {list(expected)}, got {self._entries}")
