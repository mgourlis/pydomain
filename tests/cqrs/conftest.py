from __future__ import annotations

from typing import Any

import pytest

from pydomain.cqrs import Command, CommandBus, CommandResult, EmptyCommandResult
from pydomain.ddd.domain_event import DomainEvent

# ── Sample domain types for testing ──────────────────────────────────────


class MakeGreeting(Command[EmptyCommandResult]):
    name: str


class GreetingResult(CommandResult):
    greeting: str


class GreetPerson(Command[GreetingResult]):
    name: str


class CountingResult(CommandResult):
    count: int


class CountThings(Command[CountingResult]):
    values: list[int]


# ── Sample handlers ──────────────────────────────────────────────────────


class FakeMakeGreetingHandler:
    async def __call__(self, command: MakeGreeting) -> EmptyCommandResult:
        return EmptyCommandResult()


class FakeGreetPersonHandler:
    async def __call__(self, command: GreetPerson) -> GreetingResult:
        return GreetingResult(greeting=f"Hello, {command.name}!")


class FakeCountingHandler:
    async def __call__(self, command: CountThings) -> CountingResult:
        return CountingResult(count=len(command.values))


# ── Fake Unit of Work ────────────────────────────────────────────────────


class FakeUnitOfWork:
    """In-memory Unit of Work for testing.

    Tracks commit/rollback calls and collects domain events from
    aggregates that were seen by the repository.
    """

    def __init__(self, repository: Any | None = None) -> None:
        self._committed = False
        self._rolled_back = False
        self._events: list[DomainEvent] = []
        self._repository = repository

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: Any | None = None,
    ) -> None:
        pass

    async def commit(self) -> None:
        self._committed = True
        if self._repository is not None:
            for aggregate in list(self._repository._seen):
                self._events.extend(aggregate.pull_events())

    async def rollback(self) -> None:
        self._rolled_back = True

    def collect_events(self) -> list[DomainEvent]:
        return self._events


class SomethingHappened(DomainEvent):
    """Test domain event used by StampedUoW for tracing verification."""

    data: str


class StampedUoW(FakeUnitOfWork):
    """Unit of Work that returns pre-built domain events for tracing verification.

    By default returns a single ``SomethingHappened`` event. Can be
    configured with a custom event list via the ``events`` parameter.
    """

    def __init__(self, events: list[DomainEvent] | None = None) -> None:
        super().__init__()
        self._test_events = events or [SomethingHappened(data="test")]

    def collect_events(self) -> list[DomainEvent]:
        return self._test_events


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def bus() -> CommandBus:
    return CommandBus()


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def make_greeting_handler() -> FakeMakeGreetingHandler:
    return FakeMakeGreetingHandler()


@pytest.fixture
def greet_person_handler() -> FakeGreetPersonHandler:
    return FakeGreetPersonHandler()


@pytest.fixture
def counting_handler() -> FakeCountingHandler:
    return FakeCountingHandler()
