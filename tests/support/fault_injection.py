"""Fault injection: make the Nth awaited call on an object raise.

Wrap a collaborator (a repository, an outbox store, a lock provider …) and
hand the proxy to the code under test. Every awaited call to one of the
object's coroutine methods counts; the Nth raises the given exception, or
``asyncio.CancelledError`` to simulate a cancellation at that await::

    faulty = FaultInjector(outbox, at=2, raises=asyncio.CancelledError)
    uow = FakeUnitOfWork(repositories={"outbox": faulty.proxy})
"""

from __future__ import annotations

from collections.abc import Collection


class FaultInjector[T]:
    """Wrap *target* so that its *at*-th awaited call raises *raises*.

    Parameters
    ----------
    target:
        Any object. Only its coroutine methods (``async def``, including
        ``__aenter__`` and ``__aexit__``) are counted; everything else is
        passed through unchanged.
    at:
        1-based number of the awaited call that raises. Later calls pass
        through.
    raises:
        Exception class or instance to raise, e.g. ``asyncio.CancelledError``.
    methods:
        If given, only calls to these method names are counted.
    after:
        If true, the call reaches *target* first and the fault is raised
        after it returns: the effect happened, the caller sees a failure.
    """

    def __init__(
        self,
        target: T,
        *,
        at: int,
        raises: BaseException | type[BaseException],
        methods: Collection[str] | None = None,
        after: bool = False,
    ) -> None:
        raise NotImplementedError

    @property
    def proxy(self) -> T:
        """The wrapped object to hand to the code under test."""
        raise NotImplementedError

    @property
    def awaited(self) -> list[str]:
        """Names of the counted methods, in the order they were awaited."""
        raise NotImplementedError

    @property
    def fired(self) -> bool:
        """Whether the fault has been raised."""
        raise NotImplementedError
