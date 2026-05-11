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


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def bus() -> CommandBus:
    return CommandBus()


@pytest.fixture
def query_bus() -> QueryBus:
    return QueryBus()


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
