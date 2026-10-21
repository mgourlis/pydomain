"""Does AggregateLockingBehavior prevent the shared-repository event loss?"""
import asyncio
from uuid import UUID, uuid4
from pydomain.cqrs import Command, CommandBus, CommandExecutionError, EmptyCommandResult
from pydomain.cqrs.behaviors import AggregateLockingBehavior
from pydomain.cqrs.locking import DictLockKeyResolver
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeRepository, FakeUnitOfWork
from pydomain.testing.fake_lock_provider import FakeLockProvider

class Issued(DomainEvent):
    tag: str
class Inv(AggregateRoot[UUID]):
    @classmethod
    def issue(cls, tag):
        i = cls(id=uuid4()); i._add_event(Issued(tag=tag)); return i
class Do(Command[EmptyCommandResult]):
    tag: str
    key: str
    refuse: bool = False
def tags(evs): return [e.tag for e in evs if isinstance(e, Issued)]

class SlowCommitUoW(FakeUnitOfWork):
    gate: asyncio.Event | None = None
    async def _commit(self):
        if self.gate is not None:
            await self.gate.wait()

async def run(name, key_a, key_b, a_refuse, a_slow_commit):
    repo = FakeRepository(); provider = FakeLockProvider(); resolver = DictLockKeyResolver()
    resolver.register(Do, lambda m: [m.key])
    gate = asyncio.Event(); b_started = asyncio.Event()
    async def h(cmd, uow):
        await repo.save(Inv.issue(cmd.tag))
        if cmd.tag == "A":
            for _ in range(5): await asyncio.sleep(0)
            if cmd.refuse: raise ValueError("refused")
        else:
            gate.set()
        return EmptyCommandResult()
    def factory_a():
        u = SlowCommitUoW(repository=repo); u.gate = gate if a_slow_commit else None; return u
    lock = [AggregateLockingBehavior(provider, resolver)]
    bus = CommandBus()
    bus.register(Do, h, uow_factory=lambda: FakeUnitOfWork(repository=repo), behaviors=lock)
    bus_a = CommandBus(); bus_a.register(Do, h, uow_factory=factory_a, behaviors=lock)
    async def da():
        try: _, e = await bus_a.dispatch(Do(tag="A", key=key_a, refuse=a_refuse)); return tags(e)
        except CommandExecutionError: return "failed"
    async def db():
        await asyncio.sleep(0)
        _, e = await bus.dispatch(Do(tag="B", key=key_b)); return tags(e)
    ra, rb = await asyncio.wait_for(asyncio.gather(da(), db()), 5)
    print(f"{name:55} A -> {ra!s:10} B -> {rb}")

async def main():
    await run("(i) different aggregates, A fails", "order-1", "order-2", True, False)
    await run("(i) same aggregate key, A fails", "order-1", "order-1", True, False)
    await run("(ii) same aggregate key, B saves during A's commit", "order-1", "order-1", False, True)
    await run("(ii) different aggregates, B saves during A's commit", "order-1", "order-2", False, True)
asyncio.run(main())
