"""Fault injection: make the Nth awaited call on an object raise.

Wrap a collaborator (a repository, an outbox store, a lock provider …) and
hand the proxy to the code under test. Every awaited call to one of the
object's coroutine methods counts; the Nth raises the given exception, or
``asyncio.CancelledError`` to simulate a cancellation at that await::

    faulty = FaultInjector(outbox, at=2, raises=asyncio.CancelledError)
    uow = FakeUnitOfWork(repositories={"outbox": faulty.proxy})
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from typing import Any, cast

from tests.support._proxy import make_proxy


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

    Only calls made through :attr:`proxy` count; a method the target calls on
    ``self`` does not. Raising ``CancelledError`` simulates a cancellation at
    that await; it does not cancel the surrounding task or an anyio scope.
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
        if at < 1:
            raise ValueError(f"at must be 1 or more, got {at}")
        self._at = at
        self._raises = raises
        self._methods = None if methods is None else frozenset(methods)
        self._after = after
        self._awaited: list[str] = []
        self._fired = False
        self._proxy = cast(T, make_proxy(target, self._intercept))

    @property
    def proxy(self) -> T:
        """The wrapped object to hand to the code under test."""
        return self._proxy

    @property
    def awaited(self) -> list[str]:
        """Names of the counted methods, in the order they were awaited."""
        return list(self._awaited)

    @property
    def fired(self) -> bool:
        """Whether the fault has been raised."""
        return self._fired

    async def _intercept(self, name: str, call: Callable[[], Awaitable[Any]]) -> Any:
        if self._methods is not None and name not in self._methods:
            return await call()
        self._awaited.append(name)
        if len(self._awaited) != self._at:
            return await call()
        self._fired = True
        if self._after:
            await call()
        raise self._raises
