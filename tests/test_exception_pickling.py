"""DCE-152 (B-208, B-210): every exception class pydomain exports survives a
pickle round trip with its type and message.

The sweep walks the ``__all__`` of each public package. A class whose
constructor needs more than a message has a sample in ``_SAMPLES``; every
other class is built from a synthetic message. A new exported exception that
cannot be built from a message fails ``test_every_exported_exception_has_a_sample``
until a sample is added here.
"""

from __future__ import annotations

import importlib
import inspect
import pickle
from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest

from pydomain.cqrs import (
    Command,
    CommandExecutionError,
    EmptyCommandResult,
    EventHandlersFailed,
    IdempotentCommandIgnored,
)
from pydomain.es.exceptions import (
    DuplicateCommandError,
    SnapshotRestoreError,
    StaleSnapshotError,
    StreamNotFoundError,
)

_PACKAGES = [
    "pydomain",
    "pydomain.cqrs",
    "pydomain.cqrs.saga",
    "pydomain.ddd",
    "pydomain.es",
    "pydomain.infrastructure",
    "pydomain.testing",
    "pydomain.testing.contracts",
]


class _ShipOrder(Command[EmptyCommandResult]):
    order_id: str


_SAMPLES: dict[type[BaseException], Callable[[], BaseException]] = {
    CommandExecutionError: lambda: CommandExecutionError(_ShipOrder(order_id="o-1")),
    IdempotentCommandIgnored: lambda: IdempotentCommandIgnored(
        UUID("00000000-0000-4000-8000-000000000001")
    ),
    EventHandlersFailed: lambda: EventHandlersFailed(
        "OrderShipped", [ValueError("synthetic"), RuntimeError("synthetic")]
    ),
    StreamNotFoundError: lambda: StreamNotFoundError("order-1"),
    DuplicateCommandError: lambda: DuplicateCommandError("order-1", "command-1"),
    SnapshotRestoreError: lambda: SnapshotRestoreError(
        "Order", "order-1", 3, "ValidationError at total (missing)"
    ),
    StaleSnapshotError: lambda: StaleSnapshotError("order-1", 1, 2),
}


def _exported_exceptions() -> list[type[BaseException]]:
    found: dict[type[BaseException], None] = {}
    for package in _PACKAGES:
        module = importlib.import_module(package)
        for name in module.__all__:
            obj = getattr(module, name)
            if inspect.isclass(obj) and issubclass(obj, BaseException):
                found[obj] = None
    return sorted(found, key=lambda cls: f"{cls.__module__}.{cls.__qualname__}")


def _build(cls: type[BaseException]) -> BaseException:
    sample = _SAMPLES.get(cls)
    return sample() if sample is not None else cls("synthetic message")


def _params() -> list[Any]:
    return [pytest.param(cls, id=cls.__qualname__) for cls in _exported_exceptions()]


def test_every_exported_exception_has_a_sample() -> None:
    """Each class builds from a message or has a sample in ``_SAMPLES``."""
    unbuildable = []
    for cls in _exported_exceptions():
        try:
            _build(cls)
        except TypeError:
            unbuildable.append(cls.__qualname__)
    assert unbuildable == []


@pytest.mark.parametrize("cls", _params())
def test_exported_exception_survives_pickle(cls: type[BaseException]) -> None:
    exc = _build(cls)

    restored = pickle.loads(pickle.dumps(exc))

    assert type(restored) is cls
    assert str(restored) == str(exc)
