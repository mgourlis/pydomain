"""Shared fixtures for infrastructure tests."""

from __future__ import annotations

import pytest

from pydomain.infrastructure import MessageBus
from pydomain.testing import FakeUnitOfWork


@pytest.fixture
def bus() -> MessageBus:
    return MessageBus()


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()
