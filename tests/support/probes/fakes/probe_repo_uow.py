"""FAKE-01..04 and FAKE-11 probes. Synthetic data only."""

import asyncio
from uuid import UUID, uuid4

from pydomain.cqrs.saga.state import SagaState
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.exceptions import ConcurrencyError
from pydomain.testing import FakeRepository, FakeSagaRepository, FakeUnitOfWork


class Renamed(DomainEvent):
    name: str


class Widget(AggregateRoot[UUID]):
    name: str = "a"
    tags: list[str] = []

    def rename(self, n: str) -> None:
        self.name = n
        self._add_event(Renamed(name=n))


def show(label, value):
    print(f"{label}: {value}")


async def fake01() -> None:
    print("--- FAKE-01")
    w = Widget(id=uuid4())
    repo = FakeRepository[Widget, UUID]([w])
    show("seeded object stored by reference (store[id] is caller's obj)", repo._store[w.id] is w)
    a = await repo.get_by_id(w.id)
    b = await repo.get_by_id(w.id)
    show("two loads are same object", a is b)
    a.rename("x")
    await repo.save(a)
    b.rename("y")  # stale writer in a real store
    try:
        await repo.save(b)
        show("stale second save raised ConcurrencyError", False)
    except ConcurrencyError:
        show("stale second save raised ConcurrencyError", True)
    # Mutation without save is visible to the next load
    c = await repo.get_by_id(w.id)
    c.tags.append("unsaved")
    d = await repo.get_by_id(w.id)
    show("unsaved mutation visible to next load", d.tags)
    # A separately constructed object with mismatched version DOES conflict
    other = Widget(id=w.id, version=0)
    try:
        await repo.save(other)
        show("distinct object with stale version conflicts", False)
    except ConcurrencyError:
        show("distinct object with stale version conflicts", True)


async def fake02() -> None:
    print("--- FAKE-02")
    w = Widget(id=uuid4(), version=7)
    repo = FakeRepository[Widget, UUID]()
    await repo.save(w)
    show("insert with version=7 accepted; version after insert", w.version)
    w2 = Widget(id=uuid4())
    await repo.save(w2)
    show("fresh insert version (no bump on insert)", w2.version)
    await repo.save(w2)
    show("after first update, caller's object mutated to", w2.version)


async def fake03() -> None:
    print("--- FAKE-03")
    repo = FakeRepository[Widget, UUID]()
    uow = FakeUnitOfWork(repository=repo)
    w = Widget(id=uuid4())
    try:
        async with uow:
            w.rename("leaked")
            await repo.save(w)
            raise RuntimeError("handler fails before commit")
    except RuntimeError:
        pass
    show("rolled back", uow._rolled_back)
    show("repo buffer after rollback", [e.name for e in repo._collected_events])
    # next unit of work on the same repo, saving nothing
    async with uow:
        await uow.commit()
    show("events published by the NEXT commit", [e.name for e in uow.collect_events()])


async def fake04() -> None:
    print("--- FAKE-04")
    repo = FakeRepository[Widget, UUID]()
    uow = FakeUnitOfWork(repository=repo)
    wid = uuid4()
    try:
        async with uow:
            await repo.save(Widget(id=wid, name="should-vanish"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    show("aggregate still stored after rollback", (await repo.get_by_id(wid)) is not None)
    async with uow:
        await uow.commit()
    show("_rolled_back after a later successful UoW", uow._rolled_back)
    show("commit-failure hook attrs", [a for a in vars(uow) if "fail" in a])


async def fake11() -> None:
    print("--- FAKE-11")
    repo = FakeSagaRepository()
    s = SagaState(saga_type="T", correlation_id=uuid4())
    await repo.save(s)
    a = await repo.get_by_id(s.id)
    b = await repo.get_by_id(s.id)
    show("copy on read (a is not b)", a is not b)
    a.current_step = "from-a"
    a.touch()
    await repo.save(a)
    b.current_step = "from-b"  # stale
    b.touch()
    try:
        await repo.save(b)
        show("stale saga save raised ConcurrencyError", False)
    except ConcurrencyError:
        show("stale saga save raised ConcurrencyError", True)
    final = await repo.get_by_id(s.id)
    show("lost update: final current_step", final.current_step)
    # version going backwards accepted
    old = SagaState(**{**s.model_dump(), "version": 0})
    await repo.save(old)
    show("version regress accepted; stored version", (await repo.get_by_id(s.id)).version)
    # JSON round trip: does state survive model_dump_json/validate? (fake never does it)
    rt = SagaState.model_validate_json(final.model_dump_json())
    show("json round trip equal", rt == final)


async def main() -> None:
    await fake01()
    await fake02()
    await fake03()
    await fake04()
    await fake11()


asyncio.run(main())
