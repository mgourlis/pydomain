from __future__ import annotations

import pytest

from pydomain.cqrs import (
    Command,
    CommandBus,
    CommandResult,
    EmptyCommandResult,
    QueryBus,
)
from pydomain.testing import FakeUnitOfWork
from tests.support.recording_unit_of_work import RecordingUnitOfWork

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
    async def __call__(
        self, command: MakeGreeting, uow: FakeUnitOfWork
    ) -> EmptyCommandResult:
        return EmptyCommandResult()


class FakeGreetPersonHandler:
    async def __call__(
        self, command: GreetPerson, uow: FakeUnitOfWork
    ) -> GreetingResult:
        return GreetingResult(greeting=f"Hello, {command.name}!")


class FakeCountingHandler:
    async def __call__(
        self, command: CountThings, uow: FakeUnitOfWork
    ) -> CountingResult:
        return CountingResult(count=len(command.values))


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def bus() -> CommandBus:
    return CommandBus()


@pytest.fixture
def query_bus() -> QueryBus:
    return QueryBus()


@pytest.fixture
def uow() -> RecordingUnitOfWork:
    """A fake that records whether its scope committed or rolled back."""
    return RecordingUnitOfWork()


@pytest.fixture
def make_greeting_handler() -> FakeMakeGreetingHandler:
    return FakeMakeGreetingHandler()


@pytest.fixture
def greet_person_handler() -> FakeGreetPersonHandler:
    return FakeGreetPersonHandler()


@pytest.fixture
def counting_handler() -> FakeCountingHandler:
    return FakeCountingHandler()
