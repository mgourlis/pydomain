"""A transparent proxy that routes awaited coroutine-method calls through a hook.

Shared by :mod:`tests.support.fault_injection` and :mod:`tests.support.ordering`.
Attribute reads and writes reach the target; ``isinstance`` sees the target's
class. Only calls made through the proxy are seen: a method that the target
calls on ``self`` bypasses it.

A target that is an async context manager or an async iterator keeps that
protocol: ``async with proxy as x`` binds the proxy when ``__aenter__``
returns the target, and ``async for`` goes through the proxy, each awaited
``__anext__`` (including the last, which raises ``StopAsyncIteration``)
passing through the hook. An ``async def`` method that is an async
generator is not a coroutine method: it passes through uncounted.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

Hook = Callable[[str, Callable[[], Awaitable[Any]]], Awaitable[Any]]
"""``hook(method_name, call)``: decide whether and when to await ``call()``."""


class AsyncCallProxy:
    """Proxy whose coroutine methods go through *hook*."""

    __slots__ = ("_proxy_hook", "_proxy_target")

    _proxy_target: Any
    _proxy_hook: Hook

    def __init__(self, target: object, hook: Hook) -> None:
        object.__setattr__(self, "_proxy_target", target)
        object.__setattr__(self, "_proxy_hook", hook)

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._proxy_target, name)
        if not inspect.iscoroutinefunction(value):
            return value
        hook = self._proxy_hook

        @functools.wraps(value)
        async def call(*args: Any, **kwargs: Any) -> Any:
            return await hook(name, lambda: value(*args, **kwargs))

        return call

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._proxy_target, name, value)

    def __delattr__(self, name: str) -> None:
        delattr(self._proxy_target, name)

    # Makes isinstance() see the target's class, as unittest.mock does with spec.
    @property  # type: ignore[misc]
    def __class__(self) -> type:
        return type(self._proxy_target)

    def __repr__(self) -> str:
        return repr(self._proxy_target)


class _AsyncContextMixin:
    """Forwards ``async with``; binds the proxy when the target binds itself."""

    __slots__ = ()

    async def __aenter__(self: Any) -> Any:
        result = await self.__getattr__("__aenter__")()
        return self if result is self._proxy_target else result

    async def __aexit__(self: Any, *exc_info: object) -> Any:
        return await self.__getattr__("__aexit__")(*exc_info)


class _AsyncIterableMixin:
    """Forwards ``__aiter__``; a separate iterator is proxied with the same hook."""

    __slots__ = ()

    def __aiter__(self: Any) -> Any:
        target = self._proxy_target
        iterator = type(target).__aiter__(target)
        if iterator is target:
            return self
        return make_proxy(iterator, self._proxy_hook)


class _AsyncIteratorMixin:
    """Routes each awaited ``__anext__`` through the hook."""

    __slots__ = ()

    async def __anext__(self: Any) -> Any:
        target = self._proxy_target
        return await self._proxy_hook(
            "__anext__", lambda: type(target).__anext__(target)
        )


@functools.cache
def _proxy_class(context: bool, iterable: bool, iterator: bool) -> type[AsyncCallProxy]:
    bases: list[type] = []
    if context:
        bases.append(_AsyncContextMixin)
    if iterable:
        bases.append(_AsyncIterableMixin)
    if iterator:
        bases.append(_AsyncIteratorMixin)
    if not bases:
        return AsyncCallProxy
    return type("AsyncCallProxy", (*bases, AsyncCallProxy), {"__slots__": ()})


def make_proxy(target: object, hook: Hook) -> AsyncCallProxy:
    """Wrap *target*, keeping only the async protocols its class supports."""
    cls = type(target)
    proxy_class = _proxy_class(
        hasattr(cls, "__aenter__"), hasattr(cls, "__aiter__"), hasattr(cls, "__anext__")
    )
    return proxy_class(target, hook)
