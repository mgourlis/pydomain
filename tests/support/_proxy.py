"""A transparent proxy that routes awaited coroutine-method calls through a hook.

Shared by :mod:`tests.support.fault_injection` and :mod:`tests.support.ordering`.
Attribute reads and writes reach the target; ``isinstance`` sees the target's
class. Only calls made through the proxy are seen: a method that the target
calls on ``self`` bypasses it.
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


class AsyncContextCallProxy(AsyncCallProxy):
    """:class:`AsyncCallProxy` for a target that is an async context manager."""

    __slots__ = ()

    async def __aenter__(self) -> Any:
        return await self.__getattr__("__aenter__")()

    async def __aexit__(self, *exc_info: object) -> Any:
        return await self.__getattr__("__aexit__")(*exc_info)


def make_proxy(target: object, hook: Hook) -> AsyncCallProxy:
    """Wrap *target*, keeping ``async with`` support only if it has it."""
    if hasattr(type(target), "__aenter__"):
        return AsyncContextCallProxy(target, hook)
    return AsyncCallProxy(target, hook)
