"""Shared fixtures for infrastructure tests."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from pydomain.infrastructure import MessageBus
from pydomain.testing import FakeUnitOfWork


class OrderPlacedEvent(BaseModel):
    """Simple domain event model used by EventRegistry tests."""

    order_id: str
    total: float


@pytest.fixture
def bus() -> MessageBus:
    return MessageBus()


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def order_placed_event() -> OrderPlacedEvent:
    return OrderPlacedEvent(order_id="ORD-001", total=99.95)
